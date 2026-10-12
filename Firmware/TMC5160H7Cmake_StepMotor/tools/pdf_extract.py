#!/usr/bin/env python3
"""pdf_extract.py — PDF 数据手册 → 章节/页面粒度 Markdown（cl Skill /cl init 辅助）

规划依据：提取本身不省 token，省 token 来自“索引 + 按需拉取”的检索结构。
本工具据此把每个 PDF 切成小块，保证不存在可被整读的超大文件：
  - 检测章节（标题行聚合逐页文本）
  - 章节 token ≤ --chunk(默认 4000) → 一个整章文件 <base>_chNN_<slug>.md（内聚，利于按节引用）
  - 章节 token >  --chunk            → 只拆成逐页文件 pages/<base>.chNN.p TTT.md（不留中间大文件）
  - 不变量：data 手册（technical/pymupdf/pypdf 路径）无任何产物文件超过 ~chunk tokens
  - 兜底引擎（docling / pdfminer，无逐页结构）→ 单文件 <base>.md（罕见路径，注释标注）

章节文件内保留 `--- [PAGE n] ---` 标记作页锚；大章逐页文件页号落在文件名，正文不再重复 [PAGE]。

引擎（technical 优先链）:
  pymupdf+tables → docling(可选) → pypdf → pdfminer
引擎（text 优先链）:
  pymupdf → pypdf → pdfminer

依赖安装:
  pip install pymupdf          # 通用首选, 含表格检测(find_tables)
  pip install docling          # technical 深度结构(复杂嵌套表), 较慢
  pip install pypdf            # 轻量回退
  pip install pdfminer.six     # 最终回退

用法:
  python pdf_extract.py --in datasheet/STM32F407.pdf            # auto→technical
  python pdf_extract.py --in datasheet/                         # 批量处理目录下所有 *.pdf
  python pdf_extract.py --in xx.pdf --mode text                 # 纯文本快速模式
  python pdf_extract.py --in xx.pdf --engine docling            # 强制 docling（单文件兜底）
  python pdf_extract.py --in xx.pdf --chunk 2000                # 调小分块阈值  python pdf_extract.py --in xx.pdf --no-chunk                  # 不按阈值拆页，仅按章节
  python pdf_extract.py --check                                 # 预检各引擎可用性
"""

import argparse
import json
import os
import re
import sys


# ---------- Windows 输出与基础工具 ----------

def _u8() -> None:
    """Windows 下输出 UTF-8，避免中文走 GBK 报错。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except AttributeError:
            pass


def _table_to_markdown(rows) -> str:
    """把二维表转为 markdown 表格（不依赖 fitz.Table.to_markdown 的版本差异）。"""
    if not rows:
        return ""
    lines = []
    for ri, row in enumerate(rows):
        cells = [
            (c or "").replace("\n", " ").replace("|", r"\|").strip()
            for c in row
        ]
        lines.append("| " + " | ".join(cells) + " |")
        if ri == 0:
            lines.append("|" + "|".join(["---"] * len(cells)) + "|")
    return "\n".join(lines)


def _est_tokens(text):
    """中英混排粗估 token 数：中文按 ~0.6 字/token，英文按 ~0.25 字符/token。"""
    cjk = len(re.findall(r"[\u4e00-\u9fff]", text))
    ascii_chars = len(re.sub(r"[\s\u4e00-\u9fff]", "", text))
    return int((cjk * 0.6) + (ascii_chars * 0.25) + len(text.split()) * 0.3)


def _slugify(text, max_words=4) -> str:
    """把章节标题转成干净的文件名小写 slug。"""
    words = re.findall(r"[A-Za-z0-9]+", text or "")
    return "_".join(words[:max_words]).lower() or "section"


# ---------- 章节检测 ----------

# 章级标题：行首 "N  TITLE" 或 "N.TITLE"（排除表格行/纯数字）
_TOP = re.compile(r"^\s*(\d{1,2})[\.\s]+[A-Z][A-Za-z0-9 .,&/()\-–—_]{2,70}$")
# 子级标题：行首 "N.M TITLE"
_SUB = re.compile(r"^\s*(\d{1,2})\.\d+\s+[A-Z][A-Za-z0-9 .,&/()\-–—_]{2,70}$")


_TITLE_MAX_LEN = 40   # 真实章标题通常短于 40 字符；长行多是表格行/caption，勿误判


def _detect_chapter(text):
    """扫描一页文本，返回 (章号, 标题)。无命中返回 (None, None)。"""
    title = None
    top = None
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("|") or len(line) > _TITLE_MAX_LEN:
            continue
        m = _TOP.match(line)
        if m:
            top = int(m.group(1))
            title = line
            continue
        m = _SUB.match(line)
        if m:
            top = int(m.group(1))
            if title is None:
                title = line
    return top, title


def _assign_chapters(pages_meta, page_texts):
    """逐页扫描，把页分到章（标题检测兜底法，用于无目录文档）。
    返回 (assign, chapter_titles)：assign[i] = 章号（0 表示 front matter）。"""
    assign = []
    chapter_titles = {}
    cur = 0
    cur_title = None
    for text in page_texts:
        num, title = _detect_chapter(text)
        if num is not None:
            cur = num
            if title:
                cur_title = title
        assign.append(cur)
        if cur not in chapter_titles and cur_title:
            chapter_titles[cur] = cur_title
    return assign, chapter_titles


# 目录条目：`TITLE ..... 12`（标题与页码同行的带点线行；中英混排，点线前空格可有可无）
_TOC_TITLE = re.compile(
    r"^\s*(.{2,60}?)\s*\.{3,}\s*(\d{1,3})\s*$")
# 目录条目：独立成行的顶层章号 `12`（中英文目录分别用 `12` / `12.`）
_TOC_NUM = re.compile(r"^\s*(\d{1,2})\.?\s*$")
# 目录条目：独立成行的子章号 `12.3`
_TOC_SUB = re.compile(r"^\s*\d{1,2}\.\d+\s*$")


def _find_toc_boundaries(pages_meta, page_texts, max_pages=14):
    """从文档前若干页找目录，返回 {ch: (start_page, title)}；找不到或过少返回 None。

    TMC5160 类目录格式：章号独立成行（`6`），下一行是 `TITLE ..... 31`，
    目录可能跨多页但仅首页带 "Table of Contents"。这里定位首页后连续吞并，
    状态机把「上一裸数字」作为待定章号，配标题行组条目；每章取首次出现。"""
    toc = {}
    started = False
    pending = None
    for meta, text in zip(pages_meta[:max_pages], page_texts[:max_pages]):
        if not started:
            if re.search(r"table\s*of\s*contents|目录", text, re.I):
                started = True
            else:
                continue
        lines = [ln.strip() for ln in text.splitlines()]
        page_hits = 0
        for line in lines:
            if not line:
                continue
            m = _TOC_SUB.match(line)
            if m:
                continue
            m = _TOC_NUM.match(line)
            if m:
                pending = int(m.group(1))
                continue
            m = _TOC_TITLE.match(line)
            if m:
                page_hits += 1
                title, pg = m.group(1).strip(), int(m.group(2))
                if pending is not None and pg > 0 and pending not in toc:
                    toc[pending] = (pg, title)
                continue
        # 目录页必有大量「标题.....页码」行；某页一个都没有即视为目录结束
        if started and page_hits == 0:
            break
    return toc if len(toc) >= 3 else None


def _assign_by_toc(toc, page_count):
    """按目录边界给每页分章。返回 (assign, chapter_titles)。"""
    starts = sorted(toc.items(), key=lambda kv: kv[1][0])
    assign = []
    for p in range(1, page_count + 1):
        ch = 0
        for c, (pg, _t) in starts:
            if p >= pg:
                ch = c
        assign.append(ch)
    return assign, {c: t for c, (pg, t) in starts}


# ---------- 各引擎提取 ----------

# 运行页眉/页脚逐页样板 → 删掉，减少 AI 读取噪声（如 TMC5160 每页的版权/URL/页码）
_BOILERPLATE_RES = (
    re.compile(r"^\s*www\.\S+\s*$", re.I),                              # 纯 URL 页脚
    re.compile(r"^\s*\d{1,4}\s*$"),                                     # 孤立页码
    re.compile(r"^.{0,90}DATASHEET\s*\(Rev\.?.*?\)\s*\d*\s*$", re.I),   # 运行页眉
)


def _clean_page_text(text):
    """删运行页眉/页脚等逐页样板并压缩空行（降噪，供 AI 读取）。"""
    out = []
    blanks = 0
    for ln in text.splitlines():
        if any(r.match(ln) for r in _BOILERPLATE_RES):
            continue
        s = ln.rstrip()
        if s == "":
            blanks += 1
            if blanks > 1:
                continue
        else:
            blanks = 0
        out.append(s)
    return "\n".join(out)


def _extract_pymupdf_tables(pdf_path):
    """per-page：文本 + find_tables 检测到的表格→markdown。返回逐页文本列表。"""
    import fitz
    doc = fitz.open(pdf_path)
    page_texts = []
    pages_meta = []
    try:
        for i, page in enumerate(doc, start=1):
            text = _clean_page_text(page.get_text("text") or "")
            table_md = []
            try:
                for t in page.find_tables().tables:
                    md = _table_to_markdown(t.extract())
                    if md:
                        table_md.append(md)
            except Exception:  # noqa: BLE001 — 表格检测失败不影响正文
                pass
            block = text
            if table_md:
                block = text + "\n\n<!-- detected tables -->\n\n" + \
                        "\n\n".join(table_md)
            page_texts.append(block)
            pages_meta.append(
                {"page": i, "chars": len(text), "tables": len(table_md)})
    finally:
        doc.close()
    return page_texts, pages_meta


def _extract_pymupdf_plain(pdf_path):
    import fitz
    doc = fitz.open(pdf_path)
    page_texts = []
    pages_meta = []
    try:
        for i, page in enumerate(doc, start=1):
            text = _clean_page_text(page.get_text("text") or "")
            page_texts.append(text)
            pages_meta.append({"page": i, "chars": len(text)})
    finally:
        doc.close()
    return page_texts, pages_meta


def _extract_pypdf(pdf_path):
    from pypdf import PdfReader
    reader = PdfReader(pdf_path)
    page_texts = []
    pages_meta = []
    for i, page in enumerate(reader.pages, start=1):
        text = page.extract_text() or ""
        page_texts.append(text)
        pages_meta.append({"page": i, "chars": len(text)})
    return page_texts, pages_meta


def _extract_pdfminer(pdf_path):
    """兜底：无逐页结构，单块内容。"""
    from pdfminer.high_level import extract_text
    return [extract_text(pdf_path)], [{"page": 1, "chars": 0}]


def _extract_docling(pdf_path):
    """docling 深度结构（复杂表格/多栏/公式）。兜底：单块整体 markdown，无法按页切。"""
    from docling.document_converter import DocumentConverter, PdfFormatOption
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions

    opts = PdfPipelineOptions()
    opts.do_ocr = False            # 数字文本数据手册无需 OCR，避免权重下载
    opts.do_table_structure = True
    conv = DocumentConverter(format_options={
        InputFormat.PDF: PdfFormatOption(pipeline_options=opts)})
    result = conv.convert(pdf_path)
    md = result.document.export_to_markdown()
    return [md], [{"page": "docling(全文档)", "chars": len(md), "tables": "?"}]


ENGINES = {
    "pymupdf_tables": _extract_pymupdf_tables,
    "pymupdf": _extract_pymupdf_plain,
    "docling": _extract_docling,
    "pypdf": _extract_pypdf,
    "pdfminer": _extract_pdfminer,
}

_REQUIRED_MODULES = {
    "pymupdf_tables": "fitz",
    "pymupdf": "fitz",
    "docling": "docling",
    "pypdf": "pypdf",
    "pdfminer": "pdfminer",
}

# 支持逐页切分的引擎（可拆页文件）；docling/pdfminer 为单块兜底。
PAGE_SPLIT_ENGINES = {"pymupdf_tables", "pymupdf", "pypdf"}

TECHNICAL_CHAIN = ["pymupdf_tables", "docling", "pypdf", "pdfminer"]
TEXT_CHAIN = ["pymupdf", "pypdf", "pdfminer"]


def check() -> int:
    """预检：报告安装的引擎与缺失引擎的安装命令。"""
    engines = [
        ("pymupdf (含表格检测)", "pymupdf", "pip install pymupdf"),
        ("docling (深度结构)", "docling", "pip install docling"),
        ("pypdf (轻量)", "pypdf", "pip install pypdf"),
        ("pdfminer.six (兜底)", "pdfminer", "pip install pdfminer.six"),
    ]
    available = False
    for label, mod, install in engines:
        try:
            __import__(mod)
            status = "OK"
            if mod in ("pymupdf", "pypdf"):
                available = True
        except ImportError:
            status = "missing"
            print(f"[cl] {label:<24} MISSING   → {install}")
            continue
        print(f"[cl] {label:<24} OK")
    if not available:
        print("[cl] 至少需安装其一: pip install pymupdf 或 pip install pypdf")
        return 1
    print("[cl] 预检完成：可用引擎已就绪。")
    return 0


# ---------- 章节分块与落盘 ----------

def _group_by_chapter(assign, chapter_titles, pages_meta, page_texts):
    """按章聚合页。返回有序章列表：每章 {ch, title, slugs, pages[], texts[], tokens}。"""
    chapters = []
    order = []          # 章号出现顺序
    index = {}          # 章号 -> 章 dict
    for i, ch in enumerate(assign):
        if ch not in index:
            d = {"ch": ch, "title": chapter_titles.get(ch),
                 "pages": [], "texts": []}
            index[ch] = d
            order.append(ch)
        index[ch]["pages"].append(pages_meta[i].get("page", i + 1))
        index[ch]["texts"].append(page_texts[i])
    for ch in order:
        d = index[ch]
        d["tokens"] = _est_tokens("".join(d["texts"]))
    return [index[ch] for ch in order]


def _write_chapter_file(base, dst_dir, ch, threshold, no_chunk, page_splitable):
    """写一个章：≤阈值或不可拆页 → 整章文件；否则 → 逐页文件。返回 (kind, written, tokens)。"""
    slug = _slugify(ch["title"]) if ch["title"] else f"sec{ch['ch']}"
    if page_splitable and not no_chunk and ch["tokens"] > threshold:
        # 大章 → 逐页文件，不留中间大文件
        written = []
        for pg, txt in zip(ch["pages"], ch["texts"]):
            fn = f"{base}.ch{ch['ch']:02d}.p{pg:03d}.md"
            full = os.path.join(dst_dir, "pages", fn)
            os.makedirs(os.path.dirname(full), exist_ok=True)
            with open(full, "w", encoding="utf-8") as f:
                f.write(f"# {base} — 第{ch['ch']}章 · p{pg}\n\n{txt}\n")
            written.append({
                "page": pg,
                "file": fn,
                "chars": len(txt),
                "token_est": _est_tokens(txt),
            })
        return ("pages", written, ch["tokens"])
    # 整章文件（含 front matter, ch 可能为 0）
    label = f"front" if ch["ch"] == 0 else f"ch{ch['ch']:02d}_{slug}"
    if ch["ch"] == 0:
        label = f"ch00_front"
    fn = f"{base}_{label}.md"
    full = os.path.join(dst_dir, fn)
    body = ""
    for pg, txt in zip(ch["pages"], ch["texts"]):
        body += f"\n--- [PAGE {pg}] ---\n{txt}"
    with open(full, "w", encoding="utf-8") as f:
        f.write(f"# {base} — 第{ch['ch']}章 {ch['title'] or ''}\n{body}\n")
    return ("chapter", [{
        "pages": ch["pages"],
        "file": fn,
        "chars": len(body),
        "token_est": ch["tokens"],
        "title": ch["title"],
    }], ch["tokens"])


def extract_and_write(src, out_dir, mode, engine_override, chunk, no_chunk):
    """提取单个 PDF 并按章节/页落盘。返回输出描述字典或抛异常。"""
    if engine_override:
        chain = [engine_override]
    else:
        chain = TECHNICAL_CHAIN if mode == "technical" else TEXT_CHAIN

    last_err = None
    for eng in chain:
        mod = _REQUIRED_MODULES.get(eng)
        if mod:
            try:
                __import__(mod)
            except ImportError as err:
                last_err = err
                continue
        try:
            page_texts, pages_meta = ENGINES[eng](src)
            break
        except Exception as exc:  # noqa: BLE001 — 引擎失败切下一引擎
            last_err = exc
            page_texts = pages_meta = None
    else:
        raise RuntimeError(f"所有引擎均失败 ({', '.join(chain)}): {last_err}")
    if page_texts is None:
        raise RuntimeError(f"引擎无一可用: {last_err}")

    base = os.path.splitext(os.path.basename(src))[0]
    dst_dir = out_dir
    os.makedirs(dst_dir, exist_ok=True)

    page_splitable = eng in PAGE_SPLIT_ENGINES
    total_tokens = _est_tokens("".join(page_texts))

    # 删除旧整份平铺 <base>.md（若存在），强制“无超大文件”不变量
    old_md = os.path.join(dst_dir, base + ".md")
    if os.path.exists(old_md):
        os.remove(old_md)

    if not page_splitable:
        # 兜底（docling/pdfminer）：单块整文件存 <base>.md
        full = os.path.join(dst_dir, base + ".md")
        with open(full, "w", encoding="utf-8") as f:
            f.write(f"# {base} (提取自 PDF, 引擎={eng})\n{page_texts[0]}\n")
        chapters = []
        assign_mode = "fallback"
    elif total_tokens <= chunk:
        # 小手册（总 token ≤ 阈值）：整份即小，一个文件即可，无需分章
        full = os.path.join(dst_dir, base + ".md")
        body = "".join(
            f"\n--- [PAGE {pages_meta[i].get('page', i + 1)}] ---\n{t}"
            for i, t in enumerate(page_texts))
        with open(full, "w", encoding="utf-8") as f:
            f.write(f"# {base} (提取自 PDF, 引擎={eng})\n{body}\n")
        chapters = []
        assign_mode = "small"
    else:
        # 大手册：优先目录边界，其次标题检测兜底
        toc = _find_toc_boundaries(pages_meta, page_texts)
        if toc:
            assign, chapter_titles = _assign_by_toc(toc, len(pages_meta))
            assign_mode = "toc"
            chapters = _group_by_chapter(assign, chapter_titles,
                                         pages_meta, page_texts)
        elif len(pages_meta) <= 10:
            # 无目录的小规格书（如单页 MOSFET/电机规格）：不细分章，整份单文件
            full = os.path.join(dst_dir, base + ".md")
            body = "".join(
                f"\n--- [PAGE {pages_meta[i].get('page', i + 1)}] ---\n{t}"
                for i, t in enumerate(page_texts))
            with open(full, "w", encoding="utf-8") as f:
                f.write(f"# {base} (提取自 PDF, 引擎={eng})\n{body}\n")
            chapters = []
            assign_mode = "small"
        else:
            assign, chapter_titles = _assign_chapters(pages_meta, page_texts)
            assign_mode = "heading"
            chapters = _group_by_chapter(assign, chapter_titles,
                                         pages_meta, page_texts)
            # 安全网：标题检测劣化（单章霸占大半文档）→ 全量按页拆，保不变量
            if chapters and max(c["tokens"] for c in chapters) > chunk:
                dominant = max(chapters, key=lambda c: len(c["pages"]))
                if len(dominant["pages"]) > len(pages_meta) * 0.5:
                    assign_mode = "pages"
                    chapters = [{
                        "ch": 0, "title": None,
                        "pages": [m.get("page", i + 1)
                                  for i, m in enumerate(pages_meta)],
                        "texts": page_texts,
                        "tokens": total_tokens,
                    }]

    artifacts = []
    for ch in chapters:
        kind, written, tokens = _write_chapter_file(
            base, dst_dir, ch, chunk, no_chunk, page_splitable)
        artifacts.extend(written)
    if chapters:
        # 打印按章概览
        for ch in chapters:
            tag = "前部" if ch["ch"] == 0 else f"第{ch['ch']}章"
            print(f"[cl]   {tag:<8} ~{ch['tokens']} tok  "
                  f"pages {ch['pages'][0]}-{ch['pages'][-1]}")

    meta = {
        "source": os.path.basename(src),
        "engine": eng,
        "mode": mode,
        "split": assign_mode,          # small | toc | heading | fallback
        "page_split": page_splitable,
        "chunk_threshold": chunk if page_splitable else None,
        "pages": len(pages_meta),
        "est_tokens": total_tokens,
        "artifacts": artifacts,          # 章节(单条)或逐页列表
    }
    with open(os.path.join(dst_dir, base + ".meta.json"),
              "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    return meta


def main():
    _u8()
    p = argparse.ArgumentParser(description="PDF → 章节/页粒度 Markdown（cl Skill）")
    p.add_argument("--in", dest="in_path", required=True,
                   help="PDF 文件或目录（目录则批量处理 *.pdf）")
    p.add_argument("--out", help="输出目录（默认与输入同目录；批量时必填）")
    p.add_argument("--mode", default="auto", choices=["auto", "technical", "text"],
                   help="auto→technical（数据手册默认）")
    p.add_argument("--engine", choices=list(ENGINES.keys()),
                   help="强制指定提取引擎")
    p.add_argument("--chunk", type=int, default=4000,
                   help="章节 token 阈值：超过则拆逐页文件（默认 4000）")
    p.add_argument("--no-chunk", action="store_true",
                   help="不按阈值拆页，仅按章节输出")
    args = p.parse_args()

    if args.in_path == "check":   # 兼容 `--in check` 简写
        return check()

    if not os.path.exists(args.in_path):
        sys.stderr.write(f"[cl] 找不到路径: {args.in_path}\r\n")
        return 1

    pdfs = []
    if os.path.isdir(args.in_path):
        pdfs = sorted(f for f in os.listdir(args.in_path)
                      if f.lower().endswith(".pdf"))
        if not pdfs:
            sys.stderr.write(f"[cl] 目录中无 PDF: {args.in_path}\r\n")
            return 1
        if args.out is None:
            sys.stderr.write("[cl] 批量处理时必须指定 --out 输出目录\r\n")
            return 1
        src_root = args.in_path
    else:
        pdfs = [os.path.basename(args.in_path)]
        src_root = os.path.dirname(os.path.abspath(args.in_path))

    mode = "technical" if args.mode == "auto" else args.mode
    out_dir = args.out or src_root
    if mode == "technical" and args.engine is None:
        print("[cl] 数据手册默认 technical（结构保真，含表格→markdown）。"
              "可用 --mode text 走纯文本快速路径。")

    results = {}
    for pdf in pdfs:
        src = os.path.join(src_root, pdf) if os.path.isdir(args.in_path) \
            else args.in_path
        try:
            meta = extract_and_write(src, out_dir, mode, args.engine,
                                     args.chunk, args.no_chunk)
        except RuntimeError as exc:
            sys.stderr.write(f"[cl] 提取失败 {pdf}: {exc}\r\n")
            results[pdf] = {"error": str(exc)}
            continue
        results[pdf] = meta
        mode_name = {"small": "单文件(小)", "fallback": "单文件(兜底)",
                     "toc": "章节(目录边界)", "heading": "章节(标题检测)"}
        print(f"[cl] {meta['engine']:<15} {pdf} → "
              f"{mode_name.get(meta['split'], meta['split'])} "
              f"({meta['pages']} 页 / ~{meta['est_tokens']} tokens)")

    ok = [m for m in results.values() if "error" not in m]
    if ok:
        total = sum(m["est_tokens"] for m in ok)
        print(f"[cl] 完成 {len(ok)}/{len(pdfs)} 个文件，合计 ~{total} tokens。"
              f"元数据见各 .meta.json。")
    return 0 if len(ok) == len(pdfs) else 1


if __name__ == "__main__":
    sys.exit(main())