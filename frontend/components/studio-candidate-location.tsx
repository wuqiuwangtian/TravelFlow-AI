"use client";

import type { NodeArtifact } from "@/lib/studio-types";

type Candidate = NonNullable<NodeArtifact["candidates"]>["places"][number];
const labels = {
  matched: "地图已定位",
  ambiguous: "地点待明确",
  not_found: "未找到地点",
  unavailable: "定位暂不可用",
  unconfigured: "地图未配置",
};

export default function StudioCandidateLocation({
  candidate: p,
  choice,
  onChoose,
}: {
  candidate: Candidate;
  choice?: string;
  onChoose?: (candidateId: string, placeId: string | null) => void;
}) {
  const location = p.location;
  if (!location) return null;
  return (
    <div
      aria-label={`${p.place_name}定位`}
      className="mt-2 space-y-2 border-t border-accent-line pt-2"
    >
      <p
        className={
          location.status === "matched" ? "text-accent" : "text-warning"
        }
      >
        {labels[location.status]}
      </p>
      {location.place && (
        <p className="break-words">
          {location.place.name} · {location.place.district}
          <br />
          {location.place.address}
        </p>
      )}
      <p className="text-muted">{location.reason}</p>
      {!!location.candidates.length && (
        <details open={location.status === "ambiguous"}>
          <summary className="cursor-pointer">
            查看地图候选 · {location.candidates.length}处
          </summary>
          <ul className="mt-2 space-y-2">
            {location.candidates.map((place) => (
              <li key={place.id} className="rounded border border-line p-2">
                <p>
                  {place.name} · {place.city} {place.district}
                </p>
                <p className="break-words text-muted">{place.address}</p>
                {onChoose && (
                  <button
                    type="button"
                    aria-label={`${p.place_name}选用 ${place.address || place.name}`}
                    aria-pressed={choice === place.id}
                    onClick={() => onChoose(p.id, place.id)}
                    className="mt-1 text-accent underline"
                  >
                    {choice === place.id ? "已指定，运行后应用" : "选用此地点"}
                  </button>
                )}
              </li>
            ))}
          </ul>
        </details>
      )}
      {onChoose && choice && (
        <button
          type="button"
          aria-label={`${p.place_name}清除指定`}
          onClick={() => onChoose(p.id, null)}
          className="text-muted underline"
        >
          清除指定地点
        </button>
      )}
      {onChoose && location.status === "ambiguous" && (
        <p className="text-muted">
          如需明确地点，选择后运行此节点；程序会核对本次地图结果，不直接修改正式行程。
        </p>
      )}
    </div>
  );
}
