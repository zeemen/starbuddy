'use server';
// 讲评课的写操作（server action；specs/stage-lesson-session ③b-5）：打开讲评课与课内提问。
// 每个操作先过前端开关（关时按“没有找到”返回，不发请求），再做双重提交核对（客户端组件带回的 CSRF = cookie，actionSession），
// 然后转交 BFF；授权、计量、放行都在 BFF、数据库与学习服务。
// 客户端只传学生、学科、讲评课引用、幂等键（每次打开、每一问各一个，结果未知时用同一个键重试）、票据与结构化提问。
import { redirect } from 'next/navigation';
import { askLesson, openLesson } from '../lib/lesson-api.ts';
import { actionSession } from '../lib/session.ts';
import { isLessonSubject, LESSON_SUBJECTS, lessonEnabled } from './features.ts';
import { pickAsk, pickOpen, TICKET, uiError, UUID, UUID_V4, LESSON_ID, LOCAL_REF, SEMVER, type AskView, type LessonRef, type OpenView, type UiError } from './view.ts';

export type OpenOutcome = { kind: 'open'; view: OpenView } | { kind: 'error'; error: UiError };
export type AskOutcome = { kind: 'ask'; view: AskView } | { kind: 'error'; error: UiError };
export interface AskInput { cursor: number; anchor: { kind: 'element' | 'slot' | 'step'; id: string }; intent: 'why_step' | 'what_is' | 'where_from' }

const STAGE_ID = /^[a-z][a-z0-9_]{0,31}$/;

const err = (error: UiError): { kind: 'error'; error: UiError } => {
  if (error === 'login') redirect('/login');
  return { kind: 'error', error };
};

async function begin(csrf: unknown, student: unknown, subject: unknown) {
  if (!lessonEnabled()) return null;
  if (typeof student !== 'string' || !UUID.test(student) || !isLessonSubject(subject)) return null;
  const s = await actionSession(csrf);
  return s ? { s, student, subject } : null;
}

const refOk = (r: unknown): r is LessonRef => {
  if (!r || typeof r !== 'object') return false;
  const o = r as Record<string, unknown>;
  return typeof o['lessonId'] === 'string' && LESSON_ID.test(o['lessonId']) && typeof o['lessonVersion'] === 'string' && SEMVER.test(o['lessonVersion'])
    && typeof o['node'] === 'string' && LOCAL_REF.test(o['node']) && (o['item'] === null || (typeof o['item'] === 'string' && LOCAL_REF.test(o['item'])));
};

const questionOk = (q: unknown): q is AskInput => {
  if (!q || typeof q !== 'object') return false;
  const o = q as Record<string, unknown>;
  const a = o['anchor'] as Record<string, unknown> | null;
  return Object.keys(o).length === 3 && typeof o['cursor'] === 'number' && Number.isInteger(o['cursor']) && o['cursor'] >= 1 && o['cursor'] <= 100
    && !!a && typeof a === 'object' && Object.keys(a).length === 2 && ['element', 'slot', 'step'].includes(a['kind'] as string)
    && typeof a['id'] === 'string' && STAGE_ID.test(a['id']) && ['why_step', 'what_is', 'where_from'].includes(o['intent'] as string);
};

export async function openLessonAction(csrf: string, student: string, subject: string, ref: LessonRef, key: string): Promise<OpenOutcome> {
  const b = await begin(csrf, student, subject);
  if (!b || !refOk(ref) || typeof key !== 'string' || !UUID_V4.test(key)) return err('not_found');
  const prefix = LESSON_SUBJECTS[b.subject];
  // 只带讲评课引用：节点与题由学习服务按目录补全（specs/lesson-catalog PR C）。地址里的 node、item 在 PR D 改地址时一并去掉
  const r = await openLesson(b.s, b.student, {
    subject: prefix, lesson_ref: { lesson_id: ref.lessonId, lesson_version: ref.lessonVersion }, idempotency_key: key });
  if (!r.ok) return err(uiError(r));
  const view = pickOpen(r.value);
  return view ? { kind: 'open', view } : err('unavailable');
}

export async function askLessonAction(csrf: string, student: string, subject: string, ticket: string, question: AskInput, key: string)
  : Promise<AskOutcome> {
  const b = await begin(csrf, student, subject);
  if (!b) return err('not_found');
  if (typeof ticket !== 'string' || !TICKET.test(ticket)) return err('expired');
  if (!questionOk(question) || typeof key !== 'string' || !UUID_V4.test(key)) return err('invalid');
  const r = await askLesson(b.s, b.student, {
    ticket, idempotency_key: key, question: { cursor: question.cursor, anchor: { kind: question.anchor.kind, id: question.anchor.id }, intent: question.intent } });
  if (!r.ok) return err(uiError(r));
  const view = pickAsk(r.value);
  return view ? { kind: 'ask', view } : err('unavailable');
}
