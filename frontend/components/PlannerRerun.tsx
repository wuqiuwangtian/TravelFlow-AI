"use client";

import { useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import type {
  DayPlan,
  PlannerPreview,
  PlannerRerunInput,
  VersionDetail,
} from "@/lib/types";

const money = (cents: number) => `¥${(cents / 100).toFixed(2)}`;
const message = (error: unknown) =>
  error instanceof Error ? error.message : "操作失败，请重试。";

export default function PlannerRerun({
  branchId,
  versionId,
  onApplied,
}: {
  branchId: string;
  versionId: string;
  onApplied: () => void;
}) {
  const [detail, setDetail] = useState<VersionDetail | null>(null);
  const [preview, setPreview] = useState<PlannerPreview | null>(null);
  const [day, setDay] = useState(1);
  const [mode, setMode] = useState<PlannerRerunInput["travel_mode"]>("auto");
  const [instruction, setInstruction] =
    useState("减少折返，优先安排顺路的地点");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [refresh, setRefresh] = useState(0);
  const retry = useRef<{ key: string; id: string } | null>(null);
  const alive = useRef(true);
  useEffect(() => {
    alive.current = true;
    let cancelled = false;
    Promise.all([
      api.getVersion(versionId),
      api.currentPlannerPreview(branchId),
    ])
      .then(([result, saved]) => {
        if (cancelled) return;
        setDetail(result);
        setPreview(saved);
        setDay(saved?.parameters.day ?? result.itinerary?.days[0]?.day ?? 1);
        if (saved) {
          setMode(saved.parameters.travel_mode);
          setInstruction(saved.parameters.instruction);
        }
        setError("");
      })
      .catch((err) => {
        if (!cancelled) setError(message(err));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
      alive.current = false;
    };
  }, [branchId, versionId, refresh]);

  async function generate() {
    const key = JSON.stringify([versionId, day, mode, instruction.trim()]);
    if (retry.current?.key !== key)
      retry.current = { key, id: crypto.randomUUID() };
    setBusy("generate");
    setError("");
    try {
      const result = await api.plannerPreview(versionId, {
        request_id: retry.current.id,
        day,
        travel_mode: mode,
        instruction: instruction.trim(),
      });
      if (alive.current) setPreview(result);
    } catch (err) {
      if (alive.current) setError(message(err));
    } finally {
      if (alive.current) setBusy("");
    }
  }
  async function apply() {
    if (!preview) return;
    setBusy("apply");
    setError("");
    try {
      await api.applyPlannerPreview(branchId, preview.request_id);
      if (alive.current) onApplied();
    } catch (err) {
      if (alive.current) setError(message(err));
    } finally {
      if (alive.current) setBusy("");
    }
  }
  async function discard() {
    if (!preview) return;
    setBusy("discard");
    setError("");
    try {
      await api.discardPlannerPreview(branchId, preview.request_id);
      if (alive.current) {
        setPreview(null);
        retry.current = null;
      }
    } catch (err) {
      if (alive.current) setError(message(err));
    } finally {
      if (alive.current) setBusy("");
    }
  }
  const changed =
    preview &&
    (preview.parameters.day !== day ||
      preview.parameters.travel_mode !== mode ||
      preview.parameters.instruction !== instruction.trim());
  const ended = !!detail?.itinerary?.ended_at;
  const unavailable = loading || !detail?.itinerary?.days.length || ended;
  function route(dayPlan: DayPlan, after: boolean) {
    return (
      <ol className="mt-3 space-y-2 text-sm">
        {dayPlan.activities.map((activity, index) => (
          <li key={activity.id} className="rounded-lg bg-slate-50 p-3">
            <span className="text-slate-400">{index + 1}. </span>
            {activity.start_day_offset
              ? `+${activity.start_day_offset}天 `
              : ""}
            {activity.start_time ?? "时间待核实"} ·{" "}
            {activity.place_name || activity.title}
            {after && preview?.protected_ids.includes(activity.id) && (
              <span className="ml-2 text-xs text-teal-700">固定保留</span>
            )}
          </li>
        ))}
      </ol>
    );
  }
  return (
    <section
      aria-label="局部重跑"
      className="mt-6 rounded-2xl border border-teal-200 bg-white p-5 sm:p-7"
    >
      <h2 className="font-semibold text-teal-950">单日路线优化</h2>
      <p className="mt-2 text-sm leading-7 text-slate-500">
        保留全部地点，只优化所选一天的活动顺序与市内默认交通。已执行活动、预约、固定时刻、车站与酒店保持位置；其他天、人工费用、消费和回顾保留。增删地点或调整整趟旅行，可回原对话直接提出。
      </p>
      {loading && (
        <p role="status" className="mt-3 text-sm">
          正在读取可调整的行程…
        </p>
      )}
      {ended && (
        <p className="mt-3 text-sm text-amber-800">
          旅行已结束，请先在原方案重新开启旅行。
        </p>
      )}
      {error && (
        <p role="alert" className="mt-3 text-sm text-red-700">
          {error}
        </p>
      )}
      {!loading && !detail && (
        <button
          className="mt-3 text-sm text-teal-700"
          onClick={() => {
            setLoading(true);
            setRefresh(refresh + 1);
          }}
        >
          重试载入
        </button>
      )}
      {detail && (
        <fieldset
          disabled={!!busy || unavailable}
          className="mt-5 space-y-4 disabled:opacity-60"
        >
          <div className="grid gap-4 sm:grid-cols-2">
            <label className="text-sm">
              优化日期
              <select
                aria-label="优化日期"
                value={day}
                onChange={(e) => setDay(Number(e.target.value))}
                className="mt-2 w-full rounded-lg border border-slate-200 p-2"
              >
                {detail.itinerary?.days.map((item) => (
                  <option key={item.day} value={item.day}>
                    第{item.day}天 · {item.title}
                  </option>
                ))}
              </select>
            </label>
            <label className="text-sm">
              市内默认交通
              <select
                aria-label="市内默认交通"
                value={mode}
                onChange={(e) =>
                  setMode(e.target.value as PlannerRerunInput["travel_mode"])
                }
                className="mt-2 w-full rounded-lg border border-slate-200 p-2"
              >
                <option value="auto">智能选择</option>
                <option value="walking">步行</option>
                <option value="transit">公共交通</option>
                <option value="driving">驾车／打车</option>
              </select>
            </label>
          </div>
          <p className="text-xs text-slate-500">
            单独指定的交通、铁路和航班仍沿用原安排。路线不可查或时刻冲突会在预览中提示。
          </p>
          <label className="block text-sm">
            优化要求
            <textarea
              aria-label="优化要求"
              maxLength={1000}
              value={instruction}
              onChange={(e) => setInstruction(e.target.value)}
              className="mt-2 min-h-20 w-full rounded-lg border border-slate-200 p-3"
            />
          </label>
          <button
            disabled={!instruction.trim()}
            onClick={generate}
            className="rounded-lg bg-teal-700 px-4 py-2 text-sm text-white disabled:opacity-50"
          >
            {busy === "generate" ? "正在生成预览…" : "生成优化预览"}
          </button>
        </fieldset>
      )}
      {preview && (
        <section
          aria-label="路线优化预览"
          className="mt-6 border-t border-slate-100 pt-5"
        >
          <h3 className="font-medium">第{preview.after.day}天 · 应用前预览</h3>
          <p className="mt-2 text-xs leading-6 text-slate-500">
            建议时间与路线可能尚待核实。其他天及实际账目保持原样；这里只保存预览，应用后才新增正式版本。刷新可继续查看未过期预览。
          </p>
          <div className="mt-4 grid min-w-0 gap-5 md:grid-cols-2">
            <div>
              <h4 className="text-sm font-medium">原路线</h4>
              {route(preview.before, false)}
            </div>
            <div>
              <h4 className="text-sm font-medium">建议路线</h4>
              {route(preview.after, true)}
            </div>
          </div>
          <p className="mt-5 text-sm">
            整趟预计支出：{money(preview.budget_before.projected_cents)} →{" "}
            {money(preview.budget_after.projected_cents)}；待核实费用：
            {preview.budget_after.unknown_count}项。实际记账保持不变。
          </p>
          {!!preview.after.warnings.length && (
            <ul
              aria-label="路线校验提示"
              className="mt-3 space-y-1 text-sm text-amber-800"
            >
              {[...new Set(preview.after.warnings)].map((warning) => (
                <li key={warning}>{warning}</li>
              ))}
            </ul>
          )}
          {changed && (
            <p className="mt-3 text-sm text-amber-800">
              参数已更改，请重新生成预览后再应用。
            </p>
          )}
          {!!preview.conflicts.length && (
            <div
              role="alert"
              className="mt-4 rounded-lg bg-red-50 p-4 text-sm text-red-800"
            >
              <p className="font-medium">
                存在固定安排或时间衔接冲突，暂不能应用。
              </p>
              <ul className="mt-2 space-y-1">
                {preview.conflicts.map((conflict) => (
                  <li key={conflict}>{conflict}</li>
                ))}
              </ul>
            </div>
          )}
          <div className="mt-5 flex flex-wrap gap-4">
            <button
              disabled={!!busy || !!changed || !!preview.conflicts.length}
              onClick={apply}
              className="rounded-lg bg-teal-700 px-4 py-2 text-sm text-white disabled:opacity-50"
            >
              {busy === "apply" ? "正在应用…" : "应用本次优化"}
            </button>
            <button
              disabled={!!busy}
              onClick={discard}
              className="text-sm text-slate-600"
            >
              丢弃预览
            </button>
          </div>
        </section>
      )}
    </section>
  );
}
