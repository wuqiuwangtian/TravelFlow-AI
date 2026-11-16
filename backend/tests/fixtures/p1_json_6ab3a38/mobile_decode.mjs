// Host-only candidate: no application bridge, persistence or canonical byte format.
import { parseTree } from "jsonc-parser";

class CandidateError extends Error {
  constructor(code) { super(code); this.code = code; }
}
const reject = code => { throw new CandidateError(code); };

function scalarString(value) {
  for (const character of value) {
    const point = character.codePointAt(0);
    if (point >= 0xd800 && point <= 0xdfff) reject("unicode");
  }
  return value;
}

// A diagnostic tree preserves source object order and numeric lexemes. Not JCS.
function diagnostic(node, text) {
  switch (node.type) {
    case "object": {
      const seen = new Set();
      const entries = [];
      for (const property of node.children) {
        const key = scalarString(property.children[0].value);
        if (seen.has(key)) reject("duplicate_key");
        seen.add(key);
        entries.push([key, diagnostic(property.children[1], text)]);
      }
      return ["object", entries];
    }
    case "array": return ["array", node.children.map(child => diagnostic(child, text))];
    case "number": return ["number", text.slice(node.offset, node.offset + node.length)];
    case "string": return ["string", scalarString(node.value)];
    case "boolean": return ["boolean", node.value];
    case "null": return ["null"];
    default: throw new Error("Unexpected parser node");
  }
}

export function decode(bytes) {
  if (!(bytes instanceof Uint8Array)) throw new TypeError("UTF-8 bytes required");
  let text;
  try {
    // Preserve a BOM so that it is rejected rather than silently removed.
    text = new TextDecoder("utf-8", { fatal: true, ignoreBOM: true }).decode(bytes);
  } catch { reject("encoding"); }
  const errors = [];
  const tree = parseTree(text, errors, {
    disallowComments: true, allowTrailingComma: false, allowEmptyContent: false,
  });
  // This parser recovers from syntax errors; never consume a recovered tree.
  if (errors.length || !tree || text.startsWith("\ufeff")) reject("json");
  return diagnostic(tree, text);
}

function cents(tree) {
  if (tree[0] !== "number") reject("amount");
  // JSON grammar was checked by the parser. Work on exact decimal components;
  // never use the parser's rounded node.value or Number/parseFloat on the token.
  const [mantissa, exponent = "0"] = tree[1].toLowerCase().split("e");
  if (mantissa.startsWith("-")) reject("amount");
  const [whole, fraction = ""] = mantissa.split(".");
  const coefficient = (whole + fraction).replace(/^0+/, "");
  if (!coefficient) reject("amount");
  const digits = coefficient.replace(/0+$/, "");
  const scale = BigInt(exponent) - BigInt(fraction.length) + BigInt(coefficient.length - digits.length);
  if (scale < 0n || BigInt(digits.length) + scale > 10n) reject("amount");
  // Range checks bound the exponentiation to at most nine powers of ten.
  const exact = BigInt(digits) * 10n ** scale;
  if (exact > 9999999999n) reject("amount");
  return Number(exact);
}

function money(tree) {
  if (tree[0] !== "object") reject("shape");
  const fields = new Map(tree[1]);
  if (fields.size !== 2 || !fields.has("currency") || !fields.has("amount_cents")) reject("shape");
  const currency = fields.get("currency");
  if (currency[0] !== "string" || currency[1] !== "CNY") reject("currency");
  return { currency: "CNY", amount_cents: cents(fields.get("amount_cents")) };
}

export function evaluate(entry) {
  let bytes;
  if (typeof entry.raw_json === "string") bytes = new TextEncoder().encode(entry.raw_json);
  else if (typeof entry.raw_utf8_hex === "string" && /^(?:[a-f0-9]{2})*$/.test(entry.raw_utf8_hex)) {
    bytes = Uint8Array.from(entry.raw_utf8_hex.match(/../g) ?? [], pair => Number.parseInt(pair, 16));
  } else throw new TypeError("Fixture requires raw_json or lowercase raw_utf8_hex");
  try {
    const tree = decode(bytes);
    if (entry.profile === "json") return { accepted: true, tree };
    if (entry.profile === "cny-amount") return { accepted: true, value: money(tree) };
    throw new TypeError("Unknown candidate profile");
  } catch (error) {
    if (error instanceof CandidateError) return { accepted: false, error: error.code };
    throw error;
  }
}
