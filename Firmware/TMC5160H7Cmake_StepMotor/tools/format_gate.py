#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""format_gate.py — cl 产出格式门禁（项目侧，/cl run|code 落盘后 /cl check 复用）

把 details/format-gate.md 中**可正则化**的检查项做成一条命令：
AI 生成/修改 `module/`、`test/` 代码后跑本脚本，修复 FAIL 清单，不再手工逐项 grep。
每条 FAIL 输出 `文件:行 | 证据`，全部通过退出码 0，任一 FAIL 退出码 1。

判据定义唯一权威 = `details/format-gate.md` + `templates/code_style.md`；本脚本是其执行载体，
**只做规则可正则化部分**；以下项不在此覆盖（输出"需人工复查"提示，由 AI 判断）：
  · 函数头注释按复杂度分级（平凡单行 / `@说明` / 复杂三要素，判据见 code_style §4）
  · 分层归属是否越界（编排/算法层直碰外设寄存器）
  · 符号命名是否表意（文件名模块语义、函数名含动词）
  · 关键配置值是否含可复算推导链（弱依据 regex 见下，只拦最明显的一类）

覆盖项（与 format-gate.md checklist 对应）：
  [1] 编码/BOM —— UTF-8 严格解码、无 BOM（code_style §7）
  [2] 文件头六要素 —— @文件/@作者=cl/@日期/@版本/@说明 + 首尾分隔行（code_style §1）
  [3] 段标记 —— .c 文件（>40 行）须含 `/* ==== 段名 ==== */`（统一分段，禁 drv `*` 框体，code_style §3.1；小 .h 豁免）
  [4] guard 配对 —— `#ifndef <MOD>_H` 须有 `#endif /* <MOD>_H */`
  [5] 行宽 —— 非空行 >100 字符（code_style §5）
  [6] Tab 缩进 —— 代码含 Tab（code_style §5，4 空格禁 Tab）
  [7] static 命名 —— 函数非 `S_`+PascalCase / 变量非 `s_`+snake（code_style §6）
  [8] 残留前缀 —— `ela_`/`elaco_` 标识符残留（format-gate「内部 static 命名」条）
  [9] 弱依据 —— `依据/来源 ... 参考 XX.c` 且无 `.cl/memory|datasheet` 值（codegen.md「依据写法」）
  [10] 残留测试段 —— `_TST`、`dump` 调试、注释自标"验证后移除/临时测试"（format-gate 条目）
  [11] 测试钩子残留 —— `_SIM`/`SIM_`/`_HOOK`/`HOOK_`（check.md「残留测试钩子」）
  [12] 尾随空白 —— 行尾空格（保持 diff 干净，WARN 级不计 FAIL）
  [13] drv 反向依赖未豁免 —— drv 文件 include `app/` 而文件头无「豁免登记/反向依赖」
      （codegen.md「drv 反向依赖豁免」）
  [14] 禁用前缀 —— 函数/段名出现 `DRV_`/`USR_`/`APP_`（code_style §0 总则：全项目只认 `S_`+裸名）
  [15] `S_` 私有性 —— 非 static 对外函数不得以 `S_` 开头（`S_` 仅限 static 私有）
  [16] Allman 花括号 —— `) {` / `else{` / `else )` 同行 / 单行函数体 `){ return`（code_style §5，强制）
  [17] 行内尾注注释 —— 行内尾随注释用 `/* */`（`code; // note` 违规，code_style §5）
  [18] drv 框体位 —— 出现 drv `*` 框体分段（`*{20,}`）→ WARN（code_style §3.1 已取消框体）
  [19] 依据前缀 —— `依据` 行带公共前缀 `.cl/datasheet/pages/`（应省略，只留文件名，codegen.md「寄存器依据」）
  [20] memory 行宽 —— `<root>/.cl/memory/*.md` 单行 >100 字符（init.md「memory 写纪律」）

用法：
    python tools/format_gate.py [module_dir] [test_dir]
        module_dir/test_dir  要扫描的目录（默认：当前目录下 module 与 test）
        也可传文件路径；多目录用空格分隔，未传的用默认
    python tools/format_gate.py --root <工程根>      # 从工程根解析 module/test
    python tools/format_gate.py --selftest           # 内嵌样例自检（不扫盘）

退出码：0 = 全部通过；1 = 存在 FAIL（清单打印到 stdout，AI 逐条修复）。
"""

import argparse
import os
import re
import sys

STALE_PREFIX = re.compile(r"\b(ela_|elaco_)[a-zA-Z0-9_]+")
BANNED_PREFIX = re.compile(r"\b(?:DRV_|USR_|APP_)[A-Za-z0-9_]+")
WEAK_BASIS = re.compile(r"(?:依据|来源)\s*[^\n]*参考[^\n]*\.c")
HEADER_FIELDS = ["@文件:", "@作者: cl", "@日期:", "@版本:", "@说明:"]
SECT_MARK = re.compile(r"/\* ={4,} .+ ={4,} \*/")
BOX_OPEN = re.compile(r"^/\*{20,}\s*$")
BOX_CLOSE = re.compile(r"^\*{20,}/\s*$")
BOX_NAME = re.compile(r"^\s*\*\s+[^*\s].*$")
GUARD_DEF = re.compile(r"^#ifndef\s+([A-Za-z0-9_]+)\s*$")
GUARD_END = re.compile(r"^#endif\s+/\*\s*([A-Za-z0-9_]+)\s*\*/\s*$")
STATIC_FUNC = re.compile(
    r"^\s*static\s+(?:inline\s+)?(?:[\w\s\*]+?\s+)?([A-Za-z_]\w*)\s*\(")
STATIC_VAR = re.compile(r"^\s*static\s+\w[\w\s\*]*?\s([a-zA-Z_]\w*)\s*[;=,]\s*$")
PUB_FUNC = re.compile(
    r"^\s*(?:extern\s+)?(?:[\w\s\*]+?\s+)?([A-Za-z_]\w*)\s*\([^;]*\)\s*\{")
ALLMAN_BRACE = re.compile(r"\)\s*\{|else\s*\{|do\s*\{")
TAIL_CPP_CMT = re.compile(r"[A-Za-z0-9_)\]}\"']\s*//\s")
BOX_BLOCK = BOX_OPEN
DUMP_MARK = re.compile(r"_TST\b|_TST\d|验证后移除|临时测试|调试用, 验证后移除")
HOOK_MARK = re.compile(r"\b_SIM\b|\bSIM_|\b_HOOK\b|\bHOOK_")
APP_INC = re.compile(r'^\s*#include\s*["<]app/')
EXEMPT_MARK = re.compile(r"豁免登记|反向依赖")
TAIL_WS = re.compile(r"[ \t]+$")
BASIS_PREFIX = re.compile(r"依据[^\n]*\.cl/datasheet/pages/")
MEM_LINE_MAX = 100


def _code_lines(lines):
    """剥掉 // 与 /* */ 注释后的行（跨行块注释带状态），供标识符扫描用——
    避免手册寄存器名（如 DRV_CONF）落在注释里被误判为残留前缀。"""
    out, in_block = [], False
    for ln in lines:
        res, i, n = [], 0, len(ln)
        while i < n:
            if in_block:
                j = ln.find("*/", i)
                if j < 0:
                    i = n
                else:
                    in_block, i = False, j + 2
            else:
                j1, j2 = ln.find("/*", i), ln.find("//", i)
                if j1 < 0 and j2 < 0:
                    res.append(ln[i:])
                    i = n
                elif j2 >= 0 and (j1 < 0 or j2 < j1):
                    res.append(ln[i:j2])
                    i = n
                else:
                    res.append(ln[i:j1])
                    in_block, i = True, j1 + 2
        out.append("".join(res))
    return out

PASS, FAIL, WARN = "PASS", "FAIL", "WARN"
HUMAN_REVIEW = [
    "函数头注释按复杂度分级（平凡单行 / `@说明` / 复杂三要素，判据见 code_style §4）",
    "分层归属不越界（drv 碰硬件/algo 零硬件/编排不直碰外设寄存器）",
    "符号命名表意（文件名模块语义、函数名含动词）",
    "关键配置值有可复算推导链（memory 值 + 未化简分步式）",
    "测试归属（多模块编排/产品状态机 → 应进 module/app/，见 code_style §10；仅单模块验证留 test/）",
]


def has_section_marker(lines):
    """.c 段标记：只要统一 `/* ==== 段名 ==== */`；drv `*` 框体已取消，命中仅 WARN（防拖队）。"""
    for ln in lines:
        if SECT_MARK.search(ln):
            return True
    return False


def read_text(path):
    raw = open(path, "rb").read()
    bom = raw[:3] == b"\xef\xbb\xbf"
    if bom:
        raw = raw[3:]
    return raw.decode("utf-8"), bom


def scan_file(path):
    """对单个 .c/.h 跑全部可正则化检查，返回 [(级别, 项名, 文件:行, 证据)]。"""
    issues = []
    try:
        text, bom = read_text(path)
    except UnicodeDecodeError:
        return [(FAIL, "编码", path + ":1", "非严格 UTF-8（先转码再继续）")]
    lines = text.splitlines()
    n = len(lines)

    def add(level, item, lineno, evidence):
        issues.append((level, item, "%s:%d" % (path, lineno), evidence))

    if bom:
        add(FAIL, "编码/BOM", 1, "文件头带 UTF-8 BOM（要求无 BOM）")

    # ---- [2] 文件头六要素（前 25 行内） ----
    head = "\n".join(lines[:25])
    missing = [f for f in HEADER_FIELDS if f not in head]
    if missing:
        add(FAIL, "文件头", 1, "缺要素: %s" % ", ".join(missing))
    elif not re.search(r"^/\*{10,}", head) or not re.search(r"\*{10,}/", head):
        add(FAIL, "文件头", 1, "首尾分隔行缺失/格式不符（原样复制 code_style §1 模板）")

    # ---- [3] 段标记（.c 且 >40 行须有） ----
    if path.endswith(".c") and n > 40 and not has_section_marker(lines):
        add(FAIL, "段标记", 1, ".c 文件（%d 行）无 `/* ==== 段名 ==== */` 分段（code_style §3.1）" % n)

    # ---- [4] guard 配对 ----
    if path.endswith(".h"):
        guards = {}
        for i, ln in enumerate(lines, 1):
            m = GUARD_DEF.match(ln)
            if m:
                guards[m.group(1)] = i
            m = GUARD_END.match(ln)
            if m and m.group(1) in guards:
                del guards[m.group(1)]
        for gname, lineno in guards.items():
            add(FAIL, "guard", lineno, "#ifndef %s 缺 #endif /* %s */ 配对" % (gname, gname))

    # ---- [5] 行宽 + [6] Tab + [12] 尾随空白 ----
    for i, ln in enumerate(lines, 1):
        if ln and len(ln) > 100 and not ln.strip().startswith("/**"):
            add(FAIL, "行宽", i, "%d 字符 > 100" % len(ln))
        if "\t" in ln:
            add(FAIL, "Tab", i, "含 Tab（要求 4 空格）")
        if TAIL_WS.search(ln):
            add(WARN, "尾随空白", i, "行尾有空白")

    # ---- [7] static 命名 ----
    for i, ln in enumerate(lines, 1):
        m = STATIC_FUNC.match(ln)
        if m and not m.group(1).startswith("S_"):
            add(FAIL, "static函数", i, "static 函数 %s 需以 S_+PascalCase 命名" % m.group(1))
        m = STATIC_VAR.match(ln)
        if m and not m.group(1).startswith("s_"):
            add(FAIL, "static变量", i, "static 变量 %s 需以 s_+snake 命名" % m.group(1))

    # ---- [14] 禁用前缀 DRV_/USR_/APP_（code_style §0；只扫代码，注释豁免） ----
    for i, ln in enumerate(_code_lines(lines), 1):
        for m in BANNED_PREFIX.finditer(ln):
            add(FAIL, "禁用前缀", i, "标识符 %s：全项目只认 S_+裸名，禁用 DRV_/USR_/APP_" % m.group(0))

    # ---- [15] S_ 私有性：非 static 对外函数不得以 S_ 开头 ----
    for i, ln in enumerate(lines, 1):
        if re.match(r"^\s*static\b", ln) or re.search(r"\b(?:typedef|struct|enum|union)\b", ln):
            continue
        m = PUB_FUNC.match(ln)
        if m and m.group(1).startswith("S_"):
            add(FAIL, "S_私有性", i, "对外函数 %s 以 S_ 开头（S_ 仅限 static 私有，见 code_style §0）" % m.group(1))

    # ---- [16] Allman 花括号（code_style §5 强制） ----
    for i, ln in enumerate(lines, 1):
        src = ln.split("//", 1)[0].split("/*", 1)[0]
        if ALLMAN_BRACE.search(src):
            add(FAIL, "Allman", i, "`){` / `else {` / `do {` 同行（花括号须独占一行）")
        if re.search(r"\)\s*\{\s*return[^;]*;\s*\}", src):
            add(FAIL, "Allman", i, "单行函数体 `) { return ...; }`（须 Allman 分行）")

    # ---- [17] 行内尾随注释须 `/* */`（禁 `code; // note`；纯 // 注释行豁免） ----
    for i, ln in enumerate(lines, 1):
        clean = re.sub(r"/\*.*?\*/", "", ln)
        if "//" in clean:
            lead = clean.split("//", 1)[0]
            if lead.strip() != "":
                add(FAIL, "尾注注释", i, "行内尾随注释用 `//`（要求 `/* */`，code_style §5）")

    # ---- [18] drv `*` 框体分段已取消（WARN；须开框+段名行+闭框 3 行齐，避免误伤文件头星号） ----
    if path.endswith(".c"):
        for i, ln in enumerate(lines, 1):
            if BOX_OPEN.match(ln) and i + 2 < len(lines):
                if BOX_NAME.match(lines[i + 1]) and BOX_CLOSE.match(lines[i + 2]):
                    add(WARN, "drv框体", i, "出现 `*` 框体分段（code_style §3.1 已取消，统一 `/* ==== */`）")
                    break

    # ---- [8] 残留前缀（只扫代码，注释豁免） ----
    for i, ln in enumerate(_code_lines(lines), 1):
        for m in STALE_PREFIX.finditer(ln):
            add(FAIL, "残留前缀", i, "标识符 %s 残留（归一为 cl 风格）" % m.group(0))

    # ---- [13] drv 反向依赖未豁免（include app/ 而文件头无豁免登记） ----
    is_drv = ("/drv/" in path) or ("\\drv\\" in path)
    if is_drv:
        head = "\n".join(lines[:40])
        app_inc = [i for i, ln in enumerate(lines, 1) if APP_INC.match(ln)]
        if app_inc and not EXEMPT_MARK.search(head):
            add(FAIL, "drv反依赖", app_inc[0],
                "drv 文件 include app/ 而文件头无「豁免登记/反向依赖」"
                "（并入理由+用户裁决日期+调用清单，见 codegen.md「drv 反向依赖豁免」）")

    # ---- [9] 弱依据（含 参考 XX.c 但无 memory/datasheet 数值） ----
    for i, ln in enumerate(lines, 1):
        if WEAK_BASIS.search(ln) and ".cl/memory" not in ln and "datasheet" not in ln:
            add(FAIL, "弱依据", i, "单写`依据 参考 XX.c`无推导链，需补 memory 值/公式或标'待实测确认'")

    # ---- [19] 依据行公共前缀应省略（codegen.md「寄存器依据」） ----
    for i, ln in enumerate(lines, 1):
        if BASIS_PREFIX.search(ln):
            add(FAIL, "依据前缀", i,
                "依据行带 `.cl/datasheet/pages/` 前缀（应省略，只留文件名）")

    # ---- [10] 残留测试段 / [11] 测试钩子 ----
    for i, ln in enumerate(lines, 1):
        if DUMP_MARK.search(ln):
            add(FAIL, "残留测试段", i, ln.strip()[:60])
        if HOOK_MARK.search(ln):
            add(FAIL, "测试钩子", i, ln.strip()[:60])

    return issues


def scan_memory(root):
    """[20] `.cl/memory/*.md` 单行 ≤100 字符（init.md「memory 写纪律」）。"""
    issues = []
    mem_dir = os.path.join(root, ".cl", "memory")
    if not os.path.isdir(mem_dir):
        return issues
    for fn in sorted(os.listdir(mem_dir)):
        if not fn.endswith(".md"):
            continue
        path = os.path.join(mem_dir, fn)
        try:
            text, _ = read_text(path)
        except UnicodeDecodeError:
            issues.append((FAIL, "memory编码", path + ":1", "非严格 UTF-8"))
            continue
        for i, ln in enumerate(text.splitlines(), 1):
            if len(ln) > MEM_LINE_MAX:
                issues.append((FAIL, "memory行宽", "%s:%d" % (path, i),
                               "%d 字符 > %d（init.md「memory 写纪律」）"
                               % (len(ln), MEM_LINE_MAX)))
    return issues


def collect_targets(args):
    """返回要扫描的文件列表。"""
    files = []
    paths = list(args.paths)
    if args.root:
        paths = [os.path.join(args.root, "module"), os.path.join(args.root, "test")]
    for p in paths:
        if os.path.isdir(p):
            for dirpath, dirnames, filenames in os.walk(p):
                dirnames[:] = [d for d in dirnames if not d.startswith(".")]
                for fn in sorted(filenames):
                    if fn.endswith((".c", ".h")):
                        files.append(os.path.join(dirpath, fn))
        elif os.path.isfile(p):
            files.append(p)
    return files


def run(paths, root=None):
    files = collect_targets(argparse.Namespace(paths=paths, root=None))
    if not files and not root:
        print("format_gate: 未找到 .c/.h 文件（默认扫 <cwd>/module 与 <cwd>/test）")
        return 2
    records = []
    for f in files:
        records.extend(scan_file(f))
    if root:
        records.extend(scan_memory(root))
    total_fail = 0
    by_item = {}
    for level, item, loc, ev in records:
        by_item.setdefault(item, []).append((level, loc, ev))
        if level == FAIL:
            total_fail += 1
    for item in sorted(by_item):
        rows = by_item[item]
        fails = [r for r in rows if r[0] == FAIL]
        warns = [r for r in rows if r[0] == WARN]
        for _, loc, ev in sorted(fails, key=lambda r: r[1]):
            print("[%s] %-6s %s | %s" % ("FAIL", item, loc, ev))
        if warns:
            print("[WARN] %-6s %d 处尾随空白（%s 等）" % (item, len(warns), warns[0][1]))
    print("\nformat_gate: %d 个文件，%d 项 FAIL" % (len(files), total_fail))
    print("需人工复查（脚本不判定）:")
    for h in HUMAN_REVIEW:
        print("  · " + h)
    return 1 if total_fail else 0


def selftest():
    tmp = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_fg_selftest")
    os.makedirs(tmp, exist_ok=True)
    good = os.path.join(tmp, "good_sample.c")
    bad = os.path.join(tmp, "bad_sample.c")
    try:
        with open(good, "w", encoding="utf-8") as f:
            f.write(
                "/*****************************************************************************\n"
                " * @文件: good_sample.c\n * @作者: cl\n * @日期: 2026-09-07\n"
                " * @版本: v1.0\n * @说明: 自检用合规样例（S_ 私有 + 对外裸名 + Allman）\n"
                " ****************************************************************************/\n"
                "/* ==== 内部函数 ==== */\n"
                "static int s_count = 0;\n"
                "static int S_AddOne(void)\n"
                "{\n"
                "    return s_count + 1;\n"
                "}\n"
                "/* ==== 接口函数 ==== */\n"
                "int GetCountGlob(void)\n"
                "{\n"
                "    return S_AddOne() + s_count;   /* 行内尾注用 /* */ */\n"
                "}\n"
            )
        with open(bad, "w", encoding="utf-8") as f:
            f.write(
                "static int bad_fn(void) { return 1; }   /* 单行函数体 违反 Allman */\n"
                "static int s_ok = 0;\n"
                "static void elaco_legacy(void) { /* 依据 参考 motor.c */ }\n"
                "static void tmp_dump(void) { int tst; tst = 1; }  /* 验证后移除 */\n"
                "int USR_Process(void) { return 0; }   /* 禁用前缀 USR_ */\n"
                "int main(void) { return 0; }\n"
            )
        print("== good_sample.c（期望无 FAIL）==")
        for level, item, loc, ev in scan_file(good):
            print("  [%s] %-6s %s | %s" % (level, item, loc, ev))
        print("== bad_sample.c（期望多项 FAIL）==")
        for level, item, loc, ev in scan_file(bad):
            print("  [%s] %-6s %s | %s" % (level, item, loc, ev))
        return 0
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("paths", nargs="*", help="要扫描的目录/文件（默认 module 与 test）")
    ap.add_argument("--root", default=None, help="工程根，从其中解析 module/ 与 test/")
    ap.add_argument("--selftest", action="store_true", help="内嵌样例自检")
    args = ap.parse_args()

    if args.selftest:
        return selftest()
    if args.root and not args.paths:
        return run([os.path.join(args.root, "module"),
                    os.path.join(args.root, "test")], root=args.root)
    root = args.root
    if root is None and os.path.isdir(os.path.join(os.getcwd(), ".cl", "memory")):
        root = os.getcwd()
    return run(args.paths, root=root)


if __name__ == "__main__":
    sys.exit(main())
