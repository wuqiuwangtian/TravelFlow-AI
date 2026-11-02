#!/usr/bin/env node
/**
 * TravelFlow AI 文档相对链接检查。
 *
 * 零依赖、ESM，只使用 node: 内置模块。扫描 README.md 与 docs/**\/*.md：
 *   - 校验相对链接指向的文件或目录存在；
 *   - 校验 Markdown 锚点在目标文件标题的 GitHub 风格 slug 中可达；
 *   - 跳过 http/https/mailto 等外部链接、代码块与行内代码。
 *
 * 锚点规则（与 GitHub 渲染一致）：标题转小写、去掉标点与 emoji、空格转连字符，
 * 保留中英文、数字与连字符。例如 `## 2026-11-02 接续：示例` → `2026-11-02-接续示例`。
 *
 * 用法：
 *   node scripts/check-docs-links.mjs
 *   node scripts/check-docs-links.mjs --help
 *
 * 退出码：
 *   0  全部通过
 *   1  存在问题（列出全部问题及文件、行号）
 *   2  用法错误
 */

import { existsSync, readdirSync, readFileSync, statSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const README = 'README.md';
const DOCS_DIR = 'docs';
const SKIPPED_DIRECTORIES = new Set(['node_modules', '.git']);

const USAGE = `用法:
  node scripts/check-docs-links.mjs
  node scripts/check-docs-links.mjs --help

说明:
  扫描根目录 Markdown 与 docs/**/*.md 中的相对链接和锚点，报告全部问题并以非零退出。
  外部链接（http/https/mailto 等）不检查。
`;

const problems = [];
const anchorsByFile = new Map();

function addProblem(file, line, message) {
  problems.push({ file, line: line ?? null, message });
}

function formatProblem(problem) {
  const location = problem.line ? `${problem.file}:${problem.line}` : problem.file;
  return `  - ${location}: ${problem.message}`;
}

function usageError(message) {
  process.stderr.write(`[用法错误] ${message}\n\n${USAGE}`);
  process.exit(2);
}

function lineAt(text, index) {
  if (index < 0) return null;
  let line = 1;
  for (let i = 0; i < index && i < text.length; i += 1) {
    if (text[i] === '\n') line += 1;
  }
  return line;
}

/** 递归收集 Markdown 文件，返回仓库相对路径（使用 / 分隔）。 */
function collectMarkdownFiles() {
  const files = [];
  const readmePath = path.join(ROOT, README);
  if (!existsSync(readmePath)) {
    addProblem(README, null, '文件不存在');
  } else {
    files.push(README);
  }

  const rootEntries = readdirSync(ROOT, { withFileTypes: true }).sort((a, b) =>
    a.name.localeCompare(b.name),
  );
  for (const entry of rootEntries) {
    if (
      entry.isFile() &&
      entry.name.toLowerCase().endsWith('.md') &&
      entry.name !== README
    ) {
      files.push(entry.name);
    }
  }

  const docsPath = path.join(ROOT, DOCS_DIR);
  if (!existsSync(docsPath)) {
    addProblem(DOCS_DIR, null, '目录不存在');
    return files;
  }

  const walk = (absoluteDir) => {
    const entries = readdirSync(absoluteDir, { withFileTypes: true }).sort((a, b) =>
      a.name.localeCompare(b.name),
    );
    for (const entry of entries) {
      if (entry.isSymbolicLink()) continue;
      const absoluteEntry = path.join(absoluteDir, entry.name);
      if (entry.isDirectory()) {
        if (!SKIPPED_DIRECTORIES.has(entry.name)) walk(absoluteEntry);
      } else if (entry.isFile() && entry.name.toLowerCase().endsWith('.md')) {
        const relativeEntry = path.relative(ROOT, absoluteEntry).split(path.sep).join('/');
        files.push(relativeEntry);
      }
    }
  };
  walk(docsPath);
  return files;
}

/** 把围栏代码块整段替换为空格，保留换行以维持行号。 */
function maskFencedCode(content) {
  const lines = content.split('\n');
  let fence = null;
  for (let i = 0; i < lines.length; i += 1) {
    const line = lines[i];
    if (fence) {
      const closing = line.match(/^ {0,3}(`{3,}|~{3,})[ \t]*$/);
      lines[i] = ' '.repeat(line.length);
      if (closing && closing[1][0] === fence.char && closing[1].length >= fence.length) {
        fence = null;
      }
      continue;
    }
    const opening = line.match(/^ {0,3}(`{3,}|~{3,})(.*)$/);
    if (!opening) continue;
    // 反引号围栏的信息串里不能再出现反引号，否则不是合法围栏。
    if (opening[1][0] === '`' && opening[2].includes('`')) continue;
    fence = { char: opening[1][0], length: opening[1].length };
    lines[i] = ' '.repeat(line.length);
  }
  return lines.join('\n');
}

/** 注释内容替换为空格，保留换行。 */
function maskHtmlComments(content) {
  return content.replace(/<!--[\s\S]*?-->/g, (match) => match.replace(/[^\n]/g, ' '));
}

/** 行内代码替换为空格（保留长度和换行）。 */
function maskInlineCode(content) {
  const chars = content.split('');
  let i = 0;
  while (i < content.length) {
    if (content[i] !== '`') {
      i += 1;
      continue;
    }
    let openEnd = i;
    while (openEnd < content.length && content[openEnd] === '`') openEnd += 1;
    const ticks = openEnd - i;
    let scan = openEnd;
    let closeEnd = -1;
    while (scan < content.length) {
      if (content[scan] === '`') {
        let runEnd = scan;
        while (runEnd < content.length && content[runEnd] === '`') runEnd += 1;
        if (runEnd - scan === ticks) {
          closeEnd = runEnd;
          break;
        }
        scan = runEnd;
      } else {
        scan += 1;
      }
    }
    if (closeEnd === -1) {
      i = openEnd;
      continue;
    }
    for (let p = i; p < closeEnd; p += 1) chars[p] = ' ';
    i = closeEnd;
  }
  return chars.join('');
}

function maskContent(content) {
  return maskInlineCode(maskHtmlComments(maskFencedCode(content)));
}

function unescapeMarkdown(value) {
  return value.replace(/\\([\\`*_{}[\]()#+\-.!<>|~"'])/g, '$1');
}

function isEscaped(text, index) {
  let count = 0;
  for (let i = index - 1; i >= 0 && text[i] === '\\'; i -= 1) count += 1;
  return count % 2 === 1;
}

/** 找到与 `(` 配对的右括号；遇到空行视为链接结束，避免吞掉后文。 */
function findClosingParen(text, start, maxEnd) {
  let depth = 1;
  for (let pos = start; pos < maxEnd; pos += 1) {
    const char = text[pos];
    if (char === '\\') {
      pos += 1;
      continue;
    }
    if (char === '(') {
      depth += 1;
    } else if (char === ')') {
      depth -= 1;
      if (depth === 0) return pos;
    } else if (char === '\n') {
      let probe = pos + 1;
      while (probe < maxEnd && (text[probe] === ' ' || text[probe] === '\t' || text[probe] === '\r')) {
        probe += 1;
      }
      if (probe < maxEnd && text[probe] === '\n') return -1;
    }
  }
  return -1;
}

/** 解析 `](` 之后的链接目标，返回 { target, end }。 */
function parseLinkTarget(text, start) {
  const maxEnd = Math.min(text.length, start + 4096);
  let pos = start;
  while (pos < maxEnd && /\s/.test(text[pos])) pos += 1;
  if (pos >= maxEnd) return null;

  if (text[pos] === '<') {
    const closeAngle = text.indexOf('>', pos + 1);
    if (closeAngle === -1 || closeAngle >= maxEnd) return null;
    const target = text.slice(pos + 1, closeAngle);
    let after = closeAngle + 1;
    while (after < maxEnd && /\s/.test(text[after])) after += 1;
    if (text[after] === ')') return { target: unescapeMarkdown(target), end: after + 1 };
    const closeParen = findClosingParen(text, start, maxEnd);
    if (closeParen === -1) return null;
    return { target: unescapeMarkdown(target), end: closeParen + 1 };
  }

  const closeParen = findClosingParen(text, start, maxEnd);
  if (closeParen === -1) return null;
  const raw = text.slice(start, closeParen).trim();
  if (raw === '') return { target: '', end: closeParen + 1 };
  const withTitle = raw.match(
    /^(\S+)(?:[ \t]+(?:"(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*'|\((?:[^()]|\([^()]*\))*\)))?[ \t]*$/,
  );
  const target = withTitle ? withTitle[1] : raw.split(/[ \t]+/)[0];
  return { target: unescapeMarkdown(target), end: closeParen + 1 };
}

/** 收集未被代码屏蔽的 Markdown 内联链接与图片。 */
function collectLinks(masked) {
  const links = [];
  for (let i = 0; i + 1 < masked.length; i += 1) {
    if (masked[i] !== ']' || masked[i + 1] !== '(') continue;
    if (isEscaped(masked, i)) continue;
    const parsed = parseLinkTarget(masked, i + 2);
    if (!parsed) continue;
    links.push({ start: i, target: parsed.target, end: parsed.end });
    i = parsed.end - 1;
  }
  return links;
}

function safeDecode(value) {
  try {
    return decodeURIComponent(value);
  } catch {
    return value;
  }
}

/** 拆出路径与锚点（先拆再解码，避免把 %23 当成锚点）。 */
function splitTarget(target) {
  const hashIndex = target.indexOf('#');
  const beforeHash = hashIndex === -1 ? target : target.slice(0, hashIndex);
  const fragment = hashIndex === -1 ? '' : safeDecode(target.slice(hashIndex + 1));
  const queryIndex = beforeHash.indexOf('?');
  const pathPart = queryIndex === -1 ? beforeHash : beforeHash.slice(0, queryIndex);
  return { pathPart: safeDecode(pathPart), fragment };
}

function isExternalTarget(target) {
  return /^[A-Za-z][A-Za-z0-9+.-]*:/.test(target) || target.startsWith('//');
}

/** 提取标题（ATX 与 Setext），跳过代码围栏与注释。 */
function extractHeadings(content) {
  const masked = maskInlineCode(maskHtmlComments(maskFencedCode(content)));
  const lines = masked.split('\n');
  const headings = [];
  for (let i = 0; i < lines.length; i += 1) {
    const atx = lines[i].match(/^ {0,3}(#{1,6})(?:[ \t]+(.*?))?[ \t]*$/);
    if (atx) {
      const text = (atx[2] ?? '').replace(/[ \t]+#+[ \t]*$/, '').trim();
      headings.push({ text, line: i + 1 });
      continue;
    }
    if (i === 0) continue;
    const setext = lines[i].match(/^ {0,3}(=+|-+)[ \t]*$/);
    if (!setext) continue;
    const previous = lines[i - 1];
    const previousTrimmed = previous.trim();
    if (previousTrimmed === '') continue;
    if (/^ {0,3}(#{1,6})[ \t]/.test(previous)) continue;
    if (/^ {0,3}(?:[-*+][ \t]+|\d+[.)][ \t]+|>)/.test(previous)) continue;
    if (/^ {0,3}\|/.test(previous)) continue;
    if (/^ {0,3}(`{3,}|~{3,})/.test(previous)) continue;
    headings.push({ text: previousTrimmed, line: i });
  }
  return headings;
}

/** 将标题文本转成 GitHub 风格 slug。 */
function slugify(text) {
  return text
    .replace(/<[^>]*>/g, '')
    .replace(/!\[([^\]]*)\]\([^)]*\)/g, '$1')
    .replace(/\[([^\]]*)\]\([^)]*\)/g, '$1')
    .replace(/`([^`]*)`/g, '$1')
    .replace(/[*_~]{1,3}/g, '')
    .replace(/\\([\\`*_{}[\]()#+\-.!<>|~"'])/g, '$1')
    .toLowerCase()
    .trim()
    .replace(/[\u200B-\u200D\uFE00-\uFE0F]/g, '')
    .replace(/[^\p{L}\p{N}\p{M}\p{Pc}\- ]/gu, '')
    .replace(/ /g, '-');
}

/** 生成 slug → 行号映射，并按 GitHub 规则处理重复标题。 */
function buildAnchorMap(markdownText) {
  const map = new Map();
  const baseCounts = new Map();
  for (const heading of extractHeadings(markdownText)) {
    const base = slugify(heading.text);
    if (!base) continue;
    let slug = base;
    if (baseCounts.has(base)) {
      let counter = baseCounts.get(base);
      do {
        counter += 1;
        slug = `${base}-${counter}`;
      } while (map.has(slug));
      baseCounts.set(base, counter);
    } else {
      baseCounts.set(base, 0);
    }
    map.set(slug, heading.line);
  }
  return map;
}

function anchorsFor(absolutePath, relativePath) {
  if (anchorsByFile.has(absolutePath)) return anchorsByFile.get(absolutePath);
  try {
    const content = readFileSync(absolutePath, 'utf8');
    const anchors = buildAnchorMap(content);
    anchorsByFile.set(absolutePath, anchors);
    return anchors;
  } catch (error) {
    addProblem(relativePath, null, `无法读取目标文件以校验锚点：${error.message}`);
    anchorsByFile.set(absolutePath, new Map());
    return anchorsByFile.get(absolutePath);
  }
}

function suggestAnchors(anchors, fragment) {
  const all = [...anchors.keys()];
  const lower = fragment.toLowerCase();
  const scored = all
    .map((slug) => {
      let score = 0;
      if (slug.includes(lower)) score += 3;
      if (lower.includes(slug)) score += 3;
      if (slug.startsWith(lower) || lower.startsWith(slug)) score += 2;
      if (/^[-a-z0-9]+$/.test(slug)) score += 0;
      return { slug, score };
    })
    .filter((item) => item.score > 0)
    .sort((a, b) => b.score - a.score || a.slug.localeCompare(b.slug))
    .slice(0, 3)
    .map((item) => `#${item.slug}`);
  if (scored.length > 0) return scored;
  return all.slice(0, 3).map((slug) => `#${slug}`);
}

function checkLink(sourceRelative, sourceAbsolute, rawContent, link) {
  const target = link.target.trim();
  const line = lineAt(rawContent, link.start);

  if (target === '') {
    addProblem(sourceRelative, line, '链接目标为空');
    return { checked: false, anchor: false };
  }
  if (isExternalTarget(target)) return { checked: false, anchor: false };

  const { pathPart, fragment } = splitTarget(target);
  const targetAbsolute = pathPart === ''
    ? sourceAbsolute
    : pathPart.startsWith('/')
      ? path.join(ROOT, pathPart.replace(/^\/+/, ''))
      : path.resolve(path.dirname(sourceAbsolute), pathPart);

  const relativeTarget = path.relative(ROOT, targetAbsolute);
  if (relativeTarget.startsWith('..') || path.isAbsolute(relativeTarget)) {
    addProblem(sourceRelative, line, `链接超出仓库范围：${target}`);
    return { checked: true, anchor: false };
  }
  const displayTarget = relativeTarget.split(path.sep).join('/') || '.';

  let stats;
  try {
    stats = statSync(targetAbsolute);
  } catch {
    addProblem(sourceRelative, line, `链接目标不存在：${target}（解析为 ${displayTarget}）`);
    return { checked: true, anchor: false };
  }

  if (!fragment || stats.isDirectory()) return { checked: true, anchor: false };

  // 只有 Markdown 文件能可靠校验标题锚点。
  if (!relativeTarget.toLowerCase().endsWith('.md')) return { checked: true, anchor: false };

  const anchors = anchorsFor(targetAbsolute, displayTarget);
  const normalizedFragment = fragment.replace(/^user-content-/, '');
  if (anchors.has(normalizedFragment)) return { checked: true, anchor: true };

  if (anchors.has(normalizedFragment.toLowerCase())) {
    addProblem(
      sourceRelative,
      line,
      `锚点大小写不匹配：#${fragment}，应写作 #${normalizedFragment.toLowerCase()}`,
    );
    return { checked: true, anchor: true };
  }

  const suggestions = suggestAnchors(anchors, normalizedFragment);
  const suggestionText = suggestions.length > 0
    ? `可用锚点示例：${suggestions.join('、')}`
    : '目标文件没有可用锚点';
  addProblem(
    sourceRelative,
    line,
    `锚点在 ${displayTarget} 中不存在：#${fragment}（${suggestionText}）`,
  );
  return { checked: true, anchor: true };
}

function main() {
  const args = process.argv.slice(2);
  for (const arg of args) {
    if (arg === '--help' || arg === '-h') {
      process.stdout.write(USAGE);
      process.exit(0);
    }
    usageError(`未知参数：${arg}`);
  }

  const files = collectMarkdownFiles();
  let linkCount = 0;
  let checkedCount = 0;
  let anchorCount = 0;

  for (const relativeFile of files) {
    const absoluteFile = path.join(ROOT, relativeFile);
    let content;
    try {
      content = readFileSync(absoluteFile, 'utf8');
    } catch (error) {
      addProblem(relativeFile, null, `无法读取文件：${error.message}`);
      continue;
    }
    const masked = maskContent(content);
    for (const link of collectLinks(masked)) {
      linkCount += 1;
      const result = checkLink(relativeFile, absoluteFile, content, link);
      if (result.checked) checkedCount += 1;
      if (result.anchor) anchorCount += 1;
    }
  }

  if (problems.length > 0) {
    process.stderr.write(`\n文档链接检查失败，共 ${problems.length} 项问题：\n`);
    for (const problem of problems) process.stderr.write(`${formatProblem(problem)}\n`);
    process.stderr.write(
      `\n扫描 ${files.length} 个 Markdown 文件、${linkCount} 条内联链接，其中 ${checkedCount} 条相对链接、${anchorCount} 条锚点。\n`,
    );
    process.exit(1);
  }

  process.stdout.write('文档链接检查通过\n');
  process.stdout.write(`  扫描文件: ${files.length} 个（根目录 Markdown + docs/**/*.md）\n`);
  process.stdout.write(`  内联链接: ${linkCount} 条\n`);
  process.stdout.write(`  相对链接: ${checkedCount} 条（含 ${anchorCount} 条锚点）\n`);
}

main();
