// 前端白名单与取值出处的一致性（specs/web-bff-enum-drift）。
// 前端把 BFF 的返回当 unknown，再经 pick* 按白名单收窄（apps/web/src/trust/view.ts、practice/view.ts、retest/view.ts）：出处多了一个取值，
// 前端不报错，只把整块内容静默降级成“暂时无法获取”或丢掉那一条。这里逐行登记“前端白名单 ↔ 出处”并比对：
//   出处三种——库（schema.表.列：单列 CHECK 里的字面量，或枚举类型；或库函数体里写死的列表）、契约（openapi 文件里锚点之后的第一个 enum）、
//   BFF 常量（BFF 自己推导或转写的取值）。另核对 BFF 转写的常量与契约 / 库一致。
// 口径：默认相同；有意更窄的登记 subset 与原因；前端比出处宽一律不允许。
// 前端模块用计算出来的路径动态导入：只取运行时的值，不让 BFF 的 tsconfig 去类型检查前端文件（两边的严格选项不同）。
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { after, before, test } from 'node:test';
import pg from 'pg';
import { ENTRY_STATUSES as BFF_PRACTICE_ENTRY, AFTER_STATUSES as BFF_PRACTICE_AFTER, SOURCES as BFF_PRACTICE_SOURCES, VERDICTS as BFF_VERDICTS } from '../src/practice/service.ts';
import { AFTER_STATUSES as BFF_RETEST_AFTER, ENTRY_STATUSES as BFF_RETEST_ENTRY, KINDS as BFF_RETEST_KINDS } from '../src/retest/service.ts';
import { ADJUSTMENTS as BFF_ADJUSTMENTS, DIRECTIONS as BFF_DIRECTIONS, DISPUTE_KINDS as BFF_DISPUTE_KINDS, NEXT_STEPS as BFF_NEXT_STEPS } from '../src/trust/clients.ts';
import { HAS_DB } from './harness.ts';

const ROOT = new URL('../../../', import.meta.url).pathname;
const CONTRACT = 'packages/contracts/openapi/insight-service.v0.9.yaml';

type Source =
  | { db: [schema: string, table: string, column: string] }
  | { dbFn: [fn: string, pattern: RegExp] }
  | { contract: [file: string, anchor: string, mustContain?: string] }
  | { bff: readonly string[]; where: string };
interface Row {
  name: string;
  values: () => Promise<readonly string[]>;
  source: Source;
  subset?: string;  // 有意更窄的原因；不给就要求相同
}

const web = async (path: string, name: string, key?: string): Promise<readonly string[]> => {
  const mod = (await import(`${ROOT}apps/web/src/${path}`)) as Record<string, unknown>;
  const v = key === undefined ? mod[name] : (mod[name] as Record<string, unknown>)[key];
  assert.ok(Array.isArray(v), `apps/web/src/${path} 没有导出数组 ${name}${key ? `.${key}` : ''}`);
  return v as string[];
};
const fixed = (xs: readonly string[]) => async () => xs;

/** 契约：锚点（文件里唯一的一行）之后 10 行内第一个 enum: [...]（mustContain 给了时那一行还要含它）；不引入 YAML 依赖 */
export function contractEnum(file: string, anchor: string, mustContain?: string, text?: string): string[] {
  const lines = (text ?? readFileSync(`${ROOT}${file}`, 'utf8')).split('\n');
  const at = lines.flatMap((l, i) => (l.includes(anchor) ? [i] : []));
  assert.equal(at.length, 1, `${file}：锚点“${anchor}”应恰好出现一次，实际 ${at.length} 次`);
  for (const l of lines.slice(at[0], at[0]! + 10)) {
    const m = /enum: \[([^\]]*)\]/.exec(l);
    if (m && (mustContain === undefined || l.includes(mustContain))) return m[1]!.split(',').map((s) => s.trim()).filter(Boolean);
  }
  assert.fail(`${file}：锚点“${anchor}”之后 10 行内没有 enum`);
}

/** 库：枚举类型取 pg_enum；文本列取只约束这一列的 CHECK 里的字面量（IN (...) 归一成 = ANY (ARRAY[...])，数组列是 <@ ARRAY[...]） */
async function dbValues(su: pg.Pool, schema: string, table: string, column: string): Promise<string[]> {
  const col = await su.query(
    `SELECT a.attnum, t.typtype::text AS typtype, t.oid AS typ, t.typelem AS elem FROM pg_attribute a JOIN pg_class c ON c.oid = a.attrelid
       JOIN pg_namespace n ON n.oid = c.relnamespace JOIN pg_type t ON t.oid = a.atttypid
      WHERE n.nspname = $1 AND c.relname = $2 AND a.attname = $3`, [schema, table, column]);
  assert.equal(col.rowCount, 1, `${schema}.${table}.${column} 不存在`);
  const { attnum, typtype, typ } = col.rows[0] as { attnum: number; typtype: string; typ: number };
  if (typtype === 'e') {
    return (await su.query('SELECT enumlabel FROM pg_enum WHERE enumtypid = $1', [typ])).rows.map((r: { enumlabel: string }) => r.enumlabel);
  }
  const defs = (await su.query(
    `SELECT pg_get_constraintdef(k.oid) AS d FROM pg_constraint k JOIN pg_class c ON c.oid = k.conrelid JOIN pg_namespace n ON n.oid = c.relnamespace
      WHERE n.nspname = $1 AND c.relname = $2 AND k.contype = 'c' AND k.conkey = ARRAY[$3::smallint]`, [schema, table, attnum])).rows
    .map((r: { d: string }) => r.d);
  const out = new Set<string>();
  for (const d of defs) {
    const m = /ARRAY\[([^\]]*)\]/.exec(d);
    if (m) for (const v of m[1]!.matchAll(/'([^']*)'/g)) out.add(v[1]!);
  }
  assert.ok(out.size > 0, `${schema}.${table}.${column} 没有单列的取值约束：${JSON.stringify(defs)}`);
  return [...out];
}

/** 库函数体里写死的列表（周报句的段落、客观栏的学科：insight.valid_weekly_*，0036）：pattern 的第一组是 '…', '…' */
async function dbFnValues(su: pg.Pool, fn: string, pattern: RegExp): Promise<string[]> {
  const { rows } = await su.query('SELECT pg_get_functiondef($1::regprocedure) AS d', [fn]);
  const m = pattern.exec((rows[0] as { d: string }).d);
  assert.ok(m, `${fn} 的函数体里找不到 ${pattern}（库函数改写了，先更新这里的模式）`);
  return [...m[1]!.matchAll(/'([^']*)'/g)].map((v) => v[1]!);
}

const V = 'trust/view.ts';
const P = 'practice/view.ts';
const R = 'retest/view.ts';
export const ROWS: Row[] = [
  // ---- 信任界面
  { name: '置信度分档', values: () => web(V, 'BANDS'), source: { contract: [CONTRACT, 'description: 置信度说明的编码，由前端按模板展示。'] } },
  { name: '证据来源', values: () => web(V, 'SOURCES'), source: { contract: [CONTRACT, '    SourceType:'] } },
  { name: '证据来源（库）', values: () => web(V, 'SOURCES'), source: { db: ['insight', 'claim', 'source_type'] } },
  { name: '异议类型', values: () => web(V, 'DISPUTE_KINDS'), source: { db: ['insight', 'dispute', 'kind'] } },
  { name: '异议的下一步', values: () => web(V, 'NEXT_STEPS'), source: { contract: [CONTRACT, 'verification_scheduled：已安排验证题'] } },
  { name: '异议状态', values: () => web(V, 'DISPUTE_STATUSES'), source: { db: ['insight', 'dispute', 'status'] } },
  { name: '考试科目', values: () => web(V, 'EXAM_SUBJECTS'), source: { db: ['insight', 'exam_record', 'subject'] }, subset: '英语（echo）搁置，不显示' },
  { name: '考试类型', values: () => web(V, 'EXAM_TYPES'), source: { db: ['insight', 'exam_record', 'exam_type'] } },
  { name: '偏差方向', values: () => web(V, 'DEVIATION_DIRECTIONS'), source: { contract: [CONTRACT, '偏差分析固定检查的四个方向'] } },
  { name: '对账调整', values: () => web(V, 'ADJUSTMENTS'), source: { contract: [CONTRACT, '平台据此改了什么（编码）'] } },
  { name: '周报段落（学生）', values: () => web(V, 'WEEKLY_SECTIONS', 'student'),
    source: { dbFn: ['insight.valid_weekly_sentences(jsonb, text)', /p_audience = 'student' AND \(\(s ->> 'section'\) IS NULL OR \(s ->> 'section'\) NOT IN \(([^)]*)\)/] } },
  { name: '周报段落（家长）', values: () => web(V, 'WEEKLY_SECTIONS', 'guardian'),
    source: { dbFn: ['insight.valid_weekly_sentences(jsonb, text)', /p_audience = 'guardian' AND \(\(s ->> 'section'\) IS NULL OR \(s ->> 'section'\) NOT IN \(([^)]*)\)/] } },
  { name: '周报客观栏学科', values: () => web(V, 'OBJECTIVE_SUBJECTS'),
    source: { dbFn: ['insight.valid_weekly_objective(jsonb)', /\(s ->> 'subject'\) NOT IN \(([^)]*)\)/] } },
  { name: '删除范围', values: () => web(V, 'SCOPES'), source: { db: ['platform', 'personal_table_registry', 'erase_scope'] } },
  { name: '删除范围（契约）', values: () => web(V, 'SCOPES'), source: { contract: [CONTRACT, 'required: [erase_receipt_id, status, scopes]', 'items:'] } },
  { name: '删除撤回原因', values: () => web(V, 'CANCEL_REASONS'), source: { db: ['platform', 'erasure_request', 'cancel_reason'] } },
  // ---- 错题再练
  { name: '再练条目状态', values: () => web(P, 'ENTRY_STATUSES'), source: { bff: BFF_PRACTICE_ENTRY, where: 'services/bff/src/practice/service.ts' } },
  { name: '再练作答后状态', values: () => web(P, 'AFTER_STATUSES'), source: { bff: BFF_PRACTICE_AFTER, where: 'services/bff/src/practice/service.ts' } },
  { name: '再练条目来源', values: () => web(P, 'SOURCES'), source: { bff: BFF_PRACTICE_SOURCES, where: 'services/bff/src/practice/service.ts' } },
  { name: '认知层次', values: () => web(P, 'LEVELS'), source: { db: ['kge', 'knowledge_node', 'cognitive_levels'] } },
  { name: '判分结论', values: () => web(P, 'VERDICTS'), source: { db: ['platform', 'grade_result', 'verdict'] } },
  // ---- 复测与验证题
  { name: '复测任务种类', values: () => web(R, 'KINDS'), source: { bff: BFF_RETEST_KINDS, where: 'services/bff/src/retest/service.ts' } },
  { name: '复测条目状态', values: () => web(R, 'ENTRY_STATUSES'), source: { bff: BFF_RETEST_ENTRY, where: 'services/bff/src/retest/service.ts' } },
  { name: '复测作答后状态', values: () => web(R, 'AFTER_STATUSES'), source: { bff: BFF_RETEST_AFTER, where: 'services/bff/src/retest/service.ts' } },
  // ---- BFF 转写的常量与它的出处
  { name: 'BFF 异议类型', values: fixed(BFF_DISPUTE_KINDS), source: { contract: [CONTRACT, 'i_know = 我其实会'] } },
  { name: 'BFF 异议的下一步', values: fixed(BFF_NEXT_STEPS), source: { contract: [CONTRACT, 'verification_scheduled：已安排验证题'] } },
  { name: 'BFF 偏差方向', values: fixed(BFF_DIRECTIONS), source: { contract: [CONTRACT, '偏差分析固定检查的四个方向'] } },
  { name: 'BFF 对账调整', values: fixed(BFF_ADJUSTMENTS), source: { contract: [CONTRACT, '平台据此改了什么（编码）'] } },
  { name: 'BFF 判分结论', values: fixed(BFF_VERDICTS), source: { db: ['platform', 'grade_result', 'verdict'] } },
];

export function describe(src: Source): string {
  if ('db' in src) return `库 ${src.db.join('.')}`;
  if ('dbFn' in src) return `库函数 ${src.dbFn[0]}`;
  if ('contract' in src) return `契约 ${src.contract[0]}（锚点“${src.contract[1]}”）`;
  return `BFF ${src.where}`;
}

/** 比较前端（或 BFF）的取值与出处：相同，或登记了原因的子集；返回不一致的说明（空数组 = 一致） */
export function compare(row: Pick<Row, 'name' | 'subset'>, values: readonly string[], origin: readonly string[], where: string): string[] {
  const v = new Set(values);
  const o = new Set(origin);
  const extra = [...v].filter((x) => !o.has(x)).sort();
  const missing = [...o].filter((x) => !v.has(x)).sort();
  const out: string[] = [];
  if (v.size !== values.length) out.push(`${row.name}：有重复取值`);
  if (extra.length) out.push(`${row.name}：${where} 没有 ${extra.join('、')}（前端 / BFF 认得出处不给的值，说明过期）`);
  if (missing.length && row.subset === undefined) {
    out.push(`${row.name}：${where} 多了 ${missing.join('、')}（出处会给、白名单不认：页面会静默降级；有意不显示就登记 subset 与原因）`);
  }
  if (!missing.length && row.subset !== undefined) out.push(`${row.name}：登记为子集（${row.subset}），但实际与 ${where} 相同，去掉 subset`);
  return out;
}

const skip = HAS_DB ? false : '没有测试库（PGHOST），跳过（make bff-test 会起一次性库）';
let su: pg.Pool;
before(() => { if (HAS_DB) su = new pg.Pool({ user: 'postgres', max: 2 }); });
after(async () => { if (HAS_DB) await su.end(); });

test('前端白名单与出处一致（库约束、库函数、契约枚举、BFF 常量；有意更窄的登记原因）', { skip }, async () => {
  const problems: string[] = [];
  for (const row of ROWS) {
    const s = row.source;
    const origin = 'db' in s ? await dbValues(su, ...s.db)
      : 'dbFn' in s ? await dbFnValues(su, ...s.dbFn)
        : 'contract' in s ? contractEnum(...s.contract)
          : s.bff;
    problems.push(...compare(row, await row.values(), origin, describe(s)));
  }
  assert.deepEqual(problems, [], `前端白名单与出处有 ${problems.length} 处不一致：\n${problems.join('\n')}`);
});

test('比较口径：多值、少值、登记了子集却相同、重复', () => {
  assert.deepEqual(compare({ name: 'x' }, ['a', 'b'], ['a', 'b'], 'o'), []);
  assert.match(compare({ name: 'x' }, ['a', 'b', 'c'], ['a', 'b'], 'o').join(), /没有 c/);
  assert.match(compare({ name: 'x' }, ['a'], ['a', 'b'], 'o').join(), /多了 b/);
  assert.deepEqual(compare({ name: 'x', subset: '有意' }, ['a'], ['a', 'b'], 'o'), []);
  assert.match(compare({ name: 'x', subset: '有意' }, ['a', 'b'], ['a', 'b'], 'o').join(), /去掉 subset/);
  assert.match(compare({ name: 'x' }, ['a', 'a'], ['a'], 'o').join(), /重复/);
});

test('契约锚点：唯一、10 行内取第一个 enum（mustContain 过滤）', () => {
  const text = ['a:', '  anchor-1', '  status: { enum: [x, y] }', '  items: { enum: [p, q] }', 'b:'].join('\n');
  assert.deepEqual(contractEnum('f', 'anchor-1', undefined, text), ['x', 'y']);
  assert.deepEqual(contractEnum('f', 'anchor-1', 'items:', text), ['p', 'q']);
  assert.throws(() => contractEnum('f', 'a', undefined, text), /恰好出现一次/);
});
