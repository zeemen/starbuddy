// 讲解 DSL 的版本规则与迁移阶梯（specs/stage-dsl-versioning）：
//   - 格式指纹与已发布 schema 锁在 schema/versions.lock.json：改格式不升版本、改已发布 schema 都会失败；
//   - 迁移阶梯首尾相接；未来版本、未知版本、迁移失败一律拒收；迁移不改文字、幂等、不改输入，迁移后仍过完整校验。
// 合成阶梯（0.0.1 → 0.0.2 → 0.3.0）只存在于本测试，用来验证机制；真实阶梯的第一级 0.1.0 → 0.2.0 另有用例。全部是合成数据。
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { existsSync, readdirSync, readFileSync } from 'node:fs';
import { test } from 'node:test';
import {
  compareVersions, DSL_MIGRATIONS, DSL_VERSION, dslTexts, formatSpec, isValidated, ladderProblems, migrateStageDsl, validateStageDsl,
  type DslMigration,
} from '../src/index.ts';
import { NARRATED, SAMPLE, SAMPLE_V01 } from './fixtures.ts';

const direct = (() => { const v = validateStageDsl(SAMPLE); if (!v.ok) throw new Error('合成样例应合格'); return v.dsl; })();

const sha = (s: string | Buffer) => createHash('sha256').update(s).digest('hex');
const schemaPath = (f: string) => new URL(`../schema/${f}`, import.meta.url);
const lock = JSON.parse(readFileSync(schemaPath('versions.lock.json'), 'utf8')) as {
  versions: { version: string; format_sha256: string; schema: string; schema_sha256: string; script_schema?: string; script_schema_sha256?: string }[];
};
const pkg = JSON.parse(readFileSync(new URL('../package.json', import.meta.url), 'utf8')) as { exports: Record<string, string> };

test('锁文件：版本递增不重复，最后一行是当前版本，当前格式常量的指纹与它一致（改了格式就得升版本）', () => {
  const vs = lock.versions.map((v) => v.version);
  assert.ok(vs.length >= 1);
  for (let i = 1; i < vs.length; i++) assert.ok(compareVersions(vs[i - 1]!, vs[i]!)! < 0, `${vs[i - 1]} 应早于 ${vs[i]}`);
  const last = lock.versions[lock.versions.length - 1]!;
  assert.equal(last.version, DSL_VERSION, '锁文件最后一行应是当前 DSL_VERSION（升版本时追加一行）');
  assert.equal(sha(formatSpec()), last.format_sha256,
    '格式常量变了但 DSL_VERSION 没升：升版本、新增该版本的 schema、在锁文件追加一行、在迁移阶梯追加一级（packages/stage/README.md）');
});

test('锁文件：每个已发布版本都有自己冻结的 schema，内容一字未改；没有未登记的 schema 文件；当前版本的 schema 是包导出的那一份', () => {
  for (const v of lock.versions) {
    assert.equal(v.schema, `stage-dsl-${v.version}.schema.json`, v.version);
    assert.ok(existsSync(schemaPath(v.schema)), `缺 ${v.schema}`);
    assert.equal(sha(readFileSync(schemaPath(v.schema))), v.schema_sha256, `${v.schema} 已发布，不能修改（格式变了请升版本、另起新文件）`);
    const s = JSON.parse(readFileSync(schemaPath(v.schema), 'utf8'));
    assert.equal(s.properties.dsl_version.const, v.version, `${v.schema} 里的版本号`);
    if (v.script_schema !== undefined) {
      assert.equal(v.script_schema, `stage-script-${v.version}.schema.json`, v.version);
      assert.equal(sha(readFileSync(schemaPath(v.script_schema))), v.script_schema_sha256, `${v.script_schema} 已发布，不能修改`);
      assert.equal(JSON.parse(readFileSync(schemaPath(v.script_schema), 'utf8')).properties.dsl_version.const, v.version);
    }
  }
  const files = readdirSync(new URL('../schema/', import.meta.url)).filter((f) => f.endsWith('.schema.json')).sort();
  const listed = lock.versions.flatMap((v) => [v.schema, ...(v.script_schema !== undefined ? [v.script_schema] : [])]).sort();
  assert.deepEqual(files, listed, 'schema 目录里只有锁文件登记过的文件');
  assert.equal(pkg.exports['./schema'], `./schema/stage-dsl-${DSL_VERSION}.schema.json`);
  assert.equal(pkg.exports['./script-schema'], `./schema/stage-script-${DSL_VERSION}.schema.json`);
});

test('真实阶梯：合规（首尾相接、落在当前版本）；每级的起点都是已发布版本；每个已发布的旧版本都在阶梯上', () => {
  assert.deepEqual(ladderProblems(DSL_MIGRATIONS), []);
  assert.deepEqual(DSL_MIGRATIONS.map((m) => m.from), lock.versions.slice(0, -1).map((v) => v.version), '保留全部历史版本（spec 第五节第 2 条）');
  const released = new Set(lock.versions.map((v) => v.version));
  for (const m of DSL_MIGRATIONS) assert.ok(released.has(m.from) && released.has(m.to), `${m.from} → ${m.to}`);
  assert.ok(Object.isFrozen(DSL_MIGRATIONS));
});

test('版本比较与阶梯检查', () => {
  assert.equal(compareVersions('0.1.0', '0.1.0'), 0);
  assert.ok(compareVersions('0.1.0', '0.2.0')! < 0);
  assert.ok(compareVersions('0.10.0', '0.9.9')! > 0, '按数字比，不按字符串比');
  assert.ok(compareVersions('1.0.0', '0.99.99')! > 0);
  for (const bad of ['0.1', 'v0.1.0', '0.1.0-rc', '01.0.0', '0.1.0 ', '']) assert.equal(compareVersions(bad, '0.1.0'), null, bad);
  const id = (d: Record<string, unknown>) => d;
  assert.deepEqual(ladderProblems([], '0.1.0'), []);
  assert.equal(ladderProblems([{ from: '0.0.1', to: '0.0.2', migrate: id }, { from: '0.0.3', to: '0.1.0', migrate: id }], '0.1.0').length, 1, '断档');
  assert.equal(ladderProblems([{ from: '0.0.2', to: '0.0.1', migrate: id }], '0.0.1').length, 1, '倒退');
  assert.equal(ladderProblems([{ from: '0.0.1', to: '0.0.2', migrate: id }], '0.1.0').length, 1, '没落在当前版本');
});

test('真实阶梯 0.1.0 → 0.2.0 → 0.3.0：补 narrated: false、再只升版本号，其余逐字不变，结果与直接写成当前版本的文档相同', () => {
  const before = JSON.stringify(SAMPLE_V01);
  const r = migrateStageDsl(SAMPLE_V01);
  assert.ok(r.ok, JSON.stringify(!r.ok && r.errors));
  assert.equal(r.from, '0.1.0');
  assert.equal(r.dsl.dsl_version, '0.3.0');
  assert.equal(r.dsl.narrated, false);
  assert.deepEqual(dslTexts(r.dsl), dslTexts(SAMPLE_V01));
  assert.deepEqual(r.dsl, direct);
  assert.equal(JSON.stringify(SAMPLE_V01), before);
  // 0.1.0 本来就合格的文档（老校验口径）都能升上来；带了 v0.2 字段的“0.1.0”文档不合格
  const bad = migrateStageDsl({ ...SAMPLE_V01, steps: [{ id: 's1', say: 'l1', actions: [{ type: 'speaker', role: 'teacher' }] }] });
  assert.ok(!bad.ok && bad.errors[0]!.code === 'migration_failed');
});

test('第二级真实迁移 0.2.0 → 0.3.0：DSL 结构不变，只升版本号（有台词的样例也一样）', () => {
  for (const doc of [SAMPLE, NARRATED]) {
    const v02 = { ...structuredClone(doc), dsl_version: '0.2.0' };
    const before = JSON.stringify(v02);
    const r = migrateStageDsl(v02);
    assert.ok(r.ok, JSON.stringify(!r.ok && r.errors));
    assert.equal(r.from, '0.2.0');
    assert.deepEqual(r.dsl, validateStageDsl(doc).ok && (validateStageDsl(doc) as { dsl: unknown }).dsl);
    assert.equal(JSON.stringify(v02), before);
  }
});

// 合成阶梯：0.0.1 的标题字段叫 heading、没有 roles 与 narrated；0.0.2 改名为 title；0.3.0 补上 roles 与 narrated。只改结构，不改文字
const SYN: DslMigration[] = [
  { from: '0.0.1', to: '0.0.2', textKeys: ['heading', 'text', 'latex'], migrate: (d) => { const { heading, ...rest } = d; return { ...rest, title: heading }; } },
  { from: '0.0.2', to: '0.3.0', migrate: (d) => ({ ...d, narrated: false, ...('roles' in d ? {} : { roles: ['teacher', 'buddy'] }) }) },
];
const OLD = (() => {
  const { title, roles: _roles, narrated: _n, ...rest } = structuredClone(SAMPLE) as Record<string, unknown>;
  return { ...rest, dsl_version: '0.0.1', heading: title };
})();

test('当前版本：与直接校验同一结果；缺版本或版本不是 x.y.z 时由校验器报错', () => {
  const r = migrateStageDsl(SAMPLE);
  assert.ok(r.ok && isValidated(r.dsl) && r.from === DSL_VERSION);
  assert.deepEqual(r.ok && r.dsl, direct);
  const { dsl_version: _v, ...noVersion } = SAMPLE;
  const a = migrateStageDsl(noVersion);
  assert.ok(!a.ok && a.errors.some((e) => e.code === 'missing_field'));
  const b = migrateStageDsl({ ...SAMPLE, dsl_version: 'v1' });
  assert.ok(!b.ok && b.errors.some((e) => e.code === 'bad_version'));
  assert.ok(!migrateStageDsl(null).ok && !migrateStageDsl([]).ok);
});

test('未来版本与未知版本一律拒收（不降级、不猜测）', () => {
  for (const v of ['0.3.1', '0.4.0', '1.0.0']) {
    const r = migrateStageDsl({ ...SAMPLE, dsl_version: v });
    assert.ok(!r.ok && r.errors[0]!.code === 'future_version' && r.from === v, v);
  }
  const r = migrateStageDsl({ ...SAMPLE, dsl_version: '0.0.5' });
  assert.ok(!r.ok && r.errors[0]!.code === 'unknown_version', '真实阶梯从 0.1.0 起：更早的版本找不到');
  const s = migrateStageDsl({ ...OLD, dsl_version: '0.0.0' }, SYN, '0.3.0');
  assert.ok(!s.ok && s.errors[0]!.code === 'unknown_version', '合成阶梯上也没有 0.0.0');
});

test('沿阶梯升到当前版本：两级都走、文字逐字保留、结果通过完整校验、幂等、不改输入', () => {
  const before = JSON.stringify(OLD);
  const r = migrateStageDsl(OLD, SYN, '0.3.0');
  assert.ok(r.ok, JSON.stringify(!r.ok && r.errors));
  assert.equal(r.from, '0.0.1');
  assert.ok(isValidated(r.dsl) && r.dsl.dsl_version === '0.3.0');
  assert.deepEqual(dslTexts(r.dsl), dslTexts(SAMPLE));
  assert.deepEqual(r.dsl, direct, '与直接写成当前版本的文档相同');
  assert.equal(JSON.stringify(OLD), before, '输入不被改动');
  const { heading: _h, ...noHeading } = OLD;
  const mid = migrateStageDsl({ ...noHeading, dsl_version: '0.0.2', title: OLD.heading }, SYN, '0.3.0');
  assert.ok(mid.ok && mid.from === '0.0.2', '从中间一级起步');
  const again = migrateStageDsl(JSON.parse(JSON.stringify(r.dsl)), SYN, '0.3.0');
  assert.ok(again.ok && JSON.stringify(again.dsl) === JSON.stringify(r.dsl), '幂等：已是当前版本再读一次结果相同');
});

test('迁移失败一律拒收：迁移抛错、返回非对象、改了文字、升级后不合格、阶梯不合规', () => {
  const fail = (ladder: DslMigration[], doc: unknown = OLD) => {
    const r = migrateStageDsl(doc, ladder, '0.3.0');
    return !r.ok && r.errors[0]!.code === 'migration_failed';
  };
  assert.ok(fail([SYN[0]!, { from: '0.0.2', to: '0.3.0', migrate: () => { throw new Error('合成故障'); } }]), '抛错');
  assert.ok(fail([SYN[0]!, { from: '0.0.2', to: '0.3.0', migrate: () => [] as unknown as Record<string, unknown> }]), '返回数组');
  assert.ok(fail([SYN[0]!, { from: '0.0.2', to: '0.3.0', migrate: (d) => ({ ...d, roles: ['teacher', 'buddy'], narrated: false, title: '（合成）改过的标题' }) }]), '改了标题');
  assert.ok(fail([SYN[0]!, {
    from: '0.0.2', to: '0.3.0',
    migrate: (d) => ({ ...d, roles: ['teacher', 'buddy'], narrated: false, steps: (d['steps'] as { id: string; actions: Record<string, unknown>[] }[]).map((s) => ({
      ...s, actions: s.actions.map((a) => (a['type'] === 'write_formula' ? { ...a, latex: `${a['latex']} ` } : a)),
    })) }),
  }]), '改了公式（多一个空格也不行）');
  assert.ok(fail([SYN[0]!, { from: '0.0.2', to: '0.3.0', migrate: (d) => d }]), '升级后缺 roles，完整校验不过');
  const bad = migrateStageDsl(OLD, [SYN[0]!, { from: '0.0.2', to: '0.3.0', migrate: (d) => d }], '0.3.0');
  assert.ok(!bad.ok && bad.errors.some((e) => e.code === 'missing_field'), '同时带出校验错误，便于进重审');
  assert.ok(fail([SYN[0]!, { from: '0.0.2', to: '0.3.0', textKeys: ['title'], migrate: SYN[1]!.migrate }]), '声明的旧字段名与实际不符时文字对不上');
  assert.ok(fail([{ from: '0.0.1', to: '0.0.2', migrate: SYN[0]!.migrate }]), '阶梯没落在当前版本');
  assert.ok(fail([SYN[0]!, { from: '0.0.3', to: '0.3.0', migrate: SYN[1]!.migrate }]), '阶梯断档');
});
