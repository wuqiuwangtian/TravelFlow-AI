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
  Route,
  Settings,
} from "lucide-react";

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
      <header className="tf-shell-header sticky top-0 z-50">
        <div className="mx-auto flex h-16 max-w-7xl items-center gap-4 px-5">
          <Link href="/" className="tf-brand tf-action-link">
            <Route aria-hidden="true" className="h-5 w-5" />
            <span>TravelFlow</span>
          </Link>

          <nav
            aria-label="主导航"
            className="flex min-w-0 flex-1 items-center gap-1 overflow-x-auto py-1"
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
                  className="tf-nav-link tf-action-link"
                >
                  <Icon aria-hidden="true" className="h-4 w-4" />
                  {item.label}
                </Link>
              );
            })}
          </nav>
        </div>
      </header>

      <main className="flex-1">{children}</main>

      <footer className="tf-shell-footer px-5 py-8 text-center text-xs">
        TravelFlow AI · 旅行数据保存在本机，AI 功能按需调用当前模型
      </footer>
    </div>
  );
}
