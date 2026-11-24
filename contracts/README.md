# 跨客户端契约

本仓库维护权威契约，移动应用在 [TravelFlow-Mobile](https://github.com/wuqiuwangtian/TravelFlow-Mobile) 独立开发。两端独立安装、构建和发布；不能用源码路径互相导入，也不直接同步活跃SQLite文件。

当前维护的契约目录：

| 目录 | 契约 ID 与版本 | 状态与范围 | 跨端验收状态 |
| --- | --- | --- | --- |
| [ledger-v1](ledger-v1/SPEC.md) | `travelflow.ledger-projection` `0.1.0` | 实验，只读账目汇总（14 样例），不可原地修改 | 桌面与移动已验收 |
| [expense-create-candidate](expense-create-candidate/SPEC.md) | `travelflow.expense-create-candidate` `0.1.0-candidate.2` | 历史评审候选（`shared_contract_version: null`），单笔 CNY 新增与回执查询，保留原字节 | 桌面 `94ec1fb` 与移动 `8ba3365` 已验收 |
| [expense-create-v0.1.0](expense-create-v0.1.0/SPEC.md) | `travelflow.expense-create` `0.1.0` | 已冻结（`shared_contract_version: 0.1.0`），单笔 CNY 新增与回执查询，来源 `94ec1fb` | 桌面 `d916697` 已验，移动 [250acac](https://github.com/wuqiuwangtian/TravelFlow-Mobile/commit/250acac3ff4cbe5ef1879ab2a6e096dd0279ff75) 已独立导入并校验正式包 |

已发布或已冻结目录不可原地修改；新规则建立新版本，桌面保留旧版本／检查方式，移动仓库通过固定 Git 提交显式导入并核对摘要。一方升级不等于另一方已兼容，联合验收通过后才能更新支持声明。Python 生成器当前核对 `ledger-v1`；新增消费 `0.1.0` 与候选包由 `desktop/expense_vectors.py` 与 `scripts/check-contracts.mjs` 核对。

根目录运行 `node scripts/check-contracts.mjs` 验证清单与摘要，`uv run --project backend python backend/scripts/export_ledger_vectors.py --check` 核对当前Python语义。移动仓库独立运行其领域检查，不在本仓库安装Android工具链。两端维护者共同审阅规则变化，现阶段不额外发布共享npm包或创建第三个仓库。


版本保护分两层：默认命令只验证当前文件与manifest摘要；`node scripts/check-contracts.mjs --baseline <完整40位Git提交>`还会比较基线中已存在版本的完整文件集合和字节，禁止删除、添加或覆盖既有版本内的文件，即便同时重算摘要也会失败。新版本目录和契约入口文档可正常新增／更新。基线必须在本地Git历史中存在，缺失则失败，不静默跳过。

CI的push／PR分别使用推送前提交／目标分支提交作为基线，并拉取所需历史；手动workflow_dispatch使用当前提交重验，初始仓库推送没有旧版本可比较时仅检查摘要。`node --test scripts/check-contracts.test.mjs`覆盖重算摘要覆盖旧版本、增添旧版本文件、缺失基线与正常新增版本等回归。移动端无需修改快照或立即升级，现有0.1.0内容保持不变。

P1写入语义及跨仓评审记录见[P1跨仓数据评审](../docs/internal/P1_CROSS_REPO_REVIEW.md)。阶段二单笔 CNY 新增消费与回执查询的正式冻结包为 [expense-create-v0.1.0](expense-create-v0.1.0/SPEC.md)（`0.1.0`，由第二候选 `94ec1fb` 冻结），与历史候选 [expense-create-candidate](expense-create-candidate/SPEC.md) 及 [ledger-v1](ledger-v1/SPEC.md) 并存且保留旧包原字节；移动侧已验第二候选（`8ba3365`），正式 `0.1.0` 增量导入待验，不代表整端同步或网络传输已完成。
