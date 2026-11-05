import { Capacitor, registerPlugin, CapacitorHttp } from "./capacitor-core.js";
import { summarizeLedger } from "./ledger-core.js";

const store = registerPlugin("ProbeStore");
const byId = (id) => document.getElementById(id);
let previousId = null;

function render(snapshot) {
  const summary = summarizeLedger({expenses: snapshot.entries, adjustments: []});
  if (summary.paid_cents !== snapshot.total_cents) throw new Error("Native/JS ledger mismatch");
  byId("ledger").textContent = `原生记录 ${snapshot.entries.length} 条 · 合计 ¥${(summary.paid_cents / 100).toFixed(2)}`;
}

async function save(id) {
  byId("append").disabled = true;
  byId("retry").disabled = true;
  try {
    byId("error").textContent = "";
    render(await store.append({id}));
    previousId = id;
  } catch (error) {
    byId("error").textContent = String(error);
  } finally {
    byId("append").disabled = false;
    byId("retry").disabled = !previousId;
  }
}

byId("append").onclick = () => save(crypto.randomUUID());
byId("retry").onclick = () => { if (previousId) void save(previousId); };
byId("http").onclick = async () => {
  byId("http").disabled = true;
  byId("network").textContent = "正在调用测试服务…";
  try {
    const result = await CapacitorHttp.request({
      url: "http://10.0.2.2:18510/echo",
      method: "POST",
      headers: {"Content-Type": "application/json", "X-P0-Token": "fixture-token"},
      data: {message: "synthetic-p0"},
      connectTimeout: 3000,
      readTimeout: 3000,
      disableRedirects: true,
    });
    if (result.status !== 200 || result.data?.probe !== "ok") throw new Error("Unexpected test response");
    byId("network").textContent = "原生HTTP测试通过（非真实模型调用）";
  } catch (error) {
    byId("network").textContent = `测试请求失败，本地记录仍可用：${String(error)}`;
  } finally {
    byId("http").disabled = false;
  }
};

try {
  if (!Capacitor.isNativePlatform()) throw new Error("This probe requires Android native runtime");
  render(await store.read());
  byId("runtime").textContent = "Android原生桥已就绪";
  byId("append").disabled = false;
  byId("http").disabled = false;
} catch (error) {
  byId("runtime").textContent = "原生能力未就绪";
  byId("error").textContent = String(error);
}
