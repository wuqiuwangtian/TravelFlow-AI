"use client";

import { useState } from "react";
import { TripActionFeedback, useTripAction } from "@/components/trip-action";
import { Button } from "@/components/ui";
import { api } from "@/lib/api";
import type {
  AdjustmentInput,
  ExpenseAdjustment,
  VersionDetail,
} from "@/lib/types";
const field =
  "mt-1 w-full rounded-lg border border-[var(--tf-line)] bg-[var(--tf-paper)] px-3 py-2 text-xs";

export function ExpenseReturns({
  detail,
  disabled,
  onCommit,
}: {
  detail: VersionDetail;
  disabled: boolean;
  onCommit: (work: () => Promise<VersionDetail>) => Promise<boolean>;
}) {
  const action = useTripAction(onCommit);
  const [draft, setDraft] = useState<AdjustmentInput | null>(null),
    [editing, setEditing] = useState<string | null>(null);
  const [requestId, setRequestId] = useState(""),
    [error, setError] = useState(""),
    [removing, setRemoving] = useState<string | null>(null);
  const expenses = detail.itinerary?.expenses ?? [],
    entries = detail.itinerary?.adjustments ?? [];
  function start(entry?: ExpenseAdjustment) {
    setEditing(entry?.id ?? null);
    action.clear();
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
      await action.run(
        editing ?? "new",
        "回款记录已保存，净消费与垫付已同步。",
        () =>
          editing
            ? api.editAdjustment(detail.version.id, editing, draft)
            : api.addAdjustment(detail.version.id, draft, requestId),
      )
    ) {
      setDraft(null);
      setEditing(null);
    }
  }
  const editor = draft ? (
    <form
      aria-label="回款记录"
      className="mt-3 rounded-[var(--tf-radius)] bg-[var(--tf-paper)] p-4"
      onSubmit={(e) => {
        e.preventDefault();
        void save();
      }}
    >
      <fieldset disabled={disabled} className="space-y-3 disabled:opacity-60">
        <label className="block text-xs text-[var(--tf-muted)]">
          对应原始消费
          <select
            autoFocus
            aria-label="对应原始消费"
            className={field}
            value={draft.expense_id}
            onChange={(e) => setDraft({ ...draft, expense_id: e.target.value })}
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
          <label className="text-xs text-[var(--tf-muted)]">
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
          <label className="text-xs text-[var(--tf-muted)]">
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
        <label className="block text-xs text-[var(--tf-muted)]">
          回款金额（元，全团）
          <input
            required
            inputMode="decimal"
            className={field}
            value={draft.amount}
            onChange={(e) => setDraft({ ...draft, amount: e.target.value })}
          />
        </label>
        <label className="block text-xs text-[var(--tf-muted)]">
          回款备注
          <input
            maxLength={1000}
            className={field}
            value={draft.notes}
            onChange={(e) => setDraft({ ...draft, notes: e.target.value })}
          />
        </label>
        {error && (
          <p role="alert" className="text-xs text-[var(--tf-danger)]">
            {error}
          </p>
        )}
        <div className="flex gap-2">
          <Button
            variant="primary"
            loading={action.feedback?.pending}
            type="submit"
          >
            保存回款
          </Button>
          <Button
            type="button"
            size="sm"
            onClick={() => {
              action.clear();
              setDraft(null);
              setEditing(null);
            }}
          >
            取消
          </Button>
        </div>
      </fieldset>
    </form>
  ) : null;
  return (
    <section
      aria-label="退款与报销"
      className="trip-ui mt-4 border-t border-[var(--tf-line)] pt-4"
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h4 className="text-sm font-semibold text-[var(--tf-ink)]">
          退款与报销
        </h4>
        <Button
          type="button"
          size="sm"
          disabled={disabled || expenses.length === 0}
          onClick={() => start()}
        >
          记回款
        </Button>
      </div>
      <p className="mt-1 text-[11px] leading-5 text-[var(--tf-muted)]">
        可以说「门票退款到账20元」。待到账单列；此处记录账本，实际申请和资金在原平台处理。
      </p>
      {!editing && editor}
      <TripActionFeedback action={action} target="new" />
      {action.feedback?.message && (
        <TripActionFeedback action={action} target="remove" />
      )}
      {entries.length > 0 && (
        <details className="mt-3">
          <summary className="cursor-pointer text-xs text-[var(--tf-muted)]">
            回款明细（{entries.length} 笔）
          </summary>
          <ul aria-label="回款明细" className="mt-3 space-y-3">
            {[...entries].reverse().map((entry) => {
              const original = expenses.find((e) => e.id === entry.expense_id),
                label = entry.kind === "refund" ? "退款" : "报销";
              return (
                <li
                  key={entry.id}
                  className="rounded-[var(--tf-radius)] bg-[var(--tf-paper)] p-3 text-xs"
                >
                  <div className="flex flex-wrap items-start justify-between gap-2">
                    <div className="min-w-0">
                      <p className="break-words font-medium text-[var(--tf-ink)]">
                        {original?.title} · {label}
                      </p>
                      <p className="mt-1 text-[var(--tf-muted)]">
                        ¥{(entry.amount_cents / 100).toFixed(2)} ·{" "}
                        {entry.status === "received" ? "已到账" : "待到账"}
                      </p>
                    </div>
                    <div className="flex flex-wrap gap-2">
                      {entry.status === "pending" && (
                        <Button
                          type="button"
                          size="sm"
                          disabled={disabled}
                          onClick={() =>
                            void action.run(
                              entry.id,
                              "已标记到账，回款汇总已同步。",
                              () =>
                                api.editAdjustment(
                                  detail.version.id,
                                  entry.id,
                                  {
                                    expense_id: entry.expense_id,
                                    kind: entry.kind,
                                    amount: (entry.amount_cents / 100).toFixed(
                                      2,
                                    ),
                                    status: "received",
                                    notes: entry.notes,
                                  },
                                ),
                            )
                          }
                        >
                          标记到账
                        </Button>
                      )}
                      <Button
                        type="button"
                        size="sm"
                        disabled={disabled}
                        onClick={() => start(entry)}
                      >
                        更正回款
                      </Button>
                      <Button
                        type="button"
                        size="sm"
                        disabled={disabled}
                        onClick={() => {
                          action.clear();
                          setRemoving(entry.id);
                        }}
                      >
                        移除回款
                      </Button>
                    </div>
                  </div>
                  {entry.notes && (
                    <p className="mt-2 break-words text-[var(--tf-muted)]">
                      {entry.notes}
                    </p>
                  )}
                  {editing === entry.id && editor}
                  <TripActionFeedback action={action} target={entry.id} />
                  {removing === entry.id && (
                    <div className="mt-2 flex flex-wrap gap-3">
                      <TripActionFeedback action={action} target="remove" />
                      <p className="w-full text-[var(--tf-muted)]">
                        移除这笔回款？原始消费与历史版本会保留。
                      </p>
                      <Button
                        variant="danger"
                        type="button"
                        disabled={disabled}
                        onClick={async () => {
                          if (
                            await action.run(
                              "remove",
                              "回款记录已移除，汇总已同步。",
                              () =>
                                api.deleteAdjustment(
                                  detail.version.id,
                                  entry.id,
                                ),
                            )
                          )
                            setRemoving(null);
                        }}
                      >
                        确认移除回款
                      </Button>
                      <Button
                        type="button"
                        onClick={() => {
                          action.clear();
                          setRemoving(null);
                        }}
                      >
                        保留
                      </Button>
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
