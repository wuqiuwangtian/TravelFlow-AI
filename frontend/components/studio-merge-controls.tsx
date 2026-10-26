"use client";

import type { StudioGraph, StudioNode } from "@/lib/studio-types";

export default function StudioMergeControls({
  node,
  graph,
  onChange,
}: {
  node: StudioNode;
  graph: StudioGraph;
  onChange: (patch: Partial<StudioNode["parameters"]>) => void;
}) {
  const incoming = graph.edges
    .filter((e) => e.target === node.id)
    .map((e) => e.source);
  const order = [
    ...new Set([
      ...(node.parameters.merge_order ?? []).filter((id) =>
        incoming.includes(id),
      ),
      ...incoming.slice().sort(),
    ]),
  ];
  const move = (index: number, step: number) => {
    const next = order.slice();
    [next[index], next[index + step]] = [next[index + step], next[index]];
    onChange({ merge_order: next });
  };
  return (
    <div className="space-y-4">
      <label className="block">
        合并候选上限
        <input
          type="number"
          min="1"
          max="40"
          aria-label="合并候选上限"
          value={node.parameters.candidate_limit ?? 40}
          onChange={(e) =>
            onChange({
              candidate_limit: Math.max(
                1,
                Math.min(40, Number(e.target.value) || 1),
              ),
            })
          }
          className="mt-2 w-full rounded-lg border border-slate-200 p-2"
        />
      </label>
      <label className="block">
        不同费用或口径
        <select
          aria-label="合并费用冲突策略"
          value={node.parameters.merge_cost_policy ?? "unknown"}
          onChange={(e) =>
            onChange({
              merge_cost_policy: e.target.value as "unknown" | "first",
            })
          }
          className="mt-2 w-full rounded-lg border border-slate-200 p-2"
        >
          <option value="unknown">保留未知，待核实</option>
          <option value="first">沿用优先来源</option>
        </select>
      </label>
      <div aria-label="合并来源优先级">
        <p className="font-medium">来源优先级</p>
        {!order.length && (
          <p className="mt-2 text-slate-500">先连接1至8份候选池。</p>
        )}
        <ol className="mt-2 space-y-2">
          {order.map((id, i) => (
            <li key={id} className="rounded border border-slate-200 p-2">
              <p>
                {i + 1}. {graph.nodes.find((n) => n.id === id)?.title ?? id}
              </p>
              <div className="mt-1 flex gap-3">
                <button
                  type="button"
                  disabled={i === 0}
                  aria-label={`${id}优先级上移`}
                  onClick={() => move(i, -1)}
                  className="text-teal-700 disabled:text-slate-300"
                >
                  上移
                </button>
                <button
                  type="button"
                  disabled={i === order.length - 1}
                  aria-label={`${id}优先级下移`}
                  onClick={() => move(i, 1)}
                  className="text-teal-700 disabled:text-slate-300"
                >
                  下移
                </button>
              </div>
            </li>
          ))}
        </ol>
      </div>
      <p className="leading-6 text-slate-500">
        按显示顺序取舍，调整后重跑本节点应用。保留优先来源的名称、理由、停留和引用；其他建议可在合并记录核对。明确指向不同地图地点的同名候选分别保留，超过上限的也保留取舍记录。
      </p>
    </div>
  );
}
