# 普通主流程：Windows 原始核对截图

2026-10-28，Windows 原生 Edge、1440×1000／390×844 视口、最终生产构建。旅行为临时数据库中的隔离样本，不含个人数据或真实密钥。

| 场景                     | 桌面                                              | 手机                                             |
| ------------------------ | ------------------------------------------------- | ------------------------------------------------ |
| 对话历史失败与原位重试   | [视口原图](history-error-desktop-windows.png)     | [视口原图](history-error-mobile-windows.png)     |
| 重试后恢复候选并保留草稿 | [视口原图](history-recovered-desktop-windows.png) | [视口原图](history-recovered-mobile-windows.png) |
| 短选择后进入正式路线     | [视口原图](selected-route-desktop-windows.png)    | [视口原图](selected-route-mobile-windows.png)    |
| 同一旅程的回顾与实际消费 | [原图](recap-desktop-windows.png)                 | [原图](recap-mobile-windows.png)                 |

失败只注入对话读取，其余选城、规划、实付、回顾与导出使用真实本地接口及固定模型替身。检查依据实际 `documentElement.clientWidth`，手机视口不代表硬件实测。完整结果及边界见[交付记录](../ordinary-flow.md)。
