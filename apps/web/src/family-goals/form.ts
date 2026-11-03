// 家长诉求表单 → BFF 请求体（纯函数，单测在 tests/family-goals.test.ts）。只做格式检查，取值规则（在读学科、满分上限、学校可选、
// 禁用推断词表）由 BFF 与数据库判断；这里挡掉明显不合格的输入，免得白跑一趟。

export const GOAL_SUBJECTS = ['wenli', 'aya', 'zhixing', 'echo'] as const;
export type GoalSubject = (typeof GOAL_SUBJECTS)[number];
const isSubject = (v: string): v is GoalSubject => (GOAL_SUBJECTS as readonly string[]).includes(v);
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const SCORE = /^(0|[1-9][0-9]{0,2})(\.[0-9])?$/;

export interface GoalBody {
  focus_subjects: string[];
  target_scores: { subject: string; full_score: number; target_score: number }[];
  school_id: string | null;
  note: string | null;
}

const str = (f: FormData, k: string): string => (typeof f.get(k) === 'string' ? (f.get(k) as string).trim() : '');

/**
 * 表单字段：focus（复选，可多个）、full_<学科> 与 target_<学科>（两个都填才算一科目标分，都空表示不填）、school（空 = 不选）、note。
 * 不合格返回 null（调用方回 INVALID，不发请求）。
 */
export function goalBodyFromForm(f: FormData): GoalBody | null {
  const focus = f.getAll('focus').filter((v): v is string => typeof v === 'string');
  if (focus.length > 2 || !focus.every(isSubject) || new Set(focus).size !== focus.length) return null;
  const targets: GoalBody['target_scores'] = [];
  for (const subject of GOAL_SUBJECTS) {
    const full = str(f, `full_${subject}`);
    const target = str(f, `target_${subject}`);
    if (full === '' && target === '') continue;
    if (!SCORE.test(full) || !SCORE.test(target)) return null;
    const fullN = Number(full);
    const targetN = Number(target);
    if (fullN <= 0 || fullN > 300 || targetN > fullN) return null;
    targets.push({ subject, full_score: fullN, target_score: targetN });
  }
  const school = str(f, 'school');
  if (school !== '' && !UUID.test(school)) return null;
  const note = str(f, 'note');
  if ([...note].length > 200) return null;
  return { focus_subjects: focus, target_scores: targets, school_id: school === '' ? null : school, note: note === '' ? null : note };
}
