'use server';
// 家长诉求的写操作（PLT-14 第 1 步）：双重提交——表单里的 CSRF 令牌必须等于 CSRF cookie（actionSession），再连同浏览器的 Origin 转交 BFF；
// BFF 核对它与会话绑定、学生会话一律 404、授权交给数据库。开关关时不发任何请求（./api.ts 直接回 404）。结果码原样映射成提示（./copy.ts）。
import { revalidatePath } from 'next/cache';
import { redirect } from 'next/navigation';
import type { FormResult } from '../auth/ResultForm.tsx';
import { actionSession } from '../lib/session.ts';
import { isUuid } from '../lib/star-map.ts';
import { putFamilyGoals } from './api.ts';
import { familyGoalsEnabled } from './features.ts';
import { goalBodyFromForm } from './form.ts';

export async function familyGoalsAction(_: FormResult | null, f: FormData): Promise<FormResult> {
  if (!familyGoalsEnabled()) return { code: 'NOT_FOUND' };
  const s = await actionSession(f.get('csrf'));
  if (!s) return { code: 'CSRF' };
  const student = typeof f.get('student') === 'string' ? (f.get('student') as string) : '';
  if (!isUuid(student)) return { code: 'NOT_FOUND' };
  const body = goalBodyFromForm(f);
  if (!body) return { code: 'INVALID' };
  const r = await putFamilyGoals(s, student, body);
  if (!r.ok) {
    if (r.code === 'UNAUTHENTICATED') redirect('/login');
    return { code: r.code };
  }
  revalidatePath(`/family/${student}/goals`);
  return { code: 'saved' };
}
