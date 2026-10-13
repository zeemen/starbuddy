// 舞台样张页与 packages/stage 的接线（specs/stage-whiteboard-step1）：
//   - packages/stage 的公式排版与 apps/web 的 latexToText 同一口径（命令表一致、同一组用例输出一致、禁用命令表一致）；
//   - 样张页的合成样例能通过校验、非法样例被拒；
//   - 样张页只是开发页（page.dev.tsx，渲染前核对运行环境与本机 Host）；不连 BFF、不读会话、不计量、不落库；
//   - apps/web 里只有样张页这一条链路引用 packages/stage，且只经它的接口文件（index.ts、Whiteboard.tsx）；
//   - Turbopack 根目录只在包含开发页的构建里放到仓库根。
import assert from 'node:assert/strict';
import { readdirSync, readFileSync, statSync } from 'node:fs';
import { join, relative } from 'node:path';
import { test } from 'node:test';
import { pageExtensionsFor, repoRoot, turbopackRootFor } from '../next.config.ts';
import * as web from '../src/lib/rich-item.ts';
import { STAGE_INVALID_SAMPLE, STAGE_SAMPLE } from '../src/stage/sample.ts';
import * as stage from '../../../packages/stage/src/index.ts';

const WEB = new URL('..', import.meta.url).pathname;
const read = (p: string) => readFileSync(join(WEB, p), 'utf8');
const files = (dir: string): string[] => readdirSync(dir).flatMap((n) => {
  const p = join(dir, n);
  return statSync(p).isDirectory() ? files(p) : [p];
});

test('公式排版与 apps/web 同一口径', () => {
  assert.deepEqual([...stage.LATEX_COMMANDS], [...web.LATEX_COMMANDS]);
  const cases = ['x^2', 'x_1', 'x^{10}', 'a_{n+1}', '\\frac{1}{2}', '\\dfrac{a}{b+c}', '\\sqrt{2}', '\\sqrt[3]{x}', '2x + 3 = 7',
    '\\angle ABC = 90\\circ', '\\triangle ABC \\cong \\triangle DEF', 'a \\perp b', '\\because x>0 \\therefore y<0', 'x \\le 3', '\\pi r^2',
    '\\left( x \\right)', '\\text{km}', 'x^{ab}', '\\unknown{x}', '\\frac{\\unknown}{2}', '\\,', 'a\\;b', '{x}', '\\Delta', '\\sqrt[n]{a}'];
  for (const c of cases) assert.equal(stage.latexToText(c), web.latexToText(c), c);
  // 禁用命令表一致（只比较禁用表那一段；两边的总长度上限不同：白板单条公式 200，题面 1000）
  const banned = (re: RegExp) => /\(\?:(href\|[^)]*)\)/.exec(re.source)?.[1];
  assert.ok(banned(stage.LATEX_RE));
  assert.equal(banned(stage.LATEX_RE), banned(web.LATEX_RE));
});

test('合成样例通过校验，非法样例被拒（外链、HTML、多余字段、越界、禁用命令、未出场角色）', () => {
  const ok = stage.validateStageDsl(STAGE_SAMPLE);
  assert.ok(ok.ok, JSON.stringify(!ok.ok && ok.errors));
  assert.ok(stage.isValidated(ok.dsl));
  const bad = stage.validateStageDsl(STAGE_INVALID_SAMPLE);
  assert.ok(!bad.ok);
  assert.deepEqual(bad.errors.map((e) => `${e.path} ${e.code}`), [
    '$.steps[0].actions[0].text link_or_script',
    '$.steps[0].actions[1].text html',
    '$.steps[0].actions[2].color unknown_field',
    '$.steps[0].actions[3].latex bad_latex',
    '$.steps[0].actions[4].role bad_role',
  ]);
  // 去掉多余字段后，越界坐标照样被拒
  const { color: _c, ...line } = STAGE_INVALID_SAMPLE.steps[0].actions[2];
  const r = stage.validateStageDsl({ ...STAGE_INVALID_SAMPLE, steps: [{ id: 's1', actions: [line] }] });
  assert.ok(!r.ok && r.errors.some((e) => e.code === 'out_of_canvas'));
});

test('样张页是开发页：只有 page.dev.tsx，渲染前核对运行环境；不连 BFF、不读会话、不计量、不写存储', () => {
  assert.deepEqual(readdirSync(join(WEB, 'src/app/dev/stage')).sort(), ['page.dev.tsx']);
  const page = read('src/app/dev/stage/page.dev.tsx');
  assert.ok(page.includes("if (!devRuntimeAllowed((await headers()).get('host'))) notFound();"));
  assert.ok(page.indexOf('devRuntimeAllowed(') < page.indexOf('<StageDemo'));
  assert.ok(!pageExtensionsFor({}).includes('dev.tsx'));
  const demo = read('src/stage/StageDemo.tsx');
  const sample = read('src/stage/sample.ts');
  for (const src of [page, demo, sample]) {
    const code = src.replace(/^\s*\/\/.*$/gm, '');
    for (const bad of [/fetch\(/, /STARBUDDY_BFF_URL/, /shell-api/, /session/i, /cookies\(/, /usage_id/, /idempotency/i, /localStorage/,
      /sessionStorage/, /indexedDB/, /'use server'/, /actions\.ts/, /\bteach\b/]) {
      assert.ok(!bad.test(code), `${bad}`);
    }
  }
  // 客户端组件只经 packages/stage 的两个接口文件使用舞台
  const imports = [...demo.matchAll(/from '([^']+)'/g)].map((m) => m[1]);
  assert.deepEqual(imports, ['react', '../../../../packages/stage/src/index.ts', '../../../../packages/stage/src/Whiteboard.tsx', './sample.ts']);
});

test('apps/web 里只有样张页链路引用 packages/stage', () => {
  const src = join(WEB, 'src');
  const users = files(src).filter((p) => /\.tsx?$/.test(p)).filter((p) => /packages\/stage/.test(readFileSync(p, 'utf8').replace(/^\s*\/\/.*$/gm, ''))).map((p) => relative(WEB, p));
  assert.deepEqual(users, ['src/stage/StageDemo.tsx']);
  const importers = files(src).filter((p) => /from '[^']*\/StageDemo\.tsx'/.test(readFileSync(p, 'utf8'))).map((p) => relative(WEB, p));
  assert.deepEqual(importers, ['src/app/dev/stage/page.dev.tsx']);
});

test('Turbopack 根目录只在包含开发页的构建里放到仓库根', () => {
  assert.equal(turbopackRootFor({}), undefined);
  assert.equal(turbopackRootFor({ STARBUDDY_DEV_PAGES: '1', STARBUDDY_ENV: 'staging' }), undefined);
  assert.equal(turbopackRootFor({ STARBUDDY_ENV: 'test' }), undefined);
  assert.equal(turbopackRootFor({ STARBUDDY_DEV_PAGES: '1', STARBUDDY_ENV: 'test' }), repoRoot());
  assert.equal(repoRoot(), new URL('../../..', import.meta.url).pathname.replace(/\/$/, ''));
  assert.equal(repoRoot(WEB), repoRoot());
});

test('repoRoot 失败即停：算出的目录里没有 packages/stage/package.json 就抛错', () => {
  for (const dir of ['/tmp/no-such-dir/a/b', '/', join(WEB, 'src')]) {
    assert.throws(() => repoRoot(dir), /packages\/stage\/package\.json/, dir);
  }
  const cfg = read('next.config.ts');
  assert.ok(cfg.includes("existsSync(resolve(root, 'packages', 'stage', 'package.json'))"));
});

/** 允许的仓库外相对导入：只有 packages/stage 的两个接口文件 */
const ALLOWED_OUTSIDE = new Set([join(WEB, '../../packages/stage/src/index.ts'), join(WEB, '../../packages/stage/src/Whiteboard.tsx')]);

/** src 下每个 .ts/.tsx 里解析到 apps/web 之外的相对导入（from '…'、import('…')、export … from '…'），不在白名单的列出来 */
function outsideImports(sources: { path: string; text: string }[]): string[] {
  const bad: string[] = [];
  for (const { path, text } of sources) {
    for (const m of text.matchAll(/(?:from|import)\s*\(?\s*['"](\.{1,2}\/[^'"]+)['"]/g)) {
      const target = join(path, '..', m[1]!);
      if (!target.startsWith(`${WEB}`) && !ALLOWED_OUTSIDE.has(target)) bad.push(`${relative(WEB, path)} → ${m[1]}`);
    }
  }
  return bad;
}

test('apps/web/src 里指向 apps/web 之外的相对导入只允许 packages/stage 的 index.ts 与 Whiteboard.tsx', () => {
  const sources = files(join(WEB, 'src')).filter((p) => /\.tsx?$/.test(p)).map((p) => ({ path: p, text: readFileSync(p, 'utf8') }));
  assert.deepEqual(outsideImports(sources), []);
  // 变异：把样张页的导入改到 packages/stage 的内部文件或仓库别处，核对能被发现
  const demo = sources.find((s) => s.path.endsWith('src/stage/StageDemo.tsx'))!;
  for (const [from, to] of [
    ['packages/stage/src/index.ts', 'packages/stage/src/validate.ts'],
    ['packages/stage/src/Whiteboard.tsx', 'packages/stage/src/board.ts'],
    ["'../../../../packages/stage/src/index.ts'", "'../../../../services/bff/src/main.ts'"],
    ["from './sample.ts'", "from '../../../../packages/stage/tests/fixtures.ts'"],
  ]) {
    const mutated = { ...demo, text: demo.text.replace(from!, to!) };
    assert.notEqual(mutated.text, demo.text, from);
    assert.equal(outsideImports([mutated]).length, 1, `${from} → ${to} 没被发现`);
  }
  assert.equal(outsideImports([{ path: join(WEB, 'src/x.ts'), text: "const m = await import('../../../db/x.ts');" }]).length, 1);
});
