"use client";
import { useState } from "react";
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
  "w-full rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm";
const button =
  "rounded-lg border border-teal-100 bg-white px-2 py-1.5 text-xs text-teal-700 disabled:opacity-40";
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
      day: previous?.day ?? row.day,
      activity_id: previous?.activity_id ?? row.activity_id,
      notes: previous?.notes ?? "",
      receipt_ids: previous?.receipt_ids ?? [],
      budget_item_id:
        previous?.budget_item_id ??
        (row.id.startsWith("expense-") ? null : row.id),
      settles_budget: !row.id.startsWith("expense-"),
    };
    if (
      await onCommit(() =>
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
    <section
      aria-label="费用与预算"
      className="mt-6 rounded-2xl border border-slate-200 bg-white p-4 sm:p-5"
    >
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h3 className="font-semibold text-slate-800">费用与预算</h3>
          <p className="mt-1 text-xs leading-5 text-slate-500">
            预估和实付放在同一项里。填入实际总额后，该项按实付推算；未结算项目继续保留预估。
          </p>
        </div>
        <button
          type="button"
          className={button}
          disabled={disabled}
          onClick={() => {
            setCustom({
              title: "",
              category: "other",
              amount: "",
              requestId: crypto.randomUUID(),
            });
            setError("");
          }}
        >
          添加自定义项
        </button>
      </div>
      <div
        className="mt-4 grid grid-cols-2 gap-3 sm:grid-cols-4"
        aria-label="预算推算汇总"
      >
        {[
          ["清单预估", money(overview.planned_cents)],
          ["已记净消费", money(actual)],
          ["预计总支出", money(overview.projected_cents)],
          [
            balance === null
              ? "预算口径待定"
              : balance < 0
                ? "预计超预算"
                : "预计预算余量",
            balance === null ? "待确认" : money(Math.abs(balance)),
          ],
        ].map(([name, value]) => (
          <div
            key={name}
            className={`rounded-xl p-3 ${name === "预计超预算" ? "bg-amber-50 text-amber-800" : "bg-slate-50 text-slate-700"}`}
          >
            <p className="text-[11px] opacity-70">{name}</p>
            <p className="mt-1 text-base font-semibold">{value}</p>
          </div>
        ))}
      </div>
      <p className="mt-2 text-[11px] leading-5 text-slate-500">
        {overview.unknown_count > 0
          ? `还有${overview.unknown_count}项未定价，预计数仅含已知费用，暂不能认定节省。`
          : "预计数按当前已列项目计算，未列出的开支需另计。"}
        退款到账冲减消费，报销作为回款单列。
      </p>
      <div className="mt-4 grid gap-3 lg:grid-cols-2">
        {(Object.keys(labels) as ExpenseCategory[]).map((category) => {
          const rows = overview.rows.filter((r) => r.category === category);
          const planned = rows.reduce((n, r) => n + (r.planned_cents ?? 0), 0),
            paid = rows.reduce((n, r) => n + r.actual_cents, 0);
          return (
            <details
              key={category}
              open={rows.length <= 4 && rows.length > 0}
              className="min-w-0 rounded-xl border border-slate-100 p-3"
            >
              <summary className="cursor-pointer text-sm font-medium text-slate-700">
                {labels[category]}
                <span className="ml-2 text-[11px] font-normal text-slate-500">
                  预估 {money(planned)} · 已记 {money(paid)}
                </span>
              </summary>
              <div className="mt-3 space-y-3">
                {rows.length === 0 && (
                  <p className="text-xs text-slate-400">
                    尚无项目，可添加自定义项。
                  </p>
                )}
                {rows.map((row) => (
                  <div
                    key={row.id}
                    aria-label={`费用项目：${row.title}`}
                    className="rounded-lg bg-slate-50 p-3"
                  >
                    <p className="break-words text-xs font-medium text-slate-700">
                      {row.day ? `第${row.day}天 · ` : ""}
                      {row.title}
                    </p>
                    <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-2 text-xs text-slate-500">
                      <button
                        className="text-left underline decoration-dotted underline-offset-4 disabled:no-underline"
                        disabled={disabled || row.id.startsWith("expense-")}
                        onClick={() => {
                          setEditing({
                            row,
                            mode: "plan",
                            value:
                              row.planned_cents === null
                                ? ""
                                : (row.planned_cents / 100).toFixed(2),
                            requestId: crypto.randomUUID(),
                          });
                          setError("");
                        }}
                        aria-label={`修改${row.title}预估`}
                      >
                        预估{" "}
                        {row.planned_cents === null
                          ? "待核实"
                          : money(row.planned_cents)}
                      </button>
                      <span>
                        实付净额{" "}
                        {row.expense_ids.length
                          ? money(row.actual_cents)
                          : "待填写"}
                      </span>
                      <span
                        className={
                          row.settled ? "text-teal-700" : "text-slate-400"
                        }
                      >
                        {row.settled ? "已结算" : "待结算"}
                      </span>
                    </div>
                    <div className="mt-2 flex flex-wrap items-center gap-2">
                      {row.expense_ids.length <= 1 ? (
                        <button
                          type="button"
                          className={button}
                          disabled={disabled}
                          onClick={() => {
                            const e = detail.itinerary?.expenses.find(
                              (e) => e.id === row.expense_ids[0],
                            );
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
                            setError("");
                          }}
                        >
                          {row.expense_ids.length
                            ? "更正实际总额"
                            : "填实际总额"}
                        </button>
                      ) : (
                        <span className="text-[11px] text-slate-400">
                          含多笔消费，请展开下方明细逐笔更正。
                        </span>
                      )}
                      {row.settled && row.planned_cents !== null && (
                        <span
                          className={`text-[11px] ${row.actual_cents > row.planned_cents ? "text-amber-700" : "text-teal-700"}`}
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
                        <button
                          type="button"
                          className="text-[11px] text-slate-400"
                          disabled={disabled}
                          onClick={() => {
                            if (
                              window.confirm(
                                "移除此自定义预估？已记实际消费会保留，历史版本仍可查看。",
                              )
                            )
                              void onCommit(() =>
                                api.deleteBudget(detail.version.id, row.id),
                              );
                          }}
                        >
                          移除自定义项
                        </button>
                      )}
                    </div>
                    {editing?.row.id === row.id && (
                      <form
                        aria-label={
                          editing.mode === "plan"
                            ? "修改预估费用"
                            : "填写实际总额"
                        }
                        className="mt-4 rounded-xl border border-teal-100 bg-teal-50/40 p-4"
                        onSubmit={(e) => {
                          e.preventDefault();
                          void save();
                        }}
                      >
                        <fieldset disabled={disabled}>
                          <p className="mb-2 text-sm font-medium text-slate-700">
                            {editing.row.title}
                          </p>
                          <label className="block text-xs text-slate-600">
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
                              className="mt-2 text-xs text-red-600"
                            >
                              {error}
                            </p>
                          )}
                          <div className="mt-3 flex gap-2">
                            <button type="submit" className={button}>
                              保存
                              {editing.mode === "plan" ? "预估" : "实际总额"}
                            </button>
                            <button
                              type="button"
                              className={button}
                              onClick={() => setEditing(null)}
                            >
                              取消
                            </button>
                          </div>
                        </fieldset>
                      </form>
                    )}
                  </div>
                ))}
              </div>
            </details>
          );
        })}
      </div>
      {custom && (
        <form
          aria-label="添加自定义预算"
          className="mt-4 rounded-xl bg-slate-50 p-4"
          onSubmit={(e) => {
            e.preventDefault();
            if (
              custom.amount !== "" &&
              !/^(?:0|[1-9]\d{0,7})(?:\.\d{1,2})?$/.test(custom.amount)
            ) {
              setError("请填写有效的预估金额。");
              return;
            }
            void onCommit(() =>
              api.addBudget(detail.version.id, {
                title: custom.title,
                category: custom.category,
                amount: custom.amount || null,
                day,
                request_id: custom.requestId,
              }),
            ).then((ok) => {
              if (ok) setCustom(null);
            });
          }}
        >
          <fieldset disabled={disabled} className="space-y-3">
            <label className="block text-xs">
              项目名称
              <input
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
              <p role="alert" className="text-xs text-red-600">
                {error}
              </p>
            )}
            <div className="flex gap-2">
              <button className={button} type="submit">
                保存自定义项
              </button>
              <button
                className={button}
                type="button"
                onClick={() => setCustom(null)}
              >
                取消
              </button>
            </div>
          </fieldset>
        </form>
      )}
    </section>
  );
}
