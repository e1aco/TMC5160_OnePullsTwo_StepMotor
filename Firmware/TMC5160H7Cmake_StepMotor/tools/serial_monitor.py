#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""serial_monitor.py — 串口读取工具（cl Skill 物理闭环，常驻监听唯一方案）

物理闭环中 "烧录 → 常驻监听 → 判据验证" 环节使用（见 `details/run.md`）。
端口由本进程独占；--timestamps 每行打客户端时间戳；--output 写盘。

判据监听（--watch，闭环默认）：热循环的"看日志"交给进程内确定性 watcher
（tools/_watcher.py），逐行匹配判据 → 原子写 .cl/capture/status（≤10 行）。
AI 主代理每轮只读 status 做决策，不碰日志原文、不数秒、不轮询。
配合 --boot（启动横幅，确认新固件生效）+ flash marker 握手（烧录后数据起算）。

写盘策略：进程启动时以 'wb' 覆盖旧 capture.log（新闭环重新开始），
运行中每行到达即追加写盘 + flush，可实时读到增量。

可选便捷交互（供 /cl code 手动调试或人工观察时使用，不影响闭环）：
  --list                列出可用串口（含设备类型提示）
  --auto                自动选择最可能的串口（CH340/CP210 > ST-Link > USB）
  --duration <秒>       有界监听 N 秒后自动收尾（默认 0 = 常驻到 Ctrl+C）

依赖: pyserial (pip install pyserial)

用法示例:
    python serial_monitor.py --list
    python serial_monitor.py --auto --baud 115200 --duration 0 --timestamps --output .cl/capture/capture.log
    python serial_monitor.py --port COM5 --duration 0 --timestamps --output .cl/capture/capture.log
    # 闭环判据监听（run.md 默认形态）：
    python serial_monitor.py --port COM5 --baud 115200 --timestamps \
        --output .cl/capture/capture.log \
        --watch "boot||初始化失败||ERROR||ready" --boot "boot" \
        --status .cl/capture/status --marker .cl/capture/flash_marker
"""

import argparse
import os
import re
import sys
import time

from _watcher import Watcher, daemon_entry

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass
try:
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass


def list_serial_ports():
    """列出可用串口（返回端口名列表）。"""
    from serial.tools import list_ports
    ports = list_ports.comports()
    if not ports:
        print("[cl] 未检测到可用串口")
        return []
    names = []
    print("[cl] 可用串口：")
    for i, p in enumerate(ports, 1):
        label = _port_label(p.description)
        names.append(p.device)
        print(f"  {i}. {p.device}  {p.description}  {label or ''}".rstrip())
    return names


_CANONICAL = re.compile(r"\033\[[0-9;]*m")
_LEADING_JUNK = re.compile(r"^[\ufffd\x00-\x1f]+")


def _port_label(desc):
    d = desc.upper()
    if "CH340" in d or "CH341" in d:
        return "[USB 转串口]"
    if "CMSIS-DAP" in d or "DAP-LINK" in d:
        return "[CMSIS-DAP]"
    if "STLINK" in d or "ST-LINK" in d:
        return "[ST-Link]"
    if "J-LINK" in d or "JLINK" in d:
        return "[J-Link]"
    if "CP210" in d:
        return "[CP210x]"
    return ""


def _port_priority(desc):
    d = desc.upper()
    if "CH340" in d or "CH341" in d or "CP210" in d:
        return 1
    if "CMSIS-DAP" in d or "DAPLINK" in d or "STLINK" in d or "J-LINK" in d:
        return 2
    if "USB" in d:
        return 3
    return 9


def auto_port():
    """自动选口：优先 CH340/CP210（USB 转串口），其次调试器虚拟串口。"""
    from serial.tools import list_ports
    ports = list_ports.comports()
    if not ports:
        return None
    ports.sort(key=lambda p: (_port_priority(p.description), p.device))
    return ports[0].device


def parse_args():
    p = argparse.ArgumentParser(description="串口读取（cl Skill 物理闭环，常驻监听）")
    p.add_argument("--port", help="串口，如 COM5 或 /dev/ttyUSB0")
    p.add_argument("--auto", action="store_true", help="自动选择最可能的串口")
    p.add_argument("--list", action="store_true", help="仅列出可用串口")
    p.add_argument("--baud", type=int, default=115200, help="波特率，默认 115200")
    p.add_argument("--duration", type=float, default=0.0,
                   help="监听秒数，0=常驻直到 Ctrl+C（默认）；>0 为有界监听")
    p.add_argument("--output", help="可选：日志写盘（进程启动覆盖，运行中增量追加，供判读）")
    p.add_argument("--timestamps", action="store_true",
                   help="每行加客户端时间戳前缀 [HH:MM:SS.mmm]（判段用）")
    p.add_argument("--send", default=None,
                   help="打开串口后延迟 --send-delay 秒发送该字符串（发送完继续监听；供串口指令触发的任务用）")
    p.add_argument("--send-delay", type=float, default=0.0,
                   help="--send 的发送延迟秒数（默认 0；烧录后需等固件启动时用）")
    p.add_argument("--watch",
                   help="判据监听（确定性 watcher，闭环默认）：||分隔的 regex，命中写 status/hits.log，替代 AI 盯日志")
    p.add_argument("--status", default=".cl/capture/status",
                   help="status 文件路径（--watch 时使用），默认 .cl/capture/status")
    p.add_argument("--marker", default=".cl/capture/flash_marker",
                   help="flash marker 文件路径（--watch 时使用），默认 .cl/capture/flash_marker")
    p.add_argument("--boot",
                   help="启动横幅 regex（可选）：匹配到一行即 boots+1，AI 据此确认新固件生效")
    p.add_argument("--daemon", action="store_true",
                   help="后台守护模式（AI 闭环必用）：分离进程启动，stdout/stderr 重定向 "
                        "mon_out.log/mon_err.log，探活通过后调用方立即返回，绝不阻塞")
    return p.parse_args()


def _ts():
    return time.strftime("[%H:%M:%S.", time.localtime()) + "%03d]" % (
        int((time.time() % 1) * 1000))


def _emit(out_fh, data, ts_mode, partial, watcher):
    """把一段字节还原成文本行输出到 stdout 与 out_fh（增量写盘 + flush）。

    显示硬化：剥前导不可解码/控制字节、跳过空行。返回该段字节数（用于"无数据"判定）。
    """
    n = len(data)
    if ts_mode:
        partial.extend(data)
        while True:
            nl = partial.find(b"\n")
            if nl < 0:
                break
            chunk = partial[: nl + 1].rstrip(b"\r\n")
            text = _CANONICAL.sub("", chunk.decode("utf-8", errors="replace"))
            text = _LEADING_JUNK.sub("", text)
            if not text.strip():
                del partial[: nl + 1]
                continue
            ts = _ts()
            line = ts + " " + text + "\n"
            sys.stdout.write(line)
            sys.stdout.flush()
            if out_fh:
                out_fh.write(line)
                out_fh.flush()
            if watcher:
                watcher.on_line(text, "", ts)
            del partial[: nl + 1]
        if partial and watcher:
            watcher.warn_reconstruct(len(partial))
    else:
        text = _CANONICAL.sub("", data.decode("utf-8", errors="replace"))
        sys.stdout.write(text)
        sys.stdout.flush()
        if out_fh:
            out_fh.write(text)
            out_fh.flush()
    return n


def main():
    args = parse_args()
    rc = daemon_entry(sys.argv, args.duration, bool(args.watch),
                      args.output, args.status)
    if rc is not None:
        return rc
    if args.list:
        list_serial_ports()
        return 0

    if args.auto:
        picked = auto_port()
        if not picked:
            sys.stderr.write("[cl] 无法自动检测串口，请用 --list 查看后 --port 指定\n")
            return 1
        args.port = picked
        print(f"[cl] 自动选择串口: {args.port}")

    if not args.port:
        sys.stderr.write("[cl] 缺少 --port，或加 --auto 自动选择；--list 可查看\n")
        return 1

    try:
        import serial  # pyserial
    except ImportError:
        sys.stderr.write("[cl] 缺少 pyserial，请先安装: pip install pyserial\n")
        return 1

    watcher = None
    if args.watch:
        if not args.timestamps:
            print("[cl] --watch 需要行级匹配，已自动启用 --timestamps")
            args.timestamps = True
        watcher = Watcher(
            patterns=[p.strip() for p in args.watch.split("||") if p.strip()],
            boot_pattern=args.boot,
            status_path=args.status,
            marker_path=args.marker,
            hits_path=os.path.join(os.path.dirname(args.status) or ".", "hits.log"),
        )

    try:
        port = serial.Serial(port=args.port, baudrate=args.baud, timeout=0.5)
    except Exception as exc:  # noqa: BLE001 — 串口打开失败，原因交用户
        sys.stderr.write(f"[cl] 无法打开串口 {args.port}: {exc}\n")
        sys.stderr.write("[cl] 检查: 设备是否连接 / 波特率是否匹配 / 端口是否被占用\n")
        return 1

    if args.send:
        try:
            if args.send_delay > 0:
                time.sleep(args.send_delay)
            port.write(args.send.encode())
            print(f"[cl] 已发送: {args.send!r}")
        except Exception as exc:  # noqa: BLE001 — 发送失败不阻塞监听
            sys.stderr.write(f"[cl] 发送失败: {exc}\n")

    mode = f"常驻 {args.duration}s 后收尾" if args.duration > 0 else "常驻直到 Ctrl+C"
    print(f"[cl] 监听 {args.port} @ {args.baud} baud "
          f"({mode}，{'带时间戳' if args.timestamps else '原始输出'})"
          f"{'，判据监听 -> status' if watcher else ''}\n")

    out_fh = None
    if args.output:
        out_fh = open(args.output, "w", encoding="utf-8")   # 覆盖旧文件：新闭环重新开始
        if args.timestamps:
            out_fh.write("# capture start\n")

    total = 0
    partial = bytearray()
    start = time.monotonic()
    try:
        while True:
            if args.duration > 0 and (time.monotonic() - start) >= args.duration:
                break
            if watcher:
                watcher.check_marker()
                watcher.write_status()
            data = port.read(port.in_waiting or 1)
            if not data:
                continue
            total += _emit(out_fh, data, args.timestamps, partial, watcher)
    except KeyboardInterrupt:
        print("\n[cl] 已中断")
    finally:
        if watcher:
            watcher.write_status(force=True)
        if out_fh:
            out_fh.close()
        port.close()

    if args.output:
        print(f"[cl] 已保存 {total} 字节到 {args.output}")

    if total == 0:
        sys.stderr.write(
            "[cl] 串口无数据 —— 可能原因: 未烧录/复位未触发/晶振异常/波特率不匹配\n"
            "     请按规格第七节异常处理检查硬件连接。\n")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
