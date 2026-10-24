export type StudioKind = "input" | "search" | "generate" | "output";
export type StudioPort = "text" | "sources";
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
  };
}
export interface StudioEdge {
  source: string;
  target: string;
  source_port: StudioPort;
  target_port: "context" | "query" | "sources" | "content";
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
