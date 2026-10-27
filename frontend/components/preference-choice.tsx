"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { Button, Spinner } from "@/components/ui";
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
  const [loading, setLoading] = useState(true);
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
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [retry]);
  return (
    <section aria-label="新旅行偏好" className="tf-preference-choice">
      {profile?.preferences.length ? (
        <>
          <label className="tf-preference-checkbox">
            <input
              type="checkbox"
              className="shrink-0"
              checked={!!selected}
              disabled={disabled || loading}
              onChange={(e) => onChange(e.target.checked ? profile : null)}
            />
            本次使用常用偏好
          </label>
          <p className="mt-2 break-words">{profile.preferences.join(" · ")}</p>
          <p className="mt-1 text-[var(--tf-muted)]">
            仅勾选后用于新旅行，本次对话可覆盖或清空。
          </p>
        </>
      ) : error ? (
        <p>
          常用偏好暂时未载入，仍可直接开始旅行。
          <Button
            size="sm"
            disabled={disabled || loading}
            className="ml-2"
            onClick={() => {
              setLoading(true);
              setError(false);
              setRetry(retry + 1);
            }}
          >
            重试加载偏好
          </Button>
        </p>
      ) : profile ? (
        <p>还没有常用偏好，可以直接开始旅行。</p>
      ) : (
        <Spinner label="正在加载常用偏好…" />
      )}
      <Link href="/preferences" className="tf-action-link tf-page-link mt-2">
        管理常用偏好
      </Link>
    </section>
  );
}
