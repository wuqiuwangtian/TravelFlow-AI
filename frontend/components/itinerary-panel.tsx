"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import {
  ArrowDown,
  ArrowRight,
  Bus,
  Car,
  Clock,
  Footprints,
  Hotel,
  MapPin,
  MessageCircle,
  Plane,
  RefreshCw,
  Route,
  SlidersHorizontal,
  Train,
  Utensils,
} from "lucide-react";
import { Button, Card, ErrorBanner } from "@/components/ui";
import { BudgetPanel } from "@/components/budget-panel";
import { PlacePhoto } from "@/components/place-photo";
import { PlaceNavigation } from "@/components/place-navigation";
import { RailTicketLink } from "@/components/rail-ticket-link";
import { TravelLedger } from "@/components/travel-ledger";
import { TravelRecap } from "@/components/travel-recap";
import { OnTripControls } from "@/components/ontrip-controls";
import { timeLabel, dayLabel } from "@/lib/travel-time";
import { api, ApiError } from "@/lib/api";
import { scrollToElement } from "@/lib/utils";
import { initialJourneyDay, nextJourneyStop } from "@/lib/journey-view";
import type {
  Activity,
  ActivityProgress,
  DayPlan,
  Place,
  ScheduledActivity,
  TravelMode,
  VersionDetail,
} from "@/lib/types";

const modes: Record<TravelMode, string> = {
  auto: "智能衔接",
  walking: "步行",
  transit: "公共交通",
  driving: "打车 / 驾车",
  rail: "铁路",
  flight: "航班",
  transfer: "接驳",
};
const inputClass =
  "w-full rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm";
const progressLabels: Record<ActivityProgress, string> = {
  not_started: "未开始",
  in_progress: "进行中",
  completed: "已完成",
  skipped: "已跳过",
};

function travelDate(
  departure: string | null | undefined,
  day: number,
): string | null {
  if (!departure) return null;
  const date = new Date(departure + "T00:00:00Z");
  if (Number.isNaN(date.valueOf())) return null;
  date.setUTCDate(date.getUTCDate() + day - 1);
  return date.toISOString().slice(0, 10);
}

function ModeIcon({ mode }: { mode: TravelMode }) {
  const Icon = {
    auto: Route,
    walking: Footprints,
    transit: Bus,
    driving: Car,
    rail: Train,
    flight: Plane,
    transfer: Bus,
  }[mode];
  return <Icon className="h-3.5 w-3.5 shrink-0" />;
}
function StopIcon({ kind }: { kind: Activity["kind"] }) {
  const Icon =
    kind === "hotel"
      ? Hotel
      : kind === "meal"
        ? Utensils
        : kind === "station"
          ? Train
          : MapPin;
  return <Icon className="h-4 w-4" />;
}
function legText(day: DayPlan, index: number): string {
  const leg = day.legs[index];
  if (!leg) return "继续行程";
  return `${modes[leg.mode]}${leg.duration_seconds !== null ? ` · 约 ${Math.ceil(leg.duration_seconds / 60)} 分钟` : " · 按出行安排"}`;
}

export function ItineraryPanel({
  detail,
  editable,
  disabled,
  onUpdate,
  onBusy,
  onFork,
  onAsk,
}: {
  detail: VersionDetail;
  editable: boolean;
  disabled: boolean;
  onUpdate: (detail: VersionDetail) => Promise<void>;
  onBusy: (busy: boolean) => void;
  onFork: () => void;
  onAsk: (message: string) => void;
}) {
  const [dayNumber, setDayNumber] = useState(initialJourneyDay(detail));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [needsModelSettings, setNeedsModelSettings] = useState(false);
  const [editing, setEditing] = useState<string | null>(null);
  const [advanced, setAdvanced] = useState(false);
  const [selected, setSelected] = useState<string | null>(null);
  const errorRef = useRef<HTMLDivElement>(null);
  const itinerary = detail.itinerary;
  const day =
    itinerary?.days.find((d) => d.day === dayNumber) ?? itinerary?.days[0];
  const active =
    day?.activities.find((a) => a.id === selected) ?? nextJourneyStop(day);
  const activeIndex =
    day?.activities.findIndex((a) => a.id === active?.id) ?? -1;
  const previousStop =
    activeIndex > 0 ? day?.activities[activeIndex - 1] : undefined;
  const durationKnown = day?.legs.every((leg) => leg.duration_seconds !== null);

  function selectStop(id: string) {
    setSelected(id);
    requestAnimationFrame(() =>
      scrollToElement(document.getElementById("journey-current-stop"), {
        block: "nearest",
      }),
    );
  }

  async function mutate(work: () => Promise<VersionDetail>) {
    setBusy(true);
    onBusy(true);
    setError(null);
    setNeedsModelSettings(false);
    try {
      const result = await work();
      setEditing(null);
      await onUpdate(result);
      return true;
    } catch (err) {
      setError(
        err instanceof Error ? err.message : "更新失败，当前行程已保留。",
      );
      setNeedsModelSettings(
        err instanceof ApiError && err.code === "LLM_AUTH_FAILED",
      );
      requestAnimationFrame(() =>
        scrollToElement(errorRef.current, {
          block: "center",
        }),
      );
      return false;
    } finally {
      setBusy(false);
      onBusy(false);
    }
  }

  return (
    <Card
      id="journey-route"
      tabIndex={-1}
      aria-label="旅行路线与执行"
      className="journey-itinerary min-w-0 overflow-hidden"
    >
      <div className="journey-itinerary-heading">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <p className="mb-1 text-[10px] font-semibold tracking-[0.16em] text-teal-600">
              每日路线与旅中记录
            </p>
            <h2 className="flex items-center gap-2 text-lg font-bold text-slate-800">
              <Route className="h-5 w-5 text-teal-600" />
              你的旅行线路
            </h2>
            <p className="mt-2 text-xs leading-relaxed text-slate-500">
              地点、交通与时间已经串在一起。想换一种玩法，直接告诉 AI。
            </p>
          </div>
          <div className="flex items-center gap-2">
            {!editable && (
              <Button size="sm" disabled={disabled} onClick={onFork}>
                从此版本分叉后编辑
              </Button>
            )}
            <button
              type="button"
              onClick={() => setAdvanced(!advanced)}
              aria-expanded={advanced}
              className="flex items-center gap-1 rounded-lg border border-slate-200 bg-white/80 px-2.5 py-1.5 text-[11px] text-slate-500"
            >
              <SlidersHorizontal className="h-3 w-3" />
              {advanced ? "收起细节" : "更多细节"}
            </button>
          </div>
        </div>
      </div>
      <div className="journey-itinerary-content">
        {error && (
          <div ref={errorRef} className="mb-4">
            <ErrorBanner message={error} />
            {needsModelSettings && (
              <Link
                href="/settings#llm"
                className="mt-2 inline-block text-sm text-teal-700 underline"
              >
                前往设置大模型并测试连接
              </Link>
            )}
          </div>
        )}
        {!day ? (
          <div className="rounded-xl bg-slate-50 p-5 text-sm leading-relaxed text-slate-500">
            <p>
              这份旧路书还没有连续线路。AI
              可以自动整理地点、交通和建议时间，不需要逐项填写。
            </p>
            <Button
              className="mt-3"
              loading={busy}
              disabled={!editable || disabled}
              onClick={() =>
                mutate(() => api.organiseItinerary(detail.version.id))
              }
            >
              自动整理完整线路
            </Button>
          </div>
        ) : (
          <>
            <div
              className="mb-5 flex gap-2 overflow-x-auto pb-1"
              role="group"
              aria-label="选择行程天数"
            >
              {itinerary?.days.map((item) => (
                <button
                  type="button"
                  key={item.day}
                  disabled={busy}
                  aria-pressed={day.day === item.day}
                  onClick={() => {
                    setDayNumber(item.day);
                    setEditing(null);
                    setSelected(null);
                  }}
                  className="journey-day-choice"
                >
                  第 {item.day} 天
                </button>
              ))}
            </div>
            {active && (
              <section
                id="journey-current-stop"
                className="journey-current-stop"
                aria-label="当前站点"
              >
                <p className="journey-current-label">
                  {selected
                    ? "当前查看"
                    : active.progress === "in_progress"
                      ? "正在这一站"
                      : active.progress === "completed" ||
                          active.progress === "skipped"
                        ? "今日已走完，回看此站"
                        : "接下来"}{" "}
                  ·{" "}
                  {timeLabel(
                    day.replan_context?.activity_id === active.id
                      ? day.replan_context.time
                      : active.start_time,
                    day.replan_context?.activity_id === active.id
                      ? day.replan_context.start_day_offset
                      : active.start_day_offset,
                  )}
                </p>
                <p className="journey-current-title">
                  {active.place?.name ?? active.place_name ?? active.title}
                </p>
                <p className="mt-1 text-xs leading-relaxed text-slate-500">
                  {active.place?.address || active.city}
                </p>
                {(day.replan_context ||
                  day.linked_replan ||
                  day.warnings.some((warning) =>
                    /冲突|跨日|超过.*24|跨越午夜|前段预约/.test(warning),
                  )) && (
                  <button
                    type="button"
                    className="journey-time-note"
                    onClick={() =>
                      scrollToElement(
                        document.getElementById("journey-notes"),
                        { block: "start" },
                      )
                    }
                  >
                    查看时间与调整说明
                  </button>
                )}
                {active.place && (
                  <PlaceNavigation
                    key={active.id}
                    place={active.place}
                    previousPlace={previousStop?.place}
                    previousName={
                      previousStop?.place_name || previousStop?.title
                    }
                    mode={day.legs[activeIndex - 1]?.mode ?? "transit"}
                  />
                )}
                <div className="mt-4 border-t border-slate-100 pt-3">
                  <p className="text-xs text-slate-500">
                    旅中状态：
                    {progressLabels[active.progress ?? "not_started"]}
                  </p>
                  <div className="mt-2 flex flex-wrap gap-2">
                    {(active.progress === "completed" ||
                    active.progress === "skipped"
                      ? (["not_started"] as const)
                      : (["in_progress", "completed", "skipped"] as const)
                    )
                      .filter((status) => status !== active.progress)
                      .map((status) => (
                        <button
                          type="button"
                          key={status}
                          disabled={!editable || disabled || busy}
                          className="rounded-lg border border-teal-100 px-2.5 py-1.5 text-xs text-teal-700 disabled:opacity-40"
                          onClick={async () => {
                            const ok = await mutate(() =>
                              api.updateProgress(
                                detail.version.id,
                                day.day,
                                active.id,
                                status,
                              ),
                            );
                            if (
                              ok &&
                              (status === "completed" || status === "skipped")
                            )
                              setSelected(null);
                          }}
                        >
                          {status === "not_started"
                            ? "恢复未开始"
                            : status === "in_progress"
                              ? "开始这一站"
                              : status === "completed"
                                ? "标记完成"
                                : "跳过这一站"}
                        </button>
                      ))}
                  </div>
                </div>
                <OnTripControls
                  key={`${detail.version.id}:${active.id}`}
                  day={day}
                  active={active}
                  versionId={detail.version.id}
                  disabled={!editable || disabled || busy}
                  onCommit={mutate}
                />
                {active.progress === "not_started" &&
                  !active.booking &&
                  active.time_is_suggested &&
                  !active.is_context_anchor &&
                  !["hotel", "station", "transport"].includes(active.kind) && (
                    <div className="mt-3 flex flex-wrap gap-2">
                      {itinerary?.days
                        .filter(
                          (target) => Math.abs(target.day - day.day) === 1,
                        )
                        .map((target) => (
                          <button
                            key={target.day}
                            type="button"
                            aria-label={`将${active.title}移到第${target.day}天`}
                            disabled={!editable || disabled || busy}
                            onClick={() =>
                              onAsk(
                                `把第${day.day}天第${activeIndex + 1}站「${active.title}」移到第${target.day}天`,
                              )
                            }
                            className="rounded-lg border border-teal-100 px-2.5 py-1.5 text-xs text-teal-700 disabled:opacity-40"
                          >
                            移到第{target.day}天，先看预览
                          </button>
                        ))}
                    </div>
                  )}
              </section>
            )}
            <div className="mb-5 flex flex-wrap items-start justify-between gap-3">
              <div>
                <h3 className="text-base font-bold text-slate-800">
                  {day.title || `第 ${day.day} 天的旅程`}
                </h3>
                <p className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-slate-500">
                  <span>{day.activities.length} 个停留点</span>
                  {day.travel_minutes !== null && (
                    <span>交通约 {day.travel_minutes} 分钟</span>
                  )}
                  {day.walking_meters !== null && (
                    <span>
                      站点间步行 {(day.walking_meters / 1000).toFixed(1)} 公里
                    </span>
                  )}
                  <span>
                    已列费用 ¥{day.known_cost.toFixed(0)} / 全团
                    {day.estimated_cost > 0 ? "（含估算）" : ""}
                  </span>
                </p>
              </div>
              <span className="rounded-full bg-teal-50 px-3 py-1 text-[10px] text-teal-700">
                {day.legs.length > 0 && day.legs.every((l) => l.status === "ok")
                  ? "地图路线已校验"
                  : "建议行程 · 可直接对话调整"}
              </span>
            </div>
            {advanced && (
              <RouteStrip
                day={day}
                selected={active?.id}
                onSelect={selectStop}
              />
            )}
            <section id="journey-notes" tabIndex={-1} aria-label="行程时间说明">
              {day.replan_context && (
                <div
                  role="status"
                  aria-label="途中调整说明"
                  className="mt-3 rounded-xl border border-teal-100 bg-teal-50/60 p-4 text-xs leading-6 text-teal-800"
                >
                  <p>
                    从
                    {day.activities.find(
                      (a) => a.id === day.replan_context?.activity_id,
                    )?.place_name || "所选地点"}{" "}
                    {timeLabel(
                      day.replan_context.time,
                      day.replan_context.start_day_offset,
                    )}{" "}
                    出发
                    {day.replan_context.end_time
                      ? `，${timeLabel(day.replan_context.end_time, day.replan_context.end_day_offset)}前结束`
                      : "，继续后续安排"}
                    。预约与前段记录已保留。
                  </p>
                  {day.replan_context.omitted_titles.length > 0 && (
                    <p>
                      本次精简：{day.replan_context.omitted_titles.join("、")}
                      。原安排可在版本历史中查看。
                    </p>
                  )}
                  {day.warnings
                    .filter((w) => w.includes("交通耗时尚未查到"))
                    .map((w) => (
                      <p key={w} className="text-amber-800">
                        {w}
                      </p>
                    ))}
                </div>
              )}
              {day.linked_replan && (
                <p
                  role="status"
                  aria-label="两天联动说明"
                  className="mt-3 rounded-xl bg-teal-50 px-4 py-3 text-xs leading-6 text-teal-800"
                >
                  最近联动第{day.linked_replan.first_day}—
                  {day.linked_replan.second_day}天：预留
                  {day.linked_replan.rest_minutes / 60}小时休息，第
                  {day.linked_replan.second_day}天
                  {day.linked_replan.next_departure}出发。预约与实际记录保留。
                  {!!day.linked_replan.movement_notes?.length && (
                    <span className="block">
                      跨天搬移：{day.linked_replan.movement_notes.join("；")}。
                      付款日期与凭证保留。
                    </span>
                  )}
                </p>
              )}
              {itinerary?.days.some((item) => item.day === day.day + 1) &&
                (day.replan_context ||
                  day.activities.some(
                    (activity) => activity.start_day_offset > 0,
                  )) && (
                  <button
                    type="button"
                    disabled={!editable || disabled || busy}
                    onClick={() =>
                      onAsk(
                        `联动调整第${day.day}天和第${day.day + 1}天，预留8小时休息`,
                      )
                    }
                    className="mt-3 rounded-lg border border-teal-200 px-3 py-2 text-xs text-teal-800 hover:bg-teal-50 disabled:opacity-40"
                  >
                    联动调整第{day.day}天和第{day.day + 1}天
                  </button>
                )}
              {day.warnings
                .filter((warning) =>
                  /冲突|跨日|超过.*24|跨越午夜|前段预约/.test(warning),
                )
                .map((warning, index) => (
                  <p
                    key={index}
                    role="status"
                    className="mt-3 rounded-xl bg-amber-50 px-4 py-3 text-xs text-amber-800"
                  >
                    {warning}
                  </p>
                ))}
            </section>
            <div className="journey-detail-grid">
              <div className="min-w-0">
                <p className="mb-4 flex items-center gap-1.5 text-xs font-medium text-slate-500">
                  <Clock className="h-3.5 w-3.5" />
                  一天怎么走
                  <span className="ml-1 font-normal text-slate-400">
                    建议时间
                  </span>
                </p>
                <ol aria-label="当天行程" className="relative space-y-0">
                  {day.activities.map((activity, index) => (
                    <li
                      key={activity.id}
                      className="relative grid grid-cols-[46px_minmax(0,1fr)] gap-3 pb-2 sm:grid-cols-[54px_minmax(0,1fr)]"
                    >
                      <div className="relative text-center">
                        <time className="text-xs font-semibold text-slate-600">
                          {timeLabel(
                            activity.start_time,
                            activity.start_day_offset,
                          )}
                        </time>
                        {index < day.activities.length - 1 && (
                          <span className="absolute bottom-0 left-1/2 top-8 w-px bg-teal-200" />
                        )}
                      </div>
                      <div className="min-w-0">
                        <div
                          className="journey-activity-card"
                          data-activity-card
                        >
                          <button
                            type="button"
                            onClick={() => selectStop(activity.id)}
                            aria-pressed={active?.id === activity.id}
                            className="journey-stop-select"
                          >
                            <div className="flex items-start gap-2.5">
                              <span className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-lg bg-white text-teal-700">
                                <StopIcon kind={activity.kind} />
                              </span>
                              <div className="min-w-0 flex-1">
                                <p className="text-sm font-semibold leading-relaxed text-slate-800">
                                  {activity.title}
                                </p>
                                <p className="mt-1 text-xs leading-relaxed text-slate-500">
                                  {activity.place?.name ?? activity.place_name}
                                  {activity.duration_minutes
                                    ? ` · 停留约 ${activity.duration_minutes} 分钟`
                                    : ""}
                                </p>
                                {activity.cost !== null &&
                                  !activity.is_context_anchor && (
                                    <p className="mt-1.5 text-[11px] text-slate-500">
                                      {["estimate", "manual"].includes(
                                        activity.cost_source,
                                      )
                                        ? "预计 "
                                        : ""}
                                      ¥{activity.cost}
                                      {activity.cost_basis === "per_person"
                                        ? " / 人"
                                        : activity.cost_basis === "total"
                                          ? " / 全团"
                                          : "（口径待核实）"}
                                    </p>
                                  )}
                              </div>
                              <span className="rounded-full bg-white px-2 py-0.5 text-[10px] text-teal-600">
                                {activity.progress &&
                                activity.progress !== "not_started"
                                  ? progressLabels[activity.progress]
                                  : index + 1}
                              </span>
                            </div>
                            {activity.booking && (
                              <p className="mt-2 text-[11px] text-amber-700">
                                已有预约{" "}
                                {timeLabel(
                                  activity.booking.start_time,
                                  activity.booking.start_day_offset,
                                )}{" "}
                                · 停留 {activity.booking.duration_minutes} 分钟
                              </p>
                            )}
                          </button>
                          {!activity.is_context_anchor &&
                            !["station", "meal", "transport"].includes(
                              activity.kind,
                            ) && (
                              <PlacePhoto
                                versionId={detail.version.id}
                                activity={activity}
                              />
                            )}
                        </div>
                        {activity.notes && (
                          <p className="px-2 pt-2 text-xs leading-relaxed text-slate-500">
                            {activity.notes}
                          </p>
                        )}
                        {activity.place_status !== "matched" &&
                          activity.kind !== "free_time" &&
                          !activity.is_context_anchor &&
                          !activity.booking &&
                          (!day.replan_context ||
                            index >
                              day.activities.findIndex(
                                (a) => a.id === day.replan_context?.activity_id,
                              )) &&
                          activity.progress === "not_started" && (
                            <button
                              type="button"
                              disabled={!editable || disabled || busy}
                              aria-label={`确认第${day.day}天第${index + 1}站的地点`}
                              className="mt-2 mb-1 rounded-lg border border-teal-100 px-3 py-1.5 text-xs text-teal-700 disabled:opacity-40"
                              onClick={() =>
                                onAsk(
                                  `确认第${day.day}天第${index + 1}站的地点`,
                                )
                              }
                            >
                              对话确认地点
                            </button>
                          )}
                        {advanced && !activity.is_context_anchor && (
                          <div className="py-2">
                            <Button
                              size="sm"
                              disabled={!editable || disabled || busy}
                              onClick={() =>
                                setEditing(
                                  editing === activity.id ? null : activity.id,
                                )
                              }
                            >
                              手动编辑此站
                            </Button>
                            {activity.place_status !== "matched" && (
                              <p className="mt-2 text-xs text-slate-500">
                                {activity.match_reason ||
                                  "保留建议地点，可通过对话进一步指定。"}
                              </p>
                            )}
                            {editing === activity.id && (
                              <ActivityEditor
                                key={`${detail.version.id}:${activity.id}`}
                                activity={activity}
                                city={
                                  activity.city ||
                                  detail.requirements?.destination ||
                                  ""
                                }
                                disabled={busy || disabled}
                                onCancel={() => setEditing(null)}
                                onSave={(draft, placeId) =>
                                  mutate(() =>
                                    api.updateActivity(
                                      detail.version.id,
                                      day.day,
                                      activity.id,
                                      draft,
                                      placeId,
                                    ),
                                  )
                                }
                              />
                            )}
                          </div>
                        )}
                        {day.legs[index] && (
                          <div className="flex items-start gap-2 px-2 py-4 text-xs leading-relaxed text-slate-500">
                            <span className="rounded-full bg-slate-100 p-1 text-slate-400">
                              <ArrowDown className="h-3 w-3" />
                            </span>
                            <div>
                              <p className="flex items-center gap-1.5 font-medium text-slate-600">
                                <ModeIcon mode={day.legs[index].mode} />
                                {legText(day, index)}
                              </p>
                              <p className="mt-1 text-[11px] text-slate-400">
                                {day.legs[index].status === "ok"
                                  ? "路线来自高德查询"
                                  : day.legs[index].message || "按建议行程衔接"}
                                {day.legs[index].cost !== null
                                  ? ` · ${day.legs[index].cost_source === "estimate" ? "预计 " : ""}¥${day.legs[index].cost}${day.legs[index].cost_basis === "per_person" ? "/人" : ""}`
                                  : ""}
                              </p>
                              {day.legs[index].mode === "rail" &&
                                day.activities[index + 1] && (
                                  <RailTicketLink
                                    from={
                                      activity.place?.name ||
                                      activity.place_name ||
                                      activity.title
                                    }
                                    to={
                                      day.activities[index + 1].place?.name ||
                                      day.activities[index + 1].place_name ||
                                      day.activities[index + 1].title
                                    }
                                    date={travelDate(
                                      detail.requirements?.departure_date,
                                      day.day +
                                        (activity.start_day_offset ?? 0),
                                    )}
                                  />
                                )}
                            </div>
                          </div>
                        )}
                      </div>
                    </li>
                  ))}
                </ol>
              </div>
              <div
                id="journey-map"
                tabIndex={-1}
                className="journey-map-section min-w-0 space-y-4"
              >
                <DayMap
                  key={`${detail.version.id}:${day.day}`}
                  versionId={detail.version.id}
                  day={day}
                  configured={itinerary?.map_status !== "unconfigured"}
                />
                <div className="rounded-xl bg-slate-50 p-4">
                  <p className="flex items-center gap-2 text-sm font-semibold text-slate-700">
                    <MessageCircle className="h-4 w-4 text-teal-600" />
                    想调整，直接说
                  </p>
                  <p className="mt-1 text-xs leading-relaxed text-slate-500">
                    AI 会一起更新后续路线、时间和费用。
                  </p>
                  <div className="mt-3 flex flex-wrap gap-2">
                    {["轻松一点", "换成室内活动", "少走路"].map((text) => (
                      <button
                        type="button"
                        key={text}
                        disabled={!editable || disabled || busy}
                        onClick={() =>
                          onAsk(
                            `请把第${day.day}天调整为${text}，保留其他天的安排，并同步更新路线、时间和费用。`,
                          )
                        }
                        className="rounded-full border border-slate-200 bg-white px-3 py-1.5 text-xs text-slate-600 hover:border-teal-300 disabled:opacity-50"
                      >
                        {text}
                      </button>
                    ))}
                  </div>
                </div>
                <details className="rounded-xl border border-slate-100 px-4 py-3">
                  <summary className="cursor-pointer text-xs text-slate-500">
                    时间、费用与查询说明
                  </summary>
                  <div className="mt-3 space-y-2 text-xs leading-relaxed text-slate-500">
                    {day.schedule_notes?.map((note, i) => (
                      <p key={`schedule-${i}`}>{note}</p>
                    ))}
                    <p>
                      已列小计包含估算，不代表完整旅行总价；尚有{" "}
                      {day.unknown_cost_count}{" "}
                      项费用未计入。步行距离不含景区内部游览。
                    </p>
                    {!durationKnown && (
                      <p>部分交通尚无查询耗时，建议时间需结合实际出行调整。</p>
                    )}
                    {day.warnings.map((warning, i) => (
                      <p key={i}>{warning}</p>
                    ))}
                  </div>
                </details>
              </div>
            </div>
            <div
              id="journey-budget"
              tabIndex={-1}
              className="journey-budget-section"
            >
              <BudgetPanel
                detail={detail}
                day={day.day}
                disabled={!editable || disabled || busy}
                onCommit={mutate}
              />
            </div>
            <details className="mt-4 rounded-xl border border-slate-100 p-4">
              <summary className="cursor-pointer text-xs text-slate-600">
                消费明细、图片凭证与退款报销
              </summary>
              <TravelLedger
                detail={detail}
                day={day.day}
                disabled={!editable || disabled || busy}
                onCommit={mutate}
              />
            </details>
            <TravelRecap
              key={detail.version.id}
              detail={detail}
              disabled={!editable || disabled || busy}
              onCommit={mutate}
            />
            {advanced && (
              <div className="mt-5 flex flex-wrap items-center gap-3 rounded-xl border border-dashed border-slate-200 p-4">
                <label className="text-xs text-slate-500">
                  市内交通策略
                  <select
                    aria-label="交通方式"
                    className="ml-2 rounded-lg border border-slate-200 bg-white p-2"
                    value={day.travel_mode}
                    disabled={!editable || disabled || busy}
                    onChange={(e) =>
                      mutate(() =>
                        api.recalculateDay(
                          detail.version.id,
                          day.day,
                          e.target.value as TravelMode,
                        ),
                      )
                    }
                  >
                    {["auto", "walking", "transit", "driving"].map((mode) => (
                      <option key={mode} value={mode}>
                        {modes[mode as TravelMode]}
                      </option>
                    ))}
                  </select>
                </label>
                <Button
                  size="sm"
                  loading={busy}
                  disabled={!editable || disabled}
                  onClick={() =>
                    mutate(() =>
                      api.recalculateDay(
                        detail.version.id,
                        day.day,
                        day.travel_mode,
                      ),
                    )
                  }
                >
                  <RefreshCw className="h-3 w-3" />
                  重新校验此日
                </Button>
                <span className="text-[11px] text-slate-400">
                  铁路、航班等城际段保持独立安排
                </span>
              </div>
            )}
          </>
        )}
      </div>
    </Card>
  );
}

function RouteStrip({
  day,
  selected,
  onSelect,
}: {
  day: DayPlan;
  selected?: string;
  onSelect: (id: string) => void;
}) {
  return (
    <div
      className="overflow-x-auto rounded-xl bg-slate-50 p-4"
      aria-label="连续线路图"
    >
      <div className="flex w-max min-w-full items-center gap-3">
        {day.activities.map((activity, index) => (
          <div className="flex shrink-0 items-center gap-3" key={activity.id}>
            <button
              type="button"
              onClick={() => onSelect(activity.id)}
              aria-pressed={selected === activity.id}
              className={`max-w-44 rounded-lg border bg-white px-3 py-2 text-left ${selected === activity.id ? "border-teal-400 shadow-sm" : "border-slate-200"}`}
            >
              <span className="mb-1 block text-[10px] text-teal-600">
                {activity.start_time
                  ? timeLabel(activity.start_time, activity.start_day_offset)
                  : `第 ${index + 1} 站`}
              </span>
              <span className="block max-w-40 truncate text-xs font-semibold text-slate-700">
                {activity.place?.name ??
                  (activity.place_name || activity.title)}
              </span>
            </button>
            {index < day.activities.length - 1 && (
              <div className="flex min-w-20 flex-col items-center gap-1 text-[10px] text-slate-400">
                <span>{modes[day.legs[index]?.mode ?? "auto"]}</span>
                <span className="flex w-full items-center">
                  <span className="h-px flex-1 bg-teal-200" />
                  <ArrowRight className="h-3 w-3 text-teal-500" />
                </span>
                {day.legs[index]?.duration_seconds != null && (
                  <span>
                    约 {Math.ceil(day.legs[index].duration_seconds! / 60)} 分钟
                  </span>
                )}
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}

function DayMap({
  versionId,
  day,
  configured,
}: {
  versionId: string;
  day: DayPlan;
  configured: boolean;
}) {
  const [state, setState] = useState<{
    url: string | null;
    error: string | null;
    loading: boolean;
  }>({ url: null, error: null, loading: true });
  const [attempt, setAttempt] = useState(0);
  const canShow = configured && day.activities.some((a) => a.place);
  useEffect(() => {
    if (!canShow) return;
    const controller = new AbortController();
    let objectUrl: string | null = null;
    let active = true;
    setState({ url: null, error: null, loading: true });
    void api
      .dayMapImage(versionId, day.day, controller.signal)
      .then((blob) => {
        if (!active) return;
        objectUrl = URL.createObjectURL(blob);
        setState({ url: objectUrl, error: null, loading: false });
      })
      .catch((error) => {
        if (active && !controller.signal.aborted)
          setState({
            url: null,
            error: error instanceof Error ? error.message : "地图暂时不可用",
            loading: false,
          });
      });
    return () => {
      active = false;
      controller.abort();
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [versionId, day.day, attempt, canShow]);
  return (
    <div className="overflow-hidden rounded-xl border border-slate-100 bg-slate-50">
      {canShow && state.url ? (
        // eslint-disable-next-line @next/next/no-img-element
        <img
          src={state.url}
          alt={`第 ${day.day} 天高德地图，标号对应线路顺序`}
          className="h-auto w-full"
          onError={() =>
            setState({
              url: null,
              error: "图片暂时无法显示，已保留线路示意。",
              loading: false,
            })
          }
        />
      ) : (
        <div className="p-5">
          <p className="mb-4 flex items-center gap-2 text-xs font-medium text-slate-600">
            <Route className="h-4 w-4 text-teal-600" />
            当天地图
          </p>
          <p className="text-xs leading-6 text-slate-500">
            按时间轴顺序查看当天停留与交通；地点导航可在下方打开。
          </p>
          <div className="mt-4 text-[11px] leading-relaxed text-slate-400">
            {canShow && state.loading
              ? "正在加载高德底图，线路已可查看…"
              : state.error || "具体地点定位后将自动显示高德底图。"}
          </div>
          {canShow && state.error && (
            <Button
              size="sm"
              className="mt-2"
              onClick={() => setAttempt((n) => n + 1)}
            >
              重新获取地图
            </Button>
          )}
        </div>
      )}
      {state.url && (
        <p className="border-t border-slate-100 px-3 py-2 text-[10px] text-slate-400">
          高德地图 · 最多展示前 10 个地点和前 4
          段道路路线；铁路/航班见时间轴，不绘制为市内道路。
        </p>
      )}
    </div>
  );
}
function ActivityEditor({
  activity,
  city,
  disabled,
  onSave,
  onCancel,
}: {
  activity: ScheduledActivity;
  city: string;
  disabled: boolean;
  onSave: (draft: Activity, placeId?: string) => Promise<unknown>;
  onCancel: () => void;
}) {
  const [draft, setDraft] = useState<Activity>({ ...activity, city });
  const [candidates, setCandidates] = useState(activity.candidates);
  const [placeId, setPlaceId] = useState<string | undefined>(
    activity.place?.id,
  );
  const [searching, setSearching] = useState(false);
  const [error, setError] = useState<string | null>(null);
  async function search() {
    setSearching(true);
    setError(null);
    try {
      const result = await api.searchPlaces(draft.place_name, draft.city);
      setCandidates(result.items);
      if (!result.items.length)
        setError("未找到地点，请补充城市或更具体的名称。");
    } catch (err) {
      setError(err instanceof Error ? err.message : "搜索失败");
    } finally {
      setSearching(false);
    }
  }
  function choose(place: Place) {
    const changed = place.id !== activity.place?.id;
    setPlaceId(place.id);
    setDraft({
      ...draft,
      place_name: place.name,
      city: place.city || draft.city,
      ...(changed
        ? {
            title: place.name,
            cost: null,
            cost_basis: "unknown",
            source_indices: [],
            notes: "",
          }
        : {}),
    });
  }
  return (
    <form
      className="mt-3 space-y-3 rounded-lg bg-slate-50 p-3"
      onSubmit={(e) => {
        e.preventDefault();
        void onSave(draft, placeId);
      }}
    >
      <label className="block text-xs text-slate-500">
        活动名称
        <input
          className={inputClass}
          required
          maxLength={100}
          value={draft.title}
          disabled={disabled}
          onChange={(e) => setDraft({ ...draft, title: e.target.value })}
        />
      </label>
      <div className="grid grid-cols-2 gap-2">
        <label className="text-xs text-slate-500">
          城市
          <input
            className={inputClass}
            maxLength={50}
            value={draft.city}
            disabled={disabled}
            onChange={(e) => {
              setDraft({ ...draft, city: e.target.value });
              setPlaceId(undefined);
              setCandidates([]);
            }}
          />
        </label>
        <label className="text-xs text-slate-500">
          地点名称
          <input
            className={inputClass}
            maxLength={80}
            value={draft.place_name}
            disabled={disabled}
            onChange={(e) => {
              setDraft({
                ...draft,
                place_name: e.target.value,
                cost: null,
                cost_basis: "unknown",
                source_indices: [],
                notes: "",
              });
              setPlaceId(undefined);
              setCandidates([]);
            }}
          />
        </label>
      </div>
      <Button
        type="button"
        size="sm"
        loading={searching}
        disabled={disabled || !draft.place_name.trim() || !draft.city.trim()}
        onClick={search}
      >
        搜索地点候选
      </Button>
      {candidates.length > 0 && (
        <fieldset className="space-y-2">
          <legend className="text-xs text-slate-500">确认具体地点</legend>
          {candidates.map((place) => (
            <label className="flex items-start gap-2 text-xs" key={place.id}>
              <input
                type="radio"
                name={`place-${activity.id}`}
                checked={placeId === place.id}
                onChange={() => choose(place)}
                disabled={disabled}
              />
              <span>
                {place.name}
                <span className="block text-slate-400">
                  {place.city} {place.address}
                </span>
              </span>
            </label>
          ))}
        </fieldset>
      )}
      <div className="grid grid-cols-2 gap-2">
        <label className="text-xs text-slate-500">
          开始日期
          <select
            aria-label="活动开始日期"
            className={inputClass}
            value={draft.start_day_offset ?? 0}
            disabled={disabled}
            onChange={(e) =>
              setDraft({ ...draft, start_day_offset: Number(e.target.value) })
            }
          >
            {Array.from(
              { length: Math.max(2, (draft.start_day_offset ?? 0) + 1) },
              (_, offset) => (
                <option key={offset} value={offset}>
                  {dayLabel(offset)}
                </option>
              ),
            )}
          </select>
        </label>
        <label className="text-xs text-slate-500">
          开始时间
          <input
            type="time"
            className={inputClass}
            value={draft.start_time ?? ""}
            disabled={disabled}
            onChange={(e) =>
              setDraft({ ...draft, start_time: e.target.value || null })
            }
          />
        </label>
        <label className="text-xs text-slate-500">
          停留分钟
          <input
            type="number"
            min={1}
            max={1440}
            className={inputClass}
            value={draft.duration_minutes ?? ""}
            disabled={disabled}
            onChange={(e) =>
              setDraft({
                ...draft,
                duration_minutes: e.target.value
                  ? Number(e.target.value)
                  : null,
              })
            }
          />
        </label>
        <label className="text-xs text-slate-500">
          费用（元，留空待核实）
          <input
            type="number"
            min={0}
            step="0.01"
            className={inputClass}
            value={draft.cost ?? ""}
            disabled={disabled}
            onChange={(e) =>
              setDraft({
                ...draft,
                cost: e.target.value ? Number(e.target.value) : null,
              })
            }
          />
        </label>
        <label className="text-xs text-slate-500">
          费用口径
          <select
            aria-label="费用口径"
            className={inputClass}
            value={draft.cost_basis}
            disabled={disabled}
            onChange={(e) =>
              setDraft({
                ...draft,
                cost_basis: e.target.value as Activity["cost_basis"],
              })
            }
          >
            <option value="unknown">待核实</option>
            <option value="per_person">人均</option>
            <option value="total">全团</option>
          </select>
        </label>
      </div>
      <label className="block text-xs text-slate-500">
        备注
        <textarea
          className={inputClass}
          maxLength={2000}
          value={draft.notes}
          disabled={disabled}
          onChange={(e) => setDraft({ ...draft, notes: e.target.value })}
        />
      </label>
      {error && (
        <p className="text-xs text-red-600" role="alert">
          {error}
        </p>
      )}
      <div className="flex gap-2">
        <Button
          type="submit"
          variant="primary"
          size="sm"
          loading={disabled}
          disabled={searching}
        >
          保存并重算当天
        </Button>
        <Button type="button" size="sm" onClick={onCancel} disabled={disabled}>
          取消
        </Button>
      </div>
    </form>
  );
}
