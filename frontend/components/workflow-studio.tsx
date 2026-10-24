"use client";

import { useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import { api, ApiError } from "@/lib/api";
import {
  INPUTS,
  NODE_KINDS,
  OUTPUTS,
  connectError,
  download,
  nodeNew,
  staleNodes,
} from "@/lib/studio-graph";
import type {
  StudioEdge,
  StudioGraph,
  StudioKind,
  StudioNode,
  StudioProject,
  StudioRun,
  StudioTemplate,
} from "@/lib/studio-types";
import StudioCanvas from "./studio-canvas";
import StudioAdoption from "./studio-adoption";

const empty = (): StudioGraph => ({
  schema_version: 1,
  title: "我的规划工程",
  nodes: [],
  edges: [],
});
const showError = (error: unknown) =>
  error instanceof Error ? error.message : "操作失败，请重试。";
const RUN_STATES = {
  running: "运行中",
  succeeded: "已完成",
  failed: "部分失败",
  interrupted: "已中断",
};
const NODE_STATES = {
  waiting: "待运行",
  running: "生成中",
  succeeded: "已完成",
  cached: "复用产物",
  failed: "运行失败",
  blocked: "上游阻塞",
};

export default function WorkflowStudio({
  branchId,
  sessionId,
}: {
  branchId: string;
  sessionId: string;
}) {
  const [project, setProject] = useState<StudioProject | null>(null);
  const [graph, setGraph] = useState<StudioGraph>(empty);
  const graphRef = useRef(graph);
  useEffect(() => {
    graphRef.current = graph;
  }, [graph]);
  const [templates, setTemplates] = useState<StudioTemplate[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [runs, setRuns] = useState<StudioRun[]>([]);
  const [runId, setRunId] = useState("");
  const [snapshot, setSnapshot] = useState(false);
  const [gallery, setGallery] = useState(false);
  const [busy, setBusy] = useState("load");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [fitToken, setFitToken] = useState(0);
  const [refresh, setRefresh] = useState(0);
  const [undo, setUndo] = useState<StudioGraph[]>([]);
  const [linkSource, setLinkSource] = useState("");
  const [linkTarget, setLinkTarget] = useState("");
  const alive = useRef(true);
  const ignoreDraft = useRef(false);
  const dragOrigin = useRef<StudioGraph | null>(null);
  const requestRef = useRef<{ key: string; id: string } | null>(null);
  const importFile = useRef<HTMLInputElement>(null);
  const storageKey = `travelflow:studio-draft:${branchId}`;
  useEffect(() => {
    alive.current = true;
    let cancelled = false;
    Promise.all([api.studioProject(branchId), api.studioTemplates()])
      .then(([result, presets]) => {
        if (cancelled) return;
        let draft: StudioGraph | null = null;
        let expected = result.revision;
        if (!ignoreDraft.current) {
          try {
            const saved = JSON.parse(
              sessionStorage.getItem(storageKey) || "null",
            );
            if (saved?.graph) {
              // 本页写入的草稿只恢复到当前页；服务端仍会严格校验结构与修订。
              draft = saved.graph;
              expected = saved.revision;
              if (expected !== result.revision)
                setError(
                  "其他窗口已更新工程，已保留本页草稿。请先导出草稿，再载入最新工程核对。",
                );
              else setNotice("已恢复本页尚未保存的工程草稿。");
            }
          } catch {
            /* 存储不可用时仍可编辑服务器工程。 */
          }
        }
        const next = draft ?? result.graph ?? empty();
        setProject({ ...result, revision: expected });
        setGraph(next);
        setTemplates(presets);
        setRuns(result.runs);
        setRunId(result.runs[0]?.id ?? "");
        setSnapshot(false);
        setSelected(next.nodes[0]?.id ?? null);
        setGallery(!next.nodes.length);
        setFitToken((v) => v + 1);
        ignoreDraft.current = false;
      })
      .catch((err) => {
        if (!cancelled) setError(showError(err));
      })
      .finally(() => {
        if (!cancelled) setBusy("");
      });
    return () => {
      cancelled = true;
      alive.current = false;
    };
  }, [branchId, storageKey, refresh]);
  const dirty =
    !!project &&
    JSON.stringify(graph) !== JSON.stringify(project.graph ?? empty());
  useEffect(() => {
    if (!project || busy === "load") return;
    try {
      if (dirty)
        sessionStorage.setItem(
          storageKey,
          JSON.stringify({ graph, revision: project.revision }),
        );
      else sessionStorage.removeItem(storageKey);
    } catch {
      /* 服务端保存仍可用。 */
    }
  }, [graph, project, dirty, storageKey, busy]);

  const running = runs[0]?.status === "running";
  useEffect(() => {
    if (!running && busy !== "run") return;
    let cancelled = false;
    const timer = window.setInterval(() => {
      api
        .studioProject(branchId)
        .then((result) => {
          if (cancelled) return;
          setRuns(result.runs);
          if (result.runs[0]) setRunId(result.runs[0].id);
        })
        .catch(() => {
          /* 保留当前进度；原运行请求或下次轮询会给出结果。 */
        });
    }, 1200);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [branchId, running, busy]);
  const disabled = !!busy || running || snapshot;
  const activeRun = runs.find((run) => run.id === runId) ?? runs[0] ?? null;
  const displayGraph = snapshot && activeRun ? activeRun.graph : graph;
  const node = displayGraph.nodes.find((item) => item.id === selected) ?? null;
  const stale = staleNodes(displayGraph, activeRun?.graph ?? null);
  if (activeRun?.target_node && !snapshot) {
    const entry = activeRun.nodes.find(
      (n) => n.node_id === activeRun.target_node,
    );
    if (entry?.status === "succeeded") {
      const todo = [activeRun.target_node],
        visited = new Set<string>();
      while (todo.length) {
        const source = todo.pop()!;
        if (visited.has(source)) continue;
        visited.add(source);
        for (const edge of graph.edges.filter((e) => e.source === source)) {
          stale.add(edge.target);
          todo.push(edge.target);
        }
      }
    }
  }
  const execution =
    activeRun?.nodes.find((item) => item.node_id === node?.id) ?? null;
  function state(item: StudioNode) {
    const entry = activeRun?.nodes.find((result) => result.node_id === item.id);
    if (stale.has(item.id) && activeRun)
      return { label: "输入已改 · 待更新", color: "text-amber-300" };
    return {
      label: entry ? NODE_STATES[entry.status] : "待运行",
      color:
        entry?.status === "failed"
          ? "text-red-300"
          : entry?.status === "running"
            ? "text-sky-300"
            : "text-teal-300",
    };
  }
  function change(next: StudioGraph, remember = true) {
    if (disabled) return;
    if (remember) setUndo((items) => [...items.slice(-29), graphRef.current]);
    setGraph(next);
    graphRef.current = next;
    setNotice("");
    setError("");
  }
  function parameters(patch: Partial<StudioNode["parameters"]>) {
    if (!node) return;
    change({
      ...graph,
      nodes: graph.nodes.map((n) =>
        n.id === node.id
          ? { ...n, parameters: { ...n.parameters, ...patch } }
          : n,
      ),
    });
  }
  function connect(edge: StudioEdge) {
    if (disabled) return;
    const problem = connectError(graph, edge);
    if (problem) {
      setError(problem);
      return;
    }
    change({ ...graph, edges: [...graph.edges, edge] });
    setError("");
  }
  function preset(template: StudioTemplate) {
    const next = structuredClone(template.graph);
    const input = next.nodes.find((n) => n.kind === "input");
    if (input) input.parameters.text = project?.requirements_seed || "";
    change(next);
    setSelected(input?.id ?? null);
    setFitToken((v) => v + 1);
    setGallery(false);
    setError("");
  }
  function add(kind: StudioKind) {
    if (graph.nodes.length >= 20) {
      setError("当前工程最多20个节点，可以拆成多个生成任务。");
      return;
    }
    const origin = graph.nodes.find((n) => n.id === selected);
    const next = nodeNew(
      kind,
      origin ? Math.min(origin.x + 300, 9500) : 60,
      origin ? Math.min(origin.y + 90, 9500) : 100,
    );
    change({ ...graph, nodes: [...graph.nodes, next] });
    setSelected(next.id);
    setFitToken((v) => v + 1);
  }
  async function save(): Promise<number | null> {
    if (!project) return null;
    if (!dirty) return project.revision;
    const result = await api.saveStudio(
      branchId,
      graphRef.current,
      project.revision,
    );
    if (alive.current) {
      setProject({ ...result, requirements_seed: project.requirements_seed });
      setNotice("工程已保存。");
    }
    return result.revision;
  }
  async function saveClick() {
    setBusy("save");
    setError("");
    try {
      await save();
    } catch (err) {
      if (alive.current) setError(showError(err));
    } finally {
      if (alive.current) setBusy("");
    }
  }
  async function execute(target: string | null, force = false) {
    setBusy("run");
    setError("");
    setSnapshot(false);
    try {
      const revision = await save();
      if (revision === null) return;
      const key = JSON.stringify([revision, target, force]);
      if (requestRef.current?.key !== key)
        requestRef.current = { key, id: crypto.randomUUID() };
      const result = await api.runStudio(branchId, {
        request_id: requestRef.current.id,
        expected_revision: revision,
        target_node: target,
        force,
      });
      requestRef.current = null;
      if (alive.current) {
        setRuns((old) =>
          [result, ...old.filter((r) => r.id !== result.id)].slice(0, 20),
        );
        setRunId(result.id);
        setNotice(
          result.status === "succeeded"
            ? "工程运行完成，点击节点查看独立产物。"
            : "运行记录已保存，请查看失败或阻塞节点，再重跑相关步骤。",
        );
      }
    } catch (err) {
      if (alive.current) {
        setError(showError(err));
        if (
          err instanceof ApiError &&
          err.status !== 0 &&
          err.code !== "BACKEND_UNREACHABLE" &&
          err.code !== "BACKEND_TIMEOUT"
        )
          requestRef.current = null;
      }
    } finally {
      if (alive.current) setBusy("");
    }
  }
  async function importGraph(file: File) {
    if (file.size > 250 * 1024) {
      setError("工程 JSON 超过250KB，请减少节点正文。");
      return;
    }
    setBusy("import");
    setError("");
    try {
      const result = await api.validateStudio(JSON.parse(await file.text()));
      if (alive.current) {
        setUndo((items) => [...items.slice(-29), graphRef.current]);
        setGraph(result);
        setSelected(result.nodes[0]?.id ?? null);
        setFitToken((v) => v + 1);
        setGallery(false);
      }
    } catch (err) {
      if (alive.current) setError(showError(err));
    } finally {
      if (alive.current) setBusy("");
    }
  }
  function exportArtifact() {
    if (!execution?.artifact) return;
    const { text, sources } = execution.artifact;
    download(
      "旅行工程成稿.md",
      `${text}\n\n## 资料来源\n${sources.map((s) => `- [${s.title}](${s.url})`).join("\n")}`,
    );
  }
  return (
    <section aria-label="节点工程" className="mt-6 min-w-0">
      <div className="flex flex-wrap items-center justify-between gap-3 rounded-t-2xl border border-slate-200 bg-white p-4">
        <div className="min-w-0">
          <input
            aria-label="工程名称"
            value={graph.title}
            maxLength={80}
            disabled={disabled}
            onChange={(e) => change({ ...graph, title: e.target.value })}
            className="w-full max-w-72 rounded border border-transparent px-2 py-1 text-sm font-semibold text-slate-800 focus:border-teal-300"
          />
          <p className="mt-1 text-xs text-slate-500">
            {project
              ? `修订 ${project.revision} · ${dirty ? "有未保存修改" : "已保存"}`
              : "读取工程…"}{" "}
            · 成稿输出可整理为采用预览，确认后同步正式行程。
          </p>
        </div>
        <div className="flex flex-wrap gap-2 text-xs">
          <button
            disabled={disabled}
            onClick={() => setGallery(!gallery)}
            className="rounded-lg border border-slate-200 px-3 py-2"
          >
            模板库
          </button>
          <button
            disabled={disabled || !undo.length}
            onClick={() => {
              const previous = undo.at(-1);
              if (previous) {
                setGraph(previous);
                setUndo((items) => items.slice(0, -1));
                setFitToken((v) => v + 1);
              }
            }}
            className="rounded-lg border border-slate-200 px-3 py-2 disabled:opacity-40"
          >
            撤销
          </button>
          <button
            disabled={disabled || !dirty}
            onClick={saveClick}
            className="rounded-lg border border-teal-600 px-3 py-2 text-teal-700 disabled:opacity-40"
          >
            保存工程
          </button>
          <button
            disabled={disabled || !graph.nodes.length}
            onClick={() => execute(null)}
            className="rounded-lg bg-teal-700 px-3 py-2 font-medium text-white disabled:opacity-40"
          >
            {busy === "run" || running ? "工程运行中…" : "运行全部"}
          </button>
          {(running || busy === "run") && (
            <button
              className="rounded-lg border border-red-200 px-3 py-2 text-red-700"
              onClick={async () => {
                const id =
                  runs.find((r) => r.status === "running")?.id ??
                  requestRef.current?.id;
                if (!id) return;
                try {
                  const result = await api.stopStudio(branchId, id);
                  setNotice(result.message);
                } catch (err) {
                  setError(showError(err));
                }
              }}
            >
              停止运行
            </button>
          )}
        </div>
      </div>
      {error && (
        <div
          role="alert"
          className="mt-3 rounded-xl bg-red-50 p-4 text-sm leading-6 text-red-800"
        >
          {error}
          <button
            className="ml-4 underline"
            onClick={() => {
              ignoreDraft.current = true;
              setBusy("load");
              setError("");
              setUndo([]);
              setRefresh((v) => v + 1);
            }}
          >
            载入最新工程
          </button>
        </div>
      )}
      {notice && (
        <p role="status" className="mt-3 text-sm text-teal-700">
          {notice}
        </p>
      )}
      {(gallery || !graph.nodes.length) && (
        <div
          aria-label="默认工程模板"
          role="region"
          className="mt-4 grid gap-3 md:grid-cols-3"
        >
          {templates.map((template) => (
            <button
              disabled={disabled}
              key={template.id}
              onClick={() => preset(template)}
              className="rounded-xl border border-teal-100 bg-white p-5 text-left transition hover:border-teal-500 disabled:opacity-50"
            >
              <span className="text-xs tracking-widest text-teal-600">
                可编辑模板 · {template.graph.nodes.length} 个节点
              </span>
              <span className="mt-2 block font-semibold text-slate-800">
                {template.title}
              </span>
              <span className="mt-2 block text-xs leading-6 text-slate-500">
                {template.description}
              </span>
            </button>
          ))}
        </div>
      )}
      <div className="mt-4 grid min-w-0 gap-4 xl:grid-cols-[176px_minmax(0,1fr)_310px]">
        <aside
          aria-label="节点工具箱"
          className="min-w-0 rounded-xl border border-slate-200 bg-white p-3"
        >
          <h2 className="px-1 text-xs font-semibold text-slate-700">
            节点工具箱
          </h2>
          <div className="mt-3 grid grid-cols-2 gap-2 xl:grid-cols-1">
            {(
              Object.entries(NODE_KINDS) as [
                StudioKind,
                (typeof NODE_KINDS)[StudioKind],
              ][]
            ).map(([kind, item]) => (
              <button
                key={kind}
                disabled={disabled}
                onClick={() => add(kind)}
                className="rounded-lg border border-slate-100 px-3 py-3 text-left hover:border-teal-300 disabled:opacity-40"
              >
                <span className="block text-xs font-medium text-slate-800">
                  ＋ {item.title}
                </span>
                <span className="mt-1 block text-[10px] leading-5 text-slate-500">
                  {item.subtitle}
                </span>
              </button>
            ))}
          </div>
          <div className="mt-4 flex flex-wrap gap-3 border-t border-slate-100 pt-3 text-xs text-teal-700">
            <button
              onClick={() =>
                download(
                  "旅行节点工程.json",
                  JSON.stringify(graph, null, 2),
                  "application/json",
                )
              }
            >
              导出工程 JSON
            </button>
            <button
              disabled={disabled}
              onClick={() => importFile.current?.click()}
            >
              导入工程 JSON
            </button>
            <input
              ref={importFile}
              type="file"
              accept="application/json,.json"
              aria-label="导入工程文件"
              className="hidden"
              onChange={(e) => {
                const file = e.target.files?.[0];
                if (file) void importGraph(file);
                e.target.value = "";
              }}
            />
          </div>
          <p className="mt-4 text-[11px] leading-6 text-slate-400">
            把一项生成任务拆成几步。一个输出可连接多个节点，一个节点可接收多路上下文。模板加载后仍可改线和参数。
          </p>
        </aside>
        <div className="min-w-0">
          <StudioCanvas
            graph={displayGraph}
            selected={selected}
            disabled={disabled}
            fitToken={fitToken}
            onSelect={setSelected}
            onMove={(id, x, y, finished) => {
              if (disabled) return;
              if (!dragOrigin.current) dragOrigin.current = graphRef.current;
              const next = {
                ...graphRef.current,
                nodes: graphRef.current.nodes.map((n) =>
                  n.id === id ? { ...n, x, y } : n,
                ),
              };
              change(next, false);
              if (finished) {
                setUndo((items) => [...items.slice(-29), dragOrigin.current!]);
                dragOrigin.current = null;
              }
            }}
            onConnect={connect}
            onDeleteEdge={(index) =>
              change({
                ...graph,
                edges: graph.edges.filter((_, i) => i !== index),
              })
            }
            status={state}
          />
          <details className="mt-3 rounded-xl border border-slate-200 bg-white p-4 text-xs text-slate-600">
            <summary className="cursor-pointer">
              连线列表与键盘连接 · {displayGraph.edges.length} 条
            </summary>
            <ul className="mt-3 space-y-2">
              {displayGraph.edges.map((edge, index) => (
                <li
                  key={`${edge.source}-${edge.target}-${edge.target_port}`}
                  className="flex flex-wrap items-center justify-between gap-2"
                >
                  <span>
                    {
                      displayGraph.nodes.find((n) => n.id === edge.source)
                        ?.title
                    }{" "}
                    →{" "}
                    {
                      displayGraph.nodes.find((n) => n.id === edge.target)
                        ?.title
                    }{" "}
                    · {edge.target_port}
                  </span>
                  <button
                    disabled={disabled}
                    onClick={() =>
                      change({
                        ...graph,
                        edges: graph.edges.filter((_, i) => i !== index),
                      })
                    }
                    aria-label={`移除连线 ${index + 1}`}
                    className="text-red-600"
                  >
                    移除
                  </button>
                </li>
              ))}
            </ul>
            <fieldset disabled={disabled} className="mt-4 flex flex-wrap gap-2">
              <select
                aria-label="连线输出"
                value={linkSource}
                onChange={(e) => setLinkSource(e.target.value)}
                className="min-w-0 max-w-full rounded border border-slate-200 p-2"
              >
                <option value="">选择输出端口</option>
                {graph.nodes.flatMap((n) =>
                  OUTPUTS[n.kind].map((p) => (
                    <option key={`${n.id}:${p.id}`} value={`${n.id}:${p.id}`}>
                      {n.title} · {p.title}
                    </option>
                  )),
                )}
              </select>
              <select
                aria-label="连线输入"
                value={linkTarget}
                onChange={(e) => setLinkTarget(e.target.value)}
                className="min-w-0 max-w-full rounded border border-slate-200 p-2"
              >
                <option value="">选择输入端口</option>
                {graph.nodes.flatMap((n) =>
                  INPUTS[n.kind].map((p) => (
                    <option key={`${n.id}:${p.id}`} value={`${n.id}:${p.id}`}>
                      {n.title} · {p.title}
                    </option>
                  )),
                )}
              </select>
              <button
                disabled={!linkSource || !linkTarget}
                onClick={() => {
                  const [source, source_port] = linkSource.split(":");
                  const [target, target_port] = linkTarget.split(":");
                  connect({
                    source,
                    target,
                    source_port: source_port as StudioEdge["source_port"],
                    target_port: target_port as StudioEdge["target_port"],
                  });
                }}
                className="rounded bg-teal-50 px-3 text-teal-800"
              >
                连接端口
              </button>
            </fieldset>
          </details>
        </div>
        <aside
          aria-label="节点参数与产物"
          className="min-w-0 rounded-xl border border-slate-200 bg-white p-4"
        >
          <h2 className="text-sm font-semibold text-slate-800">
            {snapshot ? "运行快照 · 只读" : "节点参数"}
          </h2>
          <label className="mt-3 block text-xs text-slate-500">
            当前节点
            <select
              aria-label="当前节点"
              value={selected ?? ""}
              onChange={(e) => setSelected(e.target.value)}
              className="mt-2 w-full rounded-lg border border-slate-200 p-2"
            >
              <option value="">选择节点</option>
              {displayGraph.nodes.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.title}
                </option>
              ))}
            </select>
          </label>
          {node ? (
            <>
              <p className="mt-2 text-xs text-slate-500">
                {NODE_KINDS[node.kind].subtitle}
              </p>
              <fieldset
                disabled={disabled}
                className="mt-4 space-y-4 text-xs disabled:opacity-60"
              >
                <label className="block">
                  节点名称
                  <input
                    aria-label="节点名称"
                    maxLength={60}
                    value={node.title}
                    onChange={(e) =>
                      change({
                        ...graph,
                        nodes: graph.nodes.map((n) =>
                          n.id === node.id
                            ? { ...n, title: e.target.value }
                            : n,
                        ),
                      })
                    }
                    className="mt-2 w-full rounded-lg border border-slate-200 p-2"
                  />
                </label>
                {node.kind === "input" && (
                  <label className="block">
                    输入内容
                    <textarea
                      aria-label="节点输入内容"
                      maxLength={12000}
                      value={node.parameters.text}
                      onChange={(e) => parameters({ text: e.target.value })}
                      placeholder="可以从一个自然语言需求开始，例如：上海出发，三天，预算2000元，想看博物馆…"
                      className="mt-2 min-h-36 w-full rounded-lg border border-slate-200 p-3 leading-6"
                    />
                  </label>
                )}
                {(node.kind === "generate" || node.kind === "search") && (
                  <label className="block">
                    {node.kind === "search" ? "补充检索词" : "本节点生成指令"}
                    <textarea
                      aria-label="节点指令"
                      maxLength={6000}
                      value={node.parameters.prompt}
                      onChange={(e) => parameters({ prompt: e.target.value })}
                      className="mt-2 min-h-32 w-full rounded-lg border border-slate-200 p-3 leading-6"
                    />
                  </label>
                )}
                {node.kind === "generate" && (
                  <label className="block">
                    生成温度 {node.parameters.temperature}
                    <input
                      aria-label="生成温度"
                      type="range"
                      min="0"
                      max="2"
                      step="0.1"
                      value={node.parameters.temperature}
                      onChange={(e) =>
                        parameters({ temperature: Number(e.target.value) })
                      }
                      className="mt-2 w-full"
                    />
                  </label>
                )}
                {node.kind === "search" && (
                  <label className="block">
                    检索条数
                    <input
                      aria-label="检索条数"
                      type="number"
                      min="1"
                      max="8"
                      value={node.parameters.max_results}
                      onChange={(e) =>
                        parameters({
                          max_results: Math.max(
                            1,
                            Math.min(8, Number(e.target.value) || 1),
                          ),
                        })
                      }
                      className="mt-2 w-full rounded-lg border border-slate-200 p-2"
                    />
                  </label>
                )}
              </fieldset>
              {!snapshot && (
                <div className="mt-4 flex flex-wrap gap-3 text-xs">
                  <button
                    disabled={disabled}
                    onClick={() => execute(node.id)}
                    className="rounded bg-teal-700 px-3 py-2 text-white disabled:opacity-40"
                  >
                    运行到此节点
                  </button>
                  <button
                    disabled={disabled}
                    onClick={() => execute(node.id, true)}
                    className="text-teal-700 disabled:opacity-40"
                  >
                    强制重跑此节点
                  </button>
                  <button
                    disabled={disabled || graph.nodes.length >= 20}
                    onClick={() => {
                      const copy = {
                        ...structuredClone(node),
                        id: `${node.kind}-${crypto.randomUUID().slice(0, 8)}`,
                        title: `${node.title} 副本`.slice(0, 60),
                        x: Math.min(node.x + 60, 10000),
                        y: Math.min(node.y + 200, 10000),
                      };
                      change({ ...graph, nodes: [...graph.nodes, copy] });
                      setSelected(copy.id);
                      setFitToken((v) => v + 1);
                    }}
                    className="text-slate-600"
                  >
                    复制节点
                  </button>
                  <button
                    disabled={disabled}
                    onClick={() => {
                      change({
                        ...graph,
                        nodes: graph.nodes.filter((n) => n.id !== node.id),
                        edges: graph.edges.filter(
                          (e) => e.source !== node.id && e.target !== node.id,
                        ),
                      });
                      setSelected(null);
                    }}
                    className="text-red-600"
                  >
                    删除节点
                  </button>
                </div>
              )}
              <div className="mt-5 border-t border-slate-100 pt-4">
                <h3 className="text-xs font-semibold text-slate-700">
                  节点产物
                </h3>
                {stale.has(node.id) && activeRun && (
                  <p className="mt-2 text-xs leading-6 text-amber-700">
                    输入或上游已变化，旧产物待更新。再次运行会复用输入一致的节点，只重算必要步骤。
                  </p>
                )}
                {execution ? (
                  <>
                    <p className="mt-2 text-xs text-slate-500">
                      {NODE_STATES[execution.status]} · {execution.elapsed_ms}ms
                      {execution.produced_at
                        ? ` · ${new Date(execution.produced_at).toLocaleString("zh-CN")}`
                        : ""}
                    </p>
                    {execution.error && (
                      <p className="mt-3 text-xs leading-6 text-red-700">
                        {execution.error}
                      </p>
                    )}
                    {execution.artifact && (
                      <>
                        <div
                          aria-label="节点生成内容"
                          className="prose-report mt-4 max-h-[440px] overflow-auto break-words text-xs"
                        >
                          <ReactMarkdown>
                            {execution.artifact.text || "此节点输出资料集合。"}
                          </ReactMarkdown>
                        </div>
                        {!!execution.artifact.sources.length && (
                          <details className="mt-3 text-xs">
                            <summary>
                              资料来源 · {execution.artifact.sources.length}
                            </summary>
                            <ul className="mt-2 space-y-2">
                              {execution.artifact.sources.map((source) => (
                                <li key={source.url}>
                                  <a
                                    href={source.url}
                                    target="_blank"
                                    rel="noreferrer"
                                    className="break-words text-teal-700"
                                  >
                                    {source.title}
                                  </a>
                                </li>
                              ))}
                            </ul>
                          </details>
                        )}
                        <div className="mt-4 flex gap-3 text-xs text-teal-700">
                          <button onClick={exportArtifact}>
                            下载此节点 Markdown
                          </button>
                          <button
                            onClick={async () => {
                              try {
                                await navigator.clipboard.writeText(
                                  execution.artifact!.text,
                                );
                                setNotice("节点正文已复制。");
                              } catch {
                                setError("剪贴板不可用，可下载 Markdown。");
                              }
                            }}
                          >
                            复制正文
                          </button>
                        </div>
                        {node.kind === "output" &&
                          activeRun &&
                          activeRun.status !== "running" &&
                          ["succeeded", "cached"].includes(
                            execution.status,
                          ) && (
                            <StudioAdoption
                              key={`${branchId}-${activeRun.id}-${node.id}`}
                              sessionId={sessionId}
                              branchId={branchId}
                              run={activeRun}
                              nodeId={node.id}
                            />
                          )}
                      </>
                    )}
                  </>
                ) : (
                  <p className="mt-2 text-xs leading-6 text-slate-400">
                    本次运行未产生此节点内容。可运行到此节点，或选择其他运行记录查看。
                  </p>
                )}
              </div>
            </>
          ) : (
            <p className="mt-4 text-xs leading-6 text-slate-500">
              选择一个节点，编辑这一小步的输入、生成指令与参数，再查看它独立产生的内容。
            </p>
          )}
        </aside>
      </div>
      {!!runs.length && (
        <section
          aria-label="工程运行记录"
          className="mt-4 rounded-xl border border-slate-200 bg-white p-4"
        >
          <div className="flex flex-wrap items-center gap-3">
            <label className="text-xs text-slate-600">
              运行记录
              <select
                aria-label="运行记录"
                value={activeRun?.id ?? ""}
                onChange={(e) => {
                  setRunId(e.target.value);
                  setSnapshot(false);
                }}
                className="ml-2 max-w-full rounded-lg border border-slate-200 p-2"
              >
                {runs.map((run, index) => (
                  <option key={run.id} value={run.id}>
                    {index === 0 ? "最近 · " : ""}
                    {new Date(run.created_at).toLocaleString("zh-CN")} · 修订
                    {run.revision} · {RUN_STATES[run.status]}
                    {run.target_node ? " · 局部" : " · 全部"}
                  </option>
                ))}
              </select>
            </label>
            <button
              disabled={!!busy || running}
              onClick={() => {
                setSnapshot(!snapshot);
                setFitToken((v) => v + 1);
              }}
              className="text-xs text-teal-700"
            >
              {snapshot ? "回到当前工程" : "查看运行时工程"}
            </button>
          </div>
          <p className="mt-3 text-xs leading-6 text-slate-500">
            保存当时的节点、连线、参数、每步产物与错误。单节点运行会先处理必要上游；输入一致且24小时内的成功产物可复用，强制重跑会更新所选节点。最近展示20次运行。
          </p>
        </section>
      )}
    </section>
  );
}
