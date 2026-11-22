# 更新日志

## v1.18.4 (2026-11-22)

### 新增功能

- **内置 Agent 分任务模型与检索计划预览**：支持为规划、打分等不同步骤分别指定模型，发起深度检索前可先预览并修改检索计划。
- **支持通过 PyPI 安装与 MCP 即装即用**：现已发布至 PyPI，可通过 `pip install litloop` 或 `uvx litloop run` 直接接入各类 MCP 客户端；新增 `smithery.yaml` 配置。
- **提供 Windows MSI 安装包**：除 NSIS（`*_x64-setup.exe`）外新增 MSI（`*_x64_zh-CN.msi`），支持自定义安装路径与覆盖安装时自动关闭旧进程。
- **筛选工作台增强**：新增批量操作、列表内筛选与多步撤销；支持通过 `litloop://` 协议链接直接唤起桌面端对应会话。
- **检索断点续跑与结果缓存**：深度检索中断后可继续执行；快速检索新增 7 天结果缓存（带安全绕过规则）。
- **一键导出诊断包**：桌面端、CLI 与 API 均支持导出排障诊断包，便于反馈问题。

### 改进与修复

- **冷启动与安装包体积优化**：后端懒加载、Tauri 并行启动以及精简打包运行时，缩短启动耗时并减小安装包体积。
- **底层存储迁移至 SQLite**：追踪主题、筛选会话、桌面端决策与深度检索候选池均迁移至 SQLite，支持自动迁移。
- **安装与卸载体验**：NSIS 卸载时新增弹窗询问是否保留 `~/.litloop` 用户数据（默认保留）；优化错误提示的下一步操作指引与可选组件安装进度。
- **空配置可用与空结果说明**：未填 Key 时也可先试跑快速检索；检索无结果时明确给出原因。
- **Windows 安装与打包兼容修复**：修复 Windows 下 npm `claude` shim 与新版 setuptools PEP 639 许可证分类器问题（感谢 [FSJDIFE](https://github.com/FSJDIFE) 在 #4 的贡献）。

### 安装与校验

- Windows 桌面端二选一：`LitLoop_1.18.4_x64-setup.exe`（NSIS，推荐个人使用）或 `LitLoop_1.18.4_x64_zh-CN.msi`（MSI，可选安装目录）。安装包未签名，首次运行 SmartScreen 点「更多信息 → 仍要运行」。
- 命令行 / MCP：`pip install litloop` 或 `uvx litloop run`。
- 校验文件见附件 `SHA256SUMS.txt`；使用说明与已知限制见 [README](https://github.com/dysprosium231/litloop-paper-agent#readme)。

**完整变更记录**：https://github.com/dysprosium231/litloop-paper-agent/compare/v1.18.2...v1.18.4
