// 建档（SIE-01，specs/sie-01-onboarding/spec.md 设计要点 3）：步骤定义与 BFF 客户端（只在服务端调用）。
// 学生版与家长版分开；每页一题，都能跳过、返回。选项编码与库里 insight.valid_report_value 一致；文案是占位，待教研定稿。
// 前端不判断身份与授权：BFF 经库函数按当前账号核对，并按身份裁剪返回内容（互不可见）。
import { bffBaseUrl, isUuid } from './star-map.ts';

export type Role = 'self' | 'guardian';

export interface Option { code: string; label: string }

export type Step =
  | { key: string; kind: 'single'; field: string; prop: string; title: string; options: readonly Option[]; numeric?: boolean }
  | { key: string; kind: 'multi'; field: string; prop: string; title: string; options: readonly Option[]; min: number; note?: boolean }
  | { key: string; kind: 'text'; field: string; prop: string; title: string }
  | { key: string; kind: 'textbooks'; title: string }
  | { key: string; kind: 'exam'; title: string };

export const SUBJECT_OPTIONS: readonly Option[] = [
  { code: 'aya', label: '数学' }, { code: 'wenli', label: '语文' }, { code: 'zhixing', label: '道法' }, { code: 'echo', label: '英语' },
];
const MINUTES: readonly Option[] = [60, 120, 180, 240, 300, 420, 600].map((m) => ({ code: String(m), label: `每周约 ${m / 60} 小时` }));
export const TEXTBOOK_OPTIONS: readonly Option[] = [
  { code: 'bubian-2024', label: '部编版（2024）' }, { code: 'suke-2024', label: '苏科版（2024）' }, { code: 'unknown', label: '不清楚' },
];
export const TEXTBOOK_SUBJECTS: readonly Option[] = SUBJECT_OPTIONS.filter((s) => s.code !== 'echo');
export const EXAM_TYPES: readonly Option[] = [
  { code: 'monthly', label: '月考' }, { code: 'midterm', label: '期中' }, { code: 'final', label: '期末' },
  { code: 'mock1', label: '一模' }, { code: 'mock2', label: '二模' }, { code: 'other', label: '其他' },
];
export const RANK_BANDS: readonly Option[] = [
  { code: 'top10', label: '前 10%' }, { code: 'top30', label: '前 30%' }, { code: 'middle', label: '中间' },
  { code: 'bottom30', label: '后 30%' }, { code: 'unknown', label: '不清楚' },
];

export const STEPS: Record<Role, readonly Step[]> = {
  self: [
    { key: 'focus_subject', kind: 'single', field: 'through.g9.focus_subject', prop: 'subject', title: '你最想先提高哪一科？', options: SUBJECT_OPTIONS },
    { key: 'goal', kind: 'text', field: 'through.g9.near_term_goal', prop: 'text', title: '最近想达到的一个目标是什么？（一句话）' },
    { key: 'time', kind: 'single', field: 'outer.g1.weekly_available_time', prop: 'minutes', title: '每周大约能拿出多少时间在这里学习？', options: MINUTES, numeric: true },
    { key: 'mode', kind: 'single', field: 'middle.g6.learning_mode_preference', prop: 'mode', title: '你更喜欢怎么学？', options: [
      { code: 'step_by_step', label: '一步步引导' }, { code: 'explain_then_practice', label: '先讲再练' },
      { code: 'independent', label: '多自己试' }, { code: 'challenge', label: '闯关' }] },
    { key: 'pain_points', kind: 'multi', field: 'middle.g6.self_pain_points', prop: 'points', title: '做题时最常卡在哪里？（可多选，也可以跳过）', min: 0, note: true, options: [
      { code: 'concept', label: '概念没弄懂' }, { code: 'decompose', label: '不会拆题' }, { code: 'read_question', label: '审题出错' },
      { code: 'time', label: '时间不够' }, { code: 'writing', label: '不会写过程' }] },
    { key: 'textbooks', kind: 'textbooks', title: '各科用的是哪一版教材？' },
    { key: 'exam', kind: 'exam', title: '最近一次大考的成绩（可以跳过）' },
  ],
  guardian: [
    { key: 'time', kind: 'single', field: 'outer.g1.weekly_available_time', prop: 'minutes', title: '您觉得孩子每周大约能拿出多少时间在这里学习？', options: MINUTES, numeric: true },
    { key: 'devices', kind: 'multi', field: 'outer.g1.devices', prop: 'devices', title: '孩子学习时能用哪些设备？', min: 1, options: [
      { code: 'phone', label: '手机' }, { code: 'tablet', label: '平板' }, { code: 'computer', label: '电脑' }, { code: 'none', label: '都没有' }] },
    { key: 'tutoring', kind: 'multi', field: 'outer.g1.tutoring', prop: 'subjects', title: '孩子在校外补习哪些科目？（自愿填写，没有就直接提交）', min: 0, options: [
      ...SUBJECT_OPTIONS, { code: 'other', label: '其他' }] },
    { key: 'cooperation', kind: 'single', field: 'outer.g8.cooperation_capacity', prop: 'level', title: '您平时能配合孩子学习的程度？', options: [
      { code: 'none', label: '基本没时间' }, { code: 'occasional', label: '偶尔' }, { code: 'regular', label: '经常' }] },
    { key: 'textbooks', kind: 'textbooks', title: '孩子各科用的是哪一版教材？' },
    { key: 'exam', kind: 'exam', title: '孩子最近一次大考的成绩（可以跳过）' },
  ],
};

export interface OnboardingView {
  role: Role;
  reports: Record<string, Record<string, unknown>>;
  exams: Record<string, unknown>[];
  textbooks: Record<string, string | null>;
  progress: { step: string; status: string }[];
}

type Result<T> = { ok: true; value: T } | { ok: false; status: number };

async function call<T>(account: string, method: string, student: string, sub: string, body?: unknown): Promise<Result<T>> {
  if (!isUuid(student)) return { ok: false, status: 404 };
  const r = await fetch(`${bffBaseUrl()}/bff/v0/students/${student}/onboarding${sub}`, {
    method,
    headers: { 'x-dev-account-id': account, ...(body === undefined ? {} : { 'content-type': 'application/json' }) },
    body: body === undefined ? undefined : JSON.stringify(body),
    cache: 'no-store',
    signal: AbortSignal.timeout(10_000),
  });
  if (!r.ok) return { ok: false, status: r.status };
  return { ok: true, value: (await r.json()) as T };
}

export const getOnboarding = (account: string, student: string) => call<OnboardingView>(account, 'GET', student, '');
export const putAnswer = (account: string, student: string, field: string, value: unknown) =>
  call<unknown>(account, 'PUT', student, '/answers', { field_id: field, value });
export const putTextbook = (account: string, student: string, subject: string, version: string | null) =>
  call<unknown>(account, 'PUT', student, '/textbooks', { subject, textbook_version: version });
export const postExam = (account: string, student: string, exam: Record<string, unknown>) => call<unknown>(account, 'POST', student, '/exams', exam);
export const postProgress = (account: string, student: string, step: string, status: 'started' | 'completed' | 'skipped') =>
  call<unknown>(account, 'POST', student, '/progress', { step, status });

/** 按步骤把表单转成库里要的取值形状（选项之外的输入一律丢掉，由库再校验一次） */
export function valueFor(step: Step, form: FormData): Record<string, unknown> | null {
  const one = (k: string) => (typeof form.get(k) === 'string' ? (form.get(k) as string) : '');
  if (step.kind === 'single') {
    const v = one('choice');
    if (!step.options.some((o) => o.code === v)) return null;
    return { [step.prop]: step.numeric ? Number(v) : v };
  }
  if (step.kind === 'multi') {
    const picked = form.getAll('choice').filter((v): v is string => typeof v === 'string' && step.options.some((o) => o.code === v));
    if (picked.length < step.min) return null;
    const out: Record<string, unknown> = { [step.prop]: [...new Set(picked)] };
    const note = one('note').trim();
    if (step.note && note) out['note'] = note.slice(0, 200);
    return out;
  }
  if (step.kind === 'text') {
    const t = one('text').trim();
    return t ? { [step.prop]: t.slice(0, 200) } : null;
  }
  return null;
}
