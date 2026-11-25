# 消费部分更正交换契约候选 0.1.0-candidate.2（评审候选）

权威仓库：TravelFlow-AI。本目录是阶段三反向交接（**电脑更正已确认的人民币消费，手机更新原记录**）的最小评审候选（`contract_id: "travelflow.expense-patch-candidate"`，`contract_version: "0.1.0-candidate.2"`，`status: "candidate"`，`shared_contract_version: null`），基于桌面 `d24951e` 起草，供桌面与移动双方独立评审。

既有已冻结契约 [ledger-v1](../ledger-v1/SPEC.md)（`0.1.0`）、历史候选 [expense-create-candidate](../expense-create-candidate/SPEC.md)（`0.1.0-candidate.2`）与正式冻结包 [expense-create-v0.1.0](../expense-create-v0.1.0/SPEC.md)（`0.1.0`）保持原字节不变。

## 1. 范围与非目标

本候选仅覆盖**单笔 CNY 消费金额的部分更正**（`expense.patch`）与**独立回执查询**（`receipt.lookup`），起点为手机已通过 `travelflow.expense-create 0.1.0` 收到桌面确认版本的既有消费：

- 规范化编码：复用 `p1-host-intent-c14n-1`（严格 RFC 8259 UTF-8、无重复键、精确整数无浮点舍入、对象键按 UTF-16BE 码元排序、紧凑 UTF-8、≤ 65536 字节）。
  - 独立摘要封装对象：`{"canonical_profile":"p1-host-intent-c14n-1","channel":"<control|receipt-query>","intent":<排序后意图>}`，可复算样例见 `canonical-digests.json`。
- 控制信封：`profile = "p1-host-control-1"`，`kind = "expense.patch"`
- 查询信封：`profile = "p1-host-query-1"`，`kind = "receipt.lookup"`

**明确未覆盖（本候选通过评审前不接生产写入口，也不宣称双向同步完成）**：网络传输与直连、设备配对与撤销、多笔并发队列调度、自动三方合并、删除与恢复（`expense.delete`）、回款（`adjustments`）联动、非 CNY 或未知币种更正、非金额字段（`title`／`notes`／`category`／`paid_on`／`day`／`activity_id`／`receipt_ids`／`budget_item_id`／`settles_budget`）的跨端写入执行。

## 2. 手机本地版本与桌面确认版本的对应关系

手机与桌面各自维护独立的本地修订号，**不得**把手机本地修订号当作桌面版本 ID 比较：

- 手机按 `(dataset_id, travel_id, branch_id)` 持久化以下两项状态：
  1. `local_revision_id`：手机本地账本当前修订号（例如 `m-rev-1`）。
  2. `synced_desktop_revision_id`：手机最近一次已确认的桌面版本 ID（例如由 `expense.create 0.1.0` 确认回执写入的 `576d3d5797a3`）。
- 电脑在 `R1`（即手机已保存的 `synced_desktop_revision_id`）基础上更正该笔消费，生成桌面新版本 `R2` 后，构造 `expense.patch` 命令：
  - `expected_revision_id` = `R1`（桌面此次更正所基于的旧桌面版本）。
  - `source_revision_id` = `R2`（桌面此次更正产生的新桌面版本）。
  - **版本一致性约束（严格校验）**：
    - 金额实际发生改变时，**必须**满足 `source_revision_id != expected_revision_id`；若两者相同，拒绝并返回 `revision_mismatch`（零写入）。
    - 金额未发生改变（无变化更正）时，**必须**满足 `source_revision_id == expected_revision_id`；若两者不同，拒绝并返回 `revision_mismatch`（零写入）。
  - `target` = `{travel_id, branch_id, entity_id}`（指向同一笔已确认消费实体）。
  - `operation` = `{dataset_id, source_device_id, operation_id}`（与 `entity_id` 严格分离）。
- **手机识别与推进规则**：
  - 手机核对 `command.expected_revision_id == synced_desktop_revision_id`（而非与 `local_revision_id` 比较）。
  - 正常更正提交时，手机在单一原子事务内：更新该 `entity_id` 的 `amount_cents`（保留全部未触及字段）、推进本地修订 `local_revision_id`（`m-rev-1 -> m-rev-2`）、将 `synced_desktop_revision_id` 更新为 `command.source_revision_id`（`R1 -> R2`），并写入 `origin_direction = "received"` 的持久回执（不重新入队发回电脑）。
  - 回执同时返回手机新本地版本 `result_revision_id` 与已对齐的 `synced_desktop_revision_id`。

## 3. 复用部分更正规则（沿用 `eee6cda7` 评审结论）

| 规则项 | 本候选行为 |
| --- | --- |
| 省略字段语义 | `patch` 为部分补丁对象；省略的字段（`title`、`notes`、`category`、`paid_on` 等）保持原记录不变，不覆盖为默认值或清空 |
| `amount_cents` | 若存在，必须为精确整数 `1..9999999999`（拒绝 `0`、负数、布尔、小数、字符串、溢出） |
| 仅限 CNY | 目标消费当前 `currency` 必须为 `"CNY"`；非 CNY 拒绝并返回 `unsupported_capability`，零写入 |
| 无变化更正 | `patch: {}` 或 `patch.amount_cents == 当前金额` 时：仍须先通过鉴权、本地待发检查、基线与实体校验；通过后返回 `effect: "no_change"`、`result_revision_id = 当前 local_revision_id`，**不递增本地修订、不改动 `updated_at`**，但持久化操作回执供后续重放与查询 |
| 本切片未开放字段 | `patch` 中出现除 `amount_cents` 以外的合法业务字段（如 `notes`、`paid_on`、`title` 等）时，返回 `unsupported_capability`，零写入，调用方保留完整原意图 |
| 未知或畸形补丁键 | 出现未定义字段名或非法类型时，返回 `invalid_field`／`type`，零写入 |

## 4. 执行顺序与本地待发修改保护

手机接收 `expense.patch` 必须按以下顺序执行：

1. **鉴权与目标作用域检查**：先核对调用者设备、写权限、数据集及显式授权的 `(travel_id, branch_id)`；失败返回 `unauthorized`，不得泄露回执或本地状态。
2. **先查持久回执**：按 `(dataset_id, source_device_id, operation_id)` 查询。
   - 命中且完整规范化意图一致：直接返回原回执（`replayed: true`、`effect`、`result_revision_id`、`synced_desktop_revision_id`），即使 `synced_desktop_revision_id` 已继续推进或本地后续出现新待发，也保持幂等、零新增写入。
   - 命中但意图不同（如 `amount_cents` 从 `2150` 变为 `2151`）：返回 `intent_conflict`，零写入。
3. **能力与字段校验**：校验 `patch` 结构与 `amount_cents` 范围；超出本切片能力返回 `unsupported_capability`，畸形输入返回 `invalid_field`／`type`。
4. **本地待发修改保护（关键）**：检查手机本地 `pending_operations` 中是否存在同一 `(dataset_id, travel_id, branch_id)` 下尚未确认（`delivery_state != 'confirmed'`）的待发操作（尤其针对同一 `entity_id` 或同一分支的未确认本地编辑）。
   - 若存在：**必须拒绝本次远端更正**，返回 `local_pending_conflict`；完整响应为 `{"ok":false,"error":"local_pending_conflict","pending_operation_id":"<阻塞的本地操作 ID>"}`。
   - **保留要求**：手机本地消费原值（含用户本地待发修改）、`pending_operations` 原记录、`local_revision_id` 与 `synced_desktop_revision_id` **全部原样保留**，且**不写入**该远端操作回执（避免污染后续冲突解决后的重试）。
5. **基线核对**：核对 `command.expected_revision_id == synced_desktop_revision_id`。不符返回 `baseline_conflict`（携带当前 `synced_desktop_revision_id`），零写入。
6. **实体存在与币种核对**：核对 `target.entity_id` 存在（否则返回 `entity_not_found`）且 `currency == "CNY"`（否则返回 `unsupported_capability`）。
7. **单事务原子提交**：写入更新后的消费（无变化时跳过记录改写）、推进或保持修订映射、写入 `origin_direction = "received"` 持久回执。

## 5. 包内文件与桌面复跑命令

- `manifest.json`：候选标识、来源提交 `d24951e` 与四份文件 SHA-256 清单。
- `canonical-digests.json`：正常更正、键重排、异金额对照与 `receipt.lookup` 的规范化字节、UTF-8 十六进制与 SHA-256。
- `normative-vectors.json`：10 组场景、21 步规范样例（覆盖正常更正、重复投递、同键异金额、基线过期、手机本地同实体／同分支待发修改逐字段保留、无变化更正、金额变化与桌面版本矛盾反例、能力／范围拒绝、越权目标与独立查询）。
- `result-mapping.json`：桌面参考实现实测映射与移动侧待评审占位（明确标注移动宿主／Android 尚未实测，等待移动评审回执）。

### 桌面复跑命令

```sh
node scripts/check-contracts.mjs --baseline d24951e7e3f7ee4d2f34edced7d3b523093b9981
python3 -m desktop.expense_patch_candidate --contract-bundle contracts/expense-patch-candidate.2
python3 -m unittest desktop.tests.test_expense_patch_candidate -v
```
