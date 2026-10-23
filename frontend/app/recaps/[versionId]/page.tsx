"use client";

import { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";
import { api, API_BASE } from "@/lib/api";
import type { RecapView, RecapPhoto } from "@/lib/types";

export default function ReadRecapPage() {
  const { versionId } = useParams<{ versionId: string }>();
  const [view, setView] = useState<RecapView | null>(null);
  const [error, setError] = useState("");
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    let cancelled = false;
    api
      .getRecap(versionId)
      .then((result) => {
        if (!cancelled) {
          setView(result);
          setError("");
        }
      })
      .catch((err) => {
        if (!cancelled)
          setError(err instanceof Error ? err.message : "回顾加载失败");
      });
    return () => {
      cancelled = true;
    };
  }, [versionId, retry]);
  const selected = view?.photos.filter((photo) => photo.included) ?? [];
  const cover = selected.find((photo) => photo.id === view?.cover_photo_id);
  function photoFigure(photo: RecapPhoto) {
    return (
      <figure key={photo.id} className="min-w-0">
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img
          src={api.travelPhotoUrl(versionId, photo.id)}
          alt={photo.caption || "旅行照片"}
          className="max-h-[560px] w-full rounded-2xl bg-slate-50 object-contain"
        />
        <figcaption className="mt-2 whitespace-pre-wrap break-words text-xs leading-6 text-slate-500">
          {photo.day ? `用户标注：第${photo.day}天 · ` : ""}
          {photo.caption}
        </figcaption>
      </figure>
    );
  }
  return (
    <div className="mx-auto max-w-4xl px-5 py-8">
      <Link href="/recaps" className="text-sm text-teal-800">
        返回旅行回顾
      </Link>
      <h1 className="mt-4 text-xl font-bold text-teal-900">阅读回顾</h1>
      <p className="mt-2 text-xs leading-6 text-slate-500">
        此页只读，展示所选版本；补记与重新开启请从回顾列表返回旅行页面。照片关联不代表确认到访。
      </p>
      {error && (
        <p role="alert" className="mt-4 text-sm text-red-700">
          {error}
          <button
            onClick={() => setRetry(retry + 1)}
            className="ml-3 text-teal-800"
          >
            重试
          </button>
        </p>
      )}
      {!view && !error && <p role="status">正在打开回顾…</p>}
      {view && (
        <section
          aria-label="旅行回顾"
          className="mt-5 space-y-7 rounded-2xl border border-teal-100 bg-white p-5 sm:p-8"
        >
          {cover && photoFigure(cover)}
          <header>
            <h2 className="break-words text-2xl font-bold leading-relaxed text-teal-900">
              {view.title}
            </h2>
            <p className="mt-2 text-xs text-slate-500">
              {view.ended_at
                ? `已结束 · ${new Date(view.ended_at).toLocaleString("zh-CN")}`
                : "所选版本尚未结束"}
            </p>
          </header>
          {view.stale && (
            <p
              role="status"
              className="rounded-xl bg-amber-50 p-3 text-xs leading-6 text-amber-800"
            >
              活动或账目已变化，原回忆文字尚待核对。下方事实汇总使用所选版本的记录。
            </p>
          )}
          <section aria-label="回忆正文">
            <h3 className="font-semibold text-teal-900">我的回忆</h3>
            <p className="mt-3 whitespace-pre-wrap break-words text-sm leading-8 text-slate-700">
              {view.body || "还没有补写回忆。"}
            </p>
          </section>
          <section aria-label="实际记录" className="space-y-4">
            <h3 className="font-semibold text-teal-900">实际记录</h3>
            {view.days.map((day) => (
              <div
                key={day.day}
                className="rounded-xl bg-teal-50/50 p-4 text-xs leading-7 text-slate-600"
              >
                <p className="font-semibold text-teal-800">第{day.day}天</p>
                <p>已完成：{day.completed.join("、") || "无记录"}</p>
                <p>已跳过：{day.skipped.join("、") || "无记录"}</p>
                <p>尚未确认到访：{day.unconfirmed.join("、") || "无记录"}</p>
              </div>
            ))}
          </section>
          <section
            aria-label="已记开支"
            className="rounded-xl bg-slate-50 p-4 text-xs leading-7 text-slate-600"
          >
            <h3 className="font-semibold text-teal-900">已记开支 · 全团</h3>
            <p>
              原始实付 ¥{(view.ledger.paid_cents / 100).toFixed(2)} · 已到账退款
              ¥{(view.ledger.refunded_cents / 100).toFixed(2)}
            </p>
            <p className="font-semibold text-teal-800">
              已记净消费 ¥{(view.ledger.net_expense_cents / 100).toFixed(2)} ·{" "}
              {view.expense_count}笔消费
            </p>
            <p>
              已到账报销 ¥{(view.ledger.reimbursed_cents / 100).toFixed(2)} ·
              净垫付 ¥{(view.ledger.net_outlay_cents / 100).toFixed(2)}
            </p>
            <p>
              待到账退款 ¥{(view.ledger.pending_refund_cents / 100).toFixed(2)}{" "}
              · 待到账报销 ¥
              {(view.ledger.pending_reimbursement_cents / 100).toFixed(2)}
            </p>
            <p>
              还有{view.unsettled_count}项未结算、{view.unknown_count}
              项未定价，已记开支不代表全部花费。
            </p>
          </section>
          {selected.some((photo) => photo.id !== view.cover_photo_id) && (
            <section aria-label="精选照片" className="space-y-4">
              <h3 className="font-semibold text-teal-900">精选照片</h3>
              <div className="grid min-w-0 gap-5 sm:grid-cols-2">
                {selected
                  .filter((photo) => photo.id !== view.cover_photo_id)
                  .map(photoFigure)}
              </div>
            </section>
          )}
          {view.social_copy && (
            <details className="rounded-xl border border-teal-100 p-4 text-sm text-slate-600">
              <summary className="cursor-pointer text-teal-800">
                朋友圈文案
              </summary>
              <p className="mt-3 whitespace-pre-wrap break-words leading-8">
                {view.social_copy}
              </p>
            </details>
          )}
          <div className="flex flex-wrap gap-4 text-sm text-teal-800">
            {(["markdown", "html"] as const).map((format) => (
              <a
                key={format}
                href={`${API_BASE}/api/versions/${versionId}/recap/export?format=${format}`}
                download
              >
                导出回顾 {format === "html" ? "HTML" : "Markdown"}
              </a>
            ))}
            {selected.length > 0 && (
              <a
                href={`${API_BASE}/api/versions/${versionId}/recap/export?format=html&include_photos=true`}
                download
              >
                导出图文 HTML
              </a>
            )}
          </div>
        </section>
      )}
    </div>
  );
}
