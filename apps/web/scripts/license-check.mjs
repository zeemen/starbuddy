#!/usr/bin/env node
// 许可证检查（spec 范围第 6 条；CLAUDE.md 规则 10）：从 package-lock.json 生成 LICENSES.md，
// 列出全部直接与传递依赖的许可证。失败条件：
//   - 任何 GPL、AGPL、LGPL 许可证（例外须人批，登记在 EXCEPTIONS，现为空）；
//   - 锁文件里出现 @img/sharp-*（libvips，LGPL-3.0-or-later），或 sharp 不是仓库内的占位包；
//   - 有包没有写许可证；
//   - 已提交的 LICENSES.md 与锁文件不一致（改了依赖却没重新生成）。
// 用法：node scripts/license-check.mjs          检查
//       node scripts/license-check.mjs --write  重新生成 LICENSES.md
import { readFileSync, writeFileSync } from 'node:fs';

const WEB = new URL('..', import.meta.url);
const lock = JSON.parse(readFileSync(new URL('package-lock.json', WEB), 'utf8'));
const EXCEPTIONS = new Set([]);  // 形如 "包名@版本"，每一项都要有人批准的记录
const COPYLEFT = /\b(A?GPL|LGPL)\b/i;

const errors = [];
const rows = [];
for (const [path, meta] of Object.entries(lock.packages ?? {})) {
  if (path === '') continue;
  const name = meta.name ?? path.replace(/^.*node_modules\//, '');
  if (/(^|\/)@img\//.test(path)) errors.push(`锁文件里出现 ${path}（sharp 的 libvips，LGPL）`);
  if (name === 'sharp' || path.endsWith('node_modules/sharp')) {
    const stub = meta.link ? meta.resolved === 'vendor/no-sharp' : path === 'vendor/no-sharp';
    if (!stub) errors.push(`${path} 不是仓库内的占位包 vendor/no-sharp`);
  }
  if (meta.link) continue;  // 链接本身不是包；被链接的目标另有一行
  const license = meta.license ?? '';
  const id = `${name}@${meta.version ?? ''}`;
  if (!license) errors.push(`${id} 没有许可证信息`);
  if (COPYLEFT.test(license) && !EXCEPTIONS.has(id)) errors.push(`${id} 的许可证是 ${license}`);
  rows.push({ name, version: meta.version ?? '', license: license || '（缺失）', scope: meta.dev ? '开发' : meta.devOptional ? '开发（next 的可选同伴依赖）' : '运行', optional: meta.optional ? '可选' : '' });
}
rows.sort((a, b) => (a.name + a.version).localeCompare(b.name + b.version));

const counts = {};
for (const r of rows) counts[r.license] = (counts[r.license] ?? 0) + 1;
const md = [
  '# apps/web 依赖许可证清单',
  '',
  '由 `node scripts/license-check.mjs --write` 从 `package-lock.json` 生成，不要手改。`make web-test` 会检查它与锁文件一致。',
  '',
  '- 规则：不得有 GPL、AGPL、LGPL（CLAUDE.md 规则 10）；例外须人批准。',
  '- `sharp` 由仓库内的占位包 `vendor/no-sharp` 顶替，锁文件里没有 `@img/sharp-libvips-*`（LGPL-3.0-or-later，ADR 07 待人确认 #2）。',
  '- MPL-2.0 的 `axe-core` 只用于测试，不进构建产物（spec 待人确认 #13）。',
  '',
  '## 汇总',
  '',
  '| 许可证 | 包数 |',
  '|---|---|',
  ...Object.entries(counts).sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0])).map(([l, n]) => `| ${l} | ${n} |`),
  '',
  '## 明细',
  '',
  '| 包 | 版本 | 许可证 | 用途 | 可选 |',
  '|---|---|---|---|---|',
  ...rows.map((r) => `| ${r.name} | ${r.version} | ${r.license} | ${r.scope} | ${r.optional} |`),
  '',
].join('\n');

const target = new URL('LICENSES.md', WEB);
if (process.argv.includes('--write')) {
  writeFileSync(target, md);
} else {
  let current = '';
  try { current = readFileSync(target, 'utf8'); } catch { /* 没有文件 */ }
  if (current !== md) errors.push('LICENSES.md 与锁文件不一致：执行 node scripts/license-check.mjs --write 重新生成');
}
if (errors.length) {
  for (const e of errors) console.log(`license-check: ${e}`);
  console.log(`license-check: 失败（${errors.length} 处）`);
  process.exit(1);
}
console.log(`license-check: 通过（${rows.length} 个包；${Object.entries(counts).map(([l, n]) => `${l} ${n}`).join('、')}）`);
