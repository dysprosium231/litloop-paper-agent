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

- [x] 下载页「粘贴 DOI」（2026-10-19 用户要求：原来只能从筛选会话选中下载）：粘贴任意文本，认出 DOI、doi.org 和出版商链接里的 DOI、arXiv 编号，边输边显示认出几篇、几篇已下载过、几行认不出，命名后开始下载。见 DECISIONS。待重启 app 后实测。

**DeepSeek 验收（2026-10-19，复核 `d06654a..6ebc61d`；已逐条核实，按此顺序改）**
1. [x] Elsevier 异步 PDF 捕获按 2 元组解包 3 元组，CLI / 批量的学校通道必失败（合并带来）。
2. [x] “页面仍在卖这篇”误判（“Get access through your institution”、推荐文章的 Purchase PDF）；随“先看页面状态再决定”一起改，见 DECISIONS「出版商官网：先看页面说什么」。
3. [ ] 最终失败原因取最后一条通道，不按 `_FAILURE_ORDER` 选最说明问题的一条。
4. [ ] 同一篇重试时，上一轮迟到的通道把步骤和错误写进新的 `DownloadAttempt`（按 DOI 反查登记表）。
5. [ ] app 的大批量任务（>30 篇，走车道）拿不到失败原因和“尝试经过”（MCP 出口剥掉 `attempts` 时一起剥了）。
6. [x] `main.py publisher-batch` 读不存在的 `summary["results"]`，永远报 0 篇。
7. [ ] 还有三处开浏览器不带所属文献：`_batch_institutional_phase`、`pipeline._inst_fetch_chunk`、`pipeline._si_one`。
8. [ ] Sci-Hub 池线程复用一个浏览器，按篇的所属与共享冲突：跳过 A 可能关掉 B 正在用的浏览器。
9. [x] `paywall` 规则匹配到 Unpaywall（没填邮箱被说成需要订阅）；`download exception` 等内部原因补文案。
10. [x] 合并后学校登录窗口跟随 `browser_headless`，改为始终可见。
11. [x] `test_login_no_access` 会真的联网。
12. [ ] 回归测试补齐（随各项）；`institutional_download` 成功后不清“学校没有订阅”的记录。
13. [~] 小问题：重复的 `if not pdf_bytes` 块（已删）；`instsci_bridge.try_institutional` 已无调用方；`_SESSION_LOCKS` 不回收；下载成功后再点跳过会残留一次；`not_entitled_after_login` 只记事件、没写进失败原因。

- [x] 下载页“跳过这篇”“取消下载”点了立即显示“正在跳过…”“正在停止…”（2026-10-19 用户提出：原来要等几秒才有变化）。
- [x] 逐篇下载排队时如实说明（2026-10-19 用户发现：没查 Sci-Hub 的新文献也写“Sci-Hub 未找到”）：查过没找到写“Sci-Hub 未找到”，按年份跳过的写“发表较新，不查 Sci-Hub”。
- [ ] Springer 选择机构页搜学校约 30 秒（11:47:26 → 11:47:57），查能否在地址里直接带上学校。
- [ ] 后台 11:48:03 已在等用户登录，app 的提示 11:48:39 才出现，查延迟在哪。
- [ ] Sci-Hub 一轮开始前又探测一次镜像（15 秒），测速刚做过，查能否直接用测速结果。
- [ ] “粘贴 DOI”进来的文献没有年份，Sci-Hub 按年份跳过不起作用：解析时顺便查年份（Crossref / OpenAlex）。

**还没做的（从 2026-10-17 的计划里留下）**
- [ ] 批量引擎：浏览器下载超过竞速时限后“迟到的成功”没算进车道结果，冷却重试又下了一遍（ceramint.2024.08.002）。（2026-10-18 从 DECISIONS 移回）
- [ ] 下载慢：开始前用 Crossref / doi.org 验证 DOI（不存在的 DOI 跑完了所有车道）；车道不必等整批走完；Sci-Hub 镜像找到 PDF 但传输慢时被 45 秒窗口判失败；arXiv 进度用编号汇报、下载任务对不上 DOI。（2026-10-18 从 DECISIONS 移回）
- [ ] 大清单（>30 篇）：先摸底分组（开放获取 / Sci-Hub 有 / 需学校权限，后者按出版商细分），app 里分成几个队列，每个队列写明怎么下，用户逐个队列点下载。
- [ ] 可视化 app 1 期继续：学校认证（选学校、弹窗登录、试下载一篇订阅论文）；之后深度链接、完成通知、安装包与自动更新（见待办池 12）。
- [ ] 阶段 2 剩余：速度（每轮 30–55 秒主要花在补摘要上，先从这里下手：扩展与补摘要并行、OpenAlex 按参考文献缓存）；深度检索 → 批量下载 → 按分类推送 Zotero 走通一次。
- [ ] 类别多、彼此引用少的课题怎么照顾到每一类（见待办池 11）。

## 待办池

**代码评审改进（2026-10-18，DeepSeek 通读代码后的评审；用户同意按下面顺序穿插进行，主线下载实测不中断）**
已抽查属实：2（降级检索不分档）、7（实际只有一个检索源）、13（写死路径、User-Agent 四种版本）、1（两套 publisher_batch，3079 / 2460 行）。
1. [x] **常量漂移 + README 检索源表述**（评审 13、7）：User-Agent 只剩一处、发布检查查写死路径。详见 DECISIONS「代码评审改进（第一轮）」。
2. [x] **降级检索质量断层**（评审 2）：两条退路都经过 `search._tidy`。详见 DECISIONS「代码评审改进（第一轮）」。
3. [x] **下载状态显式化**（评审 3）：每篇一个 `DownloadAttempt`，浏览器归属到文献。详见 DECISIONS「代码评审改进（第一轮）」。
4. [~] **失败原因让用户看得到**（评审 4；随 3 的第④步实现，待真实失败验收）：浏览器和学校通道的失败常只剩一行日志；每个来源为什么失败统一到 `error_text.py` 分类，写进下载任务的 `items[].error_text`，app 详情页说清“哪条路、为什么、下一步”。借下载实测的失败样本验收（无权限、超时、验证码各一）。和 3 一起做。
5. [x] **CI 加前端和 Rust 检查**（评审 10）。详见 DECISIONS「代码评审改进（第一轮）」。
6. [x] **app 与后端类型对齐**（评审 9）。详见 DECISIONS「代码评审改进（第一轮）」。
7. [~] **合并两套出版商下载模块**（评审 1；2026-10-18 代码已合并（93d2245 + 下一提交，见 DECISIONS「合并两套出版商下载模块」），待真实单篇、批量各下一次验收）：`publisher_batch.py` 与 `institutional/publisher_batch.py`、两份 `publisher_profiles.py`，调用交叉；以根目录为唯一实现，把 `institutional/` 独有的学校登录判断、等待用户逻辑并进来，分两次提交（先合并行为 + 测试，再删文件）。不能直接删 `institutional/`，两边各有独有修复。验收：全部测试 + 真实单篇、批量各下一次，`test_checkout_sync` 不报漂移。
8. [x] **排序增量计算**（评审 6；2026-10-18 完成，结论见 DECISIONS「排序增量计算」：每轮 30–55 秒的大头是补摘要，排序本身只有几秒；检索词命中和分词改为按文章缓存）：`deep_search._score` / `_features` 每轮全池重算分词、拼写归一、TF-IDF，池子 1800–7400 篇时排序 30–55 秒；缓存进 pool state 只算增量。验收：回放耗时降一半、找到目标的曲线不变。
9. [x] **app→Agent 协议**（评审 11）：会话 ID 只认会话工具，空闲停止。详见 DECISIONS「代码评审改进（第一轮）」。
10. [~] **补测试空洞**（评审 14；2026-10-18 第一批完成（Antigravity，已审）：`test_schools`、`test_resolver`、`test_session_broker`、`test_pdf_ownership` 共 44 个纯函数测试，不联网、不读本机设置；其余模块待补）：`schools`、`resolver`、`session_broker`、`publisher_access`、`publisher_pdf_router`、`session_store` 等约 20 个模块测试里没出现；先补四个纯函数级的：学校列表解析、标题→DOI、会话状态机、PDF 归属校验。
11. [ ] **类别多的课题建池**（评审 5）：稀土测试集漏的 7 篇里 3 篇不在候选池，是建池问题不是排序问题；离线对比“每个子方向单写检索词”和“按类别配额出批”，看读到第 60 篇找到几篇目标。
12. [ ] **安装包与自动更新**（评审 12）：`tauri.conf.json` 的 updater 为空；按 CHARTER 定的 PyInstaller sidecar + 安装包 + 自动更新做，不换方案。
13. [~] **停止规则校准**（评审 8；2026-10-18 开始攒数据：每轮记下规则和估计（`stop_checks`），`scripts/stop_calibration.py` 汇总，评测工具加 `--past-stop` 读过停止点；现有 2 个停止点，见 DECISIONS「停止规则校准」）：1/6 拐点和 `remaining_estimate` 未验证；先把每次“建议停止”时的估计和之后实际又找到的篇数记下来，攒够十几次再调，现在不改。
- [x] README 改版（2026-10-18 用户定：以桌面 app 为主角）：README 475 → 255 行，写为什么用它、一次完整的流程、app 四个页面、深度检索怎么查全（附验收结果）、下载怎么拿到全文（附 10-18 实测）、安装与启动；详细内容移到 `docs/AGENT.md`、`docs/DOWNLOAD.md`、`docs/CONFIG.md`；四张 2x 截图在 `assets/screenshots/`（筛选用稀土正式检索，设置截网络页，不含邮箱和 Key）。下载、Zotero 两张里的会话名是“下载测试”，用户下次正式下载后可重截。
14. [x] **TODO 分家**（评审 15；2026-10-18：TODO 338 → 69 行，已完成的实验结论、用户决定和「已完成」一节移到 `docs/DECISIONS.md`，按主题和日期排列；AGENTS.md 同步）：已完成的实验结论和用户决定移到 `docs/DECISIONS.md`（带日期），TODO 只留正在做 / 接下来 / 待办池。
- 评审建议不做的：不重写下载引擎；不为统一直接删 `institutional/`；不把排序换成大模型打分；没有测试集不调排序参数。

**评审修改的复核（2026-10-18，DeepSeek Harness 复核 `af88c10..3366aaa`；已逐条核实，按此顺序改）**
1. [x] **常见失败显示“出现意外错误”**（2026-10-18 完成，见 DECISIONS「复核 1–3」）：`no PDF found`、Cloudflare、`no PDF link on page` 不在 `error_text._RULES` 里（已复现，三条都翻成“出现意外错误”）；浏览器通道失败返回 None，`attempts` 里没有原因。改：补规则和 `_FAILURE_ORDER`；`_try_source` 结束时把本线程的 `get_last_error()` 写进这一步；未知原因显示“下载失败”并保留原文。
2. [x] **一篇的错误成了另一篇的失败原因**（2026-10-18 完成）：下载线程自己没跑过浏览器策略，`get_last_error()` 回退到进程级 `_LAST_ANY_ERROR`（已复现），影响最终原因和下一步建议。改：每篇最后一次通道错误存进 `DownloadAttempt`，下载线程从那里读；补两篇并发的测试。
3. [x] **“学校没有订阅”判得太宽、只管一次**（2026-10-18 完成）：官网机构登录走完后，任何失败（没找到链接、下载截断）都记进 `LOGIN_NO_ACCESS`；用一次就 `discard`，成功也不清。改：只有明确无权限（付费墙、NOT_ENTITLED）才记；成功时清；保留到成功或加期限。
4. [x] **线程池里开的浏览器没有所属文献**（2026-10-18 完成：`browser_backend.carry_owner`，Sci-Hub 提交任务经 `scihub._submit`；启动守卫也认继承的所属文献）：Sci-Hub 镜像竞速在 `_RACE_POOL` 里开浏览器，`OWNER()` 为 None，「跳过这篇」关不掉、结束时也不清。改：提交任务时带上 owner（contextvars）；补“池线程开 → 跳过能关”的测试。
5. [x] **登记表清理**（2026-10-18 完成：`_SETTLED` 只留最近 2000 篇；跳过一次就用掉，重试照常下载；登记的浏览器里已死的在下次登记时清掉）：`_SETTLED` 只增不减（字符串，量小但无上限）；`_SKIPPED` 只有 app 任务会清；`_LAUNCHED` 里 owner 为 None 的条目清不掉（同 4）。
6. [x] **排序缓存的内存**（2026-10-18 完成：分词缓存只留当前会话，命中数留 4 个会话；同一会话的 `next` 加锁）：实测 7309 篇约 37 MB（评审估 100 MB 偏高），5 万篇约 270 MB，最多留 4 个会话。改：分词缓存只留最近 1 个会话或按篇数设上限，命中数照旧缓存；同一会话的 `next` 加会话级锁。
7. [x] **不变量测试偏弱**（2026-10-18 完成：晚到结果走完整 `download()`；取消后、被取消的还在收尾时新开的一篇照常下载（取消按任务算，在途的都属于被取消的任务）；池线程复用；看门狗断言只关本篇）：晚到结果没走 `download()`；取消测试没有并发的另一篇；浏览器错误没测全局回退；看门狗测试没断言 owner。各补一条。
8. [x] **失败原因测试**（2026-10-18，随 1 完成）：用 `fail(...)` 调用点的真实字符串做参数化测试（随 1 一起）。
9. [x] **类型对齐测试只查 `Summary`**（2026-10-18，Antigravity，已审：加 `DownloadItem`、`DownloadStep`、`SessionRecord`，`SessionRecord` 补 4 个字段）：加 `DownloadItem`、`DownloadStep`、`SessionRecord`；正则不依赖两空格缩进。
10. [x] **源码卫生检查没进 CI**（2026-10-18，Antigravity，已审：规则在 `_source_hygiene.py`，脚本和测试共用；CI 跑发布检查）：CI 的 test 任务加 `check_release_consistency.py`；UA、写死路径的规则抽成一处，脚本和测试共用，给路径规则补测试。
11. [x] **TODO 分家不彻底**（2026-10-18 完成：3 条没做的移回 TODO；第一轮评审已完成的 6 项在 TODO 里各留一句，详细移到 DECISIONS）：DECISIONS 里还有 3 条没做的（批量引擎迟到的成功、下载前验证 DOI 等、学校认证实测）要移回 TODO；评审 1/2/3/5/9/10/15 补 DECISIONS 记录，TODO 里压成一句加链接（第 3 项一行 1186 字）。
12. [x] **停止规则记录**（2026-10-18 完成：`_stop` 返回 `stop_rule`；回放的估计偏大，汇总时只比记录下来的；“之后找到”不为负）：`_stop` 直接返回 `rule`，不靠中文文案反推；回放用的是最终候选池，估计偏大，输出里注明；“之后找到”在用户改评分时会失真，注明下限。
13. [x] **MCP 批量结果带 `attempts`**（2026-10-18 完成：`server._without_attempts`，单篇下载照旧带）：30 篇约多 20 KB，Agent 用不上；MCP 返回前去掉，app 的任务文件保留。
14. [x] **app→Agent 边角**（2026-10-18，Antigravity，已审）：`tool_use_id` 对不上时退回“最近一次 tool_use 是会话工具”；`agent_idle_minutes` 设 0 被当成 15，定为 0 = 关闭并写进文档。


### 检索（分阶段路线见 CHARTER）
- [ ] 阶段 3：系统综述支持（PRISMA 式统计、纳入排除理由，中断后可续跑）。
- [ ] 阶段 4：定期追踪（保存检索式，只推送新文章）。
- [ ] 中文文献（知网、万方）。
- [ ] 快速检索提速：扩大候选池后单组 5–30 秒，目标 15 秒以内（用户 2026-10-15：不急）。

### 代码审查遗留（2026-10-15 外部审查意见，已逐条核实）
- [~] **`institutional/` 与根目录两套重复模块**（2026-10-18：publisher_batch、publisher_profiles 已合并为根目录一份；`models.py`、`publisher_access.py`、`session_broker.py`、`flaresolverr.py`、`cloakbrowser_compat.py` 的小差异待查）（publisher_batch、publisher_profiles、fetcher 等，都继承自原项目）：根目录版有测试覆盖，但 `sources/__init__.py:1494` 的浏览器兜底下载、`main.py`（fetch、publisher-batch、浏览器配置检查）和 `pipeline.py:582` 实际走 `institutional/`，另有 10 个测试引用它。先比对差异、确认哪套新，再统一，最后做真实下载验证。不能直接删。
  - 初步比对（2026-10-15）：`fetcher.py`、`profile_health.py` 是转发到根目录的薄壳；`http_utils.py`、`publisher_pdf_router.py`、`session_store.py` 两边相同；`carsi_client.py`、`config_adapter.py`、`instsci_bridge.py` 只在 `institutional/`。真正分叉的是 `publisher_batch.py`（根目录 136K 多了页面池、代理槽、Wiley 登录入口等；`institutional/` 107K 却独有 `_is_school_login_host`、`_user_waiting`，可能是学校登录的修复）和 `publisher_profiles.py`（`institutional/` 版多一段机构配方说明），以及 `models.py`、`publisher_access.py`、`session_broker.py`、`flaresolverr.py`、`cloakbrowser_compat.py` 的小差异。统一时要把两边独有的修复合并，不能简单二选一。
- 不做：拆分 `server.py`、`_publisher_strategies_core.py` 两个大文件（继承自原项目，风险大、收益不直接）。

### 可视化 app（2026-10-15 用户确定为后续开发方向，设计见 CHARTER）
- [ ] 术语表（2026-10-16 用户提议并确定，筛选页之后做；设计见 CHARTER）：用户在 app 里增删缩写和同义词，设作用范围；Agent 可建议、用户确认；检索时自动展开并写明用到的展开。可先做配置文件 + MCP 动作，再做 app 页面。
- [ ] 之后：~~下载队列与 Zotero 状态页~~（2026-10-17 已完成）；分析归纳页。

### 分析归纳
- [ ] 从全文抽取要点、做对比表、起草综述。

### 期刊分区插件
见 [litloop-journal-rank 的 TODO](https://github.com/dysprosium231/litloop-journal-rank/blob/main/TODO.md)（机构数据、公开发布前移除分区数据等）。
