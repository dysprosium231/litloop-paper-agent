---
name: litloop
description: 下载学术论文。支持 DOI、arXiv ID、关键词搜索、批量下载、Elsevier API、WebVPN/CARSI 机构访问、下载失败排障。当用户要求下载论文(单篇或批量)、搜索文献、获取引文、配置 Elsevier/ScienceDirect API、或下载遇到 Cloudflare/验证页/代理问题时使用。用户仅讨论 PDF 解析/转换工具的对比或选型(如 pymupdf4llm vs MinerU)而无需检索文献证据时不要使用;只有确实要检索或下载文献时才加载本 skill。
---

# LitLoop — 学术论文下载与检索

20+ 数据源并行竞速,首个成功立即返回。数百篇以上的清单需要先分类摸底(OA/Sci-Hub/需机构)时,转用 `litloop-sort` skill。

## 环境检查

工具列表含 `litloop_*` → 用 MCP 工具;否则 CLI 兜底,先 `litloop check` 确认依赖。

## 策略选择规则(必读)

**用户指定了来源 = 只用那个来源:**

| 用户说的是 | 策略 | 说明 |
|-----------|------|------|
| "从 Sci-Hub 下载" | `scihub_only` | 只走 Sci-Hub |
| "优先 scihub" | `scihub_first` | OA 仍竞速——OA 更快时最终来源可能是 OA |
| "只要免费合法的" | `legal_only` | 排除 Sci-Hub / LibGen |
| 没指定 | `fastest`(默认) | 全源并行竞速 |

```bash
litloop config-cmd download_strategy scihub_only
```

## 单篇下载

```bash
litloop get <DOI>                    # 零配置竞速
litloop fetch <DOI> [--output DIR]   # 7 步机构级联
```

竞速分层:Tier1 出版商直链(4s) → Tier2 OpenAlex/Unpaywall(5s) → Tier3 EuropePMC/PMC/arXiv(8s) → Tier4 Sci-Hub/LibGen 无头浏览器(25s) → Tier5 WebVPN/CARSI(20s)。国内 Tor 常失败,直接不用。

单篇竞速默认是**对冲级联**(`race_mode=hedge`):车道按评分排序,最优源先发,`hedge_delay_seconds`(默认 1.5s)内无响应才加发下一车道,已发车道快速失败则立即加发——请求量/反爬触发率比齐发降 3-5 倍,尾延迟几乎不变。`config set race_mode full` 恢复旧版齐发竞速。

**换源重下必须清缓存**:`rm -f <out>/.doi_index.json && rm -rf ~/.litloop/cache/*`。

## 批量下载

```bash
litloop batch dois.txt --output <dir>            # 默认车道调度(见下)
litloop batch dois.txt --no-lanes                # 退回逐篇对冲竞速
litloop batch dois.txt --scihub                  # 灰源竞速引擎(scihub_only/grey_only/scihub_first 策略自动走此路)
litloop publisher-batch f.txt --publisher elsevier
```

**车道调度是批量默认**(CLI 默认开,MCP `batch_download` 对 ≥3 条标识符自动启用;`config set batch_default_lanes false` 全局关闭):先 **S2 批量预嗅探**(500 DOI/请求,直接拿到 OA PDF 直链,≥`lane_s2_batch_min`(10) 条才启用)→ **快车道并行 HTTP**(OA 直链 + Elsevier API + MDPI CDN 规律构造 `lane_mdpi_cdn`)→ 失败溢流 **灰色源竞速** → **机构级联**,最后瞬时失败冷却重试。MDPI(`10.3390`)走 mdpi-res.com CDN 直连(94% 命中,绕开主站反爬),不消耗嗅探请求。车道模式忽略 `batch_id` 断点(灰色道内部仍保留断点);需要断点续传用 `--no-lanes`。

⚠️ **>300 篇必须分批**——校验阶段并发 validate 会 TimeoutError 崩溃,一个都不下。断点续传:重跑同文件自动跳过已完成;MCP 用相同 `batch_id`。连续 Cloudflare 拦截时停下来排障(见下),不要硬冲。

## 检索与引文

```bash
litloop search "关键词" --limit 10 --sort cited_by_count   # 13源引擎,失败降级三源
```

搜作者优先用 `--author "Dabo Guan"` 或 OpenAlex `--author-id`,别把人名放 query(全文匹配会混入同名/被引提及)。引文格式(citation: bibtex/ris/endnote)仅 MCP 支持。发现层:CLI `plan → estimate → find --out <dir>`,再 `build-queue <dir> --out queue.txt` 接 batch。OpenAlex 配额按 IP 每日计(429 就降级 Unpaywall 单点并发);Unpaywall 批量端点常 500,自写单点并发脚本更稳。

## 检索与筛选（AI 给理由，用户决定）

用户要找文献时按这个流程，不要直接甩一个列表：

1. **定标准**：把问题拆成 2–5 条纳入标准（研究对象、关注性质或现象、方法、文章类型、年份）。
   - 快速查找：直接写在结果上方，不等确认。
   - 课题调研、综述等深度检索：先列出标准请用户确认或修改，再检索。
2. **检索**：`litloop_search` 用英文关键词；中文问题先译成英文检索词，必要时换 2–3 组同义词分别检索。用户在意期刊质量时用 `max_cas_zone` / `max_cas_sub_zone` / `max_jcr_quartile` / `exclude_warning`，不要擅自加过滤。
3. **逐篇判断**：读每篇的标题和 `abstract`，按标准给相关度 **高 / 中 / 低 / 不相关**，写一句中文理由，说明它对应哪条标准、缺了什么。`abstract_missing` 的文章在理由里注明“仅凭标题”。不要编造摘要里没有的内容。
4. **保存**：`litloop_screening(action="save", question=..., criteria=[...], query=..., mode="quick"|"deep", items=[{doi, title, year, journal, journal_rank, cited_by_count, abstract_missing, retracted, concern, relevance, reason}, ...])`。同一课题的后续检索传 `session=` 合并进同一会话。
5. **呈现**：中文表格，列为 序号、相关度、文章、年份、期刊/分区、理由。**序号和顺序一律用 save 返回的 `numbering`**（保存时会按相关度重排），不要用自己发送时的顺序。`retracted` 标“⚠已撤稿”，`concern` 标“⚠关注声明”，`journal_warning` 标“⚠预警期刊”。不相关的文章也列出（可折叠在最后），**不替用户删除任何文章**。附上会话的 Markdown 文件路径。
6. **用户选择**：用户说“要 1、3、5”后调用 `litloop_screening(action="decide", session=..., selected=[...], rejected=[...], out_file=...)` 记录。每次 decide 记录的是**完整选择**，会覆盖上一次；用户追加或改选时传入全部选中项。把返回的 `chosen` 列表（序号 + 标题）念给用户核对，再按需要下载（`litloop_batch_download`）或推送 Zotero。

### 深度检索（要把一个课题查全时）

1. 标准确认后，请用户给 1–3 篇确定相关的种子文章（DOI）；没有时先用 `litloop_search` 找几篇请用户认。
2. `litloop_deep_search(action="start", question=..., queries=[4–8 组英文检索词], seeds=[DOI], core_terms=[1–3 个核心词], criteria=[...])`，返回第一批（默认 30 篇）和 `session`。用户给了必找文章时传 `must_find=[DOI]`，结果的 `coverage` 会报告每篇的状态。
3. 逐篇判断后用 `litloop_screening(action="save", session=..., mode="deep", items=[...])` 保存，**这一批每篇都要评**，没评的下一批会再出现。
4. `litloop_deep_search(action="next", session=...)` 取下一批。评为“高”的文章会自动成为新种子；从第二批起排序由模型根据全部评分学习（`ranked_by="model"`），所以“低 / 不相关”也要如实评。
5. 返回 `stop_suggested=true` 时，把 `stop_reason` 告诉用户并建议停止；用户要继续就接着 `next`。随时可以用 `action="status"` 看已筛篇数和各相关度数量。
6. 筛完后按上面第 5、6 步呈现和让用户选择。跨了多批之后编号会变，**decide 时优先传 DOI**。

## 场景 → 工具链(常见意图直接对号入座)

| 用户说 | 动作 |
|---|---|
| "下载这篇 <DOI/arXiv/文章页URL>" | `get <标识符>`;URL 直接喂,自动抽 DOI/arXiv;要补充材料加 `--si`,要 AI 可读全文加 `--md`(PDF 仍是默认交付物) |
| "某人的全部/近年论文" | `search --author "Name" --out queue.txt` → `batch queue.txt --lanes` |
| "某主题/关键词 + 年份/被引过滤" | `search "kw" --year-from 2023 --sort cited_by_count [--out queue.txt]` |
| "给一份清单(xlsx/csv/txt/bib/APA)" | `batch 文件 --lanes`(表格/队列自动识别;渠道按 DOI 前缀自动预测) |
| "上次有失败的,补齐" | `batch --retry <output>/batch_results.json`(自动读失败清单重跑) |
| "模糊引用('Wang 2023 CRISPR 那篇')" | 先 `search "Wang 2023 CRISPR"` 拿候选让用户确认,别硬猜 DOI |
| 系统性文献发现(PRSIMA/引文追链/高召回多源检索) | 装了 `scansci-find` skill/CLI → 用它;没装 → 本地降级链 `search → verify → resolve-oa → build-queue → batch --lanes`(Find 系 MCP 工具需要可选的 scansci-find CLI) |
| "帮我找某课题的文献/筛一筛" | 按上面“检索与筛选”流程：定标准 → `litloop_search` → 逐篇给相关度和理由 → `litloop_screening` 保存，用户挑选后 `decide` |
| "放进 Zotero" | `litloop_zotero_push`（经 zotero-mcp；授权本地写入后直接写进本机 Zotero，同步交给 Zotero） |
| "只要合法来源" | `legal_only` 策略或 `--scihub` 反选 |

队列文件格式(机器可读,人也能手编):`identifier<TAB>channel<TAB>oa_url`,channel ∈ oa/elsevier/grey/institution/auto;`search --out` 与 csv/xlsx 解析自动打标(10.1016→elsevier)。`batch --lanes` 按车道调度:Elsevier API/OA 快车道并行 HTTP → 灰色源竞速 → 机构级联串行,失败逐级溢流。

## 机构渠道(按 DOI 前缀路由,先快后慢)

1. **`10.1016`(Elsevier)→ Elsevier API**:key + 校园网 IP 即可,**无需 insttoken**,1–2 s/篇(需机构桶里常占一半以上)。
   ```bash
   litloop elsevier-setup --api-key YOUR_KEY --validate
   ```
2. **`10.1007`(Springer)→ Springer TDM API**(`litloop_springer_setup`):机构订阅+TDM 授权的 key(dev.springernature.com 注册,ORCID 关联机构),交付 **JATS XML 全文**(无 PDF 端点),1–2 s/篇。`test=true` 用已知付费文章探测权限:`entitled`=通,`not_entitled`=key 有效但机构无订阅/TDM 权限(此时走 WebVPN/CARSI),`invalid_key`=重生成。竞速引擎与车道模式都已接入;无 key 时该车道零成本让位。
3. **其余出版商 → WebVPN/CARSI**:`litloop schools 清华` → `setup 清华大学` → `login`(浏览器 CAS,cookie 自动保存,勿让用户手动复制)→ 正常 `get`。CARSI:`config-cmd carsi_idp_name <学校>` + `federated-login elsevier`。卡登录页 = session 过期,重新 login。

## 排障速查

| 症状 | 修复 |
|---|---|
| Sci-Hub 返回 Cloudflare/Turnstile 页 | `browser-status` 查后端;Turnstile widget 空白是 patchright 已知 bug(1.56~1.61)→ `config-cmd browser_backend camoufox`(反指纹 Firefox,已装并实测)或 `config-cmd browser_backend cloakbrowser`,或改走机构路径 |
| camoufox 提示未安装内核 | `python -m camoufox fetch`(需代理);wrapper: `pip install litloop[camoufox]` |
| cloakbrowser 内核过老 | `config-cmd browser_executable "C:\Program Files\Google\Chrome\Application\chrome.exe"` |
| 所有源超时 | 查 `network_proxy`;本机代理可能没启动,先测端口 |
| Elsevier 只回 1 页预览 | key 无效/无权限,重新 setup --validate |
| 下载失败结果带 `source_failures` | 逐渠道失败明细(渠道多为**临时**不可用):`error_type=rate_limited/network` → 稍后重试;`config_needed` → 缺用户决策,按 `action` 处理(如 `ask_user_email`)。不要静默换渠道了事,把明细告诉用户让其选择 |
| `config_needed`/`ask_user_email` | Unpaywall 必须真实邮箱(占位邮箱 422)。**问用户要邮箱**,然后 `litloop_config(key="email", value="<用户邮箱>")` 并重试 |
| 想恢复旧版齐发竞速 | `config set race_mode full`(默认 hedge 对冲级联,请求量已降 3-5 倍) |
| 批量想回逐篇竞速 | CLI `batch --no-lanes`;MCP `batch_download(..., lanes=false)`;全局 `config set batch_default_lanes false` |
| Agent 坚持要 Elsevier insttoken | 不需要:API key + 校园网出口即可;NOT_ENTITLED=未连校园网或学校未订阅,连网重试或转其他渠道 |
| MCP 无 `litloop_*` 工具 | plugin 未启用 → 走 CLI 兜底 |
| 403/超时 ≠ 无全文 | 多为反爬或网络受限,留给浏览器/代理轮 |

自写嗅探脚本时:HTTP 200 ≠ 成功(验证页也是 200),按内容分类 hit/miss/turnstile/blocked;验证页按 IP 频率随机插入,假 DOI 也会触发——重试 + 轮换域名(sci-hub.vg/al/ee 国内可达,se/ru/st 不通;LibGen 全镜像国内不可达且 scimag 与 Sci-Hub 同库,不用重复测)。
