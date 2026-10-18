# 配置

所有配置通过 `litloop_config` 或 `litloop config-cmd` 修改，数据都放在 `~/.litloop/` 下。

<details open>
<summary><strong>常用配置项</strong></summary>

| 配置项 | 默认值 | 说明 |
|---|---:|---|
| `output_dir` | `~/.litloop/papers` | PDF 保存目录 |
| `rename_format` | `author_year_title_doi` | 文件名格式：`author_year_title_doi`、`author_year_title`、`zotero`（作者 - 年份 - 完整标题）、`year_author_title`、`title`、`doi`、`off`（保持原名）。旧配置 `auto_rename: false` 等于 `off` |
| `download_strategy` | `fastest` | 可选 `fastest`、`oa_first`、`legal_only`（不用 Sci-Hub/LibGen）、`scihub_first`、`scihub_only` |
| `scihub_enabled` | `false` | 是否使用 Sci-Hub/LibGen 类来源；默认关闭，由你自己决定是否打开 |
| `scihub_skip_from_year` | `2024` | 此年份及以后发表的文献不查 Sci-Hub（新文献基本没有）；年份取自 app 的文献列表或 DOI，不知年份的照查；0 表示都查 |
| `scihub_route` | `direct` | Sci-Hub 与 LibGen 路由：`direct`（直连）、`proxy`（走 `host_proxy_url`）或 `auto`（批量前在两条线路上测速，用最快的镜像和线路，在 `<data dir>/scihub_route.json` 缓存 1 小时） |
| `scihub_speedtest_doi` | `10.1016/j.colsurfb.2017.09.051` | Sci-Hub 测速所用的已知文献 DOI（约 1 MB） |
| `scihub_speedtest_mirrors` | `5` | Sci-Hub 测速并行测试的镜像数 |
| `scihub_speedtest_seconds` | `40` | Sci-Hub 测速最长超时（秒） |
| `scihub_round_workers` | `10` | 第一轮向 Sci-Hub 并发查询的文献数（纯 HTTP），自定值 |
| `scihub_http_workers` | `12` | Sci-Hub 纯 HTTP 镜像请求共享的线程数，自定值 |
| `search_fill_abstracts` | `true` | 检索结果自动补全摘要 |
| `openalex_api_key` | 空 | OpenAlex Key（强烈推荐，见 [README 依赖与 Key](../README.md#依赖与-key)）；也可用环境变量 `OPENALEX_API_KEY` |
| `elsevier_api_key` | 空 | Elsevier Key，用于全文下载和 Scopus 摘要；也可用环境变量 `ELSEVIER_API_KEY` |
| `elsevier_api_fulltext` | `true` | 是否通过 Elsevier API 下载全文；设为 `false` 时不请求全文，补摘要和 Scopus 仍使用 |
| `network_proxy` | 空 | HTTP/SOCKS 代理；环境变量 `HTTP(S)_PROXY` 会被忽略 |
| `host_proxy_url` | 空 | 只给指定出版商域名走的代理，Sci-Hub、LibGen 选代理线路时也走它 |
| `browser_executable` | 空 | 指定浏览器程序，例如本机 Chrome 的副本 |
| `vpnsci_enabled` / `vpnsci_school` | `false` / 空 | WebVPN |
| `carsi_enabled` / `carsi_idp_name` | `false` / 空 | CARSI |
| `auto_relogin` | `true` | 机构会话过期时自动弹出浏览器重新登录 |
| `institutional_order` | `["webvpn_http", "webvpn_browser", "carsi", "ezproxy"]` | 学校机构通道尝试顺序（依次尝试，未开启的跳过） |
| `batch_workers` | `10` | 批量并发数，被封 IP 时调到 2 |
| `race_grace_seconds` | `15` | 多源竞速结束后非浏览器渠道的迟到等待超时（秒） |
| `race_grace_browser_seconds` | `180` | 多源竞速结束后浏览器通道仍在工作时的迟到等待超时（秒） |
| `institutional_step_seconds` | `240` | 学校通道每一步最长用时（秒，自定值）；超时关掉这一步的浏览器、走下一条通道，你正在登录时自动顺延 |
| `school_routes_after_publisher_login` | `false` | 用学校身份登录出版商官网后仍无全文时，是否还走学校通道（WebVPN、CARSI 最终到同一出版商，结果通常相同） |
| `request_delay_min` / `request_delay_max` | `2.0` / `5.0` | 请求间的随机延迟（秒） |
| `cache_ttl_hours` | `168` | 下载缓存有效期，设 0 表示不缓存 |
| `abstract_translation` / `translation_provider` | `高` / `deepseek` | 摘要翻译的范围和服务（见 [桌面 app 能做什么](../README.md#桌面-app-能做什么)） |
| `update_check_days` | `7` | 每隔几天检查 LitLoop、easyseek、zotero-mcp 有没有新版本，有则在检索、下载结果和 app 设置页提示（不自动安装）；设 0 关闭 |

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
