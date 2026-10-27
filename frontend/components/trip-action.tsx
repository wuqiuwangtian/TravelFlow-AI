"use client";

import { useEffect, useRef, useState } from "react";
import { ApiError } from "@/lib/api";
import { ActionFeedback } from "@/components/ui";
import type { VersionDetail } from "@/lib/types";
import { scrollToElement } from "@/lib/utils";

type Commit = (work: () => Promise<VersionDetail>) => Promise<boolean>;
type Feedback = {
  target: string;
  pending: boolean;
  error?: string;
  settings?: boolean;
  message?: string;
};

export function useTripAction(onCommit: Commit) {
  const [feedback, setFeedback] = useState<Feedback | null>(null);
  const locked = useRef(false);
  async function run(
    target: string,
    message: string,
    work: () => Promise<VersionDetail>,
  ) {
    if (locked.current) return false;
    locked.current = true;
    setFeedback({ target, pending: true });
    let failure: unknown;
    let saved = false;
    try {
      const ok = await onCommit(async () => {
        try {
          const result = await work();
          saved = true;
          return result;
        } catch (error) {
          failure = error;
          throw error;
        }
      });
      setFeedback(
        ok
          ? { target, pending: false, message }
          : {
              target,
              pending: false,
              settings:
                failure instanceof ApiError &&
                failure.code === "LLM_AUTH_FAILED",
              error: saved
                ? "修改已保存，但页面更新失败。请刷新后核对，避免重复记录。"
                : failure instanceof Error
                  ? failure.message
                  : "暂时未能确认保存结果。草稿已保留，请核对后重试。",
            },
      );
      return ok;
    } catch (error) {
      setFeedback({
        target,
        pending: false,
        error: saved
          ? "修改已保存，但页面更新失败。请刷新后核对，避免重复记录。"
          : error instanceof Error
            ? error.message
            : "保存未完成，草稿已保留。",
      });
      return false;
    } finally {
      locked.current = false;
    }
  }
  return { feedback, run, clear: () => setFeedback(null) };
}

export function TripActionFeedback({
  action,
  target,
}: {
  action: ReturnType<typeof useTripAction>;
  target: string;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const feedback = action.feedback?.target === target ? action.feedback : null;
  useEffect(() => {
    if (feedback?.error) scrollToElement(ref.current, { block: "nearest" });
    if (feedback?.message) ref.current?.focus({ preventScroll: true });
  }, [feedback?.error, feedback?.message]);
  if (!feedback) return null;
  return (
    <div ref={ref} tabIndex={-1} className="mt-3 trip-action-feedback">
      <ActionFeedback
        pending={feedback.pending ? "正在保存，请稍候…" : undefined}
        error={feedback.error}
        message={feedback.message}
      />
      {feedback.settings && (
        <a className="tf-action-link" href="/settings">
          检查模型设置
        </a>
      )}
    </div>
  );
}
