export type StudioKind =
  | "input"
  | "search"
  | "generate"
  | "output"
  | "plan"
  | "route"
  | "budget"
  | "select"
  | "candidates"
  | "allocate";
export type StudioPort =
  | "text"
  | "sources"
  | "itinerary"
  | "budget"
  | "candidates";
export interface BudgetRules {
  meal_per_person_day_cents: number | null;
  lodging_per_room_night_cents: number | null;
  lodging_rooms: number | null;
  round_trip_per_person_cents: number | null;
  contingency_cents: number | null;
}
export interface StudioNode {
  id: string;
  kind: StudioKind;
  title: string;
  x: number;
  y: number;
  parameters: {
    text: string;
    prompt: string;
    temperature: number;
    max_results: number;
    travel_mode: "auto" | "walking" | "transit" | "driving";
    max_attractions_per_day: number | null;
    required_places: string;
    excluded_places: string;
    attraction_stay_minutes: number | null;
    route_order: "original" | "nearby";
    budget_rules: BudgetRules;
    candidate_limit: number;
    allocation_days: number | null;
    allocation_pace: "relaxed" | "balanced" | "full";
    allocation_start_time: string;
    allocation_buffer_minutes: number;
    allocation_default_stay_minutes: number;
  };
}
export interface StudioEdge {
  source: string;
  target: string;
  source_port: StudioPort;
  target_port:
    | "context"
    | "query"
    | "sources"
    | "content"
    | "itinerary"
    | "budget"
    | "candidates";
}
export interface StudioGraph {
  schema_version: 1;
  title: string;
  nodes: StudioNode[];
  edges: StudioEdge[];
}
export interface StudioSource {
  title: string;
  url: string;
  snippet: string;
  provider: string;
}
export interface NodeArtifact {
  text: string;
  sources: StudioSource[];
  candidates: {
    requirements: import("./types").TravelRequirements;
    places: {
      id: string;
      place_name: string;
      city: string;
      reason: string;
      duration_minutes: number | null;
      cost: number | null;
      cost_basis: "total" | "per_person" | "unknown";
      cost_source: "reference" | "estimate" | "unknown";
      source_indices: number[];
    }[];
    warnings: string[];
  } | null;
  plan: {
    requirements: import("./types").TravelRequirements;
    days: import("./types").DayPlan[];
    todos: import("./types").VersionDetail["todos"];
    warnings: string[];
    route_status: "unchecked" | "unconfigured" | "partial" | "ready";
    selection: {
      node_title: string;
      before_count: number;
      after_count: number;
      decisions: {
        day: number;
        activity_id: string;
        title: string;
        action: "removed" | "kept" | "duration";
        reason: string;
      }[];
    } | null;
    optimization: {
      day: number;
      before: string[];
      after: string[];
      before_meters: number | null;
      after_meters: number | null;
      before_travel_minutes: number | null;
      after_travel_minutes: number | null;
      changed: boolean;
      reason: string;
    }[];
    allocation: {
      pool_count: number;
      selected_count: number;
      pace: "relaxed" | "balanced" | "full";
      max_per_day: number;
      minutes_per_day: number;
      buffer_minutes: number;
      decisions: {
        candidate_id: string;
        place_name: string;
        day: number | null;
        reason: string;
      }[];
    } | null;
  } | null;
  budget: {
    plan_key: string;
    overview: import("./types").BudgetOverview;
    categories: Record<string, number>;
    warnings: string[];
    rules: BudgetRules;
    supplements: {
      item: Omit<
        import("./types").BudgetRow,
        "actual_cents" | "expense_ids" | "settled"
      >;
      explanation: string;
    }[];
  } | null;
}
export interface NodeExecution {
  node_id: string;
  status: "waiting" | "running" | "succeeded" | "cached" | "failed" | "blocked";
  input_key: string;
  artifact: NodeArtifact | null;
  error: string;
  error_code: string;
  elapsed_ms: number;
  produced_at: string | null;
}
export interface StudioRun {
  id: string;
  branch_id: string;
  revision: number;
  graph: StudioGraph;
  target_node: string | null;
  force: boolean;
  status: "running" | "succeeded" | "failed" | "interrupted";
  nodes: NodeExecution[];
  created_at: string;
  finished_at: string | null;
}
export interface StudioProject {
  revision: number;
  graph: StudioGraph | null;
  updated_at: string | null;
  runs: StudioRun[];
  requirements_seed: string;
}
export interface StudioTemplate {
  id: string;
  title: string;
  description: string;
  graph: StudioGraph;
}

export interface UserTemplateSummary {
  id: string;
  title: string;
  description: string;
  revision: number;
  node_count: number;
  trip_input_count: number;
  created_at: string;
  updated_at: string;
}
export interface UserTemplate extends UserTemplateSummary {
  graph: StudioGraph;
  trip_input_ids: string[];
}
export interface SaveUserTemplate {
  request_id: string;
  expected_revision: number;
  title: string;
  description: string;
  graph: StudioGraph;
  trip_input_ids: string[];
}

export interface AdoptionChange {
  day: number;
  activity_id: string;
  title: string;
  status: "added" | "removed" | "updated" | "preserved" | "unchanged";
  reason: string;
  before: string;
  after: string;
}
export interface AdoptionPreview {
  request_id: string;
  branch_id: string;
  run_id: string;
  node_id: string;
  source_title: string;
  source_revision: number;
  source_created_at: string;
  source_key: string;
  base_key: string;
  base_version_id: string | null;
  before_requirements: import("./types").TravelRequirements;
  requirements: import("./types").TravelRequirements;
  itinerary: import("./types").Itinerary;
  markdown: string;
  budget_before: import("./types").BudgetOverview | null;
  budget_after: import("./types").BudgetOverview;
  changes: AdoptionChange[];
  retained: string[];
  conflicts: string[];
  warnings: string[];
  created_at: string;
}
export interface AdoptionContext {
  base_version_id: string | null;
  base_key: string;
  source_key: string;
  blocker: string | null;
  preview: AdoptionPreview | null;
}
