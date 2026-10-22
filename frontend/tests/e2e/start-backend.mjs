import { spawn } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";

const backend = fileURLToPath(new URL("../../../backend/", import.meta.url));
const python =
  process.env.TRAVELFLOW_TEST_PYTHON ||
  path.join(
    backend,
    ".venv",
    process.platform === "win32" ? "Scripts/python.exe" : "bin/python",
  );
const child = spawn(python, [path.join(backend, "tests/browser_server.py")], {
  cwd: backend,
  stdio: "inherit",
});
child.on("error", (error) => {
  console.error(error.message);
  process.exitCode = 1;
});
child.on("exit", (code) => {
  process.exitCode = code ?? 1;
});
for (const signal of ["SIGINT", "SIGTERM"]) {
  process.on(signal, () => child.kill(signal));
}
