# 跨客户端契约

本仓库维护权威契约，移动应用在 [TravelFlow-Mobile](https://github.com/wuqiuwangtian/TravelFlow-Mobile) 独立开发。两端独立安装、构建和发布；不能用源码路径互相导入，也不直接同步活跃SQLite文件。

当前只有 [账目只读投影0.1.0](ledger-v1/SPEC.md)，不是完整业务或同步协议。已发布目录不可原地修改；新规则建立新版本，桌面保留旧版本／检查方式，移动仓库通过固定Git提交显式导入并核对摘要。一方升级不等于另一方已兼容，联合验收通过后才能更新支持声明。Python生成器当前核对ledger-v1；新增版本时需明确其对应实现和回归范围。

根目录运行 `node scripts/check-contracts.mjs` 验证清单与摘要，`uv run --project backend python backend/scripts/export_ledger_vectors.py --check` 核对当前Python语义。移动仓库独立运行其领域检查，不在本仓库安装Android工具链。两端维护者共同审阅规则变化，现阶段不额外发布共享npm包或创建第三个仓库。


版本保护分两层：默认命令只验证当前文件与manifest摘要；`node scripts/check-contracts.mjs --baseline <完整40位Git提交>`还会比较基线中已存在版本的完整文件集合和字节，禁止删除、添加或覆盖既有版本内的文件，即便同时重算摘要也会失败。新版本目录和契约入口文档可正常新增／更新。基线必须在本地Git历史中存在，缺失则失败，不静默跳过。

CI的push／PR分别使用推送前提交／目标分支提交作为基线，并拉取所需历史；手动workflow_dispatch使用当前提交重验，初始仓库推送没有旧版本可比较时仅检查摘要。`node --test scripts/check-contracts.test.mjs`覆盖重算摘要覆盖旧版本、增添旧版本文件、缺失基线与正常新增版本等回归。移动端无需修改快照或立即升级，现有0.1.0内容保持不变。
