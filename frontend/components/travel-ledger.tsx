"use client";

import { useState } from "react";
import { Plus, ReceiptText, Pencil, Trash2 } from "lucide-react";
import { api } from "@/lib/api";
import { ExpenseReturns } from "@/components/expense-returns";
import { ReceiptPicker, ReceiptGallery } from "@/components/receipt-picker";
import type {
  Expense,
  ExpenseCategory,
  ExpenseInput,
  VersionDetail,
} from "@/lib/types";

const categories: Record<ExpenseCategory, string> = {
  meal: "餐饮",
  transport: "交通",
  lodging: "住宿",
  ticket: "门票",
  shopping: "购物",
  other: "其他",
};
const field =
  "w-full rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm";
const money = (cents: number) => `¥${(cents / 100).toFixed(2)}`;

export function TravelLedger({
  detail,
  day,
  disabled,
  onCommit,
}: {
  detail: VersionDetail;
  day: number;
  disabled: boolean;
  onCommit: (work: () => Promise<VersionDetail>) => Promise<boolean>;
}) {
  const [draft, setDraft] = useState<ExpenseInput | null>(null);
  const [editing, setEditing] = useState<string | null>(null);
  const [requestId, setRequestId] = useState("");
  const [removing, setRemoving] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [receiptBusy, setReceiptBusy] = useState(false);
  const itinerary = detail.itinerary;
  if (!itinerary) return null;
  const entries = itinerary.expenses ?? [];
  const actual = entries.reduce(
    (total, entry) => total + entry.amount_cents,
    0,
  );
  const estimated = itinerary.days.reduce(
    (total, item) => total + item.known_cost,
    0,
  );
  const req = detail.requirements;
  const budget =
    req?.budget != null && req.budget_basis === "total"
      ? req.budget
      : req?.budget != null &&
          req.budget_basis === "per_person" &&
          req.travelers
        ? req.budget * req.travelers
        : null;
  const ledger = itinerary.ledger_summary;
  const balance =
    budget === null
      ? null
      : Math.round(budget * 100) - (ledger?.net_expense_cents ?? actual);

  function start(entry?: Expense) {
    setEditing(entry?.id ?? null);
    setError("");
    setRemoving(null);
    setRequestId(
      globalThis.crypto?.randomUUID?.() ??
        `expense-${Date.now()}-${Math.random().toString(36).slice(2)}`,
    );
    setDraft(
      entry
        ? {
            title: entry.title,
            amount: (entry.amount_cents / 100).toFixed(2),
            category: entry.category,
            day: entry.day,
            activity_id: entry.activity_id,
            notes: entry.notes,
            receipt_ids: entry.receipt_ids ?? [],
          }
        : {
            title: "",
            amount: "",
            category: "other",
            day,
            activity_id: null,
            notes: "",
            receipt_ids: [],
          },
    );
  }
  async function save() {
    if (!draft) return;
    if (
      !draft.title.trim() ||
      !/^(?:0|[1-9]\d{0,7})(?:\.\d{1,2})?$/.test(draft.amount) ||
      Number(draft.amount) <= 0
    ) {
      setError("请填写消费内容和大于 0 的金额，金额最多两位小数。");
      return;
    }
    const ok = await onCommit(() =>
      editing
        ? api.editExpense(detail.version.id, editing, draft)
        : api.addExpense(detail.version.id, draft, requestId),
    );
    if (ok) {
      setDraft(null);
      setEditing(null);
    }
  }
  const linkedDay = itinerary.days.find((item) => item.day === draft?.day);
  return (
    <section
      aria-label="旅行记账"
      className="mt-6 rounded-2xl border border-slate-200 bg-slate-50/60 p-4 sm:p-5"
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h3 className="flex items-center gap-2 text-sm font-semibold text-slate-800">
            <ReceiptText className="h-4 w-4 text-teal-600" />
            旅行记账
          </h3>
          <p className="mt-1 text-xs leading-5 text-slate-500">
            可以直接告诉我「午餐花了 86 元」。实际消费按全团总额记录。
          </p>
        </div>
        <button
          type="button"
          disabled={disabled}
          onClick={() => start()}
          className="flex items-center gap-1 rounded-lg bg-teal-700 px-3 py-2 text-xs text-white disabled:opacity-50"
        >
          <Plus className="h-3.5 w-3.5" />
          记一笔
        </button>
      </div>
      <div className="mt-4 grid grid-cols-2 gap-3 sm:grid-cols-3">
        <div className="rounded-xl bg-white p-3">
          <p className="text-[11px] text-slate-500">计划已列 · 含估算</p>
          <p className="mt-1 text-base font-semibold text-slate-700">
            ¥{estimated.toFixed(2)}
          </p>
        </div>
        <div className="rounded-xl bg-white p-3">
          <p className="text-[11px] text-slate-500">
            实际已记 · {entries.length} 笔
          </p>
          <p
            className="mt-1 text-base font-semibold text-teal-700"
            aria-live="polite"
          >
            {money(actual)}
          </p>
        </div>
        <div className="col-span-2 rounded-xl bg-white p-3 sm:col-span-1">
          <p className="text-[11px] text-slate-500">全团预算减已记净消费</p>
          <p
            className={`mt-1 text-base font-semibold ${balance !== null && balance < 0 ? "text-amber-700" : "text-slate-700"}`}
          >
            {balance === null ? "预算口径待明确" : money(balance)}
          </p>
        </div>
      </div>
      {(itinerary.adjustments?.length ?? 0) > 0 && (
        <div
          className="mt-3 grid grid-cols-2 gap-3 text-xs sm:grid-cols-4"
          aria-label="回款汇总"
        >
          {[
            ["退款已到账", ledger.refunded_cents],
            ["报销已到账", ledger.reimbursed_cents],
            ["净消费", ledger.net_expense_cents],
            ["净垫付（全团）", ledger.net_outlay_cents],
          ].map(([label, value]) => (
            <div key={String(label)} className="rounded-xl bg-white p-3">
              <p className="text-slate-500">{label}</p>
              <p className="mt-1 font-semibold text-teal-700">
                {money(Number(value))}
              </p>
            </div>
          ))}
          <p className="col-span-2 text-slate-500 sm:col-span-4">
            待到账退款 {money(ledger.pending_refund_cents)} · 待到账报销{" "}
            {money(ledger.pending_reimbursement_cents)}
            。净消费减已到账报销得到净垫付，报销不重复增加预算余量。
          </p>
          {ledger.over_recovered_cents > 0 && (
            <p
              role="status"
              className="col-span-2 text-amber-700 sm:col-span-4"
            >
              部分消费的已到账回款超出原支出{" "}
              {money(ledger.over_recovered_cents)}，请核对回款记录。
            </p>
          )}
        </div>
      )}
      <p className="mt-2 text-[11px] leading-5 text-slate-400">
        实际消费不与计划估算相加。未记支出尚未扣除；历史版本中的记录随版本保存。
      </p>
      {draft && (
        <form
          aria-label={editing ? "更正消费" : "新增消费"}
          className="mt-4 space-y-3 rounded-xl border border-teal-100 bg-white p-4"
          onSubmit={(event) => {
            event.preventDefault();
            void save();
          }}
        >
          <fieldset
            disabled={disabled || receiptBusy}
            className="space-y-3 disabled:opacity-60"
          >
            <div className="grid gap-3 sm:grid-cols-2">
              <label className="text-xs text-slate-600">
                消费内容
                <input
                  autoFocus
                  required
                  maxLength={100}
                  className={`${field} mt-1`}
                  value={draft.title}
                  onChange={(e) =>
                    setDraft({ ...draft, title: e.target.value })
                  }
                  placeholder="例如：午餐"
                />
              </label>
              <label className="text-xs text-slate-600">
                实际金额（元，全团）
                <input
                  required
                  inputMode="decimal"
                  className={`${field} mt-1`}
                  value={draft.amount}
                  onChange={(e) =>
                    setDraft({ ...draft, amount: e.target.value })
                  }
                  placeholder="86.00"
                />
              </label>
            </div>
            <ReceiptPicker
              versionId={detail.version.id}
              ids={draft.receipt_ids ?? []}
              disabled={disabled}
              onBusy={setReceiptBusy}
              onChange={(ids) =>
                setDraft((previous) =>
                  previous ? { ...previous, receipt_ids: ids } : previous,
                )
              }
              onDraft={(result) =>
                setDraft((previous) =>
                  previous
                    ? {
                        ...previous,
                        title: result.title || previous.title,
                        amount: result.amount ?? previous.amount,
                        category:
                          result.kind === "expense"
                            ? result.category
                            : previous.category,
                      }
                    : previous,
                )
              }
            />
            {(draft.receipt_ids?.length ?? 0) > 0 && (
              <label className="flex gap-2 text-[11px] text-slate-500">
                <input
                  type="checkbox"
                  checked={draft.allow_shared_receipts ?? false}
                  onChange={(e) =>
                    setDraft({
                      ...draft,
                      allow_shared_receipts: e.target.checked,
                    })
                  }
                />
                同一张凭证包含不同消费，允许分项记账
              </label>
            )}
            <div className="grid grid-cols-2 gap-3">
              <label className="text-xs text-slate-600">
                所属日期
                <select
                  className={`${field} mt-1`}
                  value={draft.day ?? ""}
                  onChange={(e) =>
                    setDraft({
                      ...draft,
                      day: e.target.value ? Number(e.target.value) : null,
                      activity_id: null,
                    })
                  }
                >
                  <option value="">暂不关联</option>
                  {itinerary.days.map((d) => (
                    <option key={d.day} value={d.day}>
                      第 {d.day} 天
                    </option>
                  ))}
                </select>
              </label>
              <label className="text-xs text-slate-600">
                消费类别
                <select
                  className={`${field} mt-1`}
                  value={draft.category}
                  onChange={(e) =>
                    setDraft({
                      ...draft,
                      category: e.target.value as ExpenseCategory,
                    })
                  }
                >
                  {Object.entries(categories).map(([id, label]) => (
                    <option key={id} value={id}>
                      {label}
                    </option>
                  ))}
                </select>
              </label>
            </div>
            <details>
              <summary className="cursor-pointer text-xs text-slate-500">
                关联停留点与备注
              </summary>
              <div className="mt-3 space-y-3">
                <label className="block text-xs text-slate-600">
                  关联活动
                  <select
                    className={`${field} mt-1`}
                    value={draft.activity_id ?? ""}
                    onChange={(e) =>
                      setDraft({
                        ...draft,
                        activity_id: e.target.value || null,
                      })
                    }
                  >
                    <option value="">不关联活动</option>
                    {linkedDay?.activities.map((a) => (
                      <option key={a.id} value={a.id}>
                        {a.title}
                      </option>
                    ))}
                  </select>
                </label>
                <label className="block text-xs text-slate-600">
                  消费备注
                  <input
                    maxLength={1000}
                    className={`${field} mt-1`}
                    value={draft.notes}
                    onChange={(e) =>
                      setDraft({ ...draft, notes: e.target.value })
                    }
                  />
                </label>
              </div>
            </details>
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
                {editing ? "保存更正" : "保存消费"}
              </button>
              <button
                type="button"
                onClick={() => setDraft(null)}
                className="rounded-lg border border-slate-200 px-3 py-2 text-xs text-slate-500"
              >
                取消
              </button>
            </div>
          </fieldset>
        </form>
      )}
      {entries.length === 0 ? (
        <p className="mt-4 text-xs text-slate-400">
          还没有消费记录，旅途中的每一笔都可以在这里留下。
        </p>
      ) : (
        <details className="mt-4">
          <summary className="cursor-pointer text-xs font-medium text-slate-600">
            消费明细（{entries.length} 笔）
          </summary>
          <ul
            className="mt-3 max-h-80 space-y-2 overflow-y-auto"
            aria-label="消费明细"
          >
            {[...entries].reverse().map((entry) => (
              <li key={entry.id} className="rounded-xl bg-white p-3">
                <div className="flex items-start gap-2">
                  <div className="min-w-0 flex-1">
                    <p className="break-words text-sm text-slate-700">
                      {entry.title}
                    </p>
                    <p className="mt-1 text-[11px] text-slate-400">
                      {entry.day ? `第 ${entry.day} 天` : "未关联日期"} ·{" "}
                      {categories[entry.category]}
                    </p>
                  </div>
                  <span className="shrink-0 text-sm font-semibold text-slate-700">
                    {money(entry.amount_cents)}
                  </span>
                  <button
                    type="button"
                    disabled={disabled}
                    aria-label={`更正${entry.title}`}
                    onClick={() => start(entry)}
                    className="p-1 text-slate-400 disabled:opacity-40"
                  >
                    <Pencil className="h-3.5 w-3.5" />
                  </button>
                  <button
                    type="button"
                    disabled={disabled}
                    aria-label={`移除${entry.title}`}
                    onClick={() => setRemoving(entry.id)}
                    className="p-1 text-slate-400 disabled:opacity-40"
                  >
                    <Trash2 className="h-3.5 w-3.5" />
                  </button>
                </div>
                {entry.notes && (
                  <p className="mt-1 break-words text-xs text-slate-500">
                    {entry.notes}
                  </p>
                )}
                {(entry.receipt_ids?.length ?? 0) > 0 && (
                  <details className="mt-2">
                    <summary className="cursor-pointer text-xs text-teal-700">
                      图片凭证（{entry.receipt_ids.length}张）
                    </summary>
                    <ReceiptGallery
                      versionId={detail.version.id}
                      ids={entry.receipt_ids}
                    />
                  </details>
                )}
                {removing === entry.id && (
                  <div className="mt-2 flex flex-wrap items-center gap-2 text-xs">
                    <span className="text-slate-500">
                      移除这笔误记？历史版本仍会保留。
                    </span>
                    <button
                      type="button"
                      disabled={disabled}
                      className="text-red-600"
                      onClick={async () => {
                        if (
                          await onCommit(() =>
                            api.deleteExpense(detail.version.id, entry.id),
                          )
                        )
                          setRemoving(null);
                      }}
                    >
                      确认移除
                    </button>
                    <button
                      type="button"
                      onClick={() => setRemoving(null)}
                      className="text-slate-500"
                    >
                      保留
                    </button>
                  </div>
                )}
              </li>
            ))}
          </ul>
        </details>
      )}
      <ExpenseReturns detail={detail} disabled={disabled} onCommit={onCommit} />
    </section>
  );
}
