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

const candidateDir = path.join(root, "contracts/expense-create-candidate");
assert(existsSync(candidateDir), "Registered contract candidate directory missing: contracts/expense-create-candidate");
const expectedCandidateFiles = [
  "SPEC.md",
  "canonical-digests.json",
  "desktop-assertions.json",
  "normative-vectors.json",
  "result-mapping.json",
];
const candidateManifestPath = path.join(candidateDir, "manifest.json");
assert(existsSync(candidateManifestPath), "Candidate contract missing manifest.json");
const candidateManifest = JSON.parse(readFileSync(candidateManifestPath, "utf8"));
assert.equal(candidateManifest.contract_id, "travelflow.expense-create-candidate");
assert.equal(candidateManifest.contract_version, "0.1.0-candidate.1");
assert.equal(candidateManifest.status, "candidate");
assert.equal(candidateManifest.shared_contract_version, null);
assert.deepEqual(
  Object.keys(candidateManifest.files).sort(),
  expectedCandidateFiles,
  "Candidate contract manifest file list mismatch",
);
const actualEntries = readdirSync(candidateDir, { withFileTypes: true }).map((entry) => {
  assert(!entry.isSymbolicLink(), `Candidate contract cannot contain symlink: ${entry.name}`);
  assert(!entry.isDirectory(), `Candidate contract cannot contain unexpected subdir: ${entry.name}`);
  return entry.name;
}).sort();
assert.deepEqual(
  actualEntries,
  [...expectedCandidateFiles, "manifest.json"].sort(),
  "Candidate contract directory file set mismatch",
);
for (const [name, expectedSha] of Object.entries(candidateManifest.files)) {
  const filePath = path.join(candidateDir, name);
  assert(existsSync(filePath), `Candidate contract missing file: ${name}`);
  const actualSha = createHash("sha256").update(readFileSync(filePath)).digest("hex");
  assert.equal(actualSha, expectedSha, `Candidate contract digest mismatch: ${name}`);
}

const normative = JSON.parse(readFileSync(path.join(candidateDir, "normative-vectors.json"), "utf8"));
const desktopAssertions = JSON.parse(readFileSync(path.join(candidateDir, "desktop-assertions.json"), "utf8"));
const mapping = JSON.parse(readFileSync(path.join(candidateDir, "result-mapping.json"), "utf8"));
const canonicalDigests = JSON.parse(readFileSync(path.join(candidateDir, "canonical-digests.json"), "utf8"));
assert.equal(normative.contract_version, candidateManifest.contract_version);
assert.equal(desktopAssertions.contract_version, candidateManifest.contract_version);
assert.equal(mapping.contract_version, candidateManifest.contract_version);
assert.equal(canonicalDigests.contract_version, candidateManifest.contract_version);
assert.equal(mapping.confirmation_rule.minimal_receipt_sufficient_for_intent_confirmation, false);

// Assert separated mobile_host vs mobile_android observations per 9dec892 receipt.
for (const [outcomeName, entry] of Object.entries(mapping.outcomes)) {
  assert("desktop" in entry && "mobile_host" in entry && "mobile_android" in entry, `Outcome missing separated runtime columns: ${outcomeName}`);
}
assert.equal(mapping.outcomes.rejected_unauthorized.mobile_host.wrong_target_error, "denied");
assert.equal(mapping.outcomes.rejected_unauthorized.mobile_android.wrong_target_error, "scope_mismatch");
assert.equal(mapping.outcomes.rejected_unauthorized.mobile_android.unauthorized_lookup_error, "denied");
assert.equal(mapping.outcomes.lookup_unknown_busy.mobile_host.state, "unknown");
assert.equal(mapping.outcomes.lookup_unknown_busy.mobile_android.state, "pending_busy");
assert.equal(mapping.outcomes.rejected_unsupported_capability.mobile_android.error, "unsupported_capability");
assert.equal(mapping.outcomes.rejected_unsupported_capability.mobile_host.observed_9dec892.non_null_paid_on, "committed");
assert.equal(mapping.outcomes.rejected_unsupported_capability.mobile_host.observed_9dec892.non_null_day, "baseline_conflict");
assert.equal(mapping.outcomes.rejected_unsupported_capability.mobile_host.observed_9dec892.unsupported_currency_usd, "currency");
assert.equal(mapping.outcomes.lookup_committed.mobile_host.includes_intent_sha256, false);
assert.equal(mapping.outcomes.lookup_committed.mobile_android.includes_intent_sha256, false);
assert.equal(mapping.outcomes.committed_changed.mobile_host.kind, "committed");
assert.equal(mapping.outcomes.replayed_committed.mobile_android.kind, "replayed");

// Independently recompute canonical wrapper bytes and SHA-256 in Node JS.
const sortUtf16 = (val) => {
  if (Array.isArray(val)) return val.map(sortUtf16);
  if (val && typeof val === "object") {
    const outObj = {};
    for (const key of Object.keys(val).sort()) outObj[key] = sortUtf16(val[key]);
    return outObj;
  }
  return val;
};
const computedDigests = new Map();
for (const item of canonicalDigests.cases) {
  const wrapped = {
    canonical_profile: canonicalDigests.canonical_profile,
    channel: item.channel,
    intent: sortUtf16(JSON.parse(item.raw_json)),
  };
  const encoded = Buffer.from(JSON.stringify(wrapped), "utf8");
  assert(encoded.length <= 65536, `Canonical bytes exceed 65536 for ${item.id}`);
  const actualSha = createHash("sha256").update(encoded).digest("hex");
  assert.equal(encoded.toString("utf8"), item.canonical_json, `Canonical JSON mismatch for ${item.id}`);
  assert.equal(encoded.toString("hex"), item.canonical_utf8_hex, `Canonical UTF-8 hex mismatch for ${item.id}`);
  assert.equal(actualSha, item.sha256, `Canonical SHA-256 mismatch for ${item.id}`);
  computedDigests.set(item.id, actualSha);
}
for (const rel of canonicalDigests.relations) {
  const unique = new Set(rel.ids.map((id) => computedDigests.get(id)));
  if (rel.relation === "same") {
    assert.equal(unique.size, 1, `Expected same canonical digest for ${rel.ids.join(",")}`);
  } else {
    assert.equal(unique.size, rel.ids.length, `Expected distinct canonical digests for ${rel.ids.join(",")}`);
  }
}

const normativeIds = normative.scenarios.map((sc) => sc.id).sort();
assert.deepEqual(Object.keys(desktopAssertions.scenarios).sort(), normativeIds, "Desktop assertions scenario IDs mismatch");
for (const sc of normative.scenarios) {
  const deskSteps = desktopAssertions.scenarios[sc.id];
  assert.deepEqual(Object.keys(deskSteps).sort(), sc.steps.map((st) => st.name).sort(), `Desktop assertions step mismatch in ${sc.id}`);
  for (const st of sc.steps) {
    assert(st.expected.business_outcome in mapping.outcomes, `Unmapped business_outcome in ${sc.id}/${st.name}: ${st.expected.business_outcome}`);
    assert(!("desktop_raw" in st.expected), `Normative vector must not embed desktop_raw in ${sc.id}/${st.name}`);
  }
}
console.log(`Expense-create candidate ${candidateManifest.contract_version} manifest, canonical digests, and bundle checks passed.`);

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
