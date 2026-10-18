# 进度与待办

> 这是项目唯一的进度表，只留正在做、接下来和待办池。每完成一步就更新，并和对应代码放在同一个提交里；做完的事、实验结论和用户做的决定移到 [docs/DECISIONS.md](docs/DECISIONS.md)。日期一律写绝对日期。
> 设计与验收标准见 [CHARTER.md](CHARTER.md)。

## 正在做

**当前：下载提速与实测**（2026-10-17 起，详见「接下来」，下次从「下次接着做」开始）。已完成的实验结论和决定见 [docs/DECISIONS.md](docs/DECISIONS.md)。

## 接下来

**下次接着做（2026-10-17 晚定）**
1. [ ] 重测「下载测试 · 新文献（2024–2026，20 篇）」剩下的 17 篇（app 已是最新代码：分段计时、2024 年起不查 Sci-Hub、学校通道看门狗、取消即停）；按 `[timing]` 日志给每段、每篇用时表，再定下一步优化。
2. [ ] 用计时日志查开放获取一轮 110 秒花在哪（查 OA 链接、Wiley 浏览器兜底），以及 about:blank 久等。
3. [x] T&F 书第一阶段跳过官网（2026-10-18 用户同意）：`publisher_site_skip_prefixes` 默认 `["10.1201", "10.4324"]`，这些 DOI 第一阶段不开出版商官网，直接由免费来源和学校通道处理。同日：官网浏览器打开的是 Chrome 自己的错误页（无法访问、超时）时当场放弃该网站（SSRN 在用户那里显示访问超时；直连和代理 curl 都在 1 秒内返回 403 的 Cloudflare 验证）。
   - 重测记录（10-17 晚、10-18 中午两轮，及据此做的修改）见 DECISIONS「2026-10-18：新文献下载重测」。
4. [ ] 之后：大清单分队列；app 里检索实测（用户需重新登录 `claude` 命令行）；Zotero 写入实测；翻译实测。

- [~] app 里发起检索（2026-10-17 用户定：app 把表单发给 Agent，就像对话一样）：「筛选」页右上「新建检索」，表单含问题、纳入标准、快速 / 深度与规模、年份、中科院分区 / JCR / 预警期刊、种子 DOI 与 Zotero 分类；`agent_runner.py` 用 `claude -p --output-format stream-json` 从标准输入发出，只允许 LitLoop 工具和技能；页面显示 Agent 当前在调用什么，结束后「查看结果」打开它保存的筛选会话；Agent 命令和参数在设置「Agent」里可改。已在浏览器里走通“发起 → 失败提示”（本机 `claude` 命令行登录已过期，提示用户登录）；真实检索待用户登录 `claude` 后测。

**还没做的（从 2026-10-17 的计划里留下）**
- [ ] 大清单（>30 篇）：先摸底分组（开放获取 / Sci-Hub 有 / 需学校权限，后者按出版商细分），app 里分成几个队列，每个队列写明怎么下，用户逐个队列点下载。
- [ ] 可视化 app 1 期继续：学校认证（选学校、弹窗登录、试下载一篇订阅论文）；之后深度链接、完成通知、安装包与自动更新（见待办池 12）。
- [ ] 阶段 2 剩余：速度（扩展与补摘要并行、OpenAlex 按参考文献缓存）；深度检索 → 批量下载 → 按分类推送 Zotero 走通一次。
- [ ] 类别多、彼此引用少的课题怎么照顾到每一类（见待办池 11）。

## 待办池

**代码评审改进（2026-10-18，DeepSeek 通读代码后的评审；用户同意按下面顺序穿插进行，主线下载实测不中断）**
已抽查属实：2（降级检索不分档）、7（实际只有一个检索源）、13（写死路径、User-Agent 四种版本）、1（两套 publisher_batch，3079 / 2460 行）。
1. [x] **常量漂移 + README 检索源表述**（2026-10-18 完成）（评审 13、7，2026-10-18 已派 Antigravity）：User-Agent 统一为 `network.user_agent()`，版本取 `__version__`；删掉 `main.py` 里写死的 `D:\Projects\active\scansci-find`；发布检查扫写死的用户路径和散落的 UA；README 的“三个数据源”改成实际（有 OpenAlex Key 只用 OpenAlex，没 Key 用 Crossref，Semantic Scholar 要环境变量）。
2. [x] **降级检索质量断层**（评审 2；2026-10-18 完成：整理步骤抽成 `search._tidy`，引擎缺失和引擎出错两条退路都经过它；`tests/test_search_fallback_tidy.py` 在旧代码上失败、新代码上通过）：没装发现引擎或引擎出错时 `search_papers` 只按 DOI 去重，不分相关档、不去离题标题、不按标题去重。把后处理抽成一个函数，两条路径都过；验收：固定 10 组查询离线比对有无引擎时前 10 条的档位分布。
3. [x] **下载状态显式化**（评审 3，最该提前；2026-10-18 用户同意设计，分四步：① 先写测试锁住四条规则；② 引入 `DownloadAttempt` 与登记表，旧全局变量改为从它读取；③ 取消、跳过、等用户、浏览器归属改用它，删旧全局变量；④ 每篇带 `attempts` 与 `error_text`，app 下载页失败行可展开。不在范围：放开一次一个任务。① 已完成：`tests/test_download_invariants.py`——跳过 5 秒内停下（含登录检查中）、结束后的晚到结果丢弃、取消不影响之后的下载、一篇的浏览器错误不带到另一篇；顺带修：开始下载前查元数据（联网，最长约 10 秒）期间被跳过的，查完即返回，不再进第一阶段。② 已完成：`sources/attempt.py` 的 `DownloadAttempt`（取消代号、等用户心跳、每个来源的步骤记录：路线、来源、秒数、结果、失败类型和原因）与登记表；浏览器登记时记下所属文献（`browser_backend.OWNER`），跳过、结束、第一阶段收尾、看门狗都只关这一篇自己的浏览器，去掉“只有一篇在下才关”的判断。③ 已完成：在途集合 `_INFLIGHT` 和心跳表 `_USER_WAIT` 删除，改读登记表里的 `DownloadAttempt`；`_cancelled()` 按这篇自己记下的取消代号判断。保留三样且各有其职：`_SETTLED`（下载返回之后，晚到的来源仍要知道这篇已结束）、`_SKIPPED`（还没开始就被跳过的文献，任务开始时清空）、`_CANCEL_GEN`（任务级的取消代号）。④ 已完成：每篇结果带 `attempts`（每个来源的路线、秒数、结果、失败原因）；下载任务每篇记 `attempts`（去掉不到 1 秒的接口查询）和最说明问题的失败原因（`error_text.pick_failure`：没有订阅 > 学校登录未完成 > 人机验证 > 打不开 > 超时 > 付费墙 > 没找到，不再是笼统的“所有来源都失败”）；app 下载页失败行下有「尝试经过」可展开。待用真实失败验收（学校没订、超时、人机验证各一））：`_CANCEL_GEN`、`_SKIPPED`、`_SETTLED`、`_USER_WAIT`、`_SOURCE_TLS` 是进程级 / 线程局部的隐式状态，`download_jobs` 因此只能一次一个任务。不重写引擎，收进 `DownloadAttempt`（doi、代、取消标志、浏览器归属），全局只留注册表；把“成功后不再开浏览器、晚到结果丢弃、取消后新任务不受影响”写成测试。昨晚两个 bug（取消后任务仍是 running；上一篇的错误跑到下一篇）都出在这里。下载实测告一段落后做。
4. [~] **失败原因让用户看得到**（评审 4；随 3 的第④步实现，待真实失败验收）：浏览器和学校通道的失败常只剩一行日志；每个来源为什么失败统一到 `error_text.py` 分类，写进下载任务的 `items[].error_text`，app 详情页说清“哪条路、为什么、下一步”。借下载实测的失败样本验收（无权限、超时、验证码各一）。和 3 一起做。
5. [x] **CI 加前端和 Rust 检查**（评审 10；2026-10-18：CI 新增 `app` 任务（Windows）：`npm ci`、`npm run build`（含 tsc）、`npm run lint:copy`、`cargo check`；本地 cargo check 通过。ruff 未加）：现在只跑 pytest；加 `npm ci && npx tsc --noEmit` 和 `cargo check`，可选 ruff（只查不格式化），先不上 mypy。
6. [x] **app 与后端类型对齐**（评审 9；2026-10-18：`Summary` 补 9 个字段；`tests/test_app_types.py` 用后端实际写出的深度检索摘要对比 `api.ts` 的字段，缺了就失败。重启后补跑全量测试 756 个通过，GitHub CI 的新 app 任务与 9 组 Python 测试均通过）：`app/src/lib/api.ts` 的 `Summary` 缺 `remaining_estimate`、`coverage`、`ranked_by`、`rounds`；从 Python 响应模型生成 JSON Schema → TS 类型，CI 比对，和 APP_DATA.md / `SCHEMA` 版本一起兜住。
7. [ ] **合并两套出版商下载模块**（评审 1）：`publisher_batch.py` 与 `institutional/publisher_batch.py`、两份 `publisher_profiles.py`，调用交叉；以根目录为唯一实现，把 `institutional/` 独有的学校登录判断、等待用户逻辑并进来，分两次提交（先合并行为 + 测试，再删文件）。不能直接删 `institutional/`，两边各有独有修复。验收：全部测试 + 真实单篇、批量各下一次，`test_checkout_sync` 不报漂移。
8. [ ] **排序增量计算**（评审 6）：`deep_search._score` / `_features` 每轮全池重算分词、拼写归一、TF-IDF，池子 1800–7400 篇时排序 30–55 秒；缓存进 pool state 只算增量。验收：回放耗时降一半、找到目标的曲线不变。
9. [x] **app→Agent 协议**（评审 11；2026-10-18：会话 ID 只从 `litloop_screening` / `litloop_deep_search` 的结果里取，先按 JSON 解析（按 tool_use_id 对上工具名）；`agent_idle_minutes`（15 分钟）没有输出就停止并记为失败；`_CANCELLED` 在结束时清理；内部字段不写进记录文件）：`agent_runner` 用正则从 `tool_result` 抓 session、依赖 stream-json 形状、无超时、`_CANCELLED` 不清理；改为 `litloop_screening(save)` 返回机器可读的 `session`，加无输出心跳超时，退出时清理。
10. [ ] **补测试空洞**（评审 14）：`schools`、`resolver`、`session_broker`、`publisher_access`、`publisher_pdf_router`、`session_store` 等约 20 个模块测试里没出现；先补四个纯函数级的：学校列表解析、标题→DOI、会话状态机、PDF 归属校验。
11. [ ] **类别多的课题建池**（评审 5）：稀土测试集漏的 7 篇里 3 篇不在候选池，是建池问题不是排序问题；离线对比“每个子方向单写检索词”和“按类别配额出批”，看读到第 60 篇找到几篇目标。
12. [ ] **安装包与自动更新**（评审 12）：`tauri.conf.json` 的 updater 为空；按 CHARTER 定的 PyInstaller sidecar + 安装包 + 自动更新做，不换方案。
13. [ ] **停止规则校准**（评审 8）：1/6 拐点和 `remaining_estimate` 未验证；先把每次“建议停止”时的估计和之后实际又找到的篇数记下来，攒够十几次再调，现在不改。
- [x] README 改版（2026-10-18 用户定：以桌面 app 为主角）：README 475 → 255 行，写为什么用它、一次完整的流程、app 四个页面、深度检索怎么查全（附验收结果）、下载怎么拿到全文（附 10-18 实测）、安装与启动；详细内容移到 `docs/AGENT.md`、`docs/DOWNLOAD.md`、`docs/CONFIG.md`；四张 2x 截图在 `assets/screenshots/`（筛选用稀土正式检索，设置截网络页，不含邮箱和 Key）。下载、Zotero 两张里的会话名是“下载测试”，用户下次正式下载后可重截。
14. [x] **TODO 分家**（评审 15；2026-10-18：TODO 338 → 69 行，已完成的实验结论、用户决定和「已完成」一节移到 `docs/DECISIONS.md`，按主题和日期排列；AGENTS.md 同步）：已完成的实验结论和用户决定移到 `docs/DECISIONS.md`（带日期），TODO 只留正在做 / 接下来 / 待办池。
- 评审建议不做的：不重写下载引擎；不为统一直接删 `institutional/`；不把排序换成大模型打分；没有测试集不调排序参数。


### 检索（分阶段路线见 CHARTER）
- [ ] 阶段 3：系统综述支持（PRISMA 式统计、纳入排除理由，中断后可续跑）。
- [ ] 阶段 4：定期追踪（保存检索式，只推送新文章）。
- [ ] 中文文献（知网、万方）。
- [ ] 快速检索提速：扩大候选池后单组 5–30 秒，目标 15 秒以内（用户 2026-10-15：不急）。

### 代码审查遗留（2026-10-15 外部审查意见，已逐条核实）
- [ ] **`institutional/` 与根目录两套重复模块**（publisher_batch、publisher_profiles、fetcher 等，都继承自原项目）：根目录版有测试覆盖，但 `sources/__init__.py:1494` 的浏览器兜底下载、`main.py`（fetch、publisher-batch、浏览器配置检查）和 `pipeline.py:582` 实际走 `institutional/`，另有 10 个测试引用它。先比对差异、确认哪套新，再统一，最后做真实下载验证。不能直接删。
  - 初步比对（2026-10-15）：`fetcher.py`、`profile_health.py` 是转发到根目录的薄壳；`http_utils.py`、`publisher_pdf_router.py`、`session_store.py` 两边相同；`carsi_client.py`、`config_adapter.py`、`instsci_bridge.py` 只在 `institutional/`。真正分叉的是 `publisher_batch.py`（根目录 136K 多了页面池、代理槽、Wiley 登录入口等；`institutional/` 107K 却独有 `_is_school_login_host`、`_user_waiting`，可能是学校登录的修复）和 `publisher_profiles.py`（`institutional/` 版多一段机构配方说明），以及 `models.py`、`publisher_access.py`、`session_broker.py`、`flaresolverr.py`、`cloakbrowser_compat.py` 的小差异。统一时要把两边独有的修复合并，不能简单二选一。
- 不做：拆分 `server.py`、`_publisher_strategies_core.py` 两个大文件（继承自原项目，风险大、收益不直接）。

### 可视化 app（2026-10-15 用户确定为后续开发方向，设计见 CHARTER）
- [ ] 术语表（2026-10-16 用户提议并确定，筛选页之后做；设计见 CHARTER）：用户在 app 里增删缩写和同义词，设作用范围；Agent 可建议、用户确认；检索时自动展开并写明用到的展开。可先做配置文件 + MCP 动作，再做 app 页面。
- [ ] 之后：~~下载队列与 Zotero 状态页~~（2026-10-17 已完成）；分析归纳页。

### 分析归纳
- [ ] 从全文抽取要点、做对比表、起草综述。

### 期刊分区插件
见 [litloop-journal-rank 的 TODO](https://github.com/dysprosium231/litloop-journal-rank/blob/main/TODO.md)（机构数据、公开发布前移除分区数据等）。
