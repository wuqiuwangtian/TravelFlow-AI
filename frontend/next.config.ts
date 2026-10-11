import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  reactStrictMode: true,
  // 独立输出，便于 M6 的 Docker 部署（镜像体积更小）
  output: "standalone",
};

export default nextConfig;
