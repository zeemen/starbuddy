// 讲评课会话（specs/stage-lesson-session ③b-5）的 BFF 客户端（接口 services/bff/openapi/lesson.v0.yaml）：只在服务端调用
// （server action），浏览器不直连 BFF，前端也不直连学习服务。全文件只有 call 里一处 fetch。
// 每个调用先过前端开关（src/lesson/features.ts）：开关关时不发任何请求，直接按 404 返回。
// 前端不判断授权、不计量：打开时的预留与结算、提问的放行都在 BFF、数据库与学习服务；返回值再由 src/lesson/view.ts 按白名单挑字段。
// 讲评课目录（specs/lesson-catalog PR D）：GET .../lessons/catalog/{学科}，只读、不扣次。
import { lessonEnabled } from '../lesson/features.ts';
import { BFF_TIMEOUT_MS, bffBaseUrl, bffHeaders, type BffSession } from './bff.ts';
import { isUuid } from './star-map.ts';

/** reason：402 时 BFF 给的原因编码（curfew、insufficient、no_entitlement 等） */
export type LessonApi<T> = { ok: true; value: T; replayed: boolean } | { ok: false; status: number; code: string; reason: string | null };

const OFF = { ok: false, status: 404, code: 'NOT_FOUND', reason: null } as const;
/** BFF 整体总时限 45 秒（含重试），这里多留余量 */
const TIMEOUT_MS = 60_000;

/** 目录的学科码只认前端口径（小写字母）；其余不发请求 */
const SUBJECT_CODE = /^[a-z]{2,16}$/;

async function call<T>(s: BffSession, student: string, tail: 'open' | 'ask' | `catalog/${string}`, body?: unknown): Promise<LessonApi<T>> {
  if (!lessonEnabled()) return OFF;
  if (!isUuid(student)) return OFF;
  const post = body !== undefined;
  const headers: Record<string, string> = post ? { ...(await bffHeaders(s, true)), 'content-type': 'application/json' } : await bffHeaders(s, false);
  let r: Response;
  try {
    r = await fetch(`${bffBaseUrl()}/bff/v0/students/${student}/lessons/${tail}`, {
      method: post ? 'POST' : 'GET', headers, ...(post ? { body: JSON.stringify(body) } : {}), cache: 'no-store', redirect: 'error',
      signal: AbortSignal.timeout(post ? TIMEOUT_MS : BFF_TIMEOUT_MS),  // 目录只读：与其他读接口同一个超时，科目页不久等
    });
  } catch {
    return { ok: false, status: 503, code: 'UNAVAILABLE', reason: null };
  }
  let json: unknown = null;
  try { json = JSON.parse(await r.text()); } catch { /* 非 JSON：按错误处理 */ }
  if (r.ok) return { ok: true, value: json as T, replayed: r.headers.get('idempotent-replayed') === 'true' };
  const field = (k: string): string | null => json && typeof json === 'object' && typeof (json as Record<string, unknown>)[k] === 'string'
    ? (json as Record<string, string>)[k]! : null;
  return { ok: false, status: r.status, code: field('code') ?? 'INTERNAL', reason: field('reason') };
}

/** 打开一节讲评课（BFF 预留一次 lesson_whiteboard，下发即结算；同一个幂等键重试不重扣） */
export const openLesson = (s: BffSession, student: string, body: Record<string, unknown>) => call<unknown>(s, student, 'open', body);
/** 课内提问（不扣次） */
export const askLesson = (s: BffSession, student: string, body: Record<string, unknown>) => call<unknown>(s, student, 'ask', body);
/** 这一科的讲评课目录（只读，不扣次）；学科码不合前端口径时不发请求、按 404 返回 */
export const fetchLessonCatalog = (s: BffSession, student: string, subject: string) =>
  SUBJECT_CODE.test(subject) ? call<unknown>(s, student, `catalog/${subject}`) : Promise.resolve(OFF);
