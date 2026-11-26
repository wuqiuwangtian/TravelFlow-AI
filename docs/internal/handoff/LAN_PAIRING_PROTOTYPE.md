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

## 5. Windows 原生业务服务端实测与 Android 下一轮联调入口（2026-11-26）

### 5.1 实测环境与网络／防火墙观察

- **服务端环境**：Windows 原生 `CPython 3.13.16`（由 Windows `uv` 在隔离目录 `%TEMP%\travelflow-win-py313-venv` 按 `backend/uv.lock` 冻结同步，不修改 WSL `backend/.venv` 或个人库），TLS 证书由 `C:\Program Files\Git\usr\bin\openssl.exe` 生成；业务库与配对状态均位于 Windows NTFS 隔离临时目录（`%TEMP%\travelflow-win-e2e-*`）。
- **本机网卡与配置文件**：
  - `Loopback Pseudo-Interface 1`：`127.0.0.1/8`
  - `vEthernet (WSL (Hyper-V firewall))`：`172.20.16.1/20`
  - `WLAN`：`10.226.92.198/24`（`NetworkCategory = Public`）
  - `Meta`：`198.18.0.1/30`（`NetworkCategory = Public`）
  - WSL2 `eth0`：`172.20.31.170/20`（默认网关 `172.20.16.1`）
- **防火墙与端口约束（只读核查，未修改系统防火墙策略）**：
  - Windows Defender Firewall 的 `Domain` / `Private` / `Public` 三档均开启且 `DefaultInboundAction = NotConfigured`（默认拦截未放行入站），Hyper-V 防火墙 `DefaultInboundAction = Block`。
  - **新解释器首次非回环绑定拦截**：`uv` 虚拟环境实际执行的是底层 `C:\Users\<user>\AppData\Roaming\uv\python\cpython-3.13.16-windows-x86_64-none\python.exe`。该新路径最初没有任何入站放行规则，首次绑定 `172.20.16.1` 时，同机 Windows 客户端可达（`HTTP 200`），而跨虚拟网卡的 WSL2 客户端（`172.20.31.170`）连接超时（`TimeoutError`）；当 Windows Defender Firewall 为该 `cpython-3.13.16` 解释器建立 `Public` TCP/UDP `Allow` 规则后，WSL2 访问 `10.226.92.198` 与 `172.20.16.1` 均稳定返回 `HTTP 200`（`127.0.0.1` 仍仅限 Windows 本机回环，WSL 访问返回 `ConnectionRefusedError`）。
  - **Android 联调注意事项**：
    1. 若物理 Wi-Fi／热点在 Windows 中处于 `Private` 网络配置而防火墙仅放行了 `Public`（或首次启动尚未在安全警报中允许当前 `python.exe`），物理手机连向 WLAN IP 将超时；须确认目标网络类型对应的 `python.exe` 入站已放行。
    2. Windows 存在系统 TCP 排除端口范围（可通过只读命令 `netsh interface ipv4 show excludedportrange protocol=tcp` 查看，本机含 `6025-6124`、`9819-9918`、`11115-11214`、`50000-50059` 等）；优先使用 `--port 0` 由系统分配空闲端口，如需固定端口须避开排除区间。
    3. 仅当模拟器运行在 Windows 宿主时，Android 模拟器若通过 `10.0.2.2` 访问宿主 Windows 回环，服务端须绑定 `--bind 127.0.0.1 --san-ip 10.0.2.2`，首次生成的证书需包含 `IP:10.0.2.2`，手机使用的配对地址也应为 `10.0.2.2`。当前 API 35 模拟器在 WSL 中运行，本轮 Android 联调应使用从该模拟器可达的 Windows 网卡 IPv4，并将服务绑定到同一地址。

### 5.2 已验证的完整业务往返与重启核对

通过 [test_windows_native_exchange.py](../../../backend/tests/test_windows_native_exchange.py) 复用现有 CLI 入口完成两条真实路径的端到端验收：

| 路径 | 客户端位置与网络路径 | 服务端绑定 | 实测结果 |
| --- | --- | --- | --- |
| 路径 1（跨虚拟网卡到 WLAN IP） | WSL2 `172.20.31.170` → Windows `WLAN 10.226.92.198:<随机端口>` | Windows 原生 Python 3.13.16 前台服务 | `PASSED`（26.45s） |
| 路径 2（Windows 本机回环） | Windows 原生 Python 3.13.16 → Windows `127.0.0.1:<随机端口>` | Windows 原生 Python 3.13.16 前台服务 | `PASSED`（29.57s） |

两条路径均在隔离目录实测通过以下完整序列：
1. Windows 原生 `exchange_trip_handoff.py init-trip` 初始化虚构旅行库并写入 `context.json`。
2. Windows 原生 `python -m desktop.lan_pairing serve --trip-db ... --open-pairing` 前台启动，客户端以固定证书指纹完成 `/health`（`exchange_enabled: true`）、`/pair/request` → Windows CLI `approve` → `/pair/complete` → Windows CLI `bind-exchange`。
3. 客户端经配对 HTTPS 提交 0.1.0 `expense.create`（`1723` 分，`https-win-metro-1`）获得桌面确认版本 `R1`。
4. Windows 原生 `exchange_trip_handoff.py edit-expense` 经真实 FastAPI `PUT /api/versions/{R1}/expenses/{id}` 将金额改为 `21.50` 元，推进桌面版本至 `R2` 并入队反向更正。
5. 客户端经 `POST /exchange/expense-patch/pull` 领取原命令字节，由移动参考接收器应用得到 `m-rev-2` 回执并经 `POST /exchange/expense-patch/confirm` 原子确认（重复确认返回 `already_confirmed: true`）。
6. Windows 原生 CLI `stop` 停机后，以同一 `--state-dir` 与 `--trip-db` 重新前台启动 `serve`，核对：
   - **证书身份**：重启前后 `cert_sha256` 完全一致；
   - **重连与待发队列**：`status` 返回 `lan_host`、`lan_port`、`cert_sha256`、`exchange_enabled: true` 与活跃授权；已配对客户端重连 `GET /trip/fictional` 返回 `200`、`POST /exchange/expense-patch/pull` 返回 `[]`；
   - **账本与确认版本**：Windows 原生 `inspect-trip` 与 `outbound-status` 均以 UTF-8 正常读回 2 笔消费（`"虚构午餐"` `8635` 分 + `"机场快轨"` `2150` 分 = `10785` 分）、分支头 `R2`、原分叉 0 笔消费、`pending_count == 0`、`confirmed_count == 1`、`confirmed_mobile_revision_id == "m-rev-2"`。

本轮额外修复了 Windows 原生控制台默认 `gb2312` 导致 CLI JSON 中文乱码的问题（`lan_pairing.py` 与 `exchange_trip_handoff.py` 入口自动切换 UTF-8 stdio，`_write_exclusive_bytes` 增加 `O_BINARY`），并为 `status` / `open-pairing` 补齐 `lan_host` 与 `lan_port` 字段。

未覆盖项：真实 Android APK（模拟器或物理手机）直连 Windows 原生服务、物理手机经同 Wi-Fi／热点连接 `10.226.92.198`、以及物理手机与 `Meta` 代理共存。

### 5.3 Android 下一轮可直接使用的 Windows 启动命令与配对信息获取方式

以下全部在 Windows PowerShell 中执行（仓库目录 `<WIN_REPO>` 例如 `\\wsl.localhost\Arch\home\wqwt\code\TravelFlow-AI` 或本地 Windows 检出目录）：

```powershell
# 1. 准备隔离的 Windows 原生 Python 3.13 后端虚拟环境（不改动 WSL backend/.venv）
$winVenv = "$env:TEMP\travelflow-win-py313-venv"
$env:UV_PROJECT_ENVIRONMENT = $winVenv
uv sync --frozen --python 3.13 --directory "<WIN_REPO>\backend"
$winPy = "$winVenv\Scripts\python.exe"

# 2. 在独立隔离目录初始化虚构旅行副本与业务上下文（context.json）
$workDir = "$env:TEMP\travelflow-win-android-handoff"
Set-Location "<WIN_REPO>"
& $winPy backend\scripts\exchange_trip_handoff.py init-trip `
  --work-dir "$workDir\trip" `
  --context-out "$workDir\context.json"
Get-Content -Raw -Encoding UTF8 "$workDir\context.json"

# 3. 前台启动 Windows 原生 HTTPS 业务服务并开启一次性配对会话
#   - 物理 Android 同 Wi-Fi / 热点：将 <WIN_LAN_IP> 替换为当前 WLAN IPv4（如 10.226.92.198）
& $winPy -m desktop.lan_pairing serve `
  --state-dir "$workDir\pairing-state" `
  --bind <WIN_LAN_IP> `
  --port 0 `
  --trip-db "$workDir\trip\travel.sqlite" `
  --open-pairing

#   - 若改用 Windows 本机 Android 模拟器（经 10.0.2.2 访问宿主回环）：
# & $winPy -m desktop.lan_pairing serve `
#   --state-dir "$workDir\pairing-state" `
#   --bind 127.0.0.1 `
#   --san-ip 10.0.2.2 `
#   --port 0 `
#   --trip-db "$workDir\trip\travel.sqlite" `
#   --open-pairing
```

**配对信息获取方式（安全边界：严禁将 `pairing-state\control.json` 或 `control_token` 交给手机）**：
1. **启动即取**：`serve --open-pairing` 启动后会在前台标准输出打印一行 UTF-8 JSON，直接包含手机配对所需的全部带外信息：`lan_host`、`lan_port`、`cert_sha256`、`pairing_session.pairing_session_id`、`pairing_session.pairing_code`、`pairing_session.expires_at`；业务上下文（`dataset_id`、`travel_id`、`branch_id`、`current_revision_id`）从 `$workDir\context.json` 读取。
2. **过期后重开配对或随时查询监听地址与待审请求**（另一 PowerShell 窗口执行，先设置相同的 $winPy、$workDir 和仓库工作目录）：

```powershell
# 重新开启一次性配对会话（返回 lan_host、lan_port、cert_sha256、pairing_session_id、pairing_code）
& $winPy -m desktop.lan_pairing open-pairing --state-dir "$workDir\pairing-state"

# 查看当前监听地址、证书指纹、待批准请求（含 request_id 与 6 位 sas_code）及已配对设备
& $winPy -m desktop.lan_pairing status --state-dir "$workDir\pairing-state"

# 核对手机屏幕 6 位 SAS 码后批准配对
& $winPy -m desktop.lan_pairing approve `
  --state-dir "$workDir\pairing-state" `
  --request-id <REQUEST_ID> `
  --sas-code <6位SAS码>

# 手机完成 /pair/complete 后，绑定其业务身份与隔离旅行分支
& $winPy -m desktop.lan_pairing bind-exchange `
  --state-dir "$workDir\pairing-state" `
  --device-id <ANDROID_PAIRED_DEVICE_ID> `
  --dataset-id p3-d1-handoff-dataset `
  --source-device-id <ANDROID_BUSINESS_DEVICE_ID> `
  --travel-id 92ba6a148a17 `
  --branch-id f4f921d01f4e

# 手机新增消费入账（版本 R1）后，桌面执行金额更正（如改为 21.50 元）
& $winPy backend\scripts\exchange_trip_handoff.py edit-expense `
  --database "$workDir\trip\travel.sqlite" `
  --version-id <R1> `
  --expense-id <EXPENSE_ID> `
  --amount 21.50

# 停机前记录地址和端口。重跑步骤 3 时沿用同一状态目录、数据库和绑定地址，
# 以原 lan_port 替换 --port 0，并去掉 --open-pairing，使手机保存的端点继续可用。
$endpoint = (& $winPy -m desktop.lan_pairing status --state-dir "$workDir\pairing-state") | ConvertFrom-Json
$endpoint | Select-Object lan_host, lan_port
& $winPy -m desktop.lan_pairing stop --state-dir "$workDir\pairing-state"

# 重启或结束后核对桌面账本、版本链与待发队列
& $winPy backend\scripts\exchange_trip_handoff.py inspect-trip `
  --database "$workDir\trip\travel.sqlite" `
  --branch-id f4f921d01f4e `
  --fork-branch-id 80a581ac74f9
& $winPy backend\scripts\exchange_trip_handoff.py outbound-status `
  --database "$workDir\trip\travel.sqlite"
```

### 5.4 自动化复跑命令

```sh
# 从 WSL 驱动真实 Windows 原生 Python 3.13 前台服务（跨虚拟网卡连向 Windows WLAN IP）
TRAVELFLOW_WINDOWS_BACKEND_PYTHON=/mnt/c/Users/<user>/AppData/Local/Temp/travelflow-win-py313-venv/Scripts/python.exe \
TRAVELFLOW_WINDOWS_BIND_HOST=10.226.92.198 \
bash -c 'cd backend && .venv/bin/pytest -v tests/test_windows_native_exchange.py'
```

```powershell
# 直接在 Windows 原生 Python 3.13 环境运行（默认连向 127.0.0.1，可设 $env:TRAVELFLOW_WINDOWS_BIND_HOST 覆盖）
Set-Location "<WIN_REPO>\backend"
& "$env:TEMP\travelflow-win-py313-venv\Scripts\python.exe" -m pytest -v tests\test_windows_native_exchange.py
```
