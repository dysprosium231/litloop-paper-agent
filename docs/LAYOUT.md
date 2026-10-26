# 代码布局

`src/litloop/` 按领域分子包（2026-10-25 整理，见 DECISIONS「模块按领域分子包」）。新模块放进对应的子包；几个子包都要用的基础模块留在顶层。

2026-10-25～26 分 4 批搬完（7249a9a、a35f89b、afc3849、15c08b2）。表里括号中是搬之前的名字，查旧提交、旧文档时对照用。`scripts/check_release_consistency.py` 会报出没写进这份文件的模块：新增模块时在对应子包那一行写上它。

| 子包 | 管什么 | 模块（括号里是 2026-10-25 之前的名字） |
|---|---|---|
| `terms/` | 检索用词：归一化、英美拼写、术语表 | `text.py`、`spelling.py`、`glossary.py` |
| `search/` | 检索与筛选 | `quick.py`（search.py）、`deep.py`（deep_search.py）、`scansci_find.py`（discovery.py）、`abstracts.py`、`title_to_doi.py`（resolver.py）、`screening.py` |
| `download/` | 下载流程 | `fetcher.py`、`pipeline.py`、`pdf.py`（pdf_utils.py）、`rename.py`、`doi_check.py`、`progress.py`（progress_reporter.py）、`markdown.py`（md_export.py） |
| `publishers/` | 出版商 | `profiles.py`（publisher_profiles.py）、`access.py`（publisher_access.py）、`pdf_router.py`（publisher_pdf_router.py）、`supplementary.py`、`strategies/`（publisher_strategies/，其中 `_core.py` 原为 _publisher_strategies_core.py） |
| `browser/` | 浏览器 | `backend.py`、`engine.py`、`login.py`、`cookies.py`、`pdf.py`、`runtime.py`（browser_discovery.py）、`chrome_copy.py`、`profile_health.py`、`camofox_api.py`（camofox.py，云端 API 客户端）、`cloakbrowser_compat.py`（合并了顶层和 institutional/ 下的两份） |
| `institutional/` | 学校认证 | `auth.py`、`schools.py`、`session_heal.py`、`session_store.py`、`remote_assist.py`、`proxy_check.py`、`carsi_login.py`（sources/carsi.py）、`config_adapter.py` |
| `sources/` | 下载来源 | 各来源；`carsi.py`（原 carsi_source.py）、`elsevier_check.py`、`scihub_domains.py`（domain_db.py） |
| `citations/` | 引用格式与文献列表 | `formats.py`（citation.py）、`bibtex_crossref.py`（bibtex.py）、`bib_import.py`（bibparser.py）、`paper_list.py`（paperlist.py）、`session_export.py`（export.py） |
| `desktop/` | 桌面 app 的后台 | `server.py`（app_server.py）、`settings.py`（app_settings.py）、`agent_runner.py`、`download_jobs.py`、`error_text.py`、`translate.py` |
| `cli/` | 命令行 | `main.py`、`commands.py`（_cli_core.py）、`doctor.py`（setup.py，环境诊断）、`web.py`、`_utils.py` |
| `tor/` | Tor | `proxy.py`（tor.py）、`embedded.py`（embedded_tor.py） |
| `zotero/` | Zotero | `client.py`（zotero.py）、`bridge.py`（zotero_bridge.py，在 zotero-mcp 的 Python 里按文件路径运行，不能 import litloop） |
| `extractors/` | 从 HTML、PDF 抽内容 | 未变 |

顶层：`server.py`（MCP 服务）、`config.py`、`store.py`、`network.py`、`http_utils.py`、`identifiers.py`、`models.py`、`quota.py`、`updates.py`、`cache.py`、`deps.py`、`log.py`、`_source_hygiene.py`、`__init__.py`、`__main__.py`；`app_server.py` 只转发到 `desktop/server.py`，给已装好、还在启动 `python -m litloop.app_server` 的桌面 app 用，过一个版本后删（见 TODO）。

固定的入口：命令 `litloop`（`litloop.cli.main:main`）、`python -m litloop`、桌面壳启动的 `python -m litloop.desktop.server`（`app/src-tauri/src/lib.rs`）、`from litloop import search_papers`、`from litloop import batch_download`。`from litloop import download` 已去掉（2026-10-26，和子包 `download/` 重名），用 `from litloop.sources import download`。

## 新增或挪动模块时

- 放进管它的子包，在上表那一行写上；几个子包都要用的才放顶层，写进“顶层”那一行。
- 新子包不要和 `litloop/__init__.py` 导出的名字重名（`download/` 撞上过，见 DECISIONS「模块按领域分子包」）。
- 按文件位置找数据、模板的写法（`Path(__file__).parent / "data"`）挪了层级要跟着改；改完从干净副本打一次 wheel 看子包和数据都在。
- `app/package.json` 的 `lint:copy`、`app/src-tauri/src/lib.rs`、`.claude/launch.json` 里写着文件或模块路径，挪了要跟着改（`lint:copy` 的路径不存在时一致性检查会报出来）。
