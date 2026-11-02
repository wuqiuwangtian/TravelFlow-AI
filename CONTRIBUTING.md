# 参与 TravelFlow AI 开发

TravelFlow AI 是一个本地运行的 AI 旅行助手：后端使用 FastAPI + LangGraph，前端使用 Next.js + React。项目当前处于发布准备中，本文说明开发环境、必跑检查与提交规范。安装和使用方法见 [README](./README.md)，维护入口与文档职责见 [MAINTAINING.md](./docs/internal/MAINTAINING.md)。

## 开始之前

- 先搜索现有 Issue 与 Pull Request，避免重复工作；较大改动先开 Issue 说明动机、范围和验证方式。
- 只改与本任务相关的文件，不重排无关代码或历史文档。
- 项目默认只监听 `127.0.0.1`，没有账户体系；不要在公网或不可信局域网暴露服务。
- 提问、示例和反馈中不要粘贴 API Key、`.env` 内容、数据库文件、票据或真实旅行记录。

## 环境准备

需要：

- Git
- [uv](https://docs.astral.sh/uv/) 0.5 或更高版本（负责 Python 3.13 与后端依赖）
- Node.js 20 或更高版本
- pnpm（版本以 `frontend/package.json` 的 `packageManager` 为准）

```bash
git clone https://github.com/wuqiuwangtian/TravelFlow-AI.git
cd TravelFlow-AI

# 后端：生成本地配置并安装依赖
cd backend
cp .env.example .env
uv sync

# 前端：安装锁定版本依赖
cd ../frontend
pnpm install
```

已有 `backend/.env` 时不要覆盖；密钥只在本地配置或界面设置中填写，不提交。锁文件 `backend/uv.lock` 与 `frontend/pnpm-lock.yaml` 是依赖版本依据；升级依赖时同步提交锁文件。

本地启动（两个终端）：

```bash
# 终端 1
cd backend
uv run uvicorn app.main:app --reload --port 8000

# 终端 2
cd frontend
pnpm dev -p 3100
```

访问 <http://localhost:3100>，接口文档在 <http://localhost:8000/docs>。更完整的环境说明见[开发与隔离验收](./docs/internal/handoff/DEVELOPMENT.md)。

## 必跑检查

按改动范围选择检查；提交 Pull Request 前至少运行与改动对应的命令，并在 PR 中贴出实际命令与结果。未运行的检查要明确写出，不能写成“应该通过”。

### 后端（在 `backend/` 执行）

```bash
uv run ruff format --check app
uv run ruff check app
uv run mypy app
uv run pytest -q
```

- 行为变更必须补充或更新 `backend/tests/` 下的测试。
- 只运行相关测试时，在 PR 中说明完整测试是否执行；CI 会运行完整检查。
- 安装依赖时加 `--frozen`（例如 `uv sync --frozen`）可复现 CI 的锁定环境。

### 前端（在 `frontend/` 执行）

```bash
pnpm typecheck
pnpm lint
pnpm check:design
pnpm build
```

`check:design` 校验正式源码中的语义颜色与产品文案约定。新增界面颜色应放在 `styles/tokens.css`，页面使用既有语义类。视觉与交互约定见[前端设计约定](./frontend/AGENTS.md)和[共享规范](./docs/design/SYSTEM.md)。

### 隔离浏览器验收（在 `frontend/` 执行）

先完成生产构建和 `backend/` 依赖安装，并确认 `8137`、`3137` 端口没有被其他服务占用：

```bash
pnpm exec playwright install chromium
pnpm test:e2e
```

Linux 缺系统库时使用 `pnpm exec playwright install --with-deps chromium`。

只验证相关场景时：

```bash
pnpm exec playwright test tests/e2e/ordinary-flow.spec.ts
```

测试会自动启动 `127.0.0.1:8137` 的隔离后端和 `127.0.0.1:3137` 的生产前端，使用临时 SQLite、固定模型与检索替身，不读取个人 `.env`，也不需要真实 API Key。不要把测试指向正常使用的 `8000` 服务或个人数据库。完整说明见[浏览器验收说明](./frontend/tests/e2e/README.md)。

## 测试与文档要求

- 修复缺陷时先补能复现问题的测试，再改实现；新功能覆盖主要成功路径和可预期的失败路径。
- 测试使用替身或固定数据，不依赖真实模型、检索、地图服务，不写入个人旅行记录。
- 行为、配置、环境或计划变化时，同步更新对应权威文档：当前状态写入 [PROJECT_STATUS.md](./docs/internal/PROJECT_STATUS.md#当前接续状态)，任务顺序与完成条件写入 [PRODUCT_ROADMAP.md](./docs/internal/PRODUCT_ROADMAP.md#当前执行任务)，专题证据保留在原专题页。文档职责见 [MAINTAINING.md](./docs/internal/MAINTAINING.md)。
- 文档使用简体中文，步骤直接可执行；命令要能在 `backend/` 或 `frontend/` 对应目录运行。相对链接必须指向仓库内实际存在的文件。
- 不复制个人路径、密钥、真实旅行内容；需要示例时使用虚构数据并说明是示例。
- 未验证的外部服务、平台差异和已知限制要如实标注，不能把静态检查或替身测试写成真实服务验证成功。

## 只改文档

只修改 Markdown 等文档时，不需要启动模型或重跑完整业务测试，但需要：

```bash
git diff --check
git status --short
```

并人工核对：

- 所有相对链接指向仓库内实际存在的文件；
- 命令与当前 `backend/pyproject.toml`、`frontend/package.json` 中的脚本一致；
- 没有占位符、失效命令、真实密钥或个人数据；
- 改动没有覆盖其他参与者正在修改的内容。

如果文档改动同时涉及行为说明，仍应运行对应代码检查，避免文档与实现脱节。

## 提交与 PR 规范

- 从 `main` 拉出独立分支，例如 `docs/contributing`、`fix/export-escape`；不要直接在 `main` 上提交。
- 提交信息使用简洁的英文 Conventional Commits 前缀，例如 `docs: ...`、`fix(scope): ...`、`feat(scope): ...`、`test: ...`。
- 每个提交保持可理解的小范围；不把格式化、重命名和功能改动混在一起。
- 不提交 `.env`、`data/*.db`、`frontend/test-results/`、缓存或构建产物；提交前用 `git diff --cached` 核对实际内容。
- PR 描述使用仓库的 [PR 模板](.github/pull_request_template.md)，说明改动、验证命令与结果、风险和未验证项。
- 涉及安全问题时不要开公开 Issue，按 [SECURITY.md](./SECURITY.md) 私密报告。

## 许可证与发布状态

项目计划以 MIT 许可证发布，当前处于发布准备中；许可证与发布状态以仓库根目录的 `LICENSE` 和 GitHub 发布页为准。正式发布前，请不要把仓库内容视为已获得开源授权。
