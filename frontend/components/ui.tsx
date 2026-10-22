"use client";

import { AlertTriangle, Loader2 } from "lucide-react";

import { cn } from "@/lib/utils";

/* ------------------------------------------------------------------
   极简 UI 基础组件。
   刻意不引入组件库 —— 界面结构简单，手写更可控、依赖更少。
   ------------------------------------------------------------------ */

type ButtonProps = React.ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: "primary" | "default" | "ghost" | "danger";
  size?: "sm" | "md" | "lg";
  loading?: boolean;
};

export function Button({
  className,
  variant = "default",
  size = "md",
  loading = false,
  disabled,
  children,
  ...props
}: ButtonProps) {
  const variants = {
    primary:
      "bg-gradient-to-r from-brand-500 to-teal-500 text-white font-semibold shadow-sm hover:brightness-105 border-transparent",
    default:
      "bg-white text-slate-700 border-slate-200 hover:border-brand-400 hover:text-brand-600",
    ghost:
      "bg-transparent text-slate-600 border-transparent hover:bg-slate-100",
    danger: "bg-white text-red-600 border-red-200 hover:bg-red-50",
  };
  const sizes = {
    sm: "px-2.5 py-1 text-xs rounded-md gap-1",
    md: "px-3.5 py-1.5 text-sm rounded-lg gap-1.5",
    lg: "px-5 py-2.5 text-sm rounded-xl gap-2",
  };

  return (
    <button
      className={cn(
        "inline-flex items-center justify-center border transition disabled:cursor-not-allowed disabled:opacity-50",
        variants[variant],
        sizes[size],
        className,
      )}
      disabled={disabled || loading}
      {...props}
    >
      {loading && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
      {children}
    </button>
  );
}

export function Card({
  className,
  children,
}: {
  className?: string;
  children: React.ReactNode;
}) {
  return (
    <div
      className={cn(
        "rounded-xl border border-slate-200 bg-white shadow-sm",
        className,
      )}
    >
      {children}
    </div>
  );
}

export function Spinner({ label }: { label?: string }) {
  return (
    <div className="flex items-center gap-2 text-sm text-brand-600">
      <Loader2 className="h-4 w-4 animate-spin" />
      {label ?? "加载中…"}
    </div>
  );
}

export function ErrorBanner({
  message,
  onRetry,
}: {
  message: string;
  onRetry?: () => void;
}) {
  return (
    <div
      role="alert"
      className="flex items-start gap-2.5 rounded-lg border border-amber-300 border-l-4 border-l-amber-500 bg-amber-50 px-4 py-3"
    >
      <AlertTriangle className="mt-0.5 h-4 w-4 flex-none text-amber-600" />
      <p className="flex-1 text-sm text-amber-900">{message}</p>
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
    <div className="flex flex-col items-center justify-center rounded-xl border border-dashed border-slate-300 bg-white/60 px-6 py-12 text-center">
      {icon && <div className="mb-3 text-slate-300">{icon}</div>}
      <p className="text-sm font-semibold text-slate-600">{title}</p>
      {description && (
        <p className="mt-1 max-w-md text-xs text-slate-400">{description}</p>
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
  const tones = {
    brand: "bg-brand-50 text-brand-700 border-brand-200",
    teal: "bg-teal-50 text-teal-700 border-teal-200",
    slate: "bg-slate-100 text-slate-600 border-slate-200",
    amber: "bg-amber-50 text-amber-700 border-amber-200",
  };
  return (
    <span
      className={cn(
        "inline-flex items-center rounded-full border px-2.5 py-0.5 text-[11px] font-semibold",
        tones[tone],
      )}
    >
      {children}
    </span>
  );
}
