import { DETAIL_LEVEL_LABELS, type DetailLevel } from "./types";

export interface ChatAttempt {
  message: string;
  request_id: string;
  detail_level: DetailLevel;
}

interface ChatDraft {
  input: string;
  attempt: ChatAttempt | null;
}

const key = (branch: string) => `travelflow:chat-draft:${branch}`;

export function readChatDraft(branch: string): ChatDraft {
  try {
    const data = JSON.parse(sessionStorage.getItem(key(branch)) || "null");
    const attempt = data?.attempt;
    const validAttempt: ChatAttempt | null =
      attempt &&
      typeof attempt.message === "string" &&
      typeof attempt.request_id === "string" &&
      /^[a-zA-Z0-9_-]{8,80}$/.test(attempt.request_id) &&
      Object.hasOwn(DETAIL_LEVEL_LABELS, attempt.detail_level)
        ? {
            message: attempt.message,
            request_id: attempt.request_id,
            detail_level: attempt.detail_level,
          }
        : null;
    return {
      input:
        (typeof data?.input === "string" ? data.input : "") ||
        validAttempt?.message ||
        "",
      attempt: validAttempt,
    };
  } catch {
    return { input: "", attempt: null };
  }
}

export function saveChatDraft(
  branch: string,
  input: string,
  attempt: ChatAttempt | null,
) {
  try {
    if (!input && !attempt) sessionStorage.removeItem(key(branch));
    else
      sessionStorage.setItem(key(branch), JSON.stringify({ input, attempt }));
  } catch {
    // 浏览器禁用存储时保留内存中的输入，不能因此阻止发送。
  }
}
