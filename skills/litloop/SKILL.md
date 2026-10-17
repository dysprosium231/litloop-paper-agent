---
name: litloop
description: >
  Use this skill when the user wants to search academic literature, validate DOI or
  arXiv identifiers, export citations, organize paper lists, or retrieve papers
  through configured open-access, publisher, or authorized institutional routes.
---
<!-- 由 skills-src/ 生成：改那里的文件，再运行 python scripts/build_skills.py -->

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

## 首次使用：配置 Key 和联系邮箱

第一次用、或 `litloop_diagnostics(check="setup")` 显示缺项时，按影响大小逐项问用户要不要配置（都可以跳过，跳过时说明影响），用 `litloop_config(key=..., value=...)` 写入，不要替用户编造或回显完整的 Key：
1. `openalex_api_key`：检索的主要数据源；没有时每天只有约 1000 积分，深度检索可能中途用完。
2. `elsevier_api_key`：下载 Elsevier 全文、补摘要（Scopus 每周 10000 次）。
3. `springer_meta_api_key`：Springer Nature 的 Meta API Key（不是 Open Access API 那个），补 Springer、Nature 文章的摘要；免费每天 500 次；摘要仅限个人、非商业使用。
4. `email`：联系邮箱，只发给 Crossref、OpenAlex、Unpaywall；有它时 Crossref 允许同时 3 个请求。
5. 学校的 WebVPN / CARSI / EZProxy，以及 Zotero 推送的分类。
各接口用量和剩余额度用 `litloop_diagnostics(check="quota")` 查看；深度检索返回的 `api_warnings` 不为空时，转告用户。

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
