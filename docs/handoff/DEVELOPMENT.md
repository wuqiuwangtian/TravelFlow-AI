# 接续开发与隔离验收

2026-10-28，本机工作区 `/home/wqwt/code/TravelFlow-AI`，WSL Arch＋zsh。当前 WSL Node v26.3.0、Python 3.13.13，后端虚拟环境位于 `backend/.venv`；pnpm、uv 已安装。这是环境记录，不是修改项目最低版本要求。

## 环境与日常运行

- 前端 Next.js 16.4.1／React 19.3.0／TypeScript 5.9.3；后端 FastAPI／LangGraph／SQLite。具体依赖以 `package.json`、pnpm 锁文件和 `backend/pyproject.toml`／uv 锁文件为准。
- 正常前端 3100，正常后端 8000；本站 `/api/backend` 代理到服务端 `BACKEND_URL`。普通使用和首次安装见 [README](../../README.md)。已有 `.env` 不复制覆盖，不回显密钥。
- 本机终端工具调用使用明确 cwd 与 `login:false`。构建、本地监听、Windows 进程、Git 元数据和网络在当前 Codex 沙箱中通常需工具执行权限；依当前会话权限处理。

## 后端检查

以下命令在 `backend/` 执行，使用既有虚拟环境：

```sh
.venv/bin/ruff check app tests
.venv/bin/ruff format --check app
.venv/bin/mypy app
.venv/bin/python -m pytest tests -q
```

本机完整基线为 564 项、mypy 115 个源文件。默认沙箱曾使异步测试停在早期用例；本地允许的执行环境中完整测试约 17 秒。遇此情况先确认运行环境和进程，不改生产异步逻辑来绕过测试。

## 前端检查与浏览器回归

以下命令在 `frontend/` 执行：

```sh
pnpm typecheck
pnpm lint
pnpm check:design
pnpm build
PLAYWRIGHT_BROWSERS_PATH=/tmp/travelflow-browser pnpm exec playwright test --workers=1
```

`/tmp/travelflow-browser` 是本机已有浏览器缓存，缺失时按 Playwright 常规安装；不把此缓存当作交接材料。全套当前 330 项；专项可指定 `tests/e2e/ordinary-flow.spec.ts`、`journey.spec.ts`、`recovery.spec.ts` 或 `theme-settings.spec.ts`。

- 配置自动启动临时后端 8137 和生产前端 3137，`reuseExistingServer:false`；运行前确认没有同端口其他服务，不杀不属于本任务的进程。
- 使用 1 worker，避免临时 SQLite 并发锁。先完成 build，再运行浏览器；期间冻结业务源码，不重写 `.next`。
- `backend/tests/browser_server.py` 使用临时数据库、`Settings(_env_file=None)`、固定模型／检索／地图替身，不读取个人数据或真实配置。
- 失败截图与 trace 在忽略的 `frontend/test-results/`，下一次运行可能清理。需要保留的证据先归档，最终正式原图已有文档索引。
- 手机宽度用 `document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1`，不能用可能被内容撑宽的 `innerWidth`。
- 元素可访问名称使用真实 role／label。带初始文本的 textarea 在测试中优先用 `getByRole('textbox', {name: ...})`；分叉合法继承的历史不能误判为串分支。

格式工具当前在 `/tmp/travelflow-format/node_modules/prettier/bin/prettier.cjs`，只是本机便利工具；新环境可另装，不将临时路径写成项目运行依赖。不要为了格式化一行文档重排整个历史文件。

## Windows 原生核对材料

已归档：[普通主流程](native-qa/ordinary-flow.cjs)、[三主题](native-qa/themes.cjs) 和 [共用运行器](native-qa/runtime.cjs)。用于原生 Edge 两种视口复核，不替代完整 Playwright 集合。

先用两个终端启动独立 QA 服务。第一个在仓库根目录：

```sh
backend/.venv/bin/python backend/tests/browser_server.py
```

第二个在 `frontend/`，已有当前生产构建后：

```sh
BACKEND_URL=http://127.0.0.1:8137 node node_modules/next/dist/bin/next start --hostname 127.0.0.1 --port 3137
```

本机 PowerShell 示例，仓库路径按实际发行版与用户目录替换：

```powershell
$qaNode = "$env:USERPROFILE\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe"
$qaRoot = '\\wsl.localhost\Arch\home\wqwt\code\TravelFlow-AI'
& $qaNode "$qaRoot\docs\handoff\native-qa\ordinary-flow.cjs"
& $qaNode "$qaRoot\docs\handoff\native-qa\themes.cjs"
```

脚本默认使用 Windows 用户目录内既有 Playwright 和已安装 Edge；可用 `--playwright <package.json>`、`--edge <msedge.exe>`、`--output <directory>` 指定位置，`--help` 查看用法。缺少运行库时按实际环境配置，不依赖旧 `/tmp` 驱动脚本。

- 仅连接 127.0.0.1:3137；先只读确认测试专用端点，再创建隔离样本。不要指向正常个人数据服务。
- 默认输出为 `frontend/test-results/native-qa/ordinary-flow` 或 `themes`，均已忽略；不会覆盖已提交原始截图。
- 脚本已处理 UNC 上相对 CommonJS 加载的兼容性，保留原生 `createRequire` 锚点。
- 两份脚本顺序运行；结束关闭各自浏览器，并停止自己启动的 QA 前后端。不要与完整 Playwright 同时占用 3137／8137。
- 旧静态样稿 18487 只用于历史选型；旧 4387 曾处在 Windows 保留端口范围，WSL 返回 200 不代表 Windows 能访问，不修改系统端口范围或防火墙来绕过。

## 提交与敏感内容

按可验收小阶段提交，测试失败、未确认结果及真实服务边界如实记录。推送前检查待提交路径、实际 diff、当前远端和敏感内容，确认不包含 `.env`、数据库、票据或私钥；不输出密钥，也不强推。

原对话有一次性 Git／SSH 兼容脚本留在 `/tmp`，不作为接续前提。新会话检查实际 Git 配置与可用凭据，不复制个人 SSH 配置、凭据或原始环境文件进入仓库。新版本、部署、发布和新聊天作为独立用户动作处理。
