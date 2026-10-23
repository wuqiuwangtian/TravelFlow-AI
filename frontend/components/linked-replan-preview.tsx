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
            className="min-w-0 rounded-lg border border-amber-100 bg-white p-3"
          >
            <h4 className="font-semibold">第 {day} 天</h4>
            <ol className="mt-2 space-y-3">
              {preview.changes
                ?.filter((change) => change.day === day)
                .map((change, index) => (
                  <li
                    key={index}
                    className="min-w-0 break-words border-l-2 border-teal-100 pl-2"
                  >
                    <p className="font-medium">{change.title}</p>
                    <p
                      className={
                        change.status === "omitted"
                          ? "text-rose-700"
                          : "text-slate-600"
                      }
                    >
                      {change.before} → {change.after}
                    </p>
                    {change.protected && (
                      <p className="text-[11px] text-teal-700">固定项目保留</p>
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
          className="space-y-2 rounded-lg border border-rose-200 bg-rose-50 p-3 text-rose-800"
        >
          <p className="font-medium">有冲突，原计划尚未改变</p>
          {preview.conflicts.map((conflict) => (
            <p key={conflict}>{conflict}</p>
          ))}
        </div>
      )}
      {!!preview.warnings?.length && (
        <details className="rounded-lg bg-white/70 p-2 text-slate-600">
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
