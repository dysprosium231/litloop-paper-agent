# LitLoop 开发约定

给在这个仓库里工作的 Agent（Claude Code、Codex 等）看的。

## 进度记录

三份文件各管一件事，不要混写：

| 文件 | 回答 | 写什么 | 不写什么 |
|---|---|---|---|
| [CHARTER.md](CHARTER.md) | 要做成什么样、怎样算做好 | 目标、原则、验收标准、各部分现在有效的设计 | 进度、先后顺序、“已做 / 下一步 / 规划中”、改动历史（删除线） |
| [TODO.md](TODO.md) | 还差什么、先做什么 | 路线图（CHARTER 各部分的状态）、正在做、待用户决定、待实测、待办池 | 做完的细节、设计理由 |
| [docs/DECISIONS.md](docs/DECISIONS.md) | 发生过什么 | 做了什么、为什么、实验结论、用户的决定，带日期和提交号 | 计划；旧条目不改，只追加 |

- 开始工作前，先读 TODO.md 的「正在做」和「待用户决定」，从那里接着做。
- 每完成一步，就在 TODO.md 里更新；和对应的代码放在同一个提交里。做完的事写进 DECISIONS，TODO 里删掉或只留一句和链接。日期写绝对日期。
- CHARTER 里还没达到验收标准的部分，TODO 的路线图里都要有一行；CHARTER 新增一部分时，同时在路线图加一行。
- 改 CHARTER 里的设计或验收标准要用户同意；原来的写法和理由记进 DECISIONS，CHARTER 只留现在有效的。

## 提交前

- 每次都运行 `python scripts/check_release_consistency.py`。
- 只改文档（TODO、README、`docs/` 等）时不用跑 pytest。
- 改代码时，改的过程中只跑相关的测试文件；几项小改动攒成一次提交，提交前运行一次全部测试：`python -m pytest -q`。
- 改了 `app/` 的界面，在 `app/` 里运行 `npm run build` 和 `npm run lint:copy`。
- MCP 工具描述不超过 200 个字符（Python 3.13 以前的版本会把文档字符串的缩进也算进去）。

## 测试集

- 检索验收用的测试集放在 `tests/acceptance/`，每个 JSON 写明问题、种子、目标和来源。
- 用户 Zotero 里对应的分类在「LitLoop 测试」下。

## 外部接口的限额

- 限额、并发、配额只写官方文档里明确写着的数字，附链接和核对日期；实测、自定、未核对的分别标明，不凭印象填。
- 集中记录在 [docs/API_LIMITS.md](docs/API_LIMITS.md)；改了 `quota.py` 或 `network.py` 里的限速，同步改这份文档。

## app 与 Agent 共用的文件

- 筛选记录、深度检索概况、进度文件的格式写在 [docs/APP_DATA.md](docs/APP_DATA.md)；改了字段就同步改它，改字段含义或删字段时把 `store.SCHEMA` 加一。
- 这些文件只通过 `store.py` 读写（加锁、整份替换），不要直接 `write_text`。

## 技能文档

- `skills/litloop/SKILL.md`、`zcode-plugin/skills/litloop/SKILL.md`、`skill/SKILL.md` 由 `skills-src/` 生成：改 `skills-src/` 里的模板或 `shared/` 片段，再运行 `python scripts/build_skills.py`；不要直接改生成的文件（`check_release_consistency.py` 会报出来）。

## 代码放在哪

- 模块按领域分子包，布局和每个子包管什么写在 [docs/LAYOUT.md](docs/LAYOUT.md)。新函数、新模块放进对应的子包；几个子包都要用的才放顶层。新增或挪动模块时同步改 LAYOUT.md。
- 写之前先找现成的，不要另写一份：
  - 文本归一化（大小写、各种连字符、英美拼写、单复数、截词、停用词）、元素符号、判断两条记录是不是同一篇文献：用 `terms/text.py`；
  - 缩写、同义词、拼写的展开：用 `terms/glossary.py`（`in_use`、`query_variants`、`alternatives`）；
  - DOI 的解析和规范化：用 `identifiers.py`。
- 现成的不够用，就在原模块里扩展，不在调用处另写一版。
- `check_release_consistency.py` 会报出在 `terms/text.py` 之外另写的停用词表、连字符集合、标题键和元素猜测（`_source_hygiene.py`）。模块全部搬完后，再加上“没写进 LAYOUT.md 的模块”这一项。
- 排序模型的词特征用 `RANKING_STOPWORDS`，不用 `STOPWORDS`；改它会改变深度检索的排序，改之前和改之后都跑 `scripts/eval_synergy.py`。

## 其他

- 与用户交流用简体中文；提交信息可以用英文。
- 项目要给别人用：功能要能自动探测、靠配置驱动，不写死某台电脑的路径。
