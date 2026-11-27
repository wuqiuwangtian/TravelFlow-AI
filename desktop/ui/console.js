"use strict";
const byId = (id) => document.getElementById(id);
let token = location.hash.slice(1);
try {
  if (token) sessionStorage.setItem("travelflow-console", token);
  else token = sessionStorage.getItem("travelflow-console") || "";
} catch { /* The current launch token still works if storage is unavailable. */ }
history.replaceState(null, "", "/");
let state = null;
let dirty = false;
let loaded = false;
let submitting = false;
let ended = false;
let refreshing = false;
let connected = false;
let lastConfirmation = null;
let lastRecovery = null;
let pendingPick = null;
let lastBackup = null;
let syncLoaded = false;
let syncDirty = false;
let lastPendingSig = null;
let lastDevicesSig = null;
const syncSourceDrafts = new Map();
const labels = { backup: "正在备份并校验数据库…", restore: "正在校验并恢复副本…", recover: "正在核对实例归属与运行锁…", pick: "正在等待系统文件选择器…", inspect: "正在核对配置和数据…", prepare: "正在准备环境与构建，请等待完成…", start: "正在启动旅行助手…", stop: "正在安全停止旅行助手…", exit: "正在停止并退出管理器…", "sync-start": "正在初始化隔离测试旅行并启动同步服务…", "sync-stop": "正在停止同步服务…", "sync-open-pairing": "正在生成新的配对资料…", "sync-approve": "正在批准配对请求…", "sync-reject": "正在拒绝配对请求…", "sync-authorize": "正在授权该设备访问隔离测试旅行…", "sync-revoke": "正在撤销设备并移除旅行授权…" };
const modes = { "windows-native": "Windows 原生", wsl: "WSL Linux", linux: "Linux" };
async function api(path, payload) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 10000);
  try {
    const response = await fetch(path, { signal: controller.signal, cache: "no-store", headers: { "X-TravelFlow-Control": token, "Content-Type": "application/json" }, ...(payload ? { method: "POST", body: JSON.stringify(payload) } : {}) });
    if (!response.ok) {
      if (response.status === 403) throw new Error("管理入口已失效，请重新打开启动入口。");
      const body = await response.json(); throw new Error(body.error || "管理请求失败，请重试。");
    }
    return await response.json();
  } catch (error) {
    if (error.name === "AbortError") throw new Error("管理请求超时；操作可能已被接收，请等待状态核对后再决定是否重试。");
    throw error;
  } finally { clearTimeout(timer); }
}
function payload() { const qa = byId("profile").value === "application-qa"; return { profile: byId("profile").value, env_file: qa ? "" : byId("env-file").value.trim(), database: byId("database").value.trim(), ports: [Number(byId("backend-port").value), Number(byId("frontend-port").value)] }; }
function showError(message) { byId("error").hidden = !message; byId("error").textContent = message || ""; }
function formatYuan(cents) {
  return typeof cents === "number" ? (cents / 100).toFixed(2) + " 元（" + cents + " 分）" : "—";
}
function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}
function renderSync(busy) {
  const sync = state?.sync;
  if (!sync) return;
  if (!syncLoaded && !syncDirty && sync.config) {
    byId("sync-bind-host").value = sync.config.bind_host || "127.0.0.1";
    byId("sync-port").value = String(sync.config.port ?? 0);
    byId("sync-san-ips").value = (sync.config.san_ips || []).join(", ");
    syncLoaded = true;
  }
  const canDom = typeof document.createElement === "function";
  if (canDom && Array.isArray(sync.bind_candidates)) {
    const list = byId("sync-bind-list");
    if (list && list.dataset?.candidates !== sync.bind_candidates.join(",")) {
      list.textContent = "";
      for (const ip of sync.bind_candidates) {
        const opt = document.createElement("option");
        opt.value = ip;
        list.appendChild(opt);
      }
      if (list.dataset) list.dataset.candidates = sync.bind_candidates.join(",");
    }
  }
  byId("sync-env-hint").textContent = sync.windows_native
    ? "当前为 Windows 原生环境，仅开放隔离虚构测试旅行（北京三日游），不读取个人数据库或 .env；批准配对后仍需单独授权旅行才可读写。"
    : "当前处于非 Windows 原生环境，仍可启动隔离虚构测试旅行同步服务；物理手机局域网直连请使用 Windows 原生环境。";
  byId("sync-fields").disabled = busy || Boolean(sync.running);
  byId("sync-start").disabled = busy || Boolean(sync.running) || !sync.backend_python_ready;
  byId("sync-stop").disabled = busy || !sync.running;
  byId("sync-open-pairing").disabled = busy || !sync.running;
  byId("sync-state").textContent = !connected
    ? "管理连接未确认，同步服务状态暂不可操作；请等待重新核对。"
    : !sync.backend_python_ready
      ? "未找到后端 Python 3.13+ 虚拟环境，请先准备环境与构建或配置 TRAVELFLOW_SYNC_PYTHON。"
      : sync.running
        ? `同步服务运行中（https://${sync.lan_host}:${sync.lan_port}）`
        : "同步服务已停止（重启将沿用同一证书与配对记录）";
  byId("sync-endpoint").textContent = sync.running ? `https://${sync.lan_host}:${sync.lan_port}` : "未监听";
  byId("sync-cert").textContent = sync.cert_sha256 || "尚未生成（首次启动时创建）";
  const trip = sync.trip;
  byId("sync-trip").textContent = trip
    ? `${trip.title} · 旅行 ${trip.travel_id} · 主分支 ${trip.branch_id} · 数据集 ${trip.dataset_id}`
    : "首次启动时自动初始化隔离测试旅行（北京三日游）";
  byId("sync-ledger").textContent = trip && trip.expense_count !== null
    ? `${trip.expense_count} 笔消费 · 共 ${formatYuan(trip.paid_cents)} · 分支头 ${trip.head_revision_id} · 待发更正 ${trip.pending_outbound_count} / 已确认 ${trip.confirmed_outbound_count}`
    : "尚未初始化";

  const showPairing = connected && Boolean(sync.running) && Boolean(sync.pairing);
  byId("sync-pairing-box").hidden = !showPairing;
  if (showPairing) {
    byId("sync-pair-endpoint").textContent = `https://${sync.pairing.lan_host}:${sync.pairing.lan_port}`;
    byId("sync-pair-code").textContent = sync.pairing.pairing_code;
    byId("sync-pair-session").textContent = sync.pairing.pairing_session_id;
    byId("sync-pair-cert").textContent = sync.pairing.cert_sha256;
    byId("sync-pair-expires").textContent = sync.pairing.expires_at;
    byId("sync-pairing-json").value = sync.pairing_json || "";
  }
  byId("sync-copy-pairing").disabled = busy || !showPairing;

  const pending = connected && sync.running ? (sync.pending_requests || []) : [];
  byId("sync-pending-empty").hidden = pending.length > 0;
  if (canDom) {
    const pendingSig = JSON.stringify({ busy, pending });
    if (pendingSig !== lastPendingSig) {
      lastPendingSig = pendingSig;
      const box = byId("sync-pending-list");
      box.textContent = "";
      for (const req of pending) {
        const card = el("div", "sync-card");
        card.dataset.requestId = req.request_id;
        const head = el("div", "sync-card-head");
        head.appendChild(el("span", "sync-card-title", `${req.device_name} (${req.device_id})`));
        const waiting = req.status === "awaiting_desktop_confirm";
        head.appendChild(
          el("span", waiting ? "sync-badge" : "sync-badge sync-badge-ok", waiting ? "等待核对短码并批准" : "已批准，等待手机领取")
        );
        card.appendChild(head);
        const sasRow = el("p", "", "核对短码（请确认手机屏幕一致）：");
        sasRow.appendChild(el("span", "sas-code", req.sas_code));
        card.appendChild(sasRow);
        if (waiting) {
          const actions = el("div", "actions");
          const approveBtn = el("button", "", "核对短码并批准");
          approveBtn.type = "button";
          approveBtn.disabled = busy;
          approveBtn.dataset.action = "sync-approve";
          approveBtn.addEventListener("click", () => syncAction("sync-approve", { request_id: req.request_id, sas_code: req.sas_code }));
          const rejectBtn = el("button", "secondary", "拒绝");
          rejectBtn.type = "button";
          rejectBtn.disabled = busy;
          rejectBtn.dataset.action = "sync-reject";
          rejectBtn.addEventListener("click", () => syncAction("sync-reject", { request_id: req.request_id }));
          actions.appendChild(approveBtn);
          actions.appendChild(rejectBtn);
          card.appendChild(actions);
        }
        box.appendChild(card);
      }
    }
  }

  const devices = sync.devices || [];
  const grantsByDev = new Map((sync.exchange_grants || []).map((g) => [g.paired_device_id, g]));
  byId("sync-devices-empty").hidden = devices.length > 0;
  if (canDom) {
    const devicesSig = JSON.stringify({ busy, running: sync.running, devices, grants: sync.exchange_grants || [] });
    if (devicesSig !== lastDevicesSig) {
      lastDevicesSig = devicesSig;
      const box = byId("sync-devices-list");
      box.textContent = "";
      for (const dev of devices) {
        const grant = grantsByDev.get(dev.device_id) || null;
        const card = el("div", "sync-card");
        card.dataset.deviceId = dev.device_id;
        const head = el("div", "sync-card-head");
        head.appendChild(el("span", "sync-card-title", `${dev.device_name} (${dev.device_id})`));
        const badgeText = dev.status === "revoked"
          ? "已撤销"
          : grant
            ? "活跃 · 已授权隔离旅行"
            : "活跃 · 未授权旅行";
        const badgeClass = dev.status === "revoked"
          ? "sync-badge sync-badge-danger"
          : grant
            ? "sync-badge sync-badge-ok"
            : "sync-badge";
        head.appendChild(el("span", badgeClass, badgeText));
        card.appendChild(head);
        if (grant) {
          card.appendChild(
            el(
              "p",
              "hint mono",
              `授权范围：业务设备 ${grant.source_device_id} · 旅行 ${grant.travel_id} · 分支 ${grant.branch_id} · 数据集 ${grant.dataset_id}`
            )
          );
        } else if (dev.status === "active") {
          card.appendChild(el("p", "hint", "已配对但尚未授权旅行：此时提交消费或查询回执会被拒绝（403）。"));
        }
        if (dev.status === "active") {
          const grantRow = el("div", "sync-grant-row");
          const lbl = el("label", "", "业务设备标识（source_device_id）");
          const input = document.createElement("input");
          input.autocomplete = "off";
          input.disabled = busy || !sync.running;
          input.dataset.sourceInput = dev.device_id;
          input.value = syncSourceDrafts.has(dev.device_id)
            ? syncSourceDrafts.get(dev.device_id)
            : (grant?.source_device_id || dev.device_id);
          input.addEventListener("input", () => { syncSourceDrafts.set(dev.device_id, input.value); });
          lbl.appendChild(input);
          grantRow.appendChild(lbl);

          const authBtn = el("button", "", grant ? "更新旅行授权" : "授权隔离旅行");
          authBtn.type = "button";
          authBtn.disabled = busy || !sync.running;
          authBtn.dataset.action = "sync-authorize";
          authBtn.addEventListener("click", () => syncAction("sync-authorize", { device_id: dev.device_id, source_device_id: input.value.trim() }));
          grantRow.appendChild(authBtn);
          card.appendChild(grantRow);

          const actions = el("div", "actions");
          const revokeBtn = el("button", "danger", "撤销设备");
          revokeBtn.type = "button";
          revokeBtn.disabled = busy || !sync.running;
          revokeBtn.dataset.action = "sync-revoke";
          revokeBtn.addEventListener("click", () => syncAction("sync-revoke", { device_id: dev.device_id }));
          actions.appendChild(revokeBtn);
          card.appendChild(actions);
        }
        box.appendChild(card);
      }
    }
  }
}
async function syncAction(name, body) {
  if (!connected || submitting || state?.busy) return;
  submitting = true; render();
  byId("operation").textContent = labels[name] || "正在处理同步操作…";
  try {
    await api(`/api/${name}`, body || {});
    if (name === "sync-start") syncDirty = false;
    await refresh();
  } catch (error) { state.error = error.message; showError(error.message); }
  finally { submitting = false; render(); }
}
function render() {
  if (!state || ended) return;
  const busy = !connected || Boolean(state.busy) || submitting;
  byId("mode").textContent = modes[state.mode] || state.mode;
  byId("project").textContent = state.project;
  byId("state-dir").textContent = state.state_dir;
  byId("fields").disabled = busy;
  const qa = byId("profile").value === "application-qa";
  byId("picker-hint").hidden = !state.picker_hint;
  byId("picker-hint").textContent = state.picker_hint || "";
  byId("picker-actions").hidden = !state.native_picker;
  for (const id of ["pick-env", "pick-db"]) {
    byId(id).hidden = !state.native_picker;
    byId(id).disabled = busy || (qa && id === "pick-env");
  }
  for (const id of ["pick-backup-target", "pick-restore-package", "pick-restore-target"]) byId(id).hidden = !state.native_picker;
  if (pendingPick && state.picked?.request_id === pendingPick) {
    const { kind, path } = state.picked;
    if (path && ["backup_parent", "restore_parent", "restore_package"].includes(kind)) {
      const slash = state.mode === "windows-native" ? "\\" : "/";
      const base = path.endsWith(slash) ? path : path + slash;
      const date = new Date().toISOString().replace(/[:.]/g, "-");
      if (kind === "restore_package") byId("restore-package").value = path;
      else if (kind === "backup_parent") byId("backup-target").value = base + "TravelFlow-backup-" + date;
      else byId("restore-target").value = base + "TravelFlow-copy-" + date;
    } else if (path) {
      byId(state.picked.kind === "env_file" ? "env-file" : "database").value = state.picked.path;
      dirty = true;
      byId("confirm").checked = false;
      byId("initialize").checked = false;
    }
    pendingPick = null;
  }
  byId("env-file").disabled = qa;

  if (state.preview?.confirmation !== lastConfirmation) {
    lastConfirmation = state.preview?.confirmation;
    byId("confirm").checked = false;
    byId("initialize").checked = false;
  }
  for (const id of ["inspect", "prepare", "exit"]) byId(id).disabled = busy;
  byId("operation").textContent = state.busy ? labels[state.busy] : "";
  const preview = dirty ? null : state.preview;
  byId("copy-fields").disabled = busy;
  byId("backup-source").textContent = preview?.selection.database || "请先预览配置与数据";
  byId("backup").disabled = busy || !preview?.database_exists || !byId("backup-target").value.trim();
  byId("restore").disabled = busy || !byId("restore-package").value.trim() || !byId("restore-target").value.trim();
  const result = state.data_result;
  byId("copy-result").textContent = result ? (result.kind === "backup" ? "备份已校验" : "副本已校验") + " · " + result.path : "";
  byId("use-copy").hidden = result?.kind !== "restore";
  byId("use-copy").disabled = busy;
  if (result?.kind === "backup" && result.path !== lastBackup) {
    lastBackup = result.path;
    byId("restore-package").value = result.path;
  }
  byId("preview").hidden = !preview;
  if (preview) {
    byId("preview-ports").textContent = `后端 ${preview.ports[0]} · 网页 ${preview.ports[1]}`;
    byId("preview-env").textContent = preview.selection.env_file;
    byId("preview-db").textContent = preview.selection.database;
    byId("preview-exists").textContent = preview.database_exists ? "已有数据库" : "数据库尚不存在";
    byId("preview-ready").textContent = preview.preparation.ready ? "已准备就绪" : preview.preparation.errors.join("；");
    byId("initialize-row").hidden = preview.database_exists || preview.selection.profile === "application-qa";
  }
  byId("start").disabled = busy || Boolean(state.state_error) || !preview || !preview.preparation.ready || !byId("confirm").checked || (!byId("initialize-row").hidden && !byId("initialize").checked);
  byId("recover").hidden = false;
  byId("recover").disabled = busy;
  byId("recovery").hidden = !state.recovery;
  byId("recovery").textContent = state.recovery ? state.recovery.message + (state.recovery.archives.length ? " 元数据保留位置：" + state.recovery.archives.join("；") : "") : "";
  const runtime = state.runtime;
  byId("stop").disabled = busy || (!runtime && !state.runtime_unverified);
  byId("runtime-details").hidden = !runtime;
  byId("open-app").hidden = !connected || !runtime?.ready;
  byId("runtime-state").textContent = !connected ? "管理连接未确认，以下是上次取得的数据；请等待重新核对。" : state.runtime_unverified ? "已有实例状态无法确认；请核对运行日志，保留现有状态。" : !runtime ? "尚未启动或已停止" : runtime.ready ? "旅行助手运行正常" : "服务健康检查未通过；可以查看错误或安全停止后重试";
  if (runtime) {
    const backend = runtime.services.find((service) => service.role === "backend");
    const frontend = runtime.services.find((service) => service.role === "frontend");
    const url = frontend ? `http://127.0.0.1:${frontend.port}` : "";
    byId("actual-env").textContent = runtime.selection.env_file || "无配置文件";
    byId("actual-db").textContent = backend?.database || runtime.selection.database;
    byId("web-address").textContent = url;
    byId("open-app").href = url;
    byId("health-time").textContent = runtime.checked_at ? new Date(runtime.checked_at * 1000).toLocaleString() : "等待健康检查";
  }
  renderSync(busy);
  showError(state.error || state.state_error);
}
async function refresh() {
  if (ended || refreshing) return;
  refreshing = true;
  try {
    state = await api("/api/state");
    connected = true;
    if (!loaded || (state.recovery?.reattached && state.recovery.id !== lastRecovery && !dirty)) {
      const saved = state.preferences;
      byId("profile").value = saved.profile || "";
      byId("env-file").value = saved.env_file || "";
      byId("database").value = saved.database || "";
      if (saved.ports) { byId("backend-port").value = saved.ports[0]; byId("frontend-port").value = saved.ports[1]; }
      loaded = true;
    }
    lastRecovery = state.recovery?.id;
    byId("connection").textContent = "已连接本机管理器";
    render();
    if (state.exit_ready) {
      await api("/api/close", {});
      ended = true;
      for (const button of document.querySelectorAll("button")) button.disabled = true;
      byId("connection").textContent = "服务已停止，管理器正在退出。可以关闭此页。";
      byId("operation").textContent = "";
    }
  } catch (error) {
    byId("connection").textContent = "无法连接本机管理器，请重新打开启动入口。";
    connected = false;
    byId("confirm").checked = false;
    byId("initialize").checked = false;
    render();
    showError(error.message);
  } finally { refreshing = false; }
}
async function action(name) {
  if (!connected || submitting || state?.busy) return;
  if (name === "prepare" && !window.confirm("将安装锁定依赖并构建应用，可能需要联网。是否开始准备？")) return;
  if (name === "recover" && !window.confirm("将核对实例归属与运行锁，仅保留并归档可确认无持锁管理器的旧控制状态；不会移动数据库或自动重启。是否继续？")) return;
  if (name === "exit" && !window.confirm("将停止旅行助手并退出管理器。是否继续？")) return;
  submitting = true; render();
  byId("operation").textContent = labels[name];
  try {
    let body = {};
    if (name === "inspect") body = payload();
    if (name === "start") body = { confirmation: state.preview.confirmation, initialize_database: byId("initialize").checked };
    if (name === "backup") body = { confirmation: state.preview.confirmation, target: byId("backup-target").value.trim() };
    if (name === "restore") body = { package: byId("restore-package").value.trim(), target: byId("restore-target").value.trim() };
    await api(`/api/${name}`, body);
    if (name === "inspect") { dirty = false; byId("confirm").checked = false; byId("initialize").checked = false; }
    if (name === "exit") {
      // Polling continues until shutdown is confirmed; acceptance is not completion.
      byId("operation").textContent = labels.exit;
    }
    await refresh();
  } catch (error) { state.error = error.message; showError(error.message); }
  finally { submitting = false; render(); }
}
for (const id of ["inspect", "prepare", "start", "stop", "exit", "recover", "backup", "restore"]) byId(id).addEventListener("click", () => action(id));
for (const id of ["profile", "env-file", "database", "backend-port", "frontend-port"]) byId(id).addEventListener("input", () => { dirty = true; byId("confirm").checked = false; byId("initialize").checked = false; render(); });
byId("profile").addEventListener("change", () => { byId("database").value = ""; render(); });
byId("use-copy").addEventListener("click", () => {
  byId("database").value = state.data_result.path;
  byId("database").dispatchEvent(new Event("input"));
  byId("inspect").focus();
});
for (const id of ["backup-target", "restore-package", "restore-target"]) byId(id).addEventListener("input", render);
for (const id of ["confirm", "initialize"]) byId(id).addEventListener("change", render);
byId("theme").addEventListener("change", () => { document.documentElement.dataset.theme = byId("theme").value; });
refresh();
setInterval(refresh, 2000);
function recheckConnection() {
  if (ended || document.visibilityState === "hidden") return;
  connected = false;
  byId("confirm").checked = false;
  byId("initialize").checked = false;
  byId("connection").textContent = "正在重新核对本机管理器…";
  render();
  refresh();
}
document.addEventListener("visibilitychange", recheckConnection);
window.addEventListener("focus", recheckConnection);

for (const [id, kind] of [["pick-env", "env_file"], ["pick-db", "database"], ["pick-backup-target", "backup_parent"], ["pick-restore-package", "restore_package"], ["pick-restore-target", "restore_parent"]]) {
  byId(id).addEventListener("click", async () => {
    if (!connected || submitting || state?.busy) return;
    submitting = true; render();
    pendingPick = crypto.randomUUID();
    try { await api("/api/pick", { kind, request_id: pendingPick }); await refresh(); }
    catch (error) { pendingPick = null; state.error = error.message; }
    finally { submitting = false; render(); }
  });
}
for (const id of ["sync-bind-host", "sync-port", "sync-san-ips"]) {
  byId(id).addEventListener("input", () => { syncDirty = true; });
}
byId("sync-start").addEventListener("click", () => syncAction("sync-start", {
  bind_host: byId("sync-bind-host").value.trim(),
  port: Number(byId("sync-port").value || 0),
  san_ips: byId("sync-san-ips").value.trim(),
}));
byId("sync-stop").addEventListener("click", () => syncAction("sync-stop", {}));
byId("sync-open-pairing").addEventListener("click", () => syncAction("sync-open-pairing", { ttl_seconds: 120 }));
byId("sync-copy-pairing").addEventListener("click", async () => {
  const text = byId("sync-pairing-json").value;
  if (!text) return;
  try {
    if (navigator.clipboard?.writeText) await navigator.clipboard.writeText(text);
    byId("sync-copy-status").textContent = "已复制配对资料 JSON";
  } catch {
    byId("sync-copy-status").textContent = "请手动选中上方文本框复制";
  }
});
