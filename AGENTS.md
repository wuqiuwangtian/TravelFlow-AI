# 项目开发约定

- 开发维护入口是 [MAINTAINING.md](./docs/internal/MAINTAINING.md)。新会话先核对实际 Git 状态，再读 [PROJECT_STATUS.md](./docs/internal/PROJECT_STATUS.md#当前接续状态) 与 [PRODUCT_ROADMAP.md](./docs/internal/PRODUCT_ROADMAP.md#当前执行任务)，按用户最新任务工作。
- 当前能力与验证边界只在现状页维护，任务顺序与完成条件只在路线图维护；专题记录保存历史证据。行为、环境、计划或关键决定变化时同步更新受影响文档，阶段交付前更新接续状态，不另建重复快照。
- 不同 AI 接手或并行工作前，确认分支、未提交改动归属及负责范围；保留已有工作，不覆盖其他参与者修改。并行开发优先用独立分支／worktree，集成前审阅差异并运行相关检查；文档中的历史版本不能替代当前 Git 核对。
- 前端改动遵守 [frontend/AGENTS.md](./frontend/AGENTS.md) 及共享设计规范；不要把历史设计样稿当作未完成的重新选型任务。
- 开发、隔离验收命令与 Windows 材料见 [开发说明](./docs/internal/handoff/DEVELOPMENT.md)。按变更运行必要检查，不因接手重复全部历史阶段；实现、验证、用户验收、推送与部署分别记录。
- 配置、密钥和个人数据保留在现有忽略位置；文档只记录位置、用途与验证边界，不记录实际密钥或个人旅行内容。
- 公开贡献、安全与支持分别见 [CONTRIBUTING](./CONTRIBUTING.md)、[SECURITY](./SECURITY.md)、[SUPPORT](./SUPPORT.md)；历史验收材料归档在 [docs/internal](./docs/internal/README.md)。

- 仓库边界：本仓库负责电脑端与现有网页、Python领域规则及`contracts/`权威版本；独立Android源码与验证在 [TravelFlow-Mobile](https://github.com/wuqiuwangtian/TravelFlow-Mobile)。不把相邻仓库路径或其main加入构建；变更跨端语义时发布新契约版本，并记录双方是否已验收，不因桌面CI通过就宣称手机兼容。
