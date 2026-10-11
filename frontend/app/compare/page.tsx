"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { GitBranch, Star } from "lucide-react";
import ReactMarkdown from "react-markdown";

import { Badge, Button, Card, EmptyState, ErrorBanner, Spinner } from "@/components/ui";
import { api } from "@/lib/api";
import type { BranchInfo, SessionInfo, VersionDetail } from "@/lib/types";
import { cn } from "@/lib/utils";

interface Column {
  branch: BranchInfo;
  detail: VersionDetail | null;
  loading: boolean;
  error: string | null;
}

export default function ComparePage() {
  const [sessions, setSessions] = useState<SessionInfo[] | null>(null);
  const [sessionId, setSessionId] = useState<string>("");
  const [branches, setBranches] = useState<BranchInfo[]>([]);
  const [selected, setSelected] = useState<string[]>([]);
  const [columns, setColumns] = useState<Column[]>([]);
  const [error, setError] = useState<string | null>(null);

  const loadSessions = useCallback(async () => {
    setError(null);
    try {
      const result = await api.listSessions();
      setSessions(result.items);
      if (result.items.length > 0) setSessionId((prev) => prev || result.items[0].id);
    } catch (err) {
      setError(err instanceof Error ? err.message : "加载失败");
      setSessions([]);
    }
  }, []);

  useEffect(() => {
    void loadSessions();
  }, [loadSessions]);

  const loadBranches = useCallback(async (id: string) => {
    try {
      const result = await api.listBranches(id);
      setBranches(result.items);
      setSelected(result.items.slice(0, 3).map((b) => b.id));
    } catch (err) {
      setError(err instanceof Error ? err.message : "加载分支失败");
    }
  }, []);

  useEffect(() => {
    if (sessionId) void loadBranches(sessionId);
  }, [sessionId, loadBranches]);

  // 拉取每个被选中分支的最新版本
  useEffect(() => {
    let cancelled = false;

    const initial: Column[] = selected.map((id) => ({
      branch: branches.find((b) => b.id === id) as BranchInfo,
      detail: null,
      loading: true,
      error: null,
    }));
    setColumns(initial.filter((c) => c.branch));

    void Promise.all(
      selected.map(async (branchId) => {
        const branch = branches.find((b) => b.id === branchId);
        if (!branch) return null;
        try {
          const list = await api.listVersions(branchId);
          const latest = list.items[list.items.length - 1];
          if (!latest) {
            return { branch, detail: null, loading: false, error: "该分支还没有路书" } as Column;
          }
          const detail = await api.getVersion(latest.id);
          return { branch, detail, loading: false, error: null } as Column;
        } catch (err) {
          return {
            branch,
            detail: null,
            loading: false,
            error: err instanceof Error ? err.message : "加载失败",
          } as Column;
        }
      }),
    ).then((results) => {
      if (!cancelled) setColumns(results.filter(Boolean) as Column[]);
    });

    return () => {
      cancelled = true;
    };
  }, [selected, branches]);

  function toggle(id: string) {
    setSelected((prev) =>
      prev.includes(id) ? prev.filter((x) => x !== id) : prev.length >= 3 ? prev : [...prev, id],
    );
  }

  async function setPrimary(branchId: string) {
    try {
      await api.setPrimaryBranch(branchId);
      await loadBranches(sessionId);
    } catch (err) {
      setError(err instanceof Error ? err.message : "操作失败");
    }
  }

  const hasBranches = useMemo(() => branches.length > 0, [branches]);

  if (!sessions) {
    return (
      <div className="mx-auto max-w-2xl px-5 py-10">
        {error ? <ErrorBanner message={error} onRetry={loadSessions} /> : <Spinner />}
      </div>
    );
  }

  if (sessions.length === 0) {
    return (
      <div className="mx-auto max-w-2xl px-5 py-10">
        <EmptyState
          icon={<GitBranch className="h-9 w-9" />}
          title="还没有可对比的旅行"
          description="先在工作台创建一个旅行并生成路书"
          action={
            <Link href="/">
              <Button variant="primary">去工作台</Button>
            </Link>
          }
        />
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-[1400px] px-5 py-8">
      <div className="mb-5 flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-lg font-bold">分支对比</h1>
          <p className="text-xs text-slate-400">
            最多同时对比 3 个分支（选择后自动加载各分支最新路书）
          </p>
        </div>
        <select
          className="rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm outline-none focus:border-brand-400"
          value={sessionId}
          onChange={(e) => setSessionId(e.target.value)}
        >
          {sessions.map((s) => (
            <option key={s.id} value={s.id}>
              {s.title}
            </option>
          ))}
        </select>
      </div>

      {error && (
        <div className="mb-4">
          <ErrorBanner message={error} onRetry={() => loadBranches(sessionId)} />
        </div>
      )}

      {!hasBranches ? (
        <EmptyState
          icon={<GitBranch className="h-9 w-9" />}
          title="该旅行还没有分支"
          description="在对话页可以从任意版本「从这里分叉」创建新分支"
        />
      ) : (
        <>
          <div className="mb-4 flex flex-wrap gap-2">
            {branches.map((branch) => {
              const active = selected.includes(branch.id);
              return (
                <button
                  key={branch.id}
                  onClick={() => toggle(branch.id)}
                  className={cn(
                    "rounded-full border px-3.5 py-1.5 text-xs transition",
                    active
                      ? "border-transparent bg-gradient-to-r from-brand-500 to-teal-500 font-semibold text-white"
                      : "border-slate-200 bg-white text-slate-600 hover:border-brand-300",
                  )}
                >
                  {branch.title}
                  {branch.is_primary && " ★"}
                  {branch.parent_branch_id && "（分叉）"}
                </button>
              );
            })}
          </div>

          <div
            className="grid gap-3.5"
            style={{ gridTemplateColumns: `repeat(${Math.max(columns.length, 1)}, minmax(0, 1fr))` }}
          >
            {columns.map((column) => (
              <Card key={column.branch.id} className="flex flex-col overflow-hidden">
                <div className="flex items-center justify-between border-b border-slate-100 bg-slate-50 px-3.5 py-2.5">
                  <div>
                    <div className="flex items-center gap-2 text-sm font-bold text-slate-700">
                      {column.branch.title}
                      {column.branch.is_primary ? (
                        <Badge tone="brand">当前</Badge>
                      ) : (
                        <Badge tone="teal">分支</Badge>
                      )}
                    </div>
                    {column.detail && (
                      <div className="mt-0.5 text-[11px] text-slate-400">
                        {column.detail.markdown.length} 字 ·{" "}
                        {column.detail.citations.length} 个来源
                      </div>
                    )}
                  </div>
                  {!column.branch.is_primary && (
                    <Button size="sm" onClick={() => setPrimary(column.branch.id)}>
                      <Star className="h-3 w-3" />
                      设为当前
                    </Button>
                  )}
                </div>

                <div className="max-h-[560px] overflow-y-auto p-3.5">
                  {column.loading ? (
                    <Spinner label="加载中…" />
                  ) : column.error ? (
                    <p className="text-xs text-slate-400">{column.error}</p>
                  ) : (
                    <div className="prose-report">
                      <ReactMarkdown>{column.detail?.markdown ?? ""}</ReactMarkdown>
                    </div>
                  )}
                </div>
              </Card>
            ))}
          </div>
        </>
      )}
    </div>
  );
}
