"use client";

import { useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import type { VersionDetail } from "@/lib/types";

/** 路书读取与对话读取分别恢复；同分支失败时保留已读内容及其组件草稿。 */
export function useVersionRead(
  branch: string | null,
  latest: string | null,
  reload: unknown,
) {
  const [stored, setStored] = useState<VersionDetail | null>(null);
  const [selection, setSelection] = useState<{
    branch: string;
    id: string;
    latest: string | null;
  } | null>(null);
  const [read, setRead] = useState<{
    branch: string | null;
    id: string | null;
    loading: boolean;
    error: string | null;
    reload: unknown;
    refresh: number;
  }>({
    branch: null,
    id: null,
    loading: false,
    error: null,
    reload: null,
    refresh: 0,
  });
  const [refresh, setRefresh] = useState(0);
  const epoch = useRef(0);
  const target =
    selection?.branch === branch && selection.latest === latest
      ? selection.id
      : latest;
  const matches =
    read.branch === branch &&
    read.id === target &&
    read.reload === reload &&
    read.refresh === refresh;
  const loading = !!target && (!matches || read.loading);
  const error = matches ? read.error : null;
  const detail = stored?.version.branch_id === branch ? stored : null;

  useEffect(() => {
    const request = ++epoch.current;
    if (!branch || !target) return;
    let cancelled = false;
    void api
      .getVersion(target)
      .then((data) => {
        if (cancelled || request !== epoch.current) return;
        setStored(data);
        setRead({
          branch,
          id: target,
          loading: false,
          error: null,
          reload,
          refresh,
        });
      })
      .catch((err) => {
        if (cancelled || request !== epoch.current) return;
        setRead({
          branch,
          id: target,
          loading: false,
          error: err instanceof Error ? err.message : "路书读取失败，请重试。",
          reload,
          refresh,
        });
      });
    return () => {
      cancelled = true;
    };
  }, [branch, target, reload, refresh]);

  return {
    detail,
    loading,
    error,
    target,
    retry: () => setRefresh((value) => value + 1),
    select: (id: string) => {
      if (!branch) return;
      ++epoch.current;
      setSelection({ branch, id, latest });
      setRefresh((value) => value + 1);
    },
    accept: (updated: VersionDetail) => {
      ++epoch.current;
      setSelection(null);
      setStored(updated);
      setRead({
        branch: updated.version.branch_id,
        id: updated.version.id,
        loading: false,
        error: null,
        reload,
        refresh,
      });
    },
  };
}
