import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { readFileSync, readdirSync, existsSync } from "node:fs";
import { execFileSync } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const args = process.argv.slice(2);
assert(args.length === 0 || (args.length === 2 && args[0] === "--baseline"), "Usage: check-contracts.mjs [--baseline FULL_COMMIT_SHA]");
const baseline = args[1] ?? process.env.CONTRACT_BASE_SHA;
if (baseline) assert.match(baseline, /^[a-f0-9]{40}$/, "Contract baseline must be a full commit SHA");
if (process.env.GITHUB_ACTIONS === "true") assert(baseline, "CI must supply CONTRACT_BASE_SHA");
const bundle = path.join(root, "contracts/ledger-v1");
const manifest = JSON.parse(readFileSync(path.join(bundle, "manifest.json"), "utf8"));
assert.equal(manifest.contract_id, "travelflow.ledger-projection");
assert.equal(manifest.contract_version, "0.1.0");
assert.equal(manifest.status, "experimental");
assert.deepEqual(Object.keys(manifest.files).sort(), ["SPEC.md", "ledger-vectors.json"]);
for (const [name, expected] of Object.entries(manifest.files)) {
  const actual = createHash("sha256").update(readFileSync(path.join(bundle, name))).digest("hex");
  assert.equal(actual, expected, `Contract drift: ${name}; publish a new version instead of overwriting`);
}
const corpus = JSON.parse(readFileSync(path.join(bundle, "ledger-vectors.json"), "utf8"));
assert.equal(corpus.schema_version, manifest.fixture_schema_version);
assert.equal(corpus.cases.length, 14);
console.log("Ledger contract 0.1.0 manifest and fixture checks passed.");

// 摘要一致不代表版本不可改：同时更新内容与摘要也必须被基线比较拒绝。
if (baseline && !/^0{40}$/.test(baseline)) {
  const git = (...parameters) => execFileSync("git", parameters, { cwd: root, maxBuffer: 4 * 1024 * 1024 });
  git("cat-file", "-e", `${baseline}^{commit}`); // 浅克隆／不存在的基线不能静默放行。
  const publishedFiles = git("ls-tree", "-r", "--name-only", baseline, "--", "contracts/").toString("utf8").trim().split("\n").filter(Boolean);
  const versions = publishedFiles.filter(name => /^contracts\/[^/]+\/manifest\.json$/.test(name)).map(name => path.posix.dirname(name));
  const walk = folder => readdirSync(path.join(root, folder), { withFileTypes: true }).flatMap(entry => {
    assert(!entry.isSymbolicLink(), "Versioned contract files cannot be symlinks");
    const name = `${folder}/${entry.name}`;
    return entry.isDirectory() ? walk(name) : [name];
  });
  for (const version of versions) {
    assert(existsSync(path.join(root, version)), `Published contract removed: ${version}`);
    const before = publishedFiles.filter(name => name.startsWith(`${version}/`)).sort();
    assert.deepEqual(walk(version).sort(), before, `Published contract file set changed: ${version}`);
    for (const name of before) {
      assert(readFileSync(path.join(root, name)).equals(git("show", `${baseline}:${name}`)), `Published contract changed: ${name}; create a new version`);
    }
  }
  console.log(`Published contract versions unchanged from ${baseline}.`);
} else {
  console.log("Digest checks only: no published Git baseline (or initial repository push).");
}
