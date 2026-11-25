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

const expectedExpenseFiles = [
  "SPEC.md",
  "canonical-digests.json",
  "desktop-assertions.json",
  "normative-vectors.json",
  "result-mapping.json",
];

const sortUtf16 = (val) => {
  if (Array.isArray(val)) return val.map(sortUtf16);
  if (val && typeof val === "object") {
    const outObj = {};
    for (const key of Object.keys(val).sort()) outObj[key] = sortUtf16(val[key]);
    return outObj;
  }
  return val;
};

function verifyExpenseBundle(relDir, expectedMeta) {
  const dir = path.join(root, relDir);
  assert(existsSync(dir), `Registered contract directory missing: ${relDir}`);
  const manifestPath = path.join(dir, "manifest.json");
  assert(existsSync(manifestPath), `Contract missing manifest.json: ${relDir}`);
  const manifestObj = JSON.parse(readFileSync(manifestPath, "utf8"));
  assert.equal(manifestObj.contract_id, expectedMeta.contract_id);
  assert.equal(manifestObj.contract_version, expectedMeta.contract_version);
  assert.equal(manifestObj.status, expectedMeta.status);
  assert.equal(manifestObj.shared_contract_version, expectedMeta.shared_contract_version);
  assert.deepEqual(
    Object.keys(manifestObj.files).sort(),
    expectedExpenseFiles,
    `Contract manifest file list mismatch: ${relDir}`,
  );
  const actualEntries = readdirSync(dir, { withFileTypes: true }).map((entry) => {
    assert(!entry.isSymbolicLink(), `Contract cannot contain symlink: ${relDir}/${entry.name}`);
    assert(!entry.isDirectory(), `Contract cannot contain unexpected subdir: ${relDir}/${entry.name}`);
    return entry.name;
  }).sort();
  assert.deepEqual(
    actualEntries,
    [...expectedExpenseFiles, "manifest.json"].sort(),
    `Contract directory file set mismatch: ${relDir}`,
  );
  for (const [name, expectedSha] of Object.entries(manifestObj.files)) {
    const filePath = path.join(dir, name);
    assert(existsSync(filePath), `Contract missing file: ${relDir}/${name}`);
    const actualSha = createHash("sha256").update(readFileSync(filePath)).digest("hex");
    assert.equal(actualSha, expectedSha, `Contract digest mismatch: ${relDir}/${name}`);
  }

  const normative = JSON.parse(readFileSync(path.join(dir, "normative-vectors.json"), "utf8"));
  const desktopAssertions = JSON.parse(readFileSync(path.join(dir, "desktop-assertions.json"), "utf8"));
  const mapping = JSON.parse(readFileSync(path.join(dir, "result-mapping.json"), "utf8"));
  const canonicalDigests = JSON.parse(readFileSync(path.join(dir, "canonical-digests.json"), "utf8"));
  assert.equal(normative.contract_id, manifestObj.contract_id);
  assert.equal(normative.contract_version, manifestObj.contract_version);
  assert.equal(desktopAssertions.contract_id, manifestObj.contract_id);
  assert.equal(desktopAssertions.contract_version, manifestObj.contract_version);
  assert.equal(mapping.contract_id, manifestObj.contract_id);
  assert.equal(mapping.contract_version, manifestObj.contract_version);
  assert.equal(canonicalDigests.contract_id, manifestObj.contract_id);
  assert.equal(canonicalDigests.contract_version, manifestObj.contract_version);
  assert.equal(mapping.confirmation_rule.minimal_receipt_sufficient_for_intent_confirmation, false);

  for (const [outcomeName, entry] of Object.entries(mapping.outcomes)) {
    assert("desktop" in entry && "mobile_host" in entry && "mobile_android" in entry, `Outcome missing separated runtime columns: ${outcomeName}`);
  }
  assert.equal(mapping.outcomes.rejected_unauthorized.mobile_host.wrong_target_error, "denied");
  for (const code of ["category", "budget_link"]) {
    assert(mapping.outcomes.rejected_invalid_input.mobile_host.errors.includes(code));
    assert(!mapping.outcomes.rejected_invalid_input.mobile_android.errors.includes(code));
  }
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
  for (const platform of ["mobile_host", "mobile_android"]) {
    for (const [name, replayed] of [["committed_changed", false], ["replayed_committed", true]]) {
      const observed = mapping.outcomes[name][platform];
      assert.equal(observed.kind, "committed");
      assert.equal(observed.replayed, replayed);
    }
  }

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
  console.log(`${expectedMeta.label} ${manifestObj.contract_version} manifest, canonical digests, and bundle checks passed.`);
  return { manifest: manifestObj, normative, desktopAssertions, mapping, canonicalDigests };
}

const candidateBundle = verifyExpenseBundle("contracts/expense-create-candidate", {
  label: "Expense-create candidate",
  contract_id: "travelflow.expense-create-candidate",
  contract_version: "0.1.0-candidate.2",
  status: "candidate",
  shared_contract_version: null,
});

const formalBundle = verifyExpenseBundle("contracts/expense-create-v0.1.0", {
  label: "Expense-create formal contract",
  contract_id: "travelflow.expense-create",
  contract_version: "0.1.0",
  status: "frozen",
  shared_contract_version: "0.1.0",
});

assert.equal(formalBundle.manifest.source_candidate_commit, "94ec1fb");
assert.equal(formalBundle.manifest.mobile_review_commit, "8ba3365");
assert.deepEqual(formalBundle.normative.defaults, candidateBundle.normative.defaults, "Formal 0.1.0 defaults drift from candidate 2");
assert.deepEqual(formalBundle.normative.scenarios, candidateBundle.normative.scenarios, "Formal 0.1.0 36-step normative scenarios drift from candidate 2");
assert.deepEqual(formalBundle.canonicalDigests.wrapper_schema, candidateBundle.canonicalDigests.wrapper_schema, "Formal 0.1.0 canonical wrapper_schema drifts from candidate 2");
assert.deepEqual(formalBundle.canonicalDigests.cases, candidateBundle.canonicalDigests.cases, "Formal 0.1.0 canonical digest cases drift from candidate 2");
assert.deepEqual(formalBundle.canonicalDigests.relations, candidateBundle.canonicalDigests.relations, "Formal 0.1.0 canonical digest relations drift from candidate 2");
assert.deepEqual(formalBundle.desktopAssertions.scenarios, candidateBundle.desktopAssertions.scenarios, "Formal 0.1.0 desktop assertions drift from candidate 2");
assert.deepEqual(formalBundle.mapping.confirmation_rule, candidateBundle.mapping.confirmation_rule, "Formal 0.1.0 confirmation_rule drifts from candidate 2");
assert.deepEqual(formalBundle.mapping.outcomes, candidateBundle.mapping.outcomes, "Formal 0.1.0 result-mapping outcomes drift from candidate 2");
console.log("Formal expense-create 0.1.0 parity with 0.1.0-candidate.2 verified.");

const patchDir = path.join(root, "contracts/expense-patch-candidate");
const expectedPatchFiles = [
  "SPEC.md",
  "canonical-digests.json",
  "normative-vectors.json",
  "result-mapping.json",
];
assert(existsSync(patchDir), "Registered contract directory missing: contracts/expense-patch-candidate");
const patchManifest = JSON.parse(readFileSync(path.join(patchDir, "manifest.json"), "utf8"));
assert.equal(patchManifest.contract_id, "travelflow.expense-patch-candidate");
assert.equal(patchManifest.contract_version, "0.1.0-candidate.1");
assert.equal(patchManifest.status, "candidate");
assert.equal(patchManifest.shared_contract_version, null);
assert.deepEqual(Object.keys(patchManifest.files).sort(), expectedPatchFiles);
const actualPatchEntries = readdirSync(patchDir, { withFileTypes: true }).map((entry) => {
  assert(!entry.isSymbolicLink(), `Contract cannot contain symlink: contracts/expense-patch-candidate/${entry.name}`);
  assert(!entry.isDirectory(), `Contract cannot contain unexpected subdir: contracts/expense-patch-candidate/${entry.name}`);
  return entry.name;
}).sort();
assert.deepEqual(actualPatchEntries, [...expectedPatchFiles, "manifest.json"].sort());
for (const [name, expectedSha] of Object.entries(patchManifest.files)) {
  const actualSha = createHash("sha256").update(readFileSync(path.join(patchDir, name))).digest("hex");
  assert.equal(actualSha, expectedSha, `Contract digest mismatch: contracts/expense-patch-candidate/${name}`);
}
const patchDigests = JSON.parse(readFileSync(path.join(patchDir, "canonical-digests.json"), "utf8"));
const patchNormative = JSON.parse(readFileSync(path.join(patchDir, "normative-vectors.json"), "utf8"));
const patchMapping = JSON.parse(readFileSync(path.join(patchDir, "result-mapping.json"), "utf8"));
const patchComputed = new Map();
for (const item of patchDigests.cases) {
  const wrapped = {
    canonical_profile: patchDigests.canonical_profile,
    channel: item.channel,
    intent: sortUtf16(JSON.parse(item.raw_json)),
  };
  const encoded = Buffer.from(JSON.stringify(wrapped), "utf8");
  const actualSha = createHash("sha256").update(encoded).digest("hex");
  assert.equal(encoded.toString("utf8"), item.canonical_json, `Patch canonical JSON mismatch: ${item.id}`);
  assert.equal(encoded.toString("hex"), item.canonical_utf8_hex, `Patch canonical hex mismatch: ${item.id}`);
  assert.equal(actualSha, item.sha256, `Patch canonical SHA-256 mismatch: ${item.id}`);
  patchComputed.set(item.id, actualSha);
}
for (const rel of patchDigests.relations) {
  const unique = new Set(rel.ids.map((id) => patchComputed.get(id)));
  if (rel.relation === "same") assert.equal(unique.size, 1);
  else assert.equal(unique.size, rel.ids.length);
}
for (const sc of patchNormative.scenarios) {
  for (const st of sc.steps) {
    assert(st.expected.business_outcome in patchMapping.outcomes, `Unmapped patch outcome: ${sc.id}/${st.name}`);
  }
}
console.log("Expense-patch candidate 0.1.0-candidate.1 manifest, canonical digests, and bundle checks passed.");

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
