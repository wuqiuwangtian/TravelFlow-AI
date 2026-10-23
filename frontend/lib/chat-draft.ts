import { DETAIL_LEVEL_LABELS, type DetailLevel } from "./types";

export interface ChatAttempt {
  message: string;
  request_id: string;
  detail_level: DetailLevel;
}

interface ChatDraft {
  input: string;
  attempt: ChatAttempt | null;
  retainedAttempt: ChatAttempt | null;
}

const key = (branch: string) => `travelflow:chat-draft:${branch}`;

function readAttempt(attempt: unknown): ChatAttempt | null {
  if (!attempt || typeof attempt !== "object") return null;
  const value = attempt as Partial<ChatAttempt>;
  return typeof value.message === "string" &&
    typeof value.request_id === "string" &&
    /^[a-zA-Z0-9_-]{8,80}$/.test(value.request_id) &&
    value.detail_level &&
    Object.hasOwn(DETAIL_LEVEL_LABELS, value.detail_level)
    ? {
        message: value.message,
        request_id: value.request_id,
        detail_level: value.detail_level,
      }
    : null;
}

export function readChatDraft(branch: string): ChatDraft {
  try {
    const data = JSON.parse(sessionStorage.getItem(key(branch)) || "null");
    const validAttempt = readAttempt(data?.attempt);
    const retainedAttempt = readAttempt(data?.retainedAttempt);
    return {
      input:
        (typeof data?.input === "string" ? data.input : "") ||
        validAttempt?.message ||
        "",
      attempt: validAttempt,
      retainedAttempt,
    };
  } catch {
    return { input: "", attempt: null, retainedAttempt: null };
  }
}

export function saveChatDraft(
  branch: string,
  input: string,
  attempt: ChatAttempt | null,
  retainedAttempt: ChatAttempt | null = null,
) {
  try {
    if (!input && !attempt && !retainedAttempt)
      sessionStorage.removeItem(key(branch));
    else
      sessionStorage.setItem(
        key(branch),
        JSON.stringify({ input, attempt, retainedAttempt }),
      );
  } catch {
    // 浏览器禁用存储时保留内存中的输入，不能因此阻止发送。
  }
}
