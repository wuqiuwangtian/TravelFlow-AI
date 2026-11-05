import "@/app/globals.css";

export default function LocalLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="zh-CN" data-theme="paper">
      <body>{children}</body>
    </html>
  );
}
