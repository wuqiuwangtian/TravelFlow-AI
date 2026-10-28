# 比较与指南：Windows 原始核对截图

2026-10-28，Windows 原生 Edge、1440×1000／390×844 视口，最终生产构建。临时数据库中的旅行均为隔离验收样本；不读取个人记录，不使用真实服务密钥。失败注入只针对一个方案的读取，其余内容与主方案切换走本地测试接口。

| 页面与状态             | 桌面                                        | 手机                                           |
| ---------------------- | ------------------------------------------- | ---------------------------------------------- |
| 比较，一列失败其余可读 | [原图](compare-desktop-windows.png)         | [原图](compare-mobile-windows.png)             |
| 单列失败处就近重试     | 同上                                        | [视口原图](compare-retry-mobile-windows.png)   |
| 重试完成与设为当前回执 | [原图](compare-success-desktop-windows.png) | [视口原图](compare-success-mobile-windows.png) |
| 指南与旅行阶段入口     | [原图](help-desktop-windows.png)            | [原图](help-mobile-windows.png)                |
| 键盘展开常见问题       | [原图](help-questions-desktop-windows.png)  | [视口原图](help-questions-mobile-windows.png)  |

手机整页截图用来核对内容顺序，局部视口截图用于核对跳转和反馈。检查使用实际 `documentElement.clientWidth` 判断横向溢出；两种视口不代表已经在手机硬件实测。完整覆盖与限制见[交付记录](../../frontend-reference-pages.md)。
