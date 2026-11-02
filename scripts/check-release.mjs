#!/usr/bin/env node
/**
 * TravelFlow AI 发布元数据校验。
 *
 * 零依赖、ESM，只使用 node: 内置模块。在仓库任意目录运行均可。
 *
 * 用法：
 *   node scripts/check-release.mjs                 校验许可证、版本、敏感文件与 README 发布信息
 *   node scripts/check-release.mjs --tag v1.0.0    额外校验 Git tag 与版本一致
 *   node scripts/check-release.mjs --notes --tag v1.0.0
 *                                                  输出 CHANGELOG 对应版本的发布说明（供 Release 使用）
 *
 * 退出码：
 *   0  全部通过
 *   1  校验失败（会列出全部问题）
 *   2  用法错误或环境错误（例如未安装 Git、当前目录不是 Git 仓库）
 */

import { execFileSync } from 'node:child_process';
import { existsSync, readFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const CHECKED_AT = new Date().toISOString();

const EXIT_OK = 0;
const EXIT_INVALID = 1;
const EXIT_USAGE = 2;

/** 收集所有校验问题；每条都带文件路径（必要时带行号）。 */
const problems = [];
/** 不阻断发布、但值得注意的提示。 */
const warnings = [];

function addProblem(file, line, message) {
  problems.push({ file, line: line ?? null, message });
}

function addWarning(file, line, message) {
  warnings.push({ file, line: line ?? null, message });
}

function printWarnings() {
  for (const warning of warnings) {
    const location = warning.line ? `${warning.file}:${warning.line}` : warning.file;
    process.stderr.write(`[提示] ${location}: ${warning.message}\n`);
  }
}

function formatProblem(problem) {
  const location = problem.line ? `${problem.file}:${problem.line}` : problem.file;
  return `  - ${location}: ${problem.message}`;
}

function fail(errors, exitCode = EXIT_INVALID) {
  process.stderr.write(`\n发布校验失败，共 ${errors.length} 项问题：\n`);
  for (const problem of errors) process.stderr.write(`${formatProblem(problem)}\n`);
  process.stderr.write('\n');
  printWarnings();
  process.exit(exitCode);
}

function usageError(message) {
  process.stderr.write(`[用法错误] ${message}\n\n`);
  process.stderr.write(USAGE);
  process.exit(EXIT_USAGE);
}

const USAGE = `用法:
  node scripts/check-release.mjs
  node scripts/check-release.mjs --tag v1.0.0
  node scripts/check-release.mjs --notes --tag v1.0.0

说明:
  不带参数   校验 LICENSE、三处版本号、CHANGELOG 版本段、Git 敏感文件与 README 发布信息。
  --tag      额外校验 Git tag 与版本号一致，例如 v1.0.0。
  --notes    把 CHANGELOG 中对应版本的段落输出到标准输出，供 GitHub Release 使用。
  --help     显示本帮助。

退出码: 0 通过 / 1 校验失败 / 2 用法或环境错误
`;

/** 读取仓库内文件；不存在时记一条问题并返回 null。 */
function readRepoFile(relativePath) {
  const absolutePath = path.join(ROOT, relativePath);
  if (!existsSync(absolutePath)) {
    addProblem(relativePath, null, '文件不存在');
    return null;
  }
  try {
    return readFileSync(absolutePath, 'utf8');
  } catch (error) {
    addProblem(relativePath, null, `无法读取文件：${error.message}`);
    return null;
  }
}

/** 把字符下标换算成 1 起始的行号。 */
function lineAt(text, index) {
  if (index < 0) return null;
  let line = 1;
  for (let i = 0; i < index && i < text.length; i += 1) {
    if (text[i] === '\n') line += 1;
  }
  return line;
}

function normalizeVersion(value) {
  return String(value).trim().replace(/^v/i, '');
}

function isValidVersion(value) {
  return /^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?$/.test(value);
}

/** 从 pyproject.toml 的 [project] 段读取 version 字段。 */
function readPyprojectVersion(text) {
  const lines = text.split('\n');
  let inProject = false;
  for (let i = 0; i < lines.length; i += 1) {
    const line = lines[i];
    const sectionMatch = line.match(/^\s*\[([^\]]+)\]\s*(?:#.*)?$/);
    if (sectionMatch) {
      inProject = sectionMatch[1].trim() === 'project';
      continue;
    }
    if (!inProject) continue;
    const versionMatch = line.match(/^\s*version\s*=\s*["']([^"']+)["']\s*(?:#.*)?$/);
    if (versionMatch) {
      return { version: normalizeVersion(versionMatch[1]), line: i + 1 };
    }
  }
  return null;
}

/** 从 CHANGELOG 读取所有已发布版本段（跳过 [未发布]/[Unreleased]）。 */
function readChangelogSections(text) {
  const lines = text.split('\n');
  const headings = [];
  for (let i = 0; i < lines.length; i += 1) {
    const match = lines[i].match(/^##[ \t]+\[([^\]]+)\](?:[ \t]+-[ \t]+(.+?))?[ \t]*$/);
    if (!match) continue;
    const rawVersion = normalizeVersion(match[1]);
    if (!isValidVersion(rawVersion)) continue;
    headings.push({
      version: rawVersion,
      heading: lines[i],
      titleSuffix: match[2] ?? '',
      line: i + 1,
      bodyStart: i + 1,
      bodyEnd: lines.length,
    });
  }
  for (let i = 0; i < headings.length; i += 1) {
    const current = headings[i];
    const nextIndex = lines.findIndex(
      (line, index) => index > current.bodyStart - 1 && /^##[ \t]+/.test(line),
    );
    if (nextIndex !== -1) current.bodyEnd = nextIndex;
  }
  return { lines, headings };
}

/** 抽取某个版本段的正文，去掉标题行、尾部分隔线与多余空行。 */
function extractSectionBody(section, lines) {
  const body = lines.slice(section.bodyStart, section.bodyEnd).join('\n');
  return body
    .replace(/^\s+/, '')
    .replace(/\n+---[ \t]*\s*$/, '')
    .replace(/\s+$/, '');
}

/** 校验 README 中的 LICENSE 链接与 CI 徽章。 */
function checkReadme(file, text) {
  const licenseLinkPattern = /\[[^\]]*LICENSE[^\]]*\]\(\s*<?([^)\s>]*LICENSE[^)\s>]*)>?\s*(?:"[^"]*"|'[^']*')?\s*\)/i;
  if (!licenseLinkPattern.test(text)) {
    addProblem(file, null, '未找到指向 LICENSE 的 Markdown 链接');
  }

  const badgePattern =
    /(?:actions\/workflows\/[^/\s)"']+\.ya?ml\/badge\.svg|img\.shields\.io\/github\/actions\/workflow\/status)/i;
  if (!badgePattern.test(text)) {
    addProblem(file, null, '未找到 CI 状态徽章（actions/workflows/*.yml/badge.svg 或 shields.io 工作流状态）');
  }
}

/** 读取 git 跟踪的文件列表；不可用时按环境错误退出。 */
function listTrackedFiles() {
  try {
    const output = execFileSync('git', ['ls-files', '-z'], {
      cwd: ROOT,
      encoding: 'utf8',
      stdio: ['ignore', 'pipe', 'pipe'],
    });
    return output.split('\0').filter(Boolean);
  } catch (error) {
    const detail = (error.stderr || error.message || '').toString().trim();
    process.stderr.write(
      '\n[环境错误] 无法执行 `git ls-files`，不能检查敏感文件是否被跟踪。\n' +
        '请确认已安装 Git，并在 TravelFlow AI 仓库内运行本脚本。\n' +
        (detail ? `Git 输出：${detail}\n` : ''),
    );
    process.exit(EXIT_USAGE);
  }
}

/** 判断一个被跟踪路径是否属于不应该提交的敏感路径。 */
function isSensitiveTrackedPath(filePath) {
  const normalized = filePath.replaceAll('\\', '/');
  const baseName = path.posix.basename(normalized);
  if (baseName === '.env') return true;
  if (/\.db$/i.test(baseName)) return true;
  if (/(^|\/)data\//.test(normalized)) return true;
  return false;
}

/** 解析命令行参数。 */
function parseArgs(argv) {
  const options = { tag: null, notes: false };
  for (let i = 0; i < argv.length; i += 1) {
    const arg = argv[i];
    if (arg === '--help' || arg === '-h') {
      process.stdout.write(USAGE);
      process.exit(EXIT_OK);
    } else if (arg === '--notes') {
      options.notes = true;
    } else if (arg === '--tag') {
      const value = argv[i + 1];
      if (!value || value.startsWith('--')) usageError('--tag 需要一个版本 tag 值，例如 v1.0.0');
      options.tag = value;
      i += 1;
    } else if (arg.startsWith('--tag=')) {
      const value = arg.slice('--tag='.length);
      if (!value) usageError('--tag 需要一个版本 tag 值，例如 v1.0.0');
      options.tag = value;
    } else {
      usageError(`未知参数：${arg}`);
    }
  }
  return options;
}

/** 收集版本来源（pyproject、package.json、CHANGELOG）并做一致性校验。 */
function collectVersions() {
  const versions = [];

  const pyprojectPath = 'backend/pyproject.toml';
  const pyprojectText = readRepoFile(pyprojectPath);
  if (pyprojectText !== null) {
    const parsed = readPyprojectVersion(pyprojectText);
    if (!parsed) {
      addProblem(pyprojectPath, null, '未在 [project] 段找到 version 字段');
    } else {
      versions.push({ source: pyprojectPath, ...parsed });
    }
  }

  const packagePath = 'frontend/package.json';
  const packageText = readRepoFile(packagePath);
  if (packageText !== null) {
    try {
      const packageJson = JSON.parse(packageText);
      if (typeof packageJson.version !== 'string' || !packageJson.version.trim()) {
        addProblem(packagePath, null, '缺少 version 字段');
      } else {
        const match = packageText.match(/"version"\s*:\s*"([^"]+)"/);
        versions.push({
          source: packagePath,
          version: normalizeVersion(packageJson.version),
          line: match ? lineAt(packageText, match.index) : null,
        });
      }
    } catch (error) {
      addProblem(packagePath, null, `不是合法的 JSON：${error.message}`);
    }
  }

  const changelogPath = 'CHANGELOG.md';
  const changelogText = readRepoFile(changelogPath);
  let changelogSections = null;
  if (changelogText !== null) {
    changelogSections = readChangelogSections(changelogText);
    if (changelogSections.headings.length === 0) {
      addProblem(changelogPath, null, '未找到形如 `## [1.0.0]` 的已发布版本段');
    } else {
      const latest = changelogSections.headings[0];
      versions.push({ source: changelogPath, version: latest.version, line: latest.line });

      // 同一版本出现多段时，发布说明只能取顶部一段，提示维护者核对。
      const seenVersions = new Map();
      for (const section of changelogSections.headings) {
        if (!seenVersions.has(section.version)) {
          seenVersions.set(section.version, section.line);
          continue;
        }
        addWarning(
          changelogPath,
          section.line,
          `版本 ${section.version} 重复出现（首次在第 ${seenVersions.get(section.version)} 行），发布说明将使用顶部段落`,
        );
      }
    }
  }

  if (versions.length > 0) {
    const expected = versions[0].version;
    for (const item of versions.slice(1)) {
      if (item.version !== expected) {
        addProblem(
          item.source,
          item.line,
          `版本号为 ${item.version}，与 ${versions[0].source}:${versions[0].line ?? '?'} 的 ${expected} 不一致`,
        );
      }
    }
  }

  return {
    versions,
    changelogText,
    changelogSections,
  };
}

function main() {
  const options = parseArgs(process.argv.slice(2));
  const { versions, changelogText, changelogSections } = collectVersions();
  const primaryVersion = versions.length > 0 ? versions[0].version : null;

  // tag 与版本一致性：v1.0.0 与 1.0.0 都接受。
  if (options.tag !== null) {
    if (!primaryVersion) {
      addProblem('--tag', null, `无法校验 tag ${options.tag}：未读取到项目版本号`);
    } else if (normalizeVersion(options.tag) !== primaryVersion) {
      addProblem(
        '--tag',
        null,
        `tag 为 ${options.tag}，项目版本为 ${primaryVersion}，两者不一致（期望 v${primaryVersion}）`,
      );
    }
  }

  if (options.notes) {
    if (!options.tag) usageError('--notes 需要配合 --tag 使用，例如 --tag v1.0.0');
    if (!changelogText || !changelogSections) {
      fail(problems);
    }
    if (problems.length > 0) fail(problems);

    const section = changelogSections.headings.find(
      (item) => item.version === normalizeVersion(options.tag),
    );
    if (!section) {
      addProblem('CHANGELOG.md', null, `未找到版本 ${normalizeVersion(options.tag)} 的段落`);
      fail(problems);
    }
    const body = extractSectionBody(section, changelogSections.lines);
    if (!body.trim()) {
      addProblem('CHANGELOG.md', section.line, `版本 ${section.version} 的段落内容为空，无法生成发布说明`);
      fail(problems);
    }
    printWarnings();
    process.stdout.write(`${body}\n`);
    return;
  }

  // LICENSE
  const licensePath = 'LICENSE';
  const licenseText = readRepoFile(licensePath);
  if (licenseText !== null) {
    if (!licenseText.includes('MIT License')) {
      addProblem(licensePath, null, '未包含 “MIT License” 文本');
    }
    if (!licenseText.includes('wuqiuwangtian')) {
      addProblem(licensePath, null, '未包含版权所有者 “wuqiuwangtian”');
    }
  }

  // README
  const readmePath = 'README.md';
  const readmeText = readRepoFile(readmePath);
  if (readmeText !== null) checkReadme(readmePath, readmeText);

  // 敏感文件不得被 Git 跟踪
  const trackedFiles = listTrackedFiles();
  const sensitiveTrackedFiles = trackedFiles.filter(isSensitiveTrackedPath);
  if (sensitiveTrackedFiles.length > 0) {
    const shown = sensitiveTrackedFiles.slice(0, 20);
    const suffix = sensitiveTrackedFiles.length > shown.length
      ? `（另有 ${sensitiveTrackedFiles.length - shown.length} 个未列出）`
      : '';
    addProblem(
      'git',
      null,
      `以下敏感路径已被 Git 跟踪，请从版本库移除并确保在 .gitignore 中：${shown.join('、')}${suffix}`,
    );
  }

  if (problems.length > 0) fail(problems);

  printWarnings();

  // 成功摘要
  const versionDetail = versions
    .map((item) => `${item.source}${item.line ? `:${item.line}` : ''}=${item.version}`)
    .join('，');
  const versionValue = primaryVersion ?? '未读取';
  process.stdout.write('发布校验通过\n');
  process.stdout.write(`  版本: ${versionValue}（${versionDetail}）\n`);
  process.stdout.write(
    `  Tag: ${options.tag ? `${options.tag}（与版本一致）` : '未指定（跳过 tag 校验）'}\n`,
  );
  process.stdout.write('  LICENSE: MIT License / wuqiuwangtian\n');
  process.stdout.write('  README: LICENSE 链接、CI 徽章\n');
  process.stdout.write('  Git: 敏感文件（.env、*.db、data/）均未被跟踪\n');
  process.stdout.write(`  检查时间: ${CHECKED_AT}\n`);
}

main();
