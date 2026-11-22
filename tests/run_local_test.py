#!/usr/bin/env python3
"""Local acceptance test for the document-parsing skill.

Runs the parser on
  * three real research PDFs from the local corpus (single-column reprints),
  * a synthetic two-column fixture whose reading order is known exactly,
  * a synthetic no-text-layer fixture,
and asserts measurable properties. Every number in the report is read from the
parser output, so a failing run cannot silently look like a passing one.

    python tests/run_local_test.py

Writes tests/local-test-report.json and exits non-zero on any failed check.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
OUT = os.path.join(HERE, "out")
CORPUS = os.path.join(os.path.dirname(ROOT), "ws2_refs_pdf")

REAL_CASES = [
    ("10_10.1103_physrevlett.105.136805.pdf", "prl-mos2"),
    ("31_10.1038_nnano.2012.95.pdf", "nnano-valleytronics"),
    ("40_10.1021_nl3026357.pdf", "nl-ws2"),
]


def run_parser(pdf: str, out_json: str, case_id: str, question: str | None = None) -> dict:
    cmd = [
        sys.executable,
        os.path.join(ROOT, "scripts", "parse_pdf.py"),
        "--pdf", pdf,
        "--out", out_json,
        "--case-id", case_id,
    ]
    if question:
        cmd += ["--question", question]
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
    if proc.returncode != 0:
        raise RuntimeError(f"parser failed on {pdf}: {proc.stderr[-800:]}")
    with open(out_json, encoding="utf-8") as handle:
        return json.load(handle)


def check(name: str, ok: bool, detail=None) -> dict:
    return {"check": name, "ok": bool(ok), "detail": detail}


def verify_evidence(result: dict, pages: int) -> bool:
    """Every section/table/figure anchor points into the real page count."""
    for section in result["sections"]:
        if not (1 <= section["page"] <= pages) or not section.get("bbox"):
            return False
        for para in section.get("paragraphs", []):
            if not (1 <= para["page"] <= pages) or not para.get("bbox"):
                return False
    for table in result["tables"]:
        if not (1 <= table["page"] <= pages) or not table.get("bbox"):
            return False
    for figure in result["figures"]:
        if not (1 <= figure["page"] <= pages) or not figure.get("bbox"):
            return False
    return True


def main() -> int:
    os.makedirs(os.path.join(HERE, "fixtures"), exist_ok=True)
    os.makedirs(OUT, exist_ok=True)
    subprocess.run([sys.executable, os.path.join(HERE, "make_fixtures.py")], check=True)

    checks: list[dict] = []
    cases: list[dict] = []

    for filename, case_id in REAL_CASES:
        pdf = os.path.join(CORPUS, filename)
        out_json = os.path.join(OUT, case_id + ".json")
        if not os.path.exists(pdf):
            cases.append({"case": case_id, "skipped": True, "reason": "pdf not present in local corpus"})
            continue
        result = run_parser(pdf, out_json, case_id)
        pages = result["document"]["pages"]
        sections = result["sections"]
        refs = result["references"]
        summary = {
            "case": case_id,
            "pdf": filename,
            "pages": pages,
            "status": result["status"],
            "sections": len(sections),
            "paragraphs": sum(len(s.get("paragraphs", [])) for s in sections),
            "tables": len(result["tables"]),
            "figures": len(result["figures"]),
            "formulas": len(result["formulas"]),
            "references": len(refs),
            "uncertainty_items": len(result["uncertainties"]),
            "two_column_pages": result["layout"]["two_column_pages"],
            "evidence_valid": verify_evidence(result, pages),
        }
        cases.append(summary)
        checks += [
            check(f"{case_id}:contains_text", summary["paragraphs"] > 0, summary["paragraphs"]),
            check(f"{case_id}:status_ok", result["status"] in ("success", "partial"), result["status"]),
            check(f"{case_id}:evidence_valid", summary["evidence_valid"]),
            check(f"{case_id}:no_control_chars_in_titles",
                  all(not any(ord(c) < 32 for c in s["title"]) for s in sections)),
            check(f"{case_id}:page_count_positive", pages > 0, pages),
        ]
        if case_id == "prl-mos2":
            checks.append(check("prl:references_detected", len(refs) >= 20, len(refs)))

    # ---- two-column fixture ----
    fixture = os.path.join(HERE, "fixtures", "two_column.pdf")
    result = run_parser(fixture, os.path.join(OUT, "fixture-two-column.json"), "fixture-two-column")
    first_section = result["sections"][0] if result["sections"] else {"paragraphs": []}
    page1_text = " ".join(p["text"] for p in first_section.get("paragraphs", []) if p["page"] == 1)
    all_text = " ".join(
        p["text"] for s in result["sections"] for p in s.get("paragraphs", [])
    ) + " " + " ".join(s["title"] for s in result["sections"])
    order = [token for token in ("L1", "L2", "L3", "L4", "R1", "R2", "R3", "R4") if token in page1_text]
    expected = ["L1", "L2", "L3", "L4", "R1", "R2", "R3", "R4"]
    checks += [
        check("fixture:two_column_page_detected", result["layout"]["two_column_pages"] >= 1,
              result["layout"]["two_column_pages"]),
        check("fixture:column_reading_order", order == expected, order),
        check("fixture:running_header_removed",
              "Journal of Layout Fixtures" not in all_text),
        check("fixture:table_detected_on_page2",
              any(t["page"] == 2 for t in result["tables"]),
              [t["page"] for t in result["tables"]]),
    ]
    if result["tables"]:
        table = result["tables"][0]
        checks.append(check("fixture:table_cells_present", "F1" in table["markdown"] and "1.29" in table["markdown"],
                            table["markdown"][:80]))

    # ---- no text layer fixture ----
    scan = os.path.join(HERE, "fixtures", "no_text_layer.pdf")
    result = run_parser(scan, os.path.join(OUT, "fixture-no-text.json"), "fixture-no-text")
    checks += [
        check("scan:reported_failed_or_partial", result["status"].startswith(("failed", "partial")), result["status"]),
        check("scan:uncertainty_recorded",
              any(u["issue"] == "no_text_layer" for u in result["uncertainties"]),
              [u["issue"] for u in result["uncertainties"]]),
    ]

    # ---- QA retrieval on a real paper ----
    if os.path.exists(os.path.join(OUT, "prl-mos2.json")):
        prl_json = os.path.join(OUT, "prl-mos2.json")
        result = run_parser(os.path.join(CORPUS, REAL_CASES[0][0]), prl_json, "prl-mos2",
                            question="What is the band gap of bulk MoS2?")
        qa = (result.get("qa") or [{}])[0]
        checks.append(check("prl:qa_finds_band_gap",
                            "1.29" in json.dumps(qa, ensure_ascii=False),
                            {"status": qa.get("status"), "confidence": qa.get("confidence")}))
        checks.append(check("prl:qa_evidence_has_page", bool(qa.get("evidence")) and
                            all(e.get("page") for e in qa.get("evidence", []))))

    passed = sum(1 for c in checks if c["ok"])
    report = {
        "generated_by": "tests/run_local_test.py",
        "parser": "scripts/parse_pdf.py",
        "real_cases": cases,
        "checks_passed": passed,
        "checks_total": len(checks),
        "checks": checks,
        "note": "fixtures are synthetic layout documents; the three real cases are published papers "
                "from the local corpus, not the organisers' hidden cases",
    }
    with open(os.path.join(HERE, "local-test-report.json"), "w", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=1)
        handle.write("\n")

    for c in checks:
        if not c["ok"]:
            print("FAIL", c["check"], c.get("detail"))
    print(json.dumps({"checks_passed": passed, "checks_total": len(checks),
                      "report": os.path.join(HERE, "local-test-report.json")}, indent=1))
    return 0 if passed == len(checks) else 1


if __name__ == "__main__":
    sys.exit(main())
