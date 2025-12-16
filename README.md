# ✈️ TravelFlow-AI (AI 旅游助手)

> 一个基于 DeepSeek 大模型、LangGraph 智能体工作流和 Next.js 开发的全栈 AI 旅游规划助手。

## ✨ 项目简介

本项目利用 **Next.js + FastAPI** 前后端分离架构，结合 **DeepSeek-V3** 接口与 **Tavily 联网搜索** 能力，实现了一个具备“防幻觉”机制的旅游规划 Agent。

它不仅能通过多轮对话收集用户需求，还能针对 **12306 车次** 和 **小红书避雷贴** 进行定向检索，最终生成并提供下载 Markdown/PDF 格式的详细路书。

## 🛠️ 技术栈

- **大模型**: DeepSeek-V3 (OpenAI Compatible)
- **Agent 编排**: LangGraph (StateGraph Workflow)
- **后端**: Python FastAPI
- **前端**: Next.js 14 + TailwindCSS + Shadcn/UI
- **工具**: Tavily Search API (联网检索), FPDF (文档生成)
- **部署**: Docker & Docker Compose

## 🚀 快速开始

### 1. 克隆项目
```bash
git clone https://github.com/你的用户名/TravelFlow-AI.git
cd TravelFlow-AI
```
2. 配置环境变量
```bash
在 backend 目录下创建 .env 文件：
code
Ini
OPENAI_API_KEY=sk-xxxx
OPENAI_BASE_URL=https://api.deepseek.com/v1
TAVILY_API_KEY=tvly-xxxx
```
3. 使用 Docker 一键启动
```bash
code
Bash
docker-compose up --build
```
访问浏览器：http://localhost:3000 即可开始规划你的旅行！
