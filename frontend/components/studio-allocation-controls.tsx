"use client";

import type { StudioNode } from "@/lib/studio-types";

export default function StudioAllocationControls({
  value: p,
  onChange,
}: {
  value: StudioNode["parameters"];
  onChange: (patch: Partial<StudioNode["parameters"]>) => void;
}) {
  const inputClass = "mt-2 w-full rounded-lg border border-slate-200 p-2";
  return (
    <div className="space-y-4">
      <label className="block">
        规划天数
        <input
          type="number"
          min="1"
          max="60"
          step="1"
          aria-label="分日规划天数"
          placeholder="沿用候选池中的需求天数"
          value={p.allocation_days ?? ""}
          onChange={(e) =>
            onChange({
              allocation_days: e.target.value ? Number(e.target.value) : null,
            })
          }
          className={inputClass}
        />
      </label>
      <label className="block">
        安排节奏
        <select
          aria-label="分日安排节奏"
          value={p.allocation_pace ?? "balanced"}
          onChange={(e) =>
            onChange({
              allocation_pace: e.target
                .value as StudioNode["parameters"]["allocation_pace"],
            })
          }
          className={inputClass}
        >
          <option value="relaxed">松弛 · 每天最多2处／4小时</option>
          <option value="balanced">均衡 · 每天最多3处／6小时</option>
          <option value="full">丰富 · 每天最多4处／8小时</option>
        </select>
      </label>
      <details className="space-y-4">
        <summary className="cursor-pointer">时间与筛选规则</summary>
        <label className="block">
          每日首站开始
          <input
            type="time"
            aria-label="分日开始时刻"
            value={p.allocation_start_time ?? "09:00"}
            onChange={(e) =>
              onChange({ allocation_start_time: e.target.value })
            }
            className={inputClass}
          />
        </label>
        <label className="block">
          每日景点上限
          <select
            aria-label="分日景点上限"
            value={p.max_attractions_per_day ?? ""}
            onChange={(e) =>
              onChange({
                max_attractions_per_day: e.target.value
                  ? Number(e.target.value)
                  : null,
              })
            }
            className={inputClass}
          >
            <option value="">沿用节奏</option>
            {Array.from({ length: 20 }, (_, i) => (
              <option key={i + 1} value={i + 1}>
                {i + 1}处
              </option>
            ))}
          </select>
        </label>
        <label className="block">
          未知停留的默认时长（分钟）
          <input
            type="number"
            min="15"
            max="480"
            aria-label="分日默认停留"
            value={p.allocation_default_stay_minutes ?? 90}
            onChange={(e) =>
              onChange({
                allocation_default_stay_minutes: Number(e.target.value),
              })
            }
            className={inputClass}
          />
        </label>
        <label className="block">
          段间预留缓冲（分钟）
          <input
            type="number"
            min="0"
            max="180"
            aria-label="分日段间缓冲"
            value={p.allocation_buffer_minutes ?? 30}
            onChange={(e) =>
              onChange({ allocation_buffer_minutes: Number(e.target.value) })
            }
            className={inputClass}
          />
        </label>
        <label className="block">
          必去景点
          <textarea
            aria-label="分日必去名单"
            maxLength={2000}
            value={p.required_places ?? ""}
            onChange={(e) => onChange({ required_places: e.target.value })}
            placeholder="名称关键词，换行或逗号分隔"
            className={inputClass}
          />
        </label>
        <label className="block">
          排除景点
          <textarea
            aria-label="分日排除名单"
            maxLength={2000}
            value={p.excluded_places ?? ""}
            onChange={(e) => onChange({ excluded_places: e.target.value })}
            className={inputClass}
          />
        </label>
      </details>
      <p className="leading-6 text-slate-500">
        只分配同城候选景点，按停留与缓冲平衡每天的负担。缓冲不是已核实的交通；未安排的景点仍保留在候选池。必去放不下或每天无法排入景点时提示修正，不补造景点或跨城交通。
      </p>
    </div>
  );
}
