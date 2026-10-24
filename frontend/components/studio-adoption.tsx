"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { createPortal } from "react-dom";
import { api } from "@/lib/api";
import type {
  AdoptionContext,
  AdoptionPreview,
  StudioRun,
} from "@/lib/studio-types";
import type { TravelRequirements, VersionDetail } from "@/lib/types";

const LABELS = {
  added: "新增",
  removed: "移除未来安排",
  updated: "调整",
  preserved: "保护保留",
  unchanged: "沿用",
};
const money = (value: number) => `¥${(value / 100).toFixed(2)}`;
const errorMessage = (error: unknown) =>
  error instanceof Error ? error.message : "操作失败，请重试。";
function requirementText(value: TravelRequirements) {
  return `${value.origin || "出发地待定"} → ${value.destination || "目的地待定"} · ${value.duration_days ?? "?"}天 · ${value.travelers ?? "?"}人 · 预算${value.budget ?? "待定"}元${value.budget_basis === "total" ? "（全团）" : value.budget_basis === "per_person" ? "（人均）" : "（口径待定）"}${value.departure_date ? ` · ${value.departure_date}` : ""} · 偏好：${value.preferences.join("、") || "暂无特别要求"}`;
}

export default function StudioAdoption({
  sessionId,
  branchId,
  run,
  nodeId,
}: {
  sessionId: string;
  branchId: string;
  run: StudioRun;
  nodeId: string;
}) {
  const [context, setContext] = useState<AdoptionContext | null>(null);
  const [preview, setPreview] = useState<AdoptionPreview | null>(null);
  const [applied, setApplied] = useState<VersionDetail | null>(null);
  const [busy, setBusy] = useState("load");
  const [error, setError] = useState("");
  const [refresh, setRefresh] = useState(0);
  const [review, setReview] = useState(false);
  const dialog = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const element = dialog.current;
    if (review && preview && element && !element.open) element.showModal();
    return () => {
      if (element?.open) element.close();
    };
  }, [review, preview]);
  const alive = useRef(true);
  const retry = useRef<{ key: string; id: string } | null>(null);
  useEffect(() => {
    alive.current = true;
    let cancelled = false;
    api
      .adoptionContext(branchId, run.id, nodeId)
      .then((result) => {
        if (!cancelled) {
          setContext(result);
          setPreview(result.preview);
          setError("");
        }
      })
      .catch((err) => {
        if (!cancelled) setError(errorMessage(err));
      })
      .finally(() => {
        if (!cancelled) setBusy("");
      });
    return () => {
      cancelled = true;
      alive.current = false;
    };
  }, [branchId, run.id, nodeId, refresh]);
  function reload() {
    setBusy("load");
    setError("");
    retry.current = null;
    setRefresh((v) => v + 1);
  }
  async function generate() {
    if (!context) return;
    const key = JSON.stringify([context.base_key, context.source_key]);
    if (retry.current?.key !== key)
      retry.current = { key, id: crypto.randomUUID() };
    setBusy("generate");
    setError("");
    try {
      const result = await api.previewAdoption(branchId, run.id, nodeId, {
        request_id: retry.current.id,
        base_key: context.base_key,
        source_key: context.source_key,
      });
      if (alive.current) {
        setPreview(result);
        setReview(true);
        retry.current = null;
      }
    } catch (err) {
      if (alive.current) setError(errorMessage(err));
    } finally {
      if (alive.current) setBusy("");
    }
  }
  async function discard() {
    if (!preview) return;
    setBusy("discard");
    setError("");
    try {
      await api.discardAdoption(branchId, run.id, nodeId, preview.request_id);
      if (alive.current) {
        setPreview(null);
        retry.current = null;
      }
    } catch (err) {
      if (alive.current) setError(errorMessage(err));
    } finally {
      if (alive.current) setBusy("");
    }
  }
  async function apply() {
    if (!preview) return;
    setBusy("apply");
    setError("");
    try {
      const result = await api.applyAdoption(
        branchId,
        run.id,
        nodeId,
        preview.request_id,
      );
      if (alive.current) {
        setApplied(result);
        setPreview(null);
      }
    } catch (err) {
      if (alive.current) setError(errorMessage(err));
    } finally {
      if (alive.current) setBusy("");
    }
  }
  return (
    <section
      aria-label="工程成稿采用"
      className="mt-5 border-t border-teal-100 pt-4 text-xs"
    >
      <h3 className="font-semibold text-teal-900">采用到正式行程</h3>
      <p className="mt-2 leading-6 text-slate-500">
        来源：工程修订{run.revision} ·{" "}
        {new Date(run.created_at).toLocaleString("zh-CN")}
        。整理当前所选运行的成稿，先核对每日安排、变化和保留项，再确认采用。当前工程参数另有修改时，此处仍使用该次运行的固定成稿。
      </p>
      {applied ? (
        <div
          role="status"
          className="mt-3 rounded-lg bg-teal-50 p-3 leading-6 text-teal-900"
        >
          <p>
            成稿已采用为正式行程第{applied.version.seq}版，原实际记录继续保留。
          </p>
          <Link
            href={`/chat/${sessionId}?branch=${branchId}`}
            className="mt-2 inline-block font-medium underline"
          >
            查看正式行程
          </Link>
        </div>
      ) : (
        <>
          {error && (
            <p role="alert" className="mt-3 leading-6 text-red-700">
              {error}
            </p>
          )}
          {context?.blocker && (
            <p className="mt-3 rounded-lg bg-amber-50 p-3 leading-6 text-amber-800">
              {context.blocker}
            </p>
          )}
          <div className="mt-3 flex flex-wrap gap-3">
            <button
              disabled={!!busy || !context || !!context.blocker}
              onClick={generate}
              className="rounded-lg bg-teal-700 px-3 py-2 text-white disabled:opacity-40"
            >
              {busy === "generate"
                ? "正在整理采用预览…"
                : preview
                  ? "重新整理采用预览"
                  : "生成采用预览"}
            </button>
            <button
              disabled={!!busy}
              onClick={reload}
              className="text-teal-700 disabled:opacity-40"
            >
              重新载入采用信息
            </button>
          </div>
          {busy === "load" && (
            <p role="status" className="mt-3 text-slate-500">
              正在核对正式行程与已保存预览…
            </p>
          )}
          {preview && (
            <div className="mt-3 rounded-lg bg-teal-50 p-3 leading-6 text-teal-900">
              <p>
                已保存{preview.itinerary.days.length}天采用预览 ·{" "}
                {preview.conflicts.length
                  ? `${preview.conflicts.length}项冲突待处理`
                  : "待确认"}
              </p>
              <button
                onClick={() => setReview(true)}
                className="mt-2 font-medium underline"
              >
                查看采用预览
              </button>
            </div>
          )}
          {preview &&
            review &&
            createPortal(
              <dialog
                ref={dialog}
                aria-label="成稿采用核对"
                onCancel={() => setReview(false)}
                className="fixed inset-0 m-auto max-h-[92dvh] w-[calc(100%-24px)] max-w-6xl overflow-auto rounded-2xl border border-slate-200 bg-white p-0 text-sm shadow-2xl backdrop:bg-slate-950/60"
              >
                <header className="sticky top-0 z-10 flex items-center justify-between gap-4 border-b border-slate-100 bg-white p-4">
                  <div>
                    <h2 className="font-semibold text-teal-950">
                      核对成稿与正式行程
                    </h2>
                    <p className="mt-1 text-xs text-slate-500">
                      {preview.source_title} · 工程修订{preview.source_revision}{" "}
                      ·{" "}
                      {new Date(preview.source_created_at).toLocaleString(
                        "zh-CN",
                      )}
                    </p>
                  </div>
                  <button
                    aria-label="关闭采用核对"
                    onClick={() => setReview(false)}
                    className="rounded-lg border border-slate-200 px-3 py-2 text-xs"
                  >
                    返回工程
                  </button>
                </header>
                <section
                  aria-label="正式行程采用预览"
                  className="space-y-5 bg-slate-50 p-4 sm:p-6"
                >
                  {error && (
                    <div
                      role="alert"
                      className="rounded-lg bg-red-50 p-3 leading-6 text-red-800"
                    >
                      {error}
                      <button
                        className="ml-3 underline"
                        onClick={() => {
                          setReview(false);
                          setPreview(null);
                          reload();
                        }}
                      >
                        载入最新采用信息
                      </button>
                    </div>
                  )}
                  <div>
                    <h4 className="font-semibold text-slate-800">
                      {preview.itinerary.days.length}天行程 · 待确认采用
                    </h4>
                    <p className="mt-2 leading-6 text-slate-500">
                      预览已保存，刷新可继续查看；核对后采用才会创建正式版本。原计划与历史版本保留。
                    </p>
                  </div>
                  <div className="space-y-2 leading-6">
                    <p>
                      原需求：{requirementText(preview.before_requirements)}
                    </p>
                    <p className="text-teal-900">
                      采用后：{requirementText(preview.requirements)}
                    </p>
                  </div>
                  <div className="rounded-lg bg-white p-3 leading-6">
                    <p>
                      整趟预计支出：
                      {preview.budget_before
                        ? money(preview.budget_before.projected_cents)
                        : "暂无原预算"}{" "}
                      → {money(preview.budget_after.projected_cents)}
                    </p>
                    <p>
                      待核实费用：{preview.budget_after.unknown_count}
                      项；未知与未列费用不等于零。
                    </p>
                    <p className="mt-2">
                      {Object.entries(LABELS)
                        .map(
                          ([key, title]) =>
                            `${title}${preview.changes.filter((c) => c.status === key).length}项`,
                        )
                        .join(" · ")}
                    </p>
                  </div>
                  {!!preview.retained.length && (
                    <div>
                      <h4 className="font-semibold">保留的原记录</h4>
                      <ul className="mt-2 space-y-2 leading-6 text-teal-900">
                        {preview.retained.map((item) => (
                          <li key={item}>{item}</li>
                        ))}
                      </ul>
                    </div>
                  )}
                  <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
                    {preview.itinerary.days.map((day) => (
                      <details
                        key={day.day}
                        open
                        className="rounded-lg bg-white p-3"
                      >
                        <summary className="cursor-pointer font-semibold">
                          第{day.day}天 · {day.title}
                        </summary>
                        <ol className="mt-3 space-y-2 leading-6">
                          {day.activities.map((activity) => {
                            const change = preview.changes.find(
                              (c) => c.activity_id === activity.id,
                            );
                            return (
                              <li
                                key={activity.id}
                                className="border-l-2 border-teal-200 pl-3"
                              >
                                <p>
                                  {activity.start_day_offset
                                    ? `+${activity.start_day_offset}天 `
                                    : ""}
                                  {activity.start_time || "时间待核实"} ·{" "}
                                  {activity.place_name || activity.title}
                                </p>
                                <p className="text-slate-500">
                                  {change ? LABELS[change.status] : ""}
                                  {change?.reason ? ` · ${change.reason}` : ""}
                                </p>
                              </li>
                            );
                          })}
                        </ol>
                      </details>
                    ))}
                  </div>
                  <details>
                    <summary className="cursor-pointer font-semibold">
                      逐项变化与移除安排
                    </summary>
                    <ul className="mt-2 space-y-3 leading-6">
                      {preview.changes
                        .filter((c) => c.status !== "unchanged")
                        .map((c) => (
                          <li key={`${c.status}-${c.activity_id}`}>
                            <p>
                              第{c.day}天 · {LABELS[c.status]} · {c.title}
                            </p>
                            <p className="text-slate-500">
                              {c.before || "无原安排"} →{" "}
                              {c.after || "从未来计划移除"}
                            </p>
                            {c.reason && (
                              <p className="text-teal-800">{c.reason}</p>
                            )}
                          </li>
                        ))}
                    </ul>
                  </details>
                  {!!preview.warnings.length && (
                    <details>
                      <summary className="cursor-pointer text-amber-800">
                        路线与资料提示 · {preview.warnings.length}
                      </summary>
                      <ul className="mt-2 space-y-2 leading-6 text-amber-800">
                        {preview.warnings.map((item) => (
                          <li key={item}>{item}</li>
                        ))}
                      </ul>
                    </details>
                  )}
                  {!!preview.conflicts.length && (
                    <div
                      role="alert"
                      className="rounded-lg bg-red-50 p-3 leading-6 text-red-800"
                    >
                      <p className="font-semibold">存在冲突，暂不能采用。</p>
                      <ul className="mt-2 space-y-2">
                        {preview.conflicts.map((item) => (
                          <li key={item}>{item}</li>
                        ))}
                      </ul>
                      <p className="mt-2">
                        请在工程中明确需要保留的约束或补齐安排，重新运行成稿后再预览。
                      </p>
                    </div>
                  )}
                  <div className="flex flex-wrap gap-3">
                    <button
                      disabled={
                        !!busy ||
                        !!preview.conflicts.length ||
                        !!context?.blocker
                      }
                      onClick={apply}
                      className="rounded-lg bg-teal-700 px-3 py-2 text-white disabled:opacity-40"
                    >
                      {busy === "apply" ? "正在采用…" : "确认采用到正式行程"}
                    </button>
                    <button
                      disabled={!!busy}
                      onClick={discard}
                      className="text-slate-600"
                    >
                      丢弃采用预览
                    </button>
                  </div>
                </section>
              </dialog>,
              document.body,
            )}
        </>
      )}
    </section>
  );
}
