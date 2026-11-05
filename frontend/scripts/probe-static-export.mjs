// P0隔离实验：检查现有路由／可选本地入口的静态打包边界，不改正式配置或.next。
import { cpSync, existsSync, mkdtempSync, rmSync, symlinkSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const args = process.argv.slice(2);
const portable = args[0] === "--portable-entry";
const output = portable && args.length === 3 && args[1] === "--output"
  ? path.resolve(args[2]) : null;
if (args.length && (!portable || !output)) {
  throw new Error("Usage: probe-static-export.mjs [--portable-entry --output NEW_DIRECTORY]");
}
if (output && existsSync(output)) throw new Error("Output directory must not already exist");
const temporary = mkdtempSync(path.join(tmpdir(), "travelflow-export-probe-"));
try {
  for (const name of [
    "app", "components", "lib", "styles", "public", "package.json",
    "pnpm-lock.yaml", "tsconfig.json", "next-env.d.ts", "postcss.config.mjs",
  ]) {
    if (name === "next-env.d.ts" && !existsSync(path.join(root, name))) continue;
    cpSync(path.join(root, name), path.join(temporary, name), { recursive: true });
  }
  if (portable) {
    rmSync(path.join(temporary, "app"), { recursive: true });
    cpSync(path.join(root, "prototypes/local-entry"), path.join(temporary, "app"), { recursive: true });
    cpSync(path.join(root, "app/globals.css"), path.join(temporary, "app/globals.css"));
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
  if (run.status === 0 && output) cpSync(path.join(temporary, "out"), output, {
    recursive: true, force: false, errorOnExist: true,
  });
  console.log(JSON.stringify({
    entry: portable ? "isolated_local_entry" : "production_routes",
    static_export: run.status === 0 ? "build_succeeded" : "blocked",
    build_exit_code: run.status,
    reason: blocker?.trim() ?? null,
    scope: "isolated static build only; not an Android runtime or packaging test",
  }, null, 2));
  // 已知阻塞是实验结论，不是生产构建通过；未知错误仍令实验命令失败。
  if (run.status !== 0 && (portable || !blocker)) {
    console.error(log);
    process.exitCode = 1;
  }
} finally {
  rmSync(temporary, { recursive: true, force: true });
}
