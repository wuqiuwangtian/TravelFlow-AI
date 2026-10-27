"use client";
import { useState } from "react";
import { Button } from "@/components/ui";
import { api } from "@/lib/api";
import type { Place, TravelMode } from "@/lib/types";
export function navigation(
  place: Place,
  mode: TravelMode,
  origin?: Place | null,
) {
  const q = new URLSearchParams({
    to: `${place.longitude},${place.latitude},${place.name}`,
    mode: mode === "walking" ? "walk" : mode === "driving" ? "car" : "bus",
    coordinate: "gaode",
    src: "TravelFlowAI",
    callnative: "1",
  });
  if (origin)
    q.set("from", `${origin.longitude},${origin.latitude},${origin.name}`);
  return `https://uri.amap.com/navigation?${q}`;
}
export function PlaceNavigation({
  place,
  previousPlace,
  previousName,
  mode = "transit",
}: {
  place: Place;
  previousPlace?: Place | null;
  previousName?: string;
  mode?: TravelMode;
}) {
  const [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [origin, setOrigin] = useState<Place | null>(null);
  async function locate() {
    setBusy(true);
    setError("");
    setOrigin(null);
    try {
      if (!window.isSecureContext)
        throw new Error(
          "浏览器定位需要 HTTPS 或本机 localhost，可在高德页面手动选择起点。",
        );
      if (!navigator.geolocation)
        throw new Error("当前浏览器不支持定位，可在高德页面手动选择起点。");
      const position = await new Promise<GeolocationPosition>(
        (resolve, reject) =>
          navigator.geolocation.getCurrentPosition(resolve, reject, {
            enableHighAccuracy: true,
            timeout: 10000,
            maximumAge: 0,
          }),
      );
      const current = await api.convertLocation(
        position.coords.longitude,
        position.coords.latitude,
      );
      setOrigin(current);
    } catch (e) {
      const code =
        typeof e === "object" && e !== null && "code" in e ? e.code : null;
      setError(
        code === 1
          ? "未获定位授权，可以继续在高德页面手动选择起点。"
          : code === 3
            ? "定位超时，可重试或在高德页面手动选择起点。"
            : e instanceof Error
              ? e.message
              : "定位暂不可用，可在高德页面手动选择起点。",
      );
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="trip-ui mt-3 space-y-2">
      <div className="flex flex-wrap gap-2">
        <a
          href={navigation(place, mode, previousPlace)}
          target="_blank"
          rel="noopener noreferrer"
          className="tf-action-link"
        >
          打开地点导航
        </a>
        <Button
          type="button"
          loading={busy}
          loadingLabel="正在获取位置…"
          onClick={() => void locate()}
          className="rounded-lg border border-[var(--tf-accent-line)] px-2.5 py-1.5 text-xs text-[var(--tf-accent)] disabled:opacity-40"
        >
          {busy ? "正在获取位置…" : "用我的位置导航"}
        </Button>
      </div>
      {origin && (
        <a
          href={navigation(place, mode, origin)}
          target="_blank"
          rel="noopener noreferrer"
          className="tf-action-link trip-navigation-ready"
        >
          位置已就绪，从这里出发
        </a>
      )}
      <p className="text-[10px] leading-5 text-[var(--tf-muted)]">
        {previousPlace
          ? `默认从上一站「${previousPlace.name}」出发。`
          : previousName
            ? `上一站「${previousName}」尚未定位，可在高德中选择起点。`
            : "这是当天第一站，可使用当前位置或在高德中选择起点。"}
        点击“用我的位置导航”才申请定位，坐标仅用于本次高德导航，不写入旅行记录。
      </p>
      {error && (
        <p role="status" className="text-xs text-[var(--tf-warning)]">
          {error}
        </p>
      )}
    </div>
  );
}
