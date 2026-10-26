"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  CircleHelp,
  BookOpen,
  MapPin,
  GitBranch,
  LayoutGrid,
  MessageSquare,
  Plane,
  Settings,
} from "lucide-react";

import { cn } from "@/lib/utils";

const NAV = [
  { href: "/", label: "工作台", icon: LayoutGrid },
  { href: "/sessions", label: "会话", icon: MessageSquare },
  { href: "/recaps", label: "旅行回顾", icon: BookOpen },
  { href: "/career", label: "旅行生涯", icon: MapPin },
  { href: "/compare", label: "分支对比", icon: GitBranch },
  { href: "/settings", label: "设置", icon: Settings },
  { href: "/help", label: "帮助", icon: CircleHelp },
];

export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();

  return (
    <div className="flex min-h-screen flex-col">
      <header className="sticky top-0 z-50 bg-slate-900 text-white shadow-sm">
        <div className="mx-auto flex h-14 max-w-6xl items-center gap-1 px-5">
          <Link href="/" className="mr-5 flex items-center gap-2 font-bold">
            <Plane className="h-5 w-5 text-brand-400" />
            <span>
              Travel<span className="text-brand-400">Flow</span>
            </span>
          </Link>

          <nav
            aria-label="主导航"
            className="flex items-center gap-1 overflow-x-auto"
          >
            {NAV.map((item) => {
              const active =
                item.href === "/"
                  ? pathname === "/" || pathname === "/preferences"
                  : pathname.startsWith(item.href) ||
                    (item.href === "/sessions" &&
                      /^\/(chat|workflow)\//.test(pathname));
              const Icon = item.icon;
              return (
                <Link
                  key={item.href}
                  href={item.href}
                  aria-current={
                    active
                      ? pathname === item.href
                        ? "page"
                        : "location"
                      : undefined
                  }
                  className={cn(
                    "flex items-center gap-1.5 whitespace-nowrap rounded-lg px-3 py-1.5 text-sm transition",
                    active
                      ? "bg-white/15 font-semibold text-white"
                      : "text-slate-300 hover:bg-white/10 hover:text-white",
                  )}
                >
                  <Icon className="h-4 w-4" />
                  {item.label}
                </Link>
              );
            })}
          </nav>
        </div>
      </header>

      <main className="flex-1">{children}</main>

      <footer className="py-8 text-center text-xs text-slate-400">
        TravelFlow AI · 旅行数据保存在本机，AI 功能按需调用当前模型
      </footer>
    </div>
  );
}
