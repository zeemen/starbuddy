// 内部成员测试运营（specs/launch-lt06-internal-test-ops 范围第 3、4 条，决定记录第 1–3 条；迁移 0059；接口 openapi/internal-test.v0.yaml）。
// 三条路由挂在功能开关 internal_test 上（STARBUDDY_INTERNAL_TEST，缺省关，src/internal-test/features.ts）：关闭时不注册。
// 退出判据（exitedGuard）不挂开关：迁移 0059 执行之后无条件生效（决定记录（续）第 4 条；main.ts 启动时核对判据函数在不在）。
//   - 问题反馈 POST /students/{student_id}/feedback（session）：学生本人写自己的（来源 student_self_report），有效绑定家长写孩子的
//     （来源 guardian_observation）；来源由会话的账号类型决定，请求里不收。固定分类 + 1–500 字文本 + 可选页面路由模板（只收登记的模板）。
//     授权全在库里（0059 的 RLS：提交者必须是会话账号、来源与账号关系一致、退出的档案不收）：无权与不存在同一个 404。
//     自由文本只进库：不进访问日志、SLS 与指标（访问日志只记 outcome 枚举；指标只按路由模板计数），不进判断账本与画像（规则 11、12）。
//   - 退出状态 GET /students/{student_id}/internal-test（guardian）：{ exited }。
//   - 退出测试 POST /students/{student_id}/internal-test/exit（guardian，请求体 { confirm: true }，前端先经确认页二次确认）：
//     库函数 platform.exit_internal_test 在一个事务里撤回同意、作废学生会话、写退出记录（幂等）；事务提交之后，staging 白名单固定码
//     按库给出的家长账号（已没有别的在测孩子的有效绑定家长）作废他们在固定码登录成功时对应上的条目
//     （src/http/staging-otp.ts 的 revokeByAccounts，写审计 revoked；决定记录（续）第 5 条）。
//     已有数据保留到测试结束统一删除（走数据权利的删除流程，docs/ops/internal-test-runbook.md）。
//   - 统一判据（exitedGuard，server.ts 在 CSRF 与限流之后、handler 之前调用）：库里有判据函数时，路径里带学生的请求，只要该档案已退出
//     （platform.internal_test_exited：当前账号能读且有退出记录），除登记在 ALLOW_AFTER_EXIT 的数据权利与退出状态外一律 409 TEST_EXITED——
//     读写、触发模型处理（判分、诊断、再练、复测、今日计划、周报、解释）都拦；当前账号读不到的档案判为未退出，交给原来的授权得到 404。
import { withAccount } from '../metering/db.ts';
import { SERVICE, stagingOtpRevokeAuditFailed } from '../observability/metrics.ts';
import { P } from './problems.ts';
import type { Handler, Reply, Route, Session, ShellDeps } from './routes.ts';

const json = (status: number, v: unknown): Reply => ({ status, body: JSON.stringify(v) });
const problem = (body: string): Reply => ({ status: (JSON.parse(body) as { status: number }).status, body });
const pgCode = (e: unknown): string => (e && typeof e === 'object' && 'code' in e ? String((e as { code: unknown }).code) : '');

/** 固定分类（与迁移 0059 的 CHECK 一致） */
export const FEEDBACK_CATEGORIES = ['malfunction', 'wrong_content', 'display', 'slow', 'login', 'suggestion', 'other'] as const;
export const FEEDBACK_MAX_CHARS = 500;
/** 可以附上的页面路由模板（前端 apps/web/src/internal-test/routes.ts 同一份，测试核对一致）：只是模板，不含任何标识 */
export const FEEDBACK_ROUTES: readonly string[] = [
  '/student', '/s/[studentId]/today', '/s/[studentId]/star-map', '/s/[studentId]/learn', '/s/[studentId]/learn/[subject]',
  '/s/[studentId]/learn/[subject]/practice', '/s/[studentId]/learn/[subject]/retest', '/s/[studentId]/plan', '/s/[studentId]/weekly',
  '/s/[studentId]/disputes', '/s/[studentId]/exams', '/s/[studentId]/why',
  '/family', '/family/[studentId]', '/family/[studentId]/star-map', '/family/[studentId]/time', '/family/[studentId]/weekly',
  '/family/[studentId]/data', '/family/[studentId]/disputes', '/family/[studentId]/exams', '/family/[studentId]/goals',
  '/family/[studentId]/monthly-plan', '/family/[studentId]/why', '/onboarding/[studentId]',
];
/** 文本里不收的码位区间：控制字符（换行、回车、制表除外）与零宽、方向覆盖、行段分隔等格式字符（与迁移 0059 的 CHECK 同口径）。
 *  按码位写区间、运行时拼正则，源码里不出现不可见字符 */
export const FORBIDDEN_RANGES: readonly (readonly [number, number])[] = [
  [0x00, 0x08], [0x0b, 0x0c], [0x0e, 0x1f], [0x7f, 0x9f], [0x200b, 0x200f], [0x2028, 0x202e], [0x2060, 0x2069], [0xfeff, 0xfeff],
];
const FORBIDDEN_CHARS = new RegExp(`[${FORBIDDEN_RANGES.map(([a, b]) => `\\u{${a.toString(16)}}-\\u{${b.toString(16)}}`).join('')}]`, 'u');

/** 请求体：{ category, body, route_template? }；不合格返回 null（调用方 422）。字数按 Unicode 码位数（与库里 char_length 一致） */
export function parseFeedback(b: Record<string, unknown> | null): { category: string; body: string; route: string | null } | null {
  if (!b || !Object.keys(b).every((k) => ['category', 'body', 'route_template'].includes(k))) return null;
  const { category, body } = b;
  const route = b['route_template'] ?? null;
  if (typeof category !== 'string' || !(FEEDBACK_CATEGORIES as readonly string[]).includes(category)) return null;
  if (typeof body !== 'string' || body.trim() === '' || [...body].length > FEEDBACK_MAX_CHARS || FORBIDDEN_CHARS.test(body)) return null;
  if (route !== null && (typeof route !== 'string' || !FEEDBACK_ROUTES.includes(route))) return null;
  return { category, body, route };
}

const feedback: Handler = async (ctx) => {
  const input = parseFeedback(ctx.body);
  if (!input) return problem(P.INVALID);
  const source = ctx.session!.kind === 'student' ? 'student_self_report' : 'guardian_observation';
  try {
    await ctx.q(
      `INSERT INTO platform.feedback (submitter_account_id, student_id, source, category, body, route_template)
       VALUES (platform.current_account_id(), $1, $2, $3, $4, $5)`, [ctx.params['student_id'], source, input.category, input.body, input.route]);
  } catch (e) {
    // RLS 拒绝（不是本人、未绑定、已解除、已退出）与学生不存在：同一个 404；库里的取值约束：422
    if (pgCode(e) === '42501' || pgCode(e) === '23503') return problem(P.NOT_FOUND);
    if (pgCode(e) === '23514') return problem(P.INVALID);
    throw e;
  }
  ctx.note('outcome', 'recorded');  // 只记枚举：不记分类、文本、路由模板
  return json(201, { status: 'recorded' });
};

const status: Handler = async (ctx) => {
  const id = ctx.params['student_id']!;
  const { rows } = await ctx.q<{ exited: boolean }>(
    'SELECT platform.internal_test_exited(s.id) AS exited FROM platform.student_profile s WHERE s.id = $1', [id]);
  if (!rows[0]) return problem(P.NOT_FOUND);  // RLS：读不到 = 无权或不存在
  return json(200, { student_id: id, exited: rows[0].exited === true });
};

interface ExitRow { exit_at: Date; newly: boolean; revoked_types: string[]; revoke_accounts: string[] }
class NotConfirmed extends Error {}

/** 退出测试：事务里经库函数撤回同意、作废学生会话、写退出记录；提交之后作废 staging 白名单条目（handler 自己开事务，ownTx） */
const exit: Handler = async (ctx) => {
  const b = ctx.body;
  if (!b || Object.keys(b).join(',') !== 'confirm' || typeof b['confirm'] !== 'boolean') return problem(P.INVALID);
  const confirmed = b['confirm'] === true;
  const id = ctx.params['student_id']!;
  const s = ctx.session!;
  let row: ExitRow | null;
  try {
    row = await withAccount(ctx.deps.pool, s.accountId, async (c) => {
      // 先按授权读档案（读不到 = 无权或不存在，同一个 404），再看二次确认：没确认的请求不改任何东西
      const seen = await c.query('SELECT 1 FROM platform.student_profile WHERE id = $1', [id]);
      if (seen.rowCount === 0) return null;
      if (!confirmed) throw new NotConfirmed();
      const { rows } = await c.query<ExitRow>('SELECT exit_at, newly, revoked_types, revoke_accounts FROM platform.exit_internal_test($1)', [id]);
      return rows[0] ?? null;
    });
  } catch (e) {
    if (e instanceof NotConfirmed) return problem(P.CONFIRM_REQUIRED);
    if (pgCode(e) === '42501') return problem(P.NOT_FOUND);
    throw e;
  }
  if (!row) return problem(P.NOT_FOUND);
  // 事务已提交：按库给出的家长账号（已没有别的在测孩子的有效绑定家长）作废他们在固定码登录成功时对应上的白名单条目
  // （只在 staging 配了白名单固定码时；账号只在内存里用，不回前端、不记日志）
  let entries: number | null = null;
  if (ctx.deps.stagingOtp) {
    const r = ctx.deps.stagingOtp.revokeByAccounts(new Set(row.revoke_accounts));
    entries = r.revoked;
    if (r.auditFailed) {
      ctx.note('reason', 'otp_revoke_audit_failed');
      stagingOtpRevokeAuditFailed.inc({ service: SERVICE });  // 告警 StarbuddyStagingOtpRevokeAuditFailed（deploy/observability/alerts.yaml）：重启后会丢失这次作废
    }
  }
  ctx.note('outcome', row.newly ? 'exited' : 'already_exited');
  return json(200, { student_id: id, status: 'exited', exited_at: row.exit_at.toISOString(), newly_exited: row.newly,
                     consents_revoked: row.revoked_types, whitelist_entries_revoked: entries });
};

const S = '/students/{student_id}';

export const INTERNAL_TEST_ROUTES: readonly Route[] = [
  { method: 'POST', path: `${S}/feedback`, auth: 'session', csrf: 'session', body: true, handler: feedback,
    rateLimits: [{ bucket: 'feedback.account.day', dim: 'account' }] },
  { method: 'GET', path: `${S}/internal-test`, auth: 'guardian', csrf: 'none', body: false, handler: status, rateLimits: [] },
  { method: 'POST', path: `${S}/internal-test/exit`, auth: 'guardian', csrf: 'session', body: true, handler: exit, ownTx: true,
    rateLimits: [{ bucket: 'internal_test_exit.account.hour', dim: 'account' }] },
];

/** 已退出的档案仍可用的路由（“方法 路径模板”）：数据权利（查看与发起、确认、撤回删除，导出与下载）与退出状态、退出本身（幂等） */
export const ALLOW_AFTER_EXIT: ReadonlySet<string> = new Set([
  'GET /students/{student_id}/internal-test', 'POST /students/{student_id}/internal-test/exit',
  'GET /students/{student_id}/erasure', 'POST /students/{student_id}/erasure', 'POST /students/{student_id}/erasure/{request_id}/confirm',
  'POST /students/{student_id}/erasure/{request_id}/cancel', 'POST /students/{student_id}/exports',
  'POST /students/{student_id}/exports/{export_job_id}/download',
]);

/** 判据函数在不在、本角色能不能执行（迁移 0059 执行之后为真）：main.ts 启动时查一次，写进 ShellDeps.exitGuard */
export const EXIT_GUARD_FUNCTION = 'platform.internal_test_exited(uuid)';
export async function exitGuardAvailable(q: (sql: string, args: unknown[]) => Promise<{ rows: Array<{ ok: boolean | null }> }>): Promise<boolean> {
  const { rows } = await q(
    'SELECT to_regprocedure($1) IS NOT NULL AND has_function_privilege(current_user, to_regprocedure($1), \'EXECUTE\') AS ok', [EXIT_GUARD_FUNCTION]);
  return rows[0]?.ok === true;
}

/**
 * 统一判据（决定记录（续）第 4 条：不挂内测开关，迁移 0059 执行之后无条件生效）：路径里带学生、不是登记的例外路由时，
 * 当前账号能读且已退出的档案一律 409 TEST_EXITED（返回 true 由调用方拒绝）。deps.exitGuard 只表示“库里有判据函数”（main.ts 启动时核对）。
 * 读不到的档案（无权或不存在）判为未退出，照常交给路由自己的授权（同一个 404，不泄露存在性）。
 */
export async function exitedGuard(deps: ShellDeps, route: Route, session: Session | null, student: string | undefined): Promise<boolean> {
  if (deps.exitGuard !== true || !session || !student) return false;
  if (ALLOW_AFTER_EXIT.has(`${route.method} ${route.path}`)) return false;
  return withAccount(deps.pool, session.accountId, async (c) => {
    const { rows } = await c.query<{ exited: boolean }>('SELECT platform.internal_test_exited($1) AS exited', [student]);
    return rows[0]?.exited === true;
  });
}
