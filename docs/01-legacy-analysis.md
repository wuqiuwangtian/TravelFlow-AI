# TravelFlow-AI 旧项目拆解报告

> **阶段①交付物** · 状态：待用户验收
> 仓库：`https://github.com/wuqiuwangtian/TravelFlow-AI`
> 本地路径：`/home/wqwt/code/TravelFlow-AI`
> 分析方法：完整源码静态阅读 + 本地真实环境复现运行（Python 3.11.15 + 实际依赖）+ git 历史产物考古

---

## 1. 取证概况

| 项目 | 结果 |
| :--- | :--- |
| 提交总数 | **4** 个，全部集中在 `2025-12-16 13:09 ~ 13:24`（15 分钟内完成） |
| 分支 | 仅 `main`（本地与 `origin/main` 一致，工作区干净） |
| 跟踪文件 | 14 个（其中 4 个是误提交的 `.pyc`） |
| 有效源码量 | **5 个 Python 文件，约 350 行** |
| 仓库体积 | 84 KB（`.git` 212 KB） |
| 前端源码 | **已丢失**（见 2.3） |

提交时间线：

```
35d3f7e 2025-12-16 13:09  Initial commit: TravelFlow-AI based on Next.js and FastAPI
23a100e 2025-12-16 13:18  Initialize README with project details and setup
d509f76 2025-12-16 13:19  Update README.md
65433b4 2025-12-16 13:24  Delete backend/static directory
```

---

## 2. 资产盘点

### 2.1 现存文件

```
TravelFlow-AI/
├── README.md                     39 行
├── backend/
│   ├── server.py                 90 行   FastAPI 接入层
│   ├── core/
│   │   ├── state.py              11 行   状态定义
│   │   ├── graph.py             191 行   LangGraph 编排（核心）
│   │   ├── tools.py              45 行   Tavily 检索工具
│   │   └── pdf_gen.py            18 行   导出（实为写 .md）
│   ├── Dockerfile                25 行
│   ├── requirement.txt           10 行
│   ├── .env.example               6 行
│   └── __pycache__/*.pyc          4 个   ← 误提交的编译产物
└── frontend/                            ← 空目录（见 2.3）
```

### 2.2 缺失资产清单

| 缺失项 | 影响 | 证据 |
| :--- | :--- | :--- |
| `frontend/` 源码 | **产品不可交付**，前端能力为零 | gitlink，见 2.3 |
| `docker-compose.yml` | README 主推的启动方式不存在 | README:36 要求 `docker-compose up --build` |
| `requirements.txt` | **Docker 镜像构建必然失败** | Dockerfile:14 `COPY requirements.txt .`，实际文件名为 `requirement.txt` |
| `.gitignore` | 4 个 `.pyc` 被提交入库 | `git ls-files` 含 `__pycache__` |
| 测试代码 | 零测试 | 无 `tests/`、无 pytest 配置 |
| CI/CD、LICENSE | 无工程化基线 | 无 `.github/`、无 LICENSE |
| `backend/static/` | 旧产出目录被删除 | 65433b4 删除 3 个 `.md`（已从历史中考古还原，见 7.3） |

### 2.3 前端源码丢失（不可逆）

`frontend` 在 git 中记录为 **mode `160000`（gitlink / submodule 指针）**：

```
160000 ad1b431ea6a1c22f980cfdd73f3a07e08e7bf930 0	frontend
```

但仓库中 **不存在 `.gitmodules`**，因此该 gitlink 指向的仓库地址没有任何记录，clone 后只留下一个空目录。

已做的找回尝试（**4 条路径全部失败**）：

1. 查询 `wuqiuwangtian` 的 GitHub 全部公开仓库 → **仅 `TravelFlow-AI` 这 1 个**，不存在独立前端仓库；
2. GitHub 全局 commit 搜索 `ad1b431e…`（`/search/commits`）→ **`total_count: 0`**；
3. 本地 git 对象库：共 31 个对象，`git cat-file -t ad1b431e…` → `could not get object info`，该对象**从未被 fetch 到本地**；
4. 检索本机 Windows 用户目录（Desktop / Documents / Downloads / 全盘深 5 层）→ 无任何 `travel*flow*` 残留。

> **结论（终局）**：Next.js 前端源码**永久不可恢复**，阶段②必须**重新构建前端**。

---

## 3. 技术栈拆解

### 3.1 声明 vs 实际

| 层 | README 声明 | 实际代码 | 判定 |
| :--- | :--- | :--- | :--- |
| 大模型 | DeepSeek-V3 (OpenAI Compatible) | `ChatOpenAI(model="deepseek-chat")` + 自定义 `base_url` | ✅ 一致 |
| Agent 编排 | LangGraph (StateGraph) | `StateGraph(PlanState)`，4 节点 | ✅ 一致（但拓扑有缺陷） |
| 后端 | Python FastAPI | FastAPI 0.143（实测装出） | ✅ 一致 |
| 前端 | Next.js 14 + Tailwind + Shadcn/UI | **源码不存在** | ❌ 缺失 |
| 检索 | Tavily Search API | `tavily-python`，3 个封装工具 | ✅ 一致 |
| 文档生成 | FPDF | 实为 `open().write(md)`，**fpdf2 从未被 import** | ❌ 名不副实 |
| 部署 | Docker & Docker Compose | 仅有 Dockerfile（且构建必失败） | ⚠️ 半成品 |

### 3.2 依赖实测版本（关键风险）

`requirement.txt`（**注意文件名错误**）10 个依赖**全部未锁定版本**：

```
fastapi  uvicorn  langchain  langchain-openai  langgraph
tavily-python  pydantic  python-dotenv  markdown  fpdf2
```

按此文件在当前环境实际安装得到的版本：

| 包 | 装出版本 | 2025-12 编码时的量级 | 风险 |
| :--- | :--- | :--- | :--- |
| `langchain` | **1.4.4** | ~0.3.x | 🔴 大版本跨越 |
| `langchain-core` | **1.6.9** | ~0.3.x | 🔴 大版本跨越 |
| `langgraph` | **1.2.14** | ~0.2.x | 🔴 大版本跨越 |
| `langchain-openai` | **1.7.0** | ~0.2.x | 🔴 大版本跨越 |
| `fastapi` | 0.143.0 | ~0.115 | 🟡 |
| `pydantic` | 2.14.0 | ~2.9 | 🟡 |
| `tavily-python` | 0.8.5 | ~0.5 | 🟡 |

> **结论**：`langchain` / `langgraph` 均从 0.x 跨到 1.x，**构建完全不可复现**。旧代码能 import 成功属侥幸，语义兼容性无保障。这是阶段②必须先解决的工程基线问题（锁版本 + lockfile）。

### 3.3 运行时配置

- Python：Dockerfile 指定 `python:3.11-slim`（`.pyc` 魔数 `a70d0d0a` 亦证实为 3.11）
- 环境变量：`OPENAI_API_KEY` / `OPENAI_BASE_URL` / `TAVILY_API_KEY`
- 模型参数**硬编码**在源码中：`model="deepseek-chat"`, `temperature=0.7`（graph.py:13）

---

## 4. 抽象分层结构

### 4.1 分层视图

```
┌─────────────────────────────────────────────────────────┐
│ L1 接入层        backend/server.py                       │
│   · FastAPI 应用实例、CORS、StaticFiles 挂载             │
│   · POST /api/chat        （对话主入口）                 │
│   · GET  /api/download/{thread_id}（导出）               │
│   · threads_memory: dict  （进程内会话存储）             │
└───────────────┬──────────────────────┬──────────────────┘
                │                      │
                ▼                      ▼
┌───────────────────────────┐  ┌──────────────────────────┐
│ L2 编排层 core/graph.py   │  │ L5 导出层 core/pdf_gen.py│
│   · StateGraph 拓扑定义   │  │   · 写 .md 落盘           │
│   · 4 个节点业务逻辑      │  └──────────────────────────┘
│   · 3 段 Prompt 文本      │
│   · LLM 客户端实例化      │
│   · 2 个路由函数          │
└────────┬──────────────────┘
         │
         ├──────────────┐
         ▼              ▼
┌─────────────────┐ ┌──────────────────────────┐
│ L3 状态层       │ │ L4 能力层 core/tools.py  │
│ core/state.py   │ │   · TavilyClient         │
│  PlanState      │ │   · 3 个 @tool 检索函数  │
│  (TypedDict)    │ └──────────────────────────┘
└─────────────────┘
```

### 4.2 分层职责与问题

| 层 | 文件 | 职责 | 问题 |
| :--- | :--- | :--- | :--- |
| L1 接入 | `server.py` | HTTP 契约、会话存储 | 业务逻辑（状态初始化/入口决策）外泄；`async` 内做同步阻塞调用 |
| L2 编排 | `graph.py` | 拓扑 + 节点实现 + Prompt + LLM 实例 + 路由 | **上帝模块**：5 种职责耦合在一个文件，无 Prompt 模板化、无节点复用 |
| L3 状态 | `state.py` | 状态 schema | 定义与使用不一致（`List[dict]` vs 实际 BaseMessage） |
| L4 能力 | `tools.py` | 检索封装 | `@tool` 装饰器冗余（工具从未绑定给 LLM，仅被手动 `.invoke()`） |
| L5 导出 | `pdf_gen.py` | 文件导出 | 名不副实；相对路径依赖 CWD |

**依赖方向**：`server → {graph, pdf_gen}`，`graph → {state, tools}` —— 方向本身清晰（单向、无环），**主要问题是 L2 职责过载**。

### 4.3 分层设计的合理之处（可继承）

- 前后端分离、接入层与编排层解耦的方向正确；
- 用 `TypedDict` 显式定义 Agent 状态，符合 LangGraph 惯例；
- 把检索能力抽成独立工具层（`tools.py`），并做了领域特化（交通 / 景点 / 避雷三类查询）；
- 有 `status` 字段承载状态机语义的意识。

这些骨架在阶段②可以保留并强化。

---

## 5. 运行时数据流与状态机

### 5.1 请求时序（`POST /api/chat`）

```
前端 ──{message, thread_id}──▶ server.chat_endpoint
                                   │
                                   ├─ thread_id 为空则生成 uuid
                                   ├─ 从 threads_memory 取状态（缺省初始化）
                                   ├─ state["messages"].append({user 消息})
                                   │
                                   ▼
                         app_graph.invoke(state)   ◀── 固定从 START 进入
                                   │
   START ──▶ collector ──(条件路由)──▶ researcher ──▶ planner ──▶ END
              │ LLM 抽取需求           │ Tavily×3      │ LLM 生成计划
              │ 追问 / 置 researching  │ 串行搜索      │ 置 refining
              └── 否则 END（等用户下一轮）
                                   │
                                   ▼
                    回写 threads_memory[thread_id]
                    取 messages[-1].content 作为回复
                    返回 {thread_id, response, plan, status}
```

### 5.2 状态机：设计 vs 实际

**设计意图**（state.py:10 注释）：
`collecting → researching → planning → refining → finished`

**实测可达路径**（本地真实运行，证据见 7.2）：

| 轮次 | 用户输入 | 实际执行节点序列 | 期望行为 | 实际行为 |
| :--- | :--- | :--- | :--- | :--- |
| 第 1 轮 | "去重庆 3 天" | `collector → researcher → planner` | 收集→检索→生成 | ✅ 符合预期 |
| 第 2 轮 | **"确认，就这样"** | `collector → researcher → planner` | 进入 `refiner` 确认收尾 | ❌ **重新全量检索 + 重新生成计划** |

> `refining` / `finished` 是**不可达状态**：`refiner` 节点入度为 0，永远不会被执行。

---

## 6. 缺陷清单

### P0 — 致命（功能不可用 / 必然失败）

| # | 缺陷 | 位置 | 证据与说明 |
| :--- | :--- | :--- | :--- |
| **P0-1** | **`refiner` 节点不可达，计划「确认 / 修改」功能完全失效** | graph.py:186 | 图构建只有 `add_conditional_edges("refiner", ...)`，**没有任何边指向 refiner**。实测第 2 轮用户说"确认"仍执行 `collector → researcher → planner`。后果：① 用户修改意见被当作**新需求**重新抽取，只有 destination/origin/duration/budget 四个字段能生效，其余意见丢失；② 每次追问都触发**全量重新检索**（3 次 advanced 搜索）与**整份计划重生成**，Token 与延迟成本翻倍；③ 计划永远无法"定稿"。 |
| **P0-2** | **Docker 镜像构建 100% 失败** | Dockerfile:14 | `COPY requirements.txt .`，但仓库中文件名为 `requirement.txt`（少 s）。`docker build` 必然报 `requirements.txt: not found`。 |
| **P0-3** | **前端源码丢失** | `frontend` gitlink | 见 2.3，产品无法端到端交付。 |

### P1 — 严重（正确性 / 可用性 / 性能）

| # | 缺陷 | 位置 | 说明 |
| :--- | :--- | :--- | :--- |
| **P1-1** | `async` 端点内执行同步阻塞调用 | server.py:43, 61 | `async def chat_endpoint` 中调用同步 `app_graph.invoke()`，内部含 LLM 与 3 次 Tavily 网络请求（秒级）。**阻塞事件循环**，并发请求互相排队，吞吐退化为串行。**实测：单请求 1.51s，2 个并发总耗时 3.01s —— 完全串行，见 7.5。** |
| **P1-2** | LLM JSON 输出无 schema 校验，缺字段即 500 | graph.py:64-65, 71 | `data["reply"]` / `data["status"]` 直接下标取值。LLM 少返回一个字段即抛 `KeyError`（已实测复现），且无任何错误响应契约。 |
| **P1-3** | 异常处理裸 `except:` 吞掉全部错误 | graph.py:56 | 解析失败时静默降级为"我没听清"，真实错误（网络/鉴权/限流）全部丢失，无法排障。 |
| **P1-4** | `str.strip()` 被误用为「去前缀」 | graph.py:55 | `res.content.strip("```json").strip("```")` 中 `strip` 是**按字符集剥离**，并非去除前后缀。当前因 `{`/`}` 不在字符集内而「偶然可用」（已实测），但对裸 JSON 数组、尾随说明文字、含 `n/s/o/j/\`` 边界的输出会静默损坏数据。正确做法是正则或显式解析。 |
| **P1-5** | 会话存储无上限 / 无 TTL / 无持久化 / 无锁 | server.py:36 | 进程内 `dict`：重启即全丢；无并发保护（同一 `thread_id` 并发写会丢状态）；无容量上限（内存泄漏，且状态里含完整 `research_content` 与 `plan_draft`，单会话可达数百 KB）。 |
| **P1-6** | 「并行搜索」注释与实现不符，实为串行 | graph.py:83-86 | 注释写"并行调用搜索工具"，实际 3 次 `max_results` 4~5 的 `advanced` 检索**顺序执行**，延迟叠加。**实测：注入 3×0.5s 延迟后节点耗时 1.50s（并行应为 0.5s）—— 串行确证。真实环境 advanced 检索单次 2~5s，该节点将耗时 6~15s。** |
| **P1-7** | 消息类型不一致（`dict` 与 `BaseMessage` 混用） | state.py:6 / server.py:55,67 / graph.py:19,132 | 状态声明 `Annotated[List[dict], add_messages]`，但节点内按对象属性访问 `.content`，server 层又用 `hasattr` 兼容两种形态。当前依赖 `add_messages` 隐式转换才未崩，属脆弱耦合。 |

### P2 — 一般（工程质量 / 安全性 / 可维护性）

| # | 缺陷 | 位置 | 说明 |
| :--- | :--- | :--- | :--- |
| P2-1 | CORS 全开 `allow_origins=["*"]` | server.py:30 | 生产环境任意站点可跨域调用，且未来若加 Cookie 鉴权将失效。 |
| P2-2 | 硬编码 `http://localhost:8000` 返回 URL | server.py:90 | 部署到任何非本地环境即返回错误链接；应由配置或请求上下文生成。 |
| P2-3 | 服务必须在 `backend/` 作为 CWD 启动；项目无包结构 | server.py:11-12, 22-24 | 项目根目录 `import server` **实测 `ModuleNotFoundError`**。无 `__init__.py`、无 `pyproject.toml`，无法作为包安装或从根目录运行。另：`static` 采用相对路径且 `os.makedirs` 在 import 期产生副作用，写入位置随 CWD 漂移。 |
| P2-4 | `load_dotenv()` 重复 3 次、import 重复 | server.py:2,3,10,16 | 代码整洁问题，同时暴露"边写边补"的历史痕迹（含 `# --- 新增这两行 ---` 等残留注释）。 |
| P2-5 | 模型名 / 温度硬编码 | graph.py:13 | 无法按环境切换模型，无法做 A/B 与降级。 |
| **P2-6** ⚠️ | 检索 query 硬编码年份 `2024` | tools.py:35, graph.py:85 | **实际影响已达 P1 级**（见 7.7 实测）：该缺陷使 2025-12-16 生成的路书仍写"三峡博物馆需预约"，而该馆 2025-10-01 起已取消预约 —— **生成当天即过时**。属正确性缺陷，非整洁问题。 |
| P2-7 | `@tool` 装饰器冗余 | tools.py:24,29,38 | 工具从未 `bind_tools` 给 LLM，装饰器不产生收益，反而误导架构理解。 |
| P2-8 | 依赖 `fpdf2` / `markdown` 已装未用 | requirement.txt:9,10 | 无效依赖；且「生成 PDF」的承诺未实现（实际产出 `.md`）。 |
| P2-9 | 用 `print` 输出日志 | graph.py:81 | 无结构化日志、无 trace id，多会话并发时无法追溯。 |
| P2-10 | 无输入校验 / 无长度限制 / 无速率限制 | server.py:38-40 | `message` 无长度与内容约束，可被超长输入拖垮 LLM 成本。 |
| P2-11 | `missing_fields` 定义了但几乎未被消费 | state.py:11 | 字段收集与追问逻辑未闭环。 |
| P2-12 | README 与实际严重不符 | README | ① `docker-compose up --build` 无对应文件；② 访问 `localhost:3000` 但前端不存在；③ 承诺 PDF 实为 Markdown；④ clone URL 仍是"你的用户名"占位符。 |

---

## 7. 实测证据（可复现）

复现环境：`uv venv --python 3.11 .venv-verify` + `uv pip install -r backend/requirement.txt`（已验证成功）。

### 7.1 图拓扑

`workflow` 原始 builder 数据：

```
nodes    : ['collector', 'planner', 'refiner', 'researcher']
edges    : {('researcher','planner'), ('__start__','collector'), ('planner','__end__')}
branches : {'collector': ['route_after_collect'], 'refiner': ['route_after_refine']}
```

→ `refiner` **未出现在任何 edge/branch 的目标位置**，入度为 0。

### 7.2 真实执行链（关键证据）

```python
steps = [list(c.keys())[0] for c in app_graph.stream(state, stream_mode='updates')]
```

```
第1轮 执行节点序列: collector -> researcher -> planner
第2轮(用户确认) 执行节点序列: collector -> researcher -> planner
refiner 是否出现: False
```

### 7.3 历史产物考古（旧系统真实输出）

最后一个提交删除了 `backend/static/`，已从 `35d3f7e` 还原 3 份真实生成的路书，存放于 `docs/legacy-samples/`：

| 文件 | 主题 | 行数 |
| :--- | :--- | :--- |
| `travel_plan_b97e8fa1-….md` | 重庆 3 天 2 夜（预算 2000 元） | 57 |
| `travel_plan_ca240c67-….md` | 重庆 3 天 2 夜 | 87 |
| `travel_plan_e3588dc1-….md` | 重庆 3 天 2 夜（预算 5000 元） | 63 |

**质量评估**：结构完整（交通 / 住宿 / 逐日行程 / 费用表 / 避雷贴士 / 参考资料），避雷信息确实被高亮，费用表做了经济型与舒适型双档 —— **产品思路与 Prompt 设计是有效的，值得继承**。这些文件可直接作为阶段③的 **golden sample 回归基线**。

### 7.4 「防幻觉」机制核查（重要存疑点）

系统的核心卖点是"引用来源、防幻觉"，实现方式是 **Prompt 要求 LLM"保留调研数据中的 URL"**，即把引用链路的正确性**完全托付给 LLM 复写**。

抽样核查产出中的 6 条 URL：

| URL | HTTP 状态 |
| :--- | :--- |
| `m.cq.bendibao.com/tour/49116.shtm` | ✅ 200（真实） |
| 5 条 `zhuanlan.zhihu.com/p/…` | ⚠️ 全部 403（知乎对非浏览器请求统一拦截，**无法据此判定真假**） |

> 说明：曾怀疑 19 位 ID 是幻觉，后经搜索确认**知乎现行确有 19 位 ID 的文章**，故该怀疑不成立，此路验证手段失效。
>
> **但架构层面的风险是确定的**：URL 由 LLM 二次生成，程序**没有任何校验或回填**。这是阶段②必须从设计上根除的问题 —— 引用必须由程序从检索结果**结构化透传**，不允许经过 LLM 重写。

### 7.5 并发与性能实测

测试方式：真实加载 `server.app`（FastAPI），经 `httpx.ASGITransport` 在**同一事件循环内**发起请求；向 LLM 与 3 个检索工具各注入 0.3s 人工延迟（单请求理论耗时 = LLM + 3×检索 + LLM = 1.5s）。

| 场景 | 实测耗时 | 判定 |
| :--- | :--- | :--- |
| 单请求 `POST /api/chat` | **1.51s**（HTTP 200） | 接口本身可用 |
| **2 个并发请求** | **3.01s** | 应为 ~1.51s；实测精确等于 2×1.51s |

→ **完全串行**，P1-1 成立。当前架构并发能力为 **1**，真实环境下每个用户需等待 6~15s 的串行检索。

### 7.6 正常路径验证（用于界定重构边界）

只呈现缺陷会导致误判，故对主干正常路径同样做了实测：

| 轮次 | 输入 | 实测节点链 | 结果 |
| :--- | :--- | :--- | :--- |
| 第 1 轮 | "我想去重庆"（信息不全） | `collector` | ✅ 正确追问「请问从哪出发？玩几天？预算多少？」，`status=collecting`，`missing_fields=['origin','duration','budget']` |
| 第 2 轮 | "从北京出发，3天，预算2000" | `collector → researcher → planner` | ✅ 需求正确累积为 `{destination:重庆, origin:北京, duration:3天, budget:2000}`，进入检索与生成 |

> **边界界定**：**「信息收集 → 定向检索 → 生成路书」主干是通的，Prompt 设计与产品思路有效**。缺陷集中在三处：① 确认/修改闭环（refiner 不可达）；② 工程基线（依赖/构建/打包）；③ 并发模型。
>
> 这直接决定阶段②策略：**重构，而非推倒重写** —— 保留 Prompt 资产与主干流程，重建编排闭环、工程基线与并发模型。

### 7.7 产出内容的事实核验（「防幻觉」机制实效评估）

7.4 只验证了 URL（受知乎 403 阻断，无结论）。本节改为直接核验**内容事实断言**，用公开权威来源交叉验证。

| 路书中的断言 | 核验结论 | 依据 |
| :--- | :--- | :--- |
| 人民大礼堂门票 **8 元** | ✅ 属实 | 多篇实地攻略标注"8元" |
| 重庆动物园门票 **25 元** | ✅ 属实 | 多方来源"25 元看 23 只熊猫" |
| 三峡博物馆**免费** | ✅ 属实 | 博物馆官方信息 |
| 三峡博物馆**需预约** | ❌ **已过时** | 重庆市政府网：2025-10-01 起多家博物馆取消预约、凭身份证入馆；三峡博物馆亦于 2025-11-03 发布《关于取消预约入馆的公告》 |
| 「金西梅」是色素腌制、须避雷 | ✅ **属实** | 重庆网络问政《关于整治景区"哈儿果"乱象的建议》、科普中国《小心"哈儿果"——人造水果的"巅峰之作"》、市场监管部门群发提醒短信 |
| 长江索道单程 **20 元** / **30 元** | ⚠️ **两份路书自相矛盾** | 同批次产出内部冲突，不可能同时正确 |

**结论分三层：**

1. **检索链路有效 —— 产品价值主张部分成立。**
   内容事实大体有据可查，避雷信息尤其真实（有政府问政与市监行动佐证）。这说明 **Tavily 定向检索 + Prompt 设计的组合不是空谈**，是本项目最值得继承的资产。

2. **但输出不可靠，存在三类系统性缺陷：**
   - **不一致**：同一批次生成的路书对同一事实给出不同数字（索道 20/30、动物园 25/29），无任何一致性校验；
   - **时效性「自我加速」过时**：检索 query **硬编码 `2024`**（tools.py:35、graph.py:85），导致 2025-12-16 生成的路书仍携带 2024 年信息。三峡博物馆早在 2025-10-01 就已取消预约，12 月生成的路书却仍写"需预约"——**生成当天即过时，且由自身缺陷造成**；
   - **无事实校验层**：票价、开放时间、预约政策全部由 LLM 一次性生成，程序不校验、不标注数据截止时间。

3. **对阶段②的直接要求**（无论 D1~D8 如何选，这三点都需要）：
   - 检索 query **禁止硬编码年份**，须按运行时日期动态构造；
   - 产出必须标注 **「信息截止时间」**；
   - 关键事实（票价、开放时间）应结构化保留**来源 + 抓取时间**，供前端展示与二次校验。

---

## 8. 结论

### 8.1 可复用资产

1. **产品定义与 Agent 编排思路**：多轮需求收集 → 定向检索（交通/景点/避雷）→ 结构化路书生成 → 迭代确认，闭环设计合理；
2. **Prompt 资产**：需求抽取 JSON schema、路书结构要求、避雷高亮要求，可直接迁移并打磨；
3. **领域工具设计**：三类定向检索（12306/携程、官方景点、小红书/知乎避雷）的差异化 query 策略 —— **经 7.7 实证，该检索链路确实能取回真实、有据可查的内容，是本项目最核心的可继承资产**；
4. **分层骨架**：接入层 / 编排层 / 状态层 / 能力层 / 导出层的划分方向正确；
5. **历史产出样例**：3 份真实路书，可作回归基线。

### 8.2 必须重做的部分

> **策略定位：重构，不推倒重写。** 主干流程与 Prompt 资产已验证有效（见 7.6），予以保留复用；下列部分需重建。

1. **前端全部**（源码永久不可恢复）；
2. **工程基线**：依赖锁版本 + lockfile、`.gitignore`、配置外置、`docker-compose.yml`、Dockerfile 修正、测试与 CI；
3. **编排层重构**：修复 refiner 不可达、拆分上帝模块、Prompt 外置；
4. **状态机重设计**：显式入口路由（按 `status` 决定进入哪个节点），而非固定 `START → collector`；
5. **会话与并发模型**：持久化 + TTL + 并发安全 + 异步非阻塞；
6. **引用可信链路**：从"LLM 复写 URL"改为"程序结构化透传 + 校验"。

### 8.3 缺陷统计

| 等级 | 数量 |
| :--- | :--- |
| P0 致命 | 3 |
| P1 严重 | 7 |
| P2 一般 | 12 |
| **合计** | **22** |

---

## 9. 移交阶段②的待决策事项

请用户确认以下选项后，再进入「重新设计升级」阶段：

| # | 决策点 | 备选项 |
| :--- | :--- | :--- |
| D1 | 前端如何处理 | A. 重写 Next.js 14 + Tailwind + Shadcn（沿用原技术栈）／ B. 换用 Vite + React 更轻量／ C. 其他 |
| D2 | 后端技术栈 | A. 保持 FastAPI + LangGraph，仅升级与重构／ B. 换编排框架（如 LangChain 1.x 新 Agent API 或自研状态机） |
| D3 | 依赖策略 | A. 锁定到稳定版本并提交 lockfile（推荐）／ B. 直接升级到 LangChain 1.x 全新技术栈 |
| D4 | 会话存储 | A. SQLite／ B. Redis／ C. 保持内存但加 TTL 与上限 |
| D5 | LLM 与检索 | 是否继续 DeepSeek-V3 + Tavily？是否需要多模型可切换？ |
| D6 | 部署目标 | A. Docker Compose 单机／ B. 云服务器 + 域名／ C. 其他（影响阶段④） |
| D7 | 引用可信链路 | 是否采纳「程序结构化透传，禁止 LLM 重写 URL」作为硬约束 |
| D8 | 范围边界 | 是否保留「生成 PDF」功能（需中文字体方案），还是仅 Markdown 导出 |

---

## 附录：本次新增文件

| 文件 | 说明 |
| :--- | :--- |
| `docs/01-legacy-analysis.md` | 本报告（阶段①交付物） |
| `docs/legacy-samples/*.md` | 从 git 历史还原的 3 份旧系统真实产出（阶段③回归基线） |
| `.gitignore` | 新增，覆盖 `__pycache__`、虚拟环境、`.env`、运行期 `static/` |
| `.venv-verify/` | 复现用 Python 3.11 虚拟环境（已被 `.gitignore` 忽略，不提交） |

> **旧代码未做任何修改**：`git status` 仅显示上述新增项。
