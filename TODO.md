# 进度与待办

> 这是项目唯一的进度表，只留正在做、接下来和待办池。每完成一步就更新，并和对应代码放在同一个提交里；做完的事、实验结论和用户做的决定写进 [docs/DECISIONS.md](docs/DECISIONS.md)，TODO 里只留没做完的。日期一律写绝对日期。
> 设计与验收标准见 [CHARTER.md](CHARTER.md)。2026-10-19 重构前的 TODO 原文归档在 DECISIONS 末尾。

## 正在做

**下载提速与实测**（2026-10-17 起）：下一步是「接下来」第 1 条，重测新文献。

## 接下来（按顺序）

1. [ ] **重测「下载测试 · 新文献（2024–2026，20 篇）」剩下的 17 篇**，按 `[timing]` 日志给出每段、每篇用时表，再定下一步优化。同时验收 2026-10-18～19 的这些改动：
   - 出版商官网先看页面再决定等不等（`_wait_for_pdf_link`；Springer 那篇原来白等约 80 秒）；
   - 选择机构页结果一出来就点（看日志里每个输入框的耗时）；
   - Sci-Hub 一轮不先等镜像探测；新文献按年份跳过 Sci-Hub；
   - 失败原因：真实遇到“学校没有订阅”“超时”“人机验证”时，app 里显示的原因和「尝试经过」对不对（评审 4，至今只用测试验证过）；
   - 跳过 / 取消立即有反馈，跳过后只关这一篇的浏览器。
2. [ ] **查开放获取一轮 110 秒花在哪**（查 OA 链接、Wiley 浏览器兜底），以及 about:blank 久等。用第 1 条的日志查。
3. [ ] **去掉 `publisher_batch` 这条路**（2026-10-19 用户同意，原因见 DECISIONS「publisher-batch 验收」）：
   - MCP 批量下载非车道模式的第二阶段（`sources._batch_institutional_phase`）改用 app 在用的下载引擎（学校通道、登录状态、失败原因都能用上）；
   - `litloop publisher-batch` 改为调用同一引擎或删除；
   - 一并删：`publisher_batch.py`、`institutional/instsci_bridge.py`、`institutional/sources/elsevier_api.py`、`institutional/publisher_pdf_router.py`、`institutional/publisher_profiles.py`（约 1200 行），删前照「删除没人用的代码」的做法核实；
   - 再查 `institutional/` 里剩下的与根目录同名模块（`session_broker.py`、`cloakbrowser_compat.py`、`config_adapter.py`、`fetcher.py`、`profile_health.py`）哪些还要；
   - 验收：全部测试，加 MCP 真实批量下载几篇（含一篇 Elsevier 订阅论文），要用户配合登录。
4. [ ] **大清单（>30 篇）分队列**：先摸底分组（开放获取 / Sci-Hub 有 / 需学校权限，后者按出版商细分），app 里分成几个队列，每个队列写明怎么下，用户逐个队列点下载。
5. [ ] **实测 app 里的其余功能**（要用户配合）：
   - app 里发起检索：已走通“发起 → 失败提示”，真实检索要用户先重新登录 `claude` 命令行；
   - Zotero 写入；摘要翻译。
6. [ ] **学校认证引导**：在 app 里选学校、弹窗登录、试下载一篇订阅论文。

## 待办池

### 下载
- [ ] 批量引擎（车道）：浏览器下载超过竞速时限后“迟到的成功”没算进车道结果，冷却重试又下了一遍（ceramint.2024.08.002）。
- [ ] 下载慢的几处：开始前用 Crossref / doi.org 验证 DOI（不存在的 DOI 跑完了所有车道）；车道不必等整批走完；Sci-Hub 镜像找到 PDF 但传输慢时被 45 秒窗口判失败；arXiv 进度用编号汇报、下载任务对不上 DOI。

### 检索
- [ ] 阶段 2 剩余：速度（每轮 30–55 秒主要花在补摘要上，先从这里下手：扩展与补摘要并行、OpenAlex 按参考文献缓存）；深度检索 → 批量下载 → 按分类推送 Zotero 走通一次。
- [ ] 类别多、彼此引用少的课题建池（评审 5）：稀土测试集漏的 7 篇里 3 篇不在候选池；离线对比“每个子方向单写检索词”和“按类别配额出批”，看读到第 60 篇找到几篇目标。
- [ ] 停止规则校准（评审 8）：每轮已记下规则和估计（`stop_checks`），`scripts/stop_calibration.py` 汇总，评测工具 `--past-stop` 可读过停止点；攒够十几个停止点再调 1/6 拐点和外推，现在不改。
- [ ] 术语表（2026-10-16 用户确定，设计见 CHARTER）：用户在 app 里增删缩写和同义词、设作用范围；Agent 可建议、用户确认；检索时自动展开并写明用到的展开。可先做配置文件 + MCP 动作，再做 app 页面。
- [ ] 阶段 3：系统综述支持（PRISMA 式统计、纳入排除理由，中断后可续跑）。
- [ ] 阶段 4：定期追踪（保存检索式，只推送新文章）。
- [ ] 中文文献（知网、万方）。
- [ ] 快速检索提速：扩大候选池后单组 5–30 秒，目标 15 秒以内（用户 2026-10-15：不急）。

### app
- [ ] 安装包与自动更新（评审 12）：`tauri.conf.json` 的 updater 为空；按 CHARTER 定的 PyInstaller sidecar + 安装包 + 自动更新做，不换方案。
- [ ] 深度链接、完成通知。
- [ ] 首次引导里“可选组件”一步（一直没做）：可用现成的 `deps.is_feature_available` / `warn_missing`、`embedded_tor.is_tor_installed`。
- [ ] 下载页“Agent 或命令行正在下载”加已用时间（现成的 `progress_reporter.elapsed_sec`）。

### 测试
- [ ] 补测试空洞（评审 14）：第一批（`schools`、`resolver`、`session_broker`、PDF 归属校验）已补；`publisher_access`、`publisher_pdf_router`、`session_store` 等其余模块待补。

### 分析归纳
- [ ] 从全文抽取要点、做对比表、起草综述（现成的 `extractors.extract_figures` 可抽图注）；app 的分析归纳页。

### 不做
- 不重写下载引擎；不把排序换成大模型打分；没有测试集不调排序参数（2026-10-18 评审）。
- 不拆分 `server.py`、`_publisher_strategies_core.py` 两个大文件（继承自原项目，风险大、收益不直接）。
- 不给 `publisher-batch` 补导入登录状态，整条路去掉（2026-10-19）。
- 不接回 FlareSolverr（要另跑服务；人机验证交给用户，2026-10-19）。

### 期刊分区插件
见 [litloop-journal-rank 的 TODO](https://github.com/dysprosium231/litloop-journal-rank/blob/main/TODO.md)（机构数据、公开发布前移除分区数据等）。
