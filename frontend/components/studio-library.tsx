"use client";

import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { api } from "@/lib/api";
import type {
  StudioGraph,
  UserTemplate,
  UserTemplateSummary,
} from "@/lib/studio-types";

type Editor = {
  source: UserTemplate | null;
  graph: StudioGraph;
  metadataOnly: boolean;
};
const message = (err: unknown) =>
  err instanceof Error ? err.message : "操作失败，请重试。";

export default function StudioLibrary({
  graph,
  visible,
  disabled,
  onLoad,
}: {
  graph: StudioGraph;
  visible: boolean;
  disabled: boolean;
  onLoad: (template: UserTemplateSummary) => Promise<void>;
}) {
  const [items, setItems] = useState<UserTemplateSummary[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [listError, setListError] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState("");
  const [query, setQuery] = useState("");
  const [editor, setEditor] = useState<Editor | null>(null);
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [tripIds, setTripIds] = useState<string[]>([]);
  const [deleting, setDeleting] = useState<{
    item: UserTemplateSummary;
    id: string;
  } | null>(null);
  const [refresh, setRefresh] = useState(0);
  const retry = useRef<{ key: string; id: string } | null>(null);
  const alive = useRef(true);
  const dialog = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);
  useEffect(() => {
    let cancelled = false;
    api
      .userTemplates()
      .then((result) => {
        if (!cancelled) {
          setItems(result);
          setListError("");
        }
      })
      .catch((err) => {
        if (!cancelled) setListError(message(err));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [refresh]);
  useEffect(() => {
    const element = dialog.current;
    if (editor && element && !element.open) element.showModal();
    return () => {
      if (element?.open) element.close();
    };
  }, [editor]);
  function reloadList() {
    setLoading(true);
    setRefresh((v) => v + 1);
  }
  function open(
    source: UserTemplate | null,
    current: StudioGraph,
    metadataOnly = false,
  ) {
    setEditor({ source, graph: structuredClone(current), metadataOnly });
    setTitle(source?.title ?? current.title);
    setDescription(source?.description ?? "");
    setTripIds(
      metadataOnly && source
        ? source.trip_input_ids
        : current.nodes.filter((n) => n.kind === "input").map((n) => n.id),
    );
    setError("");
    setNotice("");
    retry.current = null;
  }
  async function edit(item: UserTemplateSummary, metadataOnly: boolean) {
    setBusy(item.id);
    setError("");
    const captured = structuredClone(graph);
    try {
      const source = await api.userTemplate(item.id);
      if (alive.current)
        open(source, metadataOnly ? source.graph : captured, metadataOnly);
    } catch (err) {
      if (alive.current) setError(message(err));
    } finally {
      if (alive.current) setBusy("");
    }
  }
  async function save() {
    if (!editor) return;
    const body = {
      expected_revision: editor.source?.revision ?? 0,
      title: title.trim(),
      description,
      graph: editor.graph,
      trip_input_ids: tripIds,
    };
    const key = JSON.stringify([editor.source?.id, body]);
    if (retry.current?.key !== key)
      retry.current = { key, id: crypto.randomUUID() };
    setBusy("save");
    setError("");
    try {
      const saved = await api.saveUserTemplate(
        { ...body, request_id: retry.current.id },
        editor.source?.id,
      );
      if (alive.current) {
        // 列表只保留摘要，运行、来源旅行和当前工程均不受这次保存影响。
        const {
          graph: savedGraph,
          trip_input_ids: savedInputs,
          ...summary
        } = saved;
        void savedGraph;
        void savedInputs;
        setItems((old) => [
          summary,
          ...(old ?? []).filter((item) => item.id !== saved.id),
        ]);
        setEditor(null);
        retry.current = null;
        setNotice(
          `已保存模板「${saved.title}」第${saved.revision}版，可在其他旅行载入。`,
        );
      }
    } catch (err) {
      if (alive.current) setError(message(err));
    } finally {
      if (alive.current) setBusy("");
    }
  }
  async function remove() {
    if (!deleting) return;
    setBusy(deleting.item.id);
    setError("");
    try {
      await api.deleteUserTemplate(deleting.item.id, {
        request_id: deleting.id,
        expected_revision: deleting.item.revision,
      });
      if (alive.current) {
        setItems((old) =>
          (old ?? []).filter((item) => item.id !== deleting.item.id),
        );
        setDeleting(null);
        setNotice("模板已删除，已载入的工程和历史运行继续保留。");
      }
    } catch (err) {
      if (alive.current) setError(message(err));
    } finally {
      if (alive.current) setBusy("");
    }
  }
  const locked = disabled || !!busy;
  const filtered = items?.filter((item) =>
    `${item.title} ${item.description}`
      .toLocaleLowerCase()
      .includes(query.trim().toLocaleLowerCase()),
  );
  return (
    <div className="mt-3 min-w-0">
      <button
        disabled={locked || !graph.nodes.length}
        onClick={() => open(null, graph)}
        className="rounded-lg border border-teal-200 bg-white px-3 py-2 text-xs text-teal-800 disabled:opacity-40"
      >
        保存为我的模板
      </button>
      {notice && (
        <p role="status" className="mt-2 text-xs leading-6 text-teal-800">
          {notice}
        </p>
      )}
      {error && !editor && (
        <p role="alert" className="mt-2 text-xs leading-6 text-red-700">
          {error}
          <button
            disabled={!!busy}
            className="ml-3 underline"
            onClick={() => {
              setDeleting(null);
              setError("");
              reloadList();
            }}
          >
            刷新我的模板
          </button>
        </p>
      )}
      {visible && (
        <section
          aria-label="我的工程模板"
          className="mt-3 rounded-xl border border-slate-200 bg-white p-4"
        >
          <div className="flex flex-wrap items-center justify-between gap-3">
            <h2 className="text-sm font-semibold">我的模板</h2>
            <button
              disabled={loading || !!busy}
              className="text-xs text-teal-700"
              onClick={() => {
                setDeleting(null);
                reloadList();
              }}
            >
              刷新我的模板
            </button>
          </div>
          <p className="mt-2 text-xs leading-6 text-slate-500">
            模板保存在本机，可跨旅行使用。载入后先成为可编辑草稿，不会自动运行；选定的需求输入会替换为本次旅行需求，固定文字和节点指令沿用模板。
          </p>
          {loading && (
            <p role="status" className="mt-2 text-xs">
              正在读取我的模板…
            </p>
          )}
          {listError && (
            <p role="alert" className="mt-2 text-xs text-red-700">
              模板库读取失败：{listError}。当前工程和默认模板仍可使用。
            </p>
          )}
          {items && (
            <>
              <label className="mt-3 block text-xs">
                搜索我的模板
                <input
                  aria-label="搜索我的模板"
                  value={query}
                  onChange={(e) => setQuery(e.target.value)}
                  maxLength={200}
                  className="ml-2 max-w-full rounded border border-slate-200 p-2"
                />
              </label>
              {!items.length && (
                <p className="mt-3 text-xs text-slate-500">
                  还没有个人模板。调好工程后，点击“保存为我的模板”。
                </p>
              )}
              {!!items.length && !filtered?.length && (
                <p className="mt-3 text-xs text-slate-500">没有匹配的模板。</p>
              )}
              <div className="mt-3 grid gap-3 md:grid-cols-2">
                {filtered?.map((item) => (
                  <article
                    aria-label={`个人模板 ${item.title}`}
                    key={item.id}
                    className="min-w-0 rounded-lg border border-slate-200 p-3 text-xs leading-6"
                  >
                    <h3 className="break-words font-semibold text-slate-800">
                      {item.title}
                    </h3>
                    <p className="whitespace-pre-wrap break-words text-slate-500">
                      {item.description || "暂无说明"}
                    </p>
                    <p className="text-slate-500">
                      {item.node_count}个节点 · {item.trip_input_count}
                      个旅行需求输入 · 修订{item.revision}
                    </p>
                    <div className="mt-2 flex flex-wrap gap-x-4 gap-y-2">
                      <button
                        disabled={locked}
                        className="text-teal-700 disabled:opacity-40"
                        onClick={async () => {
                          setError("");
                          try {
                            await onLoad(item);
                          } catch (err) {
                            if (alive.current) setError(message(err));
                          }
                        }}
                      >
                        载入模板
                      </button>
                      <button
                        disabled={locked}
                        className="text-slate-600"
                        onClick={() => edit(item, true)}
                      >
                        编辑名称与说明
                      </button>
                      <button
                        disabled={locked || !graph.nodes.length}
                        className="text-slate-600"
                        onClick={() => edit(item, false)}
                      >
                        用当前工程更新
                      </button>
                      <button
                        disabled={locked}
                        className="text-red-600"
                        onClick={() => {
                          setError("");
                          setDeleting({ item, id: crypto.randomUUID() });
                        }}
                      >
                        删除模板
                      </button>
                    </div>
                    {deleting?.item.id === item.id && (
                      <div className="mt-3 rounded-lg bg-amber-50 p-3">
                        <p>
                          删除“{item.title}”？已载入的旅行工程和历史记录会保留。
                        </p>
                        <div className="mt-2 flex gap-4">
                          <button
                            disabled={locked}
                            onClick={remove}
                            className="text-red-700"
                          >
                            确认删除模板
                          </button>
                          <button
                            disabled={!!busy}
                            onClick={() => setDeleting(null)}
                          >
                            取消删除
                          </button>
                        </div>
                      </div>
                    )}
                  </article>
                ))}
              </div>
            </>
          )}
        </section>
      )}
      {editor &&
        createPortal(
          <dialog
            ref={dialog}
            aria-label="保存个人工程模板"
            onCancel={(event) => {
              if (busy) event.preventDefault();
              else setEditor(null);
            }}
            className="fixed inset-0 m-auto max-h-[90dvh] w-[calc(100%-24px)] max-w-2xl overflow-auto rounded-2xl border border-slate-200 bg-white p-5 shadow-2xl backdrop:bg-slate-950/60"
          >
            <form
              onSubmit={(e) => {
                e.preventDefault();
                void save();
              }}
              className="space-y-4 text-sm"
            >
              <div className="flex items-center justify-between gap-3">
                <h2 className="font-semibold">
                  {editor.metadataOnly
                    ? "编辑模板名称与说明"
                    : editor.source
                      ? "用当前工程更新模板"
                      : "保存为个人模板"}
                </h2>
                <button
                  type="button"
                  disabled={!!busy}
                  onClick={() => setEditor(null)}
                  className="text-xs text-slate-500"
                >
                  关闭模板编辑
                </button>
              </div>
              <p className="text-xs leading-6 text-slate-500">
                保存{editor.graph.nodes.length}
                个节点的结构、参数和指令，不包含运行结果、账目或照片。指令及保留的固定文字也会用于其他旅行，请核对其中的个人内容。
              </p>
              {editor.source && (
                <p className="text-xs text-amber-800">
                  更新模板“{editor.source.title}”修订{editor.source.revision}
                  ；已载入的工程不会跟随改变。
                </p>
              )}
              {error && (
                <p
                  role="alert"
                  className="rounded-lg bg-red-50 p-3 text-xs leading-6 text-red-700"
                >
                  {error}
                  <button
                    type="button"
                    disabled={!!busy}
                    onClick={() => {
                      reloadList();
                    }}
                    className="ml-3 underline"
                  >
                    刷新列表，保留编辑
                  </button>
                  {editor.source && (
                    <button
                      type="button"
                      disabled={!!busy}
                      onClick={() => edit(editor.source!, editor.metadataOnly)}
                      className="ml-3 underline"
                    >
                      重新载入最新模板（替换表单）
                    </button>
                  )}
                </p>
              )}
              <fieldset disabled={!!busy} className="space-y-4">
                <label className="block text-xs">
                  模板名称
                  <input
                    aria-label="个人模板名称"
                    required
                    maxLength={80}
                    value={title}
                    onChange={(e) => setTitle(e.target.value)}
                    className="mt-2 w-full rounded-lg border border-slate-200 p-2"
                  />
                </label>
                <label className="block text-xs">
                  模板说明
                  <textarea
                    aria-label="个人模板说明"
                    maxLength={600}
                    value={description}
                    onChange={(e) => setDescription(e.target.value)}
                    className="mt-2 min-h-20 w-full rounded-lg border border-slate-200 p-2"
                  />
                </label>
                <div className="space-y-3 text-xs leading-6">
                  <h3 className="font-medium">文本输入如何复用</h3>
                  {!editor.graph.nodes.some((n) => n.kind === "input") && (
                    <p className="text-slate-500">
                      此工程没有文本输入节点；提示词和检索设置按原样保留。
                    </p>
                  )}
                  {editor.graph.nodes
                    .filter((n) => n.kind === "input")
                    .map((node) => (
                      <div key={node.id} className="rounded-lg bg-slate-50 p-3">
                        <label className="flex items-start gap-2">
                          <input
                            aria-label={`${node.title} 使用本次旅行需求`}
                            type="checkbox"
                            checked={tripIds.includes(node.id)}
                            disabled={editor.metadataOnly}
                            onChange={(e) =>
                              setTripIds((old) =>
                                e.target.checked
                                  ? [...old, node.id]
                                  : old.filter((id) => id !== node.id),
                              )
                            }
                            className="mt-1"
                          />
                          <span>{node.title}：载入时使用本次旅行需求</span>
                        </label>
                        <p
                          className={
                            tripIds.includes(node.id)
                              ? "mt-1 text-teal-800"
                              : "mt-1 text-amber-800"
                          }
                        >
                          {tripIds.includes(node.id)
                            ? "当前输入正文不会保存。本次旅行暂无需求时留空。"
                            : "此输入作为固定文字保留，请确认适用于其他旅行。"}
                        </p>
                        <details className="mt-2">
                          <summary className="cursor-pointer">
                            核对当前输入正文
                          </summary>
                          <p className="mt-2 max-h-40 overflow-auto whitespace-pre-wrap break-words">
                            {node.parameters.text || "（空输入）"}
                          </p>
                        </details>
                      </div>
                    ))}
                </div>
                <details className="text-xs leading-6">
                  <summary className="cursor-pointer">
                    核对节点指令与参数
                  </summary>
                  {editor.graph.nodes
                    .filter(
                      (n) =>
                        n.parameters.prompt ||
                        [
                          "select",
                          "route",
                          "budget",
                          "candidates",
                          "allocate",
                          "locate",
                          "merge",
                        ].includes(n.kind),
                    )
                    .map((n) => (
                      <div key={n.id} className="mt-3 rounded bg-slate-50 p-3">
                        <p className="font-medium">{n.title}</p>
                        <p className="max-h-40 overflow-auto whitespace-pre-wrap break-words">
                          {n.parameters.prompt}
                        </p>
                        {n.kind === "candidates" && (
                          <p className="mt-2">
                            候选数量上限：{n.parameters.candidate_limit ?? 12}
                          </p>
                        )}
                        {n.kind === "merge" && (
                          <p className="mt-2">
                            合并上限：{n.parameters.candidate_limit}；费用冲突：
                            {n.parameters.merge_cost_policy === "first"
                              ? "沿用优先来源"
                              : "保留未知"}
                            <br />
                            来源顺序：
                            {n.parameters.merge_order
                              ?.map(
                                (id) =>
                                  editor.graph.nodes.find(
                                    (item) => item.id === id,
                                  )?.title ?? id,
                              )
                              .join(" → ") || "按连接来源编号"}
                          </p>
                        )}
                        {n.kind === "allocate" && (
                          <p className="mt-2 whitespace-pre-wrap break-words">
                            分日依据：
                            {n.parameters.allocation_strategy === "nearby"
                              ? "按确认位置分组"
                              : "按停留时长平衡"}
                            <br />
                            分日天数：
                            {n.parameters.allocation_days ?? "沿用需求"}；节奏：
                            {
                              (
                                {
                                  relaxed: "松弛",
                                  balanced: "均衡",
                                  full: "丰富",
                                } as const
                              )[n.parameters.allocation_pace ?? "balanced"]
                            }
                            <br />
                            首站：
                            {n.parameters.allocation_start_time ?? "09:00"}
                            ；每日上限：
                            {n.parameters.max_attractions_per_day ?? "沿用节奏"}
                            <br />
                            默认停留：
                            {n.parameters.allocation_default_stay_minutes ?? 90}
                            分钟；缓冲：
                            {n.parameters.allocation_buffer_minutes ?? 30}分钟
                            <br />
                            必去：{n.parameters.required_places || "无"}
                            <br />
                            排除：{n.parameters.excluded_places || "无"}
                          </p>
                        )}
                        {n.kind === "locate" && (
                          <p className="mt-2">
                            地点指定：
                            {
                              Object.values(
                                n.parameters.location_choices ?? {},
                              ).filter(Boolean).length
                            }
                            处；只在新地图结果仍包含同一编号时沿用，其他旅行的无关指定不应用。
                          </p>
                        )}
                        {n.kind === "select" && (
                          <p className="mt-2 whitespace-pre-wrap break-words">
                            每日上限：
                            {n.parameters.max_attractions_per_day ?? "不限制"}
                            ；停留：
                            {n.parameters.attraction_stay_minutes ?? "沿用上游"}
                            <br />
                            必去：{n.parameters.required_places || "无"}
                            <br />
                            排除：{n.parameters.excluded_places || "无"}
                          </p>
                        )}
                        {n.kind === "route" && (
                          <p className="mt-2">
                            顺序：
                            {n.parameters.route_order === "nearby"
                              ? "减少直线折返"
                              : "沿用上游"}
                            ；默认交通：{n.parameters.travel_mode}
                          </p>
                        )}
                        {n.kind === "budget" && (
                          <p className="mt-2 whitespace-pre-wrap break-words">
                            饮食：
                            {n.parameters.budget_rules
                              ?.meal_per_person_day_cents == null
                              ? "不补齐"
                              : `¥${(n.parameters.budget_rules.meal_per_person_day_cents / 100).toFixed(2)}/人/天`}
                            <br />
                            住宿：
                            {n.parameters.budget_rules
                              ?.lodging_per_room_night_cents == null
                              ? "不补齐"
                              : `¥${(n.parameters.budget_rules.lodging_per_room_night_cents / 100).toFixed(2)}/间/晚`}
                            ；
                            {n.parameters.budget_rules?.lodging_rooms ?? "待定"}
                            间
                            <br />
                            往返大交通：
                            {n.parameters.budget_rules
                              ?.round_trip_per_person_cents == null
                              ? "不补齐"
                              : `¥${(n.parameters.budget_rules.round_trip_per_person_cents / 100).toFixed(2)}/人/往返`}
                            <br />
                            全团备用金：
                            {n.parameters.budget_rules?.contingency_cents ==
                            null
                              ? "不补齐"
                              : `¥${(n.parameters.budget_rules.contingency_cents / 100).toFixed(2)}`}
                          </p>
                        )}
                      </div>
                    ))}
                </details>
              </fieldset>
              <button
                type="submit"
                disabled={!!busy || !title.trim()}
                className="rounded-lg bg-teal-700 px-4 py-2 text-xs text-white disabled:opacity-40"
              >
                {busy === "save"
                  ? "正在保存模板…"
                  : editor.source
                    ? "更新此模板"
                    : "保存个人模板"}
              </button>
            </form>
          </dialog>,
          document.body,
        )}
    </div>
  );
}
