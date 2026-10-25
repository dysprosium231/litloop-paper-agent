# 搭配 Agent 使用指南

LitLoop 是一个面向 Claude Code、Codex、ZCode 等 Agent 的 MCP 服务器与命令行工具，支持自动化检索、筛选、下载文献并写入本机 Zotero。

## 安装与配置详情

### 给 Agent 的步骤（也可以自己照着做）

1. 插件目录用 `~/plugins/litloop-paper-agent`：已存在就直接用，否则执行 `git clone https://github.com/dysprosium231/litloop-paper-agent.git ~/plugins/litloop-paper-agent`。
2. 用 Python 3.11+ 运行 `scripts/deploy.py`：

   ```bash
   python ~/plugins/litloop-paper-agent/scripts/deploy.py
   ```

   Windows 上路径是 `%USERPROFILE%\plugins\litloop-paper-agent\scripts\deploy.py`。脚本只用标准库，会做这几件事：
   - 同步代码；
   - 在 `~/.litloop/venv` 建环境并安装；
   - 向本机的 Claude Code、Claude Desktop（已有配置文件时）和 Codex 注册 MCP；
   - 复制技能并自检。

   已注册且可用的安装会原地更新，不会重建。
3. 把脚本最后输出的摘要（警告和下一步）转告用户，并提醒重启客户端。如果脚本因为“本地有未推送的改动”而退出，不要强行覆盖，先问用户。
4. 常用选项：
   - `--extras fast,vpnsci,patchright,zotero`：加装可选依赖，`zotero` 会把 zotero-mcp 装进同一环境；
   - `--with-find`：加装 scansci-find；
   - `--dry-run`：只看不改；
   - `--clients none`：只装不注册。

   没有 Python 3.11+ 或 Git 时，先请用户安装（Windows：`winget install Python.Python.3.12 Git.Git`）。

### 手动配置 MCP（Claude Desktop、Cursor、Cline、Cherry Studio 等）

先在虚拟环境里执行 `pip install -e ~/plugins/litloop-paper-agent`，然后在客户端里配置：

```json
{
  "mcpServers": {
    "litloop": {
      "command": "litloop",
      "args": ["run"]
    }
  }
}
```

各客户端的插件入口：
- **Codex**：`.codex-plugin/plugin.json`、`.mcp.json` 和 `skills/`；
- **ZCode**：[`zcode-plugin/`](../zcode-plugin/)，包含技能和 `/litloop` 快捷命令；
- **Claude Code**：`.claude/skills/` 加上面的 MCP 配置。

### 其他运行方式（HTTP 远程 / Web UI）

```bash
litloop run --mode streamable_http --host 127.0.0.1 --port 8000   # HTTP 模式，默认只监听本机
litloop web --port 8080                                           # Web UI：http://localhost:8080，默认只监听本机
```

## 怎么用

装好后直接对 Agent 说：

```text
找一下近五年城市热岛和绿地降温的论文，只要中科院 2 区以上，排除撤稿

这 30 篇按和我课题的相关度筛一遍，说明理由，我来挑

以这 3 篇为种子，把 SiC/Al 无压浸渗的文献尽量查全，分批给我筛

把我选中的这些下载下来，推送到 Zotero 的「热岛」分类

帮我下载 10.1038/nature12373

这个清单有 3000 篇，先分出哪些能直接下、哪些要走学校权限，再分批下载

这篇下载失败了，帮我诊断一下
```

也可以直接用命令行：

```bash
litloop search "urban heat island" --year-from 2021 --sort cited   # 检索
litloop search "carbon cycle" --out q.txt                         # 检索结果存成下载队列
litloop get 10.1038/nature12373                                   # 下载单篇
litloop batch q.txt --lanes                                       # 批量下载（支持 xlsx/csv/bib/队列文件）
litloop check                                                     # 检查依赖和环境
```

<!-- mcp-tools:start -->
<details>
<summary><strong>MCP 工具全表</strong>（21 个，按用途分组）</summary>

| 想做什么 | 工具 |
|---|---|
| 下载单篇（可拉附件、转 Markdown） | `litloop_download` |
| 批量下载（列表 / xlsx·csv·bib·APA 文件） | `litloop_batch_download` |
| 检索、作者检索、深度检索（关键词 + 共同引用扩展，分批筛选） | `litloop_search` · `litloop_find` · `litloop_deep_search` |
| 筛选记录（相关度 + 理由，记录选择，生成下载队列） | `litloop_screening` |
| 术语表（缩写、同义词；深度检索自动展开，Agent 添加的要用户确认） | `litloop_glossary` |
| 准备队列（校验 / OA 定位 / 构建） | `litloop_prepare_queue` |
| 机构登录与状态（WebVPN / CARSI / EZProxy / 出版商 SSO） | `litloop_login` · `litloop_channel_status` · `litloop_schools` |
| 引文 / 元数据 / Zotero | `litloop_citation` · `litloop_zotero_push` |
| 配置 / 诊断 / Tor / 缓存 / Elsevier Key | `litloop_config` · `litloop_diagnostics` · `litloop_tor` · `litloop_cache_clear` · `litloop_elsevier_setup` |
| 取消进行中的下载 / 批量（停止竞速、跳过未开始的论文、关闭窗口） | `litloop_cancel` |

每个工具的参数与说明由 MCP `tools/list` 自带，Agent 会自动看到。

</details>
<!-- mcp-tools:end -->

## 检索与筛选

目标、原则和验收标准见 [CHARTER.md](../CHARTER.md)。它要解决四个问题：结果杂、读不过来、不好判断质量、查不全。

**检索结果里有什么**

| 字段 | 说明 |
|---|---|
| 被引数 | 数据源给出的真实被引次数，可按它排序（`sort="cited"`，也认 `cited_by_count`、`citations`） |
| 摘要 | 依次从 Semantic Scholar、Scopus（配了 Elsevier Key 时）、Crossref 补全，并做清洗：去掉 JATS 标签和版权行，过长的摘要保留开头和结尾 |
| 撤稿标记 | `retracted`、`concern`，以及具体的声明列表。有期刊分区插件时查本地 Retraction Watch 库，否则用 OpenAlex 的撤稿标记 |
| 期刊分区 | 中科院大类 / 小类分区、JCR 分区、预警期刊（需要期刊分区插件） |

**过滤参数**（`litloop_search`，默认都不过滤）

| 参数 | 作用 |
|---|---|
| `year_from` / `year_to` | 年份范围，由数据源直接过滤 |
| `max_cas_zone` | 中科院大类分区上限，如 `2` 表示 1–2 区 |
| `max_cas_sub_zone` | 中科院小类分区上限 |
| `max_jcr_quartile` | JCR 分区上限，如 `2` 表示 Q1–Q2 |
| `exclude_warning` | 排除国际期刊预警名单上的期刊 |
| `exclude_retracted` | 排除已撤稿论文 |

被过滤掉的论文数量会在结果的 `filtered` 字段里列出，不会悄悄消失。

**筛选记录**

用 `litloop_screening` 完成：
- Agent 读完摘要后，用 `save` 存下每篇的相关度评级和理由；
- 你挑选后，用 `decide` 记下选中和排除的论文，并可以输出下载队列；
- `list` 和 `show` 用来回看以前的筛选。

记录存在 `~/.litloop/screening/`，每次一个 JSON 文件和一个方便阅读的 Markdown 文件。快速检索直接给结果，深入检索才会先和你确认筛选标准。

**深度检索**（`litloop_deep_search`）

要把一个课题查全时用。Agent 给出问题、4–8 组英文检索词、核心词和几篇种子文章；你在 Zotero 里已经有这个课题的分类时，也可以直接把整个分类当种子（`seed_collection`）：
1. 每组检索词各取 100 条候选，再找出和种子共同引用较多、且标题或摘要含核心词的文章，合并成候选池。核心词可以写几条，**每条都必须出现**（“并且”）；一条里用 `|` 写同一概念的不同说法，如 `["Al|aluminum|aluminium", "SiC"]`，照顾只写缩写的文章。和普通单词同形的元素符号（如 La、Ce、Er、Eu、In）会匹配到法语、德语等普通单词，开局时会提醒改写全称；
2. 每次给 Agent 一批（默认 30 篇），Agent 评分后存进筛选记录。第一批按“共同引用 + 检索词匹配”排序；之后由一个轻量模型根据全部评分（相关和不相关的都用）重新排序，这是系统综述筛选常用的持续主动学习；
3. 评为“高”的文章自动成为新种子，再往外扩展。你给的种子权重最高，避免越滚越偏；
4. 每次出新一批前，先给缺摘要的候选补摘要（Semantic Scholar、Scopus、Crossref 同时问，先答先用）；
5. 最近一批的高相关比例降到最好一批的 1/6 以下，或已筛满 200 篇时，建议停止。给了必找清单时，会报告每篇找到没有、排在第几。

为了不把 OpenAlex 的每日额度一次用光，扩展候选池有三道上限（LitLoop 自己定的值，见 [API_LIMITS.md](API_LIMITS.md)）：每轮最多约 800 积分；一篇种子的参考文献最多取 200 篇，被多篇种子引用的优先，没轮到的留到下一轮；当天剩余不足 1000 积分时不再扩展。

验收结果：SiC/Al 测试集筛 150 篇找到 19 篇目标中的 17 篇（第一版筛 90 篇只找到 12 篇）；PDC 气凝胶测试集筛 120 篇找到 26 篇中的 24 篇，150 篇时建议停止。离陶瓷较远的「稀土药物载体」测试集筛 150 篇只找到 9 篇中的 2 篇：这个课题分支多（配位聚合物、磷酸钙、生物玻璃、空心氧化物……），分支之间很少互相引用，排序容易被其中一支占满，还在改进。

改检索参数前，可以用 `scripts/eval_deep_search.py <测试集>` 按存下的评分自动重放一次深度检索，看“读到第 N 篇找到几篇”，一次约几百 OpenAlex 积分。

> 期刊分区和本地撤稿库由配套插件 litloop-journal-rank 提供，它目前还没有公开。没装这个插件时，检索的其他功能照常可用。
