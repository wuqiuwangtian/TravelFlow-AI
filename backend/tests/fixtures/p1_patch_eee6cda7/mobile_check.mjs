import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { spawnSync } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { evaluate } from "../prototypes/p1-patch/expense.mjs";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
assert.ok(process.argv.length <= 3, "Usage: node scripts/check-p1-patch.mjs [candidate.json]");
const inputPath = path.resolve(process.argv[2] ?? path.join(root, "docs/internal/fixtures/p1-patch-cases.json"));
const corpus = JSON.parse(readFileSync(inputPath, "utf8"));
assert.equal(corpus.fixture_format_version, 1);
assert.equal(corpus.status, "candidate-expense-patch");
assert.equal(corpus.shared_contract_version, null);
assert.deepEqual(corpus.acceptance, { desktop_joint_review: "pending", storage_execution: "not-implemented", android_native_execution: "not-implemented" });
assert.ok(Array.isArray(corpus.cases) && corpus.cases.length > 0);
const before = JSON.stringify(corpus);
function freeze(value) {
  if (value && typeof value === "object") { Object.values(value).forEach(freeze); Object.freeze(value); }
}
const results = {};
for (const entry of corpus.cases) {
  assert.match(entry.id, /^EP[0-9]{2}$/);
  assert.ok(!Object.hasOwn(results, entry.id), `Duplicate ID: ${entry.id}`);
  assert.deepEqual(Object.keys(entry.expected).sort(), entry.expected.accepted
    ? ["accepted", "entity_changes", "controls", "planned_business_revision", "cny_totals_eligible"].sort()
    : ["accepted", "error"].sort(), `${entry.id}: expected shape`);
  const base = { ...structuredClone(corpus.baseline.base), ...structuredClone(entry.base_overrides ?? {}) };
  for (const key of entry.base_omit ?? []) delete base[key];
  const input = { base, patch: structuredClone(entry.patch),
    context: { ...structuredClone(corpus.baseline.context), ...structuredClone(entry.context_overrides ?? {}) } };
  const inputBefore = JSON.stringify(input);
  freeze(input);
  results[entry.id] = evaluate({ input });
  const expected = entry.expected.accepted ? { accepted: true, value: {
    candidate: { ...structuredClone(base), ...structuredClone(entry.expected.entity_changes) },
    controls: entry.expected.controls,
    planned_business_revision: entry.expected.planned_business_revision,
    planned_operation_receipt: "required-not-persisted", cny_totals_eligible: entry.expected.cny_totals_eligible,
  } } : entry.expected;
  assert.deepEqual(results[entry.id], expected, `${entry.id}: expected patch plan`);
  assert.equal(JSON.stringify(input), inputBefore, `${entry.id}: mutated input`);
}
assert.equal(JSON.stringify(corpus), before);
const interpreter = process.env.P1_REVIEW_PYTHON ?? (process.platform === "win32" ? "python" : "python3");
const processResult = spawnSync(interpreter, [path.join(root, "scripts/check-p1-patch.py"), inputPath], {
  cwd: root, encoding: "utf8", timeout: 30000, maxBuffer: 2 * 1024 * 1024,
});
assert.ifError(processResult.error);
assert.equal(processResult.status, 0, `Python3.9+ required; no skip: ${processResult.stderr}`);
const python = JSON.parse(processResult.stdout);
assert.equal(python.stdlib_only, true);
assert.deepEqual(python.results, results, "Node/Python patch plans disagree");
console.log(JSON.stringify({ candidate_patch_cases: corpus.cases.length,
  accepted: Object.values(results).filter(result => result.accepted).length,
  rejected: Object.values(results).filter(result => !result.accepted).length,
  node: process.version, python: python.python, desktop_joint_contract: "pending",
  scope: "decoded-value pure expense patch plans; no command execution",
  raw_command_decoder_verified: false, resource_limits_verified: false, canonical_bytes_verified: false,
  durable_receipts_verified: false, storage_executed: false, android_native_executed: false,
}, null, 2));
