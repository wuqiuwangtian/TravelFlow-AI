"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { RecapImage } from "@/components/recap-image";
import { Button } from "@/components/ui";
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
    <div className="tf-page tf-history history-archive">
      <h1>旅行回顾</h1>
      <Link href="/career" className="tf-action-link tf-page-link mt-3">
        查看我的旅行生涯 →
      </Link>
      <p className="mt-2 text-sm leading-7 text-[var(--tf-muted)]">
        收录已结束的旅行方案，补记后同步更新。每个方案单独展示；会话归档不会结束旅行，重新开启后会移出此处，历史版本仍保留。
      </p>
      <label className="mt-5 block text-xs text-[var(--tf-muted)]">
        搜索回顾
        <input
          className="tf-form-control mt-2"
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
        <p role="status" className="mt-5 text-sm text-[var(--tf-muted)]">
          正在整理回顾…
        </p>
      )}
      {!busy && error && (
        <div role="alert" className="mt-5 text-sm text-[var(--tf-danger)]">
          {error}
          <Button
            className="ml-3"
            onClick={() => {
              setRetry(retry + 1);
              setBusy(true);
            }}
          >
            重试
          </Button>
        </div>
      )}
      {!busy && !error && items.length === 0 && (
        <p className="mt-8 rounded-[var(--tf-radius)] bg-[var(--tf-paper)] p-5 text-sm text-[var(--tf-accent)]">
          还没有符合条件的已结束方案。可在旅行页面点击「结束旅行并收录回顾」。
        </p>
      )}
      {!busy && !error && (
        <div aria-label="已结束旅行回顾" className="history-archive-list">
          {items.map((item) => (
            <article
              key={item.branch_id}
              aria-label={`回顾：${item.session_title} · ${item.branch_title}`}
              className="history-archive-entry"
            >
              {item.cover_photo_id && (
                <RecapImage
                  versionId={item.version_id}
                  photoId={item.cover_photo_id}
                  label="旅行回顾封面"
                />
              )}
              <div className="min-w-0">
                <h2 className="break-words text-lg font-semibold text-[var(--tf-ink)]">
                  {item.title}
                </h2>
                <p className="mt-1 break-words text-xs text-[var(--tf-muted)]">
                  {item.session_title} · {item.branch_title}
                  {item.session_archived ? " · 会话已归档" : ""}
                </p>
                <p className="mt-3 text-xs leading-6 text-[var(--tf-muted)]">
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
                  <p className="mt-2 text-xs text-[var(--tf-warning)]">
                    记录已变化，原回顾文字待核对。
                  </p>
                )}
                <div className="mt-4 flex flex-wrap gap-4 text-sm text-[var(--tf-accent)]">
                  <Link
                    className="tf-action-link tf-page-link"
                    href={`/recaps/${item.version_id}`}
                  >
                    阅读回顾
                  </Link>
                  <Link
                    className="tf-action-link tf-page-link"
                    href={`/chat/${item.session_id}?branch=${item.branch_id}`}
                  >
                    补记或重新开启
                  </Link>
                </div>
              </div>
            </article>
          ))}
        </div>
      )}
      {!busy && !error && total > 20 && (
        <div className="mt-6 flex items-center gap-4 text-sm text-[var(--tf-accent)]">
          <Button
            disabled={page === 1 || busy}
            onClick={() => {
              setPage(page - 1);
              setBusy(true);
            }}
          >
            上一页
          </Button>
          <span>
            第{page}页 · 共{total}份回顾
          </span>
          <Button
            disabled={page * 20 >= total || busy}
            onClick={() => {
              setPage(page + 1);
              setBusy(true);
            }}
          >
            下一页
          </Button>
        </div>
      )}
    </div>
  );
}
