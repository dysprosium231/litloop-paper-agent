# LitLoop 开发约定

给在这个仓库里工作的 Agent（Claude Code、Codex 等）看的。

## 进度记录

- 开始工作前，先读 [TODO.md](TODO.md) 的「正在做」和「接下来」，从那里接着做。
- 每完成一步，就在 TODO.md 里打勾，或者移到「已完成」并写上日期和提交号；和对应的代码放在同一个提交里。
- 用户做的决定、计划的变化、实验结论，也记进 TODO.md。日期写绝对日期。
- 设计和验收标准写在 [CHARTER.md](CHARTER.md)，进度不写在那里。

## 提交前

- 运行全部测试：`python -m pytest -q`。
- 运行 `python scripts/check_release_consistency.py`。
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

## 其他

- 与用户交流用简体中文；提交信息可以用英文。
- 项目要给别人用：功能要能自动探测、靠配置驱动，不写死某台电脑的路径。
