# 使用说明（docparse-evidence）

本 Skill 把一份科研 PDF 解析成**带页码与版面证据**的结构化 `result.json`。所有抽取项
都能回到原文的页、块（bbox）与逐字片段，读不出来的内容进入 `uncertainties`，不猜补。

## 依赖

- Python ≥ 3.10
- PyMuPDF ≥ 1.23（模块名 `pymupdf`，旧版为 `fitz`），用于文本块、字库信息、表格检测
- 无网络依赖；无 OCR 依赖（扫描件按「不可读」如实上报，见限制）

安装：

```bash
pip install pymupdf
```

## 运行方式

### 1. 按 case.json 运行（评测主路径）

```bash
python scripts/parse_pdf.py --case /input/case.json --out /app/output/result.json
```

`case.json` 中的 PDF 路径按键名 `pdf` / `pdf_path` / `material` / `file` /
`document` / `input` 查找；`case_id`、`id` 键会写入输出；`questions` / `question` /
`tasks` / `queries` 会触发 `qa[]`。

### 2. 直接指定 PDF

```bash
python scripts/parse_pdf.py --pdf paper.pdf --out result.json --case-id my-case
```

### 3. 追加临时问题

```bash
python scripts/parse_pdf.py --pdf paper.pdf --out result.json \
  --question "What is the band gap of bulk MoS2?"
```

## 输出字段

| 字段 | 说明 |
|---|---|
| `schema` | 固定 `docparse/1.0` |
| `status` | `success` / `partial` / `failed:no_text`；只要有页面字符数 < 80 即为 `partial` |
| `document.pages` | 页数；`sha256` 为输入指纹 |
| `document.running_headers/footers` | 被识别并剔除的页眉页脚文本 |
| `sections[]` | 章节：`level`、`title`、`page`、`bbox`、`paragraphs[]` |
| `paragraphs[]` | `text`（完整）、`quote`（逐字前 200 字）、`page`、`bbox` |
| `tables[]` | `page`、`bbox`、`rows`、`cols`、`markdown`、`caption` |
| `figures[]` | 图像块的 `page`、`bbox`、宽高、题注 |
| `formulas[]` | 含数学符号或等式的块：`text`、`symbols`、`page`、`bbox` |
| `references[]` | 参考文献条目：`index`、`text`、`page`、`bbox` |
| `qa[]` | 问题检索结果：`status`、`answer_excerpt`、`confidence`、`evidence[]` |
| `uncertainties[]` | `no_text_layer` / `unreadable_text_block` / `table_detection_unavailable` |
| `layout.pages[]` | 每页块数、字符数、表格/图/公式计数 |
| `layout.two_column_pages` | 判定为双栏的页数 |
| `evidence_index` | 页码 → 锚点列表，便于下游直接定位 |

## 方法要点

1. **版面优先**：按字号众数估计正文字号；大于正文 1.1 倍或加粗的短块作为标题候选。
2. **多栏阅读顺序**：用文本块 x 区间找双栏切分线；跨栏块（标题/摘要/宽表注）作为分段锚点，
   段内先左栏自上而下、再右栏自上而下，避免串栏。
3. **页眉页脚剔除**：页边带内、在 ≥30% 页面重复出现（≥3 页）的文本判为版面元素剔除；
   纯页码行同样剔除。
4. **表格**：PyMuPDF `find_tables()` 取网格与单元格，输出 Markdown 表格；
   合并单元格按检测结果原样保留，不做静默修补。
5. **证据**：每个章节/段落/表/图/公式都带 `page`（1 基）与 `bbox`（PDF 点）；
   `quote` 为逐字片段。
6. **问答**：对版面顺序化后的段落做三段落窗口词法检索，返回最佳片段 + 证据列表，
   **不生成**模型答案，因此不会编造；覆盖率 < 0.6 时报 `partial`。

## 限制与已知问题（如实声明）

- 不支持加密 PDF（直接报错退出）。
- 扫描版/无文字层页面不做 OCR：`status: partial`，并在 `uncertainties` 记录页面。
  需要 OCR 时应调用平台的 `ocr-tool`，再对本 Skill 输出做对照。
- 数学公式若使用未内嵌的 Type1 字体，抽取结果可能是控制字符；此类块被记为
  `unreadable_text_block`，不重建公式。
- 双栏判定依赖文本块的 x 分布；整页图片或单栏宽块排版可能不触发。
- 表格检测对无线框（纯留白）表格较弱，可能漏检。
- 页眉页脚需要 ≥3 页重复才能识别；短文档可能把页眉当正文（会在 uncertainties 提示字符数，
  但不会伪造剔除）。
- 中文 PDF 未做分词；`qa` 使用字级匹配，效果弱于英文。

## 本地自测

```bash
python tests/run_local_test.py
```

会生成 `tests/fixtures/`（合成的双栏 / 无文字层样例）、`tests/out/*.json` 与
`tests/local-test-report.json`。报告中的数字全部来自实际运行，未通过的检查会以
非零退出码暴露。

## 资源预算

文本文件 10 个左右，单文件 < 100 KB，总计 < 300 KB，远低于 50 文件 / 500 KB / 2 MB 的
上限，无符号链接。
