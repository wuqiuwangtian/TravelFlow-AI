"use client";

import { useRef, useState } from "react";
import { RecapImage } from "@/components/recap-image";
import { ActionFeedback, Button } from "@/components/ui";
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
  const uploadLock = useRef(false);
  const [notice, setNotice] = useState<{
    target: string;
    message: string;
  } | null>(null);
  const [error, setError] = useState("");
  const [uploading, setUploading] = useState(false);
  const days = detail.itinerary?.days ?? [];
  const activities = days.flatMap((d) =>
    d.activities
      .filter((a) => !a.is_context_anchor)
      .map((a) => ({ ...a, day: d.day })),
  );
  const field = "tf-form-control mt-1";
  function update(id: string, patch: Partial<RecapPhoto>) {
    onChange(
      photos.map((p) => (p.id === id ? { ...p, ...patch } : p)),
      patch.included === false && cover === id ? null : cover,
    );
  }
  async function upload(files: File[]) {
    if (uploadLock.current) return;
    uploadLock.current = true;
    setNotice(null);
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
      const added = next.length - photos.length;
      if (added)
        setNotice({
          target: "general",
          message: `已上传${added}张照片。选入后保存回顾，才会进入记录。`,
        });
      onChange(next, cover);
      uploadLock.current = false;
      setUploading(false);
      onBusy(false);
    }
  }
  return (
    <fieldset
      disabled={disabled || uploading}
      aria-label="旅行照片"
      className="history-photo-editor space-y-3 disabled:opacity-60"
    >
      <legend className="px-1 text-sm font-semibold text-[var(--tf-accent)]">
        旅行照片 · {photos.length}/40
      </legend>
      <p className="text-xs leading-6 text-[var(--tf-muted)]">
        照片保存在本机，清除定位等元数据，不发送模型。关联地点不代表确认到访；原图请自行保留。
      </p>
      <div className="flex flex-wrap gap-3 text-xs text-[var(--tf-accent)]">
        <label
          className="history-upload"
          data-disabled={
            disabled || uploading || photos.length >= 40 || undefined
          }
        >
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
          <Button
            type="button"
            onClick={() =>
              onChange(
                photos.map((p) => ({ ...p, included: true })),
                cover,
              )
            }
          >
            全部选入回顾
          </Button>
        )}
        {cover && (
          <Button type="button" onClick={() => onChange(photos, null)}>
            清除封面
          </Button>
        )}
      </div>
      {uploading && (
        <p role="status" className="text-xs text-[var(--tf-accent)]">
          正在保存照片…
        </p>
      )}
      {error && (
        <p role="alert" className="text-xs text-[var(--tf-danger)]">
          {error}
        </p>
      )}
      {notice?.target === "general" && (
        <ActionFeedback message={notice.message} />
      )}
      <div className="history-photo-list">
        {photos.map((photo, i) => (
          <article
            key={photo.id}
            aria-label={`旅行照片${i + 1}`}
            className="min-w-0 space-y-3 rounded-[var(--tf-radius)] border border-[var(--tf-line)] p-3"
          >
            <RecapImage
              versionId={detail.version.id}
              photoId={photo.id}
              label={photo.caption || `旅行照片${i + 1}`}
              mode="contain"
            />
            <label className="block text-xs text-[var(--tf-muted)]">
              照片说明
              <textarea
                rows={2}
                maxLength={500}
                className={field}
                value={photo.caption}
                onChange={(e) => update(photo.id, { caption: e.target.value })}
              />
            </label>
            <label className="block text-xs text-[var(--tf-muted)]">
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
            <label className="block text-xs text-[var(--tf-muted)]">
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
            <label className="flex items-center gap-2 text-xs text-[var(--tf-accent)]">
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
              <Button
                type="button"
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
              </Button>
              <Button
                type="button"
                onClick={() => {
                  onChange(
                    photos.filter((p) => p.id !== photo.id),
                    cover === photo.id ? null : cover,
                  );
                  setNotice({
                    target: "general",
                    message:
                      "照片已移出当前草稿，历史图片保留。保存回顾后生效。",
                  });
                }}
              >
                移除照片
              </Button>
            </div>
          </article>
        ))}
      </div>
      <p className="text-[11px] leading-5 text-[var(--tf-muted)]">
        支持静态PNG、JPEG、WebP，每张4MB。每次旅行最多100张、128MB；上传后需保存回顾才会关联，未保存的上传也占额度。移除不清理历史图片，删除旅行一起清理。图文导出只包含已保存且选入的照片，所选图片合计最多16MB。
      </p>
    </fieldset>
  );
}
