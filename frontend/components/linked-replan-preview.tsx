import type { ChatClarification } from "@/lib/types";

export function LinkedReplanPreview({
  preview,
}: {
  preview: ChatClarification;
}) {
  const days = [...new Set(preview.changes?.map((change) => change.day))].sort(
    (a, b) => a - b,
  );
  return (
    <div aria-label="两天调整预览" className="mt-3 min-w-0 space-y-3">
      <div className="grid max-h-80 min-w-0 gap-3 overflow-y-auto sm:grid-cols-2">
        {days.map((day) => (
          <section
            key={day}
            aria-label={`第${day}天变化`}
            className="min-w-0 rounded-lg border border-warning-line bg-surface p-3"
          >
            <h4 className="font-semibold">第 {day} 天</h4>
            <ol className="mt-2 space-y-3">
              {preview.changes
                ?.filter((change) => change.day === day)
                .map((change, index) => (
                  <li
                    key={index}
                    className="min-w-0 break-words border-l-2 border-accent-line pl-2"
                  >
                    <p className="font-medium">{change.title}</p>
                    <p
                      className={
                        change.status === "omitted"
                          ? "text-danger"
                          : change.status === "moved_in" ||
                              change.status === "moved_out"
                            ? "text-accent"
                            : "text-muted"
                      }
                    >
                      {change.before} → {change.after}
                    </p>
                    {change.protected && (
                      <p className="text-[11px] text-accent">
                        {change.status === "moved_in" ||
                        change.status === "moved_out"
                          ? "地点与关联记录保留"
                          : "固定项目保留"}
                      </p>
                    )}
                  </li>
                ))}
            </ol>
          </section>
        ))}
      </div>
      {!!preview.conflicts?.length && (
        <div
          role="alert"
          aria-label="两天调整冲突"
          className="space-y-2 rounded-lg border border-danger-line bg-danger-soft p-3 text-danger"
        >
          <p className="font-medium">有冲突，原计划尚未改变</p>
          {preview.conflicts.map((conflict) => (
            <p key={conflict}>{conflict}</p>
          ))}
        </div>
      )}
      {!!preview.warnings?.length && (
        <details className="rounded-lg bg-surface p-2 text-muted">
          <summary className="cursor-pointer">
            交通与资料说明（{preview.warnings.length}）
          </summary>
          {preview.warnings.map((warning) => (
            <p key={warning} className="mt-2">
              {warning}
            </p>
          ))}
        </details>
      )}
    </div>
  );
}
