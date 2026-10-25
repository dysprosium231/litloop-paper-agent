# 决定与实验记录

> 已完成的工作、实验结论和用户做的决定，按主题和日期归档；从 [TODO.md](../TODO.md) 移来（2026-10-18 起）。
> 进度和待办仍记在 TODO.md；设计与验收标准见 [CHARTER.md](../CHARTER.md)。

## 2026-10-25：术语表

- **用户的决定**：
  - 不设作用范围，全局生效。用户先选了“按语境词”，后来觉得录入时还要想语境词不方便；又担心同一学科里也有同缩写不同义的情况。
  - 查了别人的做法之后，改为现在的方案：
    - PubMed 的自动词条映射靠受控词表，并在 Search Details 里写明展开了什么；
    - 系统综述的检索把同义词和缩写在概念块里用 OR 连起来，块与块之间用 AND，歧义靠其他概念消解；
    - Schwartz–Hearst 算法从摘要里抽“全称 (缩写)”，准确率约 96%、召回约 82%，不需要训练；
    - Allie、PETER 用它从全部 MEDLINE 摘要建缩写词典；
    - PLOD（LREC 2022）是给神经网络模型用的训练集，用不上。
- **不自己实现算法**，用现成的 `abbreviations` 包（MIT，只依赖 `regex`，2019 年后没再更新，但算法没变）。和 ChemDataExtractor2 的缩写模块在 600 篇材料摘要上对比（它的化学分词器装不上，用简单分词代替）：
  - `abbreviations` 找到 663 对，ChemDataExtractor2 基础版 595 对、化学版 602 对，两边一致的 508 对。
  - 只有 `abbreviations` 找到的 155 对几乎都对，约 5 条错，其中最多的是两字母缩写：RL、TC、EM、MD、AM、YS。ChemDataExtractor2 的化学版把缩写最短长度设成 3，认不出这些。
  - 只有 ChemDataExtractor2 找到的约 90 对里，约 25 条全称截错了。
  - ChemDataExtractor2 留给以后的全文分析（见 TODO「分析归纳」）。
- **做法**（`glossary.py`，文件格式见 APP_DATA）：
  - **检索词**：含术语的检索词另搜一遍换成其他写法的版本，每条最多 3 个。检索式里只剩这个缩写、没有别的概念词时不展开，记为 `alone`。计算“完全匹配几条检索词”时，匹配了换写版本也算匹配了原检索词。
  - **核心词**：含术语的那个词加上其他写法，多词写法加引号，比如 `(PDC OR "polymer-derived ceramic") AND aerogel`。核心词只有一个词时不展开。
  - **报告**：用到的展开写进返回值和概况文件的 `glossary` 字段。
  - **自动建议**：每轮从种子和评为高、中的文献摘要里抽缩写对，在 2 篇以上出现的作为建议条目，在返回值的 `glossary_suggestions` 里列出。
    - 合并前先把全称归一化：统一大小写、连字符、末词单复数、英美拼写，并按词干把 microscopy 和 microscope 这类写法并为一种。缩写也去掉复数 s，PDCs 和 PDC 算一个。
    - SEM、TEM、XRD 这类表征手段的缩写不建议。
    - 同一个缩写有几种全称、且每种都出现在 2 篇以上的，分别建议，让用户留下对的那条。
    - 用户删掉的建议记下来，不再建议。
  - **MCP 工具** `litloop_glossary`：列出、添加、确认、修改、删除；Agent 添加的条目要等用户确认才生效。工具总数变为 21。
- **实测**：从 PDC 气凝胶检索候选池里随机取 300 篇摘要，得到 37 条建议，比如 SiOC = silicon oxycarbide（13 篇）、PDC = polymer-derived ceramics（9 篇）、PCS = polycarbosilane、PIP = precursor infiltration and pyrolysis、CVI = chemical vapor infiltration。
- **还没做**：快速检索按术语表展开（已做，见「快速检索按术语表展开」）。

## 2026-10-25：术语表页面

- 入口在筛选页顶栏，挨着「筛选历史」；页面分「待确认」「使用中」「添加术语」三块。
  - 待确认的术语可以确认、修改、忽略；忽略的不再建议，所以不再弹确认。
  - 使用中的术语可以修改、删除；删除前在行内问一次。
  - 每种写法一个输入框，至少两种，可以加写法和备注。
- 后台接口：`GET /api/glossary`、`POST /api/glossary`（添加）、`POST /api/glossary/{id}`（修改、确认）、`POST /api/glossary/{id}/delete`。删除用 POST，因为 app 后台只放行 GET 和 POST。
- 试用时发现：同一条里加 `chemical vapour infiltration` 会被当成 `vapor` 的重复去掉，可检索时又按字面匹配，查询里写英式拼写或复数（`PDCs`、`polymer-derived ceramics`）就认不出。改为匹配时也认英美拼写、末词复数，和去重用同一个标准。
- 界面用词写进 UI_COPY 的术语表：术语表、术语、写法、待确认、使用中。

## 2026-10-25：术语表集成内置的英美拼写和元素表

- **用户的要求**：把术语表和英美拼写、化学元素集成到一处。原来没有单独的化学术语表：元素符号和名称靠 Agent 每次在核心词里手写 `Al|aluminum`，撞普通单词的符号只有一条提醒。
- **用户的选择**：新建内置元素表；页面里内置部分只读、可搜索、可单条停用。用户还指出英美拼写是比元素更广的一张通用表，两者都放进「内置」。
- **做法**：
  - 元素表：118 个元素的符号和美式名称，取自 PubChem 的元素周期表（`scripts/build_element_table.py` 生成 `data/elements.tsv`，NOTICE 注明来源）。英式名称（aluminium、caesium、sulphur）由拼写表负责，不重复存。
  - 检索时用的是用户在用的术语加上没停用的元素（`glossary.in_use`），走同一套展开和报告；元素的展开在报告里带 `builtin: "element"`。
  - 元素符号按大小写匹配。单字母符号（C、I、W）和与常用词同形的符号（In、La、As 等 11 个）在检索词里不触发展开，也不会被加进检索；核心词写了 La 仍会补上 lanthanum。元素名在检索词里也不触发（lead 是常用动词），只在核心词里补符号。
  - 核心词里补上的单词也按英美拼写展开：`SiC aluminum` → `SiC AND (aluminum OR aluminium OR Al)`。
  - 停用记在 `glossary.json` 的 `disabled`：`element:Al`，或 `spelling:aluminum`（这个词不再按英美拼写展开和统一，`spelling.py` 在文件改动后重读）。
  - 自动建议跳过元素表已有的“名称 (符号)”。
  - app：术语表页面分「我的术语」「内置」两栏；「内置」可搜元素和单词（搜 aluminium 也能找到 Al），每条可停用、启用。接口 `GET/POST /api/glossary/builtin`。
  - 技能文档：元素符号和名称不用 Agent 再写；MCP 工具 `list` 返回 `builtin_disabled`。

## 2026-10-25：快速检索按术语表展开

- **用户的要求**：快速检索也接上术语表。
- **做法**：
  - 快速检索（MCP `litloop_search`、命令行、app 新建检索经 Agent）都经过 `search._collect`，在这里接入：用 `glossary.query_variants` 得到最多 3 种其他写法，和原检索词并行各搜一次（每次照常走发现引擎或内置三源检索），结果轮流取（原词第 1 条、写法一第 1 条……），按 DOI 或标题去重。这样只写全称的文献也能进第一页。
  - 按匹配程度分档时，命中任何一种写法都算命中，用全称的文献不会因为没写缩写排到后面。
  - 返回里新增 `glossary`，格式和深度检索一样。
  - 只有缩写、没有别的概念词时照旧不展开。
- **检索词也按英美拼写另搜一次**（深度检索和快速检索都是）：之前只有深度检索的核心词会展开拼写，检索词按原样搜，可 OpenAlex 不会合并两种拼写。现在整条检索词换成另一种拼写（英式换美式，美式换成第一个英式写法）作为第一种写法，报告里带 `builtin: "spelling"`。拼写是同一个词，所以检索词只有这个词时也展开。缩写（PDC、SiC）不动；用户停用的拼写不换。
- **代价**：有展开时快速检索多搜 1–3 次，并行进行；深度检索的关键词阶段同样多几次 OpenAlex 请求。

## 2026-10-25：文本归一化合成一份（`text.py`）

- **起因**：用户指出类似功能的代码散落各处。查下来，同样的事有好几份写法：
  - 3 张停用词表（glossary、search、deep_search）；
  - 2 种截词（前 7 个字母、去掉末 3 个字母但至少留 6 个）；
  - 2 种复数规则；
  - 5 套连字符集合，其中 glossary 那份里有一个乱码字符（U+FFFD）；
  - 3 处“是不是同一篇文献”的键；
  - 用 `^[A-Z][a-z]?$` 猜元素符号，Ab-Cd 也会被当成化学体系。
- **做法**：
  - 新增 `text.py`，统一提供：连字符（`DASHES`、`ascii_dashes`、`plain`、`squash`）、单复数和截词（`singular`、`stem`）、停用词（`STOPWORDS`，三张表合并）、元素表（`elements`、`element_symbols`、`is_chemical_system`）、文献键（`title_key`、`paper_key`）。
  - glossary、search、deep_search、screening、identifiers、paperlist 都改为调用它。
- **行为变化**：
  - 快速检索分档的截词从“去掉末 3 个字母”改为“前 7 个字母”。
  - 复数规则统一为：ies→y，排除 ss/us/is 结尾。
  - 排序模型的词特征多去掉 effect、analysis、review、study、based、using 等停用词。
  - 化学体系只认真正的元素符号。
  - 术语匹配接受各种连字符。
- **防止再写重复**：AGENTS.md 新增「代码放在哪」；源码规范检查会报出 `text.py` 之外另写的停用词表、连字符集合、标题键和元素猜测（规则在重构前的代码上能找出全部 15 处，重构后为 0）。
- **评测**：SYNERGY 10 个综述各 3 轮，比较改动前后的召回率和 WSS@95。结果待补。

## 2026-10-24：SYNERGY 离线评测（第二批，共 10 个综述）

- 新增 8 个中等规模的综述：医学 3 个、心理学 1 个、生物 1 个、计算机科学 3 个。每个综述换 5 组种子各跑一次，每次最多读 600 篇。“连续 1、2、3 批都满足停止条件才停”这三种规则在同一次模拟里一并记下（1 批就是现行规则）。
- 下载改走 curl：数据文件存放在荷兰的 objectstore.surf.nl。v2rayN 按进程名让 python.exe 直连（学校认证需要这条规则），实测直连约慢 8 倍，看上去像卡住；curl 走代理，8 个综述 3 分钟下完。
- 结果（5 次平均。“停在 / 找到”指停下时读了多少篇、找到了几成目标）：

  | 综述 | 篇数 / 纳入 | 读到第几篇找到 95% | WSS@95 | 读 200 篇时找到 | 连续 1 批才停 | 连续 2 批 | 连续 3 批 |
  |---|---|---|---|---|---|---|---|
  | Leenaars_2019 | 5812 / 17 | 155 | 0.92 | 99% | 102 / 87% | 132 / 93% | 162 / 96% |
  | Wassenaar_2017 | 7668 / 111 | 600 篇内未到 | — | 67% | 222 / 68% | 306 / 75% | 354 / 78% |
  | Appenzeller-Herzog_2019 | 2873 / 26 | 600 篇内未到 | — | 79% | 144 / 72% | 174 / 76% | 204 / 79% |
  | Bos_2018 | 4878 / 10 | 451（5 次中 1 次到） | — | 55% | 60 / 15% | 90 / 20% | 120 / 33% |
  | Wolters_2018 | 4280 / 19 | 600 篇内未到 | — | 65% | 84 / 29% | 126 / 44% | 210 / 65% |
  | van_de_Schoot_2018 | 4544 / 38 | 224 | 0.90 | 92% | 174 / 83% | 246 / 95% | 276 / 96% |
  | Moran_2021 | 5214 / 111 | 600 篇内未到 | — | 8% | 72 / 5% | 126 / 7% | 156 / 7% |
  | Hall_2012 | 8793 / 104 | 296 | 0.92 | 89% | 234 / 92% | 264 / 93% | 312 / 95% |
  | Radjenovic_2013 | 5935 / 48 | 364 | 0.89 | 87% | 168 / 81% | 228 / 87% | 258 / 90% |
  | Smid_2020 | 2627 / 27 | 380 | 0.80 | 81% | 138 / 70% | 186 / 79% | 216 / 82% |

- **排序**：5 个综述表现好，WSS@95 在 0.80 到 0.92 之间；另 5 个弱。数据本身没问题，纳入文献 84%–100% 有摘要和参考文献。最差的 Moran_2021 研究营养状况与动物冒险行为，纳入文献横跨很多物种和行为，用词各异、互相引用也少，和稀土测试集“类别多、彼此引用少”是同一类问题（TODO「类别多、彼此引用少的课题建池」）。模拟没有检索词，第一批只按与种子的共同引用排；真实使用时有检索词，前几批会好一些，所以这些数字偏保守。
- **停止规则**：现行规则（一批读不到就停）在 10 个综述里有 9 个停下时找到的不到九成。改成“连续 2 批”，10 个综述全都更好或持平，找到的比例平均多 6 个百分点，代价是平均多读约 50 篇；“连续 3 批”再多 5 个百分点左右，平均又多读约 40 篇。排序弱的综述，换哪种停止规则都救不回来。
- **待用户决定**：是否把正式的停止规则改成“连续 2 批”。模拟里没有“中”这一档评分，“高和中太少”那条规则在真实使用中会触发得少一些。

## 2026-10-24：SYNERGY 离线评测（第一批 2 个综述）

- 工具 `scripts/eval_synergy.py`：
  - `export` 从 DataverseNL 下载某个综述的 `labels.csv` 和 `works_*.zip`，转成 JSONL，存到 `~/.litloop/eval/synergy/`。不用官方的 `synergy-dataset` 包：它一建数据集就要联网，下载也卡住过。
  - `run` 离线模拟深度检索：候选池就是综述的全部记录；随机挑 2 篇纳入的文献当种子；每批按标注给分（纳入记“高”，其余记“低”）；排序和停止用 LitLoop 自己的函数；评为高的文献并入种子。每个综述换 5 组种子各跑一次，每次最多读 1000 篇，报告 recall@N、WSS@95，以及停止规则在哪里触发、触发时找到了几成。
  - 不用检索词（SYNERGY 没有），所以第一批只按与种子的共同引用排序。不联网，不花额度。
- 结果（5 次平均，括号内为范围）：

  | 综述 | 篇数 / 纳入 | 读到第几篇找到 95% | WSS@95 | 停在第几篇 | 那时找到 |
  |---|---|---|---|---|---|
  | Leenaars_2019 | 5812 / 17 | 155（113–213） | 0.92 | 102（90–120） | 87% |
  | Wassenaar_2017 | 7668 / 111 | 读满 1000 篇未到（recall@1000 为 0.92） | — | 222（180–300） | 68% |

- **停止规则停得早。** Wassenaar 5 次全是拐点规则触发的。把 1/6 放宽到 1/10、1/15，停止位置几乎不变，因为停下的那一批纳入数是 0 到 2 篇，取什么比例都会触发。问题出在“只要有一批读不到就停”。只在模拟里试了“连续 N 批都该停才停”：
  - 连续 2 批：Leenaars 停在第 132 篇，找到 93%；Wassenaar 停在第 306 篇，找到 75%。
  - 连续 3 批：Leenaars 停在第 162 篇，找到 96%；Wassenaar 停在第 354 篇，找到 78%。
- 正式的停止规则**暂不改**：只看了 2 个综述；模拟里没有“中”这一档（“高和中太少”那条规则因此更容易触发）；按 TODO，要攒够数据再调。下一步多跑几个综述。

## 2026-10-23：外部代码审查（用户转来的一份审查意见）

审查在 Linux / Python 3.13 上跑了全量测试，列出 9 条意见。第 1～7 条逐条核实，都属实；第 8、9 条是判断。

1. **只装基础依赖时下载导入失败**：`fetcher` 在顶部导入 `auth`，`auth` 又在顶部 `from Crypto.Cipher import AES`，而 pycryptodome 只在可选依赖 `vpnsci` 里；CI 一直装着 `[dev,vpnsci,fast]`，没暴露。改：pycryptodome 进基础依赖（`vpnsci`、`instsci` 保留为空壳，旧安装命令照常可用）；CI 加 `base-install` 任务，只装基础依赖后运行 `scripts/import_smoke.py`，导入下载要经过的 13 个模块。本机模拟没装 pycryptodome 时，它报出 `fetcher`、`auth` 两个模块。
2. **`setup.py` 的“加密保护”不起作用**：AES 密钥写在公开的 `setup.py` 里，`webvpn.json` 明文早已进了仓库；`schools.py` 解密时导入 `instsci_core`，编译的却是 `vpnsci_core`，名字对不上；三个 Cython 模块的 `.pyx` 不在仓库（`_core/.gitignore` 写着上游把它们当私有代码），这个分支从没构建过，所有 `_HAS_COMPILED_CORE` 分支都是死代码。改：删 `setup.py` 和 `_core/`，`sources/__init__.py`、`sources/scihub.py`、`sources/instsci.py` 只留纯 Python 那一支，`schools.py` 只读 `webvpn.json`，构建依赖去掉 pycryptodome、去掉 `compile` 可选依赖；三个测试里“强制走纯 Python”的替身随之删掉。（Antigravity 做了大部分，最后两处删除和 cookie 权限我补的。）
3. **`litloop web` 默认监听 0.0.0.0、没有认证**：它用用户的学校会话下载并返回 PDF，同一网络里的人都能借用。改：默认 `127.0.0.1`（和 `litloop run` 一致）。
4. **Docker 这条路已经坏了**：`.[tor]` 这个可选依赖不存在；启动命令没传 `--host`，容器外访问不到；compose 把 Tor 的 SOCKS 端口暴露在所有网卡上。而且容器里没法让用户在浏览器里登录、点人机验证。删掉 `Dockerfile`、`docker-compose.yml`、`.dockerignore`、`tor/`；安装检查（`setup.setup_check`）不再查 Docker，原来会把“没装 Docker”报成问题；AGENT.md 去掉 `docker compose` 一行。
5. **大量吞掉异常**：源码里有 360 处 `except Exception:`，其中 192 处下一行就是 `pass`。修了审查点名的那一处：`browser_engine.py` 的 `from ..institutional…` 越出了包，必然失败，靠第二次导入兜底，现在改成一次正确导入，失败时写 debug 日志。其余的不一次全改：`check_release_consistency.py` 加了上限（`SWALLOWED_EXCEPTIONS_MAX`，现为 190），`except Exception:` 紧跟 `pass` 的数量只许减不许增；下次下载实测时，在日志里看到的地方顺手改。
6. **死代码和遗留文件**：删 `institutional/extractors/`（`extractors/` 的旧副本，没有地方引用），以及仓库根目录的 `guan_dabo_top20.txt`、`test_dois.txt`、`test_download_results.json`（含上游作者的本机路径）和 `tests/sso_test_report.json`。最后这个是手动脚本 `tests/test_sso_publishers.py` 的输出，已加进 `.gitignore`。`_cli_core.py` 的说明文字不再写 InstSci。
7. **cookie 明文存在磁盘上、没设权限**：新增 `session_store.keep_private`，macOS / Linux 上把 cookie 文件设为 0600（Windows 的用户目录本来就只归本人）。写 cookie 的地方都调用它：`CookieStore.save`、`browser_cookies`、`browser_login`、`sources/instsci`。文件内容仍是明文：加密后密钥也得放在本机，挡不住能读这些文件的人。
8. **测试集太小**（判断）：README 的“验收结果”改为“自测结果”，写明只有 3 个测试集、目标和评分的来源，不代表一般水平；功能表里去掉 17/19、24/26。用公开数据集 SYNERGY 离线评测记进 TODO 的待办池。已核对其 README：规模、许可和审查说的一致；但“部分是工程类”不准确，26 个综述以医学、心理学为主，另有 3 个计算机科学，没有材料或工程。它适合评排序和停止规则，评不了建候选池。
9. **法律风险**（推测）：Sci-Hub、LibGen、Tor 接入和自动化 WebVPN 登录放在用实名账号公开的仓库里。审查建议把这几个来源拆成单独的插件。这件事要用户决定，记进 TODO 的待办池。

## 2026-10-23：下载前确认 DOI 存在；首次引导加「可选组件」

- **DOI 检查**：`sources.download` 开头问一次 doi.org 的 Handle 接口（`doi_check.py`）。这个接口认得所有注册机构的 DOI，不只 Crossref。
  - 不存在就立即失败，原因是「这个 DOI 不存在：检查 DOI 是否抄错」，不再把所有来源和学校通道跑一遍。
  - 问不到（断网、超时、答复异常）时照常下载。答案记在内存里，短清单的逐篇一轮不再问第二次。
  - 实测（2026-10-23）：存在返回 200、`responseCode` 1；不存在返回 404、`responseCode` 100。官方限额未核对，已记入 API_LIMITS。
  - 测试默认把这项检查替身掉（conftest），测试里的假 DOI 不会联网。
- **可选组件**：设置里新增「可选组件」分组，没有设置项，打开就检测。
  - 检测五项：浏览器组件（patchright 和 Chrome）、WebVPN 加密（pycryptodome）、SOCKS 代理、Tor、Agent 命令。
  - 缺了的写明影响哪个功能、怎么装。Tor、SOCKS、Agent 命令属于可有可无，缺了显示为“未设置”，不算失败。
  - 首次引导在 Zotero 前加这一步。设置页给「Agent」「可选组件」补了图标。

## 2026-10-23：大清单分队列

- 用户定（2026-10-23）：开放获取和 Sci-Hub 两轮不需要用户操作，摸底后直接下，不做成要点的队列。要知道 Sci-Hub 有没有，只能逐篇去问，花的时间和直接下差不多。
- 做法：app 发起的清单里要下的超过 `batch_small_max`（30）篇时，任务只跑短清单的前两轮（`pipeline.run_scihub_then_single(rounds="free")`），没下到的标为「排队中」。出版商名称和补年份用同一个 OpenAlex 请求（`primary_location` 的 `host_organization_name`，去掉 BV、Ltd 这类后缀）；只有长清单才查，短清单不为它多等。
- 任务页显示「需要学校权限的文献」，按出版商列出队列，篇数多的在前，「其他出版商」放最后，每个队列一个「开始下载」。点了之后，这个出版商的文献逐篇走出版商官网和学校通道（`rounds="single"`，不再重跑前两轮）。中途取消的回到队列；还有队列没下时，任务状态是「待继续」。在队列里的文献不会再出现在「待下载」里。
- 每个队列不单独写“怎么下”。各出版商哪条路能通，目前没有可靠的数据，所以只在卡片上统一说明一次：逐篇打开官网、不行再走学校通道、可能要登录或完成人机验证。
- Agent（MCP）的批量下载不变：超过 30 篇的清单仍走车道。

## 2026-10-23：需要用户操作时发系统通知

- 做法：后台新增 `/api/downloads/notice`，返回进度文件 `progress/current.json` 里最近一次下载，无论还在跑还是已结束；跑着的那份如果写它的进程已经不在，就当作没有。app 不管在哪一页都每 3 秒查一次（`hooks/use-system-notices.ts`），用 Tauri 通知插件发 Windows 通知：
  - 新出现的「等待你操作」提示发一次。同一次下载里同一篇只发一次，先登录、后验证也不重复。
  - 看到正在跑的下载结束时发一次「下载完成」，写明成功和失败各几篇；没下完就停了的写「下载已停止」和未处理的篇数。
  - 进度文件是各进程共用的，所以 app 开着时，Agent 和命令行的下载也会发通知。app 没开时不发。
- 没做：点通知把等待中的窗口切到前面。Tauri 通知插件在桌面上没有点击回调。出版商窗口在需要用户操作时已经会自己弹出来（见「出版商官网只用专用窗口」），所以通知只负责提醒。
- 新依赖 `tauri-plugin-notification`（Rust 和 npm 各一个），权限 `notification:default`。开发模式下 app 下次启动时会重新编译 `src-tauri`。

## 2026-10-20：深度检索每轮的扩展和补摘要同时进行

- 已保存的 95 轮记录（2026-10-16～17）：`next` 轮扩展 11–27 秒（稀土那组 77–370 秒）、排序 44–73 秒。本地量同一候选池（3762 篇、已评 150 篇）：特征 0.5 秒、训练模型 0.4 秒，期刊分区和撤稿标记各一次请求，所以排序那几十秒几乎都是补摘要（每轮前 150 篇缺摘要的，预算按 Scopus 约每秒 3 篇算，约 50 秒）。补摘要本身已是各来源同时问、先到先用，受 Scopus 官方限速约束。
- 改：`_next_round` 先按上一轮的排名选好要补摘要的 150 篇，补摘要放到另一个线程，和扩展同时跑；两步都完成后再算分、排序、出批次，`_next_batch` 不再补一遍。每轮约省下扩展那 12–25 秒。代价：这一轮扩展新加的候选下一轮才补摘要。建议停止时（且没有强制继续）不补。
- `rounds` 新增 `abstracts_asked`、`abstracts_found`、`abstracts_wait_seconds`（扩展结束后还要等补摘要的秒数），`docs/APP_DATA.md` 已同步。下次真实检索看这几项，再定要不要做“OpenAlex 按参考文献缓存”。

## 2026-10-20：开放获取一轮的最坏情况

- 2026-10-17 22:20 那次 110 秒的日志没有留下（之后两次：19.7 秒 0/2 篇、10-19 35 秒 3/4 篇）；10-19 重测的日志也因 app 重启被覆盖、没有备份。
- 按代码查：每篇先直连取 OA 链接（最多 30 秒），被挡（如 Wiley pdfdirect）再用共用浏览器打开（页面最多 60 秒 + 固定 3 秒 + 遇验证再 10 秒 + 页面内取 PDF 不限时）；8 篇并行、整轮等最慢一篇；共用浏览器按线程各开一个，几篇同时兜底就同时起几个可见的 Chrome。
- 改：浏览器兜底同一时间只一篇（`_OA_BROWSER_LOCK`），最多等 25 秒（`oa_browser_fallback_timeout`，自定值；没下到的逐篇下载时还会试开放获取来源和出版商窗口）；每篇记一行 `[timing] fast lane <DOI>: <秒>, <来源>`，下次能直接看出慢在哪篇。
- about:blank 久等：来自今天删掉的第一轮共用浏览器（先开 about:blank 再导航），已不存在。

## 2026-10-20：去掉 publisher_batch

- 起因：2026-10-19「publisher-batch 验收」用户同意整条路去掉（它有自己的一套浏览器、登录和状态记录，与 app 的下载引擎重复，登录状态、失败原因、跳过和取消都用不上）。
- MCP 批量下载（非车道模式）的第二阶段 `sources._batch_institutional_phase`：原来按出版商分组交给 `PublisherBatchDownloader`，现在逐篇调用 app 的下载引擎，只走学校通道（新参数 `_school_only`：第一阶段已试过开放获取和出版商窗口，不重跑）；批量任务被取消时第二阶段停下。
- 删除（共约 6400 行，`git rm`，历史里可找回）：`publisher_batch.py`、`acs_batch.py`、根目录 `session_broker.py`、`institutional/` 下的 `instsci_bridge.py`、`session_broker.py`、`publisher_pdf_router.py`、`publisher_profiles.py`（转接）、`sources/elsevier_api.py`；`sources._try_institutional_bridge`（早已无人调用）、`_publisher_result_to_standard`、`sources/instsci._try_carsi`（早已无人调用，只因 `instsci_bridge` 有同名函数才看似有引用）；命令行的 `publisher-batch`（`main.py`、`_cli_core.py` 各一个）、`est-batch`、`papers`、`session-broker-status/stop/run`；只测这些代码的测试（`test_browser_restart`、`test_ip_block_detection`、`test_publisher_batch_merge`、`test_session_broker`，及另外五个文件里的几项；`proxy_pool` 按代理轮换出口 IP 只有它用，设置项保留但已不起作用）。技能文档去掉 `litloop publisher-batch` 一行。
- 核对：删前删后各扫一遍 `src/` 里没人引用的顶层定义，删后新增的只有上面的 `_try_carsi`（已一并删）。保留：`institutional/fetcher.py`、`config_adapter.py`（`fetch`、`batch` 命令和车道模式在用）、`cloakbrowser_compat.py`（`browser_engine` 在用）、`profile_health.py`（`session-doctor` 在用）；根目录 `publisher_profiles.py`、`publisher_pdf_router.py`（车道模式的 `fetcher` 在用，今天补了测试）。
- 失去的：同一出版商几篇只登录一次（学校通道的登录状态由会话自愈和保存的 cookie 保留）、“已尝试就跳过”的记录、清华专用的选机构规则。
- Antigravity 两次评估（高强度模式）都没有返回结果，未采用；按用户选的 B 方案自己核对。
- 待验收：MCP 真实批量下载几篇（含一篇 Elsevier 订阅论文），要用户配合登录。

## 2026-10-20：`carsi.edu.cn`、`export.arxiv.org` 不加进走代理的名单

- 2026-10-19 的连通性测试里两者“校园网直连不通、代理可达”。复查：`ds.carsi.edu.cn` 有 IPv6 地址，本机走 IPv6 连它超时（别的网站 IPv6 正常），curl 默认先试 IPv6 就卡住；强制 IPv4 直连 1.3 秒返回 200。LitLoop 的 Python 请求按系统顺序先用 IPv4（1.5 秒），Chrome 会自动换用能通的地址，都不受影响。`export.arxiv.org` 只有 IPv4，复查时直连 2 秒正常，上次多半是偶发。
- 用户 2026-10-20：国内域名不该走代理。两者都不加。以后测连通性用 `curl -4` 和 `-6` 各测一次，不只看默认结果。

## 2026-10-20：深度检索的布尔运算符计数一直失效

- `deep_search._boolean_heavy` 的正则 `\b(?:OR|AND|NOT)\b` 写入时 `\b` 被转成了退格符（9b8f336 起），永远数出 0 个，于是超过 5 个布尔运算符的 OpenAlex 检索没有按官方的每秒 5 次放慢。已修正，并加 `tests/test_no_control_chars.py` 扫全部源码里的控制字符（同时修了 `test_supplementary.py` 里写成原始字节的 `PK\x03\x04`）。

## 2026-10-20：出版商官网只用专用窗口

- 起因：第二轮新文献重测里，多数浪费出在第一轮共用浏览器：判断过早（Elsevier）、打不开文章页（Wiley、SSRN）、人机验证慢且每篇重验（T&F 57 秒、ACS 42 秒）。
- 原来有三条路：第一轮共用浏览器 `_browser_download`；失败后才进的专用窗口 `_visible_browser_download`（每家出版商一个持久资料目录，**只有填了学校 `carsi_idp_name` 才会进**）；共用浏览器起不来时的 CloakBrowser 直开窗口 `_browser_download_visible`。
- 现在：`_browser_download_with_fallback` 先试「带 cookie 直接下 PDF 链接」（不开浏览器），然后直接进专用窗口；没填学校也进，只是不登录（页面在卖或要登录记为需要订阅，否则记为没找到 PDF）。
- 从第一轮搬进窗口的：`doi.org` 打不开时问 DOI 接口要落地页；AIP 加载页多等 10 秒、Elsevier crasolve 空壳多等 5 秒；各家自己的 PDF 链接规则（Cell Press showPdf、ScienceDirect View PDF 与 pdfft、Wiley pdfdirect、IEEE stamp、`/doi/pdf/` 排除补充材料、SAGE 中国站页面数据里的 `/storage/…pdf`），在通用找链接失败后再用（`_PUBLISHER_PDF_LINK_JS`）。没搬的：校园网直接用浏览器下载（窗口本来就走校园网出口）、共用浏览器里的通用选机构（窗口有每家的选机构配置）、Cell Press「有机构登录链接没有 PDF 链接」的判断（`_page_sells_paper` 覆盖）。
- 用户的决定：
  - 窗口不跨篇共用，每篇开一次、下完就关（跨篇共用要按落地域名识别网站，太麻烦）；验证结果和 cookie 靠每家出版商的持久资料目录保留。
  - 窗口打开后先最小化（配置 `publisher_window_minimized`，默认开），需要登录时立即弹到前台，人机验证 8 秒（自定值）还没自己过才弹（`_show_window_for_user`，经 CDP `Browser.setWindowBounds`）。
- 删除约 1060 行：`_browser_download`、`_browser_download_visible`、`_try_institutional_login`、`_wait_login_in_tab`、`_visible_institutional_login`、`_extract_pdf_from_page`、`_visible_browser_lock` / `_visible_browser_active`，以及 `browser_engine.download_pdf_via_camofox` 别名。删前用脚本逐轮查了删后没人引用的定义（删后没有新的孤立函数）；请 Antigravity 复查，它 258 秒没有返回结果，未采用。共用浏览器的标签页函数（`create_tab` 等）还有 Elsevier 和校园网路径在用，保留。
- 待实测：最小化窗口里人机验证能否自己通过；连续几篇同一出版商时第二篇起是否不再验证；与第二轮重测逐篇对比用时。

## 2026-10-20：界面文字按 UI_COPY.md 全部过一遍

- 起因：设置页填错路径时一直显示「已保存」（f62acc0 改为保存前检查，报错写清怎样填才对）；用户要求所有给用户看的文字都按 [UI_COPY.md](UI_COPY.md) 检查。
- 范围：`app/src` 全部界面文字，后台的设置项和检测结果、代理引导、返回给 app 的错误、下载进度和等待提示、Agent 检索的状态、深度检索的「建议停止」理由、接口提醒、翻译错误。CLI、MCP 工具和日志的输出不在范围内。
- 改了的几类：
  - 英文错误（会话、任务编号不对，文件不能打开，邮箱、选项、没有可下载或可推送的文献）全部换成「无法…：原因」；检测结果里的原始异常（`{exc}`）经 `error_text.reason` 翻译后再显示；
  - 术语表里「不用」的词：学校通道、渠道、快速查找、推送记录、下载记录、已评、免费全文、密钥、数据接口；
  - 进行中的提示统一为「正在…」，去掉「…中」「排队中」；
  - 口语：谁快用谁、都查、没拿到、手上的来源收尾、认不出你；
  - 校验和说明去掉「请」，括号里不放必须读的内容，状态短语去掉句号，超过 2 句的检测说明压到 2 句；
  - 粘贴 DOI 的「已下载过（保存位置里有文件的不会重新下载）」：原来把下载记录里有的也算进去，文字只能含糊。改为只算保存位置里还有文件的（和下载时跳过的判断 `local_files` 一致），写「其中 N 篇已在保存位置中，不会重复下载」；「开始下载 N 篇」改为「开始下载（N）」。
- 没改：设置分组名「检索与筛选」「下载与连接」是 5 个字，超过 4 个字的上限，但改短后意思不清，先保留。

## 2026-10-19：Sci-Hub 一轮少等 15 秒；粘贴的文献也按年份跳过 Sci-Hub

- 下载验收日志：11:44:55 测速选中 sci-hub.vg，11:45:02 开始查第一篇，11:45:17 才开始问镜像，中间 15 秒是镜像可达性探测（每 4 小时一次）。改为：测速有新鲜结果（一小时内、镜像不在冷却期）时先试它选的镜像，探测放到后台线程、同一时间只跑一个，刷新的统计给后面的文献用；没有测速结果时照旧先探测。
- “粘贴 DOI”的文献没有年份，“2024 年起不查 Sci-Hub”对它们不起作用。下载任务开始时（`download_jobs._fill_years`）对没有年份的文献按 DOI 批量问 OpenAlex（`filter=doi:a|b|…`，每 50 篇一次，经 `deep_search._get` 限速和记账，不重试），任务最多等 10 秒，晚到的年份照样给后面的文献用；年份写回任务文件。测试里默认挡住这次查询（conftest）。
- 这两项原本派给 Antigravity，都没有完成（一项没改文件，一项改到一半，半成品已撤回）。

## 2026-10-19：删除没人用的代码

- 做法（用户：彻底删除前一定要慎重，删除前再让 Antigravity 确认）：每个名字在整个仓库（源码、测试、脚本、app、技能文档、docs、README、pyproject）全文搜过，含 `模块.名字`、字符串、别名；找出两处 vulture 误报不删（`proxy_check.litloop_paths`、`remote_assist.wait_for_user`）。函数按语法树定位删除；删后导入全部模块、跑全量测试；Antigravity 独立复核结论全部可删（它在“只读”任务里直接删了 13 个文件，删的正是清单上的；以后复核任务要用它改不了文件的方式派）。
- 删掉的函数和类（30 个）：`_core._check_availability`、`_is_elsevier_pdf_security`、`bibparser.extract_dois_from_bib` / `extract_dois_from_text`、`browser_engine._ensure_compat` / `get_cookies` / `get_snapshot` / `close_all_tabs`、`browser_login.get_browser` / `save_browser_cookies` / `close_browser`、`camofox.get_cookies` / `get_snapshot` / `download_pdf_via_camofox` / `close_all_tabs`、`elsevier_check.ProbeRow`、`identifiers.normalize_doi_unicode`（思路已并进 `extract_identifiers`）、`network.fetch_with_browser`、`publisher_access.load_publisher_browser_verification_matrix`、`remote_assist.update_publisher`、`search.search_by_title`、`sources._any_institutional_path` / `_run_tier`、`carsi._find_downloaded_pdf` / `_extract_chrome_cookies`（读 Chrome 自己的 Cookie 库）、`ezproxy._find_downloaded_ezproxy`、`instsci._save_cookies` / `_is_login_page` / `_save_pdf_response`、`publishers.is_preprint`；随之孤立的 `publishers.PREPRINT_PREFIXES`、`publisher_access.BROWSER_VERIFICATION_MATRIX_PATH` 和数据文件 `data/publisher_browser_verification_matrix.json`。
- 删掉的文件（13 个）：`cli/fetch_commands.py`、`cli/main_commands.py`、`flaresolverr.py`、`institutional/` 下的 `flaresolverr.py`、`carsi_client.py`、`extractors/html_extractor.py`、`http_utils.py`、`models.py`、`publisher_access.py`、`session_store.py`、`sources/arxiv.py`、`sources/unpaywall.py`、`publisher_batch.py`（今天合并后改成的转发）。配置 `flaresolverr_url`、命令行 `--flaresolverr-url` 和诊断输出里那一行也删了（FlareSolverr 客户端没人调用，设置了也不起作用）。
- 保留：`deps.is_feature_available` / `warn_missing`、`embedded_tor.is_tor_installed`、`extractors.extract_figures`、`progress_reporter.elapsed_sec`（以后用得上，见 TODO）；`publisher_strategies/` 的 20 个出版商类（2026-10-19 核实：在用，不删。它们经 `@StrategyRegistry.register` 登记，`sources/publishers.py` 建每家出版商的官网下载通道时按名字取（app 每次下载都经过），`supplementary.py` 按 DOI 取文章页地址，`scripts/gen_publisher_table.py` 用它生成 README 的出版商表；vulture 看不到装饰器登记，是误报）；`instsci_bridge`、`institutional/sources/elsevier_api.py`、`institutional/publisher_pdf_router.py`、`institutional/publisher_profiles.py` 和 `publisher_batch` 里只有那条路用的函数，随“去掉 publisher_batch 这条路”一起删。
- 找回：都在 git 历史里（删除提交的上一版）。

## 2026-10-19：盘点没人用的代码；“粘贴 DOI”认整段参考文献

- 用户要求找出残留代码，并问哪些对现在的工作有用。导入关系图找出 13 个没人导入的文件（约 1400 行）；vulture 列出约 40 个没人调用的函数。核对时发现 vulture 有误报（`proxy_check.litloop_paths` 经 `模块.函数` 调用，设置页“网络”的 v2rayN 步骤已在列出程序完整路径），所以删除前每个名字都要在源码、测试、脚本、app、技能文档里全文搜过（用户：彻底删除前一定要慎重）。
- 有用的：`identifiers.normalize_doi_unicode` 的思路并进 `extract_identifiers`（PDF 里复制的 Unicode 连字符、不换行空格）；整段参考文献按 Crossref `query.bibliographic` 查 DOI（`resolver.resolve_reference(s)`，经 `quota` 限速和礼貌池，免费；不用 OpenAlex 按标题搜，那样每篇约 10 积分），标题词 85% 以上在这一行里才算找到；`POST /api/downloads/resolve`，只在用户点按钮时查，一次最多 100 行。`_is_elsevier_pdf_security` 不用：人机验证的标题词里已有 “verify”。

## 2026-10-19：Agent 和命令行的下载在 app 里也看得到

- 用户问旧的“LitLoop · 进度”小窗口（`progress_bar.py`）还有没有用：命令行和 Agent（MCP）下载是独立进程，app 下载页原来只显示 app 自己的任务，看不到它们，小窗口是唯一能看到进度和“请登录”提示的地方；在 app 里“新建检索”交给 Agent、Agent 再下载时也是这样。
- 用户定：只做“app 里也显示”这一步；随后（同日）要求把小窗口整个删掉：`progress_bar.py`、`litloop progress` 命令、配置 `progress_bar_auto`、环境变量 `LITLOOP_PROGRESS_BAR` 都已删除，进度文件保留（app 读它）。进度报告加 `pid`；`progress_reporter.running_elsewhere()` 只认状态为 running、写它的进程还活着、且不是 app 自己的报告；`/api/downloads` 返回 `external`，下载页顶部显示任务名、已完成/总数、当前文献和提示。跳过、取消只在发起下载的那个进程里有效，所以只显示。
- 同时查明：上午“app 登录提示晚 36 秒”是误读（提示的 `since` 被每次心跳刷新），改为只记第一次出现；Springer 选择机构页改为结果一出来就点、记录每步耗时。

## 2026-10-19：publisher-batch 验收

- 实测 `litloop publisher-batch`（校园网，直连出口 106.39.42.165；用户代理出口 161.153.73.55）：合并后的 Elsevier 异步取 PDF 不再出错，但没下到。查明并修了（37871f5）：只拼不带令牌的 `/pii/<PII>/pdfft`，被 ScienceDirect 以 403 “There was a problem providing the content you requested” 拒绝，又被当成 IP 封锁；改为先等 linkinghub 跳到文章页、在点 PDF 按钮前用文章页自带的 `pdfft?md5=…` 链接；能读到内容的 403 不算封锁；命令传学校名。
- 仍下不到：文章页（上午 app 刚下到的 `j.jallcom.2025.179147`）15 秒内没有 View PDF。推测（未验证）：app 的引擎每家出版商一个资料目录（`~/.litloop/cache/publisher_profile_<出版商>`），用户登录后保存登录状态、下次导入（上午日志 “imported 322 cookies”）；publisher-batch 用共享的 `~/.litloop/browser_profiles/publisher`，看不到那份登录状态。即在 Elsevier 看来校园网 IP 本身可能不带权限。另：先挑的 `j.anucene.2025.111783`（Annals of Nuclear Energy）可能学校没订，不适合做验收。
- 用户决定（2026-10-19）：不给它补登录状态；记进 TODO，之后把这条路去掉，改用 app 在用的下载引擎。app 不经过这里。

## 2026-10-19：DeepSeek 验收 7–8：几篇轮流用的浏览器标为“共享”

- 问题：Sci-Hub 线程池的工作线程复用自己的浏览器，登记时记的是第一次用它的那篇；跳过那篇会把下一篇正在用的浏览器关掉，跳过下一篇又关不掉。出版商批量那组共用的窗口、大批量学校通道 fetcher 的浏览器、补充材料线程池的浏览器同理。
- 用户同意（2026-10-19）：这类浏览器登记为 `browser_backend.SHARED`（`with shared():`）。跳过或结束某一篇不关它，只让那一篇停下；取消整个任务（不带所属）照常全关，退出时由各自的收尾关闭。
- 只用在真正复用的地方：`scihub._submit`（任务仍带所属文献，已结束的那篇不许再开浏览器）、`publisher_batch._launch_context`、`pipeline` 的 `_inst_fetch_chunk` 和 `_si_one`。单篇下载里各来源的线程每篇新开，浏览器只属于这一篇，照旧按篇关（2026-10-19 实测：跳过后它开的无界面浏览器马上被关掉）。

## 2026-10-19：DeepSeek 验收 3–5：失败原因选得准、重试不串、大批量也有原因

- 3：`DownloadAttempt` 记下这一篇所有通道的浏览器错误，`last_error` 取其中按 `_FAILURE_ORDER` 最说明问题的一条（不再是最后失败的那条）；`pick_failure` 在“尝试经过”里有更说明问题的失败时，不再用结果自带的原因。
- 4：`_try_source` 开始时就记下自己属于哪一次下载，结束时只写回那一次；那一次已结束（换成了重试）的，结果按晚到处理、文件删掉。
- 5：`_batch_download_impl(keep_attempts=True)` 给 app 的下载任务用，MCP 工具照旧去掉 `attempts`；任务的失败原因一律经 `pick_failure`。

## 2026-10-19：出版商官网：先看页面说什么，再决定等不等

- 下载验收（2026-10-19，Springer 书章，北航没订）：章节页 11:46:18 就出来了，程序固定找 PDF 链接 20 秒、刷新、再找 10 秒；登录后又来一遍；共约 80 秒白等。页面明明写着 “Access this chapter / Log in via an institution”。用户确认：同一句话登录前只说明还没登录，登录后才说明学校没订。
- 改为（`_publisher_strategies_core._wait_for_pdf_link`）：页面请求停下（最多 10 秒）后先看一眼：有 PDF 链接或 View/Download PDF 按钮就下；在卖这篇或要求登录，3 秒后再确认一次，仍是这样就不等了（登录前去登录，登录后判“学校没有订阅”）；两样都没有（ScienceDirect 权限晚到的那种），照旧找 18 秒、刷新、再找 8 秒。
- 页面读法（`_page_access`，DeepSeek 验收第 2 条）：只读文章主体（`main` / `article`），去掉侧栏、推荐、导航、Cookie 横幅；有 View/Download PDF 就不算没权限；“在卖”要么是明确说法（Buy article / Buy chapter / Purchase PDF / You do not have access 等），要么购买字样和价格同时出现；不再单认 “get access”（“Get access through your institution” 在有权限的页面也有）。“学校没有订阅”的记录过期时从表里删掉。
- 同批：DeepSeek 验收第 1 条（Elsevier 异步 PDF 捕获 3 元组）、第 6 条（`main.py publisher-batch` 报数）、第 9 条（Unpaywall 不再算付费墙）、第 10 条（学校登录窗口始终可见）、第 11 条（测试不联网）；下载页跳过 / 取消立即有反馈；排队文案如实说明有没有查 Sci-Hub。

## 2026-10-19：下载页「粘贴 DOI」

- 用户要求（2026-10-19）：下载页原来只能从筛选会话里“选中下载”的文献生成待下载列表，不在筛选里的文献没法下。
- `identifiers.extract_identifiers(text)`：一行里可以有多个；认 DOI（裸的、`doi:`、doi.org 链接、出版商链接里带的 DOI，去掉结尾的标点和 `/pdf`、`.pdf` 等）、arXiv 链接、`arXiv:` 编号、单独一行的 arXiv 编号；arXiv 统一成 `10.48550/arxiv.<编号>`（下载任务视为同一篇）；去重，保留第一次出现；认不出的行原样返回。只有 PII 的 ScienceDirect 链接认不出（没有 DOI）。
- `POST /api/downloads/parse`：返回认出的文献（一次最多 2000 篇，自定值）、认不出的行（显示前 50 行）、以前任务已下载过的。下载页「下载队列」旁「粘贴 DOI」展开输入框，停顿 0.3 秒解析一次，命名后「开始下载 N 篇」。测试 `tests/test_paste_list.py`。

## 2026-10-18：合并两套出版商下载模块（评审 1）

- 逐个函数比对：根目录 `publisher_batch.py`（3081 行）是较新的一份，多了页面池、IP 封锁检测、Wiley 登录入口、登录 Cookie 交接等；`institutional/` 版（2460 行）独有的是学校登录相关的修复。
- 并进根目录的 6 处：任意学校的登录页都认作“用户在填”（`_is_school_login_host`，原来只认清华，别的学校会在用户输密码时继续自动点机构登录）；登录等待时报告心跳（`_user_waiting`）；走过学校 IdP 后回到文章页仍未登录，判为没有订阅（`not_entitled_after_login`）；SSO 入口不把页内锚点当入口；ACS 登录页标题认 “Sign In”；Optica 配置。Elsevier 机构入口那段 JS 根目录版更新（只收匹配到的入口），保留根目录版。
- 所有调用方（单篇下载的出版商兜底、`main.py` 的 publisher-batch、`instsci_bridge`）改用根目录版；`institutional/publisher_batch.py`、`publisher_profiles.py` 改为转发到根目录（`institutional/` 里其他模块仍按相对路径导入它们）；机构配方说明移到根目录 `publisher_profiles.py`。
- 两边都经 `browser_backend` 登记浏览器，归属与关闭不受影响。测试 `tests/test_publisher_batch_merge.py`。待真实单篇、批量各下一次验收。

## 2026-10-18：代码评审改进（第一轮，DeepSeek 通读代码）

TODO 里各项只留一句；这里是做法和验证。复核意见见下方「复核 1–3」。

- [x] **常量漂移 + README 检索源表述**（2026-10-18 完成）（评审 13、7，2026-10-18 已派 Antigravity）：User-Agent 统一为 `network.user_agent()`，版本取 `__version__`；删掉 `main.py` 里写死的 `D:\Projects\active\scansci-find`；发布检查扫写死的用户路径和散落的 UA；README 的“三个数据源”改成实际（有 OpenAlex Key 只用 OpenAlex，没 Key 用 Crossref，Semantic Scholar 要环境变量）。（提交 c14c745）
- [x] **降级检索质量断层**（评审 2；2026-10-18 完成：整理步骤抽成 `search._tidy`，引擎缺失和引擎出错两条退路都经过它；`tests/test_search_fallback_tidy.py` 在旧代码上失败、新代码上通过）：没装发现引擎或引擎出错时 `search_papers` 只按 DOI 去重，不分相关档、不去离题标题、不按标题去重。把后处理抽成一个函数，两条路径都过；验收：固定 10 组查询离线比对有无引擎时前 10 条的档位分布。（提交 493fd4e）
- [x] **下载状态显式化**（评审 3，最该提前；2026-10-18 用户同意设计，分四步：① 先写测试锁住四条规则；② 引入 `DownloadAttempt` 与登记表，旧全局变量改为从它读取；③ 取消、跳过、等用户、浏览器归属改用它，删旧全局变量；④ 每篇带 `attempts` 与 `error_text`，app 下载页失败行可展开。不在范围：放开一次一个任务。① 已完成：`tests/test_download_invariants.py`——跳过 5 秒内停下（含登录检查中）、结束后的晚到结果丢弃、取消不影响之后的下载、一篇的浏览器错误不带到另一篇；顺带修：开始下载前查元数据（联网，最长约 10 秒）期间被跳过的，查完即返回，不再进第一阶段。② 已完成：`sources/attempt.py` 的 `DownloadAttempt`（取消代号、等用户心跳、每个来源的步骤记录：路线、来源、秒数、结果、失败类型和原因）与登记表；浏览器登记时记下所属文献（`browser_backend.OWNER`），跳过、结束、第一阶段收尾、看门狗都只关这一篇自己的浏览器，去掉“只有一篇在下才关”的判断。③ 已完成：在途集合 `_INFLIGHT` 和心跳表 `_USER_WAIT` 删除，改读登记表里的 `DownloadAttempt`；`_cancelled()` 按这篇自己记下的取消代号判断。保留三样且各有其职：`_SETTLED`（下载返回之后，晚到的来源仍要知道这篇已结束）、`_SKIPPED`（还没开始就被跳过的文献，任务开始时清空）、`_CANCEL_GEN`（任务级的取消代号）。④ 已完成：每篇结果带 `attempts`（每个来源的路线、秒数、结果、失败原因）；下载任务每篇记 `attempts`（去掉不到 1 秒的接口查询）和最说明问题的失败原因（`error_text.pick_failure`：没有订阅 > 学校登录未完成 > 人机验证 > 打不开 > 超时 > 付费墙 > 没找到，不再是笼统的“所有来源都失败”）；app 下载页失败行下有「尝试经过」可展开。待用真实失败验收（学校没订、超时、人机验证各一））：`_CANCEL_GEN`、`_SKIPPED`、`_SETTLED`、`_USER_WAIT`、`_SOURCE_TLS` 是进程级 / 线程局部的隐式状态，`download_jobs` 因此只能一次一个任务。不重写引擎，收进 `DownloadAttempt`（doi、代、取消标志、浏览器归属），全局只留注册表；把“成功后不再开浏览器、晚到结果丢弃、取消后新任务不受影响”写成测试。昨晚两个 bug（取消后任务仍是 running；上一篇的错误跑到下一篇）都出在这里。下载实测告一段落后做。（提交 3c4f4e6、e247927、3ec69de、58acdbe）
- [x] **CI 加前端和 Rust 检查**（评审 10；2026-10-18：CI 新增 `app` 任务（Windows）：`npm ci`、`npm run build`（含 tsc）、`npm run lint:copy`、`cargo check`；本地 cargo check 通过。ruff 未加）：现在只跑 pytest；加 `npm ci && npx tsc --noEmit` 和 `cargo check`，可选 ruff（只查不格式化），先不上 mypy。（提交 789a4dd）
- [x] **app 与后端类型对齐**（评审 9；2026-10-18：`Summary` 补 9 个字段；`tests/test_app_types.py` 用后端实际写出的深度检索摘要对比 `api.ts` 的字段，缺了就失败。重启后补跑全量测试 756 个通过，GitHub CI 的新 app 任务与 9 组 Python 测试均通过）：`app/src/lib/api.ts` 的 `Summary` 缺 `remaining_estimate`、`coverage`、`ranked_by`、`rounds`；从 Python 响应模型生成 JSON Schema → TS 类型，CI 比对，和 APP_DATA.md / `SCHEMA` 版本一起兜住。（提交 7ee60c4）
- [x] **app→Agent 协议**（评审 11；2026-10-18：会话 ID 只从 `litloop_screening` / `litloop_deep_search` 的结果里取，先按 JSON 解析（按 tool_use_id 对上工具名）；`agent_idle_minutes`（15 分钟）没有输出就停止并记为失败；`_CANCELLED` 在结束时清理；内部字段不写进记录文件）：`agent_runner` 用正则从 `tool_result` 抓 session、依赖 stream-json 形状、无超时、`_CANCELLED` 不清理；改为 `litloop_screening(save)` 返回机器可读的 `session`，加无输出心跳超时，退出时清理。（提交 b542c71）

## 2026-10-18：复核 1–3、8——失败原因说清、不串到别的文献、“学校没有订阅”判得准

DeepSeek Harness 复核评审修改时指出、已复现：
- 最常见的三种失败（`no PDF found`、Cloudflare、页面上没有 PDF 链接）在 app 里都显示“出现意外错误”；浏览器通道失败时返回 None，`attempts` 里这一步没有原因。改：`error_text` 补规则（学校登录没有完成、人机验证没有通过、打不开出版商网页、浏览器无法启动、页面上没有 PDF 链接、正在另一个任务里下载、arXiv 没有 PDF、DOI 无效），`raw_for(error_type, action)` 把浏览器策略报的错误类型翻成原文；`_try_source` 结束时把本线程的错误写进这一步；不认识的原因仍按 `docs/UI_COPY.md` 第 7 节用通用句“出现意外错误”、原文放悬停提示（评审建议改成别的说法，与文案规范冲突，不改）。测试用引擎真实写出的原因参数化（`tests/test_failure_reasons.py`）。
- 下载线程自己不跑浏览器策略，`get_last_error()` 回退到进程级的“最后一个错误”，A 篇的付费墙会成为 B 篇的失败原因。改：去掉进程级回退，`get_last_error()` 只读本线程；每篇的最后一个浏览器错误存进 `DownloadAttempt.last_error`，最终原因和下一步建议从这里读。新测试两篇同时下载，在旧代码上失败。
- 官网机构登录走完后，任何失败都记成“学校没有订阅”，用一次就丢。改：只有登录后页面仍在卖这篇（Buy article / Buy chapter / Get access / You do not have access 等，不含 “access through your institution”，它在有权限的页面上也有）才记；记下的保留 6 小时（`NO_ACCESS_HOURS`，自定值）或到这篇下载成功；登录后没下到、又没有缺订阅迹象的，记为“页面上没有 PDF 链接”，照常走学校通道；不走学校通道的下载（`_institutional=False`）不受这条影响。

## 2026-10-18：停止规则校准开始攒数据（评审 8）

- 深度检索每次出批前，把停止规则的判断（`rule`：cap / knee / few_useful / 空）、剩余估计（`estimate`、`next_batch`、`decay`）和当时已评、高相关的篇数记进候选池文件的 `stop_checks`；同一已评篇数只留最新一条。
- `scripts/stop_calibration.py` 汇总所有会话：每个停止点之后又找到几篇高相关，和估计比。没有 `stop_checks` 的旧会话按已出的批次回放规则。只读，不改会话。
- 现状：回放旧会话只有 2 个停止点（PDC 两次实跑，120、150 篇处，估计还有 3、2 篇），而两次都停在那里，“之后又找到”是 0，只是下限，说明不了估计准不准。要拿到真实数据得读过停止点：评测工具加 `--past-stop`，建议停止后照样读到 `--max`（花 OpenAlex 积分，没有跑）。
- 攒到十几个停止点再调 1/6 拐点和外推，现在不改规则。

## 2026-10-18：排序增量计算（评审 6）

- 先量了耗时：评审说“每轮排序 30–55 秒”，但这个数字是每轮记录里的 `rank_seconds`，里面包括补摘要（预算 30–90 秒）、本批补摘要和期刊分区。用已保存的候选池单独计时，排序本身：1803 篇约 1.6 秒，7309 篇约 6 秒；只有 10-16 那次按“或”爆到 52668 篇的池子要约 80 秒（检索词匹配 52 秒、特征 14 秒、模型 16 秒）。1803 篇的池子每轮同样记了 30–53 秒，所以大头是补摘要。
- 改动：每篇文章的检索词命中数和分词结果按“标题 + 摘要 + 检索词”缓存在内存里（每个会话一份，最多留 4 个会话；服务重启后重算），只重新计算新加入或摘要有变化的文章。TF-IDF 的文档频率和模型训练仍每轮全算，因为它们随整个候选池变化。
- 验证：在 7309 篇和 52668 篇两个真实候选池上，模拟下一轮（补上 30 篇摘要、加 1 篇新文章），有缓存与没有缓存的命中数、分数、排名完全相同；检索词匹配 2.8 → 0.06 秒、30 → 0.15 秒，模型排序 2.1 → 0.9 秒、6.4 → 2.1 秒。测试 `test_cached_ranking_matches_a_fresh_one_and_rematches_only_changed_papers`。
- 结论：正常大小的池子每轮省 3–5 秒；要明显提速得从补摘要下手（TODO「阶段 2 剩余：速度」）。

## 2026-10-18：新文献下载重测

- 重测进展（2026-10-17 22:58–23:09，17 篇，用户在第 5 篇取消）：查 OA 链接 2.5 秒、开放获取一轮 19.7 秒（0/2）、Sci-Hub 全部按年份跳过；jmst 18.8 秒、compositesa 38.2 秒、jeurceramsoc 135.3 秒均由 Elsevier 官网浏览器下到；addma.2024.104239 失败 266 秒——用户在三条路上都登录到了带“View PDF”的文章页却没下到。查明并修复（2026-10-18）：① Elsevier 官网浏览器 0.0 秒就放弃、不写日志：新开的竞速线程读到了上一篇留下的全局错误（paywall），跳过了 ScienceDirect；改为每个来源开始前清掉本线程的错误。② WebVPN 浏览器经联合登录到了 WebVPN 外的 ScienceDirect 文章页，没找到 PDF 链接后又去开 WebVPN 包装的 PDF 地址，被弹回首页；改为在 VPN 外就开出版商自己的地址，找链接时也认“View PDF / Download PDF”按钮文字，找不到时把页面上像 PDF 的链接写进日志。`find_page_pdf_link` 里的 `page.wait_for_timeout` 也换成 `time.sleep`。待查：CARSI 搜学校时报“Execution context was destroyed”就整条放弃；取消后任务文件仍是 running（13 篇停在等待中）。
- 重测 14 篇（2026-10-18 12:09–12:50，新代码）：2444 秒，成功 11 篇。addma（昨晚失败）26.7 秒由 Elsevier 官网下到，证实“上一篇的错误跑到下一篇”的修复有效。发现的问题按此顺序改：① SSRN 1001 秒：第一阶段的通用官网浏览器卡了 961 秒，第一阶段结束后仍占着唯一的浏览器名额，WebVPN 排队 697 秒，看门狗只关这一步自己开的浏览器（关了 0 个）；② ceramint 134.7 秒：ScienceDirect 的 View PDF 按钮在 15 秒找链接窗口后才出现，转去点机构登录白等约 1.5 分钟；③ Springer 章节 360 秒：官网机构登录成功仍无权限，又去走学校通道、弹 WebVPN 登录（学校没订，WebVPN 结果相同）；④ 开放获取一轮漏掉 OA：Wiley 兜底拼成 `pdfdirect/pdfdirect`，MDPI 没拿到，IOP 会议论文集（全 OA）用 228 秒；⑤ 进度提示不跟实际：“已找到 PDF”时实际在等 Cloudflare 验证，也没提醒用户。①–⑤ 已修（2026-10-18）：① 第一阶段结束、进入学校通道前关掉它开的浏览器，让出唯一的名额；② 官网可见窗口找 PDF 链接前先等网络请求停下（10 秒），找 20 秒，没找到刷新一次再找 10 秒，之后才去点机构登录；③ 官网用学校身份登录成功仍无全文时记下（`LOGIN_NO_ACCESS`），不再走学校通道，失败原因“学校没有订阅”，设置 `school_routes_after_publisher_login`（默认 false）；④ 浏览器兜底拼地址时先匹配最长前缀（`/doi/pdfdirect/` 被截成 `pdfdirect/…`）；MDPI 缩写表补 `ma → materials`、`nano → nanomaterials`（实测原地址 404、新地址 PDF）；IOP 会议论文集直连返回网页（有反爬），只能走浏览器，228 秒花在哪待下次日志（这轮完整日志被重启覆盖）；⑤ “已找到 PDF”改为“找到 PDF 链接，正在打开”，PDF 被拒、转可见窗口时更新进度；点机构登录后遇到人机验证时按“验证”提醒用户并一直等（不再 30 秒后放弃）。另修：SciOpen（10.26599，含 JAC）直链 `sciopen.com/local/article_pdf/<DOI 大写>.pdf`、页面链接认 `/article_pdf/`；跳过后登录状态检查结束即返回，不再进学校通道。

## 2026-10-17：下载流程与 app 的决定和实测

**2026-10-17 用户决定的下一步（按此顺序）**
1. [x]（2026-10-17：`sources.institutional_download` 是单篇下载的第二阶段——先修复学校会话，再走 WebVPN / CARSI / EZProxy / InstSci；批量车道把前两道没拿到的（开放获取除外）逐篇交给它，会话只修复一次；“跳过出版商官网”已撤回。待真实实测）批量下载与单篇下载一样完整：查明 app 下载问题的根源是批量车道调度（≥3 篇）——灰色车道调 `download(_institutional=False)`，机构车道又分不到文献，所以批量下载从不走学校通道、也不做 WebVPN 会话自愈；Agent 以前多是单篇下载（带第二阶段学校通道），所以没暴露。撤掉“出口被拒就跳过出版商官网浏览器”（第一次实测那篇正是官网浏览器 + 用户 Chrome cookie 下到的），只保留“无法通过的验证页立即放弃”。
1b. [~] 下载流程（2026-10-17 用户定；小批量已实现：`pipeline.run_scihub_then_single`，`batch_small_max` 30、`batch_small_workers` 1，1–2 篇的任务也走它；WebVPN 按设置开关加入学校通道——查明单篇下载默认 `use_vpnsci=False`、WebVPN 从未加入；会话修复加锁只弹一个登录窗口；Cloudflare“无法运行”在出版商浏览器和 CARSI 浏览器的等待循环里也立即放弃。大清单分队列未做）：单篇快、批量慢的原因是车道串行（每道车道等整批走完）加上固定等待（竞速 20 s + 迟到 180 s、Sci-Hub 45 s、冷却 60 s），车道本是为几百上千篇设计的。改为：
   - 小批量（≤30 篇，配置项）：第一轮所有文献并发只走 Sci-Hub（纯 HTTP 解 Altcha，不开浏览器；设置里关了 Sci-Hub 则跳过）；没拿到的第二轮逐篇按单篇下载的完整流程（含学校通道），一次一篇（2026-10-17 用户同意：第二轮都要用浏览器，而浏览器只有一个，3 篇并发时排队耗掉各自的竞速时间；实测同类 Elsevier 官网浏览器单篇约 25 秒，与 10-12 的 22–49 秒一致），学校登录加锁、只弹一个窗口；每篇下完立即显示。
   - 第五次实测（2026-10-17，7 篇，3 篇并发的旧设置，约 16.5 分钟，6 篇成功）：Sci-Hub 一轮 3 分钟内拿到 3 篇；第二轮 Elsevier 官网浏览器、RSC 浏览器、CARSI 浏览器各 1 篇。唯一失败的 CRC 电子书（10.1201/9781003075837）学校其实有权限：WebVPN 找到了 T&F 的下载链接 `api.taylorfrancis.com/content/books/mono/download?...type=googlepdf`，但“像不像 PDF 链接”的检查不认这种地址，悄悄丢掉了；日志里的“No valid session”是 WebVPN 步骤结尾的固定提示，与会话无关。修复后同一路径实测 30 秒下到 3.2 MB。另一个耗时点：susmat 已由 CARSI 下到，另一路 CARSI 还在“等你登录 CAS”的循环里（最多 5 分钟），占着唯一的浏览器。修复：等待用户的循环每次检查文献是否已下完，下完就退出（`SourceWaived`，不计为来源失败，也不弹提示）。WebVPN 的下载步骤失败时写明原因（不像 PDF 链接 / HTTP 状态码 / 返回的不是 PDF）。用户决定保留 WebVPN。
   - 第六次实测（2026-10-17，8 篇，逐篇；用户在约 17 分钟、下到 2 篇时停止）：Sci-Hub 一轮 3.5 分钟拿到 ACS、Springer 两篇。修复：① 第一轮之前加“开放获取”一轮，复用车道的 HTTP 快速通道（已知 OA 链接、MDPI CDN、Elsevier API），MDPI 等 OA 文献不再排在 Sci-Hub 和逐篇后面，下到的文件写进 DOI 索引并改名（车道的快速通道原先不写索引，app 看不到）；② T&F / CRC / Routledge 整本书：编著书（books/edit）页面上没有整本下载链接，但 `api.taylorfrancis.com/content/books/{mono|edit}/download?...&type=googlepdf` 通过 WebVPN 能直接拿到 PDF，按 DOI 拼出；单章用同样写法返回网页，未解决；③ SAGE 中国镜像（sage.cnpereading.com，国内网络下 DOI 跳到这里）：PDF 按钮没有链接，真实地址 `/storage/.../<DOI 的 / 换成 _>.pdf` 在页面数据里，不登录也能下（实测 7.5 MB）；原来判为“页面上没有 PDF 链接”，又走 CARSI 让用户登录，自动化浏览器打开 `ds.carsi.edu.cn` 超时（Python 访问 0.3 秒正常，待查是浏览器线路还是拼出的登录地址不对）。另见：单篇下载里浏览器一路失败后仍等其他来源迟到结果最多 300 秒才进学校通道，SAGE 这篇在这里多等了约 2 分钟。
   - 取消只停正在下的那篇、逐篇轮里后面的文献照样开始（d67dfae 修复：任务开始时记下取消代号，变了就跳过没开始的文献）。
   - 用户问为什么这么难（2026-10-17）：根本原因是 LitLoop 的出口不是学校的，订阅文献只能走浏览器 + 登录的 CARSI / WebVPN；其次失败一篇要等满竞速和迟到结果（最多 300 秒）才进学校通道。待用户确认：不开代理时普通浏览器能否直接看 ScienceDirect 订阅全文——能则改路由让 LitLoop 走校园网出口；不能则出口不被认时订阅文献先走 WebVPN 的 HTTP（CRC 书 30 秒），浏览器一路明确失败后不再等迟到结果。
   - **更正（2026-10-17）：出口是学校认可的。** 用户确认：不开代理时普通浏览器的出口 IP 与 LitLoop 相同（219.143.23.107），ScienceDirect 页面显示学校名称。LitLoop 自己的浏览器（`litloop-chrome.exe`）也是这个出口；用同一出口实测 Elsevier 订阅论文浏览器下载 19 秒成功，而 Elsevier API 同时返回 403。结论：API 的 403“Requestor configuration settings insufficient”只说明 Key 没有全文权限，与出口无关。据此撤掉 `egress.py`（API 403 后一小时内学校通道跳过出版商官网）；网络检测的「出版商权限」改为「Elsevier 全文接口」，Key 没有全文权限时只提示、不判失败，也不再说出口不被认可。之前“出口是运营商线路、出版商不认”的判断作废。
   - 等用户操作的“心跳”补全（2026-10-17）：原来只有 CARSI 浏览器等 CAS、出版商官网等机构登录 / 人机验证、批量分组登录会报告“在等用户”；补上 WebVPN 浏览器遇到学校登录页时的等待、`browser_login` 的登录窗口（CARSI 另一条登录路线、EZProxy 都走它）。下载页“请在浏览器中登录”的提示旁加「跳过这篇」（`sources.skip_download`，只停这一篇，状态为“已跳过”，任务结束后可重试）；跳过或取消后不再进学校通道。下一步（待用户同意）：没有窗口在等用户时，迟到等待从 180 / 300 秒缩到 15–30 秒；某条途径已明确失败时不再为它等。
   - 单篇下载顺序重排（2026-10-17 用户定）：① 学校通道不再并发，按 `institutional_order` 逐个尝试：WebVPN 直接下载（保存的登录状态，不开浏览器）→ WebVPN 浏览器 → CARSI → EZProxy，第一个成功就停，同一时间最多一个窗口等用户；InstSci 桥（开放获取、Elsevier API、CARSI、官网、WebVPN 全部重复一遍，还会自己弹登录）不再参与；WebVPN 按设置开关，不再要求调用方显式打开。② 出版商官网全部在第一阶段试：没有专门官网浏览器的订阅出版商（PNAS、Optica、World Scientific、未收录的出版商）用通用官网浏览器；开放获取出版商不用。③ 迟到等待：还有浏览器途径在工作（开页面、走向登录页）时最多等 `race_grace_browser_seconds`（180），都结束后只等 `race_grace_seconds`（15）；有窗口在等用户时照旧等用户。原来固定 180 / 300 秒。④ 第一轮 Sci-Hub 只用 HTTP（`_scihub_http_only`），不占唯一的浏览器名额，真正并行；需要浏览器的留到第二轮。另：测试默认使用临时设置文件（conftest `_isolated_config`）——此前有测试读到用户真实设置，改动后真的打开了 WebVPN 浏览器查假 DOI；隔离后全量测试从 4 分钟降到 76 秒。
   - 第七次实测（2026-10-17 20:26，重试复测（三）没下到的 6 篇，新顺序）：MDPI 开放获取一轮 5 秒；Elsevier 由 Sci-Hub 下到；SAGE 中国镜像从页面数据取到 PDF；CRC 整本书约 3 分钟走完全部途径（旧代码约 9 分钟）仍失败；Routledge 章节、Wiley 由用户跳过。据此修改：① 用户要求不用 Elsevier API 下载全文（Key 没有全文权限）：新设置 `elsevier_api_fulltext`（用户已关），API 返回 403“Requestor configuration settings insufficient”后按 Key 的哈希记 30 天不再请求全文（`elsevier_no_fulltext.json`）；② 用户要求 Sci-Hub 走代理：新设置 `scihub_via_proxy`（用户已开），Sci-Hub、LibGen 的 HTTP 请求和浏览器都经 `host_proxy_url`，代理端口没开时直连；设置页「网络」加「代理软件地址」；③ 超时或连不上的 Sci-Hub 镜像标为不可达，10 分钟内跳过（sci-hub.mksa.top 现在跳到一个不响应的网站，每篇白等 15–45 秒）；Sci-Hub 说“没收录”记一小时，第二轮不再查；④ T&F 整本书：DOI 没解析出 T&F 地址时，从 WebVPN 返回的书页地址（`/books/edit/`）拼下载地址；⑤ 未知出版商的进度显示“正在打开出版商官网”，不再显示 Unknown；第一阶段文案改为“正在查找免费全文和出版商官网”；⑥ 出版商官网只在真正到了登录页时才提示用户（原来点机构登录没到登录页也提示）；点机构登录回到了文章页（Wiley，学校 IP 已有权限）时直接在文章页找 PDF，不再判失败；⑦ CARSI 浏览器文章页没加载出来（chrome-error）时不再要求登录；⑧ 逐篇下载每篇一出结果就在 app 里显示完成 / 失败（原来整批结束才写），“正在处理”跟着切换；下载中每篇未完成的文献都有「跳过」，排队中的轮到时跳过；⑨ 测试默认用临时设置文件（此前一个测试读到真实设置开了 WebVPN 浏览器）。Wiley 经 WebVPN 会被 Cloudflare 拦（403 / 请稍候），对 Wiley 只能靠第一阶段的学校 IP 官网。
   - 第八次实测（2026-10-17 20:50，重试三篇难例）：CRC 整本书经 WebVPN 直接下载拿到（第一阶段打开 T&F 官网白耗约 75 秒：T&F 服务器从这里每次响应约 12 秒，学校 IP 直连整本书下载地址 403）；Wiley 在用户点了人机验证后从文章页抓到 PDF；Routledge 章节失败（T&F 不提供单章 PDF）。据此：T&F 章节改为下载所在整本书（用户选的 A），文件名加“（整本书）”，下载页写明本章页码；官网和 CARSI 遇到 Cloudflare 验证时提醒用户并最多等 5 分钟（原来不提醒、只等 1 分钟）；持久化浏览器不再弹“要恢复页面吗”；Sci-Hub 进度改为“Sci-Hub 有下载链接，正在核对”（曾拿到占位文件却显示已找到）；下载页的打开、跳过改为带提示的图标按钮，跳过只在鼠标移到该行时出现。待用户确认：T&F 书（10.1201、10.4324）第一阶段不开官网，直接进学校通道。
   - 界面字体与按钮（2026-10-17 用户选 C，主题色保持北航蓝 #004CD8、需要用户操作的提示用琥珀黄）：中文改用随 app 打包的思源黑体（`@fontsource-variable/noto-sans-sc` 5.3.0，OFL，按字符范围分片按需加载，共 4.6 MB），英文仍是 Geist；小字 12.5px；按钮统一 32px 高、13–13.5px；圆角 8px；评分选中用主题蓝并去掉按钮里的快捷键数字；筛选页顶部标题单行省略。
   - 测试隔离（2026-10-17）：测试改用独立的临时数据目录（conftest 在导入 litloop 之前设 `LITLOOP_DATA_DIR`）。此前测试把进度文件写进用户的 `~/.litloop/progress`，用户正在运行的 app 读到了测试里的“请完成人机验证”提示。
   - Sci-Hub 一轮慢（2026-10-17 实测 20 篇老文献）：所有文献的镜像请求共用 3 个线程（本为限制浏览器数），4 篇 × 3 个镜像排队，输掉的备用镜像还占着线程等 30 秒超时，备用镜像 45 秒期限又把排队时间算进去，日志满是“backup domains timed out”。改为纯 HTTP 时用单独的 12 线程池（`scihub_http_workers`），第一轮同时查 6 篇（`scihub_round_workers`）。镜像经代理 2–5 秒可达，sci-hub.mksa.top 直连超时、经代理正常。用户指出老文献多数 Sci-Hub 有，另建「下载测试 · 新文献（2024–2026，20 篇）」测出版商官网和学校通道。
   - Sci-Hub 测速（2026-10-17 用户定“谁快用谁”）：实测经代理 sci.bban.top 传 PDF 56 KB/s、直连 175 KB/s，一轮的时间大多花在传 PDF 上。新设置 `scihub_route`（直连 / 只走代理 / 谁快用谁，替代设置页的 `scihub_via_proxy`，旧值仍认）；选“谁快用谁”时，Sci-Hub 一轮之前用一篇已知文献（`scihub_speedtest_doi`）在前 5 个镜像 × 两条线路上同时走完整流程（打开、解验证、取链接、下 PDF，40 秒为限），最快的镜像排第一、整轮用它的线路；结果记一小时（`scihub_route.json`），所选镜像连续失败 3 次作废、下批重测（`sources/scihub_speed.py`）。第一轮同时查的篇数 6 → 10（`scihub_round_workers`，自定值）。同样 20 篇老文献：237 秒 14 篇 → 137 秒 16 篇（测速选中 sci-hub.vg 直连，125 KB/s，单篇 8.5 秒）；最慢的一篇是 MDPI 开放获取，正式流程里在开放获取一轮就拿到。日志加时间戳，用于查 about:blank 久等。
   - 第九次实测（2026-10-17 22:20，新文献 20 篇；下到 3 篇后第三篇卡住、用户取消，剩 17 篇待重测）：开放获取一轮 110 秒（查 OA 链接加一篇 Wiley 用浏览器兜底等了 30 秒超时）；测速本该几秒，却等满 40 秒（sci-hub.bz 连不上），收尾时 Windows 删临时目录报“文件被占用”，结果整个作废、仍用默认线路。修复：所有镜像同时起跑，第一个下完的就是最快的，拿到即停；输掉的在后台下完再删临时目录。用户要求全测时每段都记时间：日志加 `[timing]` 行——查 OA 链接、开放获取一轮、测速、Sci-Hub 一轮、逐篇每篇、整张清单；单篇里每个来源的耗时、等浏览器名额的时间、第一阶段、会话检查、学校通道逐步。Sci-Hub 一轮 106 秒 0 篇（这批 2024–2026 的文献 Sci-Hub 都没收录）→ 用户定：2024 年及以后发表的文献自动跳过 Sci-Hub（`scihub_skip_from_year`，默认 2024，设置页可改；年份取自 app 文献列表或 DOI 里的年份，不知年份的照查；SciBban 是 Sci-Hub 镜像，一并跳过，LibGen 保留）。第三篇 jmst.2024.02.008 在学校通道「WebVPN 浏览器」卡了 10 分钟以上：线程栈显示下载线程在等浏览器回一条指令；单独重放这一步（记下每次跳转）看到 ScienceDirect 经 WebVPN 打开文章后 302 到 WebVPN 外的 auth.elsevier.com 机构登录，再经学校 IdP 回到 ScienceDirect 首页（不是文章），全程走直连，其间 id.elsevier.com 引用的 unpkg.com 脚本连接超时；重放时没有卡死，app 里卡死的那个调用未能定位（py-spy 看不到 greenlet 里的栈）。修复：① 学校通道每一步加看门狗，`institutional_step_seconds`（240 秒，自定值）内没结果就关掉这一步开的浏览器，卡住的调用随之报错，进入下一条通道；用户正在登录（心跳新鲜）时顺延；② WebVPN 浏览器被出版商带出 WebVPN（既不是学校域名、也不是学校登录页）时直接放弃这条通道——那是官网通道，第一阶段已试过。用户点「取消下载」后取消日志写着关掉了 2 个浏览器，卡住的调用随之报错，查明是 `wait_page_settled` 里的 `page.wait_for_timeout(1000)`——停在加载中的页面上它不返回（改为 `time.sleep`）；但之后 `browser.close()` 又在已断的驱动上空转（线程栈 active+gil，app 一直显示加载中）：patchright 的同步等待在驱动断开后只会原地空转。修复：同步等待发现驱动已断就报错退出（`browser_backend._guard_dead_driver`）；被强制关掉的浏览器不再调用 close。测试 `test_dead_driver`：修复前 60 秒超时，修复后 2.4 秒。
   - 大清单（>30 篇）：先摸底分组（开放获取 / Sci-Hub 有 / 需学校权限，后者按出版商细分），app 里分成几个队列，每个队列写明怎么下（如“连接校园网或登录 WebVPN 后下载”），用户逐个队列点下载。
2. [x]（2026-10-17：`screening.history` / `GET /api/history`，筛选页右上角「历史」；按问题分组、每次检索的已评 / 高 / 中 / 已选，展开看编号的最终清单，导出 BibTeX / RIS / DOI 列表到 `<保存位置>/exports`（`export.py`，只用记录、不联网），一键加入下载。另：下载队列跨会话去重、arXiv 编号与 arXiv DOI 视为同一篇、队列和下载详情加编号）筛选历史：会话列表，每个会话最终选中的论文清单，可导出（BibTeX / RIS / DOI 列表）、加入下载、推送 Zotero。
3. 在 app 里发起检索：用户选“像对话一样交给 Agent”——app 后台以无界面模式调起 Claude Code（`claude -p`，本机 2.1.239，litloop 与 zotero 的 MCP 已连），把表单拼成指令；表单有问题和筛选标准、年份、期刊分区、种子（DOI 或 Zotero 分类）、模式（快速 / 深度）与规模（每批篇数、最多读多少）。Agent 命令做成配置项，耗用户的 Claude 额度；app 显示 Agent 的进度和每步动作。

（2026-10-17 更新）

0. ~~重启 Claude~~、~~验证两项 10-16 的检索改动~~（2026-10-17 完成，结果见上方稀土测试集一节）。
1. 实测一次 app 里的真实下载（可能弹出浏览器窗口，等用户方便时）。
2. **可视化 app 1 期继续**（`app/`，开发方式见 `app/README.md`；改了 Python 后台要重启窗口，界面改动会热更新）：
   - ~~进度条在真实深度检索里验证~~（已完成）；学校认证（选学校、弹窗登录、试下载一篇订阅论文）；代理直连设好后的验证；
   - 之后：深度链接、完成通知、安装包（PyInstaller 打包后台）与自动更新；再进入 2 期任务层（新建检索表单、确认点、后端 A）。
3. 阶段 2 剩余（穿插）：~~Zotero 当种子~~、~~停止时估计剩余~~、提速、深度检索 → 下载 → Zotero。
4. ~~评估 easyseek 调 OpenAlex 用 `search=` 与 `title_and_abstract.search` 的取舍~~（2026-10-17：四个测试集共 34 组检索词，各取前 200 条，`search=` 命中目标 21/4/3/18 篇，`title_and_abstract` 16/3/2/18 篇，后者没有一篇是前者漏掉的；后者对多词检索很严，每组中位结果数 7/2/57/53 对 107/1305/298/616。两者同价（每次 10 积分），共花约 680 积分。结论：easyseek 保持 `search=`；LitLoop 只在按被引排序时用 `title_and_abstract`（防止全文匹配把无关名篇排前），维持不变）。

## 2026-10-15 ～ 10-17：阶段 2 补验收与后续优化

**阶段 2 补验收**（2026-10-15，用户要求先把阶段 2 做扎实再做 app）

- [x] 候选池过滤非论文类型（数据集、勘误、书评、社论等）。统计两个候选池：目标全部是期刊论文；能过滤的每池只有十来篇，书章、学位论文、会议论文保留（SiC/Al 有会议论文评为高），交给模型排序。那篇疫情数据集在 OpenAlex 里被标成学位论文，过滤不掉。
- [x] 离线重放没有 Scopus 的情况（去掉来自 Scopus 的摘要，属最坏估计）：PDC 候选池 1840 篇中 413 篇的摘要、26 篇目标中 16 篇的摘要来自 Scopus；剩余目标进前 60 名的篇数在 4 个检查点从 3/4/3/2 降到 1/1/2/2。SiC/Al 那次实跑早于补摘要修复，Scopus 只补了 128 篇，影响较小。结论：Elsevier Key 对深度检索很重要，写进 README。
- [x] 用最终代码实跑 SiC/Al、PDC，并第一次跑 SiBCN（检验参数是否只对前两个测试集有效）。
  - [x] SiC/Al（2026-10-15，核心词改为 `Al|aluminum|aluminium SiC`、加一组缩写检索词；评过的沿用评分）：读到 30 / 60 / 90 / 120 / 150 篇找到 7 / 11 / 12 / 15 / **18** 篇（上一次 8 / 11 / 15 / 16 / 17）。1991 年那篇没有摘要的经典论文找到了；微波载板那篇第一次进了候选池并补到摘要，排第 131 名。中段变慢：“Al”太宽，候选池从 4489 涨到 7309 篇，相邻课题多了。150 篇时停止规则未触发（仍有新目标），合理。
  - [x] PDC（2026-10-15）：读到 30 / 60 / 90 / 120 / 150 篇找到 9 / **17** / **21** / 22 / 23 篇（上一次 9 / 15 / 18 / 24 / 24），前中段变好，最终少 1 篇；150 篇时最后一批高相关 10%，未到最好一批的 1/6，没有建议停止。
    - 漏掉的 3 篇都缺摘要：这次 Scopus 只补到 115 篇（上一次 413 篇）。原因有二：“各来源同时问”时每篇也问 Crossref，批量补摘要的请求把 Scopus 挤到预算之外；更关键的是并发太高触发 Scopus 每秒限流（429），代码把 429 当成周配额用完，整轮不再问 Scopus。
    - 修复：Elsevier 的 DOI 只问 Scopus（Elsevier 不向 Crossref 提交摘要），Scopus 请求排在前面；429 时只有剩余配额为 0 才停，否则等待重试；Scopus 最多 5 个并发。同样 150 篇缺摘要的文章（114 篇是 Elsevier 的）30 秒内：旧代码 62 篇、出问题的版本 24 篇、修复后 90 篇。
    - 修复后在这次会话上内存模拟：两轮补摘要后漏掉的 3 篇中 2 篇补到摘要，读完 120 篇时排第 15、67 名左右；第 3 篇排名太后还没轮到。
    - 规则与目标清单又有一处不一致：SiC/莫来石气凝胶的前驱体是邻苯二酚-甲醛/二氧化硅/勃姆石杂化气凝胶（无先驱体聚合物）。2026-10-16 用户决定算目标；规则补充为“论文自称 PDC 路线的杂化凝胶碳热气凝胶也算目标”。
    - SiC/Al 候选池变大的原因（2026-10-16 核对，用户原以为三种写法一般都会出现）：第三次比第二次多出 3377 篇，其中 3202 篇来自引用路线。有摘要的新文章里 863 篇只写“Al”、560 篇写了 aluminum/aluminium（后者是拆分查询取全和种子不同带来的）；另有约 1500 篇没有摘要，其中 731 篇标题只有“Al”。所以在这个领域只写缩写的文章确实很多，多数是 SiC/Al 的其他制备路线（搅拌铸造、粉末冶金、锻造等），也混进了 Al2O3 这类噪音（OpenAlex 把“Al 2 O 3”拆成了“Al”）。
  - [x] 新拼写处理后重跑（2026-10-16，评过的沿用评分）：SiC/Al 读到 30 / 60 / 90 / 120 / 150 篇找到 7 / 13 / 16 / 16 / 17（上一次 7 / 11 / 12 / 15 / 18），PDC 9 / 14 / 19 / 22 / 23（上一次 9 / 17 / 21 / 22 / 23）。两次之间差两三篇，属于正常波动，单次实跑判断不了改动好坏——要靠评测工具多跑几次。SiC/Al 少的是 1991 年那篇无摘要的经典论文（这次排第 2124 名）；PDC 剩下 3 篇都补到摘要、排第 4、19、92 名。补摘要覆盖率：推给 Agent 的文章约九成有摘要（SiC/Al 91%、PDC 88%），缺的主要是 Elsevier（Scopus 也没有）、Springer、SSRN。重跑后半程每轮扩展约 20–40 OpenAlex 积分，建池是大头；今天全部实跑合计约 2800 积分。
  - [x] 补摘要的三个漏洞（2026-10-16，用户问“为什么连 Scopus 都没有”时查出）：两次重跑里推给评分、却没摘要的 22 篇 Elsevier 文章，直接问 Scopus 有 17 篇其实有摘要，只有 5 篇是真没有（未收录的中文期刊英文版、百科条目、短文、书章）。原因：①出批时补到的摘要没写回候选池，Agent 看得到（最后一批约 93% 有摘要）但排序模型用不上；②每轮 150 篇、预算固定 30 秒，Scopus 按限速每秒约 3 篇问不完，没轮到的也被标成“试过”；③超时、限流也被标成“试过”。修复：写回候选池；来源返回 None 表示没问成、空串表示明确没有，只有各来源都明确答复才标“试过”；预算按篇数算（约每 3 篇 1 秒，30–90 秒）。实测这 22 篇 8 秒内补到 17 篇、5 篇判定为真没有。
  - [x] 外部接口限额集中整理（2026-10-16，用户要求；“限额千万不要随意揣测”）：遍历代码里 92 个外部域名，写成 `docs/API_LIMITS.md`，每个数字标官方（附链接）、实测、自定或未核对。按官方文档修正：arXiv 每 3 秒 1 次、单连接（原为 2 并发）；CORE 每 10 秒 5 次（原为 3 并发、每秒 2 次）；OpenAIRE 无 Token 每小时 60 次（一小时用满 55 次后跳过）。NCBI 文档需人机验证、Europe PMC 和 DOAJ 未查，标为未核对；Semantic Scholar 批量接口上限未核对。补摘要用的 Springer Key 改名 `springer_meta_api_key`，避免和下载用的 TDM Key `springer_api_key` 冲突；联系邮箱并入原有的 `email`。AGENTS.md 写明限额规矩。
  - [x] NCBI 核对（2026-10-16，用户代读 E-utilities 页面）：LitLoop 实际用的是 PMC ID 转换接口，不是 E-utilities。旧地址已 301 跳转，改用新地址 `pmc.ncbi.nlm.nih.gov/tools/idconv/api/v1/articles/`，按文档带 `tool=litloop` 和用户的 `email`；该接口官方没写每秒上限。写入 `docs/API_LIMITS.md`。
  - [x] PMC 合规（2026-10-16，用户选“只在单篇下载时用”）：PMC 规定自动获取只能走 Cloud / OAI-PMH / E-utilities / BioC API，其他方式批量获取禁止。`download()` 加内部参数 `_batch`，批量下载及其机构回退、预印本回退都带上，PMC 来源在批量时跳过；Europe PMC 不受影响。
  - [x] 未核对的限额补核（2026-10-17）：Semantic Scholar 批量接口一次最多 500 个 ID（官方 OpenAPI，代码已符合）；DOAJ 每秒 2 次、可排队 5 个（现有控制在限额内）；Europe PMC 文档页有人机验证，用户代为读取：官方没有公布限额数字。详见 docs/API_LIMITS.md。
  - [x] Springer Nature Meta API 作为可选摘要来源（2026-10-16 接入，用户已注册、Key 存本机配置）：只用于 Springer Nature 自家 DOI（10.1007、10.1038、10.1186、10.1023、10.1057、10.1140），每天上限可配（默认 500）、每分钟约 90 次。实测重跑里缺摘要的 15 篇 Springer 订阅文章，Meta API 有 14 篇（含 1991 年那篇漏掉的目标）。
  - [x] SiBCN 观察集（第一次，经 MCP 起步）：读到 150 篇找到 2/5 篇，另 3 篇是聚硅氮烷/酚醛气凝胶、致密 SiOC、多孔 SiC(rGO)，都不是 SiBCN。原因是 Agent 把核心词只写了 SiBCN，而课题是“PDC，尤其 SiBCN”——检索设计问题，不是排序问题。技能文档已补：核心词要覆盖整个问题范围，重点交给评分。这个领域相关文章极多（150 篇里 109 篇评为高），150 篇时停止规则未触发，合理。
- [x] 新测试集「稀土药物载体」（2026-10-16，用户提供 Zotero 分类「非晶稀土材料-药物递送载体」，13 篇）：写成 `tests/acceptance/rare_earth_drug_carriers.json`，Zotero 里在「LitLoop 测试/深度检索 · 稀土药物载体」下也放了一份（只加分类归属）。课题范围由用户定为“载体本身含稀土，非晶优先”：非晶的评高、晶态的评中，稀土只做发光或造影、药由别的材料装载的算边缘。种子 3 篇（2008 年 Tb 配位聚合物、2011 年 Eu 掺杂无定形磷酸钙、2022 年镧系核苷酸自组装），目标 9 篇；Yuan 的学位论文没有 DOI、OpenAlex 里查不到，单列不计。这是第一个离陶瓷较远的测试集，用来检验参数是否只适合材料领域。
  - [~] 第一次实跑（2026-10-16，经 MCP 工具，同时测 MCP）：读到 80 篇时暂停——OpenAlex 当天额度只剩 830 积分。MCP 本身正常（start / rate / next，按编号存评分无误）。读 80 篇找到目标 1/9（Tb 掺杂磷酸钙），另 5 篇已在候选池；评为高 11 篇，多是稀土掺杂硅酸钙、生物玻璃，不在用户收藏里但符合课题。第二批起漂向同一课题组的无稀土无定形磷酸钙。这个课题跨度大（配位聚合物、磷酸钙、玻璃、CeO2、碳点、纳米金刚石），各类之间共同参考文献少，引用路线难以跨类。
    - 发现并修复：①额度失控——一次花约 5900 积分，最贵一轮 2184 积分、6 分钟，候选池 52668 篇（51595 篇来自“引用种子参考文献”路线）；改为单篇参考文献超上限只取 200 篇、每轮最多 800 积分、当天剩余不到 1000 积分不扩展，没查的参考文献按重要性留到下一轮。②本地记账漏记关键词检索（实际 9170、本地记到约 6500），`search.py` 的 OpenAlex 请求也计入；提示里 OpenAlex 的单位改为积分。③Angewandte 德文版 `10.1002/ange.*` 与国际版 `anie.*` 合并。
    - 爆炸的根本原因（2026-10-16 核对候选池）：核心词写成两条 `[稀土…, drug…]`，本意“稀土并且药物”，但代码把每条单独检索再合并，实际成了“稀土或药物”。引用路线进来的 51595 篇里，只提药物、不提稀土的 27214 篇，两者都提到的仅 534 篇。PDC 测试集也是两条，但两条都含 aerogel，所以没暴露。另外 La、Ce、Er、Eu 等符号会匹配法语 la、ce，德语 er，EU 等普通单词（检索不区分大小写），候选池里单 Eu 就出现 4567 次。
    - [x] 修复（用户要求改代码逻辑，不只改文档）：多条核心词改为“并且”——每条是一个必须出现的概念，合成一次检索，`|` 仍表示同一概念的不同说法；每组参考文献只查一次，积分也减半。与普通单词同形的元素符号（as、at、be、ce、er、es、eu、he、in、la、no）开局时在 `core_term_notes` 里提醒写全称。三份技能文档同步。PDC 测试集核心词改成等价新写法 `["aerogel", "SiOC|…|polymer-derived|preceramic|…"]`；稀土测试集改为两条“并且”的概念、去掉易撞车的符号，已评的 80 篇导出为 `rare_earth_drug_carriers.judgments.json`（高 11 篇）。
    - [x] 验证（2026-10-17，MCP 服务器 9:09 启动，已是新代码）：
      - PDC 评测工具重跑：读到 30 / 60 / 90 / 120 / 150 篇找到 9 / 14 / 19 / 23 / 25 篇，与 10-16 完全相同，512 积分。核心词改写成“并且”不改变结果。
      - 稀土测试集用评测工具 `--stop-on-unjudged` 重新开局（遇到没评过的就停下补评再重跑，共补评 102 篇，判断文件现 182 条：高 22、中 11、低 98、不相关 51）：候选池 1826 → 3762 篇（上次按“或”爆到 5 万多篇），每次重跑约 500 积分；读到 30 / 60 / 90 / 120 / 150 篇找到 1 / 1 / 1 / 2 / 2 篇目标（Tb 掺杂磷酸钙、La 掺杂磷酸盐玻璃）。
      - 稀土这个课题仍然难：漏掉的 7 篇里 3 篇不在候选池（CeO2/纳米金刚石、2026 年的新文章等），其余排在第 292–2027 名。读到的 150 篇中 22 篇评为高，多是稀土掺杂的生物玻璃、无定形磷酸钙载药，符合课题但不在用户收藏里；排序很快被生物玻璃这一类占满，配位聚合物、空心氧化物等其他类别上不来。停止规则未触发。
      - 待定：类别多、彼此引用少的课题怎么照顾到每一类（例如按类别分配名额、让 Agent 给每类单写检索词），先记下，不急着改。
      - 每次重跑排序有小波动（补到的摘要略有不同），所以会零星冒出一两篇没评过的。
- [x] 通过 MCP 工具真实走一遍深度检索（2026-10-15，SiBCN 测试集，start → 存评分 → next）。发现并修复：
  - 一批 30 篇的返回有 57–59KB，Claude Code 把它存成文件、不显示在对话里。改为默认每批 20 篇、摘要最长 1200 字、作者只留前 3 位、来源说明只留前 2 条。
  - 存评分要把每篇的 DOI、标题逐篇抄回去（一批约 8KB）。新增 `litloop_deep_search(action="rate", ratings=[{n, relevance, reason}])`，按编号存，标题、DOI、年份、期刊由插件补；编号或相关度不对时整批不存；返回漏评的编号。工具总数仍为 20。技能文档同步改写。
  - `next` 一次约 2 分 20 秒（17 篇新种子、新增 755 篇：扩展 101 秒、补摘要和排序 34 秒），没有超时，但 Agent 只能干等。
- [x] README 新增「依赖与 Key」：OpenAlex、Elsevier（强烈推荐）、发现引擎、Zotero、Chrome、学校账号、Semantic Scholar（审批严）、期刊分区插件，各写用途、没有时的影响、怎么获取。
- [x] 缩写与拼写变体（2026-10-16，用户：不只是化学元素，其他术语也要考虑缩写和变体）：新增 `spelling.py`，英美拼写由插件自动处理。用户要求找成熟方案后（2026-10-16），由手写规则改为 VarCon（SCOWL 的英美加澳拼写对照库，宽松许可，需随附版权声明）：`scripts/build_spelling_table.py` 从 varcon.txt 生成两张压缩表（约 150KB，13357 种拼写→美式、12651 个美式词的英式写法），随包分发，许可声明在 `data/VARCON-LICENSE.txt`、NOTICE 已注明。相比手写规则不再误伤（precise）、能处理不规则词（tyre/tire、programme/program、mould/mold）；VarCon 把 aerogel 当英式、airgel 当美式，科研写法不对，列入例外。实测 OpenAlex 不会合并两种拼写（2020 年 aluminum 20869 篇、aluminium 8278 篇、OR 28282 篇）。用法：核心词自动展开成 OR，检索词匹配和排序模型的词特征先统一成美式拼写；缩写、化学式、同义词（PDC|polymer-derived、EMW|microwave、MMC|metal-matrix 等）需要领域知识，由 Agent 用 `|` 写，技能文档已写明并给例子。
- 阶段 2 后续优化（2026-10-16 用户同意，按此顺序；可视化 app 筛选页之前或穿插进行）：
  1. [x] **评测工具进仓库**：把三个测试集已有的 600 多条评分存成标准判断（qrels），写 `scripts/eval_deep_search.py`，用存下的评分模拟完整筛选，自动给出“读到第 N 篇找到几篇”，只有没评过的新文章才需要补评。之后每项改动都先过它。（进行中，2026-10-16：三个测试集的评分已导出为 `tests/acceptance/<set>.judgments.json`（633 条），检索参数存进测试集的 `deep_search` 字段，`scripts/eval_deep_search.py` 已写好：按存下的参数重跑、用标准判断自动评分、没评过的默认按低并计数、输出曲线和 OpenAlex 积分；2026-10-16 实跑 PDC 验证通过：读到 30 / 60 / 90 / 120 / 150 篇找到 9 / 14 / 19 / 23 / **25**/26 篇，漏的 1 篇在候选池排第 195 名；150 篇里 15 篇没评过、按低计；花 476 OpenAlex 积分、5 分半钟。比人工评分的实跑便宜很多，以后每项改动先跑它，并多跑几次看波动）
  2. [x] **作者特征**（2026-10-16 离线比较，不采用）：特征为“和其他评为高的文章或种子共有的作者数”（排除自身，避免假信号）。剩余目标进前 60 名的篇数，四个检查点合计：现模型 SiC/Al 7、PDC 9；加作者特征（权重 1）10、2；（权重 2）6、3。SiC/Al 有帮助，PDC 明显变差：PDC 的高产课题组同时发大量致密 PDC、电池负极文章，作者特征把它们一起推上来。另外候选池里 OpenAlex 记录只存前 3 位作者。以后若要再试，可只用通讯作者或限定与多篇高相关文章共有。
  3. [x] **宽泛缩写的噪音**（2026-10-16 离线分析，不改）：用两个测试集最近一次重跑的候选池，看“一组检索词都没完整命中”（`query_hits==0`）的文章能不能直接去掉。
     - SiC/Al：候选池 7385 篇，其中 2270 篇零命中，里面没有目标、高、中；但推给评分的 180 篇里零命中的只有 1 篇——排序模型已经把它们压到后面，去掉也省不了阅读量。
     - PDC：候选池 1803 篇，其中 1232 篇零命中，含 1 篇目标（Si/O/C/N 四元气凝胶）、2 篇评为高；推给评分的 180 篇里 48 篇零命中。硬过滤会误伤。
     - 耗时：两个测试集每轮排序都是 30–55 秒，候选池 7385 篇和 1803 篇差不多，时间主要花在补摘要（预算按篇数、30–90 秒），不随候选池大小明显变化。
     - 结论：噪音不占阅读量、也不明显拖慢，不加过滤。宽泛缩写只在标题匹配的办法同样会误伤只在摘要里写全称的目标，不做。
  4. [x] **用 Zotero 里已有的文献当种子和正例**（2026-10-17）：`litloop_deep_search(seed_collection="分类路径或唯一分类名")` 读分类里有 DOI 的文献并入种子（可与 `seeds` 同用，去重），返回 `seed_collection` 报告用了几篇、几篇无 DOI；读取走 zotero-mcp 的本地接口，只读（SiC/Al 22 篇约 2 秒）。三份技能文档和 README 已写。种子多时引用扩展按现有额度上限分几轮。还没在真实课题上跑过一次完整深度检索。
  5. [x] **停止时估计还剩多少相关文章**（2026-10-17：改按每批高相关比例外推，见下方 `remaining_estimate`；模型分数没有校准，不用它）。
  - [x] 接口用量监控（2026-10-16，用户要求以官方文档为准）：新增 `quota.py`，按 OpenAlex、Elsevier、Crossref、Semantic Scholar 官方文档设置各自的并发和每秒请求数（OpenAlex 有/无 Key、布尔运算符超过 5 个的检索、Crossref 有/无联系邮箱各一套），记录每天的请求数、OpenAlex 实际扣的积分、被限流次数和剩余额度，存 `~/.litloop/api_usage.json`；`litloop_diagnostics(check="quota")` 查看；深度检索每轮记录花费的积分，返回 `api_warnings`。按文档修正：Scopus 额度用完以 `X-ELS-Status: QUOTA_EXCEEDED` 判断；OpenAlex 带关键词检索每次 10 积分（实测），去掉“先问总数”的多余请求；没有 OpenAlex Key 时改用页码翻页（实测与游标结果一致）并提醒；联系邮箱沿用原有配置 `email`（原先一度另加 `contact_email`，2026-10-16 合并），Crossref 带上即进入礼貌池。README 依赖表换成官方数字，技能文档加“首次使用”引导。
  - [x] OpenAlex 限流与每日额度（2026-10-16 重跑时两个会话并行触发 429，整轮扩展失败）：Key 每天 10000 次请求；被限流时按 Retry-After 或逐次加长等待、最多 6 次；额度用完时直接说明多久后重置；每轮记录剩余额度（`openalex_remaining`）。README 写明额度。
  6. [ ] **速度**：扩展与补摘要并行；OpenAlex 查询按参考文献缓存，重跑和续跑不重复请求；给出进度。
  7. [ ] **深度检索之后的流程**：在深度检索会话上走通选择 → 批量下载 → 按分类推送 Zotero（可随 app 筛选页一起做）。
  8. [x] **评分边界说明**（2026-10-16）：三份技能文档写明分档规则（对象/方法/关注点都符合为高，对象同方法异或直接相关综述为中，相邻体系或泛泛综述为低），拿不准时往高评，用户定过的规则优先。同时 `skill/SKILL.md` 补上深度检索流程、工具数改为 20（Antigravity 完成，已审）。
- 之后再做：扩展与补摘要并行；技能文档写明边界情况的评分；~~停止时估计剩余相关文章数~~（2026-10-17：`remaining_estimate`，对每批“高”的比例做几何衰减拟合（对数最小二乘，近批权重大），外推剩余批次之和；模型分数没有校准，所以不用它。实测：PDC 三次实跑在 150 篇处估计还有 5.5 / 12.4 / 7.1 篇高，已知漏掉的高相关至少 2 篇（另有 12 篇没评过），估计偏高，文字写明“粗略外推”；稀土测试集比例仍在上升（衰减系数 0.98–1.21），只给下一批估计，不给总数）；`skill/SKILL.md` 补深度检索。

## 待办池里已完成的

### 代码审查遗留（2026-10-15）
- [x] API Key 读取改为“配置优先，环境变量兜底”（`OPENALEX_API_KEY`、`ELSEVIER_API_KEY`），保存配置时不会把环境变量里的 Key 写进文件（2026-10-15，Antigravity 实现，补了写盘保护）。
- [x] DOI 规范化统一成一个函数（2026-10-16，Antigravity 实现、已审：`identifiers.canonical_doi`，用于筛选记录和深度检索）（去前缀、URL 解码、转小写），各模块共用；目前各自转小写，未发现实际出错。
- [x] 删除无引用的 `src/litloop/mcp_server.py`、`build_core.py`（2026-10-15）。`_cli_core.py`（命令行注册在这里）和 `camofox.py`（`sources/publishers.py` 的下载兜底在用）**仍在使用**，不能删。
- [x] 深度检索跨批次按编号选文章时，让 Agent 用 DOI 选（技能文档已写明 decide 优先传 DOI；评分改为批内编号的 `rate`，不受重排影响）（编号会随筛选记录重排变化；`decide` 已支持 DOI，并拒绝对不上的编号）。

### 可视化 app
- [x] 界面库与风格（2026-10-15，用户确认）：React + shadcn/ui + Recharts，筛选页示意的风格可以，细节见 CHARTER。
- [x] 第一版（2026-10-17 核对：桌面 app 的筛选页已实现）：本地网页的筛选页。显示每批文章、相关度和理由；勾选、排除写回筛选记录；显示深度检索进度（已读篇数、每批高相关比例、停止建议、必找文章状态）。阶段 2 数据格式稳定后开始。
- [x] （2026-10-17 核对：已由 `store.py` 统一）筛选记录、候选池的读写统一成一组函数并加文件锁，Agent 和 app 同时写时不互相覆盖。
- [x] 初次使用引导与设置页（2026-10-16 用户提议并确定，设计见 CHARTER；2026-10-17 完成：引导复用设置页的分组和检测，按 检索接口 → 筛选 → 下载 → 网络 → 学校认证 → Zotero 逐步，每步写明为什么要设、可跳过；首次打开 app 全屏显示，完成或跳过后写 `onboarding_done`；已填过 Key 或改过联系邮箱的人视为已设置，不再弹出；设置页可「重新运行引导」。CHARTER 里的浏览器、可选组件两步没有对应设置项，暂未放进来；学校认证的“弹窗登录并试下载一篇”和 Zotero 的“写入并删除测试条目”需真实操作，待与用户一起做）：逐项引导填写 Key、联系邮箱、学校、Zotero，每项可测试；设置页可改，含接口额度面板。

### 维护
- [x] **真 Chrome 副本自动跟随**（2026-10-15）：启动浏览器前比较系统 Chrome 与副本的版本号，系统更新了就先复制到临时目录再对调，正在运行时跳过；配置项 `browser_copy_follow_system`，默认开启。本机副本当时已落后（.39 → .41），已更新。
- [x] Sci-Hub、LibGen 等灰色来源默认关闭（2026-10-16 用户决定），由首次引导让用户选；`litloop get` 不再写死开启、改为遵从配置。
- [x] 保存配置时只写用户改过的项（2026-10-16）：现在 `save_config` 把所有默认值一起写进文件，默认值一旦被固定，以后改默认值对老用户不生效（如本机 `scihub_enabled` 仍是 true）。
- [x] **新版本提醒**（2026-10-17）：`updates.py`，按 `update_check_days`（默认 7，0 关闭）在后台线程检查，结果缓存在 `~/.litloop/update_check.json`，不让任何操作等它。LitLoop、easyseek 读 GitHub main 分支 `pyproject.toml` 的版本（git 标签停在 v1.9.0，不能用），zotero-mcp 查 PyPI 并和它自己 Python 里装的版本比。检索、批量下载结果带 `update_notice`，app 设置页顶部显示。只提醒，不自动安装。实测：zotero-mcp 有 0.15.1（本机 0.14.1），升级前需确认桥接兼容。测试 `tests/test_updates.py`。
- [x] ZCode 插件的技能文档补上“检索与筛选”流程和 Zotero 本地推送（2026-10-15）。
- [x] （2026-10-17：`skills-src/` 为唯一来源——三份模板各写独有内容，反爬必读、首次使用、检索与筛选三段放在 `shared/`，`scripts/build_skills.py` 生成；`skill/SKILL.md` 的深度检索改为引用共享的完整一节，不再另写精简版；发布一致性检查会报出没重新生成或直接改了生成文件的情况。`litloop-sort` 的两份是有意的（`skills/` 那份只是指向 ZCode 版的入口），只改正了 ZCode 版里“灰色来源默认开启”的过时说法）三份技能文档已经分叉，需要合成一份来源、生成其余副本：`skills/litloop/SKILL.md`（简短入口）、`skill/SKILL.md`（完整参考，还写着 17 个工具）、`zcode-plugin/skills/litloop/SKILL.md`（中文操作手册）。`litloop-sort` 也有两份不同的。
- [x] 出版商巡检工作流（canary.yml）改为只手动触发（2026-10-15，用户同意）：GitHub 的美国服务器测不了机构渠道，网络环境也和国内不同；需要时在 Actions 页面手动运行，或在本机运行 `python scripts/canary.py`。


## 2026-10-17：app 主题
- [x] 从 awesome-design-md 里挑风格，用户看了截图对比后决定：浅色套用 Vercel Geist，深色套用 Linear，跟随系统切换；保留北航蓝和琥珀色。英文字体换成 Geist（随 app 打包），中文仍用系统字体。之前 app 没有深色模式，这次补上；同时修了深色下选中的评分按钮看不出高亮。
- [x] 用户试用后的界面修改（2026-10-17）：
  - 保存位置等路径设置旁加文件夹按钮，用系统窗口选择（Tauri dialog 插件，只在 app 窗口里出现）；
  - 输入框和下拉框聚焦时不再有蓝色描边，只把边框加深；
  - 设置页边栏固定 320px、去掉拖动条；
  - 筛选页边栏最小宽度改为 360px，标题过长时显示省略号，「只看本批」开关不再被挡住；
  - 修复切换设置分组后仍显示上一组的检测结果（检测未完成时切换，结果会晚到）。
- [x] 进度条在真实深度检索里验证（2026-10-17，稀土评测实跑时用 app 读进度文件）：各阶段实时更新、剩余时间合理；计数满了还在收尾时原来显示“约还要 0 秒”，改为“即将完成”。
- [x] app 下载页（2026-10-17）：
  - 后台 `download_jobs.py`：app 发起的下载任务在后台线程里跑正常的批量下载引擎，逐篇状态写进 `downloads/<job>.json`（格式见 `docs/APP_DATA.md`）；同一时间一个任务，可取消；app 中途关闭的任务读出时标为“已中断”。`progress_reporter` 加了逐篇监听钩子；app 后台关掉了独立的悬浮进度条。
  - 页面：「待下载」汇总各次筛选里「选中下载」且还没下到的文章，按筛选分组一键下载；下载记录里看逐篇结果、来源、失败原因，打开 PDF 和文件夹（只允许打开任务记录过的 PDF 和文件夹），重试没下到的文章；需要你处理的验证码、登录提示用琥珀色显示。
  - 测试 `tests/test_download_jobs.py`（Antigravity 写，18 项通过）。`.gitignore` 里根目录临时脚本的规则原来不限目录，把 `src/litloop/download_jobs.py` 也忽略了，已改为只匹配根目录。
  - [x] 真实下载实测（2026-10-17，用户在 app 里点的，SiBCN 会话选中的 6 篇：5 篇 Elsevier、1 篇 JAC 开放获取；不在校园网）：新下 2 篇（ceramint.2024.08.002 由浏览器从 ScienceDirect 取得，JAC 走开放获取直链），4 篇 10-12 已下过。Elsevier API 校外返回 403（预期）；Sci-Hub 当天各域名不通或要验证；没走到机构通道，未弹学校登录。发现并修复：
    - 某个车道失败时页面立即标“失败”，后面的车道其实还在试：现在只标“换渠道重试中”，以引擎最后的汇总为准；
    - 保存目录里已有的 PDF（`.doi_index.json` 有记录且文件在）不再进下载队列，新任务里直接标“本机已有”、不交给引擎（这次 4 篇白跑了 Elsevier API、Sci-Hub、浏览器一圈，JAC 还多存了一份）；
    - 「正在处理」显示两遍，改为显示一次、显示标题。
  - [x] 第二次实测（2026-10-17，「下载测试 · 典型场景」：MDPI、Elsevier、Wiley、ACS、Springer、arXiv、本机已有、不存在的 DOI；用户在校内）发现：
    - 用户在校内，但出版商不认：v2rayN TUN 已按进程设直连，LitLoop 的出口是北京的运营商线路（219.143.23.107，AS4847），不是教育网，Elsevier 全文权限 403。网络检测原来只看 TUN 有没有开，现在加了「出口 IP」和「出版商权限」（用一篇 Elsevier 订阅论文查全文权限，只读权限不下正文），不认时指向 WebVPN / CARSI 或校园出口代理。
    - Wiley 走浏览器直接打开官网，Cloudflare 判定自动浏览器“扩展或网络配置不兼容”拦下（不做绕过）。
    - Sci-Hub：Altcha 验证一直能解（1–2 秒）；2024 年的论文是 Sci-Hub 没收录。修复：解开验证后页面没有 PDF 即记“未收录”、不再开浏览器重试；“未收录”不再记为域名失败；所有镜像都说未收录时不再走 Tor。
    - 下载首页看不到正在跑的任务、没法取消：加了「正在下载」提示条（查看、取消下载）。
  - [x] 批量下载从不走学校通道（2026-10-17 查明）：按 DOI 前缀预判时没有出版商会被分到机构通道，快速通道失败只溢出到灰色来源、灰色来源失败就停了，所以 WebVPN / CARSI 在批量下载里从没用上（之前看到的“浏览器下载”是直接开出版商官网）。修复：配置了学校认证或在校园网时，前两道都没拿到的文献（开放获取的除外）继续进机构通道。测试 `tests/test_lane_overflow_institution.py`；待真实实测。
  - [x] 第三次实测（2026-10-17）：Wiley 在灰色来源竞速里由“官网浏览器”打开，Cloudflare 判定不兼容（无可点的验证框），每篇等满 150 秒、整道车道停住，轮不到学校通道。修复：Elsevier API 答复“出口不在学校登记范围”后记一小时（`egress.py`），配置了学校认证时跳过出版商官网浏览器、交给学校通道；“不兼容”这种无法通过的验证页立即放弃，不再等。测试 `tests/test_egress_skip.py`。
  - [x] 第四次实测（2026-10-17）：已走到学校通道，但通道内部先直接拼出版商 PDF 地址、再开浏览器访问官网，WebVPN 排最后；出口不被认可时前两步只会被拒（ScienceDirect CPE00001，浏览器出口 106.39.42.165，与 Python 的 219.143.23.107 不同，都是北京运营商线路）。修复：出口被拒且不在校园网时，学校通道跳过这两步，CARSI 之后直接 WebVPN。另见：批次编号沿用了上次被中断的批次（“Resuming batch … 1 already completed”），下载任务应各用各的批次。
- [x] 文件名格式可选（2026-10-17，用户要求）：`rename_format` 七种——作者年份_标题_DOI（默认，与以前相同）、作者年份_标题、作者 - 年份 - 完整标题（Zotero 式，保留中文和重音）、年份_作者_标题、完整标题、DOI、保持原名；旧的 `auto_rename: false` 等于保持原名，app 里选了格式会把它置回 true。去掉 Windows 禁用字符；判断已下载时仍核对 PDF 里的 DOI，换格式不会误认。测试 `tests/test_rename_formats.py`（Antigravity）。
- [x] 左侧图标栏（2026-10-17，用户要求）：可展开（168px，图标加名称）或收起（只留图标），记住状态；图标栏变宽时列表宽度不变；顶栏隐藏列表时图标栏只留图标。
- [x] 开发窗口重启脚本 `app/scripts/dev-restart.ps1`（2026-10-17，用户嫌后台任务挂太久）：结束旧窗口、后台和本项目的 Vite（按命令行找，netstat 状态名是本地化的），等端口空出来再单独启动 `tauri dev`，约 8 秒返回。另修文件夹按钮没反应：当时开着的是加插件之前的旧窗口；按钮出错时现在会显示原因。
- [x] app 的 Zotero（2026-10-17，用户定：推送时机做成设置、默认手动；默认分类「LitLoop/<检索问题>」；附件体检列出来由用户确认）：
  - [x] 第 1 步：下载记录页推送到 Zotero。`zotero_bridge.py` 加了读分类树、按路径确保分类存在（缺的层级新建）两个操作；`zotero.py` 把调用桥接抽成 `_run_bridge`（子进程不弹窗）；下载任务加 `push_zotero`（单独的锁，可与下载同时进行），逐篇记录新建 / 已有 / PDF 未附上 / 失败，结果写进任务文件，已推送的不重复推；设置加「下载完自动推送」（默认关）、「默认分类」（默认 LitLoop）。页面：推送区可从已有分类选或填新路径。读分类树已对用户真实 Zotero 实测（只读，39 个分类）；真实写入待用户确认后再测。
  - [x] 第 2 步（2026-10-17）：Zotero 页——左栏显示 Zotero、zotero-mcp 是否就绪和推送记录（点开跳到对应下载记录）；右栏「待推送」按下载任务分组，一键推送到默认分类，换分类去下载记录页。后台 `zotero_pending` / `zotero_history`、`GET /api/zotero`。测试由 Antigravity 补。
  - [x] 第 3 步（2026-10-17）：附件检查——Zotero 页选分类后检测（只读，一次列表请求拿全条目和子附件，22 篇约 2 秒），列出「缺少附件」「附件为空」，标出本机有没有 PDF（先查下载任务，再按 DOI 在下载目录里找），默认全选；确认后「重新附加」（空附件先移到 Zotero 回收站，可恢复）或「加入下载」（下载任务带 `zotero_target`，推送时默认推回这个分类）。修复用的 PDF 由后台按 DOI 重新查，不用前端传来的路径。对用户真实库只读检测：8 个分类共 37 篇缺 PDF，没有空附件；修复与下载待用户确认后实测。
  - [x] 只用本地接口（2026-10-17，用户要求）：删掉原项目的 Zotero 网页接口代码（`push_to_zotero` 网页分支、`_metadata_to_zotero_item`、`_upload_attachment` 等，只建条目不传 PDF）和 `zotero_api_key` / `zotero_library_*` 默认配置，`litloop_zotero_push` 直接走 zotero-mcp；Zotero 页显示写入方式（用户这台为本地），不是本地时可一键「授权本地写入」（zotero-mcp 的 authorize，Zotero 里弹对话框）。README 改为只推荐本地写入。
- [x] 界面文案统一（2026-10-17，用户要求）：`docs/UI_COPY.md`（参考 Ant Design 文案规范、中文文案排版指北、微软中文风格指南；用户定：直角引号「」、「推送到」、「检索接口」、状态统一为「失败」），全部界面文字按它改写（Antigravity），`npm run lint:copy`（autocorrect）检查中英文空格与标点。
- [x] 报错翻译（2026-10-17）：`error_text.py` 把下载引擎、zotero-mcp、各接口的原始报错译成「无法<动作>：<原因>。<下一步>。」；文件里仍存原文，app 后台给下载任务、每篇文献和推送结果加 `error_text`，页面显示译文、悬停看原文；Zotero 各接口的报错直接返回译文。前端 `call` 出错时取后台的 `detail`，不再显示 `Error: 404 {...}`；窗口与后台版本不一致（旧窗口没有新接口）时提示重启应用。
- [x] 下载页、Zotero 页去掉第二栏（2026-10-17，用户觉得左栏多余、空荡，给了参考图：标题和标签页同一行、设置放在图标栏最下面）：下载页一页显示——页头是累计已下载 / 失败篇数和「打开保存位置」（`/api/open` 允许打开设置里的保存位置），下面是下载队列和历史记录（可按「全部 / 有失败 / 未完成」筛选），点一行进详情，详情顶部可返回；Zotero 页页头「Zotero｜推送队列 · 附件检查 · 推送记录」，下面一行是 Zotero、zotero-mcp、写入方式的状态。「设置」移到图标栏底部，和「收起」放在一起。
- [x] 分类选择改成树（2026-10-17，用户反馈下拉框按完整路径平铺、位置漂移、卡顿）：`collection-picker.tsx` 按 Zotero 的层级显示，可展开收起，顶部搜索（显示匹配项及其上级），面板直接在按钮下方展开、无动画；附件检查先用上，下载记录页的推送分类仍是输入框加候选。
- [x] Zotero 页「写入方式」不再延迟出现（2026-10-17，用户反馈点开后几秒才冒出来）：读写入方式要启动一次 zotero-mcp（2–3 秒），后台现在记住第一次成功的结果，授权后才重新读取；读到之前显示「写入方式：检测中…」占位。
- [x] 列表宽度（2026-10-17，用户发现筛选、下载、Zotero 共用一个宽度）：下载和 Zotero 不再有第二栏，只有筛选的列表可拖动宽度。
- [x] 字号与间距（2026-10-17，用户给了参考 app 的截图）：新增共用页面框架 `components/page.tsx`——60px 白色页头（标题 20px 半粗，可带标签页或按钮）、灰色内容底（`--canvas`）、白色圆角卡片（内边距 20px、间距 24px）、内容居中限宽；下载、Zotero、设置都用它。设置页的分组列表移进页面（「检索与下载」「连接」两组，带图标），每组标题下有一句说明，设置项放进卡片。左侧图标栏项高 40px，选中为主色浅底、主色文字和左侧竖条。下载页顶部加三个数字卡片（已下载 / 失败 / 待下载），详情页头为「下载 › 任务名」。
- [x] 摘要翻译（2026-10-17，用户要求；担心 Agent 翻译耗 token，选了三种都支持、让用户选翻译范围）：记录加 `abstract_zh`。「设置 → 筛选」选自动翻译的范围（评为高 / 高和中 / 全部 / 不自动翻译，默认高）和服务：DeepSeek（默认，提示词要求保留化学式、单位、缩写）、DeepL（`:fx` 结尾的 Key 走免费接口）或 Agent。DeepSeek / DeepL 由 app 后台在用户打开范围内的文献时翻译一次并写回记录，范围外的可手动点「翻译摘要」；没填 Key 时什么都不翻译，不耗对话额度。选 Agent 时 `rate` 的 `next_step` 才要求附 `abstract_zh`，漏的列在 `untranslated`，用新的 `litloop_screening(action="translate")` 补。详情页摘要可切换译文 / 原文。测试 `tests/test_translate.py`。真实 Key 还没测。
- [x] 筛选详情页排版（2026-10-17，用户反馈中间空、加入下载按钮太长）：灰底加卡片、内容限宽居中；评分改成分段按钮，和「排除」「加入下载」（常规宽度）放在同一行；操作区紧跟内容，内容长时停在底部。
- [x] 柱状图悬停（2026-10-17，用户反馈要精确指到柱子上才有提示）：每根柱子的悬停范围扩到整列（全高、含间隙），提示改成即时显示的浮层（第 n 批：高 x / 已评 y 和百分比）。柱高改为相对最高一批缩放（用户反馈按百分比都很矮），旁边和提示里的百分比仍是实际比例。
- [x] app 里「继续检索」（2026-10-17，用户反馈改评分后还要回对话说「继续」；用户选了由 app 自己出下一批）：Agent 一直在跑时，每次 `next` 本来就读用户在 app 里的评分，只有 Agent 停下后才需要回对话。现在状态条有「继续检索」，`POST /api/sessions/{id}/next` 在后台线程里跑 `next_batch(force=True)`（建议停止时也出下一批；正在生成时返回 409），进度条照常显示，跑完自动刷新。Agent 没评过的文献可以由用户先评：评分接口按最近一批补出条目（`reason` 为空），清除评分即删除；Agent 之后评分时用户的评分保留。`status` 新增 `unrated`（最近一批里 Agent 还没评的，带编号），技能文档要求回到对话时先 status、评完再 next。去掉了「回到对话说继续」的提示。测试 `tests/test_app_continue.py`。
- [x] 状态条精简（2026-10-17，用户反馈默认窗口下显示不全、字太多）：只留「已评 120 / 3,762」、柱状图加百分比、右侧「建议停止」标签和 ⚠ 提醒条数，「建议停止」和 ⚠ 提醒点击后弹出面板显示全文（用户反馈只有数字看不到内容，悬停提示不好发现），其余说明在悬停提示里。另修整页滚动：外层容器只设了最小高度，内容长时整页被撑开（详情页的操作区因此停不住），改为固定为窗口高度、各栏自己滚动。
- [x] 三处体验问题（2026-10-17，用户反馈）：接口额度页的 OpenAlex 今日剩余要等几秒才出现——后台缓存 2 分钟、「刷新」才重新查询，进设置页时提前查询，查询中显示占位行；从其他页切到筛选时侧栏有滑动动画——切换页面时跳过宽度过渡（新样式类 `no-transition`，收起侧栏仍有动画）；状态条的蓝色柱状图没有说明——加上「每批高相关比例」和最近一批的百分比，悬停每根柱子显示「第 n 批：高 x / 已评 y」；状态条整体放大（14px 字、柱高 24px）。
- [x] 筛选页状态条（2026-10-17，用户反馈挤在一起）：接口告警原来是给 Agent 的英文、逐条截断；`quota.warnings("zh")` 给 app 用中文（「OpenAlex 今日被限流 110 次，已放慢请求」），app 读概况时换成当前的中文告警；状态条不再换行，告警只显示第一条加「另有 n 条」，悬停看全部。测试 `tests/test_quota_warnings.py`。

## 2026-10-15：阶段 2（深度检索）完成并验收
- [x] 验证共同引用扩展（2026-10-15）：取种子的全部参考文献，向 OpenAlex 查“引用了其中任何一篇、且标题或摘要含核心词”的文章，再加上种子的参考文献和施引文献。
  - SiC/Al：候选池 838 篇，包含 16/19 目标；PDC 气凝胶：1611 篇，包含 23/26。
  - 只按共同引用数排序时，前 100 名分别只有 7、15 篇目标；改成“共同引用数 + 2 × 完全匹配的检索词组数”后，前 100 名为 13–14、17 篇，前 200 名为 14、20–21 篇。
  - 种子自己引用的老文献要补取标题和摘要，否则关键词得不到分（SiC/Al 有 2 篇目标因此排在 700 名以后）。
- [x] 实现（2026-10-15）：`src/litloop/deep_search.py` 和 `litloop_deep_search` 工具（取代 `litloop_expand_citations`，工具仍为 20 个），5 个单元测试。
- [x] 第一次实跑，主测试集 SiC/Al（2026-10-15，Agent 按问题评分，不看目标清单）：
  - 建池 67 秒，1351 篇，包含 18/19 目标；筛选前目标排名：前 30 名 6 篇，前 100 名 12 篇，前 200 名 17 篇。
  - 第 1 批：高 16、中 7；第 2 批：高 18（滚雪球带进了三维网络、互穿结构的目标）；第 3 批：高 8、无新目标。每次滚雪球扩展约 1 分钟，池子涨到 3563 篇。
  - 筛 90 篇后找到 12/19 目标，**12 篇都被评为“高”**，评分与用户判断一致。
  - 剩下 6 篇排名 81–1189：其中 4 篇在池中没有摘要，只凭标题打分；另外种子增加到 40 多篇后，“共同引用数”被相近但不相关的文章（超重力、真空压力浸渗）抬高，目标被挤后。
  - 2006 年 Al/SiC 微波电路载板一文不在候选池中。
- [x] 改进排序（2026-10-15 用户同意此方向）：
  - **问题 1：滚雪球稀释**。只把“高”当正例往外扩，“低/不相关”评分没用上；共同引用按数量计，种子越多分数越膨胀。这就是信息检索里的查询漂移和自训练中的语义漂移。
  - **问题 2：缺摘要的文章翻不了身**。
    - 原因一：OpenAlex 本身缺摘要（Elsevier 2022 年底起不再提供；早年文章和部分会议、IEEE 文章的出版商没交给 Crossref）。这是推断，4 篇目标各属哪种待核对。
    - 原因二：我们的流程。`fill_abstracts` 只对已选进当批的 30 篇运行，排序却要用摘要做检索式匹配，形成死循环：缺摘要 → 分低 → 进不了批次 → 一直不补。
  - 方案：改用**持续主动学习（CAL）**，即系统综述筛选的标准做法，同时锚定原始意图，并做归一化。
    1. [x] **先补摘要再排序**（2026-10-15）：每次出下一批前，给排名前 150、缺摘要的候选补一次摘要（30 秒预算，每篇只试一次），补到后重算分数。
    2. [x] **共同引用归一化**：离线比较后**不用 IDF 加权**（剩余目标反而更靠后：前 60 名 0 篇），改用第 3 条的锚定方式。
    3. [x] **锚定原始意图**（2026-10-15）：用户种子的参考文献每篇计 1；只有滚雪球种子引用的参考文献，按“几篇种子共同引用”计 1/3、2/3、1。第一批仍用公式“锚定共同引用 + 2 × 完全匹配的检索式”。
    4. [x] **CAL 排序**（2026-10-15）：纯 Python 逻辑回归。正例为“高”和种子，反例为中/低/不相关，再随机取 100 篇未筛的当反例；特征为标题加摘要的词（TF-IDF）、锚定共同引用、检索式匹配、有无摘要。正例至少 3 篇、反例至少 5 篇才启用，之前用公式。每批重训，训练加打分不到 1 秒。
       - 离线验证（SiC/Al 实跑的池子 3563 篇和已有 90 条评分，剩余 6 篇目标的排名）：旧公式 [18, 39, 68, 70, 98, 1196]；IDF [63, 94, 109, 124, 155, 723]；只锚定 [4, 18, 43, 83, 87, 2175]；CAL + 锚定 3 组随机数分别为 [9, 19, 30, 38, 52, 56]、[5, 18, 23, 27, 63, 181]、[5, 16, 30, 33, 40, 64]；写进代码后重放为 [10, 22, 27, 33, 44, 62]。“中”算反例比算正例好。
    5. [x] **停止规则**（2026-10-15）：改为拐点法，最近一批“高”的比例不到此前最好一批的 1/6 就建议停止；原规则数“高+中”，相邻课题让“中”一直不少，从未触发。回放：PDC 在第 4 批（120 篇，已找到 23/26）建议停止；SiC/Al 到第 5 批仍不停止（期间每批都还有新目标），两者都合理。
    6. [x] **提速**：请求改为并行、引用查询先问数量再拆分、补摘要各来源同时问（见下方提速第一、二步）。
  - [x] 实跑评估（2026-10-15，SiC/Al，同样的问题、8 组检索词、3 篇种子；Agent 只按摘要评分，筛完才看目标）：
    - 读到第 30 / 60 / 90 / 120 / 150 篇时找到的目标：旧方案 6 / 12 / 12（筛 90 篇停止）；**新方案 8 / 11 / 15 / 16 / 17**（共 19 篇目标）。
    - 新方案还没找到的 2 篇：1 篇是 1990 年代的经典论文、没有摘要，目前排在第 35 名，下一批就会出现；另 1 篇（2006 年微波电路载板）不在候选池里。
    - [x] 不在池里的那篇（10.1109/tepm.2005.862627，空气中无压浸渗做微波电路载板，很对题）的原因（2026-10-15 核实）：标题和摘要只写“Al”，没有“aluminum”，核心词“aluminum SiC”把它挡在引用路线之外，关键词路线的检索词也多写“aluminum”。修复：核心词支持 `|` 表示任意一个（`Al|aluminum|aluminium SiC` → `(Al OR aluminum OR aluminium) AND SiC`，OpenAlex 支持）；技能文档提醒检索词照顾缩写和英式拼写。用它和种子共有的 5 篇参考文献实测：改前引用路线 446 条、不含它；改后 1000 条、含它。
    - [x] 引用路线碰到 1000 条上限时，把这组参考文献对半拆开重查，直到单篇；单篇仍超限时按被引数从高到低截（2026-10-15）。用微波载板那篇和种子共有的 5 篇参考文献实测：原来截在 1000 条，现在取全 1171 篇，耗时 80 秒（拆开后的子查询在同一线程里串行）。
  - [x] PDC 气凝胶辅助测试集验收（2026-10-15）：同样的问题和 3 篇种子，年份到 2021；8 组检索词和 2 个核心词由 Agent 按问题自拟（用了 `|` 写法），Agent 按测试集规则评分、筛完才看目标。
    - 读到第 30 / 60 / 90 / 120 篇找到目标 9 / 18 / 22 / 23 篇（共 26 篇）；此前只用关键词时候选池里一共只有 17 篇。边缘文章找到 4/18 篇（不要求）。
    - 找到的 23 篇里 21 篇评为“高”，2 篇评为“中”（SiC 纳米纤维气凝胶看不出路线；常压干燥的杂化凝胶 ambigel）。
    - 漏掉的 3 篇都没有摘要，排在第 172–455 名：补摘要只在全体前 150 名里挑，而缺摘要的文章正因为缺摘要才排得低，永远轮不到。未筛的 1469 篇里 607 篇缺摘要。
    - [x] 修复：改为在缺摘要的文章里按排名取前 150 篇补。用这个会话模拟：补 1 轮（约 35 秒，补到 120 篇，Elsevier 的靠 Scopus）后这 3 篇从第 203、96、383 名升到第 65、1、98 名。
    - 耗时：建池 59 秒；三次滚雪球扩展分别 128、45、18 秒（最后一次已改为并行请求，但新种子也少），排序和补摘要 29、43、6 秒。
  - [x] 提速第一步（2026-10-15）：OpenAlex 按 50 个 ID 分块的请求改为 4 线程并行（`_by_filter`、`_citing`、`_cites_refs_on_topic`）。
  - [x] PDC 重跑（2026-10-15，修复补摘要后；已评过的文章沿用上次评分，新出现的由 Agent 按摘要评分）：读到 30 / 60 / 90 / 120 / 150 篇找到 9 / 15 / 18 / **24** / 24 篇，150 篇时停止规则触发（最后一批只有 1 篇高）。
    - 最终多找到 1 篇（SiC/C 气凝胶），但 60、90 篇时比上次少 3–4 篇：补摘要范围扩大后，几百篇带上摘要的多孔 PDC 等相近文章进入竞争，把目标往后挤。评分沿用，差异只来自排序。
    - 仍漏 2 篇，都已补到摘要但排在第 82、268 名（SiC/莫来石气凝胶；吸附卷烟烟气的 Si/O/C/N 气凝胶），用词和其他文章差别大。
    - [x] 离线比较后加入标题词特征（见本节最后一条“收尾”）。
  - [x] 测试集规则修订（2026-10-15，用户决定）：前驱体是先驱体聚合物（聚倍半硅氧烷、聚碳硅烷等）的气凝胶，热解或碳热还原都算 PDC 路线目标；没有先驱体聚合物的杂化凝胶（酚醛、间苯二酚-甲醛与二氧化硅）碳热还原仍算边缘。核对 18 篇边缘文章，无需调整。
  - [x] 提速第二步（2026-10-15）：
    - 引用查询先问总数，超过上限直接拆分、各轮并行（同一组例子 80 → 33 秒）；PDC 第一次滚雪球同样 11 篇新种子、新增 453 篇，扩展 128 → 36 秒。
    - 补摘要改为各来源同时问、先答先用（不再截断，用户 2026-10-15：摘要很重要，不能为提速少补）。快速检索里锂金属负极一组原来 18 秒，其中 13 秒是等没有 Key 的 Semantic Scholar，而 Scopus 1.5 秒就给了。
    - 快速检索耗时实测（每组 10 条）：发现引擎 3–5 秒，补摘要 2–14 秒，期刊分区首次 1.2 秒，其余不到 0.1 秒；目前只查 OpenAlex。
    - 改为同时询问后复测（2026-10-15）：四组 7.0–9.2 秒，锂金属负极一组 18.2 → 8.6 秒（补摘要 13.5 → 1.4 秒）；现在最慢的是发现引擎（4–7 秒）。
  - Semantic Scholar API Key：用户申请过，没有通过（2026-10-15）。不再指望它：补摘要已改为各来源同时问，没 Key 的 Semantic Scholar 慢也不再拖住别的来源；检索仍只靠 OpenAlex，第二个数据源以后另找（Crossref 慢且噪音多，已试过）。
    - 找到的 17 篇里 15 篇评为“高”。2 篇评为“中”（Al-40Si 在空气中高温自发浸渗；振动辅助浸渗），用户认为相关，说明 Agent 对“中”的把握偏严，可在技能文档里提示边界情况算“高”。
    - 建池 77 秒，池子 1352 篇；之后 4 次滚雪球分别新增 1466、433、716、211 篇，最后 4178 篇。每次“取下一批”的耗时没有记录，下次给 `rounds` 加上秒数。
    - 第 4、5 批里高相关明显变少（8、11 篇），混入超重力、压力浸渗、铜基等相邻课题，停止规则还没有触发。
- 原方案（保留备查）：
  1. **开始**：Agent 给出问题、4–8 组英文检索词、1–3 个核心词和种子 DOI。插件对每组检索词取 100 条原始候选（不截断），再做共同引用扩展，合并、去重、打分。
  2. **分批筛选**：按分数每次给 Agent 一批（默认 30 篇，带摘要），Agent 用 `litloop_screening` 存相关度和理由。
  3. **滚雪球**：新评为“高”的文章自动加入种子，重新做共同引用扩展、重新打分，再给下一批。
  4. **停止**：最近一批几乎没有新的高、中相关文章，或者已经筛了 200 篇时，建议停止并说明原因。
  5. **覆盖率**：给了必找清单时，报告每篇的状态（不在池里 / 在池里但排名靠后 / 已筛，评为什么）。
  6. 工具：新增 `litloop_deep_search`，取代只返回 DOI 列表的 `litloop_expand_citations`，工具总数保持 20 个。
  - [x] 收尾（2026-10-15）：排序模型加入标题词特征（每个标题词另计权重 0.3）。用两次实跑会话离线比较“剩余目标进前 60 名的篇数”，四个检查点合计：现模型 13、标题含核心词 15、标题词 18、两者都加 19（最差排名更差）。PDC 读完 4 批后剩下 2 篇目标从第 298 名内升到第 1、12 名；SiC/Al 基本持平。README 更新为阶段 2 已验收。

## 2026-10-15：修代码审查中两个紧急问题
- 深度检索中没有 DOI 的文章评分后读不回来，每批都会被重新推送（SiC/Al 池里有 58/3563 篇）。改为按标题对应回池中的条目。
- Semantic Scholar 批量补摘要按 500 篇分块（接口上限），回复不是列表时停止请求、不崩溃。为“排序前先补摘要”做准备。
- 各加 1 个测试，确认修复前失败、修复后通过。

## 2026-10-15：CHARTER 改为纲领
- 用户确认它是纲领：只写痛点、原则、验收标准、测试集角色和各阶段设计要点；路线进度不再写在那里，统一记在本表。
- 更新了已过时的内容：查不全列为第 4 个痛点；场景 B 的验收改为“主测试集目标全部找到”；阶段 2 写成引用耦合 + 滚雪球 + 防漂移的设计。

## 2026-10-15：修检索的候选池和过滤器
- 用两个测试集各 8 组检索词验证：原始候选卡在 20 条；连字符规则误删目标（Al-Si-Mg 对 Al-Mg-Si）；去离题只凭残缺摘要，误删目标。
- 20 条上限来自 easyseek `quick` 深度，提到 100（easyseek 78642c5）。只放大候选池时，原始候选里的目标明显变多，但最终数量不变，说明瓶颈在过滤器。
- 去离题和连字符规则改为分档排序、不再删除：完整匹配 → 缺词组或只有标题可判 → 有摘要但命中不足。化学式和带数字的连字符词不算词组；长词做简单词干截断；各种破折号统一处理；按被引或日期排序时在档内排。
- 结果（只看前 25 条）：SiC/Al 10 → 12/19，PDC 气凝胶 16 → 17/26。剩下的目标大多不在原始候选里，要靠阶段 2。

## 2026-10-15：阶段 2 第 1 步，建立测试集
- 摸底实验（2026-10-15）：从 SiBCN 的 2 篇种子出发，看 5 篇目标各靠什么能找到。
  - 沿引用走一步（参考文献 + 施引文献，共 257 篇）：0/5。
  - OpenAlex 相关文章、Semantic Scholar 推荐：0/5。
  - 和种子有共同参考文献：3/5（分别重叠 29、6、5 篇）。
  - 7 组关键词检索：2/5，恰好是共同参考文献找不到的那 2 篇。
  - 结论：关键词检索和“共同参考文献”互补，阶段 2 两者都要。
- 决定测试集方案（2026-10-15，用户同意）：
  - **主测试集**：用户 Zotero 的「SiC/Al」分类（铝合金无压浸渗 SiC 预制体，22 篇，1991–2025）。主题集中，相关性由用户自己判定，也能评判噪音。
  - **辅助测试集**：综述 Polymer derived ceramic aerogels（10.1016/j.cossms.2021.100936）的参考文献，筛掉综述、书籍和离题文献后使用，只测召回率。
  - **SiBCN 7 篇**：文章分属几个子问题，只用来观察，不用来验收。
  - 以后再加：金属/陶瓷互穿复合材料综述（10.1016/j.ceramint.2021.09.232），作为宽课题测试集。
- Zotero「LitLoop 测试 / 深度检索 · SiBCN」（种子 2、目标 5），以及 `tests/acceptance/sibcn_deep_search.json`（2026-10-15）。
- 主测试集（2026-10-15，用户确认问题和种子）：`tests/acceptance/sic_al_infiltration.json`，Zotero「LitLoop 测试 / 深度检索 · SiC/Al」（种子 3、目标 19）。
  - 种子按年代和预制体类型错开：2000 年颗粒预制体浸渗机理、2006 年三维网络预制体、2025 年粘结剂喷射打印预制体。
  - 稻壳灰双层预制体那篇（2015）一度存疑，看摘要确认是一步无压浸渗，保留为目标。
- 辅助测试集（2026-10-15）：`tests/acceptance/pdc_aerogel_review.json`。综述 148 篇参考文献，OpenAlex 能取到 141 篇，全部归类：种子 3、目标 26、边界 18、排除 94，排除原因逐篇记在文件里。
  - 用户决定：碳热还原路线做的 SiC/Si3N4 气凝胶算边界，不作为目标。
  - 4 篇没有摘要的用 Scopus 补全后判断：3 篇是碳热路线，归边界；静电纺丝 SiC 纳米纤维气凝胶推断为先驱体路线，归目标，文件里注明是推断。
  - 这组文章不在用户的 Zotero 库里，所以只存 JSON。
- SiBCN 测试集的 JSON 标注为“跨子方向，仅观察”（2026-10-15）。

## 2026-10-12 ～ 10-13
- 用 SiBCN 课题验收阶段 1：6 篇走完“检索 → 筛选 → 下载 → Zotero”。修复了验收中发现的问题：
  - 同名文件被当成另一篇论文的 PDF；文件名改为包含 DOI（44feb31）。
  - Zotero 改为本地写入；localhost 强制走 IPv4，单次推送从十几秒降到约 4 秒；空附件检查（55dbdf2）。
  - 预印本并入正式版、连字符词组、垃圾标题、无 DOI 期刊名提示、筛选编号、命令行默认目录（b9474d2）。
- CI 在 3 个系统 × 3 个 Python 版本上全部通过（c4b4349、6819595）。
- README 重写并加上项目状态（a32796c、6c1c3b7）；新图标（04b1fb1）。
- 本机：虚拟环境改为 `~/.mcp-envs/litloop`，清理 scansci 残留。

## 2026-10-11
- 改名 LitLoop（0c680d7）。
- 筛选记录工具（c3eba9a）；摘要补全与清洗（0de4af9）。
- 检索：真实被引数、排序、离题过滤、期刊分区过滤、撤稿标记与本地 Retraction Watch（05aeb78 … 1eb3d0b）。
- 独立仓库、`scripts/deploy.py` 一键安装（01c34cf）、Zotero 推送（2afcd43、141193a）。
- 出版商下载修复：机构登录、Springer、RSC、浏览器提速等（b0306ec … 08cc284）。

## 2026-10-07 ～ 10-08
- 在原项目基础上修复机构登录、Sci-Hub 无浏览器模式等，详见 [NOTICE](NOTICE)。

## 2026-10-19：新文献重测（第二轮，20 篇，保存位置临时改到空文件夹）

- 背景：原 20 篇里 18 篇已在 10-17/18 下到，待下载列表和下载缓存都会把它们当成“已完成”，测不出用时。改法：保存位置临时指到空文件夹，清掉这 20 篇的下载缓存（缓存只是加速记录，不动已下载的 PDF 和下载记录），用“粘贴 DOI”重新提交。顺带发现两个 bug（见待办池「下载」）：换保存位置后旧文件的缓存仍判“已完成”；粘贴时“已下载过”的提示把“下载记录里有”和“文件还在”混在一起说。
- 结果：共 1769 秒（约 29.5 分钟），18/20 篇成功（SSRN 用户跳过，Springer 书章按预期判“学校没有订阅”）。对比 10-18 那次 14 篇 2444 秒。
  - 查 OA 链接 0 秒（缓存命中）；开放获取一轮 35 秒 3/4 篇（10-17 那次 110 秒）；Sci-Hub 一轮整轮跳过（补到年份后 20 篇都是 2024 年以后发表）。
  - 逐篇：Elsevier 8 篇 28–81 秒；Wiley 186 秒（第一轮打不开白等 43 秒 + Cloudflare 验证等用户约 2 分钟）；Springer 书章 250 秒失败（10-18 是 360 秒）；SAGE 102 秒、T&F 91 秒、IOP 157 秒（10-18 是 228 秒）、ACS 84 秒；Nature 16 秒直接下载；SSRN 450 秒后用户跳过（10-18 是 1001 秒）。
- 查明的两个根因（TODO「重测发现的其他问题」）：选机构慢是 Playwright `fill()` 自带的可交互等待（最长 30 秒），不是选择器轮询的问题；跳过 SSRN 后等 101 秒，是 `sources/instsci.py` 的 WebVPN 登录循环仍用 `page.wait_for_timeout`，10-18 修第三篇卡死时这一处漏改。
- 连通性排查：SSRN 从校园网直连/当时测的 HTTP 代理都连不上（curl 直连超时、HTTP 代理连接被拒），用户的浏览器开代理能秒开 → 已把 `ssrn.com` 加进 `host_proxy_hosts`。另用 curl 测了出版商、登录跳转、检索接口、Sci-Hub/LibGen 共约 50 个域名的直连/HTTP 代理/SOCKS 代理连通性（一次性快照，访问规则会变，不记录在此）：CARSI（`ds.carsi.edu.cn`）和 arXiv（`export.arxiv.org`）直连不通、代理可达，是否也加入名单待用户确认。

## 归档：2026-10-19 重构前的 TODO 原文

2026-10-19 用户要求重构 TODO：已完成的条目全部移出，原文（「正在做」起）照录于此，各项的做法和结论见上面对应的节。

<details>
<summary>展开原文</summary>

### 正在做

**当前：下载提速与实测**（2026-10-17 起，详见「接下来」，下次从「下次接着做」开始）。已完成的实验结论和决定见 [docs/DECISIONS.md](docs/DECISIONS.md)。

### 接下来

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
3. [x] 最终失败原因取最后一条通道，不按 `_FAILURE_ORDER` 选最说明问题的一条。
4. [x] 同一篇重试时，上一轮迟到的通道把步骤和错误写进新的 `DownloadAttempt`（按 DOI 反查登记表）。
5. [x] app 的大批量任务（>30 篇，走车道）拿不到失败原因和“尝试经过”（MCP 出口剥掉 `attempts` 时一起剥了）。
6. [x] `main.py publisher-batch` 读不存在的 `summary["results"]`，永远报 0 篇。
7. [x] 还有三处开浏览器不带所属文献：`_batch_institutional_phase`、`pipeline._inst_fetch_chunk`、`pipeline._si_one`。
8. [x] Sci-Hub 池线程复用一个浏览器，按篇的所属与共享冲突：跳过 A 可能关掉 B 正在用的浏览器。
9. [x] `paywall` 规则匹配到 Unpaywall（没填邮箱被说成需要订阅）；`download exception` 等内部原因补文案。
10. [x] 合并后学校登录窗口跟随 `browser_headless`，改为始终可见。
11. [x] `test_login_no_access` 会真的联网。
12. [x] 回归测试补齐（随各项）；`institutional_download` 成功后清“学校没有订阅”的记录（2026-10-19）。
13. [~] 小问题：重复的 `if not pdf_bytes` 块（已删）；`_SESSION_LOCKS` 超过 50 个时清掉空闲的（已改）；下载成功时清掉跳过标记（已改）；`instsci_bridge.try_institutional` 已无调用方（随“去掉 publisher_batch 这条路”删）；`not_entitled_after_login` 只记事件、没写进失败原因（同上，这条路之后去掉）。

- [x] 下载页“跳过这篇”“取消下载”点了立即显示“正在跳过…”“正在停止…”（2026-10-19 用户提出：原来要等几秒才有变化）。
- [x] 逐篇下载排队时如实说明（2026-10-19 用户发现：没查 Sci-Hub 的新文献也写“Sci-Hub 未找到”）：查过没找到写“Sci-Hub 未找到”，按年份跳过的写“发表较新，不查 Sci-Hub”。
- [ ] **去掉 `publisher_batch` 这条路**（2026-10-19 用户同意记下、之后做）：MCP 批量下载非车道模式的第二阶段（`_batch_institutional_phase`）改用 app 在用的下载引擎（学校通道、登录状态、失败原因都能用上）；`litloop publisher-batch` 改为调用同一引擎或删除；`instsci_bridge` 已无调用方，一并删。原因见 DECISIONS「publisher-batch 验收」。
- [x] Agent / 命令行的下载在 app 下载页也看得到（2026-10-19 用户定：只做这一步，随后用户要求把旧的“LitLoop · 进度”小窗口整个删掉，已删）：`/api/downloads` 的 `external`，下载页顶部显示任务、进度、当前文献和登录提示，只显示不能控制。
- [x] Springer 选择机构页搜学校约 30 秒（11:47:26 → 11:47:57）：改为填完学校名后每 0.5 秒看一次结果、出来就点，并记录每个输入框用了多久；下次实测看效果。
- [x] “app 的登录提示晚 36 秒”是误读：提示的 `since` 每次心跳都被刷新，11:48:39 只是读文件那一刻的心跳；提示 11:48:03 就有了。`since` 改为只记第一次出现。
- [x] “粘贴 DOI”认 PDF 里复制来的 Unicode 连字符和不换行空格；认不出的行（整段参考文献、只有标题）点「按参考文献查找 DOI」用 Crossref `query.bibliographic` 查，标题词 85% 以上出现在这一行才算，列出标题供核对（2026-10-19，盘点残留代码时由 `normalize_doi_unicode`、`search_by_title` 得到的启发）。
- [x] 删除没人用的代码（2026-10-19，清单见 DECISIONS「删除没人用的代码」）。
- [ ] 以后用得上的现成代码（2026-10-19 盘点）：`deps.is_feature_available` / `embedded_tor.is_tor_installed` 可做首次引导里一直没做的“可选组件”一步；`extractors.extract_figures` 留给“分析归纳”；`progress_reporter.elapsed_sec` 可给 app 里“Agent 正在下载”加已用时间。
- [x] Sci-Hub 一轮前不重复探测镜像（2026-10-19）：测速有新鲜结果时先试它选的镜像，探测放后台、同时只跑一个。
- [x] “粘贴 DOI”进来的文献没有年份（2026-10-19）：任务开始时向 OpenAlex 补查（每 50 篇一次，最多等 10 秒，查不到就不管），写回任务文件并交给 Sci-Hub 的年份判断，Sci-Hub 按年份跳过不起作用：解析时顺便查年份（Crossref / OpenAlex）。

**还没做的（从 2026-10-17 的计划里留下）**
- [ ] 批量引擎：浏览器下载超过竞速时限后“迟到的成功”没算进车道结果，冷却重试又下了一遍（ceramint.2024.08.002）。（2026-10-18 从 DECISIONS 移回）
- [ ] 下载慢：开始前用 Crossref / doi.org 验证 DOI（不存在的 DOI 跑完了所有车道）；车道不必等整批走完；Sci-Hub 镜像找到 PDF 但传输慢时被 45 秒窗口判失败；arXiv 进度用编号汇报、下载任务对不上 DOI。（2026-10-18 从 DECISIONS 移回）
- [ ] 大清单（>30 篇）：先摸底分组（开放获取 / Sci-Hub 有 / 需学校权限，后者按出版商细分），app 里分成几个队列，每个队列写明怎么下，用户逐个队列点下载。
- [ ] 可视化 app 1 期继续：学校认证（选学校、弹窗登录、试下载一篇订阅论文）；之后深度链接、完成通知、安装包与自动更新（见待办池 12）。
- [ ] 阶段 2 剩余：速度（每轮 30–55 秒主要花在补摘要上，先从这里下手：扩展与补摘要并行、OpenAlex 按参考文献缓存）；深度检索 → 批量下载 → 按分类推送 Zotero 走通一次。
- [ ] 类别多、彼此引用少的课题怎么照顾到每一类（见待办池 11）。

### 待办池

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

</details>

