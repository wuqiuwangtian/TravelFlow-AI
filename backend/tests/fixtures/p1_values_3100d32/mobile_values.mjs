// P1值片段评审候选：不属于正式命令，不读写存储／网络，不处理原始JSON字节。
export class CandidateError extends Error {
  constructor(code) { super(code); this.code = code; }
}
function requireValue(condition, code) {
  if (!condition) throw new CandidateError(code);
}
function shape(value, required, optional = []) {
  requireValue(value !== null && typeof value === "object" && !Array.isArray(value), "shape");
  const allowed = new Set([...required, ...optional]);
  requireValue(required.every(key => Object.hasOwn(value, key)) && Object.keys(value).every(key => allowed.has(key)), "shape");
}
function integer(value, low, high) {
  return Number.isSafeInteger(value) && value >= low && value <= high;
}
function text(value) { return typeof value === "string" && value.length > 0; }

export function moneyText(value) {
  requireValue(typeof value === "string", "text_syntax");
  const match = /^(0|[1-9][0-9]*)(?:\.([0-9]{1,2}))?$/.exec(value);
  requireValue(match !== null && match[0] === value, "text_syntax");
  requireValue(match[1].length <= 8, "amount_range");
  const cents = BigInt(match[1]) * 100n + BigInt((match[2] ?? "").padEnd(2, "0"));
  requireValue(cents >= 1n && cents <= 9999999999n, "amount_range");
  return { amount_cents: Number(cents) };
}

export function moneyValue(value) {
  shape(value, ["currency", "amount_cents"]);
  requireValue(value.currency === "CNY", "currency");
  requireValue(integer(value.amount_cents, 1, 9999999999), "amount_range");
  return { currency: "CNY", amount_cents: value.amount_cents };
}

function validDate(value) {
  if (typeof value !== "string" || value.length !== 10 || !/^[0-9]{4}-[0-9]{2}-[0-9]{2}$/.test(value)) return false;
  const [year, month, day] = value.split("-").map(Number);
  if (year < 1 || month < 1 || month > 12) return false;
  const leap = year % 4 === 0 && (year % 100 !== 0 || year % 400 === 0);
  const days = [31, leap ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31];
  return day >= 1 && day <= days[month - 1];
}

export function paymentDate(value, mode) {
  requireValue(mode === "create" || mode === "edit", "mode");
  shape(value, mode === "create" ? ["paid_on"] : [], mode === "edit" ? ["paid_on"] : []);
  if (!Object.hasOwn(value, "paid_on")) return { effect: "unchanged" };
  if (value.paid_on === null) return { effect: "set_unknown" };
  requireValue(validDate(value.paid_on), "date");
  return { effect: "set_date", paid_on: value.paid_on };
}

function localParts(formatter, instant) {
  const p = Object.fromEntries(formatter.formatToParts(new Date(instant)).map(part => [part.type, part.value]));
  const date = `${p.year.padStart(4, "0")}-${p.month}-${p.day}`;
  requireValue(validDate(date) && p.second === "00", "booking_range");
  return { local: `${date}T${p.hour}:${p.minute}`, date };
}

export function elapsedBooking(value) {
  shape(value, ["local_start", "time_zone", "utc_offset", "start_instant", "duration_minutes", "duration_semantics"]);
  requireValue(value.duration_semantics === "elapsed", "duration_semantics");
  requireValue(integer(value.duration_minutes, 1, 1440), "duration");
  requireValue(typeof value.local_start === "string" && value.local_start.length === 16 && /^[0-9]{4}-[0-9]{2}-[0-9]{2}T(?:[01][0-9]|2[0-3]):[0-5][0-9]$/.test(value.local_start)
    && validDate(value.local_start.slice(0, 10)), "date");
  requireValue(typeof value.utc_offset === "string" && value.utc_offset.length === 6 && /^[+-](?:[01][0-9]|2[0-3]):[0-5][0-9]$/.test(value.utc_offset)
    && value.utc_offset !== "-00:00", "offset");
  requireValue(text(value.time_zone) && !/^[+-]/.test(value.time_zone), "zone");
  let formatter;
  try {
    formatter = new Intl.DateTimeFormat("en-US", {
      timeZone: value.time_zone, calendar: "gregory", numberingSystem: "latn", hourCycle: "h23",
      year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit",
    });
  } catch { throw new CandidateError("zone"); }
  const start = Date.parse(`${value.local_start}:00${value.utc_offset}`);
  requireValue(Number.isFinite(start), "instant");
  requireValue(validDate(new Date(start).toISOString().slice(0, 10)), "booking_range");
  requireValue(value.start_instant === new Date(start).toISOString(), "instant");
  // UTC往返不匹配即拒绝，不选择重复时刻、不挪动不存在的时刻。
  requireValue(localParts(formatter, start).local === value.local_start, "resolution");
  const end = start + value.duration_minutes * 60000;
  const endLocal = localParts(formatter, end).local;
  const offsetMinutes = (Date.parse(`${endLocal}:00Z`) - end) / 60000;
  requireValue(Number.isInteger(offsetMinutes), "booking_range");
  const absolute = Math.abs(offsetMinutes);
  const offset = `${offsetMinutes < 0 ? "-" : "+"}${String(Math.floor(absolute / 60)).padStart(2, "0")}:${String(absolute % 60).padStart(2, "0")}`;
  return {
    start_instant: value.start_instant, end_instant: new Date(end).toISOString(),
    end_local: endLocal, end_utc_offset: offset, duration_semantics: "elapsed",
  };
}

export function receiptView(value) {
  shape(value, ["operation", "committed_revision_id", "replayed", "applied_on_this_call"], ["observed_head"]);
  shape(value.operation, ["dataset_id", "source_device_id", "operation_id"]);
  requireValue(Object.values(value.operation).every(text) && text(value.committed_revision_id), "identity");
  requireValue(typeof value.replayed === "boolean" && typeof value.applied_on_this_call === "boolean"
    && value.replayed !== value.applied_on_this_call, "receipt_semantics");
  if (Object.hasOwn(value, "observed_head")) {
    shape(value.observed_head, ["revision_id", "entity_state"]);
    requireValue(text(value.observed_head.revision_id)
      && ["present", "deleted", "unknown"].includes(value.observed_head.entity_state), "head");
  }
  // 只验证片段；不查询真实回执，原提交修订与当前头可以不同。
  return structuredClone(value);
}

export function evaluate(entry) {
  try {
    let value;
    switch (entry.kind) {
      case "money_text": value = moneyText(entry.input); break;
      case "money_value": value = moneyValue(entry.input); break;
      case "payment_date": value = paymentDate(entry.input, entry.mode); break;
      case "elapsed_booking": value = elapsedBooking(entry.input); break;
      case "receipt_view": value = receiptView(entry.input); break;
      default: throw new Error(`Unknown candidate kind: ${entry.kind}`);
    }
    return { accepted: true, value };
  } catch (error) {
    if (!(error instanceof CandidateError)) throw error;
    return { accepted: false, error: error.code };
  }
}
