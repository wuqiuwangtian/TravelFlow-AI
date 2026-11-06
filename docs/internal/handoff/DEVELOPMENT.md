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

## 跨仓库开发与验收

本仓库负责电脑端Windows／WSL入口与现有网页。Android原型、工程生成器、原生SQLite插件、回显服务、TS投影和移动CI已迁入 [TravelFlow-Mobile](https://github.com/wuqiuwangtian/TravelFlow-Mobile)；独立准备与APK验收见该仓库[开发维护入口](https://github.com/wuqiuwangtian/TravelFlow-Mobile/blob/main/docs/internal/MAINTAINING.md)。旧静态入口／导出探针由原生原型替代并移除，历史源码与验收可从本仓库提交974743f追溯；不是正式网页功能删除。

电脑端继续维护 [接口能力清单](../MOBILE_CAPABILITIES.md)，在根目录运行 `python backend/scripts/check_mobile_inventory.py` 或 `uv run --project backend python backend/scripts/check_mobile_inventory.py`；Windows原生可用 `py backend/scripts/check_mobile_inventory.py`。清单声明覆盖不代表手机已完成迁移。

当前契约为 [contracts/ledger-v1](../../../contracts/ledger-v1/SPEC.md)，仅覆盖账目只读投影。根目录执行：

```sh
node scripts/check-contracts.mjs
node --test scripts/*.test.mjs
uv run --project backend python backend/scripts/export_ledger_vectors.py --check
```

契约检查默认核对清单与摘要；完整版本保护使用 `node scripts/check-contracts.mjs --baseline <完整已发布提交SHA>`，基线缺失会失败。Node回归覆盖发布版本保护与start.sh失败退出；Python检查用正式模型核对14组虚构样例；两项纳入本仓库CI。移动仓库单独校验快照与TS实现，不在桌面CI安装移动依赖或SDK。首次提取来源为本仓库提交015ca90；移动快照记录完整提交SHA。既有版本目录不能原地改写；新语义需建新版本、审阅差异并与移动端共同验收，不能盲目重生成旧样例消除失败。源码和契约升级不触碰个人数据。

SDK工具缓存仍在本机 `~/.cache/travelflow-p0-android/`，用户已授权用于隔离原型；它不属于本仓库，移仓不会卸载或重新接受许可。先前模拟器交互证据是历史结果，迁移后的构建与检查必须分别记录。桌面下一项按专项计划P0验证Windows／WSL免终端入口、数据目录识别与安全启停，随后继续连接原型；不继续在此仓库扩展Android实验。


## 桌面源码启动故障核对

`start.sh`健康检查需HTTP成功，后端或前端超过等待次数就失败退出、进入清理，不打印“已启动”或打开浏览器。该修复不改变数据路径和正式业务。运行 `bash -n start.sh` 及 `node --test scripts/startup.test.mjs`：用临时目录、假uv／pnpm／curl和短生命周期假服务验证后端超时、HTTP500、前端超时；不安装依赖、不启动用户后端、不读取个人.env。测试加速等待次数只发生在假seq／sleep中，正式等待次数不变。

这不是完整进程管理验证：真实uv／pnpm子进程树、Windows start.bat、重复实例、外来端口、休眠唤醒及原生／WSL数据切换仍需后续P0实测。日常使用当前仍需源码环境与终端。Windows脚本的超时和退出语义尚未纳入本批修复，应与管理入口一起核对，不能拿POSIX测试替代Windows证据。


## D0运行管理核心验证

仓库根目录使用Python 3.13+，无新增运行依赖。`desktop/`是可复用进程管理核心，当前仅开放隔离`--probe`配置：启动两个虚构HTTP服务及真实子孙进程，不加载.env、不调用外部服务、不打开或创建SQLite。数据库参数只是显式的路径标识，必须位于自己的隔离状态目录，不能将此结果当作真实账本／配置解析已完成。当前仍为开发命令，不是日常用户的免终端入口。

```sh
python -m desktop inspect --state-dir .desktop-run --database .desktop-run/probe.sqlite
python -m desktop start --probe --state-dir .desktop-run --database .desktop-run/probe.sqlite --ports 18520 18521
python -m desktop status --state-dir .desktop-run
python -m desktop stop --state-dir .desktop-run
```

`inspect`不创建状态目录；start返回ready与两个服务的实例标识，再次用相同参数start复用同一实例。status不输出控制token。stop通过本机控制通道确认实例，再等待自己拥有的进程组退出；它不从状态文件读取PID来杀进程。外来端口被占用会失败，不结束原服务。停止后selection.json保留模式／项目／路径选择，换数据库／运行模式要用新的隔离状态目录，不能静默覆盖原选择。状态目录已忽略，不提交或分享runtime.json及日志；默认POSIX控制文件为0600，Windows继承ACL尚需正式应用接入前核对。

状态与日志位于传入目录；探针只绑定127.0.0.1，控制接口要求私有token及正确Host，拒绝不匹配Origin，禁用本机请求的环境代理和重定向。状态文件失效时stop明确拒绝，保留诊断，不报告已经停止。正常stop、第二个服务启动失败、管理器异常退出均需清理其实际子孙进程。POSIX采用独立进程组及父管道关闭检测；Windows采用[Job Object](https://learn.microsoft.com/en-us/windows/win32/procthread/job-objects)，受控包装进程先加入Job再获准派生子进程，使用kill-on-close，不靠猜测PID或taskkill。

验收命令：`python -m unittest discover -s desktop/tests -v`。九项使用随机空闲端口、中文／空格临时路径、真实Python子孙进程及虚构数据，覆盖重复／并发启动、端口占用、启动部分失败、跨选择拒绝、失效状态不杀无关PID、控制鉴权／Origin、管理器异常退出。Windows测试使用只读系统查询确认进程状态，不用os.kill(pid, 0)作存活探测。CI新增ubuntu-latest／windows-latest独立任务；远端结果按提交核对，不把Linux本机通过写成Windows原生通过。

剩余工作：接入实际Python／Next并实现可验证的服务实例标识、配置与真实数据目录识别、首次准备和日常启动分离；随后验证Windows／WSL统一管理入口、模式切换和UI，真实应用与WSL互操作仍未覆盖。当前探针不表示完整D0、桌面安装包或免终端已完成，不改已有start.sh／start.bat使用方式。
