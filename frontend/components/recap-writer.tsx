"use client";

import { useRef, useState } from "react";
import { ActionFeedback, Button } from "@/components/ui";
import { api } from "@/lib/api";
import type { RecapDraft } from "@/lib/types";

export function RecapWriter({
  versionId,
  fingerprint,
  memory,
  disabled,
  onBusy,
  onAdopt,
}: {
  versionId: string;
  fingerprint: string;
  memory: string;
  disabled: boolean;
  onBusy: (value: boolean) => void;
  onAdopt: (draft: RecapDraft) => void;
}) {
  const locked = useRef(false);
  const [kind, setKind] = useState<"recap" | "social" | null>(null);
  const [notice, setNotice] = useState("");
  const [useMemory, setUseMemory] = useState(false);
  const [spending, setSpending] = useState(false);
  const [draft, setDraft] = useState<RecapDraft | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function generate(kind: "recap" | "social") {
    if (locked.current) return;
    locked.current = true;
    setKind(kind);
    setNotice("");
    setBusy(true);
    onBusy(true);
    setError("");
    try {
      setDraft(
        await api.generateRecapDraft(versionId, {
          kind,
          source_fingerprint: fingerprint,
          memory: useMemory ? memory : "",
          include_spending: spending,
        }),
      );
    } catch (err) {
      setError(
        err instanceof Error
          ? err.message
          : "生成未完成，原文字保留，可重试或手动编辑。",
      );
    } finally {
      locked.current = false;
      setKind(null);
      setBusy(false);
      onBusy(false);
    }
  }
  return (
    <section
      aria-label="AI回顾写作"
      className="space-y-3 rounded-[var(--tf-radius)] border border-[var(--tf-line)] bg-[var(--tf-paper)] p-4"
    >
      <p className="text-sm font-semibold text-[var(--tf-ink)]">让 AI 帮你写</p>
      <p className="text-xs leading-6 text-[var(--tf-muted)]">
        点击生成会将已完成活动和勾选的素材发送给当前模型。照片、票据、消费备注和未完成地点不发送。生成只出草稿，采用后仍可修改，保存才写入版本。
      </p>
      <div className="flex flex-wrap gap-3 text-xs text-[var(--tf-muted)]">
        <label className="flex gap-2">
          <input
            type="checkbox"
            checked={useMemory}
            disabled={disabled || busy}
            onChange={(e) => setUseMemory(e.target.checked)}
          />
          使用「我的回忆」文字
        </label>
        <label className="flex gap-2">
          <input
            type="checkbox"
            checked={spending}
            disabled={disabled || busy}
            onChange={(e) => setSpending(e.target.checked)}
          />
          包含已记支出汇总
        </label>
      </div>
      <div className="flex flex-wrap gap-3">
        <Button
          variant="primary"
          type="button"
          disabled={disabled || busy}
          loading={kind === "recap"}
          onClick={() => void generate("recap")}
        >
          AI写旅行回顾
        </Button>
        <Button
          type="button"
          disabled={disabled || busy}
          loading={kind === "social"}
          onClick={() => void generate("social")}
        >
          AI写朋友圈文案
        </Button>
      </div>
      <ActionFeedback
        pending={busy ? "正在生成草稿，原文字保留…" : undefined}
        error={error}
        message={notice}
      />
      {draft && (
        <section
          aria-label="AI文字草稿"
          className="space-y-3 rounded-lg bg-[var(--tf-surface)] p-3 text-sm leading-7 text-[var(--tf-ink)]"
        >
          <p className="font-semibold">{draft.title}</p>
          <p className="whitespace-pre-wrap break-words">{draft.text}</p>
          <p className="text-xs text-[var(--tf-muted)]">
            请核对经历与金额。采用将替换
            {draft.kind === "recap" ? "「我的回忆」文字" : "「朋友圈文案」文字"}
            ，不会改标题、照片或实际记录。
          </p>
          <div className="flex flex-wrap gap-3 text-xs text-[var(--tf-accent)]">
            <Button
              type="button"
              disabled={disabled || busy}
              onClick={() => {
                onAdopt(draft);
                setNotice("草稿已填入编辑区，保存回顾后才会写入版本。");
                setDraft(null);
              }}
            >
              采用这份草稿
            </Button>
            <Button
              type="button"
              disabled={disabled || busy}
              onClick={() => {
                setDraft(null);
                setNotice("草稿已丢弃，原文字保留。");
              }}
            >
              丢弃草稿
            </Button>
          </div>
        </section>
      )}
    </section>
  );
}
