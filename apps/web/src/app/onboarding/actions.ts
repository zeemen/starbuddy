'use server';
// 建档的写操作（server action）：身份只来自真实会话（4-H H2），表单里的 CSRF 令牌必须等于 CSRF cookie，再连同 Origin 经 BFF 写入；
// 每一步都能提交、跳过，写完跳到下一步。
// 表单只收步骤定义里的选项（valueFor），库里再按字段形状校验一次。出错只带一个固定的提示标记回到本步，不回显输入。
import { notFound, redirect } from 'next/navigation';
import type { BffSession } from '../../lib/bff.ts';
import { EXAM_TYPES, getOnboarding, postExam, postProgress, putAnswer, putTextbook, RANK_BANDS, STEPS, SUBJECT_OPTIONS,
  TEXTBOOK_OPTIONS, TEXTBOOK_SUBJECTS, valueFor } from '../../lib/onboarding.ts';
import { actionSession } from '../../lib/session.ts';
import { isUuid } from '../../lib/star-map.ts';

const text = (f: FormData, k: string): string => (typeof f.get(k) === 'string' ? (f.get(k) as string) : '');

async function who(f: FormData, student: string): Promise<{ account: BffSession; role: 'self' | 'guardian' }> {
  const account = await actionSession(f.get('csrf'));
  if (!account) redirect('/login');
  if (!isUuid(student)) notFound();
  const v = await getOnboarding(account, student);
  if (!v.ok) notFound();
  return { account, role: v.value.role };
}

export async function startAction(f: FormData): Promise<void> {
  const student = text(f, 'student');
  const { account } = await who(f, student);
  await postProgress(account, student, 'onboarding', 'started');
  redirect(`/onboarding/${student}?step=1`);
}

export async function stepAction(f: FormData): Promise<void> {
  const student = text(f, 'student');
  const { account, role } = await who(f, student);
  const steps = STEPS[role];
  const n = Number(text(f, 'step'));
  const step = Number.isInteger(n) && n >= 1 && n <= steps.length ? steps[n - 1]! : null;
  if (!step) redirect(`/onboarding/${student}`);
  const here = `/onboarding/${student}?step=${n}`;
  if (text(f, 'op') === 'skip') {
    await postProgress(account, student, step.key, 'skipped');
  } else {
    let ok = true;
    if (step.kind === 'textbooks') {
      for (const s of TEXTBOOK_SUBJECTS) {
        const v = text(f, `textbook_${s.code}`);
        if (!v) continue;
        if (!TEXTBOOK_OPTIONS.some((o) => o.code === v)) { ok = false; break; }
        ok = (await putTextbook(account, student, s.code, v === 'unknown' ? null : v)).ok && ok;
      }
    } else if (step.kind === 'exam') {
      const subject = text(f, 'subject');
      const examType = text(f, 'exam_type');
      const rank = text(f, 'rank_band');
      const exam = {
        subject, exam_type: examType, exam_date: text(f, 'exam_date'),
        full_score: Number(text(f, 'full_score')), raw_score: Number(text(f, 'raw_score')),
        rank_band: role === 'guardian' && RANK_BANDS.some((o) => o.code === rank) ? rank : null,
      };
      ok = SUBJECT_OPTIONS.some((o) => o.code === subject) && EXAM_TYPES.some((o) => o.code === examType)
        && Number.isFinite(exam.full_score) && Number.isFinite(exam.raw_score)
        && (await postExam(account, student, exam)).ok;
    } else {
      const value = valueFor(step, f);
      ok = value !== null && (await putAnswer(account, student, step.field, value)).ok;
    }
    if (!ok) redirect(`${here}&error=1`);
    await postProgress(account, student, step.key, 'completed');
  }
  if (n === steps.length) {
    await postProgress(account, student, 'onboarding', 'completed');
    redirect(`/onboarding/${student}?step=done`);
  }
  redirect(`/onboarding/${student}?step=${n + 1}`);
}
