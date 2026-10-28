"use client";

import { useEffect, useState, useSyncExternalStore } from "react";
import { Check, Palette } from "lucide-react";
import { Button, Card } from "@/components/ui";
import {
  currentTheme,
  saveTheme,
  serverTheme,
  subscribeTheme,
  THEME_CHANGED_EVENT,
  THEMES,
  type Theme,
} from "@/lib/theme";

export function ThemeSettings() {
  const theme = useSyncExternalStore(subscribeTheme, currentTheme, serverTheme);
  const [feedback, setFeedback] = useState<{
    saved: boolean;
    label: string;
  } | null>(null);
  useEffect(() => {
    const clear = () => setFeedback(null);
    window.addEventListener(THEME_CHANGED_EVENT, clear);
    return () => window.removeEventListener(THEME_CHANGED_EVENT, clear);
  }, []);
  function choose(value: Theme, label: string) {
    const saved = saveTheme(value);
    setFeedback({ saved, label });
  }
  return (
    <Card
      className="tf-settings-section tf-theme-settings"
      role="region"
      aria-labelledby="appearance"
    >
      <h2 id="appearance" className="tf-section-title">
        <Palette aria-hidden="true" className="h-4 w-4" />
        外观主题
      </h2>
      <p className="tf-field-help">
        立即生效，只保存在当前浏览器；不会改动服务配置或旅行内容。电脑保持松弛布局，手机保持紧凑布局。
      </p>
      <div className="tf-theme-options" role="group" aria-label="选择外观主题">
        {THEMES.map((option) => (
          <Button
            key={option.id}
            aria-pressed={theme === option.id}
            onClick={() => choose(option.id, option.label)}
            data-theme-option={option.id}
            className="tf-theme-option"
          >
            <span className="tf-theme-sample" aria-hidden="true">
              <span />
              <span />
              <span />
            </span>
            <span className="tf-theme-label">
              {option.label}
              {option.default && <span className="tf-theme-default">默认</span>}
            </span>
            <span className="tf-theme-description">{option.description}</span>
            <span className="tf-theme-selected">
              {theme === option.id ? (
                <>
                  <Check aria-hidden="true" className="h-3.5 w-3.5" />
                  当前主题
                </>
              ) : (
                "切换此主题"
              )}
            </span>
          </Button>
        ))}
      </div>
      {feedback && (
        <p
          role="status"
          className="tf-feedback"
          data-tone={feedback.saved ? "success" : "warning"}
        >
          {feedback.saved
            ? `已切换为${feedback.label}，并在此浏览器保存。`
            : `已切换为${feedback.label}，但浏览器未允许保存；刷新后可能恢复之前的主题。`}
        </p>
      )}
    </Card>
  );
}
