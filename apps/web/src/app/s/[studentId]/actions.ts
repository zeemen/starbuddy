'use server';
// 学生壳的写操作（H4）：连续使用提醒的“知道了”（确认休息，BFF 从现在起重新计）。
// 双重提交同家长端（actionSession）；回到提交时所在的壳内页面（只接受 /s/{本人}/… 下的固定几种路径，不做开放跳转）。
// 今日计划“完成”“先跳过”（PLT-15 PR2）：markPlanAction 经 BFF 现有事件接口写 plan_done / plan_skip。
import { redirect } from 'next/navigation';
import type { FormResult } from '../../../auth/ResultForm.tsx';
import { postEventQuietly } from '../../../lib/events.ts';
import { actionSession, pageSession } from '../../../lib/session.ts';
import { ackRest } from '../../../lib/shell-api.ts';
import { isUuid } from '../../../lib/star-map.ts';
import { isPlanAction, isTaskId, type PlanAction } from '../../../today/plan-mark.ts';
import { planMarkKey, planMarkTicket, verifyPlanMarkTicket } from '../../../today/plan-mark-server.ts';
import { presenceKey } from '../../../behavior/presence-server.ts';

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

/**
 * “完成”“先跳过”的结果：recorded = 已记下（含同一键的重放、409），ticket 是换上的新票据（下一次点击用新键）；
 * silent = 没写（不显示任何提示，对学生静默），ticket 为 null（前端继续用原票据：重试是同一个键）
 */
export interface PlanMarkResult { code: 'recorded' | 'silent'; action: PlanAction | null; ticket: string | null }
const SILENT: PlanMarkResult = { code: 'silent', action: null, ticket: null };

/**
 * 今日计划“完成”“先跳过”（PLT-15 PR2，specs/today-plan-snapshot-plt15 范围第 3 条）：
 *   - 双重提交 CSRF；再经 /me 确认当前会话是路径里的学生本人（家长会话、别的学生到此为止，不写库）；
 *   - 任务 ID 与动作按格式核对；票据（页面渲染时签的，src/today/plan-mark-server.ts）必须对得上本会话、本学生、本任务；
 *     任务是否属于该生当天（或前一天）的快照由库函数核对（0056，别人的、越日的一律拒绝）；
 *   - 幂等键 = H(任务 ID, 动作, 票据 nonce)：同一张票据内重复点击、重试是同一个键；记下后返回新票据，同一页面内改主意每次都入库；
 *   - 只经 BFF 现有事件接口（POST /students/{id}/events）写；失败对学生静默（不提示、不重试）。
 * actionSession 与 pageSession 读的是同一个会话 cookie，所以不再比较两者的令牌（审查建议）。
 */
export async function markPlanAction(_: PlanMarkResult | null, f: FormData): Promise<PlanMarkResult> {
  const s = await actionSession(f.get('csrf'));
  if (!s) return SILENT;
  const student = f.get('student');
  const task = f.get('task');
  const action = f.get('act');
  if (typeof student !== 'string' || !isUuid(student) || !isTaskId(task) || !isPlanAction(action)) return SILENT;
  const ps = await pageSession();
  if (!ps || ps.me.account_kind !== 'student' || ps.me.student_id !== student) return SILENT;
  const key = presenceKey();
  if (!key) return SILENT;  // 正式环境没配签名密钥：不写
  const ctx = { key, token: s.token, student };
  const nonce = verifyPlanMarkTicket(f.get('ticket'), ctx, task);
  if (!nonce) return SILENT;
  const ok = await postEventQuietly(s, student, { kind: action, payload: { plan_task_id: task } }, planMarkKey(task, action, nonce));
  return ok ? { code: 'recorded', action, ticket: planMarkTicket(ctx, task) } : SILENT;
}
