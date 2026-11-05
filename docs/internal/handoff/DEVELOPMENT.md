# 开发与隔离验收

2026-10-28记录的本机环境（新环境须重新核实），工作区为仓库根目录（示例路径 `<repo>`），WSL Arch＋zsh。当前 WSL Node v26.3.0、Python 3.13.13，后端虚拟环境位于 `backend/.venv`；pnpm、uv 已安装。这是环境记录，不是修改项目最低版本要求。

## 环境与日常运行

- 前端 Next.js 16.4.1／React 19.3.0／TypeScript 5.9.3；后端 FastAPI／LangGraph／SQLite。具体依赖以 `package.json`、pnpm 锁文件和 `backend/pyproject.toml`／uv 锁文件为准。
- 正常前端 3100，正常后端 8000；本站 `/api/backend` 代理到服务端 `BACKEND_URL`。普通使用和首次安装见 [README](../../../README.md)。已有 `.env` 不复制覆盖，不回显密钥。
- 本机终端工具调用使用明确 cwd 与 `login:false`。构建、本地监听、Windows 进程、Git 元数据和网络在当前执行环境的沙箱中通常需工具执行权限；依当前会话权限处理。

## 后端检查

以下命令在 `backend/` 执行，使用既有虚拟环境：

```sh
.venv/bin/ruff check app tests
.venv/bin/ruff format --check app
.venv/bin/mypy app
.venv/bin/python -m pytest tests -q
```

历史测试数量不作为接手基线；当前结果见[项目现状](../PROJECT_STATUS.md#当前接续状态)。只按本次改动选检查，不为接手重复跑全部历史阶段。

## 前端检查与浏览器回归

以下命令在 `frontend/` 执行：

```sh
pnpm typecheck
pnpm lint
pnpm check:design
pnpm build
PLAYWRIGHT_BROWSERS_PATH=/tmp/travelflow-browser pnpm exec playwright test --workers=1
```

`/tmp/travelflow-browser` 是本机已有浏览器缓存，缺失时按 Playwright 常规安装；不把此缓存当作交接材料。上一轮全量330项通过，2026-10-29另增16项条件与路书恢复测试，最终相关80项通过；专项可指定 `tests/e2e/planning-recovery.spec.ts`、`ordinary-flow.spec.ts`、`journey.spec.ts`、`recovery.spec.ts` 或 `theme-settings.spec.ts`。

2026-10-29本环境Turbopack因创建本地端口权限失败，申请本机执行后仍失败；使用内置 `pnpm exec next build --webpack` 完成生产构建及上述浏览器验证，没有改动默认脚本或依赖。新环境以实际权限与构建结果为准。

- 配置自动启动临时后端 8137 和生产前端 3137，`reuseExistingServer:false`；运行前确认没有同端口其他服务，不杀不属于本任务的进程。
- 使用 1 worker，避免临时 SQLite 并发锁。先完成 build，再运行浏览器；期间冻结业务源码，不重写 `.next`。
- `backend/tests/browser_server.py` 使用临时数据库、`Settings(_env_file=None)`、固定模型／检索／地图替身，不读取个人数据或真实配置。
- 失败截图与 trace 在忽略的 `frontend/test-results/`，下一次运行可能清理。需要保留的证据先归档，最终正式原图已有文档索引。
- 手机宽度用 `document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1`，不能用可能被内容撑宽的 `innerWidth`。
- 元素可访问名称使用真实 role／label。带初始文本的 textarea 在测试中优先用 `getByRole('textbox', {name: ...})`；分叉合法继承的历史不能误判为串分支。

格式工具当前在 `/tmp/travelflow-format/node_modules/prettier/bin/prettier.cjs`，只是本机便利工具；新环境可另装，不将临时路径写成项目运行依赖。不要为了格式化一行文档重排整个历史文件。

## Windows 原生核对材料

已归档：[普通主流程](./native-qa/ordinary-flow.cjs)、[三主题](./native-qa/themes.cjs) 和 [共用运行器](./native-qa/runtime.cjs)。用于原生 Edge 两种视口复核，不替代完整 Playwright 集合。

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
$qaRoot = '<Windows 可访问的仓库路径>'
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

## 接手后的最短验收路径

1. 从[开发维护入口](../MAINTAINING.md)读当前状态与路线图，核对Git分支、未提交改动和负责范围。
2. 安装依赖／配置按[README](../../../README.md)，已有环境先检查 `backend/.venv` 和前端依赖，不重装或覆盖配置。锁文件是依赖版本依据；本文机器版本只是历史环境记录。
3. 只改文档时检查差异、内部链接和引用路径，不启动模型或重跑业务。后端行为修改按上文运行Ruff、mypy与相关pytest；前端行为修改先完成生产构建，再运行相关浏览器场景。
4. 浏览器验收应由仓库测试配置启动隔离服务；预期3137页面可打开、8137健康检查成功，测试用例通过。不得把正常8000个人数据库服务当QA后端。完整入口和场景见[测试说明](../../../frontend/tests/e2e/README.md)。
5. 阶段完成更新[现状](../PROJECT_STATUS.md#当前接续状态)的版本／证据／未完成工作，以及[路线图](../PRODUCT_ROADMAP.md#当前执行任务)中的任务状态；历史证据按需补到已有专题，不另建交接总览。

`/tmp`工具与本机浏览器缓存只是可选便利，不是新AI接手的前置条件。没有Windows运行库或外部服务凭据时标注未验证，不把静态检查写作实际运行成功。

## 依赖安全检查

前端执行 `pnpm install --frozen-lockfile`、`pnpm audit --audit-level=moderate`；审计包括开发依赖，CI遇到中危及以上告警或审计请求失败会阻断。修复后运行完整lint、类型与设计检查、生产构建及受影响浏览器流程；先构建再执行依赖 `.next` 类型的检查，不并发改写构建目录。

临时安全版本覆盖在 `frontend/pnpm-workspace.yaml`（pnpm 12），不放package.json，不通过audit忽略列表掩盖问题。当前覆盖zod的受影响4.x与minimatch的受影响7—10.x；旧minimatch 3.x不在本次公告范围。待上游兼容约束允许安全版本后再移除覆盖，并重新检查锁文件、审计和lint。GitHub Dependabot的告警状态与pnpm审计分别核对，清零不替代业务访问边界和数据恢复验收。

## P0性能与打包边界工具

在backend执行 `PYTHONPATH=. .venv/bin/python scripts/benchmark_local.py --samples 30 --output /tmp/local-api.json`；临时数据库自动清理，禁用外部服务，不读取个人配置。每类操作至少30次才用作本地基线；CI用 `--samples 2` 冒烟，仅验证工具与数据不变量，不设机器时延阈值。报告分段有嵌套，不相加；ASGI数据不含网络／代理／浏览器／Android或冷启动。

在已安装前端依赖后执行 `node scripts/probe-static-export.mjs`。它复制必要源码到临时目录、改临时配置并尝试静态导出，结束清理，不覆盖正常.next。输出blocked表示已复现动态路由阻塞，不是静态构建通过；未知构建错误返回非零。当前结果和后续动作见[计划P0证据](../MOBILE_CONTINUITY_PLAN.md#p0现有证据与接续点2026-11-05)。
