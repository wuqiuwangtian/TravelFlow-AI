"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";
import {
  Download,
  GitBranch,
  Loader2,
  Send,
  Sparkles,
  Undo2,
} from "lucide-react";
import ReactMarkdown from "react-markdown";

import { Badge, Button, Card, ErrorBanner, Spinner } from "@/components/ui";
import { api } from "@/lib/api";
import type {
  BranchInfo,
  DetailLevel,
  SessionDetail,
  VersionDetail,
  VersionInfo,
} from "@/lib/types";
import { DETAIL_LEVEL_LABELS, STATUS_LABELS } from "@/lib/types";
import { cn, relativeTime } from "@/lib/utils";

interface Message {
  role: "user" | "assistant";
  content: string;
}

export default function ChatPage() {
  const params = useParams<{ sessionId: string }>();
  const sessionId = params.sessionId;

  const [detail, setDetail] = useState<SessionDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [activeBranchId, setActiveBranchId] = useState<string | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [versionDetail, setVersionDetail] = useState<VersionDetail | null>(null);
  const [input, setInput] = useState("");
  const [level, setLevel] = useState<DetailLevel>("L2_standard");
  const [sending, setSending] = useState(false);
  const bottomRef = useRef<HTMLDivElement>(null);

  const activeBranch = useMemo(
    () => detail?.branches.find((b) => b.id === activeBranchId) ?? null,
    [detail, activeBranchId],
  );
  const versions: VersionInfo[] = useMemo(
    () => (activeBranchId ? (detail?.versions[activeBranchId] ?? []) : []),
    [detail, activeBranchId],
  );

  const load = useCallback(async () => {
    setError(null);
    try {
      const data = await api.getSession(sessionId);
      setDetail(data);
      const primary = data.branches.find((b) => b.is_primary) ?? data.branches[0];
      setActiveBranchId((prev) => prev ?? primary?.id ?? null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "加载会话失败");
    }
  }, [sessionId]);

  useEffect(() => {
    void load();
  }, [load]);

  // 切换分支时载入该分支最新版本的路书
  useEffect(() => {
    const list = activeBranchId ? (detail?.versions[activeBranchId] ?? []) : [];
    const latest = list[list.length - 1];
    if (!latest) {
      setVersionDetail(null);
      return;
    }
    let cancelled = false;
    void api
      .getVersion(latest.id)
      .then((data) => {
        if (!cancelled) setVersionDetail(data);
      })
      .catch(() => {
        if (!cancelled) setVersionDetail(null);
      });
    return () => {
      cancelled = true;
    };
  }, [detail, activeBranchId]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, sending]);

  async function send() {
    const text = input.trim();
    if (!text || !activeBranchId || sending) return;

    setMessages((prev) => [...prev, { role: "user", content: text }]);
    setInput("");
    setSending(true);
    try {
      const result = await api.chat({
        message: text,
        session_id: sessionId,
        branch_id: activeBranchId,
        detail_level: level,
      });
      setMessages((prev) => [...prev, { role: "assistant", content: result.reply }]);
      await load();
      if (result.version_id) {
        const fresh = await api.getVersion(result.version_id);
        setVersionDetail(fresh);
      }
    } catch (err) {
      setMessages((prev) => [
        ...prev,
        { role: "assistant", content: `⚠️ ${err instanceof Error ? err.message : "发送失败"}` },
      ]);
    } finally {
      setSending(false);
    }
  }

  async function forkFromVersion(version: VersionInfo) {
    const title = window.prompt("新分支的名称", `分支 ${version.seq}`);
    if (!title) return;
    try {
      await api.createBranch(sessionId, {
        title,
        parent_branch_id: version.branch_id,
        fork_version_id: version.id,
      });
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "创建分支失败");
    }
  }

  if (error && !detail) {
    return (
      <div className="mx-auto max-w-2xl px-5 py-10">
        <ErrorBanner message={error} onRetry={load} />
      </div>
    );
  }

  if (!detail) {
    return (
      <div className="mx-auto max-w-2xl px-5 py-10">
        <Spinner label="正在加载会话…" />
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-[1400px] px-4 py-5">
      {/* 顶部：会话标题 + 分支切换 */}
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-lg font-bold">{detail.session.title}</h1>
          <p className="text-xs text-slate-400">
            {detail.branches.length} 个分支 · 版本历史可随时回退
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {detail.branches.map((branch: BranchInfo) => (
            <button
              key={branch.id}
              onClick={() => setActiveBranchId(branch.id)}
              className={cn(
                "rounded-full border px-3 py-1 text-xs transition",
                branch.id === activeBranchId
                  ? "border-transparent bg-gradient-to-r from-brand-500 to-teal-500 font-semibold text-white"
                  : "border-slate-200 bg-white text-slate-600 hover:border-brand-300",
              )}
            >
              {branch.title}
              {branch.is_primary && " ★"}
            </button>
          ))}
          <Link href="/compare">
            <Button size="sm">
              <GitBranch className="h-3.5 w-3.5" />
              对比分支
            </Button>
          </Link>
        </div>
      </div>

      <div className="grid gap-3.5 lg:grid-cols-[200px_1fr_1fr]">
        {/* ---------------- 版本树 ---------------- */}
        <Card className="overflow-hidden">
          <div className="border-b border-slate-100 bg-slate-50 px-3.5 py-2.5 text-xs font-bold text-slate-500">
            版本历史（{versions.length}）
          </div>
          <div className="max-h-[540px] overflow-y-auto p-3">
            {versions.length === 0 ? (
              <p className="px-1 py-3 text-xs text-slate-400">还没有版本，先发一条消息吧</p>
            ) : (
              <ol className="relative ml-1.5 border-l border-slate-200">
                {[...versions].reverse().map((version) => (
                  <li key={version.id} className="relative mb-3 pl-4">
                    <span
                      className={cn(
                        "absolute -left-[5px] top-1.5 h-2.5 w-2.5 rounded-full border-2 border-white",
                        version.id === versionDetail?.version.id ? "bg-brand-500" : "bg-slate-300",
                      )}
                    />
                    <button
                      className="block w-full rounded-lg px-2 py-1 text-left transition hover:bg-slate-50"
                      onClick={() => void api.getVersion(version.id).then(setVersionDetail)}
                    >
                      <span className="text-[11px] font-bold text-brand-600">v{version.seq}</span>
                      <span className="mt-0.5 block text-xs text-slate-600">{version.summary}</span>
                      <span className="text-[10px] text-slate-300">
                        {relativeTime(version.created_at)}
                      </span>
                    </button>
                    <button
                      className="mt-1 ml-2 flex items-center gap-1 text-[10px] text-slate-400 hover:text-brand-600"
                      onClick={() => forkFromVersion(version)}
                      title="从该版本新建分支"
                    >
                      <Undo2 className="h-3 w-3" />
                      从这里分叉
                    </button>
                  </li>
                ))}
              </ol>
            )}
          </div>
        </Card>

        {/* ---------------- 对话 ---------------- */}
        <Card className="flex flex-col overflow-hidden">
          <div className="flex items-center justify-between border-b border-slate-100 bg-slate-50 px-3.5 py-2.5">
            <span className="text-xs font-bold text-slate-500">对话</span>
            {activeBranch && (
              <Badge tone={activeBranch.is_primary ? "brand" : "teal"}>{activeBranch.title}</Badge>
            )}
          </div>

          <div className="max-h-[460px] min-h-[300px] flex-1 space-y-3 overflow-y-auto p-4">
            {messages.length === 0 && (
              <div className="rounded-lg bg-slate-50 px-4 py-3 text-xs leading-relaxed text-slate-500">
                告诉我你的旅行想法即可，例如：
                <br />
                「我想去重庆玩 3 天，从北京出发，预算 2000」
                <br />
                我会先问清楚必要信息，再联网检索真实资料生成路书。
              </div>
            )}

            {messages.map((message, index) => (
              <div
                key={index}
                className={cn("flex gap-2.5", message.role === "user" && "flex-row-reverse")}
              >
                <div
                  className={cn(
                    "flex h-7 w-7 flex-none items-center justify-center rounded-lg text-xs font-bold",
                    message.role === "user"
                      ? "bg-slate-200 text-slate-600"
                      : "bg-gradient-to-br from-brand-500 to-teal-500 text-white",
                  )}
                >
                  {message.role === "user" ? "我" : "AI"}
                </div>
                <div
                  className={cn(
                    "animate-fade-up max-w-[85%] whitespace-pre-wrap rounded-xl px-3.5 py-2 text-sm",
                    message.role === "user"
                      ? "bg-brand-100 text-brand-900"
                      : "bg-slate-50 text-slate-700",
                  )}
                >
                  {message.content}
                </div>
              </div>
            ))}

            {sending && (
              <div className="flex items-center gap-2 text-xs text-brand-600">
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
                正在检索并生成路书，通常需要数秒…
              </div>
            )}
            <div ref={bottomRef} />
          </div>

          <div className="border-t border-slate-100 bg-slate-50 p-3">
            <div className="flex gap-2">
              <input
                className="flex-1 rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm outline-none transition focus:border-brand-400 focus:ring-2 focus:ring-brand-100"
                placeholder="说出来你的想法，或回复「确认」定稿…"
                value={input}
                onChange={(e) => setInput(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && !e.shiftKey) {
                    e.preventDefault();
                    void send();
                  }
                }}
                disabled={sending}
              />
              <Button variant="primary" loading={sending} onClick={send}>
                <Send className="h-3.5 w-3.5" />
                发送
              </Button>
            </div>
          </div>
        </Card>

        {/* ---------------- 路书 ---------------- */}
        <Card className="flex flex-col overflow-hidden">
          <div className="flex items-center justify-between border-b border-slate-100 bg-slate-50 px-3.5 py-2.5">
            <span className="text-xs font-bold text-slate-500">
              路书预览
              {versionDetail && (
                <span className="ml-2 font-normal text-slate-400">
                  v{versionDetail.version.seq} · {versionDetail.markdown.length} 字
                </span>
              )}
            </span>
            <select
              className="rounded-md border border-slate-200 bg-white px-2 py-1 text-[11px]"
              value={level}
              onChange={(e) => setLevel(e.target.value as DetailLevel)}
            >
              {(Object.keys(DETAIL_LEVEL_LABELS) as DetailLevel[]).map((key) => (
                <option key={key} value={key}>
                  {DETAIL_LEVEL_LABELS[key]}档
                </option>
              ))}
            </select>
          </div>

          <div className="max-h-[460px] min-h-[300px] flex-1 overflow-y-auto p-4">
            {versionDetail?.markdown ? (
              <div className="prose-report">
                <ReactMarkdown>{versionDetail.markdown}</ReactMarkdown>
              </div>
            ) : (
              <div className="flex h-full flex-col items-center justify-center gap-2 text-center text-xs text-slate-400">
                <Sparkles className="h-7 w-7 text-slate-300" />
                路书生成后显示在这里
              </div>
            )}
          </div>

          <div className="flex flex-wrap items-center gap-2 border-t border-slate-100 bg-slate-50 p-3">
            {versionDetail ? (
              <>
                <a href={api.exportUrl(versionDetail.version.id, "md")}>
                  <Button size="sm">
                    <Download className="h-3.5 w-3.5" />
                    导出 Markdown
                  </Button>
                </a>
                <a href={api.exportUrl(versionDetail.version.id, "html")}>
                  <Button size="sm">
                    <Download className="h-3.5 w-3.5" />
                    导出 HTML
                  </Button>
                </a>
                {versionDetail.citations.length > 0 && (
                  <span className="text-[11px] text-slate-400">
                    {versionDetail.citations.length} 个来源
                  </span>
                )}
              </>
            ) : (
              <span className="text-[11px] text-slate-400">暂无内容可导出</span>
            )}
          </div>
        </Card>
      </div>

      {error && (
        <div className="mt-4">
          <ErrorBanner message={error} onRetry={load} />
        </div>
      )}
    </div>
  );
}
