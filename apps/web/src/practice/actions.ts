'use server';
// 错题再练的写操作（server action；specs/practice-loop-stage1 PR 4）：取题与作答。
// 每个操作先过前端开关（关时按“没有找到”返回，不发请求），再做双重提交核对（客户端组件带回的 CSRF = cookie，actionSession），
// 然后连同浏览器的 Origin 转交 BFF；授权、计次、判分都在 BFF、数据库与学习服务。
// 客户端只传学生、学科、条目、票据与作答；幂等键在 BFF 封装的票据里，学科码、条目 ID 按白名单核对。结果只回视图与错误编码。
import { redirect } from 'next/navigation';
import type { Answer } from '../lib/diagnosis.ts';
import { answerPractice, nextPracticeItem } from '../lib/practice-api.ts';
import { actionSession } from '../lib/session.ts';
import { isPracticeSubject, practiceEnabled } from './features.ts';
import { pickAnswer, pickNext, uiError, UUID, type AnswerView, type NextView, type UiError } from './view.ts';

export type NextOutcome = NextView | { kind: 'error'; error: UiError };
export type AnswerOutcome = { kind: 'result'; result: AnswerView } | { kind: 'error'; error: UiError };

const TICKET = /^sbp1\.[A-Za-z0-9_-]{1,2395}$/;

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
  if (!practiceEnabled()) return null;
  if (typeof student !== 'string' || !UUID.test(student) || !isPracticeSubject(subject)) return null;
  const s = await actionSession(csrf);
  return s ? { s, student, subject } : null;
}

export async function nextPracticeAction(csrf: string, student: string, subject: string, entry: string): Promise<NextOutcome> {
  const b = await begin(csrf, student, subject);
  if (!b || typeof entry !== 'string' || !UUID.test(entry)) return err('not_found');
  const r = await nextPracticeItem(b.s, b.student, b.subject, entry);
  if (!r.ok) return err(uiError(r, 'next'));
  return pickNext(r.value, entry) ?? err('unavailable');
}

export async function answerPracticeAction(csrf: string, student: string, subject: string, entry: string, ticket: string, answer: Answer)
  : Promise<AnswerOutcome> {
  const b = await begin(csrf, student, subject);
  if (!b || typeof entry !== 'string' || !UUID.test(entry)) return err('not_found');
  if (typeof ticket !== 'string' || !TICKET.test(ticket)) return err('ticket_used');
  if (!isAnswer(answer)) return err('invalid_answer');
  const r = await answerPractice(b.s, b.student, b.subject, ticket, answer);
  if (!r.ok) return err(uiError(r, 'answer'));
  const result = pickAnswer(r.value, entry);
  return result ? { kind: 'result', result } : err('unavailable');
}
