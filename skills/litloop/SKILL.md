---
name: litloop
description: >
  Use this skill when the user wants to search academic literature, validate DOI or
  arXiv identifiers, export citations, organize paper lists, or retrieve papers
  through configured open-access, publisher, or authorized institutional routes.
---

# LitLoop

Use the bundled `litloop` MCP server for paper discovery, metadata, citations,
download queues, access diagnostics, and result reporting. The repository's full
workflow reference is `../../skill/SKILL.md`; read it when the task needs detailed
tool routing or configuration guidance.


## ⚠️ 修改反爬/嗅探/镜像相关代码前必读

先读 [`docs/PLAYBOOK.md`](../../docs/PLAYBOOK.md)：镜像健康唯一存储（domain_db wall_state 表）、
结构性失败签名表、节奏参数、已知死镜像清单、以及六条铁律（含"造轮子前先 grep 家底"——
镜像健康存储曾被重复造过三代）。
## Route by intent

- Search or identify papers: `litloop_search`, `litloop_verify_identifiers`,
  and `litloop_parse_list` for list files.
- Resolve open-access locations: `litloop_prepare_queue(action="resolve_oa")`.
- Download one paper: `litloop_download` with an explicit strategy when the
  user has a source preference.
- Process a list: `litloop_parse_list` or `litloop_batch_download`.
- Export citations: `litloop_citation`; push to Zotero via `litloop_zotero_push`.
- Diagnose setup or access: `litloop_diagnostics` (check=health|network|sources|setup).

## 检索与筛选（AI 给理由，用户决定）

用户要找文献时按这个流程，不要直接甩一个列表：

1. **定标准**：把问题拆成 2–5 条纳入标准（研究对象、关注性质或现象、方法、文章类型、年份）。
   - 快速查找：直接写在结果上方，不等确认。
   - 课题调研、综述等深度检索：先列出标准请用户确认或修改，再检索。
2. **检索**：`litloop_search` 用英文关键词；中文问题先译成英文检索词，必要时换 2–3 组同义词分别检索。用户在意期刊质量时用 `max_cas_zone` / `max_cas_sub_zone` / `max_jcr_quartile` / `exclude_warning`，不要擅自加过滤。
3. **逐篇判断**：读每篇的标题和 `abstract`，按标准给相关度 **高 / 中 / 低 / 不相关**，写一句中文理由，说明它对应哪条标准、缺了什么。`abstract_missing` 的文章在理由里注明“仅凭标题”。不要编造摘要里没有的内容。
4. **保存**：`litloop_screening(action="save", question=..., criteria=[...], query=..., mode="quick"|"deep", items=[{doi, title, year, journal, journal_rank, cited_by_count, abstract_missing, retracted, concern, relevance, reason}, ...])`。同一课题的后续检索传 `session=` 合并进同一会话。
5. **呈现**：中文表格，列为 序号、相关度、文章、年份、期刊/分区、理由。**序号和顺序一律用 save 返回的 `numbering`**（保存时会按相关度重排），不要用自己发送时的顺序。`retracted` 标“⚠已撤稿”，`concern` 标“⚠关注声明”，`journal_warning` 标“⚠预警期刊”。不相关的文章也列出（可折叠在最后），**不替用户删除任何文章**。附上会话的 Markdown 文件路径。
6. **用户选择**：用户说“要 1、3、5”后调用 `litloop_screening(action="decide", session=..., selected=[...], rejected=[...], out_file=...)` 记录。每次 decide 记录的是**完整选择**，会覆盖上一次；用户追加或改选时传入全部选中项。把返回的 `chosen` 列表（序号 + 标题）念给用户核对，再按需要下载（`litloop_batch_download`）或推送 Zotero。

### 深度检索（要把一个课题查全时）

1. 标准确认后，请用户给 1–3 篇确定相关的种子文章（DOI）；没有时先用 `litloop_search` 找几篇请用户认。
2. `litloop_deep_search(action="start", question=..., queries=[4–8 组英文检索词], seeds=[DOI], core_terms=[1–3 个核心词], criteria=[...])`，返回第一批（默认 20 篇）和 `session`。**检索词和核心词要照顾缩写、全称和英式拼写**：很多文章只写 Al、SiC 这类缩写或化学式。核心词里用 `|` 表示“任意一个”，如 `Al|aluminum|aluminium SiC`；检索词里也要有一组用缩写的。**核心词要覆盖整个问题的范围，不要只写其中的重点**：问题是“先驱体陶瓷，尤其 SiBCN”时，核心词写 SiBCN 会把其他 PDC 体系挡在引用扩展之外；应写成 `SiBCN|SiCN|SiOC|polymer-derived|preceramic` 这类覆盖全题的写法，重点留给评分。用户给了必找文章时传 `must_find=[DOI]`，结果的 `coverage` 会报告每篇的状态。
3. 逐篇判断后用 `litloop_deep_search(action="rate", session=..., ratings=[{"n": 1, "relevance": "高", "reason": "..."}, ...])` 按编号保存，标题和 DOI 由插件补上。**这一批每篇都要评**，返回的 `not_rated` 列出漏评的编号；没评的下一批会再出现。
4. `litloop_deep_search(action="next", session=...)` 取下一批。评为“高”的文章会自动成为新种子；从第二批起排序由模型根据全部评分学习（`ranked_by="model"`），所以“低 / 不相关”也要如实评。
5. 返回 `stop_suggested=true` 时，把 `stop_reason` 告诉用户并建议停止；用户要继续就接着 `next`。随时可以用 `action="status"` 看已筛篇数和各相关度数量。
6. 筛完后按上面第 5、6 步呈现和让用户选择。跨了多批之后编号会变，**decide 时优先传 DOI**。

## Operating boundaries

- Preserve the user's requested source strategy and report the actual source in
  every download result.
- Prefer open-access, publisher API, and institution-authorized routes when no
  source preference is given.
- Ask before opening an interactive login, importing cookies, or changing access
  configuration.
- Treat API keys, cookies, proxy credentials, and institution details as secrets;
  never repeat them in the response.
- Gray-source and anti-bot routes require the user's explicit choice and must be
  used only where the user has the right to do so and applicable rules permit it.
