/** 与后端 DTO 对应的类型定义（见 backend/app/domain/schemas.py）。 */

export type DetailLevel = "L1_minimal" | "L2_standard" | "L3_detailed" | "L4_butler";

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

export interface ChatResponse {
  session_id: string;
  branch_id: string;
  version_id: string | null;
  status: string;
  reply: string;
  plan: string;
  missing_fields: string[];
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
  todos: { kind: string; what: string; when: string | null; note: string | null }[];
  searched_at: string | null;
}
