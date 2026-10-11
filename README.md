<div align="center">
  <h1>LitLoop</h1>
  <p><strong>Academic Paper Agent</strong> · 学术论文 Agent：检索 → 下载 → Zotero 归档 → 分析归纳</p>

  <a href="./LICENSE"><img alt="License" src="https://img.shields.io/badge/license-Apache--2.0-0F766E?style=flat-square" /></a>
  <a href="https://modelcontextprotocol.io"><img alt="MCP" src="https://img.shields.io/badge/MCP-20%20tools-111827?style=flat-square" /></a>

  让 Agent 替你找论文、下论文、整理论文 —— 20+ 数据源并行竞速，100+ 高校机构通道（WebVPN / CARSI），下载后直接进 Zotero。

  [能力](#能力) · [快速开始](#快速开始) · [怎么用](#怎么用) · [机构通道](#机构通道) · [Zotero](#zotero) · [来源与致谢](#致谢)
</div>

> LitLoop 是 [Rimagination/scansci-pdf](https://github.com/Rimagination/scansci-pdf)（Apache-2.0）的独立衍生版，由 [dysprosium231](https://github.com/dysprosium231) 维护，与原作者无隶属关系。主要改动见 [NOTICE](NOTICE)。

## 能力

给出 DOI、arXiv 号或一份文献清单，LitLoop 会自动挑最快能下的那条路：OA 直链、预印本、出版商 API、你的学校通道都试一遍，付费墙自动路由，结果落地成规整命名的 PDF 文件。

- **单篇，一句话的事** — DOI / arXiv 进，`作者_年份_标题.pdf` 出；BibTeX / RIS / EndNote 引文顺手导出，可直推 Zotero。
- **清单，整批拿下** — APA / BibTeX / DOI 列表直接喂，自动补全缺失 DOI；上千篇先分「OA / 灰色源 / 需机构」三桶再分批下载，不瞎跑不浪费。
- **付费墙，走你的学校** — 100+ 高校 WebVPN、CARSI 联邦认证、EZProxy、Elsevier API 快速通道（1–2 秒/篇）；登录在你自己的浏览器完成，密码不经过工具。
- **对抗与自愈全自动** — Cloudflare / CAPTCHA / SSO 分层处理；出版商封 IP 自动停损；机构会话过期自动重登，登录一次全程复用。
- **Agent 原生** — 标准 MCP 服务器、20 个工具即装即用；Codex App、ZCode、Claude Code 都有现成的插件或配置。

### 近期更新

- **统一任务管线** — 任意输入（xlsx/csv 表格、文献清单、检索结果、文章页 URL）自动归一成带渠道预测的下载队列；`batch --lanes` 四车道调度：Elsevier API / OA 快车道并行，灰色源竞速与机构级联接续，失败逐级溢流。
- **SI 附件下载** — `get <DOI> --si` 把补充材料一并拉下来；被 Cloudflare 挡住的 Elsevier 自动切隐身浏览器通道。
- **AI 阅读层** — `get <DOI> --md` 顺手把 PDF 转成 agent 友好的 Markdown（PDF 仍是默认交付物）。
- **OpenAIRE 仓储源接入** — 绿色 OA / 机构库副本进入免费竞速池，Unpaywall 漏掉的它来补（现已 20+ 数据源）。
- **MCP 工具面瘦身** — 45 → 17 个工具，每次会话的 schema token 开销降约 60%，能力零损失。
- **更抗封、更稳** — 竞速引擎新增 (源 × 出版商) 负缓存，被 Cloudflare 拦一次不再反复烧超时；cloakbrowser 强制 0.5.9+（Chromium 151 内核）；每周出版商金丝雀巡检。

## 快速开始

安装和更新是同一句话——对 Codex、ZCode、Claude Code 都这么说：

```text
帮我安装或更新这个插件：https://github.com/dysprosium231/litloop-paper-agent
```

Agent 会克隆（或原地更新）仓库、装好依赖并注册插件，然后你就能直接让它下论文了——例子见下一节[怎么用](#怎么用)。

> **给 Agent 的步骤**（安装和更新相同）：
> 1. 插件目录：`~/plugins/litloop-paper-agent`（或旧的 `~/plugins/litloop`）已存在就直接用；否则 `git clone https://github.com/dysprosium231/litloop-paper-agent.git ~/plugins/litloop-paper-agent`。
> 2. 用 Python 3.11+ 运行该目录下的 `scripts/deploy.py`，如 `python ~/plugins/litloop-paper-agent/scripts/deploy.py`（Windows 用 `%USERPROFILE%\plugins\litloop-paper-agent\scripts\deploy.py`）。它会同步代码（含 fork 改写历史后的更新）、在 `~/.litloop/venv` 建独立环境并安装、向本机的 Claude Code / Claude Desktop（已有配置文件时）/ Codex 注册 MCP、复制 skills，最后自检。已注册且可用的安装会被保留并就地更新。
> 3. 把脚本末尾的 summary（警告和下一步）转告用户，并提醒重启客户端。脚本以“本地有未推送改动”退出时，不要强行覆盖，问用户。
> 4. 可选：`--with-find` 加装 scansci-find；`--extras fast,vpnsci,patchright,zotero` 把 zotero-mcp 装进同一环境；`--dry-run` 只看不改。没有 Python 3.11+ 或 Git 时，先请用户安装（Windows：`winget install Python.Python.3.12 Git.Git`）。

<details>
<summary><strong>手动安装 / pip / MCP 配置</strong>（不走对话流程时）</summary>

```bash
pip install litloop
```

标准 MCP 配置（Claude Desktop / Cursor / Windsurf / Cline / Cherry Studio…）：

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

Codex App / Codex CLI（Windows 一键：克隆 + 装环境 + 注册插件）：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -Command '$p=Join-Path $HOME "plugins\litloop"; if(Test-Path $p){ git -C $p fetch origin main; git -C $p merge --ff-only origin/main } else { git clone --branch main --single-branch https://github.com/dysprosium231/litloop-paper-agent.git $p }; & (Join-Path $p "scripts\install-litloop.ps1")'
```

ZCode：插件市场搜 `litloop` 一键安装，MCP、skills、快捷命令一次带齐。

**插件入口**：Codex = `.codex-plugin/plugin.json` + `.mcp.json`（自动注册 MCP server）+ `skills/`；ZCode = [`zcode-plugin/`](./zcode-plugin/)（marketplace 包：skills + `/litloop` 快捷命令）；Claude Code = `.claude/skills/` + 上面的标准 MCP 配置。

手动安装（Codex）：仓库放到 `%USERPROFILE%\plugins\litloop` → `python -m pip install -e .` → 插件管理页刷新个人 marketplace；Codex CLI 可用时执行 `codex plugin add litloop@local-plugins`。

</details>

<details>
<summary><strong>其他部署方式</strong>（HTTP 远程 / Docker / Web UI）</summary>

HTTP 模式（远程部署或不支持 stdio 的场景，默认只监听本机）：

```bash
litloop run --mode streamable_http --host 127.0.0.1 --port 8000
```

Web UI：

```bash
litloop web --port 8080   # 浏览器打开 http://localhost:8080
```

Docker（内置 MCP 服务器 + Tor，数据卷持久化）：

```bash
docker compose up -d
```

</details>

## 怎么用

装好后，对着 Agent 说一句话就行。这些例子覆盖最常见的日常场景：

```text
帮我下载 10.1038/nature12373 这篇论文

这个清单里有 3000 篇文献，先分类哪些能直接下、哪些要走机构权限，然后分批下载

搜一下 "urban heat island" 2023 年以来被引最高的 10 篇论文，并下载

用北大的 WebVPN 登录，把这篇 Elsevier 的论文下下来

把刚才那批论文的 BibTeX 导出，再推送到 Zotero

这篇下载失败了，帮我诊断一下网络问题
```

不动 Agent、直接命令行也行：

```bash
litloop get 10.1038/nature12373            # 单篇下载
litloop batch 文献清单.xlsx --lanes        # 批量下载（xlsx/csv/队列通用，四车道调度）
litloop search "carbon cycle" --out q.txt  # 检索结果落成队列文件，直接接 batch
litloop check                             # 依赖与环境体检
```

<!-- mcp-tools:start -->
<details>
<summary><strong>MCP 工具全表</strong>（20 个，按意图分组）</summary>

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

## 机构通道

付费墙论文优先走这四条机构通道。登录都在你自己的浏览器里完成，密码不经过本工具。

### Elsevier API（推荐，无需浏览器）

ScienceDirect / Cell Press 等 Elsevier 论文走 API 直接下载，速度从 15–30 秒降到 1–2 秒。Key 申请免费、个人邮箱即可：

1. 访问 [Elsevier Developer Portal](https://dev.elsevier.com/) 创建应用，勾选 **ScienceDirect Article Retrieval**
2. 配置 Key：`litloop_elsevier_setup`（MCP 会打开浏览器引导）或 `litloop_config(key="elsevier_api_key", value="...")`

### WebVPN（高校代理）

```text
1. litloop_schools(action="search", query="北京")  → 搜你的学校
2. litloop_schools(action="set", school="你的学校")
3. litloop_login(kind="webvpn")                    → 浏览器完成 CAS 认证
4. litloop_channel_status(kind="webvpn_test")      → 确认连接正常
```

支持 100+ 所中国高校，命令行等价：`litloop schools 北京` / `litloop setup 北京航空航天大学`。

### CARSI（出版商联邦认证）

```text
1. litloop_config(key="carsi_enabled", value="true")
2. litloop_config(key="carsi_idp_name", value="你的学校名称")
3. litloop_login(kind="carsi", publisher="sciencedirect")
```

支持 sciencedirect、springer、wiley、ieee、tandfonline、nature 等。SAGE 中国站首次使用
`litloop_login(kind="carsi", publisher="sage-cn")` 登录；之后下载会复用已保存会话，
从文章页解析实际 `articleId` 并请求站点自身的 PDF 下载接口，无需反复开启浏览器。

### EZProxy（图书馆代理）

```text
1. litloop_config(key="ezproxy_enabled", value="true")
2. litloop_config(key="ezproxy_login_url", value="https://libproxy.你的学校.edu.cn/login?url={url}")
3. litloop_login(kind="ezproxy")
```

`ezproxy_login_url` 支持两种模板：

- **前缀式**（大多数学校）：`https://ezproxy.lib.xxx.edu.cn/login?url={url}`，`{url}` 会被替换成目标文章地址；
- **域名重写式**（如港科大 `lib.ezproxy.hkust.edu.hk`）：`https://{host_dashed}.lib.ezproxy.hkust.edu.hk`，
  `{host_dashed}` 会被替换成出版商域名的连字符形式（`onlinelibrary.wiley.com` →
  `onlinelibrary-wiley-com`）。这类部署不认 `login?url=` 前缀，必须用域名重写。

登录检测以 ezproxy/libproxy 域名下的会话 cookie 为准，不会把「被重定向到出版商原站」
误判为登录成功；会话校验也只探测代理自身的 `/login` 端点，对两类部署都有效。

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

`litloop_zotero_push` 优先交给 [zotero-mcp](https://github.com/54yyyu/zotero-mcp)（≥ 0.14.1）完成：按 DOI 建条目、用 CrossRef 元数据，库里已有就复用；PDF 作为附件上传，配了 WebDAV 就存到 WebDAV。可以带 `collections`（已有分类）和 `tags`。

已经在 Claude Code / Claude Desktop 里装好 zotero-mcp 的话，什么都不用配，插件会自己找到它，并沿用它的 API Key、WebDAV 设置。查找顺序：

1. `zotero_mcp_python` 指定的 Python（设为 `off` 则不用 zotero-mcp）
2. 和本插件装在同一个环境里的 zotero-mcp（`pip install "litloop[zotero]"`）
3. Claude Code（`~/.claude.json`、项目 `.mcp.json`）或 Claude Desktop 配置里名字或命令含 `zotero` 的服务器，连同它的 `env`
4. PATH 上的 `zotero-mcp`，以及 `uv tool` / pipx 安装的 zotero-mcp

zotero-mcp 需要能写库：配好 Web API Key（`zotero-mcp setup`）。只开了本地只读模式时推送会失败，并返回 zotero-mcp 的原始报错。

都找不到时退回 Zotero Web API（`zotero_api_key` + `zotero_library_id`），这条路只建条目，**不上传 PDF**。

## 配置与策略

<details>
<summary><strong>配置参考</strong>（完整表）</summary>

通过 `litloop_config` 或 `litloop config-cmd` 修改：

| 配置项 | 默认值 | 说明 |
|---|---:|---|
| `output_dir` | `~/.litloop/papers` | PDF 保存目录 |
| `auto_rename` | `true` | 自动按作者/标题重命名 |
| `download_strategy` | `fastest` | 下载策略 |
| `scihub_enabled` | `true` | 启用 Sci-Hub/LibGen 类来源 |
| `network_proxy` | 空 | HTTP/SOCKS 代理地址（机构 IP 授权类来源——Elsevier/Springer TDM API——必须走这里；`HTTP_PROXY`/`HTTPS_PROXY` 环境变量被有意忽略，也可用 `LITLOOP_PROXY` 环境变量） |
| `proxy_pool` | 空 | 逗号分隔的代理列表；非空时批量下载按代理轮换出口 IP |
| `batch_workers` | `10` | 批量下载并发数（被封 IP 时建议调低到 2） |
| `request_delay_min` | `2.0` | 请求间随机延迟下限（秒） |
| `request_delay_max` | `5.0` | 请求间随机延迟上限（秒） |
| `vpnsci_enabled` / `instsci_enabled` | `false` | 启用 WebVPN |
| `vpnsci_school` / `instsci_school` | 空 | WebVPN 学校名称 |
| `carsi_enabled` | `false` | 启用 CARSI |
| `carsi_idp_name` | 空 | CARSI 机构名称 |
| `auto_relogin` | `true` | 机构会话自愈：下载前自动校验 WebVPN 会话，明确过期才弹浏览器重登 |
| `cache_ttl_hours` | `168` | 下载缓存 TTL（小时），设 0 禁用 |
| `elsevier_api_key` | 空 | Elsevier / ScienceDirect API Key |
| `elsevier_insttoken` | 空 | Elsevier institutional token，仅拿不到校园出口时需要（找机构管理员申请） |
| `use_tor_for_scihub` | `false` | Sci-Hub 走 Tor |
| `flaresolverr_url` | `http://localhost:8191/v1` | FlareSolverr 服务地址 |
| `browser_headless` | `false` | 浏览器是否无头运行 |
| `browser_humanize` | `true` | 浏览器人性化操作 |

</details>

<details>
<summary><strong>下载策略与缓存语义</strong></summary>

| 策略 | 描述 |
|------|------|
| `fastest`（默认） | 多数据源并行，最快获胜 |
| `oa_first` | 优先开放获取，Sci-Hub 兜底 |
| `scihub_only` / `scihub_first` | 仅用 / 优先 Sci-Hub |
| `legal_only` | 仅使用合法数据源（不含 Sci-Hub/LibGen） |

- 每个输出目录的 `.doi_index.json` 记录 `{file, source, strategy, ts}`；超过 `cache_ttl_hours` 视为过期自动重下。
- 显式策略与缓存记录的策略不符时视为未命中重新下载，无需手动删缓存。
- 下载进入机构阶段前自动校验 WebVPN 会话（HTTP 探测重定向），判定过期且 `auto_relogin=true` 时自动重登；CARSI 自带 24 小时新鲜度校验。
- 与 ScanSci Find 闭环：`find` 产出候选 → `manifest` / `batch` 下载并写 `download_results.json` → `scansci-find reconcile` 回写候选状态。（Find 系命令需要可选的 scansci-find CLI：`pip install git+https://github.com/dysprosium231/easyseek.git`（PyPI 上没有 scansci-find；此 fork 版本在缺少私有组件 Journal Scout 时也能检索），未安装时用 search/verify/resolve-oa/build-queue 本地降级链。）

</details>

<details>
<summary><strong>批量调优：代理池与封 IP 停损</strong></summary>

**自动停损**（默认开启）：批量任务连续 3 次检测到 IP 被封（ACS 封锁页 / 403 / 429）会自动取消剩余下载，终端提示：

```
⚠ 已自动停止：连续检测到 IP 被出版商封禁（N 篇返回 ip_blocked），剩余任务已取消。
```

**降低被封概率** —— 让请求像人：

```bash
litloop config-cmd batch_workers 2          # 调低并发（默认 10）
litloop config-cmd request_delay_min 5      # 拉大随机延迟下限（默认 2）
litloop config-cmd request_delay_max 12     # 拉大随机延迟上限（默认 5）
```

**代理池轮换（进阶）**：多个代理轮换出口 IP，每个代理一个独立浏览器上下文，登录一次 cookies 全复用；连续 3 次被封的代理自动剔除：

```bash
litloop config-cmd proxy_pool "socks5://1.1.1.1:1080,http://2.2.2.2:8080"
```

> ⚠ **权衡**：同一登录态从多个 IP 并发访问，少数出版商可能视为异常。机构对这种检测敏感的话，保持 `proxy_pool` 为空即可。

</details>

## 故障排查

| 现象 | 先做 |
|---|---|
| 下载失败 | `litloop check`，会话问题再跑 `litloop session-doctor` |
| Agent 说 Elsevier 需要 insttoken | 不需要：API key + 校园网出口即可（insttoken 仅拿不到校园出口时申请）；NOT_ENTITLED=未连校园网或学校未订阅，连网重试或转其他渠道 |
| Elsevier 403 Requestor configuration settings insufficient | key 有效但 key+出口未配置全文：`litloop elsevier-check --doi <DOI>`——未走校园出口则配 `network_proxy`/`LITLOOP_PROXY`；已走校园出口则是机构未给 key 开 API 全文权限（找机构 Elsevier 管理员，insttoken 为替代路径） |
| 新申请的 Elsevier key 一直 403 | 注册环节问题：机构邮箱注册 Elsevier 账号 + 校园网/学校 VPN 下创建 key（门户靠邮箱域 + 出口 IP 识别机构）；创建后当场 `litloop elsevier-check` 验证 |
| 数据源全红 / 打不开 | Agent 里调 `litloop_diagnostics(check="network")`，给出针对性修复建议 |
| 以前能下的站点突然 403 / 弹 Cloudflare | 大概率 cloakbrowser 过旧：`pip install -U cloakbrowser`（`litloop browser-doctor` 会标出 outdated） |
| WebVPN / CARSI 登录失败 | `pip install "litloop[cloakbrowser,instsci]"`，在可见浏览器完成登录后重试 |
| Sci-Hub 连不上 | 内嵌 Tor：`litloop_tor(action="start", use_bridges=true)`，或配置 `network_proxy` |
| 下载速度慢 | 配置 Elsevier API Key；调 `batch_workers`；数据源延迟用 `litloop_diagnostics(check="health")` 查看 |

<details>
<summary><strong>ACS 提示 IP Address Blocked 怎么办</strong></summary>

批量下载 ACS（`pubs.acs.org`）等出版商时可能遇到整页报错：

> IP Address Blocked — Your IP address has been blocked automatically due to unusual behavior.

这是出版商的自动反爬。机构出口 IP（如校园网）是共享的，一个人触发就可能让整段 IP 被封。

**立即解除**（封禁在出版商侧，代码改不了）：

- 邮件 `ipblock@acs.org` 申诉，附上被封 IP，通常 1–3 个工作日解封
- 换出口 IP（代理 / 手机热点）可绕过，但会失去机构订阅授权，只能下 OA 论文
- 部分封锁会在 24–48 小时后自动解除

**预防**：见上方「批量调优」折叠块 —— 调低并发、拉大延迟、启用代理池。

</details>

<details>
<summary><strong>工作原理：五层竞速</strong></summary>

下载一篇论文时，LitLoop 同时启动多个数据源，按优先级分层竞速，第一个成功立即返回：

```
Tier 1 (4s)  ─ 出版商直链（OA/机构访问）
Tier 2 (5s)  ─ OpenAlex / Unpaywall / DOAJ
Tier 3 (8s)  ─ EuropePMC / CORE / PMC / arXiv
Tier 4 (25s) ─ LibGen / Sci-Hub（带 FlareSolverr 绕过）
Tier 5 (20s) ─ WebVPN / CARSI 机构代理
```

架构说明：公开层（`.py` 源码、配置、文档）Apache 2.0；`_core/*.pyx`（Cython 源码）为专有层不公开；PyPI 安装自带编译二进制（`.pyd`），GitHub 克隆使用纯 Python 回退实现（功能相同，性能略低）。

</details>

## 交流 / Community

使用问题、改进建议请到 [Issues](https://github.com/dysprosium231/litloop-paper-agent/issues) 或 [Discussions](https://github.com/dysprosium231/litloop-paper-agent/discussions) 开贴。

## 贡献者

<a href="https://github.com/annsyun"><img src="https://avatars.githubusercontent.com/u/151621087?v=4" width="50" height="50" alt="annsyun" title="annsyun"/></a>

- **[annsyun](https://github.com/annsyun)** — SAGE 中国站（sage.cnpereading.com）机构授权下载通道：CARSI 会话复用、`articleId` 解析与站点原生下载接口（[#58](https://github.com/Rimagination/scansci-pdf/pull/58)）

## 致谢

LitLoop 基于 **[scansci-pdf](https://github.com/Rimagination/scansci-pdf)**（作者 [Rimagination](https://github.com/Rimagination)，Apache-2.0）开发，绝大部分下载与机构通道能力来自原项目。

原项目在开发过程中参考和借鉴了以下开源项目：

- **[FlareSolverr](https://github.com/FlareSolverr/FlareSolverr)** — 早期反 bot 绕过架构设计
- **[ref-downloader](https://github.com/ltczding-gif/ref-downloader)** — Publisher 专用下载策略（Elsevier crasolve 检测、Wiley PDFDirect、AIP loading page 等）
- **[paper-fetch-skill](https://github.com/Dictation354/paper-fetch-skill)** — 论文获取 Agent Skill 设计
- **[paper-fetcher](https://github.com/fermionoid/paper-fetcher)** — 论文下载流程参考
- **[cloakbrowser](https://github.com/CloakHQ/CloakBrowser)** — Chromium stealth 浏览器引擎

感谢以上项目作者的开源贡献。

## 许可证

[Apache License 2.0](LICENSE)。原项目版权声明保留在 LICENSE 中；衍生版的改动说明见 [NOTICE](NOTICE)。

例外：`src/litloop/_core/` 中的 Cython 编译扩展（`.pyd`/`.so`）为预编译二进制，仅通过 PyPI 分发。其 Cython 源码（`.pyx`）为专有代码，不包含在本仓库中。
