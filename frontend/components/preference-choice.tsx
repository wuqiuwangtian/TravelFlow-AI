"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { api } from "@/lib/api";
import type { SavedPreferences } from "@/lib/types";

export function PreferenceChoice({
  selected,
  onChange,
  disabled,
}: {
  selected: SavedPreferences | null;
  onChange: (value: SavedPreferences | null) => void;
  disabled: boolean;
}) {
  const [profile, setProfile] = useState<SavedPreferences | null>(null);
  const [error, setError] = useState(false);
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    let cancelled = false;
    api
      .travelPreferences()
      .then((value) => {
        if (!cancelled) {
          setProfile(value);
          setError(false);
        }
      })
      .catch(() => {
        if (!cancelled) setError(true);
      });
    return () => {
      cancelled = true;
    };
  }, [retry]);
  return (
    <section
      aria-label="新旅行偏好"
      className="mt-5 max-w-lg rounded-xl bg-white/95 p-4 text-xs leading-6 text-slate-600"
    >
      {profile?.preferences.length ? (
        <>
          <label className="flex cursor-pointer items-start gap-2 text-sm font-medium text-teal-900">
            <input
              type="checkbox"
              className="mt-1 shrink-0 accent-teal-700"
              checked={!!selected}
              disabled={disabled}
              onChange={(e) => onChange(e.target.checked ? profile : null)}
            />
            本次使用常用偏好
          </label>
          <p className="mt-2 break-words">{profile.preferences.join(" · ")}</p>
          <p className="mt-1 text-slate-500">
            仅勾选后用于新旅行，本次对话可覆盖或清空。
          </p>
        </>
      ) : error ? (
        <p>
          常用偏好暂时未载入，仍可直接开始旅行。
          <button
            disabled={disabled}
            className="ml-2 text-teal-800 underline"
            onClick={() => setRetry(retry + 1)}
          >
            重试加载偏好
          </button>
        </p>
      ) : profile ? (
        <p>还没有常用偏好，可以直接开始旅行。</p>
      ) : (
        <p>正在加载常用偏好…</p>
      )}
      <Link
        href="/preferences"
        className="mt-2 inline-block text-teal-800 underline"
      >
        管理常用偏好
      </Link>
    </section>
  );
}
