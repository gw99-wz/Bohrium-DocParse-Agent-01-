#!/usr/bin/env python3
"""Layout-aware scientific PDF parser with page/block evidence.

The script is deliberately conservative: every extracted section, table,
figure and formula carries a page number and a bounding box on that page,
and anything that cannot be read (no text layer, encrypted file, dropped
fonts) is reported as an uncertainty instead of being guessed.

Usage:
    python scripts/parse_pdf.py --pdf paper.pdf --out result.json
    python scripts/parse_pdf.py --pdf paper.pdf --case case.json --out result.json
    python scripts/parse_pdf.py --pdf paper.pdf --question "What is the sample?"

Dependencies: PyMuPDF (module name ``pymupdf`` or legacy ``fitz``).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time

try:  # PyMuPDF >= 1.24 prefers the pymupdf import name
    import pymupdf as fitz  # type: ignore
except ImportError:  # pragma: no cover - legacy name
    import fitz  # type: ignore

VERSION = "0.1.0"

BODY_SIZE_FALLBACK = 10.0
HEADING_RATIO = 1.10
FULL_WIDTH_RATIO = 0.62
HEADER_BAND = 0.075
FOOTER_BAND = 0.075
MIN_TEXT_CHARS_PER_PAGE = 80

MATH_SYMBOLS = set(
    "∑∏∫∮√∞≈≠≤≥±∓×÷∂∇∆αβγδεζηθικλμνξοπρστυφχψωΓΔΘΛΞΠΣΦΨΩ"
    "→←↔⇒⇔∈∉⊂⊃∪∩∧∨¬∀∃∝≡≃≪≫"
)
NUMBERED_HEADING = re.compile(r"^(\d+(?:\.\d+){0,3})\.?\s+(\S.*)$")
KNOWN_HEADINGS = {
    "abstract", "introduction", "background", "related work", "methods",
    "method", "methodology", "results", "results and discussion",
    "discussion", "conclusion", "conclusions", "acknowledgments",
    "acknowledgements", "references", "bibliography", "supplementary",
    "supplementary information", "experimental", "experimental section",
    "摘要", "引言", "前言", "方法", "结果", "讨论", "结论", "参考文献", "致谢",
}
CAPTION_PATTERNS = {
    "table": re.compile(r"^\s*(table|tab\.)\s*([0-9]+[a-z]?|s[0-9]+)", re.I),
    "figure": re.compile(r"^\s*(figure|fig\.)\s*([0-9]+[a-z]?|s[0-9]+)", re.I),
}
REF_ENTRY = re.compile(r"^\s*(\[\d{1,3}\]|\d{1,3}\.)\s+")
GARBAGE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def is_garbage(text: str) -> bool:
    """Equation internals exported as control characters are not headings."""
    if GARBAGE.search(text):
        return True
    if not text:
        return True
    weird = sum(1 for ch in text if ord(ch) < 32 or 0xE000 <= ord(ch) <= 0xF8FF)
    return weird / len(text) > 0.15


def norm(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def bbox_dict(rect) -> list[float]:
    return [round(float(v), 2) for v in (rect.x0, rect.y0, rect.x1, rect.y1)]


def join_lines(lines: list[str]) -> str:
    """Join wrapped lines; repair hyphenated words split across lines."""
    out = ""
    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        if not out:
            out = line
            continue
        if out.endswith("-") and line[:1].islower():
            out = out[:-1] + line
        else:
            out = out + " " + line
    return norm(out)


def block_text(block: dict) -> str:
    lines = []
    for line in block.get("lines", []):
        spans = line.get("spans", [])
        lines.append("".join(span.get("text", "") for span in spans))
    return join_lines(lines)


def block_fonts(block: dict) -> tuple[float, bool]:
    """Return (max span size, any bold span) for a block."""
    size = 0.0
    bold = False
    for line in block.get("lines", []):
        for span in line.get("spans", []):
            size = max(size, float(span.get("size", 0.0) or 0.0))
            if int(span.get("flags", 0) or 0) & 16:
                bold = True
    return size, bold


class Document:
    def __init__(self, path: str, params: dict):
        self.path = path
        self.params = params
        self.uncertainties: list[dict] = []
        self.pages_total_chars: list[int] = []

        if not os.path.isfile(path):
            raise SystemExit(f"PDF not found: {path}")
        self.doc = fitz.open(path)
        if self.doc.needs_pass:
            raise SystemExit("PDF is encrypted; decryption is out of scope for this skill")
        if self.doc.page_count == 0:
            raise SystemExit("PDF has no pages")

        self.body_size = self._estimate_body_size()
        self.headers, self.footers = self._detect_running_heads()
        self.tables: list[dict] = []
        self.figures: list[dict] = []
        self.formulas: list[dict] = []

    # ---------- global layout facts ----------
    def _estimate_body_size(self) -> float:
        counts: dict[float, int] = {}
        for page in self.doc:
            page_dict = page.get_text("dict")
            for block in page_dict.get("blocks", []):
                if block.get("type") != 0:
                    continue
                for line in block.get("lines", []):
                    for span in line.get("spans", []):
                        text = span.get("text", "")
                        key = round(float(span.get("size", 0.0) or 0.0), 1)
                        counts[key] = counts.get(key, 0) + len(text.strip())
        if not counts:
            return BODY_SIZE_FALLBACK
        return max(counts.items(), key=lambda kv: kv[1])[0] or BODY_SIZE_FALLBACK

    def _page_bands(self, page) -> tuple[list[dict], list[dict]]:
        h = page.rect.height
        top_limit = page.rect.y0 + h * HEADER_BAND
        bottom_limit = page.rect.y1 - h * FOOTER_BAND
        top, bottom = [], []
        for block in page.get_text("dict").get("blocks", []):
            if block.get("type") != 0:
                continue
            text = block_text(block)
            if not text:
                continue
            y0, y1 = block["bbox"][1], block["bbox"][3]
            if y1 <= top_limit:
                top.append({"text": text, "bbox": bbox_dict(fitz.Rect(*block["bbox"]))})
            elif y0 >= bottom_limit:
                bottom.append({"text": text, "bbox": bbox_dict(fitz.Rect(*block["bbox"]))})
        return top, bottom

    def _detect_running_heads(self) -> tuple[dict, dict]:
        """Text that repeats in the top/bottom margin of many pages is furniture."""
        threshold = max(3, int(self.doc.page_count * 0.3))
        top_counts: dict[str, int] = {}
        bottom_counts: dict[str, int] = {}
        for page in self.doc:
            top, bottom = self._page_bands(page)
            for item in top:
                key = norm(item["text"])
                if len(key) <= 120:
                    top_counts[key] = top_counts.get(key, 0) + 1
            for item in bottom:
                key = norm(item["text"])
                if len(key) <= 120:
                    bottom_counts[key] = bottom_counts.get(key, 0) + 1
        headers = {k: v for k, v in top_counts.items() if v >= threshold}
        footers = {k: v for k, v in bottom_counts.items() if v >= threshold}
        # bare page numbers are page furniture even when the header text rotates
        self._page_number_pages = {
            page.number + 1
            for page in self.doc
            for _, band in (self._page_bands(page),)
            for item in band
            if re.fullmatch(r"\s*\d{1,4}\s*(/\s*\d{1,4})?\s*", item["text"])
        }
        return headers, footers

    def _is_furniture(self, text: str) -> bool:
        key = norm(text)
        if key in self.headers or key in self.footers:
            return True
        if len(key) <= 8 and re.fullmatch(r"\d{1,4}", key):
            return True
        return False

    # ---------- page reading order ----------
    def _column_cut(self, blocks: list[dict], page_rect) -> float | None:
        """Return the x-cut separating two columns, or None for single column."""
        spans = [b for b in blocks if b["x1"] - b["x0"] > 10 and b["width"] < page_rect.width * 0.75]
        if len(spans) < 6:
            return None
        xs = sorted(b["x0"] for b in spans)
        mid = page_rect.width / 2
        left = [b for b in spans if b["x1"] <= mid + 10]
        right = [b for b in spans if b["x0"] >= mid - 10]
        if len(left) < 3 or len(right) < 3:
            return None
        left_max = max(b["x1"] for b in left)
        right_min = min(b["x0"] for b in right)
        if right_min - left_max < 12:
            return None
        coverage = len(left) + len(right)
        if coverage < 0.6 * len(spans):
            return None
        return (left_max + right_min) / 2

    def _order_page(self, page) -> list[dict]:
        blocks = []
        for block in page.get_text("dict").get("blocks", []):
            if block.get("type") != 0:
                continue
            text = block_text(block)
            if not text or self._is_furniture(text):
                continue
            rect = fitz.Rect(*block["bbox"])
            size, bold = block_fonts(block)
            blocks.append(
                {
                    "text": text,
                    "x0": rect.x0, "y0": rect.y0, "x1": rect.x1, "y1": rect.y1,
                    "width": rect.width,
                    "size": size, "bold": bold,
                    "bbox": bbox_dict(rect),
                    "page": page.number + 1,
                }
            )
        cut = self._column_cut(blocks, page.rect)
        full = [b for b in blocks if b["width"] >= page.rect.width * FULL_WIDTH_RATIO]
        narrow = [b for b in blocks if b not in full]

        if cut is None:
            return sorted(blocks, key=lambda b: (b["y0"], b["x0"]))

        def order_segment(segment: list[dict]) -> list[dict]:
            left = [b for b in segment if b["x0"] < cut]
            right = [b for b in segment if b["x0"] >= cut]
            return sorted(left, key=lambda b: (b["y0"], b["x0"])) + sorted(
                right, key=lambda b: (b["y0"], b["x0"])
            )

        # Full-width blocks (title, wide abstract, spanning caption) are anchors:
        # text above an anchor is read in columns, then the anchor, then the rest.
        anchors = sorted(full, key=lambda b: b["y0"])
        remaining = sorted(narrow, key=lambda b: (b["y0"], b["x0"]))
        ordered: list[dict] = []
        for anchor in anchors:
            segment = [b for b in remaining if b["y0"] < anchor["y0"]]
            remaining = [b for b in remaining if b["y0"] >= anchor["y0"]]
            ordered.extend(order_segment(segment))
            ordered.append(anchor)
        ordered.extend(order_segment(remaining))
        return ordered

    # ---------- page elements ----------
    def _find_tables(self, page) -> list[dict]:
        found = []
        try:
            tables = page.find_tables().tables
        except Exception as exc:  # pragma: no cover - version dependent
            self.uncertainties.append(
                {"issue": "table_detection_unavailable", "page": page.number + 1, "detail": str(exc)}
            )
            return found
        for table in tables:
            try:
                rows = table.extract()
            except Exception:
                rows = []
            rect = fitz.Rect(table.bbox)
            markdown = rows_to_markdown(rows)
            found.append(
                {
                    "page": page.number + 1,
                    "bbox": bbox_dict(rect),
                    "rows": len(rows),
                    "cols": max((len(r) for r in rows), default=0),
                    "markdown": markdown,
                    "caption": self._nearby_caption(page, rect, "table"),
                }
            )
        return found

    def _find_figures(self, page) -> list[dict]:
        found = []
        for block in page.get_text("dict").get("blocks", []):
            if block.get("type") != 1:
                continue
            rect = fitz.Rect(*block["bbox"])
            found.append(
                {
                    "page": page.number + 1,
                    "bbox": bbox_dict(rect),
                    "width": round(rect.width, 1),
                    "height": round(rect.height, 1),
                    "caption": self._nearby_caption(page, rect, "figure"),
                }
            )
        return found

    def _nearby_caption(self, page, rect, kind: str) -> str | None:
        pattern = CAPTION_PATTERNS[kind]
        best = None
        best_dist = 1e9
        for block in page.get_text("dict").get("blocks", []):
            if block.get("type") != 0:
                continue
            text = block_text(block)
            if not text or not pattern.match(text):
                continue
            b = fitz.Rect(*block["bbox"])
            dist = min(abs(b.y0 - rect.y1), abs(rect.y0 - b.y1))
            if dist < best_dist and dist < 120:
                best_dist = dist
                best = text
        return best

    def _find_formulas(self, page, ordered: list[dict]) -> list[dict]:
        found = []
        for b in ordered:
            text = b["text"]
            if len(text) > 220 or len(text) < 3:
                continue
            math_chars = {c for c in text if c in MATH_SYMBOLS}
            has_equation = "=" in text and (
                math_chars or re.search(r"\(\d{1,2}\)\s*$", text) or re.search(r"[α-ωΑ-Ω]", text)
            )
            if len(math_chars) >= 2 or (math_chars and re.search(r"[=<>≤≥±]", text)) or has_equation:
                found.append(
                    {
                        "page": page.number + 1,
                        "bbox": b["bbox"],
                        "text": text,
                        "symbols": sorted(math_chars),
                    }
                )
        return found

    # ---------- document walk ----------
    def parse(self) -> dict:
        started = time.time()
        pages_text = []
        per_page_stats = []
        for page in self.doc:
            ordered = self._order_page(page)
            pages_text.append("\n".join(b["text"] for b in ordered))
            chars = sum(len(b["text"]) for b in ordered)
            tables = self._find_tables(page)
            figures = self._find_figures(page)
            formulas = self._find_formulas(page, ordered)
            self.tables.extend(tables)
            self.figures.extend(figures)
            self.formulas.extend(formulas)
            per_page_stats.append(
                {
                    "page": page.number + 1,
                    "blocks": len(ordered),
                    "chars": chars,
                    "tables": len(tables),
                    "figures": len(figures),
                    "formulas": len(formulas),
                }
            )
            if chars < MIN_TEXT_CHARS_PER_PAGE:
                self.uncertainties.append(
                    {
                        "issue": "no_text_layer",
                        "page": page.number + 1,
                        "detail": f"only {chars} characters reconstructed; page may be scanned or image-only",
                    }
                )

        sections, references = self._sections(pages_text)
        layout = self._layout_summary(per_page_stats)
        result = {
            "schema": "docparse/1.0",
            "status": self._status(per_page_stats),
            "document": {
                "file": os.path.basename(self.path),
                "sha256": sha256(self.path),
                "pages": self.doc.page_count,
                "body_font_size": round(self.body_size, 1),
                "title": sections[0]["title"] if sections else None,
                "running_headers": sorted(self.headers)[:5],
                "running_footers": sorted(self.footers)[:5],
            },
            "sections": sections,
            "tables": self.tables,
            "figures": self.figures,
            "formulas": self.formulas,
            "references": references,
            "layout": layout,
            "uncertainties": self.uncertainties,
            "provenance": {
                "script": "scripts/parse_pdf.py",
                "version": VERSION,
                "pymupdf": getattr(fitz, "__version__", "unknown"),
                "params": self.params,
                "elapsed_s": round(time.time() - started, 2),
            },
        }
        return result

    def _layout_summary(self, stats: list[dict]) -> dict:
        two_col_pages = 0
        for page in self.doc:
            blocks = [b for b in page.get_text("dict").get("blocks", []) if b.get("type") == 0 and block_text(b)]
            if self._column_cut(
                [
                    {"x0": b["bbox"][0], "x1": b["bbox"][2], "width": b["bbox"][2] - b["bbox"][0]}
                    for b in blocks
                ],
                page.rect,
            ) is not None:
                two_col_pages += 1
        return {
            "pages": stats,
            "two_column_pages": two_col_pages,
            "header_pages": len(self.headers),
            "footer_pages": len(self.footers),
        }

    def _status(self, stats: list[dict]) -> str:
        total_chars = sum(s["chars"] for s in stats)
        if total_chars < 50:
            return "failed:no_text"
        if any(s["chars"] < MIN_TEXT_CHARS_PER_PAGE for s in stats):
            return "partial"
        return "success"

    # ---------- structure ----------
    def _sections(self, pages_text: list[str]) -> tuple[list[dict], list[dict]]:
        """Split the ordered text stream into sections at heading-like blocks."""
        sections: list[dict] = []
        current = {"level": 1, "title": None, "page": 1, "bbox": None, "paragraphs": []}
        references: list[dict] = []
        in_refs = False

        for page in self.doc:
            ordered = self._order_page(page)
            for b in ordered:
                text = b["text"]
                if is_garbage(text):
                    self.uncertainties.append(
                        {
                            "issue": "unreadable_text_block",
                            "page": b["page"],
                            "detail": "block contains control characters from an unembedded math font; text skipped",
                            "bbox": b["bbox"],
                        }
                    )
                    continue
                if in_refs and not self._is_known_heading(text):
                    references.extend(self._reference_entries(b, references))
                    continue
                if in_refs:
                    in_refs = False
                level = self._heading_level(b)
                if level:
                    if current["paragraphs"] or current["title"]:
                        sections.append(current)
                    current = {
                        "level": level,
                        "title": text,
                        "page": b["page"],
                        "bbox": b["bbox"],
                        "paragraphs": [],
                    }
                    in_refs = bool(re.search(r"^(references|bibliography|参考文献)\b", text, re.I))
                    continue
                if current["title"] is None:
                    current["title"] = text
                    current["page"] = b["page"]
                    current["bbox"] = b["bbox"]
                    continue
                if in_refs:
                    references.extend(self._reference_entries(b))
                    continue
                current["paragraphs"].append(
                    {
                        "text": text,
                        "page": b["page"],
                        "bbox": b["bbox"],
                        "quote": text[:200],
                    }
                )
        if current["paragraphs"] or current["title"]:
            sections.append(current)
        return sections, dedupe_refs(references)

    def _is_known_heading(self, text: str) -> bool:
        return norm(text).lower().rstrip(":") in KNOWN_HEADINGS

    def _heading_level(self, block: dict) -> int | None:
        text = block["text"]
        if len(text) > 140 or is_garbage(text):
            return None
        size_ratio = block["size"] / self.body_size if self.body_size else 1.0
        numbered = NUMBERED_HEADING.match(text)
        known = self._is_known_heading(text)
        all_caps = text.isupper() and 3 <= len(text) <= 80 and any(ch.isalpha() for ch in text)
        if size_ratio >= HEADING_RATIO:
            return 1 if size_ratio >= 1.45 else 2
        if numbered and (block["bold"] or size_ratio >= 1.02):
            depth = numbered.group(1).count(".") + 1
            return min(depth, 4)
        if (known or all_caps) and (block["bold"] or size_ratio >= 1.02 or all_caps):
            return 1
        return None

    def _reference_entries(self, block: dict, existing: list[dict] | None = None) -> list[dict]:
        text = block["text"]
        matches = list(REF_ENTRY.finditer(text))
        if not matches:
            if existing:
                existing[-1]["text"] = (existing[-1]["text"] + " " + text)[:400]
                return []
            return [{"index": None, "text": text[:400], "page": block["page"], "bbox": block["bbox"]}]
        entries = []
        for pos, match in enumerate(matches):
            end = matches[pos + 1].start() if pos + 1 < len(matches) else len(text)
            body = text[match.end():end].strip()
            entries.append(
                {
                    "index": int(re.sub(r"\D", "", match.group(1))),
                    "text": body[:400],
                    "page": block["page"],
                    "bbox": block["bbox"],
                }
            )
        return entries


def rows_to_markdown(rows: list[list]) -> str:
    rows = [[norm("" if cell is None else str(cell)).replace("|", "\\|") for cell in row] for row in rows]
    if not rows:
        return ""
    width = max(len(row) for row in rows)
    rows = [row + [""] * (width - len(row)) for row in rows]
    lines = ["| " + " | ".join(rows[0]) + " |", "|" + "---|" * width]
    for row in rows[1:]:
        lines.append("| " + " | ".join(row) + " |")
    return "\n".join(lines)


def dedupe_refs(refs: list[dict]) -> list[dict]:
    seen = set()
    out = []
    for ref in refs:
        key = norm(ref["text"])[:80]
        if key in seen:
            continue
        seen.add(key)
        out.append(ref)
    return out


def sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def evidence_index(result: dict) -> dict:
    """Compact page -> block anchors for downstream agents."""
    anchors = {}
    for section in result.get("sections", []):
        anchors.setdefault(section["page"], []).append(
            {"kind": "section", "title": section["title"], "bbox": section["bbox"]}
        )
        for para in section.get("paragraphs", []):
            anchors.setdefault(para["page"], []).append(
                {"kind": "paragraph", "quote": para["quote"][:120], "bbox": para["bbox"]}
            )
    for table in result.get("tables", []):
        anchors.setdefault(table["page"], []).append(
            {"kind": "table", "caption": table["caption"], "bbox": table["bbox"]}
        )
    return anchors


def qa(result: dict, question: str) -> dict:
    """Retrieve the best-matching passages; never invent an answer.

    Scoring runs over three-paragraph windows so a one-line PDF block does not
    lose the sentence that actually carries the value being asked about.
    """
    tokens = set(re.findall(r"[\u4e00-\u9fff]|[A-Za-z0-9]{3,}", question.lower()))
    if not tokens:
        return {"question": question, "status": "无法判断", "evidence": [], "confidence": 0.0,
                "detail": "question has no indexable tokens"}

    scored = []
    for section in result.get("sections", []):
        paras = section.get("paragraphs", [])
        for index, para in enumerate(paras):
            window = " ".join(p["text"] for p in paras[max(0, index - 1): index + 2]).lower()
            hits = sum(1 for token in tokens if token in window)
            if hits:
                scored.append((hits / len(tokens), para, section, window))
    if not scored:
        return {"question": question, "status": "无法判断", "evidence": [], "confidence": 0.0,
                "detail": "no passage in the extracted text matches the question terms"}

    scored.sort(key=lambda item: item[0], reverse=True)
    coverage, para, section, window = scored[0]
    candidates = []
    seen = set()
    for cov, candidate_para, candidate_section, candidate_window in scored:
        if len(candidates) >= 4:
            break
        key = candidate_para["text"][:60]
        if key in seen:
            continue
        seen.add(key)
        candidates.append(
            {
                "page": candidate_para["page"],
                "bbox": candidate_para["bbox"],
                "quote": candidate_window[:240],
                "section": candidate_section.get("title"),
                "coverage": round(cov, 2),
            }
        )
    return {
        "question": question,
        "status": "answered" if coverage >= 0.6 else "partial",
        "answer_excerpt": window[:400],
        "section": section.get("title"),
        "confidence": round(coverage, 2),
        "evidence": candidates,
        "method": "lexical retrieval over three-paragraph windows of layout-ordered text (no generative answer)",
    }


def load_case(path: str) -> dict:
    with open(path, encoding="utf-8") as handle:
        data = json.load(handle)
    return data if isinstance(data, dict) else {"case": data}


def find_pdf(case: dict, fallback: str | None) -> str | None:
    if fallback:
        return fallback
    for key in ("pdf", "pdf_path", "pdfPath", "material", "file", "document", "input"):
        value = case.get(key)
        if isinstance(value, str) and value.lower().endswith(".pdf"):
            return value
        if isinstance(value, dict):
            for nested in value.values():
                if isinstance(nested, str) and nested.lower().endswith(".pdf"):
                    return nested
    return None


def questions_from_case(case: dict) -> list[str]:
    for key in ("questions", "question", "tasks", "queries"):
        value = case.get(key)
        if isinstance(value, str):
            return [value]
        if isinstance(value, list):
            out = []
            for item in value:
                if isinstance(item, str):
                    out.append(item)
                elif isinstance(item, dict):
                    for k in ("question", "text", "prompt", "task"):
                        if isinstance(item.get(k), str):
                            out.append(item[k])
                            break
            if out:
                return out
    return []


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Layout-aware scientific PDF parser")
    parser.add_argument("--pdf", help="path to PDF (overrides case.json discovery)")
    parser.add_argument("--case", help="path to case.json")
    parser.add_argument("--question", action="append", default=[], help="question to answer from the text")
    parser.add_argument("--out", default="result.json", help="output JSON path")
    parser.add_argument("--case-id", help="case identifier recorded in the output")
    args = parser.parse_args(argv)

    case = load_case(args.case) if args.case else {}
    pdf = find_pdf(case, args.pdf)
    if not pdf:
        raise SystemExit("no PDF given: pass --pdf or a case.json containing a PDF path")

    params = {
        "body_size_fallback": BODY_SIZE_FALLBACK,
        "heading_ratio": HEADING_RATIO,
        "header_band": HEADER_BAND,
        "footer_band": FOOTER_BAND,
    }
    doc = Document(pdf, params)
    result = doc.parse()
    result["case_id"] = args.case_id or case.get("case_id") or case.get("id")
    result["input"] = {
        "pdf": os.path.abspath(pdf),
        "case": os.path.abspath(args.case) if args.case else None,
    }
    result["evidence_index"] = evidence_index(result)

    questions = list(args.question) + questions_from_case(case)
    if questions:
        result["qa"] = [qa(result, q) for q in questions]

    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as handle:
        json.dump(result, handle, ensure_ascii=False, indent=1)
        handle.write("\n")

    summary = {
        "status": result["status"],
        "pages": result["document"]["pages"],
        "sections": len(result["sections"]),
        "tables": len(result["tables"]),
        "figures": len(result["figures"]),
        "formulas": len(result["formulas"]),
        "references": len(result["references"]),
        "uncertainties": len(result["uncertainties"]),
        "two_column_pages": result["layout"]["two_column_pages"],
        "out": os.path.abspath(args.out),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
