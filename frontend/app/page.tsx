"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { Plane, Plus } from "lucide-react";

import {
  Button,
  Card,
  EmptyState,
  ErrorBanner,
  Spinner,
} from "@/components/ui";
import { PreferenceChoice } from "@/components/preference-choice";
import { api, ApiError } from "@/lib/api";
import type { ConfigCheck, SessionInfo, SavedPreferences } from "@/lib/types";
import { relativeTime } from "@/lib/utils";

export default function HomePage() {
  const router = useRouter();
  const [sessions, setSessions] = useState<SessionInfo[] | null>(null);
  const [config, setConfig] = useState<ConfigCheck | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [configError, setConfigError] = useState<string | null>(null);
  const creatingRef = useRef(false);
  const [creating, setCreating] = useState(false);
  const [selectedPreferences, setSelectedPreferences] =
    useState<SavedPreferences | null>(null);
  const [preferenceRefresh, setPreferenceRefresh] = useState(0);

  const load = useCallback(async () => {
    setLoadError(null);
    setConfigError(null);
    const [list, cfg] = await Promise.allSettled([
      api.listSessions(),
      api.config(),
    ]);
    if (list.status === "fulfilled") setSessions(list.value.items);
    else
      setLoadError(
        list.reason instanceof Error ? list.reason.message : "旅行列表加载失败",
      );
    if (cfg.status === "fulfilled") setConfig(cfg.value);
    else setConfigError("服务配置暂时未能载入，仍可查看旅行和选用偏好。");
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  async function createTrip() {
    if (creatingRef.current) return;
    creatingRef.current = true;
    setCreating(true);
    setError(null);
    try {
      const detail = await api.createSession("新的旅行", selectedPreferences);
      router.push(`/chat/${detail.session.id}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "创建失败");
      if (
        err instanceof ApiError &&
        (err.code === "CONFLICT" || err.code === "INVALID_REQUEST")
      ) {
        setSelectedPreferences(null);
        setPreferenceRefresh(preferenceRefresh + 1);
      }
      setCreating(false);
      creatingRef.current = false;
    }
  }

  const notConfigured = config && (!config.llm.ready || !config.search.ready);

  return (
    <div className="mx-auto max-w-6xl px-5 py-8">
      {/* 未完成配置时给出明确出路，而不是让用户在对话页撞墙 */}
      {notConfigured && (
        <div className="mb-5 flex flex-wrap items-center justify-between gap-3 rounded-lg border border-amber-300 border-l-4 border-l-amber-500 bg-amber-50 px-4 py-3">
          <div className="text-sm text-amber-900">
            <span className="font-semibold">还没完成配置</span>
            <span className="ml-2">
              {config?.warnings?.[0] ?? "请先配置大模型与检索服务"}
            </span>
          </div>
          <Button size="sm" onClick={() => router.push("/settings")}>
            去设置
          </Button>
        </div>
      )}

      {configError && (
        <div className="mb-4">
          <ErrorBanner message={configError} onRetry={load} />
        </div>
      )}
      <div className="mb-5">
        <PreferenceChoice
          key={preferenceRefresh}
          selected={selectedPreferences}
          onChange={setSelectedPreferences}
          disabled={creating}
        />
      </div>

      <section className="relative mb-8 overflow-hidden rounded-2xl bg-gradient-to-br from-brand-600 via-brand-500 to-teal-600 px-7 py-9 text-white">
        <div className="absolute -top-16 -right-10 h-56 w-56 rounded-full bg-teal-300/30 blur-3xl" />
        <div className="relative">
          <h1 className="text-2xl font-extrabold tracking-tight sm:text-3xl">
            想去哪儿旅行？
          </h1>
          <p className="mt-2 max-w-lg text-sm text-white/85">
            告诉我目的地和天数，我来帮你把行程、交通、预算和避雷都安排好。
          </p>
          <Button
            size="lg"
            className="mt-6 !border-transparent !bg-white !text-brand-700 hover:!brightness-100 hover:!bg-white/90"
            loading={creating}
            loadingLabel="正在创建旅行…"
            onClick={createTrip}
          >
            <Plus className="h-4 w-4" />
            新建旅行
          </Button>
        </div>
      </section>

      {error && (
        <div className="mb-5">
          <ErrorBanner message={error} onRetry={createTrip} />
        </div>
      )}

      <h2 className="mb-3 text-sm font-bold text-slate-700">最近的旅行</h2>

      {loadError ? (
        <ErrorBanner message={loadError} onRetry={load} />
      ) : sessions === null ? (
        <Card className="p-6">
          <Spinner label="正在加载…" />
        </Card>
      ) : sessions.length === 0 ? (
        <EmptyState
          icon={<Plane className="h-9 w-9" />}
          title="还没有旅行计划"
          description="点上面的「新建旅行」开始，我会先问你几个必要的问题。"
        />
      ) : (
        <div className="grid gap-3.5 sm:grid-cols-2 lg:grid-cols-3">
          {sessions.map((session) => (
            <Card
              key={session.id}
              className="cursor-pointer p-4 transition hover:-translate-y-1 hover:border-brand-200 hover:shadow-md"
            >
              <Link
                className="block rounded-lg focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-brand-500"
                href={`/chat/${session.id}`}
              >
                <h3 className="font-bold text-slate-800">{session.title}</h3>
                <div className="mt-2 flex flex-wrap gap-3 text-xs text-slate-400">
                  <span>{session.branch_count} 个分支</span>
                  <span>{relativeTime(session.updated_at)}</span>
                </div>
              </Link>
            </Card>
          ))}
        </div>
      )}
    </div>
  );
}
