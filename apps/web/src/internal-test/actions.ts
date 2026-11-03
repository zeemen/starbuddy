'use server';
// 内部成员测试运营的写操作（LT-06）：双重提交——表单里的 CSRF 令牌必须等于 CSRF cookie（actionSession），再连同浏览器的 Origin 转交 BFF。
// 开关关时不发任何请求（./api.ts 直接回 404）。结果码原样映射成提示（./copy.ts）。反馈文本只放进请求体，不打日志、不回显。
import { revalidatePath } from 'next/cache';
import { redirect } from 'next/navigation';
import type { FormResult } from '../auth/ResultForm.tsx';
import { actionSession } from '../lib/session.ts';
import { isUuid } from '../lib/star-map.ts';
import { postFeedback, postInternalTestExit } from './api.ts';
import { internalTestEnabled } from './features.ts';
import { feedbackFromForm } from './form.ts';

const text = (f: FormData, k: string): string => (typeof f.get(k) === 'string' ? (f.get(k) as string) : '');

/** 问题反馈（学生本人与家长共用；来源由 BFF 按会话决定，表单里不收） */
export async function feedbackAction(_: FormResult | null, f: FormData): Promise<FormResult> {
  if (!internalTestEnabled()) return { code: 'NOT_FOUND' };
  const s = await actionSession(f.get('csrf'));
  if (!s) return { code: 'CSRF' };
  const student = text(f, 'student');
  if (!isUuid(student)) return { code: 'NOT_FOUND' };
  const input = feedbackFromForm(f);
  if (!input) return { code: 'INVALID' };
  const r = await postFeedback(s, student, input);
  if (!r.ok) {
    if (r.code === 'UNAUTHENTICATED') redirect('/login');
    return { code: r.code };
  }
  return { code: 'recorded' };
}

/** 退出内部测试：确认页上勾选“我已了解”才提交（二次确认）；没勾不发请求 */
export async function exitInternalTestAction(_: FormResult | null, f: FormData): Promise<FormResult> {
  if (!internalTestEnabled()) return { code: 'NOT_FOUND' };
  const s = await actionSession(f.get('csrf'));
  if (!s) return { code: 'CSRF' };
  const student = text(f, 'student');
  if (!isUuid(student)) return { code: 'NOT_FOUND' };
  if (f.get('confirm') !== 'on') return { code: 'CONFIRM_MISSING' };
  const r = await postInternalTestExit(s, student);
  if (!r.ok) {
    if (r.code === 'UNAUTHENTICATED') redirect('/login');
    return { code: r.code };
  }
  revalidatePath(`/family/${student}`);
  return { code: 'exited' };
}
