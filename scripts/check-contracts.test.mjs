import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { execFileSync, spawnSync } from "node:child_process";
import { cpSync, mkdtempSync, readFileSync, rmSync, writeFileSync, mkdirSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
function fixture() {
  const folder = mkdtempSync(path.join(tmpdir(), "travelflow-contract-guard-"));
  mkdirSync(path.join(folder, "scripts"));
  cpSync(path.join(root, "scripts/check-contracts.mjs"), path.join(folder, "scripts/check-contracts.mjs"));
  cpSync(path.join(root, "contracts/ledger-v1"), path.join(folder, "contracts/ledger-v1"), { recursive: true });
  const git = (...args) => execFileSync("git", args, { cwd: folder, encoding: "utf8", stdio: ["ignore", "pipe", "pipe"] }).trim();
  git("init", "-q");
  git("config", "user.name", "Contract test");
  git("config", "user.email", "contract-test@example.invalid");
  git("config", "commit.gpgsign", "false");
  git("config", "core.hooksPath", path.join(folder, "no-hooks"));
  git("add", ".");
  git("commit", "-qm", "Published fixture");
  const baseline = git("rev-parse", "HEAD");
  const check = (...args) => spawnSync(process.execPath, ["scripts/check-contracts.mjs", "--baseline", baseline, ...args], { cwd: folder, encoding: "utf8" });
  return { folder, check, close: () => rmSync(folder, { recursive: true, force: true }) };
}

test("published content remains valid; new version and ordinary docs may be added", () => {
  const f = fixture();
  try {
    mkdirSync(path.join(f.folder, "contracts/next-version"));
    writeFileSync(path.join(f.folder, "contracts/next-version/proposal.md"), "New version proposal\n");
    writeFileSync(path.join(f.folder, "contracts/README.md"), "Updated maintenance instructions\n");
    assert.equal(f.check().status, 0);
  } finally { f.close(); }
});

test("recalculating the digest cannot overwrite published semantics", () => {
  const f = fixture();
  try {
    const spec = path.join(f.folder, "contracts/ledger-v1/SPEC.md");
    writeFileSync(spec, readFileSync(spec, "utf8") + "\nChanged published meaning\n");
    const manifest = path.join(f.folder, "contracts/ledger-v1/manifest.json");
    const data = JSON.parse(readFileSync(manifest, "utf8"));
    data.files["SPEC.md"] = createHash("sha256").update(readFileSync(spec)).digest("hex");
    writeFileSync(manifest, JSON.stringify(data) + "\n");
    const result = f.check();
    assert.notEqual(result.status, 0);
    assert.match(result.stderr, /Published contract changed/);
  } finally { f.close(); }
});

test("adding files to an existing version is rejected", () => {
  const f = fixture();
  try {
    writeFileSync(path.join(f.folder, "contracts/ledger-v1/new-rule.md"), "Hidden new rule\n");
    const result = f.check();
    assert.notEqual(result.status, 0);
    assert.match(result.stderr, /Published contract file set changed/);
  } finally { f.close(); }
});

test("an unavailable baseline fails rather than silently skipping immutability", () => {
  const f = fixture();
  try {
    const result = spawnSync(process.execPath, ["scripts/check-contracts.mjs", "--baseline", "1".repeat(40)], { cwd: f.folder, encoding: "utf8" });
    assert.notEqual(result.status, 0);
  } finally { f.close(); }
});
