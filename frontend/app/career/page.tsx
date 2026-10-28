"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { ArrowUpRight } from "lucide-react";
import { RecapImage } from "@/components/recap-image";
import { Button } from "@/components/ui";
import { api } from "@/lib/api";
import type { TravelCareer } from "@/lib/types";
import { CareerFootprints } from "@/components/career-footprints";
import { CareerMemories } from "@/components/career-memories";

const money = (cents: number) =>
  `¥${(cents / 100).toLocaleString("zh-CN", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })}`;

export default function TravelCareerPage() {
  const [data, setData] = useState<TravelCareer | null>(null);
  const [city, setCity] = useState("");
  const [page, setPage] = useState(1);
  const [busy, setBusy] = useState(true);
  const [error, setError] = useState("");
  const [retry, setRetry] = useState(0);

  useEffect(() => {
    let cancelled = false;
    api
      .travelCareer(page, city)
      .then((result) => {
        if (!cancelled) {
          setData(result);
          setError("");
        }
      })
      .catch((err) => {
        if (!cancelled)
          setError(err instanceof Error ? err.message : "生涯加载失败");
      })
      .finally(() => {
        if (!cancelled) setBusy(false);
      });
    return () => {
      cancelled = true;
    };
  }, [page, city, retry]);

  function selectCity(value: string) {
    if (value === city && page === 1) return;
    setCity(value);
    setPage(1);
    setBusy(true);
  }

  return (
    <div className="tf-page tf-history history-career">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <p className="mb-2 text-xs tracking-widest text-[var(--tf-accent)]">
            每段旅程，留下自己的记录
          </p>
          <h1 className="text-2xl font-bold text-[var(--tf-ink)]">旅行生涯</h1>
          <p className="mt-3 text-sm leading-7 text-[var(--tf-muted)]">
            从已结束旅行的实际记录出发，看看走过的城市和留下的回忆。
          </p>
        </div>
        <Link href="/recaps" className="tf-action-link tf-page-link ">
          全部方案回顾 <ArrowUpRight className="h-4 w-4" />
        </Link>
      </div>
      <Link href="/preferences" className="tf-action-link tf-page-link mt-3">
        管理常用旅行偏好 →
      </Link>

      {busy && (
        <p role="status" className="mt-8 text-sm text-[var(--tf-muted)]">
          正在整理旅行记录…
        </p>
      )}
      {!busy && error && (
        <div
          role="alert"
          className="mt-8 rounded-[var(--tf-radius)] bg-[var(--tf-danger-soft)] p-5 text-sm text-[var(--tf-danger)]"
        >
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
      {!busy && !error && data && (
        <>
          <section aria-label="生涯汇总" className="history-career-summary">
            <dl className="history-career-stats">
              {[
                ["已结束旅行", `${data.summary.trip_count}次`],
                ["到访城市", `${data.summary.city_count}座`],
                ["已完成活动", `${data.summary.completed_count}项`],
                ["已记净消费", money(data.summary.ledger.net_expense_cents)],
              ].map(([label, value]) => (
                <div key={label} className="min-w-0">
                  <dt className="text-xs text-[var(--tf-muted)]">{label}</dt>
                  <dd className="history-career-value">{value}</dd>
                </div>
              ))}
            </dl>
            <p className="mt-6 border-t border-[var(--tf-line)] pt-4 text-xs leading-6 text-[var(--tf-muted)]">
              同一次会话计一次旅行。优先采用已结束的主方案，否则采用最近结束的方案；不同方案的活动和账目不合并。
            </p>
          </section>

          {data.summary.trip_count === 0 ? (
            <div className="mt-7 rounded-[var(--tf-radius)] border border-[var(--tf-line)] bg-[var(--tf-surface)] p-7 text-sm leading-7 text-[var(--tf-muted)]">
              <h2 className="mb-2 text-lg font-semibold text-[var(--tf-ink)]">
                第一段回忆，从一次旅行开始
              </h2>
              在旅行页面点击「结束旅行并收录回顾」，就能在这里看到记录。无需补齐所有活动或费用，未确认的信息会保留。
              <Link
                href="/sessions"
                className="tf-action-link tf-page-link mt-4"
              >
                打开我的旅行 →
              </Link>
            </div>
          ) : (
            <>
              <section
                aria-label="已记消费口径"
                className="mt-5 rounded-[var(--tf-radius)] border border-[var(--tf-line)] bg-[var(--tf-surface)] p-5 text-xs leading-7 text-[var(--tf-muted)]"
              >
                <p>
                  已付 {money(data.summary.ledger.paid_cents)} − 已到账退款{" "}
                  {money(data.summary.ledger.refunded_cents)} = 已记净消费{" "}
                  {money(data.summary.ledger.net_expense_cents)}
                </p>
                <p>
                  已到账报销 {money(data.summary.ledger.reimbursed_cents)} ·
                  净支出 {money(data.summary.ledger.net_outlay_cents)}
                  。报销不抵减消费。
                </p>
                <p>
                  待到账退款 {money(data.summary.ledger.pending_refund_cents)} ·
                  待到账报销{" "}
                  {money(data.summary.ledger.pending_reimbursement_cents)}（共
                  {data.summary.unsettled_count}项），未抵减当前金额。
                </p>
                <p className="text-[var(--tf-warning)]">
                  仅汇总所选方案的已记人民币消费，不代表完整开支；
                  {data.summary.trips_without_expenses}
                  次旅行尚无消费记录。预算估算不计入。
                </p>
                {data.summary.ledger.over_recovered_cents > 0 && (
                  <p className="text-[var(--tf-warning)]">
                    到账回款超过对应实付{" "}
                    {money(data.summary.ledger.over_recovered_cents)}
                    ，可返回原旅行核对。
                  </p>
                )}
              </section>

              <section aria-label="到访城市筛选" className="mt-9">
                <h2 className="text-lg font-semibold text-[var(--tf-ink)]">
                  走过的城市
                </h2>
                <p className="mt-2 text-xs leading-6 text-[var(--tf-muted)]">
                  只取已标记完成的非交通活动，车站中转、定位起点和计划目的地不计入。城市沿用活动或已确认地点的标注，「市」后缀合并。
                  {data.summary.unlocated_completed_count > 0 &&
                    `还有${data.summary.unlocated_completed_count}项已完成活动未标注城市。`}
                </p>
                <div className="mt-4 flex flex-wrap gap-2">
                  {[
                    { name: "", trip_count: data.summary.trip_count },
                    ...data.cities,
                  ].map((item) => (
                    <Button
                      key={item.name}
                      aria-pressed={city === item.name}
                      onClick={() => selectCity(item.name)}
                    >
                      {item.name || "全部旅行"}{" "}
                      <span className="text-xs opacity-75">
                        {item.trip_count}次
                      </span>
                    </Button>
                  ))}
                </div>
                {data.cities.length === 0 && (
                  <p className="mt-3 text-sm text-[var(--tf-muted)]">
                    还没有已标注城市的到访活动，可以返回原旅行补记。
                  </p>
                )}
              </section>

              <CareerFootprints key={`footprints-${city}`} city={city} />
              <CareerMemories key={`memories-${city}`} city={city} />

              <section aria-label="生涯来源旅行" className="mt-9">
                <div className="flex flex-wrap items-baseline justify-between gap-2">
                  <h2 className="text-lg font-semibold text-[var(--tf-ink)]">
                    {city ? `${city}的旅行` : "每一次出发"}
                  </h2>
                  <p className="text-xs text-[var(--tf-muted)]">
                    共{data.total}次 · 城市筛选不改变上方生涯总计
                  </p>
                </div>
                <div className="mt-4 grid gap-5 sm:grid-cols-2">
                  {data.items.map((item) => (
                    <article
                      key={item.session_id}
                      aria-label={`旅行：${item.session_title}`}
                      className="min-w-0 overflow-hidden rounded-[var(--tf-radius)] border border-[var(--tf-line)] bg-[var(--tf-surface)]"
                    >
                      {item.cover_photo_id && (
                        <RecapImage
                          versionId={item.version_id}
                          photoId={item.cover_photo_id}
                          label="旅行回顾封面"
                        />
                      )}
                      <div className="p-5">
                        <p className="text-xs text-[var(--tf-muted)]">
                          记录结束于{" "}
                          {new Date(item.ended_at).toLocaleDateString("zh-CN")}
                        </p>
                        <h3 className="mt-2 break-words text-lg font-semibold text-[var(--tf-ink)]">
                          {item.title}
                        </h3>
                        <p className="mt-1 break-words text-xs leading-6 text-[var(--tf-muted)]">
                          {item.session_title}
                          {item.session_archived ? " · 会话已归档" : ""}
                        </p>
                        <p className="mt-3 break-words text-sm leading-7 text-[var(--tf-ink)]">
                          到访：{item.cities.join("、") || "城市待标注"}
                          <br />
                          已完成{item.completed_count}项 · 已记净消费{" "}
                          {money(item.ledger.net_expense_cents)}（
                          {item.expense_count}笔）
                        </p>
                        <p className="mt-2 break-words text-xs leading-6 text-[var(--tf-muted)]">
                          统计采用：{item.branch_title}（
                          {item.source_basis === "primary"
                            ? "已结束主方案"
                            : "最近结束方案"}
                          ）
                          {item.finished_branch_count > 1
                            ? `；已结束的${item.finished_branch_count}份方案仅计入这一份。`
                            : "。"}
                        </p>
                        {item.unsettled_count > 0 && (
                          <p className="mt-1 text-xs text-[var(--tf-warning)]">
                            还有{item.unsettled_count}项回款待到账。
                          </p>
                        )}
                        {item.photo_count > 0 && (
                          <p className="mt-2 text-xs text-[var(--tf-muted)]">
                            已选入{item.photo_count}张照片回忆
                          </p>
                        )}
                        <div className="mt-4 flex flex-wrap gap-5 text-sm text-[var(--tf-accent)]">
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
                            返回原方案补记
                          </Link>
                        </div>
                      </div>
                    </article>
                  ))}
                </div>
                {data.items.length === 0 && (
                  <p className="mt-5 text-sm text-[var(--tf-muted)]">
                    当前条件下没有旅行记录，可选择「全部旅行」查看。
                  </p>
                )}
                {data.total > 20 && (
                  <div className="mt-6 flex items-center gap-4 text-sm text-[var(--tf-accent)]">
                    <Button
                      disabled={page === 1}
                      onClick={() => {
                        setPage(page - 1);
                        setBusy(true);
                      }}
                    >
                      上一页
                    </Button>
                    <span>
                      第{page}页 · 共{data.total}次旅行
                    </span>
                    <Button
                      disabled={page * 20 >= data.total}
                      onClick={() => {
                        setPage(page + 1);
                        setBusy(true);
                      }}
                    >
                      下一页
                    </Button>
                  </div>
                )}
              </section>
            </>
          )}
        </>
      )}
    </div>
  );
}
