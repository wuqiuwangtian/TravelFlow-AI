# ✈️ TravelFlow AI · AI 旅行助手

> 一个跑在**你自己电脑上**的旅行规划助手。
> 用大白话描述想去哪、玩几天、预算多少，它会联网查真实信息，生成一份可以照着走的行程路书。

**为什么用它**

- 🏠 **数据只在你本地** —— 会话和路书存在自己的电脑上，不上传任何服务器
- 🔍 **联网查真实信息** —— 不是凭记忆编造，而是检索后再生成，并附上信息来源
- 💰 **自己掌控模型** —— 用你自己的 API Key，或接本地模型（Ollama 等），不经过第三方
- 📄 **能带走的成品** —— 导出 Markdown 或精美单页 HTML，手机电脑都能看
- 🎚️ **详略可调** —— 从「极简」到「保姆级」四档，一日游和一周行程篇幅自然不同

---

## 🚀 快速开始

两种方式**任选其一**。

> 💡 **没配 API Key 也能先启动** —— 打开界面后在「设置」里填即可。

### 方式一：一键脚本（最简单，推荐）

```bash
git clone https://github.com/wuqiuwangtian/TravelFlow-AI.git
cd TravelFlow-AI

./start.sh        # macOS / Linux / WSL
start.bat         # Windows：双击运行
```

脚本会自动检查环境 → 安装依赖 → 启动前后端 → 打开浏览器。按 `Ctrl+C` 同时停止。

**需要先装**：[uv](https://docs.astral.sh/uv/) 和 [Node.js](https://nodejs.org/) 20 以上。
缺了的话脚本会直接告诉你安装命令，不会默默失败。

### 方式二：手动启动（适合开发调试）

```bash
# 终端 1 —— 后端
cd backend
cp .env.example .env
uv sync
uv run uvicorn app.main:app --reload --port 8000

# 终端 2 —— 前端
cd frontend
pnpm install
pnpm dev -p 3100
```

访问 <http://localhost:3100>，接口文档在 <http://localhost:8000/docs>。

---

## ⚙️ 配置

推荐**用界面配置**（保存后立即生效，无需重启）：

1. 打开应用 → 左侧「**设置**」
2. 选模型服务商 → 填 API Key → 点「**获取可用模型**」挑一个（不用手写模型名）
3. 保存

也可以直接改环境文件 `backend/.env`（写法参考 `backend/.env.example` 的注释）。

### 大模型

支持**任何 OpenAI 兼容接口**，包括本地部署的模型：

| 类型 | 服务商 | 说明 |
| :--- | :--- | :--- |
| 云端 | DeepSeek、月之暗面、智谱、通义千问、OpenAI、Anthropic 等 | 需要 API Key |
| 本地 | Ollama、vLLM、LM Studio、llama.cpp、Xinference | 通常无需 Key，填本机地址即可 |

> 各家的政策与模型名称可能随时调整，请以**服务商官方文档**为准。
> 本项目的「设置」页会直接向服务商查询当前可用模型，不写死模型名。

### 信息检索

**需要配一个检索源**，路书才会包含真实的票价、开放时间等信息：

| 检索源 | 是否需要 Key | 说明 |
| :--- | :--- | :--- |
| **Tavily** | 需要 | 默认选项，专为 AI 优化的搜索 API |
| **博查 / 百度千帆** | 需要 | 国内直连，无需代理 |
| **SearXNG** | 不需要 | 需自行部署一个 SearXNG 实例，把地址填进设置即可 |
| **DuckDuckGo** | 不需要 | 零配置，但在中国大陆**需要代理** |

> 不确定选哪个？国内网络建议用**百度千帆**或**博查**（申请免费额度即可）；
> 不想申请任何 Key，可自行部署 [SearXNG](https://docs.searxng.org/)（开源元搜索），
> 然后在设置里把检索源选为 SearXNG 并填入地址。

---

## 🧭 怎么用

1. **新建一次旅行** —— 说一句「我想去重庆玩 3 天，从北京出发，预算 2000」
2. **补齐信息** —— 目的地、出发地、天数、预算四项齐全后才会开始检索（缺了它会追问）
3. **看路书、提修改** —— 生成过程会实时显示（正在检索 / 正在撰写，正文逐字出现），
   不满意就直说「第二天太赶了，轻松点」，它只改你说的部分，不重新检索
4. **满意就确认** —— 回复「确认」定稿
5. **导出带走** —— Markdown 或 HTML，HTML 是单文件，双击即可打开

**几个实用功能**

- **多方案对比**：不满意时可以从任意版本**分叉**出新方案，两个方案各自独立演进，随时切换对比
- **改错了能回退**：每个版本都保留，随时回到早期版本
- **重新检索**：信息过时了一键重查

---

## ❓ 常见问题

<details>
<summary><b>提示「连接不上后端服务」</b></summary>

1. 先确认后端在运行：访问 <http://localhost:8000/api/system/health>，应返回 `{"status":"ok",...}`
2. 若前端改了端口，后端要放行对应来源：在 `backend/.env` 里改 `APP_CORS_ORIGINS`，加上前端地址
   - 用 `start.sh` / `start.bat` 启动时会**自动处理**，无需手动改
3. **在 WSL 里请用 `localhost` 而不是 `127.0.0.1`** —— WSL2 的端口转发保证 `localhost` 可用

</details>

<details>
<summary><b>提示「模型返回的内容格式异常」</b></summary>

部分服务商（如 DeepSeek）不支持 `json_schema` 格式，会直接报错。
本项目已做**自动降级**（改用 `json_object`）。若仍出现，请到「设置」点「测试连接」查看具体原因。

</details>

<details>
<summary><b>提示「检索服务暂时不可用」</b></summary>

多半是网络问题：**DuckDuckGo 在中国大陆需要代理**。
建议改用百度千帆 / 博查（国内直连），或自建 SearXNG（无需 Key）。

</details>

<details>
<summary><b>我的数据存在哪？怎么备份？</b></summary>

- 配置：`backend/.env`——含有 API Key，请勿提交或分享
- 会话与路书：`backend/data/travelflow.db`（SQLite 单文件）

都只在你本机，不会上传。

**建议定期备份** —— 所有会话、分支、路书都在这一个文件里，删掉或损坏就没了：

```bash
cp backend/data/travelflow.db ~/travelflow-backup-$(date +%Y%m%d).db
```

想清空全部记录，直接删掉这个文件即可（下次启动会自动重建）。

</details>

---

## 🔒 安全与隐私

- 服务默认**只监听 `127.0.0.1`**，不对局域网开放
- 跨域默认**严格模式**，只放行你指定的前端地址
- API Key **永不回显**（接口只返回「是否已配置」）
- 导出的 Markdown / HTML **禁用原始 HTML**，不会被注入脚本
- 路书中的引用链接由**程序**填入（模型被禁止输出 URL），避免编造来源

---

## 🛠 技术栈

| 层 | 技术 |
| :--- | :--- |
| 前端 | Next.js 16 · React 19 · TypeScript 5.9 · TailwindCSS 4 |
| 后端 | Python 3.13 · FastAPI · Pydantic v2 |
| 编排 | LangGraph（收集 → 检索 → 生成 → 修订） |
| 存储 | SQLite（本地文件） |
| 依赖管理 | uv（Python）· pnpm（前端），均锁定版本 |

**项目结构**

```
TravelFlow-AI/
├── backend/          # FastAPI 服务
│   └── app/
│       ├── api/      # HTTP 接口
│       ├── agents/   # LangGraph 编排与 Prompt
│       ├── llm/      # 模型接入（OpenAI 兼容）
│       ├── search/   # 检索源适配
│       ├── storage/  # SQLite 持久化
│       └── export/   # Markdown / HTML 导出
├── frontend/         # Next.js 界面
├── start.sh          # Linux / macOS / WSL 一键启动
└── start.bat         # Windows 一键启动
```

---

## 📋 更新日志

见 [CHANGELOG.md](CHANGELOG.md)。

## 📄 许可

个人学习项目。使用第三方模型与检索服务时，请遵守对应服务商的条款。
