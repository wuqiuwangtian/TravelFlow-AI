# 同网前台配对原型说明（阶段四 · Android 可复用握手）

本原型实现位于 [lan_pairing.py](../../../desktop/lan_pairing.py)，回归测试位于 [test_lan_pairing.py](../../../desktop/tests/test_lan_pairing.py)。原型仅在前台显式启停、仅暴露虚构旅行数据（来自 `desktop/tests/fixtures/d1/expected.json`），不打开个人数据库或 `.env`，不安装后台常驻服务。

## 1. 认证与加密机制

- **传输层加密**：标准 OpenSSL ECDSA P-256 自签名 X.509 证书 + TLS 1.2+/1.3（`ssl.TLSVersion.TLSv1_2` 及以上），服务端证书包含 `IP:127.0.0.1` 与绑定地址的 SAN。
- **证书指纹固定（Pinning）与初始可信传递**：
  - 当前原型用 serve --open-pairing 的前台 JSON 展示地址、端口和一次性配对信息，供手机从桌面屏幕核对；二维码属于后续界面。配对信息**必须带外（out-of-band）包含** `host`、`port`、`cert_sha256`（服务端证书 DER 字节的 64 位小写十六进制 SHA-256）、`pairing_session_id` 与 6 位 `pairing_code`。
  - **Android 必须从桌面屏幕／二维码等可信带外通道取得初始 `cert_sha256`**，并在每次 TLS 握手后核对对端证书 DER SHA-256 与该固定指纹完全一致；**禁止**在未固定指纹前先明文或免校验调用 `GET /health` 来“自动信任”局域网返回的地址或证书指纹（否则同网中间人可替换证书与指纹）。
- **首次配对双方确认（SAS）与延迟激活**：
  1. 桌面在前台生成一次性短时效配对会话（`pairing_session_id` + 6 位 `pairing_code`，默认 120 秒过期）。
  2. 手机在固定证书 TLS 上提交 `POST /pair/request`，双方独立按 `HMAC-SHA256(key=pairing_code, msg=cert_sha256|pairing_session_id|device_id|client_nonce|server_nonce)` 取前 4 字节大端无符号整数模 `1000000` 得到 6 位核对码 `sas_code`（零填充）。
  3. 桌面用户核对设备名与 6 位 `sas_code` 后，通过**仅监听 `127.0.0.1` 且受 `0600` 私有 `X-Control-Token` 保护的控制面**显式批准（LAN HTTPS 监听端口不暴露任何 `/control/*` 路径）。批准后请求进入**待领取（`approved_pending_claim`）**状态，**此时不写入也不覆盖 `paired_devices` 中的已有凭据**（同设备重配若在领取前中断，旧凭据完整保留）。
  4. 手机在配对有效期内提交 `POST /pair/complete` 携带同一 `sas_code` 与 `client_nonce`，服务端校验未过期、未被撤销后，**此刻才原子激活** 256 位随机 `device_token`（仅保存 `SHA-256` 哈希至 `pairing.sqlite`）并一次性返回给手机（`POST /pair/complete` 不可重放）。若请求超时过期、被撤销或桌面重启，未完成请求即刻失效。
  5. 若服务端已处理领取、手机却未收到响应，旧凭据此时已被替换。手机重新发起一次配对并由桌面再次批准。
- **重连与撤销**：
  - 已配对设备在固定证书 TLS 上携带 `Authorization: Bearer <device_token>` 与 `X-Device-Id: <device_id>` 访问 `GET /trip/fictional`；服务端显式停止并重开后仍可重连。
  - 桌面撤销设备后，`paired_devices.status` 置为 `revoked`，访问立即返回 `403 device_revoked`（零数据返回）且重启后持续生效。未配对或错误 token 返回 `401 unauthorized`。

## 2. Android 可复用最小请求样例

### 2.1 健康检查（不含旅行数据）

```http
GET /health HTTP/1.1
Host: <desktop_ip>:<port>
```

```json
{
  "ok": true,
  "status": "ok",
  "protocol": "travelflow-lan-pairing/0-draft",
  "cert_sha256": "<64-hex-der-sha256>"
}
```

### 2.2 首次配对步骤 1：手机发起请求（尚未授权读数据）

```http
POST /pair/request HTTP/1.1
Content-Type: application/json

{
  "pairing_session_id": "<session_id>",
  "pairing_code": "123456",
  "device_id": "android-pixel-1",
  "device_name": "Pixel 9",
  "client_nonce": "<32-hex-random>"
}
```

响应（`200 OK`，此时 `GET /trip/fictional` 仍返回 `401 unauthorized`）：

```json
{
  "ok": true,
  "request_id": "<24-hex>",
  "server_nonce": "<32-hex>",
  "sas_code": "482910",
  "status": "awaiting_desktop_confirm"
}
```

### 2.3 首次配对步骤 2：桌面显式批准后，手机一次性领取凭据

```http
POST /pair/complete HTTP/1.1
Content-Type: application/json

{
  "request_id": "<24-hex>",
  "device_id": "android-pixel-1",
  "client_nonce": "<32-hex-random>",
  "sas_code": "482910"
}
```

- 桌面尚未批准时返回 `409 {"ok":false,"error":"awaiting_desktop_confirm"}`。
- 桌面批准后返回 `200 OK`（仅此一次，重放返回 `409 token_already_delivered`）：

```json
{
  "ok": true,
  "status": "paired",
  "device_id": "android-pixel-1",
  "device_token": "<base64url-256bit>",
  "cert_sha256": "<64-hex-der-sha256>"
}
```

### 2.4 已配对重连读取虚构旅行

```http
GET /trip/fictional HTTP/1.1
Authorization: Bearer <device_token>
X-Device-Id: android-pixel-1
```

- 已配对且活跃：`200 OK` 返回虚构旅行摘要（`fictional_only: true`、`travel_id: "92ba6a148a17"`、`sample_expense_amount_cents: 8635`）。
- 未配对 / 错 token：`401 {"ok":false,"error":"unauthorized"}`。
- 已撤销：`403 {"ok":false,"error":"device_revoked"}`。

### 2.5 已配对且已绑定作用域：投递正式 0.1.0 `expense.create`

设备撤销或同一设备 ID 成功重新领取凭据后，桌面需重新绑定业务授权。普通重连继续使用既有凭据和绑定。

启动服务端时须显式传入经 `init-trip` 创建的隔离旅行库 `--trip-db <travel.sqlite>`，并由桌面经本地控制面绑定已配对设备到获准的业务设备身份与旅行分支：

```sh
backend/.venv/bin/python -m desktop.lan_pairing bind-exchange \
  --state-dir /tmp/travelflow-lan-proto \
  --device-id android-paired-1 \
  --dataset-id p3-d1-handoff-dataset \
  --source-device-id android-business-1 \
  --travel-id 92ba6a148a17 \
  --branch-id f4f921d01f4e
```

移动客户端携带固定证书 TLS 与已配对凭据，直接发送持久保存的 0.1.0 原命令字节（不重排、不改写）：

```http
POST /exchange/expense-create HTTP/1.1
Authorization: Bearer <device_token>
X-Device-Id: android-paired-1
Content-Type: application/json; charset=utf-8

{"profile":"p1-host-control-1","kind":"expense.create","operation":{"dataset_id":"p3-d1-handoff-dataset","source_device_id":"android-business-1","operation_id":"op-https-metro-1"},"target":{"travel_id":"92ba6a148a17","branch_id":"f4f921d01f4e","entity_id":"https-metro-1"},"expected_revision_id":"9639db1eb3b8","payload":{"title":"机场快轨","amount_cents":1723,"currency":"CNY","paid_on":null,"category":"transport","day":null,"activity_id":null,"notes":"HTTPS 实连测试","receipt_ids":[],"allow_shared_receipts":false,"budget_item_id":null,"settles_budget":false}}
```

- 首次提交成功或同键原意图重放：`200 OK`，返回完整 0.1.0 原始回执（含 `replayed`、`state: "committed"`、`receipt.result_revision_id`、`receipt.intent_sha256`）。
- 已配对但未绑定旅行授权：`403 {"ok":false,"error":"exchange_not_authorized"}`（零写入）。
- 伪造 `source_device_id` 或投向未授权分支：`403 {"ok":false,"error":"unauthorized"}`（零写入）。
- 设备已撤销：`403 {"ok":false,"error":"device_revoked"}`（零写入）。
- 冲突（同键异意图 / 过期基线 / 实体已存在）：`409` 并返回原契约错误码。

### 2.6 已配对且已绑定作用域：独立查询 `receipt.lookup`

```http
POST /exchange/receipt-lookup HTTP/1.1
Authorization: Bearer <device_token>
X-Device-Id: android-paired-1
Content-Type: application/json; charset=utf-8

{"profile":"p1-host-query-1","kind":"receipt.lookup","operation":{"dataset_id":"p3-d1-handoff-dataset","source_device_id":"android-business-1","operation_id":"op-https-metro-1"}}
```

- 命中已提交回执：`200 OK`，返回 `state: "committed"` 及含 `intent_sha256`、`result_revision_id` 的完整回执；未授权或已撤销分别返回 `403 unauthorized` / `403 device_revoked` 且不泄露回执。

### 2.7 已配对且已绑定作用域：手机主动领取 `expense.patch` 待发原命令

沿用手机主动连接桌面的方式，手机在固定证书 TLS 上调用 `POST /exchange/expense-patch/pull`，桌面按 `(dataset_id, travel_id, branch_id, 原记账设备 source_device_id)` 过滤仅属于该设备的待发更正命令，复用 `prepare_outbound_delivery` 持久标记 `verification_pending` 后返回原始字节：

```http
POST /exchange/expense-patch/pull HTTP/1.1
Authorization: Bearer <device_token>
X-Device-Id: android-paired-1
Content-Type: application/json; charset=utf-8

{}
```

响应（`200 OK`）：

```json
{
  "ok": true,
  "items": [
    {
      "operation_id": "op-desktop-patch-...",
      "dataset_id": "p3-d1-handoff-dataset",
      "source_device_id": "desktop-device-1",
      "expected_revision_id": "<R1>",
      "source_revision_id": "<R2>",
      "entity_id": "https-metro-1",
      "intent_sha256": "<64-hex>",
      "delivery_state": "verification_pending",
      "raw_command_json": "{\"profile\":\"p1-host-control-1\",\"kind\":\"expense.patch\",...}"
    }
  ]
}
```

- 若拉取响应丢失，手机再次调用 `POST /exchange/expense-patch/pull` 仍可重领完全相同的 `raw_command_json` 原字节。
- 其他业务设备、未绑定授权或已撤销设备无法领取（分别返回空列表 / `403 exchange_not_authorized` / `403 device_revoked`）。

### 2.8 已配对且已绑定作用域：手机交回 `expense.patch` 完整回执

手机应用 `raw_command_json`（或本地查询已提交回执）后，将完整移动回执 JSON 直接提交到 `POST /exchange/expense-patch/confirm`：

```http
POST /exchange/expense-patch/confirm HTTP/1.1
Authorization: Bearer <device_token>
X-Device-Id: android-paired-1
Content-Type: application/json; charset=utf-8

{
  "ok": true,
  "state": "committed",
  "receipt": {
    "operation": {
      "dataset_id": "p3-d1-handoff-dataset",
      "source_device_id": "desktop-device-1",
      "operation_id": "op-desktop-patch-..."
    },
    "kind": "expense.patch",
    "canonical_profile": "p1-host-intent-c14n-1",
    "intent_sha256": "<64-hex>",
    "effect": "changed",
    "result_revision_id": "m-rev-2",
    "synced_desktop_revision_id": "<R2>",
    "travel_id": "92ba6a148a17",
    "branch_id": "f4f921d01f4e",
    "entity_id": "https-metro-1",
    "origin_direction": "received"
  }
}
```

- 桌面核对归属（数据集、旅行、分支、原记账设备）及完整回执字段后，原子保存 `confirmed_mobile_revision_id` 并标记 `confirmed`（`200 OK`）；重复提交返回 `200 OK`（`already_confirmed: true`）且不重复写账；重启桌面后待发队列为 0。
- 错配回执保持 `verification_pending` 返回 `400 receipt_mismatch`；其他设备、未绑定授权或已撤销设备返回 `403`。

## 3. 复跑命令

### Linux / WSL

```sh
# 1. 自动化回归（配对双方确认、重开重连、撤销、错证书指纹、控制面隔离）
backend/.venv/bin/python -m unittest desktop.tests.test_lan_pairing -v

# 2. 前台手动启停示例（独立临时目录）
backend/.venv/bin/python -m desktop.lan_pairing serve \
  --state-dir /tmp/travelflow-lan-proto --bind 127.0.0.1 --port 0 --open-pairing
# 另一终端批准、撤销或停止：
backend/.venv/bin/python -m desktop.lan_pairing status --state-dir /tmp/travelflow-lan-proto
backend/.venv/bin/python -m desktop.lan_pairing approve --state-dir /tmp/travelflow-lan-proto --request-id <id> --sas-code <6位>
backend/.venv/bin/python -m desktop.lan_pairing revoke --state-dir /tmp/travelflow-lan-proto --device-id <device_id>
backend/.venv/bin/python -m desktop.lan_pairing stop --state-dir /tmp/travelflow-lan-proto
```

### Windows 原生（Python 3.12+ 与 Git OpenSSL）

```powershell
python -m unittest desktop.tests.test_lan_pairing -v
python -m desktop.lan_pairing serve --state-dir "$env:TEMP\travelflow-lan-proto" --bind 10.226.92.198 --port 0 --open-pairing
```

## 4. Windows 原生与 WSL 实测可达性记录（2026-11-25）

本机网络环境观察：Windows 原生存在无线网卡 `WLAN`（`10.226.92.198`）、WSL 虚拟桥 `vEthernet (WSL (Hyper-V firewall))`（`172.20.16.1`）以及代理虚拟网卡 `Meta`（`198.18.0.1`）；WSL2 内部存在 `lo`（`127.0.0.1`）与 `eth0`（`172.20.31.170/20`）。

| 服务端运行环境 | 绑定地址（示例端口） | 测试对端与连接类别 | 实测结果 | 边界说明 |
| --- | --- | --- | --- | --- |
| WSL2 (Python 3.13.13 / OpenSSL 3.5.6) | `127.0.0.1:45895` | WSL 本机回环；Windows 原生本机回环 | 均 `HTTP 200`（证书指纹校验通过） | **仅本机回环**（WSL localhost 转发），不可供局域网手机连接 |
| WSL2 (Python 3.13.13 / OpenSSL 3.5.6) | `172.20.31.170:44291` | WSL 自测；Windows 原生跨虚拟网卡访问 | 均 `HTTP 200`（证书指纹校验通过） | **同机 Hyper-V 私有子网**，物理 Wi-Fi 手机默认不可直达（需 Windows 端口转发或镜像网络） |
| Windows 原生 (Python 3.12.10 / OpenSSL 3.0.16) | `127.0.0.1:9950` | Windows 本机回环；WSL 访问 `127.0.0.1` | Windows `HTTP 200`；WSL `ConnectionRefusedError` | **仅本机回环**，WSL 的 `127.0.0.1` 指向 WSL 自身 |
| Windows 原生 (Python 3.12.10 / OpenSSL 3.0.16) | `172.20.16.1:9956` | Windows 自测；WSL 跨虚拟桥访问 | 均 `HTTP 200`（证书指纹校验通过） | **同机 WSL 桥接地址**，不面向物理局域网 |
| Windows 原生 (Python 3.12.10 / OpenSSL 3.0.16) | `10.226.92.198:9953` | Windows 自测；WSL 跨虚拟网卡访问物理 WLAN IP | 均 `HTTP 200`（证书指纹校验通过） | **物理 WLAN 地址（同机实测通过）**；**真实跨设备 Android 手机同 Wi-Fi / 热点连接、Windows 防火墙入站放行及 `Meta` 代理共存尚未在本轮实测** |

自动化单元测试实测：
- WSL2（Python 3.13.13 / OpenSSL 3.5.6）：`test_lan_pairing` 通过（1/1，0.62s）。
- Windows 原生（Python 3.12.10 / OpenSSL 3.0.16）：`test_lan_pairing` 通过（1/1，0.99s）。

未覆盖项：真实 Android 手机经同 Wi-Fi／热点连接 `10.226.92.198`、Windows 防火墙入站弹窗实测、异地网络与 VPN／代理共存、以及 Android 真实业务命令经此通道投递和反向更正。
