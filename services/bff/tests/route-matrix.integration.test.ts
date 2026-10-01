// 冲刺 4-H H6：渗透测试第二轮第 7 节的仓库内回归（specs/unified-shell-sprint4/spec.md 范围第 6 条与验收“路由授权矩阵”
// “身份注入”“连接池串号”“会话”“CSRF”“限流”“安全头与缓存”“计量入口”；specs/household-pentest-round2/spec.md 第 7 节）。
// 5-H spec 待人确认 #1 的默认：H6 在信任界面之后补，与本冲刺新增路由一起生成授权矩阵。
//
// 路由集合 = 统一路由表按开关全开的完整集合（activeRoutes(全部信任界面开关)：认证、壳、建档、事件、诊断、信任界面、周报）。
// 每条路由必须在下面的 SAMPLES 里有一份合成请求样例（能通过请求体校验，授权矩阵才测得到库里的授权，而不是停在 422），
// 并且在 RATE_EXEMPT 与“挂了限流桶”之间二选一：新增路由不登记就失败（第二轮 7.1“新路由不进表就失败”）。
// “谁能用”的第二份来源是 BFF 自己的接口定义（openapi/*.v0.yaml 的 x-sb-auth；只认学生本人的路由另有 x-sb-not-self），
// 路由表与它不一致即失败（tests/openapi-ops.ts、tests/shell-openapi.test.ts）。
//
// 二级资源（考试、判断、导出任务、删除申请、诊断会话、探针）在 before 里给 S、S2、T 各造真实的一份：
//   - 授权矩阵用 S 的真实二级 ID（越权拒绝不是因为二级对象不存在）；
//   - 跨对象：本人用自己孩子的路径带别的孩子（另一户 T、兄妹 S2）的二级 ID，与不存在的二级 ID 逐字节相同；
//   - 阳性对照：本人用真实二级 ID 的同一请求能通过授权（不是 401 / 404 / 403 FORBIDDEN / CSRF），证明矩阵里的拒绝不是空转。
//
// 一次性 Postgres（db/tests/pg-ephemeral.sh），BFF 登录角色只继承 starbuddy_bff，真实会话（OTP + 捕获桩）、真实 RLS 与库函数；
// 学习服务（今日计划、诊断）与洞察服务（异议、预测、对账卡）用计数的假实现：越权请求一次也不能到达它们。全部合成数据（规则 8）。
// 依赖 --test-concurrency=1（make bff-test 与 tests/mutations.sh 都这样跑）：本文件改写测试库里的 platform.auth_now()，
// 并按顺序推进会话时钟（会话过期的用例放在最后）；与别的测试文件并行会互相干扰。
import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import { readdirSync, readFileSync, statSync } from 'node:fs';
import { after, before, test } from 'node:test';
import pg from 'pg';
import type { DiagnosisLearning, DiagOutcome, StartBody } from '../src/diagnosis/learning-client.ts';
import { parseStart } from '../src/diagnosis/service.ts';
import { loadAuthConfig } from '../src/http/config.ts';
import { P } from '../src/http/problems.ts';
import { activeRoutes, compile, PREFIX, ROUTES, type Route } from '../src/http/routes.ts';
import { withAccount } from '../src/metering/db.ts';
import { usageIdFor } from '../src/metering/uuid.ts';
import type { PlanClient, PlanSection, PlanSubject } from '../src/shell/plan-client.ts';
import type { DisputeClient, DisputeOutcome, ExamViewClient } from '../src/trust/clients.ts';
import { MemoryExportStore } from '../src/trust/exports.ts';
import { TRUST_FEATURES } from '../src/trust/features.ts';
import { createFamily, HAS_DB, setupHarness, type Harness } from './harness.ts';
import { operations } from './openapi-ops.ts';
import { callAs, ORIGIN, startShell, type Sess, type ShellHarness } from './shell-harness.ts';

/** 本次部署可能注册的全部路由（信任界面开关全开） */
const ALL: readonly Route[] = activeRoutes(new Set(TRUST_FEATURES));
const keyOf = (r: Route) => `${r.method} ${r.path}`;
const OPS = operations();
const opOf = (r: Route) => OPS.get(`${r.method} ${PREFIX}${r.path}`)!;
const MISSING = '6e000000-0000-4000-8000-0000000000ff';
const FIXED_NOW = Date.now();  // 注入的固定时钟（星图生成时间、近期登录、导出暂存时限）：取启动时刻，GET 两次逐字节可比
const TPL = 'syn.h6.node_mastery';
const now = () => new Date().toISOString();
const dayPlus = (n: number) => new Date(Date.now() + 8 * 3600_000 + n * 86400_000).toISOString().slice(0, 10);

// ---------------------------------------------------------------- 每条路由的合成请求样例

type Body = Record<string, unknown>;
/** body：能通过请求体校验的合成请求体（ids 是路径里二级对象的 ID，导出下载要用对应的下载凭据） */
interface Sample { body?: (ids: Ids) => Body }
const none: Sample = {};
const b = (body: (ids: Ids) => Body): Sample => ({ body });

/** 键 = “方法 路径模板”。新增路由不在这里登记，下面第一条测试失败 */
export const SAMPLES: Record<string, Sample> = {
  'POST /auth/otp/request': b(() => ({ phone: '+99900006001' })),
  'POST /auth/otp/verify': b(() => ({ phone: '+99900006001', code: '000000' })),
  'POST /auth/logout': none,
  'POST /auth/logout-all': none,
  'GET /me': none,
  'POST /students': b(() => ({ grade: 8, region: 'nanjing' })),
  'POST /students/{student_id}/consents': b(() => ({ consent_type: 'pilot_participation', document_version: 'pilot@synthetic-0', decision: 'granted' })),
  'POST /students/{student_id}/student-session': none,
  // 星图、建档（SIE-01）、行为事件与探针（2-L）、数学诊断（SIE-02a）
  'GET /students/{student_id}/star-map': none,
  'GET /students/{student_id}/onboarding': none,
  'PUT /students/{student_id}/onboarding/answers': b(() => ({ field_id: 'outer.g1.weekly_available_time', value: { minutes: 300 } })),
  'POST /students/{student_id}/onboarding/exams': b(() => ({ subject: 'aya', exam_type: 'midterm', exam_date: '2026-11-10', full_score: 100, raw_score: 92 })),
  'PUT /students/{student_id}/onboarding/textbooks': b(() => ({ subject: 'aya', textbook_version: 'suke-2024' })),
  'POST /students/{student_id}/onboarding/progress': b(() => ({ step: 'onboarding', status: 'started' })),
  'POST /students/{student_id}/events': b(() => ({ kind: 'session_start', payload: { entry: 'today' }, occurred_at: now(), idempotency_key: randomUUID() })),
  'POST /students/{student_id}/probes': b(() => ({ subject: 'zhixing', item_id: 'SYN-ZX-ITEM-01', prediction: 'unsure', asked_at: now(), idempotency_key: randomUUID() })),
  'POST /students/{student_id}/probes/{probe_id}/attempt': b(() => ({ attempt_id: randomUUID() })),
  'POST /students/{student_id}/diagnosis/sessions': b(() => ({ subject: 'aya', idempotency_key: randomUUID() })),
  'GET /students/{student_id}/diagnosis/sessions/{session_id}': none,
  'POST /students/{student_id}/diagnosis/sessions/{session_id}/probe': b(() => ({ seq: 1, prediction: 'unsure', asked_at: now(), idempotency_key: randomUUID() })),
  'POST /students/{student_id}/diagnosis/sessions/{session_id}/answer': b(() => ({ seq: 1, answer: { text: '1' }, elapsed_sec: 10, idempotency_key: randomUUID() })),
  'POST /students/{student_id}/diagnosis/sessions/{session_id}/pause': b(() => ({ idempotency_key: randomUUID() })),
  'POST /students/{student_id}/diagnosis/sessions/{session_id}/abandon': b(() => ({ idempotency_key: randomUUID() })),
  // 统一壳 H4
  'GET /students/{student_id}/enrollments': none,
  'POST /students/{student_id}/enrollments/{subject}': none,
  'POST /students/{student_id}/enrollments/{subject}/pause': none,
  'GET /students/{student_id}/today': none,
  'GET /students/{student_id}/balance': none,
  'GET /students/{student_id}/time-guard': none,
  'PUT /students/{student_id}/time-guard': b(() => ({ daily_cap_minutes: 60, curfew_start: null, curfew_end: null })),
  'GET /students/{student_id}/rest-reminder': none,
  'POST /students/{student_id}/rest-reminder/ack': none,
  // 信任界面（SIE-03 PR③、5-H 周报），开关全开时注册
  'POST /students/{student_id}/claims/{claim_id}/disputes': b(() => ({ kind: 'data_error', idempotency_key: 'matrix-key-0000000001' })),
  'GET /students/{student_id}/disputes': none,
  'POST /students/{student_id}/exams': b(() => ({ subject: 'aya', exam_type: 'monthly', exam_date: dayPlus(10), full_score: 100, scope_node_ids: ['MATH.TRU.T1.A'] })),
  'POST /students/{student_id}/exams/{exam_id}/score': b(() => ({ raw_score: 90 })),
  'GET /students/{student_id}/exams/{exam_id}/prediction': none,
  'GET /students/{student_id}/exams/{exam_id}/reconciliation': none,
  'GET /students/{student_id}/weekly-report': none,
  'POST /students/{student_id}/exports': none,
  'POST /students/{student_id}/exports/{export_job_id}/download': b((ids) => ({ download_ref: ids.download_ref ?? `dl_${'0'.repeat(64)}` })),
  'GET /students/{student_id}/erasure': none,
  'POST /students/{student_id}/erasure': none,
  'POST /students/{student_id}/erasure/{request_id}/confirm': b(() => ({ confirm_token: '0'.repeat(64) })),
  'POST /students/{student_id}/erasure/{request_id}/cancel': none,
};

/**
 * 没有挂限流桶的路由与理由（spec 第 17 节的阈值表只列了登录、建档、学生模式、时长设置；其余由 H4 / 信任界面按需加）。
 * 这是 H6 的登记口径：新增路由要么挂桶，要么在这里写明理由。理由按库里的实际约束写（安全审查 M3）；
 * 加桶与阈值属待人确认 AA（lessons H6，必停：改路由表与 config/auth.json）。
 */
export const RATE_EXEMPT: Record<string, string> = {
  'POST /auth/logout': '只作废本会话',
  'POST /auth/logout-all': '只作废本账号的会话',
  'GET /me': '只读；会话解析本身每次请求都发生（AA：建议每会话缺省桶）',
  'POST /students/{student_id}/consents': '只追加同意记录，库里没有条数上限（AA）',
  'GET /students/{student_id}/star-map': '只读（AA）',
  'GET /students/{student_id}/onboarding': '只读（AA）',
  'PUT /students/{student_id}/onboarding/answers': '库里没有 24 小时写入上限，改答案只追加历史行（AA-1：建议加桶，必停）',
  'POST /students/{student_id}/onboarding/exams': '库里 24 小时写入上限（0022：每个录入方 50 条）',
  'PUT /students/{student_id}/onboarding/textbooks': '按（学生、学科、填写账号）upsert，行数有界，但没有频率上限（AA）',
  'POST /students/{student_id}/onboarding/progress': '库里 24 小时写入上限（0022：每个账号 500 条）',
  'POST /students/{student_id}/events': '库里 24 小时写入上限（0025）',
  'POST /students/{student_id}/probes': '库里 24 小时写入上限（0025）',
  'POST /students/{student_id}/probes/{probe_id}/attempt': '每个探针只能关联一次作答（0025 confidence_probe_attempt 唯一）',
  'POST /students/{student_id}/diagnosis/sessions': '消耗类：额度、单日上限与宵禁由 meter_reserve 卡；续做与失败释放后可无限重来（AA：建议每学生每小时桶）',
  'GET /students/{student_id}/diagnosis/sessions/{session_id}': '只认学生本人（AA：建议每会话缺省桶）',
  'POST /students/{student_id}/diagnosis/sessions/{session_id}/probe': '同一步只认第一次探针（派生键），但没有频率上限（AA）',
  'POST /students/{student_id}/diagnosis/sessions/{session_id}/answer': '按会话与序号幂等，但没有频率上限（AA）',
  'POST /students/{student_id}/diagnosis/sessions/{session_id}/pause': '按会话幂等（AA）',
  'POST /students/{student_id}/diagnosis/sessions/{session_id}/abandon': '按会话幂等（AA）',
  'GET /students/{student_id}/enrollments': '只读（AA）',
  'POST /students/{student_id}/enrollments/{subject}': '换科次数由 enroll_subject 卡（change_limit_reached），没有频率上限（AA）',
  'POST /students/{student_id}/enrollments/{subject}/pause': '同上（AA）',
  'GET /students/{student_id}/balance': '只读（AA）',
  'GET /students/{student_id}/time-guard': '只读（AA）',
  'GET /students/{student_id}/rest-reminder': '进程内计时，只读（AA）',
  'POST /students/{student_id}/rest-reminder/ack': '进程内计时（AA）',
};

// ---------------------------------------------------------------- 假的学习服务与洞察服务（计数：越权请求不能到达）

const calls = { plan: [] as string[], diagnosis: [] as string[], starts: [] as { student: string; usage: string; key: string }[], dispute: 0, exams: 0 };
const plan: PlanClient = {
  async plan(studentRef: string, subjects: PlanSubject[]): Promise<PlanSection[]> {
    calls.plan.push(studentRef);
    return subjects.map((subject) => ({ subject, status: 'ok' as const, tasks: [] }));
  },
};
/** 诊断开始：同一个幂等键第二次来时按学习服务的语义重放（同一会话、created false、replayed true） */
const sessionsByKey = new Map<string, string>();
const okStart = (usage: string, key: string): DiagOutcome<StartBody> => {
  const seen = sessionsByKey.get(key);
  const sid = seen ?? randomUUID();
  sessionsByKey.set(key, sid);
  return { kind: 'ok', status: seen ? 200 : 201, body: { session_id: sid, usage_id: usage, status: 'active', created: !seen }, replayed: seen !== undefined };
};
let diagnosisStart: (usage: string, key: string) => DiagOutcome<StartBody> = okStart;
const failure = { kind: 'failure' as const, status: 422, problem: { code: 'INVALID', retryable: false } };
const learning: DiagnosisLearning = {
  async start(student, _subject, usage, key) {
    calls.diagnosis.push(`start:${student}`);
    calls.starts.push({ student, usage, key });
    return diagnosisStart(usage, key);
  },
  async current(student) { calls.diagnosis.push(`current:${student}`); return failure; },
  async probe(student) { calls.diagnosis.push(`probe:${student}`); return failure; },
  async answer(student) { calls.diagnosis.push(`answer:${student}`); return failure; },
  async pause(student) { calls.diagnosis.push(`pause:${student}`); return failure; },
  async abandon(student) { calls.diagnosis.push(`abandon:${student}`); return failure; },
};
const dispute: DisputeClient = { async dispute(): Promise<DisputeOutcome> { calls.dispute++; return { kind: 'unavailable' }; } };
const exams: ExamViewClient = {
  async prediction() { calls.exams++; return null; },
  async reconciliation() { calls.exams++; return null; },
};
const callCount = () => calls.plan.length + calls.diagnosis.length + calls.dispute + calls.exams;

// ---------------------------------------------------------------- 合成家庭、账号与二级资源

let h: Harness;
let shell: ShellHarness;
let originalAuthNow = '';
/** F：家长 gF，两个孩子（S 与兄妹 S2）；O：另一户家长 gO 与孩子 T */
const A = { gF: '', gO: '', gU: '', gR: '', gD2: '', gX: '', S: '', aS: '', S2: '', aS2: '', T: '', aT: '' };
/** 路径参数名 → 二级对象 ID（另带导出的一次性下载凭据） */
type Ids = Partial<Record<'exam_id' | 'claim_id' | 'export_job_id' | 'request_id' | 'session_id' | 'probe_id' | 'download_ref', string>>;
const SEC: Record<'S' | 'S2' | 'T', Ids> = { S: {}, S2: {}, T: {} };
const sess = (account: string) => shell.sessionFor(account);

interface Res { status: number; headers: Headers; text: string; body: any }
const stable = (r: Res) => Object.fromEntries([...r.headers.entries()].filter(([k]) => k !== 'date'));
/** 状态、响应体、响应头（去掉 date）逐字节相同 */
const same = (a: Res, b: Res, what: string) => {
  assert.equal(a.status, b.status, `${what}：${a.text} ≠ ${b.text}`);
  assert.equal(a.text, b.text, what);
  assert.deepEqual(stable(a), stable(b), what);
};
/** 授权层面的拒绝（阳性对照不能是这些） */
const DENIALS = new Set<string>([P.UNAUTHENTICATED, P.NOT_FOUND, P.FORBIDDEN, P.CSRF]);

const ALLOWED_PROBLEM_KEYS = new Set(['type', 'title', 'status', 'code', 'reason']);
/** 第二轮 7.6 / P3：每个 BFF 响应都 no-store、nosniff、JSON；没有 CORS 放行头、cookie、请求 ID、服务器标识；错误体只有错误码 */
function checkHeaders(r: Res, what: string) {
  assert.equal(r.headers.get('cache-control'), 'no-store', `${what} no-store`);
  assert.equal(r.headers.get('x-content-type-options'), 'nosniff', `${what} nosniff`);
  assert.match(r.headers.get('content-type') ?? '', /^application\/(problem\+)?json$/, `${what} content-type`);
  for (const k of r.headers.keys()) {
    assert.ok(!k.startsWith('access-control-'), `${what} 没有 CORS 头（${k}）`);
    assert.ok(!['set-cookie', 'x-powered-by', 'server', 'x-request-id'].includes(k), `${what} 没有 ${k}`);
  }
  if (r.status >= 400) {
    assert.ok(r.body && typeof r.body === 'object', `${what} 错误体是 JSON`);
    for (const k of Object.keys(r.body)) assert.ok(ALLOWED_PROBLEM_KEYS.has(k), `${what} 错误体只有错误码（多了 ${k}）`);
    assert.ok(!/\bat \S+\.ts:|SELECT |ERROR:|postgres|127\.0\.0\.1|\/home\//i.test(r.text), `${what} 错误体不回显堆栈、SQL、内部地址`);
  }
}

/** 统一入口：每个响应都过安全头检查 */
async function call(method: string, path: string, who: Sess | null, body?: unknown, o: Parameters<typeof callAs>[5] = {}): Promise<Res> {
  const r = await callAs(shell.base, method, path, who, body, o);
  checkHeaders(r, `${method} ${path}`);
  return r;
}

/** 按路由模板生成路径：student_id 取给定值，学科码取 math（可换），其他参数取 ids 里的二级 ID，没有就是不存在的 UUID */
const pathFor = (r: Route, student: string, ids: Ids = {}, subject = 'math') =>
  r.path.replace('{student_id}', student).replace('{subject}', subject)
    .replace(/\{([a-z_]+)\}/g, (_, n: string) => ids[n as keyof Ids] ?? MISSING);
const bodyFor = (r: Route, ids: Ids = {}): Body | undefined => SAMPLES[keyOf(r)]?.body?.(ids);
/** “不是本人”的拒绝：只认学生本人的路由按接口定义的 x-sb-not-self，其余一律 404 */
const denialOf = (r: Route): string => (opOf(r).notSelf === 403 ? P.FORBIDDEN : P.NOT_FOUND);

async function account(kind: 'guardian' | 'student'): Promise<string> {
  const id = randomUUID();
  await h.su.query(`INSERT INTO platform.account (id, kind) VALUES ($1, $2)`, [id, kind]);
  return id;
}
const link = (g: string, s: string) => h.su.query(
  `INSERT INTO platform.guardian_student_link (guardian_account_id, student_id, granted_via) VALUES ($1, $2, 'invite')`, [g, s]);

/** 只在测试库里造数据：复制模式（跳过触发器与外键）直接写只有服务才能写的行 */
async function replica(sql: string, params: unknown[]): Promise<void> {
  const c = await h.su.connect();
  try {
    await c.query('BEGIN');
    await c.query('SET LOCAL session_replication_role = replica');
    await c.query(sql, params);
    await c.query('COMMIT');
  } catch (e) {
    await c.query('ROLLBACK').catch(() => undefined);
    throw e;
  } finally {
    c.release(true);
  }
}

/** 给一个孩子造全部二级资源：判断、考试安排、导出任务、删除申请、诊断会话、探针 */
async function secondary(student: string, guardian: string, opts: { erasure: boolean; diagnosis: boolean }): Promise<Ids> {
  const g = await sess(guardian);
  const ids: Ids = {};
  ids.claim_id = randomUUID();
  await replica(
    `INSERT INTO insight.claim (id, student_id, subject, target_type, target_id, template_key, template_version, confidence,
                                source_type, status, change_reason, visibility, expires_at, basis_kind, basis_id, model_version, evidence_ids, created_at, updated_at)
     VALUES ($1, $2, 'aya', 'node', 'MATH.TRU.T1.A', $3, 1, 0.7, 'inferred', 'established', 'created', ARRAY['agent', 'student', 'guardian'],
             now() + interval '30 days', 'mastery_estimate', gen_random_uuid(), 'algo:sie-claims@0.1.0', ARRAY[gen_random_uuid()], now(), now())`,
    [ids.claim_id, student, TPL]);
  const exam = await callAs(shell.base, 'POST', `/students/${student}/exams`, g,
    { subject: 'aya', exam_type: 'monthly', exam_date: dayPlus(20), full_score: 100, scope_node_ids: ['MATH.TRU.T1.A'] });
  assert.equal(exam.status, 201, exam.text);
  ids.exam_id = exam.body.exam_id;
  const ex = await callAs(shell.base, 'POST', `/students/${student}/exports`, g);
  assert.equal(ex.status, 201, ex.text);
  ids.export_job_id = ex.body.export_job_id;
  ids.download_ref = ex.body.download_ref;
  if (opts.erasure) {
    const er = await callAs(shell.base, 'POST', `/students/${student}/erasure`, g);
    assert.equal(er.status, 201, er.text);
    ids.request_id = er.body.request_id;
  }
  if (opts.diagnosis) {
    const { rows } = await h.su.query(
      `INSERT INTO platform.diagnosis_session (student_id, subject, algorithm_version, map_version, usage_id, idempotency_key)
       VALUES ($1, 'aya', 'h6-synthetic@1', '0.1.0', gen_random_uuid(), gen_random_uuid()) RETURNING id`, [student]);
    ids.session_id = rows[0].id;
  }
  ids.probe_id = randomUUID();
  await replica(`INSERT INTO insight.confidence_probe (id, student_id, subject, item_id, prediction, asked_at, idempotency_key)
                 VALUES ($1, $2, 'zhixing', 'SYN-ZX-ITEM-01', 'unsure', now(), gen_random_uuid())`, [ids.probe_id, student]);
  return ids;
}

before(async () => {
  if (!HAS_DB) return;
  h = await setupHarness();
  // 会话时钟：把 platform.auth_now() 换成读 h6test.clock 的版本（只在测试库里），结束时恢复原定义
  originalAuthNow = (await h.su.query(`SELECT pg_get_functiondef('platform.auth_now()'::regprocedure) AS d`)).rows[0].d;
  await h.su.query(`CREATE SCHEMA IF NOT EXISTS h6test;
    GRANT USAGE ON SCHEMA h6test TO starbuddy_authz;
    CREATE TABLE IF NOT EXISTS h6test.clock (now timestamptz NOT NULL);
    GRANT SELECT ON h6test.clock TO starbuddy_authz;`);
  await h.su.query(`DELETE FROM h6test.clock; INSERT INTO h6test.clock SELECT platform.auth_now();`);
  await h.su.query(`CREATE OR REPLACE FUNCTION platform.auth_now() RETURNS timestamptz
    LANGUAGE sql VOLATILE SET search_path = pg_catalog AS $f$ SELECT now FROM h6test.clock $f$;`);
  await h.su.query(`INSERT INTO insight.claim_template (template_key, version, target_type, max_visibility, max_ttl, text_template, status, reviewed_by, reviewed_at)
                    VALUES ($1, 1, 'node', ARRAY['agent', 'student', 'guardian'], interval '60 days', '（合成模板）', 'approved', 'synthetic', now())
                    ON CONFLICT DO NOTHING`, [TPL]);
  const F = await createFamily(h, { source: 'pilot_virtual', children: 2 });
  const O = await createFamily(h, { source: 'pilot_virtual' });
  Object.assign(A, { gF: F.guardian, S: F.students[0]!.id, aS: F.students[0]!.account, S2: F.students[1]!.id, aS2: F.students[1]!.account,
                     gO: O.guardian, T: O.students[0]!.id, aT: O.students[0]!.account });
  A.gU = await account('guardian');   // 没有绑定任何孩子
  A.gR = await account('guardian');   // 曾绑定 S，拿到会话后解除
  A.gD2 = await account('guardian');  // 与 gF 同号的第二个家长账号（拿到会话后把号码改成 gF 的）
  A.gX = await account('guardian');   // 绑定 S，拿到会话后停用
  await link(A.gR, A.S);
  await link(A.gX, A.S);
  shell = await startShell(h.bff, h.su, {
    log: () => undefined, now: () => new Date(FIXED_NOW),
    shell: { plan }, diagnosis: { learning, retryDelaysMs: [1] },
    trust: { features: new Set(TRUST_FEATURES), dispute, exams, exports: new MemoryExportStore() },
  });
  // 会话都在改状态之前拿到（学生会话经 gF 打开学生模式）
  for (const a of [A.gF, A.gO, A.gU, A.gR, A.gD2, A.gX, A.aS, A.aS2, A.aT]) await sess(a);
  // S 与 T 选数学：今日计划才会调学习服务（注入用例核对转发的学生）
  for (const [g, s] of [[A.gF, A.S], [A.gO, A.T]] as const) {
    const e = await callAs(shell.base, 'POST', `/students/${s}/enrollments/math`, await sess(g));
    assert.equal(e.body?.outcome, 'enrolled', e.text);
  }
  // 二级资源：S、T 全套；兄妹 S2 不造删除申请与诊断会话（计量用例要它没有进行中的诊断）
  SEC.S = await secondary(A.S, A.gF, { erasure: true, diagnosis: true });
  SEC.T = await secondary(A.T, A.gO, { erasure: true, diagnosis: true });
  SEC.S2 = await secondary(A.S2, A.gF, { erasure: false, diagnosis: false });
  await h.su.query(`UPDATE platform.guardian_student_link SET status = 'revoked' WHERE guardian_account_id = $1`, [A.gR]);
});

after(async () => {
  if (!HAS_DB) return;
  if (originalAuthNow) await h.su.query(originalAuthNow);
  await shell.close();
  await h.close();
});

// ---------------------------------------------------------------- 7.1 路由表覆盖

test('H6 路由表覆盖：开关全开的每条路由都有合成样例；谁能用与接口定义的 x-sb-auth 一致；每条路由要么挂限流桶、要么登记豁免理由', () => {
  const keys = ALL.map(keyOf).sort();
  assert.deepEqual(Object.keys(SAMPLES).sort(), keys, '新增路由必须在 SAMPLES 里登记（授权矩阵从路由表生成）');
  for (const r of ALL) {
    const k = keyOf(r);
    const exempt = RATE_EXEMPT[k];
    assert.ok((r.rateLimits.length > 0) !== (exempt !== undefined), `${k}：挂了限流桶与登记豁免必须恰好其一`);
    assert.equal(r.body, SAMPLES[k]!.body !== undefined, `${k}：样例有没有请求体与路由一致`);
    const op = opOf(r);
    assert.ok(op, `${k} 不在 openapi/*.v0.yaml 里`);
    assert.equal(r.auth, op.auth, `${k}：路由表的 auth 与接口定义的 x-sb-auth（${op.file}）一致`);
    if (r.auth === 'student' && r.path.includes('{student_id}')) assert.ok(op.notSelf === 403 || op.notSelf === 404, `${k}：x-sb-not-self`);
  }
  for (const k of Object.keys(RATE_EXEMPT)) assert.ok(keys.includes(k), `豁免表里的 ${k} 不在路由表里`);
  // 开关全开的集合 = 固定路由表 + 信任界面路由；缺省部署只有固定路由表
  assert.equal(activeRoutes().length, ROUTES.length);
  assert.ok(ALL.length > ROUTES.length);
});

// ---------------------------------------------------------------- 7.1 授权矩阵

test('H6 授权矩阵（从路由表生成）：未登录 401；账号类型不对 403；写路由缺 CSRF / 错 Origin / 别人会话的 CSRF 403；跨户、未绑定、解除绑定、同号第二账号、不存在、非 UUID 同一个拒绝', { skip: !HAS_DB }, async () => {
  const [gF, gO, gU, gR, gD2, aS, aS2, aT] = await Promise.all([A.gF, A.gO, A.gU, A.gR, A.gD2, A.aS, A.aS2, A.aT].map(sess));
  // 同号第二账号：号码改成与 gF 相同（授权不看手机号，第一轮 C1 / C2 的第 3 层）
  const { rows: [{ phone }] } = await h.su.query(`SELECT phone_e164 AS phone FROM platform.account_contact WHERE account_id = $1`, [A.gF]);
  const { rows: [{ phone: own }] } = await h.su.query(`SELECT phone_e164 AS phone FROM platform.account_contact WHERE account_id = $1`, [A.gD2]);
  await h.su.query(`UPDATE platform.account_contact SET phone_e164 = $2 WHERE account_id = $1`, [A.gD2, phone]);
  const before = callCount();
  const { rows: [{ n: reservations0 }] } = await h.su.query(`SELECT count(*)::int AS n FROM platform.usage_reservation`);
  try {
    for (const r of ALL) {
      const k = keyOf(r);
      // S 的真实二级 ID：越权被拒不是因为二级对象不存在
      const path = pathFor(r, A.S, SEC.S);
      const body = bodyFor(r, SEC.S);
      if (r.auth === 'public') continue;
      const anon = await call(r.method, path, null, body);
      assert.equal(anon.status, 401, `未登录 ${k}`);
      assert.equal(anon.text, P.UNAUTHENTICATED, `未登录 ${k}`);
      same(await call(r.method, path, { token: 'f'.repeat(64), csrf: '' }, body), anon, `伪造令牌 ${k}`);
      // 账号类型：学生会话调家长路由、家长会话调学生路由，与路径里是谁无关，全表同一个 403
      if (r.auth === 'guardian' || r.auth === 'student') {
        const wrong = r.auth === 'guardian' ? aS : gF;
        for (const target of [A.S, A.T, MISSING]) {
          const x = await call(r.method, pathFor(r, target, SEC.S), wrong, body);
          assert.equal(x.status, 403, `账号类型不对 ${k} → ${target}`);
          assert.equal(x.text, P.FORBIDDEN, `账号类型不对 ${k}`);
        }
      }
      // CSRF：本人会话（能通过 auth）缺令牌、错 Origin、没有 Origin、别的会话的令牌都同一个 403
      if (r.method !== 'GET') {
        const me = r.auth === 'student' ? aS : gF;
        const noCsrf = await call(r.method, path, me, body, { csrf: null });
        assert.equal(noCsrf.status, 403, `缺 CSRF ${k}`);
        assert.equal(noCsrf.text, P.CSRF, `缺 CSRF ${k}`);
        same(await call(r.method, path, me, body, { origin: 'http://evil.test' }), noCsrf, `错 Origin ${k}`);
        same(await call(r.method, path, me, body, { origin: null }), noCsrf, `没有 Origin ${k}`);
        same(await call(r.method, path, me, body, { origin: ORIGIN.replace('http:', 'https:') }), noCsrf, `Origin 协议不同 ${k}`);
        same(await call(r.method, path, me, body, { csrf: (r.auth === 'student' ? aS2 : gO).csrf }), noCsrf, `别的会话的 CSRF ${k}`);
      }
      if (!r.path.includes('{student_id}')) continue;
      const deny = denialOf(r);
      const badId = r.path.replace('{student_id}', 'not-a-uuid').replace('{subject}', 'math').replace(/\{[a-z_]+\}/g, MISSING);
      if (r.auth === 'student') {
        // 只认学生本人的接口：兄妹、另一户学生、不存在同一个拒绝，状态码按接口定义的 x-sb-not-self（契约审查 M3）
        const missing = await call(r.method, pathFor(r, MISSING), aS2, body);
        assert.equal(missing.text, deny, `不存在 ${k}（x-sb-not-self ${opOf(r).notSelf}）`);
        same(await call(r.method, path, aS2, body), missing, `兄妹 ${k}`);
        same(await call(r.method, path, aT, body), missing, `另一户学生 ${k}`);
        assert.equal((await call(r.method, badId, aS2, body)).text, P.NOT_FOUND, `非 UUID ${k}`);
        continue;
      }
      const missing = await call(r.method, pathFor(r, MISSING), gO, body);
      assert.equal(missing.status, 404, `不存在 ${k}：${missing.text}`);
      assert.equal(missing.text, P.NOT_FOUND, `不存在 ${k}`);
      same(await call(r.method, path, gO, body), missing, `另一户家长 ${k}`);
      same(await call(r.method, path, gU, body), missing, `没有绑定的家长 ${k}`);
      same(await call(r.method, path, gR, body), missing, `已解除绑定的家长（旧会话）${k}`);
      same(await call(r.method, path, gD2, body), missing, `与 gF 同号的第二个家长账号 ${k}`);
      same(await call(r.method, badId, gO, body), missing, `非 UUID ${k}`);
      same(await call(r.method, pathFor(r, A.S.toUpperCase(), SEC.S), gF, body), missing, `大写 UUID 不是同一个学生 ${k}`);
      if (r.auth === 'session') {
        same(await call(r.method, path, aS2, body), missing, `兄妹的学生会话 ${k}`);
        same(await call(r.method, path, aT, body), missing, `另一户的学生会话 ${k}`);
        same(await call(r.method, pathFor(r, MISSING), aS, body), missing, `学生会话 + 不存在 ${k}`);
      }
    }
  } finally {
    await h.su.query(`UPDATE platform.account_contact SET phone_e164 = $2 WHERE account_id = $1`, [A.gD2, own]);
  }
  assert.equal(callCount(), before, '没有一次越权请求到达学习服务或洞察服务');
  const { rows: [{ n: reservations1 }] } = await h.su.query(`SELECT count(*)::int AS n FROM platform.usage_reservation`);
  assert.equal(reservations1, reservations0, '越权请求没有预留任何额度');
});

test('H6 跨对象：本人用自己孩子的路径带另一户（T）或兄妹（S2）的二级 ID，与不存在的二级 ID 逐字节相同', { skip: !HAS_DB }, async () => {
  const [gF, aS] = await Promise.all([A.gF, A.aS].map(sess));
  let checked = 0;
  for (const r of ALL.filter((x) => x.path.includes('{student_id}') && /\{(?!student_id|subject)[a-z_]+\}/.test(x.path))) {
    const k = keyOf(r);
    const me = r.auth === 'student' ? aS : gF;
    for (const [what, other] of [['另一户 T', SEC.T], ['兄妹 S2', SEC.S2]] as const) {
      // 不存在的二级 ID（请求体取同一份，例如导出下载用对方的凭据，只差路径里的任务 ID）
      const missing = await call(r.method, pathFor(r, A.S), me, bodyFor(r, other));
      const x = await call(r.method, pathFor(r, A.S, other), me, bodyFor(r, other));
      // 预测与对账卡的响应回显路径里的考试编号：换成同一个占位后比较（回显的是请求方自己给的编号，不是别人的数据）
      const echo = (t: string) => Object.values(other).reduce((acc, id) => (id ? acc.split(id).join(MISSING) : acc), t);
      same({ ...x, text: echo(x.text) }, missing, `跨对象（${what} 的二级 ID）${k}`);
      checked++;
    }
  }
  assert.ok(checked >= 20, `跨对象用例数 ${checked}`);
});

test('H6 阳性对照：本人用真实二级 ID 的同一请求能通过授权（不是 401 / 404 / 403 FORBIDDEN / CSRF），矩阵里的拒绝不是空转', { skip: !HAS_DB }, async () => {
  const [gF, aS, gU] = await Promise.all([A.gF, A.aS, A.gU].map(sess));
  const throwaway = await account('guardian');
  await shell.sessionFor(throwaway);  // 生成号码
  shell.forget(throwaway);
  for (const r of ALL.filter((x) => x.auth !== 'public')) {
    const k = keyOf(r);
    const owners: Sess[] = r.auth === 'student' ? [aS] : r.auth === 'guardian' ? [gF] : [gF, aS];
    for (const owner of owners) {
      let who = owner;
      let subject = 'math';
      if (k === 'POST /auth/logout-all') {
        who = await login(throwaway);  // 退出所有设备：另一个家长（不能作废 gF 的会话）
      } else if (k === 'POST /auth/logout' || k === 'POST /students/{student_id}/student-session') {
        who = await login(A.gF);  // 会作废会话的路由：用一次性的新会话
      } else if (k === 'POST /students') {
        who = gU;                 // 建档：没有孩子的家长建一个（不改 F 家庭的孩子列表）
      } else if (k.includes('/enrollments/{subject}')) {
        subject = 'politics';     // 选科与暂停：用道法，不动 S 在读的数学（今日计划用例要它）
      }
      let ids = SEC.S;
      if (k === 'POST /students/{student_id}/exports/{export_job_id}/download') {
        // 下载凭据一次性、同一学生新建导出会替换暂存：现建一份再兑换
        const ex = await call('POST', `/students/${A.S}/exports`, gF);
        assert.equal(ex.status, 201, ex.text);
        ids = { ...SEC.S, export_job_id: ex.body.export_job_id, download_ref: ex.body.download_ref };
      }
      const x = await call(r.method, pathFor(r, A.S, ids, subject), who, bodyFor(r, ids));
      assert.ok(x.status !== 401 && !DENIALS.has(x.text), `阳性对照 ${k}（${who === aS ? '学生本人' : '家长'}）：${x.status} ${x.text}`);
    }
  }
});

test('H6 表外：服务器能响应但不在路由表里的路径为 0——尾斜杠、多余段、大小写、版本号、双斜杠一律与表外同一个 404；表内路径的其他方法 405、没有 CORS 头', { skip: !HAS_DB }, async () => {
  const gF = await sess(A.gF);
  const outside = await call('GET', '/bff/v0/definitely-not-a-route', gF);
  assert.equal(outside.text, P.NOT_FOUND);
  const variants = (p: string) => [`${p}/`, `${p}/x-1`, p.replace('/bff/v0', '/bff/V0'), p.replace('/bff/v0', '/bff/v1'), p.replace('/bff/v0/', '/bff/v0//'),
                                   p.replace('/bff/v0', '/bff'), `${p}%2F..`, `${p.toUpperCase()}`];
  const table = compile(ALL);
  for (const r of ALL) {
    const p = `${PREFIX}${pathFor(r, A.S)}`;
    for (const v of variants(p)) {
      if (table.some((c) => c.re.test(v))) continue;  // 恰好是另一条路由的合法路径（例如学科码）
      same(await call(r.method, v, gF, bodyFor(r)), outside, `${r.method} ${v}`);
    }
    const declared = ALL.filter((x) => x.path === r.path).map((x) => x.method as string);
    for (const m of ['GET', 'POST', 'PUT', 'DELETE', 'PATCH', 'OPTIONS'].filter((x) => !declared.includes(x))) {
      const x = await call(m, p, gF, m === 'GET' || m === 'OPTIONS' ? undefined : {});
      assert.equal(x.status, 405, `${m} ${r.path}`);
      assert.equal(x.text, P.METHOD_NOT_ALLOWED);
    }
  }
});

// ---------------------------------------------------------------- 7.2 身份注入

const INJECT_QUERY = (victim: { account: string; student: string }) =>
  `account_id=${victim.account}&student_id=${victim.student}&guardian_account_id=${victim.account}&link_id=${MISSING}&role=guardian&account_kind=guardian&as=${victim.account}`;
const INJECT_HEADERS = (victim: { account: string; student: string }) => ({
  'x-account-id': victim.account, 'x-dev-account-id': victim.account, 'x-student-id': victim.student, 'x-link-id': MISSING,
  'x-role': 'guardian', 'x-account-kind': 'guardian', 'x-forwarded-user': victim.account, 'x-sb-account': victim.account,
  'x-sb-client-ip': '10.0.0.1', authorization: `Bearer ${victim.account}`,
});
const INJECT_BODY = (victim: { account: string; student: string }) => ({
  account_id: victim.account, student_id: victim.student, guardian_account_id: victim.account, link_id: MISSING, role: 'guardian', account_kind: 'guardian',
});
const withQuery = (p: string, q: string) => `${p}${p.includes('?') ? '&' : '?'}${q}`;

test('H6 身份注入：每条路由在查询参数、请求头、请求体里注入别人的 account / student / link / role，结果与不注入时相同（只认会话）', { skip: !HAS_DB }, async () => {
  const [gF, gO, aS, aS2] = await Promise.all([A.gF, A.gO, A.aS, A.aS2].map(sess));
  const victimOfGo = { account: A.gF, student: A.S };   // gO 冒充 gF 读 S
  const planBefore = calls.plan.length;
  for (const r of ALL) {
    const k = keyOf(r);
    if (r.auth === 'public') continue;
    const path = pathFor(r, A.S, SEC.S);
    const body = bodyFor(r, SEC.S);
    // 1. 没有会话：注入身份仍是未登录
    const anon = await call(r.method, path, null, body);
    same(await call(r.method, withQuery(path, INJECT_QUERY(victimOfGo)), null, body, { headers: INJECT_HEADERS(victimOfGo) }), anon, `未登录 + 注入 ${k}`);
    // 2. 账号类型：学生会话自称家长、家长会话自称学生，仍是同一个 403
    if (r.auth === 'guardian' || r.auth === 'student') {
      const wrong = r.auth === 'guardian' ? aS : gF;
      const plain = await call(r.method, path, wrong, body);
      same(await call(r.method, withQuery(path, INJECT_QUERY({ account: r.auth === 'guardian' ? A.gF : A.aS, student: A.S })), wrong, body,
                      { headers: { ...INJECT_HEADERS({ account: r.auth === 'guardian' ? A.gF : A.aS, student: A.S }), 'x-account-kind': r.auth } }), plain, `自称别的账号类型 ${k}`);
    }
    if (!r.path.includes('{student_id}')) continue;
    // 3. 越权的会话注入受害者身份：结果与不注入相同（仍是拒绝）
    const actor = r.auth === 'student' ? aS2 : gO;
    const victim = r.auth === 'student' ? { account: A.aS, student: A.S } : victimOfGo;
    const plain = await call(r.method, path, actor, body);
    assert.equal(plain.text, denialOf(r), `${k}：${plain.status}`);
    same(await call(r.method, withQuery(path, INJECT_QUERY(victim)), actor, body, { headers: INJECT_HEADERS(victim) }), plain, `查询参数 + 请求头注入 ${k}`);
    if (r.body) {
      // 请求体注入（H1 待人确认 C 的口径）：未知键一律 422 或被忽略（与不注入相同）；无论哪种都不能变成成功
      const x = await call(r.method, path, actor, { ...body, ...INJECT_BODY(victim) });
      assert.ok(x.text === P.INVALID || (x.status === plain.status && x.text === plain.text), `请求体注入 ${k}：${x.status} ${x.text}`);
    }
  }
  // 4. 本人读路由：注入别人的身份后读到的仍是自己的数据（逐字节相同）
  for (const r of ALL.filter((x) => x.method === 'GET' && x.auth !== 'public')) {
    const me = r.auth === 'student' ? aS : gF;
    const plain = await call(r.method, pathFor(r, A.S, SEC.S), me);
    const injected = await call(r.method, withQuery(pathFor(r, A.S, SEC.S), INJECT_QUERY({ account: A.gO, student: A.T })), me, undefined,
                                { headers: INJECT_HEADERS({ account: A.gO, student: A.T }) });
    same(injected, plain, `本人 GET + 注入另一户 ${keyOf(r)}`);
  }
  // /me 与 /me + 注入：仍是 gF 的孩子列表
  const me = await call('GET', withQuery('/me', INJECT_QUERY({ account: A.gO, student: A.T })), gF, undefined, { headers: INJECT_HEADERS({ account: A.gO, student: A.T }) });
  assert.deepEqual(me.body.children.map((c: { student_id: string }) => c.student_id).sort(), [A.S, A.S2].sort());
  // 今日计划：学习服务收到的学生只来自路径（且已过 RLS），注入的学生没有被转发
  const planned = calls.plan.slice(planBefore);
  assert.ok(planned.length > 0 && planned.every((x) => x === A.S), `今日计划只为 S 排：${planned.join(',')}`);
});

// ---------------------------------------------------------------- 7.3 连接池串号

test('H6 连接池串号：两个连接、两户的家长与学生 × 6 条读路由 × 240 次交替并发，每个响应都等于本账号的基线；成功与出错的事务之后连接上都没有残留 app.account_id', { skip: !HAS_DB }, async () => {
  // 专门的小连接池（2 个连接）：保证不同账号的请求一定复用同一批连接
  const small = new pg.Pool({ user: 'bff_app', max: 2 });
  const tiny = await startShell(small, h.su, { log: () => undefined, now: () => new Date(FIXED_NOW), shell: { plan } });
  try {
    const who = [{ account: A.gF, student: A.S }, { account: A.gO, student: A.T }, { account: A.aS, student: A.S }, { account: A.aT, student: A.T }];
    const reads = ['/me', '/students/{s}/enrollments', '/students/{s}/balance', '/students/{s}/time-guard', '/students/{s}/star-map', '/students/{s}/onboarding'];
    const sessions = new Map<string, Sess>();
    for (const w of who) sessions.set(w.account, await tiny.sessionFor(w.account));
    const get = (w: { account: string; student: string }, p: string) => callAs(tiny.base, 'GET', p.replace('{s}', w.student), sessions.get(w.account)!);
    const base = new Map<string, string>();
    for (const w of who) for (const p of reads) base.set(`${w.account} ${p}`, (await get(w, p)).text);
    // 家长 gF 与 gO 的基线确实不同（否则串号测不出来）
    assert.notEqual(base.get(`${A.gF} /me`), base.get(`${A.gO} /me`));
    assert.notEqual(base.get(`${A.gF} /students/{s}/time-guard`), base.get(`${A.gO} /students/{s}/time-guard`));
    const jobs = Array.from({ length: 240 }, (_, i) => ({ w: who[i % who.length]!, p: reads[Math.floor(i / who.length) % reads.length]! }));
    const results = await Promise.all(jobs.map(async (j) => ({ j, r: await get(j.w, j.p) })));
    for (const { j, r } of results) assert.equal(r.text, base.get(`${j.w.account} ${j.p}`), `串号：${j.w.account} ${j.p}`);
    const clean = async (what: string) => {
      const clients = await Promise.all([small.connect(), small.connect()]);
      try {
        for (const c of clients) {
          const { rows } = await c.query(`SELECT coalesce(current_setting('app.account_id', true), '') AS v, platform.current_account_id() AS cur`);
          assert.deepEqual(rows[0], { v: '', cur: null }, `${what}：连接上没有残留账号`);
        }
      } finally {
        clients.forEach((c) => c.release());
      }
    };
    await clean('成功的请求之后');
    // 故意让事务出错：经 HTTP（库里拦下的过期事件时间，事务回滚）与直接调 withAccount（SQL 报错）
    const st = sessions.get(A.aS)!;
    const bad = await callAs(tiny.base, 'POST', `/students/${A.S}/events`, st,
      { kind: 'session_start', payload: { entry: 'today' }, occurred_at: new Date(Date.now() - 3 * 86400_000).toISOString(), idempotency_key: randomUUID() });
    assert.equal(bad.status, 422);
    await assert.rejects(withAccount(small, A.gF, async (c) => { await c.query('SELECT 1 / 0'); }));
    await assert.rejects(withAccount(small, A.gO, async (c) => { await c.query(`SELECT current_setting('app.account_id')`); throw new Error('handler failed'); }));
    await clean('出错的事务之后');
    // 出错之后：两户的读仍各是各的
    for (const w of who) assert.equal((await get(w, '/me')).text, base.get(`${w.account} /me`), `出错之后 ${w.account}`);
    await clean('出错后再请求');
  } finally {
    await tiny.close();
    await small.end();
  }
});

// ---------------------------------------------------------------- 7.5 限流

test('H6 限流（从路由表断言）：每个桶都在 config/auth.json、名字里的维度与窗口和配置一致；每个挂桶的路由超限后返回逐字节相同的 429，且没有调用下游、没有预留额度、没有写入', { skip: !HAS_DB }, async () => {
  const cfg = loadAuthConfig();
  const WINDOW: Record<string, number> = { minute: 60, quarter: 900, hour: 3600, day: 86400 };
  for (const r of ALL) {
    for (const l of r.rateLimits) {
      const rule = cfg.rate_limits[l.bucket];
      assert.ok(rule, `${keyOf(r)} 的限流桶 ${l.bucket} 在配置里`);
      const [, dim, win] = l.bucket.split('.');
      assert.equal(dim, l.dim, `${l.bucket} 名字里的维度`);
      assert.equal(rule.window_seconds, WINDOW[win!], `${l.bucket} 名字里的窗口`);
      assert.ok(rule.limit >= 1);
    }
    if (r.auth === 'public') assert.ok(r.rateLimits.some((l) => l.dim === 'ip'), `公开路由 ${keyOf(r)} 按 IP 限流`);
  }
  const counts = async () => {
    const { rows: [x] } = await h.su.query(`SELECT (SELECT count(*) FROM platform.usage_reservation)::int AS rsv, (SELECT count(*) FROM platform.usage_ledger)::int AS led,
                                                   (SELECT count(*) FROM platform.student_profile)::int AS stu, (SELECT count(*) FROM platform.consent_record)::int AS con,
                                                   (SELECT count(*) FROM platform.auth_session)::int AS ses, (SELECT count(*) FROM platform.auth_otp_challenge)::int AS otp`);
    return { ...x, calls: callCount(), sms: shell.sms.inbox.length };
  };
  const gO = await sess(A.gO);
  const gF = await sess(A.gF);
  const tooMany: Res[] = [];
  for (const r of ALL.filter((x) => x.rateLimits.length > 0)) {
    for (const l of r.rateLimits) {
      const saved = shell.config.rate_limits[l.bucket]!;
      // 谁来撞桶：越权的家长（handler 会拒绝，不改任何东西）；按会话限流的今日计划用本人（只读）
      const who = r.auth === 'public' ? null : keyOf(r) === 'GET /students/{student_id}/today' ? gF : r.auth === 'student' ? await sess(A.aS2) : gO;
      const body = keyOf(r) === 'POST /students' ? {} : bodyFor(r);  // 建档：撞桶用不合格的请求体（422，不建档）
      shell.config.rate_limits[l.bucket] = { window_seconds: saved.window_seconds, limit: 1 };
      try {
        await call(r.method, pathFor(r, A.S), who, body);
        const c0 = await counts();
        const x = await call(r.method, pathFor(r, A.S), who, body);
        assert.equal(x.status, 429, `${keyOf(r)} ${l.bucket}：${x.text}`);
        assert.deepEqual(await counts(), c0, `${keyOf(r)} ${l.bucket}：429 时没有调用下游、没有预留、没有写入、没有发短信`);
        tooMany.push(x);
      } finally {
        shell.config.rate_limits[l.bucket] = saved;
      }
    }
  }
  assert.ok(tooMany.length >= 15);
  for (const x of tooMany) same(x, tooMany[0]!, '所有 429 逐字节相同（不分桶、不分维度、不分原因）');
  assert.equal(tooMany[0]!.text, P.TOO_MANY);
});

// ---------------------------------------------------------------- 7.7 计量入口

const srcFiles = (dir: URL): string[] => readdirSync(dir).flatMap((n) => {
  const u = new URL(n, dir);
  return statSync(u).isDirectory() ? srcFiles(new URL(`${n}/`, dir)) : n.endsWith('.ts') ? [u.pathname] : [];
});

/** 允许调用消耗类计量函数的文件（第二轮 7.7）：新增消耗类入口要在这里登记，并补 H6 的计量用例 */
const METER_CALLERS = ['src/diagnosis/service.ts'];

test('H6 计量入口（静态）：只有登记的文件导入消耗类计量函数；诊断开始的资源类型、数量、usage_id 由 BFF 决定，请求体不收这些字段', () => {
  const METER_FNS = /\b(reserve|settle|release|meteredCall)\b/;
  const hits: string[] = [];
  for (const f of srcFiles(new URL('../src/', import.meta.url)).filter((x) => !x.includes('/src/metering/'))) {
    const text = readFileSync(f, 'utf8');
    const rel = f.slice(f.indexOf('/src/') + 1);
    // 从 metering/meter.ts 或 metering/index.ts 导入（具名导入四个函数之一，或 * as 整个模块，或动态导入）
    const imports = [...text.matchAll(/import\s+([\s\S]*?)\s+from\s+'[^']*metering\/(meter|index)\.ts'/g)].map((m) => m[1]!);
    const dynamic = /import\(\s*'[^']*metering\/(meter|index)\.ts'\s*\)/.test(text);
    const sql = /platform\.meter_(reserve|settle|release|practice|open_subscription)\b/.test(text.replace(/\/\/.*$/gm, ''));
    if (dynamic || sql || imports.some((clause) => /\*\s+as\s+\w+/.test(clause) || METER_FNS.test(clause))) hits.push(rel);
  }
  assert.deepEqual(hits.sort(), [...METER_CALLERS].sort(), '新增消耗类入口要在 METER_CALLERS 登记并补 H6 的计量用例（第二轮 7.7）');
  const svc = readFileSync(new URL('../src/diagnosis/service.ts', import.meta.url), 'utf8');
  // 预留：资源类型、数量、来源方法是常量，usage_id 由幂等键派生（前端传不进来）；结算数量固定为 1（G4）
  assert.match(svc, /reserve\(deps\.pool, account, \{ studentId: student, resourceType: 'diagnosis_full', idempotencyKey: key,\s*requestFingerprint: fingerprint, quantity: 1, sourceMethod: 'diagnose' \}\)/);
  assert.match(svc, /const usage = usageIdFor\(key\);/);
  assert.match(svc, /const s = await settle\(deps\.pool, account, usage, 1\);/, '结算数量固定为 1（G4：前端不能决定）');
  assert.equal(parseStart({ subject: 'aya', idempotency_key: randomUUID() })?.subject, 'aya');
  for (const extra of [{ usage_id: randomUUID() }, { quantity: 0 }, { resource_type: 'diagnosis_lite' }, { amount: 5 }, { student_ref: MISSING }]) {
    assert.equal(parseStart({ subject: 'aya', idempotency_key: randomUUID(), ...extra }), null, `诊断开始拒收 ${Object.keys(extra)[0]}`);
  }
});

test('H6 计量入口（集成）：前端带来的 usage_id、数量、资源类型被拒收、不预留；学习服务确定失败时释放（回滚）；正常开始只预留 1 次 diagnosis_full，usage_id 由幂等键派生、幂等键原样转发；同键再开始仍一笔预留、响应标为重放', { skip: !HAS_DB }, async () => {
  const st = await sess(A.aS2);  // 兄妹 S2：还没有诊断会话
  const path = `/students/${A.S2}/diagnosis/sessions`;
  const rows = async () => (await h.su.query(
    `SELECT usage_id::text, resource_type, reserved_qty::int AS qty, state::text, source_method FROM platform.usage_reservation WHERE student_id = $1 ORDER BY reserved_at`, [A.S2])).rows;
  const r0 = await rows();
  for (const extra of [{ usage_id: randomUUID() }, { quantity: 3 }, { resource_type: 'diagnosis_lite' }]) {
    const x = await call('POST', path, st, { subject: 'aya', idempotency_key: randomUUID(), ...extra });
    assert.equal(x.status, 422, JSON.stringify(extra));
  }
  assert.deepEqual(await rows(), r0, '拒收的请求没有预留');
  // 学习服务确定失败：预留被释放（失败回滚）
  diagnosisStart = () => failure;
  const k1 = randomUUID();
  try {
    const x = await call('POST', path, st, { subject: 'aya', idempotency_key: k1 });
    assert.ok(x.status >= 400, x.text);
  } finally {
    diagnosisStart = okStart;
  }
  const failed = (await rows()).find((r: { usage_id: string }) => r.usage_id === usageIdFor(k1));
  assert.deepEqual(failed, { usage_id: usageIdFor(k1), resource_type: 'diagnosis_full', qty: 1, state: 'released', source_method: 'diagnose' },
                   '学习服务确定失败时预留被释放');
  // 正常开始：恰好一笔、数量 1、usage_id = 由幂等键派生；查询参数与请求头里的 usage_id、幂等键被忽略
  const k2 = randomUUID();
  const startsBefore = calls.starts.length;
  const ok = await call('POST', withQuery(path, `usage_id=${MISSING}&quantity=9`), st, { subject: 'aya', idempotency_key: k2 },
                        { headers: { 'x-usage-id': MISSING, 'idempotency-key': randomUUID() } });
  assert.equal(ok.status, 201, ok.text);
  const sent = calls.starts.slice(startsBefore);
  assert.deepEqual(sent, [{ student: A.S2, usage: usageIdFor(k2), key: k2 }], '学习服务收到的 usage_id = usageIdFor(幂等键)，幂等键原样转发');
  const after = await rows();
  assert.equal(after.length, r0.length + 2);
  assert.deepEqual(after.find((r: { usage_id: string }) => r.usage_id === usageIdFor(k2)),
                   { usage_id: usageIdFor(k2), resource_type: 'diagnosis_full', qty: 1, state: 'reserved', source_method: 'diagnose' });
  assert.ok(!after.some((r: { usage_id: string }) => r.usage_id === MISSING), '查询参数与请求头里的 usage_id 被忽略');
  // 同一个幂等键再开始一次：仍是同一笔预留，响应标为重放（同一会话）
  const again = await call('POST', path, st, { subject: 'aya', idempotency_key: k2 });
  assert.equal(again.status, 200, again.text);
  assert.equal(again.headers.get('idempotent-replayed'), 'true');
  assert.equal(again.body.session_id, ok.body.session_id);
  assert.deepEqual(await rows(), after, '同键重试没有新预留');
});

// ---------------------------------------------------------------- 7.4 会话

const setAuthClock = (sql: string) => h.su.query(`UPDATE h6test.clock SET now = ${sql}`);

/** 拿这个令牌把每条要会话的路由都调一遍：一律与未登录逐字节相同的 401（登出后重放、吊销、过期、停用） */
async function sweep401(token: string, what: string) {
  const dead: Sess = { token, csrf: '' };
  for (const r of ALL.filter((x) => x.auth !== 'public')) {
    const anon = await call(r.method, pathFor(r, A.S, SEC.S), null, bodyFor(r, SEC.S));
    same(await call(r.method, pathFor(r, A.S, SEC.S), dead, bodyFor(r, SEC.S)), anon, `${what} ${keyOf(r)}`);
  }
}

/** 走登录页同样的接口登录（可以带本设备旧令牌） */
async function login(guardian: string, prev?: string): Promise<Sess> {
  const { rows: [{ phone }] } = await h.su.query(`SELECT phone_e164 AS phone FROM platform.account_contact WHERE account_id = $1`, [guardian]);
  await setAuthClock(`now + interval '61 seconds'`);  // 每号每分钟 1 次（阈值已放宽，仍按时间走）
  assert.equal((await call('POST', '/auth/otp/request', null, { phone })).status, 202);
  const code = shell.sms.latest(phone)!;
  const v = await call('POST', '/auth/otp/verify', prev ? { token: prev, csrf: '' } : null, { phone, code });
  assert.equal(v.status, 200, v.text);
  return { token: v.body.session_token, csrf: v.body.csrf_token };
}

test('H6 会话：登出后重放、退出所有设备、登录轮换、打开学生模式轮换、账号停用：旧令牌在每条路由上都是 401；跨用户的令牌与 CSRF 不能拼用', { skip: !HAS_DB }, async () => {
  const g = await account('guardian');
  // 这个家长要有号码（harness 首次登录时生成）
  await shell.sessionFor(g);
  shell.forget(g);
  // 登出后重放
  const a = await login(g);
  assert.equal((await call('POST', '/auth/logout', a)).status, 200);
  await sweep401(a.token, '登出后重放');
  // 退出所有设备：本设备与另一台设备都失效，别的账号不受影响
  const b1 = await login(g);
  const b2 = await login(g);
  assert.equal((await call('POST', '/auth/logout-all', b1)).status, 200);
  await sweep401(b1.token, '退出所有设备（本设备）');
  await sweep401(b2.token, '退出所有设备（另一台设备）');
  assert.equal((await call('GET', '/me', await sess(A.gO))).status, 200, '别的账号不受影响');
  // 登录轮换：带本设备旧令牌登录，旧令牌立即作废，令牌变化
  const c1 = await login(g);
  const c2 = await login(g, c1.token);
  assert.notEqual(c2.token, c1.token);
  assert.notEqual(c2.csrf, c1.csrf);
  await sweep401(c1.token, '登录轮换后的旧令牌');
  // 打开学生模式：家长令牌换成学生令牌，家长令牌作废
  const d = await login(A.gF);
  const st = await call('POST', `/students/${A.S}/student-session`, d);
  assert.equal(st.status, 200, st.text);
  assert.notEqual(st.body.session_token, d.token);
  await sweep401(d.token, '打开学生模式后的家长令牌');
  // 跨用户：A 的令牌配 B 的 CSRF（写路由）一律 CSRF 403；B 的令牌读不到 A 的 /me
  const mine = await login(g);
  const other = await sess(A.gO);
  for (const r of ALL.filter((x) => x.method !== 'GET' && x.auth !== 'public' && x.auth !== 'student')) {
    const x = await call(r.method, pathFor(r, A.S), { token: mine.token, csrf: other.csrf }, bodyFor(r));
    assert.equal(x.text, P.CSRF, `跨用户 CSRF ${keyOf(r)}`);
  }
  assert.equal((await call('GET', '/me', mine)).body.children.length, 0);
  // 账号停用：这个账号的会话在每条路由上都失效
  const gX = await sess(A.gX);
  await h.su.query(`UPDATE platform.account SET status = 'disabled' WHERE id = $1`, [A.gX]);
  await sweep401(gX.token, '账号停用后');
  // 令牌不进 URL：查询参数里的令牌不被认
  const q = await call('GET', `/me?x-sb-session=${mine.token}&session=${mine.token}&token=${mine.token}`, null);
  assert.equal(q.text, P.UNAUTHENTICATED, '查询参数里的令牌不被认');
});

test('H6 会话：空闲 24 小时与绝对 7 天后（注入时钟），家长与学生的令牌在每条路由上都是 401', { skip: !HAS_DB }, async () => {
  const cfg = loadAuthConfig();
  const g = await account('guardian');
  await shell.sessionFor(g);
  shell.forget(g);
  const idle = await login(g);
  const kid = await callAs(shell.base, 'POST', `/students/${A.T}/student-session`, await login(A.gO));
  assert.equal(kid.status, 200, kid.text);
  const kidSess: Sess = { token: kid.body.session_token, csrf: kid.body.csrf_token };
  assert.equal((await call('GET', '/me', kidSess)).status, 200);
  await setAuthClock(`now + make_interval(secs => ${cfg.session.guardian.idle_seconds + 1})`);
  await sweep401(idle.token, '家长空闲超时');
  await sweep401(kidSess.token, '学生空闲超时');
  // 绝对时长：一直在用（每 20 小时一次），满 7 天仍失效
  const abs = await login(g);
  const steps = Math.ceil(cfg.session.guardian.absolute_seconds / (20 * 3600)) - 1;
  for (let i = 0; i < steps; i++) {
    await setAuthClock(`now + interval '20 hours'`);
    assert.equal((await call('GET', '/me', abs)).status, 200, `一直在用：第 ${i + 1} 次`);
  }
  await setAuthClock(`now + interval '20 hours'`);
  await sweep401(abs.token, '满绝对时长');
  // 本测试把会话时钟推到了几天之后：后面没有别的测试用这些会话
  shell.forget(A.gO);
});
