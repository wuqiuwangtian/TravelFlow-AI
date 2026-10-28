"use client";
import { ExternalLink } from "lucide-react";
import { Button } from "@/components/ui";
import { useState } from "react";
export function RailTicketLink({
  from,
  to,
  date,
}: {
  from: string;
  to: string;
  date: string | null;
}) {
  const [copied, setCopied] = useState(false),
    [error, setError] = useState("");
  const info = `出发站：${from}\n到达站：${to}\n乘车日期：${date || "尚未确定"}`;
  return (
    <div className="mt-2 rounded-lg border border-accent-line bg-accent-soft p-3">
      <p className="text-[11px] text-muted">
        {from} → {to}
        {date ? ` · ${date}` : " · 日期待定"}
      </p>
      <div className="mt-2 flex flex-wrap gap-3">
        <a
          href="https://kyfw.12306.cn/otn/leftTicket/init"
          target="_blank"
          rel="noopener noreferrer"
          className="tf-action-link tf-page-link text-xs font-medium text-accent"
        >
          去12306查询购票{" "}
          <ExternalLink aria-hidden="true" className="h-3.5 w-3.5" />
        </a>
        <Button
          type="button"
          className="text-xs text-muted"
          onClick={() => {
            if (!navigator.clipboard?.writeText) {
              setError(info);
              return;
            }
            void navigator.clipboard
              .writeText(info)
              .then(() => setCopied(true))
              .catch(() => setError(info));
          }}
        >
          {copied ? "已复制乘车信息" : "复制乘车信息"}
        </Button>
      </div>
      <p className="mt-1 text-[10px] text-muted">
        将在官方页面查询与购票；车站、日期及票价请核对，本站未查询实时余票。
      </p>
      {error && (
        <p className="mt-1 whitespace-pre-line text-[11px] text-muted">
          {error}
        </p>
      )}
    </div>
  );
}
