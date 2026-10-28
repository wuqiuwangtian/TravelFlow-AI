"use client";

import { useEffect, useRef, useState } from "react";
import {
  INPUTS,
  NODE_KINDS,
  OUTPUTS,
  PORT_COLORS,
  PORT_LABELS,
  WIDTH,
  nodeHeight,
  portY,
} from "@/lib/studio-graph";
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
    if (drag.current) return;
    const box = container.current?.getBoundingClientRect(),
      nodes = graphRef.current.nodes;
    if (!box || !nodes.length) return;
    const minX = Math.min(...nodes.map((n) => n.x)),
      minY = Math.min(...nodes.map((n) => n.y));
    const width = Math.max(...nodes.map((n) => n.x)) + WIDTH - minX;
    const height =
      Math.max(...nodes.map((n) => n.y + nodeHeight(n.kind))) - minY;
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
    <div className="studio-canvas-frame">
      <div className="studio-canvas-tools">
        <button
          aria-label="缩小画布"
          onClick={() => zoomBy(0.8)}
          className="min-h-10 min-w-10 px-2"
        >
          −
        </button>
        <span className="min-w-10 text-center">
          {Math.round(view.zoom * 100)}%
        </span>
        <button
          aria-label="放大画布"
          onClick={() => zoomBy(1.25)}
          className="min-h-10 min-w-10 px-2"
        >
          ＋
        </button>
        <button onClick={fit} className="min-h-10 border-l border-line px-2">
          适应画布
        </button>
        {selected && (
          <button
            className="min-h-10 border-l border-line px-2"
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
        <span className="ml-auto">
          {graph.nodes.length} 节点 · {graph.edges.length} 连线
        </span>
        {pending && (
          <button
            onClick={() => {
              pendingRef.current = null;
              setPending(null);
            }}
            className="min-h-10 px-2 text-warning"
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
        className="studio-canvas-viewport"
        style={{
          backgroundImage:
            "radial-gradient(var(--tf-line) 1px, transparent 1px)",
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
                <g
                  className="studio-wire"
                  key={`${edge.source}-${edge.target}-${edge.target_port}`}
                >
                  <path
                    className="studio-wire-line"
                    d={d}
                    fill="none"
                    stroke={PORT_COLORS[edge.source_port]}
                    strokeWidth="2"
                    opacity=".7"
                  />
                  <path
                    d={d}
                    fill="none"
                    stroke="transparent"
                    strokeWidth="16"
                    className="studio-wire-hit"
                    tabIndex={disabled ? -1 : 0}
                    role="button"
                    aria-disabled={disabled}
                    aria-label={`删除连线 ${source.title} 到 ${target.title} ${edge.target_port}`}
                    onClick={(e) => {
                      e.stopPropagation();
                      if (!disabled) onDeleteEdge(index);
                    }}
                    onKeyDown={(e) => {
                      if (
                        (e.key === "Delete" ||
                          e.key === "Enter" ||
                          e.key === " ") &&
                        !disabled
                      ) {
                        e.preventDefault();
                        onDeleteEdge(index);
                      }
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
                    stroke="var(--tf-accent)"
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
                className="studio-canvas-node"
                data-selected={selected === node.id}
                style={{
                  left: node.x,
                  top: node.y,
                  width: WIDTH,
                  height: nodeHeight(node.kind),
                }}
              >
                <button
                  aria-label={`选择节点 ${node.title}`}
                  aria-pressed={selected === node.id}
                  data-spatial="node"
                  data-readonly={disabled}
                  title={
                    disabled
                      ? "查看此节点；当前不能移动或改线"
                      : "拖动标题移动，或用方向键微调位置"
                  }
                  className="studio-node-heading"
                  style={{ borderTop: `3px solid ${kind.color}` }}
                  onKeyDown={(event) => {
                    const delta: Record<string, [number, number]> = {
                      ArrowLeft: [-1, 0],
                      ArrowRight: [1, 0],
                      ArrowUp: [0, -1],
                      ArrowDown: [0, 1],
                    };
                    const direction = delta[event.key];
                    if (disabled || !direction) return;
                    event.preventDefault();
                    onSelect(node.id);
                    const step = event.shiftKey ? 40 : 10;
                    onMove(
                      node.id,
                      Math.max(
                        -5000,
                        Math.min(10000, node.x + direction[0] * step),
                      ),
                      Math.max(
                        -5000,
                        Math.min(10000, node.y + direction[1] * step),
                      ),
                      true,
                    );
                  }}
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
                  <span className="studio-node-title">{node.title}</span>
                  <span className="studio-node-kind">{kind.title}</span>
                </button>
                {INPUTS[node.kind].map((port, index) => (
                  <div
                    key={port.id}
                    className="studio-port-label left-0"
                    style={{ top: portY(index) - 10 }}
                  >
                    <button
                      data-input-node={node.id}
                      data-input-port={port.id}
                      data-spatial="port"
                      aria-label={`${node.title} 输入 ${port.title}`}
                      title={`输入 ${port.title}（${PORT_LABELS[port.type]}）`}
                      onClick={() => finish(node.id, port.id)}
                      disabled={disabled}
                      className="studio-port absolute -left-[11px]"
                    >
                      <span
                        className="block h-3 w-3 rounded-full border-2 border-line"
                        style={{
                          backgroundColor: PORT_COLORS[port.type],
                        }}
                      />
                    </button>
                    <span className="ml-4 leading-5">{port.title}</span>
                  </div>
                ))}
                {OUTPUTS[node.kind].map((port) => (
                  <div
                    key={port.id}
                    className="studio-port-label right-0"
                    style={{ top: portY(0) - 10 }}
                  >
                    <span className="mr-4 leading-5">{port.title}</span>
                    <button
                      disabled={disabled}
                      aria-label={`${node.title} 输出 ${port.title}`}
                      title="拖动到输入端口，或依次点击两个端口"
                      aria-pressed={pending?.node === node.id}
                      data-spatial="port"
                      onPointerDown={(event) => {
                        event.stopPropagation();
                        pickOutput(node.id, port.id);
                      }}
                      onClick={(event) => {
                        if (event.detail === 0) pickOutput(node.id, port.id);
                      }}
                      className="studio-port absolute -right-[11px]"
                    >
                      <span
                        className="block h-3 w-3 rounded-full border-2 border-line"
                        style={{
                          backgroundColor: PORT_COLORS[port.type],
                        }}
                      />
                    </button>
                  </div>
                ))}
                <div className="studio-node-footer">
                  <span className={state.color}>{state.label}</span>
                  <span className="text-muted">{node.id.slice(0, 18)}</span>
                </div>
              </article>
            );
          })}
        </div>
      </div>
      <div className="studio-canvas-footer" aria-live="polite">
        {pending
          ? "已选择输出端口，请连接同类型输入；按 Esc 取消。"
          : "拖动标题移动节点，或聚焦标题后用方向键移动（Shift 加快）。拖动背景平移，Ctrl＋滚轮缩放。"}
        <p>
          从输出拖到输入或依次点击端口；点击连线删除。也可使用下方连线列表与键盘连接。
        </p>
        <div className="flex flex-wrap gap-x-3">
          {Object.entries(PORT_LABELS).map(([key, label]) => (
            <span key={key} style={{ color: PORT_COLORS[key as StudioPort] }}>
              {label}
            </span>
          ))}
        </div>
      </div>
    </div>
  );
}
