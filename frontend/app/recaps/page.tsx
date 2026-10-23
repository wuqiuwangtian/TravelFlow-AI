"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { api } from "@/lib/api";
import type { RecapArchiveItem } from "@/lib/types";

export default function RecapArchivePage() {
  const [items, setItems] = useState<RecapArchiveItem[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [query, setQuery] = useState("");
  const [busy, setBusy] = useState(true);
  const [error, setError] = useState("");
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    let cancelled = false;
    api
      .recapArchive(page, query)
      .then((result) => {
        if (!cancelled) {
          setItems(result.items);
          setTotal(result.total);
          setError("");
        }
      })
      .catch((err) => {
        if (!cancelled)
          setError(err instanceof Error ? err.message : "回顾加载失败");
      })
      .finally(() => {
        if (!cancelled) setBusy(false);
      });
    return () => {
      cancelled = true;
    };
  }, [page, query, retry]);
  return (
    <div className="mx-auto max-w-5xl px-5 py-8">
      <h1 className="text-xl font-bold text-teal-900">旅行回顾</h1>
      <p className="mt-2 text-sm leading-7 text-slate-500">
        收录已结束的旅行方案，补记后同步更新。每个方案单独展示；会话归档不会结束旅行，重新开启后会移出此处，历史版本仍保留。
      </p>
      <label className="mt-5 block text-xs text-slate-600">
        搜索回顾
        <input
          className="mt-2 w-full rounded-xl border border-slate-200 p-3 text-sm"
          maxLength={100}
          placeholder="旅行名称、方案或城市"
          value={query}
          onChange={(e) => {
            setQuery(e.target.value);
            setPage(1);
            setBusy(true);
          }}
        />
      </label>
      {busy && (
        <p role="status" className="mt-5 text-sm text-slate-500">
          正在整理回顾…
        </p>
      )}
      {error && (
        <div role="alert" className="mt-5 text-sm text-red-700">
          {error}
          <button
            className="ml-3 text-teal-800"
            onClick={() => {
              setRetry(retry + 1);
              setBusy(true);
            }}
          >
            重试
          </button>
        </div>
      )}
      {!busy && !error && items.length === 0 && (
        <p className="mt-8 rounded-xl bg-teal-50 p-5 text-sm text-teal-800">
          还没有符合条件的已结束方案。可在旅行页面点击「结束旅行并收录回顾」。
        </p>
      )}
      {!error && (
        <div
          aria-label="已结束旅行回顾"
          className="mt-6 grid min-w-0 gap-5 sm:grid-cols-2"
        >
          {items.map((item) => (
            <article
              key={item.branch_id}
              aria-label={`回顾：${item.session_title} · ${item.branch_title}`}
              className="min-w-0 rounded-2xl border border-teal-100 bg-white p-5"
            >
              {item.cover_photo_id && (
                <>
                  {/* eslint-disable-next-line @next/next/no-img-element */}
                  <img
                    src={api.travelPhotoUrl(
                      item.version_id,
                      item.cover_photo_id,
                    )}
                    alt="旅行回顾封面"
                    className="mb-4 h-44 w-full rounded-xl bg-slate-50 object-cover"
                  />
                </>
              )}
              <h2 className="break-words text-lg font-semibold text-teal-900">
                {item.title}
              </h2>
              <p className="mt-1 break-words text-xs text-slate-500">
                {item.session_title} · {item.branch_title}
                {item.session_archived ? " · 会话已归档" : ""}
              </p>
              <p className="mt-3 text-xs leading-6 text-slate-600">
                结束于{new Date(item.ended_at).toLocaleDateString("zh-CN")} ·{" "}
                {item.destination || "城市未标注"}
                <br />
                已完成{item.completed_count}项 · 未确认到访
                {item.unconfirmed_count}项<br />
                已记净消费 ¥{(item.net_expense_cents / 100).toFixed(2)} ·{" "}
                {item.expense_count}笔<br />
                还有{item.unsettled_count}项未结算，不代表全部开支。
              </p>
              {item.stale && (
                <p className="mt-2 text-xs text-amber-800">
                  记录已变化，原回顾文字待核对。
                </p>
              )}
              <div className="mt-4 flex flex-wrap gap-4 text-sm text-teal-800">
                <Link href={`/recaps/${item.version_id}`}>阅读回顾</Link>
                <Link
                  href={`/chat/${item.session_id}?branch=${item.branch_id}`}
                >
                  补记或重新开启
                </Link>
              </div>
            </article>
          ))}
        </div>
      )}
      {!error && total > 20 && (
        <div className="mt-6 flex items-center gap-4 text-sm text-teal-800">
          <button
            disabled={page === 1 || busy}
            onClick={() => {
              setPage(page - 1);
              setBusy(true);
            }}
          >
            上一页
          </button>
          <span>
            第{page}页 · 共{total}份回顾
          </span>
          <button
            disabled={page * 20 >= total || busy}
            onClick={() => {
              setPage(page + 1);
              setBusy(true);
            }}
          >
            下一页
          </button>
        </div>
      )}
    </div>
  );
}
