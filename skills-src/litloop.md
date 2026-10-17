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


<!-- include: shared/anti-scraping.md -->

## Route by intent

- Search or identify papers: `litloop_search`, `litloop_verify_identifiers`,
  and `litloop_parse_list` for list files.
- Resolve open-access locations: `litloop_prepare_queue(action="resolve_oa")`.
- Download one paper: `litloop_download` with an explicit strategy when the
  user has a source preference.
- Process a list: `litloop_parse_list` or `litloop_batch_download`.
- Export citations: `litloop_citation`; push to Zotero via `litloop_zotero_push`.
- Diagnose setup or access: `litloop_diagnostics` (check=health|network|sources|setup).

<!-- include: shared/first-use.md -->

<!-- include: shared/screening.md -->

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
