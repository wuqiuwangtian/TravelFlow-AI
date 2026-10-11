# ✈️ TravelFlow-AI（AI 旅行助手）

> 一个**小白友好**的、通过**本地 Web 服务**启动的旅行规划助手。
> 基于大模型 + LangGraph 智能体工作流：多轮对话收集需求 → 联网检索真实信息 → 生成结构化路书 → 交互式修改 → 导出 Markdown / HTML。

---

## ⚠️ 项目状态：重构升级中

本仓库正在按四阶段重构升级，**当前处于阶段②，M1 工程基线已完成**。

| 阶段 | 内容 | 状态 |
| :--- | :--- | :--- |
| ① | 拆解旧项目技术栈与抽象分层 | ✅ 已完成并验收 |
| ② | 重新设计升级方案（M1~M7） | ✅ **已完成** |
| ③ | 全面测试 | ✅ **已完成**（186 测试 / 覆盖率 94%） |
| ④ | 重新部署上线 | 🔄 进行中 |

### 📦 里程碑进度

| 里程碑 | 内容 | 状态 |
| :--- | :--- | :--- |
| **M1** | 工程基线（骨架 / 配置 / 日志 / 错误体系 / 自检接口） | ✅ **完成** |
| **M2** | LLM 与检索抽象层（多模型 + 可插拔检索） | ✅ **完成** |
| **M3** | Agent 编排重构（修复 refiner 闭环、并行检索、会话持久化） | ✅ **完成** |
| **M4** | 会话 / 分支 / 版本 / 双格式导出 | ✅ **完成** |
| **M5** | 前端多页面（Next.js 16 + React 19 + Tailwind 4） | ✅ **完成** |
| **M6** | 打包与部署（一键脚本 + Docker Compose） | ✅ **完成** |
| **M7** | 测试与加固（覆盖率 92%，164 个测试） | ✅ **完成** |

> **旧代码已全部删除**（`server.py`、`core/`、`requirement.txt`、旧 `Dockerfile`）。
> 旧项目的技术方案与 22 项缺陷已完整记录在 [阶段①拆解报告](docs/01-legacy-analysis.md)，git 历史亦可随时追溯。
> 新代码从 `backend/app/` 开始，按 [M1 蓝图](docs/02e-m1-blueprint.md) 推进。

---

## 📚 项目文档

| 文档 | 说明 |
| :--- | :--- |
| [阶段① 旧项目拆解报告](docs/01-legacy-analysis.md) | 技术栈、分层结构、数据流、22 项缺陷、5 组实测证据 |
| [决策输入与环境约束](docs/01b-decision-inputs.md) | 本机环境实测数据、D1~D8 决策依据 |
| [**阶段② 决策摘要**](docs/02c-decision-summary.md) | **一页纸**：已拍板事项 + 实测结论（建议先看这份） |
| [阶段② 升级规划](docs/02-upgrade-plan.md) | 完整设计稿：架构、选型、API 契约、部署方案 |
| [阶段② Prompt 设计](docs/02b-prompt-design.md) | 四个节点的完整 Prompt、结构化输出契约、防幻觉闭环 |
| [阶段② 会话与体验设计](docs/02d-session-ux-design.md) | 会话分支对比、多页面结构、详细度档位、HTML 呈现 |
| [**M1 实施蓝图**](docs/02e-m1-blueprint.md) | 编码起点：目录结构、文件职责、完成定义、执行步骤 |
| [**阶段③ 测试报告**](docs/03-test-report.md) | 186 个测试 / 覆盖率 94% / 缺陷与已知限制清单 |
| [历史产出样例](docs/legacy-samples/) | 从 git 历史还原的 3 份真实路书，作为回归基线 |
| [可点击原型 · 路书效果](docs/prototype/report-preview.html) | HTML 导出的视觉与动画效果预览 |
| [可点击原型 · 6 个页面](docs/prototype/ui-preview.html) | 前端多页面 UI 结构预览 |

---

## 🎯 目标形态

| 特性 | 说明 |
| :--- | :--- |
| **小白友好** | 一条命令启动；配置有引导、有自检；报错说人话 |
| **本地 Web 服务** | 前端跑在 `localhost`，数据与模型调用都在自己机器上 |
| **多模型支持** | 云端主流模型（DeepSeek / OpenAI / 通义 / 智谱 / Kimi…）+ **本地部署模型**（Ollama / vLLM / LM Studio） |
| **可插拔检索** | Tavily / 博查 / 百度千帆 / SearXNG / DuckDuckGo 自由切换 |
| **可信引用** | 引用 URL 由程序从检索结果**结构化透传**，不经大模型复写 |
| **可核验** | 购票与预约事项附**官方办理渠道**，用户可自行验证 |
| **不误导** | AI 生成声明 + 信息时效标注；第三方政策一律链接官方，不写死数字 |
| **会话分支对比** | 版本树可回退、可分叉；多个方案并排对比，差异高亮 |
| **详细度档位** | 极简 / 标准 / 详细 / 保姆级，篇幅随行程天数动态伸缩 |
| **多页面应用** | 工作台 / 对话规划 / 分支对比 / 会话管理 / 设置 / 帮助 |
| **双格式导出** | Markdown 与单文件 HTML（图文并茂、带动画、可打印为 PDF） |
| **双部署方式** | 源码一键脚本 / Docker Compose |
| **数据全本地** | 无账号体系，所有会话与路书存于本机 SQLite 单文件 |

---

## 🛠️ 技术栈

**后端**：Python 3.13.13 · FastAPI 0.143.0 · LangGraph 1.2.14 · langchain-openai 1.7.0 · Pydantic 2.14.0 · SQLite（LangGraph Checkpointer）· uv
**前端**：Next.js 16 · React 19 · TailwindCSS 4 · TypeScript 5.9 · pnpm

> 完整版本锁定表见 [升级规划 第 3 章](docs/02-upgrade-plan.md#3-技术栈与版本锁定)。

---

## 🚀 快速开始

三种方式任选其一。**未配置 API Key 也能启动** —— 打开界面后在「设置」里填即可。

### 方式一：一键脚本（最简单）

```bash
git clone https://github.com/wuqiuwangtian/TravelFlow-AI.git
cd TravelFlow-AI
./start.sh          # macOS / Linux / WSL
start.bat           # Windows：双击运行
```

脚本会自动检查环境 → 安装依赖 → 拉起前后端 → 打开浏览器；按 `Ctrl+C` 同时停止。

**前置要求**：[uv](https://docs.astral.sh/uv/)（Python 依赖管理器）与 [Node.js](https://nodejs.org/) 20+。
缺少时脚本会直接告诉你安装命令，不会静默失败。

### 方式二：Docker Compose

```bash
cp .env.example .env      # 可选：填写 API Key
docker compose up -d
```

访问 <http://localhost:3000>。

**可选：自带检索服务**（不依赖任何第三方 Key）

```bash
docker compose --profile searxng up -d
# 然后把 .env 里的 SEARCH_PROVIDER 改为 searxng
```

> ⚠️ **Docker 构建尚未成功实测**：本机容器**无法访问外网**（`docker run` 内的 DNS/HTTPS 全部超时），
> 因此 `uv sync` / `pnpm install` 会永久阻塞。判断与 Clash 的 TUN 模式冲突有关，
> 而非 Dockerfile 本身的问题（compose 文件已通过 YAML 校验）。
>
> **若你遇到同样情况**：临时关闭 Clash 的 TUN 模式后重试；国内网络建议用镜像源加速：
>
> ```bash
> docker compose build --build-arg UV_DEFAULT_INDEX=https://pypi.tuna.tsinghua.edu.cn/simple
> ```
>
> **请以阶段④的实测结果为准**，不要假定它已验证。

### 方式三：手动分别启动（开发用）

```bash
# 终端 1 —— 后端
cd backend
cp .env.example .env
uv sync
uv run uvicorn app.main:app --reload           # http://127.0.0.1:8000

# 终端 2 —— 前端
cd frontend
pnpm install
pnpm dev                                        # http://127.0.0.1:3000
```

### 启动之后

| 地址 | 说明 |
| :--- | :--- |
| <http://127.0.0.1:3000> | 界面：工作台 / 对话规划 / 分支对比 / 会话 / 设置 / 帮助 |
| <http://127.0.0.1:8000/docs> | 交互式 API 文档 |
| <http://127.0.0.1:8000/api/system/health> | 健康检查 |
| <http://127.0.0.1:8000/api/system/config> | 配置自检（含**可操作**的提示，而非一句报错） |

**第一次使用**：按界面顶部黄色提示条进入「设置」，选一个模型服务商并填 Key；
不想注册任何服务就直接选「本地 Ollama」。
检索若也不想注册，把检索源改成 **DuckDuckGo**（无需 Key）即可零成本跑通全流程。

**未配置密钥时不会阻止启动**，而是给出可操作的提示：

```
配置提示: 未配置 LLM_API_KEY，请在 .env 中填写（服务商：deepseek），或改用本地模型（LLM_PROVIDER=ollama，需先启动 Ollama）
配置提示: 未配置 SEARCH_API_KEY（检索服务：tavily），可改用 SEARCH_PROVIDER=ddg 免密钥检索
```

### 开发者

```bash
cd backend
uv run pytest              # 运行测试
uv run ruff check app      # 代码检查

cd ../frontend
pnpm typecheck             # 类型检查
pnpm build                 # 生产构建
```

---

## 📄 许可证

待定（阶段②确认）。
