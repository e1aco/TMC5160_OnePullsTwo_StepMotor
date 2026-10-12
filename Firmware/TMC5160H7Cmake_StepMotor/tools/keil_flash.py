#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""cl skill 的 Keil 命令行烧录工具：调 UV4.exe -f 下载并复位运行

背景：
    部分 ST-Link/pyocd 组合 pyocd 写 Flash 报 Memory transfer fault
    （Get IDCODE / write fault），但 UV4 -f（Keil 自带下载器）与 pyocd --reset
    均可用。本工具封装 UV4 -f 流程作为 flash.py（pyocd）的替代路径。

用法：
    python keil_flash.py <工程.uvprojx> [--timeout N]
        <工程>      必填，MDK-ARM/*.uvprojx 路径
        --timeout   等待秒数（默认 120）

    注意：UV4 -f 无"仅复位"命令；单独复位用 `python flash.py --reset`
    （pyocd 复位通道独立于写 Flash 通道，写失败时复位通常仍可用）。

退出码：0 = Verify OK / Application running；1 = 失败（日志无成功标志或超时）
输出：
    - 解析后的烧录结果行（Erase/Programming/Verify/Application running）
    - 日志原始全文写入 <工程目录>/_keil_flash.log

细节：
    - UV4 定位同 keil_build.py：环境变量 UV4 > 常见安装路径 > PATH。
    - UV4 -j0 派生独立进程后立即返回，本脚本轮询日志等待烧录结束。
    - 成功判定以日志中 "Verify OK" 或 "Application running" 为准
      （UV4 退出码历史版本不一致，不作依据）。
"""

import argparse
import os
import re
import shutil
import subprocess
import sys
import time

UV4_DEFAULT = r"D:\keil5\UV4\UV4.exe"

UV4_SEARCH_PATHS = [
    r"D:\keil5\UV4\UV4.exe",
    r"C:\Keil_v5\UV4\UV4.exe",
    r"C:\Keil\UV4\UV4.exe",
    r"D:\Keil_v5\UV4\UV4.exe",
    r"D:\Keil\UV4\UV4.exe",
]

OK_RE = re.compile(r"(Erase Done|Programming Done|Verify OK|Application running)")
ERROR_RE = re.compile(r"(Error|Fault|failed)", re.IGNORECASE)


def find_uv4():
    env = os.environ.get("UV4")
    if env and os.path.isfile(env):
        return env
    for p in UV4_SEARCH_PATHS:
        if os.path.isfile(p):
            return p
    found = shutil.which("UV4") or shutil.which("UV4.exe")
    if found:
        return found
    return UV4_DEFAULT


def read_log_any_encoding(path):
    for enc in ("utf-8", "utf-8-sig", "gbk", "latin-1"):
        try:
            with open(path, "r", encoding=enc, errors="strict") as f:
                return f.read()
        except (UnicodeDecodeError, OSError):
            continue
    return ""


def main():
    ap = argparse.ArgumentParser(description="Keil MDK 命令行烧录（UV4 -f）")
    ap.add_argument("project", help="MDK-ARM/*.uvprojx 路径")
    ap.add_argument("--timeout", type=int, default=120, help="等待秒数(默认120)")
    args = ap.parse_args()

    project = os.path.abspath(args.project)
    if not os.path.exists(project):
        print("ERROR: 工程不存在: %s" % project)
        return 2

    uv4 = find_uv4()
    if not os.path.isfile(uv4):
        print("ERROR: 找不到 UV4.exe: %s（用环境变量 UV4 覆盖）" % uv4)
        return 2

    proj_dir = os.path.dirname(project)
    log = os.path.join(proj_dir, "_keil_flash.log")
    if os.path.exists(log):
        try:
            os.remove(log)
        except OSError:
            pass

    print("[keil_flash] 烧录: %s" % project)
    print("[keil_flash] 下载器: %s (-f Flash Download)" % uv4)
    subprocess.Popen([uv4, "-j0", "-f", project, "-o", "_keil_flash.log"],
                     cwd=proj_dir)

    start = time.time()
    text = ""
    while time.time() - start < args.timeout:
        if os.path.exists(log):
            text = read_log_any_encoding(log)
            # 完整流程四行齐或出现失败标志即结束等待
            if "Application running" in text or "Flash Load finished" in text \
                    or ERROR_RE.search(text):
                break
        time.sleep(1)

    ok_lines = [ln for ln in text.splitlines() if ln.strip() and
                (OK_RE.search(ln) or ERROR_RE.search(ln) or
                 "Load" in ln or "finished" in ln)]
    for ln in ok_lines:
        print(ln)

    success = ("Verify OK" in text or "Application running" in text) and \
              not re.search(r"(Error|Fault)", text, re.IGNORECASE)
    if success:
        print("[keil_flash] RESULT: FLASH OK（已复位运行；如需再次复位: "
              "python flash.py --reset）")
        return 0
    if not text:
        print("RESULT: TIMEOUT（%ds 内日志无内容）" % args.timeout)
    else:
        print("RESULT: FLASH FAILED")
    return 1


if __name__ == "__main__":
    sys.exit(main())
