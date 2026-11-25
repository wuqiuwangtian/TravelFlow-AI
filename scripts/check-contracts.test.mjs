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
  cpSync(
    path.join(root, "contracts/expense-create-candidate"),
    path.join(folder, "contracts/expense-create-candidate"),
    { recursive: true },
  );
  cpSync(
    path.join(root, "contracts/expense-create-v0.1.0"),
    path.join(folder, "contracts/expense-create-v0.1.0"),
    { recursive: true },
  );
  cpSync(
    path.join(root, "contracts/expense-patch-candidate"),
    path.join(folder, "contracts/expense-patch-candidate"),
    { recursive: true },
  );
  cpSync(
    path.join(root, "contracts/expense-patch-candidate.2"),
    path.join(folder, "contracts/expense-patch-candidate.2"),
    { recursive: true },
  );
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

test("tampering with a candidate contract file is rejected", () => {
  const f = fixture();
  try {
    const spec = path.join(f.folder, "contracts/expense-create-candidate/SPEC.md");
    writeFileSync(spec, readFileSync(spec, "utf8") + "\nTampered candidate\n");
    const result = spawnSync(process.execPath, ["scripts/check-contracts.mjs"], { cwd: f.folder, encoding: "utf8" });
    assert.notEqual(result.status, 0);
    assert.match(result.stderr, /Contract digest mismatch: contracts\/expense-create-candidate\/SPEC\.md/);
  } finally { f.close(); }
});

test("missing a candidate contract file is rejected", () => {
  const f = fixture();
  try {
    rmSync(path.join(f.folder, "contracts/expense-create-candidate/result-mapping.json"));
    const result = spawnSync(process.execPath, ["scripts/check-contracts.mjs"], { cwd: f.folder, encoding: "utf8" });
    assert.notEqual(result.status, 0);
    assert.match(result.stderr, /Contract directory file set mismatch: contracts\/expense-create-candidate/);
  } finally { f.close(); }
});

test("adding an unlisted file to candidate contract directory is rejected", () => {
  const f = fixture();
  try {
    writeFileSync(path.join(f.folder, "contracts/expense-create-candidate/extra.txt"), "extra\n");
    const result = spawnSync(process.execPath, ["scripts/check-contracts.mjs"], { cwd: f.folder, encoding: "utf8" });
    assert.notEqual(result.status, 0);
    assert.match(result.stderr, /Contract directory file set mismatch: contracts\/expense-create-candidate/);
  } finally { f.close(); }
});

test("removing the entire candidate contract directory fails with and without baseline", () => {
  const f = fixture();
  try {
    rmSync(path.join(f.folder, "contracts/expense-create-candidate"), { recursive: true, force: true });
    const noBase = spawnSync(process.execPath, ["scripts/check-contracts.mjs"], { cwd: f.folder, encoding: "utf8" });
    assert.notEqual(noBase.status, 0);
    assert.match(noBase.stderr, /Registered contract directory missing: contracts\/expense-create-candidate/);
    const withBase = f.check();
    assert.notEqual(withBase.status, 0);
    assert.match(withBase.stderr, /Registered contract directory missing: contracts\/expense-create-candidate/);
  } finally { f.close(); }
});

test("swapping mobile_host and mobile_android values in result-mapping is rejected even if manifest updated", () => {
  const f = fixture();
  try {
    const mappingPath = path.join(f.folder, "contracts/expense-create-candidate/result-mapping.json");
    const manifestPath = path.join(f.folder, "contracts/expense-create-candidate/manifest.json");
    const mapping = JSON.parse(readFileSync(mappingPath, "utf8"));
    const swapped = mapping.outcomes.lookup_unknown_busy.mobile_host;
    mapping.outcomes.lookup_unknown_busy.mobile_host = mapping.outcomes.lookup_unknown_busy.mobile_android;
    mapping.outcomes.lookup_unknown_busy.mobile_android = swapped;
    writeFileSync(mappingPath, JSON.stringify(mapping, null, 2) + "\n");
    const manifest = JSON.parse(readFileSync(manifestPath, "utf8"));
    manifest.files["result-mapping.json"] = createHash("sha256").update(readFileSync(mappingPath)).digest("hex");
    writeFileSync(manifestPath, JSON.stringify(manifest, null, 2) + "\n");
    const result = spawnSync(process.execPath, ["scripts/check-contracts.mjs"], { cwd: f.folder, encoding: "utf8" });
    assert.notEqual(result.status, 0);
    assert.match(result.stderr, /pending_busy|unknown/);
  } finally { f.close(); }
});

test("corrupting a canonical digest expectation is rejected even if manifest updated", () => {
  const f = fixture();
  try {
    const digestsPath = path.join(f.folder, "contracts/expense-create-candidate/canonical-digests.json");
    const manifestPath = path.join(f.folder, "contracts/expense-create-candidate/manifest.json");
    const digests = JSON.parse(readFileSync(digestsPath, "utf8"));
    digests.cases[0].sha256 = "0".repeat(64);
    writeFileSync(digestsPath, JSON.stringify(digests, null, 2) + "\n");
    const manifest = JSON.parse(readFileSync(manifestPath, "utf8"));
    manifest.files["canonical-digests.json"] = createHash("sha256").update(readFileSync(digestsPath)).digest("hex");
    writeFileSync(manifestPath, JSON.stringify(manifest, null, 2) + "\n");
    const result = spawnSync(process.execPath, ["scripts/check-contracts.mjs"], { cwd: f.folder, encoding: "utf8" });
    assert.notEqual(result.status, 0);
    assert.match(result.stderr, /Canonical SHA-256 mismatch/);
  } finally { f.close(); }
});

test("tampering with or removing the formal 0.1.0 contract bundle is rejected", () => {
  const f = fixture();
  try {
    const norm = path.join(f.folder, "contracts/expense-create-v0.1.0/normative-vectors.json");
    writeFileSync(norm, readFileSync(norm, "utf8") + " ");
    const tampered = spawnSync(process.execPath, ["scripts/check-contracts.mjs"], { cwd: f.folder, encoding: "utf8" });
    assert.notEqual(tampered.status, 0);
    assert.match(tampered.stderr, /Contract digest mismatch: contracts\/expense-create-v0\.1\.0\/normative-vectors\.json/);

    rmSync(path.join(f.folder, "contracts/expense-create-v0.1.0"), { recursive: true, force: true });
    const removed = spawnSync(process.execPath, ["scripts/check-contracts.mjs"], { cwd: f.folder, encoding: "utf8" });
    assert.notEqual(removed.status, 0);
    assert.match(removed.stderr, /Registered contract directory missing: contracts\/expense-create-v0\.1\.0/);
  } finally { f.close(); }
});

test("semantic drift between formal 0.1.0 and candidate 2 is rejected even if manifest updated", () => {
  const f = fixture();
  try {
    const normPath = path.join(f.folder, "contracts/expense-create-v0.1.0/normative-vectors.json");
    const manifestPath = path.join(f.folder, "contracts/expense-create-v0.1.0/manifest.json");
    const norm = JSON.parse(readFileSync(normPath, "utf8"));
    norm.defaults.base_command.payload.amount_cents = 8636;
    writeFileSync(normPath, JSON.stringify(norm, null, 2) + "\n");
    const manifest = JSON.parse(readFileSync(manifestPath, "utf8"));
    manifest.files["normative-vectors.json"] = createHash("sha256").update(readFileSync(normPath)).digest("hex");
    writeFileSync(manifestPath, JSON.stringify(manifest, null, 2) + "\n");
    const result = spawnSync(process.execPath, ["scripts/check-contracts.mjs"], { cwd: f.folder, encoding: "utf8" });
    assert.notEqual(result.status, 0);
    assert.match(result.stderr, /Formal 0\.1\.0 defaults drift from candidate 2/);
  } finally { f.close(); }
});
