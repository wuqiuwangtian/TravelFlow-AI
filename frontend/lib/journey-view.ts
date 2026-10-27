import type { DayPlan, ScheduledActivity, VersionDetail } from "./types";

/** An exact calendar match is useful during a trip; otherwise preserve day 1. */
export function initialJourneyDay(
  detail: VersionDetail,
  now = new Date(),
): number {
  const days = detail.itinerary?.days ?? [];
  const departure = detail.requirements?.departure_date;
  if (departure && /^\d{4}-\d{2}-\d{2}$/.test(departure)) {
    const start = Date.parse(`${departure}T00:00:00Z`);
    const today = Date.UTC(now.getFullYear(), now.getMonth(), now.getDate());
    const number = Math.floor((today - start) / 86_400_000) + 1;
    if (days.some((day) => day.day === number)) return number;
  }
  return days[0]?.day ?? 1;
}

export function nextJourneyStop(day?: DayPlan): ScheduledActivity | undefined {
  const activities = day?.activities ?? [];
  return (
    activities.find(
      (stop) => !stop.is_context_anchor && stop.progress === "in_progress",
    ) ??
    activities.find(
      (stop) =>
        !stop.is_context_anchor &&
        (!stop.progress || stop.progress === "not_started"),
    ) ??
    activities.find((stop) => !stop.is_context_anchor) ??
    activities[0]
  );
}
