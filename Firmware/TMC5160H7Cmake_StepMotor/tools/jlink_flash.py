#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""cl skill 的 J-Link 一体烧录工具：烧录 + 校验 + 复位运行一次完成。

依赖：J-Link 软件包（JLink.exe）。
安装：从 https://www.segger.com/downloads/jlink/ 下载安装 J-Link 软件包。

用法：
    python jlink_flash.py --flash <hex路径> --target stm32h750vb
    python jlink_flash.py --flash <hex路径> --no-run        # 烧录但不复位
    python jlink_flash.py --reset --target stm32h750vb     # 仅复位运行
    python jlink_flash.py --kill                            # 仅清理残留 J-Link 进程

目标型号（--target，映射 J-Link device 名）：
    stm32h750vb / stm32h750 / stm32h743 / stm32f103c8 等
    不确定时用 --device 直接给 J-Link device 名。

退出码 0 = 成功；非 0 = 失败。
"""

import argparse
import os
import re
import subprocess
import sys
import tempfile
import time

# J-Link 安装位置（按序探测）
JLINK_SEARCH = [
    r"C:\Program Files\SEGGER\JLink_V948",
    r"C:\Program Files\SEGGER\JLink_V932",
    r"C:\Program Files\SEGGER\JLink",
    r"C:\Program Files (x86)\SEGGER\JLink",
    os.path.expandvars(r"%LOCALAPPDATA%\SEGGER\JLink"),
]

# J-Link device 名映射（CLI --target → J-Link device 名）
DEVICE_MAP = {
    "stm32h750vb": "STM32H750VB",
    "stm32h750": "STM32H750VB",
    "stm32h743": "STM32H743XI",
    "stm32h743vi": "STM32H743VI",
    "stm32f103c8": "STM32F103C8",
    "stm32f103rb": "STM32F103RB",
    "stm32f407vg": "STM32F407VG",
    "stm32f429zi": "STM32F429ZI",
    "stm32l476rg": "STM32L476RG",
    "stm32g071rb": "STM32G071RB",
}

# STM32H750 默认参数
DEFAULT_FLASH_BASE = 0x08000000
DEFAULT_FLASH_SIZE = 0x20000  # 128 KB


class FlashError(Exception):
    def __init__(self, category, message, detail=""):
        super().__init__(message)
        self.category = category
        self.detail = detail


def find_jlink():
    """定位 JLink.exe：env JLINK_BIN > env JLINK > PATH > 常见目录。"""
    env1 = os.environ.get("JLINK_BIN")
    if env1:
        cand = os.path.join(env1, "JLink.exe")
        if os.path.isfile(cand):
            return cand
        if os.path.isfile(env1):
            return env1
    env2 = os.environ.get("JLINK")
    if env2:
        cand = os.path.join(env2, "JLink.exe")
        if os.path.isfile(cand):
            return cand
        if os.path.isfile(env2):
            return env2
    for d in JLINK_SEARCH:
        cand = os.path.join(d, "JLink.exe")
        if os.path.isfile(cand):
            return cand
    from shutil import which
    w = which("JLink.exe") or which("jlink")
    return w or "JLink.exe"


def kill_stale_jlink():
    """杀残留 J-Link 进程。返回杀掉的数量。"""
    n = 0
    try:
        out = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq JLink.exe", "/FO", "CSV", "/NH"],
            capture_output=True, text=True, timeout=15).stdout or ""
        pids = re.findall(r'"JLink\.exe","(\d+)"', out)
        for pid in pids:
            subprocess.run(["taskkill", "/F", "/PID", pid],
                           capture_output=True, text=True, timeout=15)
            n += 1
        # 也清理 JLinkGDBServerCL
        out2 = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq JLinkGDBServerCL.exe", "/FO", "CSV", "/NH"],
            capture_output=True, text=True, timeout=15).stdout or ""
        pids2 = re.findall(r'"JLinkGDBServerCL\.exe","(\d+)"', out2)
        for pid in pids2:
            subprocess.run(["taskkill", "/F", "/PID", pid],
                           capture_output=True, text=True, timeout=15)
            n += 1
    except Exception as e:
        print(f"[warn] 清理残留进程失败：{e}")
    return n


def resolve_device(target, device):
    """解析 J-Link device 名。"""
    if device:
        return device
    if target:
        key = target.lower().replace("-", "").replace("_", "")
        if key in DEVICE_MAP:
            return DEVICE_MAP[key]
        return target  # 直接透传给 J-Link
    raise FlashError("target-missing",
                     "未指定 --target 也未指定 --device",
                     "如 --target stm32h750vb 或 --device STM32H750VB")


def run_jlink(jlink_exe, cmd_file, timeout=60):
    """执行 J-Link Commander，返回 (rc, 输出全文)。"""
    cmd = [jlink_exe, "-CommandFile", cmd_file, "-nogui", "1"]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=timeout)
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except FileNotFoundError:
        raise FlashError("jlink-missing", f"JLink.exe 未找到：{jlink_exe}",
                         "安装 J-Link 软件包后设环境变量 JLINK_BIN 指向其 bin 目录")
    except subprocess.TimeoutExpired:
        kill_stale_jlink()
        raise FlashError("command-timeout",
                         f"J-Link 执行超时（{timeout}s），已清理残留进程",
                         "查探针连接/供电；重跑前先 --kill 清理")


def judge(rc, text, action):
    """判读 J-Link 输出。"""
    # J-Link 成功标志
    ok_patterns = re.compile(
        r"(O\.K\.|Downloaded|Verified|J-Link.*connected|Ready.*passed|"
        r"Flash download.*completed|Programming.*completed|"
        r"J-Link .* Flash|Firmware.*download|RAMSizeProbe)",
        re.IGNORECASE)
    fail_patterns = re.compile(
        r"(ERROR|FAILED|Cannot|Could not|Not found|Timeout|"
        r"No connection|Invalid|Error)",
        re.IGNORECASE)

    # 先检查失败（即使也有成功标志，ERROR 优先）
    if fail_patterns.search(text):
        raise FlashError(f"{action}-fail", f"{action} 失败：\n{text.strip()[-800:]}",
                         "看 ERROR 行；先 --kill 清残留再查探针连接")

    if rc != 0:
        raise FlashError(f"{action}-fail",
                         f"{action} 失败（rc={rc}）：\n{text.strip()[-800:]}",
                         "同上")

    if not ok_patterns.search(text):
        # J-Link 输出格式多变，尝试宽松判断
        if "Script processing completed" in text:
            return  # 命令文件正常执行完毕
        raise FlashError("output-ambiguous",
                         f"输出无明确成功/失败标志：\n{text.strip()[-800:]}",
                         "人工核对输出")


def build_cmd_file(device, hex_file=None, flash_base=DEFAULT_FLASH_BASE,
                   flash_size=DEFAULT_FLASH_SIZE, speed=4000, no_run=False,
                   reset_only=False):
    """生成 J-Link Commander 命令文件。"""
    lines = [
        "si SWD",
        f"speed {speed}",
        f"device {device}",
        "r",  # Reset
        "h",  # Halt
    ]

    if reset_only:
        lines.append("g")  # Go (run)
        lines.append("exit")
        return "\n".join(lines)

    if hex_file:
        # 清除整片 Flash（可选，注释掉以加速）
        # lines.append(f"erase {hex_file}")
        lines.append(f"loadfile {hex_file} {flash_base:#x}")
        lines.append("r")  # Reset after load
        if not no_run:
            lines.append("g")  # Go (run)

    lines.append("exit")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--flash", metavar="HEX", help="烧录该 hex（自动 reset+run）")
    g.add_argument("--reset", action="store_true", help="仅复位运行（不烧录）")
    g.add_argument("--kill", action="store_true", help="仅清理残留 J-Link 进程")
    ap.add_argument("--target", help="目标型号（如 stm32h750vb），映射 J-Link device 名")
    ap.add_argument("--device", help="J-Link device 名（优先于 --target）")
    ap.add_argument("--speed", type=int, default=4000,
                    help="SWD 速度 kHz（默认 4000）")
    ap.add_argument("--no-run", dest="no_run", action="store_true",
                    help="烧录但不复位运行")
    ap.add_argument("--flash-base", type=lambda x: int(x, 0), default=DEFAULT_FLASH_BASE,
                    help=f"Flash 起始地址（默认 {DEFAULT_FLASH_BASE:#x}）")
    ap.add_argument("--flash-size", type=lambda x: int(x, 0), default=DEFAULT_FLASH_SIZE,
                    help=f"Flash 大小（默认 {DEFAULT_FLASH_SIZE:#x}）")
    ap.add_argument("--serial", help="多探针时指定 J-Link 序列号")
    args = ap.parse_args()

    try:
        if args.kill:
            n = kill_stale_jlink()
            print(f"已清理 {n} 个残留 J-Link 进程。[FAILURE: none]")
            return 0

        jlink_exe = find_jlink()

        # 预清理
        n = kill_stale_jlink()
        if n:
            print(f"[pre-clean] 已清理 {n} 个残留 J-Link 进程")

        device = resolve_device(args.target, args.device)

        # 构建命令文件
        hex_file = None
        if args.flash:
            from pathlib import Path
            p = Path(args.flash)
            if not p.is_file():
                raise FlashError("artifact-missing", f"hex 不存在：{p}",
                                 "先用 cmake --build 编译生成 hex 再烧录")
            hex_file = p.as_posix()

        cmd_content = build_cmd_file(
            device, hex_file, args.flash_base, args.flash_size,
            args.speed, args.no_run, args.reset)

        # 写入临时命令文件
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jlink",
                                          delete=False, encoding="utf-8") as f:
            f.write(cmd_content)
            cmd_file = f.name

        try:
            if args.reset:
                print(f"复位目标（{jlink_exe}, device={device}）…")
                rc, text = run_jlink(jlink_exe, cmd_file)
                judge(rc, text, "reset")
                print(text.strip()[-400:])
                print("已复位运行。[FAILURE: none]")
                return 0

            if args.flash:
                print(f"烧录 {hex_file}（device={device}, SWD={args.speed}kHz）…")
                rc, text = run_jlink(jlink_exe, cmd_file)
                judge(rc, text, "flash")
                print(text.strip()[-600:])
                tail_msg = "已烧录（未复位）" if args.no_run else "已烧录+复位运行"
                print(f"完成：{tail_msg}  [FAILURE: none]")
                return 0
        finally:
            try:
                os.unlink(cmd_file)
            except OSError:
                pass

    except FlashError as e:
        print(f"\n[FAILURE: {e.category}] {e}\n  排查: {e.detail}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"\n[FAILURE: unknown] 未预期异常：{e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
