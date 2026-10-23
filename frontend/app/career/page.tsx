"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { MapPin, ArrowUpRight } from "lucide-react";
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
    <div className="mx-auto max-w-5xl px-5 py-8">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <p className="mb-2 text-xs tracking-widest text-teal-700">
            每段旅程，留下自己的记录
          </p>
          <h1 className="text-2xl font-bold text-teal-950">旅行生涯</h1>
          <p className="mt-3 text-sm leading-7 text-slate-500">
            从已结束旅行的实际记录出发，看看走过的城市和留下的回忆。
          </p>
        </div>
        <Link
          href="/recaps"
          className="inline-flex items-center gap-1 text-sm text-teal-800"
        >
          全部方案回顾 <ArrowUpRight className="h-4 w-4" />
        </Link>
      </div>
      <Link
        href="/preferences"
        className="mt-3 inline-block text-sm text-teal-800"
      >
        管理常用旅行偏好 →
      </Link>

      {busy && (
        <p role="status" className="mt-8 text-sm text-slate-500">
          正在整理旅行记录…
        </p>
      )}
      {!busy && error && (
        <div
          role="alert"
          className="mt-8 rounded-xl bg-red-50 p-5 text-sm text-red-800"
        >
          {error}
          <button
            className="ml-3 underline"
            onClick={() => {
              setRetry(retry + 1);
              setBusy(true);
            }}
          >
            重试
          </button>
        </div>
      )}
      {!busy && !error && data && (
        <>
          <section
            aria-label="生涯汇总"
            className="mt-7 rounded-2xl bg-teal-950 p-5 text-white sm:p-7"
          >
            <dl className="grid grid-cols-2 gap-x-4 gap-y-6 sm:grid-cols-4">
              {[
                ["已结束旅行", `${data.summary.trip_count}次`],
                ["到访城市", `${data.summary.city_count}座`],
                ["已完成活动", `${data.summary.completed_count}项`],
                ["已记净消费", money(data.summary.ledger.net_expense_cents)],
              ].map(([label, value]) => (
                <div key={label} className="min-w-0">
                  <dt className="text-xs text-teal-100">{label}</dt>
                  <dd className="mt-2 break-words text-2xl font-semibold tabular-nums sm:text-3xl">
                    {value}
                  </dd>
                </div>
              ))}
            </dl>
            <p className="mt-6 border-t border-white/15 pt-4 text-xs leading-6 text-teal-100">
              同一次会话计一次旅行。优先采用已结束的主方案，否则采用最近结束的方案；不同方案的活动和账目不合并。
            </p>
          </section>

          {data.summary.trip_count === 0 ? (
            <div className="mt-7 rounded-2xl border border-teal-100 bg-white p-7 text-sm leading-7 text-slate-600">
              <h2 className="mb-2 text-lg font-semibold text-teal-900">
                第一段回忆，从一次旅行开始
              </h2>
              在旅行页面点击「结束旅行并收录回顾」，就能在这里看到记录。无需补齐所有活动或费用，未确认的信息会保留。
              <Link href="/sessions" className="mt-4 block text-teal-800">
                打开我的旅行 →
              </Link>
            </div>
          ) : (
            <>
              <section
                aria-label="已记消费口径"
                className="mt-5 rounded-xl border border-slate-200 bg-white p-5 text-xs leading-7 text-slate-600"
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
                <p className="text-amber-800">
                  仅汇总所选方案的已记人民币消费，不代表完整开支；
                  {data.summary.trips_without_expenses}
                  次旅行尚无消费记录。预算估算不计入。
                </p>
                {data.summary.ledger.over_recovered_cents > 0 && (
                  <p className="text-amber-800">
                    到账回款超过对应实付{" "}
                    {money(data.summary.ledger.over_recovered_cents)}
                    ，可返回原旅行核对。
                  </p>
                )}
              </section>

              <section aria-label="到访城市筛选" className="mt-9">
                <h2 className="text-lg font-semibold text-teal-950">
                  走过的城市
                </h2>
                <p className="mt-2 text-xs leading-6 text-slate-500">
                  只取已标记完成的非交通活动，车站中转、定位起点和计划目的地不计入。城市沿用活动或已确认地点的标注，「市」后缀合并。
                  {data.summary.unlocated_completed_count > 0 &&
                    `还有${data.summary.unlocated_completed_count}项已完成活动未标注城市。`}
                </p>
                <div className="mt-4 flex flex-wrap gap-2">
                  {[
                    { name: "", trip_count: data.summary.trip_count },
                    ...data.cities,
                  ].map((item) => (
                    <button
                      key={item.name}
                      aria-pressed={city === item.name}
                      onClick={() => selectCity(item.name)}
                      className={`inline-flex items-center gap-2 rounded-full border px-4 py-2 text-sm ${city === item.name ? "border-teal-800 bg-teal-800 text-white" : "border-teal-100 bg-white text-teal-800 hover:bg-teal-50"}`}
                    >
                      {item.name || "全部旅行"}{" "}
                      <span className="text-xs opacity-75">
                        {item.trip_count}次
                      </span>
                    </button>
                  ))}
                </div>
                {data.cities.length === 0 && (
                  <p className="mt-3 text-sm text-slate-500">
                    还没有已标注城市的到访活动，可以返回原旅行补记。
                  </p>
                )}
              </section>

              <CareerFootprints key={`footprints-${city}`} city={city} />
              <CareerMemories key={`memories-${city}`} city={city} />

              <section aria-label="生涯来源旅行" className="mt-9">
                <div className="flex flex-wrap items-baseline justify-between gap-2">
                  <h2 className="text-lg font-semibold text-teal-950">
                    {city ? `${city}的旅行` : "每一次出发"}
                  </h2>
                  <p className="text-xs text-slate-500">
                    共{data.total}次 · 城市筛选不改变上方生涯总计
                  </p>
                </div>
                <div className="mt-4 grid gap-5 sm:grid-cols-2">
                  {data.items.map((item) => (
                    <article
                      key={item.session_id}
                      aria-label={`旅行：${item.session_title}`}
                      className="min-w-0 overflow-hidden rounded-2xl border border-teal-100 bg-white"
                    >
                      {item.cover_photo_id ? (
                        // eslint-disable-next-line @next/next/no-img-element
                        <img
                          src={api.travelPhotoUrl(
                            item.version_id,
                            item.cover_photo_id,
                          )}
                          alt="旅行回顾封面"
                          className="h-40 w-full bg-teal-50 object-cover"
                        />
                      ) : (
                        <div className="flex h-24 items-center gap-3 bg-gradient-to-r from-teal-50 to-slate-50 px-5 text-teal-700">
                          <MapPin className="h-6 w-6 shrink-0" />
                          <span className="truncate text-sm">
                            {item.cities.join(" · ") || "城市待标注"}
                          </span>
                        </div>
                      )}
                      <div className="p-5">
                        <p className="text-xs text-slate-500">
                          记录结束于{" "}
                          {new Date(item.ended_at).toLocaleDateString("zh-CN")}
                        </p>
                        <h3 className="mt-2 break-words text-lg font-semibold text-teal-950">
                          {item.title}
                        </h3>
                        <p className="mt-1 break-words text-xs leading-6 text-slate-500">
                          {item.session_title}
                          {item.session_archived ? " · 会话已归档" : ""}
                        </p>
                        <p className="mt-3 break-words text-sm leading-7 text-slate-700">
                          到访：{item.cities.join("、") || "城市待标注"}
                          <br />
                          已完成{item.completed_count}项 · 已记净消费{" "}
                          {money(item.ledger.net_expense_cents)}（
                          {item.expense_count}笔）
                        </p>
                        <p className="mt-2 break-words text-xs leading-6 text-slate-500">
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
                          <p className="mt-1 text-xs text-amber-800">
                            还有{item.unsettled_count}项回款待到账。
                          </p>
                        )}
                        {item.photo_count > 0 && (
                          <p className="mt-2 text-xs text-slate-500">
                            已选入{item.photo_count}张照片回忆
                          </p>
                        )}
                        <div className="mt-4 flex flex-wrap gap-5 text-sm text-teal-800">
                          <Link href={`/recaps/${item.version_id}`}>
                            阅读回顾
                          </Link>
                          <Link
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
                  <p className="mt-5 text-sm text-slate-500">
                    当前条件下没有旅行记录，可选择「全部旅行」查看。
                  </p>
                )}
                {data.total > 20 && (
                  <div className="mt-6 flex items-center gap-4 text-sm text-teal-800">
                    <button
                      disabled={page === 1}
                      onClick={() => {
                        setPage(page - 1);
                        setBusy(true);
                      }}
                    >
                      上一页
                    </button>
                    <span>
                      第{page}页 · 共{data.total}次旅行
                    </span>
                    <button
                      disabled={page * 20 >= data.total}
                      onClick={() => {
                        setPage(page + 1);
                        setBusy(true);
                      }}
                    >
                      下一页
                    </button>
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
