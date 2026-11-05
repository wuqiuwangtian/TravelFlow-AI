// P0只读领域投影：无平台、网络或存储依赖。不是写入、同步或完整预算引擎。
export type LedgerInput = {
  expenses: readonly { id: string; amount_cents: number }[];
  adjustments: readonly {
    expense_id: string;
    amount_cents: number;
    kind: "refund" | "reimbursement";
    status: "received" | "pending";
  }[];
};

function cents(value: number): number {
  if (!Number.isSafeInteger(value) || value <= 0) {
    throw new Error("Amount must be a positive safe integer in cents");
  }
  return value;
}

function exact(value: number): number {
  if (!Number.isSafeInteger(value)) throw new Error("Ledger total exceeds safe integer range");
  return value;
}

export function summarizeLedger(input: LedgerInput) {
  const result = {
    paid_cents: 0,
    refunded_cents: 0,
    reimbursed_cents: 0,
    pending_refund_cents: 0,
    pending_reimbursement_cents: 0,
    net_expense_cents: 0,
    net_outlay_cents: 0,
    over_recovered_cents: 0,
  };
  const recovered = new Map<string, number>();
  for (const entry of input.expenses) {
    result.paid_cents = exact(result.paid_cents + cents(entry.amount_cents));
  }
  for (const entry of input.adjustments) {
    const amount = cents(entry.amount_cents);
    if (entry.kind !== "refund" && entry.kind !== "reimbursement") throw new Error("Invalid recovery kind");
    if (entry.status !== "received" && entry.status !== "pending") throw new Error("Invalid recovery status");
    if (entry.status === "received") {
      const field = entry.kind === "refund" ? "refunded_cents" : "reimbursed_cents";
      result[field] = exact(result[field] + amount);
      recovered.set(entry.expense_id, exact((recovered.get(entry.expense_id) ?? 0) + amount));
    } else {
      const field = entry.kind === "refund" ? "pending_refund_cents" : "pending_reimbursement_cents";
      result[field] = exact(result[field] + amount);
    }
  }
  result.net_expense_cents = exact(result.paid_cents - result.refunded_cents);
  result.net_outlay_cents = exact(result.net_expense_cents - result.reimbursed_cents);
  for (const entry of input.expenses) {
    result.over_recovered_cents = exact(
      result.over_recovered_cents + Math.max(0, (recovered.get(entry.id) ?? 0) - entry.amount_cents),
    );
  }
  return result;
}
