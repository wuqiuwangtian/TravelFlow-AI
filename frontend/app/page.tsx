"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { ArrowUpRight, Plane, Plus } from "lucide-react";

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
  const loadRequest = useRef(0);
  const [loading, setLoading] = useState(true);
  const [creating, setCreating] = useState(false);
  const [selectedPreferences, setSelectedPreferences] =
    useState<SavedPreferences | null>(null);
  const [preferenceRefresh, setPreferenceRefresh] = useState(0);

  const load = useCallback(async () => {
    const token = ++loadRequest.current;
    setLoading(true);
    setLoadError(null);
    setConfigError(null);
    await Promise.all([
      api
        .listSessions()
        .then(
          (list) => {
            if (token === loadRequest.current) setSessions(list.items);
          },
          (err) => {
            if (token === loadRequest.current)
              setLoadError(
                err instanceof Error ? err.message : "旅行列表加载失败",
              );
          },
        )
        .finally(() => {
          if (token === loadRequest.current) setLoading(false);
        }),
      api.config().then(
        (cfg) => {
          if (token === loadRequest.current) setConfig(cfg);
        },
        () => {
          if (token === loadRequest.current)
            setConfigError("服务配置暂时未能载入，仍可查看旅行和选用偏好。");
        },
      ),
    ]);
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
    <div className="tf-page tf-home-page">
      {notConfigured && (
        <aside className="tf-config-notice" aria-label="服务配置提醒">
          <div>
            <strong>还没完成配置</strong>
            <p>
              {!config?.llm.ready && !config?.search.ready
                ? "请先在设置中完成模型与检索服务配置。已有旅行仍可查看。"
                : !config?.llm.ready
                  ? "请先在设置中完成模型服务配置。已有旅行仍可查看。"
                  : "请先在设置中完成检索服务配置。已有旅行仍可查看。"}
            </p>
          </div>
          <Link className="tf-action-link tf-page-link" href="/settings">
            去设置
          </Link>
        </aside>
      )}
      {configError && <ErrorBanner message={configError} onRetry={load} />}
      <section className="tf-home-start" aria-label="开始新的旅行">
        <div className="tf-home-copy">
          <p className="tf-eyebrow">你的旅行工作台</p>
          <h1>想去哪儿旅行？</h1>
          <p className="tf-page-intro">
            从想去的城市开始，也可以先聊聊兴趣、天数和预算。
          </p>
        </div>
        <div className="tf-home-preferences">
          <PreferenceChoice
            key={preferenceRefresh}
            selected={selectedPreferences}
            onChange={setSelectedPreferences}
            disabled={creating}
          />
        </div>
        <div className="tf-home-create">
          <Button
            variant="primary"
            size="lg"
            loading={creating}
            loadingLabel="正在创建旅行…"
            onClick={createTrip}
          >
            <Plus aria-hidden="true" className="h-4 w-4" />
            新建旅行
          </Button>
          {creating && <Spinner label="正在准备旅行对话…" />}
          {error && <ErrorBanner message={error} onRetry={createTrip} />}
        </div>
      </section>
      <section aria-labelledby="recent-trips" className="tf-home-recent">
        <div className="tf-section-heading">
          <h2 id="recent-trips">最近的旅行</h2>
          <Link href="/sessions" className="tf-action-link tf-page-link">
            全部旅行
            <ArrowUpRight aria-hidden="true" className="h-4 w-4" />
          </Link>
        </div>
        {loading && sessions !== null && <Spinner label="正在刷新旅行…" />}
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
            description="点上面的「新建旅行」开始，也可以先聊聊还没确定的想法。"
          />
        ) : (
          <ul className="tf-home-trip-list" aria-label="最近旅行">
            {sessions.slice(0, 6).map((session) => (
              <li key={session.id}>
                <Link
                  className="tf-action-link tf-trip-link"
                  href={`/chat/${session.id}`}
                >
                  <div className="min-w-0">
                    <h3>{session.title}</h3>
                    <p>
                      {session.branch_count} 个分支
                      <span aria-hidden="true"> · </span>
                      {relativeTime(session.updated_at)}
                    </p>
                  </div>
                  <ArrowUpRight
                    aria-hidden="true"
                    className="h-4 w-4 shrink-0"
                  />
                </Link>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}
