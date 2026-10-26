// FE-0 source inventory. Presence of a prop is a review hint, not a UX verdict.
import ts from "typescript";
import { readdirSync, readFileSync, mkdirSync, writeFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const frontend = fileURLToPath(new URL("../", import.meta.url));
// Re-scanning current code must not overwrite the committed FE-0 baseline.
const output = path.resolve(
  process.argv[2] ?? path.join(frontend, "test-results/frontend-inventory"),
);
const walk = (dir) =>
  readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const name = path.join(dir, entry.name);
    return entry.isDirectory()
      ? walk(name)
      : name.endsWith(".tsx")
        ? [name]
        : [];
  });
const files = [
  ...walk(path.join(frontend, "app")),
  ...walk(path.join(frontend, "components")),
];
const sources = new Map(
  files.map((file) => [
    file,
    ts.createSourceFile(
      file,
      readFileSync(file, "utf8"),
      ts.ScriptTarget.Latest,
      true,
      ts.ScriptKind.TSX,
    ),
  ]),
);
const imports = new Map();
const compact = (value) => value.replace(/\s+/g, " ").trim();
for (const [file, source] of sources) {
  const targets = [];
  source.forEachChild((node) => {
    if (
      !ts.isImportDeclaration(node) ||
      !ts.isStringLiteral(node.moduleSpecifier)
    )
      return;
    const name = node.moduleSpecifier.text;
    const base = name.startsWith("@/")
      ? path.join(frontend, name.slice(2))
      : name.startsWith(".")
        ? path.resolve(path.dirname(file), name)
        : "";
    const match = [base + ".tsx", path.join(base, "index.tsx")].find(
      (candidate) => sources.has(candidate),
    );
    if (match) targets.push(match);
  });
  imports.set(file, targets);
}
const routes = files.filter((file) => /\/app\/(?:.*\/)?page\.tsx$/.test(file));
const reachable = (file, seen = new Set()) => {
  if (seen.has(file)) return seen;
  seen.add(file);
  for (const child of imports.get(file) || []) reachable(child, seen);
  return seen;
};
const routeFiles = routes.map((file) => ({
  route:
    "/" +
    path
      .relative(path.join(frontend, "app"), file)
      .replace(/(?:^|\/)page\.tsx$/, ""),
  files: reachable(file),
}));
const globalFiles = reachable(path.join(frontend, "app/layout.tsx"));
const controls = [];
for (const [file, source] of sources) {
  const visit = (node) => {
    if (ts.isJsxOpeningElement(node) || ts.isJsxSelfClosingElement(node)) {
      const tag = node.tagName.getText(source);
      const attrs = Object.fromEntries(
        node.attributes.properties
          .filter(ts.isJsxAttribute)
          .map((attr) => [
            attr.name.getText(source),
            attr.initializer
              ? compact(attr.initializer.getText(source))
              : "true",
          ]),
      );
      if (
        [
          "button",
          "Button",
          "Link",
          "a",
          "input",
          "select",
          "textarea",
          "form",
          "summary",
        ].includes(tag) ||
        attrs.onClick ||
        attrs.onKeyDown ||
        attrs.role === '"button"'
      ) {
        const line =
          source.getLineAndCharacterOfPosition(node.getStart(source)).line + 1;
        const parent = ts.isJsxOpeningElement(node) ? node.parent : null;
        const children =
          parent && ts.isJsxElement(parent)
            ? parent.children.map((child) => child.getText(source)).join(" ")
            : "";
        controls.push({
          file: "frontend/" + path.relative(frontend, file),
          line,
          tag,
          routes: routeFiles
            .filter((route) => route.files.has(file) || globalFiles.has(file))
            .map((route) => route.route)
            .join(" | "),
          label_source: compact(
            attrs["aria-label"] ||
              attrs.title ||
              children ||
              attrs.placeholder ||
              attrs.type ||
              "",
          ).slice(0, 220),
          handler: compact(
            attrs.onClick ||
              attrs.onSubmit ||
              attrs.onChange ||
              attrs.onKeyDown ||
              "",
          ).slice(0, 250),
          events: Object.entries(attrs)
            .filter(([name]) => /^on[A-Z]/.test(name))
            .map(([name, value]) => `${name}=${value}`)
            .join(" | "),
          href: attrs.href || "",
          disabled: attrs.disabled || "",
          loading: attrs.loading || "",
          aria_busy: attrs["aria-busy"] || "",
          aria_describedby: attrs["aria-describedby"] || "",
          spread_props: node.attributes.properties.some(
            ts.isJsxSpreadAttribute,
          ),
          shared_implementation: file.endsWith("/components/ui.tsx"),
        });
      }
    }
    ts.forEachChild(node, visit);
  };
  visit(source);
}
mkdirSync(output, { recursive: true });
const columns = Object.keys(controls[0]);
const csv = (value) => '"' + String(value).replaceAll('"', '""') + '"';
writeFileSync(
  path.join(output, "action-inventory.csv"),
  "\uFEFF" +
    [columns, ...controls.map((control) => columns.map((key) => control[key]))]
      .map((row) => row.map(csv).join(","))
      .join("\n") +
    "\n",
);
const summary = {
  scope:
    "JSX source sites, including dynamic/map definitions and shared implementations; not rendered counts or missing-feedback verdicts",
  files_scanned: files.length,
  source_sites: controls.length,
  routes: routeFiles.map(({ route, files: dependencies }) => ({
    route,
    source_sites: controls.filter(
      (control) =>
        dependencies.has(path.resolve(frontend, "..", control.file)) ||
        globalFiles.has(path.resolve(frontend, "..", control.file)),
    ).length,
  })),
  files: [...new Set(controls.map((control) => control.file))].map((file) => ({
    file,
    source_sites: controls.filter((control) => control.file === file).length,
  })),
};
writeFileSync(
  path.join(output, "inventory-summary.json"),
  JSON.stringify(summary, null, 2) + "\n",
);
console.log(JSON.stringify(summary, null, 2));
