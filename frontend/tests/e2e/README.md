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

当前共42个场景（21项流程分别在桌面和手机尺寸的 Chromium 运行），覆盖跨轮城市推荐、结构化生成、批量记账与预约、对话修订、刷新、导出、旧文字路书整理、原位费用编辑，以及澄清承接与取消、地点候选与区域选择、导航和预算同步、断网／超时／鉴权错误恢复、后台已记账但回复丢失后的重试和分支草稿隔离；失败记账后先使用快捷建议、快捷建议再次断流及刷新后仍保留原请求号。同日同名同额消费覆盖刷新后选择、连续多项澄清、关联回款、删除保护、草稿保留、预算与导出同步。两天联动覆盖夜间延误预览、刷新后一次应用、固定预约冲突拒绝、条件修改、可选项目精简展示、取消不留部分改动、预算／导出同步及应用回复丢失后的重试。景点搬移覆盖时间轴入口、移入移出、同名选择、刷新取消、双日期展示、预算与账本编辑保留付款日、未知付款日保留、预约／未知交通保护、历史版本及导出同步和应用断流重试。替身验收用于发现界面与真实接口、持久化的集成回归，不证明真实模型、搜索或地图服务的可用性。

运行失败时 `frontend/test-results/` 保留截图和 trace；CI 上传7天有效的失败证据。可用 `pnpm exec playwright show-trace <trace.zip路径>` 查看。测试结果目录已忽略，不提交数据库或截图。
