#!/usr/bin/env python3
"""Build small synthetic PDFs that exercise layout cases the real corpus lacks.

The fixtures are *layout* fixtures, not scientific content: they exist so the
reading-order, furniture-removal and table detectors can be checked against a
document whose ground truth is known exactly. Results derived from them are
reported separately from the real-paper results.
"""

from __future__ import annotations

import os

import pymupdf


def two_column_fixture(path: str) -> None:
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)

    def furniture(p, number: int) -> None:
        p.insert_text((72, 30), "Journal of Layout Fixtures 12 (2026) 1-3", fontsize=8)
        p.insert_text((290, 820), str(number), fontsize=8)

    furniture(page, 1)
    page.insert_text((72, 80), "A Fixture Paper on Two Column Reading Order", fontsize=16, fontname="hebo")
    page.insert_text((72, 105), "A. Author, B. Author", fontsize=10)
    page.insert_textbox(
        pymupdf.Rect(72, 120, 523, 165),
        "Abstract: this fixture exists only to test reading order. Its left column comes "
        "first, its right column second, and the header must not leak into the body.",
        fontsize=10,
    )

    left = [
        "L1: first paragraph of the left column, spanning two lines because the "
        "column is narrow and the sentence is deliberately long.",
        "L2: second paragraph of the left column.",
        "L3: third paragraph of the left column.",
        "L4: fourth paragraph of the left column.",
    ]
    right = [
        "R1: first paragraph of the right column.",
        "R2: second paragraph of the right column.",
        "R3: third paragraph of the right column.",
        "R4: fourth paragraph of the right column.",
    ]
    y = 180
    for text in left:
        rect = pymupdf.Rect(72, y, 290, y + 90)
        page.insert_textbox(rect, text, fontsize=10)
        y += 95
    y = 180
    for text in right:
        rect = pymupdf.Rect(305, y, 523, y + 90)
        page.insert_textbox(rect, text, fontsize=10)
        y += 95

    # table page
    page2 = doc.new_page(width=595, height=842)
    furniture(page2, 2)
    page2.insert_text((72, 60), "Table 1: Measured fixture values", fontsize=11, fontname="hebo")
    x0, y0, w, h = 72, 80, 150, 24
    for row in range(3):
        for col in range(3):
            rect = pymupdf.Rect(x0 + col * w, y0 + row * h, x0 + (col + 1) * w, y0 + (row + 1) * h)
            page2.draw_rect(rect, color=(0, 0, 0), width=0.8)
    cells = [["Sample", "Gap (eV)", "Phase"], ["F1", "1.29", "bulk"], ["F2", "1.80", "monolayer"]]
    for row in range(3):
        for col in range(3):
            page2.insert_text(
                (x0 + col * w + 6, y0 + row * h + 16), cells[row][col], fontsize=9
            )
    page2.insert_text((72, 200), "Equation: E = h * c / lambda, with alpha = 0.5", fontsize=10)

    # third page keeps the running head detectable across three pages
    page3 = doc.new_page(width=595, height=842)
    furniture(page3, 3)
    page3.insert_textbox(
        pymupdf.Rect(72, 90, 523, 220),
        "Closing remarks for the layout fixture. This page exists so the running head appears "
        "on three pages and can be classified as furniture rather than body text. It also gives "
        "the parser a page whose character count is above the no-text-layer threshold.",
        fontsize=10,
    )
    doc.save(path)
    doc.close()


def scan_like_fixture(path: str) -> None:
    """A page of pure vector shapes: no text layer at all."""
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    for i in range(20):
        page.draw_line((72, 80 + i * 30), (520, 80 + i * 30), color=(0.2, 0.2, 0.2), width=1)
    doc.save(path)
    doc.close()


def main() -> None:
    here = os.path.dirname(os.path.abspath(__file__))
    out = os.path.join(here, "fixtures")
    os.makedirs(out, exist_ok=True)
    two_column_fixture(os.path.join(out, "two_column.pdf"))
    scan_like_fixture(os.path.join(out, "no_text_layer.pdf"))
    print("fixtures written to", out)


if __name__ == "__main__":
    main()
