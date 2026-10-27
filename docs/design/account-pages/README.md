# FE-4 入口与配置页面的 Windows 实测

2026-10-27，Windows 原生 Edge 访问正式 Next.js 构建。截图为浏览器原始视口，未做图片编辑；390px 用于手机布局核对，不等同于手机硬件实测。

| 页面     | 电脑 1440 × 1000                                      | 手机 390 × 844                                 |
| -------- | ----------------------------------------------------- | ---------------------------------------------- |
| 工作台   | [开始旅行与最近旅行](home-desktop-windows.png)        | [紧凑工作台](home-mobile-windows.png)          |
| 我的旅行 | [旅行列表与管理](sessions-desktop-windows.png)        | [长名称与操作区](sessions-mobile-windows.png)  |
| 设置     | [分区与模型配置](settings-desktop-windows.png)        | [模型设置](settings-mobile-windows.png)        |
| 常用偏好 | [编辑区与已保存内容](preferences-desktop-windows.png) | [先编辑再核对](preferences-mobile-windows.png) |

手机另有 [高德设置直达](settings-map-mobile-windows.png)。分区跳转保留未保存的模型名称，偏好编辑与已保存内容分开显示，新旅行的偏好默认不选；两端没有页面脚本错误和横向溢出。

旅行使用临时数据库中的固定样本，配置与偏好使用隔离响应，不包含个人旅行、环境文件或真实 API Key。截图中的服务商及模型名称是界面验收样本。完整范围与剩余任务见 [交付记录](../../frontend-account-pages.md)；专用 QA 服务已关闭。
