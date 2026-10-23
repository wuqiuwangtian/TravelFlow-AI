import type {
  StudioEdge,
  StudioGraph,
  StudioKind,
  StudioNode,
  StudioPort,
} from "./studio-types";

export const NODE_KINDS: Record<
  StudioKind,
  { title: string; subtitle: string; color: string }
> = {
  input: {
    title: "文本输入",
    subtitle: "旅行需求、约束或自备资料",
    color: "#2dd4bf",
  },
  search: {
    title: "资料检索",
    subtitle: "真实搜索，输出带来源的资料",
    color: "#fbbf24",
  },
  generate: {
    title: "AI 生成",
    subtitle: "为这一小步编写独立指令",
    color: "#a78bfa",
  },
  output: {
    title: "成稿输出",
    subtitle: "汇总上游内容，保留来源",
    color: "#38bdf8",
  },
};
export const INPUTS: Record<
  StudioKind,
  { id: StudioEdge["target_port"]; type: StudioPort; title: string }[]
> = {
  input: [],
  search: [{ id: "query", type: "text", title: "查询" }],
  generate: [
    { id: "context", type: "text", title: "上下文" },
    { id: "sources", type: "sources", title: "资料" },
  ],
  output: [
    { id: "content", type: "text", title: "正文" },
    { id: "sources", type: "sources", title: "资料" },
  ],
};
export const OUTPUTS: Record<
  StudioKind,
  { id: StudioPort; type: StudioPort; title: string }[]
> = {
  input: [{ id: "text", type: "text", title: "文本" }],
  search: [{ id: "sources", type: "sources", title: "资料" }],
  generate: [{ id: "text", type: "text", title: "文本" }],
  output: [],
};
export const WIDTH = 242;
export const portY = (index: number) => 89 + index * 28;
export function connectError(graph: StudioGraph, edge: StudioEdge): string {
  const source = graph.nodes.find((n) => n.id === edge.source),
    target = graph.nodes.find((n) => n.id === edge.target);
  if (!source || !target) return "节点已变化，请重新连接。";
  if (source.id === target.id) return "节点不能连接自己。";
  if (
    OUTPUTS[source.kind].find((p) => p.id === edge.source_port)?.type !==
    INPUTS[target.kind].find((p) => p.id === edge.target_port)?.type
  )
    return "端口不兼容：绿色文本接文本，黄色资料接资料。";
  if (graph.edges.some((e) => JSON.stringify(e) === JSON.stringify(edge)))
    return "这两个端口已经连接。";
  const todo = [edge.target],
    visited = new Set<string>();
  while (todo.length) {
    const id = todo.pop()!;
    if (id === edge.source)
      return "这条连线会形成循环，请保持从上游到下游的方向。";
    if (visited.has(id)) continue;
    visited.add(id);
    todo.push(
      ...graph.edges.filter((e) => e.source === id).map((e) => e.target),
    );
  }
  return "";
}
export const semantic = (node: StudioNode) =>
  JSON.stringify({
    kind: node.kind,
    title: node.title,
    parameters: node.parameters,
  });
export function staleNodes(
  graph: StudioGraph,
  previous: StudioGraph | null,
): Set<string> {
  const stale = new Set<string>();
  for (const node of graph.nodes) {
    const old = previous?.nodes.find((n) => n.id === node.id);
    const incoming = (g: StudioGraph) =>
      JSON.stringify(
        g.edges
          .filter((e) => e.target === node.id)
          .map((e) => JSON.stringify(e))
          .sort(),
      );
    if (
      !old ||
      semantic(old) !== semantic(node) ||
      !previous ||
      incoming(graph) !== incoming(previous)
    )
      stale.add(node.id);
  }
  for (let i = 0; i < graph.nodes.length; i++)
    for (const e of graph.edges) if (stale.has(e.source)) stale.add(e.target);
  return stale;
}
export function nodeNew(kind: StudioKind, x: number, y: number): StudioNode {
  return {
    id: `${kind}-${crypto.randomUUID().slice(0, 8)}`,
    kind,
    title: NODE_KINDS[kind].title,
    x,
    y,
    parameters: {
      text: "",
      prompt:
        kind === "generate"
          ? "根据上游输入完成这一小步任务，明确估算与待核实事项。"
          : "",
      temperature: 0.5,
      max_results: 5,
    },
  };
}
export function download(
  name: string,
  text: string,
  mime = "text/plain;charset=utf-8",
) {
  const url = URL.createObjectURL(new Blob([text], { type: mime }));
  const link = document.createElement("a");
  link.href = url;
  link.download = name;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
