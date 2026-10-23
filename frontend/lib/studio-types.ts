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
