// 在新目录生成候选运行时工程；不安装SDK、不接受许可、不改正式前端依赖。
import { cpSync, existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import path from "node:path";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import ts from "typescript";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const source = path.join(root, "prototypes/android-runtime");
const args = process.argv.slice(2);
if (args.length !== 2 || args[0] !== "--output") throw new Error("Usage: prepare-android-probe.mjs --output NEW_DIRECTORY");
const output = path.resolve(args[1]);
if (existsSync(output)) throw new Error("Output directory must not already exist");
mkdirSync(output, { recursive: true });
for (const name of ["package.json", "pnpm-lock.yaml", "capacitor.config.json"]) {
  cpSync(path.join(source, name), path.join(output, name));
}

function run(command, parameters) {
  const result = spawnSync(command, parameters, { cwd: output, stdio: "inherit" });
  if (result.error) throw result.error;
  if (result.status !== 0) throw new Error(`Isolated command failed: ${command}`);
}

if (process.platform === "win32") {
  // 命令固定；用户路径只通过cwd传递，不拼进shell。
  run(process.env.ComSpec ?? "cmd.exe", ["/d", "/s", "/c", "pnpm install --frozen-lockfile --ignore-scripts"]);
} else {
  run("pnpm", ["install", "--frozen-lockfile", "--ignore-scripts"]);
}
const www = path.join(output, "www");
mkdirSync(www);
for (const name of ["index.html", "probe.js"]) cpSync(path.join(source, name), path.join(www, name));
for (const name of ["tokens.css", "components.css"]) cpSync(path.join(root, "styles", name), path.join(www, name));
cpSync(path.join(output, "node_modules/@capacitor/core/dist/index.js"), path.join(www, "capacitor-core.js"));
cpSync(path.join(output, "node_modules/@capacitor/core/LICENSE"), path.join(www, "CAPACITOR-LICENSE.txt"));
writeFileSync(path.join(www, "ledger-core.js"), ts.transpileModule(
  readFileSync(path.join(root, "prototypes/domain/ledger.ts"), "utf8"),
  { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext } },
).outputText);

const cli = path.join(output, "node_modules/@capacitor/cli/bin/capacitor");
run(process.execPath, [cli, "add", "android"]);
const java = path.join(output, "android/app/src/main/java/org/travelflow/p0");
for (const name of ["MainActivity.java", "ProbeStorePlugin.java"]) cpSync(path.join(source, name), path.join(java, name));
const manifest = path.join(output, "android/app/src/main/AndroidManifest.xml");
writeFileSync(manifest, readFileSync(manifest, "utf8").replace('android:allowBackup="true"', 'android:allowBackup="false"'));
// 仅debug测试服务可用明文；默认与release仍拒绝明文流量。
const debug = path.join(output, "android/app/src/debug");
mkdirSync(path.join(debug, "res/xml"), { recursive: true });
writeFileSync(path.join(debug, "AndroidManifest.xml"), '<manifest xmlns:android="http://schemas.android.com/apk/res/android"><application android:networkSecurityConfig="@xml/p0_network_security" /></manifest>\n');
writeFileSync(path.join(debug, "res/xml/p0_network_security.xml"), '<network-security-config><base-config cleartextTrafficPermitted="false"/><domain-config cleartextTrafficPermitted="true"><domain>10.0.2.2</domain></domain-config></network-security-config>\n');
console.log(JSON.stringify({
  project: output,
  appId: "org.travelflow.p0",
  phase: "prepared_only",
  sdkInstalledByThisCommand: false,
  apkBuilt: false,
  note: "Requires an approved Android SDK and JDK21 before build; no emulator/device test yet.",
}, null, 2));
