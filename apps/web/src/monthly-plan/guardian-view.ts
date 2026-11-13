// 家长端月度计划的返回值白名单（只给家长端页面用；学生端不引用本文件，tests/monthly-plan.test.ts 静态核对）：
// 学习计划（./student-view.ts 同一套挑法）、家长注意事项、上月回顾、分差区间（只有 5 分一档的区间，没有成绩与目标分）。
// 没有权益时 entitled = false、plan = null（页面显示升级说明占位）。
import { isDay, isMonth, isSubject, obj, pickClaimLine, pickStudentPlan, type ClaimLine, type StudentPlan } from './student-view.ts';

export const EXAM_TYPES: readonly string[] = ['monthly', 'midterm', 'final', 'mock1', 'mock2', 'other'];
const text = (v: unknown): v is string => typeof v === 'string' && v.trim() !== '' && v.length <= 400;
const int = (v: unknown, lo: number, hi: number): v is number => typeof v === 'number' && Number.isInteger(v) && v >= lo && v <= hi;
const list = (v: unknown, max: number): unknown[] => (Array.isArray(v) ? v.slice(0, max) : []);

export type Band = { reached: true } | { reached: false; low: number; high: number };
export interface ScoreGap { claimId: string; subject: string; examType: string; examDate: string; band: Band; text: string }
export interface GuardianPlan {
  generatedDay: string;
  student: StudentPlan;
  notes: string[];
  lastMonth: { month: string; plannedNodes: number; text: string; lit: ClaimLine[] } | null;
  gaps: ScoreGap[];
}
export interface GuardianPlanView { month: string; entitled: boolean; plan: GuardianPlan | null }

function band(v: unknown): Band | null {
  if (!obj(v)) return null;
  if (v['reached'] === true) return { reached: true };
  if (v['reached'] === false && int(v['low'], 0, 1000) && int(v['high'], 5, 1005) && v['high'] === v['low'] + 5) return { reached: false, low: v['low'], high: v['high'] };
  return null;
}

function pickGap(v: unknown): ScoreGap | null {
  const line = pickClaimLine(v);
  if (!line || !obj(v) || typeof v['exam_type'] !== 'string' || !EXAM_TYPES.includes(v['exam_type']) || !isDay(v['exam_date'])) return null;
  const b = band(v['gap_band']);
  return b ? { ...line, examType: v['exam_type'], examDate: v['exam_date'], band: b } : null;
}

/** 生成时间（ISO）→ 上海时区的日期 */
const shanghaiDay = (iso: unknown): string => {
  const t = typeof iso === 'string' ? Date.parse(iso) : Number.NaN;
  return Number.isFinite(t) ? new Date(t + 8 * 3600_000).toISOString().slice(0, 10) : '';
};

export function pickGuardianPlan(v: unknown): GuardianPlanView | null {
  if (!obj(v) || !isMonth(v['month']) || typeof v['entitled'] !== 'boolean') return null;
  if (!v['entitled'] || v['plan'] === null) return { month: v['month'], entitled: v['entitled'] && v['plan'] !== null, plan: null };
  const p = v['plan'];
  if (!obj(p)) return null;
  const student = pickStudentPlan(p['student_plan']);
  const review = obj(p['review']) ? p['review'] : {};
  const lm = review['last_month'];
  if (!student) return null;
  return {
    month: v['month'], entitled: true,
    plan: {
      generatedDay: shanghaiDay(p['generated_at']),
      student,
      notes: list(p['guardian_notes'], 3).filter(obj).map((n) => n['text']).filter(text),
      lastMonth: obj(lm) && isMonth(lm['month']) && int(lm['planned_nodes'], 0, 20) && text(lm['text'])
        ? { month: lm['month'], plannedNodes: lm['planned_nodes'], text: lm['text'],
            lit: list(lm['lit'], 20).map(pickClaimLine).filter((x): x is ClaimLine => x !== null) }
        : null,
      gaps: list(review['score_gaps'], 16).map(pickGap).filter((x): x is ScoreGap => x !== null && isSubject(x.subject)),
    },
  };
}
