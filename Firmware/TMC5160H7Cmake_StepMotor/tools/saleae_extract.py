#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""cl skill 的 Saleae Logic 2 采集与 CSV 解析工具（时序实测硬件通道）

用途（/cl tim 阶段2）：
    1. 探测本机 Saleae Logic 2 安装与 CLI 可用性
    2. 触发捕获（Logic 2 CLI）或提示用户手动在 Logic 2 里捕获
    3. 导出 CSV 并容错解析脉冲宽度（测试 IO 翻转 → 实测值）

用法：
    python saleae_extract.py detect                  # 探测 CLI 可用性并打印用法
    python saleae_extract.py capture --duration 5   # 触发捕获（CLI 不可用时打印手动步骤并退出 2）
    python saleae_extract.py parse <capture.csv> [--io <通道名/列号>] [--unit us|ns|s]
    python saleae_extract.py export <capture.bin> --csv <out.csv>   # CLI 导出 CSV
    python saleae_extract.py auto [--duration 5] [--channels 0] [--out dir] [--launch]
                                    # 自动化一条龙（logic2-automation API）：
                                    # 连接/启动 Logic 2 → 定时捕获 → 导出 digital.csv → 解析脉宽
                                    # --launch 自动启动软件（默认 E:\logic_any\Logic.exe），
                                    # 否则要求 Logic 2 已开且 Preferences 底部勾选 Automation Server

退出码：0 = 成功；2 = 环境/参数错误（AI 应降级提示用户手动）。

CSV 容错（针对不同版本/语言导出差异）：
    - 表头列名识别：time/Time/时间，Channel 0/Ch0/通道 0，自动匹配 --io
    - 时间单位识别：表头 [s]/[us]/[ns] 或数值量级推断
    - 分隔符嗅探：逗号 / 分号 / 制表符（csv.Sniffer）
    - 电平值容错：0/1、True/False、HIGH/LOW
    - 输出：脉宽序列（同 tag 多脉冲取最大值 = 最坏情况）
"""

import argparse
import csv
import os
import re
import shutil
import subprocess
import sys
import glob

CLI_NAMES = ["logic", "logic-cli", "Logic", "Logic.exe", "Logic.exe --cli"]
SALEAE_DIRS = [
    r"C:\Program Files\Saleae",
    r"C:\Program Files (x86)\Saleae",
    r"D:\Saleae",
    r"D:\Program Files\Saleae",
]
LOGIC2_APP_DEFAULT = r"E:\logic_any\Logic.exe"
LOGIC2_PORT = 10430

LEVEL_TRUE = {"1", "true", "high", "h", "on", "yes", "1.0"}
LEVEL_FALSE = {"0", "false", "low", "l", "off", "no", "0.0"}

COL_NAME_RE = re.compile(r"(?i)(time|时间|t\[|\bt\b)")
IO_NAME_RE = re.compile(r"(?i)(channel|ch|通道|io|引脚|pin)\s*[:_]?\s*(\d+)")


def find_cli():
    """探测 Saleae CLI：环境变量 SALEAE_CLI > 常见安装目录 cli 子目录 > PATH。"""
    env = os.environ.get("SALEAE_CLI")
    if env and os.path.isfile(env):
        return env
    for d in SALEAE_DIRS:
        if not os.path.isdir(d):
            continue
        for sub in ("", "cli", "Logic2", "Logic 2"):
            pat = os.path.join(d, "*", sub, "*")
            for f in glob.glob(pat):
                base = os.path.basename(f).lower()
                if base.startswith("logic") and (f.endswith(".exe") or os.path.isfile(f) and not os.path.isdir(f)):
                    return f
        pat = os.path.join(d, "*", "Logic.exe")
        for f in glob.glob(pat):
            if os.path.isfile(f):
                return f
    for name in ("logic", "logic-cli", "Logic", "Logic.exe"):
        found = shutil.which(name)
        if found:
            return found
    return None


def run_cli(cli, args, timeout=120):
    try:
        p = subprocess.run([cli] + args, capture_output=True, timeout=timeout)
        out = (p.stdout or b"").decode("utf-8", errors="replace")
        err = (p.stderr or b"").decode("utf-8", errors="replace")
        return p.returncode, out + "\n" + err
    except subprocess.TimeoutExpired:
        return -1, "CLI 超时（%ds）" % timeout
    except OSError as e:
        return -1, "CLI 启动失败: %s" % e


def detect():
    cli = find_cli()
    if not cli:
        print("SALEAE: 未找到 Saleae CLI（Logic 2 命令行工具）")
        print("SALEAE: 手动方式：Logic 2 软件中连接探头 → 捕获 → 导出 CSV → parse 回填")
        return 2
    code, out = run_cli(cli, ["--help"])
    print("SALEAE: CLI = %s" % cli)
    print("SALEAE: --help 退出码=%d, 前 20 行:" % code)
    for ln in out.splitlines()[:20]:
        print("  " + ln)
    return 0


def capture(cli, duration, output):
    if not cli:
        print("SALEAE: CLI 不可用，请手动操作：")
        print("SALEAE:   1. Logic 2 打开，探头接好测试 IO + GND")
        print("SALEAE:   2. 设置采样率 ≥ 10MHz，点击捕获，时长约 %ds" % duration)
        print("SALEAE:   3. 导出 CSV（File → Export）后运行 parse")
        return 2
    if not output:
        output = os.path.join(os.getcwd(), ".cl", "capture", "saleae_capture.bin")
    os.makedirs(os.path.dirname(output), exist_ok=True)
    code, out = run_cli(cli, ["capture", "--duration", str(duration), "--output", output])
    print(out)
    if code != 0:
        print("SALEAE: capture 失败（退出码 %d），请改用 Logic 2 手动捕获" % code)
        return 2
    print("SALEAE: 捕获完成: %s" % output)
    return 0


def export(cli, src, dst):
    if not cli:
        print("SALEAE: CLI 不可用，请在 Logic 2 中手动导出 CSV")
        return 2
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    code, out = run_cli(cli, ["export", src, "--csv", dst])
    print(out)
    if code != 0 or not os.path.isfile(dst):
        print("SALEAE: export 失败（退出码 %d）" % code)
        return 2
    print("SALEAE: CSV 已导出: %s" % dst)
    return 0


def sniff_dialect(path):
    """分隔符嗅探：逗号 / 分号 / 制表符，失败回退逗号。"""
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        sample = f.read(8192)
    for sep in (",", ";", "\t"):
        if sample.count(sep) > 0:
            return sep
    return ","


def detect_unit(header):
    """从表头识别时间单位：[s]/[us]/[ns]；否则按数值量级推断。"""
    m = re.search(r"\[\s*(\w+)\s*\]", header)
    if m:
        u = m.group(1).lower()
        if "ns" in u or u == "n":
            return 1e-9
        if "us" in u or u in ("u", "µs", "μs"):
            return 1e-6
        if "ms" in u or u == "m":
            return 1e-3
        return 1.0  # [s] 或其他一律按秒
    return None


def parse_csv(path, io, unit_scale):
    """解析 Saleae CSV：返回 [(脉冲宽度s, 起始时间s), ...]。io 为通道列号。"""
    sep = sniff_dialect(path)
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        reader = csv.reader(f, delimiter=sep)
        rows = list(reader)
    if not rows:
        return [], None, None
    header = rows[0]
    # 找时间列与 IO 列
    time_col = None
    for i, name in enumerate(header):
        if COL_NAME_RE.search(name):
            time_col = i
            break
    io_col = io
    if isinstance(io, str):
        for i, name in enumerate(header):
            if io.lower() in name.lower():
                io_col = i
                break
        else:
            return [], header, "未找到 IO 列（表头: %s）" % (header[:8],)
    if time_col is None:
        return [], header, "未找到时间列（表头: %s）" % (header[:8],)
    # 时间单位
    unit = detect_unit(header[time_col]) if header[time_col] else None
    scale = unit if unit is not None else (unit_scale or 1.0)
    # 电平序列
    events = []  # (t_s, level_bool)
    for r in rows[1:]:
        if len(r) <= max(time_col, io_col):
            continue
        t_str, lvl_str = r[time_col], r[io_col]
        try:
            t = float(t_str) * scale
        except ValueError:
            continue
        lvl = lvl_str.strip().lower()
        if lvl in LEVEL_TRUE:
            events.append((t, True))
        elif lvl in LEVEL_FALSE:
            events.append((t, False))
    if len(events) < 2:
        return [], header, "有效电平跳变不足（仅 %d 个事件）——检查 IO 是否翻转、采样率是否够" % len(events)
    # 脉宽：每对 相邻跳变 的宽度（翻转沿间隔）
    widths = []
    for (t0, _), (t1, _) in zip(events, events[1:]):
        w = t1 - t0
        if w > 0:
            widths.append((w, t0))
    if not widths:
        return [], header, "无正脉宽"
    return widths, header, None


def auto_capture(duration, channels, sample_rate, threshold, out_dir, launch_app, app_path):
    """logic2-automation 自动化：连接/启动 Logic 2 → 定时捕获 → 导出 digital.csv → 解析脉宽。

    返回 0 = 成功并打印脉宽；2 = 环境/设备错误（AI 降级提示手动）。
    """
    try:
        from saleae import automation
    except ImportError:
        print("SALEAE: 缺少 logic2-automation 包：pip install logic2-automation")
        return 2
    manager = None
    try:
        if launch_app:
            print("SALEAE: 启动 Logic 2: %s" % app_path)
            manager = automation.Manager.launch(application_path=app_path, port=LOGIC2_PORT)
        else:
            try:
                manager = automation.Manager.connect(port=LOGIC2_PORT)
            except Exception as e:
                print("SALEAE: 无法连接 Automation Server(port %d): %s" % (LOGIC2_PORT, e))
                print("SALEAE: 请在 Logic 2 菜单 Preferences 底部勾选 Automation Server（或加 --launch 自动启动）")
                return 2
        devices = manager.get_devices()
        real = [d for d in devices if getattr(d, "device_id", "demo") != "F4241"]
        if not real:
            print("SALEAE: 未检测到设备（Logic 2 中可见 %d 个模拟/设备）" % len(devices))
            print("SALEAE: 请确认 Saleae 已插 USB 且 Logic 2 能识别")
            return 2
        dev = real[0]
        print("SALEAE: 设备 = %s" % dev)
        os.makedirs(out_dir, exist_ok=True)

        def do_capture(sample_rate_hz, threshold_volts):
            dev_cfg = automation.LogicDeviceConfiguration(
                enabled_digital_channels=channels,
                digital_sample_rate=sample_rate_hz,
                digital_threshold_volts=threshold_volts,
            )
            cap_cfg = automation.CaptureConfiguration(
                capture_mode=automation.TimedCaptureMode(duration_seconds=duration))
            with manager.start_capture(
                    device_id=getattr(dev, "device_id", None),
                    device_configuration=dev_cfg,
                    capture_configuration=cap_cfg) as capture:
                print("SALEAE: 捕获 %gs 中…" % duration)
                capture.wait()
                capture.export_raw_data_csv(directory=out_dir, digital_channels=channels)

        sample_rate_use = sample_rate
        threshold_use = threshold
        for attempt in range(3):
            try:
                do_capture(sample_rate_use, threshold_use)
                break
            except Exception as e:
                msg = str(e)
                lower = msg.lower()
                if "threshold" in lower and threshold_use is not None:
                    print("SALEAE: 设备不支持阈值配置，降级重试（无 threshold）")
                    threshold_use = None
                    continue
                if "sample rate" in lower or "sampling rate" in lower:
                    rates = sorted({int(x) for x in re.findall(r'"digital"\s*:\s*(\d+)', msg)})
                    if rates:
                        pick = max(rates)
                        if pick == sample_rate_use:
                            raise
                        print("SALEAE: 采样率 %d 不受支持，改用设备允许最高 %d" % (sample_rate_use, pick))
                        sample_rate_use = pick
                        continue
                raise
        csv_path = os.path.join(out_dir, "digital.csv")
        if not os.path.isfile(csv_path):
            print("SALEAE: 导出失败（未生成 %s）" % csv_path)
            return 2
        print("SALEAE: 已导出: %s" % csv_path)
    except Exception as e:
        print("SALEAE: 自动化失败: %s（降级：Logic 2 手动捕获 → parse）" % e)
        return 2
    finally:
        if manager is not None:
            try:
                manager.close()
            except Exception:
                pass
    return parse_file(csv_path, "0", None)


def analyze_all_channels(path):
    """逐通道统计：toggles/min/max 脉宽。多通道 CSV（auto 全通道捕获）用。"""
    sep = sniff_dialect(path)
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        rows = list(csv.reader(f, delimiter=sep))
    if not rows:
        print("SALEAE: CSV 为空")
        return 1
    header = rows[0]
    try:
        tcol = header.index("Time [s]")
    except ValueError:
        for i, name in enumerate(header):
            if COL_NAME_RE.search(name):
                tcol = i
                break
        else:
            print("SALEAE: 未找到时间列")
            return 1
    unit = detect_unit(header[tcol]) if header[tcol] else 1.0
    scale = unit if unit else 1.0
    for ci in range(1, len(header)):
        evs = []
        for r in rows[1:]:
            if len(r) > ci and r[ci].strip().lower() in LEVEL_TRUE | LEVEL_FALSE:
                try:
                    t = float(r[tcol]) * scale
                except ValueError:
                    continue
                evs.append(t)
        widths = [b - a for a, b in zip(evs, evs[1:]) if b > a]
        if not widths:
            print("SALEAE: %s: 无翻转" % header[ci])
            continue
        print("SALEAE: %s: toggles=%d min=%.1f us max=%.1f us avg=%.1f us"
              % (header[ci], len(widths), min(widths) * 1e6, max(widths) * 1e6,
                 (sum(widths) / len(widths)) * 1e6))
    return 0


def parse_file(path, io, unit_scale):
    """解析 CSV 并打印脉宽（parse 子命令与 auto 共用）。"""
    widths, header, err = parse_csv(path, io, unit_scale)
    if err:
        print("SALEAE: 解析失败: %s" % err)
        return 2
    widths_sorted = sorted(widths)
    total = sum(w for w, _ in widths)
    print("SALEAE: 脉宽数=%d" % len(widths))
    for w, t0 in widths_sorted:
        print("  +%.6f us  @ t=%.6f s" % (w * 1e6, t0))
    if widths_sorted:
        mx = widths_sorted[-1][0]
        avg = total / len(widths)
        print("RESULT: max=%.6f us  avg=%.6f us  n=%d" % (mx * 1e6, avg * 1e6, len(widths)))
    return 0


def main():
    ap = argparse.ArgumentParser(description="Saleae Logic 2 采集与 CSV 解析")
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("detect", help="探测 CLI 可用性")
    p_cap = sub.add_parser("capture", help="触发捕获")
    p_cap.add_argument("--duration", type=float, default=5.0, help="捕获时长秒(默认5)")
    p_cap.add_argument("--output", default=None, help="输出文件")
    p_exp = sub.add_parser("export", help="导出 CSV")
    p_exp.add_argument("src", help="捕获文件")
    p_exp.add_argument("--csv", required=True, help="导出 CSV 路径")
    p_parse = sub.add_parser("parse", help="解析 CSV 脉宽")
    p_parse.add_argument("csv", help="CSV 路径")
    p_parse.add_argument("--io", default="0", help="IO 通道（列号或通道名，如 0 / Ch1）")
    p_parse.add_argument("--unit", default=None, choices=["s", "ms", "us", "ns"],
                         help="时间单位（表头无单位标注时用）")
    p_auto = sub.add_parser("auto", help="自动化一条龙（logic2-automation）")
    p_auto.add_argument("--duration", type=float, default=5.0, help="捕获时长秒(默认5)")
    p_auto.add_argument("--channels", default="0", help="数字通道，逗号分隔(默认0)")
    p_auto.add_argument("--sample-rate", type=int, default=10_000_000, help="采样率(默认10MSa/s)")
    p_auto.add_argument("--threshold", type=float, default=3.3, help="数字阈值电压(默认3.3V)")
    p_auto.add_argument("--out", default=None, help="导出目录(默认 .cl/capture/saleae)")
    p_auto.add_argument("--launch", action="store_true", help="自动启动 Logic 2（否则需已开启 Automation Server）")
    p_auto.add_argument("--app", default=LOGIC2_APP_DEFAULT, help="Logic 2 可执行路径")
    p_auto.add_argument("--all", dest="analyze_all", action="store_true",
                        help="逐通道统计（多通道捕获用）")
    args = ap.parse_args()

    if not args.cmd:
        ap.print_help()
        return 2

    cli = find_cli()
    if args.cmd == "detect":
        return detect()
    if args.cmd == "capture":
        return capture(cli, args.duration, args.output)
    if args.cmd == "export":
        return export(cli, args.src, args.csv)
    if args.cmd == "parse":
        if not os.path.isfile(args.csv):
            print("ERROR: CSV 不存在: %s" % args.csv)
            return 2
        unit_scale = {"s": 1.0, "ms": 1e-3, "us": 1e-6, "ns": 1e-9}.get(args.unit)
        return parse_file(args.csv, args.io, unit_scale)
    if args.cmd == "auto":
        out_dir = args.out or os.path.join(os.getcwd(), ".cl", "capture", "saleae")
        channels = [int(c) for c in args.channels.split(",") if c.strip()]
        rc = auto_capture(args.duration, channels, args.sample_rate,
                          args.threshold, out_dir, args.launch, args.app)
        if rc == 0 and args.analyze_all:
            csv_path = os.path.join(out_dir, "digital.csv")
            if os.path.isfile(csv_path):
                return analyze_all_channels(csv_path)
        return rc
    return 2


if __name__ == "__main__":
    sys.exit(main())