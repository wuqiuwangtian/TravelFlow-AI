"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { ActionFeedback, Button, Card, Spinner } from "@/components/ui";
import { api } from "@/lib/api";
import type { SavedPreferences } from "@/lib/types";
import { scrollToElement } from "@/lib/utils";

type Operation = "load" | "save" | "remove" | null;
export default function PreferencesPage() {
  const [current, setCurrent] = useState<SavedPreferences | null>(null);
  const [text, setText] = useState("");
  const [operation, setOperation] = useState<Operation>("load");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const lock = useRef(true);
  const feedback = useRef<HTMLDivElement>(null);
  const busy = operation !== null;

  useEffect(() => {
    if (operation || (!error && !notice)) return;
    feedback.current?.focus({ preventScroll: true });
    if (error) scrollToElement(feedback.current, { block: "nearest" });
  }, [error, notice, operation]);

  async function load(keepInput = false) {
    if (lock.current) return;
    lock.current = true;
    setOperation("load");
    setNotice("");
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
      lock.current = false;
      setOperation(null);
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
        if (!cancelled) {
          lock.current = false;
          setOperation(null);
        }
      });
    return () => {
      cancelled = true;
    };
  }, []);

  async function save() {
    if (!current || lock.current) return;
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
      setNotice("");
      return;
    }
    lock.current = true;
    setOperation("save");
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
      lock.current = false;
      setOperation(null);
    }
  }

  async function remove() {
    if (!current || lock.current) return;
    lock.current = true;
    setOperation("remove");
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
      lock.current = false;
      setOperation(null);
    }
  }

  const result = (
    <div ref={feedback} tabIndex={-1} className="tf-preference-feedback">
      <ActionFeedback
        pending={
          operation === "save"
            ? "正在保存常用偏好…"
            : operation === "remove"
              ? "正在删除常用偏好…"
              : current && operation === "load"
                ? "正在载入最新偏好，你的输入仍保留…"
                : undefined
        }
        error={error}
        message={notice}
      />
      {error && (
        <Button
          type="button"
          size="sm"
          className="mt-2"
          disabled={busy}
          onClick={() => void load(!!current)}
        >
          {current ? "载入最新偏好，保留我的输入" : "重试加载偏好"}
        </Button>
      )}
    </div>
  );
  return (
    <div className="tf-page tf-preferences-page">
      <header className="tf-page-heading">
        <div>
          <h1>常用旅行偏好</h1>
          <p>记住你主动写下的习惯，下次规划时再选择使用。</p>
        </div>
        <Link href="/" className="tf-action-link tf-page-link">
          回到工作台
        </Link>
      </header>
      <p className="tf-page-intro">
        可以写旅行节奏、兴趣、饮食和出行限制。目的地、日期和预算仍由每次旅行单独确定，不从旅行记录自动推断偏好。
      </p>
      {!current && (busy ? <Spinner label="正在加载偏好…" /> : result)}
      {current && (
        <div className="tf-preferences-grid">
          <Card className="tf-preference-editor">
            <form
              aria-label="常用偏好编辑"
              onSubmit={(e) => {
                e.preventDefault();
                void save();
              }}
            >
              <label className="tf-section-title" htmlFor="travel-preferences">
                编辑常用偏好
              </label>
              <p id="preference-help" className="tf-field-help">
                每行一条，最多12条，每条60字。保存后不自动应用，新建旅行时再选择。
              </p>
              <textarea
                id="travel-preferences"
                aria-describedby="preference-help preference-sharing"
                value={text}
                onChange={(e) => {
                  setText(e.target.value);
                  setNotice("");
                }}
                disabled={busy}
                maxLength={1200}
                rows={7}
                placeholder={"慢节奏\n当地美食\n少走路"}
                className="tf-form-control mt-4"
              />
              <div className="mt-4 flex flex-wrap gap-3">
                <Button
                  type="submit"
                  variant="primary"
                  loading={operation === "save"}
                  disabled={busy || !text.trim()}
                >
                  保存常用偏好
                </Button>
                <Button
                  type="button"
                  variant="danger"
                  loading={operation === "remove"}
                  disabled={busy || !current.preferences.length}
                  onClick={() => void remove()}
                >
                  删除常用偏好
                </Button>
              </div>
              {result}
            </form>
            <p id="preference-sharing" className="tf-field-help mt-4">
              内容保存在本机。主动选用于新旅行后，联网规划时会提供给当前模型及必要的检索服务。
            </p>
          </Card>
          <section aria-label="当前已保存偏好" className="tf-preference-saved">
            <h2 className="tf-section-title">当前已保存</h2>
            {current.preferences.length ? (
              <ul className="tf-saved-preference-list">
                {current.preferences.map((value) => (
                  <li key={value}>{value}</li>
                ))}
              </ul>
            ) : (
              <p className="tf-field-help">
                尚无常用偏好，新旅行不会自动使用任何偏好。
              </p>
            )}
            {current.updated_at && (
              <p className="tf-field-help">
                更新于 {new Date(current.updated_at).toLocaleString("zh-CN")}
              </p>
            )}
            <p className="tf-field-help mt-4">
              修改、删除只影响后续新建时可选的内容，已有旅行保留创建时选用的副本。若本次不想沿用，可在旅行对话中说「这次清空偏好」或提出新的偏好。
            </p>
          </section>
        </div>
      )}
    </div>
  );
}
