"use client";

import { useState } from "react";
import { api } from "@/lib/api";
import type {
  AdjustmentInput,
  ExpenseAdjustment,
  VersionDetail,
} from "@/lib/types";
const field =
  "mt-1 w-full rounded-lg border border-slate-200 bg-white px-3 py-2 text-xs";
const button =
  "rounded-lg border border-slate-200 px-3 py-1.5 text-xs text-teal-700 disabled:opacity-40";

export function ExpenseReturns({
  detail,
  disabled,
  onCommit,
}: {
  detail: VersionDetail;
  disabled: boolean;
  onCommit: (work: () => Promise<VersionDetail>) => Promise<boolean>;
}) {
  const [draft, setDraft] = useState<AdjustmentInput | null>(null),
    [editing, setEditing] = useState<string | null>(null);
  const [requestId, setRequestId] = useState(""),
    [error, setError] = useState(""),
    [removing, setRemoving] = useState<string | null>(null);
  const expenses = detail.itinerary?.expenses ?? [],
    entries = detail.itinerary?.adjustments ?? [];
  function start(entry?: ExpenseAdjustment) {
    setEditing(entry?.id ?? null);
    setError("");
    setRemoving(null);
    setRequestId(crypto.randomUUID());
    setDraft(
      entry
        ? {
            expense_id: entry.expense_id,
            kind: entry.kind,
            amount: (entry.amount_cents / 100).toFixed(2),
            status: entry.status,
            notes: entry.notes,
          }
        : {
            expense_id: expenses[0]?.id ?? "",
            kind: "refund",
            amount: "",
            status: "received",
            notes: "",
          },
    );
  }
  async function save() {
    if (!draft) return;
    if (
      !draft.expense_id ||
      !/^(?:0|[1-9]\d{0,7})(?:\.\d{1,2})?$/.test(draft.amount) ||
      Number(draft.amount) <= 0
    ) {
      setError("请选择原始消费，并填写大于0且最多两位小数的回款总额。");
      return;
    }
    if (
      await onCommit(() =>
        editing
          ? api.editAdjustment(detail.version.id, editing, draft)
          : api.addAdjustment(detail.version.id, draft, requestId),
      )
    ) {
      setDraft(null);
      setEditing(null);
    }
  }
  return (
    <section
      aria-label="退款与报销"
      className="mt-4 border-t border-slate-200 pt-4"
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h4 className="text-sm font-semibold text-slate-700">退款与报销</h4>
        <button
          type="button"
          className={button}
          disabled={disabled || expenses.length === 0}
          onClick={() => start()}
        >
          记回款
        </button>
      </div>
      <p className="mt-1 text-[11px] leading-5 text-slate-400">
        可以说「门票退款到账20元」。待到账单列；此处记录账本，实际申请和资金在原平台处理。
      </p>
      {draft && (
        <form
          aria-label="回款记录"
          className="mt-3 rounded-xl bg-white p-4"
          onSubmit={(e) => {
            e.preventDefault();
            void save();
          }}
        >
          <fieldset
            disabled={disabled}
            className="space-y-3 disabled:opacity-60"
          >
            <label className="block text-xs text-slate-600">
              对应原始消费
              <select
                aria-label="对应原始消费"
                className={field}
                value={draft.expense_id}
                onChange={(e) =>
                  setDraft({ ...draft, expense_id: e.target.value })
                }
              >
                {expenses.map((e) => (
                  <option key={e.id} value={e.id}>
                    {e.day ? `第${e.day}天 · ` : ""}
                    {e.title} · ¥{(e.amount_cents / 100).toFixed(2)}
                  </option>
                ))}
              </select>
            </label>
            <div className="grid grid-cols-2 gap-3">
              <label className="text-xs text-slate-600">
                回款类别
                <select
                  aria-label="回款类别"
                  className={field}
                  value={draft.kind}
                  onChange={(e) =>
                    setDraft({
                      ...draft,
                      kind: e.target.value as AdjustmentInput["kind"],
                    })
                  }
                >
                  <option value="refund">退款</option>
                  <option value="reimbursement">报销</option>
                </select>
              </label>
              <label className="text-xs text-slate-600">
                到账状态
                <select
                  aria-label="到账状态"
                  className={field}
                  value={draft.status}
                  onChange={(e) =>
                    setDraft({
                      ...draft,
                      status: e.target.value as AdjustmentInput["status"],
                    })
                  }
                >
                  <option value="received">已到账</option>
                  <option value="pending">待到账</option>
                </select>
              </label>
            </div>
            <label className="block text-xs text-slate-600">
              回款金额（元，全团）
              <input
                required
                inputMode="decimal"
                className={field}
                value={draft.amount}
                onChange={(e) => setDraft({ ...draft, amount: e.target.value })}
              />
            </label>
            <label className="block text-xs text-slate-600">
              回款备注
              <input
                maxLength={1000}
                className={field}
                value={draft.notes}
                onChange={(e) => setDraft({ ...draft, notes: e.target.value })}
              />
            </label>
            {error && (
              <p role="alert" className="text-xs text-red-600">
                {error}
              </p>
            )}
            <div className="flex gap-2">
              <button
                type="submit"
                className="rounded-lg bg-teal-700 px-3 py-2 text-xs text-white"
              >
                保存回款
              </button>
              <button
                type="button"
                className={button}
                onClick={() => setDraft(null)}
              >
                取消
              </button>
            </div>
          </fieldset>
        </form>
      )}
      {entries.length > 0 && (
        <details className="mt-3">
          <summary className="cursor-pointer text-xs text-slate-600">
            回款明细（{entries.length} 笔）
          </summary>
          <ul
            aria-label="回款明细"
            className="mt-3 max-h-80 space-y-2 overflow-y-auto"
          >
            {[...entries].reverse().map((entry) => {
              const original = expenses.find((e) => e.id === entry.expense_id),
                label = entry.kind === "refund" ? "退款" : "报销";
              return (
                <li key={entry.id} className="rounded-xl bg-white p-3 text-xs">
                  <div className="flex flex-wrap items-start justify-between gap-2">
                    <div className="min-w-0">
                      <p className="break-words font-medium text-slate-700">
                        {original?.title} · {label}
                      </p>
                      <p className="mt-1 text-slate-500">
                        ¥{(entry.amount_cents / 100).toFixed(2)} ·{" "}
                        {entry.status === "received" ? "已到账" : "待到账"}
                      </p>
                    </div>
                    <div className="flex flex-wrap gap-2">
                      {entry.status === "pending" && (
                        <button
                          type="button"
                          className={button}
                          disabled={disabled}
                          onClick={() =>
                            void onCommit(() =>
                              api.editAdjustment(detail.version.id, entry.id, {
                                expense_id: entry.expense_id,
                                kind: entry.kind,
                                amount: (entry.amount_cents / 100).toFixed(2),
                                status: "received",
                                notes: entry.notes,
                              }),
                            )
                          }
                        >
                          标记到账
                        </button>
                      )}
                      <button
                        type="button"
                        className={button}
                        disabled={disabled}
                        onClick={() => start(entry)}
                      >
                        更正回款
                      </button>
                      <button
                        type="button"
                        className={button}
                        disabled={disabled}
                        onClick={() => setRemoving(entry.id)}
                      >
                        移除回款
                      </button>
                    </div>
                  </div>
                  {entry.notes && (
                    <p className="mt-2 break-words text-slate-500">
                      {entry.notes}
                    </p>
                  )}
                  {removing === entry.id && (
                    <div className="mt-2 flex gap-3">
                      <button
                        type="button"
                        disabled={disabled}
                        className="text-red-600"
                        onClick={async () => {
                          if (
                            await onCommit(() =>
                              api.deleteAdjustment(detail.version.id, entry.id),
                            )
                          )
                            setRemoving(null);
                        }}
                      >
                        确认移除回款
                      </button>
                      <button type="button" onClick={() => setRemoving(null)}>
                        保留
                      </button>
                    </div>
                  )}
                </li>
              );
            })}
          </ul>
        </details>
      )}
    </section>
  );
}
