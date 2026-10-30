import type { BudgetRow, ScheduledActivity, VersionDetail } from "./types";

export interface PlaceAttention {
  day: number;
  stop: number;
  activity: ScheduledActivity;
}
export interface JourneyNote {
  day: number;
  message: string;
}

/** 只投影所选版本的真实字段，不从对话或模型正文猜测风险或补齐费用。 */
export function journeyAttention(detail: VersionDetail) {
  const places: PlaceAttention[] = [];
  const notes: JourneyNote[] = [];
  for (const day of detail.itinerary?.days ?? []) {
    day.activities.forEach((activity, index) => {
      if (
        !activity.is_context_anchor &&
        !["completed", "skipped"].includes(activity.progress) &&
        !["free_time", "transport"].includes(activity.kind) &&
        (activity.place_status !== "matched" || !activity.place)
      ) {
        places.push({ day: day.day, stop: index + 1, activity });
      }
    });
    const messages = new Set(day.warnings.filter((message) => message.trim()));
    for (const leg of day.legs) {
      if (leg.status === "ok" && leg.duration_seconds !== null) continue;
      const from = day.activities.find(
        (activity) => activity.id === leg.from_id,
      );
      const to = day.activities.find((activity) => activity.id === leg.to_id);
      if (to?.progress === "skipped") continue;
      const endpoints = `${from?.place_name || from?.title || "出发点"} → ${to?.place_name || to?.title || "下一站"}`;
      messages.add(
        `${endpoints}：${leg.message || (leg.duration_seconds === null ? "交通耗时尚未明确。" : "交通为建议安排，需核对。")}`,
      );
    }
    for (const message of messages) notes.push({ day: day.day, message });
  }
  const overview = detail.budget_overview;
  const unknownRows: BudgetRow[] =
    overview?.rows.filter(
      (row) => row.planned_cents === null && !row.settled,
    ) ?? [];
  return {
    places,
    notes,
    unknownRows,
    unknownCount: overview?.unknown_count ?? null,
    coverageWarnings: overview?.coverage_warnings ?? [],
    budgetScopeUnknown:
      !!overview &&
      detail.requirements?.budget != null &&
      overview.budget_cents === null,
  };
}
