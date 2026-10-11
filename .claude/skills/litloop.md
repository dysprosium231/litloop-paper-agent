---
name: litloop
description: 下载学术论文。支持 DOI、arXiv ID、关键词搜索、批量下载、机构登录绕过付费墙。当用户要求下载论文、搜索文献、获取引文时使用。
---

# LitLoop — 学术论文下载

MCP 工具前缀：`litloop_`

---

## 快速开始

**首次使用**：运行 `litloop_auto_setup` 一键检测环境（Tor、Sci-Hub、camofox）。

**下载论文**：用 `smart_download`，零配置，自动尝试所有来源：

```
litloop_smart_download(identifier="10.1038/nature12373")
litloop_smart_download(identifier="10.1038/nature12373", bibtex=true)
```

## 付费墙：统一登录（推荐）

遇到付费论文时，用 `login` — 自动识别出版商、打开浏览器、引导用户完成机构 SSO 登录，cookie 跨所有下载复用：

```
litloop_login(identifier="10.1126/science.aec6396")
```

`identifier` 可以是 DOI 或出版商名（`elsevier`, `wiley`, `nature`, `springer`, `ieee`, `science`, `tandfonline`, `acs`, `rsc`, `aip`, `aps`, `iop`, `oxford`, `acm`）。

登录完成后关闭浏览器，cookie 自动保存。后续下载自动使用。

---

## 搜索论文

```
litloop_search(query="CRISPR gene therapy", limit=10, year_from=2020)
litloop_search(query="machine learning", sort="cited_by_count")
```

## 批量下载

```
litloop_batch_download(identifiers=["10.1038/a", "10.1016/b", "2301.00001"])
```

支持断点续传（同 batch_id 自动跳过已完成项）。

## 从 .bib 文件批量下载

```
litloop_import_bib(bib_file="/path/to/refs.bib")
```

## 从文本文件解析并下载

解析 APA/BibTeX/DOI 列表 → 补全缺失 DOI → 批量下载：

```
litloop_resolve_and_download(file_path="/path/to/papers.md")
```

## 获取引文

```
litloop_citation(identifier="10.1038/nature12373", format="bibtex")
```

format: `bibtex`（默认）、`ris`、`endnote`

---

## Tor 匿名代理

Sci-Hub/LibGen 被封锁时：

```
litloop_tor_install              → 安装 Tor（~30MB）
litloop_tor_start                → 启动 SOCKS5 代理
litloop_tor_start(use_bridges=true) → 网络受限时用桥接
litloop_tor_stop                 → 停止
```

`smarter_download` 自动启用 Tor，无需手动传参。

---

## 高级：机构代理（WebVPN / CARSI / EZProxy）

以下为手动配置流程。大多数情况下 `login` 工具已足够。

### WebVPN 高校代理

```
litloop_vpnsci_schools(query="北京")        → 搜索学校
litloop_vpnsci_set_school(school="北京大学") → 设置学校
litloop_vpnsci_login                        → 浏览器登录
litloop_vpnsci_status                       → 确认 session_valid=true
```

### CARSI 联邦认证

直接走出版商机构登录，无需 WebVPN 中转：

```
litloop_config_set(key="carsi_enabled", value="true")
litloop_config_set(key="carsi_idp_name", value="你的学校名称")
litloop_carsi_login(publisher="sciencedirect")
litloop_carsi_status
```

支持：sciencedirect, springer, wiley, ieee, tandfonline, nature

### EZProxy 图书馆代理

```
litloop_config_set(key="ezproxy_enabled", value="true")
litloop_config_set(key="ezproxy_login_url", value="https://libproxy.你的学校.edu.cn/login?url={url}")
litloop_ezproxy_login
litloop_ezproxy_status
```

### camofox-browser（持久化浏览器）

```
litloop_camofox_status                       → 检查状态
litloop_camofox_login(login_type="webvpn")   → 浏览器登录并导入 cookie
litloop_camofox_import_cookies(cookie_file="cookies.txt") → 导入 Netscape cookie
```

---

## 诊断与配置

```
litloop_health_check(detailed=true)  → 所有数据源状态与延迟
litloop_network_diagnose             → 网络诊断 + 修复建议
litloop_setup_check                  → 环境检测 + 安装建议
litloop_source_scores                → 各数据源成功率排名
litloop_config_get                   → 查看当前配置（敏感值已掩码）
litloop_config_set(key="scihub_enabled", value="true")
litloop_cache_clear                  → 清除下载缓存
```

---

## 常见场景

| 场景 | 操作 |
|------|------|
| 下载一篇论文 | `smart_download(identifier="DOI")` |
| 首次使用 | `auto_setup` → `smart_download(identifier="DOI")` |
| 下载失败（付费墙） | `login(identifier="DOI")` → 用户登录 → 重试 `smart_download` |
| 下载失败（网络） | `tor_start` → `smart_download(identifier="DOI")` |
| 批量导入 .bib | `import_bib(bib_file="refs.bib")` |
| 搜索某领域文献 | `search(query="...")` → 选 DOI → `smart_download` |
| 诊断问题 | `network_diagnose` 或 `health_check(detailed=true)` |
