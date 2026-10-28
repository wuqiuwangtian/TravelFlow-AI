import { readdir, readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import path from "node:path";
const root = fileURLToPath(new URL("../", import.meta.url));
async function sources(folder) {
  const entries = await readdir(folder, { withFileTypes: true });
  return (
    await Promise.all(
      entries.map((entry) =>
        entry.isDirectory()
          ? sources(path.join(folder, entry.name))
          : /\.(tsx?|css)$/.test(entry.name)
            ? [path.join(folder, entry.name)]
            : [],
      ),
    )
  ).flat();
}
const files = (
  await Promise.all(
    ["app", "components", "lib", "styles"].map((folder) =>
      sources(path.join(root, folder)),
    ),
  )
).flat();
const errors = [];
for (const file of files) {
  if (file === path.join(root, "styles/tokens.css")) continue;
  const text = await readFile(file, "utf8");
  for (const [i, line] of text.split("\n").entries()) {
    // Keep all palette values in tokens.css; user/model text is rendered from data.
    if (
      /\b(?:bg|text|border|divide|ring|outline|fill|stroke)-(?:white|black|(?:slate|gray|red|blue|green|emerald|amber|teal|brand|purple|violet|indigo|orange|sky|rose)-\d+)\b/.test(
        line,
      ) ||
      /#[\da-f]{3,8}\b|\brgba?\(/i.test(line)
    )
      errors.push(
        `${path.relative(root, file)}:${i + 1}: use a semantic color token`,
      );
    if (/\p{Extended_Pictographic}/u.test(line))
      errors.push(
        `${path.relative(root, file)}:${i + 1}: use the shared icon set for authored UI`,
      );
  }
}
if (errors.length) {
  console.error(errors.join("\n"));
  process.exitCode = 1;
} else
  console.log(
    `Design contract passed: ${files.length} source files; semantic colors and no authored emoji.`,
  );
