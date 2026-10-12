#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""cl skill 的 OpenOCD 一体烧录工具：烧录 + 校验 + 复位运行一次完成。

来源：stm32debug-skill（E:/Desktop/XM/stm32debug-skill）的 flash.sh 核心模式
    openocd -f <cfg> -c "program <hex> verify reset exit"
移植为 Windows 原生 Python 版并加入 cl 的结构化失败分级。

解决的两个历史痛点（教训与诊断单源见 details/run-fault.md「假烧录」条）：
    1. 假烧录——pyocd 缺 Flash 算法时报告成功但未写入。本工具走 OpenOCD
       verify（校验失败即报 FAILURE: verify-fail），且成功判定以输出中
       "Programming Finished / verified" 为准，不信 exit code。
    2. 烧录后不重启——program/verify/reset 在同一次 OpenOCD 会话内原子完成，
       不存在"烧完忘了复位/复位失败"的中间态；结束后 OpenOCD 自动退出释放探针，
       不留残留进程占探针导致下次烧录失败。

依赖：openocd（本机未装时 FAILURE: openocd-missing 给安装指引）。
安装（任选其一）：
    xpm install @xpack-dev-tools/openocd@latest   （推荐，装完设环境变量 OPENOCD_BIN）
    或 STM32CubeIDE 自带 openocd.exe 所在目录设为 OPENOCD_BIN

用法：
    python openocd_flash.py --flash <hex路径> --target stm32f1x
    python openocd_flash.py --flash <hex路径> --cfg board/stm32f7discovery.cfg
    python openocd_flash.py --flash <hex> --no-run        # 烧录+校验但不复位
    python openocd_flash.py --reset --target stm32f1x     # 仅复位运行
    python openocd_flash.py --kill                        # 仅清理残留 openocd 进程

目标型号（--target，映射 OpenOCD target cfg 名）：
    stm32f1x / stm32f2x / stm32f4x / stm32f7x / stm32l4x / stm32h7x / stm32g0x / stm32g4x
    Keil 工程名到 cfg 名的粗映射由 AI 完成（如 STM32F103C8 -> stm32f1x）；
    不确定时用 --cfg 直接给完整配置文件。

退出码 0 = 成功；非 0 = 失败（分类见 main() 底部）。
"""

import argparse
import os
import re
import subprocess
import sys
import time

# 常见 openocd 安装位置（按序探测）
OPENOCD_SEARCH = [
    os.path.expandvars(r"%APPDATA%\xPacks\@xpack-dev-tools\openocd"),
    r"E:\qp\qtools\opt\xpack-openocd-0.12.0-7\bin",
    r"C:\Program Files\OpenOCD",
    r"C:\openocd",
    r"D:\openocd",
    r"C:\ST\STM32CubeIDE_*",
]

SUCCESS_RE = re.compile(
    r"(Programming Finished|verified|Verified OK|shutdown command invoked)", re.IGNORECASE)
FAIL_RE = re.compile(
    r"(verify failed|verification failed|error: |failed|timeout|Polling target.*failed)",
    re.IGNORECASE)


class FlashError(Exception):
    def __init__(self, category, message, detail=""):
        super().__init__(message)
        self.category = category
        self.detail = detail


def find_openocd():
    """定位 openocd.exe：env OPENOCD > env OPENOCD_BIN > PATH > 常见目录。"""
    env1 = os.environ.get("OPENOCD")
    if env1 and os.path.isfile(env1):
        return env1
    env2 = os.environ.get("OPENOCD_BIN")
    if env2:
        cand = os.path.join(env2, "openocd.exe")
        if os.path.isfile(cand):
            return cand
        if os.path.isfile(env2):
            return env2
    found = None
    for d in OPENOCD_SEARCH:
        if any(ch in d for ch in "*["):
            import glob
            hits = glob.glob(os.path.join(d, "*", "bin", "openocd.exe"))
            if hits:
                found = hits[-1]  # 取最新版本目录
                break
        elif os.path.isdir(d):
            hits = _walk_find(d, depth=3)
            if hits:
                found = hits[0]
                break
    if found:
        return found
    from shutil import which
    w = which("openocd")
    return w or "openocd"


def _walk_find(root, depth=3):
    hits = []
    base = len(root.split(os.sep))
    for dirpath, dirnames, filenames in os.walk(root):
        if len(dirpath.split(os.sep)) - base >= depth:
            dirnames[:] = []
            continue
        if "openocd.exe" in filenames:
            hits.append(os.path.join(dirpath, "openocd.exe"))
    return hits


def kill_stale_openocd():
    """杀残留 openocd 进程（占探针导致下次 init 失败的头号根因）。返回杀掉的数量。"""
    n = 0
    try:
        out = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq openocd.exe", "/FO", "CSV", "/NH"],
            capture_output=True, text=True, timeout=15).stdout or ""
        pids = re.findall(r'"openocd\.exe","(\d+)"', out)
        for pid in pids:
            subprocess.run(["taskkill", "/F", "/PID", pid],
                           capture_output=True, text=True, timeout=15)
            n += 1
    except Exception as e:  # noqa: BLE001 — 清理失败不阻塞主流程
        print(f"[warn] 清理残留进程失败：{e}")
    return n


def build_cfg_args(target, cfg):
    """组装 -f 配置参数：--cfg 优先；否则 interface/stlink.cfg + target/<t>.cfg。"""
    if cfg:
        return ["-f", cfg]
    if not target:
        raise FlashError("target-missing",
                         "未指定 --target 也未指定 --cfg",
                         "如 --target stm32f1x 或 --cfg board/xxx.cfg")
    return ["-f", "interface/stlink.cfg", "-f", f"target/{target}.cfg"]


def run_openocd(openocd, args, timeout=120):
    """执行 openocd，返回 (rc, 输出全文)。超时杀进程树。"""
    cmd = [openocd] + args
    try:
        p = subprocess.run(cmd, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=timeout)
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except FileNotFoundError:
        raise FlashError("openocd-missing", f"openocd 未找到：{openocd}",
                         "安装：xpm install @xpack-dev-tools/openocd@latest "
                         "后设环境变量 OPENOCD_BIN 指向其 bin 目录")
    except subprocess.TimeoutExpired:
        kill_stale_openocd()
        raise FlashError("command-timeout",
                         f"openocd 执行超时（{timeout}s），已清理残留进程",
                         "查探针连接/供电；重跑前先 --kill 清理")


def judge(rc, text, action):
    """以输出内容为准判成败（不信 exit code——假烧录教训）。"""
    if FAIL_RE.search(text):
        raise FlashError(f"{action}-fail", f"{action} 失败：\n{text.strip()[-800:]}",
                         "看 FAIL 行；verify-fail = 写入未生效（换 UV4 -f 通道）；"
                         "init 失败先 --kill 清残留再查探针")
    if rc != 0 and not SUCCESS_RE.search(text):
        raise FlashError(f"{action}-fail",
                         f"{action} 失败（rc={rc}）：\n{text.strip()[-800:]}",
                         "同上")
    if not SUCCESS_RE.search(text):
        raise FlashError("output-ambiguous",
                         f"输出无成功标志也无失败标志，按失败处理：\n{text.strip()[-800:]}",
                         "人工核对输出；必要时读回 0x08000000 与 hex 首字节比对")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--flash", metavar="HEX", help="烧录该 hex（自动 verify+reset）")
    g.add_argument("--reset", action="store_true", help="仅复位运行（不烧录）")
    g.add_argument("--kill", action="store_true", help="仅清理残留 openocd 进程")
    ap.add_argument("--target", help="OpenOCD target cfg 名（如 stm32f1x/stm32f4x）")
    ap.add_argument("--cfg", help="完整 OpenOCD 配置文件（优先于 --target）")
    ap.add_argument("--no-run", dest="no_run", action="store_true",
                    help="烧录+校验但不复位（等价 program verify exit）")
    ap.add_argument("--serial", help="多探针时指定适配器序列号")
    ap.add_argument("--speed", type=int, default=None,
                    help="adapter speed kHz（如 1000）")
    args = ap.parse_args()

    try:
        if args.kill:
            n = kill_stale_openocd()
            print(f"已清理 {n} 个残留 openocd 进程。[FAILURE: none]")
            return 0

        openocd = find_openocd()

        # 预清理：残留 openocd 占探针是"无法烧录"头号根因，动作前先清一次
        n = kill_stale_openocd()
        if n:
            print(f"[pre-clean] 已清理 {n} 个残留 openocd 进程")

        pre = []
        if args.serial:
            pre += ["-c", f"adapter serial {args.serial}"]
        if args.speed:
            pre += ["-c", f"adapter speed {args.speed}"]

        if args.reset:
            cfg = build_cfg_args(args.target, args.cfg)
            print(f"复位目标（{openocd}）…")
            rc, text = run_openocd(
                openocd, cfg + pre + ["-c", "init", "-c", "reset run", "-c", "exit"])
            judge(rc, text, "reset")
            print(text.strip()[-400:])
            print("已复位运行。[FAILURE: none]")
            return 0

        if args.flash:
            from pathlib import Path
            p = Path(args.flash)
            if not p.is_file():
                raise FlashError("artifact-missing", f"hex 不存在：{p}",
                                 "先用 keil_build.py 编译生成 hex 再烧录")
            cfg = build_cfg_args(args.target, args.cfg)
            tail = ["verify", "exit"] if args.no_run else ["verify", "reset", "exit"]
            # 文件名必须正斜杠+内嵌引号：Tcl 会把双引号内的反斜杠当转义吞掉
            # （"E:\a\b.hex"→"Eab.hex"，2026-08-22 实测）；引号兜空格路径
            prog = f'program "{p.as_posix()}" {" ".join(tail)}'
            print(f"烧录 {p}（program+verify+{'-' if args.no_run else 'reset '}exit 一体）…")
            rc, text = run_openocd(openocd, cfg + pre + ["-c", prog])
            judge(rc, text, "flash")
            print(text.strip()[-600:])
            tail_msg = "已烧录+校验（未复位）" if args.no_run else "已烧录+校验+复位运行"
            print(f"完成：{tail_msg}  [FAILURE: none]")
            return 0
    except FlashError as e:
        print(f"\n[FAILURE: {e.category}] {e}\n  排查: {e.detail}", file=sys.stderr)
        return 1
    except Exception as e:  # noqa: BLE001 — 未预期异常兜底
        print(f"\n[FAILURE: unknown] 未预期异常：{e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
