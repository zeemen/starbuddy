// 讲解 DSL 的版本规则与迁移阶梯（specs/stage-dsl-versioning；借鉴 OpenMAIC packages/@openmaic/dsl/src/version.ts 的思路，未引入其代码）。
//   - DSL_VERSION 只描述序列化格式（字段、动作类型、枚举、上限、id 规则、公式命令表），与包版本无关；任何格式变化都升 minor 并追加一级迁移；
//   - 格式指纹：formatSpec() 把格式常量按固定顺序序列化；tests/version.test.ts 用它的 sha256 对照 schema/versions.lock.json，
//     “改了格式却没升版本”“改了已发布版本的 schema”都会让 make stage-test 失败（指纹只在测试里算哈希，本文件不依赖 node:crypto，浏览器可用）；
//   - 迁移阶梯 DSL_MIGRATIONS：有序、首尾相接、不跳级；每级是纯函数、幂等，只改结构、不改任何文字与公式；
//   - migrateStageDsl：沿阶梯升到当前版本后再过一遍完整校验；比当前更新的版本、阶梯上找不到的版本、迁移失败一律拒收（不降级、不猜测）。
// 第一级真实迁移 0.1.0 → 0.2.0（specs/stage-dsl-v0.2）：补 narrated: false（无声板书，与 v0.1 相同），其余不动；机制另用测试里注入的合成阶梯验证。
// 第二级 0.2.0 → 0.3.0（specs/stage-point-to-ask-answer PR ②）：DSL 结构不变，只升版本号；格式变化在台词稿（可选的预制答疑 answers）。
// 台词稿另有自己的读入入口 migrateStageScript：0.2.0 的稿没有 answers，升到 0.3.0 只改版本号；台词文字逐字不变（执行器核对）。
import {
  ACTION_FIELDS, ACTION_TYPES, BOARD_WRITE_TYPES, CANVAS_FIELDS, DSL_VERSION, FONT_SIZE, ID_RE, LIMITS, POINT_MODES, ROLES, SLOT_FIELDS, SLOT_KINDS,
  STEP_FIELDS, STEP_OPTIONAL, TOP_FIELDS, TOP_OPTIONAL,
} from './dsl.ts';
import { ANSWER_FIELDS, ANSWER_INTENTS, LINE_FIELDS, SCRIPT_FIELDS, SCRIPT_LIMITS, SCRIPT_OPTIONAL, validateStagePair, type PairResult } from './script.ts';
import { LATEX_COMMANDS, LATEX_RE } from './latex.ts';
import { validateStageDsl, type DslError, type ValidatedStageDsl } from './validate.ts';

type Json = Record<string, unknown>;

/**
 * 一级迁移：把 from 版本的文档改写成 to 版本的结构（纯函数、幂等；不改文字；dsl_version 由执行器盖章）。
 * textKeys：from 版本里装文字与公式的字段名（缺省 TEXT_KEYS）；字段改名的迁移要写明旧名，执行器据此核对每一级迁移前后文字逐字相同。
 */
export interface DslMigration { from: string; to: string; migrate(doc: Json): Json; textKeys?: readonly string[] }

/** 当前版本里装文字与公式的字段：标题、write_text.text、write_formula.latex */
export const TEXT_KEYS: readonly string[] = Object.freeze(['title', 'text', 'latex']);

/** 真实的迁移阶梯（按版本从旧到新）。改格式时追加一级，并在 versions.lock.json 追加该版本 */
export const DSL_MIGRATIONS: readonly DslMigration[] = Object.freeze([
  // 0.1.0 → 0.2.0：v0.1 没有台词，迁移过来是无声板书；步骤、动作、文字都不动
  Object.freeze({ from: '0.1.0', to: '0.2.0', migrate: (d: Json): Json => ({ ...d, narrated: false }) }),
  // 0.2.0 → 0.3.0：DSL 结构不变（预制答疑在台词稿里），只升版本号
  Object.freeze({ from: '0.2.0', to: '0.3.0', migrate: (d: Json): Json => ({ ...d }) }),
]);

/** 台词稿的迁移阶梯：台词稿从 0.2.0 起才有（v0.1 没有台词）。改台词稿格式时与 DSL 同步升版本，在这里追加一级 */
export const SCRIPT_MIGRATIONS: readonly DslMigration[] = Object.freeze([
  // 0.2.0 → 0.3.0：旧稿没有预制答疑，只升版本号；台词一句不动
  Object.freeze({ from: '0.2.0', to: '0.3.0', migrate: (s: Json): Json => ({ ...s }) }),
]);

const SEMVER = /^(0|[1-9]\d{0,3})\.(0|[1-9]\d{0,3})\.(0|[1-9]\d{0,3})$/;

/** 版本比较：a < b 为负，相等为 0，a > b 为正；不是 x.y.z 时为 null */
export function compareVersions(a: string, b: string): number | null {
  const x = SEMVER.exec(a); const y = SEMVER.exec(b);
  if (!x || !y) return null;
  for (let i = 1; i <= 3; i++) {
    const d = Number(x[i]) - Number(y[i]);
    if (d !== 0) return d;
  }
  return 0;
}

/** 阶梯是否合规：每级 from < to、首尾相接、不重复，非空时最后一级落在 current。返回问题列表（空 = 合规） */
export function ladderProblems(ladder: readonly DslMigration[], current: string = DSL_VERSION): string[] {
  const out: string[] = [];
  for (const [i, m] of ladder.entries()) {
    const c = compareVersions(m.from, m.to);
    if (c === null || c >= 0) out.push(`第 ${i + 1} 级 ${m.from} → ${m.to}：版本号不合规或没有升高`);
    if (i > 0 && ladder[i - 1]!.to !== m.from) out.push(`第 ${i + 1} 级没有接上第 ${i} 级（${ladder[i - 1]!.to} ≠ ${m.from}）`);
  }
  if (ladder.length > 0 && ladder[ladder.length - 1]!.to !== current) out.push(`最后一级没有落在当前版本 ${current}`);
  return out;
}

/** 格式指纹的输入：会决定“校验器收不收一份文档”的格式常量，按固定顺序序列化（键排序） */
export function formatSpec(): string {
  const sortKeys = (v: unknown): unknown => (Array.isArray(v) ? v.map(sortKeys)
    : v && typeof v === 'object' ? Object.fromEntries(Object.keys(v).sort().map((k) => [k, sortKeys((v as Json)[k])])) : v);
  return JSON.stringify(sortKeys({
    top: TOP_FIELDS, topOptional: TOP_OPTIONAL, step: STEP_FIELDS, stepOptional: STEP_OPTIONAL, canvas: CANVAS_FIELDS, actionTypes: ACTION_TYPES,
    actionFields: ACTION_FIELDS, boardWrite: BOARD_WRITE_TYPES, pointModes: POINT_MODES, slotFields: SLOT_FIELDS, slotKinds: SLOT_KINDS, roles: ROLES,
    fontSize: FONT_SIZE, limits: LIMITS, id: `${ID_RE.source}/${ID_RE.flags}`, latexCommands: LATEX_COMMANDS, latexForbidden: `${LATEX_RE.source}/${LATEX_RE.flags}`,
    script: { fields: SCRIPT_FIELDS, optional: SCRIPT_OPTIONAL, line: LINE_FIELDS, answer: ANSWER_FIELDS, answerIntents: ANSWER_INTENTS, limits: SCRIPT_LIMITS },
  }));
}

/** 文档里全部文字与公式（按 keys 指定的字段名，缺省为当前版本的 TEXT_KEYS），按出现顺序；每级迁移前后必须逐字相同 */
export function dslTexts(doc: unknown, keys: readonly string[] = TEXT_KEYS): string[] {
  const out: string[] = [];
  const walk = (v: unknown, key: string | null) => {
    if (typeof v === 'string') { if (key !== null && keys.includes(key)) out.push(v); return; }
    if (Array.isArray(v)) { for (const x of v) walk(x, null); return; }
    if (v && typeof v === 'object') for (const k of Object.keys(v)) walk((v as Json)[k], k);
  };
  walk(doc, null);
  return out;
}

export type MigrationResult =
  | { ok: true; dsl: ValidatedStageDsl; from: string }
  | { ok: false; errors: DslError[]; from: string | null };

const isRecord = (v: unknown): v is Json => typeof v === 'object' && v !== null && !Array.isArray(v);
const clone = (v: unknown): unknown => JSON.parse(JSON.stringify(v));

/**
 * 读入任意版本的讲解 DSL：当前版本直接校验；旧版本沿阶梯逐级升到当前版本、核对文字逐字未变，再完整校验；其余拒收。
 * 输入不会被改动。ladder、current 只给测试注入合成阶梯用；正式调用一律用默认值。
 */
export function migrateStageDsl(raw: unknown, ladder: readonly DslMigration[] = DSL_MIGRATIONS, current: string = DSL_VERSION): MigrationResult {
  const version = isRecord(raw) ? raw['dsl_version'] : undefined;
  // 没有版本号或不是 x.y.z：交给校验器报 missing_field / bad_version（与直接校验同一口径）
  if (typeof version !== 'string' || compareVersions(version, current) === null) {
    const r = validateStageDsl(raw);
    return r.ok ? { ...r, from: current } : { ok: false, errors: r.errors, from: null };
  }
  const cmp = compareVersions(version, current)!;
  if (cmp > 0) return { ok: false, errors: [{ path: '$.dsl_version', code: 'future_version' }], from: version };
  if (cmp === 0) {
    const r = validateStageDsl(raw);
    return r.ok ? { ...r, from: version } : { ok: false, errors: r.errors, from: version };
  }
  // 阶梯本身不合规（断档、跳级、没落在当前版本）时，任何旧文档都不迁移
  if (ladderProblems(ladder, current).length > 0) return { ok: false, errors: [{ path: '$', code: 'migration_failed' }], from: version };
  const start = ladder.findIndex((m) => m.from === version);
  if (start < 0) return { ok: false, errors: [{ path: '$.dsl_version', code: 'unknown_version' }], from: version };
  let doc: unknown;
  try {
    doc = clone(raw);
    const steps = ladder.slice(start);
    for (const [i, m] of steps.entries()) {
      const next = m.migrate(clone(doc) as Json);
      if (!isRecord(next)) throw new Error('迁移结果不是对象');
      const out = { ...clone(next) as Json, dsl_version: m.to };
      // 文字不动：这一级迁移前（按 from 版本的字段名）与迁移后（按 to 版本的字段名）的文字与公式逐字相同、顺序相同
      const before = dslTexts(doc, m.textKeys ?? TEXT_KEYS);
      const after = dslTexts(out, steps[i + 1]?.textKeys ?? TEXT_KEYS);
      if (before.length !== after.length || before.some((t, j) => t !== after[j])) throw new Error('迁移改动了文字');
      doc = out;
    }
  } catch {
    return { ok: false, errors: [{ path: '$', code: 'migration_failed' }], from: version };
  }
  const r = validateStageDsl(doc);
  if (!r.ok) return { ok: false, errors: [{ path: '$', code: 'migration_failed' }, ...r.errors], from: version };
  return { ...r, from: version };
}

export type ScriptMigrationResult = (PairResult & { from: string | null });

/** 台词稿里的全部台词文字，按出现顺序（每级迁移前后必须逐字相同） */
const scriptTexts = (doc: unknown): string[] =>
  isRecord(doc) && Array.isArray(doc['lines']) ? doc['lines'].map((l) => (isRecord(l) && typeof l['text'] === 'string' ? l['text'] : '\u0000')) : [];

/**
 * 读入任意版本的台词稿，并与已校验（已迁移到当前版本）的 DSL 配对：当前版本直接配对核对；旧版本沿 SCRIPT_MIGRATIONS 逐级升级、
 * 核对台词逐字未变，再配对核对；比当前更新、阶梯上找不到、迁移失败一律拒收。输入不会被改动。
 */
export function migrateStageScript(dsl: ValidatedStageDsl, raw: unknown, ladder: readonly DslMigration[] = SCRIPT_MIGRATIONS,
  current: string = DSL_VERSION): ScriptMigrationResult {
  const version = isRecord(raw) ? raw['dsl_version'] : undefined;
  if (typeof version !== 'string' || compareVersions(version, current) === null) return { ...validateStagePair(dsl, raw), from: null };
  const cmp = compareVersions(version, current)!;
  if (cmp > 0) return { ok: false, errors: [{ path: '$.dsl_version', code: 'future_version' }], from: version };
  if (cmp === 0) return { ...validateStagePair(dsl, raw), from: version };
  if (ladderProblems(ladder, current).length > 0) return { ok: false, errors: [{ path: '$', code: 'migration_failed' }], from: version };
  const start = ladder.findIndex((m) => m.from === version);
  if (start < 0) return { ok: false, errors: [{ path: '$.dsl_version', code: 'unknown_version' }], from: version };
  let doc: unknown;
  try {
    doc = clone(raw);
    for (const m of ladder.slice(start)) {
      const next = m.migrate(clone(doc) as Json);
      if (!isRecord(next)) throw new Error('迁移结果不是对象');
      const out = { ...clone(next) as Json, dsl_version: m.to };
      const before = scriptTexts(doc); const after = scriptTexts(out);
      if (before.length !== after.length || before.some((t, j) => t !== after[j])) throw new Error('迁移改动了台词');
      doc = out;
    }
  } catch {
    return { ok: false, errors: [{ path: '$', code: 'migration_failed' }], from: version };
  }
  const r = validateStagePair(dsl, doc);
  return r.ok ? { ...r, from: version } : { ok: false, errors: [{ path: '$', code: 'migration_failed' }, ...r.errors], from: version };
}
