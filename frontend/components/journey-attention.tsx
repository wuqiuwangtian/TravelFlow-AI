import { AlertTriangle } from "lucide-react";
import { Button } from "@/components/ui";
import { journeyAttention } from "@/lib/journey-attention";
import type { VersionDetail } from "@/lib/types";

const placeStatus: Record<string, string> = {
  ambiguous: "有同名候选，尚未选定",
  not_found: "尚未找到明确地点",
  unavailable: "定位服务暂不可用",
  pending: "尚未定位",
};

export function JourneyAttention({
  detail,
  busy,
  onPlace,
  onBudget,
  onNote,
}: {
  detail: VersionDetail;
  busy: boolean;
  onPlace: (day: number, activityId: string) => void;
  onBudget: (rowId: string) => void;
  onNote: (day: number) => void;
}) {
  const { places, notes, unknownRows, unknownCount, budgetScopeUnknown } =
    journeyAttention(detail);
  if (
    !places.length &&
    !notes.length &&
    unknownCount === 0 &&
    !budgetScopeUnknown
  )
    return null;
  return (
    <section
      aria-label="行程待核对事项"
      className="trip-ui mb-5 rounded-lg border border-warning-line bg-warning-soft text-sm"
    >
      <details>
        <summary className="cursor-pointer px-4 py-3 text-warning">
          <span className="inline-flex items-center gap-2 font-medium">
            <AlertTriangle aria-hidden="true" className="h-4 w-4" />
            出发前需留意
          </span>
          <span className="mt-1 block text-xs leading-6 text-warning">
            {[
              places.length ? `地点 ${places.length} 处待明确` : null,
              unknownCount === null
                ? "费用汇总未取得"
                : unknownCount > 0
                  ? `费用 ${unknownCount} 项未定`
                  : null,
              budgetScopeUnknown ? "预算比较口径未定" : null,
              notes.length ? `时间与行程 ${notes.length} 条提醒` : null,
            ]
              .filter(Boolean)
              .join(" · ")}
          </span>
        </summary>
        <div className="space-y-5 border-t border-warning-line px-4 py-4 text-ink">
          <p className="text-xs leading-6 text-warning">
            汇总整趟旅行，按当前查看版本展示。展开和查看只定位已有内容，不会重新规划。
          </p>
          {places.length > 0 && (
            <section aria-label="待明确地点">
              <h3 className="font-medium">地点待明确</h3>
              <ul className="mt-2 space-y-3">
                {places.map(({ day, stop, activity }) => (
                  <li
                    key={`${day}:${activity.id}`}
                    className="break-words text-xs leading-6"
                  >
                    <p>
                      第{day}天第{stop}站 · {activity.title}：
                      {placeStatus[activity.place_status] ?? "地点资料尚未完整"}
                      。
                    </p>
                    <Button
                      size="sm"
                      variant="ghost"
                      className="text-warning"
                      disabled={busy}
                      onClick={() => onPlace(day, activity.id)}
                      aria-label={`查看第${day}天第${stop}站待明确地点`}
                    >
                      查看本站
                    </Button>
                  </li>
                ))}
              </ul>
            </section>
          )}
          {(unknownCount === null ||
            unknownCount > 0 ||
            budgetScopeUnknown) && (
            <section aria-label="待明确费用">
              <h3 className="font-medium">费用与预算口径</h3>
              <p className="mt-2 text-xs leading-6 text-warning">
                未定费用不等于零，已列预估也不代表最终价格。
              </p>
              {unknownCount === null && (
                <p className="mt-2 text-xs leading-6">
                  本版本费用汇总尚未取得，暂不能判断总价或预算余量。
                </p>
              )}
              {budgetScopeUnknown && (
                <p className="mt-2 text-xs leading-6">
                  已记录预算，但人均／全团口径或换算人数尚未明确，暂不能判断是否超预算；可继续使用已有行程。
                </p>
              )}
              <ul className="mt-2 space-y-3">
                {unknownRows.map((row) => (
                  <li key={row.id} className="break-words text-xs leading-6">
                    <p>
                      {row.day ? `第${row.day}天 · ` : "整趟旅行 · "}
                      {row.title}：金额或换算条件未明确。
                    </p>
                    <Button
                      size="sm"
                      variant="ghost"
                      className="text-warning"
                      disabled={busy}
                      onClick={() => onBudget(row.id)}
                      aria-label={`查看待明确费用：${row.title}`}
                    >
                      查看费用
                    </Button>
                  </li>
                ))}
              </ul>
            </section>
          )}
          {notes.length > 0 && (
            <section aria-label="时间与行程提醒">
              <h3 className="font-medium">时间与行程提醒</h3>
              <ul className="mt-2 space-y-3">
                {notes.map((note, index) => (
                  <li
                    key={`${note.day}:${index}`}
                    className="break-words text-xs leading-6"
                  >
                    <p>
                      第{note.day}天 · {note.message}
                    </p>
                    <Button
                      size="sm"
                      variant="ghost"
                      className="text-warning"
                      disabled={busy}
                      onClick={() => onNote(note.day)}
                      aria-label={`查看第${note.day}天提醒：${note.message}`}
                    >
                      查看当天说明
                    </Button>
                  </li>
                ))}
              </ul>
            </section>
          )}
        </div>
      </details>
    </section>
  );
}
