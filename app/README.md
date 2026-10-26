# LitLoop 桌面 app

Tauri 2 外壳 + React / shadcn/ui 界面 + LitLoop 的 Python 后台（`litloop.desktop.server`）。设计见仓库根目录 [CHARTER.md](../CHARTER.md)「可视化 app」，数据格式见 [docs/APP_DATA.md](../docs/APP_DATA.md)。

## 开发

需要 Node、Rust（MSVC 工具链）和装了 LitLoop 的 Python 环境。

```bash
cd app
npm install
LITLOOP_PYTHON=/path/to/python npm run tauri dev
```

`LITLOOP_PYTHON` 指向装了 LitLoop 的 Python；不设时用 PATH 里的 `python`。窗口启动时由外壳拉起后台（只监听 127.0.0.1 的随机端口，带随机令牌），关闭窗口时一并结束。

改了 Python 后台（后台不会热更新）或 Rust 外壳后，用重启脚本：它结束旧窗口、后台和本项目的 Vite，等端口空出来，再单独启动 `tauri dev`，窗口起来就返回（输出在 `app/.dev.log`）：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File app/scripts/dev-restart.ps1
```

只调界面、不开 Tauri 窗口时，可以自己起后台和 Vite，再在浏览器里打开 `http://localhost:1420/?port=8765&token=devtoken`：

```bash
python -m litloop.desktop.server --token devtoken --port 8765
npm run dev
```

## 结构

- `src/App.tsx`：筛选页（会话切换、进度、详情与评分、键盘操作）。
- `src/components/app-sidebar.tsx`：左侧图标栏和本批文章列表，改自 shadcn 区块 sidebar-09。
- `src/components/settings-view.tsx`：设置页（分组、检测、接口额度）。
- `src/components/downloads-view.tsx`：下载页（待下载、下载记录、推送 Zotero）；后台是 `litloop/desktop/download_jobs.py`。
- `src/components/ui/`：shadcn 组件，按官方方式添加（`npx shadcn add …`）；颜色主题在 `src/index.css`。改过的组件：`switch.tsx`（40×22，关闭为中灰轨道、白色圆钮）。
- `src/lib/api.ts`：调用后台的接口。
- `src-tauri/src/lib.rs`：启动后台、把端口和令牌交给界面、退出时结束后台。
