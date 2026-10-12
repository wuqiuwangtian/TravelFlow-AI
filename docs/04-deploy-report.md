# 阶段④ 部署验收报告

> **版本**：v1.0.0
> **日期**：2026-10-12
> **结论**：**源码部署方式已完整实测通过**；Docker 方式因本机 Docker Desktop 引擎故障未能验证，已列出待办与排查步骤。

---

## 1. 部署方式验证结果

| 方式 | 状态 | 说明 |
| :--- | :--- | :--- |
| **一键脚本**（`start.sh` / `start.bat`） | ✅ 已验证 | 端到端跑通，见阶段②记录 |
| **下载源码手动部署** | ✅ **本次完整实测** | 全新克隆 → 装依赖 → 启动 → 接口与页面全部正常，见 §2 |
| **Docker Compose** | ⚠️ **未验证** | 本机 Docker 引擎故障，见 §4 |

---

## 2. 源码部署实测记录

为验证「下载源码就能用」这一承诺，在**全新克隆的副本**中完整走了一遍，而非在开发目录里测试。

### 2.1 仓库卫生

```
克隆体积 : 5.2 MB
文件数   : 132
```

检查确认**没有任何应当忽略的内容被提交**：

| 检查项 | 结果 |
| :--- | :--- |
| `node_modules` | 0 个 |
| `.next` | 0 个 |
| `.venv` | 0 个 |
| `data/` | 0 个 |
| `*.db` | 0 个 |
| `.env` | 0 个 |

### 2.2 后端

```bash
cp backend/.env.example backend/.env
cd backend && uv sync
uv run uvicorn app.main:app --host 127.0.0.1 --port 8100
```

结果：

```
健康检查 : {"status":"ok","name":"TravelFlow AI","version":"1.0.0","uptime_seconds":7.52}
配置自检 : llm_ready=False  search_ready=False
           提示 2 条（未配置 LLM_API_KEY / SEARCH_API_KEY）
```

✅ 未配置任何密钥时，服务**仍能正常启动**，并给出可操作的中文提示——这符合「小白友好」的目标：先能跑起来，再去配 Key。

### 2.3 前端

```bash
cd frontend && pnpm install --frozen-lockfile
NEXT_PUBLIC_API_BASE=http://localhost:8100 pnpm build
pnpm start -p 3200
```

结果：

```
依赖安装 : Done in 14.1s using pnpm v12.6.0
生产构建 : 7 个路由全部生成成功

页面可访问性：
  /          HTTP 200
  /sessions  HTTP 200
  /settings  HTTP 200
  /help      HTTP 200
  /compare   HTTP 200
```

### 2.4 前后端联调（跨域）

**白名单外的来源被拒绝**（证明严格模式确实生效）：

```
前端跑在 :3200，后端白名单为 :3100
→ 无 access-control-allow-origin  ← 正确拒绝
```

按文档把白名单改成实际端口后：

```
启动日志    : 跨域白名单（严格模式）: http://localhost:3200, http://127.0.0.1:3200
预检 OPTIONS: HTTP/1.1 200 OK
allow-origin: http://localhost:3200
```

### 2.5 接口冒烟

| 接口 | 结果 |
| :--- | :--- |
| 新建会话 | ✅ 返回 session_id |
| 会话列表 | ✅ 返回总数 |
| 设置读取 | ✅ `provider=deepseek, cors_strict=True` |
| 设置保存 | ✅ 返回明确消息 |

---

## 3. 上线前检查清单

| 检查项 | 结果 |
| :--- | :--- |
| 硬编码密钥扫描（含提交历史） | ✅ 未发现 |
| 版本号一致性（pyproject / config / package.json） | ✅ 均为 1.0.0 |
| `.gitignore` 覆盖 `.env` / `node_modules` / `.next` / `.venv` / `data/` / `*.db` | ✅ 全部覆盖 |
| 服务监听地址 | ✅ 默认 `127.0.0.1`（不对局域网开放） |
| 跨域默认策略 | ✅ 严格模式 + 白名单 |
| 密钥回显 | ✅ 接口只返回「是否已配置」 |
| 配置写入白名单 | ✅ 无法通过接口改 `app_host` 等敏感项 |
| Markdown 原始 HTML | ✅ 已禁用（防导出文件执行脚本） |
| 自动化测试 | ✅ 194 个全绿，覆盖率 94% |
| 前端类型检查与构建 | ✅ 通过 |
| 测试隔离（不读真实 .env、不消耗额度） | ✅ 已加固 |

---

## 4. Docker 部署：待办与排查步骤

### 现象

```
docker version  → Client 29.8.2 正常
                → Server: request returned 500 Internal Server Error
docker ps / info / images / run  → 全部 500
```

`docker-desktop` WSL 发行版显示 Running，但**引擎内部故障，所有 API 均返回 500**。

### 这不是项目问题

- `docker-compose.yml` / 两个 Dockerfile 均已完成并通过 YAML 校验
- 基础镜像曾成功拉取（Clash 关闭后 `node:22-alpine` 55MB 仅用 9.5 秒）
- 后端依赖层已开始执行
- 故障发生在 **Docker Desktop 引擎本身**

### 建议排查顺序

1. Docker Desktop → **Troubleshoot → Restart**
2. 若无效：**Settings → Resources → WSL Integration**，确认当前发行版已勾选
3. 若仍无效：Troubleshoot → **Clean / Purge data**（会清除本地镜像）
4. 最后手段：**Reset to factory defaults**

### 修复后的验证步骤

```bash
docker compose build
docker compose up -d
curl http://localhost:8000/api/system/health     # 期望 200
# 浏览器打开 http://localhost:3000
```

> ⚠️ 容器需要能访问外网（`uv sync` / `pnpm install`）。
> 若使用代理工具，请确认其 TUN 模式未阻断 Docker 的容器网络。

---

## 5. 验收结论

- **可交付**：v1.0.0 已具备「下载源码即可运行」的完整能力，用户按 README 三步即可启动；
- **待补**：Docker 部署方式需在 Docker 引擎修复后补做验证，验证清单见 §4；
- **已知限制**：DuckDuckGo 检索源在国内网络需代理，建议改用国内检索源或自建 SearXNG。
