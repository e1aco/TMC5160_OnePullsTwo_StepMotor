#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""can_monitor.py — CAN 调试回传读取工具（cl Skill 物理闭环，常驻监听唯一方案）

用于无串口、以 CAN 作为调试回传接口的板子。物理闭环中替代 serial_monitor.py：
"烧录 → 常驻监听 → 判据验证" 环节（见 `details/run.md`）。

协议（与 templates/debug_can_proto.md 一致）：
    板端把调试文本逐 8 字节切帧，用固定仲裁 ID 发出；
    每帧 data[0:dlc] 是文本的一截，监控端按接收顺序拼接还原。

判据监听（--watch，闭环默认）：热循环的"看日志"交给进程内确定性 watcher
（tools/_watcher.py），逐行匹配判据 → 原子写 .cl/capture/status（≤10 行）。
AI 主代理每轮只读 status 做决策，不碰日志原文、不数秒、不轮询。
配合 --boot（启动横幅，确认新固件生效）+ flash marker 握手（烧录后数据起算）。

写盘策略：进程启动覆盖旧文件，运行中逐行追加 + flush，时间戳一并写进文件。

可选便捷交互（供 /cl code 手动调试或人工观察时使用，不影响闭环）：
  --duration <秒>       有界监听 N 秒后自动收尾（默认 0 = 常驻到 Ctrl+C）

依赖: python-can + PEAK PCAN-USB 官方驱动
    pip install python-can
    （PCAN 驱动安装后即出现虚拟 COM: PEAK-..., 本工具经 pcan 后端访问总线）

用法示例:
    python can_monitor.py --channel PCAN_USBBUS1 --id 0x100 --duration 3
    python can_monitor.py --id 0 --timestamps --output capture.log
    python can_monitor.py --id 256 --extended --duration 0 --timestamps --output capture.log
    # --id 0 = 全监（不过滤，多 ID 分组还原，输出带 ID=0x... 前缀）
    # 闭环判据监听（run.md 默认形态）：
    python can_monitor.py --channel PCAN_USBBUS1 --id 0 --timestamps \
        --output .cl/capture/capture.log \
        --watch "boot||初始化失败||ERROR||OTPW" --boot "boot" \
        --status .cl/capture/status --marker .cl/capture/flash_marker
"""

import argparse
import os
import re
import sys
import time

from _watcher import Watcher, daemon_entry


def parse_args():
    p = argparse.ArgumentParser(description="读取 CAN 调试回传帧（cl Skill 物理闭环）")
    p.add_argument("--channel", default="PCAN_USBBUS1",
                   help="PCAN 通道，默认 PCAN_USBBUS1")
    p.add_argument("--id", type=lambda s: int(s, 0), default=0x100,
                   help="监听帧仲裁 ID（0x 十六进制或十进制）；0=全监不过滤（多 ID 分组还原，输出带 ID 前缀），默认 0x100")
    p.add_argument("--extended", action="store_true",
                   help="使用扩展帧 29 位 ID（默认标准帧 11 位；全监模式自动兼容两种）")
    p.add_argument("--duration", type=float, default=0.0,
                   help="监听秒数，0=常驻直到 Ctrl+C（默认）；>0 为有界监听")
    p.add_argument("--output", help="可选：将还原的原始字节追加写入此文件")
    p.add_argument("--timestamps", action="store_true",
                   help="每行加客户端时间戳前缀 [HH:MM:SS.mmm]（判段用）")
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


_CANONICAL = re.compile(r"\033\[[0-9;]*m")
_LEADING_JUNK = re.compile(r"^[\ufffd\x00-\x1f]+")


def _ts():
    return time.strftime("[%H:%M:%S.", time.localtime()) + "%03d]" % (
        int((time.time() % 1) * 1000))


def _emit(out_fh, data, ts_mode, partials, arb_id, all_mode, watcher):
    """把一帧/一段字节还原成文本输出到 stdout 与 out_fh（增量写盘 + flush）。

    CAN 按分帧还原：跨帧的"未成行残段"由 per-ID partial 累积拼接。
    全监模式（all_mode）按仲裁 ID 分组，每行输出 `ID=0x...` 前缀；单 ID 模式保持原格式。
    显示硬化：剥前导不可解码/控制字节、跳过空行（板端多余的 \\r\\n 帧）。
    返回本段字节数。
    """
    partial = partials.setdefault(arb_id, bytearray())
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
            idpre = f"ID=0x{arb_id:X} " if all_mode else ""
            if not text.strip():
                del partial[: nl + 1]
                continue
            ts = _ts()
            line = ts + " " + idpre + text + "\n"
            sys.stdout.write(line)
            sys.stdout.flush()
            if out_fh:
                out_fh.write(line)
                out_fh.flush()
            if watcher:
                watcher.on_line(text, idpre, ts)
            del partial[: nl + 1]
        if partial and watcher:
            watcher.warn_reconstruct(len(partial))
    else:
        text = _CANONICAL.sub("", data.decode("utf-8", errors="replace"))
        idpre = f"ID=0x{arb_id:X} " if all_mode else ""
        sys.stdout.write(idpre + text)
        sys.stdout.flush()
        if out_fh:
            out_fh.write(idpre + text)
            out_fh.flush()
    return n


def main():
    args = parse_args()
    rc = daemon_entry(sys.argv, args.duration, bool(args.watch),
                      args.output, args.status)
    if rc is not None:
        return rc
    try:
        import can  # python-can
    except ImportError:
        sys.stderr.write("[cl] 缺少 python-can，请先安装: pip install python-can\n")
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
        bus = can.Bus(interface="pcan", channel=args.channel, receive_own_messages=False)
    except Exception as exc:  # noqa: BLE001 — 打开失败原因交给用户
        sys.stderr.write(f"[cl] 无法打开 PCAN 通道 {args.channel}: {exc}\n")
        sys.stderr.write(
            "[cl] 检查: PEAK 驱动已装 / 设备已连接 / 波特率与板端一致（PCAN 驱动侧配置）\n")
        return 1

    all_mode = (args.id == 0)
    mode = f"常驻 {args.duration}s 后收尾" if args.duration > 0 else "常驻直到 Ctrl+C"
    scope = "全部帧（多 ID 分组）" if all_mode else f"帧 ID={'0x%X' % args.id}"
    print(f"[cl] 监听 {args.channel} {scope}"
          f"{'(ext)' if args.extended else ''} "
          f"({mode}，{'带时间戳' if args.timestamps else '原始输出'})"
          f"{'，判据监听 -> status' if watcher else ''}\n")

    out_fh = None
    if args.output:
        out_fh = open(args.output, "w", encoding="utf-8")  # 覆盖旧文件：新闭环重新开始
        if args.timestamps:
            out_fh.write("# capture start\n")

    total = 0
    partials = {}
    start = time.monotonic()
    try:
        while True:
            if args.duration > 0 and (time.monotonic() - start) >= args.duration:
                break
            if watcher:
                watcher.check_marker()
                watcher.write_status()
            frame = bus.recv(timeout=0.2)
            if frame is None:
                continue
            if not all_mode:
                if frame.is_extended_id != args.extended:
                    continue
                if frame.arbitration_id != args.id:
                    continue
            payload = bytes(frame.data[:frame.dlc]) if frame.dlc else b""
            if not payload:
                continue
            total += _emit(out_fh, payload, args.timestamps, partials,
                           frame.arbitration_id, all_mode, watcher)
    except KeyboardInterrupt:
        print("\n[cl] 已中断")
    finally:
        if watcher:
            watcher.write_status(force=True)
        if out_fh:
            out_fh.close()
        bus.shutdown()

    if args.output:
        print(f"[cl] 已保存 {total} 字节到 {args.output}")

    if total == 0:
        sys.stderr.write(
            "[cl] CAN 无调试帧 —— 可能原因: 未烧录/复位未触发/帧 ID 不符/"
            "波特率不一致/总线无终端电阻\n"
            "     请按规格第七节处理检查硬件连接。\n")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
