# 代码布局

`src/litloop/` 按领域分子包（2026-10-25 整理，见 DECISIONS「模块按领域分子包」）。新模块放进对应的子包；几个子包都要用的基础模块留在顶层。

> **整理进行中**：下表是用户确认的目标布局。已搬完的是 `terms/`、`citations/`、`tor/`、`zotero/`（7249a9a）；其余子包按 TODO「代码整理」分批搬，搬之前模块还在顶层的原名下（括号里的名字）。新代码：子包已建好的，放进子包；还没搬的，先写进原模块，随它一起搬。

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

顶层：`server.py`（MCP 服务）、`config.py`、`store.py`、`network.py`、`http_utils.py`、`identifiers.py`、`models.py`、`quota.py`、`updates.py`、`cache.py`、`deps.py`、`log.py`、`_source_hygiene.py`、`__init__.py`、`__main__.py`。

固定的入口：命令 `litloop`（`litloop.cli.main:main`）、`python -m litloop`、桌面壳启动的 `python -m litloop.desktop.server`（`app/src-tauri/src/lib.rs`）、`from litloop import search_papers`。

## 还没搬的几批要注意的

每批只搬文件、改 import 和测试里写死的路径字符串（`patch("litloop.xxx")`），不改逻辑；搬完跑 `scripts/import_smoke.py`、全量测试和一致性检查，单独提交。

- **`search` 从模块变成包**：`__init__.py` 的 `from .search import search_papers` 改指 `search.quick`；测试里 `from litloop import search` 再用 `search._match_tier` 的，改成 `from litloop.search import quick as search`。
- **按文件位置找路径的**，搬后要指回原处：

  | 模块（搬到） | 现在 | 改成 |
  |---|---|---|
  | `cloakbrowser_compat.py`（`browser/`） | `parent / "_browsers"` | `parent.parent / "_browsers"`（用户已下载的内置浏览器在 `src/litloop/_browsers`） |
  | `browser_discovery.py`（`browser/runtime.py`） | 按名字列出 `browser_cookies.py` 等 | 新名字 `cookies.py`、`engine.py`、`login.py` |
  | `publisher_access.py`（`publishers/access.py`） | `parent / "data"` | `parent.parent / "data"` |
  | `schools.py`（`institutional/`） | `parent / "data"` | `parent.parent / "data"` |
  | `web.py`（`cli/web.py`） | `parent / "templates"` | `parent.parent / "templates"` |

- **写死文件路径的工具和文档**：`app/package.json` 的 `lint:copy` 列着 `app_settings.py`、`proxy_check.py`、`download_jobs.py`、`app_server.py`，搬了不改，文案检查会悄悄漏掉它们（搬后在 `app/` 里跑一次 `npm run -s lint:copy` 确认）；`docs/UI_COPY.md`、`docs/API_LIMITS.md` 里提到的文件名一并改。
- **合并、删除**：`institutional/cloakbrowser_compat.py` 的函数和顶层那份函数体相同，保留顶层的（换上较长的文档字符串）；`institutional/fetcher.py`、`institutional/profile_health.py` 只做转发，删掉，调用处直接指向真正的模块。
- **唯一保留的转发**：`litloop/app_server.py`，只转发到 `desktop/server.py`，给已经装好的桌面 app 用（它们启动 `python -m litloop.app_server`），留一个版本后删。`lib.rs`、`.claude/launch.json`、`app/README.md` 改用新路径；`tests/test_mcp_lifecycle.py` 的 `-m litloop.main` 改成 `-m litloop.cli.main`。验收时实际运行 `python -m litloop --help`、`-m litloop.app_server --help`、`-m litloop.desktop.server --help`。
