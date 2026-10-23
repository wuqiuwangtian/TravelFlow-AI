"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { Button } from "@/components/ui";
import { api } from "@/lib/api";
import type { SavedPreferences } from "@/lib/types";

export default function PreferencesPage() {
  const [current, setCurrent] = useState<SavedPreferences | null>(null);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(true);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  async function load(keepInput = false) {
    try {
      const result = await api.travelPreferences();
      setCurrent(result);
      if (!keepInput) setText(result.preferences.join("\n"));
      setError("");
      setNotice(
        keepInput ? "已载入最新偏好，你的输入仍保留。核对后可再次保存。" : "",
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : "偏好加载失败");
    } finally {
      setBusy(false);
    }
  }
  useEffect(() => {
    let cancelled = false;
    api
      .travelPreferences()
      .then((result) => {
        if (!cancelled) {
          setCurrent(result);
          setText(result.preferences.join("\n"));
        }
      })
      .catch((err) => {
        if (!cancelled)
          setError(err instanceof Error ? err.message : "偏好加载失败");
      })
      .finally(() => {
        if (!cancelled) setBusy(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  async function save() {
    if (!current) return;
    const values = text
      .split("\n")
      .map((value) => value.trim())
      .filter(Boolean);
    if (
      !values.length ||
      values.length > 12 ||
      values.some((value) => value.length > 60)
    ) {
      setError(
        "请填写1至12条偏好，每条最多60字；如需清空，请点击删除常用偏好。",
      );
      return;
    }
    setBusy(true);
    setNotice("");
    try {
      const result = await api.saveTravelPreferences(values, current.revision);
      setCurrent(result);
      setText(result.preferences.join("\n"));
      setError("");
      setNotice("常用偏好已保存。下次新建时可选择使用，已有旅行保持原样。");
    } catch (err) {
      setError(err instanceof Error ? err.message : "保存失败，输入已保留");
    } finally {
      setBusy(false);
    }
  }

  async function remove() {
    if (!current) return;
    setBusy(true);
    setNotice("");
    try {
      const result = await api.deleteTravelPreferences(current.revision);
      setCurrent(result);
      setText("");
      setError("");
      setNotice(
        "常用偏好已删除。已有旅行创建时选用的副本仍保留，可在对应旅行中修改。",
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : "删除失败");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="mx-auto max-w-3xl px-5 py-8">
      <div className="flex flex-wrap items-baseline justify-between gap-4">
        <h1 className="text-xl font-bold text-teal-950">常用旅行偏好</h1>
        <Link href="/" className="text-sm text-teal-800">
          回到工作台
        </Link>
      </div>
      <p className="mt-3 text-sm leading-7 text-slate-500">
        把常用的旅行节奏、兴趣、饮食和出行限制写在这里，下次不用重新说一遍。只保存你主动填写的内容，不从旅行记录自动推断。
      </p>
      <p className="mt-2 text-xs leading-6 text-slate-500">
        内容保存在本机；新建旅行时主动勾选后，作为该旅行的参考，联网规划时会提供给当前模型及必要的检索服务。目的地、日期和预算仍由每次旅行单独确定。
      </p>

      {error && (
        <div
          role="alert"
          className="mt-5 rounded-xl bg-amber-50 p-4 text-sm leading-7 text-amber-900"
        >
          <p>{error}</p>
          <button
            disabled={busy}
            className="mt-2 text-teal-800 underline"
            onClick={() => {
              setBusy(true);
              void load(!!current);
            }}
          >
            {current ? "载入最新偏好，保留我的输入" : "重试加载偏好"}
          </button>
        </div>
      )}
      {notice && (
        <p
          role="status"
          className="mt-5 rounded-xl bg-teal-50 p-4 text-sm leading-7 text-teal-800"
        >
          {notice}
        </p>
      )}
      {!current && !error && (
        <p className="mt-6 text-sm text-slate-500">正在加载偏好…</p>
      )}
      {current && (
        <>
          <section
            aria-label="当前已保存偏好"
            className="mt-6 rounded-xl border border-teal-100 bg-white p-5"
          >
            <h2 className="text-sm font-semibold text-teal-900">当前已保存</h2>
            {current.preferences.length ? (
              <div className="mt-3 flex flex-wrap gap-2">
                {current.preferences.map((value) => (
                  <span
                    key={value}
                    className="max-w-full break-words rounded-lg bg-teal-50 px-3 py-1.5 text-xs text-teal-800"
                  >
                    {value}
                  </span>
                ))}
              </div>
            ) : (
              <p className="mt-3 text-sm text-slate-500">
                尚无常用偏好，新旅行不会自动使用任何偏好。
              </p>
            )}
            {current.updated_at && (
              <p className="mt-3 text-xs text-slate-400">
                更新于 {new Date(current.updated_at).toLocaleString("zh-CN")}
              </p>
            )}
          </section>
          <form
            className="mt-6"
            onSubmit={(e) => {
              e.preventDefault();
              void save();
            }}
          >
            <label
              className="block text-sm font-medium text-slate-700"
              htmlFor="travel-preferences"
            >
              编辑常用偏好
            </label>
            <p
              id="preference-help"
              className="mt-2 text-xs leading-6 text-slate-500"
            >
              每行一条，最多12条，每条60字。保存后不自动应用，新建旅行时再选择。
            </p>
            <textarea
              id="travel-preferences"
              aria-describedby="preference-help"
              value={text}
              onChange={(e) => {
                setText(e.target.value);
                setNotice("");
              }}
              disabled={busy}
              maxLength={1200}
              rows={7}
              placeholder={"慢节奏\n当地美食\n少走路"}
              className="mt-3 w-full rounded-xl border border-slate-200 bg-white p-4 text-sm leading-7 focus:border-teal-400 focus:outline-none"
            />
            <div className="mt-4 flex flex-wrap gap-3">
              <Button
                type="submit"
                variant="primary"
                loading={busy}
                disabled={!text.trim()}
              >
                保存常用偏好
              </Button>
              <Button
                type="button"
                variant="danger"
                disabled={busy || !current.preferences.length}
                onClick={() => void remove()}
              >
                删除常用偏好
              </Button>
            </div>
          </form>
          <p className="mt-5 text-xs leading-6 text-slate-500">
            修改、删除只影响后续新建时可选的内容，已有旅行保留创建时选用的副本。若本次不想沿用，可在该旅行对话中直接说「这次清空偏好」或提出新的偏好。
          </p>
        </>
      )}
    </div>
  );
}
