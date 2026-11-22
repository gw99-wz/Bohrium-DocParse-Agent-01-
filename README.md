# 阅读01 | 文献文档解析 — 证据锚定的科研 PDF 解析 Skill

玻尔分站赛 **product-arena-08-3027d350** 参赛作品。

- 赛题：https://play.bohrium.com/challenge/product-arena-08-3027d350
- 作品形态：可被 Agent 调用的 Skill（`SKILL.md` + 解析脚本 + 测试 + 文档）
- 核心主张：**每条结论都能回到原文页码与区块（bbox），读不出的部分如实标注**

## 它解决什么

| 题面要求 | 本 Skill 的做法 |
|---|---|
| 提得全 | 块级文本抽取；页眉页脚按跨页重复度剔除；参考文献按编号切分 |
| 排得对 | 双栏检测 + 跨栏块分段锚点，段内先左栏后右栏，修串栏错行 |
| 拆得清 | 字号/加粗判定章节层级；`find_tables` 还原表格网格；公式按符号库存证 |
| 核得回 | 段落/表/图/公式全部带 `page` + `bbox` + 逐字 `quote`；不可读项进 `uncertainties` |

## 快速开始

```bash
pip install pymupdf
python scripts/parse_pdf.py --case /input/case.json --out /app/output/result.json
```

直接指定 PDF：

```bash
python scripts/parse_pdf.py --pdf paper.pdf --out result.json --case-id demo
```

问题检索：

```bash
python scripts/parse_pdf.py --pdf paper.pdf --out result.json \
  --question "What is the band gap of bulk MoS2?"
```

## 仓库结构

```
SKILL.md                  # Skill 本体（YAML frontmatter + 工作流 + 证据规则）
scripts/parse_pdf.py      # 解析器：版面顺序、页眉页脚、章节、表格、公式、引用、问答
tests/run_local_test.py   # 本地验收测试（25 项断言，写 local-test-report.json）
tests/make_fixtures.py    # 合成双栏/无文字层测试夹具
tests/local-test-report.json  # 最近一次真实运行的测试报告
docs/usage.md             # 完整用法、字段说明、限制
docs/challenge.md         # 官方题面存档
docs/harbor-contract.md   # Harbor 运行契约摘要
docs/arm-protocol.json    # 官方 ARM 协议原文
examples/                 # 最小调用示例（示意输出，已标明）
GITHUB_URL.txt            # 提交目录要求的作品链接
tools/push_to_github.py   # 本机网络下的 API 推送工具（见下）
tools/export_session_trace.mjs  # 把 DSH 会话日志导出为 playground 可读的 JSONL 轨迹
```

轨迹（不随仓库公开，提交时用 `--trace` 单独传入）：

```bash
node tools/export_session_trace.mjs --out trace/session-trace.jsonl
```

## 本地测试（真实结果）

`python tests/run_local_test.py` 在 3 篇真实论文 + 2 个合成夹具上运行，最近一次 **25/25 通过**：

| 案例 | 页数 | 状态 | 段落 | 图 | 参考文献 | 不可读项 | 证据有效 |
|---|---|---|---|---|---|---|---|
| PRL 105, 136805（MoS₂） | 15 | success | 217 | 4 | 30 | 17 | ✅ |
| Nat. Nanotechnol. 2012（valleytronics） | 15 | partial | 171 | 11 | 0 | 7 | ✅ |
| Nano Lett. 2012（WS₂） | 25 | success | 331 | 6 | 35 | 16 | ✅ |
| 合成双栏夹具 | 3 | success | — | 0 | 0 | 0 | ✅ |
| 合成无文字层夹具 | 1 | failed:no_text | 0 | 0 | 0 | 1 | ✅ |

数字直接读自 `tests/local-test-report.json`；报告未通过时脚本以非零码退出。
`partial` 表示有页面字符数 < 80（该 PDF 有图表页），不是解析失败。

## 限制（如实声明）

- 不做 OCR：扫描件页记为 `no_text_layer`，`status: partial`，建议接平台 `ocr-tool` 后再对照。
- 未内嵌 Type1 数学字体导致的乱码块记为 `unreadable_text_block`，不重建公式。
- 无线框表格可能漏检；合并单元格按检测结果原样保留，不静默修补。
- 页眉页脚需 ≥3 页重复才识别；短文档可能保留页眉（该情况会进入 uncertainties）。
- `qa` 是词法检索 + 三段落窗口，不生成答案，因此不会编造，但覆盖面有限。

## 提交

- 提交目录根路径的 `GITHUB_URL.txt` 第一行即本仓库链接。
- 平台使用固定 3 个案例，分别对比「基础 Agent」与「加载本 Skill 的 Agent」。
- 运行时契约见 `docs/harbor-contract.md`：输入 `/input/case.json`，输出 `/app/output/result.json`，
  预算 64 次请求 / 900 秒 / 16k tokens。

## 本机推送说明

本 Windows 环境无法直连 `github.com`（TLS 被阻断），`api.github.com` 与 `ghproxy.net` 可用：

```powershell
# 读：镜像拉取
git fetch ghproxy && git reset --hard ghproxy/main
# 写：REST API 快照提交（无需 git 协议）
$env:GITHUB_TOKEN = "<token>"; python tools/push_to_github.py
```

在直连可用的网络里，`git push origin main` 同样可用。
