import assert from "node:assert/strict";
import { test } from "node:test";
import { mkdtempSync, mkdirSync, writeFileSync, readFileSync, rmSync, chmodSync, existsSync } from "node:fs";
import { spawnSync } from "node:child_process";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

for (const mode of ["backend-timeout", "backend-http500", "frontend-timeout", "ready"]) {
  test(`source launcher readiness and owned-service cleanup: ${mode}`, { skip: process.platform === "win32" }, () => {
    const folder = mkdtempSync(path.join(tmpdir(), "travelflow-start-"));
    try {
      mkdirSync(path.join(folder, "backend"));
      mkdirSync(path.join(folder, "frontend"));
      mkdirSync(path.join(folder, "bin"));
      writeFileSync(path.join(folder, "backend/.env.example"), "# synthetic fixture\n");
      // 只将副本日志重定向到临时目录，避免覆盖实际源码启动日志。
      writeFileSync(path.join(folder, "start.sh"), readFileSync(path.join(root, "start.sh"), "utf8").replaceAll("/tmp/travelflow-", `${folder}/travelflow-`));
      const commands = {
        uv: 'if [ "$1" = --version ]; then echo "uv fixture"; exit 0; fi\nif [ "$1" = sync ]; then exit 0; fi\necho $$ >> "$FIXTURE_ROOT/pids"\nif [ "$FIXTURE_MODE" = ready ]; then exec /bin/sleep 0.2; fi\nexec /bin/sleep 5\n',
        pnpm: 'if [ "$1" = install ]; then exit 0; fi\necho $$ >> "$FIXTURE_ROOT/pids"\nif [ "$FIXTURE_MODE" = ready ]; then exec /bin/sleep 0.2; fi\nexec /bin/sleep 5\n',
        seq: 'echo 1\n',
        sleep: 'exit 0\n',
        curl: `count=0
[ ! -f "$FIXTURE_ROOT/count" ] || count=$(cat "$FIXTURE_ROOT/count")
count=$((count+1)); echo "$count" > "$FIXTURE_ROOT/count"
[ "$count" -gt 2 ] || exit 7
[ "$FIXTURE_MODE" != backend-timeout ] || exit 7
[ "$FIXTURE_MODE" != ready ] || exit 0
if [ "$FIXTURE_MODE" = backend-http500 ]; then
  for arg in "$@"; do [ "$arg" != --fail ] || exit 22; done
  exit 0
fi
case "$*" in *api/system/health*) exit 0;; *) exit 7;; esac
`,
        "xdg-open": 'touch "$FIXTURE_ROOT/browser-opened"\n',
        wslview: 'touch "$FIXTURE_ROOT/browser-opened"\n',
        "cmd.exe": 'touch "$FIXTURE_ROOT/browser-opened"\n',
        "explorer.exe": 'touch "$FIXTURE_ROOT/browser-opened"\n',
        open: 'touch "$FIXTURE_ROOT/browser-opened"\n',
      };
      for (const [name, body] of Object.entries(commands)) {
        const target = path.join(folder, "bin", name);
        writeFileSync(target, `#!/bin/sh\n${body}`); chmodSync(target, 0o755);
      }
      const result = spawnSync("bash", ["start.sh"], {
        cwd: folder, timeout: 3000, killSignal: "SIGKILL", encoding: "utf8",
        env: { ...process.env, NODE_ENV: "development", PATH: `${folder}/bin:${process.env.PATH}`, FIXTURE_ROOT: folder, FIXTURE_MODE: mode },
      });
      if (mode === "ready") {
        assert.equal(result.status, 0, result.stdout + result.stderr);
        assert.match(result.stdout, /TravelFlow AI 已启动/);
        assert(existsSync(path.join(folder, "browser-opened")));
      } else {
        assert.equal(result.status, 1, result.stdout + result.stderr);
        assert.doesNotMatch(result.stdout, /TravelFlow AI 已启动/);
        assert.match(result.stderr, /等待后端超时|等待前端超时/);
        assert(!existsSync(path.join(folder, "browser-opened")));
      }
      const pids = readFileSync(path.join(folder, "pids"), "utf8").trim().split(/\s+/).filter(value => /^[1-9]\d*$/.test(value)).map(Number).filter(pid => pid > 1);
      for (const pid of pids) assert.throws(() => process.kill(pid, 0), { code: "ESRCH" });
    } finally {
      // 即使断言失败或测试超时，也只清理本测试创建并记录的假服务。
      if (existsSync(path.join(folder, "pids"))) {
        for (const value of readFileSync(path.join(folder, "pids"), "utf8").trim().split(/\s+/)) {
          if (!/^[1-9]\d*$/.test(value) || Number(value) <= 1) continue;
          try { process.kill(Number(value), "SIGKILL"); } catch (error) { if (error.code !== "ESRCH") throw error; }
        }
      }
      rmSync(folder, { recursive: true, force: true });
    }
  });
}
