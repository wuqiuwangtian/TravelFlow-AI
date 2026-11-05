// 同一Python参考样例验证可移植原型；不启动后端、不使用DOM或新依赖。
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import ts from "typescript";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const source = readFileSync(path.join(root, "prototypes/domain/ledger.ts"), "utf8");
const compiled = ts.transpileModule(source, {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext },
}).outputText;
const { summarizeLedger } = await import(`data:text/javascript;base64,${Buffer.from(compiled).toString("base64")}`);
const corpus = JSON.parse(readFileSync(path.join(root, "prototypes/domain/ledger-vectors.json"), "utf8"));
assert.equal(corpus.schema_version, 1);
for (const scenario of corpus.cases) {
  const input = scenario.repeat ? {
    ...scenario.input,
    expenses: Array.from({length: scenario.repeat}, (_, i) => ({...scenario.input.expenses[0], id: `e${i}`})),
    adjustments: Array.from({length: scenario.repeat}, (_, i) => ({...scenario.input.adjustments[0], expense_id: `e${i}`})),
  } : scenario.input;
  const before = JSON.stringify(input);
  assert.deepEqual(summarizeLedger(input), scenario.expected, scenario.name);
  assert.equal(JSON.stringify(input), before, `input mutated: ${scenario.name}`);
}
for (const amount of [0, -1, 0.1, NaN, Infinity, Number.MAX_SAFE_INTEGER + 1]) {
  assert.throws(() => summarizeLedger({ expenses: [{ id: "invalid", amount_cents: amount }], adjustments: [] }));
}
assert.throws(() => summarizeLedger({expenses: [
  { id: "a", amount_cents: Number.MAX_SAFE_INTEGER },
  { id: "b", amount_cents: 1 },
], adjustments: []}));
for (const entry of [
  { expense_id: "a", amount_cents: 1, kind: "unknown", status: "received" },
  { expense_id: "a", amount_cents: 1, kind: "refund", status: "unknown" },
]) assert.throws(() => summarizeLedger({expenses: [], adjustments: [entry]}));
console.log(`${corpus.cases.length} Python-reference cases passed; inputs unchanged; invalid cents/enums/overflow rejected.`);
