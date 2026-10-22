"use client";
import { useState } from "react";
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
export function PlaceNavigation({ place }: { place: Place }) {
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
    <div className="mt-3 space-y-2">
      <div className="flex flex-wrap gap-2">
        <a
          href={navigation(place, "transit")}
          target="_blank"
          rel="noopener noreferrer"
          className="rounded-lg border border-teal-100 px-2.5 py-1.5 text-xs text-teal-700"
        >
          打开地点导航
        </a>
        <button
          type="button"
          disabled={busy}
          onClick={() => void locate()}
          className="rounded-lg border border-teal-100 px-2.5 py-1.5 text-xs text-teal-700 disabled:opacity-40"
        >
          {busy ? "正在获取位置…" : "用我的位置导航"}
        </button>
      </div>
      {origin && (
        <a
          href={navigation(place, "transit", origin)}
          target="_blank"
          rel="noopener noreferrer"
          className="block rounded-lg bg-teal-600 px-3 py-2 text-center text-xs font-medium text-white"
        >
          位置已就绪，从这里出发
        </a>
      )}
      <p className="text-[10px] leading-5 text-slate-400">
        仅在点击时申请定位，坐标发给高德用于本次导航，不写入旅行记录。未提供起点时，可在高德中选择。
      </p>
      {error && (
        <p role="status" className="text-xs text-amber-700">
          {error}
        </p>
      )}
    </div>
  );
}
