# 浏览器主流程验收

在仓库根目录准备后端，在前端目录运行：

```sh
cd backend
uv sync --frozen
cd ../frontend
pnpm install --frozen-lockfile
pnpm exec playwright install chromium
pnpm build
pnpm test:e2e
```

Linux 缺系统库时使用 `pnpm exec playwright install --with-deps chromium`。默认读取 `backend/.venv` 的 Python（兼容 Windows）；虚拟环境在其他位置时设置 `TRAVELFLOW_TEST_PYTHON` 为 Python 可执行文件路径。

测试自动启动 `127.0.0.1:8137` 的专用后端和 `127.0.0.1:3137` 的生产前端，拒绝复用已占用端口，结束时关闭服务。后端使用独立临时 SQLite 数据库和固定模型、检索替身，不加载 `.env`，不需要真实 API Key，也不接触个人旅行记录。测试种子接口只定义在 `backend/tests/browser_server.py`，不会进入正常应用。

当前12个场景在桌面和手机尺寸的 Chromium 上分别运行，覆盖跨轮城市推荐、结构化生成、批量记账与预约、对话修订、刷新、导出、旧文字路书整理、原位费用编辑，以及澄清承接与取消、断网／超时／鉴权错误恢复、后台已记账但回复丢失后的重试和分支草稿隔离。替身验收用于发现界面与真实接口、持久化的集成回归，不证明真实模型、搜索或地图服务的可用性。

运行失败时 `frontend/test-results/` 保留截图和 trace；CI 上传7天有效的失败证据。可用 `pnpm exec playwright show-trace <trace.zip路径>` 查看。测试结果目录已忽略，不提交数据库或截图。
