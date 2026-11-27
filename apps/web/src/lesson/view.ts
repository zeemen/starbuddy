// 讲评课会话的视图（specs/stage-lesson-session ③b-5）：BFF 返回值按白名单挑字段，错误映射成页面编码。纯函数，服务端与客户端共用。
// 讲解 DSL 与台词稿在这里只核对是对象；真正的校验在客户端组件里用 packages/stage 的 migrateStageDsl / migrateStageScript（与样张页同一套入口）。
// 票据是 BFF 封装的不透明串（sbl1.），只放在组件内存里，不进 DOM、不进地址栏、不写浏览器存储。
export const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
export const UUID_V4 = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
export const LESSON_ID = /^[a-z][a-z0-9_.-]{0,63}$/;
export const SEMVER = /^\d{1,3}\.\d{1,3}\.\d{1,4}$/;
/** 节点、题目的本地 ID（地址栏里不带学科前缀，由前端补上 aya:） */
export const LOCAL_REF = /^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$/;
export const TICKET = /^sbl1\.[A-Za-z0-9_-]{1,1695}$/;

export interface LessonRef { lessonId: string; lessonVersion: string; node: string; item: string | null }
export interface OpenView { dsl: unknown; script: unknown; ticket: string; questionsLeft: number }
export type AskView = { kind: 'answered'; text: string; questionsLeft: number } | { kind: 'no_answer'; reason: 'not_available' | 'limit'; questionsLeft: number };

/** 页面错误编码（文案在 copy.ts 的 LESSON_ERROR） */
export type UiError = 'login' | 'not_found' | 'quota' | 'curfew' | 'expired' | 'closed' | 'retry' | 'unavailable' | 'invalid';

const isRecord = (v: unknown): v is Record<string, unknown> => v !== null && typeof v === 'object' && !Array.isArray(v);
const count = (v: unknown): number | null => (typeof v === 'number' && Number.isInteger(v) && v >= 0 && v <= 20 ? v : null);

/** 地址栏参数 → 讲评课引用；不合格返回 null（页面 404） */
export function parseLessonRef(lessonId: unknown, v: unknown, node: unknown, item: unknown): LessonRef | null {
  if (typeof lessonId !== 'string' || !LESSON_ID.test(lessonId) || typeof v !== 'string' || !SEMVER.test(v)) return null;
  if (typeof node !== 'string' || !LOCAL_REF.test(node)) return null;
  if (item !== undefined && (typeof item !== 'string' || !LOCAL_REF.test(item))) return null;
  return { lessonId, lessonVersion: v, node, item: typeof item === 'string' ? item : null };
}

/** 打开：只挑讲解 DSL、台词稿、票据与余量（过期时间、会话 ID 等不进页面） */
export function pickOpen(v: unknown): OpenView | null {
  if (!isRecord(v) || v['dsl_version'] !== '0.3.0' || !isRecord(v['dsl']) || !isRecord(v['script'])) return null;
  const ticket = v['ticket'];
  const left = count(v['questions_left']);
  if (typeof ticket !== 'string' || !TICKET.test(ticket) || left === null) return null;
  return { dsl: v['dsl'], script: v['script'], ticket, questionsLeft: left };
}

/** 提问：回答文字或“不答”的原因，与余量 */
export function pickAsk(v: unknown): AskView | null {
  if (!isRecord(v)) return null;
  const left = count(v['questions_left']);
  if (left === null) return null;
  if (v['outcome'] === 'answered' && typeof v['text'] === 'string' && v['text'].trim() !== '' && v['text'].length <= 4000) {
    return { kind: 'answered', text: v['text'], questionsLeft: left };
  }
  if (v['outcome'] === 'no_answer' && (v['reason'] === 'not_available' || v['reason'] === 'limit')) {
    return { kind: 'no_answer', reason: v['reason'], questionsLeft: left };
  }
  return null;
}

/** BFF 的失败 → 页面编码 */
export function uiError(r: { status: number; code: string; reason: string | null }): UiError {
  if (r.status === 401) return 'login';
  if (r.status === 404 || r.status === 403) return 'not_found';
  if (r.status === 402) return r.reason === 'curfew' || r.reason === 'curfew_soon' ? 'curfew' : 'quota';
  if (r.status === 410) return 'expired';
  if (r.status === 409 && (r.code === 'LESSON_CLOSED' || r.code === 'KEY_FAILED_PREVIOUSLY' || r.code === 'KEY_EXPIRED')) return 'closed';
  if (r.status === 503 && r.code === 'UPSTREAM_UNAVAILABLE') return 'retry';
  if (r.status === 422 && r.code === 'LESSON_NOT_AVAILABLE') return 'unavailable';
  if (r.status === 422 || r.status === 409) return 'invalid';
  return 'unavailable';
}
