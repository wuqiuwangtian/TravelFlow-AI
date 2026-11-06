import type { NextConfig } from "next";

const desktopDist = process.env.TRAVELFLOW_NEXT_DIST_DIR;
if (desktopDist && ![".next-desktop-wsl", ".next-desktop-linux", ".next-desktop-windows-native"].includes(desktopDist)) {
  throw new Error("Invalid desktop build directory");
}
const nextConfig: NextConfig = {
  distDir: desktopDist ?? ".next",
  typescript: desktopDist ? { tsconfigPath: desktopDist + "-tsconfig.json" } : undefined,
  reactStrictMode: true,
  // 独立输出，便于 M6 的 Docker 部署（镜像体积更小）
  output: desktopDist ? undefined : "standalone",
};

export default nextConfig;
