#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""telemetry_csv.py — 板端遥测日志 → 仿真对照 CSV（cl Skill /cl run sim 校准回灌）

从 capture.log（serial_monitor.py 产物）提取 `[TELE]` 行，转成
Simulation/telemetry.csv 供 run_compare_telemetry.m 读取（列名严格对齐：
t,pos,vel,cur,mode,state，t=秒，pos=细分步 int32，vel=细分步/s，cur=mA）。

板端打印格式（固件侧约定，全 %d 定点，发送在主循环）:
    [TELE] <t_ms>,<pos>,<vel>,<cur>,<mode>,<state>
    例: [TELE] 1234,12800,51200,1500,1,3

用法:
    python tools/telemetry_csv.py <capture.log> [--out telemetry.csv]

输出: 统计行数/时间跨度/首尾各 3 行；无 [TELE] 行 → 报错退出（提示先固件加打印）。

依赖: 无（纯标准库）
"""

import argparse
import os
import re
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass

_TELE_RE = re.compile(
    r"\[TELE\]\s*([-\d]+)\s*,\s*([-\d]+)\s*,\s*([-\d]+)\s*,\s*([-\d]+)\s*,\s*(\d+)\s*,\s*(\d+)"
)


def main():
    ap = argparse.ArgumentParser(description="遥测 [TELE] 行 → CSV")
    ap.add_argument("log", help="capture.log 路径")
    ap.add_argument("--out", default=None, help="输出 CSV 路径（默认 <log 同目录>/telemetry.csv）")
    args = ap.parse_args()

    if not os.path.isfile(args.log):
        sys.exit("[cl] 日志文件不存在: %s" % args.log)
    out = args.out or os.path.join(os.path.dirname(os.path.abspath(args.log)), "telemetry.csv")

    rows = []
    skipped = 0
    with open(args.log, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            m = _TELE_RE.search(line)
            if not m:
                continue
            t_ms, pos, vel, cur, mode, state = (int(g) for g in m.groups())
            rows.append((t_ms / 1000.0, pos, vel, cur, mode, state))

    if not rows:
        sys.exit("[cl] 日志中无 [TELE] 行（固件是否已加 100Hz 遥测打印？）")

    with open(out, "w", encoding="utf-8", newline="") as f:
        f.write("t,pos,vel,cur,mode,state\n")
        for t, pos, vel, cur, mode, state in rows:
            f.write("%.3f,%d,%d,%d,%d,%d\n" % (t, pos, vel, cur, mode, state))

    span = rows[-1][0] - rows[0][0]
    print("[cl] 遥测 CSV: %d 行, 时间跨度 %.1fs, 输出 %s" % (len(rows), span, out))
    print("[cl] 首 3 行:")
    for r in rows[:3]:
        print("  " + ",".join(str(v) for v in r))
    print("[cl] 尾 3 行:")
    for r in rows[-3:]:
        print("  " + ",".join(str(v) for v in r))
    if len(rows) < 100:
        print("[cl] 提示: 仅 %d 行（<100），建议采集更长时间段（100Hz×10s=1000 行）" % len(rows))


if __name__ == "__main__":
    main()
