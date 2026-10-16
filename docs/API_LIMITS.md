# 外部接口：限额与配置

LitLoop 调用的所有外部服务、它们的限额、对应的配置项，以及 LitLoop 怎样遵守这些限额。

**数字的来源一律标明**：

- **官方**：出自服务方的官方文档，附链接，核对日期 2026-10-16；
- **实测**：LitLoop 实际请求得到，写明测的是什么；
- **自定**：官方没有规定、LitLoop 自己选的保守值，不是官方限额；
- **未核对**：官方文档没读到（页面需要人机验证、拒绝访问等），不凭印象填。

限额会变，以官方页面为准；发现变化请更新本文和 `src/litloop/quota.py`、`src/litloop/network.py`。

## 怎么查看用量

- `litloop_diagnostics(check="quota")`：各接口今天的请求数、被限流次数、剩余额度；OpenAlex 一项直接读官方 `/rate-limit` 接口。
- 深度检索每轮记录本轮花费的 OpenAlex 积分，额度不足或频繁被限流时在返回的 `api_warnings` 里提醒。
- 用量存在 `~/.litloop/api_usage.json`。

LitLoop 在两处控制请求节奏：检索和补摘要走 `quota.py`（按服务分别限并发和每秒次数），下载那一侧走 `network.py`（按域名限并发和请求间隔）。

## 一、检索、元数据与摘要

### OpenAlex

- **用途**：快速检索、深度检索（关键词路线和引用路线）的主要数据源；发现引擎（scansci-find）也查它，计入同一个 Key 的额度。
- **配置**：`openalex_api_key`（环境变量 `OPENALEX_API_KEY`），在 [openalex.org](https://openalex.org) 注册后免费获取；联系邮箱 `email`。
- **官方限额**（[速率限制与认证](https://help.openalex.org/how-to-use-the-api/rate-limits-and-authentication)、[价格](https://help.openalex.org/access/pricing/)、[各类请求价格](https://help.openalex.org/access/example-costs/)）：
  - 每日预算：无 Key 0.1 美元；免费 Key 1 美元；每天 UTC 零点重置。1 积分 = 0.0001 美元，响应头里的上限为 10000 积分。
  - 单价：按 ID 取单篇免费；列表或过滤 1 积分；关键词检索 10 积分；内容下载 100 积分；出错不扣。
  - 每秒：无 Key 10 次；有 Key 30 次；布尔运算符超过 5 个的检索，无 Key 1 次、有 Key 5 次。
  - 游标翻页过第一页需要 Key；文档写每页最多 100 条。
  - 超额：日预算用完和每秒超限都返回 429；文档没给区分方法，只能看 `X-RateLimit-Remaining` 是否为 0。
- **实测**（2026-10-16）：
  - 带 `title_and_abstract.search` 的过滤请求每次扣 10 积分，和关键词检索同价；
  - 每页 200 条照常返回、只扣 1 积分（文档写最多 100 条）；
  - 当天三个测试集的全部实跑合计约 2800 积分；深度检索后期每轮扩展约 20–40 积分，大头在建候选池。
  - 生物医学课题（稀土药物载体测试集，2026-10-16）一次实跑读 80 篇就花了约 5900 积分，最贵的一轮 2184 积分：高被引领域里一篇参考文献就被几千篇切题文章引用，旧代码对每篇取回最多 1000 篇（5 页、50 积分）。本地记账当时只记到约 3200，漏掉了关键词检索的请求（已补上）。
- **LitLoop 怎么控制**：
  - 每秒：有 Key 25 次；复杂检索 4 次；无 Key 8 次、复杂检索 0.9 次。都按官方数字留了余量。
  - 并发：有 Key 4 个、复杂检索 2 个；无 Key 2 个、复杂检索 1 个（**自定**，官方未规定并发）。
  - 被限流时按逐次加长的等待重试，最多 6 次；剩余为 0 时直接报“今日额度用完，约几小时后重置”。
  - 无 Key 时改用页码翻页，深度检索开始时提醒。
  - 深度检索每轮扩展的额度上限（**自定**，`deep_search.py`）：
    - 单篇参考文献超过上限时只取被引最多的 200 篇（一页，10 积分）；
    - 一轮最多花 800 积分，当天剩余不到 1000 积分时不再扩展；没来得及查的参考文献按“被几篇种子引用”排在后面，下一轮再查，并在 `api_warnings` 里说明。
  - 剩余额度以 OpenAlex 响应头为准（单位是积分）；快速检索和深度检索的请求都计入本地用量记录。
- **没配 Key**：每天只有约 1000 积分，一次深度检索就可能用完。

### Elsevier（Scopus 摘要、ScienceDirect 全文）

- **用途**：从 Scopus 补摘要；通过 API 下载 ScienceDirect 全文（需要校园网出口）。
- **配置**：`elsevier_api_key`（环境变量 `ELSEVIER_API_KEY`），在 [dev.elsevier.com](https://dev.elsevier.com) 申请；`elsevier_insttoken` 一般不需要。
- **官方限额**（[API Key 设置](https://dev.elsevier.com/api_key_settings.html)）：

  | 接口 | 每周配额 | 每秒 |
  |---|---|---|
  | Abstract Retrieval（Scopus 摘要） | 10,000 | 9 |
  | Scopus Search | 20,000 | 9 |
  | Article Retrieval（ScienceDirect 全文） | 50,000（文本挖掘 Key 不限） | 10 |

  - 配额每 7 天重置，每个接口单独计算。
  - 响应头：`X-RateLimit-Limit`、`X-RateLimit-Remaining`、`X-RateLimit-Reset`（重置时间，Unix 时间戳）。
  - 配额用完：返回 429，带 `X-ELS-Status: QUOTA_EXCEEDED - Quota Exceeded`，不再带剩余额度的头；超过每秒次数也返回 429，文档没说带什么头。
  - 新 Key 默认没有数据权限，要等 Elsevier 核实机构或合作资格；实际配额以核实结果为准。
- **LitLoop 怎么控制**：
  - 补摘要：每秒 8 次；并发 5 个（**自定**）。
  - 判断：只有带 `QUOTA_EXCEEDED` 才认定本周用完；其他 429 等待后重试，最多 3 次。
- **实测**（2026-10-16）：一次深度检索补摘要约用几百次 Abstract Retrieval；当天结束时本周还剩 5688 次。

### Springer Nature

- **用途**：
  - Meta API：补 Springer、Nature、BMC 等文章的摘要，订阅文章也有；
  - Full Text API（TDM）：下载全文，需要机构有文本挖掘权限。
- **配置**：
  - `springer_meta_api_key`（环境变量 `SPRINGER_META_API_KEY`）：Meta API 的 Key。注意用 Meta API 那个，Open Access API 只覆盖开放获取文章；
  - `springer_meta_daily_limit`：默认 500，对应免费档；
  - `springer_api_key`：下载用的 Full Text（TDM）Key，见 `litloop_springer_setup`。
  - 在 [dev.springernature.com](https://dev.springernature.com) 注册后生成 Key。
- **官方限额**（[订阅方案](https://dev.springernature.com/subscription/)）：

  | 档位 | Meta API、Open Access API | TDM |
  |---|---|---|
  | 免费 | 每天 500 次，每分钟 100 次；每账号 1 个 Key | — |
  | 学术和政府机构（需联系） | 每天 10,000 次，每分钟 300 次 | 订阅内容每天 20,000 次，每分钟 200 次 |

- **使用条款**（[API 用户附加条款](https://dev.springernature.com/terms-conditions/)）：摘要受版权保护，只许个人、非商业使用；开放获取文章的摘要按其许可证使用；不得用多个账号或 Key 绕过限额。超限时返回什么，文档没写。
- **LitLoop 怎么控制**：
  - 只查 Springer Nature 自家的 DOI 前缀：10.1007、10.1038、10.1186、10.1023、10.1057、10.1140；
  - 每秒 1.5 次（低于每分钟 100 次）；并发 2 个（**自定**）；当天用满 `springer_meta_daily_limit` 后不再请求。
- **实测**（2026-10-16）：重跑里缺摘要的 15 篇 Springer 订阅文章，Meta API 有 14 篇。

### Crossref

- **用途**：补摘要。
- **配置**：联系邮箱 `email`。填了真实邮箱就进入礼貌池；默认的 `litloop@example.invalid` 不算。
- **官方限额**（[2025-12-01 起的新限额](https://crossref.org/blog/announcing-changes-to-rest-api-rate-limits/)、[访问说明](https://crossref.org/documentation/retrieve-metadata/rest-api/access-and-authentication/)）：

  | 池 | 单篇（`/works/{doi}`） | 列表或检索 |
  |---|---|---|
  | 公共池（不带邮箱） | 每秒 5 次，同时 1 个 | 每秒 1 次，同时 1 个 |
  | 礼貌池（带 `mailto`） | 每秒 10 次，同时 3 个 | 每秒 3 次，同时 3 个 |

  - 超限返回 429；被封返回 403。
  - 响应头 `x-rate-limit-limit`、`x-rate-limit-interval`、`x-concurrency-limit`。
- **LitLoop 怎么控制**：只查单篇。公共池每秒 4 次、同时 1 个；礼貌池每秒 8 次、同时 3 个。

### Semantic Scholar

- **用途**：补摘要（批量接口）；有 Key 时作为检索来源。
- **配置**：环境变量 `SEMANTIC_SCHOLAR_API_KEY`（可选，审批较严）。
- **官方限额**（[产品页](https://www.semanticscholar.org/product/api)）：
  - 无 Key：所有未认证用户共用每秒 1000 次，高峰期会进一步限流；
  - 有 Key：每秒 1 次。
  - 超限返回 429。
  - 批量接口一次最多多少个 ID：**未核对**（接口文档页靠 JavaScript 渲染，没读到）。代码按 500 个分块，沿用以前的写法，待核对。
- **LitLoop 怎么控制**：每秒 1 次、同时 1 个（**自定**，按有 Key 的标准）；补摘要时和其他来源同时问，它慢也不拖住别人。

### Unpaywall

- **用途**：按 DOI 查开放获取全文的位置。
- **配置**：`email`（必填，作为请求参数）。
- **官方限额**（[REST API](https://unpaywall.org/products/api)）：
  - 每天不超过 100,000 次；
  - 请求必须带邮箱；
  - 检索接口 `/v2/search` 已于 2026-09-18 停用，返回 410。LitLoop 只用按 DOI 查单篇的接口。
- **LitLoop 怎么控制**：并发 3 个（继承自原项目，**非官方**）；同一域名两次请求间隔至少 0.5 秒。

### CORE

- **用途**：查开放获取全文。
- **配置**：`core_api_key`（可选），在 [core.ac.uk](https://core.ac.uk/services/api) 用邮箱注册。
- **官方限额**（[CORE API](https://core.ac.uk/services/api)）：每 10 秒 1 次批量请求或 5 次单篇请求；不注册也能用（受其条款约束），注册后性能更好。
- **LitLoop 怎么控制**：同时 1 个，两次请求间隔至少 2 秒。

### OpenAIRE

- **用途**：查开放获取全文。
- **配置**：无（LitLoop 不带 Token）。
- **官方限额**（[使用条款](https://graph.openaire.eu/docs/apis/terms/)）：
  - 带 Token 每小时 7200 次，不带每小时 60 次；
  - 按偏移翻页最多 1 万条；
  - 超限时返回什么，文档没写。
- **LitLoop 怎么控制**：一小时内用满 55 次后跳过 OpenAIRE，交给其他开放获取来源；并发 3 个（继承，**非官方**）。

### arXiv

- **用途**：取预印本元数据和 PDF。
- **配置**：无。
- **官方限额**（[API 使用条款](https://info.arxiv.org/help/api/tou.html)，针对 API、OAI-PMH 和 RSS）：每 3 秒不超过 1 次，同时只用 1 个连接，按你控制的所有机器合计。
- **LitLoop 怎么控制**：`export.arxiv.org` 和 `arxiv.org` 都按同时 1 个、间隔 3 秒。

### NCBI / PubMed Central

- **用途**：用 PMC ID 转换接口把 DOI 换成 PMCID，再取 PMC 的 PDF。LitLoop 不调用 E-utilities。
- **配置**：`email`（选填；填了真实邮箱就随请求带上，另带 `tool=litloop`）。
- **官方说明**（2026-10-16 核对）：
  - [PMC ID Converter API](https://pmc.ncbi.nlm.nih.gov/tools/id-converter-api/)（页面最后修改 2025-06-10）：
    - 新地址 `https://pmc.ncbi.nlm.nih.gov/tools/idconv/api/v1/articles/`，旧地址 `/pmc/utils/idconv/v1.0/` 301 跳转到这里；
    - 每次最多 200 个 ID，同一次请求的 ID 必须同一类型；
    - 请求方应带上 `tool` 和 `email`（“Requested”，不是必填）；
    - **页面没有写每秒请求上限。**
  - [PMC For Developers](https://pmc.ncbi.nlm.nih.gov/tools/developers/)（页面最后修改 2026-08-20）：自动获取 PMC 内容只能用 PMC Cloud Service、OAI-PMH、E-utilities 和 BioC API，通过其他自动方式系统性（批量）获取文章是禁止的。
  - [E-utilities](https://www.ncbi.nlm.nih.gov/books/NBK25497/)（页面最后更新 2022-11-17，用户代为读取）：无 API Key 每秒不超过 3 次，有 Key 默认每秒 10 次；大任务放在周末或美东时间工作日 21:00–5:00；违规可能封 IP。**这条只适用于 `eutils.ncbi.nlm.nih.gov`，LitLoop 目前不用。**
- **LitLoop 怎么控制**：NCBI 没有并发上限，同一域名间隔至少 0.5 秒（自定）。
- **PMC 的 PDF**：直接从 `www.ncbi.nlm.nih.gov/pmc/articles/<PMCID>/pdf/` 下载不属于上面四种允许的自动获取方式，所以只在单篇下载时用；批量下载（含分道调度、批量的机构回退和预印本回退）不走 PMC，开放获取的生物医学论文由 Europe PMC 等来源覆盖。（2026-10-16 用户决定）

### Europe PMC、DOAJ

- **用途**：查生物医学文献和开放获取期刊。
- **官方限额**：**未核对**，尚未查阅。
- **LitLoop 怎么控制**：Europe PMC（`europepmc.org`、`www.ebi.ac.uk`）和 DOAJ 并发 3 个（继承，**非官方**）；同一域名间隔至少 0.5 秒。

## 二、出版商网站与机构通道

- **出版商网站**（ACS、Wiley、ScienceDirect、Science、Nature、IOP、IEEE、AIP、RSC、Taylor & Francis、SAGE、OUP 等约 30 家）：
  - 官方没有公开的限额；批量下载过快会被封 IP，例如 ACS 的 “IP Address Blocked”，封禁在出版商那边。
  - 配置：`batch_workers`、`institutional_workers`、`max_browser_workers`、`request_delay_min` / `request_delay_max`、`browser_*`。
  - LitLoop：连续 3 次检测到封锁页、403 或 429 时自动停止批量任务；被封时可调低并发、加大间隔（见 README）。
- **机构认证**：
  - WebVPN（`vpnsci_*`）、CARSI（`carsi_*`）、EZProxy（`ezproxy_*`）、OpenAthens；
  - 限制由学校决定，主要是会话过期；`auto_relogin` 控制是否自动弹出浏览器重新登录。
- **网络**：
  - `network_proxy`：所有请求走同一个代理；
  - `host_proxy_url` / `host_proxy_hosts`：只给指定出版商域名走代理；机构通道必须绕开代理；
  - `proxy_pool`。

## 三、灰色来源

- Sci-Hub、LibGen 等：**默认关闭**（`scihub_enabled`），由用户自己决定是否打开。
- 相关配置：`scihub_domains`、`scihub_use_browser`、`use_tor_for_scihub`、`tor_proxy`、`tor_use_bridges`、`flaresolverr_url`。
- 没有官方限额；镜像经常失效。

## 四、Zotero

- **本机 Zotero 的本地接口**（`localhost:23119`）：官方说明不做限流（[Web API 基础](https://www.zotero.org/support/dev/web_api/v3/basics)，2026-07-29 更新）。LitLoop 默认通过 zotero-mcp 本地写入。
- **Zotero Web API**（`zotero_api_key`、`zotero_library_id`、`zotero_library_type`）：
  - 官方建议同时不超过 4 个请求；
  - 服务器繁忙时可能带 `Backoff` 头，或返回 429 带 `Retry-After`，应按其等待；反复触发 429 可能被临时封禁。
- **其他配置**：`zotero_mcp_python`、`zotero_mcp_env_script`、`zotero_push_timeout`。

## 五、配套组件

- **scansci-find（发现引擎）**：快速检索和深度检索的关键词路线由它请求 OpenAlex 等来源，计入同一个 OpenAlex Key 的额度，用量也会反映在 `/rate-limit` 里。
- **litloop-journal-rank（期刊分区插件，未公开）**：
  - 分区数据每月检查更新，撤稿库每周刷新；
  - 数据来源的限制见该插件的文档。
