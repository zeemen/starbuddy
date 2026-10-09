'use server';
// 复测与验证题的写操作（server action；specs/retest-delivery-kge05）：取题与作答。做法同错题再练（src/practice/actions.ts）：
// 先过前端开关（关时按“没有找到”返回，不发请求），再做双重提交核对（actionSession），然后转交 BFF；授权、计次、判分都在 BFF、数据库与学习服务。
// 客户端只传学生、学科、条目、票据与作答；幂等键在 BFF 封装的票据里。结果只回视图与错误编码。
import { redirect } from 'next/navigation';
import type { Answer } from '../lib/diagnosis.ts';
import { answerRetest, nextRetestItem } from '../lib/retest-api.ts';
import { actionSession } from '../lib/session.ts';
import type { UiError } from '../practice/view.ts';
import { isRetestSubject, retestEnabled } from './features.ts';
import { pickRetestAnswer, pickRetestNext, retestUiError, UUID, type RetestAnswerView, type RetestNextView } from './view.ts';

export type RetestNextOutcome = RetestNextView | { kind: 'error'; error: UiError };
export type RetestAnswerOutcome = { kind: 'result'; result: RetestAnswerView } | { kind: 'error'; error: UiError };

const TICKET = /^sbr1\.[A-Za-z0-9_-]{1,2395}$/;

function isAnswer(a: unknown): a is Answer {
  if (!a || typeof a !== 'object' || Array.isArray(a)) return false;
  const o = a as Record<string, unknown>;
  const keys = Object.keys(o);
  if (keys.length !== 1) return false;
  if (keys[0] === 'text') return typeof o['text'] === 'string' && o['text'].trim().length > 0 && o['text'].length <= 200;
  return keys[0] === 'choice' && Array.isArray(o['choice']) && o['choice'].length > 0 && o['choice'].length <= 8
    && new Set(o['choice']).size === o['choice'].length && o['choice'].every((k) => typeof k === 'string' && /^[A-Za-z0-9]{1,8}$/.test(k));
}

const err = (error: UiError): { kind: 'error'; error: UiError } => {
  if (error === 'login') redirect('/login');
  return { kind: 'error', error };
};

async function begin(csrf: unknown, student: unknown, subject: unknown) {
  if (!retestEnabled()) return null;
  if (typeof student !== 'string' || !UUID.test(student) || !isRetestSubject(subject)) return null;
  const s = await actionSession(csrf);
  return s ? { s, student, subject } : null;
}

export async function nextRetestAction(csrf: string, student: string, subject: string, entry: string): Promise<RetestNextOutcome> {
  const b = await begin(csrf, student, subject);
  if (!b || typeof entry !== 'string' || !UUID.test(entry)) return err('not_found');
  const r = await nextRetestItem(b.s, b.student, b.subject, entry);
  if (!r.ok) return err(retestUiError(r, 'next'));
  return pickRetestNext(r.value, entry) ?? err('unavailable');
}

export async function answerRetestAction(csrf: string, student: string, subject: string, entry: string, ticket: string, answer: Answer)
  : Promise<RetestAnswerOutcome> {
  const b = await begin(csrf, student, subject);
  if (!b || typeof entry !== 'string' || !UUID.test(entry)) return err('not_found');
  if (typeof ticket !== 'string' || !TICKET.test(ticket)) return err('ticket_used');
  if (!isAnswer(answer)) return err('invalid_answer');
  const r = await answerRetest(b.s, b.student, b.subject, ticket, answer);
  if (!r.ok) return err(retestUiError(r, 'answer'));
  const result = pickRetestAnswer(r.value, entry);
  return result ? { kind: 'result', result } : err('unavailable');
}
