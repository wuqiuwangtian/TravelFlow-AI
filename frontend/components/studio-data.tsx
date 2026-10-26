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
  const { plan, budget, candidates } = artifact;
  if (!plan && !budget && !candidates) return null;
  return (
    <section
      aria-label="结构化节点数据"
      className="mt-3 space-y-3 rounded-xl bg-slate-50 p-3 text-xs leading-6"
    >
      <h4 className="font-semibold text-slate-800">结构化数据</h4>
      {candidates && (
        <div aria-label="候选景点池数据">
          <p className="font-medium">
            {candidates.requirements.destination} · {candidates.places.length}
            处候选 · 尚未分日
          </p>
          <ol className="mt-3 space-y-3">
            {candidates.places.map((p) => (
              <li
                key={p.id}
                className="rounded-lg border border-purple-100 bg-white p-3"
              >
                <p className="font-medium">
                  {p.place_name} · {p.city}
                </p>
                <p>{p.reason}</p>
                <p className="text-slate-500">
                  停留{p.duration_minutes ?? "待定"}分钟 ·{" "}
                  {p.cost === null
                    ? "费用待核实"
                    : `¥${p.cost.toFixed(2)}/${p.cost_basis === "total" ? "全团" : p.cost_basis === "per_person" ? "人均" : "口径未知"}`}{" "}
                  · 来源
                  {p.source_indices.length
                    ? p.source_indices.map((i) => `[${i}]`).join(" ")
                    : "待核实"}
                </p>
              </li>
            ))}
          </ol>
          {candidates.warnings.map((w, i) => (
            <p key={i} className="mt-2 text-amber-800">
              {w}
            </p>
          ))}
        </div>
      )}
      {plan && (
        <>
          <p>
            {plan.days.length}天 ·{" "}
            {plan.days.reduce((sum, day) => sum + day.activities.length, 0)}
            个活动 · {routeLabels[plan.route_status]}
          </p>
          {plan.allocation && (
            <details
              open
              aria-label="候选分日记录"
              className="rounded-lg border border-rose-100 bg-white p-2"
            >
              <summary className="cursor-pointer font-medium">
                候选{plan.allocation.pool_count}处 · 本次安排
                {plan.allocation.selected_count}处
              </summary>
              <p className="mt-2 text-slate-500">
                每天最多{plan.allocation.max_per_day}处／
                {plan.allocation.minutes_per_day}分钟；段间缓冲
                {plan.allocation.buffer_minutes}分钟，非核实交通。
              </p>
              <ul className="mt-2 space-y-3">
                {plan.allocation.decisions.map((d) => (
                  <li
                    key={d.candidate_id}
                    className={
                      d.day == null ? "text-amber-800" : "text-slate-700"
                    }
                  >
                    <p>
                      {d.place_name} ·{" "}
                      {d.day == null ? "未安排" : `第${d.day}天`}
                    </p>
                    <p>{d.reason}</p>
                  </li>
                ))}
              </ul>
            </details>
          )}
          {plan.selection && (
            <details
              aria-label="景点筛选记录"
              open
              className="rounded-lg border border-pink-100 bg-white p-2"
            >
              <summary className="cursor-pointer font-medium">
                {plan.selection.node_title} · {plan.selection.before_count} →{" "}
                {plan.selection.after_count}个活动
              </summary>
              {!plan.selection.decisions.length && (
                <p className="mt-2 text-slate-500">
                  全部沿用上游，没有移除或改动。
                </p>
              )}
              <ul className="mt-2 space-y-2">
                {plan.selection.decisions.map((d, i) => (
                  <li
                    key={`${d.activity_id}-${i}`}
                    className={
                      d.action === "removed"
                        ? "text-amber-800"
                        : "text-teal-800"
                    }
                  >
                    第{d.day}天 ·{" "}
                    {d.action === "removed"
                      ? "移除"
                      : d.action === "duration"
                        ? "调整停留"
                        : "保留"}{" "}
                    · {d.title}
                    <br />
                    {d.reason}
                  </li>
                ))}
              </ul>
            </details>
          )}
          {!!plan.optimization?.length && (
            <details
              aria-label="路线顺序核对"
              open
              className="rounded-lg border border-blue-100 bg-white p-2"
            >
              <summary className="cursor-pointer font-medium">
                路线顺序核对
              </summary>
              <ul className="mt-2 space-y-3">
                {plan.optimization.map((order) => (
                  <li key={order.day}>
                    <p className="font-medium">
                      第{order.day}天 ·{" "}
                      {order.changed ? "已调整顺序" : "沿用上游"}
                    </p>
                    <p className="text-slate-500">{order.reason}</p>
                    <p>原：{order.before.join(" → ")}</p>
                    <p>现：{order.after.join(" → ")}</p>
                    {(order.before_travel_minutes != null ||
                      order.after_travel_minutes != null) && (
                      <p className="text-slate-600">
                        地图交通合计：{order.before_travel_minutes ?? "待核实"}{" "}
                        → {order.after_travel_minutes ?? "待核实"}分钟
                      </p>
                    )}
                    {order.before_meters !== null &&
                      order.after_meters !== null && (
                        <p className="text-teal-800">
                          地点间直线合计{" "}
                          {(order.before_meters / 1000).toFixed(1)} →{" "}
                          {(order.after_meters / 1000).toFixed(1)}
                          公里；非实际路程或耗时。
                        </p>
                      )}
                  </li>
                ))}
              </ul>
            </details>
          )}
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
          {!!budget.supplements?.length && (
            <details
              open
              aria-label="分类预算补齐"
              className="rounded-lg border border-orange-100 bg-white p-2"
            >
              <summary className="cursor-pointer font-medium">
                按设置补齐 · {budget.supplements.length}项
              </summary>
              <ul className="mt-2 space-y-3">
                {budget.supplements.map((entry) => (
                  <li key={entry.item.id}>
                    <p>
                      {entry.item.title} · {money(entry.item.planned_cents)}
                    </p>
                    <p className="text-slate-500">{entry.explanation}</p>
                  </li>
                ))}
              </ul>
            </details>
          )}
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
              { plan, budget, candidates, sources: artifact.sources },
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
