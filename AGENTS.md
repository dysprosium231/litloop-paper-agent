# LitLoop 开发约定

给在这个仓库里工作的 Agent（Claude Code、Codex 等）看的。

## 进度记录

- 开始工作前，先读 [TODO.md](TODO.md) 的「正在做」和「接下来」，从那里接着做。
- 每完成一步，就在 TODO.md 里打勾，或者移到「已完成」并写上日期和提交号；和对应的代码放在同一个提交里。
- 用户做的决定、计划的变化、实验结论，也记进 TODO.md。日期写绝对日期。
- 设计和验收标准写在 [docs/SEARCH_SPEC.md](docs/SEARCH_SPEC.md)，进度不写在那里。

## 提交前

- 运行全部测试：`python -m pytest -q`。
- 运行 `python scripts/check_release_consistency.py`。
- MCP 工具描述不超过 200 个字符（Python 3.13 以前的版本会把文档字符串的缩进也算进去）。

## 测试集

- 检索验收用的测试集放在 `tests/acceptance/`，每个 JSON 写明问题、种子、目标和来源。
- 用户 Zotero 里对应的分类在「LitLoop 测试」下。

## 其他

- 与用户交流用简体中文；提交信息可以用英文。
- 项目要给别人用：功能要能自动探测、靠配置驱动，不写死某台电脑的路径。
