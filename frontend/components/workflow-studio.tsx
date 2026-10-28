"use client";

import StudioBudgetControls from "@/components/studio-budget-controls";
import StudioAllocationControls from "@/components/studio-allocation-controls";
import StudioMergeControls from "@/components/studio-merge-controls";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  Focus,
  Settings2,
  FileText,
  Plus,
  Play,
  Save,
  Undo2,
} from "lucide-react";
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
  UserTemplateSummary,
} from "@/lib/studio-types";
import StudioCanvas from "./studio-canvas";
import StudioAdoption from "./studio-adoption";
import StudioData from "./studio-data";
import StudioLibrary from "./studio-library";
import DocumentActions from "./document-actions";
import { ActionFeedback, Button, PendingHint } from "./ui";
import { scrollToElement } from "@/lib/utils";

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
  const [transfer, setTransfer] = useState<{
    pending?: string;
    error?: string;
    message?: string;
  }>({});
  const transferLock = useRef(false);
  const stopLock = useRef(false);
  const [stopState, setStopState] = useState<{
    pending?: string;
    error?: string;
    message?: string;
  }>({});
  const executionGeneration = useRef(0);
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
  const toolbar = useRef<HTMLDivElement>(null);
  const templateArea = useRef<HTMLDivElement>(null);
  const errorArea = useRef<HTMLDivElement>(null);
  const stage = useRef<HTMLDivElement>(null);
  const inspectorScroll = useRef<HTMLDivElement>(null);
  const parameterSection = useRef<HTMLDivElement>(null);
  const artifactSection = useRef<HTMLDivElement>(null);
  const [inspectorPart, setInspectorPart] = useState<"parameters" | "artifact">(
    "parameters",
  );
  useEffect(() => {
    const element = toolbar.current;
    if (!element) return;
    const update = () =>
      element
        .closest<HTMLElement>(".studio-workbench")
        ?.style.setProperty(
          "--studio-toolbar-height",
          `${element.getBoundingClientRect().height}px`,
        );
    const observer = new ResizeObserver(update);
    observer.observe(element);
    update();
    return () => observer.disconnect();
  }, []);
  useEffect(() => {
    if (error && window.matchMedia("(max-width: 900px)").matches) {
      scrollToElement(errorArea.current, { block: "nearest" });
      errorArea.current?.focus({ preventScroll: true });
    }
  }, [error]);
  function focusStage() {
    requestAnimationFrame(() => {
      scrollToElement(stage.current, { block: "start" });
      stage.current
        ?.querySelector<HTMLElement>('[aria-label="自由节点画布"]')
        ?.focus({ preventScroll: true });
    });
  }
  const scrollInspector = useCallback(
    (part: "parameters" | "artifact", navigate = false) => {
      const target =
        part === "parameters"
          ? parameterSection.current
          : artifactSection.current;
      const viewport = inspectorScroll.current;
      if (!target || !viewport) return;
      if (window.matchMedia("(min-width: 901px)").matches) {
        viewport.scrollTo({
          top:
            viewport.scrollTop +
            target.getBoundingClientRect().top -
            viewport.getBoundingClientRect().top,
          behavior: window.matchMedia("(prefers-reduced-motion: reduce)")
            .matches
            ? "instant"
            : "smooth",
        });
      } else if (navigate) scrollToElement(target, { block: "start" });
    },
    [],
  );
  function inspect(part: "parameters" | "artifact", navigate = false) {
    setInspectorPart(part);
    scrollInspector(part, navigate);
  }
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
  useEffect(() => {
    // 切换节点保留当前检查位置；手机选中或拖动节点不会跳离画布。
    if (window.matchMedia("(min-width: 901px)").matches)
      scrollInspector(inspectorPart);
  }, [selected, snapshot, inspectorPart, scrollInspector]);
  function state(item: StudioNode) {
    const entry = activeRun?.nodes.find((result) => result.node_id === item.id);
    if (stale.has(item.id) && activeRun)
      return { label: "输入已改 · 待更新", color: "text-warning" };
    return {
      label: entry ? NODE_STATES[entry.status] : "待运行",
      color:
        entry?.status === "failed"
          ? "text-danger"
          : entry?.status === "running"
            ? "text-accent"
            : "text-accent",
    };
  }
  function change(next: StudioGraph, remember = true) {
    if (disabled) return;
    const previous = graphRef.current;
    if (remember) setUndo((items) => [...items.slice(-29), previous]);
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
    focusStage();
  }
  async function loadNamedTemplate(template: UserTemplateSummary) {
    setBusy("template");
    setError("");
    try {
      const next = await api.loadUserTemplate(
        branchId,
        template.id,
        template.revision,
      );
      if (!alive.current) return;
      // 先加载草稿；保留撤销入口，不自动保存、执行或采用正式行程。
      const previous = graphRef.current;
      setUndo((items) => [...items.slice(-29), previous]);
      setGraph(next);
      graphRef.current = next;
      setSelected(
        next.nodes.find((n) => n.kind === "input")?.id ??
          next.nodes[0]?.id ??
          null,
      );
      setFitToken((v) => v + 1);
      setGallery(false);
      setNotice(
        `已载入模板「${template.title}」修订${template.revision}，${template.trip_input_count ? "已填入本次旅行需求" : "固定输入与指令已保留"}。工程尚未保存，可撤销。`,
      );
      focusStage();
    } finally {
      if (alive.current) setBusy("");
    }
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
    focusStage();
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
    executionGeneration.current++;
    setStopState({});
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
  async function stop() {
    if (stopLock.current) return;
    const id =
      runs.find((run) => run.status === "running")?.id ??
      requestRef.current?.id;
    if (!id) {
      setStopState({ message: "正在保存工程，运行开始后可停止。" });
      return;
    }
    stopLock.current = true;
    const generation = executionGeneration.current;
    setStopState({ pending: "正在请求停止运行…" });
    try {
      const result = await api.stopStudio(branchId, id);
      if (alive.current && generation === executionGeneration.current)
        setStopState({ message: result.message });
    } catch (err) {
      if (alive.current && generation === executionGeneration.current)
        setStopState({ error: showError(err) });
    } finally {
      stopLock.current = false;
    }
  }
  async function importGraph(file: File) {
    if (transferLock.current) return;
    if (file.size > 250 * 1024) {
      setTransfer({ error: "工程 JSON 超过250KB，请减少节点正文。" });
      return;
    }
    transferLock.current = true;
    setTransfer({ pending: "正在校验并导入工程…" });
    setBusy("import");
    setError("");
    try {
      const result = await api.validateStudio(JSON.parse(await file.text()));
      if (alive.current) {
        const previous = graphRef.current;
        setUndo((items) => [...items.slice(-29), previous]);
        setGraph(result);
        setSelected(result.nodes[0]?.id ?? null);
        setFitToken((v) => v + 1);
        setGallery(false);
        setTransfer({
          message: `已导入「${result.title}」，工程尚未保存，可撤销。`,
        });
      }
    } catch (err) {
      if (alive.current)
        setTransfer({
          error: `${showError(err)} 原工程已保留，可重新选择文件。`,
        });
    } finally {
      transferLock.current = false;
      if (alive.current) setBusy("");
    }
  }
  return (
    <section aria-label="节点工程" className="studio-workbench mt-6 min-w-0">
      <div ref={toolbar} className="studio-toolbar">
        <div className="min-w-0">
          <input
            aria-label="工程名称"
            value={graph.title}
            maxLength={80}
            disabled={disabled}
            onChange={(e) => change({ ...graph, title: e.target.value })}
            className="w-full max-w-72 rounded border border-transparent px-2 py-1 text-sm font-semibold text-ink focus:border-accent-line"
          />
          <p className="mt-1 text-xs text-muted">
            <span className="studio-project-state">
              {project
                ? `修订 ${project.revision} · ${dirty ? "有未保存修改" : "已保存"}`
                : "读取工程…"}
            </span>{" "}
            · 成稿输出可整理为采用预览，确认后同步正式行程。
          </p>
        </div>
        <div className="flex flex-wrap gap-2 text-xs">
          <Button
            size="sm"
            disabled={disabled}
            onClick={() => {
              setGallery(!gallery);
              if (!gallery)
                requestAnimationFrame(() =>
                  scrollToElement(templateArea.current, { block: "start" }),
                );
            }}
            className="rounded-lg border border-line px-3 py-2"
          >
            模板库
          </Button>
          <Button
            size="sm"
            disabled={disabled || !undo.length}
            onClick={() => {
              const previous = undo.at(-1);
              if (previous) {
                setGraph(previous);
                graphRef.current = previous;
                setUndo((items) => items.slice(0, -1));
                setFitToken((v) => v + 1);
              }
            }}
            className="rounded-lg border border-line px-3 py-2 disabled:opacity-40"
          >
            <Undo2 size={14} aria-hidden="true" /> 撤销
          </Button>
          <Button
            size="sm"
            loading={busy === "save"}
            loadingLabel="正在保存…"
            disabled={disabled || !dirty}
            onClick={saveClick}
            className="rounded-lg border border-accent px-3 py-2 text-accent disabled:opacity-40"
          >
            <Save size={14} aria-hidden="true" /> 保存工程
          </Button>
          <Button
            size="sm"
            variant="primary"
            loading={busy === "run" || running}
            loadingLabel="工程运行中…"
            disabled={disabled || !graph.nodes.length}
            onClick={() => execute(null)}
            className="rounded-lg bg-accent px-3 py-2 font-medium text-accent-ink disabled:opacity-40"
          >
            <Play size={14} aria-hidden="true" /> 运行全部
          </Button>
          {(running || busy === "run") && (
            <button
              disabled={!!stopState.pending}
              aria-busy={!!stopState.pending}
              className="rounded-lg border border-danger-line px-3 py-2 text-danger"
              onClick={() => void stop()}
            >
              {stopState.pending ? "正在请求停止…" : "停止运行"}
            </button>
          )}
        </div>
        <div className="studio-toolbar-feedback">
          <ActionFeedback
            pending={stopState.pending}
            error={stopState.error}
            message={stopState.message}
            onRetry={stopState.error ? () => void stop() : undefined}
          />
          {(busy === "run" || running) && (
            <PendingHint message="仍在等待运行结果，可查看已有内容；需要中断时使用“停止运行”。" />
          )}
          {error && (
            <div
              role="alert"
              ref={errorArea}
              tabIndex={-1}
              className="mt-3 rounded-lg bg-danger-soft p-4 text-sm leading-6 text-danger"
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
            <p role="status" className="mt-3 text-sm text-accent">
              {notice}
            </p>
          )}
        </div>
      </div>
      <div ref={templateArea} className="studio-template-area">
        <StudioLibrary
          key={branchId}
          graph={graph}
          visible={gallery || !graph.nodes.length}
          disabled={disabled}
          onLoad={loadNamedTemplate}
        />
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
                className="rounded-lg border border-accent-line bg-surface p-5 text-left transition hover:border-accent disabled:opacity-50"
              >
                <span className="text-xs tracking-widest text-accent">
                  可编辑模板 · {template.graph.nodes.length} 个节点
                </span>
                <span className="mt-2 block font-semibold text-ink">
                  {template.title}
                </span>
                <span className="mt-2 block text-xs leading-6 text-muted">
                  {template.description}
                </span>
              </button>
            ))}
          </div>
        )}
      </div>
      <nav aria-label="工程查看位置" className="studio-view-nav">
        <Button size="sm" variant="ghost" onClick={focusStage}>
          <Focus size={14} aria-hidden="true" /> 画布
        </Button>
        <Button
          size="sm"
          variant="ghost"
          onClick={() => {
            scrollToElement(document.getElementById("studio-inspector"), {
              block: "start",
            });
          }}
        >
          <Settings2 size={14} aria-hidden="true" /> 参数与产物
        </Button>
        <Button
          size="sm"
          variant="ghost"
          onClick={() =>
            scrollToElement(document.getElementById("studio-tools"), {
              block: "start",
            })
          }
        >
          <Plus size={14} aria-hidden="true" /> 添加节点
        </Button>
      </nav>
      <aside
        aria-label="节点工具箱"
        id="studio-tools"
        className="studio-toolbox"
      >
        <h2 className="px-1 text-xs font-semibold text-ink">节点工具箱</h2>
        <div className="studio-node-palette">
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
              title={item.subtitle}
              className="studio-add-node"
            >
              <span className="block text-xs font-medium text-ink">
                ＋ {item.title}
              </span>
              <span className="sr-only">{item.subtitle}</span>
            </button>
          ))}
        </div>
        <div className="studio-transfer-tools">
          <button
            onClick={() => {
              try {
                download(
                  "旅行节点工程.json",
                  JSON.stringify(graph, null, 2),
                  "application/json",
                );
                setTransfer({
                  message: "已发起工程 JSON 下载，请在浏览器下载列表中核对。",
                });
              } catch {
                setTransfer({ error: "工程下载未能发起，请重试。" });
              }
            }}
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
        <ActionFeedback
          pending={transfer.pending}
          error={transfer.error}
          message={transfer.message}
        />
        <p className="studio-toolbox-hint">
          把一项生成任务拆成几步。一个输出可连接多个节点，一个节点可接收多路上下文。模板加载后仍可改线和参数。
        </p>
      </aside>
      <div className="studio-layout">
        <div ref={stage} id="studio-stage" className="studio-stage">
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
                const previous = dragOrigin.current;
                setUndo((items) => [...items.slice(-29), previous]);
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
        </div>
        <aside
          aria-label="节点参数与产物"
          id="studio-inspector"
          className="studio-inspector"
        >
          <header className="studio-inspector-heading">
            <h2 className="text-sm font-semibold text-ink">
              {snapshot ? "运行快照 · 只读" : "节点检查"}
            </h2>
            <label className="mt-3 block text-xs text-muted">
              当前节点
              <select
                aria-label="当前节点"
                value={selected ?? ""}
                onChange={(e) => setSelected(e.target.value)}
                className="mt-2 w-full rounded-lg border border-line p-2"
              >
                <option value="">选择节点</option>
                {displayGraph.nodes.map((item) => (
                  <option key={item.id} value={item.id}>
                    {item.title}
                  </option>
                ))}
              </select>
            </label>
            <nav aria-label="检查区定位" className="studio-inspector-nav">
              <button
                type="button"
                disabled={!node}
                aria-pressed={inspectorPart === "parameters"}
                onClick={() => inspect("parameters", true)}
              >
                <Settings2 size={14} aria-hidden="true" /> 参数
              </button>
              <button
                type="button"
                disabled={!node}
                aria-pressed={inspectorPart === "artifact"}
                onClick={() => inspect("artifact", true)}
              >
                <FileText size={14} aria-hidden="true" /> 产物
              </button>
            </nav>
          </header>
          <div ref={inspectorScroll} className="studio-inspector-scroll">
            {node ? (
              <>
                <div
                  ref={parameterSection}
                  id="studio-parameters"
                  className="studio-parameter-section"
                >
                  <p className="mt-2 text-xs text-muted">
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
                        className="mt-2 w-full rounded-lg border border-line p-2"
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
                          className="mt-2 min-h-36 w-full rounded-lg border border-line p-3 leading-6"
                        />
                      </label>
                    )}
                    {["generate", "plan", "search", "candidates"].includes(
                      node.kind,
                    ) && (
                      <label className="block">
                        {node.kind === "search"
                          ? "补充检索词"
                          : "本节点生成指令"}
                        <textarea
                          aria-label="节点指令"
                          maxLength={6000}
                          value={node.parameters.prompt}
                          onChange={(e) =>
                            parameters({ prompt: e.target.value })
                          }
                          className="mt-2 min-h-32 w-full rounded-lg border border-line p-3 leading-6"
                        />
                      </label>
                    )}
                    {["generate", "plan", "candidates"].includes(node.kind) && (
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
                          className="mt-2 w-full rounded-lg border border-line p-2"
                        />
                      </label>
                    )}
                    {node.kind === "candidates" && (
                      <label className="block">
                        候选数量上限
                        <input
                          type="number"
                          min="1"
                          max="40"
                          aria-label="候选数量上限"
                          value={node.parameters.candidate_limit ?? 12}
                          onChange={(e) =>
                            parameters({
                              candidate_limit: Math.max(
                                1,
                                Math.min(40, Number(e.target.value) || 1),
                              ),
                            })
                          }
                          className="mt-2 w-full rounded-lg border border-line p-2"
                        />
                      </label>
                    )}
                    {node.kind === "allocate" && (
                      <StudioAllocationControls
                        value={node.parameters}
                        onChange={parameters}
                      />
                    )}
                    {node.kind === "merge" && (
                      <StudioMergeControls
                        node={node}
                        graph={displayGraph}
                        onChange={parameters}
                      />
                    )}
                    {node.kind === "locate" && (
                      <div className="space-y-3 leading-6 text-muted">
                        <p>
                          自动按名称、城市和地点类型查询地图，未定位或同名歧义保留待核实；可在产物中指定地图候选，再运行本节点应用。
                        </p>
                        <p>
                          24小时内的明确地点可供多个分日分支及路线节点复用；需要刷新地点时强制重跑本节点，路线节点仍重查实际交通。
                        </p>
                        <p>
                          已指定地点：
                          {
                            Object.values(
                              node.parameters.location_choices ?? {},
                            ).filter(Boolean).length
                          }
                          处；已设置清除
                          {
                            Object.values(
                              node.parameters.location_choices ?? {},
                            ).filter((v) => v === null).length
                          }
                          项。上游指定会继承，可在下方清除。
                        </p>
                        {(!!Object.keys(node.parameters.location_choices ?? {})
                          .length ||
                          !!execution?.artifact?.candidates?.places.some(
                            (p) => p.location?.selected_id,
                          )) && (
                          <button
                            type="button"
                            onClick={() => {
                              const ids = new Set([
                                ...Object.keys(
                                  node.parameters.location_choices ?? {},
                                ),
                                ...(execution?.artifact?.candidates?.places
                                  .filter((p) => p.location?.selected_id)
                                  .map((p) => p.id) ?? []),
                              ]);
                              parameters({
                                location_choices: Object.fromEntries(
                                  [...ids].map((id) => [id, null]),
                                ),
                              });
                            }}
                            className="text-accent underline"
                          >
                            清除所有地点指定
                          </button>
                        )}
                      </div>
                    )}
                    {node.kind === "select" && (
                      <div className="space-y-4">
                        <label className="block">
                          每日景点上限
                          <select
                            aria-label="每日景点上限"
                            value={
                              node.parameters.max_attractions_per_day ?? ""
                            }
                            onChange={(e) =>
                              parameters({
                                max_attractions_per_day: e.target.value
                                  ? Number(e.target.value)
                                  : null,
                              })
                            }
                            className="mt-2 w-full rounded-lg border border-line p-2"
                          >
                            <option value="">不限制</option>
                            {Array.from({ length: 20 }, (_, i) => (
                              <option key={i + 1} value={i + 1}>
                                {i + 1}处
                              </option>
                            ))}
                          </select>
                        </label>
                        <label className="block">
                          必去景点
                          <textarea
                            aria-label="必去景点名单"
                            maxLength={2000}
                            value={node.parameters.required_places ?? ""}
                            onChange={(e) =>
                              parameters({ required_places: e.target.value })
                            }
                            placeholder="每行一个名称或关键词，最多20项"
                            className="mt-2 min-h-20 w-full rounded-lg border border-line p-2"
                          />
                        </label>
                        <label className="block">
                          排除景点
                          <textarea
                            aria-label="排除景点名单"
                            maxLength={2000}
                            value={node.parameters.excluded_places ?? ""}
                            onChange={(e) =>
                              parameters({ excluded_places: e.target.value })
                            }
                            placeholder="只从上游景点中筛选，不会添加新地点"
                            className="mt-2 min-h-20 w-full rounded-lg border border-line p-2"
                          />
                        </label>
                        <label className="block">
                          景点建议停留
                          <select
                            aria-label="景点建议停留"
                            value={
                              node.parameters.attraction_stay_minutes ?? ""
                            }
                            onChange={(e) =>
                              parameters({
                                attraction_stay_minutes: e.target.value
                                  ? Number(e.target.value)
                                  : null,
                              })
                            }
                            className="mt-2 w-full rounded-lg border border-line p-2"
                          >
                            <option value="">沿用上游</option>
                            {[30, 60, 90, 120, 180, 240].map((n) => (
                              <option key={n} value={n}>
                                {n}分钟
                              </option>
                            ))}
                            {node.parameters.attraction_stay_minutes &&
                              ![30, 60, 90, 120, 180, 240].includes(
                                node.parameters.attraction_stay_minutes,
                              ) && (
                                <option
                                  value={
                                    node.parameters.attraction_stay_minutes
                                  }
                                >
                                  {node.parameters.attraction_stay_minutes}分钟
                                </option>
                              )}
                          </select>
                        </label>
                        <p className="leading-6 text-muted">
                          必去与固定项目优先保留，可能超过数量上限。住宿、餐饮、车站及已执行记录不参与删减；找不到必去地点、规则矛盾或筛成空白日会提示修正。调整后请连接路线校验。
                        </p>
                      </div>
                    )}
                    {node.kind === "route" && (
                      <label className="block">
                        路线顺序
                        <select
                          aria-label="路线顺序策略"
                          value={node.parameters.route_order ?? "original"}
                          onChange={(e) =>
                            parameters({
                              route_order: e.target.value as
                                | "original"
                                | "nearby",
                            })
                          }
                          className="mt-2 w-full rounded-lg border border-line p-2"
                        >
                          <option value="original">沿用上游顺序</option>
                          <option value="nearby">减少折返（确认坐标）</option>
                        </select>
                        <p className="mt-2 leading-6 text-muted">
                          每日首站及固定项目作为分段锚点，只重排中间的普通景点。依据直线距离取舍后重新核验交通；无确认坐标时保留顺序，不保证实际用时更短。
                        </p>
                      </label>
                    )}
                    {node.kind === "route" && (
                      <label className="block">
                        市内默认交通
                        <select
                          aria-label="路线默认交通"
                          value={node.parameters.travel_mode ?? "auto"}
                          onChange={(e) =>
                            parameters({
                              travel_mode: e.target
                                .value as StudioNode["parameters"]["travel_mode"],
                            })
                          }
                          className="mt-2 w-full rounded-lg border border-line p-2"
                        >
                          <option value="auto">智能衔接</option>
                          <option value="walking">步行</option>
                          <option value="transit">公共交通</option>
                          <option value="driving">驾车／打车</option>
                        </select>
                        <p className="mt-2 leading-6 text-muted">
                          明确标注的铁路、航班和接驳仍沿用原安排。未配置地图或服务失败时保留建议，不冒充已校验。
                        </p>
                      </label>
                    )}
                    {node.kind === "budget" && (
                      <StudioBudgetControls
                        value={node.parameters.budget_rules}
                        onChange={(budget_rules) =>
                          parameters({ budget_rules })
                        }
                      />
                    )}
                  </fieldset>
                  {!snapshot && (
                    <div className="mt-4 flex flex-wrap gap-3 text-xs">
                      <button
                        disabled={disabled}
                        onClick={() => execute(node.id)}
                        className="rounded bg-accent px-3 py-2 text-accent-ink disabled:opacity-40"
                      >
                        运行到此节点
                      </button>
                      <button
                        disabled={disabled}
                        onClick={() => execute(node.id, true)}
                        className="text-accent disabled:opacity-40"
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
                        className="text-muted"
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
                              (e) =>
                                e.source !== node.id && e.target !== node.id,
                            ),
                          });
                          setSelected(null);
                        }}
                        className="text-danger"
                      >
                        删除节点
                      </button>
                    </div>
                  )}
                </div>
                <div
                  ref={artifactSection}
                  id="studio-artifact"
                  className="studio-artifact-section"
                  role="region"
                  aria-label="节点产物检查"
                >
                  <h3 className="text-xs font-semibold text-ink">节点产物</h3>
                  {stale.has(node.id) && activeRun && (
                    <p className="mt-2 text-xs leading-6 text-warning">
                      输入或上游已变化，旧产物待更新。再次运行会复用输入一致的节点，只重算必要步骤。
                    </p>
                  )}
                  {execution ? (
                    <>
                      <p className="mt-2 text-xs text-muted">
                        {NODE_STATES[execution.status]} · {execution.elapsed_ms}
                        ms
                        {execution.produced_at
                          ? ` · ${new Date(execution.produced_at).toLocaleString("zh-CN")}`
                          : ""}
                      </p>
                      {execution.error && (
                        <p className="mt-3 text-xs leading-6 text-danger">
                          {execution.error}
                        </p>
                      )}
                      {execution.artifact && (
                        <>
                          <StudioData
                            artifact={execution.artifact}
                            locationChoices={
                              node.kind === "locate"
                                ? node.parameters.location_choices
                                : undefined
                            }
                            onLocationChoice={
                              node.kind === "locate" && !disabled
                                ? (id, poi) => {
                                    const choices = {
                                      ...node.parameters.location_choices,
                                    };
                                    choices[id] = poi;
                                    parameters({ location_choices: choices });
                                  }
                                : undefined
                            }
                          />
                          <div
                            aria-label="节点生成内容"
                            className="prose-report mt-4 max-h-[440px] overflow-auto break-words text-xs"
                          >
                            <ReactMarkdown>
                              {execution.artifact.text ||
                                "此节点输出资料集合。"}
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
                                      className="break-words text-accent"
                                    >
                                      {source.title}
                                    </a>
                                  </li>
                                ))}
                              </ul>
                            </details>
                          )}
                          <DocumentActions
                            key={`${activeRun?.id}:${node.id}`}
                            text={execution.artifact.text}
                            markdown={`${execution.artifact.text}\n\n## 资料来源\n${execution.artifact.sources.map((source) => `- [${source.title}](${source.url})`).join("\n")}`}
                          />
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
                    <p className="mt-2 text-xs leading-6 text-muted">
                      本次运行未产生此节点内容。可运行到此节点，或选择其他运行记录查看。
                    </p>
                  )}
                </div>
              </>
            ) : (
              <p className="mt-4 text-xs leading-6 text-muted">
                选择一个节点，编辑这一小步的输入、生成指令与参数，再查看它独立产生的内容。
              </p>
            )}
          </div>
        </aside>
      </div>
      <details className="studio-connections">
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
                {displayGraph.nodes.find((n) => n.id === edge.source)?.title} →{" "}
                {displayGraph.nodes.find((n) => n.id === edge.target)?.title} ·{" "}
                {edge.target_port}
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
                className="text-danger"
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
            className="min-w-0 max-w-full rounded border border-line p-2"
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
            className="min-w-0 max-w-full rounded border border-line p-2"
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
            className="rounded bg-accent-soft px-3 text-accent"
          >
            连接端口
          </button>
        </fieldset>
      </details>
      {!!runs.length && (
        <section
          aria-label="工程运行记录"
          className="studio-run-history mt-4 rounded-lg border border-line bg-surface p-4"
        >
          <div className="flex flex-wrap items-center gap-3">
            <label className="text-xs text-muted">
              运行记录
              <select
                aria-label="运行记录"
                value={activeRun?.id ?? ""}
                onChange={(e) => {
                  setRunId(e.target.value);
                  setSnapshot(false);
                }}
                className="ml-2 max-w-full rounded-lg border border-line p-2"
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
              className="text-xs text-accent"
            >
              {snapshot ? "回到当前工程" : "查看运行时工程"}
            </button>
          </div>
          <p className="mt-3 text-xs leading-6 text-muted">
            保存当时的节点、连线、参数、每步产物与错误。单节点运行会先处理必要上游；输入一致且24小时内的成功产物可复用，强制重跑会更新所选节点。最近展示20次运行。
          </p>
        </section>
      )}
    </section>
  );
}
