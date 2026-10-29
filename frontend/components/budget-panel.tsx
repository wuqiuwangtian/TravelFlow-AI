"use client";
import { useState } from "react";
import { TripActionFeedback, useTripAction } from "@/components/trip-action";
import { Button } from "@/components/ui";
import { api } from "@/lib/api";
import type {
  BudgetRow,
  ExpenseCategory,
  ExpenseInput,
  VersionDetail,
} from "@/lib/types";
const labels: Record<ExpenseCategory, string> = {
  meal: "饮食",
  lodging: "住宿",
  transport: "交通",
  ticket: "门票",
  shopping: "购物",
  other: "其他",
};
const money = (value: number) => `¥${(value / 100).toFixed(2)}`;
const field =
  "w-full rounded-lg border border-[var(--tf-line)] bg-[var(--tf-paper)] px-3 py-2 text-sm";
export function BudgetPanel({
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
  const action = useTripAction(onCommit);
  const [editing, setEditing] = useState<{
    row: BudgetRow;
    mode: "plan" | "actual";
    value: string;
    requestId: string;
  } | null>(null);
  const [custom, setCustom] = useState<{
    title: string;
    category: ExpenseCategory;
    amount: string;
    day: number;
    requestId: string;
  } | null>(null);
  const [error, setError] = useState("");
  const overview = detail.budget_overview;
  if (!overview) return null;
  const balance =
    overview.budget_cents === null
      ? null
      : overview.budget_cents - overview.projected_cents;
  const actual = detail.itinerary?.ledger_summary.net_expense_cents ?? 0;
  async function save() {
    if (!editing) return;
    const { row, mode, value, requestId } = editing;
    if (
      (value !== "" && !/^(?:0|[1-9]\d{0,7})(?:\.\d{1,2})?$/.test(value)) ||
      (mode === "actual" && Number(value) <= 0)
    ) {
      setError("请输入有效金额，实际总额需大于0，最多两位小数。");
      return;
    }
    const previous = detail.itinerary?.expenses.find(
      (e) => e.id === row.expense_ids[0],
    );
    const data: ExpenseInput = {
      title: previous?.title ?? row.title,
      amount: value,
      category: previous?.category ?? row.category,
      day: previous ? previous.day : row.day,
      activity_id: previous?.activity_id ?? row.activity_id,
      notes: previous?.notes ?? "",
      receipt_ids: previous?.receipt_ids ?? [],
      budget_item_id:
        previous?.budget_item_id ??
        (row.id.startsWith("expense-") ? null : row.id),
      settles_budget: !row.id.startsWith("expense-"),
    };
    if (
      await action.run(
        row.id,
        mode === "plan"
          ? "预估已更新，预计总支出已同步。"
          : "实际总额已保存，预算差额已同步。",
        () =>
          mode === "plan"
            ? api.updateBudget(detail.version.id, row.id, value || null)
            : previous
              ? api.editExpense(detail.version.id, previous.id, data)
              : api.addExpense(detail.version.id, data, requestId),
      )
    )
      setEditing(null);
  }
  return (
    <section aria-label="费用与预算" className="trip-ui trip-budget">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h3 className="font-semibold text-[var(--tf-ink)]">费用与预算</h3>
          <p className="mt-1 text-xs leading-5 text-[var(--tf-muted)]">
            预估和实付放在同一项里。填入实际总额后，该项按实付推算；未结算项目继续保留预估。
          </p>
        </div>
        <Button
          type="button"
          size="sm"
          disabled={disabled}
          onClick={() => {
            setEditing(null);
            setCustom({
              title: "",
              category: "other",
              amount: "",
              day,
              requestId: crypto.randomUUID(),
            });
            action.clear();
            setError("");
          }}
        >
          添加自定义项
        </Button>
      </div>
      {custom && (
        <form
          aria-label="添加自定义预算"
          className="mt-4 rounded-[var(--tf-radius)] bg-[var(--tf-surface)] p-4"
          onSubmit={(e) => {
            e.preventDefault();
            if (
              custom.amount !== "" &&
              !/^(?:0|[1-9]\d{0,7})(?:\.\d{1,2})?$/.test(custom.amount)
            ) {
              setError("请填写有效的预估金额。");
              return;
            }
            void action
              .run("custom", "自定义项已添加，预估已同步。", () =>
                api.addBudget(detail.version.id, {
                  title: custom.title,
                  category: custom.category,
                  amount: custom.amount || null,
                  day: custom.day,
                  request_id: custom.requestId,
                }),
              )
              .then((ok) => {
                if (ok) setCustom(null);
              });
          }}
        >
          <fieldset disabled={disabled} className="space-y-3">
            <p className="text-xs text-muted">此项目保存到第{custom.day}天。</p>
            <label className="block text-xs">
              项目名称
              <input
                autoFocus
                required
                maxLength={100}
                className={`${field} mt-1`}
                value={custom.title}
                onChange={(e) =>
                  setCustom({ ...custom, title: e.target.value })
                }
              />
            </label>
            <label className="block text-xs">
              项目类别
              <select
                aria-label="项目类别"
                className={`${field} mt-1`}
                value={custom.category}
                onChange={(e) =>
                  setCustom({
                    ...custom,
                    category: e.target.value as ExpenseCategory,
                  })
                }
              >
                {Object.entries(labels).map(([key, label]) => (
                  <option key={key} value={key}>
                    {label}
                  </option>
                ))}
              </select>
            </label>
            <label className="block text-xs">
              预估金额（元，全团）
              <input
                inputMode="decimal"
                className={`${field} mt-1`}
                value={custom.amount}
                onChange={(e) =>
                  setCustom({ ...custom, amount: e.target.value })
                }
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
                loading={
                  action.feedback?.target === "custom" &&
                  action.feedback.pending
                }
                type="submit"
              >
                保存自定义项
              </Button>
              <Button
                size="sm"
                type="button"
                onClick={() => {
                  action.clear();
                  setCustom(null);
                }}
              >
                取消
              </Button>
            </div>
          </fieldset>
          <TripActionFeedback action={action} target="custom" />
        </form>
      )}
      {!custom && <TripActionFeedback action={action} target="custom" />}
      {action.feedback?.message &&
        action.feedback.target !== "custom" &&
        !overview.rows.some((row) => row.id === action.feedback?.target) && (
          <TripActionFeedback action={action} target={action.feedback.target} />
        )}
      <div className="trip-budget-summary" aria-label="预算推算汇总">
        {[
          ["预计总支出", money(overview.projected_cents)],
          [
            balance === null
              ? "预算口径待定"
              : balance < 0
                ? "预计超预算"
                : "预计预算余量",
            balance === null ? "待确认" : money(Math.abs(balance)),
          ],
          ["清单预估", money(overview.planned_cents)],
          ["已记净消费", money(actual)],
        ].map(([name, value]) => (
          <div
            key={name}
            data-primary={name === "预计总支出" || undefined}
            data-warning={name === "预计超预算" || undefined}
          >
            <p className="trip-budget-stat-label">{name}</p>
            <p className="trip-budget-stat-value">{value}</p>
          </div>
        ))}
      </div>
      <p className="mt-2 text-[11px] leading-5 text-[var(--tf-muted)]">
        {overview.unknown_count > 0
          ? `还有${overview.unknown_count}项未定价，预计数仅含已知费用，暂不能认定节省。`
          : "预计数按当前已列项目计算，未列出的开支需另计。"}
        退款到账冲减消费，报销作为回款单列。
      </p>
      <div className="trip-budget-categories">
        {(Object.keys(labels) as ExpenseCategory[]).map((category) => {
          const rows = overview.rows.filter((r) => r.category === category);
          const planned = rows.reduce((n, r) => n + (r.planned_cents ?? 0), 0),
            paid = rows.reduce((n, r) => n + r.actual_cents, 0);
          return (
            <details
              key={category}
              open={rows.length <= 4 && rows.length > 0}
              className="min-w-0 rounded-[var(--tf-radius)] border border-[var(--tf-line)] p-3"
            >
              <summary className="cursor-pointer text-sm font-medium text-[var(--tf-ink)]">
                {labels[category]}
                <span className="ml-2 text-[11px] font-normal text-[var(--tf-muted)]">
                  预估 {money(planned)} · 已记 {money(paid)}
                </span>
              </summary>
              <div className="mt-3 space-y-3">
                {rows.length === 0 && (
                  <p className="text-xs text-[var(--tf-muted)]">
                    尚无项目，可添加自定义项。
                  </p>
                )}
                {rows.map((row) => (
                  <div
                    key={row.id}
                    id={`journey-budget-row-${row.id}`}
                    tabIndex={-1}
                    aria-label={`费用项目：${row.title}`}
                    className="rounded-lg bg-[var(--tf-surface)] p-3"
                  >
                    <p className="break-words text-xs font-medium text-[var(--tf-ink)]">
                      {row.day ? `第${row.day}天 · ` : ""}
                      {row.title}
                    </p>
                    <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-2 text-xs text-[var(--tf-muted)]">
                      <Button
                        variant="ghost"
                        size="sm"
                        disabled={disabled || row.id.startsWith("expense-")}
                        onClick={() => {
                          setCustom(null);
                          setEditing({
                            row,
                            mode: "plan",
                            value:
                              row.planned_cents === null
                                ? ""
                                : (row.planned_cents / 100).toFixed(2),
                            requestId: crypto.randomUUID(),
                          });
                          action.clear();
                          setError("");
                        }}
                        aria-label={`修改${row.title}预估`}
                      >
                        预估{" "}
                        {row.planned_cents === null
                          ? "待核实"
                          : money(row.planned_cents)}
                      </Button>
                      <span>
                        实付净额{" "}
                        {row.expense_ids.length
                          ? money(row.actual_cents)
                          : "待填写"}
                      </span>
                      <span
                        className={
                          row.settled
                            ? "text-[var(--tf-accent)]"
                            : "text-[var(--tf-muted)]"
                        }
                      >
                        {row.settled ? "已结算" : "待结算"}
                      </span>
                    </div>
                    <div className="mt-2 flex flex-wrap items-center gap-2">
                      {row.expense_ids.length <= 1 ? (
                        <Button
                          type="button"
                          size="sm"
                          disabled={disabled}
                          onClick={() => {
                            const e = detail.itinerary?.expenses.find(
                              (e) => e.id === row.expense_ids[0],
                            );
                            setCustom(null);
                            setEditing({
                              row,
                              mode: "actual",
                              value: e
                                ? (e.amount_cents / 100).toFixed(2)
                                : row.planned_cents === null
                                  ? ""
                                  : (row.planned_cents / 100).toFixed(2),
                              requestId: crypto.randomUUID(),
                            });
                            action.clear();
                            setError("");
                          }}
                        >
                          {row.expense_ids.length
                            ? "更正实际总额"
                            : "填实际总额"}
                        </Button>
                      ) : (
                        <span className="text-[11px] text-[var(--tf-muted)]">
                          含多笔消费，请展开下方明细逐笔更正。
                        </span>
                      )}
                      {row.settled && row.planned_cents !== null && (
                        <span
                          className={`text-[11px] ${row.actual_cents > row.planned_cents ? "text-[var(--tf-warning)]" : "text-[var(--tf-accent)]"}`}
                        >
                          较预估
                          {row.actual_cents > row.planned_cents
                            ? "多花"
                            : "少花"}{" "}
                          {money(
                            Math.abs(row.actual_cents - row.planned_cents),
                          )}
                        </span>
                      )}
                      {row.id.startsWith("custom-") && (
                        <Button
                          type="button"
                          disabled={disabled}
                          onClick={() => {
                            if (
                              window.confirm(
                                "移除此自定义预估？已记实际消费会保留，历史版本仍可查看。",
                              )
                            )
                              void action.run(
                                row.id,
                                "自定义预估已移除，已记实际消费保留。",
                                () =>
                                  api.deleteBudget(detail.version.id, row.id),
                              );
                          }}
                        >
                          移除自定义项
                        </Button>
                      )}
                    </div>
                    {editing?.row.id === row.id && (
                      <form
                        aria-label={
                          editing.mode === "plan"
                            ? "修改预估费用"
                            : "填写实际总额"
                        }
                        className="mt-4 rounded-[var(--tf-radius)] border border-[var(--tf-accent-line)] bg-[var(--tf-accent-soft)] p-4"
                        onSubmit={(e) => {
                          e.preventDefault();
                          void save();
                        }}
                      >
                        <fieldset disabled={disabled}>
                          <p className="mb-2 text-sm font-medium text-[var(--tf-ink)]">
                            {editing.row.title}
                          </p>
                          <label className="block text-xs text-[var(--tf-muted)]">
                            {editing.mode === "plan"
                              ? "预估总额（元，全团；留空待核实）"
                              : "实际总额（元，全团；确认该项已结算）"}
                            <input
                              className={`${field} mt-1`}
                              inputMode="decimal"
                              autoFocus
                              onFocus={(event) => event.currentTarget.select()}
                              value={editing.value}
                              onChange={(e) =>
                                setEditing({
                                  ...editing,
                                  value: e.target.value,
                                })
                              }
                            />
                          </label>
                          {error && (
                            <p
                              role="alert"
                              className="mt-2 text-xs text-[var(--tf-danger)]"
                            >
                              {error}
                            </p>
                          )}
                          <div className="mt-3 flex gap-2">
                            <Button
                              type="submit"
                              variant="primary"
                              loading={
                                action.feedback?.target === row.id &&
                                action.feedback.pending
                              }
                            >
                              保存
                              {editing.mode === "plan" ? "预估" : "实际总额"}
                            </Button>
                            <Button
                              type="button"
                              size="sm"
                              onClick={() => {
                                action.clear();
                                setEditing(null);
                              }}
                            >
                              取消
                            </Button>
                          </div>
                        </fieldset>
                      </form>
                    )}
                    <TripActionFeedback action={action} target={row.id} />
                  </div>
                ))}
              </div>
            </details>
          );
        })}
      </div>
    </section>
  );
}
