"use client";

import type { BudgetRules } from "@/lib/studio-types";

export const emptyBudgetRules: BudgetRules = {
  meal_per_person_day_cents: null,
  lodging_per_room_night_cents: null,
  lodging_rooms: null,
  round_trip_per_person_cents: null,
  contingency_cents: null,
};

export default function StudioBudgetControls({
  value,
  onChange,
}: {
  value: BudgetRules | undefined;
  onChange: (rules: BudgetRules) => void;
}) {
  const rules = { ...emptyBudgetRules, ...value };
  return (
    <div className="space-y-4">
      <p className="leading-6 text-slate-500">
        留空只汇总原行程。填写后补充尚未列出的分类，已有金额或待核实项目均沿用，避免重复；不生成实际消费。
      </p>
      {(
        [
          ["meal_per_person_day_cents", "饮食预留（元／人／天）"],
          ["lodging_per_room_night_cents", "住宿预留（元／间／晚）"],
          ["round_trip_per_person_cents", "往返大交通预留（元／人／往返）"],
          ["contingency_cents", "备用金（元／全团整趟）"],
        ] as const
      ).map(([key, label]) => (
        <label className="block" key={key}>
          {label}
          <input
            type="number"
            min="0"
            max="1000000"
            step="0.01"
            aria-label={label}
            placeholder="不额外预留"
            value={rules[key] == null ? "" : rules[key] / 100}
            onChange={(e) =>
              onChange({
                ...rules,
                [key]:
                  e.target.value === ""
                    ? null
                    : Math.round(Number(e.target.value) * 100),
              })
            }
            className="mt-2 w-full rounded-lg border border-slate-200 p-2"
          />
        </label>
      ))}
      <label className="block">
        住宿房间数
        <input
          type="number"
          min="1"
          max="1000"
          step="1"
          aria-label="住宿房间数"
          placeholder="不推测房间数"
          value={rules.lodging_rooms ?? ""}
          onChange={(e) =>
            onChange({
              ...rules,
              lodging_rooms:
                e.target.value === "" ? null : Number(e.target.value),
            })
          }
          className="mt-2 w-full rounded-lg border border-slate-200 p-2"
        />
      </label>
      <p className="leading-6 text-slate-500">
        饮食按完整旅行日，住宿按天数减一晚计算；特殊过夜安排请核对。人数或房间数未知时保留待核实，已有车站、大交通安排或自定义交通预算时不再叠加往返预留。
      </p>
      <p className="leading-6 text-slate-500">
        采用时会根据保留的实际记录重新核算。确认后的预留成为可编辑自定义预算，后续采用会保留现有金额。
      </p>
    </div>
  );
}
