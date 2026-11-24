# 新增消费交换契约候选 0.1.0-candidate.1（候选，未冻结）

权威仓库：TravelFlow-AI。本目录是阶段二评审用自包含候选包（`status: "candidate"`，`shared_contract_version: null`），供桌面复核与移动仓库按固定 Git 提交显式导入核对；**不是**已冻结的正式写入契约，也不影响既有 [ledger-v1 0.1.0](../ledger-v1/SPEC.md)。

## 1. 范围与非目标

本候选仅覆盖两端已验证的**单笔 CNY 新增消费**（`expense.create`）与**独立回执查询**（`receipt.lookup`）：

- 规范化编码：`p1-host-intent-c14n-1`（严格 RFC 8259 UTF-8、无重复键、精确整数无浮点舍入、对象键按 UTF-16BE 码元排序、紧凑 UTF-8、≤ 65536 字节）。
  - **独立摘要封装格式**：摘要必须对紧凑 UTF-8 封装对象 `{"canonical_profile":"p1-host-intent-c14n-1","channel":"<control|receipt-query>","intent":<排序后意图>}` 计算 SHA-256，不能只对裸 `payload` 或未封装的 `command` 哈希。具体可独立复算字节、十六进制与固定预期摘要见 `canonical-digests.json`（含键重排同摘要与 `allow_shared_receipts` 翻转异摘要对照）。
- 控制信封：`profile = "p1-host-control-1"`，`kind = "expense.create"`
- 查询信封：`profile = "p1-host-query-1"`，`kind = "receipt.lookup"`

**明确未覆盖（不因本候选通过而宣称整端同步可用）**：网络传输与直连协议、设备配对与撤销、协议版本协商、跨设备持久确认协议、多笔队列调度、`expense.patch`／`expense.delete`／`expense.restore`、回款（adjustments）、预约（bookings）、执行进度（progress）、票据与照片附件绑定、预算关联、显式 `paid_on` 与非 CNY 币种的写入执行、个人主库迁移。

## 2. 身份、操作键与实体 ID

- **操作键（operation key）**：`(dataset_id, source_device_id, operation_id)`，用于幂等去重、持久回执索引与待发送恢复。
- **实体 ID（entity_id）**：`target.entity_id`，标识该分支下的具体消费实体，与 `operation_id` 严格分离。更换 `operation_id` 但沿用已存在的 `entity_id` 必须按 `entity_exists` 拒绝，不得当作旧操作重试。
- **调用者身份与权限**：接收端必须独立核对调用者设备身份、数据集与写／查权限，不得仅信任载荷内声明的 `source_device_id`。鉴权与目标作用域检查必须位于回执查询与业务读取之前；未授权或错误目标响应不得携带 `receipt` 或 `result_revision_id`。

## 3. 支持字段、范围与能力阻断

| 字段 | 本候选支持范围 | 超出范围行为 |
| --- | --- | --- |
| `title` | 1..100 标量，非空白，无 C0/C1 控制字符 | `invalid_field`／`type`，零写入 |
| `amount_cents` | 精确整数 `1..9999999999`（拒绝 `0`、布尔、小数、溢出） | `integer_range`／`type`，零写入 |
| `currency` | 仅 `"CNY"` | `unsupported_capability`（或移动端原错 `currency`），零写入 |
| `paid_on` | 仅 `null`（未知付款日） | 桌面／Android 候选 `unsupported_capability`，零写入；保留原输入 |
| `category` | `transport`／`lodging`／`meal`／`ticket`／`shopping`／`other` | `invalid_field`／`type`，零写入 |
| `day` | 仅 `null` | `unsupported_capability`，零写入；保留原输入 |
| `activity_id` | 仅 `null` | `unsupported_capability`，零写入；保留原输入 |
| `notes` | 0..1000 标量，仅允许 `\t`/`\n`/`\r` 控制符 | `invalid_field`，零写入 |
| `receipt_ids` | 仅 `[]` | `unsupported_capability`，零写入；保留原输入 |
| `allow_shared_receipts` | 布尔值（参与完整原意图比较） | `type`，零写入 |
| `budget_item_id` | 仅 `null` | `unsupported_capability`，零写入；保留原输入 |
| `settles_budget` | 仅 `false` | `invalid_field`／`type`，零写入 |
| 单分支消费上限 | `2000` 笔（含第 2000 笔；第 2001 笔拒绝） | `expense_limit_exceeded`（移动 `expense_count`），零写入 |

**能力阻断与原意图留存**：遇到合法但对端当前不支持的字段（非空 `paid_on`、`day`、`activity_id`、`receipt_ids`、`budget_item_id` 或非 CNY 币种）时，接收端零写入；调用方必须在本地持久保留完整原操作键、原 `expected_revision_id` 与原意图，不得剥离字段、改基线、换 `operation_id` 或标记已同步。

## 4. 执行顺序、原子提交与回执确认规则

1. **鉴权与目标作用域检查**：先验调用者身份、权限及目标 `(dataset_id, travel_id, branch_id)`；失败直接拒绝且不泄露回执。
2. **先查持久回执**：按 `(dataset_id, source_device_id, operation_id)` 查询。
   - 命中且完整规范化意图完全一致（含 `target`、`expected_revision_id`、`allow_shared_receipts` 及全部业务字段）：直接返回原 `effect` 与原 `result_revision_id`（即使分支头已推进或分支已满 2000 笔），零新增写入。
   - 命中但意图不一致（如金额 `8635→8636` 或 `allow_shared_receipts` `false→true`）：返回 `intent_conflict`，保留原账与原回执。
3. **新操作校验**：校验声明能力、字段合法性、`expected_revision_id == current_head`（不符返 `baseline_conflict`）、`entity_id` 未存在（已存在返 `entity_exists`）及单分支 `< 2000`。
4. **单事务原子提交**：在同一事务内原子写入新版本、更新分支头、写入消费、写入含完整规范化意图与摘要的持久回执，以及本地待发记录（outbox）。提交前中断零写入；提交后丢回包可通过原键查询或原键重试恢复，最终仅入账一次。
5. **最小成功回执确认规则**：若对端 `receipt.lookup` 的最小投影仅返回 `{effect, result_revision_id}` 而不含 `intent_sha256` 或完整原意图，**仅凭同操作键查到已提交不足以确认原意图**。发送方必须满足以下之一才可标记 `confirmed`：
   - 对端回执携带与本地一致的 `canonical_profile`、`operation` 及 `intent_sha256`（或等价完整原意图证据）；或
   - 以原操作键与完整原意图重放 `expense.create`，由对端核对完整意图一致后返回原结果版本（若对端已提交同键异意图，重放会返回 `intent_conflict`，避免误确认）。
6. **写锁与不确定查询**：查询遭遇写锁返回 `unknown / storage_busy`，稳定读取无记录返回 `not_found_at_settled_read`；两者均须保留待核实原意图，迟到的旧查询结果不得覆盖已确认状态。

## 5. 包内文件与双端导入复跑

- `manifest.json`：候选标识、范围与全部文件 SHA-256 清单（不含符号链接或未登记文件）。
- `canonical-digests.json`：固定午餐命令（原始与键重排）、`allow_shared_receipts=true` 对照及 `receipt.lookup` 查询的完整封装规范化字节、UTF-8 十六进制、SHA-256 与同／异关系，供双端独立复算。
- `normative-vectors.json`：与实现解耦的 10 组场景、36 步规范样例（引用固定午餐基础输入与变量绑定 `R1`/`R2`）。
- `desktop-assertions.json`：桌面隔离执行器的逐例原始返回断言（与规范样例分离）。
- `result-mapping.json`：分别记录桌面、移动宿主（`9dec892` 实测）与 Android（`9dec892`／`b870ad5` 实测）原始返回到共同 `business_outcome` 的映射、已知偏差及确认规则。

### 桌面复跑命令

```sh
node scripts/check-contracts.mjs --baseline <40位已发布基线提交>
node --test scripts/check-contracts.test.mjs
python3 -m desktop.expense_vectors --contract-bundle contracts/expense-create-candidate
python3 -m unittest desktop.tests.test_expense_vectors desktop.tests.test_expense_executor desktop.tests.test_expense_pending -v
```

### 移动固定提交导入说明

1. 从桌面固定提交（不引用相邻源码目录）提取整个 `contracts/expense-create-candidate/` 目录到移动仓独立快照位置。
2. 逐文件核对 `manifest.json` 中的 SHA-256，确认 `contract_version` 与 `shared_contract_version: null` 未漂移。
3. 使用 `normative-vectors.json` 与 `result-mapping.json` 在移动宿主与 Android 独立执行，记录各场景原始返回、持久状态差量及差异项；不依赖 `desktop-assertions.json` 的桌面专用错误名。
