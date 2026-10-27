"use client";

import { AlertTriangle, Loader2 } from "lucide-react";
import { useEffect, useState } from "react";

import { cn } from "@/lib/utils";

/* ------------------------------------------------------------------
   极简 UI 基础组件。
   刻意不引入组件库 —— 界面结构简单，手写更可控、依赖更少。
   ------------------------------------------------------------------ */

type ButtonProps = React.ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: "primary" | "default" | "ghost" | "danger";
  size?: "sm" | "md" | "lg";
  loading?: boolean;
  loadingLabel?: string;
  motion?: "standard" | "spatial";
};

export function Button({
  className,
  variant = "default",
  size = "md",
  loading = false,
  loadingLabel,
  motion = "standard",
  disabled,
  children,
  ...props
}: ButtonProps) {
  return (
    <button
      {...props}
      className={cn("tf-button", className)}
      data-variant={variant}
      data-size={size}
      data-motion={motion}
      disabled={disabled || loading}
      aria-busy={loading ? true : props["aria-busy"]}
    >
      {loading && (
        <Loader2
          aria-hidden="true"
          className="h-3.5 w-3.5 animate-spin motion-reduce:animate-none"
        />
      )}
      {loading && loadingLabel ? loadingLabel : children}
    </button>
  );
}

export function Card({
  className,
  children,
  ...props
}: React.HTMLAttributes<HTMLDivElement>) {
  return (
    <div className={cn("tf-card", className)} {...props}>
      {children}
    </div>
  );
}

export function Spinner({ label }: { label?: string }) {
  return (
    <div
      role="status"
      aria-live="polite"
      className="tf-spinner flex items-center gap-2 text-sm"
    >
      <Loader2
        aria-hidden="true"
        className="h-4 w-4 animate-spin motion-reduce:animate-none"
      />
      {label ?? "加载中…"}
    </div>
  );
}

/** Keep results next to their action. Error and success are mutually exclusive. */
export function ActionFeedback({
  pending,
  error,
  message,
  onRetry,
}: {
  pending?: string;
  error?: string | null;
  message?: string | null;
  onRetry?: () => void;
}) {
  if (pending)
    return (
      <>
        <p
          role="status"
          aria-atomic="true"
          className="tf-feedback"
          data-tone="pending"
        >
          {pending}
        </p>
        <PendingHint />
      </>
    );
  if (error)
    return (
      <div role="alert" className="tf-feedback" data-tone="error">
        {error}
        {onRetry && (
          <Button className="ml-2" size="sm" onClick={onRetry}>
            重试
          </Button>
        )}
      </div>
    );
  if (message)
    return (
      <p
        role="status"
        aria-atomic="true"
        className="tf-feedback"
        data-tone="success"
      >
        {message}
      </p>
    );
  return null;
}

/** Mount for one operation; completion clears its timer. */
export function PendingHint({
  message = "仍在等待服务响应，请勿重复提交。",
}: {
  message?: string;
}) {
  const [slow, setSlow] = useState(false);
  useEffect(() => {
    const timer = window.setTimeout(() => setSlow(true), 12_000);
    return () => window.clearTimeout(timer);
  }, []);
  return slow ? (
    <p role="status" className="tf-feedback" data-tone="warning">
      {message}
    </p>
  ) : null;
}

export function ErrorBanner({
  message,
  onRetry,
}: {
  message: string;
  onRetry?: () => void;
}) {
  return (
    <div role="alert" className="tf-error">
      <AlertTriangle aria-hidden="true" className="mt-0.5 h-4 w-4 flex-none" />
      <p className="flex-1 text-sm">{message}</p>
      {onRetry && (
        <Button size="sm" onClick={onRetry}>
          重试
        </Button>
      )}
    </div>
  );
}

export function EmptyState({
  icon,
  title,
  description,
  action,
}: {
  icon?: React.ReactNode;
  title: string;
  description?: string;
  action?: React.ReactNode;
}) {
  return (
    <div className="tf-empty flex flex-col items-center justify-center px-6 py-12 text-center">
      {icon && <div className="tf-empty-icon mb-3">{icon}</div>}
      <p className="tf-empty-title text-sm font-semibold">{title}</p>
      {description && (
        <p className="tf-empty-description mt-1 max-w-md text-xs">
          {description}
        </p>
      )}
      {action && <div className="mt-4">{action}</div>}
    </div>
  );
}

export function Badge({
  children,
  tone = "brand",
}: {
  children: React.ReactNode;
  tone?: "brand" | "teal" | "slate" | "amber";
}) {
  return (
    <span className="tf-badge" data-tone={tone}>
      {children}
    </span>
  );
}
