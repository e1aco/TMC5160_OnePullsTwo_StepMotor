#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""[已废弃] cl skill 的烧录 + 复位工具（pyocd）。请改用 openocd_flash.py。

废弃原因：pyocd 缺 Flash 算法时报告成功但不写入（假烧录），reset 会弄挂 ST-Link VCP。
新工具 openocd_flash.py 走 program+verify+reset+exit 一体模式，verify 拦假烧录、原子复位、退出释放探针。

保留本文件仅用于：--list 列出探针（应急诊断）。

失败分级（结构化输出，供 AI 闭环定位，不再靠猜全文）：
    FAILURE: <category> 一行 + 引导行 -- 类别见 main() 底部分类表。

依赖：pip install pyocd ；已有 ST-Link 探针（pyocd list 可见）。
chippack：目标 STM32F1/Cortex-M，pyocd 0.45.1 内置 stm32f103 等 target。

用法：
    python flash.py --flash <hex路径>            # 烧录该 hex，烧完自动复位运行
    python flash.py --flash --reset              # 同上（--reset 是默认，可省略）
    python flash.py --reset                      # 仅复位运行已烧固件（不烧录）
    python flash.py --list                       # 仅列出探针，不动作
    python flash.py --flash <hex> --target <型号>  # 指定目标型号（如 stm32f407vg）

    # 烧录后不自动运行（停在复位态，便于外部捕获复位时间点）：
    python flash.py --flash <hex> --no-run

注意: 烧录后自动复位走 pyocd reset——若调试回传通道是 ST-Link VCP 虚拟串口，
pyocd reset 可能把该串口弄成半死态（监听 OSError(22) 死亡、遥测全无，需断电重启恢复）。
该场景请用 --no-run 烧录后断电重启，或改用 Keil UV4 -j0 烧录（见 details/flash.md「复位方式门禁」）。

退出码 0 = 成功；非 0 = 失败（含探针缺失/烧录失败/复位失败）。
"""

import argparse
import subprocess
import sys

PYOCD = "pyocd"


class FlashError(Exception):
    """烧录相关失败，category 为结构化分类。"""
    def __init__(self, category, message, detail=""):
        super().__init__(message)
        self.category = category
        self.detail = detail


def run_pyocd(args, check=True):
    """执行 python -m pyocd <>，返回 (code, text)。不设 check 时由调用方判失败。"""
    cmd = [sys.executable, "-m", PYOCD] + args
    try:
        p = subprocess.run(cmd, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=120)
    except FileNotFoundError as e:
        raise FlashError("pyocd-missing",
                         f"pyocd 未找到：{e}", "pip install pyocd")
    except subprocess.TimeoutExpired:
        raise FlashError("command-timeout", "pyocd 命令执行超时（120s）")
    text = (p.stdout or "") + (p.stderr or "")
    if check and p.returncode != 0:
        raise FlashError("pyocd-error",
                         f"pyocd 命令失败（rc={p.returncode}）：\n{text}")
    return p.returncode, text


def list_probes():
    """返回探测到的探针列表。"""
    return run_pyocd(["list"])[1]


def probes_present():
    """pyocd list 是否有探针（输出含优 'ST-LINK' 等字样或非空）。"""
    rc, text = run_pyocd(["list"], check=False)
    s = (text or "").strip()
    return rc == 0 and bool(s)


def resolve_target(target):
    """目标型号：未指定时按 Cortex-M 兜底；返回 pyocd --target 参数。"""
    return target or "cortex_m"


def reset_target(ext_args, target):
    """复位目标并继续运行。返回输出文本。"""
    rc, text = run_pyocd(["commander", "--target", target] + ext_args +
                         ["-c", "reset", "-c", "go", "-c", "exit"])
    return rc, text


def flash_hex(hex_path, target, do_run=True):
    """烧录 hex -> 按需复位运行；失败抛 FlashError(带分类)。"""
    rc, out = run_pyocd(["load", hex_path, "--target", target], check=False)
    if rc != 0:
        raise FlashError("flash-fail",
                         f"烧录失败：\n{out}",
                         "检查目标型号 --target 是否匹配 / hex 是否最新编译")
    if not do_run:
        return out
    rc, rst = reset_target([], target)
    if rc != 0:
        raise FlashError("reset-fail",
                         f"烧录成功但复位运行失败：\n{rst}",
                         "检查目标型号与复位电路（见规格第七节）")
    return out + "\n" + rst


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--flash", metavar="HEX", help="烧录该 hex 文件")
    g.add_argument("--reset", action="store_true", help="仅复位目标（不烧录）")
    g.add_argument("--list", action="store_true", help="仅列出探针")
    ap.add_argument("--no-run", dest="no_run", action="store_true",
                    help="烧录后不自动复位运行")
    ap.add_argument("--target", help="目标型号（如 stm32f407vg；缺省 cortex_m）")
    args = ap.parse_args()

    try:
        target = resolve_target(args.target)

        if args.list:
            print(list_probes())
            return 0

        # 探针门禁：动作前先确认有探针，缺失直接给分类
        if not probes_present():
            raise FlashError(
                "probe-missing",
                "未检测到 ST-Link 探针（pyocd list 为空）",
                "检查: 探针 USB 连接 / ST-Link 驱动 / 设备管理器；烧录用 require.md 固化通道")

        if args.reset:
            print("复位目标…")
            rc, txt = reset_target([], target)
            if rc != 0:
                raise FlashError("reset-fail", f"复位失败：\n{txt}",
                                 "目标型号 / 复位电路 / 探针供电")
            print(txt)
            print("已复位运行。")
            return 0

        if args.flash:
            from pathlib import Path
            p = Path(args.flash)
            if not p.is_file():
                raise FlashError("artifact-missing",
                                 f"hex 不存在：{p}",
                                 "先用 keil_build.py 编译生成 hex 再烧录")
            print(f"烧录 {p} (target={target})…")
            out = flash_hex(str(p), target, do_run=not args.no_run)
            print(out)
            tail = "已复位运行" if not args.no_run else "已烧录（未复位）"
            print(f"完成：{tail}  [FAILURE: none]")
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