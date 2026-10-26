"use client";

import { useRef, useState } from "react";
import { download } from "@/lib/studio-graph";
import { ActionFeedback, Button } from "./ui";

export default function DocumentActions({
  text,
  markdown,
}: {
  text: string;
  markdown: string;
}) {
  const [copying, setCopying] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [manualCopy, setManualCopy] = useState(false);
  const lock = useRef(false);
  async function copy() {
    if (lock.current) return;
    lock.current = true;
    setCopying(true);
    setError("");
    setMessage("");
    try {
      if (!navigator.clipboard?.writeText)
        throw new Error("Clipboard unavailable");
      await navigator.clipboard.writeText(text);
      setManualCopy(false);
      setMessage("节点正文已复制。");
    } catch {
      setManualCopy(true);
      setError("复制未成功，可在下方选择正文手动复制，或下载 Markdown。");
    } finally {
      lock.current = false;
      setCopying(false);
    }
  }
  return (
    <section aria-label="节点正文操作" className="mt-4 text-xs">
      <div className="flex flex-wrap gap-2">
        <Button
          size="sm"
          onClick={() => {
            setError("");
            setMessage("");
            try {
              download("旅行工程成稿.md", markdown);
              setMessage("已发起 Markdown 下载，请在浏览器下载列表中核对。");
            } catch {
              setError("下载未能发起，可复制正文后自行保存。");
            }
          }}
        >
          下载此节点 Markdown
        </Button>
        <Button
          size="sm"
          loading={copying}
          loadingLabel="正在复制…"
          onClick={() => void copy()}
        >
          复制正文
        </Button>
      </div>
      <ActionFeedback error={error} message={message} />
      {manualCopy && (
        <label className="mt-3 block text-slate-600">
          手动复制正文
          <textarea
            aria-label="可手动复制的节点正文"
            readOnly
            value={text}
            rows={6}
            className="mt-1 w-full rounded-lg border border-slate-300 p-3"
            onFocus={(event) => event.currentTarget.select()}
          />
        </label>
      )}
    </section>
  );
}
