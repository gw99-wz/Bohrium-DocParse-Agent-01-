---
name: docparse-evidence
description: "Parse a scientific PDF into an evidence-anchored structure: reading order for single/multi-column pages, running-header and footer removal, section hierarchy, tables with cell content, figure/formula anchors, references, and page+bbox evidence for every extraction. Trigger on: parse this paper, extract sections/tables from a PDF, correct column reading order, where does this claim appear, build a structured result.json from a research PDF, 文献解析, PDF 结构化, 多栏阅读顺序, 表格提取, 证据定位."
---

# docparse-evidence — layout-aware scientific PDF parsing

Use this skill whenever a task hands you a research PDF and asks for text, structure,
tables, formulas, references, or answers that must point back to the source.

The skill encodes one rule above all others: **no claim without a page anchor.** Text
that cannot be read is reported as unreadable, never reconstructed from context.

## When this skill applies

- The input is a real research PDF (journal article, preprint, thesis chapter).
- The task asks for full text, section structure, tables, figure/table captions,
  formula presence, references, or question answering over a PDF.
- The task requires the evidence to be checkable: page number, bounding box, quote.

Do **not** use it to invent content for scanned pages that have no text layer, and do
not use it to translate or summarise without also emitting the page-level evidence.

## Inputs

The harness provides directories at run time:

| Path | Meaning |
|---|---|
| `/input/case.json` | case metadata: `case_id`, the PDF path, task questions/constraints |
| `/input/task.md` (or the statement) | what the case asks for |
| PDF itself | the material; there is **no pre-extracted text layer** supplied |

The PDF path is looked up in `case.json` under `pdf`, `pdf_path`, `material`, `file`,
`document`, or `input`; pass it explicitly with `--pdf` when the key is uncommon.

## Outputs

Work under `/app/work`; write the final artifact to `/app/output/result.json`.
`result.json` always contains:

| Field | Content |
|---|---|
| `case_id` | copied from `case.json` |
| `status` | `success`, `partial`, or `failed:<reason>` — honest, never optimistic |
| `document` | pages, body font size, detected running heads/footers, SHA-256 of the PDF |
| `sections[]` | `level`, `title`, `page`, `bbox`, `paragraphs[]` (each with `text`, `page`, `bbox`, `quote`) |
| `tables[]` | `page`, `bbox`, `rows`, `cols`, `markdown`, caption |
| `figures[]` | `page`, `bbox`, caption |
| `formulas[]` | `page`, `bbox`, `text`, `symbols` (structure only, semantics not interpreted) |
| `references[]` | `index`, `text`, `page`, `bbox` |
| `qa[]` | when the case asks questions: `status`, `answer_excerpt`, `confidence`, `evidence[]` |
| `uncertainties[]` | `issue`, `page`, `detail` for everything unreadable or ambiguous |
| `layout` | per-page block/char counts, two-column page count |
| `evidence_index` | page → anchors, for quick downstream lookup |

`status` is `partial` whenever any page yielded fewer than 80 characters (scanned
page, image-only page, failed font); `failed:no_text` when the whole file has none.

## Workflow

1. **Read the case first.** `case.json` gives the PDF path and the questions. Do not
   assume the PDF content from filenames or labels.
2. **Run the parser.**
   ```bash
   python scripts/parse_pdf.py --case /input/case.json --out /app/output/result.json
   ```
   With questions in `case.json`, `qa[]` is filled by lexical retrieval over the
   layout-ordered text. Add `--question "…"` for ad-hoc questions.
3. **Inspect `status` and `uncertainties[]` before building on the result.** Any
   `no_text_layer` page must be reported to the user as unreadable; do not paraphrase
   it from surrounding pages.
4. **Answer from evidence.** For each question, quote `answer_excerpt` and cite the
   page/bbox from `qa[].evidence[]`. If `qa[].status` is `partial` or the coverage is
   below 0.6, say the material does not state it.
5. **Return the structured result** and a short prose summary that cites page numbers.
   Never replace `result.json` with a free-text answer alone.

## Evidence rules

- Page numbers are 1-based, bounding boxes are PDF points `[x0, y0, x1, y1]`.
- `quote` keeps the verbatim beginning of the paragraph; the full text is in `text`.
- Tables keep the cell grid (`markdown`); merged cells are reported as they come out
  of the detector and are **not** silently repaired.
- Formulas are exported as text with their symbol inventory. Equations rendered with
  unembedded Type1 math fonts can arrive as control characters; those blocks are
  reported under `uncertainties` (`unreadable_text_block`) instead of being guessed.

## Failure handling

| Situation | Behaviour |
|---|---|
| Encrypted PDF | stop with a clear error; decryption is out of scope |
| No text layer on a page | keep the page, emit `no_text_layer` uncertainty, `status: partial` |
| Whole file scanned | `status: failed:no_text`; suggest OCR as an explicit next step |
| Math font garbled | emit `unreadable_text_block` with page/bbox; keep other blocks |
| No section headings detected | emit a single title-level section; do not invent headings |
| Question not answerable | `qa[].status: partial`/`无法判断` with zero or low-coverage evidence |

## Self-check before finishing

- `status` matches what the pages actually contain.
- Every `sections[].paragraphs[]`, `tables[]`, `figures[]` entry has `page` and `bbox`.
- `uncertainties[]` mentions each page whose characters fell below 80.
- `qa[].evidence[]` is non-empty for any question marked `answered`.
- The file is valid JSON and `case_id` is not null when the case supplied one.

## Resources

- `scripts/parse_pdf.py` — the parser (requires PyMuPDF ≥ 1.23; `import pymupdf`).
- `tests/run_local_test.py` — acceptance test, writes `tests/local-test-report.json`.
- `docs/usage.md` — full usage, schema and limitations.
- Budget: the skill ships 10 text files, well under the 50-file / 2 MB caps.

## Minimal example (示例, not a case result)

`examples/input-case.json` points at `examples/sample.pdf` and asks one question:

```bash
python scripts/parse_pdf.py --case examples/input-case.json --out examples/output.example.json
```

The output in `examples/output.example.json` is a *示意输出* produced by running the
parser on a synthetic three-page fixture; it shows the schema, not a benchmark result.
