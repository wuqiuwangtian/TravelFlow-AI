import { ActionFeedback, Button, Card } from "@/components/ui";
import type { PlanningContext } from "@/lib/types";

const fields: Record<string, string> = {
  destination: "目的地",
  origin: "出发地",
  duration_days: "游玩天数",
  budget: "大致预算",
};
const stages: Record<string, string> = {
  collecting: "正在了解这趟旅行",
  researching: "正在检索旅行资料",
  planning: "正在生成行程",
  reviewing: "行程待确认",
  finished: "计划已确认",
};

export function PlanningContextPanel({
  context,
  pending,
  error,
  onRetry,
}: {
  context: PlanningContext | null;
  pending: boolean;
  error: string | null;
  onRetry: () => void;
}) {
  const req = context?.requirements;
  const known = req
    ? [
        ["目的地", req.destination],
        ["出发地", req.origin],
        [
          "游玩天数",
          req.duration_days == null ? null : `${req.duration_days} 天`,
        ],
        [
          "预算",
          req.budget == null
            ? null
            : `${req.budget.toLocaleString("zh-CN")} 元${req.budget_basis === "per_person" ? "／人" : req.budget_basis === "total" ? "（全团）" : "（口径未定）"}`,
        ],
        ["出行人数", req.travelers == null ? null : `${req.travelers} 人`],
        ["出发日期", req.departure_date],
        ["本次偏好", req.preferences.join(" · ")],
      ].filter(([, value]) => value != null && value !== "")
    : [];
  const recommending =
    !!context?.destination_recommendation_requested && !req?.destination;
  const missing = (context?.missing_fields ?? []).filter(
    (field) => !(recommending && field === "destination"),
  );
  return (
    <Card
      className="journey-planning-help"
      role="region"
      aria-label="本次旅行条件"
    >
      <p className="text-xs font-medium tracking-widest text-accent">
        {context?.status ? (stages[context.status] ?? "本次旅行") : "本次旅行"}
      </p>
      <h2>
        {known.length
          ? "已经了解的条件"
          : pending
            ? "正在读取旅行条件"
            : error
              ? "旅行条件暂未读出"
              : "先聊旅行想法"}
      </h2>
      <ActionFeedback
        pending={pending ? "正在读取本次旅行条件…" : undefined}
        error={
          error
            ? "旅行条件暂未读出，可在对话区重试读取；已有输入会保留。"
            : null
        }
      />
      {known.length > 0 && (
        <dl className="mt-4 space-y-3 text-sm leading-6">
          {known.map(([label, value]) => (
            <div
              key={label}
              className="grid grid-cols-[5rem_minmax(0,1fr)] gap-3"
            >
              <dt className="text-muted">{label}</dt>
              <dd className="break-words text-ink">{value}</dd>
            </div>
          ))}
        </dl>
      )}
      {context && (
        <div className="mt-5 border-t border-line pt-4 text-sm leading-7 text-muted">
          {missing.length > 0 ? (
            <p>
              还需要知道：
              {missing.map((field) => fields[field] ?? field).join("、")}
              。直接在对话里补充即可。
            </p>
          ) : !recommending && known.length ? (
            <p>必要条件已齐，可以继续对话生成或调整行程。</p>
          ) : null}
          {recommending && (
            <p className="mt-3">
              已记住你想先推荐城市，条件齐全后会继续推荐；选定城市后再生成行程。
            </p>
          )}
          {!req?.destination && !!context.destination_ideas.length && (
            <div className="mt-3">
              <p className="font-medium text-ink">已推荐的城市</p>
              <ol className="mt-1 list-inside list-decimal break-words">
                {context.destination_ideas.map((city, index) => (
                  <li key={index}>{city}</li>
                ))}
              </ol>
              <p>在对话中回复序号或城市名即可选择。</p>
            </div>
          )}
          <p className="mt-3 text-xs">
            日期、人数和预算口径可稍后补充，未知时保留未定。
          </p>
        </div>
      )}
      {!context && !pending && !error && (
        <p className="mt-4 text-sm leading-7 text-muted">
          暂未取得本次条件摘要，可继续对话或
          <Button size="sm" variant="ghost" onClick={onRetry}>
            重新读取
          </Button>
          。
        </p>
      )}
    </Card>
  );
}
