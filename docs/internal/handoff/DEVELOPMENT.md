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

仓库根目录使用Python 3.13+，无新增运行依赖。`desktop/`是可复用进程管理核心，`--probe`仍保留为独立核心验证配置：启动两个虚构HTTP服务及真实子孙进程，不加载.env、不调用外部服务、不打开或创建SQLite。数据库参数只是显式的路径标识，必须位于自己的隔离状态目录，不能将此结果当作真实账本／配置解析已完成。当前仍为开发命令，不是日常用户的免终端入口。

```sh
python -m desktop inspect --state-dir .desktop-run --database .desktop-run/probe.sqlite
python -m desktop start --probe --state-dir .desktop-run --database .desktop-run/probe.sqlite --ports 18520 18521
python -m desktop status --state-dir .desktop-run
python -m desktop stop --state-dir .desktop-run
```

`inspect`不创建状态目录；start返回ready与两个服务的实例标识，再次用相同参数start复用同一实例。status不输出控制token。stop通过本机控制通道确认实例，再等待自己拥有的进程组退出；它不从状态文件读取PID来杀进程。外来端口被占用会失败，不结束原服务。停止后selection.json保留模式／项目／路径选择，换数据库／运行模式要用新的隔离状态目录，不能静默覆盖原选择。状态目录已忽略，不提交或分享runtime.json及日志；默认POSIX控制文件为0600，Windows继承ACL尚需正式应用接入前核对。

状态与日志位于传入目录；探针只绑定127.0.0.1，控制接口要求私有token及正确Host，拒绝不匹配Origin，禁用本机请求的环境代理和重定向。状态文件失效时stop明确拒绝，保留诊断，不报告已经停止。正常stop、第二个服务启动失败、管理器异常退出均需清理其实际子孙进程。正常stop还等待已认证管理器的内核进程句柄／Linux pidfd确认退出，防止Windows日志句柄仍被占用就提前报成功；不依靠反复猜测PID。Linux内核须支持pidfd，启动前先检查退出确认能力，缺少该能力会拒绝启动，不声明兼容尚未验证的老内核。POSIX采用独立进程组及父管道关闭检测；Windows采用[Job Object](https://learn.microsoft.com/en-us/windows/win32/procthread/job-objects)，受控包装进程先加入Job再获准派生子进程，使用kill-on-close，不靠猜测PID或taskkill。

验收命令：`python -m unittest discover -s desktop/tests -v`。九项使用随机空闲端口、中文／空格临时路径、真实Python子孙进程及虚构数据，覆盖重复／并发启动、端口占用、启动部分失败、跨选择拒绝、失效状态不杀无关PID、控制鉴权／Origin、管理器异常退出。Windows测试使用只读系统查询确认进程状态，不用os.kill(pid, 0)作存活探测。CI新增ubuntu-latest／windows-latest独立任务；远端结果按提交核对，不把Linux本机通过写成Windows原生通过。

实际应用接入见下节；统一管理入口、跨模式切换和UI仍待验证。probe不表示完整D0、桌面安装包或免终端已完成，不改已有start.sh／start.bat使用方式。


## D0实际应用准备与验收

本阶段提供源码运行管理命令与下方浏览器管理原型，尚无统一Windows／WSL安装包。Python 3.13+、uv、pnpm及Node是准备前提（CI使用Python3.13／Node22）。在仓库根目录执行：

```sh
python -m desktop prepare
python -m desktop doctor
```

prepare才运行冻结依赖安装和Webpack生产构建；start不安装依赖、不编译。Python环境按模式保存于backend/.venv-desktop-*，构建保存于frontend/.next-desktop-*，桌面专用tsconfig另存忽略位置，不修改已跟踪tsconfig或默认.next路径。doctor核对解释器、Node版本／平台、构建ID和前端源码摘要；源码变化后需先停止，再prepare。准备／应用运行期间互斥保护同一模式的构建目录。

node_modules属于工作副本，不能在Windows和WSL中交替安装共享。首次准备会在忽略目录登记Node平台／架构，跨平台重装被拒绝；请为Windows原生与WSL各用一份源码检出。两种模式不共用Python二进制或构建产物，也不自动复制配置和SQLite。旧start.sh／start.bat实例不受新管理器协调，准备或运行前需先停止；当前未承诺对未受管理的实例进行全面检测。

### 隔离QA配置

此模式运行真实API、Graph与SQLite，模型／检索／地图沿用固定验收替身，清空子进程继承的供应商Key。不会读个人.env，配置写回目标仅为自己的qa.env；不要在QA界面输入真实Key。数据库仅允许在该隔离状态目录内，正常停止后保留，用于重启验收。

```sh
python -m desktop inspect --qa --state-dir .desktop-run/qa
python -m desktop start --qa --state-dir .desktop-run/qa --ports 18520 18521
python -m desktop status --state-dir .desktop-run/qa
python -m desktop stop --state-dir .desktop-run/qa
```

就绪后可打开 `http://127.0.0.1:18521`；第一个端口为Python，第二个为Next。新建虚构旅程后停止、原参数重启，应保留相同会话ID。服务记录的database来自实际连接的PRAGMA database_list，不只依据配置字符串猜测；前端启动就绪还核对本次实例及生产构建ID。状态中的selection是启动前选择，实际打开情况见services中的database_opened。普通启动未启用管理配置时，前端内部健康接口返回404；启用后仅接受私有服务证明token，token不进入浏览器。

### 明确选择真实应用配置

普通应用配置使用正式app.main，不包含测试路由或服务替身。配置准备遵循[README](../../../README.md)，prepare不会生成或覆盖个人配置。以下命令由使用者主动选择已有配置／数据，不是本轮个人数据库验收步骤：

```sh
python -m desktop inspect --app --env-file backend/.env --state-dir .desktop-run/app
python -m desktop start --app --env-file backend/.env --state-dir .desktop-run/app --ports 8000 3100
python -m desktop stop --state-dir .desktop-run/app
```

先核对inspect显示的绝对数据库地址、配置来源和database_exists。相对数据库路径始终相对backend解析；--database可显式覆盖。数据库不存在默认拒绝，只有使用者核对路径后明确传--allow-new-database才允许初始化，且父目录须存在；目录不能冒充数据库文件。APP_ENV_FILE进程变量选择配置读取文件，设置页写回该文件、cache_clear后仍从同文件重载；状态不输出配置内容。前端进程不继承后端Key／配置地址，代理目标在启动时绑定当前后端端口，本机请求绕过环境代理。

管理器为所选数据库加协作锁及.travelflow-owner.json模式／项目标记，拒绝不同模式／项目／QA类型静默接管；不提供自动迁移。该保护只协调遵守管理器协议的实例，不能代替旧脚本退出或正式备份。现阶段两模式切换、升级／恢复、已有主库迁移仍需后续评审，不直接共享活跃SQLite。应用停止先认证请求后端完成生命周期关闭，随后回收进程组并确认管理器退出；关闭超时会报告未确认，不冒充已停止。端口预检只检查活动连接，最终仍以私有实例证明确认，避免把TCP TIME_WAIT当作仍有服务。

### 回归入口与当前边界

```sh
python -m unittest discover -s desktop/tests -v
```

默认跳过真实应用测试，便于仅有Python的环境检查核心与预检。先prepare，再在POSIX设置 `TRAVELFLOW_RUN_APP_TESTS=1`、Windows PowerShell设置 `$env:TRAVELFLOW_RUN_APP_TESTS='1'`，运行同一命令即验收真实代理、SQLite路径／重启持久化、运行中禁止准备、正式后端缺失库拒绝和显式初始化（均用临时库与空白配置，不调用真实供应商）。CI的Desktop application矩阵在Linux／Windows分别准备并开启该门禁。

运行状态现定期重验服务身份、SQLite路径、前端构建及代理目标；这不是供应商可用性或完整业务巡检。统一Windows／WSL入口、原生路径选择、固定浏览器Origin以保留草稿，以及数据切换／迁移评审仍是D0后续；D1继续备份与故障恢复。本轮未验证真实模型／地图调用，没有更改移动仓库或共享账目契约。


## D0可视运行管理原型

管理页面由Python标准库直接提供，不依赖Next／业务后端先启动。先安装Python3.13+；首次准备仍需要uv、Node、pnpm。原型使用现有共享三主题变量，没有引入桌面框架，也未替换旧start.sh／start.bat。

- Windows统一入口：安装含Tk的Python并关联.pyw后，可双击仓库根目录的[TravelFlow.pyw](../../../TravelFlow.pyw)，先选择环境再打开管理页，详见下节。Windows原生业务仍要求独立本机源码与工具链，WSL选项使用所选发行版自己的源码和解释器。
- Linux／WSL开发入口：仓库根目录运行 `python -m desktop console`，需要该环境可调用的默认浏览器。此直接命令仍依赖该环境的浏览器配置；Windows侧统一入口可以桥接WSL并交给Windows默认浏览器，见下节。打不开浏览器会明确报错，管理器保留，配置好浏览器后可重开。
- 默认管理目录是忽略位置 `.desktop-run/console-<mode>`；开发验收可加 `--state-dir /absolute/isolated/path`。重复打开复用同一管理进程；浏览器标签关闭不停止应用，无系统开机自启。

页面操作：选择真实应用或隔离体验 → 需要时点击“准备环境与构建” → 预览配置与数据 → 核对模式、绝对配置／SQLite地址和端口 → 勾选确认 → 启动 → 从“打开旅行助手”进入现有网页。真实应用必须指定已有配置文件；缺失数据库还需明确勾选创建，预览后配置文件内容或构建变化须重新预览。编辑表单或另一个页面产生新预览会清除旧确认，不能沿用旧回执启动新选择。隔离体验的自定义地址禁用，采用独立QA文件，勿在体验页输入真实Key。

准备、预览、启停在后台执行，页面给出等待并拒绝重复操作；失败保留表单，显示错误。管理器只保存配置／数据库地址、端口与使用方式，不保存Key或配置内容；重开记住上次地址，但不自动读取配置、不自动启动，需重新预览。真实应用按选择摘要隔离运行状态目录，不复制／迁移数据库；切换前须停止当前应用，底层构建／数据库协作锁仍保留。管理目录中preferences.json的active给出对应运行状态／日志目录；console.log记录准备与管理错误，manager.log／services.log位于该active目录，含私人信息，不提交或分享原始日志。

“停止旅行助手”只停业务服务，管理页面保留。“停止并退出管理器”先确认业务管理器及进程树退出，再关闭页面管理服务；退出响应发完后才关闭HTTP服务，避免响应截断。页面提示“管理器正在退出”，内核退出由自动验收确认。旧状态不能认证时保留现场、显示无法确认，不能按旧PID杀进程或直接显示已停止。

业务管理器每约2秒重新检查私有服务身份、实际数据库／配置路径、Next构建ID与代理目标；状态包含checked_at及逐服务healthy，失败时ready=false，恢复后可再次变true。初始路径不被后来异常响应替换。检查期间或休眠后可能短暂显示上次结果，页面显示检查时间；真实休眠唤醒、供应商故障和写入中崩溃恢复尚未验收。

安全边界：仅绑定127.0.0.1；管理API要求独立随机凭据及正确Host／Origin，不开放CORS，静态页设置CSP与no-store。管理入口凭据仅通过URL片段交给浏览器，立即清除地址栏并保存在会话存储；服务管理与业务身份凭据不会进入浏览器。不要分享带片段的初始链接、console.json或状态目录；Windows ACL验证仍未完成。该入口不是远程管理或跨端同步服务。

复跑沿用上一节桌面测试命令；开启TRAVELFLOW_RUN_APP_TESTS后包含管理API→实际应用→SQLite→退出／重开持久化、预览后配置变化拒绝启动。原生Windows CI验证管理API／进程与实际应用，不等于验证了文件关联双击和默认浏览器。浏览器实际点击证据与当前结果见[项目现状](../PROJECT_STATUS.md#d0可视运行管理原型2026-11-06)。统一环境选择与Windows路径对话框已接入下节；WSL文件选择见下节；人工实机交互、休眠及故障验证仍需补齐，安装包、常驻策略与默认模式仍待用户看过原型后决定。


## D0统一环境选择与原生路径

Windows双击TravelFlow.pyw先打开轻量Tk系统窗口（使用Python可选的Tcl/Tk组件，未新增第三方包或替换业务界面），运行模式初始为空，不因保存过选择就自动执行。选择Windows原生或WSL Linux，核对源码目录、发行版与已有Python3.13+解释器，再点击打开管理页。连接在后台执行，期间禁用重复操作；失败留在窗口内说明原因，恢复控件，不改用其他环境。关闭选择窗口不停止已打开的管理器，仍到管理页停止退出。

- Windows原生项目必须是独立本机磁盘源码目录；拒绝把WSL共享目录当成Windows源码使用。选择窗口本身可以从WSL共享目录运行，但原生业务目标不能共用这份WSL源码／node_modules。
- WSL必须明确选择已安装发行版；列举不安装发行版，连接时才启动所选环境。源码可填Linux绝对路径，也可用系统浏览选择同一发行版的 `\\wsl.localhost\<distribution>\...` 或 `\\wsl$\<distribution>\...`，路径转换保留Linux大小写，跨发行版目录被拒绝。Windows驱动器目录不被自动猜成/mnt路径。解释器可填名称／Linux绝对路径，浏览同一发行版的Python文件时也转换成Linux路径。
- 目标源码须含本轮启动桥接，目标Python须3.13+，不隐式下载解释器、安装依赖或克隆源码。失败只显示受控错误，不记录原始桥接stdout；stdout包含一次本地管理凭据，禁止复制进文档／日志。内部桥接与管理版本以desktop/bridge.py和desktop/console.py为准，与contracts业务契约无关。

WSL调用使用[官方命令](https://learn.microsoft.com/en-us/windows/wsl/basic-commands)的显式发行版／工作目录和[--exec语义](https://github.com/microsoft/WSL/blob/master/localization/strings/en-US/Resources.resw)，全程传参数数组，不拼bash／PowerShell／cmd命令。内部_connect校验请求nonce、目标Python版本及实际源码根目录；启动器再校验模式／源码、管理实例身份和本机HTTP连接后，才用Windows默认浏览器打开本机URL。只传本机地址，不开放远程端口或修改防火墙／WSL配置；WSL本机端口转发不通会报告未确认，不回退Windows。WSL非交互启动不读取shell配置，桥接仅把已存在的 ~/.local/bin 与 ~/.local/share/pnpm加入目标管理器PATH；nvm等自定义工具链仍需使用者配置，不自动选择版本。

启动器仅把上次成功连接的模式、源码地址、发行版和解释器保存在忽略位置.desktop-run/launcher.json，不保存凭据、配置内容或数据库，也不把其当默认模式。切换只是打开另一环境的管理页，不复制数据、不自动启动业务；先停止原环境，再在新管理页明确预览。别名／链接导致实际源码位置不同会拒绝，需选择实际根目录；本轮不提供主库迁移。

Windows原生管理页增加“选择已有配置文件／数据库”，使用独立Tk子进程的[原生文件名对话框](https://docs.python.org/3.13/library/dialog.html#native-load-save-dialogs)，不读取所选文件内容、不创建文件。回执仅更新本标签页草稿并清除旧确认；取消保留原值，仍须预览后才启动。隔离体验不启用自定义文件选择；WSL系统文件选择已接入下节，未配置该能力的WSL／Linux仍可手填本环境地址。缺少Tk时有明确错误，保留手动输入；选择等待上限300秒，不由此终止业务服务。

新入口拒绝复用旧版管理API，不强制结束进程。先用原管理页停止并退出；找不到页面时，在对应环境及源码目录运行 `python -m desktop console-stop`（自定义管理目录还需传同一--state-dir）。它通过旧管理通道认证并等待实际退出；不可认证时保留现场，不能按持久化PID终止或宣称已停止。新旧状态目录与个人配置均不删除。

CI的Windows核心任务显式开启TRAVELFLOW_RUN_NATIVE_UI_TESTS，操作真实Tk控件、模拟文件对话框返回；Windows目标桥接另用中文／空格独立源码副本验证。Linux跳过Windows专属用例，不当作已验证。WSL真实互通不在GitHub托管机安装发行版，使用本机隔离源码副本单独验证；当前证据与未验证边界统一见项目现状。真实系统文件对话框的人工点击、.pyw文件关联／默认浏览器、休眠唤醒、Windows ACL、数据切换和恢复仍按D0后续验收，不因桥接通就收口。


## D0异常退出与运行状态恢复

本节恢复的是本地控制状态，不是数据库备份／恢复。入口是管理页“核对并恢复运行状态”；正常运行时只核对现有实例，不自动停止或重新启动。原选择文件损坏／缺失、尚有其他未接续实例状态时，先显示状态不明并禁止新启动，保留原文件，避免覆盖指向或误开新库。管理版本以源码定义为准；升级前先退出旧管理页，丢失页面时用对应环境的console-stop，不强杀旧版本。

恢复只检查当前管理目录的qa、instances及合法的原目标，不扫描个人数据目录。发现一个可认证、属于同项目／模式的实例时，恢复它的配置／数据地址与端口选择，业务实例保持不变；多个实例或仍有无法认证的持锁实例时拒绝处理。找不到活实例时，仅在取得其独占运行锁后，把runtime.json和过期pending-*.json控制记录移入该实例目录的recovery/<随机ID>；损坏preferences.json保留到管理目录的同类归档。selection.json、配置文件和SQLite本体不移动、不删除、不打开；归档含私人地址及旧控制凭据，保持忽略和私有权限，不分享原始内容。

公共start会先生成短期启动票据；管理进程取得实例锁后才消费票据并启动服务。恢复会归档旧票据，晚到的旧启动进程因票据已失效而拒绝启动，防止恢复刚完成又被旧请求启动。已取得运行锁的启动过程不被取消；等待就绪或查看日志，不按保存的PID终止。状态文件缺失但仍有票据／运行锁时，页面和CLI stop均不会报告已经停止。异常符号链接锁会被拒绝，不打开其目标；这不是完整的恶意本地用户／Windows ACL审计。

恢复会清除旧预览与确认，操作后重新预览并确认才能启动。仅在找回活实例且用户未编辑草稿时回填选择；其他情况保留当前表单，取消或失败不覆盖草稿。页面显示归档位置，区分状态不明、正常运行与已停止，运行区同时显示实际配置文件和数据库。未预料的后台异常也会恢复控件并显示异常类别，不把私密异常值回显到页面。

隔离验收仍用 `TRAVELFLOW_RUN_APP_TESTS=1 python -m unittest discover -s desktop/tests -v`（PowerShell先设置同名环境变量）。本轮实际终止的仅为测试自己创建并持有Popen句柄的临时进程，包含：管理页进程异常退出后原应用继续运行；选择文件再损坏后重开、找回同一业务实例；业务管理器异常退出后归档控制记录、重新确认启动，已提交的虚构会话仍在。另有持锁拒绝、延迟旧启动取消、元数据字节保留、异常可见与链接锁拒绝回归。测试不读取个人配置／主库，不调用真实模型或地图，不让整机休眠。

只证明上述已提交记录和控制状态案例，不宣称写入中断、断电、损坏SQLite、遗留内部启动命令或所有系统版本都已恢复。新票据保证适用于本版本公共启动路径；升级前仍须完整停止旧版。数据库备份、迁移、损坏恢复与回退按D1另行安排；真实休眠唤醒、文件关联与默认浏览器人工操作仍需实机证据。


## WSL业务文件选择

Windows资源管理器打开TravelFlow.pyw，选择WSL发行版、该发行版源码目录和Python3.13+，点击打开管理页。真实应用模式可点“选择已有配置文件／数据库”，系统窗口初始位置为当前发行版的源码目录；选择 `\\wsl.localhost\<当前发行版>\...` 或 `\\wsl$\<当前发行版>\...` 下的已有文件，回执转换成Linux绝对路径。支持普通及扩展UNC前缀、中文／空格等路径；其他发行版、普通网络共享或C:等盘符回执拒绝，不自动猜测/mnt映射。需要使用挂载盘时，可显式选择当前发行版命名空间下的路径或手填Linux地址。

应用只接收文件名并检查当前Linux环境下的普通文件是否存在，不读取业务配置／数据库内容、不创建文件或复制数据。取消保留原值；被拒绝的选择和互通故障显示错误，保留草稿。选择成功只改当前标签页的路径草稿并清除旧确认，仍需预览实际地址才能启动，隔离体验的自定义文件选择保持禁用。

Windows启动器通过私有桥接提供宿主Python地址及明确发行版，WSL以wslpath转换已存在的宿主程序位置。独立native_dialog.py只依赖标准库Tk，不从WSL源码导入Windows业务环境；所有地址作为参数数组传递，不拼shell命令。管理目录windows-picker.json仅保存宿主路径、发行版和项目归属，不含密钥或管理凭据；管理页只展示能力与范围提示，不展示宿主解释器细节。已有当前版本管理器可直接读取新能力，无需重启业务；更新源码时仍须先退出旧版管理器。

宿主Python／Tk、路径转换或WSL互通不可用时保留手填；能力元数据写入失败也不阻断原管理入口。父进程等待上限300秒，独立原生选择进程另有280秒自退出，避免中转异常后长期挂窗；只退出该选择进程，不根据持久化PID终止其他进程。它不是远程管理服务，不安装Windows依赖到WSL工作副本，不修改WSL配置或防火墙。

复跑使用现有桌面测试命令，新增test_wsl_picker.py覆盖名称空间、普通／扩展UNC、无内容读取、取消、失败回退和独立进程超时。Windows CI继续验证原生Tk工厂；POSIX文件检查用例只在Linux执行。真实Windows→WSL能力启用、Windows Python/Tk→WSL路径回传与超时的本机证据见项目现状；对话框返回值使用替身，人工点击系统文件窗口及文件关联／默认浏览器仍未验收，不能把替身当成人工交互完成。


### 管理页断连与返回核对

管理页所有请求（包括响应体读取）在10秒后超时，释放轮询占用。超时不代表服务器取消了启动、停止或准备工作；页面不会自动重发控制命令，只继续每2秒查询状态。连接失败时保留草稿和上次数据，明确标为未确认，隐藏旧健康入口并禁用控制操作；浏览器返回可见／获得焦点时立即重查，清除旧勾选，避免沿用离开前的启动确认。准备与系统文件选择在服务器后台执行，10秒仅限制接收请求的等待，不限制整个后台任务。

根目录运行 `node --test scripts/desktop-console.test.mjs`，预期4项通过：状态请求悬挂超时后再次查询可恢复、断连后再次渲染不重新开放控制、返回页面只查询并保留草稿、控制请求的响应体超时不自动重发。已纳入现有 `node --test scripts/*.test.mjs` 门禁，无新增依赖。

可用隔离管理页核对：填写一个虚构数据库地址作为草稿，通过浏览器开发工具临时阻断 `/api/state` 后等待超时，应看到连接未确认、控制不可用；恢复请求后，页面应自动连接且草稿不变，旧确认不被恢复。本轮Playwright实际使用临时管理器与拦截响应复现，旧健康状态由测试响应提供，未启动业务服务或读取个人数据。这是请求故障注入，不能替代真实整机休眠、文件关联、默认浏览器和系统窗口人工验收。


### 默认浏览器失败与Windows文件关联核对

Windows选择窗口与 `python -m desktop console` 共用浏览器交接：浏览器返回false或抛出OSError／webbrowser.Error时，给出“管理器已运行，但默认浏览器未打开”的提示，不回显底层异常中的私有启动URL。Windows窗口恢复按钮，管理器和原业务实例不被停止；修复本机默认浏览器后重开同一入口，应复用原管理器，不重复准备或自动启动业务。

根目录运行 `python -m unittest discover -s desktop/tests -v`。浏览器失败回归使用返回值／异常替身，核对三种失败、实例身份保持、未启动业务及重试交接；Windows核心CI显式开启 `TRAVELFLOW_RUN_NATIVE_UI_TESTS=1`，另用真实Tk窗口核对失败提示与按钮恢复。真实浏览器是否显示页面仍需人工验收，webbrowser返回成功只表示交接成功。

人工最短路径：Windows安装含Tk的Python并配置.pyw关联 → 从资源管理器双击根目录TravelFlow.pyw → 明确选择环境、该环境源码与Python3.13+ → 打开所选管理页 → 核对显示的环境／项目与实际数据位置。重复打开应回到同一管理器，不能静默换模式或新建主库；测试结束从管理页停止并退出。不要为故障验收修改个人默认浏览器或系统关联，失败路径使用隔离回归。本机仅对系统关联做只读检查，并用中文／空格临时.pyw通过Shell执行取得无终端标记；该探针未打开业务、未修改系统设置，不代替完整产品的人工操作。


Windows窗口回归注意：Tk变量须在创建它的主线程回收。多个窗口用例在同一进程执行时，仅destroy窗口不保证Python引用环立即释放；后台线程触发GC可能报 `main thread is not in main loop` 并拖延后续工作。NativeWindowTests在每个用例结束后由主线程gc.collect；启动窗口销毁事件同时取消自身after轮询，避免下一窗口事件循环触发已删除的旧命令。回归新增销毁后定时器为空断言，保留原有连接超时和按钮恢复检查，不通过延长等待绕过失败。


### Windows侧WSL端口预检

WSL监听健康不代表Windows浏览器可访问同一端口。通过Windows统一入口连接后，复用windows-picker.json中绑定当前项目／发行版的宿主Python，运行独立标准库host_ports.py，短暂绑定并释放127.0.0.1 TCP端口，不开放监听、不改系统保留范围或防火墙。未有已认证活实例时，预览与启动均检查；活实例复用不尝试抢占自身端口。10013提示系统保留或拒绝绑定，其他绑定错误提示占用／不可绑定；用户修改端口后重新预览确认，不自动选端口或迁移数据。宿主存在但探针失败时拒绝继续新启动；没有宿主桥接的纯WSL入口不执行此检查，不能宣称已验证Windows访问。

复跑 `python -m unittest discover -s desktop/tests -p test_host_ports.py -v`，预期5项通过；完整桌面门禁继续沿用前述命令。人工核对：在管理页显式选择隔离体验，填写可用后端端口及一个Windows已保留网页端口，预览应显示错误码并拒绝继续；改为空闲网页端口后重新预览、确认、启动，再从Windows打开管理页给出的实际地址。Windows可只读运行 `netsh interface ipv4 show excludedportrange protocol=tcp` 核对系统排除范围。不要将某台电脑上的可用端口作为全平台默认值；端口绑定通过仍不能保证所有防火墙、转发或代理条件。

本次实机3100落在3081–3180排除范围，WSL HTTP200而Windows拒绝连接，宿主绑定返回10013；更换端口后Windows HTTP200。管理API版本升级要求旧管理器正常退出重开，已有用户应用不被自动打断。
