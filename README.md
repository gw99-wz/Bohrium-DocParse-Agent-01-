# 阅读01 | 文献文档解析 — Bohrium DocParse Agent

玻尔分站赛 **product-arena-08-3027d350** 参赛作品仓库。

## 赛题

| 项目 | 内容 |
|---|---|
| 赛题 | 阅读01 \| 文献文档解析（科研阅读：回答问题，也给出依据） |
| Challenge ID | `product-arena-08-3027d350` |
| 赛题页 | https://play.bohrium.com/challenge/product-arena-08-3027d350 |
| 提交入口 | Playground（ARM bundle / 提交目录） |

## 交付核心

平台固定 3 个科研案例，分别在「基础 Agent」与「加载本 Skill 的 Agent」下运行，评分关注：

1. **提得全** — 正文、图表题注、脚注、参考文献逐字提取，不漏行、不混页眉页脚。
2. **排得对** — 双栏/多栏/跨页按原版面顺序，不串栏、不错行。
3. **拆得清** — 章节层级、表格行列与单元格归属、公式上下标与希腊字母，还原成可继续处理的结构。
4. **核得回** — 每段文字、每张表格可指回原文页码与区块；读不出处如实标注，不猜补。

交付物：`SKILL.md` + 脚本/资源 + 使用说明 + 最小调用示例；每个案例返回结构化结果（主结果、证据定位、运行状态、不确定性）。

## 仓库结构（规划）

```
SKILL.md              # Skill 本体（name/description YAML frontmatter）
scripts/              # 解析脚本与后处理
docs/
  challenge.md        # 官方题面原文（存档，便于复核）
  harbor-contract.md  # Harbor 运行契约要点整理
examples/             # 最小调用示例（输入 → 调用 → 示意输出）
README.md
GITHUB_URL.txt        # 提交目录根路径要求的链接文件
```

## 提交要求提醒

- 在提交目录根路径放置 `GITHUB_URL.txt`，第一行写本仓库链接。
- GitHub 公开链接需在未登录浏览器中可访问（本仓库为 Public）。
- 不提交预制答案；示例输出必须标明「示例」。
- Skill 资源上限：≤50 个 UTF-8 文本文件，单文件 ≤500KB，总计 ≤2MB，无符号链接。

## 本机环境与推送说明

- 本 Windows 环境无法直连 `github.com`（TLS 被阻断），`api.github.com` 与 `ghproxy.net` 可用。
- 因此提供 `tools/push_to_github.py`：通过 GitHub REST API 以快照方式提交（无需 git 协议）。
- 读取（fetch/clone）可走 `ghproxy.net` 镜像；推送走 API。
- 凭据：`GITHUB_TOKEN` 环境变量（git.exe 直连可用时也可配合 `~/.git-credentials`）。

### 推送

```powershell
# 快照式推送（默认 branch=main，读取 GITHUB_TOKEN）
python tools/push_to_github.py
```

```powershell
# 直连 git 可用时（如个人网络）
git push origin main
```

### 拉取

```powershell
git fetch ghproxy
git reset --hard ghproxy/main
```

## 时间

- 2026-08-18 初始化仓库骨架。
