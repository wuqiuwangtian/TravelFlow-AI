"use client";

import { useRef, useState } from "react";
import { ActionFeedback, Button } from "./ui";

export function RenameForm({
  title,
  onSave,
  onClose,
}: {
  title: string;
  onSave: (title: string) => Promise<void>;
  onClose: () => void;
}) {
  const [draft, setDraft] = useState(title);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  const lock = useRef(false);
  const input = useRef<HTMLInputElement>(null);
  async function save() {
    if (lock.current) return;
    const next = draft.trim();
    if (!next) {
      setError("请填写旅行名称，或取消改名。");
      input.current?.focus();
      return;
    }
    lock.current = true;
    setPending(true);
    setError("");
    try {
      await onSave(next);
      onClose();
    } catch (err) {
      setError(
        err instanceof Error ? err.message : "改名未完成，输入已保留，请重试。",
      );
      input.current?.focus();
    } finally {
      lock.current = false;
      setPending(false);
    }
  }
  return (
    <form
      aria-label="修改旅行名称"
      className="min-w-0"
      onSubmit={(event) => {
        event.preventDefault();
        void save();
      }}
    >
      <label className="text-xs text-[var(--tf-muted)]">
        旅行名称
        <input
          ref={input}
          autoFocus
          maxLength={120}
          value={draft}
          readOnly={pending}
          className="tf-form-control mt-1"
          onChange={(event) => setDraft(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Escape" && !lock.current) {
              event.preventDefault();
              onClose();
            }
          }}
        />
      </label>
      <div className="mt-2 flex flex-wrap gap-2">
        <Button
          type="submit"
          size="sm"
          loading={pending}
          loadingLabel="正在保存…"
        >
          保存名称
        </Button>
        <Button type="button" size="sm" disabled={pending} onClick={onClose}>
          取消改名
        </Button>
      </div>
      <ActionFeedback
        error={error}
        pending={pending ? "正在保存旅行名称…" : undefined}
      />
    </form>
  );
}
