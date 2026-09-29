// 建档（SIE-01，specs/sie-01-onboarding/spec.md 设计要点 3）：BFF 只转发到库函数，身份用当前账号（withAccount）。
// 授权与“互不可见”全在库里：insight.onboarding_view 按调用者身份裁剪，写入函数核对学生本人 / 已绑定家长。
// 无权与不存在同样返回 not_found（调用方回 404），取值不合要求返回 invalid（422）。本模块不产生任何判断（规则 11）。
import type { Pool } from 'pg';
import { withAccount } from '../metering/db.ts';
import { isUuid } from '../star-map/query.ts';

export type Outcome<T = null> = { kind: 'ok'; value: T } | { kind: 'not_found' } | { kind: 'invalid' };

export interface OnboardingView {
  role: 'self' | 'guardian';
  reports: Record<string, unknown>;
  exams: Record<string, unknown>[];
  textbooks: Record<string, string | null>;
  progress: { step: string; status: string }[];
}

export interface ExamInput {
  subject: string;
  exam_type: string;
  exam_date: string;
  full_score: number;
  raw_score: number;
  rank_band?: string | null;
  scope_note?: string | null;
  difficulty_note?: string | null;
}

// 42501：无权（与不存在同样处理）；22023 / 23514 / 22P02 / 22007 / 22008 / 23502 / 22003：取值不合要求
const INVALID_CODES = new Set(['22023', '23514', '22P02', '22007', '22008', '23502', '22003']);

async function run<T>(pool: Pool, account: string, student: string, fn: (c: import('pg').PoolClient) => Promise<T>): Promise<Outcome<T>> {
  if (!isUuid(student) || !isUuid(account)) return { kind: 'not_found' };
  try {
    return { kind: 'ok', value: await withAccount(pool, account, fn) };
  } catch (e) {
    const code = (e as { code?: string }).code ?? '';
    if (code === '42501') return { kind: 'not_found' };
    if (INVALID_CODES.has(code)) return { kind: 'invalid' };
    throw e;
  }
}

export async function getOnboarding(pool: Pool, account: string, student: string): Promise<Outcome<OnboardingView>> {
  const r = await run(pool, account, student, async (c) => {
    await c.query('SET TRANSACTION READ ONLY');
    const { rows } = await c.query<{ v: OnboardingView | null }>('SELECT insight.onboarding_view($1) AS v', [student]);
    return rows[0]?.v ?? null;
  });
  if (r.kind !== 'ok') return r;
  return r.value === null ? { kind: 'not_found' } : { kind: 'ok', value: r.value };
}

/** 提交一题：按调用者身份写学生自述或家长观察（身份由库判断，BFF 不看请求自报） */
export async function submitAnswer(pool: Pool, account: string, student: string, fieldId: string, value: unknown): Promise<Outcome> {
  if (typeof fieldId !== 'string' || !/^[a-z]+\.g[1-9]\.[a-z0-9_]{2,48}$/.test(fieldId) || value === null || typeof value !== 'object' || Array.isArray(value)) {
    return { kind: 'invalid' };
  }
  const r = await run(pool, account, student, async (c) => {
    const { rows } = await c.query<{ v: OnboardingView | null }>('SELECT insight.onboarding_view($1) AS v', [student]);
    const role = rows[0]?.v?.role;
    if (!role) return false;
    const fn = role === 'self' ? 'insight.submit_self_report' : 'insight.submit_guardian_report';
    await c.query(`SELECT ${fn}($1, $2, $3::jsonb)`, [student, fieldId, JSON.stringify(value)]);
    return true;
  });
  if (r.kind !== 'ok') return r;
  return r.value ? { kind: 'ok', value: null } : { kind: 'not_found' };
}

export async function submitExam(pool: Pool, account: string, student: string, e: ExamInput): Promise<Outcome> {
  const r = await run(pool, account, student, (c) => c.query(
    'SELECT insight.submit_exam_record($1, $2::platform.subject, $3, $4::date, $5, $6, $7, $8, $9)',
    [student, e.subject, e.exam_type, e.exam_date, e.full_score, e.raw_score, e.rank_band ?? null, e.scope_note ?? null, e.difficulty_note ?? null]));
  return r.kind === 'ok' ? { kind: 'ok', value: null } : r;
}

export async function setTextbook(pool: Pool, account: string, student: string, subject: string, version: string | null): Promise<Outcome> {
  const r = await run(pool, account, student, (c) => c.query('SELECT platform.set_subject_textbook($1, $2::platform.subject, $3)',
    [student, subject, version]));
  return r.kind === 'ok' ? { kind: 'ok', value: null } : r;
}

export async function recordStep(pool: Pool, account: string, student: string, step: string, status: string): Promise<Outcome> {
  const r = await run(pool, account, student, (c) => c.query('SELECT insight.record_onboarding_step($1, $2, $3)', [student, step, status]));
  return r.kind === 'ok' ? { kind: 'ok', value: null } : r;
}
