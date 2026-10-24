"use client";

import type { NodeArtifact } from "@/lib/studio-types";
import { download } from "@/lib/studio-graph";

const categories: Record<string, string> = {
  meal: "饮食",
  lodging: "住宿",
  transport: "交通",
  ticket: "门票",
  shopping: "购物",
  other: "其他",
};
const money = (value: number | null) =>
  value === null ? "待核实" : `¥${(value / 100).toFixed(2)}`;
const routeLabels = {
  unchecked: "尚未校验",
  unconfigured: "地图未配置 · 保留建议安排",
  partial: "部分待核实",
  ready: "地图校验完成",
};

export default function StudioData({ artifact }: { artifact: NodeArtifact }) {
  const { plan, budget } = artifact;
  if (!plan && !budget) return null;
  return (
    <section
      aria-label="结构化节点数据"
      className="mt-3 space-y-3 rounded-xl bg-slate-50 p-3 text-xs leading-6"
    >
      <h4 className="font-semibold text-slate-800">结构化数据</h4>
      {plan && (
        <>
          <p>
            {plan.days.length}天 ·{" "}
            {plan.days.reduce((sum, day) => sum + day.activities.length, 0)}
            个活动 · {routeLabels[plan.route_status]}
          </p>
          {plan.days.map((day) => (
            <details key={day.day} className="rounded-lg bg-white p-2">
              <summary className="cursor-pointer font-medium">
                第{day.day}天 · {day.title}
              </summary>
              <ol className="mt-2 space-y-3">
                {day.activities.map((a) => (
                  <li key={a.id} className="border-l-2 border-blue-200 pl-2">
                    <p>
                      {a.start_day_offset ? `+${a.start_day_offset}天 ` : ""}
                      {a.start_time || "时间待定"} · {a.place_name || a.title}
                    </p>
                    <p className="text-slate-500">
                      {a.city} · 停留{a.duration_minutes ?? "未知"}分钟 ·{" "}
                      {a.place_status === "matched"
                        ? "地图已定位"
                        : a.place_status === "ambiguous"
                          ? "定位有歧义"
                          : a.place_status === "pending"
                            ? "未查询地图"
                            : "定位待核实"}
                    </p>
                    {a.place && (
                      <p className="text-slate-500">{a.place.address}</p>
                    )}
                    {a.match_reason && (
                      <p className="text-amber-800">{a.match_reason}</p>
                    )}
                  </li>
                ))}
              </ol>
              {!!day.legs.length && (
                <ul className="mt-3 space-y-2 border-t border-slate-100 pt-2 text-slate-600">
                  {day.legs.map((leg) => (
                    <li key={`${leg.from_id}-${leg.to_id}`}>
                      {
                        day.activities.find((a) => a.id === leg.from_id)
                          ?.place_name
                      }{" "}
                      →{" "}
                      {
                        day.activities.find((a) => a.id === leg.to_id)
                          ?.place_name
                      }
                      <br />
                      {leg.duration_seconds === null
                        ? "耗时未知"
                        : `约${Math.ceil(leg.duration_seconds / 60)}分钟`}{" "}
                      · {leg.status === "ok" ? "地图结果" : "建议／待核实"}
                      {leg.message ? ` · ${leg.message}` : ""}
                    </li>
                  ))}
                </ul>
              )}
              {!!day.warnings.length && (
                <ul className="mt-2 text-amber-800">
                  {day.warnings.map((w, i) => (
                    <li key={i}>{w}</li>
                  ))}
                </ul>
              )}
            </details>
          ))}
          {plan.warnings.map((w, i) => (
            <p key={i} className="text-amber-800">
              {w}
            </p>
          ))}
        </>
      )}
      {budget && (
        <>
          <p className="font-medium">
            已列预估小计 {money(budget.overview.planned_cents)}
          </p>
          <p>
            未知费用 {budget.overview.unknown_count}项 · 全团预算{" "}
            {money(budget.overview.budget_cents)}
          </p>
          {budget.overview.budget_cents !== null && (
            <p
              className={
                budget.overview.planned_cents > budget.overview.budget_cents
                  ? "text-red-700"
                  : "text-teal-800"
              }
            >
              相对预算
              {budget.overview.planned_cents > budget.overview.budget_cents
                ? "超出"
                : "尚余"}{" "}
              {money(
                Math.abs(
                  budget.overview.budget_cents - budget.overview.planned_cents,
                ),
              )}
              （仅比较已列小计）
            </p>
          )}
          <dl className="grid grid-cols-2 gap-x-3">
            {Object.entries(budget.categories).map(([category, amount]) => (
              <div key={category} className="flex justify-between gap-2">
                <dt>{categories[category] || category}</dt>
                <dd>{money(amount)}</dd>
              </div>
            ))}
          </dl>
          <details>
            <summary className="cursor-pointer">
              费用明细 · {budget.overview.rows.length}项
            </summary>
            <ul className="mt-2 space-y-2">
              {budget.overview.rows.map((row) => (
                <li key={row.id}>
                  {row.day ? `第${row.day}天 · ` : ""}
                  {row.title} · {money(row.planned_cents)}
                </li>
              ))}
            </ul>
          </details>
          {budget.warnings.map((w, i) => (
            <p key={i} className="text-amber-800">
              {w}
            </p>
          ))}
        </>
      )}
      <button
        className="text-teal-700 underline"
        onClick={() =>
          download(
            "节点结构化数据.json",
            JSON.stringify(
              { plan, budget, sources: artifact.sources },
              null,
              2,
            ),
            "application/json",
          )
        }
      >
        下载结构化数据 JSON
      </button>
    </section>
  );
}
