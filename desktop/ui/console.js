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
let lastConfirmation = null;
const labels = { inspect: "正在核对配置和数据…", prepare: "正在准备环境与构建，请等待完成…", start: "正在启动旅行助手…", stop: "正在安全停止旅行助手…", exit: "正在停止并退出管理器…" };
const modes = { "windows-native": "Windows 原生", wsl: "WSL Linux", linux: "Linux" };
async function api(path, payload) {
  const response = await fetch(path, { cache: "no-store", headers: { "X-TravelFlow-Control": token, "Content-Type": "application/json" }, ...(payload ? { method: "POST", body: JSON.stringify(payload) } : {}) });
  if (!response.ok) {
    if (response.status === 403) throw new Error("管理入口已失效，请重新打开启动入口。");
    const body = await response.json(); throw new Error(body.error || "管理请求失败，请重试。");
  }
  return response.json();
}
function payload() { const qa = byId("profile").value === "application-qa"; return { profile: byId("profile").value, env_file: qa ? "" : byId("env-file").value.trim(), database: qa ? "" : byId("database").value.trim(), ports: [Number(byId("backend-port").value), Number(byId("frontend-port").value)] }; }
function showError(message) { byId("error").hidden = !message; byId("error").textContent = message || ""; }
function render() {
  if (!state || ended) return;
  const busy = Boolean(state.busy) || submitting;
  byId("mode").textContent = modes[state.mode] || state.mode;
  byId("project").textContent = state.project;
  byId("state-dir").textContent = state.state_dir;
  byId("fields").disabled = busy;
  const qa = byId("profile").value === "application-qa";
  byId("env-file").disabled = qa;
  byId("database").disabled = qa;
  if (state.preview?.confirmation !== lastConfirmation) {
    lastConfirmation = state.preview?.confirmation;
    byId("confirm").checked = false;
    byId("initialize").checked = false;
  }
  for (const id of ["inspect", "prepare", "exit"]) byId(id).disabled = busy;
  byId("operation").textContent = state.busy ? labels[state.busy] : "";
  const preview = dirty ? null : state.preview;
  byId("preview").hidden = !preview;
  if (preview) {
    byId("preview-ports").textContent = `后端 ${preview.ports[0]} · 网页 ${preview.ports[1]}`;
    byId("preview-env").textContent = preview.selection.env_file;
    byId("preview-db").textContent = preview.selection.database;
    byId("preview-exists").textContent = preview.database_exists ? "已有数据库" : "数据库尚不存在";
    byId("preview-ready").textContent = preview.preparation.ready ? "已准备就绪" : preview.preparation.errors.join("；");
    byId("initialize-row").hidden = preview.database_exists || preview.selection.profile === "application-qa";
  }
  byId("start").disabled = busy || !preview || !preview.preparation.ready || !byId("confirm").checked || (!byId("initialize-row").hidden && !byId("initialize").checked);
  const runtime = state.runtime;
  byId("stop").disabled = busy || (!runtime && !state.runtime_unverified);
  byId("runtime-details").hidden = !runtime;
  byId("open-app").hidden = !runtime?.ready;
  byId("runtime-state").textContent = state.runtime_unverified ? "已有实例状态无法确认；请核对运行日志，保留现有状态。" : !runtime ? "尚未启动或已停止" : runtime.ready ? "旅行助手运行正常" : "服务健康检查未通过；可以查看错误或安全停止后重试";
  if (runtime) {
    const backend = runtime.services.find((service) => service.role === "backend");
    const frontend = runtime.services.find((service) => service.role === "frontend");
    const url = frontend ? `http://127.0.0.1:${frontend.port}` : "";
    byId("actual-db").textContent = backend?.database || runtime.selection.database;
    byId("web-address").textContent = url;
    byId("open-app").href = url;
    byId("health-time").textContent = runtime.checked_at ? new Date(runtime.checked_at * 1000).toLocaleString() : "等待健康检查";
  }
  showError(state.error);
}
async function refresh() {
  if (ended || refreshing) return;
  refreshing = true;
  try {
    state = await api("/api/state");
    if (!loaded) {
      const saved = state.preferences;
      byId("profile").value = saved.profile || "";
      byId("env-file").value = saved.env_file || "";
      byId("database").value = saved.database || "";
      if (saved.ports) { byId("backend-port").value = saved.ports[0]; byId("frontend-port").value = saved.ports[1]; }
      loaded = true;
    }
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
    byId("start").disabled = true;
    byId("stop").disabled = true;
    showError(error.message);
  } finally { refreshing = false; }
}
async function action(name) {
  if (submitting || state?.busy) return;
  if (name === "prepare" && !window.confirm("将安装锁定依赖并构建应用，可能需要联网。是否开始准备？")) return;
  if (name === "exit" && !window.confirm("将停止旅行助手并退出管理器。是否继续？")) return;
  submitting = true; render();
  byId("operation").textContent = labels[name];
  try {
    await api(`/api/${name}`, name === "inspect" ? payload() : name === "start" ? { confirmation: state.preview.confirmation, initialize_database: byId("initialize").checked } : {});
    if (name === "inspect") { dirty = false; byId("confirm").checked = false; byId("initialize").checked = false; }
    if (name === "exit") {
      // Polling continues until shutdown is confirmed; acceptance is not completion.
      byId("operation").textContent = labels.exit;
    }
    await refresh();
  } catch (error) { state.error = error.message; showError(error.message); }
  finally { submitting = false; render(); }
}
for (const id of ["inspect", "prepare", "start", "stop", "exit"]) byId(id).addEventListener("click", () => action(id));
for (const id of ["profile", "env-file", "database", "backend-port", "frontend-port"]) byId(id).addEventListener("input", () => { dirty = true; byId("confirm").checked = false; byId("initialize").checked = false; render(); });
for (const id of ["confirm", "initialize"]) byId(id).addEventListener("change", render);
byId("theme").addEventListener("change", () => { document.documentElement.dataset.theme = byId("theme").value; });
refresh();
setInterval(refresh, 2000);
