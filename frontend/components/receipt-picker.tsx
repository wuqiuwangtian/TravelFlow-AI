"use client";

import { useState } from "react";
import { Button } from "@/components/ui";
import { api } from "@/lib/api";
import type { ReceiptDraft } from "@/lib/types";

export function ReceiptGallery({
  versionId,
  ids,
}: {
  versionId: string;
  ids: string[];
}) {
  return (
    <div className="trip-ui mt-2 flex flex-wrap gap-3">
      {ids.map((id, i) => (
        <div key={id} className="w-28">
          <a
            href={api.receiptUrl(versionId, id)}
            className="trip-receipt-preview"
            target="_blank"
            rel="noopener noreferrer"
          >
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img
              src={api.receiptUrl(versionId, id)}
              alt={`消费凭证${i + 1}`}
              className="h-20 w-28 rounded-lg border border-[var(--tf-line)] object-contain bg-[var(--tf-paper)]"
            />
          </a>
          <a
            href={api.receiptUrl(versionId, id, true)}
            className="tf-action-link mt-1"
          >
            下载原图
          </a>
        </div>
      ))}
    </div>
  );
}

export function ReceiptPicker({
  versionId,
  ids,
  disabled,
  onChange,
  onDraft,
  onBusy,
}: {
  versionId: string;
  ids: string[];
  disabled: boolean;
  onChange: (ids: string[]) => void;
  onDraft: (draft: ReceiptDraft) => void;
  onBusy: (busy: boolean) => void;
}) {
  const [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [notice, setNotice] = useState("");
  async function run(work: () => Promise<void>) {
    setBusy(true);
    onBusy(true);
    setError("");
    setNotice("");
    try {
      await work();
    } catch (e) {
      setError(
        e instanceof Error ? e.message : "凭证处理失败，可继续手动记账。",
      );
    } finally {
      setBusy(false);
      onBusy(false);
    }
  }
  return (
    <div className="trip-ui rounded-[var(--tf-radius)] border border-[var(--tf-line)] p-3">
      <div className="flex flex-wrap items-center gap-3">
        <p className="text-xs font-medium text-[var(--tf-muted)]">
          图片凭证（选填）
        </p>
        <label
          className="trip-upload"
          data-disabled={disabled || busy || ids.length >= 6 || undefined}
        >
          添加凭证图片
          <input
            aria-label="添加凭证图片"
            type="file"
            accept="image/png,image/jpeg,image/webp"
            className="sr-only"
            disabled={disabled || busy || ids.length >= 6}
            onChange={(e) => {
              const file = e.target.files?.[0];
              e.target.value = "";
              if (!file) return;
              if (file.size > 4 * 1024 * 1024) {
                setError("请选择4MB以内的图片。");
                return;
              }
              void run(async () => {
                const result = await api.uploadReceipt(versionId, file);
                onChange([...new Set([...ids, result.id])]);
                setNotice("图片已保存在本机，保存消费后会关联这张凭证。");
              });
            }}
          />
        </label>
        {busy && (
          <span role="status" className="text-xs text-[var(--tf-muted)]">
            正在处理凭证…
          </span>
        )}
      </div>
      <p className="mt-2 text-[11px] leading-5 text-[var(--tf-muted)]">
        支持PNG、JPEG、WebP，每张4MB以内，最多6张。点击识别时，会将去除元数据后的预览发送给当前模型，原图保留在本机。
      </p>
      {ids.length > 0 && (
        <>
          <ReceiptGallery versionId={versionId} ids={ids} />
          <div className="mt-3 flex flex-wrap gap-2">
            {ids.map((id, index) => (
              <div
                key={id}
                className="flex gap-2 rounded-lg bg-[var(--tf-surface)] px-2 py-1.5 text-[11px]"
              >
                <Button
                  type="button"
                  disabled={disabled || busy}
                  onClick={() =>
                    void run(async () => {
                      const draft = await api.recogniseReceipt(versionId, id);
                      onDraft(draft);
                      setNotice(
                        [
                          "识别结果已填入草稿，请核对内容与金额后保存。",
                          ...draft.warnings,
                        ].join(" "),
                      );
                    })
                  }
                >
                  识别第{index + 1}张凭证
                </Button>
                <Button
                  type="button"
                  disabled={disabled || busy}
                  onClick={() => onChange(ids.filter((value) => value !== id))}
                >
                  移除第{index + 1}张
                </Button>
              </div>
            ))}
          </div>
        </>
      )}
      {error && (
        <p role="alert" className="mt-2 text-xs text-[var(--tf-danger)]">
          {error}
        </p>
      )}
      {notice && (
        <p
          role="status"
          className="mt-2 text-xs leading-5 text-[var(--tf-accent)]"
        >
          {notice}
        </p>
      )}
    </div>
  );
}
