"use client";

import { useState } from "react";
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
  const [useMemory, setUseMemory] = useState(false);
  const [spending, setSpending] = useState(false);
  const [draft, setDraft] = useState<RecapDraft | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function generate(kind: "recap" | "social") {
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
      setBusy(false);
      onBusy(false);
    }
  }
  return (
    <section
      aria-label="AI回顾写作"
      className="space-y-3 rounded-xl border border-teal-100 bg-teal-50/50 p-4"
    >
      <p className="text-sm font-semibold text-teal-900">让 AI 帮你写</p>
      <p className="text-xs leading-6 text-slate-500">
        点击生成会将已完成活动和勾选的素材发送给当前模型。照片、票据、消费备注和未完成地点不发送。生成只出草稿，采用后仍可修改，保存才写入版本。
      </p>
      <div className="flex flex-wrap gap-3 text-xs text-slate-600">
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
        <button
          type="button"
          disabled={disabled || busy}
          onClick={() => void generate("recap")}
          className="rounded-lg bg-teal-700 px-3 py-2 text-xs text-white disabled:opacity-50"
        >
          AI写旅行回顾
        </button>
        <button
          type="button"
          disabled={disabled || busy}
          onClick={() => void generate("social")}
          className="rounded-lg border border-teal-200 bg-white px-3 py-2 text-xs text-teal-800 disabled:opacity-50"
        >
          AI写朋友圈文案
        </button>
      </div>
      {busy && (
        <p role="status" className="text-xs text-teal-800">
          正在生成草稿，原文字保留…
        </p>
      )}
      {error && (
        <p role="alert" className="text-xs text-red-700">
          {error}
        </p>
      )}
      {draft && (
        <section
          aria-label="AI文字草稿"
          className="space-y-3 rounded-lg bg-white p-3 text-sm leading-7 text-slate-700"
        >
          <p className="font-semibold">{draft.title}</p>
          <p className="whitespace-pre-wrap break-words">{draft.text}</p>
          <p className="text-xs text-slate-500">
            请核对经历与金额。采用将替换
            {draft.kind === "recap" ? "「我的回忆」文字" : "「朋友圈文案」文字"}
            ，不会改标题、照片或实际记录。
          </p>
          <div className="flex flex-wrap gap-3 text-xs text-teal-800">
            <button
              type="button"
              disabled={disabled || busy}
              onClick={() => {
                onAdopt(draft);
                setDraft(null);
              }}
            >
              采用这份草稿
            </button>
            <button
              type="button"
              disabled={disabled || busy}
              onClick={() => setDraft(null)}
            >
              丢弃草稿
            </button>
          </div>
        </section>
      )}
    </section>
  );
}
