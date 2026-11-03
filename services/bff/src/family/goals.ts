// 家长诉求（specs/family-goals-monthly-plan/spec.md 第 1 步，PLT-14；迁移 0053）：BFF 只做请求体校验与禁用推断词表过滤，
// 授权与可见范围全在库里——insight.guardian_goal_view / insight.submit_guardian_goal（属主 authz，按 guardian_student_link 核对当前账号）。
// 学生会话由路由层在任何查询之前回 404；无权、不存在、已删除的档案同一个 404。不计量（诉求收集不是消耗类请求）。
// 本模块不产生任何关于学生的陈述（规则 11）：只原样转交家长自己写的诉求。
import type { Pool, PoolClient } from 'pg';
import { withAccount } from '../metering/db.ts';
import { isUuid } from '../star-map/query.ts';
import { forbiddenHits, lexicon, type Lexicon } from '../trust/lexicon.ts';

/** 平台学科码（platform.subject）；是否在读由库按当时的选科核对 */
export const GOAL_SUBJECTS = ['wenli', 'aya', 'zhixing', 'echo'] as const;
export const MAX_FOCUS_SUBJECTS = 2;
export const MAX_NOTE_CHARS = 200;
const MAX_FULL_SCORE = 300;

/** Unicode Cc、Cf、Zl、Zp（与库里 policy.has_hidden_chars 同一口径：换行、零宽、双向控制、软连字符、BOM 等） */
export const HIDDEN_CHARS = /[\p{Cc}\p{Cf}\p{Zl}\p{Zp}]/u;

export interface TargetScore { subject: string; full_score: number; target_score: number }
export interface GoalInput { focus_subjects: string[]; target_scores: TargetScore[]; school_id: string | null; note: string | null }

export type Parsed = { kind: 'ok'; value: GoalInput } | { kind: 'invalid' } | { kind: 'forbidden_text' };

const isSubject = (v: unknown): v is string => typeof v === 'string' && (GOAL_SUBJECTS as readonly string[]).includes(v);
const oneDecimal = (n: number): boolean => Number.isFinite(n) && Math.abs(Math.round(n * 10) - n * 10) < 1e-9;
const onlyKeys = (o: Record<string, unknown>, keys: readonly string[]): boolean =>
  Object.keys(o).every((k) => keys.includes(k)) && keys.every((k) => k in o);

/** 补充说明：去掉首尾空白，空串当作不填；1–200 字（按码位计，与库里 char_length 一致）、不含隐形字符 */
export function normalizeNote(v: unknown): string | null | undefined {
  if (v === null || v === undefined) return null;
  if (typeof v !== 'string') return undefined;
  const s = v.replace(/^[ 　]+|[ 　]+$/gu, '');
  if (s === '') return null;
  if (HIDDEN_CHARS.test(s) || [...s].length > MAX_NOTE_CHARS) return undefined;
  return s;
}

/**
 * 请求体校验：只收四个键（未知键 422，不静默忽略）。补充说明命中禁用推断词表时 forbidden_text（422，不存）。
 * 词表读不到时抛错（lexicon() fail closed），调用方回 503，不存。
 */
export function parseGoalInput(body: Record<string, unknown> | null, loadLexicon: () => Lexicon = lexicon): Parsed {
  if (body === null || !onlyKeys(body, ['focus_subjects', 'target_scores', 'school_id', 'note'])) return { kind: 'invalid' };
  const fs = body['focus_subjects'];
  if (!Array.isArray(fs) || fs.length > MAX_FOCUS_SUBJECTS || !fs.every(isSubject) || new Set(fs).size !== fs.length) return { kind: 'invalid' };
  const ts = body['target_scores'];
  if (!Array.isArray(ts) || ts.length > GOAL_SUBJECTS.length) return { kind: 'invalid' };
  const targets: TargetScore[] = [];
  for (const t of ts) {
    if (t === null || typeof t !== 'object' || Array.isArray(t)) return { kind: 'invalid' };
    const o = t as Record<string, unknown>;
    if (!onlyKeys(o, ['subject', 'full_score', 'target_score'])) return { kind: 'invalid' };
    const { subject, full_score: full, target_score: target } = o;
    if (!isSubject(subject) || typeof full !== 'number' || typeof target !== 'number') return { kind: 'invalid' };
    if (!oneDecimal(full) || !oneDecimal(target) || full <= 0 || full > MAX_FULL_SCORE || target < 0 || target > full) return { kind: 'invalid' };
    targets.push({ subject, full_score: full, target_score: target });
  }
  if (new Set(targets.map((t) => t.subject)).size !== targets.length) return { kind: 'invalid' };
  const school = body['school_id'];
  if (!(school === null || (typeof school === 'string' && isUuid(school)))) return { kind: 'invalid' };
  const note = normalizeNote(body['note']);
  if (note === undefined) return { kind: 'invalid' };
  // 只有带补充说明时才读词表（没有说明的请求不依赖词表文件；有说明而词表读不到时抛错，调用方 503）
  if (note !== null && forbiddenHits(note, loadLexicon()).length > 0) return { kind: 'forbidden_text' };
  return { kind: 'ok', value: { focus_subjects: fs as string[], target_scores: targets, school_id: school as string | null, note } };
}

export type Outcome<T> = { kind: 'ok'; value: T } | { kind: 'not_found' } | { kind: 'invalid' } | { kind: 'conflict' }
  | { kind: 'too_many' } | { kind: 'school_not_open' } | { kind: 'grade_out_of_range' };

export interface GoalView {
  grade: number | null;
  can_set_school: boolean;
  enrolled_subjects: string[];
  school_options: { id: string; public_name: string }[];
  current: Record<string, unknown> | null;
  history: Record<string, unknown>[];
}

// 42501：无权（与不存在同样处理）；22023 / 23514 / 22P02 / 23502 / 22003：取值不合要求
const INVALID_CODES = new Set(['22023', '23514', '22P02', '23502', '22003']);

async function run<T>(pool: Pool, account: string, student: string, fn: (c: PoolClient) => Promise<T>): Promise<Outcome<T>> {
  if (!isUuid(student) || !isUuid(account)) return { kind: 'not_found' };
  try {
    return { kind: 'ok', value: await withAccount(pool, account, fn) };
  } catch (e) {
    const code = (e as { code?: string }).code ?? '';
    const message = (e as { message?: string }).message ?? '';
    if (code === '42501') return { kind: 'not_found' };
    if (INVALID_CODES.has(code)) return { kind: 'invalid' };
    // 55000：只有初三可设学校；当前年级不在 7–9（授权通过之后才会走到，不泄露别家学生的年级）
    if (code === '55000' && message === 'SCHOOL_NOT_OPEN_FOR_GRADE') return { kind: 'school_not_open' };
    if (code === '55000' && message === 'GRADE_OUT_OF_RANGE') return { kind: 'grade_out_of_range' };
    if (code === '23505') return { kind: 'conflict' };
    if (code === '54000') return { kind: 'too_many' };
    throw e;
  }
}

export async function getGoals(pool: Pool, account: string, student: string): Promise<Outcome<GoalView>> {
  const r = await run(pool, account, student, async (c) => {
    await c.query('SET TRANSACTION READ ONLY');
    const { rows } = await c.query<{ v: GoalView | null }>('SELECT insight.guardian_goal_view($1) AS v', [student]);
    return rows[0]?.v ?? null;
  });
  if (r.kind !== 'ok') return r;
  return r.value === null ? { kind: 'not_found' } : { kind: 'ok', value: r.value };
}

export async function submitGoals(pool: Pool, account: string, student: string, g: GoalInput): Promise<Outcome<{ version: number }>> {
  const r = await run(pool, account, student, async (c) => {
    const { rows } = await c.query<{ v: { version: number } }>(
      'SELECT insight.submit_guardian_goal($1, $2::platform.subject[], $3::jsonb, $4::uuid, $5) AS v',
      [student, g.focus_subjects, JSON.stringify(g.target_scores), g.school_id, g.note]);
    return { version: rows[0]!.v.version };
  });
  return r;
}
