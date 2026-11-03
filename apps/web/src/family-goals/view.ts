// BFF 返回的诉求视图 → 页面用的形状：按白名单挑字段（不认识的字段一律丢掉，免得以后 BFF 多给的东西直接进 DOM）。
import { GOAL_SUBJECTS, type GoalSubject } from './form.ts';

export interface TargetView { subject: GoalSubject; full_score: number; target_score: number }
export interface GoalsView {
  canSetSchool: boolean;
  enrolled: GoalSubject[];
  schools: { id: string; name: string }[];
  current: { version: number; focus: GoalSubject[]; targets: TargetView[]; schoolId: string | null; note: string | null } | null;
  history: { version: number; createdAt: string }[];
}

const isSubject = (v: unknown): v is GoalSubject => typeof v === 'string' && (GOAL_SUBJECTS as readonly string[]).includes(v);
const obj = (v: unknown): Record<string, unknown> | null => (v && typeof v === 'object' && !Array.isArray(v) ? v as Record<string, unknown> : null);
const arr = (v: unknown): unknown[] => (Array.isArray(v) ? v : []);
const num = (v: unknown): number | null => (typeof v === 'number' && Number.isFinite(v) ? v : null);
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;

function targets(v: unknown): TargetView[] {
  return arr(v).flatMap((t) => {
    const o = obj(t);
    const full = num(o?.['full_score']);
    const target = num(o?.['target_score']);
    return o && isSubject(o['subject']) && full !== null && target !== null ? [{ subject: o['subject'], full_score: full, target_score: target }] : [];
  });
}

export function pickGoalsView(raw: unknown): GoalsView | null {
  const o = obj(raw);
  if (!o) return null;
  const cur = obj(o['current']);
  const version = num(cur?.['version']);
  const school = obj(cur?.['school']);
  return {
    canSetSchool: o['can_set_school'] === true,
    enrolled: arr(o['enrolled_subjects']).filter(isSubject),
    schools: arr(o['school_options']).flatMap((s) => {
      const x = obj(s);
      return x && typeof x['id'] === 'string' && UUID.test(x['id']) && typeof x['public_name'] === 'string' ? [{ id: x['id'], name: x['public_name'] }] : [];
    }),
    current: cur && version !== null ? {
      version,
      focus: arr(cur['focus_subjects']).filter(isSubject),
      targets: targets(cur['target_scores']),
      schoolId: school && typeof school['id'] === 'string' && UUID.test(school['id']) ? school['id'] : null,
      note: typeof cur['note'] === 'string' ? cur['note'] : null,
    } : null,
    history: arr(o['history']).flatMap((h) => {
      const x = obj(h);
      const v = num(x?.['version']);
      return x && v !== null && typeof x['created_at'] === 'string' ? [{ version: v, createdAt: x['created_at'] }] : [];
    }),
  };
}
