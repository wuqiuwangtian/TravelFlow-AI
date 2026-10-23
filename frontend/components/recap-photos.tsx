"use client";

import { useState } from "react";
import { api } from "@/lib/api";
import type { RecapPhoto, VersionDetail } from "@/lib/types";

export function RecapPhotos({
  detail,
  photos,
  cover,
  disabled,
  onChange,
  onBusy,
}: {
  detail: VersionDetail;
  photos: RecapPhoto[];
  cover: string | null;
  disabled: boolean;
  onChange: (photos: RecapPhoto[], cover: string | null) => void;
  onBusy: (busy: boolean) => void;
}) {
  const [error, setError] = useState("");
  const [uploading, setUploading] = useState(false);
  const days = detail.itinerary?.days ?? [];
  const activities = days.flatMap((d) =>
    d.activities
      .filter((a) => !a.is_context_anchor)
      .map((a) => ({ ...a, day: d.day })),
  );
  const field =
    "mt-1 w-full rounded-lg border border-slate-200 bg-white p-2 text-xs";
  function update(id: string, patch: Partial<RecapPhoto>) {
    onChange(
      photos.map((p) => (p.id === id ? { ...p, ...patch } : p)),
      patch.included === false && cover === id ? null : cover,
    );
  }
  async function upload(files: File[]) {
    setError("");
    setUploading(true);
    onBusy(true);
    const next = [...photos];
    try {
      if (files.length + photos.length > 40)
        throw new Error("每份回顾最多40张照片，请减少本次选择。");
      for (const file of files) {
        if (file.size > 4 * 1024 * 1024)
          throw new Error("请选择每张4MB以内的照片。");
        const result = await api.uploadTravelPhoto(detail.version.id, file);
        if (!next.some((p) => p.id === result.id))
          next.push({
            id: result.id,
            caption: "",
            day: null,
            activity_id: null,
            included: false,
          });
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "上传失败，请重试。");
    } finally {
      onChange(next, cover);
      setUploading(false);
      onBusy(false);
    }
  }
  return (
    <fieldset
      disabled={disabled || uploading}
      aria-label="旅行照片"
      className="space-y-3 rounded-xl border border-teal-100 bg-white p-4 disabled:opacity-60"
    >
      <legend className="px-1 text-sm font-semibold text-teal-800">
        旅行照片 · {photos.length}/40
      </legend>
      <p className="text-xs leading-6 text-slate-500">
        照片保存在本机，清除定位等元数据，不发送模型。关联地点不代表确认到访；原图请自行保留。
      </p>
      <div className="flex flex-wrap gap-3 text-xs text-teal-800">
        <label className="cursor-pointer rounded-lg border border-teal-100 px-3 py-2">
          添加旅行照片
          <input
            aria-label="添加旅行照片"
            type="file"
            multiple
            accept="image/png,image/jpeg,image/webp"
            className="sr-only"
            disabled={disabled || uploading || photos.length >= 40}
            onChange={(e) => {
              const files = Array.from(e.target.files ?? []);
              e.target.value = "";
              if (files.length) void upload(files);
            }}
          />
        </label>
        {photos.length > 0 && (
          <button
            type="button"
            onClick={() =>
              onChange(
                photos.map((p) => ({ ...p, included: true })),
                cover,
              )
            }
          >
            全部选入回顾
          </button>
        )}
        {cover && (
          <button type="button" onClick={() => onChange(photos, null)}>
            清除封面
          </button>
        )}
      </div>
      {uploading && (
        <p role="status" className="text-xs text-teal-700">
          正在保存照片…
        </p>
      )}
      {error && (
        <p role="alert" className="text-xs text-red-700">
          {error}
        </p>
      )}
      <div className="grid min-w-0 gap-4 sm:grid-cols-2">
        {photos.map((photo, i) => (
          <article
            key={photo.id}
            aria-label={`旅行照片${i + 1}`}
            className="min-w-0 space-y-3 rounded-xl border border-slate-100 p-3"
          >
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img
              src={api.travelPhotoUrl(detail.version.id, photo.id)}
              alt={photo.caption || `旅行照片${i + 1}`}
              className="h-44 w-full rounded-lg bg-slate-50 object-contain"
            />
            <label className="block text-xs text-slate-600">
              照片说明
              <textarea
                rows={2}
                maxLength={500}
                className={field}
                value={photo.caption}
                onChange={(e) => update(photo.id, { caption: e.target.value })}
              />
            </label>
            <label className="block text-xs text-slate-600">
              照片日期
              <select
                aria-label="照片日期"
                className={field}
                value={photo.day ?? ""}
                onChange={(e) =>
                  update(photo.id, {
                    day: e.target.value ? Number(e.target.value) : null,
                  })
                }
              >
                <option value="">暂不标注</option>
                {photo.day && !days.some((d) => d.day === photo.day) && (
                  <option value={photo.day}>原行程第{photo.day}天</option>
                )}
                {days.map((d) => (
                  <option key={d.day} value={d.day}>
                    第{d.day}天
                  </option>
                ))}
              </select>
            </label>
            <label className="block text-xs text-slate-600">
              关联地点
              <select
                aria-label="照片关联地点"
                className={field}
                value={photo.activity_id ?? ""}
                onChange={(e) =>
                  update(photo.id, { activity_id: e.target.value || null })
                }
              >
                <option value="">不关联</option>
                {photo.activity_id &&
                  !activities.some((a) => a.id === photo.activity_id) && (
                    <option value={photo.activity_id}>
                      原关联地点已不在当前计划
                    </option>
                  )}
                {activities.map((a) => (
                  <option key={a.id} value={a.id}>
                    第{a.day}天 · {a.title}
                  </option>
                ))}
              </select>
            </label>
            <label className="flex items-center gap-2 text-xs text-teal-800">
              <input
                type="checkbox"
                checked={photo.included}
                onChange={(e) =>
                  update(photo.id, { included: e.target.checked })
                }
              />
              选入图文回顾
            </label>
            <div className="flex flex-wrap gap-3 text-xs">
              <button
                type="button"
                className="text-teal-800"
                aria-pressed={cover === photo.id}
                onClick={() =>
                  onChange(
                    photos.map((p) =>
                      p.id === photo.id ? { ...p, included: true } : p,
                    ),
                    photo.id,
                  )
                }
              >
                {cover === photo.id ? "当前封面" : "设为封面并选入"}
              </button>
              <button
                type="button"
                className="text-slate-500"
                onClick={() =>
                  onChange(
                    photos.filter((p) => p.id !== photo.id),
                    cover === photo.id ? null : cover,
                  )
                }
              >
                移除照片
              </button>
            </div>
          </article>
        ))}
      </div>
      <p className="text-[11px] leading-5 text-slate-500">
        支持静态PNG、JPEG、WebP，每张4MB。每次旅行最多100张、128MB；上传后需保存回顾才会关联，未保存的上传也占额度。移除不清理历史图片，删除旅行一起清理。图文导出只包含已保存且选入的照片，所选图片合计最多16MB。
      </p>
    </fieldset>
  );
}
