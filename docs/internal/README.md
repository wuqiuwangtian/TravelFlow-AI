# 内部开发与验收材料

本目录保存 TravelFlow AI 的开发维护、接续状态和逐批验收记录，面向维护者，不是当前用户文档。用户安装与使用请看 [README](../../README.md) 与 [使用与配置指南](../user-guide.md)。

内容按日期保留当时的结论和证据，不能替代当前代码与 Git 状态；接手开发前先核对 `git status --short --branch`、`git log` 和最新 CI。

## 入口

| 材料 | 用途 |
| :--- | :--- |
| [MAINTAINING.md](MAINTAINING.md) | 开发维护入口、资料导航与最短启动/验收路径 |
| [PROJECT_STATUS.md](PROJECT_STATUS.md) | 当前版本、已完成能力、验证边界与接续位置 |
| [PRODUCT_ROADMAP.md](PRODUCT_ROADMAP.md) | 有效产品目标、任务顺序与完成条件 |
| [handoff/DEVELOPMENT.md](handoff/DEVELOPMENT.md) | 环境、检查命令、隔离测试服务与 Windows 核对方法 |
| [handoff/VALIDATION.md](handoff/VALIDATION.md) | 验证摘要与临时材料处置 |
| [ordinary-flow.md](ordinary-flow.md) | 普通用户主流程走查、修复与逐批证据 |
| [FRONTEND_UPGRADE_PLAN.md](FRONTEND_UPGRADE_PLAN.md) | 前端升级历史计划与交付顺序 |

## 逐批记录

- `frontend-*.md`：前端升级各批次、主题、工作区、费用、回顾、比较与高级工程的交付记录。
- `frontend-audit/`、`frontend-feedback/`：FE-0 盘点、交互审查与反馈修复证据。
- `ordinary-flow/`：主流程走查截图，保留时间与边界说明。

## 维护约定

- 行为、环境、计划或关键决定变化时，同轮更新对应权威文档；任务状态只在本目录的路线图维护。
- 阶段交付前更新 `PROJECT_STATUS.md` 的接续状态；不另建每轮交接报告。
- 历史材料保留日期和当时边界，不用旧结果证明当前版本；未验证项如实标注。
- 不在文档中记录真实密钥、个人旅行内容或私密路径。
