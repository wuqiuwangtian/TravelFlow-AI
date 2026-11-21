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
const labels = { backup: "正在备份并校验数据库…", restore: "正在校验并恢复副本…", recover: "正在核对实例归属与运行锁…", pick: "正在等待系统文件选择器…", inspect: "正在核对配置和数据…", prepare: "正在准备环境与构建，请等待完成…", start: "正在启动旅行助手…", stop: "正在安全停止旅行助手…", exit: "正在停止并退出管理器…" };
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
