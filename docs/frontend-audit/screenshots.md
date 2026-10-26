# 截图与观测索引

[返回盘点报告](README.md)。基线应用提交 `640f150`，2026-10-26；桌面 1440×1000，手机 390×844。全为隔离夹具，不含个人旅行及密钥。每项取视口截图，长页不代表全页截图；控制列表包含已布局但不一定在当前视口内的控件。

## 11 类页面基线

| 页面 | 桌面 | 手机 |
| --- | --- | --- |
| 工作台 | [截图](desktop/home.png) · [观测](desktop/home.json) | [截图](mobile/home.png) · [观测](mobile/home.json) |
| 会话管理 | [截图](desktop/sessions.png) · [观测](desktop/sessions.json) | [截图](mobile/sessions.png) · [观测](mobile/sessions.json) |
| 对话与行程 | [截图](desktop/chat.png) · [观测](desktop/chat.json) | [截图](mobile/chat.png) · [观测](mobile/chat.json) |
| 高级工程 | [截图](desktop/workflow.png) · [观测](desktop/workflow.json) | [截图](mobile/workflow.png) · [观测](mobile/workflow.json) |
| 设置 | [截图](desktop/settings.png) · [观测](desktop/settings.json) | [截图](mobile/settings.png) · [观测](mobile/settings.json) |
| 常用偏好 | [截图](desktop/preferences.png) · [观测](desktop/preferences.json) | [截图](mobile/preferences.png) · [观测](mobile/preferences.json) |
| 旅行生涯 | [截图](desktop/career.png) · [观测](desktop/career.json) | [截图](mobile/career.png) · [观测](mobile/career.json) |
| 回顾列表 | [截图](desktop/recaps.png) · [观测](desktop/recaps.json) | [截图](mobile/recaps.png) · [观测](mobile/recaps.json) |
| 只读回顾 | [截图](desktop/recap-detail.png) · [观测](desktop/recap-detail.json) | [截图](mobile/recap-detail.png) · [观测](mobile/recap-detail.json) |
| 分支对比 | [截图](desktop/compare.png) · [观测](desktop/compare.json) | [截图](mobile/compare.png) · [观测](mobile/compare.json) |
| 帮助 | [截图](desktop/help.png) · [观测](desktop/help.json) | [截图](mobile/help.png) · [观测](mobile/help.json) |

## 其他状态与操作取证

`empty` 为空数据，`failed` 为注入的 503，`pending` 为响应暂未返回；`home-keyboard` 的 JSON 记录入口 DOM 语义，`studio-copy` 记录反馈相对视口的位置。`home-configured`／`home-unconfigured` 仅切换隔离配置响应，不测试真实服务。

| 状态／探测 | 桌面 | 手机 |
| --- | --- | --- |
| archive-pending | [截图](desktop/archive-pending.png) · [观测](desktop/archive-pending.json) | [截图](mobile/archive-pending.png) · [观测](mobile/archive-pending.json) |
| budget-failed | [截图](desktop/budget-failed.png) · [观测](desktop/budget-failed.json) | [截图](mobile/budget-failed.png) · [观测](mobile/budget-failed.json) |
| budget | [截图](desktop/budget.png) · [观测](desktop/budget.json) | [截图](mobile/budget.png) · [观测](mobile/budget.json) |
| career-empty | [截图](desktop/career-empty.png) · [观测](desktop/career-empty.json) | [截图](mobile/career-empty.png) · [观测](mobile/career-empty.json) |
| career-failed | [截图](desktop/career-failed.png) · [观测](desktop/career-failed.json) | [截图](mobile/career-failed.png) · [观测](mobile/career-failed.json) |
| chat-failed | [截图](desktop/chat-failed.png) · [观测](desktop/chat-failed.json) | [截图](mobile/chat-failed.png) · [观测](mobile/chat-failed.json) |
| compare-empty | [截图](desktop/compare-empty.png) · [观测](desktop/compare-empty.json) | [截图](mobile/compare-empty.png) · [观测](mobile/compare-empty.json) |
| compare-failed | [截图](desktop/compare-failed.png) · [观测](desktop/compare-failed.json) | [截图](mobile/compare-failed.png) · [观测](mobile/compare-failed.json) |
| home-configured | [截图](desktop/home-configured.png) · [观测](desktop/home-configured.json) | [截图](mobile/home-configured.png) · [观测](mobile/home-configured.json) |
| home-empty | [截图](desktop/home-empty.png) · [观测](desktop/home-empty.json) | [截图](mobile/home-empty.png) · [观测](mobile/home-empty.json) |
| home-failed | [截图](desktop/home-failed.png) · [观测](desktop/home-failed.json) | [截图](mobile/home-failed.png) · [观测](mobile/home-failed.json) |
| home-keyboard | [截图](desktop/home-keyboard.png) · [观测](desktop/home-keyboard.json) | [截图](mobile/home-keyboard.png) · [观测](mobile/home-keyboard.json) |
| home-unconfigured | [截图](desktop/home-unconfigured.png) · [观测](desktop/home-unconfigured.json) | [截图](mobile/home-unconfigured.png) · [观测](mobile/home-unconfigured.json) |
| preferences-failed | [截图](desktop/preferences-failed.png) · [观测](desktop/preferences-failed.json) | [截图](mobile/preferences-failed.png) · [观测](mobile/preferences-failed.json) |
| preferences-pending | [截图](desktop/preferences-pending.png) · [观测](desktop/preferences-pending.json) | [截图](mobile/preferences-pending.png) · [观测](mobile/preferences-pending.json) |
| preferences-save-failed | [截图](desktop/preferences-save-failed.png) · [观测](desktop/preferences-save-failed.json) | [截图](mobile/preferences-save-failed.png) · [观测](mobile/preferences-save-failed.json) |
| recap-detail-failed | [截图](desktop/recap-detail-failed.png) · [观测](desktop/recap-detail-failed.json) | [截图](mobile/recap-detail-failed.png) · [观测](mobile/recap-detail-failed.json) |
| recaps-empty | [截图](desktop/recaps-empty.png) · [观测](desktop/recaps-empty.json) | [截图](mobile/recaps-empty.png) · [观测](mobile/recaps-empty.json) |
| recaps-failed | [截图](desktop/recaps-failed.png) · [观测](desktop/recaps-failed.json) | [截图](mobile/recaps-failed.png) · [观测](mobile/recaps-failed.json) |
| rename-failed | [截图](desktop/rename-failed.png) · [观测](desktop/rename-failed.json) | [截图](mobile/rename-failed.png) · [观测](mobile/rename-failed.json) |
| rename-pending | [截图](desktop/rename-pending.png) · [观测](desktop/rename-pending.json) | [截图](mobile/rename-pending.png) · [观测](mobile/rename-pending.json) |
| sessions-empty | [截图](desktop/sessions-empty.png) · [观测](desktop/sessions-empty.json) | [截图](mobile/sessions-empty.png) · [观测](mobile/sessions-empty.json) |
| sessions-failed | [截图](desktop/sessions-failed.png) · [观测](desktop/sessions-failed.json) | [截图](mobile/sessions-failed.png) · [观测](mobile/sessions-failed.json) |
| settings-failed | [截图](desktop/settings-failed.png) · [观测](desktop/settings-failed.json) | [截图](mobile/settings-failed.png) · [观测](mobile/settings-failed.json) |
| studio-copy | [截图](desktop/studio-copy.png) · [观测](desktop/studio-copy.json) | [截图](mobile/studio-copy.png) · [观测](mobile/studio-copy.json) |
| studio-run-pending | [截图](desktop/studio-run-pending.png) · [观测](desktop/studio-run-pending.json) | [截图](mobile/studio-run-pending.png) · [观测](mobile/studio-run-pending.json) |
| workflow-failed | [截图](desktop/workflow-failed.png) · [观测](desktop/workflow-failed.json) | [截图](mobile/workflow-failed.png) · [观测](mobile/workflow-failed.json) |

合计：每种视口 38 张，共 76 张。另有 `chat-actions.json` 供核对运行时操作文案，以及 `page-errors.json` 记录 11 路由基线的未处理页面异常（本次均为空）。JSON 未包含表单密钥值；截图中的金额、旅行标题与偏好均为测试夹具。
