"use client";

import { useEffect, useState } from "react";
import { api, API_BASE } from "@/lib/api";
import type { VersionDetail, RecapView } from "@/lib/types";

export function TravelRecap({
  detail,
  disabled,
  onCommit,
}: {
  detail: VersionDetail;
  disabled: boolean;
  onCommit: (work: () => Promise<VersionDetail>) => Promise<boolean>;
}) {
  const [view, setView] = useState<RecapView | null>(null);
  const [title, setTitle] = useState("");
  const [body, setBody] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [dirty, setDirty] = useState(false);
  useEffect(() => {
    if (!detail.itinerary?.recap) return;
    let cancelled = false;
    api
      .getRecap(detail.version.id)
      .then((result) => {
        if (!cancelled) {
          setView(result);
          setTitle(result.title);
          setBody(result.body);
        }
      })
      .catch((err) => {
        if (!cancelled)
          setError(err instanceof Error ? err.message : "回顾加载失败");
      });
    return () => {
      cancelled = true;
    };
  }, [detail.version.id, detail.itinerary?.recap]);
  async function open() {
    setBusy(true);
    setError("");
    try {
      const result = await api.getRecap(detail.version.id);
      setView(result);
      setTitle(result.title);
      setBody(result.body);
      setDirty(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : "回顾加载失败，请重试。");
    } finally {
      setBusy(false);
    }
  }
  const field =
    "mt-1 w-full rounded-lg border border-teal-100 bg-white p-3 text-sm text-slate-700";
  return (
    <section
      aria-label="旅行回顾"
      className="mt-5 rounded-2xl border border-teal-100 bg-teal-50/40 p-4 sm:p-5"
    >
      <h3 className="font-semibold text-teal-900">把旅程留下来</h3>
      <p className="mt-2 text-xs leading-6 text-slate-600">
        根据已完成活动和已记支出整理回顾，没确认去过的地方会单独列出。回忆可以补写，原行程和账目保留。
      </p>
      {!view && (
        <button
          type="button"
          disabled={busy}
          onClick={() => void open()}
          className="mt-3 rounded-lg bg-teal-700 px-4 py-2 text-sm text-white disabled:opacity-50"
        >
          {busy ? "整理中…" : "打开旅行回顾"}
        </button>
      )}
      {error && (
        <p role="alert" className="mt-3 text-sm text-red-700">
          {error}
        </p>
      )}
      {view && (
        <div className="mt-4 space-y-4">
          {view.stale && (
            <p
              role="status"
              className="rounded-lg bg-amber-50 p-3 text-xs text-amber-800"
            >
              行程或账目已有变化。事实汇总已更新，请核对原回忆文字后保存。
            </p>
          )}
          <div
            aria-label="回顾事实汇总"
            className="space-y-3 rounded-xl bg-white p-4 text-xs leading-6 text-slate-600"
          >
            {view.days.map((day) => (
              <div key={day.day}>
                <p className="font-semibold text-teal-800">第{day.day}天</p>
                <p>已完成：{day.completed.join("、") || "无记录"}</p>
                <p>已跳过：{day.skipped.join("、") || "无记录"}</p>
                <p>尚未确认到访：{day.unconfirmed.join("、") || "无记录"}</p>
              </div>
            ))}
            <p className="font-semibold text-teal-800">
              已记净消费 ¥{(view.ledger.net_expense_cents / 100).toFixed(2)} ·{" "}
              {view.expense_count}笔消费
            </p>
            <p>
              已到账退款 ¥{(view.ledger.refunded_cents / 100).toFixed(2)} ·
              已到账报销 ¥{(view.ledger.reimbursed_cents / 100).toFixed(2)}
            </p>
            <p>
              待到账退款 ¥{(view.ledger.pending_refund_cents / 100).toFixed(2)}{" "}
              · 待到账报销 ¥
              {(view.ledger.pending_reimbursement_cents / 100).toFixed(2)}
            </p>
            <p>
              还有{view.unsettled_count}项未结算、{view.unknown_count}
              项未定价。这里只汇总已记录支出，不代表全部花费；预估不算实际消费。
            </p>
          </div>
          <form
            aria-label="编辑旅行回顾"
            onSubmit={(event) => {
              event.preventDefault();
              void onCommit(() =>
                api.saveRecap(detail.version.id, {
                  title,
                  body,
                  source_fingerprint: view.source_fingerprint,
                }),
              );
            }}
          >
            <fieldset
              disabled={disabled || busy}
              className="space-y-3 disabled:opacity-60"
            >
              <label className="block text-xs text-slate-600">
                回顾标题
                <input
                  required
                  maxLength={100}
                  className={field}
                  value={title}
                  onChange={(e) => {
                    setTitle(e.target.value);
                    setDirty(true);
                  }}
                />
              </label>
              <label className="block text-xs text-slate-600">
                我的回忆
                <textarea
                  rows={8}
                  maxLength={20000}
                  className={field}
                  value={body}
                  onChange={(e) => {
                    setBody(e.target.value);
                    setDirty(true);
                  }}
                />
              </label>
              <button
                type="submit"
                className="rounded-lg bg-teal-700 px-4 py-2 text-sm text-white"
              >
                保存旅行回顾
              </button>
            </fieldset>
          </form>
          <p className="text-xs text-slate-500">
            {dirty
              ? "文字尚未保存，导出使用已保存内容；请先保存。"
              : view.saved
                ? "回顾已随当前版本保存。"
                : "当前为事实模板，保存后可在版本历史中查看。"}{" "}
            导出不附带票据图片、消费备注和订单资料；自己补写的文字会包含在导出中。
          </p>
          <div className="flex flex-wrap gap-3 text-xs text-teal-800">
            {(["markdown", "html"] as const).map((format) => (
              <a
                key={format}
                href={`${API_BASE}/api/versions/${detail.version.id}/recap/export?format=${format}`}
                download
                className="rounded-lg border border-teal-100 bg-white px-3 py-2"
              >
                导出回顾 {format === "html" ? "HTML" : "Markdown"}
              </a>
            ))}
          </div>
        </div>
      )}
    </section>
  );
}
