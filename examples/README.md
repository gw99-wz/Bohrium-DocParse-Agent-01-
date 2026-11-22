# 最小调用示例

> 以下输出为**示意输出（示例）**，由合成样例 `sample.pdf`（3 页版面夹具）实际运行得到，
> 用于说明接口与 schema，**不是**官方固定案例的结果。

## 输入

- `input-case.json`：声明 `case_id`、PDF 路径与一个问题
- `sample.pdf`：3 页合成 PDF（双栏页 + 表格页 + 收尾页，均带页眉页脚）

## 调用

```bash
cd examples
python ../scripts/parse_pdf.py --case input-case.json --out output.example.json
```

## 示意输出（节选）

`output.example.json` 的关键字段：

```json
{
 "schema": "docparse/1.0",
 "status": "success",
 "case_id": "example-fixture",
 "document": {"pages": 3, "body_font_size": 10.0,
              "running_headers": ["Journal of Layout Fixtures 12 (2026) 1-3"]},
 "sections": [
   {"level": 1, "title": "A Fixture Paper on Two Column Reading Order",
    "page": 1, "bbox": [...],
    "paragraphs": [
      {"text": "L1: first paragraph of the left column, ...",
       "page": 1, "bbox": [...], "quote": "L1: first paragraph ..."}
    ]}
 ],
 "tables": [{"page": 2, "rows": 3, "cols": 3,
             "markdown": "| Sample | Gap (eV) | Phase |\\n|---|---|---|\\n| F1 | 1.29 | bulk | ..."}],
 "qa": [{"question": "What does the left column of the first page contain?",
         "status": "answered", "answer_excerpt": "...",
         "evidence": [{"page": 1, "bbox": [...]}]}],
 "uncertainties": [],
 "layout": {"two_column_pages": 1}
}
```

要点：

- 页眉 `Journal of Layout Fixtures ...` 被识别为版面元素并从正文剔除；
- 双栏阅读顺序为 L1→L4 后 R1→R4，不串栏；
- 表格 3×3 单元格完整输出为 Markdown；
- 每个段落/表格都带 `page` 与 `bbox`，可回原文核对。
