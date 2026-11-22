# MCP 目录与社区提交素材

`litloop` 已于 2026-11-22 发布至 [PyPI](https://pypi.org/project/litloop/)（`pip install litloop` / `uvx litloop run`），本文件整理了提交各大 MCP 目录时可直接复用的信息。

## 基础元数据

- **Name**: `LitLoop`
- **Package (PyPI)**: `litloop`
- **Repository**: `https://github.com/dysprosium231/litloop-paper-agent`
- **License**: `Apache-2.0`
- **One-liner**: `Search, screen, download, and file papers into Zotero — 21 tools`
- **Category / Tags**: `research`, `academic`, `literature-review`, `systematic-review`, `zotero`, `pdf`, `mcp-server`
- **Server Config (`server.json` / `smithery.yaml`)**: 已放在仓库根目录。

## 标准客户端配置

```json
{
  "mcpServers": {
    "litloop": {
      "command": "uvx",
      "args": ["litloop", "run"]
    }
  }
}
```

## 目录提交清单

1. **Smithery**（https://smithery.ai/new）
   - 关联仓库 `dysprosium231/litloop-paper-agent`，自动读取根目录 `smithery.yaml`。
2. **Glama**（https://glama.ai/mcp/servers）
   - 提交仓库 URL `https://github.com/dysprosium231/litloop-paper-agent`。
3. **mcp.so / PulseMCP**
   - 项目名：`LitLoop`，安装方式：`uvx litloop run`（PyPI `litloop`）。
4. **awesome-mcp-servers** PR 条目模板

```markdown
- [dysprosium231/litloop-paper-agent](https://github.com/dysprosium231/litloop-paper-agent) 🐍 🏠 - Search, screen, download (open access, publisher & institutional WebVPN/CARSI/EZProxy), and file academic papers into Zotero (21 MCP tools).
```
