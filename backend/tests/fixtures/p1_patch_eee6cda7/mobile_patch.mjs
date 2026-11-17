// Pure decoded-value candidate. No command envelope, auth, IO or transaction.
class Invalid extends Error { constructor(code) { super(code); this.code = code; } }
const require = (condition, code) => { if (!condition) throw new Invalid(code); };
const has = (object, key) => Object.hasOwn(object, key);
const mutable = new Set(["title", "amount_cents", "currency", "paid_on", "category", "day", "activity_id", "notes", "receipt_ids", "budget_item_id", "settles_budget"]);
const categories = ["transport", "lodging", "meal", "ticket", "shopping", "other"];
const integer = (value, low, high) => Number.isSafeInteger(value) && value >= low && value <= high;
const whitespace = new Set([0x20, 0xa0, 0x1680, 0x2028, 0x2029, 0x202f, 0x205f, 0x3000,
  ...Array.from({ length: 11 }, (_, i) => 0x2000 + i)]);

function paymentDate(value) {
  if (value === null) return true;
  if (typeof value !== "string" || !/^[0-9]{4}-[0-9]{2}-[0-9]{2}$/.test(value) || value.startsWith("0000")) return false;
  const parsed = new Date(`${value}T00:00:00.000Z`);
  return Number.isFinite(parsed.valueOf()) && parsed.toISOString().slice(0, 10) === value;
}

function text(value, limit, title = false) {
  require(typeof value === "string", "text");
  const points = Array.from(value, character => character.codePointAt(0));
  require(points.length <= limit && (!title || points.length > 0), "text_length");
  require(points.every(point => !(point >= 0xd800 && point <= 0xdfff)), "unicode");
  require(points.every(point => point !== 0xfeff && !(point >= 0x7f && point <= 0x9f)
    && (point >= 0x20 || (!title && [9, 10, 13].includes(point)))), "text_control");
  if (title) require(points.some(point => !whitespace.has(point)), "title_blank");
}

function validatePatch(base, patch) {
  require(patch && typeof patch === "object" && !Array.isArray(patch), "patch_shape");
  require(Object.keys(patch).every(key => mutable.has(key) || key === "allow_shared_receipts"), "unknown_field");
  for (const [key, value] of Object.entries(patch)) {
    switch (key) {
      case "title": text(value, 100, true); break;
      case "notes": text(value, 1000); break;
      case "amount_cents": require(integer(value, 1, 9999999999), "amount"); break;
      case "currency": require(value === "CNY" || (value === null && (base.currency ?? null) === null), "currency"); break;
      case "paid_on": require(paymentDate(value), "date"); break;
      case "category": require(categories.includes(value), "category"); break;
      case "day": require(value === null || integer(value, 1, 60), "day"); break;
      case "activity_id": require(value === null || (typeof value === "string" && value.length > 0 && Array.from(value).length <= 100), "activity"); break;
      case "budget_item_id": require(value === null || (typeof value === "string" && value.length > 0 && Array.from(value).length <= 150), "budget"); break;
      case "settles_budget": case "allow_shared_receipts": require(typeof value === "boolean", "boolean"); break;
      case "receipt_ids":
        require(Array.isArray(value) && value.length <= 6 && value.every(id => typeof id === "string" && id.length === 32 && /^[a-f0-9]{32}$/.test(id))
          && new Set(value).size === value.length, "receipts");
        require(has(patch, "allow_shared_receipts"), "receipt_control");
        break;
    }
  }
}

export function evaluate(entry) {
  const { base, patch, context } = entry.input;
  try {
    validatePatch(base, patch);
    const candidate = structuredClone(base);
    for (const key of Object.keys(patch)) if (mutable.has(key)) candidate[key] = structuredClone(patch[key]);
    // The fixture baseline is already decoded, lossless, in-range and coherent.
    // This helper is not a validator for arbitrary imported legacy snapshots.
    require(integer(candidate.amount_cents, 1, 9999999999), "amount");
    if (has(patch, "amount_cents") || has(patch, "currency")) {
      require((candidate.currency ?? null) === null || candidate.currency === "CNY", "currency");
      require((base.currency ?? null) === null || base.currency === candidate.currency, "currency_conversion");
    }
    require(candidate.day === null || context.days.includes(candidate.day), "day_link");
    require(candidate.activity_id === null || context.actual_activity_ids.includes(candidate.activity_id), "activity_link");
    require(!candidate.settles_budget || candidate.budget_item_id !== null, "budget_link");
    if (candidate.budget_item_id !== null) {
      const budget = context.budget_rows.find(row => row.id === candidate.budget_item_id);
      require(budget && !budget.id.startsWith("expense-") && budget.category === candidate.category, "budget_link");
    }
    for (const kind of ["refund", "reimbursement"]) {
      const claimed = context.adjustments.filter(item => item.expense_id === base.id && item.kind === kind)
        .reduce((sum, item) => sum + BigInt(item.amount_cents), 0n);
      require(claimed <= BigInt(candidate.amount_cents), "recovery_limit");
    }
    if (has(patch, "receipt_ids")) {
      for (const id of candidate.receipt_ids) {
        if (base.receipt_ids.includes(id)) continue;
        require(context.available_receipt_ids.includes(id), "receipt_unavailable");
        require(!context.other_receipt_ids.includes(id) || patch.allow_shared_receipts, "receipt_shared");
      }
    }
    const changed = [...mutable].some(key => has(base, key) !== has(candidate, key)
      || JSON.stringify(base[key]) !== JSON.stringify(candidate[key]));
    return { accepted: true, value: { candidate, controls: has(patch, "allow_shared_receipts")
      ? { allow_shared_receipts: patch.allow_shared_receipts } : {},
    planned_business_revision: changed ? "new" : "unchanged",
    planned_operation_receipt: "required-not-persisted", cny_totals_eligible: candidate.currency === "CNY" } };
  } catch (error) {
    if (error instanceof Invalid) return { accepted: false, error: error.code };
    throw error;
  }
}
