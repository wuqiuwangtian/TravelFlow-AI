"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  CheckCircle2,
  ExternalLink,
  Plug,
  Save,
  Settings as SettingsIcon,
  XCircle,
} from "lucide-react";

import {
  ActionFeedback,
  Badge,
  Button,
  Card,
  ErrorBanner,
  Spinner,
} from "@/components/ui";
import { api } from "@/lib/api";
import type { SettingsPayload } from "@/lib/settings-types";
import type {
  ConnectionTest,
  DetailLevel,
  ProvidersResponse,
} from "@/lib/types";
import { DETAIL_LEVEL_LABELS } from "@/lib/types";

type TestState = ConnectionTest | null;

export default function SettingsPage() {
  const [current, setCurrent] = useState<SettingsPayload | null>(null);
  const [providers, setProviders] = useState<ProvidersResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [saveError, setSaveError] = useState<string | null>(null);
  const savingRef = useRef(false);
  const testingRef = useRef(false);
  const modelsRef = useRef(false);

  // 表单（密钥单独保存，避免回显）
  const [llmKey, setLlmKey] = useState("");
  const [searchKey, setSearchKey] = useState("");
  const [mapKey, setMapKey] = useState("");
  const [removeMapKey, setRemoveMapKey] = useState(false);
  const [form, setForm] = useState({
    llm_provider: "",
    llm_model: "",
    llm_base_url: "",
    search_provider: "",
    searxng_base_url: "",
    default_detail_level: "L2_standard",
  });
  const [saving, setSaving] = useState(false);
  const [models, setModels] = useState<string[]>([]);
  const [modelHint, setModelHint] = useState<string | null>(null);
  const [loadingModels, setLoadingModels] = useState(false);
  const [llmTest, setLlmTest] = useState<TestState>(null);
  const [searchTest, setSearchTest] = useState<TestState>(null);
  const [mapTest, setMapTest] = useState<TestState>(null);
  const [testing, setTesting] = useState<"llm" | "search" | "map" | null>(null);

  const load = useCallback(async () => {
    setError(null);
    try {
      const [s, p] = await Promise.all([api.getSettings(), api.providers()]);
      setCurrent(s);
      setProviders(p);
      setForm({
        llm_provider: s.llm_provider,
        llm_model: s.llm_model,
        llm_base_url: s.llm_base_url,
        search_provider: s.search_provider,
        searxng_base_url: s.searxng_base_url,
        default_detail_level: s.default_detail_level,
      });
    } catch (err) {
      setError(err instanceof Error ? err.message : "加载设置失败");
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  async function save() {
    if (savingRef.current) return;
    savingRef.current = true;
    setSaving(true);
    setNotice(null);
    setSaveError(null);
    setError(null);
    try {
      const payload: Record<string, string> = { ...form };
      if (llmKey.trim()) payload.llm_api_key = llmKey.trim();
      if (searchKey.trim()) payload.search_api_key = searchKey.trim();
      if (removeMapKey) payload.amap_api_key = "";
      else if (mapKey.trim()) payload.amap_api_key = mapKey.trim();

      const result = await api.saveSettings(payload);
      setNotice(result.message);
      setLlmKey("");
      setSearchKey("");
      setMapKey("");
      setRemoveMapKey(false);
      await load();
      // 保存后顺便刷新可用模型列表，方便直接切换
      void fetchModels();
    } catch (err) {
      setSaveError(
        err instanceof Error ? err.message : "保存失败，输入已保留，请重试。",
      );
    } finally {
      savingRef.current = false;
      setSaving(false);
    }
  }

  /** 向服务商查询可用模型 —— 模型名会随官方调整，手写极易出错。 */
  async function fetchModels() {
    if (modelsRef.current) return;
    modelsRef.current = true;
    setLoadingModels(true);
    setModelHint(null);
    try {
      const result = await api.getModels();
      setModels(result.models);
      setModelHint(result.message);
    } catch (err) {
      setModelHint(err instanceof Error ? err.message : "获取模型列表失败");
    } finally {
      modelsRef.current = false;
      setLoadingModels(false);
    }
  }

  async function runTest(kind: "llm" | "search" | "map") {
    if (testingRef.current) return;
    testingRef.current = true;
    setTesting(kind);
    if (kind === "llm") setLlmTest(null);
    else if (kind === "map") setMapTest(null);
    else setSearchTest(null);
    try {
      const result =
        kind === "llm"
          ? await api.testLlm()
          : kind === "map"
            ? await api.testMap()
            : await api.testSearch();
      if (kind === "llm") setLlmTest(result);
      else if (kind === "map") setMapTest(result);
      else setSearchTest(result);
    } catch (err) {
      const failure: ConnectionTest = {
        ok: false,
        provider: "-",
        message: err instanceof Error ? err.message : "测试失败",
      };
      if (kind === "llm") setLlmTest(failure);
      else if (kind === "map") setMapTest(failure);
      else setSearchTest(failure);
    } finally {
      testingRef.current = false;
      setTesting(null);
    }
  }

  if (!current || !providers) {
    return (
      <div className="mx-auto max-w-2xl px-5 py-10">
        {error ? (
          <ErrorBanner message={error} onRetry={load} />
        ) : (
          <Spinner label="正在读取配置…" />
        )}
      </div>
    );
  }

  const llmMeta = providers.llm.find((p) => p.id === form.llm_provider);
  const searchMeta = providers.search.find(
    (p) => p.id === form.search_provider,
  );
  const llmKeyOptional = llmMeta?.requires_api_key === false;
  const searchKeyOptional = searchMeta?.requires_api_key === false;

  return (
    <div className="mx-auto max-w-2xl px-5 py-8">
      <div className="mb-5 flex items-center gap-2.5">
        <SettingsIcon className="h-5 w-5 text-brand-500" />
        <div>
          <h1 className="text-lg font-bold">设置</h1>
          <p className="text-xs text-slate-400">
            保存到{" "}
            <code className="rounded bg-slate-100 px-1">
              {current.env_path}
            </code>
          </p>
        </div>
      </div>

      <fieldset
        disabled={saving || !!testing || loadingModels}
        className="min-w-0"
      >
        {/* ---------------- 大模型 ---------------- */}
        <Card className="mb-5 p-5">
          <div className="mb-4 flex items-center justify-between">
            <h2
              id="llm"
              className="scroll-mt-24 text-sm font-bold text-slate-700"
            >
              🤖 大模型
            </h2>
            {current.llm_has_key || llmKeyOptional ? (
              <Badge tone="teal">已配置</Badge>
            ) : (
              <Badge tone="amber">未配置</Badge>
            )}
          </div>

          <div className="space-y-3.5">
            <Field label="服务商">
              <select
                className={inputClass}
                value={form.llm_provider}
                onChange={(e) =>
                  setForm({ ...form, llm_provider: e.target.value })
                }
              >
                {providers.llm.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.name}
                    {p.requires_api_key ? "" : "（无需 Key）"}
                  </option>
                ))}
              </select>
              {llmMeta && (
                <Hint>
                  {llmMeta.note}
                  {llmMeta.docs_url && (
                    <>
                      {" · "}
                      <a
                        className="text-brand-600 underline"
                        href={llmMeta.docs_url}
                        target="_blank"
                        rel="noopener noreferrer"
                      >
                        官方文档 <ExternalLink className="inline h-3 w-3" />
                      </a>
                    </>
                  )}
                </Hint>
              )}
            </Field>

            <Field label="模型名称">
              <div className="flex gap-2">
                <input
                  className={inputClass}
                  value={form.llm_model}
                  onChange={(e) =>
                    setForm({ ...form, llm_model: e.target.value })
                  }
                  placeholder="例如 deepseek-flash / qwen2.5:7b"
                  list="model-options"
                />
                <Button size="sm" loading={loadingModels} onClick={fetchModels}>
                  获取可用模型
                </Button>
              </div>
              {/* 服务商返回的模型直接做成候选，避免手写错名字 */}
              <datalist id="model-options">
                {models.map((name) => (
                  <option key={name} value={name} />
                ))}
              </datalist>
              {modelHint && <Hint>{modelHint}</Hint>}
            </Field>

            <Field label="API Key">
              <input
                className={inputClass}
                type="password"
                value={llmKey}
                onChange={(e) => setLlmKey(e.target.value)}
                placeholder={
                  llmKeyOptional
                    ? "本地模型无需填写"
                    : current.llm_has_key
                      ? "已保存（留空则不修改）"
                      : "请填写你的 API Key"
                }
                disabled={llmKeyOptional}
              />
            </Field>

            <Field label="接口地址">
              <input
                className={inputClass}
                value={form.llm_base_url}
                onChange={(e) =>
                  setForm({ ...form, llm_base_url: e.target.value })
                }
                placeholder={llmMeta?.default_base_url ?? "留空则使用默认地址"}
              />
            </Field>

            <TestRow
              state={llmTest}
              testing={testing === "llm"}
              onTest={() => runTest("llm")}
            />
          </div>
        </Card>

        {/* ---------------- 检索 ---------------- */}
        <Card className="mb-5 p-5">
          <div className="mb-4 flex items-center justify-between">
            <h2 className="text-sm font-bold text-slate-700">🔍 信息检索</h2>
            {current.search_has_key || searchKeyOptional ? (
              <Badge tone="teal">已配置</Badge>
            ) : (
              <Badge tone="amber">未配置</Badge>
            )}
          </div>

          <div className="space-y-3.5">
            <Field label="检索服务">
              <select
                className={inputClass}
                value={form.search_provider}
                onChange={(e) =>
                  setForm({ ...form, search_provider: e.target.value })
                }
              >
                {providers.search.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.name}
                    {p.requires_api_key ? "" : "（无需 Key）"}
                  </option>
                ))}
              </select>
              {searchMeta && (
                <Hint>
                  {searchMeta.note} · 额度与计费以{" "}
                  <a
                    className="text-brand-600 underline"
                    href={searchMeta.docs_url}
                    target="_blank"
                    rel="noopener noreferrer"
                  >
                    官方文档
                  </a>{" "}
                  为准
                </Hint>
              )}
            </Field>

            {form.search_provider === "searxng" ? (
              <Field label="SearXNG 地址">
                <input
                  className={inputClass}
                  value={form.searxng_base_url}
                  onChange={(e) =>
                    setForm({ ...form, searxng_base_url: e.target.value })
                  }
                  placeholder="http://localhost:8080"
                />
                <Hint>
                  需要自行部署 SearXNG，并在其配置中开启 json 输出格式
                </Hint>
              </Field>
            ) : (
              <Field label="API Key">
                <input
                  className={inputClass}
                  type="password"
                  value={searchKey}
                  onChange={(e) => setSearchKey(e.target.value)}
                  placeholder={
                    searchKeyOptional
                      ? "当前服务无需填写"
                      : current.search_has_key
                        ? "已保存（留空则不修改）"
                        : "请填写检索服务的 API Key"
                  }
                  disabled={searchKeyOptional}
                />
              </Field>
            )}

            <TestRow
              state={searchTest}
              testing={testing === "search"}
              onTest={() => runTest("search")}
            />
          </div>
        </Card>

        {/* ---------------- 生成偏好 ---------------- */}
        <Card className="mb-5 p-5">
          <div className="mb-4 flex items-center justify-between">
            <h2 className="text-sm font-bold text-slate-700">🗺️ 高德地图</h2>
            <Badge tone={current.amap_has_key ? "teal" : "amber"}>
              {current.amap_has_key ? "已配置" : "选填"}
            </Badge>
          </div>
          <div className="space-y-3.5">
            <Field label="Web 服务 Key">
              <input
                className={inputClass}
                type="password"
                autoComplete="new-password"
                value={mapKey}
                disabled={removeMapKey}
                onChange={(e) => setMapKey(e.target.value)}
                placeholder={
                  current.amap_has_key
                    ? "已保存（留空则不修改）"
                    : "填写高德 Web 服务类型的 Key"
                }
              />
              <Hint>
                用于地点、步行/公交/驾车路线及每日地图；请开通相应接口权限。保存后再测试连接。
              </Hint>
              <Hint>
                <a
                  className="text-brand-600 underline"
                  href="https://developer.amap.com/api/webservice/guide/create-project/get-key"
                  target="_blank"
                  rel="noopener noreferrer"
                >
                  申请高德 Key
                </a>
              </Hint>
            </Field>
            {current.amap_has_key && (
              <label className="flex items-center gap-2 text-xs text-slate-500 sm:pl-[122px]">
                <input
                  type="checkbox"
                  checked={removeMapKey}
                  onChange={(e) => setRemoveMapKey(e.target.checked)}
                />
                保存时移除高德 Key
              </label>
            )}
            <TestRow
              state={mapTest}
              testing={testing === "map"}
              onTest={() => runTest("map")}
            />
          </div>
        </Card>

        <Card className="mb-5 p-5">
          <h2 className="mb-4 text-sm font-bold text-slate-700">📐 生成偏好</h2>
          <Field label="默认详细度">
            <select
              className={inputClass}
              value={form.default_detail_level}
              onChange={(e) =>
                setForm({ ...form, default_detail_level: e.target.value })
              }
            >
              {(Object.keys(DETAIL_LEVEL_LABELS) as DetailLevel[]).map(
                (level) => (
                  <option key={level} value={level}>
                    {DETAIL_LEVEL_LABELS[level]}
                  </option>
                ),
              )}
            </select>
            <Hint>生成时仍可在对话页随时切换，切换会产生新版本而非覆盖</Hint>
          </Field>
        </Card>
      </fieldset>
      <section aria-label="设置保存操作">
        <div className="flex flex-wrap items-center gap-3">
          <Button
            variant="primary"
            size="lg"
            loading={saving}
            loadingLabel="正在保存设置…"
            disabled={!!testing || loadingModels}
            onClick={save}
          >
            <Save className="h-4 w-4" />
            保存设置
          </Button>
          <span className="text-xs text-slate-400">
            保存后
            <strong className="font-semibold text-teal-600">立即生效</strong>
            ，无需重启后端
          </span>
        </div>
        <ActionFeedback
          pending={saving ? "正在保存设置，输入已保留…" : undefined}
          error={saveError}
          message={notice}
          onRetry={saveError ? save : undefined}
        />
        {error && (
          <ErrorBanner
            message={
              notice ? `设置已保存，但配置状态刷新失败：${error}` : error
            }
            onRetry={load}
          />
        )}
      </section>
    </div>
  );
}

/* ------------------------------------------------------------------ */

const inputClass =
  "w-full rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm outline-none transition focus:border-brand-400 focus:ring-2 focus:ring-brand-100";

function Field({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <label className="grid gap-1.5 sm:grid-cols-[110px_1fr] sm:items-start">
      <span className="pt-2 text-xs font-medium text-slate-500">{label}</span>
      <span className="block">{children}</span>
    </label>
  );
}

function Hint({ children }: { children: React.ReactNode }) {
  return (
    <span className="mt-1 block text-[11px] leading-relaxed text-slate-400">
      {children}
    </span>
  );
}

function TestRow({
  state,
  testing,
  onTest,
}: {
  state: TestState;
  testing: boolean;
  onTest: () => void;
}) {
  return (
    <div className="flex flex-wrap items-center gap-3 sm:pl-[122px]">
      <Button
        size="sm"
        loading={testing}
        loadingLabel="正在测试…"
        onClick={onTest}
      >
        <Plug className="h-3.5 w-3.5" />
        测试连接
      </Button>
      {state && (
        <span
          role={state.ok ? "status" : "alert"}
          className={`flex items-center gap-1 text-xs ${state.ok ? "text-teal-600" : "text-red-600"}`}
        >
          {state.ok ? (
            <CheckCircle2 className="h-3.5 w-3.5" />
          ) : (
            <XCircle className="h-3.5 w-3.5" />
          )}
          {state.message}
        </span>
      )}
    </div>
  );
}
