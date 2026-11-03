// 学习计划（学生与家长都看得到的一段）的返回值白名单：只留页面要用的字段，不认识的字段一律丢掉；关于学生的句子没有合法 claim_id 不留
// （规则 11：前端以 data-claim-id 渲染）。本文件只有学习计划，学生端只引用它与 ./student-copy.ts（tests/monthly-plan.test.ts 静态核对）。

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const DAY = /^\d{4}-\d{2}-\d{2}$/;
const MONTH = /^\d{4}-\d{2}$/;
export const PLAN_SUBJECTS: readonly string[] = ['aya', 'zhixing', 'wenli'];

type Obj = Record<string, unknown>;
export const obj = (v: unknown): v is Obj => typeof v === 'object' && v !== null && !Array.isArray(v);
const text = (v: unknown): v is string => typeof v === 'string' && v.trim() !== '' && v.length <= 400;
const int = (v: unknown, lo: number, hi: number): v is number => typeof v === 'number' && Number.isInteger(v) && v >= lo && v <= hi;
export const isSubject = (v: unknown): v is string => typeof v === 'string' && PLAN_SUBJECTS.includes(v);
export const isDay = (v: unknown): v is string => typeof v === 'string' && DAY.test(v);
export const isMonth = (v: unknown): v is string => typeof v === 'string' && MONTH.test(v);

export interface ClaimLine { claimId: string; subject: string; text: string }
export interface StudentPlan {
  priority: ClaimLine[];
  rhythm: { sessions: number; minutes: number; text: string } | null;
  retests: { count: number; dates: string[]; text: string } | null;
  miniReviews: { subject: string; date: string; text: string }[];
}

export function pickClaimLine(v: unknown): ClaimLine | null {
  if (!obj(v) || typeof v['claim_id'] !== 'string' || !UUID.test(v['claim_id']) || !isSubject(v['subject']) || !text(v['text'])) return null;
  return { claimId: v['claim_id'], subject: v['subject'], text: v['text'] };
}

const list = (v: unknown, max: number): unknown[] => (Array.isArray(v) ? v.slice(0, max) : []);

export function pickStudentPlan(v: unknown): StudentPlan | null {
  if (!obj(v)) return null;
  const w = v['weekly_rhythm'];
  const r = v['retests'];
  return {
    priority: list(v['priority_nodes'], 20).map(pickClaimLine).filter((x): x is ClaimLine => x !== null),
    rhythm: obj(w) && int(w['sessions_per_week'], 1, 7) && int(w['minutes_per_session'], 1, 120) && text(w['text'])
      ? { sessions: w['sessions_per_week'], minutes: w['minutes_per_session'], text: w['text'] } : null,
    retests: obj(r) && int(r['count'], 0, 999) && text(r['text'])
      ? { count: r['count'], dates: list(r['dates'], 31).filter(isDay), text: r['text'] } : null,
    miniReviews: list(v['mini_reviews'], 8).filter(obj)
      .filter((x) => isSubject(x['subject']) && isDay(x['date']) && text(x['text']))
      .map((x) => ({ subject: x['subject'] as string, date: x['date'] as string, text: x['text'] as string })),
  };
}

/** 学生端 GET my-plan：本月还没有生成时 plan = null（空状态） */
export interface MyPlanView { month: string; plan: StudentPlan | null }
export function pickMyPlan(v: unknown): MyPlanView | null {
  if (!obj(v) || !isMonth(v['month'])) return null;
  if (v['plan'] === null) return { month: v['month'], plan: null };
  if (!obj(v['plan'])) return null;
  const plan = pickStudentPlan(v['plan']['student_plan']);
  return plan ? { month: v['month'], plan } : null;
}
