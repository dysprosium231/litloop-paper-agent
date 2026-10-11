---
name: scansci-pdf
description: >
  Use this skill when the user wants to search academic literature, validate DOI or
  arXiv identifiers, export citations, organize paper lists, or retrieve papers
  through configured open-access, publisher, or authorized institutional routes.
---

# ScanSci PDF

Use the bundled `scansci-pdf` MCP server for paper discovery, metadata, citations,
download queues, access diagnostics, and result reporting. The repository's full
workflow reference is `../../skill/SKILL.md`; read it when the task needs detailed
tool routing or configuration guidance.


## ⚠️ 修改反爬/嗅探/镜像相关代码前必读

先读 [`docs/PLAYBOOK.md`](../../docs/PLAYBOOK.md)：镜像健康唯一存储（domain_db wall_state 表）、
结构性失败签名表、节奏参数、已知死镜像清单、以及六条铁律（含"造轮子前先 grep 家底"——
镜像健康存储曾被重复造过三代）。
## Route by intent

- Search or identify papers: `scansci_pdf_search`, `scansci_pdf_verify_identifiers`,
  and `scansci_pdf_parse_list` for list files.
- Resolve open-access locations: `scansci_pdf_prepare_queue(action="resolve_oa")`.
- Download one paper: `scansci_pdf_download` with an explicit strategy when the
  user has a source preference.
- Process a list: `scansci_pdf_parse_list` or `scansci_pdf_batch_download`.
- Export citations: `scansci_pdf_citation`; push to Zotero via `scansci_pdf_zotero_push`.
- Diagnose setup or access: `scansci_pdf_diagnostics` (check=health|network|sources|setup).

## 检索与筛选（AI 给理由，用户决定）

用户要找文献时按这个流程，不要直接甩一个列表：

1. **定标准**：把问题拆成 2–5 条纳入标准（研究对象、关注性质或现象、方法、文章类型、年份）。
   - 快速查找：直接写在结果上方，不等确认。
   - 课题调研、综述等深度检索：先列出标准请用户确认或修改，再检索。
2. **检索**：`scansci_pdf_search` 用英文关键词；中文问题先译成英文检索词，必要时换 2–3 组同义词分别检索。用户在意期刊质量时用 `max_cas_zone` / `max_cas_sub_zone` / `max_jcr_quartile` / `exclude_warning`，不要擅自加过滤。
3. **逐篇判断**：读每篇的标题和 `abstract`，按标准给相关度 **高 / 中 / 低 / 不相关**，写一句中文理由，说明它对应哪条标准、缺了什么。`abstract_missing` 的文章在理由里注明“仅凭标题”。不要编造摘要里没有的内容。
4. **保存**：`scansci_pdf_screening(action="save", question=..., criteria=[...], query=..., mode="quick"|"deep", items=[{doi, title, year, journal, journal_rank, cited_by_count, abstract_missing, retracted, concern, relevance, reason}, ...])`。同一课题的后续检索传 `session=` 合并进同一会话。
5. **呈现**：中文表格，列为 序号、相关度、文章、年份、期刊/分区、理由；按相关度排序。`retracted` 标“⚠已撤稿”，`concern` 标“⚠关注声明”，`journal_warning` 标“⚠预警期刊”。不相关的文章也列出（可折叠在最后），**不替用户删除任何文章**。附上会话的 Markdown 文件路径。
6. **用户选择**：用户说“要 1、3、5”后调用 `scansci_pdf_screening(action="decide", session=..., selected=[...], out_file=...)` 记录，并按需要下载（`scansci_pdf_batch_download`）或推送 Zotero。

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
