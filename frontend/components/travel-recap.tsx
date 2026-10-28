"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { RecapWriter } from "@/components/recap-writer";
import { RecapPhotos } from "@/components/recap-photos";
import { TripActionFeedback, type TripAction } from "@/components/trip-action";
import { ActionFeedback, Spinner, Button } from "@/components/ui";
import { api, API_BASE } from "@/lib/api";
import type { VersionDetail, RecapView, RecapPhoto } from "@/lib/types";

export function TravelRecap({
  detail,
  disabled,
  action,
}: {
  detail: VersionDetail;
  disabled: boolean;
  action: TripAction;
}) {
  const [view, setView] = useState<RecapView | null>(null);
  const [title, setTitle] = useState("");
  const [body, setBody] = useState("");
  const [busy, setBusy] = useState(false);
  const [reading, setReading] = useState(!!detail.itinerary?.recap);
  const [copying, setCopying] = useState(false);
  const readLock = useRef(false);
  const [error, setError] = useState("");
  const [photos, setPhotos] = useState<RecapPhoto[]>([]);
  const [cover, setCover] = useState<string | null>(null);
  const [socialCopy, setSocialCopy] = useState("");
  const [copyNotice, setCopyNotice] = useState("");
  const [dirty, setDirtyState] = useState(false);
  const dirtyRef = useRef(false);
  function setDirty(value: boolean) {
    dirtyRef.current = value;
    setDirtyState(value);
  }
  useEffect(() => {
    if (!detail.itinerary?.recap) return;
    let cancelled = false;
    api
      .getRecap(detail.version.id)
      .then((result) => {
        if (!cancelled) {
          setView(result);
          if (!dirtyRef.current) {
            setTitle(result.title);
            setBody(result.body);
            setSocialCopy(result.social_copy);
            setPhotos(result.photos);
            setCover(result.cover_photo_id);
          }
        }
      })
      .catch((err) => {
        if (!cancelled)
          setError(err instanceof Error ? err.message : "回顾加载失败");
      })
      .finally(() => {
        if (!cancelled) setReading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [detail.version.id, detail.itinerary?.recap]);
  async function open() {
    if (readLock.current) return;
    readLock.current = true;
    setReading(true);
    setError("");
    try {
      const result = await api.getRecap(detail.version.id);
      setView(result);
      if (!dirtyRef.current) {
        setTitle(result.title);
        setBody(result.body);
        setSocialCopy(result.social_copy);
        setPhotos(result.photos);
        setCover(result.cover_photo_id);
        setDirty(false);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "回顾加载失败，请重试。");
    } finally {
      readLock.current = false;
      setReading(false);
    }
  }
  async function copySocial() {
    if (copying) return;
    setCopying(true);
    try {
      if (!navigator.clipboard) throw new Error("Clipboard unavailable");
      await navigator.clipboard.writeText(socialCopy);
      setCopyNotice("已复制文案，请自行选择图片并发布。");
    } catch {
      setCopyNotice("复制未成功，可在文案框中手动选择并复制。");
    } finally {
      setCopying(false);
    }
  }
  const field = "tf-form-control mt-1";
  const ended = !!detail.itinerary?.ended_at;
  const unconfirmed =
    view?.days.reduce((n, day) => n + day.unconfirmed.length, 0) ?? 0;
  return (
    <section aria-label="旅行回顾" className="tf-history history-editor">
      <h3 className="font-semibold text-[var(--tf-ink)]">把旅程留下来</h3>
      <div
        aria-label="旅行结束状态"
        className="mt-3 space-y-2 rounded-lg bg-[var(--tf-surface)] p-3 text-xs leading-6 text-[var(--tf-muted)]"
      >
        <p role="status">
          {ended
            ? "当前方案已结束，回顾已收录。仍可补记活动、账目和回忆。"
            : "当前方案尚未结束。结束后会收录到旅行回顾，原始活动和账目不变。"}
        </p>
        {ended && (
          <p>
            结束时间：
            {new Date(detail.itinerary!.ended_at!).toLocaleString("zh-CN")}
          </p>
        )}
        {view && (
          <p>
            还有{unconfirmed}项未确认到访、{view.unsettled_count}
            项未结算。结束不会自动补全这些记录。
          </p>
        )}
        {!view && <p>结束不会自动标记活动完成或费用结算，可继续补记。</p>}
        <div className="flex flex-wrap gap-3">
          <Button
            type="button"
            disabled={disabled || busy || reading || dirty}
            onClick={() =>
              void action.run(
                "recap-status",
                ended
                  ? "旅行已重新开启，原记录与回顾保留。"
                  : "旅行已结束并收录回顾，原活动与账目保留。",
                () => api.setJourneyStatus(detail.version.id, !ended),
              )
            }
          >
            {ended ? "重新开启旅行" : "结束旅行并收录回顾"}
          </Button>
          <Link href="/recaps" className="tf-action-link tf-page-link ">
            查看旅行回顾
          </Link>
        </div>
        <TripActionFeedback action={action} target="recap-status" />
        {dirty && <p>请先保存当前回顾的改动，再更改旅行状态。</p>}
      </div>
      <p className="mt-2 text-xs leading-6 text-[var(--tf-muted)]">
        根据已完成活动和已记支出整理回顾，没确认去过的地方会单独列出。回忆可以补写，原行程和账目保留。
      </p>
      {!view && (
        <Button
          variant="primary"
          type="button"
          disabled={busy || reading}
          loading={reading}
          onClick={() => void open()}
          className="mt-3"
        >
          打开旅行回顾
        </Button>
      )}
      {reading && <Spinner label="正在打开回顾…" />}
      {!view && <TripActionFeedback action={action} target="recap-save" />}
      {error && (
        <p role="alert" className="mt-3 text-sm text-[var(--tf-danger)]">
          {error}
          {view && (
            <Button
              type="button"
              className="mt-2"
              disabled={busy || reading}
              loading={reading}
              onClick={() => void open()}
            >
              重新读取回顾，保留我的输入
            </Button>
          )}
        </p>
      )}
      {view && (
        <div className="mt-4 space-y-4">
          {view.stale && (
            <p
              role="status"
              className="rounded-lg bg-[var(--tf-warning-soft)] p-3 text-xs text-[var(--tf-warning)]"
            >
              行程或账目已有变化。事实汇总已更新，请核对回忆与朋友圈文案后保存。
            </p>
          )}
          <div
            aria-label="回顾事实汇总"
            className="space-y-3 rounded-[var(--tf-radius)] bg-[var(--tf-surface)] p-4 text-xs leading-6 text-[var(--tf-muted)]"
          >
            {view.days.map((day) => (
              <div key={day.day}>
                <p className="font-semibold text-[var(--tf-accent)]">
                  第{day.day}天
                </p>
                <p>已完成：{day.completed.join("、") || "无记录"}</p>
                <p>已跳过：{day.skipped.join("、") || "无记录"}</p>
                <p>尚未确认到访：{day.unconfirmed.join("、") || "无记录"}</p>
              </div>
            ))}
            <p className="font-semibold text-[var(--tf-accent)]">
              已记净消费 ¥{(view.ledger.net_expense_cents / 100).toFixed(2)} ·{" "}
              {view.expense_count}笔消费
            </p>
            <p>
              已到账退款 ¥{(view.ledger.refunded_cents / 100).toFixed(2)} ·
              已到账报销 ¥{(view.ledger.reimbursed_cents / 100).toFixed(2)}
            </p>
            <p>
              待到账退款 ¥{(view.ledger.pending_refund_cents / 100).toFixed(2)}{" "}
              · 待到账报销 ¥
              {(view.ledger.pending_reimbursement_cents / 100).toFixed(2)}
            </p>
            <p>
              还有{view.unsettled_count}项未结算、{view.unknown_count}
              项未定价。这里只汇总已记录支出，不代表全部花费；预估不算实际消费。
            </p>
          </div>
          <form
            aria-label="编辑旅行回顾"
            onSubmit={(event) => {
              event.preventDefault();
              void action
                .run(
                  "recap-save",
                  "旅行回顾已保存，文字与照片已随当前版本更新。",
                  () =>
                    api.saveRecap(detail.version.id, {
                      title,
                      body,
                      source_fingerprint: view.source_fingerprint,
                      social_copy: socialCopy,
                      photos,
                      cover_photo_id: cover,
                    }),
                )
                .then((saved) => {
                  if (saved) setDirty(false);
                });
            }}
          >
            <div className="history-save-bar">
              <div className="flex flex-wrap items-center gap-3">
                <Button
                  variant="primary"
                  type="submit"
                  disabled={disabled || busy || reading}
                  loading={
                    action.feedback?.target === "recap-save" &&
                    action.feedback.pending
                  }
                >
                  保存旅行回顾
                </Button>
                <span className="text-xs text-[var(--tf-muted)]">
                  {dirty ? "有未保存的改动" : "当前回顾"}
                </span>
              </div>
              <TripActionFeedback action={action} target="recap-save" />
            </div>
            <fieldset
              disabled={disabled || busy || reading}
              className="space-y-3 disabled:opacity-60"
            >
              <label className="block text-xs text-[var(--tf-muted)]">
                回顾标题
                <input
                  required
                  maxLength={100}
                  className={field}
                  value={title}
                  onChange={(e) => {
                    setTitle(e.target.value);
                    setDirty(true);
                  }}
                />
              </label>
              <label className="block text-xs text-[var(--tf-muted)]">
                我的回忆
                <textarea
                  rows={8}
                  maxLength={20000}
                  className={field}
                  value={body}
                  onChange={(e) => {
                    setBody(e.target.value);
                    setDirty(true);
                  }}
                />
              </label>
              <RecapWriter
                versionId={detail.version.id}
                fingerprint={view.source_fingerprint}
                memory={body}
                disabled={disabled || busy || reading}
                onBusy={setBusy}
                onAdopt={(draft) => {
                  if (draft.kind === "recap") setBody(draft.text);
                  else setSocialCopy(draft.text);
                  setDirty(true);
                  setCopyNotice("");
                }}
              />
              <label className="block text-xs text-[var(--tf-muted)]">
                朋友圈文案
                <textarea
                  rows={4}
                  maxLength={2000}
                  className={field}
                  value={socialCopy}
                  onChange={(e) => {
                    setSocialCopy(e.target.value);
                    setDirty(true);
                    setCopyNotice("");
                  }}
                />
              </label>
              <Button
                type="button"
                disabled={!socialCopy.trim()}
                loading={copying}
                onClick={() => void copySocial()}
              >
                复制朋友圈文案
              </Button>
              {copyNotice && (
                <p role="status" className="text-xs text-[var(--tf-accent)]">
                  {copyNotice}
                </p>
              )}
              <RecapPhotos
                detail={detail}
                photos={photos}
                cover={cover}
                disabled={disabled || busy || reading}
                onBusy={setBusy}
                onChange={(items, selected) => {
                  setPhotos(items);
                  setCover(selected);
                  setDirty(true);
                }}
              />
            </fieldset>
          </form>
          <p className="text-xs text-[var(--tf-muted)]">
            {dirty
              ? "改动尚未保存，导出使用已保存内容；请先保存。"
              : view.saved
                ? "回顾已随当前版本保存。"
                : "当前为事实模板，保存后可在版本历史中查看。"}{" "}
            导出不附带票据图片、消费备注和订单资料；回忆正文会包含在回顾导出中；朋友圈文案单独复制发布。
          </p>
          <div className="flex flex-wrap gap-3 text-xs text-[var(--tf-accent)]">
            {(["markdown", "html"] as const).map((format) => (
              <a
                key={format}
                href={`${API_BASE}/api/versions/${detail.version.id}/recap/export?format=${format}`}
                download
                className="tf-action-link tf-page-link "
              >
                导出回顾 {format === "html" ? "HTML" : "Markdown"}
              </a>
            ))}
            {view.photos?.some((p) => p.included) && (
              <a
                href={`${API_BASE}/api/versions/${detail.version.id}/recap/export?format=html&include_photos=true`}
                download
                className="tf-action-link tf-page-link "
              >
                导出图文 HTML
              </a>
            )}
          </div>
        </div>
      )}
    </section>
  );
}
