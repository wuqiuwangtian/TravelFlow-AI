// P0隔离实验：验证现有页面能否直接作为Android静态资源，不改正式配置或.next。
import { cpSync, existsSync, mkdtempSync, rmSync, symlinkSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const temporary = mkdtempSync(path.join(tmpdir(), "travelflow-export-probe-"));
try {
  for (const name of [
    "app", "components", "lib", "styles", "public", "package.json",
    "pnpm-lock.yaml", "tsconfig.json", "next-env.d.ts",
  ]) {
    if (name === "next-env.d.ts" && !existsSync(path.join(root, name))) continue;
    cpSync(path.join(root, name), path.join(temporary, name), { recursive: true });
  }
  symlinkSync(
    path.join(root, "node_modules"), path.join(temporary, "node_modules"),
    process.platform === "win32" ? "junction" : "dir",
  );
  writeFileSync(
    path.join(temporary, "next.config.ts"),
    'export default { output: "export", reactStrictMode: true };\n',
  );
  const run = spawnSync(process.execPath, [
    path.join(root, "node_modules/next/dist/bin/next"), "build", "--webpack",
  ], {
    cwd: temporary,
    env: { ...process.env, NEXT_TELEMETRY_DISABLED: "1" },
    encoding: "utf8",
    timeout: 120_000,
    maxBuffer: 10 * 1024 * 1024,
  });
  if (run.error) throw run.error;
  const log = run.stdout + run.stderr;
  const blocker = log.split("\n").find((line) =>
    line.includes("missing") && line.includes("generateStaticParams()"),
  );
  console.log(JSON.stringify({
    static_export: run.status === 0 ? "build_succeeded" : "blocked",
    build_exit_code: run.status,
    reason: blocker?.trim() ?? null,
    scope: "isolated static build only; not an Android runtime or packaging test",
  }, null, 2));
  // 已知阻塞是实验结论，不是生产构建通过；未知错误仍令实验命令失败。
  if (run.status !== 0 && !blocker) {
    console.error(log);
    process.exitCode = 1;
  }
} finally {
  rmSync(temporary, { recursive: true, force: true });
}
