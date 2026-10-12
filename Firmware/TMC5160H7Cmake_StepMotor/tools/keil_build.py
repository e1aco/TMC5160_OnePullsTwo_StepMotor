#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""cl skill 的 Keil MDK 命令行编译工具：调 UV4.exe 编译 .uvprojx 并解析结果

背景：
    AI 在 Keil 场景下缺少真实编译能力时，会退而求其次用 `gcc -fsyntax-only`
    交叉验证——但 gcc 与 AC5/AC6 方言不等价，且**看不到链接阶段**（静态死代码、
    未引用函数等门禁项在 gcc 下不报，Keil 下会报 #177-D）。
    本工具用 UV4 命令行构建真实工程，让 AI 具备"编译 -> 解析 -> 修复"闭环。

用法：
    python keil_build.py <工程.uvprojx> [--rebuild] [--list-targets] [--timeout N] [--define 宏]
        <工程>      必填，MDK-ARM/*.uvprojx 路径
        --rebuild   全量重编译（默认增量 -b；--rebuild 时用 -r 即 Rebuild）
        --list-targets  列出工程 Target 名后退出（不编译）
        --timeout   构建等待秒数（默认 300）
        --define    宏注入（如 --define CL_TIMING_MEASURE）：临时写入 uvprojx 全部
                    Target 的 <Define>，编译完成后自动恢复原文件（时序宏版构建用，
                    免手动改 uvprojx）

退出码：0 = 构建成功（0 Error）；非 0 = 失败（编译/链接错误或构建超时）。
输出：
    - 解析后的编译摘要（末尾一行 * 0 Error(s), N Warning(s) [Flash≈KB RAM≈KB]）
    - 所有 error/warning 行（带 文件:行 前缀，供 AI 定位修复）
    - 构建日志原始全文写入 <工程目录>/../_keil_build.log（供复查）

细节：
    - UV4 定位：环境变量 UV4 > 常见安装路径 > PATH；多编码读取日志
      （utf-8→gbk→latin-1），Keil 中文路径/注释不乱码。
    - 0 Error 0 Warning 才算通过；Warning 若非 0 优先清掉（#177-D 未引用 = 静态残留铁证）。

注意：
    - UV4 -j0 派生独立构建进程后立即返回，本脚本轮询日志文件等待构建结束。
    - UV4 退出码：0=成功，1=警告，2=错误（历史版本），11=构建错误（新版）。
      因此不以退出码为准，而以日志中的 "X Error(s)" 行为准。
"""

import argparse
import os
import re
import shutil
import subprocess
import sys
import time

UV4_DEFAULT = r"D:\keil5\UV4\UV4.exe"

# Keil MDK 常见安装路径（尽量自动探测，减少换机器改代码）
UV4_SEARCH_PATHS = [
    r"D:\keil5\UV4\UV4.exe",
    r"C:\Keil_v5\UV4\UV4.exe",
    r"C:\Keil\UV4\UV4.exe",
    r"D:\Keil_v5\UV4\UV4.exe",
    r"D:\Keil\UV4\UV4.exe",
]


def find_uv4():
    """定位 UV4.exe：环境变量 UV4 > 常见路径 > PATH。找不到返回默认路径（触发后续报错）。"""
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
    """读构建日志，尝试 utf-8 → gbk → latin-1，防止 Keil 中文日志乱码。"""
    for enc in ("utf-8", "utf-8-sig", "gbk", "latin-1"):
        try:
            with open(path, "r", encoding=enc, errors="strict") as f:
                return f.read()
        except (UnicodeDecodeError, OSError):
            continue
    return ""


PROGRAM_SIZE_RE = re.compile(
    r"Program Size:\s*Code=(\d+)\s+RO-data=(\d+)\s+RW-data=(\d+)\s+ZI-data=(\d+)")


def parse_result(log_text):
    """从构建日志提取 error/warning 行、最终统计与 Program Size。"""
    lines = log_text.splitlines()
    errors, warnings = [], []
    program_size = None
    for ln in lines:
        m = re.search(r"(\d+)\s+Error\(s\),\s+(\d+)\s+Warning\(s\)", ln)
        if m:
            return int(m.group(1)), int(m.group(2)), lines, program_size
        m2 = PROGRAM_SIZE_RE.search(ln)
        if m2:
            code = int(m2.group(1)); ro = int(m2.group(2))
            rw = int(m2.group(3)); zi = int(m2.group(4))
            program_size = (code, ro, rw, zi)
        if re.search(r"(^|:)\s*error\s*:", ln, re.IGNORECASE):
            errors.append(ln)
        elif re.search(r"warning:\s*#?\d*", ln, re.IGNORECASE):
            warnings.append(ln)
    # 日志以 "Rebuild target" 开头但没统计行 => 构建被中断/超时
    return None, None, lines, program_size


def read_file_any_encoding(path):
    """读文件，尝试 utf-8 → gbk → latin-1。"""
    for enc in ("utf-8", "utf-8-sig", "gbk", "latin-1"):
        try:
            with open(path, "r", encoding=enc, errors="strict") as f:
                return f.read(), enc
        except (UnicodeDecodeError, OSError):
            continue
    return "", None


DEFINE_RE = re.compile(r"<Define>(.*?)</Define>", re.DOTALL)


def inject_define(project, define):
    """向 uvprojx 所有 Target 的 <Define> 追加宏；返回 (原内容, 原编码)。异常时返回 (None, None)。"""
    text, enc = read_file_any_encoding(project)
    if text == "" or enc is None:
        return None, None

    def repl(m):
        val = m.group(1)
        items = [x.strip() for x in val.replace(";", ",").split(",") if x.strip()]
        if define not in items:
            items.append(define)
        return "<Define>%s</Define>" % ",".join(items)

    new_text = DEFINE_RE.sub(repl, text)
    if new_text == text:
        return None, None
    try:
        with open(project, "w", encoding=enc, newline="") as f:
            f.write(new_text)
    except OSError:
        return None, None
    return text, enc


def build(uv4, project, rebuild, timeout_s):
    log = os.path.join(os.path.dirname(project), "_keil_build.log")
    if os.path.exists(log):
        try:
            os.remove(log)
        except OSError:
            pass
    args = [uv4, "-j0", project, "-o", log] + (["-r"] if rebuild else ["-b"])
    subprocess.Popen(args)
    start = time.time()
    while time.time() - start < timeout_s:
        if os.path.exists(log):
            text = read_log_any_encoding(log)
            if re.search(r"\d+\s+Error\(s\),\s+\d+\s+Warning\(s\)", text):
                return text
        time.sleep(1)
    text = ""
    if os.path.exists(log):
        text = read_log_any_encoding(log)
    return text + "\n*** Build TIMEOUT after %ds ***" % timeout_s


def list_targets(project):
    """解析 .uvprojx 列出 Target 名（供 --list-targets）。"""
    import xml.etree.ElementTree as ET
    try:
        tree = ET.parse(project)
    except Exception:
        return []
    targets = []
    for t in tree.getroot().iter("Target"):
        name = t.findtext("TargetName")
        if name and name.strip():
            targets.append(name.strip())
    return targets


def main():
    ap = argparse.ArgumentParser(description="Keil MDK 命令行编译")
    ap.add_argument("project", help="MDK-ARM/*.uvprojx 路径")
    ap.add_argument("--rebuild", action="store_true", help="全量 Rebuild (-r)")
    ap.add_argument("--list-targets", action="store_true",
                    help="列出工程 Target 名后退出（不编译）")
    ap.add_argument("--timeout", type=int, default=300, help="构建等待秒数(默认300)")
    ap.add_argument("--define", default=None,
                    help="宏注入（临时写入 uvprojx 的 <Define>，编译后自动恢复）")
    args = ap.parse_args()

    project = os.path.abspath(args.project)
    if not os.path.exists(project):
        print("ERROR: 工程不存在: %s" % project)
        return 2

    if args.list_targets:
        targets = list_targets(project)
        if not targets:
            print("ERROR: 未能解析工程 Target（.uvprojx 格式？）: %s" % project)
            return 2
        for name in targets:
            print(name)
        return 0

    uv4 = find_uv4()
    if not os.path.exists(uv4):
        print("ERROR: 找不到 UV4.exe: %s（用环境变量 UV4 覆盖）" % uv4)
        return 2

    original = None
    if args.define:
        original, enc = inject_define(project, args.define)
        if original is None:
            print("ERROR: 宏注入失败（未找到 <Define> 或文件不可写）: %s" % project)
            return 2
        print("[keil_build] 宏注入: %s （编译后恢复）" % args.define)
    try:
        print("[keil_build] 编译: %s" % project)
        print("[keil_build] 编译器: %s" % uv4)
        text = build(uv4, project, args.rebuild, args.timeout)
    finally:
        if original is not None:
            try:
                with open(project, "w", encoding=enc, newline="") as f:
                    f.write(original)
                print("[keil_build] uvprojx 已恢复原状")
            except OSError as e:
                print("ERROR: uvprojx 恢复失败: %s（需手动还原工程文件）" % e)

    err_count, warn_count, lines, program_size = parse_result(text)
    # 打印所有错误/警告行
    for ln in lines:
        if re.search(r"error\s*:", ln, re.IGNORECASE) or \
           re.search(r"warning:\s*#", ln, re.IGNORECASE) or \
           re.search(r"\d+\s+Error\(s\)", ln):
            print(ln)

    if err_count is None:
        print("RESULT: BUILD FAILED / TIMEOUT（无结果统计行）")
        return 1
    size_note = ""
    if program_size:
        code, ro, rw, zi = program_size
        flash_kb = (code + ro + rw) / 1024.0
        ram_kb = (rw + zi) / 1024.0
        size_note = "  [Flash≈%.1fKB RAM≈%.1fKB]" % (flash_kb, ram_kb)
    print("RESULT: %d Error(s), %d Warning(s)%s" % (err_count, warn_count, size_note))
    return 0 if err_count == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
