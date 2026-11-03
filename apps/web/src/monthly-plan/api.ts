// 月度计划的 BFF 客户端（接口 services/bff/openapi/monthly-plan.v0.yaml）：只在服务端调用（服务端组件），浏览器不直连 BFF。
// 每个调用先过前端开关（./features.ts）：开关关时不发任何请求，直接按 404 返回。全文件只有 call 里一处 fetch。只读，不计量。
// 前端不判断授权与权益：BFF 与数据库判断，这里只把结果原样交回；返回值再由 ./student-view.ts、./guardian-view.ts 按白名单挑字段。
import { bffBaseUrl, bffHeaders, type BffSession } from '../lib/bff.ts';
import { isUuid } from '../lib/star-map.ts';
import { monthlyPlanEnabled } from './features.ts';

export type PlanApi = { ok: true; value: unknown } | { ok: false; status: number };

const OFF = { ok: false, status: 404 } as const;
/** 家长端本月没有快照时 BFF 要等洞察服务生成（BFF 一侧的时限 10 秒），留出余量 */
const TIMEOUT_MS = 20_000;

async function call(s: BffSession, student: string, leaf: 'monthly-plan' | 'my-plan'): Promise<PlanApi> {
  if (!monthlyPlanEnabled()) return OFF;
  if (!isUuid(student)) return OFF;
  let r: Response;
  try {
    r = await fetch(`${bffBaseUrl()}/bff/v0/students/${student}/${leaf}`, {
      method: 'GET', headers: await bffHeaders(s, false), cache: 'no-store', redirect: 'error', signal: AbortSignal.timeout(TIMEOUT_MS),
    });
  } catch {
    return { ok: false, status: 503 };
  }
  const text = await r.text();
  if (!r.ok) return { ok: false, status: r.status };
  try {
    return { ok: true, value: JSON.parse(text) as unknown };
  } catch {
    return { ok: false, status: 502 };
  }
}

/** 家长读本月的月度计划（本月没有快照时 BFF 先请洞察服务生成） */
export const fetchGuardianPlan = (s: BffSession, student: string) => call(s, student, 'monthly-plan');

/** 学生本人读本月的学习计划（不触发生成） */
export const fetchMyPlan = (s: BffSession, student: string) => call(s, student, 'my-plan');
