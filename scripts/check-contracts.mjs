import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
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
