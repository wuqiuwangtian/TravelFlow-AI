# FE-3 正式工作区的 Windows 截图

2026-10-27，使用 Windows Edge 与 Playwright 对正式 Next.js 生产构建进行复核，视口截图保留浏览器原始输出，未做图片编辑。

- [电脑 1440 × 1000](desktop-windows.png)：D3 对话主导，右侧为同一旅行的行程与操作。
- [手机 390 × 844](mobile-windows.png)：M1 当前站／今日路线优先，底部直达入口常驻。

页面通过 `backend/tests/browser_server.py` 创建的独立临时数据库提供数据，使用 `test/moves` 样本、固定模型与地图替身；没有用户旅行、环境文件或真实 API Key。验证没有发起真实模型或第三方图片请求。专用 QA 服务在截图与草稿复核结束后关闭。

截图说明实际界面层级与视口，不替代日期、费用、版本、故障恢复与图片比例的业务检查。完整覆盖与剩余工作见[工作区交付记录](../../frontend-workspace.md)。
