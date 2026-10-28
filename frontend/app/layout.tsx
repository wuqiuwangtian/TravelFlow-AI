import type { Metadata } from "next";

import { ThemeSync } from "@/components/theme-sync";
import { THEME_INIT_SCRIPT } from "@/lib/theme";

import { AppShell } from "@/components/app-shell";

import "./globals.css";

export const metadata: Metadata = {
  title: "TravelFlow AI · 旅行助手",
  description:
    "本地运行的 AI 旅行规划助手：多轮对话收集需求、联网检索真实信息、生成可执行路书",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="zh-CN" data-theme="paper" suppressHydrationWarning>
      <head>
        <script
          id="travelflow-theme-bootstrap"
          dangerouslySetInnerHTML={{ __html: THEME_INIT_SCRIPT }}
        />
      </head>
      <body className="min-h-screen antialiased">
        <ThemeSync />
        <AppShell>{children}</AppShell>
      </body>
    </html>
  );
}
