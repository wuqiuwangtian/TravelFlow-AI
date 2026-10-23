/** 与后端 DTO 对应的类型定义（见 backend/app/domain/schemas.py）。 */

export type DetailLevel =
  | "L1_minimal"
  | "L2_standard"
  | "L3_detailed"
  | "L4_butler";

export const DETAIL_LEVEL_LABELS: Record<DetailLevel, string> = {
  L1_minimal: "极简",
  L2_standard: "标准",
  L3_detailed: "详细",
  L4_butler: "保姆级",
};

export interface SessionInfo {
  id: string;
  title: string;
  created_at: string;
  updated_at: string;
  archived: boolean;
  branch_count: number;
}

export interface BranchInfo {
  id: string;
  session_id: string;
  title: string;
  created_at: string;
  parent_branch_id: string | null;
  fork_version_id: string | null;
  is_primary: boolean;
}

export interface VersionInfo {
  id: string;
  branch_id: string;
  seq: number;
  summary: string;
  detail_level: string;
  trigger: string;
  checkpoint_id: string | null;
  created_at: string;
}

export interface ProviderItem {
  id: string;
  name: string;
  docs_url: string;
  requires_api_key: boolean;
  default_base_url: string | null;
  note: string;
}

export interface ProvidersResponse {
  llm: ProviderItem[];
  search: ProviderItem[];
}

export interface ConfigCheck {
  llm: {
    provider: string;
    model: string;
    base_url: string | null;
    has_key: boolean;
    ready: boolean;
  };
  search: { provider: string; has_key: boolean; ready: boolean };
  default_detail_level: string;
  warnings: string[];
}

export interface ConnectionTest {
  ok: boolean;
  provider: string;
  message: string;
}

export interface ChatClarification {
  kind: string;
  prompt: string;
  options?: { label: string; reply: string }[];
  changes?: {
    day: number;
    title: string;
    before: string;
    after: string;
    status: "kept" | "shifted" | "omitted";
    protected: boolean;
  }[];
  conflicts?: string[];
  warnings?: string[];
}

export interface ChatResponse {
  session_id: string;
  branch_id: string;
  version_id: string | null;
  status: string;
  reply: string;
  plan: string;
  missing_fields: string[];
  clarification?: ChatClarification | null;
}

export interface SessionDetail {
  session: SessionInfo;
  branches: BranchInfo[];
  versions: Record<string, VersionInfo[]>;
}

export interface Citation {
  index: number;
  title: string;
  url: string;
  provider: string;
  retrieved_at: string;
}

export interface VersionDetail {
  version: VersionInfo;
  markdown: string;
  citations: Citation[];
  todos: {
    kind: string;
    what: string;
    when: string | null;
    note: string | null;
  }[];
  searched_at: string | null;
  itinerary: Itinerary | null;
  requirements: TravelRequirements | null;
  budget_overview: BudgetOverview | null;
}

export type TravelMode =
  | "auto"
  | "walking"
  | "transit"
  | "driving"
  | "rail"
  | "flight"
  | "transfer";

export interface TravelRequirements {
  destination: string | null;
  origin: string | null;
  duration_days: number | null;
  budget: number | null;
  travelers: number | null;
  departure_date: string | null;
  budget_basis: "per_person" | "total" | null;
}

export interface Place {
  id: string;
  name: string;
  address: string;
  longitude: number;
  latitude: number;
  citycode: string;
  city: string;
  district: string;
  typecode: string;
}

export interface Activity {
  title: string;
  place_name: string;
  city: string;
  kind:
    | "attraction"
    | "station"
    | "hotel"
    | "meal"
    | "free_time"
    | "transport"
    | "other";
  address_hint: string;
  start_time: string | null;
  start_day_offset: number;
  duration_minutes: number | null;
  cost: number | null;
  cost_basis: "per_person" | "total" | "unknown";
  cost_source: "reference" | "estimate" | "manual" | "unknown";
  transport_mode: TravelMode;
  transport_minutes: number | null;
  transport_cost: number | null;
  transport_cost_basis: "per_person" | "total" | "unknown";
  transport_source_indices: number[];
  notes: string;
  source_indices: number[];
}

export interface ScheduledActivity extends Activity {
  id: string;
  place: Place | null;
  place_status:
    | "pending"
    | "matched"
    | "ambiguous"
    | "not_found"
    | "unavailable";
  candidates: Place[];
  match_reason: string;
  time_is_suggested: boolean;
  progress: ActivityProgress;
  progress_updated_at: string | null;
  booking: Booking | null;
  is_context_anchor: boolean;
}

export interface Booking {
  start_time: string;
  start_day_offset: number;
  duration_minutes: number;
  notes: string;
}
export interface ReplanContext {
  activity_id: string;
  time: string;
  start_day_offset: number;
  end_time: string | null;
  end_day_offset: number;
  end_activity_id: string | null;
  omitted_titles: string[];
  updated_at: string;
}
export interface ReplanInput {
  activity_id?: string;
  location_name?: string;
  city?: string;
  time: string;
  start_day_offset?: number;
  end_time: string | null;
  end_day_offset?: number;
  end_activity_id: string | null;
}

export type ActivityProgress =
  | "not_started"
  | "in_progress"
  | "completed"
  | "skipped";
export type ExpenseCategory =
  | "transport"
  | "lodging"
  | "meal"
  | "ticket"
  | "shopping"
  | "other";

export interface ExpenseInput {
  title: string;
  amount: string;
  category: ExpenseCategory;
  day: number | null;
  activity_id: string | null;
  notes: string;
  receipt_ids?: string[];
  allow_shared_receipts?: boolean;
  budget_item_id?: string | null;
  settles_budget?: boolean;
}

export interface Expense {
  id: string;
  title: string;
  amount_cents: number;
  category: ExpenseCategory;
  day: number | null;
  activity_id: string | null;
  notes: string;
  created_at: string;
  updated_at: string;
  receipt_ids: string[];
  budget_item_id: string | null;
  settles_budget: boolean;
}

export interface BudgetRow {
  id: string;
  title: string;
  category: ExpenseCategory;
  day: number | null;
  activity_id: string | null;
  source: "activity" | "transport" | "custom";
  planned_cents: number | null;
  actual_cents: number;
  expense_ids: string[];
  settled: boolean;
}
export interface BudgetOverview {
  rows: BudgetRow[];
  planned_cents: number;
  projected_cents: number;
  unknown_count: number;
  budget_cents: number | null;
}

export interface ReceiptDraft {
  title: string;
  amount: string | null;
  category: ExpenseCategory;
  currency: "CNY" | "other" | "unknown";
  kind: "expense" | "refund" | "reimbursement" | "unknown";
  warnings: string[];
}

export interface RouteLeg {
  from_id: string;
  to_id: string;
  mode: TravelMode;
  status: "ok" | "planned" | "unavailable" | "unmatched";
  distance_meters: number | null;
  duration_seconds: number | null;
  walking_meters: number | null;
  cost: number | null;
  message: string;
  cost_basis: "per_person" | "total" | "unknown";
  cost_source: "reference" | "estimate" | "manual" | "unknown";
  source_indices: number[];
}

export interface DayPlan {
  day: number;
  title: string;
  activities: ScheduledActivity[];
  travel_mode: TravelMode;
  legs: RouteLeg[];
  checked_at: string | null;
  warnings: string[];
  travel_minutes: number | null;
  walking_meters: number | null;
  known_cost: number;
  unknown_cost_count: number;
  estimated_cost: number;
  schedule_notes: string[];
  replan_context: ReplanContext | null;
  linked_replan?: {
    first_day: number;
    second_day: number;
    rest_minutes: number;
    next_departure: string;
  } | null;
}

export interface Itinerary {
  days: DayPlan[];
  map_status: "unconfigured" | "ready" | "partial";
  expenses: Expense[];
  adjustments: ExpenseAdjustment[];
  ledger_summary: LedgerSummary;
}

export interface AdjustmentInput {
  expense_id: string;
  kind: "refund" | "reimbursement";
  amount: string;
  status: "pending" | "received";
  notes: string;
}
export interface ExpenseAdjustment {
  id: string;
  expense_id: string;
  kind: "refund" | "reimbursement";
  amount_cents: number;
  status: "pending" | "received";
  notes: string;
  created_at: string;
  updated_at: string;
}
export interface LedgerSummary {
  paid_cents: number;
  refunded_cents: number;
  reimbursed_cents: number;
  pending_refund_cents: number;
  pending_reimbursement_cents: number;
  net_expense_cents: number;
  net_outlay_cents: number;
  over_recovered_cents: number;
}
