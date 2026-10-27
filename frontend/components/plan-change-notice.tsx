"use client";

import {
  useEffect,
  useMemo,
  useRef,
  useState,
  useSyncExternalStore,
} from "react";
import { Undo2 } from "lucide-react";
import { Button } from "@/components/ui";
import { api, ApiError } from "@/lib/api";
import type { PlanChange } from "@/lib/types";
import { scrollToElement, scrollToLatest } from "@/lib/utils";

interface UndoAttempt {
  version_id: string;
  request_id: string;
  change_version_id: string;
}
const draftKey = (branch: string) => `travelflow:plan-undo:${branch}`;
const memoryDrafts = new Map<string, string>();
const draftEvent = "travelflow:plan-undo-draft";

function readDraft(branch: string): string {
  if (memoryDrafts.has(branch)) return memoryDrafts.get(branch)!;
  try {
    return (
      sessionStorage.getItem(draftKey(branch)) ?? memoryDrafts.get(branch) ?? ""
    );
  } catch {
    return memoryDrafts.get(branch) ?? "";
  }
}

function subscribeDraft(listener: () => void) {
  const storageChanged = () => {
    memoryDrafts.clear();
    listener();
  };
  window.addEventListener(draftEvent, listener);
  window.addEventListener("storage", storageChanged);
  return () => {
    window.removeEventListener(draftEvent, listener);
    window.removeEventListener("storage", storageChanged);
  };
}

function parseAttempt(raw: string): UndoAttempt | null {
  try {
    const data = JSON.parse(raw);
    return data &&
      typeof data.version_id === "string" &&
      typeof data.change_version_id === "string" &&
      typeof data.request_id === "string" &&
      /^[a-zA-Z0-9_-]{8,80}$/.test(data.request_id)
      ? data
      : null;
  } catch {
    return null;
  }
}

function saveAttempt(branch: string, attempt: UndoAttempt | null) {
  const raw = attempt ? JSON.stringify(attempt) : "";
  memoryDrafts.set(branch, raw);
  try {
    if (attempt) sessionStorage.setItem(draftKey(branch), raw);
    else sessionStorage.removeItem(draftKey(branch));
  } catch {
    /* 禁用存储时，当前页面仍保留原请求号。 */
  }
  window.dispatchEvent(new Event(draftEvent));
}

export function PlanChangeNotice({
  branchId,
  versionId,
  visible,
  disabled,
  onBusy,
  onRefresh,
}: {
  branchId: string;
  versionId: string;
  visible: boolean;
  disabled: boolean;
  onBusy: (busy: boolean) => void;
  onRefresh: () => Promise<void>;
}) {
  const [offer, setOffer] = useState<PlanChange | null>(null);
  const rawDraft = useSyncExternalStore(
    subscribeDraft,
    () => readDraft(branchId),
    () => "",
  );
  const attempt = useMemo(() => parseAttempt(rawDraft), [rawDraft]);
  const [saving, setSaving] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [loadError, setLoadError] = useState(false);
  const [reload, setReload] = useState(0);
  const note = useRef<HTMLElement>(null);

  useEffect(() => {
    let cancelled = false;
    void api
      .planningChange(versionId)
      .then((result) => {
        if (!cancelled) {
          setOffer(result);
          setLoadError(false);
        }
      })
      .catch(() => {
        if (!cancelled) {
          setOffer(null);
          setLoadError(true);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [versionId, reload]);

  useEffect(() => {
    if (visible && !notice && (offer || attempt))
      scrollToLatest(
        note.current?.closest<HTMLElement>(".journey-messages") ?? null,
      );
  }, [offer, notice, attempt, visible]);

  useEffect(() => {
    if (notice && visible) {
      // 撤销可能改变手机上方路线的高度；这是主动操作的结果，不改变初次打开的 M1 顺序。
      if (window.matchMedia("(max-width: 900px)").matches) {
        scrollToLatest(
          note.current?.closest<HTMLElement>(".journey-messages") ?? null,
        );
        scrollToElement(note.current?.closest(".journey-chat-column") ?? null, {
          block: "start",
        });
      } else scrollToElement(note.current, { block: "nearest" });
      note.current?.focus({ preventScroll: true });
    }
  }, [notice, visible]);

  async function undo() {
    if (
      saving ||
      refreshing ||
      disabled ||
      (!attempt &&
        (!offer ||
          offer.blocked_reason ||
          offer.current_version_id !== versionId))
    )
      return;
    const request = attempt ?? {
      version_id: versionId,
      change_version_id: offer!.version_id,
      request_id: crypto.randomUUID(),
    };
    saveAttempt(branchId, request);
    setSaving(true);
    onBusy(true);
    setError("");
    setNotice("");
    try {
      await api.undoPlan(request.version_id, request);
      saveAttempt(branchId, null);
      setOffer(null);
      setNotice(
        "已撤销规划修改，实际消费与旅行记录保留。可继续告诉我新的想法。",
      );
      // 重试可能返回此前的回执；始终重新载入当前最新版本，不把旧回执当作最新行程。
      await onRefresh();
    } catch (err) {
      setError(
        err instanceof Error ? err.message : "撤销结果尚未确认，请重试原操作。",
      );
      if (err instanceof ApiError && [400, 409].includes(err.status)) {
        saveAttempt(branchId, null);
        setOffer(null);
      }
    } finally {
      setSaving(false);
      onBusy(false);
    }
  }

  if (!visible || (!offer && !attempt && !error && !notice && !loadError))
    return null;
  return (
    <section
      tabIndex={-1}
      ref={note}
      aria-label="规划修改记录"
      className="journey-change-note"
    >
      {offer && !attempt && (
        <>
          <p className="font-medium">{offer.summary}</p>
          <ul>
            {offer.changes.map((change) => (
              <li key={change}>{change}</li>
            ))}
          </ul>
        </>
      )}
      {offer?.blocked_reason && !attempt && <p>{offer.blocked_reason}</p>}
      {loadError && <p>暂时无法读取修改记录，已保存的规划不受影响。</p>}
      {error && <p role="alert">{error}</p>}
      {attempt && !saving && !error && (
        <p role="alert">上次撤销的结果尚未确认，可重试同一次操作核对结果。</p>
      )}
      {notice && <p role="status">{notice}</p>}
      <div className="mt-2 flex flex-wrap gap-2">
        {(offer || attempt) && (
          <Button
            size="sm"
            variant="default"
            loading={saving}
            loadingLabel="正在撤销并同步…"
            disabled={
              disabled ||
              refreshing ||
              (!attempt &&
                (!!offer?.blocked_reason ||
                  offer?.current_version_id !== versionId))
            }
            onClick={() => void undo()}
          >
            <Undo2 aria-hidden="true" size={14} />
            {attempt ? "重试撤销" : "撤销这次规划修改"}
          </Button>
        )}
        {(error || loadError || attempt) && (
          <Button
            size="sm"
            variant="ghost"
            disabled={disabled || saving}
            loading={refreshing}
            loadingLabel="正在刷新…"
            onClick={async () => {
              setRefreshing(true);
              try {
                await onRefresh();
                if (!attempt) setError("");
                setReload((value) => value + 1);
              } finally {
                setRefreshing(false);
              }
            }}
          >
            刷新当前安排
          </Button>
        )}
      </div>
    </section>
  );
}
