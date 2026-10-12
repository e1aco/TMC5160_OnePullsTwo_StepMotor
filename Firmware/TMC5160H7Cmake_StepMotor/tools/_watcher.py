#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""_watcher.py — 确定性判据监听（cl Skill 物理闭环"热循环"的唯一看日志者）

把"谁在盯日志"从 AI（主/子代理）手中拿走，交给这个确定性脚本：
can_monitor.py / serial_monitor.py 常驻监听时，本模块在进程内逐行匹配判据，
把结论原子写进一份 ≤10 行的 status 文件。AI（主代理）每轮只读这份 status
做决策，不碰日志原文、不数秒、不轮询。

为什么不是子代理/主代理盯？——热循环的活极小（看最后几行），每次召唤 AI
都要付固定开销（系统提示词+工具定义+任务指令，数千 token），为一小段日志
付一次整车运费；且主代理一旦上手读日志就容易陷入无界轮询出不来。
确定性脚本 ≈ 0 token，且时序（顺序/原子/握手）是硬保证。

写盘保证（读侧时序）：
  · status 原子覆写（写 .tmp 再 os.replace）→ AI 永远读到完整版，无 torn read
  · updated 单调 = 最新数据行时间戳 → AI 据此判断数据在流 / 停滞
  · flash_marker 握手：AI 烧录前写 marker 文件，本 watcher 消费后回写
    marker_seen=<内容> 到 status → AI 读到与自己写的一致，即知"这轮数据从烧录后起算"
  · --boot 启动横幅匹配 → boots+1 → AI 确认"新固件已生效"再判读（改参轮必需）

协议（与 details/run.md「判据监听」一致）：
  --watch "p1||p2||p3"   判据（|| 分隔的 regex）
  --boot  "横幅 regex"    启动横幅
  --status <路径>         状态文件（默认 .cl/capture/status）
  --marker <路径>         flash marker（默认 .cl/capture/flash_marker）
"""

import os
import re
import subprocess
import sys
import time


# ---------------- 常驻监听防呆 + 守护（serial/can monitor 共用） ----------------

DAEMON_ENV = "CL_MONITOR_DAEMON_CHILD"


def daemon_entry(argv, duration, watch, out_path, status_path):
    """常驻监听入口防呆。返回 None = 继续前台主循环；返回 int = 立即退出码。

    规则（机械保证，AI 无法跑错）：
      · duration > 0 有界监听 → 照常前台（会自然结束，不阻塞）
      · TTY 环境（人在自己终端手动观察）→ 照常前台
      · 非 TTY（被 AI bash 工具等自动化调用）且未带 --daemon → 拒绝启动 exit 2
        （前台同步运行常驻进程必阻塞调用方直到超时——uart:reopen_loop 事故根因）
      · 带 --daemon → 分离重启自身（去掉 --daemon，注入 DAEMON_ENV），
        stdout/stderr 重定向 mon_out.log/mon_err.log，父进程探活后立即返回
    """
    if duration > 0:
        return None
    if os.environ.get(DAEMON_ENV) == "1":
        return None                      # 分离子进程：正常进入主循环
    if "--daemon" in argv:
        return _launch_detached(argv, watch, out_path, status_path)
    try:
        tty = sys.stdout.isatty()
    except Exception:                    # noqa: BLE001 — 判定失败按非交互处理
        tty = False
    if tty:
        return None
    sys.stderr.write(
        "[cl] 常驻监听禁止在自动化环境中前台启动（会阻塞调用方直到超时，"
        "ESC 后留孤儿占端口 → uart:reopen_loop 事故链）\n"
        "[cl] 请加 --daemon 后台启动：探活通过后调用方立即返回\n")
    return 2


def _launch_detached(argv, watch, out_path, status_path):
    child_argv = [sys.executable] + [a for a in argv if a != "--daemon"]
    base = os.path.dirname(out_path or status_path) or "."
    os.makedirs(base, exist_ok=True)
    out_log = os.path.join(base, "mon_out.log")
    err_log = os.path.join(base, "mon_err.log")
    kwargs = {}
    env = dict(os.environ)
    env[DAEMON_ENV] = "1"
    if os.name == "nt":
        kwargs["creationflags"] = (
            getattr(subprocess, "DETACHED_PROCESS", 0x00000008)
            | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200))
    else:
        kwargs["start_new_session"] = True
    with open(out_log, "w", encoding="utf-8") as fo, \
            open(err_log, "w", encoding="utf-8") as fe:
        proc = subprocess.Popen(child_argv, stdout=fo, stderr=fe,
                                cwd=os.getcwd(), env=env, **kwargs)
    probe = status_path if watch else out_path
    deadline = time.time() + 3.0
    alive, ok = True, False
    while time.time() < deadline:
        if proc.poll() is not None:
            alive = False
            break
        if probe and os.path.exists(probe) and os.path.getsize(probe) > 0:
            ok = True
            break
        time.sleep(0.3)
    if ok and alive:
        print("[cl] 监听已后台启动 PID=%d（日志 %s / %s）；"
              "停止/重开规范见 details/run-fault.md" % (proc.pid, out_log, err_log))
        return 0
    tail = ""
    try:
        with open(err_log, encoding="utf-8", errors="replace") as f:
            tail = f.read()[-300:]
    except OSError:
        pass
    print("[cl] 后台启动失败或探活超时（PID=%d，存活=%s）%s"
          % (proc.pid, alive, ("\n" + tail) if tail else ""))
    return 1


def _ts():
    return time.strftime("[%H:%M:%S.", time.localtime()) + "%03d]" % (
        int((time.time() % 1) * 1000))


class Watcher:
    """每行一次 on_line()，周期 maybe_flush()；一切状态原子写进 status 文件。"""

    def __init__(self, patterns, boot_pattern, status_path, marker_path, hits_path):
        self.pats = [re.compile(p) for p in patterns]
        self.pat_names = [p[:60] for p in patterns]
        self.boot_re = re.compile(boot_pattern) if boot_pattern else None
        self.status_path = status_path
        self.marker_path = marker_path
        self.hits_path = hits_path
        # 会话产物: 启动时截断 hits.log, 防跨会话 append 无限增长 (见 watcher.md 日志生命周期)
        if hits_path:
            try:
                with open(hits_path, "w", encoding="utf-8"):
                    pass
            except OSError:
                pass
        self.updated = ""
        self.boots = 0
        self.last_boot = ""
        self.hits = 0
        self.pat_counts = [0] * len(self.pats)
        self.last_hit = ""
        self.marker_seen = ""
        self.fresh_episode = False
        self.sf_lines = 0
        self.sf_first = ""
        self.sf_last = ""
        self.decode_errors = 0
        self.recon_warn = 0
        self._recon_last = 0
        self._last_write = 0.0

    # ---- 事件 ----
    def on_line(self, text, idpre, ts):
        """处理一行完整还原文本。text 已剥 ANSI/前导垃圾；idpre 形如 'ID=0x1AA55F44 '。"""
        self.updated = ts
        if "\ufffd" in text:
            self.decode_errors += 1
        if self.fresh_episode:
            self.sf_lines += 1
            if not self.sf_first:
                self.sf_first = ts
            self.sf_last = ts
        if self.boot_re and self.boot_re.search(text):
            self.boots += 1
            self.last_boot = ts
        for i, r in enumerate(self.pats):
            if r.search(text):
                self.hits += 1
                self.pat_counts[i] += 1
                self.last_hit = "%s [%s] %s%s" % (ts, self.pat_names[i], idpre, text)
                self._append_hit(self.last_hit)
                break

    def _append_hit(self, line):
        try:
            with open(self.hits_path, "a", encoding="utf-8") as fh:
                fh.write(line.rstrip("\n") + "\n")
        except OSError:
            pass

    def check_marker(self):
        """AI 烧录前写 marker 文件；本 watcher 消费后回写 marker_seen 并重置 since_flash。"""
        if not self.marker_path or not os.path.exists(self.marker_path):
            return False
        try:
            with open(self.marker_path, "r", encoding="utf-8") as fh:
                content = fh.read().strip()
            os.remove(self.marker_path)
        except OSError:
            return False
        self.marker_seen = content
        self.fresh_episode = True
        self.sf_lines = 0
        self.sf_first = ""
        self.sf_last = ""
        return True

    def warn_reconstruct(self, cur_len):
        """还原缓冲持续膨胀无行结束符 → 拥塞/断行告警（每 256 字节记一次）。"""
        if cur_len > 512 and cur_len > self._recon_last + 256:
            self.recon_warn += 1
            self._recon_last = cur_len
        elif cur_len < 256:
            self._recon_last = 0

    # ---- 写盘 ----
    def _status_text(self):
        def zero(v):
            return v if v else "-"

        lines = [
            "updated: %s" % zero(self.updated),
            "since_flash: lines=%d first=%s last=%s" % (
                self.sf_lines, zero(self.sf_first), zero(self.sf_last)),
            "marker_seen: %s" % zero(self.marker_seen),
            "boots: %d last_boot: %s" % (self.boots, zero(self.last_boot)),
            "hits: %d" % self.hits,
        ]
        if self.pats:
            lines.append("patterns:")
            for name, c in zip(self.pat_names, self.pat_counts):
                lines.append("  %s: %d" % (name, c))
        lines.append("last_hit: %s" % zero(self.last_hit[:200]))
        lines.append("decode_errors: %d reconstruct_warn: %d" % (
            self.decode_errors, self.recon_warn))
        return "\n".join(lines) + "\n"

    def write_status(self, force=False):
        now = time.monotonic()
        if not force and (now - self._last_write) < 0.5:
            return
        self._last_write = now
        if not self.status_path:
            return
        try:
            tmp = self.status_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                fh.write(self._status_text())
            os.replace(tmp, self.status_path)   # 原子覆写：AI 永远读到完整版
        except OSError:
            pass
