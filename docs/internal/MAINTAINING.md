# 开发维护文档

本页面向项目开发者、维护者及参与开发的 AI，统一导航开发资料；用户产品说明、安装和使用方法见 [README](../../README.md)。先核对 `git status --short --branch` 和 `git log -3 --oneline`，再按任务阅读下列资料，无需旧聊天或临时脚本。

| 资料 | 职责与维护位置 |
| --- | --- |
| [项目现状](./PROJECT_STATUS.md#当前接续状态) | 当前版本、已完成能力、验证边界、阻塞与接续位置 |
| [产品路线](./PRODUCT_ROADMAP.md#当前执行任务) | 有效产品目标、任务顺序与完成条件；任务状态只在这里维护 |
| [电脑—手机改进里程碑](MOBILE_CONTINUITY_PLAN.md) | 个人Android＋Windows／WSL本地独立运行、免终端与直接同步的专项计划；框架及连接工具待P0验证 |
| [跨端契约](../../contracts/README.md) | 本仓库维护权威版本；移动仓库按固定Git提交导入快照，独立构建与验证 |
| [移动功能迁移矩阵](MOBILE_CAPABILITIES.md) | 84个当前接口的目标执行层与迁移边界，CI检查声明覆盖，不作为已移植证明 |
| [D0状态恢复](handoff/DEVELOPMENT.md#d0异常退出与运行状态恢复)、[统一入口](handoff/DEVELOPMENT.md#d0统一环境选择与原生路径)、[可视管理](handoff/DEVELOPMENT.md#d0可视运行管理原型)、[应用验收](handoff/DEVELOPMENT.md#d0实际应用准备与验收) | 浏览器管理原型、源码入口、模式隔离准备与显式数据选择；运行核心另有纯探针 |
| [开发与隔离验收](./handoff/DEVELOPMENT.md) | 环境、检查命令、测试服务与 Windows 核对方法 |
| [项目约定](../../AGENTS.md) | AI 开工与协作规则；前端另读其目录约定 |
| [设计约束](../../DESIGN_BRIEF.md)、[共享规范](../design/SYSTEM.md) | 已确认体验与实现规范；历史样稿不是待重新选型任务 |
| [手机端说明](../design/MOBILE.md) | 面向所有者与维护者的实际页面、设计归属、源码入口与待评审取舍；不作为手机设计已获认可的证明 |
| [主流程验收记录](./ordinary-flow.md)、[更新日志](../../CHANGELOG.md) | 按日期保留的问题、修复与证据，不承担当前任务清单 |

独立Android开发入口为 [TravelFlow-Mobile](https://github.com/wuqiuwangtian/TravelFlow-Mobile/blob/main/docs/internal/MAINTAINING.md)；Android工具链与实验不再放入本仓库。现有网页手机适配继续维护，跨端总目标仍由专项计划管理。

最短启动路径见 [README 的手动启动说明](../../README.md#手动启动开发调试)：后端 `uv sync` 后运行 `uv run uvicorn app.main:app --reload --port 8000`，前端 `pnpm install` 后运行 `pnpm dev -p 3100`（分别在对应目录执行）。配置步骤按 README 说明，勿覆盖已有 `.env`。最短隔离验收与预期结果见[开发说明](./handoff/DEVELOPMENT.md#接手后的最短验收路径)。

行为、环境、任务或关键决定变化时，同轮更新对应权威文档及受影响链接；阶段结束更新现状中的接续信息，不另建每轮交接报告。历史结果须带日期和版本，不能作为当前版本已验证的证明。
