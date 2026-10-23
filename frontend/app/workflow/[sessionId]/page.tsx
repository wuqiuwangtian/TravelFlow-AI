"use client";

import { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";
import {
  ArrowDown,
  ArrowRight,
  MapPin,
  Route,
  Search,
  SlidersHorizontal,
} from "lucide-react";
import { api } from "@/lib/api";
import type {
  SessionDetail,
  WorkflowNodeId,
  WorkflowNodeState,
  WorkflowView,
} from "@/lib/types";

const STATES: Record<WorkflowNodeState, { label: string; color: string }> = {
  waiting: { label: "等待前置", color: "bg-slate-700 text-slate-200" },
  needs_input: { label: "待补充／确认", color: "bg-amber-100 text-amber-950" },
  available: { label: "已有数据", color: "bg-teal-100 text-teal-950" },
  ready: { label: "可继续", color: "bg-sky-100 text-sky-950" },
  interrupted: { label: "记录中断", color: "bg-red-100 text-red-900" },
  unknown: { label: "记录不足", color: "bg-slate-200 text-slate-700" },
};
const ICONS = {
  collector: MapPin,
  researcher: Search,
  planner: Route,
  refiner: SlidersHorizontal,
};
interface Selection {
  branch: string;
  version: string;
}

export default function WorkflowPage() {
  const { sessionId } = useParams<{ sessionId: string }>();
  const [detail, setDetail] = useState<SessionDetail | null>(null);
  const [selection, setSelection] = useState<Selection | null>(null);
  const [data, setData] = useState<WorkflowView | null>(null);
  const [selectedNode, setSelectedNode] = useState<WorkflowNodeId | null>(null);
  const [metaBusy, setMetaBusy] = useState(true);
  const [busy, setBusy] = useState(true);
  const [metaError, setMetaError] = useState("");
  const [error, setError] = useState("");
  const [refresh, setRefresh] = useState(0);

  useEffect(() => {
    let cancelled = false;
    api
      .getSession(sessionId)
      .then((result) => {
        if (cancelled) return;
        setDetail(result);
        const params = new URLSearchParams(window.location.search);
        const requested = params.get("branch");
        const branch = requested
          ? result.branches.find((item) => item.id === requested)
          : (result.branches.find((item) => item.is_primary) ??
            result.branches[0]);
        if (!branch) {
          setSelection(null);
          setMetaError(
            requested
              ? "指定方案不存在或不属于本次旅行，请重新选择。"
              : "此旅行还没有可查看的方案。",
          );
        } else {
          setSelection({
            branch: branch.id,
            version: params.get("version") || "",
          });
          setMetaError("");
        }
      })
      .catch((err) => {
        if (!cancelled)
          setMetaError(err instanceof Error ? err.message : "旅行信息加载失败");
      })
      .finally(() => {
        if (!cancelled) setMetaBusy(false);
      });
    return () => {
      cancelled = true;
    };
  }, [sessionId, refresh]);

  useEffect(() => {
    if (!selection) return;
    let cancelled = false;
    api
      .workflow(selection.branch, selection.version)
      .then((result) => {
        if (!cancelled) {
          setData(result);
          setError("");
          setSelectedNode((prev) => prev ?? result.focus_node);
        }
      })
      .catch((err) => {
        if (!cancelled)
          setError(err instanceof Error ? err.message : "流程加载失败");
      })
      .finally(() => {
        if (!cancelled) setBusy(false);
      });
    return () => {
      cancelled = true;
    };
  }, [selection]);

  function choose(next: Selection) {
    const params = new URLSearchParams({ branch: next.branch });
    if (next.version) params.set("version", next.version);
    window.history.replaceState(
      null,
      "",
      `${window.location.pathname}?${params}`,
    );
    setData(null);
    setSelectedNode(null);
    setBusy(true);
    setMetaError("");
    setError("");
    setSelection(next);
  }
  function reload() {
    setData(null);
    setSelectedNode(null);
    setMetaBusy(true);
    setBusy(true);
    setError("");
    setRefresh(refresh + 1);
  }
  const versions = selection ? (detail?.versions[selection.branch] ?? []) : [];
  const selected = data?.nodes.find((node) => node.id === selectedNode) ?? null;

  return (
    <div className="mx-auto max-w-6xl px-5 py-8">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <p className="text-xs tracking-widest text-teal-700">
            高级流程 · 只读
          </p>
          <h1 className="mt-2 text-2xl font-bold text-teal-950">规划节点</h1>
          <p className="mt-3 max-w-2xl text-sm leading-7 text-slate-500">
            查看当前规划的数据如何串起来。节点展示已有输入和输出，选择节点或切换版本只影响查看内容；调整仍在原对话完成。
          </p>
        </div>
        <div className="flex flex-wrap gap-4 text-sm text-teal-800">
          <Link
            href={`/chat/${sessionId}${selection ? `?branch=${selection.branch}` : ""}`}
          >
            回到当前方案
          </Link>
          <button disabled={metaBusy || busy} onClick={reload}>
            刷新流程
          </button>
        </div>
      </div>

      {detail && (
        <div className="mt-6 rounded-2xl border border-teal-100 bg-white p-5">
          <p className="break-words text-sm font-semibold text-slate-800">
            {detail.session.title}
            {detail.session.archived ? " · 会话已归档" : ""}
          </p>
          <div className="mt-4 grid gap-4 sm:grid-cols-2">
            <label className="text-xs text-slate-600">
              选择方案
              <select
                aria-label="选择方案"
                disabled={metaBusy}
                value={selection?.branch ?? ""}
                onChange={(e) =>
                  choose({ branch: e.target.value, version: "" })
                }
                className="mt-2 block w-full min-w-0 rounded-lg border border-slate-200 bg-white p-3 text-sm"
              >
                {!selection && <option value="">请选择有效方案</option>}
                {detail.branches.map((branch) => (
                  <option key={branch.id} value={branch.id}>
                    {branch.title}
                    {branch.is_primary ? " · 主方案" : ""}
                  </option>
                ))}
              </select>
            </label>
            <label className="text-xs text-slate-600">
              查看版本
              <select
                aria-label="查看版本"
                disabled={metaBusy || !selection}
                value={selection?.version ?? ""}
                onChange={(e) =>
                  selection && choose({ ...selection, version: e.target.value })
                }
                className="mt-2 block w-full min-w-0 rounded-lg border border-slate-200 bg-white p-3 text-sm"
              >
                <option value="">当前过程</option>
                {!!selection?.version &&
                  !versions.some(
                    (version) => version.id === selection.version,
                  ) && (
                    <option value={selection.version}>所选版本不可用</option>
                  )}
                {[...versions].reverse().map((version) => (
                  <option key={version.id} value={version.id}>
                    第{version.seq}版 · {version.summary}
                  </option>
                ))}
              </select>
            </label>
          </div>
        </div>
      )}
      {(metaError || error) && (
        <div
          role="alert"
          className="mt-5 rounded-xl bg-amber-50 p-4 text-sm leading-7 text-amber-900"
        >
          {metaError || error}
          <button
            onClick={reload}
            disabled={metaBusy}
            className="ml-3 underline"
          >
            重新载入流程
          </button>
        </div>
      )}
      {!metaError && !error && (metaBusy || busy) && (
        <p role="status" className="mt-6 text-sm text-slate-500">
          正在读取规划流程…
        </p>
      )}

      {!metaBusy && !busy && !metaError && !error && data && (
        <>
          <section
            aria-label="流程数据来源"
            className="mt-5 rounded-xl bg-teal-50 p-4 text-xs leading-7 text-teal-900"
          >
            <p className="font-semibold">
              {data.branch.title} ·{" "}
              {data.historical
                ? `第${data.version?.seq}版固定快照`
                : "当前过程"}
              （只读）
            </p>
            <p>
              {data.source === "checkpoint"
                ? "来源：本方案的过程记录"
                : data.source === "saved_version"
                  ? "来源：已保存版本，执行过程未完整保留"
                  : "尚未开始对话，仅展示创建时的初始参考"}
              {!data.historical && data.version
                ? ` · 最近保存第${data.version.seq}版`
                : ""}
            </p>
            <p>
              读取于 {new Date(data.observed_at).toLocaleString("zh-CN")}
              {data.checkpoint_at
                ? ` · 过程记录保存于 ${new Date(data.checkpoint_at).toLocaleString("zh-CN")}`
                : ""}
            </p>
            <p>
              “已有数据”表示内容已保留，不代表营业、价格或全部执行步骤已核实；本页不是完整运行日志。
            </p>
            {data.busy && (
              <p className="mt-2 font-medium text-amber-800">
                此方案正在生成或更新，当前显示的是已取得的数据，可稍后刷新。
              </p>
            )}
            {data.warnings.map((warning) => (
              <p key={warning} className="mt-2 text-amber-800">
                {warning}
              </p>
            ))}
          </section>

          <section
            aria-label="规划流程节点"
            className="mt-6 overflow-hidden rounded-2xl bg-slate-900 p-5 sm:p-7"
            style={{
              backgroundImage: "radial-gradient(#334155 1px, transparent 1px)",
              backgroundSize: "18px 18px",
            }}
          >
            <p className="mb-5 text-xs leading-6 text-slate-300">
              需求齐全 → 检索资料 → 生成路书；生成后等待下一轮对话确认或修改。
            </p>
            <ol className="grid gap-5 xl:grid-cols-4">
              {data.nodes.map((node, index) => {
                const Icon = ICONS[node.id];
                const state = STATES[node.state];
                const output =
                  node.outputs.find(
                    (item) =>
                      item.label ===
                      (node.id === "collector"
                        ? "仍需补充"
                        : node.id === "researcher"
                          ? "已保留引用"
                          : node.id === "planner"
                            ? "结构化行程"
                            : "待确认操作"),
                  ) ?? node.outputs[0];
                return (
                  <li key={node.id} className="relative min-w-0">
                    <button
                      aria-label={node.title}
                      aria-pressed={selectedNode === node.id}
                      onClick={() => setSelectedNode(node.id)}
                      className={`relative w-full rounded-xl border-2 p-4 text-left text-white transition focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-teal-300 ${selectedNode === node.id ? "border-teal-300 bg-slate-700" : "border-slate-600 bg-slate-800 hover:border-slate-400"}`}
                    >
                      <span className="flex items-center justify-between gap-2">
                        <Icon className="h-5 w-5 text-teal-300" />
                        <span className="text-xs text-slate-400">
                          0{index + 1}
                        </span>
                      </span>
                      <span className="mt-4 block text-sm font-semibold">
                        {node.title}
                      </span>
                      <span
                        className={`mt-3 inline-block rounded-full px-2.5 py-1 text-xs font-medium ${state.color}`}
                      >
                        {state.label}
                      </span>
                      <span className="mt-3 block break-words text-xs leading-6 text-slate-200">
                        {output
                          ? `${output.label}：${output.value}`
                          : "暂无保留输出"}
                      </span>
                      <span
                        aria-hidden
                        className="absolute top-7 -left-1.5 hidden h-2.5 w-2.5 rounded-full border-2 border-teal-300 bg-slate-900 xl:block"
                      />
                      <span
                        aria-hidden
                        className="absolute top-7 -right-1.5 hidden h-2.5 w-2.5 rounded-full border-2 border-teal-300 bg-slate-900 xl:block"
                      />
                    </button>
                    {index < data.nodes.length - 1 && (
                      <span
                        aria-hidden
                        className="mt-3 flex justify-center text-teal-300 xl:absolute xl:top-6 xl:-right-5 xl:mt-0"
                      >
                        <ArrowDown className="h-4 w-4 xl:hidden" />
                        <ArrowRight className="hidden h-4 w-4 xl:block" />
                      </span>
                    )}
                  </li>
                );
              })}
            </ol>
          </section>

          {selected && (
            <section
              aria-label="节点详情"
              className="mt-6 rounded-2xl border border-teal-100 bg-white p-5 sm:p-7"
            >
              <div className="flex flex-wrap items-center justify-between gap-3">
                <h2 className="text-lg font-semibold text-teal-950">
                  {selected.title}
                </h2>
                <span
                  className={`rounded-full px-3 py-1 text-xs ${STATES[selected.state].color}`}
                >
                  {STATES[selected.state].label}
                </span>
              </div>
              <p className="mt-3 text-sm leading-7 text-slate-500">
                {selected.description}
              </p>
              <div className="mt-5 grid gap-6 md:grid-cols-[1fr_2fr]">
                <div>
                  <h3 className="text-sm font-semibold text-slate-800">
                    输入依据
                  </h3>
                  <ul className="mt-3 space-y-2 text-sm leading-7 text-slate-600">
                    {selected.inputs.map((item) => (
                      <li key={item} className="rounded-lg bg-slate-50 p-3">
                        {item}
                      </li>
                    ))}
                  </ul>
                </div>
                <div className="min-w-0">
                  <h3 className="text-sm font-semibold text-slate-800">
                    已有输出与记录
                  </h3>
                  <dl className="mt-3 divide-y divide-slate-100 text-sm">
                    {selected.outputs.map((item) => (
                      <div
                        key={item.label}
                        className="grid gap-1 py-3 sm:grid-cols-[120px_minmax(0,1fr)] sm:gap-4"
                      >
                        <dt className="text-slate-500">{item.label}</dt>
                        <dd className="min-w-0 break-words text-slate-800">
                          {item.value}
                        </dd>
                      </div>
                    ))}
                  </dl>
                </div>
              </div>
              <div className="mt-5 rounded-xl bg-slate-50 p-4 text-xs leading-7 text-slate-600">
                {selected.notes.map((note) => (
                  <p key={note}>{note}</p>
                ))}
              </div>
              <h3 className="mt-5 text-sm font-semibold text-slate-800">
                如何继续
              </h3>
              <ul className="mt-3 space-y-2 text-xs leading-7 text-slate-600">
                {data.edges
                  .filter((edge) => edge.source === selected.id)
                  .map((edge) => (
                    <li key={`${edge.target}-${edge.trigger}`}>
                      {edge.label} →{" "}
                      {
                        data.nodes.find((node) => node.id === edge.target)
                          ?.title
                      }
                      （
                      {edge.trigger === "automatic"
                        ? "原流程自动衔接"
                        : "等待下一条对话"}
                      ）
                    </li>
                  ))}
              </ul>
              <div className="mt-5 flex flex-wrap gap-5 text-sm text-teal-800">
                <Link href={`/chat/${sessionId}?branch=${data.branch.id}`}>
                  回原对话继续
                </Link>
                {data.version && (
                  <a href={api.exportUrl(data.version.id, "html")} download>
                    {data.historical
                      ? "导出所选版本 HTML"
                      : "导出最近保存版本 HTML"}
                  </a>
                )}
              </div>
            </section>
          )}
        </>
      )}
    </div>
  );
}
