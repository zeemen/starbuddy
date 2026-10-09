'use server';
// 学生壳的写操作（H4）：连续使用提醒的“知道了”（确认休息，BFF 从现在起重新计）。
// 双重提交同家长端（actionSession）；回到提交时所在的壳内页面（只接受 /s/{本人}/… 下的固定几种路径，不做开放跳转）。
import { redirect } from 'next/navigation';
import type { FormResult } from '../../../auth/ResultForm.tsx';
import { actionSession } from '../../../lib/session.ts';
import { ackRest } from '../../../lib/shell-api.ts';
import { isUuid } from '../../../lib/star-map.ts';

const BACK = /^\/s\/[0-9a-f-]{36}\/(today|star-map|learn(\/chinese|\/(math|politics)(\/(practice|retest)(\/[0-9a-f-]{36})?)?)?|disputes|weekly|exams(\/[0-9a-f-]{36})?|why\/[0-9a-f-]{36})$/;

export async function ackRestAction(_: FormResult | null, f: FormData): Promise<FormResult> {
  const s = await actionSession(f.get('csrf'));
  if (!s) return { code: 'CSRF' };
  const student = typeof f.get('student') === 'string' ? (f.get('student') as string) : '';
  if (!isUuid(student)) return { code: 'NOT_FOUND' };
  const r = await ackRest(s, student);
  if (!r.ok) {
    if (r.code === 'UNAUTHENTICATED') redirect('/login');
    return { code: r.code };
  }
  const back = typeof f.get('back') === 'string' ? (f.get('back') as string) : '';
  redirect(BACK.test(back) && back.startsWith(`/s/${student}/`) ? back : `/s/${student}/today`);
}
