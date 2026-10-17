## 首次使用：配置 Key 和联系邮箱

第一次用、或 `litloop_diagnostics(check="setup")` 显示缺项时，按影响大小逐项问用户要不要配置（都可以跳过，跳过时说明影响），用 `litloop_config(key=..., value=...)` 写入，不要替用户编造或回显完整的 Key：
1. `openalex_api_key`：检索的主要数据源；没有时每天只有约 1000 积分，深度检索可能中途用完。
2. `elsevier_api_key`：下载 Elsevier 全文、补摘要（Scopus 每周 10000 次）。
3. `springer_meta_api_key`：Springer Nature 的 Meta API Key（不是 Open Access API 那个），补 Springer、Nature 文章的摘要；免费每天 500 次；摘要仅限个人、非商业使用。
4. `email`：联系邮箱，只发给 Crossref、OpenAlex、Unpaywall；有它时 Crossref 允许同时 3 个请求。
5. 学校的 WebVPN / CARSI / EZProxy，以及 Zotero 推送的分类。
各接口用量和剩余额度用 `litloop_diagnostics(check="quota")` 查看；深度检索返回的 `api_warnings` 不为空时，转告用户。
