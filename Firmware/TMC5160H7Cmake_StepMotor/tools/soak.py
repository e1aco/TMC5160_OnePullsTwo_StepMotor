#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""soak.py — cl Skill 通用浸泡验证工具（单进程监听+发送+判据统计）

背景：PCAN 通道单客户端独占 → 监听与发送必须同一进程
（此前 TMC5160 项目临时写
soak_switch_test.py，现泛化为通用工具。判据按掩码位组合设计，
见 details/run.md「判据设计」；浸泡流程见 details/run-soak.md）。

用法:
  # 串口回传 + 周期发命令
  python tools/soak.py --port COM5 --baud 115200 \
      --send-hex "02 00 00 00 01 01 00 03" --send-interval 0.5 --rounds 50 \
      --watch "A=..|S=10|FAULT" --boot "System Start!" \
      --log .cl/capture/soak.log --status .cl/capture/soak_status

  # PCAN 回传（全监 ID 0，多 ID 分组还原）+ 周期发命令
  python tools/soak.py --channel PCAN_USBBUS1 --bitrate 500000 --id 0 \
      --send-hex "..." --send-interval 0.5 --rounds 50 --watch "..." \
      --tx-id 0x1AA55F42

  # 自定义协议帧（带校验和/多字段命令）→ 提供发送脚本模块：
  #   脚本内定义 make_frame(round_index) -> bytes
  python tools/soak.py --port COM5 --baud 115200 \
      --send-script .cl/tools/my_soak_send.py --rounds 100 --watch "..."

  # 只监听不发送（纯浸泡观察，--duration 秒后收尾；0=常驻 Ctrl+C）
  python tools/soak.py --port COM5 --baud 115200 --duration 600 --watch "..."

退出码: 0 = 浸泡完成且 0 命中; 2 = 浸泡完成但有命中（看 --status final 行）;
1 = 中断/失败。

产物:
  --log      捕获日志（增量追加写）
  --status   浸泡状态文件（原子覆写；主代理只读它决策，不碰日志）
"""

import argparse
import os
import re
import sys
import time

from _watcher import Watcher

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass

_CANONICAL = re.compile(r"\033\[[0-9;]*m")
_LEADING_JUNK = re.compile(r"^[\ufffd\x00-\x1f]+")


def _ts():
    return time.strftime("[%H:%M:%S.", time.localtime()) + "%03d]" % (
        int((time.time() % 1) * 1000))


def parse_args():
    p = argparse.ArgumentParser(
        description="cl Skill 通用浸泡验证（单进程监听+发送+判据）",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--port", help="串口回传通道（如 COM5）")
    g.add_argument("--channel", help="PCAN 回传通道（如 PCAN_USBBUS1）")
    p.add_argument("--baud", type=int, default=115200, help="串口波特率")
    p.add_argument("--bitrate", type=int, default=500000, help="CAN 波特率")
    p.add_argument("--id", type=lambda s: int(s, 0), default=0,
                   help="CAN 监听仲裁 ID；0=全监（默认，多 ID 分组还原带前缀）")
    p.add_argument("--extended", action="store_true", help="使用扩展帧 29 位 ID")
    p.add_argument("--tx-id", type=lambda s: int(s, 0),
                   help="CAN 发送命令帧仲裁 ID（--send-hex/--send-script 时）")
    p.add_argument("--send-hex", help="周期发送的固定帧（空格分隔十六进制字节）")
    p.add_argument("--send-script",
                   help="周期发送脚本模块（.py）：定义 make_frame(round_index)->bytes")
    p.add_argument("--send-interval", type=float, default=0.5,
                   help="发送间隔秒（默认 0.5）")
    p.add_argument("--rounds", type=int, help="发送总轮数（到数即收尾）")
    p.add_argument("--duration", type=float, default=0.0,
                   help="浸泡时长秒（0=直到 rounds 完成或 Ctrl+C）")
    p.add_argument("--watch",
                   help="判据 regex（|| 分隔），命中计数并写入 hits.log/status")
    p.add_argument("--boot", help="启动横幅 regex（可选），匹配即 boots+1")
    p.add_argument("--log", default=".cl/capture/soak.log",
                   help="捕获日志路径（增量追加写）")
    p.add_argument("--status", default=".cl/capture/soak_status",
                   help="浸泡状态文件（原子覆写）")
    return p.parse_args()


def load_send_script(path):
    """加载发送脚本模块，返回 make_frame(i)->bytes。"""
    import importlib.util
    spec = importlib.util.spec_from_file_location("_soak_send", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    if not hasattr(mod, "make_frame"):
        raise SystemExit("send-script 必须定义 make_frame(round_index) -> bytes")
    return mod.make_frame


class Soak:
    def __init__(self, args):
        self.args = args
        self.watcher = Watcher(
            patterns=[s.strip() for s in (args.watch or "").split("||") if s.strip()],
            boot_pattern=args.boot or "",
            status_path=None, marker_path=None, hits_path=None)
        self.hits_path = os.path.join(os.path.dirname(args.status) or ".",
                                      "soak_hits.log")
        self.watcher.hits_path = self.hits_path
        self.rounds_done = 0
        self.start = time.monotonic()
        self.final = ""
        self.running = True
        self.frame = None
        self.make_frame = None
        if args.send_hex:
            self.frame = bytes(int(x, 16) for x in args.send_hex.split())
        elif args.send_script:
            self.make_frame = load_send_script(args.send_script)
        self._last_send = 0.0
        os.makedirs(os.path.dirname(args.log) or ".", exist_ok=True)
        self.fh = open(args.log, "w", encoding="utf-8")

    # ---- 收尾 ----
    def finish(self, rc_note):
        self.running = False
        self.final = rc_note
        self.write_status(force=True)
        elapsed = time.monotonic() - self.start
        print("[soak] final: %s elapsed=%.1fs rounds=%d/%d hits=%d" % (
            rc_note, elapsed, self.rounds_done,
            self.args.rounds or 0, self.watcher.hits))
        self.fh.close()

    def exit_code(self):
        if self.final == "FAIL":
            return 2
        if self.final == "INTERRUPTED":
            return 1
        return 0

    def write_status(self, force=False):
        """合并 watcher 状态 + 浸泡字段，原子覆写 status 文件。"""
        base = self.watcher._status_text().rstrip("\n")
        elapsed = time.monotonic() - self.start
        extra = [
            "mode: soak",
            "elapsed: %.1fs" % elapsed,
            "rounds_done: %d/%d" % (self.rounds_done, self.args.rounds or 0),
            "final: %s" % (self.final or "running"),
        ]
        text = base + "\n" + "\n".join(extra) + "\n"
        try:
            tmp = self.args.status + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                fh.write(text)
            os.replace(tmp, self.args.status)
        except OSError:
            pass

    # ---- 输入处理 ----
    def on_line(self, text, idpre, ts):
        self.fh.write("%s %s%s\n" % (ts, idpre, text))
        self.fh.flush()
        self.watcher.on_line(text, idpre, ts)
        self.write_status()

    def stop_reason(self):
        """返回 (停止与否, 收尾标记)。"""
        if not self.running:
            return True, self.final
        now = time.monotonic()
        if self.args.duration and (now - self.start) >= self.args.duration:
            return True, "PASS" if self.watcher.hits == 0 else "FAIL"
        if self.args.rounds and self.rounds_done >= self.args.rounds:
            return True, "PASS" if self.watcher.hits == 0 else "FAIL"
        return False, ""

    # ---- 发送 ----
    def maybe_send(self, bus=None, ser=None):
        """到点且未达轮数上限时发送一帧。"""
        if self.frame is None and self.make_frame is None:
            return
        now = time.monotonic()
        if now - self._last_send < self.args.send_interval:
            return
        frame = self.frame if self.frame is not None else \
            self.make_frame(self.rounds_done)
        if ser is not None:
            ser.write(frame)
        elif bus is not None:
            import can
            msg = can.Message(
                arbitration_id=self.args.tx_id, data=frame,
                is_extended_id=self.args.extended)
            bus.send(msg)
        self._last_send = now
        self.rounds_done += 1
        self.write_status()

def run_serial(args, soak):
    import serial as pyserial
    ser = pyserial.Serial(args.port, args.baud, timeout=0.1)
    partial = bytearray()
    while True:
        data = ser.read(4096)
        if data:
            partial.extend(data)
            while True:
                nl = partial.find(b"\n")
                if nl < 0:
                    break
                chunk = partial[: nl + 1].rstrip(b"\r\n")
                text = _CANONICAL.sub("", chunk.decode("utf-8", errors="replace"))
                text = _LEADING_JUNK.sub("", text)
                if text:
                    soak.on_line(text, "", _ts())
                del partial[: nl + 1]
        soak.maybe_send(ser=ser)
        stop, note = soak.stop_reason()
        if stop:
            soak.finish(note)
            return


def run_can(args, soak):
    import can
    bus = can.Bus(channel=args.channel, interface="pcan",
                  bitrate=args.bitrate)
    partials = {}
    while True:
        msg = bus.recv(timeout=0.1)
        if msg is not None:
            if args.id != 0 and msg.arbitration_id != args.id:
                continue
            part = partials.setdefault(msg.arbitration_id, bytearray())
            part.extend(msg.data)
            while True:
                nl = part.find(b"\n")
                if nl < 0:
                    break
                chunk = part[: nl + 1].rstrip(b"\r\n")
                text = _CANONICAL.sub("", chunk.decode("utf-8", errors="replace"))
                text = _LEADING_JUNK.sub("", text)
                idpre = ("ID=0x%X " % msg.arbitration_id) if args.id == 0 else ""
                if text:
                    soak.on_line(text, idpre, _ts())
                del part[: nl + 1]
        soak.maybe_send(bus=bus)
        stop, note = soak.stop_reason()
        if stop:
            soak.finish(note)
            return


def main():
    args = parse_args()
    if args.rounds and args.send_hex is None and args.send_script is None:
        print("[soak] --rounds 需配合 --send-hex/--send-script 使用"
              "（轮数=发送轮数）；纯监听请用 --duration", file=sys.stderr)
        return 1
    soak = Soak(args)
    try:
        if args.port:
            run_serial(args, soak)
        else:
            run_can(args, soak)
        return soak.exit_code()
    except KeyboardInterrupt:
        soak.finish("INTERRUPTED")
        return soak.exit_code()
    except Exception as e:  # noqa: BLE001
        print("[soak] 失败: %r" % (e,), file=sys.stderr)
        soak.finish("ERROR")
        return 1


if __name__ == "__main__":
    sys.exit(main())
