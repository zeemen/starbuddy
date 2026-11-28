// 讲评课引用（编号与版本）：打开讲评课的请求体、讲评课目录与再练、复测条目上的 lesson_ref 共用一份校验（specs/lesson-catalog PR C）。
// 只有编号与版本：节点与题不出学习服务（再练“不把题目引用给前端”同一口径）。
export interface LessonRef { lesson_id: string; lesson_version: string }

const LESSON_ID = /^[a-z][a-z0-9_.-]{0,63}$/;
const SEMVER = /^\d{1,3}\.\d{1,3}\.\d{1,4}$/;

/** 编号与版本都合格、别的键一律没有才收；否则 null */
export function parseLessonRef(ref: unknown): LessonRef | null {
  if (ref === null || typeof ref !== 'object' || Array.isArray(ref)) return null;
  const o = ref as Record<string, unknown>;
  const keys = Object.keys(o);
  if (keys.length !== 2 || !('lesson_id' in o) || !('lesson_version' in o)) return null;
  const { lesson_id: id, lesson_version: ver } = o;
  if (typeof id !== 'string' || !LESSON_ID.test(id) || typeof ver !== 'string' || !SEMVER.test(ver)) return null;
  return { lesson_id: id, lesson_version: ver };
}
