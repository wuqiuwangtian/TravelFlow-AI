# M1 工程基线 · 实施蓝图

> **性质**：这是**执行蓝图**，不是设计稿。M1 完成后本文档即作废。
> **状态**：待放行 —— 你说「开始」我就按第 8 节顺序落地。

---

## 1. M1 的目标与完成定义

**目标**：项目能**启动**、能**自检**、能**正确报错**，但还**没有业务能力**（LLM/检索/编排留到 M2、M3）。

**完成定义（DoD）—— 全部勾上才算 M1 完成**：

- [ ] `uv run uvicorn app.main:app --reload` 能启动，无报错
- [ ] `GET /api/system/health` → 200
- [ ] `GET /api/system/config` → 配置自检，**不回显密钥**（只回 `has_key: true/false`）
- [ ] `GET /api/system/providers` → 可用 LLM 与检索 Provider 列表
- [ ] 触发错误时返回统一格式，且含 `trace_id`
- [ ] 日志为结构化输出，每条含 `trace_id`
- [ ] `pytest` 全绿（≥3 个测试）
- [ ] `uv.lock` 生成并入库
- [ ] README 增加「开发环境启动」小节

---

## 2. 目录结构（M1 建立，后续里程碑只增不改）

```
TravelFlow-AI/
├── backend/
│   ├── pyproject.toml            # 依赖与元数据（uv 管理）
│   ├── uv.lock                   # 锁定文件，入库
│   ├── .env.example              # 全量配置示例
│   ├── app/
│   │   ├── __init__.py
│   │   ├── main.py               # 应用装配：lifespan / 中间件 / 路由挂载 / 异常处理
│   │   ├── core/
│   │   │   ├── __init__.py
│   │   │   ├── config.py         # pydantic-settings 配置模型
│   │   │   ├── logging.py        # 结构化日志 + trace_id
│   │   │   ├── errors.py         # 异常体系 + 统一异常处理器
│   │   │   └── deps.py           # 依赖注入（预留）
│   │   ├── api/
│   │   │   ├── __init__.py
│   │   │   └── routes_system.py  # M1 的 3 个系统接口
│   │   └── domain/
│   │       ├── __init__.py
│   │       ├── enums.py          # 状态枚举等
│   │       └── schemas.py        # 统一响应 DTO
│   └── tests/
│       ├── __init__.py
│       ├── conftest.py           # TestClient fixture
│       ├── test_health.py
│       ├── test_config.py
│       └── test_errors.py
└── frontend/                     # 留到 M5
```

---

## 3. 关键文件职责

| 文件 | 职责 | 关键点 |
| :--- | :--- | :--- |
| `app/main.py` | 装配 FastAPI | `lifespan` 初始化日志与存储；注册 CORS、trace 中间件、异常处理器、路由 |
| `core/config.py` | 全部配置的单一来源 | `Settings(BaseSettings)`，启动即校验；**缺关键项时给人话报错** |
| `core/logging.py` | 结构化日志 | JSON 行输出；`trace_id` 由中间件注入 contextvar |
| `core/errors.py` | 异常体系 + 处理器 | `AppError` 基类携带 `code`/`http_status`/`message`；处理器转统一响应 |
| `api/routes_system.py` | 系统自检接口 | 只读、无副作用、不依赖外部服务（健康检查必须永远能答） |
| `domain/schemas.py` | 对外 DTO | `ErrorResponse`、`HealthResponse`、`ConfigCheckResponse`、`ProvidersResponse` |

**分层铁律（M1 就守住）**：`api/` 不写业务逻辑；`core/` 不 import `api/`。

---

## 4. 依赖清单（M1 只装必要的）

```toml
[project]
requires-python = ">=3.13"
dependencies = [
  "fastapi==0.143.0",
  "uvicorn[standard]==0.54.0",
  "pydantic==2.14.0",
  "pydantic-settings==2.15.0",
  "python-dotenv==1.2.4",
  "httpx==0.28.1",
]

[dependency-groups]
dev = ["pytest", "pytest-asyncio", "ruff"]
```

> LangGraph / langchain-openai / 检索 SDK 留到 **M2** 再加，避免 M1 被无关依赖拖累。

---

## 5. 配置项（M1 落地）

| 分组 | 配置项 | 默认 | 说明 |
| :--- | :--- | :--- | :--- |
| 服务 | `APP_HOST` | `127.0.0.1` | 默认仅本机可访问（安全） |
| | `APP_PORT` | `8000` | |
| | `APP_LOG_LEVEL` | `INFO` | |
| | `APP_CORS_ORIGINS` | `http://localhost:3000` | 不再用 `*` |
| 数据 | `SESSION_DB_PATH` | `./data/travelflow.db` | M4 才真正使用 |
| LLM | `LLM_PROVIDER` | `deepseek` | M2 使用 |
| | `LLM_MODEL` / `LLM_API_KEY` / `LLM_BASE_URL` | — | |
| 检索 | `SEARCH_PROVIDER` | `tavily` | M2 使用 |
| | `SEARCH_API_KEY` | — | |

**M1 阶段 config 接口只做自检**（是否配置齐全），不实际调用外部服务。

---

## 6. 异常体系与错误码（与规划 §9.2 对齐）

```
AppError (基类：code, http_status, message, detail)
├── ConfigError                500  CONFIG_INVALID
├── ValidationFail             400  INVALID_REQUEST
├── NotFoundError              404  ...
│   ├── SessionNotFound        404  SESSION_NOT_FOUND
│   └── BranchNotFound         404  BRANCH_NOT_FOUND
├── ConflictError              409  ...
│   └── BranchConflict         409  BRANCH_CONFLICT
└── ProviderError              502  ...
    ├── LLMUnavailable         502  LLM_UNAVAILABLE
    ├── LLMTimeout             504  LLM_TIMEOUT
    ├── LLMOutputInvalid       422  LLM_OUTPUT_INVALID
    └── SearchUnavailable      502  SEARCH_PROVIDER_UNAVAILABLE
```

统一响应：

```json
{ "error": { "code": "SEARCH_PROVIDER_UNAVAILABLE",
             "message": "检索服务暂时不可用，请检查网络或稍后重试",
             "trace_id": "b1f3c8e2" } }
```

**关键约束**：`message` 是**给人看的**，必须说人话；技术细节放日志，不放响应。

---

## 7. M1 交付的 3 个接口

| 方法 | 路径 | 返回 |
| :--- | :--- | :--- |
| `GET` | `/api/system/health` | `{status:"ok", version, uptime_seconds}` |
| `GET` | `/api/system/config` | `{llm:{provider,model,has_key}, search:{provider,has_key}, warnings:[...]}` |
| `GET` | `/api/system/providers` | `{llm:[...], search:[...]}` 每个含 `id`/`name`/`docs_url`（**链接官方，不写死额度**） |

> `providers` 接口体现本轮确立的原则：**只给名称与官方文档链接，不给任何免费额度数字**。

---

## 8. 执行步骤（我会严格按此顺序）

1. 建目录 + `uv init`
2. 写 `pyproject.toml` → `uv lock`
3. 实现 `core/config.py`（含人话校验错误）
4. 实现 `core/logging.py`（结构化 + trace_id）
5. 实现 `core/errors.py`（异常体系 + 处理器）
6. 实现 `domain/schemas.py`
7. 实现 `api/routes_system.py`（3 接口）
8. 装配 `app/main.py`
9. 写 3 个测试并跑通
10. 逐条核对第 1 节 DoD
11. **提交 git + 更新 README**
12. 向你汇报（附启动命令与实际输出）

**预估规模**：约 400~600 行（含测试）。

---

## 9. 完成后你会看到什么

```bash
$ cd backend && uv run uvicorn app.main:app --reload
INFO  TravelFlow AI v0.1.0 starting...
INFO  config loaded: llm=deepseek search=tavily
INFO  Uvicorn running on http://127.0.0.1:8000

$ curl localhost:8000/api/system/health
{"status":"ok","version":"0.1.0","uptime_seconds":3.2}

$ curl localhost:8000/api/system/config
{"llm":{"provider":"deepseek","model":"deepseek-chat","has_key":false},
 "search":{"provider":"tavily","has_key":false},
 "warnings":["未配置 LLM_API_KEY，请在 .env 中填写，或改用本地 Ollama"]}
```

—— 一个**能跑、能自检、报错说人话**的空壳，业务能力从 M2 开始叠加。
