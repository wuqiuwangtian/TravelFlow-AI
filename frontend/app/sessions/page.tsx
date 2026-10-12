"use client";

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { Archive, MessageSquare, Pencil, Plus, RotateCcw, Search, Trash2 } from "lucide-react";

import { Badge, Button, Card, EmptyState, ErrorBanner, Spinner } from "@/components/ui";
import { api } from "@/lib/api";
import type { SessionInfo } from "@/lib/types";
import { cn, relativeTime } from "@/lib/utils";

type Filter = "active" | "archived";

export default function SessionsPage() {
  const router = useRouter();
  const [filter, setFilter] = useState<Filter>("active");
  const [query, setQuery] = useState("");
  const [sessions, setSessions] = useState<SessionInfo[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  // 正在重命名的会话 id（行内编辑）
  const [renamingId, setRenamingId] = useState<string | null>(null);

  const load = useCallback(async () => {
    setError(null);
    try {
      const result = await api.listSessions({
        archived: filter === "archived",
        q: query.trim() || undefined,
      });
      setSessions(result.items);
    } catch (err) {
      setError(err instanceof Error ? err.message : "加载失败");
      setSessions([]);
    }
  }, [filter, query]);

  useEffect(() => {
    void load();
  }, [load]);

  async function toggleArchive(session: SessionInfo) {
    try {
      await api.updateSession(session.id, { archived: !session.archived });
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "操作失败");
    }
  }

  /** 提交行内改名；空标题视为取消。 */
  async function commitRename(sessionId: string, rawTitle: string) {
    const title = rawTitle.trim();
    setRenamingId(null);
    const current = sessions?.find((s) => s.id === sessionId);
    if (!title || title === current?.title) return;

    try {
      await api.updateSession(sessionId, { title });
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "重命名失败");
    }
  }

  async function remove(session: SessionInfo) {
    if (!window.confirm(`确定删除「${session.title}」？该操作会同时删除其所有分支与版本。`)) {
      return;
    }
    try {
      await api.deleteSession(session.id);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "删除失败");
    }
  }

  return (
    <div className="mx-auto max-w-4xl px-5 py-8">
      <div className="mb-5 flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-lg font-bold">会话管理</h1>
          <p className="text-xs text-slate-400">所有数据保存在本机数据库中</p>
        </div>
        <Button variant="primary" onClick={() => router.push("/")}>
          <Plus className="h-4 w-4" />
          新建旅行
        </Button>
      </div>

      {error && (
        <div className="mb-4">
          <ErrorBanner message={error} onRetry={load} />
        </div>
      )}

      <div className="mb-4 flex flex-wrap items-center gap-3">
        <div className="relative flex-1 min-w-[200px]">
          <Search className="pointer-events-none absolute top-2.5 left-3 h-4 w-4 text-slate-300" />
          <input
            className="w-full rounded-lg border border-slate-200 bg-white py-2 pr-3 pl-9 text-sm outline-none transition focus:border-brand-400 focus:ring-2 focus:ring-brand-100"
            placeholder="搜索旅行名称…"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
        </div>

        <div className="inline-flex overflow-hidden rounded-lg border border-slate-200">
          {(
            [
              ["active", "活跃"],
              ["archived", "已归档"],
            ] as [Filter, string][]
          ).map(([value, label]) => (
            <button
              key={value}
              onClick={() => setFilter(value)}
              className={cn(
                "px-3.5 py-2 text-xs transition",
                filter === value
                  ? "bg-gradient-to-r from-brand-500 to-teal-500 font-semibold text-white"
                  : "bg-white text-slate-600 hover:bg-slate-50",
              )}
            >
              {label}
            </button>
          ))}
        </div>
      </div>

      {sessions === null ? (
        <Card className="p-6">
          <Spinner />
        </Card>
      ) : sessions.length === 0 ? (
        <EmptyState
          icon={<MessageSquare className="h-9 w-9" />}
          title={filter === "archived" ? "没有已归档的旅行" : "还没有旅行计划"}
          description={filter === "archived" ? undefined : "回到工作台新建一个吧"}
        />
      ) : (
        <Card className="overflow-hidden">
          <table className="w-full">
            <thead>
              <tr className="border-b border-slate-100 bg-slate-50 text-xs text-slate-400">
                <th className="px-4 py-2.5 text-left font-medium">旅行</th>
                <th className="px-4 py-2.5 text-left font-medium">分支</th>
                <th className="px-4 py-2.5 text-left font-medium">更新时间</th>
                <th className="px-4 py-2.5 text-right font-medium">操作</th>
              </tr>
            </thead>
            <tbody>
              {sessions.map((session) => (
                <tr key={session.id} className="border-b border-slate-50 last:border-0 hover:bg-slate-50/60">
                  <td className="px-4 py-3">
                    {renamingId === session.id ? (
                      <input
                        autoFocus
                        defaultValue={session.title}
                        className="w-full max-w-[220px] rounded-md border border-brand-300 px-2 py-1 text-sm outline-none focus:ring-2 focus:ring-brand-100"
                        onBlur={(e) => void commitRename(session.id, e.target.value)}
                        onKeyDown={(e) => {
                          if (e.key === "Enter") e.currentTarget.blur();
                          if (e.key === "Escape") setRenamingId(null);
                        }}
                      />
                    ) : (
                      <>
                        <button
                          className="text-sm font-semibold text-slate-700 hover:text-brand-600"
                          onClick={() => router.push(`/chat/${session.id}`)}
                          title="点击打开；如需改名请用右侧「重命名」"
                        >
                          {session.title}
                        </button>
                        {session.archived && (
                          <span className="ml-2">
                            <Badge tone="slate">已归档</Badge>
                          </span>
                        )}
                      </>
                    )}
                  </td>
                  <td className="px-4 py-3 text-sm text-slate-500">{session.branch_count}</td>
                  <td className="px-4 py-3 text-sm text-slate-400">
                    {relativeTime(session.updated_at)}
                  </td>
                  <td className="px-4 py-3">
                    <div className="flex justify-end gap-1.5">
                      <Button size="sm" onClick={() => router.push(`/chat/${session.id}`)}>
                        打开
                      </Button>
                      <Button size="sm" onClick={() => setRenamingId(session.id)}>
                        <Pencil className="h-3 w-3" />
                        重命名
                      </Button>
                      <Button size="sm" onClick={() => toggleArchive(session)}>
                        {session.archived ? (
                          <>
                            <RotateCcw className="h-3 w-3" />
                            恢复
                          </>
                        ) : (
                          <>
                            <Archive className="h-3 w-3" />
                            归档
                          </>
                        )}
                      </Button>
                      <Button size="sm" variant="danger" onClick={() => remove(session)}>
                        <Trash2 className="h-3 w-3" />
                      </Button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      )}
    </div>
  );
}
