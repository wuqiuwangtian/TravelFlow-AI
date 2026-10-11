# TravelFlow-AI 升级规划（阶段②设计稿）

> **状态：待用户验证** —— 验证通过后方可进入编码实施。
> 本文档是阶段②的**规划交付物**，不含任何已落地的代码改动。
> 制定依据：阶段①拆解报告（`docs/01-legacy-analysis.md`）+ 决策输入（`docs/01b-decision-inputs.md`）+ 用户下达的项目目标。
> **配套设计材料**：`docs/02b-prompt-design.md`（四个节点的完整 Prompt 设计与防幻觉闭环实现）。
>
> ⚠️ **修订说明（2026-10-11，依据用户反馈）**：新增 `docs/02d-session-ux-design.md`，本文档以下内容以该文档为准 ——
> - **第 9 章 数据模型** → 增加**会话 / 分支 / 版本树**三张表（见 02d §2）
> - **第 10 章 前端设计** → 由「单页三栏」改为**多页面应用（6 个路由）**（见 02d §3）
> - **第 11 章 导出设计** → 增加**详细度档位（L1~L4）**与**图文并茂 + 动画 HTML** 要求（见 02d §4、§5）
> - **第 17 章 待确认事项** → 用户已答复，最新待确认清单见 02d §6

---

## 0. 本次规划的三个待拍板项（先看这里）

绝大多数设计已按你给的目标定型，只有 **3 处**需要你明确表态（详见第 17 章）：

| # | 待拍板项 | 我的建议 | 影响 |
| :--- | :--- | :--- | :--- |
| **①** | **默认检索 Provider** | 默认 **Tavily**（你已有 key，旧项目验证有效），同时内置 4 个可切换 Provider | 决定「小白开箱即用」的成本 |
| **②** | **TypeScript 版本** | 锁 **TS 5.9.x**（成熟）；TS 7.0.2 作为实验项在阶段③验证 | TS 7 是 Go 重写的全新实现，生态工具链可能未跟上 |
| **③** | **会话状态方案** | LangGraph 内置 **SQLite Checkpointer** | 同时解决持久化、并发、断点续聊。**已实测可行**（18.V2） |

其余全部可按本文档执行。

---

## 1. 项目定位与目标

### 1.1 产品定位

> **一个小白友好的、通过本地 Web 服务启动的旅行助手。**

关键词拆解：

| 关键词 | 落地要求 |
| :--- | :--- |
| **小白友好** | ① 启动只需一条命令；② 配置有引导、有自检、报错说人话；③ 前端无需培训即可上手 |
| **本地 Web 服务** | 数据与模型调用都在用户自己机器上发起；前端跑在 `localhost`；不做账号体系、不做云端多租户 |
| **旅行助手** | 多轮对话收集需求 → 联网检索真实信息 → 生成结构化路书 → 交互式修改 → 导出 |

### 1.2 核心用户故事

```
US-1  我打开浏览器，输入"我想去重庆玩3天，从北京出发，预算2000"
      → 系统自动追问缺失信息（出发地/天数/预算）
      → 信息齐全后自动检索，产出完整路书

US-2  我对路书不满意，说"把第二天换成更轻松的行程"
      → 系统只改第二天，其余保留（不重新全量生成）

US-3  我满意了，点"导出" → 得到 Markdown 或 HTML 文件

US-4  我想用自己的本地模型（Ollama），改一行配置就能切换，不用改代码

US-5  我不想注册国外服务，能换成国内检索源

US-6  我不懂技术，下载源码后双击一个脚本就能跑起来；或者装了 Docker 就 docker compose up
```

### 1.3 可验收的成功标准

| # | 标准 | 验证方式 |
| :--- | :--- | :--- |
| S1 | 修复旧项目 P0-1：**计划确认/修改闭环可用**，修改不触发全量重搜 | 阶段③集成测试：第 2 轮"改第二天"仅进入 refiner |
| S2 | 检索 query **不含硬编码年份**，产出标注信息截止时间 | 代码审查 + 快照测试 |
| S3 | 引用由程序从检索结果**结构化透传**，不经 LLM 复写 | 单测：引用 URL 必须与检索结果完全一致 |
| S4 | 并发请求不被阻塞（并发 3 请求总耗时显著小于串行） | 阶段③并发压测 |
| S5 | 导出 MD 与 HTML 均正确、HTML 可离线打开且打印为 PDF | E2E |
| S6 | 配置切换 LLM（云 ↔ 本地 Ollama）无需改代码 | 集成测试 |
| S7 | 源码部署与 Docker 部署均可一键启动 | 阶段④实测 |
| S8 | 依赖全部锁定版本，`uv.lock` / `pnpm-lock.yaml` 入库 | 构建可复现 |

---

## 2. 设计原则

| 原则 | 具体含义 | 反面教材（旧项目） |
| :--- | :--- | :--- |
| **简洁清晰** | 一个文件只做一件事；不引入用不到的抽象 | `graph.py` 一个文件塞进拓扑+节点+Prompt+LLM实例+路由 |
| **分层合理** | 依赖单向、层间通过接口通信 | `server.py` 里写业务、`graph.py` 里建 LLM 客户端 |
| **符合规范** | 标准包结构、类型注解、结构化日志、错误契约 | 无 `__init__.py`、无包结构、`print` 当日志 |
| **健壮性** | 所有外部调用有超时/重试/降级；LLM 输出有 schema 校验 | `data["status"]` 直接下标 → KeyError 500 |
| **可扩展** | LLM 与检索均可插拔；新增 Provider 不改核心 | 检索写死 Tavily，模型名写死 `deepseek-chat` |
| **不过度设计** | 不做微服务、不做消息队列、不引入 Redis（单机部署无必要） | — |
| **不缓存易变信息** | 第三方政策、价格、免费额度、优惠等**易变信息一律链接官方来源**，不在代码或文案里写死具体数字；确需展示时必须带「以官方为准」 | 旧项目把 `2024` 写死在检索 query 里 |
| **内容可验证** | AI 产出必须同时具备：**可溯源**（引用来源）、**可核验**（官方办理渠道）、**有告知**（AI 生成免责声明）。系统的职责不是让用户相信 AI，而是**让用户能方便地验证 AI** | 旧项目只给了一串来路不明的 URL |

> 「不缓存易变信息」与「内容可验证」的详细设计见 [第 19 章](#19-可信度设计与免责依据用户反馈新增)。

---

## 3. 技术栈与版本锁定

### 3.1 后端（全部为**实测查得的最新稳定版**）

| 组件 | 锁定版本 | 说明 |
| :--- | :--- | :--- |
| Python | **3.13.13** | ✅ 已实测：该版本下安装 LangGraph 1.2.14 全家桶并 import 成功 |
| FastAPI | **0.143.0** | Web 框架 |
| uvicorn[standard] | **0.54.0** | ASGI 服务器 |
| pydantic | **2.14.0** | 数据校验 |
| pydantic-settings | **2.15.0** | 配置管理（替代裸 `os.getenv`） |
| LangGraph | **1.2.14** | Agent 编排 |
| langchain-core | **1.6.9** | 由 langgraph 依赖带入 |
| langchain-openai | **1.7.0** | LLM 接入（OpenAI 兼容协议） |
| httpx | **0.28.1** | 异步 HTTP |
| tavily-python | **0.8.5** | 检索 Provider 之一 |
| python-dotenv | **1.2.4** | 本地 `.env` 加载 |
| Jinja2 | 最新稳定 | HTML 导出模板 |
| aiosqlite | 最新稳定 | 会话持久化 |
| langgraph-checkpoint-sqlite | 与 langgraph 同源 | 状态持久化 |
| pytest / pytest-asyncio / respx | 最新稳定 | 测试 |

### 3.2 前端

| 组件 | 锁定版本 | 说明 |
| :--- | :--- | :--- |
| Next.js | **16.4.0** | 当前 latest；engines `>=20.9.0`，与 Node 26 同期 |
| React / React-DOM | **19.3.0** | Next 16 peerDependencies 已声明支持 `^19.0.0` |
| 包管理器 | **pnpm 12.6.0** | 本机已装 |
| TailwindCSS | **4.3.3** | v4 使用 CSS-first 配置 |
| TypeScript | **5.9.x**（建议）| ⚠️ 待拍板，见第 17 章 |
| zustand | **5.0.15** | 轻量状态管理 |
| react-markdown | **10.1.0** | 路书渲染 |
| lucide-react | **1.55.0** | 图标 |

### 3.3 版本锁定策略

1. **后端**：使用 `uv` + `pyproject.toml`，**提交 `uv.lock`**。依赖精确固定（`==`），杜绝旧项目「装出 LangChain 1.4.4 而代码是 0.3.x 时代」的惨剧。
2. **前端**：`package.json` 使用精确版本（不用 `^`），**提交 `pnpm-lock.yaml`**。
3. **Docker 基础镜像固定 digest**：`python:3.13-slim@sha256:...`、`node:22-alpine@sha256:...`，避免镜像漂移。

---

## 4. 系统架构

### 4.1 总体架构

```
┌──────────────────────────────────────────────────────────────┐
│  浏览器 (localhost:3000)                                      │
│  Next.js 16 App Router + React 19 + Tailwind 4 + zustand     │
└───────────────────────────┬──────────────────────────────────┘
                            │ HTTP / SSE (流式)
                            ▼
┌──────────────────────────────────────────────────────────────┐
│  FastAPI (localhost:8000)                                     │
│  ┌────────────────────────────────────────────────────────┐  │
│  │ api/          路由层：只做参数校验与响应封装           │  │
│  ├────────────────────────────────────────────────────────┤  │
│  │ services/     业务服务层：会话、编排调度、导出         │  │
│  ├────────────────────────────────────────────────────────┤  │
│  │ agents/       LangGraph 图与节点（纯编排逻辑）         │  │
│  ├───────────────┬────────────────┬───────────────────────┤  │
│  │ llm/          │ search/        │ export/               │  │
│  │ Provider 抽象 │ Provider 抽象  │ MD/HTML 渲染          │  │
│  ├───────────────┴────────────────┴───────────────────────┤  │
│  │ storage/      会话持久化 (SQLite)                      │  │
│  ├────────────────────────────────────────────────────────┤  │
│  │ core/         配置、日志、异常、依赖注入               │  │
│  └────────────────────────────────────────────────────────┘  │
└──────┬──────────────────┬───────────────────┬────────────────┘
       │                  │                   │
       ▼                  ▼                   ▼
  LLM Provider       Search Provider     SQLite / 文件系统
  · DeepSeek         · Tavily
  · OpenAI           · 博查 Bocha
  · 通义/智谱/Kimi   · 百度千帆
  · 本地 Ollama      · SearXNG
  · vLLM / LM Studio · DuckDuckGo
```

### 4.2 依赖方向（严格单向）

```
api ──▶ services ──▶ agents ──▶ {llm, search}
                 └─▶ export
                 └─▶ storage
        core ◀── 所有层均可依赖（配置/日志/异常）
```

**铁律**：
- `agents/` **不得** import `fastapi`；
- `llm/` 与 `search/` **不得**互相依赖；
- `api/` 不含任何业务逻辑，只做 I/O 转换。

---

## 5. 后端目录结构

```
backend/
├── pyproject.toml              # 依赖与元数据（uv 管理）
├── uv.lock                     # 锁定文件，入库
├── Dockerfile
├── .env.example
├── app/
│   ├── __init__.py
│   ├── main.py                 # FastAPI 应用装配、生命周期、中间件
│   ├── core/
│   │   ├── config.py           # pydantic-settings 配置模型（全部环境变量）
│   │   ├── logging.py          # 结构化日志（含 trace_id）
│   │   ├── errors.py           # 异常体系 + 统一错误响应
│   │   └── deps.py             # 依赖注入容器
│   ├── api/
│   │   ├── routes_chat.py      # POST /api/chat, /api/chat/stream
│   │   ├── routes_session.py   # 会话 CRUD
│   │   ├── routes_export.py    # 导出 MD/HTML
│   │   └── routes_system.py    # 健康检查、配置自检、Provider 列表
│   ├── domain/
│   │   ├── models.py           # 领域模型（Pydantic）
│   │   ├── enums.py            # 状态枚举
│   │   └── schemas.py          # 请求/响应 DTO
│   ├── services/
│   │   ├── chat_service.py     # 对话编排调度
│   │   ├── session_service.py  # 会话生命周期
│   │   └── export_service.py   # 导出业务
│   ├── agents/
│   │   ├── graph.py            # 图装配（只做拓扑）
│   │   ├── state.py            # PlanState 定义
│   │   ├── nodes/              # 一节点一文件
│   │   │   ├── collector.py
│   │   │   ├── researcher.py
│   │   │   ├── planner.py
│   │   │   └── refiner.py
│   │   ├── routing.py          # 路由函数集中管理
│   │   └── prompts/            # Prompt 模板外置（.md 或 .jinja）
│   │       ├── collect.md
│   │       ├── plan.md
│   │       └── refine.md
│   ├── llm/
│   │   ├── base.py             # LLMProvider 抽象
│   │   ├── openai_compat.py    # OpenAI 兼容实现（覆盖 95% 场景）
│   │   ├── registry.py         # 预设 Provider 注册表
│   │   └── factory.py          # 按配置构造
│   ├── search/
│   │   ├── base.py             # SearchProvider 抽象 + SearchResult 模型
│   │   ├── tavily_provider.py
│   │   ├── bocha_provider.py
│   │   ├── qianfan_provider.py
│   │   ├── searxng_provider.py
│   │   ├── ddg_provider.py
│   │   ├── registry.py
│   │   └── factory.py
│   ├── export/
│   │   ├── markdown_exporter.py
│   │   ├── html_exporter.py
│   │   └── templates/
│   │       └── report.html.jinja
│   └── storage/
│       ├── session_store.py    # SQLite 会话存储
│       └── migrations/
└── tests/
    ├── unit/                   # 单测（LLM/检索全部 mock）
    ├── integration/            # 集成（含图编排、导出）
    └── fixtures/               # 旧项目 3 份路书作为回归基线
```

---

## 6. LLM 抽象层（多模型 + 本地部署）

### 6.1 设计要点

**结论先行**：市面上主流大模型与几乎所有本地部署方案，**都提供 OpenAI 兼容接口**。因此不需要为每家写适配器，只需**一个 `OpenAICompatProvider` + 一张预设表**。

| 类型 | 代表 | 接入方式 |
| :--- | :--- | :--- |
| 云端主流 | DeepSeek、OpenAI、月之暗面 Kimi、智谱 GLM、通义千问、MiniMax、硅基流动、OpenRouter | 原生 OpenAI 兼容 |
| 本地部署 | **Ollama**、**vLLM**、**LM Studio**、**llama.cpp server**、Xinference | 均暴露 `/v1` OpenAI 兼容端点 |

### 6.2 接口设计

```python
class LLMProvider(Protocol):
    async def chat(self, messages: list[Message], **opts) -> str: ...
    async def structured(self, messages: list[Message], schema: type[BaseModel], **opts) -> BaseModel: ...
    async def stream(self, messages: list[Message], **opts) -> AsyncIterator[str]: ...
```

- `structured()` 基于 LangChain 的 `with_structured_output()`，**从根上解决**旧项目「LLM 返回 JSON 但缺字段导致 KeyError」的问题。
- 所有调用带**超时 + 重试 + 降级**。

### 6.3 预设表（`registry.py`，节选）

| 名称 | base_url | 默认模型 |
| :--- | :--- | :--- |
| `deepseek` | `https://api.deepseek.com/v1` | `deepseek-chat` |
| `openai` | `https://api.openai.com/v1` | `gpt-4o-mini` |
| `moonshot` | `https://api.moonshot.cn/v1` | `moonshot-v1-8k` |
| `zhipu` | `https://open.bigmodel.cn/api/paas/v4` | `glm-4-flash` |
| `dashscope` | `https://dashscope.aliyuncs.com/compatible-mode/v1` | `qwen-plus` |
| `siliconflow` | `https://api.siliconflow.cn/v1` | `Qwen/Qwen2.5-7B-Instruct` |
| **`ollama`** | `http://localhost:11434/v1` | `qwen2.5:7b` |
| **`vllm`** | `http://localhost:8000/v1` | 用户自填 |
| **`lmstudio`** | `http://localhost:1234/v1` | 用户自填 |
| `custom` | 用户自填 | 用户自填 |

### 6.4 配置示例（切换本地模型只需改 3 行）

```dotenv
# 使用云端 DeepSeek
LLM_PROVIDER=deepseek
LLM_MODEL=deepseek-chat
LLM_API_KEY=sk-xxxx

# —— 或用本地 Ollama（无需联网、无需 API Key）——
# LLM_PROVIDER=ollama
# LLM_MODEL=qwen2.5:7b
# LLM_BASE_URL=http://localhost:11434/v1
# LLM_API_KEY=ollama
```

---

## 7. 检索抽象层（含方案建议）★

### 7.1 约束

1. 用户在中国大陆，**国外服务常需代理**（阶段①已实测：本机 GitHub 直连不通）；
2. 检索质量直接决定「防幻觉」效果 —— 阶段①实测已证明这条链路是**有价值的**；
3. 小白用户不应被要求注册一堆账号。

### 7.2 候选方案调研结果（2026-10 实测/查证）

| Provider | 免费额度 | 付费价格 | 国内直连 | 中文质量 | 备注 |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Tavily** | 1,000 credits/月 | $0.008/credit | ❌ 需代理 | 中 | Basic 1 credit/次，**Advanced 2 credits/次**；专为 LLM 设计，旧项目已验证有效 |
| **博查 BochaAI** | 1,000 次 / 3 个月（体验包） | **¥36 / 1000 次** | ✅ | 优 | 国产，专为 AI 搜索设计 |
| **百度智能云·千帆** | **100 次/天**（≈3000 次/月） | ¥36 / 1000 次 | ✅ | 优 | 免费额度最实用，日限 10 万次 |
| **智谱搜索 API** | ❌ | **¥10 / 1000 次** | ✅ | 优 | 单价最低，支持意图分析 |
| **腾讯云** | ❌ | ¥30 / 1000 次 | ✅ | 优 | 基于搜狗 |
| **阿里云 IQS** | 试用 1000 次/15 天 | ¥42 / 1000 次 | ✅ | 优 | 仅试用版 |
| **SearXNG（自建）** | **无限** | 仅服务器成本 | ✅ | 取决于上游 | 开源元搜索，Docker 一键部署，可聚合 Google/Bing/百度 |
| **DuckDuckGo** | 无限，**无需 Key** | 免费 | ⚠️ 不稳定 | 中 | `ddgs` 库，适合零配置兜底 |

> 数据来源：阶段②调研（[搜索 API 价格汇总](https://hebangwen.github.io/2026/03/04/search-api-summary/)、[Tavily 官方计价文档](https://docs.tavily.com/documentation/api-credits)）。价格为 2026-10 时点，可能变动。

### 7.3 我的建议 ★

**不要只选一个，而是做成可插拔 —— 这同时满足你的「可扩展性」要求，成本也不高**（每个 Provider 约 50~80 行）。

具体方案：

1. **统一接口**：
   ```python
   class SearchProvider(Protocol):
       async def search(self, query: str, *, max_results: int = 5) -> list[SearchResult]: ...

   @dataclass
   class SearchResult:
       title: str
       url: str          # ← 引用直接来自这里，程序透传，不经 LLM
       content: str
       published_at: datetime | None   # ← 用于「信息新鲜度」判断
       source: str       # 哪个 Provider 返回的
   ```

2. **内置 5 个 Provider**：`tavily` / `bocha` / `qianfan` / `searxng` / `ddg`

3. **默认值建议 `tavily`**：你已有 key，且旧项目已验证检索质量有效。

4. **给小白的两条「省钱/省事」路径**（写进 README）：
   - **零成本路线**：`SEARCH_PROVIDER=ddg`（无需任何 Key，开箱即用，但稳定性一般）
   - **国内免费路线**：`SEARCH_PROVIDER=qianfan`（注册百度智能云，每天白嫖 100 次，够个人用）

5. **可扩展**：新增 Provider 只需实现接口 + 注册，不动核心代码。

### 7.4 与「防幻觉」的关系（呼应阶段① 7.7）

阶段①实测发现旧项目输出过时的根因是 **query 硬编码 `2024`**。本方案从三处根治：

| 措施 | 说明 |
| :--- | :--- |
| query 动态构造 | 年份取自 `datetime.now().year`，禁止字面量 |
| 结构化引用 | `SearchResult.url` 由程序直接写入导出文档，**LLM 只负责引用编号**（如 `[1]`），不接触 URL 文本 |
| 时效标注 | 路书头部展示「信息检索时间：YYYY-MM-DD HH:mm」+ 每条引用的抓取时间 |

---

## 8. Agent 编排设计（修复核心缺陷）

### 8.1 旧项目问题回顾（阶段①实测）

- `refiner` 节点**入度为 0**，永远不可达 → 确认/修改闭环失效；
- 入口固定 `START → collector` → 第 2 轮用户说"确认"仍触发全量重搜重生成。

### 8.2 新状态机

```
                         ┌─────────────────────────────────┐
                         │                                 │
   START ──▶ [入口路由] ──┼──▶ collector ──(信息齐全?)──▶ researcher
              (按 status) │        │                        │
                         │        └─(缺信息)─▶ END(等用户)  ▼
                         │                              planner
                         │                                 │
                         │                                 ▼
                         └────────────────────────────▶ reviewing ◀──┐
                                                           │          │
                                              (用户确认?) ──┤          │
                                                   │       └─(修改)──▶ refiner
                                                   ▼
                                                finished
```

**关键改动**：

| 改动 | 说明 |
| :--- | :--- |
| **显式入口路由** | 新增 `route_entry()`，按 `state.status` 决定进入 `collector` / `planner` / `refiner`，替代固定 `START → collector` |
| **refiner 接入主链** | `reviewing → refiner` 建立入边；`refiner` 自身也可成为入口 |
| **refiner 只改不重做** | 修改意见走 `refiner`，**复用已有的 `research_content`**，不重新检索（除非用户明确要求补充信息） |
| **状态持久化** | 接入 SQLite Checkpointer，会话可断点续聊、重启不丢 |

### 8.3 状态定义（`PlanState`）

```python
class PlanState(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]  # 统一为 BaseMessage，消除旧的 dict/对象混用
    requirements: Requirements                            # 强类型，替代 Dict
    research: ResearchBundle | None                       # 结构化检索结果（含引用列表）
    plan_markdown: str
    status: PlanStatus                                    # 枚举，非裸字符串
    missing_fields: list[str]
    revision: int                                         # 修改轮次
    trace_id: str
```

改进点（对照阶段① P1-7）：**状态字段全部强类型**，不再 `Dict` + 裸 `str` 混用。

### 8.4 节点职责

| 节点 | 输入 | 输出 | 外部调用 |
| :--- | :--- | :--- | :--- |
| `collector` | 用户消息 + 已有需求 | 增量需求 + 追问话术 + 是否齐全 | LLM（结构化输出） |
| `researcher` | 完整需求 | 结构化检索结果 + 引用列表 | Search × N（**并行**） |
| `planner` | 需求 + 检索结果 | Markdown 路书 | LLM |
| `refiner` | 原路书 + 修改意见 | 新路书（增量修改，**不重检索**） | LLM |

> 对照阶段① P1-6：`researcher` 的多次检索改为 `asyncio.gather` 并行，预计从 6~15s 降至 2~5s。

> **Prompt 详细设计**见配套文档 [`docs/02b-prompt-design.md`](02b-prompt-design.md)：含四个节点的完整 Prompt 文本、结构化输出契约、以及「LLM 只写编号、程序替换 URL」的防幻觉闭环（已实测验证，能检出 LLM 编造的引用编号）。

---

## 9. 数据模型与 API 契约

### 9.1 领域模型（节选）

```python
class Requirements(BaseModel):
    destination: str | None
    origin: str | None
    duration_days: int | None
    budget: Decimal | None
    preferences: list[str] = []      # 新增：偏好（亲子/美食/徒步…）
    travelers: int | None = None     # 新增：人数

class Citation(BaseModel):
    index: int
    title: str
    url: HttpUrl                     # 程序透传，非 LLM 生成
    retrieved_at: datetime
    provider: str

class ResearchBundle(BaseModel):
    transport: list[SearchResult]
    attractions: list[SearchResult]
    reviews: list[SearchResult]
    citations: list[Citation]
    searched_at: datetime
```

### 9.2 API 契约

**通用约定**

| 项 | 约定 |
| :--- | :--- |
| 前缀 | `/api` |
| 内容类型 | `application/json`（导出接口除外） |
| 分页 | `?page=1&page_size=20` → `{ items, total, page, page_size }` |
| 命名 | 路径 kebab-case，字段 snake_case |
| 幂等 | `GET` / `PUT` / `DELETE` 幂等；`POST` 非幂等 |

**统一错误响应**（对照阶段①「无错误契约」）

```json
{ "error": { "code": "SEARCH_PROVIDER_UNAVAILABLE",
             "message": "检索服务暂时不可用，请检查网络或稍后重试",
             "trace_id": "..." } }
```

**错误码表**

| HTTP | code | 含义 | 前端建议提示 |
| :--- | :--- | :--- | :--- |
| 400 | `INVALID_REQUEST` | 参数校验失败 | 请检查输入 |
| 404 | `SESSION_NOT_FOUND` | 会话不存在 | 该会话已被删除 |
| 404 | `BRANCH_NOT_FOUND` | 分支不存在 | 该分支已被删除 |
| 409 | `BRANCH_CONFLICT` | 分支操作冲突 | 请刷新后重试 |
| 422 | `LLM_OUTPUT_INVALID` | 模型输出不符合 schema（已自动重试仍失败） | 模型返回异常，请重试或更换模型 |
| 502 | `LLM_UNAVAILABLE` | 模型服务不可达 | 模型连不上，请检查配置与网络 |
| 502 | `SEARCH_PROVIDER_UNAVAILABLE` | 检索服务不可达 | 检索连不上，可点击重试 |
| 504 | `LLM_TIMEOUT` | 模型调用超时 | 模型响应超时，请重试 |

**接口清单**

*① 会话*

| 方法 | 路径 | 说明 |
| :--- | :--- | :--- |
| `GET` | `/api/sessions` | 列表，支持 `?status=active\|archived&q=关键词&page=&page_size=` |
| `POST` | `/api/sessions` | 新建会话 → `{ session }` |
| `GET` | `/api/sessions/{sid}` | 详情，**含分支树概要** |
| `PATCH` | `/api/sessions/{sid}` | 改标题 / 归档 / 恢复 |
| `DELETE` | `/api/sessions/{sid}` | 删除（级联删除分支与版本，**需二次确认**） |

*② 分支*

| 方法 | 路径 | 说明 |
| :--- | :--- | :--- |
| `GET` | `/api/sessions/{sid}/branches` | 该会话的全部分支（含血缘） |
| `POST` | `/api/sessions/{sid}/branches` | **创建分支（fork）** |
| `GET` | `/api/branches/{bid}` | 分支详情 |
| `PATCH` | `/api/branches/{bid}` | 改名 |
| `POST` | `/api/branches/{bid}/primary` | 设为当前主分支 |
| `DELETE` | `/api/branches/{bid}` | 删除分支（主分支不可删） |
| `POST` | `/api/sessions/{sid}/candidates` | **一次生成 N 个候选分支**（对比用） |

fork 请求体：

```json
{ "parent_branch_id": "b-1",
  "fork_version_id": "v-3",
  "title": "亲子版",
  "overrides": { "preferences": ["亲子"], "detail_level": "L3_detailed" } }
```

`overrides` 为可选：不填则完全继承分叉点状态（用于「回到初版」）；填了则在继承基础上覆盖需求字段（用于「同一需求换个方向」）。

*③ 版本*

| 方法 | 路径 | 说明 |
| :--- | :--- | :--- |
| `GET` | `/api/branches/{bid}/versions` | 版本列表（用于版本树渲染） |
| `GET` | `/api/versions/{vid}` | 版本详情（含路书 Markdown 与引用列表） |
| `POST` | `/api/versions/{vid}/restore` | **回到该版本** → 实质是创建一个继承该版本的新分支（不破坏历史） |

*④ 对话*

| 方法 | 路径 | 说明 |
| :--- | :--- | :--- |
| `POST` | `/api/chat` | 发送消息，返回本轮完整结果 |
| `POST` | `/api/chat/stream` | **SSE 流式**：`event: progress` 推送节点进度，`event: token` 推送增量文本，`event: done` 收尾 |

```json
// POST /api/chat
{ "session_id": "s-1", "branch_id": "b-1",
  "message": "第二天太赶了，轻松点",
  "detail_level": "L2_standard" }
```

```json
// 200 OK
{ "branch_id": "b-1", "version_id": "v-5", "status": "reviewing",
  "reply": "已调整第二天，其余内容保持不变。",
  "plan": { "markdown": "...", "citations": [...], "searched_at": "..." } }
```

*⑤ 对比与导出*

| 方法 | 路径 | 说明 |
| :--- | :--- | :--- |
| `GET` | `/api/compare?branch_ids=b1,b2,b3` | 对比数据（各分支同序号版本的路书 + 差异标记） |
| `GET` | `/api/versions/{vid}/export?format=md\|html` | 导出单个版本，返回文件流（`Content-Disposition: attachment`） |
| `GET` | `/api/compare/export?branch_ids=...` | 导出对比报告（Markdown） |

*⑥ 系统与设置*

| 方法 | 路径 | 说明 |
| :--- | :--- | :--- |
| `GET` | `/api/system/health` | 健康检查（供 Docker healthcheck 与启动脚本使用） |
| `GET` | `/api/system/config` | 当前配置自检（**不回显密钥**，只回 `has_key: true/false`） |
| `GET` | `/api/system/providers` | 可用 LLM / 检索 Provider 列表与各自必填项 |
| `POST` | `/api/system/test-llm` | **测试模型连接**（设置页「🔌 测试连接」） |
| `POST` | `/api/system/test-search` | **测试检索连接** |
| `GET` | `/api/settings` | 读取当前设置 |
| `PUT` | `/api/settings` | 更新设置（写入本地 `.env`） |

> **数据表结构**（sessions / branches / versions / plans）见 [`02d §2.4`](02d-session-ux-design.md)。

---

## 10. 前端设计

### 10.1 技术选型

Next.js 16 App Router + React 19 + Tailwind 4 + zustand；**纯前端渲染**，不做 SSR 数据获取（本地服务无 SEO 需求）。

### 10.2 页面结构（单页三栏）

```
┌──────────────┬──────────────────────────┬─────────────────────┐
│  会话列表     │  对话区                  │  路书预览            │
│              │                          │                     │
│  + 新建会话   │  用户: 我想去重庆玩3天    │  # 重庆3天2夜计划    │
│  · 重庆之行   │  助手: 请问从哪出发？     │  ## 交通建议         │
│  · 北京周末   │  用户: 北京，预算2000     │  ## 每日行程         │
│              │  助手: ⏳ 正在检索…       │  ## 费用预估         │
│              │  ✅ 计划已生成，请查看 →  │  ## 参考资料 [1][2]  │
│              │                          │                     │
│              │  [输入框]        发送     │  [导出MD] [导出HTML]  │
└──────────────┴──────────────────────────┴─────────────────────┘
```

### 10.3 小白友好细节

- **首次启动引导**：检测到未配置 Key 时，页面顶部显示「配置向导」卡片，给出三种方案（本地 Ollama / 免费 DDG / 云端 Key），点击可展开步骤；
- **进度可视化**：SSE 推送节点进度（正在收集需求 → 正在检索 → 正在生成），避免用户面对空白等待 6~15s；
- **错误说人话**：把 `SEARCH_PROVIDER_UNAVAILABLE` 渲染成「检索服务连不上，可能是网络问题，点击重试」；
- **引用可点击**：路书中的 `[1]` 悬浮显示来源标题与抓取时间。

---

## 11. 导出设计

| 格式 | 实现 | 说明 |
| :--- | :--- | :--- |
| **Markdown** | 直接输出 `plan_markdown` + 引用附录（程序拼接，URL 来自 `Citation`） | 轻量、通用 |
| **HTML** | Jinja2 模板 + **内联 CSS**，生成**单文件** | 双击即可离线打开；样式适配 A4 打印，用户可 `Ctrl+P` 存为 PDF |

**不引入 PDF 库**：fpdf2 + 中文字体是阶段①确认的负担（需打包 2~10 MB 字体），而「HTML → 浏览器打印 PDF」体验等价且零成本。若你坚持要服务端直接产 PDF，可在阶段③追加 `weasyprint` 方案。

---

## 12. 配置设计（`.env.example` 全量）

```dotenv
# ============ LLM ============
LLM_PROVIDER=deepseek            # deepseek|openai|moonshot|zhipu|dashscope|siliconflow|ollama|vllm|lmstudio|custom
LLM_MODEL=deepseek-chat
LLM_API_KEY=sk-xxxx
LLM_BASE_URL=                    # 留空则用 Provider 预设
LLM_TEMPERATURE=0.7
LLM_TIMEOUT_SECONDS=90
LLM_MAX_RETRIES=2

# ============ 检索 ============
SEARCH_PROVIDER=tavily           # tavily|bocha|qianfan|searxng|ddg
SEARCH_API_KEY=tvly-xxxx
SEARCH_MAX_RESULTS=5
SEARCH_TIMEOUT_SECONDS=20
SEARXNG_BASE_URL=http://localhost:8080   # 仅 searxng 需要

# ============ 服务 ============
APP_HOST=127.0.0.1
APP_PORT=8000
APP_LOG_LEVEL=INFO
APP_CORS_ORIGINS=http://localhost:3000
SESSION_DB_PATH=./data/sessions.db
SESSION_TTL_HOURS=168

# ============ 前端 ============
NEXT_PUBLIC_API_BASE=http://127.0.0.1:8000
```

> 全部配置集中在 `core/config.py` 的 `Settings` 类中，启动时校验并给出**人话错误**（如「未配置 LLM_API_KEY，请在 .env 中填写，或改用本地 Ollama」）。

---

## 13. 部署设计

### 13.1 方式一：源码部署（小白首选）

```bash
git clone <repo> && cd TravelFlow-AI
./start.sh          # macOS / Linux / WSL
start.bat           # Windows
```

`start.sh` 自动完成：检测 Python/Node → 安装 uv（如缺）→ `uv sync` → `pnpm install` → 启动前后端 → 打开浏览器。

### 13.2 方式二：Docker 部署

```bash
docker compose up -d
```

`docker-compose.yml` 包含 3 个服务：

| 服务 | 镜像 | 端口 |
| :--- | :--- | :--- |
| `backend` | 自建（python:3.13-slim） | 8000 |
| `frontend` | 自建（node:22-alpine，standalone 输出） | 3000 |
| `searxng`（可选 profile） | `searxng/searxng` | 8080 |

> ⚠️ **前置条件（阶段①已查明）**：本机 Docker Desktop 已安装，但**未给 Arch 发行版开启 WSL 集成**，需在 Docker Desktop → Settings → Resources → WSL Integration 勾选 `Arch`。阶段④会用到。

### 13.3 一键健康检查

两个部署方式都提供 `/api/system/config` 自检接口与前端「配置向导」，确保小白能自己定位「Key 没填 / Ollama 没启动 / 检索源不通」。

---

## 14. 测试策略（为阶段③铺路）

| 层级 | 工具 | 覆盖重点 |
| :--- | :--- | :--- |
| 单元测试 | pytest + pytest-asyncio | 每个节点、每个 Provider、导出渲染、配置校验 |
| Mock 策略 | respx（HTTP）+ 假 LLM Provider | **不消耗真实 API 额度** |
| 编排测试 | LangGraph `stream()` 断言节点序列（**不可用静态图渲染判断可达性**，见 18.V1） | **回归旧项目 P0-1**：第 2 轮"确认"必须进 refiner |
| 并发测试 | asyncio + httpx | 回归旧项目 P1-1：3 并发不得串行 |
| 基线回归 | 旧项目 3 份路书（`docs/legacy-samples/`） | 结构完整性快照对比 |
| 前端测试 | Vitest + Playwright | 三栏渲染、导出下载、错误提示 |
| E2E | docker compose 起全栈后跑一遍 US-1~US-6 | 阶段④验收 |

---

## 15. 实施计划（编码阶段里程碑）

| 里程碑 | 内容 | 产出 |
| :--- | :--- | :--- |
| **M1 工程基线** | 目录结构、pyproject + uv.lock、配置系统、日志、错误体系、CI 骨架 | 可启动的空壳服务 + 自检接口 |
| **M2 LLM 与检索抽象** | Provider 接口、注册表、5 个检索实现、Ollama 接入 | 单测覆盖，可切换 |
| **M3 Agent 编排重构** | 新状态机、显式入口路由、refiner 接入、SQLite Checkpointer、并行检索 | 修复 P0-1 / P1-1 / P1-6 |
| **M4 导出与会话** | MD/HTML 导出、会话 CRUD、SSE 流式 | 满足 S3 / S5 |
| **M5 前端** | 三栏界面、配置向导、进度、引用交互、导出下载 | 满足 US-1~US-6 |
| **M6 打包部署** | start.sh / start.bat、Dockerfile、docker-compose、README 重写 | 满足 S7 |
| **M7 测试与加固** | 全量测试、并发压测、错误路径、基线回归 | 移交阶段③ |

> 每个里程碑结束 **提交一次 git** 并同步更新 README（按你的要求）。

---

## 16. 风险与对策

| 风险 | 概率 | 影响 | 对策 |
| :--- | :--- | :--- | :--- |
| TypeScript 7 与 Next 16 / 工具链不兼容 | 中 | 中 | 先锁 5.9.x；TS 7 作为实验项在阶段③验证 |
| Tailwind 4 与旧写法（v3 配置）差异大 | 中 | 低 | 直接用 v4 CSS-first 写法，不迁移旧代码 |
| 国内网络导致 Tavily/OpenAI 不可达 | **高** | 高 | 默认提供 DDG（无需 Key）与千帆（国内）兜底；配置向导显式提示 |
| 本地模型（Ollama）上下文窗口小、指令遵循弱 | 中 | 中 | 结构化输出加 schema 校验与重试；文档标注最低推荐模型（≥7B） |
| LangChain 1.x API 仍在演进 | 中 | 中 | 锁定版本 + 依赖集中在 `llm/` 一层，隔离框架变动 |
| 检索结果质量波动导致路书质量不稳 | 中 | 中 | 引用程序透传 + 时效标注；阶段③建立基线回归 |
| Docker Desktop WSL 集成未开启 | **确定** | 低 | 已在 13.2 写明前置步骤 |

---

## 17. 待你确认的事项

### ★ 需要拍板的 3 项

| # | 问题 | 选项 | 我的建议 |
| :--- | :--- | :--- | :--- |
| **①** | 默认检索 Provider | A. Tavily（你已有 key）<br>B. 百度千帆（国内免费 100/天）<br>C. DuckDuckGo（零配置无需 key）<br>D. 博查（¥36/1000） | **A**，并把 B/C/D 作为可切换项写进 README |
| **②** | TypeScript 版本 | A. 5.9.x（成熟）<br>B. 7.0.2（最新，Go 重写版） | **A**；B 在阶段③做兼容性实验 |
| **③** | 会话状态方案 | A. LangGraph SQLite Checkpointer<br>B. 自管 SQLite 表 + 显式入口路由 | **A**，代码更少且原生支持断点续聊。**已实测验证可行**（见 18.V2） |

### 需要你确认的理解（是否有偏差）

| # | 我的理解 | 请确认 |
| :--- | :--- | :--- |
| ④ | **不做账号/登录体系**（本地单用户工具） | ? |
| ⑤ | **不做 PDF 服务端生成**，用「HTML + 浏览器打印」替代 | ? |
| ⑥ | 前端保持**单页三栏**，不做多页应用与移动端深度适配 | ? |
| ⑦ | 检索 Provider 做**可插拔**（5 个内置），而非只留一个 | ? |
| ⑧ | 保留旧项目的设计语言与 Prompt 思路，但**代码全部重写** | ? |

---

## 18. 技术可行性验证（阶段②实测记录）

规划定稿前，对方案中**最关键的机制**做了实机验证。**4 项全部通过**，方案无阻塞。

### V1. 显式入口路由 —— 修复 P0-1 的核心机制 ✅

**验证目标**：能否让 LangGraph 按会话状态决定进入哪个节点，替代旧项目固定的 `START → collector`。

**方法**：把条件边挂在 `START` 上 —— `g.add_conditional_edges(START, route_entry, {...})`

**实测结果**：

| 入口 `status` | 实际执行链 |
| :--- | :--- |
| `collecting` | `collector → planner` |
| `planning` | `planner` |
| **`reviewing`** | **`refiner`** ✅ |

→ 旧项目「第 2 轮用户说"确认"仍走 collector」的缺陷，在新机制下**彻底解决**。

> **附带印证**：`draw_mermaid()` **不渲染条件边**（只画普通边），这与阶段①在旧项目上观察到的现象完全一致 —— 属框架渲染特性，**不代表图结构损坏**。因此判断可达性**必须用运行时 `stream()`**，不能只看静态图渲染。这条经验已写入第 14 章测试策略。

### V2. SQLite Checkpointer —— 持久化与断点续聊 ✅

| 验证项 | 结果 |
| :--- | :--- |
| `SqliteSaver` 导入 | ✅ |
| `AsyncSqliteSaver`（后端全异步需要它） | ✅ |
| 写入后读取同一 thread 状态 | ✅ |
| **重新建立连接（模拟进程重启）后读取** | ✅ 状态未丢失 |

→ 会话持久化、断点续聊、重启不丢，均确认可实现。

### V3. `with_structured_output` —— 根除 KeyError ✅

`ChatOpenAI(...).with_structured_output(PydanticModel)` 可用（返回 `RunnableSequence`）。

→ 旧项目 `data["status"]` 直接下标导致 500 的问题，在 schema 约束下**从机制上不可能再发生**。

### V4. 各检索 Provider 的 SDK 可用性（PyPI 实测）

| Provider | 包 | 版本 | 实现方式 |
| :--- | :--- | :--- | :--- |
| Tavily | `tavily-python` | 0.8.5 | 官方 SDK |
| DuckDuckGo | `ddgs` | 9.16.0 | 官方 SDK（`duckduckgo-search` 已更名） |
| 百度千帆 | `qianfan` | 0.4.12.3 | 官方 SDK |
| 博查 Bocha | **无官方包** | — | 用 `httpx` 直接调 REST API（≈50 行） |
| SearXNG | **无需 SDK** | — | 自建服务，`httpx` 调 JSON API |

→ 印证第 7 章判断：**Provider 抽象层是必要的**，且每个实现的成本确实可控。

### 验证环境

Python 3.13.13（并在 3.11.15 复现）· LangGraph 1.2.14 · langgraph-checkpoint-sqlite 3.1.1 · langchain-openai 1.7.0

---

## 19. 可信度设计与免责（依据用户反馈新增）

### 19.1 设计出发点

你的原话，也是本设计的唯一目标：

> 「我的体验是关于这些信息**哪怕 AI 说的再信誓旦旦我也必须得自己去官网验证一下**」

→ 系统的职责**不是让用户相信 AI**，而是**让用户能方便地验证 AI**。

### 19.2 三层可信度机制

| 层 | 机制 | 用户得到什么 |
| :--- | :--- | :--- |
| **可溯源** | 引用编号 + 程序透传的真实 URL（已有设计） | 「AI 是从哪读到的」 |
| **可核验** | **官方办理渠道**（本次新增） | 「我该去哪办、去哪验」 |
| **有告知** | 免责声明 + 信息时效标注（本次新增） | 「这内容有多可靠、什么时间点的」 |

### 19.3 引用 ≠ 验证：两个必须分开的东西

这是本次设计的关键区分：

| | 引用 Citation | 验证渠道 Verification |
| :--- | :--- | :--- |
| 回答的问题 | 我从**哪读到的** | 你**去哪办理/核实** |
| 典型例子 | 某篇旅游攻略、某篇新闻 | **12306 官网**、景点官方公众号 |
| 数量 | 多（一次 10~20 条） | **少（一份路书 2~5 条）** |
| 时效性 | 弱，容易过时 | **强，官方渠道极其稳定** |
| 来源 | 检索结果，程序透传 | **内置官方渠道表**，程序维护 |
| LLM 参与度 | 只写编号 `[1]` | **完全不参与**，由程序按类型匹配 |

### 19.4 官方验证渠道设计

```python
class VerificationChannel(BaseModel):
    kind: Literal["railway","flight","ticket","reservation","hotel","other"]
    label: str                      # 中国铁路 12306
    url: str | None                 # https://www.12306.cn
    app: str | None                 # 铁路12306 App
    miniprogram: str | None         # 微信小程序「铁路12306」
    note: str | None                # 铁路售票唯一官方渠道
    verified_at: date | None        # 该渠道信息最后核实日期
```

内置注册表（节选）：

| kind | label | url | App / 小程序 |
| :--- | :--- | :--- | :--- |
| `railway` | 中国铁路 12306 | `https://www.12306.cn` | 铁路12306 App / 小程序 |
| `flight` | 航空公司官网 | 按航司匹配 | 航司 App |
| `ticket` | 景点官方购票渠道 | 按景点匹配 | 微信搜索「景点名 + 官方」 |
| `reservation` | 场馆官方预约 | 按场馆匹配 | 微信搜索「场馆名」公众号 |
| `hotel` | 酒店官方 / 主流平台 | — | — |

**维护成本控制（呼应你「减小维护成本」的要求）**：

1. 官方渠道的稳定性**远高于价格**（12306 十几年未变），因此内置是合理的；
2. 即便如此，仍支持在配置文件中**覆盖或新增**渠道，不必改代码；
3. 每条渠道标注 `verified_at`，超过 1 年时界面自动提示「该渠道信息核实于 X 年 X 月，建议重新确认」；
4. **不写任何第三方的价格、额度、优惠政策**（见 19.8）。

### 19.5 LLM 的职责：只识别「要办什么」，不负责「去哪办」

Prompt 要求 LLM 额外输出结构化的「待办事项」：

```python
class TodoItem(BaseModel):
    kind: Literal["railway","flight","ticket","reservation","hotel","other"]
    what: str          # "北京→重庆 高铁二等座"
    when: str | None   # "建议提前 15 天购票"
    note: str | None   # "南站上车排队更少"
```

程序据 `kind` 匹配官方渠道 → 渲染成路书中的「**办理与验证**」区块：

```
📌 需要提前办理的事项
┌────────────────────────────────────────────────┐
│ 🚄 火车票  北京→重庆 高铁二等座                  │
│    建议提前 15 天购票                            │
│    官方渠道：中国铁路 12306                       │
│    → www.12306.cn  |  铁路12306 App  |  小程序   │
├────────────────────────────────────────────────┤
│ 🎫 景点门票  长江索道                              │
│    可现场购票，建议错峰                            │
│    官方渠道：微信搜索「长江索道」官方公众号         │
└────────────────────────────────────────────────┘
```

**为什么不让 LLM 直接给链接**：LLM 给的链接会幻觉、会过时；而官方渠道是**有限且稳定**的集合，用程序匹配远比让模型生成可靠。

### 19.6 免责声明（首尾各一次，Markdown 与 HTML 均需）

```
> ⚠️ 本路书由 AI 基于 2026-10-11 15:32 的联网检索结果生成，仅供参考。
> 票价、开放时间、预约政策等信息可能随时变动，出行前请通过【官方渠道】核实。
```

| 位置 | 形式 |
| :--- | :--- |
| 路书开头 | Markdown 引用块 / HTML 顶部醒目徽章 |
| 路书末尾 | 固定提示块（HTML 中为独立卡片，视觉突出） |
| 导出文件名 | 不加（避免干扰），但页脚保留 |

### 19.7 按信息类型分级处理

| 信息类型 | 处理方式 |
| :--- | :--- |
| 票价、门票价格 | 强制标注「仅供参考」；同时归入「需办理事项」给出官方渠道 |
| 开放时间、亮灯时间 | 标注**检索时间**，提示可能随季节调整 |
| 预约政策 | 归入「需办理事项」；提示「政策可能已调整，请以官方为准」 |
| 交通时刻、余票 | 提示「以 12306 / 航司实时信息为准」 |
| 景点特色、美食、路线描述 | 仅需引用编号，风险较低 |

### 19.8 落实「不硬编码易变信息」原则

你举的例子（帮助页写死各家免费额度）正是要消除的模式。统一改为**链接官方来源**：

| 位置 | 原设计（❌ 会过时） | 新设计（✅ 链接官方） |
| :--- | :--- | :--- |
| 帮助页 FAQ「检索要花钱吗」 | 「DuckDuckGo 完全免费」「千帆每天 100 次」「Tavily 每月 1000 额度」 | 一句话说明「各家政策由服务商制定且随时调整，请以官方页面为准」+ **各 Provider 官方定价页链接** |
| 设置页 Provider 下拉 | 括号里写「每天免费 100 次」 | 只写 Provider 名称 + 一个「官方文档 ↗」链接 |
| Provider 连通性提示 | — | 失败时给出该 Provider 的官方状态页 / 文档链接 |

**统一话术**：

> 各家服务的免费额度与计费方式由其官方制定并可能随时调整，本项目不做承诺，请以官方页面为准。

---

## 附：本规划的制定依据

| 依据 | 来源 |
| :--- | :--- |
| 旧项目 22 项缺陷 | `docs/01-legacy-analysis.md` 第 6 章 |
| refiner 不可达等 5 组实测证据 | 同上，第 7 章 |
| 依赖版本、环境约束 | `docs/01b-decision-inputs.md` |
| 检索方案价格调研 | [搜索 API 价格汇总](https://hebangwen.github.io/2026/03/04/search-api-summary/)、[Tavily 计价文档](https://docs.tavily.com/documentation/api-credits)、[Ollama OpenAI 兼容](https://docs.ollama.com/api/openai-compatibility) |
| Python 3.13 可行性 | 阶段②实测（`uv` 建 3.13.13 环境装 LangGraph 1.2.14 成功） |
| 显式入口路由 / Checkpointer / 结构化输出 / 检索 SDK | 阶段②实测，见第 18 章 |
