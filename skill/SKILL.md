---
name: litloop
description: >
  Use this skill whenever the user wants to download academic papers, search for research literature,
  get citations (BibTeX/RIS/EndNote), manage WebVPN institutional proxy for paper access,
  import .bib files, or batch-download papers. This skill orchestrates the litloop MCP server
  which exposes 20 high-level tools covering download (OA/grey/institutional channels),
  discovery, queue preparation, diagnostics, and login.
  TRIGGER when: user mentions downloading papers, DOI, arXiv ID, Sci-Hub, paper search,
  literature review, citation export, WebVPN, institutional access, "帮我下载论文", "搜索文献",
  "批量下载", "论文下载", "文献检索", or provides a list of DOIs/arXiv IDs.
  SKIP: user is only discussing papers conceptually without intent to download/search/cite,
  or user asks about non-academic PDFs (invoices, reports, etc.).
---
<!-- 由 skills-src/ 生成：改那里的文件，再运行 python scripts/build_skills.py -->

# litloop — 学术论文下载 MCP 服务


## ⚠️ 修改反爬/嗅探/镜像相关代码前必读

先读 [`docs/PLAYBOOK.md`](../../docs/PLAYBOOK.md)：镜像健康唯一存储（domain_db wall_state 表）、
结构性失败签名表、节奏参数、已知死镜像清单、以及六条铁律（含"造轮子前先 grep 家底"——
镜像健康存储曾被重复造过三代）。

## 概述

litloop 是一个 MCP 服务器，提供 **20 个高层工具**，覆盖学术论文的搜索、下载（开放获取 / 灰色源 / 机构渠道）、发现与队列准备、引文导出和诊断排障。下载引擎支持 13+ 数据源并行竞速、100+ 中国高校 WebVPN。

## MCP 工具参考（20 个）

### 下载

| 工具 | 功能 | 关键参数 |
|------|------|----------|
| `litloop_download` | 单篇下载（OA/灰色源/机构级联） | `identifier`（必需）、`strategy`（可覆盖全局策略）、`markdown`/`bibtex`/`download_si`（可选） |
| `litloop_batch_download` | 批量下载 | `identifiers`（必需）、`scihub_enabled`、`batch_id`（断点续传）、`resume`（默认 true） |
| `litloop_cache_clear` | 清理下载缓存 | `identifier`（可选，省略清全部） |

**identifier 格式**：DOI（`10.1038/nature12373`）、DOI URL、arXiv ID（`2301.00001`）。

**下载策略（`download_strategy`，全局配置或单次 `strategy=` 覆盖）**：

| 策略 | 含义 |
|------|------|
| `fastest`（默认） | 全部源并行竞速（含灰色源，若 `scihub_enabled=true`） |
| `oa_first` | OA 源优先，失败后回退灰色源 |
| `scihub_first` | 灰色源优先，失败后回退合法源 |
| `scihub_only` | 仅 Sci-Hub |
| `legal_only` | 仅合法源（Unpaywall/出版商/OpenAIRE 等），不碰 Sci-Hub/LibGen |

**来源授权不可被调度扩张**：用户显式设置 `scihub_enabled=false` 或 `legal_only` 时，任何批量/车道调度都不会重新启用灰色源；只有显式 `--scihub`/`scihub_enabled=true` 或全局策略允许时才启用。

### 发现与队列准备

| 工具 | 功能 | 关键参数 |
|------|------|----------|
| `litloop_find` | ScanSci Find 引擎：`action=plan\|estimate\|smoke\|calibrate` 系统综述搜索协议 | `action`、`query`、`domain`、`depth`、`sample_size` |
| `litloop_deep_search` | 深度迭代检索与主动学习筛选：`action=start\|rate\|next\|status` | `action`（start/rate/next/status）、`question`、`queries`、`seeds`、`seed_collection`、`core_terms`、`criteria`、`must_find`、`session`、`ratings`、`batch_size` |
| `litloop_prepare_queue` | 准备下载队列：`action=verify\|resolve_oa\|build\|full` | `candidates_json`、`query`、`limit` |
| `litloop_search` | 关键词/作者搜索（OpenAlex），带摘要、期刊分区、撤稿标记 | `query`/`author`/`author_id`、`limit`、`year_from`、`year_to`、`sort`（cited/date）、`max_cas_zone`、`max_cas_sub_zone`、`max_jcr_quartile`、`exclude_warning`、`exclude_retracted` |
| `litloop_screening` | 保存筛选记录、记录用户选择 | `action`=save/decide/list/show、`question`、`criteria`、`items`、`session`、`selected`、`out_file` |
| `litloop_parse_list` | 解析论文列表文件（APA/BibTeX/DOI 列表/表格） | `file_path`（必需） |
| `litloop_citation` | 导出引文 | `identifier`（必需）、`format`（bibtex/ris/endnote/metadata） |
| `litloop_zotero_push` | 推送已下载论文和 PDF 到 Zotero（自动使用已安装的 zotero-mcp） | `identifier`（必需，需先下载）, `collections`, `tags` |

**重要**：`verify`/`resolve_oa` 是轻量操作，预算 45 秒内返回；超时会返回结构化错误（`stage`/`timeout_seconds`/`retryable`），不会无反馈地等 180 秒。`resolve_oa` 依赖 Unpaywall 邮箱配置（`litloop_config(key='email', ...)`），缺失时直接返回 `blocked_configuration` 而不是逐条失败。

### 配置、通道与诊断

| 工具 | 功能 | 关键参数 |
|------|------|----------|
| `litloop_config` | 读取/设置配置（值会脱敏） | `key`（可选）、`value`（可选） |
| `litloop_channel_status` | 机构渠道状态：`kind=webvpn\|carsi\|ezproxy\|browser\|webvpn_test` | `kind`、`doi`（仅测试） |
| `litloop_schools` | 搜索/设置 WebVPN 高校 | `action=search\|set`、`query`、`school` |
| `litloop_diagnostics` | 健康/网络/来源/设置诊断 | `check=health\|network\|sources\|setup` |
| `litloop_tor` | 内嵌 Tor：`action=install\|start\|stop` | `action`、`use_bridges` |

### 登录（机构渠道）

| 工具 | 功能 | 关键参数 |
|------|------|----------|
| `litloop_login` | 统一登录：`kind=publisher\|webvpn\|carsi\|ezproxy\|custom\|cookie_import` | `kind`（默认 publisher）、`identifier`、`publisher`、`custom_url`、`cookie_file` |
| `litloop_elsevier_setup` | Elsevier API Key 配置向导（免费，无需机构邮箱） | `test`（验证 key） |

## 工作流编排

### 流程 1：模糊研究查询 → 下载

```
1. litloop_find(action="plan"|"estimate", query="植物功能性状 气候变化")
   或 litloop_search(query="plant functional traits climate change", limit=20, sort="cited_by_count")
2. litloop_prepare_queue(action="full", candidates_json=<candidates>)
   → 验证 DOI + 解析 OA 位置（45s 预算，超时给出结构化错误）
3. litloop_batch_download(identifiers=[...])
```

**关键点**：搜索后必须让用户确认，不要自动下载所有结果。

### 流程 2：深度检索与主动学习筛选（查全课题）

步骤、评分规则和返回字段见下文「检索与筛选」一节。

### 流程 3：论文列表全文下载

```
1. litloop_parse_list(file_path="papers.md") → 查看解析结果
2. litloop_batch_download(identifiers=entries)（或 resolve_and_download 的等价组合）
```

### 流程 4：WebVPN 机构代理

```
1. litloop_schools(action="search", query="清华") → 找到学校
2. litloop_schools(action="set", school="清华大学")
3. litloop_login(kind="webvpn") → 浏览器 CAS 认证
4. litloop_channel_status(kind="webvpn_test", doi="10.1038/nature12373") → 确认连通
5. litloop_download(identifier="...") → 自动走 WebVPN 渠道
```

### 流程 5：付费论文登录下载（出版社 SSO）

当下载返回 `error_type="paywall"` 和 `action="login_required"` 时：

```
1. litloop_download(identifier="10.1126/science.aec6396")
   → 返回 {"error_type": "paywall", "action": "login_required", ...}
2. litloop_login(identifier="10.1126/science.aec6396")  # kind=publisher 默认
   → 打开浏览器到论文页 → 用户点 "Access through your institution" → 选择机构 → SSO → 关闭浏览器
3. litloop_download(identifier="10.1126/science.aec6396") → 用已保存 cookies 成功下载
```

**要点**：
- 任何有机构账号的用户都能用，无需预配置 WebVPN/CARSI
- Cookies 持久化，同一出版商登录一次即可
- 浏览器引擎为 **CloakBrowser**（Playwright 兼容反检测浏览器），能通过 Cloudflare Turnstile；出版社检测 TLS 指纹，Python HTTP 客户端即使带 cookies 也可能 403

### 流程 6：Elsevier API 快速通道（1-2 秒下载）

```
1. litloop_elsevier_setup → 浏览器注册指引 → 复制 API Key
2. litloop_config(key="elsevier_api_key", value="...")
3. litloop_elsevier_setup(test=true) → 验证
4. 后续 10.1016/ 开头 DOI 自动走 API 直接下载（无需 insttoken：校园网出口 + API key 即可）
```

**NOT_ENTITLED 含义**：未连校园网或学校未订阅该刊，不是缺 insttoken。

### 流程 7：内嵌 Tor（灰色源被墙时）

```
1. litloop_tor(action="install")   # 首次下载 Tor Expert Bundle
2. litloop_tor(action="start")     # 受限网络可 use_bridges=true
3. litloop_download(identifier="...", strategy="scihub_only")
```

## 检索与筛选（AI 给理由，用户决定）

用户要找文献时按这个流程，不要直接甩一个列表：

1. **定标准**：把问题拆成 2–5 条纳入标准（研究对象、关注性质或现象、方法、文章类型、年份）。
   - 快速查找：直接写在结果上方，不等确认。
   - 课题调研、综述等深度检索：先列出标准请用户确认或修改，再检索。
2. **检索**：`litloop_search` 用英文关键词；中文问题先译成英文检索词，必要时换 2–3 组同义词分别检索。用户在意期刊质量时用 `max_cas_zone` / `max_cas_sub_zone` / `max_jcr_quartile` / `exclude_warning`，不要擅自加过滤。
3. **逐篇判断**：读每篇的标题和 `abstract`，按标准给相关度 **高 / 中 / 低 / 不相关**，写一句中文理由，说明它对应哪条标准、缺了什么。`abstract_missing` 的文章在理由里注明“仅凭标题”。不要编造摘要里没有的内容。
   - **怎么分档**：研究对象、方法、关注点都符合 → 高；对象相同但方法或路线不同，或只覆盖问题的一部分，或与问题直接相关的综述 → 中；方法相同但对象是相邻体系，或泛泛的综述、书章 → 低；与问题无关 → 不相关。
   - **拿不准时往高评**：漏掉一篇相关文章的代价比多读一篇大得多。只有标题、但标题明确符合时也可以评高，理由里注明“仅凭标题”。
   - 用户定过的判定规则（如“某某路线算目标”）优先于上面的通用规则，并前后一致地用在所有批次上。
4. **保存**：`litloop_screening(action="save", question=..., criteria=[...], query=..., mode="quick"|"deep", items=[{doi, title, year, journal, journal_rank, cited_by_count, abstract_missing, retracted, concern, relevance, reason}, ...])`。同一课题的后续检索传 `session=` 合并进同一会话。
5. **呈现**：中文表格，列为 序号、相关度、文章、年份、期刊/分区、理由。**序号和顺序一律用 save 返回的 `numbering`**（保存时会按相关度重排），不要用自己发送时的顺序。`retracted` 标“⚠已撤稿”，`concern` 标“⚠关注声明”，`journal_warning` 标“⚠预警期刊”。不相关的文章也列出（可折叠在最后），**不替用户删除任何文章**。附上会话的 Markdown 文件路径。
6. **用户选择**：用户说“要 1、3、5”后调用 `litloop_screening(action="decide", session=..., selected=[...], rejected=[...], out_file=...)` 记录。每次 decide 记录的是**完整选择**，会覆盖上一次；用户追加或改选时传入全部选中项。把返回的 `chosen` 列表（序号 + 标题）念给用户核对，再按需要下载（`litloop_batch_download`）或推送 Zotero。

### 深度检索（要把一个课题查全时）

1. 标准确认后，请用户给 1–3 篇确定相关的种子文章（DOI）；没有时先用 `litloop_search` 找几篇请用户认。如果用户在 Zotero 里已经有这个课题的分类，用 `seed_collection="分类路径"`（如 `"1 陶瓷-金属复合材料/SiC/Al"`，也可以只写唯一的分类名）把其中有 DOI 的文献都当种子，可以和 `seeds` 一起用；返回的 `seed_collection` 报告用了几篇、几篇没有 DOI。分类较大时引用扩展会分几轮完成，额度上限照常生效。
2. `litloop_deep_search(action="start", question=..., queries=[4–8 组英文检索词], seeds=[DOI], core_terms=[1–3 个核心词], criteria=[...])`，返回第一批（默认 20 篇）和 `session`。**每个关键概念都要照顾缩写、全称、化学式和同义词**：很多文章只写缩写或化学式，或换一种说法。核心词里用 `|` 表示“任意一个”，例如 `Al|aluminum SiC`、`PDC|polymer-derived|preceramic`、`EMW|electromagnetic|microwave`、`MMC|metal-matrix`、`3D-printing|additive-manufacturing`；检索词里也要有用缩写和用全称的各一组。**每条核心词是一个必须出现的概念，多条之间是“并且”**：`["lanthanide|rare-earth|europium|gadolinium", "drug|doxorubicin"]` 表示稀土并且药物；同一概念的不同说法用 `|` 写在同一条里，不要拆成两条。元素符号和普通单词撞车的（La、Ce、Er、Eu、In、As 等，检索不区分大小写）只写全称，开局返回的 `core_term_notes` 会提醒。英美拼写（aluminum/aluminium、fiber/fibre、-ize/-ise 等）由插件自动处理，不用写。**核心词要覆盖整个问题的范围，不要只写其中的重点**：问题是“先驱体陶瓷，尤其 SiBCN”时，核心词写 SiBCN 会把其他 PDC 体系挡在引用扩展之外；应写成 `SiBCN|SiCN|SiOC|polymer-derived|preceramic` 这类覆盖全题的写法，重点留给评分。用户给了必找文章时传 `must_find=[DOI]`，结果的 `coverage` 会报告每篇的状态。
3. 逐篇判断后用 `litloop_deep_search(action="rate", session=..., ratings=[{"n": 1, "relevance": "高", "reason": "..."}, ...])` 按编号保存，标题和 DOI 由插件补上。**这一批每篇都要评**，返回的 `not_rated` 列出漏评的编号；没评的下一批会再出现。
   只有在设置里把翻译服务选为 Agent 时，`rate` 的返回才会要求翻译：`next_step` 写明哪些评分要附 `abstract_zh`（中文摘要），漏掉的列在 `untranslated` 里，用 `litloop_screening(action="translate", session=..., items=[{"doi": ..., "abstract_zh": ...}])` 补上。没有要求时不要翻译（默认由 app 调用 DeepSeek / DeepL 翻译，不耗对话额度）。
4. `litloop_deep_search(action="next", session=...)` 取下一批。评为“高”的文章会自动成为新种子；从第二批起排序由模型根据全部评分学习（`ranked_by="model"`），所以“低 / 不相关”也要如实评。
5. 返回 `stop_suggested=true` 时，把 `stop_reason` 告诉用户并建议停止；用户要继续就接着 `next`。用户也可能在桌面 app 里自己评分并点「继续检索」生成了新的一批：**回到对话接着检索时先 `action="status"`**，返回里有 `unrated` 就按其中的编号用 `rate` 评完（用户在 app 里给的评分会保留），再 `next`。返回里的 `remaining_estimate` 是按每批高相关比例的下降趋势粗略外推的剩余数（`remaining` 约还有几篇高、`next_batch` 下一批约几篇），一并告诉用户供决定，但说明这是粗略估计；比例还在上升时没有 `remaining`，说明课题还没读到尽头。随时可以用 `action="status"` 看已筛篇数和各相关度数量。
6. 筛完后按上面第 5、6 步呈现和让用户选择。跨了多批之后编号会变，**decide 时优先传 DOI**。

## 能力边界

| 请求 | 处理方式 |
|------|----------|
| 阅读/理解论文内容 | 不支持——只下载 PDF（可用 `markdown=true` 得 AI 可读文本层） |
| 翻译论文 | 需要其他工具 |
| 生成文献综述/摘要 | 需要 LLM 读取 PDF 后生成 |
| 下载非学术 PDF | 不支持 |
| 用户给了标题没有 DOI | 先 `search` 获取 DOI 再下载 |
| 批量 100+ 篇 | `batch_download`，并发数由 `batch_workers` 配置控制 |
| 需要机构权限的论文 | `login` 登录后重试下载 |
| 下载失败 | 结果含 `error_type`；`paywall`→登录流程，`rate_limited`→稍后重试，`cloudflare_blocked`→启动 CloakBrowser/配置代理 |

## 常见边界情况

- 科学符号（版权 ©、重音姓名）在 Markdown 导出中可能有质量告警（`markdown_warnings`）——文本可读，罕见字符需人工核对。
- 批量任务无论成功、失败或超时都会在预算内退出并写 `batch_results.json`（原子写入）；挂住的源会被放弃而不是拖住整批。
- `scan_find` 未安装时，`find`/`search`/`deep_search` 会给出安装指引；`verify`/`resolve_oa` 超时返回结构化错误。

## 环境安装引导

```
1. litloop_diagnostics(check="setup") → 环境状态
2. 按 readiness 处理：ready / partial / limited
3. 缺少组件时按建议安装（pymupdf4llm 用于 markdown、cloakbrowser 用于反爬登录）
```

## 快速安装

```
pip install -U litloop
pip install -U cloakbrowser    # 反爬浏览器（Chromium 内核）
litloop doctor             # 或 MCP: litloop_diagnostics(check="health")
```