const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { createRequire } = require("node:module");

/** Shared launcher for the archived Windows checks; never uses application data. */
function nativeRuntime(name) {
  const args = process.argv.slice(2);
  if (args.includes("--help")) {
    console.log(
      "Windows Node: <driver.cjs> [--output <directory>] [--playwright <package.json>] [--edge <msedge.exe>]",
    );
    process.exit(0);
  }
  if (process.platform !== "win32") {
    throw new Error(
      "Run with Windows Node; the normal Playwright suite runs on Linux.",
    );
  }
  const options = {};
  for (let i = 0; i < args.length; i += 2) {
    if (
      !["--output", "--playwright", "--edge"].includes(args[i]) ||
      !args[i + 1]
    ) {
      throw new Error("Unknown or incomplete option; use --help.");
    }
    options[args[i]] = args[i + 1];
  }
  const packageFile =
    options["--playwright"] ||
    path.join(
      process.env.USERPROFILE,
      ".cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright/package.json",
    );
  const { chromium, request } = createRequire(path.resolve(packageFile))("./");
  const edge =
    options["--edge"] ||
    path.join(
      process.env["ProgramFiles(x86)"] || "C:\\Program Files (x86)",
      "Microsoft/Edge/Application/msedge.exe",
    );
  const directory = path.resolve(
    options["--output"] ||
      path.join(__dirname, "../../../frontend/test-results/native-qa", name),
  );
  fs.mkdirSync(directory, { recursive: true });
  return {
    chromium,
    request,
    edge,
    output: (file) => path.join(directory, file),
  };
}

async function assertTestServer(api) {
  try {
    // These fixture routes accept POST only; GET confirms their presence without creating data.
    for (const name of ["moves", "career"]) {
      const response = await api.get(`/api/backend/test/${name}`);
      assert.equal(
        response.status(),
        405,
        "Start the isolated browser_server.py and QA frontend first; no fixtures were written.",
      );
    }
  } catch (error) {
    await api.dispose();
    throw error;
  }
}

module.exports = { nativeRuntime, assertTestServer };
