/**
 * 后端 API 客户端。
 *
 * 设计要点：
 * - 统一解析后端的 `{error: {code, message, trace_id}}` 结构，
 *   把「说人话」的 message 直接交给界面展示；
 * - 非 2xx 一律抛 ApiError，调用方只需处理一种错误形态。
 */

import type {
  Activity,
  ActivityProgress,
  AdjustmentInput,
  ReceiptDraft,
  ExpenseInput,
  Booking,
  ReplanInput,
  BranchInfo,
  ChatResponse,
  ChatClarification,
  ConfigCheck,
  ConnectionTest,
  DetailLevel,
  ProvidersResponse,
  Place,
  SessionDetail,
  SessionInfo,
  VersionDetail,
  VersionInfo,
  TravelMode,
  RecapView,
} from "./types";
import type {
  SettingsPayload,
  SettingsSaveResult,
  SettingsUpdate,
} from "./settings-types";

export const API_BASE = "/api/backend";

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
      "网络连接暂时中断，已保存的行程不受影响，请稍后重试。",
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
  getRecap: (versionId: string) =>
    request<RecapView>(`/api/versions/${versionId}/recap`),
  saveRecap: (
    versionId: string,
    payload: { title: string; body: string; source_fingerprint: string },
  ) =>
    request<VersionDetail>(`/api/versions/${versionId}/recap`, {
      method: "PUT",
      body: JSON.stringify(payload),
    }),
  deleteBudget: (versionId: string, itemId: string) =>
    request<VersionDetail>(
      `/api/versions/${versionId}/budget-items/${encodeURIComponent(itemId)}`,
      { method: "DELETE" },
    ),
  updateBudget: (versionId: string, itemId: string, amount: string | null) =>
    request<VersionDetail>(
      `/api/versions/${versionId}/budget-items/${encodeURIComponent(itemId)}`,
      { method: "PUT", body: JSON.stringify({ amount }) },
    ),
  addBudget: (
    versionId: string,
    payload: {
      title: string;
      category: string;
      amount: string | null;
      day: number | null;
      request_id: string;
    },
  ) =>
    request<VersionDetail>(`/api/versions/${versionId}/budget-items`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  placePhotos: (versionId: string, activityId: string) =>
    request<{ photos: string[] }>(
      `/api/versions/${versionId}/activities/${encodeURIComponent(activityId)}/photos`,
    ),
  convertLocation: (longitude: number, latitude: number) =>
    request<Place>("/api/maps/convert-location", {
      method: "POST",
      body: JSON.stringify({ longitude, latitude }),
    }),
  // ---- 系统 ----
  health: () =>
    request<{ status: string; name: string; version: string }>(
      "/api/system/health",
    ),
  config: () => request<ConfigCheck>("/api/system/config"),
  providers: () => request<ProvidersResponse>("/api/system/providers"),
  testLlm: () =>
    request<ConnectionTest>("/api/system/test-llm", { method: "POST" }),
  testSearch: () =>
    request<ConnectionTest>("/api/system/test-search", { method: "POST" }),
  testMap: () =>
    request<ConnectionTest>("/api/system/test-map", { method: "POST" }),
  getSettings: () => request<SettingsPayload>("/api/system/settings"),
  saveSettings: (payload: SettingsUpdate) =>
    request<SettingsSaveResult>("/api/system/settings", {
      method: "PUT",
      body: JSON.stringify(payload),
    }),
  /** 向服务商查询可用模型（模型名会随官方调整，故不让用户手写）。 */
  getModels: () =>
    request<{ models: string[]; message: string }>("/api/system/models"),

  // ---- 会话 ----
  listSessions: (params?: { archived?: boolean; q?: string }) => {
    const search = new URLSearchParams();
    if (params?.archived) search.set("archived", "true");
    if (params?.q) search.set("q", params.q);
    const suffix = search.toString() ? `?${search}` : "";
    return request<{ items: SessionInfo[]; total: number }>(
      `/api/sessions${suffix}`,
    );
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
    payload: {
      title: string;
      parent_branch_id?: string;
      fork_version_id?: string;
    },
  ) =>
    request<{ items: BranchInfo[] }>(`/api/sessions/${sessionId}/branches`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  setPrimaryBranch: (branchId: string) =>
    request<{ items: BranchInfo[] }>(`/api/branches/${branchId}/primary`, {
      method: "POST",
    }),
  listVersions: (branchId: string) =>
    request<{ items: VersionInfo[] }>(`/api/branches/${branchId}/versions`),
  getVersion: (versionId: string) =>
    request<VersionDetail>(`/api/versions/${versionId}`),
  organiseItinerary: (versionId: string) =>
    request<VersionDetail>(`/api/versions/${versionId}/itinerary`, {
      method: "POST",
    }),
  searchPlaces: (keyword: string, city: string) =>
    request<{ items: Place[] }>(
      `/api/maps/places?${new URLSearchParams({ keyword, city })}`,
    ),
  updateActivity: (
    versionId: string,
    day: number,
    activityId: string,
    activity: Activity,
    placeId?: string,
  ) =>
    request<VersionDetail>(
      `/api/versions/${versionId}/days/${day}/activities/${activityId}`,
      {
        method: "PUT",
        body: JSON.stringify({ activity, place_id: placeId ?? null }),
      },
    ),
  recalculateDay: (versionId: string, day: number, travelMode: TravelMode) =>
    request<VersionDetail>(`/api/versions/${versionId}/days/${day}/routes`, {
      method: "POST",
      body: JSON.stringify({ travel_mode: travelMode }),
    }),
  updateProgress: (
    versionId: string,
    day: number,
    activityId: string,
    progress: ActivityProgress,
  ) =>
    request<VersionDetail>(
      `/api/versions/${versionId}/days/${day}/activities/${activityId}/progress`,
      {
        method: "PATCH",
        body: JSON.stringify({ progress }),
      },
    ),
  addExpense: (versionId: string, expense: ExpenseInput, requestId: string) =>
    request<VersionDetail>(`/api/versions/${versionId}/expenses`, {
      method: "POST",
      body: JSON.stringify({ ...expense, request_id: requestId }),
    }),
  recordBooking: (
    versionId: string,
    day: number,
    activityId: string,
    booking: Booking | null,
  ) =>
    request<VersionDetail>(
      `/api/versions/${versionId}/days/${day}/activities/${activityId}/booking`,
      {
        method: "PATCH",
        body: JSON.stringify({ booking }),
      },
    ),
  replanDay: (versionId: string, day: number, context: ReplanInput) =>
    request<VersionDetail>(`/api/versions/${versionId}/days/${day}/replan`, {
      method: "POST",
      body: JSON.stringify(context),
    }),
  editExpense: (versionId: string, expenseId: string, expense: ExpenseInput) =>
    request<VersionDetail>(`/api/versions/${versionId}/expenses/${expenseId}`, {
      method: "PUT",
      body: JSON.stringify(expense),
    }),
  deleteExpense: (versionId: string, expenseId: string) =>
    request<VersionDetail>(`/api/versions/${versionId}/expenses/${expenseId}`, {
      method: "DELETE",
    }),
  dayMapUrl: (versionId: string, day: number) =>
    `${API_BASE}/api/versions/${versionId}/days/${day}/map`,
  dayMapImage: async (
    versionId: string,
    day: number,
    signal?: AbortSignal,
  ): Promise<Blob> => {
    let response: Response;
    try {
      response = await fetch(
        `${API_BASE}/api/versions/${versionId}/days/${day}/map`,
        {
          signal: signal
            ? AbortSignal.any([signal, AbortSignal.timeout(22_000)])
            : AbortSignal.timeout(22_000),
          cache: "no-store",
        },
      );
    } catch {
      throw new ApiError(
        "MAP_UNAVAILABLE",
        "地图连接暂时不可用，行程连线仍可查看。",
        0,
      );
    }
    if (!response.ok) {
      let message = "地图暂时不可用，行程连线仍可查看。";
      try {
        const data = await response.json();
        message = data?.error?.message ?? message;
      } catch {
        /* 保留安全提示 */
      }
      throw new ApiError("MAP_UNAVAILABLE", message, response.status);
    }
    if (!response.headers.get("content-type")?.startsWith("image/")) {
      throw new ApiError(
        "MAP_UNAVAILABLE",
        "地图服务暂未返回图片，已保留行程连线。",
        502,
      );
    }
    return response.blob();
  },
  /** 读取该分支的对话历史（存在后端的 checkpoint 里）。 */
  getMessages: (branchId: string) =>
    request<{
      messages: { role: string; content: string }[];
      clarification?: ChatClarification | null;
    }>(`/api/branches/${branchId}/messages`),
  addAdjustment: (
    versionId: string,
    data: AdjustmentInput,
    requestId: string,
  ) =>
    request<VersionDetail>(`/api/versions/${versionId}/expense-adjustments`, {
      method: "POST",
      body: JSON.stringify({ ...data, request_id: requestId }),
    }),
  editAdjustment: (versionId: string, id: string, data: AdjustmentInput) =>
    request<VersionDetail>(
      `/api/versions/${versionId}/expense-adjustments/${id}`,
      { method: "PUT", body: JSON.stringify(data) },
    ),
  deleteAdjustment: (versionId: string, id: string) =>
    request<VersionDetail>(
      `/api/versions/${versionId}/expense-adjustments/${id}`,
      { method: "DELETE" },
    ),

  // ---- 对话 ----
  chat: (payload: {
    message: string;
    session_id: string;
    branch_id?: string;
    detail_level?: DetailLevel;
    request_id?: string;
  }) =>
    request<ChatResponse>("/api/chat", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  uploadReceipt: (versionId: string, file: File) =>
    request<{ id: string; mime: string; width: number; height: number }>(
      `/api/versions/${versionId}/receipts`,
      {
        method: "POST",
        body: file,
        headers: { "Content-Type": file.type || "application/octet-stream" },
      },
    ),
  receiptUrl: (versionId: string, id: string, original = false) =>
    `${API_BASE}/api/versions/${versionId}/receipts/${id}${original ? "?original=true" : ""}`,
  recogniseReceipt: (versionId: string, id: string) =>
    request<ReceiptDraft>(
      `/api/versions/${versionId}/receipts/${id}/recognise`,
      { method: "POST" },
    ),

  // ---- 导出（直接触发浏览器下载）----
  exportUrl: (versionId: string, format: "md" | "html") =>
    `${API_BASE}/api/versions/${versionId}/export?format=${format}`,
};

/** 流式对话的事件回调。 */
export interface ChatStreamHandlers {
  /** 阶段进度，例如「正在检索航班、住宿与景点信息…」 */
  onProgress?: (message: string, stage: string) => void;
  /** 增量文本（路书正在逐字生成） */
  onToken?: (text: string) => void;
  /** 提示（如部分检索失败） */
  onNotice?: (message: string) => void;
  /** 完成，结构与 POST /api/chat 的响应一致 */
  onDone?: (result: ChatResponse) => void;
}

function parseSseBlock(block: string): { event: string; data: string } | null {
  let event = "message";
  const dataLines: string[] = [];
  for (const line of block.split("\n")) {
    if (line.startsWith("event: ")) event = line.slice(7).trim();
    else if (line.startsWith("data: ")) dataLines.push(line.slice(6));
  }
  return dataLines.length ? { event, data: dataLines.join("\n") } : null;
}

/**
 * 流式发送消息（SSE）。
 *
 * 为什么用 fetch 而不是 EventSource：EventSource 只能发 GET，
 * 而这里需要 POST 一个 JSON 请求体。
 */
export async function chatStream(
  payload: {
    message: string;
    session_id: string;
    branch_id?: string;
    detail_level?: DetailLevel;
    request_id?: string;
  },
  handlers: ChatStreamHandlers,
  signal?: AbortSignal,
): Promise<void> {
  let response: Response;
  try {
    response = await fetch(`${API_BASE}/api/chat/stream`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
      cache: "no-store",
      signal,
    });
  } catch {
    throw new ApiError(
      "BACKEND_UNREACHABLE",
      "网络连接暂时中断，已保存的行程不受影响，请稍后重试。",
      0,
    );
  }

  // 会话不存在之类的错误仍走普通 HTTP 状态码，这里按统一结构解析
  if (!response.ok || !response.body) {
    let code = "INTERNAL_ERROR";
    let message = `请求失败（HTTP ${response.status}）`;
    try {
      const body = await response.json();
      if (body?.error?.code) {
        code = body.error.code;
        message = body.error.message ?? message;
      }
    } catch {
      // 保持默认文案
    }
    throw new ApiError(code, message, response.status);
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let completed = false;

  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      buffer = buffer.replace(/\r\n/g, "\n");

      // SSE 以空行分隔一条消息；用 \n\n 切分并保留不完整尾巴
      let index = buffer.indexOf("\n\n");
      while (index !== -1) {
        const block = buffer.slice(0, index);
        buffer = buffer.slice(index + 2);

        const parsed = parseSseBlock(block);
        if (parsed) {
          let data: Record<string, unknown>;
          try {
            data = JSON.parse(parsed.data);
          } catch {
            index = buffer.indexOf("\n\n");
            continue;
          }

          if (parsed.event === "custom") {
            const type = data.type as string;
            if (type === "progress") {
              handlers.onProgress?.(
                (data.message as string) ?? "",
                (data.stage as string) ?? "",
              );
            } else if (type === "token") {
              handlers.onToken?.((data.text as string) ?? "");
            } else if (type === "notice") {
              handlers.onNotice?.((data.message as string) ?? "");
            }
          } else if (parsed.event === "done") {
            if (
              typeof data.reply !== "string" ||
              typeof data.branch_id !== "string" ||
              typeof data.status !== "string"
            ) {
              throw new ApiError(
                "STREAM_INTERRUPTED",
                "回复未完整收到，输入已保留，请重试这条消息。",
                0,
              );
            }
            completed = true;
            handlers.onDone?.(data as unknown as ChatResponse);
          } else if (parsed.event === "error") {
            throw new ApiError(
              (data.code as string) ?? "INTERNAL_ERROR",
              (data.message as string) ?? "服务出错了",
              200,
            );
          }
        }
        index = buffer.indexOf("\n\n");
      }
    }
    if (!completed) {
      throw new ApiError(
        "STREAM_INTERRUPTED",
        "回复中断，结果可能已保存。可以重试这条消息，系统会核对上次结果。",
        0,
      );
    }
  } catch (error) {
    if (error instanceof ApiError) throw error;
    throw new ApiError(
      "STREAM_INTERRUPTED",
      "回复连接中断，输入已保留。可以重试这条消息，系统会核对上次结果。",
      0,
    );
  } finally {
    reader.releaseLock();
  }
}
