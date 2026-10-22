"use client";
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
    <div className="mt-2 rounded-lg border border-sky-100 bg-sky-50/50 p-3">
      <p className="text-[11px] text-slate-600">
        {from} → {to}
        {date ? ` · ${date}` : " · 日期待定"}
      </p>
      <div className="mt-2 flex flex-wrap gap-3">
        <a
          href="https://kyfw.12306.cn/otn/leftTicket/init"
          target="_blank"
          rel="noopener noreferrer"
          className="text-xs font-medium text-teal-700"
        >
          去12306查询购票 ↗
        </a>
        <button
          type="button"
          className="text-xs text-slate-500"
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
        </button>
      </div>
      <p className="mt-1 text-[10px] text-slate-400">
        将在官方页面查询与购票；车站、日期及票价请核对，本站未查询实时余票。
      </p>
      {error && (
        <p className="mt-1 whitespace-pre-line text-[11px] text-slate-500">
          {error}
        </p>
      )}
    </div>
  );
}
