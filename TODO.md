# 进度与待办

> 这是项目唯一的进度表，只留没做完的事。每完成一步就更新，并和对应代码放在同一个提交里；做完的事、实验结论和用户做的决定写进 [docs/DECISIONS.md](docs/DECISIONS.md)，这里只留一句和链接。日期一律写绝对日期。
> 设计与验收标准见 [CHARTER.md](CHARTER.md)，代码布局见 [docs/LAYOUT.md](docs/LAYOUT.md)。2026-10-19 重构前的 TODO 原文归档在 DECISIONS 末尾。
>
> 分六块：**路线图**（CHARTER 各部分的状态）、**正在做**（Agent 在推进）、**待用户决定**、**待实测**（代码已改，要用户配合在真实环境里验）、**待办池**（还没开始）、**推广**（让需要的人找到它；用户 2026-11-04 要求记下）。CHARTER 里还没达到验收标准的部分，路线图里都要有一行。

## 路线图

排期（2026-10-16 定）：先做 app，检索阶段 2 的优化穿插进行。状态：✅ 达标 · 🔶 做了，未达标或待实测 · ⬜ 没开始。

| CHARTER 的部分 | 状态 | 还差什么 |
|---|---|---|
| 检索 A 快速查找 | 🔶 | 速度：每组 5–30 秒，标准是 15 秒内（待办池「快速检索提速」） |
| 检索 B 深度检索 | 🔶 | 稀土药物载体测试集只找到 9 篇中的 2 篇（待办池「类别多、彼此引用少的课题」） |
| 检索 C 系统综述 | 🔶 | 流程统计与导出已做（历史记录里的「检索流程」和「导出流程」）；还差中断后续跑（待办池「阶段 3」） |
| 检索 D 定期追踪 | ✅ | 保存检索式、检查、历史页「追踪」卡片（检查后列出新文献并可一键加入下载）、app 打开时自动检查并通知新文献（2026-11-05） |
| 中文文献 | ⬜ | 待办池 |
| 下载 | 🔶 | 待实测 1–4、8–11 |
| Zotero | 🔶 | 待实测 5（app 里写入） |
| 分析归纳 | ⬜ | 待办池「分析归纳」；开工前和用户确认验收标准 |
| app：地基 | ✅ | |
| app：筛选页 | 🔶 | 深度链接 2026-11-05 做了（`litloop://session/<id>`，待实测）；完成通知 2026-11-04 做了（待实测 12） |
| app：任务层 + 后端 A（外部 Agent） | 🔶 | 待实测 5（真实检索） |
| app：后端 B（内置 Agent，多家模型） | 🔶 | 客户端、任务循环、五个工具、`agent_backend` 开关、系统提示词与按批数的步数都已做完（2026-11-06）；还差真实 Key 实测与按任务分模型（待办池「app」） |
| app：初次引导与设置 | ✅ | 后端 B 的模型配置随后端 B 做 |
| app：术语表 | 🔶 | 待实测 7 |
| app：下载队列与 Zotero | 🔶 | 待实测 1–5 |
| 发布：标准 Windows 程序（安装包与自动更新） | 🔶 | NSIS 与 MSI 两个包都能打出来（v1.18.2 只发了 NSIS）；还差自动更新、诊断包、签名（暂不做）、干净机验收 |
| 完整流程（真实课题从检索到 Zotero 不用手动补救） | 🔶 | 待办池「阶段 2 剩余」：深度检索 → 批量下载 → 推送 Zotero 连起来走一遍 |

## 正在做

（无。2026-11-05 收工：当天做到「缺口盘点写进文档 + `_core.py` 抽出 Elsevier 一段 + 两件适配器测试」；CI 队列里还有几次 `queued` 的推送结果没看。下次开工最顺的一刀是给内置 Agent 补系统提示词并放宽步数（自测就能验，见待办池「app → 后端 B」的缺口），其余等用户定：分析归纳验收标准、深度检索第三路候选、MSI 与诊断包。）

## 待用户决定

- [ ] **要不要重议「不拆大文件」这条约定**（2026-11-05 提出）：`src/litloop/sources/__init__.py` 2112 行，预印本兜底、车道晚到文件、Sci-Hub 慢速镜像三处新逻辑这周都加在里面；抽出一个 `download/race.py` 的风险可控，但会动竞速与兜底的主干。`server.py` 与 `publishers/strategies/_core.py` 维持「不拆」。
- [ ] 停止规则改成“连续 2 批才停”：SYNERGY 10 个综述上都更好（见 DECISIONS「SYNERGY 离线评测（第二批，共 10 个综述）」）。用户 2026-10-28 要求先在自己的 3 个材料测试集上重放看看；重放要花 OpenAlex 积分（每天 10,000），三个都已跑完。**三个材料集上“连续 2 批”都是多读 0 篇**：SiC/Al 现行规则 180 篇停、已找全 19 篇，多读 30 篇还是 19；PDC 现行规则 150 篇停（25/26），180 篇仍是 25/26；稀土现行规则 120 篇停（2/9），读到 150、180 篇仍是 2/9。建议不改（综述类样本上的收益没有在材料课题上复现），等用户定（见 DECISIONS「停止规则重放」三条）。
- [ ] **「分析归纳」的验收标准**（CHARTER 里这条一直写「待定」，不先定就做不出可验收的东西）。我拟的最小版本，请用户确认或改：
  1. 输入：一次检索里标为「高」并已下载 PDF 的文献（至少 10 篇，其中混 2 篇故意放进去的无关文献）。
  2. 输出：一张 Markdown 表，每行一篇，列固定为「文献（作者年份）/ 材料体系 / 制备或表征方法 / 关键性能数字（带单位）/ 出处（页码或章节）/ 备注」；抽不到的格写「文中未见」，不猜。
  3. 定量指标：数字类字段准确率 ≥90%（人工抽 30 格核对）；每篇至少有 3 格非「文中未见」；无关文献必须被标成「与本课题无关」。
  4. 可追溯：每一格都能点回 PDF 的那一页；表能导出 Markdown/CSV。
  5. 时间：30 篇 PDF 在 10 分钟内出表（本机，不含模型费用）；失败的 PDF 单独列出原因，不中断整批。
  6. 不做：不写综述正文、不替人下结论（那是人的事，见 CHARTER 第一条原则）。

## 待实测（要用户配合）

代码都已改完，测试只验证过逻辑；要在真实网络、真实账号下各验一次。

1. [ ] **重测「下载测试 · 新文献（2024–2026，20 篇）」剩下的 17 篇**，按 `[timing]` 日志给出每段、每篇用时表，再定下一步优化。一起验收：
   - 出版商官网先看页面再决定等不等（`_wait_for_pdf_link`；Springer 那篇原来白等约 80 秒）；
   - 出版商官网只用专用窗口（见 DECISIONS「出版商官网只用专用窗口」）：最小化的窗口里人机验证能否自己通过、需要操作时窗口有没有弹到前台；连续下几篇同一出版商，专用窗口能否留住验证记录；
   - 选择机构页结果一出来就点、`fill` 短超时（看日志里每个输入框的耗时）；
   - 跳过 / 取消立即有反馈，跳过后只关这一篇的浏览器（原来要等 101 秒）；
   - SAGE 等网站直连超时一次后 30 分钟内不再直连；
   - Sci-Hub 一轮不先等镜像探测，新文献按年份跳过 Sci-Hub；
   - 失败原因：真实遇到“学校没有订阅”“超时”“人机验证”时，app 里显示的原因和「尝试经过」对不对（评审 4）。
   - 细节和各家用时见 DECISIONS「第二轮重测发现的问题（细节从 TODO 移来）」。
2. [ ] **需要用户操作时发系统通知**（见 DECISIONS「需要用户操作时发系统通知」）：新的登录 / 人机验证提示、整批完成各发一次。点通知切到前面没做，插件在桌面上没有点击回调。
3. [ ] **大清单（>30 篇）分队列**（见 DECISIONS「大清单分队列」）：开放获取和 Sci-Hub 两轮自动跑完，剩下的按出版商分队列。
4. [ ] **MCP 真实批量下载几篇**（含一篇 Elsevier 订阅论文；验收「去掉 publisher_batch」），要用户登录。
5. [ ] **app 里的其余功能**：发起检索（已走通“发起 → 失败提示”，真实检索要用户先重新登录 `claude` 命令行）；Zotero 写入；摘要翻译。
6. [ ] **学校认证引导**：在 app 里选学校、弹窗登录、试下载一篇订阅论文。
7. [ ] **术语表**：真实检索后看自动建议条目的质量（功能已做完，见 DECISIONS「术语表」及之后几条）。
8. [ ] **预印本**：拿 `10.1038/s41598-024-67653-y` 重下一次，看是否先拿到 Scientific Reports 的 12 页正式版；只给预印本来源时看文件名有没有 `_preprint`、下载任务里有没有「预印本，不是正式发表版」（见 DECISIONS「预印本另存，正式版优先」）。
9. [ ] **车道晚到落盘**：真实批量里看日志有没有「N 篇的 PDF 在超时之后才落盘」，确认同一篇没有被重下（见 DECISIONS「车道里晚到落盘的文件按成功收下」）。
10. [ ] **Sci-Hub 慢速镜像**：遇到「找到 PDF 但传得慢」的镜像时看是否多等并成功、单篇最坏多久（见 DECISIONS「Sci-Hub 慢速镜像多等一会儿」）。
11. [ ] **arXiv 任务行**：在 app 里粘贴一条 arXiv 编号，看下载任务的进度、来源、结果是否都正常显示（见 DECISIONS「arXiv 编号与 DOI 对不上时也能更新任务」）。
12. [ ] **深度检索完成通知**：app 开着、人在别的页面时，让 Agent 跑完一轮，看是否弹「深度检索有新的一批」、同一轮只弹一次（见 DECISIONS「深度检索一轮跑完发通知」）。

## 待办池

### 下载
- [x] Sci-Hub 落败镜像留副本：已修（DECISIONS「Sci-Hub 落败镜像自己删副本」）。用户文件夹里已有的 2 个旧文件没动，删不删由用户定。
- [x] 预印本被存成正式版：已修（DECISIONS「预印本另存，正式版优先」），待实测 8。
- [x] 车道里晚到落盘的文件没算进结果、冷却重试又下一遍：已修（DECISIONS「车道里晚到落盘的文件按成功收下」），待实测 9。
- [x] Sci-Hub 慢镜像被 45 秒窗口判失败：已修（DECISIONS「Sci-Hub 慢速镜像多等一会儿」），待实测 10。
- [x] arXiv 编号与下载任务项对不上：已修（DECISIONS「arXiv 编号与 DOI 对不上时也能更新任务」），待实测 11。
- [ ] 车道不必等整批走完：现在是 fast → 灰色 → 学校通道三段串行，前半批失败的文献要等整批 fast 跑完才进浏览器通道；改成按篇推进能缩短大批量时间，但会动浏览器争用和进度上报，等用户决定。

### 检索
- [ ] 阶段 2 剩余：速度（下次真实检索看 `rounds` 里的 `abstracts_wait_seconds`，再定要不要做 OpenAlex 按参考文献缓存）；深度检索 → 批量下载 → 按分类推送 Zotero 走通一次。
- [~] 类别多、彼此引用少的课题（评审 5）：建池时稀土测试集漏的 7 篇里 3 篇不在候选池；排序在这类综述上也弱（SYNERGY 的 Moran_2021 读 200 篇只找到 8%）。离线对比“每个子方向单写检索词”和“按类别配额出批”，看读到第 60 篇找到几篇目标。SYNERGY（`scripts/eval_synergy.py`）评不了建候选池和材料领域，这两项仍靠 `tests/acceptance/`。**按类别配额出批已排除**（2026-11-03，`scripts/replay_batching.py` 在 PDC 池上对比，现行 rank 全面更好，见 DECISIONS「离线回放分批策略」）；还剩“每个子方向单写检索词”，等稀土测试集跑完再看。
- [ ] 停止规则校准（评审 8）：每轮已记下规则和估计（`stop_checks`），`scripts/stop_calibration.py` 汇总；SYNERGY 上可离线试（`eval_synergy.py run --knee/--patience`），攒够数据再改。
- [~] 阶段 3：系统综述支持。已完成：`screening.prisma()` 的流程统计（命中/去重/纳入/排除/低相关未定 + 排除理由）、`GET /api/sessions/{id}/prisma`、历史记录展开时显示「检索流程」、`prisma_markdown()` 导出成 Markdown 表（「导出流程」按钮，2026-11-04）；排除时可选理由（「主题不相关」「不是研究文献」「材料或体系不符」「数据不可靠」「重复文献」或不写，2026-11-05，存在条目的 `reject_reason`，流程统计优先用它）。还差（2026-11-05 盘点评）：**中断后续跑**（深度检索停在哪、下次接着读）、**双人筛选与一致性**（两份决策的 Cohen's κ、分歧清单）、**检索式与复现存档**（每次检索的检索词、日期、数据源版本写进会话，导出时带上），这三项凑齐才算「PRISMA 式」。前一项是实现问题，后两项要先定验收标准再动手。
- [x] 阶段 4：定期追踪（2026-11-04～05）。`search/watches.py`（`~/.litloop/watches.json`，按 DOI/标题记已见文章）、`GET/POST /api/watches`、`POST /api/watches/{id}/check`（重跑检索式、只回没见过的）、`DELETE /api/watches/{id}`；历史页「追踪」卡片与每个会话展开区的「追踪这个检索式」按钮；`watches.check_due()` 与 `POST /api/watches/check-due`——app 打开时检查超过 `watch_check_days`（默认 7 天）的追踪并通知。2026-11-05 补上最后一块：检查后卡片里直接列出这次的新文献（标题、年份、期刊，前 20 条）并可以「加入下载」。还没实测（要真实检索额度）。**已知半闭环**：新文献只能直接进下载队列，不能并回原会话的筛选列表让人逐篇评分；自动检查只发生在打开 app 时。
- [ ] 中文文献（知网、万方、维普，以及各校学位论文库）：现在只有 OpenAlex/Crossref 等英文源的覆盖，国内学位论文和中文期刊完全不在候选池，中文课题只能靠英文源的少量覆盖。要先解决「从哪拿、能不能拿」（大多没有开放 API，或要机构订阅），再谈取舍与去重。
- [ ] 快速检索提速：扩大候选池后单组 5–30 秒，目标 15 秒以内（用户 2026-10-15：不急）。**没有任何缓存**：同一课题反复查会重复付网络与额度，先做「同一天同检索式的结果缓存」是最省事的一档。

### app
- [ ] 下次发版后删掉 `src/litloop/app_server.py`：模块整理时留下的唯一转发，给已装好、还在启动 `python -m litloop.app_server` 的桌面 app 用（15c08b2）；删时把 `scripts/import_smoke.py` 里的 `litloop.app_server` 和 LAYOUT.md 顶层那句一起去掉。
- [~] 后端 B：内置 Agent（设计见 CHARTER「可视化 app → 任务与后端」）：app 直接调模型 API，Anthropic 原生接口和 OpenAI 兼容接口（DeepSeek、通义千问、Kimi、智谱 GLM、OpenRouter、本地 Ollama / vLLM）；设置里按服务商、地址、Key、模型名配置，可按任务分模型，测试按钮检查工具调用；用测试集存档比较各模型的评分质量。2026-10-26 补记：原来只写在 CHARTER 的「顺序」里，TODO 漏了这一项。
  - 已完成（2026-11-04）：模型客户端 `agents/client.py`——两种协议一套返回（文本、工具调用、停止原因、用量），`test_model()` 让模型当场调一个 `ping` 工具来验工具调用；设置页新增「内置 Agent」一组（接口、地址、Key、模型名）与对应测试；配置项 `builtin_agent_*`。见 DECISIONS「内置 Agent 的模型客户端」。**还没实测**：真实 Key 点一次「测试」。
  - 已完成（2026-11-04）：任务循环 `agents/runner.py`（问模型 → 跑工具 → 再问，工具出错转成文字不中断）、工具清单 `agents/tools.py`（检索、深度检索三步、保存评分）、`agent_backend` 开关——选「内置 Agent」后 app 发起的检索由本进程调模型完成，进度、session、取消都接上了。见 DECISIONS「内置 Agent 接手 app 发起的检索」。**还没实测**：填一个真 Key 在 app 里跑一次快速查找与一次深度检索。
  - 缺口（2026-11-05 盘点，见 DECISIONS「内置 Agent 现在的样子」）：
    - [x] **提示词**（2026-11-06）：新增 `agents/system.py` 的系统提示词（四档相关度怎么判、拿不准往高评、快速/深度检索的步骤、不许下载与写 Zotero），`_run_builtin()` 传给 `run_task(system=…)`；`build_prompt(form, skill=False)` 给内置后端去掉「用 litloop 技能」这句——它读不到技能文档。见 DECISIONS「内置 Agent 拿到自己的规则」。
    - [x] **步数**（2026-11-06）：按批数算，深度检索 `min(4 + 2 × 批数, 24)`、快速查找仍是 8。
    - [ ] 真实 Key 实测一次快速查找、一次深度检索（DeepSeek 与本地 Ollama 各一次）。
    - [ ] 设置里按任务分模型、用测试集存档比较各模型的评分质量、补「先给检索词再由你确认」的确认点。
- [ ] 安装包与自动更新（评审 12；设计和验收见 CHARTER「可视化 app → 发布」，2026-10-26 用户定了安装范围、签名、分发和浏览器的做法，见 DECISIONS「作为标准 Windows 程序分发」）。模块整理做完后开始，大致步骤：
  - [x] 先摸底：核心依赖 **336 MB**、拆掉版面分析 271 MB（DECISIONS「发布摸底（1）」）；官方 embeddable Python 跑起 `litloop.desktop.server`，合计约 **358 MB**（DECISIONS「发布摸底（2）」）；NSIS 安装包实测 **113.38 MiB**（DECISIONS「第一个 Windows 安装包」）。还剩：用 Defender 开着的机器扫一遍（本机 Defender 是关的）、干净 Windows 验收；
  - [x] 安装包带嵌入式 Python 和核心依赖：`scripts/build_release.py` 装配运行时，Tauri 把它映射成安装目录里的 `python/`，`npm run tauri build` 出安装包；去掉版面分析后是 **99.59 MiB**（DECISIONS「第一个 Windows 安装包」「版面分析不进安装包」）。
  - [x] 按需组件：版面分析（`pymupdf-layout` + `onnxruntime` + `networkx`，约 65 MB）用 uv 装到 `~/.litloop/packages` 并加进 `sys.path`（2026-11-04，`src/litloop/optional.py`、设置页「可选组件」的「安装」按钮、`POST /api/components/{key}/install`、镜像配置 `package_index_url`；uv.exe 由 `scripts/build_release.py` 放进安装包；见 DECISIONS「版面分析改成按需安装」）。还没实测：在装了安装包的机器上点一次安装、看进度与失败提示；失败提示目前是 uv 输出末尾 15 行，没有百分比进度。
  - [~] **MSI 安装包**（2026-11-05 用户定）：`bundle.targets` 已加 `msi`，`bundle.windows.wix.language` 为 `["zh-CN", "en-US"]`；已在本机打出 `LitLoop_1.18.2_x64_zh-CN.msi`（134,520,105 字节 = 128.3 MiB，`ProductVersion 1.18.2`、`ALLUSERS=1` 按机器安装，带 `InstallDirDlg`/`BrowseDlg` 可选安装位置）。发版流程已改成两个包一起给、`SHA256SUMS.txt` 覆盖两者（DECISIONS「MSI 安装包也能打出来」）。**还差**：真机装一次看安装位置页、卸载是否问保留 `~/.litloop`；MSI 是 `ALLUSERS=1`，需要管理员权限，发版说明要写明。
  - [ ] **诊断包**：设置里「导出诊断包」（版本、系统信息、最近一次任务日志、脱敏后的设置；不含 PDF 与凭据），见 CHARTER「发布」。
  - NSIS `installMode: "both"`，图标、名称、开始菜单；系统通知显示 LitLoop 自己的名称和图标（用户 2026-10-23 要求；开发模式下借用的是 PowerShell 的）；
  - 按需下载都要有进度和失败提示；
  - 更新插件读 GitHub Releases，`passive` 模式；发版流程（打包、生成更新清单、上传）写成脚本；
  - 卸载时问是否保留 `~/.litloop`；README 写 SmartScreen 提示怎么继续；
  - 在干净的 Windows 上验收（另找一台电脑或虚拟机）。
- [x] 完成通知：一轮深度检索跑完（agent 或 app 发起）发一次系统通知，见 DECISIONS「深度检索一轮跑完发通知」。
- [~] 深度链接（2026-11-05）：`litloop://session/<会话 id>` 打开 app 并跳到该会话的筛选页。插件 `tauri-plugin-deep-link` 注册了 `litloop` 协议（安装时写注册表），前端用 `onOpenUrl` 解析。已经开着 app 时点链接会由 `tauri-plugin-single-instance` 转发给现有窗口（事件 `litloop-url`）。技能文档（`skills-src/shared/screening.md`，已重建）现在会提示 Agent 把 `litloop://session/<session>` 和表格一起给用户。**还差**：实测（装一次安装包、点链接）。

### 测试
- [~] 补测试空洞（评审 14）：已补 `schools`、`resolver`、`session_broker`、PDF 归属校验、`publisher_pdf_router`、`session_store`、`publisher_access`；2026-11-05 又补 `bib_import`、`bibtex_crossref`、`http_utils`、`europepmc`、`oa_discovery`、`models`、`pdf_extractor`、`profile_health`、`remote_assist`、`camofox_api`、`cloakbrowser_compat`、`cli/commands` 的辅助函数、`extractors` 的 HTML 路由（7 家出版商 + 兜底 + 大小写不敏感，11 个）与通用适配器 `publisher_adapters/generic.py`（标题/摘要/正文/图注/参考文献/作者的回退顺序，14 个）。顺手修了四个真 bug（citekey 撞键、`to_text` 多空行、PDF 段落分隔被拼掉、远程协助页面 500）。还剩 `desktop/` 的一些分支。

### 分析归纳
- [ ] 先定验收标准再动手（草案在「待用户决定」），做到「表格级」可用（材料体系 / 方法 / 关键数字 / 出处页码），不写综述正文。
- [ ] 从全文抽取要点、做对比表（现成的 `extractors.extract_figures` 可抽图注）；app 的分析归纳页。
  - 抽取性质数据考虑用 [ChemDataExtractor2](https://github.com/CambridgeMolecularEngineering/ChemDataExtractor2)（2026-10-25 用户提出；MIT，剑桥维护，能从材料与化学论文的正文和表格里抽化合物、性质数值、光谱）。它要求 Python ≤ 3.12、numpy < 2，依赖 transformers、stanza 等，几个 GB，和 LitLoop 的环境（Python 3.14、numpy 2）冲突：单独建一个环境，LitLoop 用子进程调用它（像 zotero-mcp 那样）。它的缩写识别没有用于术语表：600 篇材料摘要上实测不如 `abbreviations`（见 DECISIONS「术语表」）。

### 工程债（2026-11-05 盘点，用户认可，先记账不动手）
- [~] **三个巨型文件**：`src/litloop/publishers/strategies/_core.py`（2873 → **2340** 行，2026-11-05 抽出 `_elsevier_api.py`，见 DECISIONS）、`src/litloop/sources/__init__.py` 2112 行、`src/litloop/server.py` 1731 行。「不拆」是继承来的约定，但新逻辑仍在往里加（预印本兜底、车道晚到文件、Sci-Hub 慢速镜像全在 `sources/__init__.py`）。建议至少把竞速/兜底抽成 `download/race.py`，其余保持。
- [ ] **数据层换 SQLite**：现在是 JSON + 文件锁，候选池 1600–3400 篇是几 MB 的整份读整份写，会议话越攒越大越慢；`store.SCHEMA` 只有版本号、没有迁移机制。先做「会话/候选池/决策/追踪」四张表，再补迁移。
- [ ] **真实世界回归**：测试多但都打桩，下载链路（竞速、迟到落盘、慢镜像、预印本）只有真网络能验。做一组录制的真实响应（HTML + 状态码 + 小 PDF）在本地回放，再加一条「装好的 exe 起得来 + `/api/health` 通」的冒烟脚本。
- [ ] **筛选页批量与撤销**：30 篇只能逐篇点；缺「按 Agent 理由批量接受/排除」、多选、撤销、列表内按年份/期刊过滤与搜索。
- [ ] **错误提示补「下一步」**：文案已统一成「无法…：原因」，但下载失败后没给动作（去设置填学校 / 开代理 / 跳过这篇），只在日志里。
- [ ] **体积与冷启动**：安装包 101.8 MiB、启动要起 Python 子进程；可做后端懒加载与首屏预热。

### 不做
- 不重写下载引擎；不把排序换成大模型打分；没有测试集不调排序参数（2026-10-18 评审）。
- 不拆分 `server.py`、`_publisher_strategies_core.py` 两个大文件（继承自原项目，风险大、收益不直接）。2026-10-25 的模块整理只把后者原样挪到 `publishers/strategies/_core.py`，不拆。**例外（2026-11-05）**：把其中与浏览器无关的 ScienceDirect 文章 API 一段抽成了 `strategies/_elsevier_api.py`，`_core.py` 回导名字、调用处不变（2873 → 2340 行）；其余部分仍不拆。
- 不给 `publisher-batch` 补导入登录状态，整条路去掉（2026-10-19）。
- 不接回 FlareSolverr（要另跑服务；人机验证交给用户，2026-10-19）。

### 期刊分区插件
见 [litloop-journal-rank 的 TODO](https://github.com/dysprosium231/litloop-journal-rank/blob/main/TODO.md)（机构数据、公开发布前移除分区数据等）。

## 推广

对这类工具，曝光度等于被搜到：GitHub 搜索、PyPI、MCP 目录、小红书/知乎/公众号的搜索结果，以及别人写方法部分时的引用。按性价比排，先做 1–3（都不需要用户操作），再发内容。

**现在卡在用户这边的三件事**：①PyPI 的 API token（有了就能 `twine upload dist/*`，之后 `uvx litloop run` 才成立）；②Smithery / Glama / mcp.so / PulseMCP 的账号（提交目录用）；③录屏用的真机运行（90 秒 GIF，或由我起草分镜你来录）。其余我能自己往前推。

### 1. 能被搜到（先做，约半天）

- [x] 补仓库元信息：description 与 16 个 topics 已于 2026-11-05 用 `gh repo edit` 设好（「学术文献 Agent：检索 → 筛选 → 下载（校园网 / CARSI / WebVPN）→ Zotero 归档 | 21 MCP tools for Claude, Cursor and Codex」）。社交预览图还没做。
- [x] 英文 `README.md` + 中文 `README.zh-CN.md`，两份顶部互相链接（2026-11-05）。
- [x] 打第一个正式 Release（2026-11-05）：tag `v1.18.2` 跑通 `release.yml`，发布 [v1.18.2](https://github.com/dysprosium231/litloop-paper-agent/releases/tag/v1.18.2)，产物 `LitLoop_1.18.2_x64-setup.exe`（106,757,379 字节 = 101.8 MiB）与 `SHA256SUMS.txt`。第一次 tag `v1.18.1` 失败是 CI 用 3.13 的 pip 往 3.14 的嵌入式运行时装依赖（numpy cp313 轮子），改成先用 uv 按嵌入式解释器装后通过（见 DECISIONS）。后续发版只需打 tag。
- [~] 发上 PyPI（`pip install litloop`）：`pyproject.toml` 补了 keywords 与 11 条 classifiers；`python -m build` 出 sdist 818 KB + wheel 707 KB（170 个文件），`twine check` 都 PASSED，装进临时目录后 `import litloop` 与 `litloop.agents.TOOLS` 正常；`server.json` 已按官方注册表格式写好并纳入一致性检查。**只剩上传**：需要用户的 PyPI API token。
- [ ] 90 秒演示 GIF 放 README 顶部（一句话 → 30 篇 → 下载 → Zotero），替换现在 4 张静态截图的位置。

### 2. MCP 生态（当下最大的杠杆）

- [~] 写 `server.json` / `smithery.yaml`：`server.json` 已写（pypi、`uvx litloop run`、stdio）；`smithery.yaml` 等 PyPI 发布后再补，Smithery 要能拉到包才算数。
- [ ] 提交到 Smithery、Glama、mcp.so、PulseMCP、awesome-mcp-servers。定位一句话：「Search, screen, download, and file papers into Zotero — 21 tools」。需要各站账号，排到 PyPI 之后。

### 3. 中文社区种草（决定早期用户量）

- [ ] 小红书：「一句话查全一个课题」录屏 + 前后对比，标题打痛点（读不完 300 篇摘要）。
- [ ] B 站：5–8 分钟完整流程（检索 → 筛选 → 下载 → Zotero）。
- [ ] 知乎：回答「如何高效做文献调研/系统综述」并附工具对比。
- [ ] 少数派 / 科研公众号投稿：一篇「我用它做了什么课题」的实测文，要有数字（SiC/Al 150 篇找到 17/19 这类）。
- [ ] V2EX「分享创造」、即刻：一句话 + 截图。
- 素材原则：讲一个真实课题的结果，不讲功能列表；数字从 `docs/DECISIONS.md` 里挑三个做成图。

### 4. 先修掉会劝退新用户的硬门槛

- [ ] 安装包没签名，SmartScreen 会拦（README 已写怎么继续，但多数人到这里就走了）：评估买代码签名证书（个人开发者约 ¥1000+/年）。
- [x] 保证「不填任何 Key 也能跑一次快速检索」，引导里给 OpenAlex Key 申请的直达链接：2026-11-05 实测空数据目录、没有 Key 时 `search_papers()` 5.0 秒返回 5 条；初次引导的「检索接口」现在写明「什么都不填也能先试一次快速检索（退回 Crossref）」，并指向 help.openalex.org。
- [ ] 宣传里主动写清楚 Sci-Hub、LibGen 默认关闭、只用合法途径，避免被平台判违规限流。

### 5. 长期：可被引用

- [ ] 工具论文或预印本（ChemRxiv / Zenodo + DOI），别人写方法部分时会引；成本高，但比任何平台都持久。
