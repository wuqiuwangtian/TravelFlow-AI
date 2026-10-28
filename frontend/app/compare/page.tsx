"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { ArrowUpRight, GitBranch, Star } from "lucide-react";
import ReactMarkdown from "react-markdown";

import {
  ActionFeedback,
  Badge,
  Button,
  Card,
  EmptyState,
  ErrorBanner,
  Spinner,
} from "@/components/ui";
import { api } from "@/lib/api";
import type { BranchInfo, SessionInfo, VersionDetail } from "@/lib/types";

interface PrimaryState {
  session: string;
  branch: string;
  pending?: boolean;
  error?: string;
  message?: string;
}

/** Each selected branch owns its request, so slow or failed siblings do not block it. */
function ComparisonColumn({
  branch,
  primary,
  primaryBusy,
  onPrimary,
}: {
  branch: BranchInfo;
  primary: PrimaryState | null;
  primaryBusy: boolean;
  onPrimary: (id: string) => Promise<void>;
}) {
  const [result, setResult] = useState<{
    detail: VersionDetail | null;
    loading: boolean;
    error: string | null;
  }>({ detail: null, loading: true, error: null });
  const [refresh, setRefresh] = useState(0);
  useEffect(() => {
    let cancelled = false;
    setResult({ detail: null, loading: true, error: null });
    void (async () => {
      try {
        const versions = await api.listVersions(branch.id);
        const latest = versions.items.at(-1);
        const detail = latest ? await api.getVersion(latest.id) : null;
        if (!cancelled) setResult({ detail, loading: false, error: null });
      } catch (err) {
        if (!cancelled)
          setResult({
            detail: null,
            loading: false,
            error: err instanceof Error ? err.message : "加载失败",
          });
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [branch.id, refresh]);

  return (
    <Card
      role="region"
      aria-label={`对比方案：${branch.title}`}
      className="tf-compare-column"
    >
      <div className="tf-compare-column-heading">
        <div className="min-w-0">
          <h2 id={`compare-${branch.id}`} tabIndex={-1}>
            {branch.title}
          </h2>
          <div className="tf-compare-meta">
            <Badge tone={branch.is_primary ? "brand" : "teal"}>
              {branch.is_primary ? "当前" : "分支"}
            </Badge>
            {result.detail && (
              <span>
                版本 {result.detail.version.seq} ·{" "}
                {result.detail.citations.length} 个来源
              </span>
            )}
          </div>
        </div>
        {!branch.is_primary && (
          <Button
            size="sm"
            loading={primary?.pending}
            loadingLabel="正在设置…"
            disabled={primaryBusy}
            onClick={() => void onPrimary(branch.id)}
          >
            <Star aria-hidden="true" className="h-3.5 w-3.5" />
            设为当前
          </Button>
        )}
      </div>
      {primary && (
        <div className="tf-compare-feedback">
          <ActionFeedback
            pending={primary.pending ? "正在设置当前方案…" : undefined}
            error={primary.error}
            message={primary.message}
            onRetry={
              primary.error ? () => void onPrimary(branch.id) : undefined
            }
          />
        </div>
      )}
      <div
        className="tf-compare-content"
        role="region"
        aria-label={`${branch.title}的路书正文`}
        tabIndex={0}
        aria-busy={result.loading}
      >
        {result.loading ? (
          <Spinner label="正在加载最新路书…" />
        ) : result.error ? (
          <ActionFeedback
            error={result.error}
            onRetry={() => setRefresh((value) => value + 1)}
          />
        ) : result.detail ? (
          <div className="prose-report">
            <ReactMarkdown>{result.detail.markdown}</ReactMarkdown>
          </div>
        ) : (
          <EmptyState
            title="该分支还没有路书"
            description="可进入旅行，继续对话生成。"
          />
        )}
      </div>
      <div className="tf-compare-column-footer">
        <Link
          className="tf-action-link tf-page-link"
          href={`/chat/${branch.session_id}?branch=${branch.id}`}
          aria-label={`继续编辑：${branch.title}`}
        >
          继续编辑
          <ArrowUpRight aria-hidden="true" className="h-3.5 w-3.5" />
        </Link>
        <a
          className="tf-action-link tf-page-link tf-compare-back"
          href="#compare-selection"
        >
          返回方案选择
        </a>
      </div>
    </Card>
  );
}

export default function ComparePage() {
  const [sessions, setSessions] = useState<SessionInfo[] | null>(null);
  const [sessionId, setSessionId] = useState<string>("");
  const [branches, setBranches] = useState<BranchInfo[]>([]);
  const [selected, setSelected] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [branchError, setBranchError] = useState<string | null>(null);
  const [branchLoading, setBranchLoading] = useState(true);
  const [branchOwner, setBranchOwner] = useState("");
  const [primaryState, setPrimaryState] = useState<PrimaryState | null>(null);
  const primaryLock = useRef(false);
  const branchRequest = useRef(0);
  const currentSession = useRef("");

  const loadSessions = useCallback(async () => {
    setError(null);
    try {
      const result = await api.listSessions();
      setSessions(result.items);
      if (result.items.length > 0)
        setSessionId((prev) => prev || result.items[0].id);
    } catch (err) {
      setError(err instanceof Error ? err.message : "加载失败");
    }
  }, []);

  useEffect(() => {
    void loadSessions();
  }, [loadSessions]);

  const loadBranches = useCallback(async (id: string) => {
    const token = ++branchRequest.current;
    setBranchLoading(true);
    setBranchError(null);
    setSelected([]);
    try {
      const result = await api.listBranches(id);
      if (token !== branchRequest.current || id !== currentSession.current)
        return;
      setBranches(result.items);
      setBranchOwner(id);
      setSelected(result.items.slice(0, 3).map((b) => b.id));
    } catch (err) {
      if (token === branchRequest.current)
        setBranchError(err instanceof Error ? err.message : "加载分支失败");
    } finally {
      if (token === branchRequest.current) setBranchLoading(false);
    }
  }, []);

  useEffect(() => {
    currentSession.current = sessionId;
    if (sessionId) void loadBranches(sessionId);
    return () => {
      branchRequest.current++;
    };
  }, [sessionId, loadBranches]);

  function toggle(id: string) {
    setSelected((prev) =>
      prev.includes(id)
        ? prev.filter((x) => x !== id)
        : prev.length >= 3
          ? prev
          : [...prev, id],
    );
  }

  async function setPrimary(branchId: string) {
    if (primaryLock.current) return;
    primaryLock.current = true;
    const owner = sessionId;
    setPrimaryState({ session: owner, branch: branchId, pending: true });
    try {
      const result = await api.setPrimaryBranch(branchId);
      if (owner !== currentSession.current) {
        setPrimaryState(null);
        return;
      }
      setBranches(result.items);
      setPrimaryState({
        session: owner,
        branch: branchId,
        message: "已设为当前方案。",
      });
    } catch (err) {
      if (owner === currentSession.current)
        setPrimaryState({
          session: owner,
          branch: branchId,
          error: err instanceof Error ? err.message : "操作失败",
        });
      else setPrimaryState(null);
    } finally {
      primaryLock.current = false;
    }
  }

  if (!sessions) {
    return (
      <div className="tf-page tf-compare-page">
        {error ? (
          <ErrorBanner message={error} onRetry={loadSessions} />
        ) : (
          <Spinner label="正在加载旅行…" />
        )}
      </div>
    );
  }

  if (sessions.length === 0) {
    return (
      <div className="tf-page tf-compare-page">
        <EmptyState
          icon={<GitBranch className="h-9 w-9" />}
          title="还没有可对比的旅行"
          description="先在工作台创建一个旅行并生成路书"
          action={
            <Link href="/" className="tf-action-link tf-page-link">
              去工作台
            </Link>
          }
        />
      </div>
    );
  }

  return (
    <div className="tf-page tf-compare-page">
      <div className="tf-page-heading">
        <div>
          <h1>分支对比</h1>
          <p>选择同一次旅行的不同方案，比较各自的最新路书，再决定用哪一版。</p>
        </div>
        <Link
          className="tf-action-link tf-page-link"
          href="/help#guide-branches"
        >
          了解分支与版本
        </Link>
      </div>
      <div id="compare-selection" className="tf-compare-selection">
        <label htmlFor="compare-trip">选择对比旅行</label>
        <select
          id="compare-trip"
          value={sessionId}
          onChange={(e) => {
            if (e.target.value === sessionId) return;
            currentSession.current = e.target.value;
            branchRequest.current++;
            setSelected([]);
            setBranches([]);
            setBranchLoading(true);
            setBranchError(null);
            setSessionId(e.target.value);
          }}
        >
          {sessions.map((s) => (
            <option key={s.id} value={s.id}>
              {s.title}
            </option>
          ))}
        </select>
      </div>
      {primaryState?.pending && primaryState.session !== sessionId && (
        <ActionFeedback pending="正在为上一份旅行设置当前方案，完成前请稍候。" />
      )}
      {branchError && (
        <ErrorBanner
          message={branchError}
          onRetry={() => loadBranches(sessionId)}
        />
      )}
      {branchLoading || (branchOwner !== sessionId && !branchError) ? (
        <Spinner label="正在加载方案…" />
      ) : branchError ? null : branches.length === 0 ? (
        <EmptyState
          icon={<GitBranch aria-hidden="true" className="h-9 w-9" />}
          title="该旅行还没有分支"
          description="在对话页可以从任意版本「从这里分叉」创建新分支"
        />
      ) : (
        <>
          <div
            className="tf-compare-choices"
            role="group"
            aria-label="选择对比方案"
            aria-describedby="compare-limit"
          >
            {branches.map((branch) => {
              const active = selected.includes(branch.id);
              return (
                <Button
                  key={branch.id}
                  aria-pressed={active}
                  disabled={!active && selected.length >= 3}
                  onClick={() => toggle(branch.id)}
                >
                  {branch.title}
                  {branch.is_primary && (
                    <span className="tf-compare-current">
                      <Star aria-hidden="true" className="h-3 w-3" />
                      当前
                    </span>
                  )}
                  {branch.parent_branch_id && (
                    <span className="text-[var(--tf-muted)]">（分叉）</span>
                  )}
                </Button>
              );
            })}
          </div>
          <p id="compare-limit" className="tf-field-help">
            已选 {selected.length} / 3 个方案
            {selected.length === 3
              ? "，取消一个后可选择其他方案。"
              : "；点击方案名称可加入或取消。"}
            设为当前只切换旅行的默认方案，保留各分支与历史版本。
          </p>
          {selected.length > 0 ? (
            <>
              <nav className="tf-compare-jumps" aria-label="跳转到对比方案">
                {selected.map((id) => {
                  const branch = branches.find((item) => item.id === id);
                  return branch ? (
                    <a
                      key={id}
                      className="tf-action-link tf-page-link"
                      href={`#compare-${id}`}
                    >
                      {branch.title}
                    </a>
                  ) : null;
                })}
              </nav>
              <div
                className="tf-compare-grid"
                style={
                  { "--compare-count": selected.length } as React.CSSProperties
                }
              >
                {selected.map((id) => {
                  const branch = branches.find((item) => item.id === id);
                  return branch ? (
                    <ComparisonColumn
                      key={`${sessionId}:${id}`}
                      branch={branch}
                      primary={
                        primaryState?.session === sessionId &&
                        primaryState.branch === id
                          ? primaryState
                          : null
                      }
                      primaryBusy={!!primaryState?.pending}
                      onPrimary={setPrimary}
                    />
                  ) : null;
                })}
              </div>
            </>
          ) : (
            <EmptyState
              icon={<GitBranch aria-hidden="true" className="h-9 w-9" />}
              title="选择想比较的方案"
              description="在上方选择 1 至 3 个方案，自动读取最新路书。"
            />
          )}
        </>
      )}
    </div>
  );
}
