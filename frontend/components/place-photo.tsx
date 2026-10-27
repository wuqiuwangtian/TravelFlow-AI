"use client";

import { useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui";
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
  const dialog = useRef<HTMLDialogElement>(null);
  const [photos, setPhotos] = useState<string[]>([]);
  const [photoIndex, setPhotoIndex] = useState(0);
  const [loading, setLoading] = useState(!!activity.place);
  const [viewing, setViewing] = useState(false);
  const [fullFailed, setFullFailed] = useState(false);
  const source = photos[photoIndex];
  const title = activity.place?.name || activity.place_name || activity.title;

  useEffect(() => {
    let active = true;
    dialog.current?.close();
    setViewing(false);
    setPhotos([]);
    setPhotoIndex(0);
    setLoading(!!activity.place);
    if (!activity.place || !host.current) return;
    const key = activity.place.id;
    const saved = cache.get(key);
    if (saved) {
      setPhotos(saved);
      setLoading(false);
      return;
    }
    const observer = new IntersectionObserver(
      (entries) => {
        if (!entries.some((entry) => entry.isIntersecting)) return;
        observer.disconnect();
        const existing = cache.get(key);
        if (existing) {
          setPhotos(existing);
          setLoading(false);
          return;
        }
        void api
          .placePhotos(versionId, activity.id)
          .then((result) => {
            if (cache.size >= 100) cache.delete(cache.keys().next().value!);
            cache.set(key, result.photos);
            if (active) setPhotos(result.photos);
          })
          .catch(() => {
            // Photo availability never blocks a route or becomes a required field.
          })
          .finally(() => {
            if (active) setLoading(false);
          });
      },
      { rootMargin: "120px" },
    );
    // Observe the real card, including while a missing thumbnail has no area.
    observer.observe(
      host.current.closest("[data-activity-card]") || host.current,
    );
    return () => {
      active = false;
      observer.disconnect();
    };
  }, [versionId, activity.id, activity.place]);

  useEffect(() => {
    if (viewing) dialog.current?.showModal();
  }, [viewing]);

  return (
    <div
      ref={host}
      className="journey-photo"
      data-state={source ? "ready" : loading ? "loading" : "unavailable"}
    >
      {source ? (
        <>
          <button
            type="button"
            className="journey-photo-trigger"
            aria-label={`查看${title}地点参考图`}
            onClick={() => {
              setFullFailed(false);
              setViewing(true);
            }}
          >
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img
              src={source}
              width={64}
              height={56}
              alt={`${title}地点参考图`}
              loading="lazy"
              decoding="async"
              referrerPolicy="no-referrer"
              onError={() => {
                dialog.current?.close();
                setViewing(false);
                setPhotoIndex((index) => index + 1);
              }}
            />
          </button>
          <dialog
            ref={dialog}
            className="journey-photo-dialog"
            aria-label={`${title}完整参考图`}
            onClose={() => setViewing(false)}
          >
            <div className="journey-photo-heading">
              <h2>{title}</h2>
              <Button
                type="button"
                size="sm"
                onClick={() => dialog.current?.close()}
              >
                关闭参考图
              </Button>
            </div>
            {viewing &&
              (fullFailed ? (
                <p role="status">大图暂不可用，路线与缩略图仍保留。</p>
              ) : (
                // eslint-disable-next-line @next/next/no-img-element
                <img
                  className="journey-full-photo"
                  src={source}
                  alt={`${title}完整地点参考图`}
                  referrerPolicy="no-referrer"
                  onError={() => setFullFailed(true)}
                />
              ))}
            <p className="journey-photo-credit">
              图片来自高德地点资料，仅作地点参考。缩略图适度裁切，展开按原比例完整显示。
            </p>
          </dialog>
        </>
      ) : loading ? (
        <span className="journey-photo-wait" aria-hidden="true" />
      ) : null}
    </div>
  );
}
