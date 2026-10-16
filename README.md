<div align="center">
  <img src="assets/brand/litloop-icon.png" width="88" alt="LitLoop" />
  <h1>LitLoop</h1>
  <p><strong>Academic Paper Agent</strong> · 让 Agent 帮你找论文、筛论文、下论文、存进 Zotero</p>

  <a href="./LICENSE"><img alt="License" src="https://img.shields.io/badge/license-Apache--2.0-0F766E?style=flat-square" /></a>
  <a href="https://modelcontextprotocol.io"><img alt="MCP" src="https://img.shields.io/badge/MCP-20%20tools-111827?style=flat-square" /></a>
  <img alt="Python" src="https://img.shields.io/badge/python-3.11%2B-3776AB?style=flat-square" />
  <img alt="Status" src="https://img.shields.io/badge/%E7%8A%B6%E6%80%81-%E5%BC%80%E5%8F%91%E4%B8%AD-F59E0B?style=flat-square" />

  [它能做什么](#它能做什么) · [安装](#安装) · [依赖与 Key](#依赖与-key) · [怎么用](#怎么用) · [检索与筛选](#检索与筛选) · [下载与机构通道](#下载与机构通道) · [Zotero](#zotero) · [项目状态](#项目状态)
</div>

> **项目还在开发中。** 检索、筛选、下载、存进 Zotero 这条流程已经能用，并在真实课题上验收过；深度检索已在两个测试集上验收；系统综述、分析归纳还没做，可视化界面在规划中。详见[项目状态](#项目状态)。
>
> LitLoop 是 [Rimagination/scansci-pdf](https://github.com/Rimagination/scansci-pdf)（Apache-2.0）的独立衍生项目，由 [dysprosium231](https://github.com/dysprosium231) 维护，与原作者无隶属关系。下载和机构通道的大部分能力来自原项目，LitLoop 的改动见 [NOTICE](NOTICE)。

## 它能做什么

LitLoop 是一个 MCP 服务器加命令行工具，装进 Claude Code、Codex 等 Agent 后，用一句话就能走完一条文献流程：

```
检索 ──► 筛选 ──► 下载 ──► Zotero ──► （分析归纳，规划中）
```

- **检索：结果少而准。** 同时查 OpenAlex、Semantic Scholar、Crossref，合并去重，去掉离题结果。可以按真实被引数排序，年份过滤在数据源一侧完成。每条结果带清洗过的完整摘要，并标出撤稿和关注声明。
- **筛选：AI 给理由，你来拍板。** Agent 读摘要，把每篇评为 高 / 中 / 低 / 不相关，并写一句理由。你选定要哪些，选择记录存在本地，可以直接生成下载队列。
- **下载：自动选最快的路。** 开放获取直链、预印本、出版商 API、你学校的机构通道同时竞速，先成功的那条胜出。论文按 `作者年份_标题前几个词_DOI.pdf` 命名，文件名和论文一一对应。支持 100 多所高校的 WebVPN、CARSI 联邦认证、EZProxy，以及 Elsevier API 快速通道。
- **归档：直接进 Zotero。** 按 DOI 建条目，库里已有就复用，PDF 直接写进你本机的 Zotero，同步交给 Zotero 自己。

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

## 依赖与 Key

装好就能用；下面几样配上以后效果明显更好。各接口今天用了多少、还剩多少，可以用 `litloop_diagnostics(check="quota")` 查看；深度检索在额度不足或频繁被限流时会在返回里提醒。Key 都可以用 `litloop_config` 写进配置，也可以设成环境变量（配置文件优先，来自环境变量的 Key 不会被写进文件）。

| 组件 | 必需吗 | 用来做什么 | 没有时 | 怎么获取 |
|---|---|---|---|---|
| Python 3.11+、Git | 必需 | 运行和更新 LitLoop | 装不上 | Windows：`winget install Python.Python.3.12 Git.Git` |
| OpenAlex API Key | **强烈推荐** | 快速检索和深度检索的主要数据源（目前唯一） | 每天只有约 0.1 美元（1000 积分）、每秒 10 次，一次深度检索就可能用完；也不能用游标翻页（LitLoop 会自动改用页码翻页） | 在 [openalex.org](https://openalex.org) 注册后免费获取；`openalex_api_key` 或环境变量 `OPENALEX_API_KEY`。有 Key 时每天 1 美元 = 10000 积分（UTC 零点重置），每秒 30 次。普通过滤每次 1 积分，**带关键词检索每次 10 积分**；深度检索的引用路线每页都是检索请求。[官方说明](https://help.openalex.org/how-to-use-the-api/rate-limits-and-authentication) |
| Elsevier API Key | **强烈推荐** | ScienceDirect 全文下载（需校园网出口）；从 Scopus 补摘要 | Elsevier 期刊多的领域大量文章缺摘要，深度检索只能凭标题排序。PDC 测试集上，26 篇目标里 16 篇的摘要靠它补 | [dev.elsevier.com](https://dev.elsevier.com) 免费申请；`litloop_elsevier_setup`，或 `elsevier_api_key` / 环境变量 `ELSEVIER_API_KEY`。Scopus 摘要每周 10000 次、每秒 9 次；全文下载每周 50000 次、每秒 10 次（[官方额度表](https://dev.elsevier.com/api_key_settings.html)）。一次深度检索补摘要约用几百次 |
| 联系邮箱 | 推荐 | 随请求发给 Crossref 和 OpenAlex，出问题时它们能联系你 | Crossref 按公共池限速：同时只能 1 个请求，补摘要慢一些 | `litloop_config(key="contact_email", value="你的邮箱")`。有邮箱时 Crossref 允许每秒 10 次、同时 3 个（[官方说明](https://crossref.org/blog/announcing-changes-to-rest-api-rate-limits/)） |
| scansci-find（发现引擎） | 推荐 | 快速检索和深度检索的关键词路线 | 自动退回内置的三源检索，较慢 | 安装时加 `--with-find` |
| Zotero 桌面版 + zotero-mcp | 可选 | 把文献和 PDF 直接写进本机 Zotero | 只能下载到本地文件夹 | 见下文 [Zotero](#zotero) |
| 本机 Chrome | 可选 | 需要浏览器的出版商和机构通道 | 这些通道不可用，开放获取和 API 照常 | 正常安装 Chrome 即可，LitLoop 会自动复制一份私有副本 |
| 学校账号（WebVPN / CARSI / EZProxy） | 可选 | 下载学校订阅的论文 | 只能下开放获取和 API 能拿到的 | 见下文 [下载与机构通道](#下载与机构通道) |
| Semantic Scholar API Key | 可选 | 多一个检索来源；补摘要更快 | 不影响使用：补摘要时几个来源同时问，它慢也不会拖住别的来源 | [semanticscholar.org/product/api](https://www.semanticscholar.org/product/api)，审批较严，不一定能通过；环境变量 `SEMANTIC_SCHOLAR_API_KEY` |
| litloop-journal-rank | 可选（未公开） | 期刊分区、预警名单、本地撤稿库 | 没有分区信息，撤稿标记改用 OpenAlex 的数据 | 暂未公开 |

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
<summary><strong>MCP 工具全表</strong>（20 个，按用途分组）</summary>

| 想做什么 | 工具 |
|---|---|
| 下载单篇（可拉附件、转 Markdown） | `litloop_download` |
| 批量下载（列表 / xlsx·csv·bib·APA 文件） | `litloop_batch_download` |
| 检索、作者检索、深度检索（关键词 + 共同引用扩展，分批筛选） | `litloop_search` · `litloop_find` · `litloop_deep_search` |
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

目标、原则和验收标准见 [CHARTER.md](CHARTER.md)。它要解决四个问题：结果杂、读不过来、不好判断质量、查不全。

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

要把一个课题查全时用。Agent 给出问题、4–8 组英文检索词、核心词和几篇种子文章：
1. 每组检索词各取 100 条候选，再找出和种子共同引用较多、且标题或摘要含核心词的文章，合并成候选池。核心词可以用 `|` 写同义词，如 `Al|aluminum|aluminium SiC`，照顾只写缩写的文章；
2. 每次给 Agent 一批（默认 30 篇），Agent 评分后存进筛选记录。第一批按“共同引用 + 检索词匹配”排序；之后由一个轻量模型根据全部评分（相关和不相关的都用）重新排序，这是系统综述筛选常用的持续主动学习；
3. 评为“高”的文章自动成为新种子，再往外扩展。你给的种子权重最高，避免越滚越偏；
4. 每次出新一批前，先给缺摘要的候选补摘要（Semantic Scholar、Scopus、Crossref 同时问，先答先用）；
5. 最近一批的高相关比例降到最好一批的 1/6 以下，或已筛满 200 篇时，建议停止。给了必找清单时，会报告每篇找到没有、排在第几。

验收结果：SiC/Al 测试集筛 150 篇找到 19 篇目标中的 17 篇（第一版筛 90 篇只找到 12 篇）；PDC 气凝胶测试集筛 120 篇找到 26 篇中的 24 篇，150 篇时建议停止。

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
- PDF 作为附件挂到条目上；
- 可以指定已有的分类（`collections`）和标签（`tags`）；
- 推送后会核对附件确实有文件，上传中断留下的空附件会报出来，不会当作成功。

如果你已经在 Claude Code 或 Claude Desktop 里装了 zotero-mcp，什么都不用配。LitLoop 会自己找到它，并沿用它的 API Key 和 WebDAV 设置。查找顺序：

1. 配置项 `zotero_mcp_python` 指定的 Python（设为 `off` 表示不用 zotero-mcp）；
2. 和 LitLoop 装在同一环境里的 zotero-mcp（`--extras zotero`）；
3. Claude Code（`~/.claude.json`、项目 `.mcp.json`）或 Claude Desktop 配置里名字或命令含 `zotero` 的服务器，连同它的环境变量；
4. PATH 上的 `zotero-mcp`，以及用 `uv tool` 或 pipx 装的 zotero-mcp。

**推荐用本地写入。** Zotero 7 以上开着时，让 zotero-mcp 申请一次本地写入权限：调用 `zotero_authorize_local_writes`，在 Zotero 弹出的对话框里点「始终允许」。之后条目和 PDF 直接写进你电脑上的 Zotero，一篇几秒钟；同步到 Zotero 云端或 WebDAV 交给 Zotero 自己完成。

没有授权本地写入时，zotero-mcp 走 Zotero 网络接口（需要 `zotero-mcp setup` 配好 Web API Key），PDF 直接传到 WebDAV。从国内上传大文件到境外的 WebDAV 又慢又容易中断，所以不推荐。

都找不到 zotero-mcp 时，会退回 Zotero Web API（`zotero_api_key` + `zotero_library_id`）。这条路只建条目，**不上传 PDF**。

## 配置

所有配置通过 `litloop_config` 或 `litloop config-cmd` 修改，数据都放在 `~/.litloop/` 下。

<details>
<summary><strong>常用配置项</strong></summary>

| 配置项 | 默认值 | 说明 |
|---|---:|---|
| `output_dir` | `~/.litloop/papers` | PDF 保存目录 |
| `auto_rename` | `true` | 按作者、年份、标题和 DOI 重命名 |
| `download_strategy` | `fastest` | 可选 `fastest`、`oa_first`、`legal_only`（不用 Sci-Hub/LibGen）、`scihub_first`、`scihub_only` |
| `scihub_enabled` | `false` | 是否使用 Sci-Hub/LibGen 类来源；默认关闭，由你自己决定是否打开 |
| `search_fill_abstracts` | `true` | 检索结果自动补全摘要 |
| `openalex_api_key` | 空 | OpenAlex Key（强烈推荐，见[依赖与 Key](#依赖与-key)）；也可用环境变量 `OPENALEX_API_KEY` |
| `elsevier_api_key` | 空 | Elsevier Key，用于全文下载和 Scopus 摘要；也可用环境变量 `ELSEVIER_API_KEY` |
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

## 项目状态

2026 年 10 月：开发中，维护者自己在用，也欢迎试用和反馈。每次改动都在 Linux、Windows、macOS × Python 3.11–3.13 上跑全部测试。

### 已经可用

在真实课题（先驱体陶瓷 SiBCN 的隔热与吸波）上完整跑过“检索 → 筛选 → 下载 → Zotero”：

| 环节 | 状态 |
|---|---|
| 检索 | 三个数据源合并去重；过滤离题结果和垃圾记录；预印本并入正式版；补全并清洗摘要；标出撤稿和关注声明；可按被引数或年份排序 |
| 筛选 | AI 逐篇给相关度和理由，你按编号挑选，记录存在本地，可直接生成下载队列 |
| 深度检索 | 多组检索词 + 共同引用扩展建池，分批筛选，按评分持续重新排序，高相关文章自动滚雪球；两个测试集分别找到 17/19、24/26 篇目标 |
| 下载 | 多来源竞速；Elsevier API、WebVPN、CARSI、EZProxy；文件名带 DOI，不会把一篇论文的 PDF 当成另一篇 |
| Zotero | 本地写入，几秒一篇；会检查附件是不是空的 |

### 已知限制

- 一次快速检索约 6–10 秒；数据源目前只有 OpenAlex。
- 快速检索每组检索词只给前 10–20 条；要查全请用深度检索。
- 深度检索每出一批约 1 分钟（扩展候选池加补摘要）。
- 期刊分区和本地撤稿库依赖配套插件 litloop-journal-rank，它还没公开；没装时检索照常可用，只是没有分区信息，撤稿标记改用 OpenAlex 的数据。
- 还不支持中文文献（知网、万方）。

### 待开发

按计划顺序排列。详细进度和计划见 [TODO.md](TODO.md)，检索部分的设计和验收标准见 [CHARTER.md](CHARTER.md)。

1. **可视化界面**：Agent 在后台查找和评分，文章、理由和进度显示在本地网页里，你在网页里勾选和确认。第一版先做筛选页。
2. **系统综述支持**：记录每个数据源的检索量、去重数、纳入和排除的数量及理由，生成 PRISMA 式统计，中断后可以接着做。
3. **定期追踪**：保存检索式，定期运行，只推送上次之后的新文章。
4. **分析归纳**：PDF 转 Markdown 已经有了。下一步是从全文抽取要点、做对比表、起草综述，完成整个闭环。
5. **中文文献**：接入知网、万方。
6. **维护类**：LitLoop、zotero-mcp 有新版本时提醒。
7. **期刊分区插件公开**：分区数据有版权，公开前要改成由用户自己提供数据。

## 致谢

LitLoop 基于 **[scansci-pdf](https://github.com/Rimagination/scansci-pdf)**（作者 [Rimagination](https://github.com/Rimagination)，Apache-2.0）开发。原项目参考过以下开源项目：[FlareSolverr](https://github.com/FlareSolverr/FlareSolverr)、[ref-downloader](https://github.com/ltczding-gif/ref-downloader)、[paper-fetch-skill](https://github.com/Dictation354/paper-fetch-skill)、[paper-fetcher](https://github.com/fermionoid/paper-fetcher)、[cloakbrowser](https://github.com/CloakHQ/CloakBrowser)。

原项目贡献者 [annsyun](https://github.com/annsyun) 实现了 SAGE 中国站的机构授权下载通道（[Rimagination/scansci-pdf#58](https://github.com/Rimagination/scansci-pdf/pull/58)）。

检索用到了 [OpenAlex](https://openalex.org)、[Semantic Scholar](https://www.semanticscholar.org)、[Crossref](https://www.crossref.org) 的公开数据。撤稿数据来自 [Retraction Watch](https://retractionwatch.com)，由 Crossref 公开发布。

## 许可证

[Apache License 2.0](LICENSE)。原项目的版权声明保留在 LICENSE 和 [NOTICE](NOTICE) 中。原项目通过 PyPI 分发的预编译扩展（`_core/*.pyd`、`*.so`）不在本仓库内，LitLoop 使用纯 Python 实现。

使用机构通道时，请遵守你所在机构和出版商的使用条款。
