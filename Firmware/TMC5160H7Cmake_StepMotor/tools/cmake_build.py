#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""cl skill 的 CMake 命令行编译工具：配置+构建 CMake 工程并解析 GCC 诊断结果

背景：
    与 keil_build.py 同契约的 CMake 版（CLion / CubeMX CMake 工程）。
    编译真相判据不变：0 Error 0 Warning 才算门禁通过；AI 拿解析后的
    error/warning 行做"编译 -> 定位 -> 修复"闭环。
    构建目录固定 `build_cl`，与 CLion 默认的 cmake-build-* 隔离，
    两边可并存互不踩缓存。

用法：
    python cmake_build.py <工程根|CMakeLists.txt路径> [--define 宏] [--clean]
                          [--target 名] [--timeout N]
        <工程>      必填，含 CMakeLists.txt 的工程根（或直接给该文件路径）
        --define    宏注入（如 --define CL_TIMING_MEASURE）：configure 级注入
                    `-DCMAKE_C_FLAGS="-D<宏>"`，不改动任何工程文件；
                    多宏用逗号分隔
        --clean     删除 build_cl 后全新 configure（首次/换编译器/改宏集合后建议）
        --target    只构建指定 target（默认全建）
        --timeout   单步（configure 或 build）等待秒数（默认 600）

退出码：0 = 构建成功（0 Error）；非 0 = 失败（配置/编译/链接错误或超时）。
输出：
    - 所有 error/warning 行（GCC 原生 文件:行:列. 前缀，供 AI 定位修复）
    - 末尾一行 RESULT: N Error(s), M Warning(s)（与 keil_build.py 同格式）
    - 构建原始输出全文写入 <工程根>/_cmake_build.log（供复查）
    - 成功时列出产物（.elf/.hex/.bin）绝对路径（烧录/mcu_dbg 直接取用）

细节：
    - cmake/ninja 定位：环境变量 CMAKE_BIN/NINJA_BIN > PATH > CLion 捆绑目录
      （CLion 自带 cmake.exe/ninja.exe，装了 CLion 通常无需另装）。
    - 超时整树杀：taskkill /F /T 杀 cmake 及其 ninja/gcc 子进程，不留孤儿。
"""

import argparse
import glob
import os
import re
import shutil
import subprocess
import sys

BUILD_DIR = "build_cl"
LOG_NAME = "_cmake_build.log"

CMAKE_SEARCH = [
    r"C:\Program Files\JetBrains\CLion*\bin\cmake\win\x64\bin\cmake.exe",
    os.path.expandvars(r"%LOCALAPPDATA%\Programs\CLion*\bin\cmake\win\x64\bin\cmake.exe"),
    r"D:\Program Files\JetBrains\CLion*\bin\cmake\win\x64\bin\cmake.exe",
    r"E:\STM32CubeCLT*\CMake\bin\cmake.exe",
    r"C:\ST\STM32CubeCLT*\CMake\bin\cmake.exe",
    r"D:\STM32CubeCLT*\CMake\bin\cmake.exe",
]
NINJA_SEARCH = [
    r"C:\Program Files\JetBrains\CLion*\bin\ninja\win\x64\ninja.exe",
    os.path.expandvars(r"%LOCALAPPDATA%\Programs\CLion*\bin\ninja\win\x64\ninja.exe"),
    r"D:\Program Files\JetBrains\CLion*\bin\ninja\win\x64\ninja.exe",
    r"E:\STM32CubeCLT*\Ninja\bin\ninja.exe",
    r"C:\ST\STM32CubeCLT*\Ninja\bin\ninja.exe",
    r"D:\STM32CubeCLT*\Ninja\bin\ninja.exe",
]

DIAG_RE = re.compile(r"(error|warning)\s*:", re.IGNORECASE)


def _glob_first(patterns):
    for pat in patterns:
        hits = sorted(glob.glob(pat))
        if hits:
            return hits[-1]
    return None


def find_tool(name, env_key, search):
    """定位 cmake.exe/ninja.exe：env > PATH > CLion 捆绑目录。"""
    env = os.environ.get(env_key)
    if env and os.path.isfile(env):
        return env
    found = shutil.which(name)
    if found:
        return found
    return _glob_first(search)


def detect_toolchain(proj):
    """探测交叉编译工具链文件（CubeMX 约定 cmake/*-arm*.cmake 等）。"""
    hits = sorted(glob.glob(os.path.join(proj, "cmake", "*.cmake")))
    for h in hits:
        base = os.path.basename(h).lower()
        if "arm" in base or "toolchain" in base or "clang" in base:
            return h
    return None


def run_step(args, timeout_s):
    """跑一步子进程：流式落日志 + 超时整树杀。返回 (输出文本, 是否超时)。"""
    proc = subprocess.Popen(args, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT)
    chunks = []
    try:
        out, _ = proc.communicate(timeout=timeout_s)
        text = out.decode("utf-8", errors="replace")
        timed_out = False
    except subprocess.TimeoutExpired:
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                       capture_output=True)
        out, _ = proc.communicate()
        text = (out or b"").decode("utf-8", errors="replace")
        timed_out = True
    return text, timed_out


def main():
    ap = argparse.ArgumentParser(description="CMake 命令行编译（cl skill）")
    ap.add_argument("project", help="含 CMakeLists.txt 的工程根（或该文件路径）")
    ap.add_argument("--define", default=None,
                    help="宏注入，逗号分隔多宏（configure 级 -DCMAKE_C_FLAGS，不改工程文件）")
    ap.add_argument("--clean", action="store_true",
                    help="删除 %s 后全新 configure" % BUILD_DIR)
    ap.add_argument("--target", default=None, help="只构建指定 target（默认全建）")
    ap.add_argument("--toolchain", default=None,
                    help="交叉工具链文件路径（缺省自动探测 <工程>/cmake/*arm*.cmake；"
                         "纯主机项目无需）")
    ap.add_argument("--timeout", type=int, default=600,
                    help="单步等待秒数(默认600)")
    args = ap.parse_args()

    proj = os.path.abspath(args.project)
    if os.path.isdir(proj):
        cmakelists = os.path.join(proj, "CMakeLists.txt")
    else:
        cmakelists = proj
        proj = os.path.dirname(proj)
    if not os.path.isfile(cmakelists):
        print("ERROR: 未找到 CMakeLists.txt: %s" % cmakelists)
        return 2

    cmake = find_tool("cmake", "CMAKE_BIN", CMAKE_SEARCH)
    if not cmake:
        print("ERROR: 找不到 cmake.exe（env CMAKE_BIN 或安装 "
              "xpack-arm-none-eabi-gcc/CMake，或确认 CLion 已安装）")
        return 2
    ninja = find_tool("ninja", "NINJA_BIN", NINJA_SEARCH)

    build_dir = os.path.join(proj, BUILD_DIR)
    log_path = os.path.join(proj, LOG_NAME)

    defines = [d.strip() for d in args.define.split(",")] if args.define else []
    cflags = " ".join("-D%s" % d for d in defines)

    full_log = []

    def tee(text):
        full_log.append(text)
        sys.stdout.write(text if text.endswith("\n") else text + "\n")

    # ---- 防呆：缓存宏集合残留（宏版切生产版）自动 --clean ----
    # build_cl 的 CMAKE_C_FLAGS 若残留 -D<宏>，本次不带 --define 时 cmake "no work to do"
    # 仍产出宏版固件（2026-09-06 elaco_stepmotor 实测）。此处无条件比对缓存，
    # 不一致即强制全量重配置，替代人工 --clean。
    cached = read_cache_cflags(build_dir)
    if cached and cached != cflags:
        tee("[cmake_build] ⚠️ 检测到 build_cl 缓存宏集合残留（cache: %r → 本次: %r），"
            "自动 --clean 重建，防止产物仍是宏版固件" % (cached, cflags))
        args.clean = True
    # 对称防呆（2026-09-09 实测坑）：生产版→宏版方向 cached="" 为 falsy，
    # 旧逻辑既不 clean 也不重 configure → --define 静默失效、产物仍生产版。
    # cached 与本次 cflags 不一致即强制 reconfigure（cmake 原地更新缓存 flags，
    # ninja 随后全量重建，无需删目录）。
    force_cfg = (cached or "") != cflags

    # ---- configure（缓存缺失/--define 变化/--clean 时执行）----
    need_cfg = args.clean or force_cfg or not os.path.exists(
        os.path.join(build_dir, "CMakeCache.txt"))
    toolchain = args.toolchain or detect_toolchain(proj)
    cfg_args = [cmake, "-S", proj, "-B", build_dir, "-G", "Ninja",
                "-DCMAKE_BUILD_TYPE=Debug"]
    if toolchain:
        cfg_args.append("-DCMAKE_TOOLCHAIN_FILE=%s" % os.path.abspath(toolchain))
        print("[cmake_build] 工具链: %s" % toolchain)
    if cflags:
        cfg_args.append("-DCMAKE_C_FLAGS=%s" % cflags)
    env = dict(os.environ)
    if ninja:
        env["PATH"] = os.path.dirname(ninja) + os.pathsep + env["PATH"]

    if need_cfg:
        if args.clean and os.path.exists(build_dir):
            shutil.rmtree(build_dir, ignore_errors=True)
            tee("[cmake_build] --clean: 已删除 %s" % BUILD_DIR)
        print("[cmake_build] configure: %s" % " ".join(cfg_args))
        out, timed_out = run_step(cfg_args, args.timeout)
        tee(out)
        if timed_out:
            open(log_path, "w", encoding="utf-8").write("".join(full_log))
            print("RESULT: BUILD FAILED / TIMEOUT（configure 超时 %ds）" % args.timeout)
            return 1
        if proc_failed(out) or "-- Configuring incomplete" in out:
            print("RESULT: CONFIGURE FAILED（详见 %s）" % log_path)
            flush_log(log_path, full_log)
            return 1

    # ---- build ----
    bld_args = [cmake, "--build", build_dir, "--parallel"]
    if args.target:
        bld_args += ["--target", args.target]
    print("[cmake_build] build: %s" % " ".join(bld_args))
    out, timed_out = run_step(bld_args, args.timeout)
    tee(out)
    flush_log(log_path, full_log)
    if timed_out:
        print("RESULT: BUILD FAILED / TIMEOUT（build 超时 %ds）" % args.timeout)
        return 1

    errors = [ln.strip() for ln in out.splitlines() if DIAG_RE.search(ln)
              and re.search(r"\berror\b\s*:", ln, re.IGNORECASE)]
    warnings = [ln.strip() for ln in out.splitlines()
                if re.search(r"\bwarning\b\s*:", ln, re.IGNORECASE)]
    for ln in errors + warnings:
        print(ln)

    err_n, warn_n = len(errors), len(warnings)
    artifacts = []
    for root, dirs, files in os.walk(build_dir):
        if "CMakeFiles" in root:
            continue
        for fn in files:
            if fn.endswith((".elf", ".hex", ".bin")):
                artifacts.append(os.path.join(root, fn))
    art_note = ""
    if artifacts:
        art_note = "  [产物] " + "; ".join(os.path.abspath(a)
                                           for a in sorted(artifacts))
    print("RESULT: %d Error(s), %d Warning(s)%s"
          % (err_n, warn_n, art_note))

    build_stopped = "ninja: build stopped" in out or "FAILED:" in out
    if err_n > 0 or build_stopped:
        return 1
    if warn_n == 0 and not artifacts and \
            "no work to do" not in out.lower():
        # 全绿但无产物：可能 target 名不对或链接目标缺失，提示确认
        print("[cmake_build] ⚠️ 0E0W 但未发现 .elf 产物——确认 target 名是否正确"
              "（--target <名> 或检查 CMakeLists）")
    return 0


def proc_failed(out):
    return "CMake Error" in out or "Configuring incomplete" in out


def read_cache_cflags(build_dir):
    path = os.path.join(build_dir, "CMakeCache.txt")
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for ln in f:
                if ln.startswith("CMAKE_C_FLAGS:STRING="):
                    return ln.split("=", 1)[1].strip()
    except OSError:
        pass
    return ""


def flush_log(path, chunks):
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write("".join(chunks))
    except OSError as e:
        print("ERROR: 日志写入失败: %s" % e)


if __name__ == "__main__":
    sys.exit(main())
