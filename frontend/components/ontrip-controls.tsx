"use client";

import { useState } from "react";
import { CalendarClock, Route } from "lucide-react";
import type { DayPlan, ScheduledActivity, VersionDetail } from "@/lib/types";
import { api } from "@/lib/api";

const field =
  "mt-1 w-full rounded-lg border border-slate-200 bg-white px-2 py-2 text-xs";
const button =
  "rounded-lg border border-teal-100 px-3 py-2 text-xs text-teal-700 disabled:opacity-40";

export function OnTripControls({
  day,
  active,
  versionId,
  disabled,
  onCommit,
}: {
  day: DayPlan;
  active: ScheduledActivity;
  versionId: string;
  disabled: boolean;
  onCommit: (work: () => Promise<VersionDetail>) => Promise<boolean>;
}) {
  const [mode, setMode] = useState<"booking" | "replan" | null>(null);
  const [time, setTime] = useState(
    active.booking?.start_time ?? active.start_time ?? "14:00",
  );
  const [duration, setDuration] = useState(
    active.booking?.duration_minutes ?? active.duration_minutes ?? 60,
  );
  const [notes, setNotes] = useState(active.booking?.notes ?? "");
  const [endTime, setEndTime] = useState(day.replan_context?.end_time ?? "");
  const [endId, setEndId] = useState(() => {
    const candidate = day.replan_context?.end_activity_id;
    return candidate &&
      day.activities.findIndex((a) => a.id === candidate) >
        day.activities.findIndex((a) => a.id === active.id)
      ? candidate
      : "";
  });
  const [error, setError] = useState("");
  const index = day.activities.findIndex((a) => a.id === active.id);
  const ends = day.activities
    .slice(index + 1)
    .filter((a) => !a.is_context_anchor);

  function open(next: "booking" | "replan") {
    setMode(mode === next ? null : next);
    setError("");
    setTime(
      next === "booking"
        ? (active.booking?.start_time ?? active.start_time ?? "14:00")
        : (day.replan_context?.time ?? active.start_time ?? "14:00"),
    );
  }
  async function save() {
    if (mode === "replan" && endTime && endTime <= time) {
      setError("结束时间需晚于出发时间，跨日请分别安排。");
      return;
    }
    const ok = await onCommit(() =>
      mode === "booking"
        ? api.recordBooking(versionId, day.day, active.id, {
            start_time: time,
            duration_minutes: duration,
            notes,
          })
        : api.replanDay(versionId, day.day, {
            activity_id: active.id,
            time,
            end_time: endTime || null,
            end_activity_id: endId || null,
          }),
    );
    if (ok) setMode(null);
  }
  return (
    <div className="mt-3 border-t border-slate-100 pt-3">
      {active.booking && (
        <p className="mb-2 flex items-center gap-1 text-xs text-amber-700">
          <CalendarClock className="h-3.5 w-3.5" />
          已有预约 {active.booking.start_time} ·{" "}
          {active.booking.duration_minutes} 分钟
        </p>
      )}
      {active.booking?.notes && (
        <p className="mb-2 text-xs leading-5 text-slate-500">
          {active.booking.notes}
        </p>
      )}
      <div className="flex flex-wrap gap-2">
        <button
          type="button"
          disabled={disabled}
          className={button}
          onClick={() => open("replan")}
        >
          <Route className="mr-1 inline h-3.5 w-3.5" />
          从这里调整后续
        </button>
        {active.progress === "not_started" && !active.is_context_anchor && (
          <button
            type="button"
            disabled={disabled}
            className={button}
            onClick={() => open("booking")}
          >
            {active.booking ? "更正预约记录" : "记录已有预约"}
          </button>
        )}
      </div>
      {mode && (
        <form
          aria-label={mode === "booking" ? "记录预约" : "调整后续安排"}
          className="mt-3 rounded-xl bg-slate-50 p-3"
          onSubmit={(e) => {
            e.preventDefault();
            void save();
          }}
        >
          <fieldset
            disabled={disabled}
            className="space-y-3 disabled:opacity-60"
          >
            <p className="text-xs leading-5 text-slate-500">
              {mode === "booking"
                ? "记录您已有的预约，商家订单请在原平台管理。"
                : "从所选地点接上后续路线，保留预约；时间不够时减少普通活动。想换景点可继续对话。"}
            </p>
            <div className="grid grid-cols-2 gap-2">
              <label className="text-xs text-slate-600">
                {mode === "booking" ? "预约开始时间" : "从此处出发的时间"}
                <input
                  required
                  type="time"
                  className={field}
                  value={time}
                  onChange={(e) => setTime(e.target.value)}
                />
              </label>
              {mode === "booking" ? (
                <label className="text-xs text-slate-600">
                  预约停留（分钟）
                  <input
                    required
                    type="number"
                    min={1}
                    max={1440}
                    className={field}
                    value={duration}
                    onChange={(e) => setDuration(Number(e.target.value))}
                  />
                </label>
              ) : (
                <label className="text-xs text-slate-600">
                  最晚结束时间
                  <input
                    type="time"
                    className={field}
                    value={endTime}
                    onChange={(e) => setEndTime(e.target.value)}
                  />
                </label>
              )}
            </div>
            {mode === "replan" && (
              <label className="block text-xs text-slate-600">
                结束地点
                <select
                  aria-label="结束地点"
                  className={field}
                  value={endId}
                  onChange={(e) => setEndId(e.target.value)}
                >
                  <option value="">不限地点</option>
                  {ends.map((a) => (
                    <option key={a.id} value={a.id}>
                      {a.title}
                    </option>
                  ))}
                </select>
              </label>
            )}
            {mode === "booking" && (
              <label className="block text-xs text-slate-600">
                预约备注
                <input
                  maxLength={500}
                  className={field}
                  value={notes}
                  onChange={(e) => setNotes(e.target.value)}
                  placeholder="可填到场要求，无需填写票号"
                />
              </label>
            )}
            {error && (
              <p role="alert" className="text-xs text-red-600">
                {error}
              </p>
            )}
            <div className="flex flex-wrap gap-2">
              <button
                type="submit"
                className="rounded-lg bg-teal-700 px-3 py-2 text-xs text-white"
              >
                {mode === "booking" ? "保存预约记录" : "更新后续安排"}
              </button>
              <button
                type="button"
                className={button}
                onClick={() => setMode(null)}
              >
                取消
              </button>
              {mode === "booking" && active.booking && (
                <button
                  type="button"
                  className="text-xs text-slate-500"
                  onClick={async () => {
                    if (
                      await onCommit(() =>
                        api.recordBooking(versionId, day.day, active.id, null),
                      )
                    )
                      setMode(null);
                  }}
                >
                  清除预约记录
                </button>
              )}
            </div>
            {mode === "booking" && active.booking && (
              <p className="text-[11px] text-slate-400">
                清除仅影响本计划的约束记录；外部预约仍需您在原平台处理。
              </p>
            )}
          </fieldset>
        </form>
      )}
    </div>
  );
}
