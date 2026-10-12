/**
 * 后端 API 客户端。
 *
 * 设计要点：
 * - 统一解析后端的 `{error: {code, message, trace_id}}` 结构，
 *   把「说人话」的 message 直接交给界面展示；
 * - 非 2xx 一律抛 ApiError，调用方只需处理一种错误形态。
 */

import type {
  BranchInfo,
  ChatResponse,
  ConfigCheck,
  ConnectionTest,
  DetailLevel,
  ProvidersResponse,
  SessionDetail,
  SessionInfo,
  VersionDetail,
  VersionInfo,
} from "./types";
import type { SettingsPayload, SettingsSaveResult, SettingsUpdate } from "./settings-types";

export const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE ?? "http://127.0.0.1:8000";

export class ApiError extends Error {
  constructor(
    public readonly code: string,
    message: string,
    public readonly status: number,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${API_BASE}${path}`, {
      ...init,
      headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
      cache: "no-store",
    });
  } catch {
    throw new ApiError(
      "BACKEND_UNREACHABLE",
      `连不上后端服务（${API_BASE}）。请确认后端已启动。`,
      0,
    );
  }

  if (!response.ok) {
    let code = "UNKNOWN_ERROR";
    let message = `请求失败（HTTP ${response.status}）`;
    try {
      const body = await response.json();
      if (body?.error) {
        code = body.error.code ?? code;
        message = body.error.message ?? message;
      }
    } catch {
      /* 响应体不是 JSON，沿用默认提示 */
    }
    throw new ApiError(code, message, response.status);
  }

  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

export const api = {
  // ---- 系统 ----
  health: () => request<{ status: string; name: string; version: string }>("/api/system/health"),
  config: () => request<ConfigCheck>("/api/system/config"),
  providers: () => request<ProvidersResponse>("/api/system/providers"),
  testLlm: () => request<ConnectionTest>("/api/system/test-llm", { method: "POST" }),
  testSearch: () => request<ConnectionTest>("/api/system/test-search", { method: "POST" }),
  getSettings: () => request<SettingsPayload>("/api/system/settings"),
  saveSettings: (payload: SettingsUpdate) =>
    request<SettingsSaveResult>("/api/system/settings", {
      method: "PUT",
      body: JSON.stringify(payload),
    }),
  /** 向服务商查询可用模型（模型名会随官方调整，故不让用户手写）。 */
  getModels: () => request<{ models: string[]; message: string }>("/api/system/models"),

  // ---- 会话 ----
  listSessions: (params?: { archived?: boolean; q?: string }) => {
    const search = new URLSearchParams();
    if (params?.archived) search.set("archived", "true");
    if (params?.q) search.set("q", params.q);
    const suffix = search.toString() ? `?${search}` : "";
    return request<{ items: SessionInfo[]; total: number }>(`/api/sessions${suffix}`);
  },
  createSession: (title: string) =>
    request<SessionDetail>("/api/sessions", {
      method: "POST",
      body: JSON.stringify({ title }),
    }),
  getSession: (id: string) => request<SessionDetail>(`/api/sessions/${id}`),
  updateSession: (id: string, patch: { title?: string; archived?: boolean }) =>
    request<SessionDetail>(`/api/sessions/${id}`, {
      method: "PATCH",
      body: JSON.stringify(patch),
    }),
  deleteSession: (id: string) =>
    request<{ deleted: boolean }>(`/api/sessions/${id}`, { method: "DELETE" }),

  // ---- 分支 / 版本 ----
  listBranches: (sessionId: string) =>
    request<{ items: BranchInfo[] }>(`/api/sessions/${sessionId}/branches`),
  createBranch: (
    sessionId: string,
    payload: { title: string; parent_branch_id?: string; fork_version_id?: string },
  ) =>
    request<{ items: BranchInfo[] }>(`/api/sessions/${sessionId}/branches`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  setPrimaryBranch: (branchId: string) =>
    request<{ items: BranchInfo[] }>(`/api/branches/${branchId}/primary`, { method: "POST" }),
  listVersions: (branchId: string) =>
    request<{ items: VersionInfo[] }>(`/api/branches/${branchId}/versions`),
  getVersion: (versionId: string) => request<VersionDetail>(`/api/versions/${versionId}`),
  /** 读取该分支的对话历史（存在后端的 checkpoint 里）。 */
  getMessages: (branchId: string) =>
    request<{ messages: { role: string; content: string }[] }>(
      `/api/branches/${branchId}/messages`,
    ),

  // ---- 对话 ----
  chat: (payload: {
    message: string;
    session_id: string;
    branch_id?: string;
    detail_level?: DetailLevel;
  }) =>
    request<ChatResponse>("/api/chat", { method: "POST", body: JSON.stringify(payload) }),

  // ---- 导出（直接触发浏览器下载）----
  exportUrl: (versionId: string, format: "md" | "html") =>
    `${API_BASE}/api/versions/${versionId}/export?format=${format}`,
};
