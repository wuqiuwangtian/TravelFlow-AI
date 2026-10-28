"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { Button } from "@/components/ui";
import { api } from "@/lib/api";
import type { CareerFootprints as Footprints } from "@/lib/types";

function FootprintMap({ data, city }: { data: Footprints; city: string }) {
  const [state, setState] = useState({ url: "", error: "", loading: true });
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    let active = true;
    let imageUrl = "";
    api
      .careerMapImage(data.map_fingerprint, city, controller.signal)
      .then((blob) => {
        if (active) {
          imageUrl = URL.createObjectURL(blob);
          setState({ url: imageUrl, error: "", loading: false });
        }
      })
      .catch((err) => {
        if (active)
          setState({
            url: "",
            error:
              err instanceof Error
                ? err.message
                : "地图暂时不可用，已保留到访记录。",
            loading: false,
          });
      });
    return () => {
      active = false;
      controller.abort();
      if (imageUrl) URL.revokeObjectURL(imageUrl);
    };
  }, [data.map_fingerprint, city, retry]);
  return (
    <div className="flex min-h-48 items-center justify-center overflow-hidden rounded-[var(--tf-radius)] bg-[var(--tf-paper)]">
      {state.url ? (
        // eslint-disable-next-line @next/next/no-img-element
        <img
          src={state.url}
          alt="已确认到访地点的高德足迹地图，标号对应地点列表"
          className="h-auto w-full"
          onError={() =>
            setState({
              url: "",
              error: "地图图片无法显示，已保留到访记录。",
              loading: false,
            })
          }
        />
      ) : (
        <div className="p-5 text-xs leading-7 text-[var(--tf-muted)]">
          <p>
            {state.loading
              ? "正在加载足迹地图，到访记录已可查看…"
              : state.error}
          </p>
          {!state.loading && (
            <Button
              className="mt-2"
              onClick={() => {
                setState({ url: "", error: "", loading: true });
                setRetry(retry + 1);
              }}
            >
              重试足迹地图
            </Button>
          )}
        </div>
      )}
    </div>
  );
}

export function CareerFootprints({ city }: { city: string }) {
  const [data, setData] = useState<Footprints | null>(null);
  const [page, setPage] = useState(1);
  const [busy, setBusy] = useState(true);
  const [error, setError] = useState("");
  const [retry, setRetry] = useState(0);
  const [recordsOpen, setRecordsOpen] = useState(false);
  useEffect(() => {
    let cancelled = false;
    api
      .careerFootprints(page, city)
      .then((result) => {
        if (!cancelled) {
          setData(result);
          setError("");
        }
      })
      .catch((err) => {
        if (!cancelled)
          setError(err instanceof Error ? err.message : "足迹加载失败");
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
    <section aria-label="城市足迹" className="mt-9">
      <div className="flex flex-wrap items-baseline justify-between gap-3">
        <h2 className="text-lg font-semibold text-[var(--tf-ink)]">城市足迹</h2>
        <Button disabled={busy} onClick={reload}>
          刷新足迹记录
        </Button>
      </div>
      <p className="mt-2 text-xs leading-6 text-[var(--tf-muted)]">
        只展示已完成的非交通活动。地图取本筛选下前10个不同的已确认位置，标号对应下方列表；不连成路线，也不代表GPS轨迹或实际里程。
      </p>
      {busy && (
        <p role="status" className="mt-4 text-sm text-[var(--tf-muted)]">
          正在整理足迹…
        </p>
      )}
      {!busy && error && (
        <div
          role="alert"
          className="mt-4 rounded-[var(--tf-radius)] bg-[var(--tf-warning-soft)] p-4 text-sm text-[var(--tf-warning)]"
        >
          {error}
          <Button onClick={reload} className="ml-3">
            重试加载足迹
          </Button>
        </div>
      )}
      {!busy && !error && data && (
        <>
          <p className="mt-3 text-xs leading-6 text-[var(--tf-muted)]">
            已完成停留{data.total}项 · 坐标已确认{data.located_count}项 ·
            尚无确认坐标{data.unlocated_count}
            项。同一位置可有多次到访记录，地图合并点位。
          </p>
          {data.map_points.length > 0 ? (
            <div className="mt-4 grid items-start gap-4 rounded-[var(--tf-radius)] border border-[var(--tf-line)] bg-[var(--tf-surface)] p-4 md:grid-cols-[minmax(0,1fr)_240px]">
              <FootprintMap
                key={data.map_fingerprint}
                data={data}
                city={city}
              />
              <ol
                aria-label="地图标号对应地点"
                className="max-h-80 space-y-3 overflow-y-auto text-xs leading-6"
              >
                {data.map_points.map((point, index) => (
                  <li
                    key={`${point.version_id}-${point.activity_id}`}
                    className="flex gap-2"
                  >
                    <span className="mt-1 flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-[var(--tf-accent)] text-[var(--tf-accent-ink)]">
                      {index + 1}
                    </span>
                    <div className="min-w-0 break-words">
                      <p className="font-medium text-[var(--tf-ink)]">
                        {point.location?.name || point.place_name}
                      </p>
                      <p>
                        {point.city || "城市未标注"} · 第{point.day}天
                      </p>
                      <Link
                        href={`/recaps/${point.version_id}`}
                        className="tf-action-link tf-page-link "
                      >
                        {point.session_title} · 阅读来源回顾
                      </Link>
                    </div>
                  </li>
                ))}
              </ol>
            </div>
          ) : (
            <p className="mt-4 rounded-[var(--tf-radius)] bg-[var(--tf-paper)] p-5 text-sm leading-7 text-[var(--tf-muted)]">
              {data.total
                ? "已有完成记录，但尚无坐标已确认的地点。可以先查看下方记录，地图不会用计划城市中心补点。"
                : "此筛选下还没有已完成的停留记录。照片或计划地点不会自动写入足迹。"}
            </p>
          )}
          {data.total > 0 && (
            <details
              open={recordsOpen || data.map_points.length === 0}
              onToggle={(event) => setRecordsOpen(event.currentTarget.open)}
              className="mt-4 rounded-[var(--tf-radius)] border border-[var(--tf-line)] bg-[var(--tf-surface)] p-4"
            >
              <summary className="cursor-pointer text-sm text-[var(--tf-ink)]">
                查看全部到访记录（{data.total}项）
              </summary>
              <ol
                aria-label="全部到访记录"
                className="mt-4 space-y-4 text-xs leading-6 text-[var(--tf-muted)]"
              >
                {data.items.map((item) => (
                  <li
                    key={`${item.version_id}-${item.activity_id}`}
                    className="break-words border-t border-[var(--tf-line)] pt-3"
                  >
                    <p className="font-medium text-[var(--tf-ink)]">
                      {item.place_name} · {item.city || "城市未标注"} · 第
                      {item.day}天
                    </p>
                    <p>
                      {item.location
                        ? `已确认坐标 ${item.location.longitude.toFixed(6)}, ${item.location.latitude.toFixed(6)}`
                        : "尚无确认坐标"}{" "}
                      · {item.session_title} · {item.branch_title}
                    </p>
                    <div className="mt-1 flex flex-wrap gap-4 text-[var(--tf-accent)]">
                      <Link
                        className="tf-action-link tf-page-link"
                        href={`/recaps/${item.version_id}`}
                      >
                        阅读来源回顾
                      </Link>
                      <Link
                        className="tf-action-link tf-page-link"
                        href={`/chat/${item.session_id}?branch=${item.branch_id}`}
                      >
                        回原方案补记
                      </Link>
                    </div>
                  </li>
                ))}
              </ol>
              {data.total > 10 && (
                <div className="mt-4 flex items-center gap-4 text-xs text-[var(--tf-accent)]">
                  <Button
                    disabled={page === 1}
                    onClick={() => {
                      setPage(page - 1);
                      setBusy(true);
                    }}
                  >
                    上一页足迹
                  </Button>
                  <span>
                    第{page}页 · 共{data.total}项
                  </span>
                  <Button
                    disabled={page * 10 >= data.total}
                    onClick={() => {
                      setPage(page + 1);
                      setBusy(true);
                    }}
                  >
                    下一页足迹
                  </Button>
                </div>
              )}
            </details>
          )}
        </>
      )}
    </section>
  );
}
