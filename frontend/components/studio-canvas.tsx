"use client";

import { useEffect, useRef, useState } from "react";
import { INPUTS, NODE_KINDS, OUTPUTS, WIDTH, portY } from "@/lib/studio-graph";
import type {
  StudioEdge,
  StudioGraph,
  StudioNode,
  StudioPort,
} from "@/lib/studio-types";

type Connector = { node: string; port: StudioPort };
export default function StudioCanvas({
  graph,
  selected,
  disabled,
  fitToken,
  onSelect,
  onMove,
  onConnect,
  onDeleteEdge,
  status,
}: {
  graph: StudioGraph;
  selected: string | null;
  disabled: boolean;
  fitToken: number;
  onSelect: (id: string) => void;
  onMove: (id: string, x: number, y: number, finished: boolean) => void;
  onConnect: (edge: StudioEdge) => void;
  onDeleteEdge: (index: number) => void;
  status: (node: StudioNode) => { label: string; color: string };
}) {
  const container = useRef<HTMLDivElement>(null);
  const [view, setView] = useState({ x: 35, y: 35, zoom: 0.8 });
  const [pending, setPending] = useState<Connector | null>(null);
  const pendingRef = useRef<Connector | null>(null);
  const [cursor, setCursor] = useState({ x: 0, y: 0 });
  const drag = useRef<{
    id: string | null;
    clientX: number;
    clientY: number;
    x: number;
    y: number;
  } | null>(null);
  const graphRef = useRef(graph);
  useEffect(() => {
    graphRef.current = graph;
  }, [graph]);
  useEffect(() => {
    const element = container.current;
    if (!element) return;
    const wheel = (event: WheelEvent) => {
      if (!event.ctrlKey && !event.metaKey) return;
      event.preventDefault();
      const box = element.getBoundingClientRect();
      const x = event.clientX - box.left,
        y = event.clientY - box.top;
      setView((old) => {
        const zoom = Math.max(
          0.2,
          Math.min(1.8, old.zoom * Math.exp(-event.deltaY * 0.002)),
        );
        return {
          zoom,
          x: x - ((x - old.x) * zoom) / old.zoom,
          y: y - ((y - old.y) * zoom) / old.zoom,
        };
      });
    };
    element.addEventListener("wheel", wheel, { passive: false });
    return () => element.removeEventListener("wheel", wheel);
  }, []);
  function fit() {
    const box = container.current?.getBoundingClientRect(),
      nodes = graphRef.current.nodes;
    if (!box || !nodes.length) return;
    const minX = Math.min(...nodes.map((n) => n.x)),
      minY = Math.min(...nodes.map((n) => n.y));
    const width = Math.max(...nodes.map((n) => n.x)) + WIDTH - minX;
    const height = Math.max(...nodes.map((n) => n.y)) + 180 - minY;
    const zoom = Math.max(
      0.2,
      Math.min(1, (box.width - 60) / width, (box.height - 80) / height),
    );
    setView({
      x: (box.width - width * zoom) / 2 - minX * zoom,
      y: (box.height - height * zoom) / 2 - minY * zoom,
      zoom,
    });
  }
  useEffect(() => {
    const observer = new ResizeObserver(fit);
    if (container.current) observer.observe(container.current);
    return () => observer.disconnect();
  }, [fitToken]);
  function zoomBy(factor: number) {
    const box = container.current?.getBoundingClientRect();
    if (!box) return;
    setView((old) => {
      const zoom = Math.max(0.2, Math.min(1.8, old.zoom * factor));
      return {
        zoom,
        x: box.width / 2 - ((box.width / 2 - old.x) * zoom) / old.zoom,
        y: box.height / 2 - ((box.height / 2 - old.y) * zoom) / old.zoom,
      };
    });
  }
  function pickOutput(node: string, port: StudioPort) {
    if (disabled) return;
    const next =
      pendingRef.current?.node === node && pendingRef.current.port === port
        ? null
        : { node, port };
    pendingRef.current = next;
    setPending(next);
    const item = graph.nodes.find((n) => n.id === node)!;
    setCursor({ x: item.x + WIDTH + 70, y: item.y + portY(0) });
  }
  function finish(target: string, target_port: StudioEdge["target_port"]) {
    const from = pendingRef.current;
    if (!from) return;
    onConnect({
      source: from.node,
      source_port: from.port,
      target,
      target_port,
    });
    pendingRef.current = null;
    setPending(null);
  }
  function path(sx: number, sy: number, tx: number, ty: number) {
    const bend = Math.max(65, Math.abs(tx - sx) * 0.45);
    return `M ${sx} ${sy} C ${sx + bend} ${sy}, ${tx - bend} ${ty}, ${tx} ${ty}`;
  }
  return (
    <div className="relative min-w-0 overflow-hidden rounded-2xl border border-slate-700 bg-[#0c1422]">
      <div className="absolute top-3 left-3 z-10 flex max-w-[calc(100%-24px)] flex-wrap gap-2 rounded-lg bg-slate-900/90 p-2 text-xs text-slate-300">
        <button
          aria-label="缩小画布"
          onClick={() => zoomBy(0.8)}
          className="px-2"
        >
          −
        </button>
        <span className="min-w-10 text-center">
          {Math.round(view.zoom * 100)}%
        </span>
        <button
          aria-label="放大画布"
          onClick={() => zoomBy(1.25)}
          className="px-2"
        >
          ＋
        </button>
        <button onClick={fit} className="border-l border-slate-700 px-2">
          适应画布
        </button>
        {selected && (
          <button
            className="border-l border-slate-700 px-2"
            onClick={() => {
              const node = graph.nodes.find((n) => n.id === selected),
                box = container.current?.getBoundingClientRect();
              if (!node || !box) return;
              const zoom = Math.min(1, (box.width - 60) / WIDTH);
              setView({
                zoom,
                x: box.width / 2 - (node.x + WIDTH / 2) * zoom,
                y: box.height / 2 - (node.y + 87) * zoom,
              });
            }}
          >
            聚焦节点
          </button>
        )}
        {pending && (
          <button
            onClick={() => {
              pendingRef.current = null;
              setPending(null);
            }}
            className="text-amber-300"
          >
            取消连线
          </button>
        )}
      </div>
      <div
        ref={container}
        aria-label="自由节点画布"
        role="region"
        tabIndex={0}
        className="h-[520px] touch-none overflow-hidden outline-none sm:h-[650px]"
        style={{
          backgroundImage: "radial-gradient(#334155 1px, transparent 1px)",
          backgroundSize: `${22 * view.zoom}px ${22 * view.zoom}px`,
          backgroundPosition: `${view.x}px ${view.y}px`,
        }}
        onKeyDown={(event) => {
          if (event.key === "Escape") {
            pendingRef.current = null;
            setPending(null);
          }
        }}
        onPointerDown={(event) => {
          if ((event.target as Element).closest("[data-node],button,path"))
            return;
          event.currentTarget.setPointerCapture(event.pointerId);
          drag.current = {
            id: null,
            clientX: event.clientX,
            clientY: event.clientY,
            x: view.x,
            y: view.y,
          };
        }}
        onPointerMove={(event) => {
          const box = container.current?.getBoundingClientRect();
          if (pendingRef.current && box)
            setCursor({
              x: (event.clientX - box.left - view.x) / view.zoom,
              y: (event.clientY - box.top - view.y) / view.zoom,
            });
          const item = drag.current;
          if (!item) return;
          const dx = event.clientX - item.clientX,
            dy = event.clientY - item.clientY;
          if (item.id)
            onMove(
              item.id,
              Math.max(-5000, Math.min(10000, item.x + dx / view.zoom)),
              Math.max(-5000, Math.min(10000, item.y + dy / view.zoom)),
              false,
            );
          else setView((v) => ({ ...v, x: item.x + dx, y: item.y + dy }));
        }}
        onPointerUp={(event) => {
          const item = drag.current;
          if (item?.id) {
            const node = graph.nodes.find((n) => n.id === item.id);
            if (node) onMove(node.id, node.x, node.y, true);
          }
          drag.current = null;
          const target = (event.target as Element).closest<HTMLElement>(
            "[data-input-node]",
          );
          if (target)
            finish(
              target.dataset.inputNode!,
              target.dataset.inputPort as StudioEdge["target_port"],
            );
        }}
        onPointerCancel={() => {
          const item = drag.current,
            node = graph.nodes.find((n) => n.id === item?.id);
          if (node) onMove(node.id, node.x, node.y, true);
          drag.current = null;
        }}
      >
        <div
          className="relative origin-top-left"
          style={{
            transform: `translate(${view.x}px, ${view.y}px) scale(${view.zoom})`,
          }}
        >
          <svg
            className="pointer-events-none absolute top-0 left-0 overflow-visible"
            width="1"
            height="1"
            aria-label="节点连线"
          >
            {graph.edges.map((edge, index) => {
              const source = graph.nodes.find((n) => n.id === edge.source),
                target = graph.nodes.find((n) => n.id === edge.target);
              if (!source || !target) return null;
              const targetIndex = INPUTS[target.kind].findIndex(
                (p) => p.id === edge.target_port,
              );
              const d = path(
                source.x + WIDTH,
                source.y + portY(0),
                target.x,
                target.y + portY(targetIndex),
              );
              return (
                <g key={`${edge.source}-${edge.target}-${edge.target_port}`}>
                  <path
                    d={d}
                    fill="none"
                    stroke={
                      edge.source_port === "sources" ? "#fbbf24" : "#2dd4bf"
                    }
                    strokeWidth="2"
                    opacity=".7"
                  />
                  <path
                    d={d}
                    fill="none"
                    stroke="transparent"
                    strokeWidth="16"
                    className="pointer-events-auto cursor-pointer"
                    tabIndex={0}
                    role="button"
                    aria-label={`删除连线 ${source.title} 到 ${target.title} ${edge.target_port}`}
                    onClick={(e) => {
                      e.stopPropagation();
                      if (!disabled) onDeleteEdge(index);
                    }}
                    onKeyDown={(e) => {
                      if (
                        (e.key === "Delete" || e.key === "Enter") &&
                        !disabled
                      )
                        onDeleteEdge(index);
                    }}
                  />
                </g>
              );
            })}
            {pending &&
              (() => {
                const source = graph.nodes.find((n) => n.id === pending.node);
                return source ? (
                  <path
                    d={path(
                      source.x + WIDTH,
                      source.y + portY(0),
                      cursor.x,
                      cursor.y,
                    )}
                    fill="none"
                    stroke="#e2e8f0"
                    strokeWidth="2"
                    strokeDasharray="5 5"
                  />
                ) : null;
              })()}
          </svg>
          {graph.nodes.map((node) => {
            const kind = NODE_KINDS[node.kind],
              state = status(node);
            return (
              <article
                key={node.id}
                data-node={node.id}
                className={`absolute h-[174px] rounded-xl border bg-slate-800 shadow-xl ${selected === node.id ? "border-teal-300 ring-1 ring-teal-300/30" : "border-slate-600"}`}
                style={{ left: node.x, top: node.y, width: WIDTH }}
              >
                <button
                  aria-label={`选择节点 ${node.title}`}
                  aria-pressed={selected === node.id}
                  className="block w-full cursor-grab rounded-t-xl border-b border-slate-600 px-4 py-3 text-left active:cursor-grabbing"
                  style={{ borderTop: `3px solid ${kind.color}` }}
                  onClick={() => onSelect(node.id)}
                  onPointerDown={(event) => {
                    event.stopPropagation();
                    onSelect(node.id);
                    if (disabled) return;
                    event.currentTarget.setPointerCapture(event.pointerId);
                    drag.current = {
                      id: node.id,
                      clientX: event.clientX,
                      clientY: event.clientY,
                      x: node.x,
                      y: node.y,
                    };
                  }}
                >
                  <span className="block truncate text-xs font-medium text-slate-100">
                    {node.title}
                  </span>
                  <span className="mt-1 block text-[10px] text-slate-400">
                    {kind.title}
                  </span>
                </button>
                {INPUTS[node.kind].map((port, index) => (
                  <div
                    key={port.id}
                    className="absolute left-0 text-[10px] text-slate-300"
                    style={{ top: portY(index) - 10 }}
                  >
                    <button
                      data-input-node={node.id}
                      data-input-port={port.id}
                      aria-label={`${node.title} 输入 ${port.title}`}
                      title={`输入 ${port.title}（${port.type === "sources" ? "资料" : "文本"}）`}
                      onClick={() => finish(node.id, port.id)}
                      disabled={disabled}
                      className="absolute -left-[11px] h-[22px] w-[22px] p-[5px]"
                    >
                      <span
                        className="block h-3 w-3 rounded-full border-2 border-slate-900"
                        style={{
                          backgroundColor:
                            port.type === "sources" ? "#fbbf24" : "#2dd4bf",
                        }}
                      />
                    </button>
                    <span className="ml-4 leading-5">{port.title}</span>
                  </div>
                ))}
                {OUTPUTS[node.kind].map((port) => (
                  <div
                    key={port.id}
                    className="absolute right-0 text-[10px] text-slate-300"
                    style={{ top: portY(0) - 10 }}
                  >
                    <span className="mr-4 leading-5">{port.title}</span>
                    <button
                      disabled={disabled}
                      aria-label={`${node.title} 输出 ${port.title}`}
                      title="拖动到输入端口，或依次点击两个端口"
                      aria-pressed={pending?.node === node.id}
                      onPointerDown={(event) => {
                        event.stopPropagation();
                        pickOutput(node.id, port.id);
                      }}
                      onClick={(event) => {
                        if (event.detail === 0) pickOutput(node.id, port.id);
                      }}
                      className="absolute -right-[11px] h-[22px] w-[22px] p-[5px]"
                    >
                      <span
                        className="block h-3 w-3 rounded-full border-2 border-slate-900"
                        style={{
                          backgroundColor:
                            port.type === "sources" ? "#fbbf24" : "#2dd4bf",
                        }}
                      />
                    </button>
                  </div>
                ))}
                <div className="absolute right-4 bottom-3 left-4 flex items-center justify-between gap-2 text-[10px]">
                  <span className={state.color}>{state.label}</span>
                  <span className="text-slate-500">{node.id.slice(0, 18)}</span>
                </div>
              </article>
            );
          })}
        </div>
      </div>
      <div className="border-t border-slate-700 px-4 py-3 text-[11px] leading-5 text-slate-400">
        拖动节点标题移动 · 拖动背景平移 · Ctrl＋滚轮缩放 ·
        从输出拖到输入，或依次点击端口连接 ·
        点击连线删除。绿色为文本，黄色为资料。
      </div>
    </div>
  );
}
