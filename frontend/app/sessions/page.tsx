"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import {
  Archive,
  MessageSquare,
  Pencil,
  Plus,
  RotateCcw,
  Search,
  Trash2,
} from "lucide-react";
import {
  ActionFeedback,
  Badge,
  Button,
  Card,
  EmptyState,
  ErrorBanner,
  Spinner,
} from "@/components/ui";
import { RenameForm } from "@/components/rename-form";
import { api } from "@/lib/api";
import type { SessionInfo } from "@/lib/types";
import { relativeTime } from "@/lib/utils";

type Filter = "active" | "archived";
type RowAction = "archive" | "delete";
type RowState = { pending?: RowAction; error?: string; retry?: RowAction };

export default function SessionsPage() {
  const [filter, setFilter] = useState<Filter>("active");
  const [query, setQuery] = useState("");
  const [sessions, setSessions] = useState<SessionInfo[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [notice, setNotice] = useState("");
  const [renamingId, setRenamingId] = useState<string | null>(null);
  const [rows, setRows] = useState<Record<string, RowState>>({});
  const locks = useRef(new Set<string>());
  const generation = useRef(0);
  const renameTrigger = useRef<HTMLButtonElement | null>(null);
  const reload = useRef<() => Promise<void>>(async () => {});

  const load = useCallback(async () => {
    const token = ++generation.current;
    setLoading(true);
    setError(null);
    try {
      const result = await api.listSessions({
        archived: filter === "archived",
        q: query.trim() || undefined,
      });
      if (token === generation.current) setSessions(result.items);
    } catch (err) {
      if (token === generation.current)
        setError(err instanceof Error ? err.message : "加载失败");
    } finally {
      if (token === generation.current) setLoading(false);
    }
  }, [filter, query]);
  useEffect(() => {
    void load();
  }, [load]);
  useEffect(() => {
    reload.current = load;
  }, [load]);

  async function act(session: SessionInfo, action: RowAction) {
    if (locks.current.has(session.id)) return;
    if (
      action === "delete" &&
      !window.confirm(
        `确定删除「${session.title}」？该操作会同时删除其所有分支与版本。`,
      )
    )
      return;
    locks.current.add(session.id);
    setRows((old) => ({ ...old, [session.id]: { pending: action } }));
    setNotice("");
    try {
      if (action === "delete") await api.deleteSession(session.id);
      else await api.updateSession(session.id, { archived: !session.archived });
      setNotice(
        `「${session.title}」已${action === "delete" ? "删除" : session.archived ? "恢复" : "归档"}。`,
      );
      setRows((old) => ({ ...old, [session.id]: {} }));
      await reload.current();
    } catch (err) {
      setRows((old) => ({
        ...old,
        [session.id]: {
          error: err instanceof Error ? err.message : "操作未完成，请重试。",
          retry: action,
        },
      }));
    } finally {
      locks.current.delete(session.id);
    }
  }

  function closeRename() {
    setRenamingId(null);
    requestAnimationFrame(() => renameTrigger.current?.focus());
  }

  return (
    <div className="tf-page tf-sessions-page">
      <div className="tf-page-heading">
        <div>
          <h1>我的旅行</h1>
          <p className="text-xs text-[var(--tf-muted)]">
            继续规划、归档或整理已保存的旅行。
          </p>
        </div>
        <Link href="/" className="tf-action-link tf-page-link">
          <Plus className="h-4 w-4" />
          新建旅行
        </Link>
      </div>
      <div className="tf-session-tools">
        <label className="relative min-w-0 flex-1">
          <span className="sr-only">搜索旅行名称</span>
          <Search
            aria-hidden="true"
            className="pointer-events-none absolute top-3 left-3 h-4 w-4 text-[var(--tf-muted)]"
          />
          <input
            className="tf-form-control"
            placeholder="搜索旅行名称…"
            disabled={!!renamingId}
            value={query}
            onChange={(event) => setQuery(event.target.value)}
          />
        </label>
        <div className="tf-segment">
          {(
            [
              ["active", "活跃"],
              ["archived", "已归档"],
            ] as [Filter, string][]
          ).map(([value, label]) => (
            <Button
              size="sm"
              key={value}
              disabled={!!renamingId}
              aria-pressed={filter === value}
              onClick={() => setFilter(value)}
            >
              {label}
            </Button>
          ))}
        </div>
      </div>
      <ActionFeedback message={notice} />
      {error && (
        <div className="mb-4">
          <ErrorBanner message={error} onRetry={load} />
        </div>
      )}
      {loading ? (
        <Card className="p-6">
          <Spinner label="正在加载旅行…" />
        </Card>
      ) : !error && sessions?.length === 0 ? (
        <EmptyState
          icon={<MessageSquare className="h-9 w-9" />}
          title={
            query.trim()
              ? "没有匹配的旅行"
              : filter === "archived"
                ? "没有已归档的旅行"
                : "还没有旅行计划"
          }
          description={
            query.trim()
              ? "试试其他名称或清空搜索。"
              : filter === "active"
                ? "回到工作台新建一个吧"
                : undefined
          }
        />
      ) : (
        sessions && (
          <Card className="mt-3 overflow-hidden">
            <ul aria-label="旅行列表" className="tf-session-list">
              {sessions.map((session) => {
                const state = rows[session.id] ?? {};
                const editing = renamingId === session.id;
                return (
                  <li
                    key={session.id}
                    aria-label={`旅行：${session.title}`}
                    className="tf-session-row"
                  >
                    <div className="min-w-0 flex-1">
                      {editing ? (
                        <RenameForm
                          title={session.title}
                          onClose={closeRename}
                          onSave={async (title) => {
                            if (title === session.title) {
                              setNotice("名称未更改。");
                              return;
                            }
                            const result = await api.updateSession(session.id, {
                              title,
                            });
                            generation.current++;
                            setSessions(
                              (old) =>
                                old?.map((item) =>
                                  item.id === session.id
                                    ? { ...item, ...result.session }
                                    : item,
                                ) ?? null,
                            );
                            setNotice(
                              `旅行名称已保存为「${result.session.title}」。`,
                            );
                          }}
                        />
                      ) : (
                        <Link
                          className="tf-action-link tf-session-title"
                          href={`/chat/${session.id}`}
                        >
                          {session.title}
                        </Link>
                      )}
                      <div className="mt-2 flex flex-wrap items-center gap-3 text-xs text-[var(--tf-muted)]">
                        <span>{session.branch_count} 个分支</span>
                        <span>{relativeTime(session.updated_at)}</span>
                        {session.archived && <Badge tone="slate">已归档</Badge>}
                      </div>
                      <ActionFeedback
                        pending={
                          state.pending
                            ? state.pending === "delete"
                              ? "正在删除旅行…"
                              : session.archived
                                ? "正在恢复旅行…"
                                : "正在归档旅行…"
                            : undefined
                        }
                        error={state.error}
                        onRetry={
                          state.retry
                            ? () => void act(session, state.retry!)
                            : undefined
                        }
                      />
                    </div>
                    <div className="tf-session-row-actions">
                      <Link
                        className="tf-action-link tf-page-link"
                        href={`/chat/${session.id}`}
                      >
                        打开
                      </Link>
                      <Button
                        size="sm"
                        disabled={!!state.pending || !!renamingId}
                        onClick={(event) => {
                          renameTrigger.current = event.currentTarget;
                          setNotice("");
                          setRenamingId(session.id);
                        }}
                      >
                        <Pencil className="h-3 w-3" />
                        重命名
                      </Button>
                      <Button
                        size="sm"
                        disabled={!!renamingId || !!state.pending}
                        loading={state.pending === "archive"}
                        loadingLabel={session.archived ? "恢复中…" : "归档中…"}
                        onClick={() => void act(session, "archive")}
                      >
                        {session.archived ? (
                          <RotateCcw className="h-3 w-3" />
                        ) : (
                          <Archive className="h-3 w-3" />
                        )}
                        {session.archived ? "恢复" : "归档"}
                      </Button>
                      <Button
                        size="sm"
                        variant="danger"
                        aria-label={`删除旅行：${session.title}`}
                        disabled={!!renamingId || !!state.pending}
                        loading={state.pending === "delete"}
                        loadingLabel="删除中…"
                        onClick={() => void act(session, "delete")}
                      >
                        <Trash2 className="h-3 w-3" />
                        删除
                      </Button>
                    </div>
                  </li>
                );
              })}
            </ul>
          </Card>
        )
      )}
    </div>
  );
}
