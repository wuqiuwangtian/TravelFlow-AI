"use client";

import { useState } from "react";
import type { NodeArtifact } from "@/lib/studio-types";

export default function StudioMergeData({
  report,
}: {
  report: NonNullable<NonNullable<NodeArtifact["candidates"]>["merge"]>;
}) {
  const [all, setAll] = useState(false);
  const labels = {
    kept: "保留",
    combined: "合并",
    separate: "分别保留",
    omitted: "未入池",
  };
  return (
    <section
      aria-label="候选合并记录"
      className="mt-3 space-y-3 rounded-lg border border-lime-200 bg-white p-3"
    >
      <h5 className="font-medium">
        {report.input_pools}路候选 · {report.before_count} →{" "}
        {report.after_count}处
      </h5>
      <p className="text-slate-500">
        去重{report.duplicate_count}项 · 因上限未入池{report.omitted_count}组
      </p>
      {(all ? report.decisions : report.decisions.slice(0, 12)).map((d) => (
        <details
          key={d.candidate_id}
          className="rounded border border-slate-100 p-2"
        >
          <summary className="cursor-pointer">
            {d.place_name} · {labels[d.action]}
            {d.conflicts.length ? ` · ${d.conflicts.join("、")}` : ""}
          </summary>
          <p className="mt-2 text-slate-600">{d.reason}</p>
          <ul className="mt-2 space-y-3">
            {d.variants.map((v, i) => (
              <li
                key={`${v.source_id}-${i}`}
                className="border-l-2 border-lime-200 pl-2"
              >
                <p className="font-medium">来源：{v.source_title}</p>
                <p>
                  {v.candidate.place_name} · {v.candidate.city}
                </p>
                <p>
                  停留{v.candidate.duration_minutes ?? "待定"}分钟 ·{" "}
                  {v.candidate.cost === null
                    ? "费用待核实"
                    : `¥${v.candidate.cost.toFixed(2)}/${v.candidate.cost_basis === "total" ? "全团" : v.candidate.cost_basis === "per_person" ? "人均" : "口径未知"}`}
                </p>
                <p>{v.candidate.reason}</p>
                <p className="text-slate-500">
                  引用：
                  {v.candidate.source_indices.map((n) => `[${n}]`).join(" ") ||
                    "待核实"}
                </p>
                {v.candidate.location && (
                  <p className="text-slate-500">
                    原定位：
                    {v.candidate.location.place?.name ??
                      v.candidate.location.reason}
                  </p>
                )}
              </li>
            ))}
          </ul>
        </details>
      ))}
      {report.decisions.length > 12 && (
        <button
          type="button"
          onClick={() => setAll(!all)}
          aria-expanded={all}
          className="text-teal-700 underline"
        >
          {all ? "收起其余记录" : `显示全部${report.decisions.length}组记录`}
        </button>
      )}
    </section>
  );
}
