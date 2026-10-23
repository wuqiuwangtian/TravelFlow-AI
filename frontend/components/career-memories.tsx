"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { api } from "@/lib/api";
import type { CareerMemories as Memories, CareerMemory } from "@/lib/types";

function MemoryPhoto({ item }: { item: CareerMemory }) {
  const [failed, setFailed] = useState(false);
  const [retry, setRetry] = useState(0);
  return (
    <div className="flex aspect-[4/3] items-center justify-center overflow-hidden bg-teal-50">
      {failed ? (
        <div className="p-4 text-xs leading-6 text-slate-600">
          照片暂时无法显示，仍可阅读原回顾。
          <button
            className="mt-2 block text-teal-800 underline"
            onClick={() => {
              setRetry(retry + 1);
              setFailed(false);
            }}
          >
            重试照片
          </button>
        </div>
      ) : (
        // eslint-disable-next-line @next/next/no-img-element
        <img
          key={retry}
          src={`${api.travelPhotoUrl(item.version_id, item.photo_id)}${retry ? `?retry=${retry}` : ""}`}
          alt={item.caption || "旅行照片"}
          loading="lazy"
          className="h-full w-full object-cover"
          onError={() => setFailed(true)}
        />
      )}
    </div>
  );
}

export function CareerMemories({ city }: { city: string }) {
  const [data, setData] = useState<Memories | null>(null);
  const [page, setPage] = useState(1);
  const [busy, setBusy] = useState(true);
  const [error, setError] = useState("");
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    let cancelled = false;
    api
      .careerMemories(page, city)
      .then((result) => {
        if (!cancelled) {
          setData(result);
          setError("");
        }
      })
      .catch((err) => {
        if (!cancelled)
          setError(err instanceof Error ? err.message : "照片回忆加载失败");
      })
      .finally(() => {
        if (!cancelled) setBusy(false);
      });
    return () => {
      cancelled = true;
    };
  }, [page, city, retry]);
  function reload() {
    setRetry(retry + 1);
    setBusy(true);
  }
  return (
    <section aria-label="旅行照片回忆" className="mt-9">
      <div className="flex flex-wrap items-baseline justify-between gap-3">
        <h2 className="text-lg font-semibold text-teal-950">照片里的旅程</h2>
        <button
          disabled={busy}
          onClick={reload}
          className="text-xs text-teal-800"
        >
          刷新照片回忆
        </button>
      </div>
      <p className="mt-2 text-xs leading-6 text-slate-500">
        仅展示已保存且选入图文回顾的照片，按来源旅行的结束记录排列，日序为用户标注，不推测拍摄日期。城市筛选取来源旅行的实际到访城市，照片标注不作为到访证明。
      </p>
      {busy && <p className="mt-4 text-sm text-slate-500">正在整理照片回忆…</p>}
      {!busy && error && (
        <div
          role="alert"
          className="mt-4 rounded-xl bg-amber-50 p-4 text-sm text-amber-900"
        >
          {error}
          <button className="ml-3 underline" onClick={reload}>
            重试加载照片回忆
          </button>
        </div>
      )}
      {!busy && !error && data && (
        <>
          {data.total === 0 ? (
            <p className="mt-4 rounded-xl bg-teal-50 p-5 text-sm leading-7 text-teal-800">
              此筛选下还没有选入回顾的照片。可在原旅行的回顾里上传、选入并保存；未选照片不会自动进入生涯。
            </p>
          ) : (
            <>
              <p className="mt-3 text-xs text-slate-500">
                共{data.total}张 · 不重复收录同一旅行的备选方案
              </p>
              <div className="mt-4 grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
                {data.items.map((item) => (
                  <article
                    key={`${item.version_id}-${item.photo_id}`}
                    aria-label={`照片回忆：${item.session_title} · ${item.photo_id}`}
                    className="min-w-0 overflow-hidden rounded-2xl border border-teal-100 bg-white"
                  >
                    <MemoryPhoto
                      key={`${item.version_id}-${item.photo_id}`}
                      item={item}
                    />
                    <div className="p-4 text-xs leading-6 text-slate-600">
                      <p className="break-words font-medium text-slate-800">
                        {item.caption || "一段旅行回忆"}
                      </p>
                      <p className="mt-2 break-words">
                        {item.day
                          ? `第${item.day}天（用户标注）`
                          : "未标注行程日"}
                        {item.activity_id
                          ? ` · 关联：${item.activity_title || "原地点已不在当前计划"}（用户标注）`
                          : ""}
                      </p>
                      <p className="mt-2 break-words">
                        {item.session_title} · {item.branch_title}
                      </p>
                      <p className="text-slate-400">
                        来源旅行记录结束于{" "}
                        {new Date(item.ended_at).toLocaleDateString("zh-CN")}
                      </p>
                      <Link
                        href={`/recaps/${item.version_id}`}
                        className="mt-3 inline-block text-teal-800 underline"
                      >
                        阅读这段回忆
                      </Link>
                    </div>
                  </article>
                ))}
              </div>
              {data.total > 6 && (
                <div className="mt-5 flex items-center gap-4 text-xs text-teal-800">
                  <button
                    disabled={page === 1}
                    onClick={() => {
                      setPage(page - 1);
                      setBusy(true);
                    }}
                  >
                    上一页照片
                  </button>
                  <span>
                    第{page}页 · 共{data.total}张
                  </span>
                  <button
                    disabled={page * 6 >= data.total}
                    onClick={() => {
                      setPage(page + 1);
                      setBusy(true);
                    }}
                  >
                    下一页照片
                  </button>
                </div>
              )}
            </>
          )}
        </>
      )}
    </section>
  );
}
