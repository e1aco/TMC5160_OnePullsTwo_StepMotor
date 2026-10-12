#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""skill_check.py — cl Skill 包自检（/cl check 库侧调用，也可独立运行）

检查项（全部基于本仓重构期沉淀的规则）：
  1. 悬空引用     — *.md 中引用的 details/templates/tools/knowledge 相对路径必须存在
  2. 死术语残留   — 已退役术语不得再现（知识节点/故障视角/sim 后缀等）
  3. 版本一致     — SKILL.md frontmatter version == README.md 标题版本
  4. 案例 成对完整 — knowledge/cases/ 公开半案 <id>.md 必须有配套 <id>.root.md
  5. 索引对账     — index.md 登记的 case_id 必须有文件；文件必须有登记行
  6. 模板编码     — 全部 .md UTF-8 无 BOM 且严格可解码

退出码：0 = 全部通过；1 = 存在失败项（清单打印到 stdout）。
"""

import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 已退役术语（重构历史中移除的概念，再出现即为残留）
STALE_TERMS = [
    r"节点消费", r"故障视角", r"知识节点沉淀", r"每次变更都测",
    r"tick 基准校准", r"/cl run sim", r"/cl code sim",
    r"matlab_run\.py", r"details/sim\.md", r"details/reuse\.md",
    r"safety\.md", r"knowledge/drv/", r"knowledge/ref/",
]

REF_PAT = re.compile(
    r"(?:details|templates|tools|knowledge)/[A-Za-z0-9_./-]+\.(?:md|py)")
VERSION_PAT = re.compile(r"^version:\s*(\S+)", re.M)
TITLE_VERSION_PAT = re.compile(r"# cl Skill（Closed Loop）v(\S+)")


def iter_md(skip=("build_cl", ".workbuddy", ".git")):
    for dirpath, dirnames, filenames in os.walk(ROOT):
        rel = os.path.relpath(dirpath, ROOT)
        if any(rel.startswith(s) or s in rel.split(os.sep) for s in skip):
            dirnames[:] = []
            continue
        for fn in filenames:
            if fn.endswith(".md"):
                yield os.path.join(dirpath, fn)


def read_text(path):
    raw = open(path, "rb").read()
    if raw[:3] == b"\xef\xbb\xbf":
        return raw[3:].decode("utf-8"), True
    return raw.decode("utf-8"), False


def main():
    failures = []

    md_files = list(iter_md())

    # ---- 1. 悬空引用 ----
    refs = set()
    for path in md_files:
        text, _ = read_text(path)
        base = os.path.basename(path)
        for m in REF_PAT.finditer(text):
            ref = m.group(0)
            # 引用自身所在目录的相对名跳过
            if not ref.startswith(("details/", "templates/", "tools/", "knowledge/")):
                continue
            refs.add((base, ref))
    for src, ref in sorted(refs):
        target = os.path.join(ROOT, *ref.split("/"))
        if not os.path.exists(target):
            failures.append("悬空引用: %s → %s" % (src, ref))

    # ---- 2. 死术语残留 ----
    allow = {"knowledge/README.md"}  # README 允许出现历史说明
    for path in md_files:
        rel = os.path.relpath(path, ROOT).replace("\\", "/")
        text, _ = read_text(path)
        for term in STALE_TERMS:
            if rel == "knowledge/README.md":
                continue
            for ln in text.splitlines():
                if re.search(term, ln):
                    failures.append("死术语: %s: %r → %s"
                                    % (rel, term, ln.strip()[:60]))

    # ---- 3. 版本一致 ----
    skill = read_text(os.path.join(ROOT, "SKILL.md"))[0]
    readme = read_text(os.path.join(ROOT, "README.md"))[0]
    v1 = VERSION_PAT.search(skill)
    v2 = TITLE_VERSION_PAT.search(readme)
    if not v1:
        failures.append("SKILL.md 缺 version 字段")
    elif not v2:
        failures.append("README.md 标题缺版本号")
    elif v1.group(1) != v2.group(1):
        failures.append("版本不一致: SKILL=%s README=%s"
                        % (v1.group(1), v2.group(1)))

    # ---- 4/5. 案例成对 + 索引对账 ----
    cases_dir = os.path.join(ROOT, "knowledge", "cases")
    pub, root_half = set(), set()
    if os.path.isdir(cases_dir):
        for fn in os.listdir(cases_dir):
            if fn.endswith(".root.md"):
                root_half.add(fn[:-8])
            elif fn.endswith(".md"):
                pub.add(fn[:-3])
    for cid in sorted(pub - root_half):
        failures.append("案例缺封存半案: %s.root.md 不存在" % cid)
    for cid in sorted(root_half - pub):
        failures.append("封存半案无公开半案: %s.md 不存在" % cid)

    idx_path = os.path.join(ROOT, "knowledge", "index.md")
    if os.path.exists(idx_path):
        idx_ids = set(re.findall(r"^\|\s*([a-z0-9][a-z0-9-]+)\s*\|",
                                 read_text(idx_path)[0], re.M))
        idx_ids.discard("case_id")
        for cid in sorted(idx_ids - pub):
            failures.append("index 登记了不存在的案例: %s" % cid)
        for cid in sorted(pub - idx_ids):
            failures.append("案例未登记 index: %s" % cid)

    # ---- 6. 编码/BOM ----
    for path in md_files:
        try:
            _, has_bom = read_text(path)
            if has_bom:
                failures.append("BOM: %s" % os.path.relpath(path, ROOT))
        except UnicodeDecodeError:
            failures.append("非严格 UTF-8: %s" % os.path.relpath(path, ROOT))

    if failures:
        print("skill_check: %d 项失败" % len(failures))
        for f in failures:
            out = "  [X] " + f
            try:
                print(out)
            except UnicodeEncodeError:
                print(out.encode("gbk", errors="replace").decode("gbk"))
        return 1
    print("skill_check: 全部通过（%d 个 md 文件，%d 个案例）"
          % (len(md_files), len(pub)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
