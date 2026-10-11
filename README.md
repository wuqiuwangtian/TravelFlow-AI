# ✈️ TravelFlow-AI（AI 旅行助手）

> 一个**小白友好**的、通过**本地 Web 服务**启动的旅行规划助手。
> 基于大模型 + LangGraph 智能体工作流：多轮对话收集需求 → 联网检索真实信息 → 生成结构化路书 → 交互式修改 → 导出 Markdown / HTML。

---

## ⚠️ 项目状态：重构升级中

本仓库正在按四阶段进行重构升级，**当前处于阶段②（重新设计升级）**。

| 阶段 | 内容 | 状态 |
| :--- | :--- | :--- |
| ① | 拆解旧项目技术栈与抽象分层 | ✅ 已完成并验收 |
| ② | 重新设计升级方案 | 🔄 进行中（规划待验证） |
| ③ | 全面测试 | ⏳ 待开始 |
| ④ | 重新部署上线 | ⏳ 待开始 |

### 🚫 当前代码尚不可用，请勿直接部署

阶段①拆解发现旧代码存在**致命缺陷**，已在计划中修复，但**修复尚未实施**：

| 缺陷 | 后果 |
| :--- | :--- |
| `refiner` 节点入度为 0（不可达） | 计划的「确认 / 修改」功能完全失效，修改意见会被当作新需求触发全量重搜重生成 |
| `Dockerfile` 引用 `requirements.txt`，实际文件名为 `requirement.txt` | Docker 镜像**必然构建失败** |
| `frontend/` 为空 submodule 引用，无 `.gitmodules` | 前端源码已永久丢失 |
| 依赖全部未锁版本 | 同一份代码在不同时间安装会得到跨大版本的依赖（实测已装出 LangChain 1.4.4） |
| 检索 query 硬编码 `2024` | 产出内容生成当天即过时 |

完整清单（22 项）见 [阶段①拆解报告](docs/01-legacy-analysis.md#6-缺陷清单)。

---

## 📚 项目文档

| 文档 | 说明 |
| :--- | :--- |
| [阶段① 旧项目拆解报告](docs/01-legacy-analysis.md) | 技术栈、分层结构、数据流、22 项缺陷、5 组实测证据 |
| [决策输入与环境约束](docs/01b-decision-inputs.md) | 本机环境实测数据、D1~D8 决策依据 |
| [**阶段② 决策摘要**](docs/02c-decision-summary.md) | **一页纸**：9 项待拍板 + 实测结论 + 验收标准 |
| [阶段② 升级规划](docs/02-upgrade-plan.md) | 完整设计稿：架构、选型、API 契约、部署方案 |
| [阶段② Prompt 设计](docs/02b-prompt-design.md) | 四个节点的完整 Prompt、结构化输出契约、防幻觉闭环 |
| [历史产出样例](docs/legacy-samples/) | 从 git 历史还原的 3 份真实路书，作为回归基线 |

---

## 🎯 目标形态

| 特性 | 说明 |
| :--- | :--- |
| **小白友好** | 一条命令启动；配置有引导、有自检；报错说人话 |
| **本地 Web 服务** | 前端跑在 `localhost`，数据与模型调用都在自己机器上 |
| **多模型支持** | 云端主流模型（DeepSeek / OpenAI / 通义 / 智谱 / Kimi…）+ **本地部署模型**（Ollama / vLLM / LM Studio） |
| **可插拔检索** | Tavily / 博查 / 百度千帆 / SearXNG / DuckDuckGo 自由切换 |
| **可信引用** | 引用 URL 由程序从检索结果**结构化透传**，不经大模型复写 |
| **双格式导出** | Markdown 与单文件 HTML（可直接打印为 PDF） |
| **双部署方式** | 源码一键脚本 / Docker Compose |

---

## 🛠️ 技术栈（规划中，实施后更新）

**后端**：Python 3.13 · FastAPI · LangGraph 1.x · LangChain · SQLite · uv
**前端**：Next.js 16 · React 19 · TailwindCSS 4 · TypeScript · pnpm
**部署**：Docker Compose / 源码一键启动脚本

> 具体版本锁定表见 [升级规划 第 3 章](docs/02-upgrade-plan.md#3-技术栈与版本锁定)。

---

## 🚀 快速开始

> **重构进行中，暂不可用。** 待阶段②实施完成后，本节将更新为：

```bash
# 方式一：源码部署
git clone https://github.com/wuqiuwangtian/TravelFlow-AI.git
cd TravelFlow-AI
./start.sh

# 方式二：Docker 部署
docker compose up -d
```

---

## 📄 许可证

待定（阶段②确认）。
