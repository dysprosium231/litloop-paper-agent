# 下载与机构通道

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

只要 Key 加上校园网出口就够了，不需要 insttoken。返回 `NOT_ENTITLED` 说明当前不在校园网，或者学校没订这本期刊。同一个 Key 也用来从 Scopus 补摘要。若 API 返回 403 `Requestor configuration settings insufficient`（Key 无全文权限），LitLoop 会在 `<data dir>/elsevier_no_fulltext.json` 记录该 Key 的 sha256 哈希前缀（不存明文 Key），并在 30 天内不再向 API 请求全文；也可通过配置项 `elsevier_api_fulltext: false`（默认 `true`）主动关闭 API 全文下载。

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

1. 机构通道靠校园网出口 IP 识别身份，LitLoop 自己的流量必须直连。
2. 代理软件只开“系统代理”模式时不用管：LitLoop 不读系统代理和 HTTP_PROXY/HTTPS_PROXY（requests 用 trust_env=False），它启动的浏览器带 `--no-proxy-server`。
3. 开了 TUN 模式（如 v2rayN 的 TUN、Clash 的虚拟网卡）时，要在代理软件里给 LitLoop 的进程设直连：运行 LitLoop 的 `python.exe`，以及 LitLoop 启动的浏览器 `litloop-chrome.exe`。桌面 app 的“设置 → 网络 → 检测”会自动识别模式并给出步骤。
4. **v2rayN**：路由设置里新建规则，“进程名”一栏填进程名，出口选 direct，把规则移到最前面（规则按顺序匹配，前面的先生效）；每条规则只填域名、IP、进程名中的一项。官方文档没有写进程名的值格式，不生效时改填完整路径。来源：[v2rayN UI 说明](https://github.com/2dust/v2rayN/wiki/Description-of-some-ui)
5. **Clash Verge Rev**：右键订阅 →“编辑规则”→ 前置规则，添加 `PROCESS-NAME,python.exe,DIRECT` 和 `PROCESS-NAME,litloop-chrome.exe,DIRECT`；若不生效，在“扩展配置”里加 `find-process-mode: always`。来源：[规则配置](https://www.clashverge.dev/guide/rules.html)、[General 配置](https://wiki.metacubex.one/config/general/)

`network_proxy` 只填学校提供的校园隧道；个人代理不要填（填了会跳过学校登录，机构下载会失败）。

个别出版商只在直连时很慢，这种情况可以用 `host_proxy_url` 让指定域名单独走代理，Sci-Hub、LibGen 选代理线路时也走它。

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
