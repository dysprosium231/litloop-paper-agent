<div align="center">
  <img src="assets/brand/litloop-icon.png" width="88" alt="LitLoop" />
  <h1>LitLoop</h1>
  <p><strong>Academic Paper Agent</strong> · 让 Agent 帮你找论文、筛论文、下论文、存进 Zotero</p>

  <a href="./LICENSE"><img alt="License" src="https://img.shields.io/badge/license-Apache--2.0-0F766E?style=flat-square" /></a>
  <a href="https://modelcontextprotocol.io"><img alt="MCP" src="https://img.shields.io/badge/MCP-20%20tools-111827?style=flat-square" /></a>
  <img alt="Python" src="https://img.shields.io/badge/python-3.11%2B-3776AB?style=flat-square" />

  [它能做什么](#它能做什么) · [安装](#安装) · [怎么用](#怎么用) · [检索与筛选](#检索与筛选) · [下载与机构通道](#下载与机构通道) · [Zotero](#zotero) · [路线图](#路线图)
</div>

> LitLoop 是 [Rimagination/scansci-pdf](https://github.com/Rimagination/scansci-pdf)（Apache-2.0）的独立衍生项目，由 [dysprosium231](https://github.com/dysprosium231) 维护，与原作者无隶属关系。下载和机构通道的大部分能力来自原项目，LitLoop 的改动见 [NOTICE](NOTICE)。

## 它能做什么

LitLoop 是一个 MCP 服务器加命令行工具，装进 Claude Code、Codex 等 Agent 后，用一句话就能走完一条文献流程：

```
检索 ──► 筛选 ──► 下载 ──► Zotero ──► （分析归纳，规划中）
```

- **检索：结果少而准。** 同时查 OpenAlex、Semantic Scholar、Crossref，合并去重，去掉离题结果。可以按真实被引数排序，年份过滤在数据源一侧完成。每条结果带清洗过的完整摘要，并标出撤稿和关注声明。
- **筛选：AI 给理由，你来拍板。** Agent 读摘要，把每篇评为 高 / 中 / 低 / 不相关，并写一句理由。你选定要哪些，选择记录存在本地，可以直接生成下载队列。
- **下载：自动选最快的路。** 开放获取直链、预印本、出版商 API、你学校的机构通道同时竞速，先成功的那条胜出。论文按 `作者_年份_标题.pdf` 命名。支持 100 多所高校的 WebVPN、CARSI 联邦认证、EZProxy，以及 Elsevier API 快速通道。
- **归档：直接进 Zotero。** 按 DOI 建条目，库里已有就复用，PDF 作为附件上传，配了 WebDAV 就存到 WebDAV。

登录都在你自己的浏览器里完成，密码不经过 LitLoop。

## 安装

对 Claude Code、Codex 或 ZCode 里的 Agent 说：

```text
帮我安装或更新这个插件：https://github.com/dysprosium231/litloop-paper-agent
```

安装和更新都是这一句。Agent 会克隆或更新仓库、建独立环境、注册 MCP、装好技能，最后跑一次自检。装完重启客户端即可使用。

<details>
<summary><strong>给 Agent 的步骤</strong>（也可以自己照着做）</summary>

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

</details>

<details>
<summary><strong>手动配置 MCP</strong>（Claude Desktop、Cursor、Cline、Cherry Studio 等）</summary>

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
- **ZCode**：[`zcode-plugin/`](./zcode-plugin/)，包含技能和 `/litloop` 快捷命令；
- **Claude Code**：`.claude/skills/` 加上面的 MCP 配置。

</details>

<details>
<summary><strong>其他运行方式</strong>（HTTP 远程 / Web UI / Docker）</summary>

```bash
litloop run --mode streamable_http --host 127.0.0.1 --port 8000   # HTTP 模式，默认只监听本机
litloop web --port 8080                                           # Web UI：http://localhost:8080
docker compose up -d                                              # MCP 服务器 + Tor，数据卷持久化
```

</details>

## 怎么用

装好后直接对 Agent 说：

```text
找一下近五年城市热岛和绿地降温的论文，只要中科院 2 区以上，排除撤稿

这 30 篇按和我课题的相关度筛一遍，说明理由，我来挑

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
<summary><strong>MCP 工具全表</strong>（20 个，按用途分组）</summary>

| 想做什么 | 工具 |
|---|---|
| 下载单篇（可拉附件、转 Markdown） | `litloop_download` |
| 批量下载（列表 / xlsx·csv·bib·APA 文件） | `litloop_batch_download` |
| 检索、作者检索、系统性发现 | `litloop_search` · `litloop_find` · `litloop_expand_citations` |
| 筛选记录（相关度 + 理由，记录选择，生成下载队列） | `litloop_screening` |
| 准备队列（校验 / OA 定位 / 构建） | `litloop_prepare_queue` |
| 机构登录与状态（WebVPN / CARSI / EZProxy / 出版商 SSO） | `litloop_login` · `litloop_channel_status` · `litloop_schools` |
| 引文 / 元数据 / Zotero | `litloop_citation` · `litloop_zotero_push` |
| 配置 / 诊断 / Tor / 缓存 / Elsevier Key | `litloop_config` · `litloop_diagnostics` · `litloop_tor` · `litloop_cache_clear` · `litloop_elsevier_setup` |
| 取消进行中的下载 / 批量（停止竞速、跳过未开始的论文、关闭窗口） | `litloop_cancel` |

每个工具的参数与说明由 MCP `tools/list` 自带，Agent 会自动看到。

</details>
<!-- mcp-tools:end -->

## 检索与筛选

设计原则见 [docs/SEARCH_SPEC.md](docs/SEARCH_SPEC.md)。它要解决三个问题：结果杂、读不过来、不好判断质量。

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

> 期刊分区和本地撤稿库由配套插件 litloop-journal-rank 提供，它目前还没有公开。没装这个插件时，检索的其他功能照常可用。

## 下载与机构通道

下载一篇论文时，多个来源分层同时尝试，第一个成功就返回：

```
第 1 层 (4s)   出版商直链（开放获取 / 机构 IP）
第 2 层 (5s)   OpenAlex / Unpaywall / DOAJ / OpenAIRE
第 3 层 (8s)   EuropePMC / CORE / PMC / arXiv
第 4 层 (25s)  LibGen / Sci-Hub（可关闭）
第 5 层 (20s)  WebVPN / CARSI 机构代理
```

付费墙论文走下面四条机构通道之一。

### Elsevier API（推荐）

ScienceDirect 和 Cell Press 的论文通过 API 下载，每篇只要 1–2 秒，不用开浏览器。Key 免费申请：

1. 在 [Elsevier Developer Portal](https://dev.elsevier.com/) 创建应用。最好用机构邮箱，并在校园网下操作。
2. 配置：`litloop_elsevier_setup`，或 `litloop_config(key="elsevier_api_key", value="...")`。

只要 Key 加上校园网出口就够了，不需要 insttoken。返回 `NOT_ENTITLED` 说明当前不在校园网，或者学校没订这本期刊。同一个 Key 也用来从 Scopus 补摘要。

### WebVPN

```text
litloop_schools(action="search", query="北京")      # 找到你的学校
litloop_schools(action="set", school="你的学校")
litloop_login(kind="webvpn")                        # 在浏览器里完成统一认证
litloop_channel_status(kind="webvpn_test")          # 确认连接正常
```

### CARSI

```text
litloop_config(key="carsi_enabled", value="true")
litloop_config(key="carsi_idp_name", value="你的学校名称")
litloop_login(kind="carsi", publisher="sciencedirect")
```

支持 sciencedirect、springer、wiley、ieee、tandfonline、nature、science、sage-cn 等出版商。

### EZProxy

```text
litloop_config(key="ezproxy_enabled", value="true")
litloop_config(key="ezproxy_login_url", value="https://libproxy.你的学校.edu.cn/login?url={url}")
litloop_login(kind="ezproxy")
```

除了上面这种前缀式地址，也支持域名重写式，例如 `https://{host_dashed}.lib.ezproxy.hkust.edu.hk`。

<details>
<summary><strong>如果你开着代理软件</strong></summary>

机构通道靠校园网出口 IP 识别身份，流量经过代理（尤其是境外出口）后，学校权限就会失效。LitLoop 有意忽略 `HTTP_PROXY` 和 `HTTPS_PROXY` 环境变量，只认 `network_proxy` 配置项，或 `LITLOOP_PROXY` 环境变量。

如果你用的是 TUN 或全局模式的代理软件，请按进程名给下面两个程序设置直连：
- 运行 LitLoop 的 `python.exe`；
- LitLoop 启动的浏览器 `litloop-chrome.exe`。

个别出版商只在直连时很慢，这种情况可以用 `host_proxy_url` 让指定域名单独走代理。

</details>

<details>
<summary><strong>支持的出版商路由表</strong>（19 家，自动生成）</summary>

<!-- publisher-table:start -->
| 策略 | DOI 前缀 | 域名 |
|---|---|---|
| ACM | `10.1145/` | `dl.acm.org` |
| ACS | `10.1021/` | `pubs.acs.org` |
| AIP | `10.1063/` | `pubs.aip.org` |
| APS | `10.1103/` | `journals.aps.org` |
| ASCE | `10.1061/` | `ascelibrary.org` |
| Copernicus | `10.5194/` | `copernicus.org` |
| Elsevier | `10.1016/`, `10.1016/j.` 等 18 个 | `sciencedirect.com` |
| Generic | — | `—` |
| IEEE | `10.1109/` | `ieeexplore.ieee.org` |
| IOP | `10.1088/` | `iopscience.iop.org` |
| Nature | `10.1038/` | `nature.com` |
| Oxford | `10.1093/` | `academic.oup.com` |
| Royal Society | `10.1098/` | `royalsocietypublishing.org` |
| RSC | `10.1039/` | `pubs.rsc.org` |
| SAGE | `10.1177/` | `journals.sagepub.com` |
| Science | `10.1126/` | `science.org` |
| Springer | `10.1007/`, `10.1023/` | `link.springer.com` |
| Tandfonline | `10.1080/` | `tandfonline.com` |
| Wiley | `10.1002/`, `10.1111/` | `onlinelibrary.wiley.com` |
<!-- publisher-table:end -->

</details>

## Zotero

`litloop_zotero_push` 交给 [zotero-mcp](https://github.com/54yyyu/zotero-mcp)（0.14.1 及以上）完成：
- 按 DOI 用 Crossref 元数据建条目，库里已有就复用；
- PDF 作为附件上传，配了 WebDAV 就存到 WebDAV；
- 可以指定已有的分类（`collections`）和标签（`tags`）。

如果你已经在 Claude Code 或 Claude Desktop 里装了 zotero-mcp，什么都不用配。LitLoop 会自己找到它，并沿用它的 API Key 和 WebDAV 设置。查找顺序：

1. 配置项 `zotero_mcp_python` 指定的 Python（设为 `off` 表示不用 zotero-mcp）；
2. 和 LitLoop 装在同一环境里的 zotero-mcp（`--extras zotero`）；
3. Claude Code（`~/.claude.json`、项目 `.mcp.json`）或 Claude Desktop 配置里名字或命令含 `zotero` 的服务器，连同它的环境变量；
4. PATH 上的 `zotero-mcp`，以及用 `uv tool` 或 pipx 装的 zotero-mcp。

zotero-mcp 需要写入权限，要配好 Web API Key（`zotero-mcp setup`）。都找不到时，会退回 Zotero Web API（`zotero_api_key` + `zotero_library_id`）。这条路只建条目，**不上传 PDF**。

## 配置

所有配置通过 `litloop_config` 或 `litloop config-cmd` 修改，数据都放在 `~/.litloop/` 下。

<details>
<summary><strong>常用配置项</strong></summary>

| 配置项 | 默认值 | 说明 |
|---|---:|---|
| `output_dir` | `~/.litloop/papers` | PDF 保存目录 |
| `auto_rename` | `true` | 按作者、年份、标题重命名 |
| `download_strategy` | `fastest` | 可选 `fastest`、`oa_first`、`legal_only`（不用 Sci-Hub/LibGen）、`scihub_first`、`scihub_only` |
| `scihub_enabled` | `true` | 是否使用 Sci-Hub/LibGen 类来源 |
| `search_fill_abstracts` | `true` | 检索结果自动补全摘要 |
| `openalex_api_key` | 空 | OpenAlex Key，可提高检索稳定性 |
| `elsevier_api_key` | 空 | Elsevier Key，用于全文下载和 Scopus 摘要 |
| `network_proxy` | 空 | HTTP/SOCKS 代理；环境变量 `HTTP(S)_PROXY` 会被忽略 |
| `host_proxy_url` | 空 | 只给指定出版商域名走的代理 |
| `browser_executable` | 空 | 指定浏览器程序，例如本机 Chrome 的副本 |
| `vpnsci_enabled` / `vpnsci_school` | `false` / 空 | WebVPN |
| `carsi_enabled` / `carsi_idp_name` | `false` / 空 | CARSI |
| `auto_relogin` | `true` | 机构会话过期时自动弹出浏览器重新登录 |
| `batch_workers` | `10` | 批量并发数，被封 IP 时调到 2 |
| `request_delay_min` / `request_delay_max` | `2.0` / `5.0` | 请求间的随机延迟（秒） |
| `cache_ttl_hours` | `168` | 下载缓存有效期，设 0 表示不缓存 |

</details>

<details>
<summary><strong>批量下载被封 IP 怎么办</strong></summary>

连续 3 次检测到 IP 被封（封锁页、403、429）时，批量任务会自动停止。降低风险的办法：

```bash
litloop config-cmd batch_workers 2
litloop config-cmd request_delay_min 5
litloop config-cmd request_delay_max 12
```

ACS 出现 “IP Address Blocked” 时，封禁在出版商那边，代码解决不了。可以发邮件到 `ipblock@acs.org` 申诉，通常 1–3 个工作日解封。校园网出口是全校共用的，请克制批量下载的速度。

</details>

## 故障排查

| 现象 | 先做 |
|---|---|
| 下载失败 | `litloop check`；疑似登录问题再跑 `litloop session-doctor` |
| 数据源打不开 | 让 Agent 调 `litloop_diagnostics(check="network")` |
| 机构权限失效 | 确认没走代理（见上面“如果你开着代理软件”），再 `litloop_channel_status` |
| Elsevier 返回 403 | `litloop elsevier-check --doi <DOI>` 检查 Key 和出口 |
| 以前能下的站点突然出 Cloudflare | `litloop browser-doctor`，按提示更新浏览器组件 |
| 想中止正在进行的下载 | `litloop_cancel` |

## 路线图

LitLoop 的目标是把文献工作做成一个闭环。待办事项见 [TODO.md](TODO.md)，主要方向：

- 分析归纳：读全文、抽取要点、做综述草稿；
- 中文文献来源；
- 浏览器副本跟随本机 Chrome 自动更新，以及新版本提醒；
- 期刊分区插件接入机构订阅数据后公开。

## 致谢

LitLoop 基于 **[scansci-pdf](https://github.com/Rimagination/scansci-pdf)**（作者 [Rimagination](https://github.com/Rimagination)，Apache-2.0）开发。原项目参考过以下开源项目：[FlareSolverr](https://github.com/FlareSolverr/FlareSolverr)、[ref-downloader](https://github.com/ltczding-gif/ref-downloader)、[paper-fetch-skill](https://github.com/Dictation354/paper-fetch-skill)、[paper-fetcher](https://github.com/fermionoid/paper-fetcher)、[cloakbrowser](https://github.com/CloakHQ/CloakBrowser)。

原项目贡献者 [annsyun](https://github.com/annsyun) 实现了 SAGE 中国站的机构授权下载通道（[Rimagination/scansci-pdf#58](https://github.com/Rimagination/scansci-pdf/pull/58)）。

检索用到了 [OpenAlex](https://openalex.org)、[Semantic Scholar](https://www.semanticscholar.org)、[Crossref](https://www.crossref.org) 的公开数据。撤稿数据来自 [Retraction Watch](https://retractionwatch.com)，由 Crossref 公开发布。

## 许可证

[Apache License 2.0](LICENSE)。原项目的版权声明保留在 LICENSE 和 [NOTICE](NOTICE) 中。原项目通过 PyPI 分发的预编译扩展（`_core/*.pyd`、`*.so`）不在本仓库内，LitLoop 使用纯 Python 实现。

使用机构通道时，请遵守你所在机构和出版商的使用条款。
