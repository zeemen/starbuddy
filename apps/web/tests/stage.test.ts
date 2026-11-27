// 舞台样张页与 packages/stage 的接线（specs/stage-whiteboard-step1）：
//   - packages/stage 的公式排版与 apps/web 的 latexToText 同一口径（命令表一致、同一组用例输出一致、禁用命令表一致）；
//   - 样张页的合成样例能通过校验、非法样例被拒；
//   - 样张页只是开发页（page.dev.tsx，渲染前核对运行环境与本机 Host）；不连 BFF、不读会话、不计量、不落库；
//   - apps/web 里只有样张页与讲评课页面两条链路引用 packages/stage，且只经它的接口文件（index.ts、Whiteboard.tsx）；
//   - Turbopack 根目录在构建与开发阶段放到仓库根，运行阶段（next start）不放、不调用 repoRoot()（specs/stage-production-build）。
import assert from 'node:assert/strict';
import { readdirSync, readFileSync, statSync } from 'node:fs';
import { join, relative } from 'node:path';
import { test } from 'node:test';
import { fileURLToPath } from 'node:url';
import { PHASE_DEVELOPMENT_SERVER, PHASE_EXPORT, PHASE_PRODUCTION_BUILD, PHASE_PRODUCTION_SERVER, PHASE_TEST } from 'next/constants.js';
import nextConfig, { pageExtensionsFor, repoRoot, turbopackRootFor } from '../next.config.ts';
import * as web from '../src/lib/rich-item.ts';
import { STAGE_INVALID_SAMPLE, STAGE_SAMPLE, STAGE_SCRIPT } from '../src/stage/sample.ts';
import * as stage from '../../../packages/stage/src/index.ts';

const WEB = fileURLToPath(new URL('..', import.meta.url));
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
  // v0.2（specs/stage-dsl-v0.2）：有台词的样例与台词稿配对通过；台词全是“（合成台词）”占位；样张页读入走版本迁移入口
  assert.equal(ok.dsl.narrated, true);
  const pair = stage.validateStagePair(ok.dsl, STAGE_SCRIPT);
  assert.ok(pair.ok, JSON.stringify(!pair.ok && pair.errors));
  for (const l of STAGE_SCRIPT.lines) assert.ok(l.text.startsWith('（合成台词）') || l.text.startsWith('（合成答疑）'), l.text);
  // v0.3（specs/stage-point-to-ask-answer PR ②）：预制答疑合格，按步骤与问法能取到；样张页台词稿经迁移入口读入
  assert.equal(pair.ok && stage.answerFor(ok.dsl, pair.script, 's3', 'why_step')?.id, 'a1');
  assert.equal(pair.ok && stage.answerFor(ok.dsl, pair.script, 's1', 'what_is'), null);
  assert.ok(stage.migrateStageDsl(STAGE_SAMPLE).ok);
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
  assert.deepEqual(imports, ['react', '../../../../packages/stage/src/index.ts', '../../../../packages/stage/src/Whiteboard.tsx', '../shell/copy.ts',
    './sample.ts']);
});

test('指着元素提问（specs/stage-point-to-ask）：文案全是占位并入壳文案扫描；样张页只经校验后的提问对象，不发请求、不作证据', async () => {
  const { ALL_SHELL_COPY, STAGE_ASK } = await import('../src/shell/copy.ts');
  for (const [k, v] of Object.entries(STAGE_ASK)) {
    assert.ok(v.startsWith(`{{TODO_COPY:ask.${k}}}`), `${k}: ${v}`);
    assert.ok(ALL_SHELL_COPY.includes(v), k);
  }
  for (const intent of stage.QUESTION_INTENTS) assert.ok(intent in STAGE_ASK, intent);
  const demo = read('src/stage/StageDemo.tsx').replace(/^\s*\/\/.*$/gm, '');
  assert.ok(demo.includes('validateStageQuestion(dsl, {'), '提问对象必须经校验');
  assert.ok(demo.includes('migrateStageScript(valid.dsl, STAGE_SCRIPT)'), '台词稿经迁移入口读入');
  assert.ok(demo.includes('answerFor(dsl, pair.script, stepId, intent)'), '回答只取台词稿里的预制答疑');
  assert.ok(demo.includes('askable={{ selected, onSelect: select, label }}'));
  // 只显示占位，不把提问对象交给任何网络或存储（上一条测试已禁 fetch、存储、server action）；不出现自由文字输入
  for (const bad of [/<input[^>]*type="text"/, /<textarea/, /contentEditable/, /evidence/i, /record\(/]) assert.ok(!bad.test(demo), `${bad}`);
  // 菜单与标签文字只来自文案，组件不拼接其他中文
  const strings = [...demo.matchAll(/'([^'\n]*[\u4e00-\u9fff][^'\n]*)'/g)].map((m) => m[1]);
  assert.deepEqual(strings.filter((x) => !['桌面', '平板', '手机', '未开始', '播放中', '已暂停', '已结束'].includes(x!)), []);
});

test('apps/web 里只有样张页（开发页）与讲评课页面链路引用 packages/stage', () => {
  const src = join(WEB, 'src');
  const users = files(src).filter((p) => /\.tsx?$/.test(p)).filter((p) => /packages\/stage/.test(readFileSync(p, 'utf8').replace(/^\s*\/\/.*$/gm, ''))).map((p) => relative(WEB, p));
  assert.deepEqual(users.sort(), ['src/lesson/LessonPlayer.tsx', 'src/stage/StageDemo.tsx']);
  const importers = files(src).filter((p) => /from '[^']*\/StageDemo\.tsx'/.test(readFileSync(p, 'utf8'))).map((p) => relative(WEB, p));
  assert.deepEqual(importers, ['src/app/dev/stage/page.dev.tsx']);
  // 讲评课：播放组件只由讲评课页面组件引用，页面组件只由讲评课路由引用（specs/stage-production-build 起是普通页面 page.tsx）
  const of = (name: string) => files(src).filter((p) => new RegExp(`from '[^']*/${name}\\.tsx'`).test(readFileSync(p, 'utf8'))).map((p) => relative(WEB, p));
  assert.deepEqual(of('LessonPlayer'), ['src/lesson/LessonPage.tsx']);
  assert.deepEqual(of('LessonPage'), ['src/app/s/[studentId]/learn/[subject]/lesson/[lessonId]/page.tsx']);
});

test('Turbopack 根目录：构建与开发阶段放到仓库根，运行阶段（next start）与其他阶段保持默认', () => {
  assert.equal(turbopackRootFor(PHASE_PRODUCTION_BUILD), repoRoot());
  assert.equal(turbopackRootFor(PHASE_DEVELOPMENT_SERVER), repoRoot());
  for (const ph of [PHASE_PRODUCTION_SERVER, PHASE_EXPORT, PHASE_TEST, '']) assert.equal(turbopackRootFor(ph), undefined, ph);
  // 与开发页开关、运行环境无关：生产口径的构建（不设 STARBUDDY_DEV_PAGES）同样放宽
  assert.deepEqual((nextConfig(PHASE_PRODUCTION_BUILD) as { turbopack?: unknown }).turbopack, { root: repoRoot() });
  assert.equal((nextConfig(PHASE_PRODUCTION_SERVER) as { turbopack?: unknown }).turbopack, undefined);
  // 其余配置三个阶段相同（安全头、输出目录、页面扩展名）
  const { turbopack: _t, ...build } = nextConfig(PHASE_PRODUCTION_BUILD);
  assert.deepEqual(build, nextConfig(PHASE_PRODUCTION_SERVER));
  assert.equal(repoRoot(), fileURLToPath(new URL('../../..', import.meta.url)).replace(/\/$/, ''));
  assert.equal(repoRoot(WEB), repoRoot());
});

test('运行阶段不调用 repoRoot()：运行镜像里没有 packages/stage 也能加载配置', () => {
  const cfg = read('next.config.ts').replace(/\/\*[\s\S]*?\*\//g, '').replace(/(^|[^:])\/\/.*$/gm, '$1');  // 去掉注释
  // 顶层不调用 repoRoot()（模块加载时不碰文件系统）；只有 turbopackRootFor 在构建、开发阶段调用
  assert.equal(cfg.match(/repoRoot\(\)/g)?.length, 1);
  assert.match(cfg, /phase === PHASE_PRODUCTION_BUILD \|\| phase === PHASE_DEVELOPMENT_SERVER \? repoRoot\(\) : undefined/);
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
