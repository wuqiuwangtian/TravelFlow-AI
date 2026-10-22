"use client";
import { useEffect, useRef, useState } from "react";
import { MapPin } from "lucide-react";
import { api } from "@/lib/api";
import type { ScheduledActivity } from "@/lib/types";
const cache = new Map<string, string[]>();
export function PlacePhoto({
  versionId,
  activity,
}: {
  versionId: string;
  activity: ScheduledActivity;
}) {
  const host = useRef<HTMLDivElement>(null);
  const [photos, setPhotos] = useState<string[]>([]);
  const [photoIndex, setPhotoIndex] = useState(0);
  useEffect(() => {
    let active = true;
    setPhotos([]);
    setPhotoIndex(0);
    if (!activity.place || !host.current) return;
    const key = activity.place.id;
    const cached = cache.get(key);
    if (cached) {
      setPhotos(cached);
      return;
    }
    const observer = new IntersectionObserver(
      (entries) => {
        if (!entries.some((e) => e.isIntersecting)) return;
        observer.disconnect();
        const saved = cache.get(key);
        if (saved) {
          setPhotos(saved);
          return;
        }
        void api
          .placePhotos(versionId, activity.id)
          .then((result) => {
            if (cache.size >= 100) cache.delete(cache.keys().next().value!);
            cache.set(key, result.photos);
            if (active) setPhotos(result.photos);
          })
          .catch(() => {});
      },
      { rootMargin: "120px" },
    );
    observer.observe(host.current);
    return () => {
      active = false;
      observer.disconnect();
    };
  }, [versionId, activity.id, activity.place]);
  return (
    <div
      ref={host}
      className="relative mb-3 h-36 overflow-hidden rounded-xl bg-gradient-to-br from-teal-100 via-sky-50 to-slate-100 sm:h-44"
    >
      {photos[photoIndex] ? (
        <>
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img
            src={photos[photoIndex]}
            alt={`${activity.place?.name || activity.place_name}地点参考图`}
            loading="lazy"
            referrerPolicy="no-referrer"
            className="h-full w-full object-cover"
            onError={() => setPhotoIndex((index) => index + 1)}
          />
          <span className="absolute bottom-2 right-2 rounded bg-black/45 px-2 py-0.5 text-[9px] text-white">
            图片来自高德地点资料
          </span>
        </>
      ) : (
        <div className="flex h-full items-end justify-between p-4 text-teal-800">
          <div>
            <p className="text-[10px] uppercase tracking-widest text-teal-600">
              {activity.city || "Along the journey"}
            </p>
            <p className="mt-1 text-sm font-semibold">
              {activity.place_name || activity.title}
            </p>
            <p className="mt-1 text-[10px] text-slate-500">
              {activity.place ? "地点参考图暂不可用" : "按当前建议安排探索"}
            </p>
          </div>
          <MapPin className="h-9 w-9 opacity-25" />
        </div>
      )}
    </div>
  );
}
