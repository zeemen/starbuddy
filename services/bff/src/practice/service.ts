// 错题再练的学生端接口（specs/practice-loop-stage1/spec.md 第 5 节，PR 3）。接口定义 openapi/practice.v0.yaml。
//
// 只认学生本人（路由 auth = student：家长会话在任何查询之前就是 403）；兄妹、另一户、不存在的学生一律同一个 404
// （先经 platform.is_self_student 核对，再查宵禁、调学习服务：越权请求一次也到不了学习服务）。
// 学习服务内部接口（services/learning/openapi/practice-internal.v0.yaml）只认 BFF 的服务间令牌。
// 转给前端的只有：条目状态、轮次、下一轮时间、知识节点与层次、题面（format、stem、options）、判分结论与条目新状态。
// 不转：掌握、置信度、证据 ID、错因、原题与再练题的题目引用、复测标记、同族限定标记、学习服务票据（规则 3、11）。
// 学习服务的票据封进不透明的 sbp1 票据下发（src/practice/ticket.ts），前端读不到学生内部 ID 与题目引用。
//
// 计量（规则 4；spec 第 5 节默认 #4，迁移 0015 的题库自练，不改计量函数与额度）：
//   - 只有作答计次：platform.meter_practice(学生, 幂等键)，以学生本人身份调用；usage_id 由幂等键派生（与库里同一算法，核对一致）；
//     再用同一个 usage_id 与幂等键调学习服务。queue、next 不计次。
//   - 幂等键是票据里 BFF 封装时生成的 per-ticket UUIDv4（审查 H2），前端不带幂等键：一张票据最多扣一次。
//     同一张票据重试 → 同一个键：meter_practice 返回 existing（不再计次），学习服务按幂等返回原结果（响应头 idempotent-replayed）；
//     同一张票据换作答 → 学习服务 409 IDEMPOTENCY_CONFLICT → 409 TICKET_USED（不再扣）。
//   - 题库自练“开始即计数、没有释放”（specs/metering-v2/lessons.md 偏差 13）：计次之后学习服务拒绝（条目已变、票据已用、
//     学习服务那边已过期、结果未知）仍算一次。为了不扣冤枉次，能在本地判断的拒绝都放在计次之前：
//     请求体格式、学科码、学生本人、票据（伪造、换学生、换学科、换条目以外的篡改）、作答形状（按票据里的题型与选项键）、
//     票据过期（按票据里的过期时间，留 30 秒余量）、宵禁。
//   - 票据已过期但它的键已经计过次（上一次结果未知），照样转给学习服务找回原结果（学习服务对已提交的键放行过期票据）。
//   - no_entitlement、daily_limit_reached（留存态每天 5 次）回 402 QUOTA_UNAVAILABLE，不调学习服务。
// 宵禁（审查 B1）：取题与作答在本人核对之后、计次之前查 meter_guard_status 的 in_curfew（src/metering/curfew.ts，与诊断共用），
//   宵禁中回 402 QUOTA_UNAVAILABLE reason = curfew，不计次、不调学习服务；列表不挡（只读、不出题）。
// 学习服务调用：结果未知时同一请求重试，整体总时限 45 秒（含每次调用的超时）；确定性拒绝（501、502 ADAPTER_RESULT_REJECTED）不重试。
// 分学科并发闸门（PLT-16 第一步，src/gate/subject-gate.ts；开关关时 deps.gate 为空，直接执行原来的代码）：学科按学习服务学科码。
//   取题：宵禁检查之后包住转发；作答：宵禁检查之后、计次之前取名额（被拒回 503 SUBJECT_BUSY：本次请求没有新增计量、不调学习服务；
//   之前已计次的用同一张票据重试不再计），计次与学习服务调用在名额内，处理结束（含失败、抛错）在 finally 里释放；
//   同一张票据已计过次的重试绕过闸门（不再被拒）；列表不经闸门。
import type { Pool } from 'pg';
import { busyHeaders, withSubjectSlot, type GateObserve, type SubjectGate } from '../gate/subject-gate.ts';
import { inCurfew } from '../metering/curfew.ts';
import { withAccount } from '../metering/db.ts';
import { usageIdFor } from '../metering/uuid.ts';
import { P } from '../http/problems.ts';
import type { Reply } from '../http/routes.ts';
import { pickRich, type RichItem } from '../item-rich.ts';
import type { PracticeLearning, PracticeOutcome } from './learning-client.ts';
import { MAX_SEALED, openTicket, sealTicket, SEALED_PREFIX, type ItemFormat, type OpenedTicket } from './ticket.ts';

export interface PracticeOptions {
  /** 功能开关 practice（缺省关闭：路由不注册） */
  enabled?: boolean;
  /** 学习服务客户端（开关打开但不配时三条路由一律 503 PRACTICE_UNAVAILABLE） */
  learning?: PracticeLearning;
  sleep?: (ms: number) => Promise<void>;
  retryDelaysMs?: number[];
  /** 学习服务调用（含重试）的整体总时限，缺省 RETRY_BUDGET_MS */
  retryBudgetMs?: number;
  /** 单调时钟（毫秒；测试注入），缺省 performance.now */
  monotonic?: () => number;
  /** 计次等旁路记录（测试断言用）；只收事件名 */
  note?: (event: string) => void;
}

export interface PracticeDeps {
  pool: Pool;
  learning: PracticeLearning;
  /** 票据封装密钥（keys.practice） */
  key: Buffer;
  now: () => number;
  sleep?: (ms: number) => Promise<void>;
  retryDelaysMs?: number[];
  retryBudgetMs?: number;
  monotonic?: () => number;
  note?: (event: string) => void;
  /** 分学科并发闸门（PLT-16；开关关时为空）、访问日志的记录入口（ctx.note：只记学科码与闸门结果）、请求方断开的信号 */
  gate?: SubjectGate;
  observe?: GateObserve;
  signal?: AbortSignal | undefined;
}

const problem = (status: number, code: string, title: string, extra: Record<string, unknown> = {}): Reply =>
  ({ status, body: JSON.stringify({ type: `/problems/${code.toLowerCase()}`, title, status, code, ...extra }) });
const fixed = (body: string): Reply => ({ status: (JSON.parse(body) as { status: number }).status, body });
const json = (status: number, v: unknown, headers?: Record<string, string>): Reply =>
  ({ status, body: JSON.stringify(v), ...(headers ? { headers } : {}) });

export const NOT_FOUND = fixed(P.NOT_FOUND);
export const INVALID = fixed(P.INVALID);
const KEY_REUSED = fixed(P.KEY_REUSED);
export const PRACTICE_UNAVAILABLE = problem(503, 'PRACTICE_UNAVAILABLE', 'practice is not available right now');
const UPSTREAM_UNAVAILABLE = problem(503, 'UPSTREAM_UNAVAILABLE', 'practice service unavailable, retry with the same request');
const TICKET_INVALID = problem(422, 'TICKET_INVALID', 'this practice item is no longer valid; fetch a new one');
const TICKET_EXPIRED = problem(410, 'TICKET_EXPIRED', 'this practice item has expired; fetch a new one');
const INVALID_ANSWER = problem(422, 'INVALID_ANSWER', 'answer does not fit this item');
const NOT_DUE = problem(409, 'PRACTICE_NOT_DUE', 'this entry is not open for practice right now');
const TICKET_USED = problem(409, 'TICKET_USED', 'this practice item was already answered; fetch a new one');
const KEY_SEALED = problem(409, 'KEY_SEALED', 'this practice item can no longer be answered; fetch a new one');
const quota = (reason: string) => problem(402, 'QUOTA_UNAVAILABLE', 'no practice available right now', { reason });
const CURFEW = quota('curfew');
/** 闸门被拒（PLT-16）：503 SUBJECT_BUSY 带 retry-after；再练与复测共用 */
export const subjectBusy = (retryAfterSec: number): Reply => ({ ...fixed(P.SUBJECT_BUSY), headers: busyHeaders(retryAfterSec) });
/** 闸门的请求描述（学生 ID 只作进程内每生上限的计数键）；bypass：这次是已计次的重试，绕过闸门。再练与复测共用 */
export const gateSlot = (deps: Pick<PracticeDeps, 'signal' | 'observe'>, subject: string, student: string, bypass = false) =>
  ({ subject, student, signal: deps.signal, observe: deps.observe, bypass });

/** 前端的学科码（与选科、今日计划同一套）→ 学习服务的学科码。开不开由学习服务的配置决定（语文默认关，回 404） */
export const SUBJECTS: Readonly<Record<string, string>> = { math: 'aya', politics: 'zhixing', chinese: 'wenli' };

/** 本地判过期的余量：留给 BFF 计次到学习服务验票之间的耗时与两边时钟差（过期边缘的票据宁可请学生重新取题，也不扣次） */
export const EXPIRY_MARGIN_MS = 30_000;
/** 学习服务调用（含重试与每次调用的超时）的整体总时限 */
export const RETRY_BUDGET_MS = 45_000;
const MAX_FILL = 200;
/** 选项键：与插件契约 PresentedOption.key 同一规则（PLT-12 审查建议 10；适配器题面的选项键原样转到这里） */
const CHOICE_KEY = /^[A-Za-z0-9]{1,8}$/;
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const RFC3339 = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,6})?(Z|[+-]\d{2}:\d{2})$/;
const CODE = /^[a-z][a-z0-9_]{0,47}$/;
/** 判分结论：契约枚举（packages/contracts learning-internal v1 GradeSummary.verdict） */
export const VERDICTS = ['correct', 'incorrect', 'partial', 'needs_review', 'not_applicable'] as const;

const exactKeys = (o: Record<string, unknown>, keys: readonly string[]): boolean =>
  keys.every((k) => k in o) && Object.keys(o).every((k) => keys.includes(k));
const isRecord = (v: unknown): v is Record<string, unknown> => v !== null && typeof v === 'object' && !Array.isArray(v);
const isInt = (v: unknown, lo: number, hi: number): v is number => typeof v === 'number' && Number.isInteger(v) && v >= lo && v <= hi;
const isTime = (v: unknown): v is string => typeof v === 'string' && RFC3339.test(v) && Number.isFinite(Date.parse(v));

// ---------- 请求体 ----------
export type AnswerValue = { text: string } | { choice: string[] };
export interface AnswerInput { ticket: string; answer: AnswerValue }

function parseAnswerValue(a: unknown): AnswerValue | null {
  if (!isRecord(a)) return null;
  if (exactKeys(a, ['text'])) {
    const t = a['text'];
    return typeof t === 'string' && t.trim() !== '' && t.length <= MAX_FILL ? { text: t } : null;
  }
  if (exactKeys(a, ['choice'])) {
    const c = a['choice'];
    if (!Array.isArray(c) || c.length < 1 || c.length > 8 || !c.every((k) => typeof k === 'string' && CHOICE_KEY.test(k))) return null;
    return new Set(c).size === c.length ? { choice: c as string[] } : null;
  }
  return null;
}

/** 作答请求体：只收票据与作答。幂等键在票据里（BFF 生成）；usage_id、幂等键、数量、资源类型、学生、purpose、channel 一律拒收 */
export function parseAnswer(b: Record<string, unknown>): AnswerInput | null {
  if (!exactKeys(b, ['ticket', 'answer'])) return null;
  const { ticket } = b;
  const answer = parseAnswerValue(b['answer']);
  if (!answer || typeof ticket !== 'string' || !ticket.startsWith(SEALED_PREFIX) || ticket.length > MAX_SEALED) return null;
  return { ticket, answer };
}

/** 作答形状是否合这道题（与学习服务 validate_answer 同一口径）：不合的在计次之前就拒绝 */
export function answerFits(format: ItemFormat, optionKeys: readonly string[], a: AnswerValue): boolean {
  if (format === 'fill') return 'text' in a;
  if (!('choice' in a)) return false;
  if (format === 'single' && a.choice.length !== 1) return false;
  return a.choice.every((k) => optionKeys.includes(k));
}

// ---------- 身份 ----------
// isSelf、curfewBlocked、countPractice、alreadyCounted、isDeterministic 另由复测与验证题（KGE-05，src/retest/service.ts）复用：
// 本人核对、宵禁与题库自练计次只有这一份实现（计次的 SQL 只在本文件，H6 的 METER_CALLERS 不变）
export async function isSelf(pool: Pool, account: string, student: string): Promise<boolean> {
  if (!UUID.test(account) || !UUID.test(student)) return false;
  return withAccount(pool, account, async (c) =>
    (await c.query<{ ok: boolean }>('SELECT platform.is_self_student($1::uuid) AS ok', [student])).rows[0]!.ok === true);
}

// ---------- 宵禁（审查 B1） ----------
/** 宵禁中回 402 curfew，否则 null。只在本人核对之后调用：读不到（42501，账号状态刚变）与不是本人同一个 404 */
export async function curfewBlocked(deps: Pick<PracticeDeps, 'pool' | 'note'>, account: string, student: string): Promise<Reply | null> {
  const c = await inCurfew(deps.pool, account, student);
  if (c === 'forbidden') {
    deps.note?.('curfew_forbidden');  // 正常情况下到不了这里（本人核对在前）；测试据此确认越权请求停在本人核对
    return NOT_FOUND;
  }
  return c ? CURFEW : null;
}

// ---------- 计次 ----------
export type PracticeCount =
  | { outcome: 'allowed' | 'existing'; usage_id: string }
  | { outcome: 'conflict' | 'no_entitlement' | 'daily_limit_reached' };

/** 题库自练计次（0015）：以学生本人身份，参数只有学生与幂等键（次数、资源、usage_id 都由库决定） */
export async function countPractice(pool: Pool, account: string, student: string, key: string): Promise<PracticeCount | 'forbidden' | 'invalid'> {
  try {
    return await withAccount(pool, account, async (c) =>
      (await c.query<{ r: PracticeCount }>('SELECT platform.meter_practice($1::uuid, $2) AS r', [student, key])).rows[0]!.r);
  } catch (e) {
    const code = (e as { code?: string }).code ?? '';
    if (code === '42501') return 'forbidden';  // 调用前已核对本人；账号状态刚变时同一个 404
    if (code === '22023') return 'invalid';
    throw e;
  }
}

/** 这个键是否已经计过次（RLS：本人读得到自己的记录）：票据过期后同一张票据重试找回原结果用 */
export async function alreadyCounted(pool: Pool, account: string, student: string, key: string): Promise<boolean> {
  return withAccount(pool, account, async (c) =>
    (await c.query('SELECT 1 FROM platform.practice_log WHERE idempotency_key = $1 AND student_id = $2::uuid', [key, student])).rowCount === 1);
}

// ---------- 学习服务调用：结果未知时同一请求重试（整体总时限；确定性拒绝不重试） ----------
const DEFAULT_DELAYS = [300, 1000, 2000];
/** 归为“结果未知”（5xx）但其实是确定的拒绝：没配再练或题面（501）、判分结果没通过平台核对（502，一行不写）。重试也不会变 */
const DETERMINISTIC = /^(?:501\b|502 ADAPTER_RESULT_REJECTED$)/;
export const isDeterministic = (out: PracticeOutcome<unknown>): boolean => out.kind === 'unknown' && DETERMINISTIC.test(out.reason);

/** fn 收到本次调用可用的剩余时间（毫秒），客户端把它作为单次超时的上限 */
export async function withRetry<T>(deps: Pick<PracticeDeps, 'sleep' | 'retryDelaysMs' | 'retryBudgetMs' | 'monotonic' | 'note'>,
                                   fn: (timeoutMs: number) => Promise<PracticeOutcome<T>>): Promise<PracticeOutcome<T>> {
  const sleep = deps.sleep ?? ((ms: number) => new Promise<void>((r) => setTimeout(r, ms)));
  const mono = deps.monotonic ?? (() => performance.now());
  const budget = deps.retryBudgetMs ?? RETRY_BUDGET_MS;
  const start = mono();
  const left = () => budget - (mono() - start);
  let out = await fn(left());
  for (const d of deps.retryDelaysMs ?? DEFAULT_DELAYS) {
    if (out.kind !== 'unknown' || isDeterministic(out)) break;
    if (left() - d <= 0) {  // 总时限：等完这一下就没有时间再调了
      deps.note?.('learning_retry_budget_exhausted');
      break;
    }
    await sleep(d);
    deps.note?.('learning_retry');
    out = await fn(left());
  }
  return out;
}

/** 结果未知（重试后仍未知）：确定性拒绝回 PRACTICE_UNAVAILABLE（不必重试），其余 UPSTREAM_UNAVAILABLE（可同请求重试） */
const fromUnknown = (out: PracticeOutcome<unknown>): Reply => (isDeterministic(out) ? PRACTICE_UNAVAILABLE : UPSTREAM_UNAVAILABLE);

export type PracticeOp = 'queue' | 'next' | 'answer';

/** 学习服务的确定失败映射到学生端（只给编码，不带明细；按操作区分：票据、作答类的编码只在作答时有意义） */
export function fromFailure(f: { status: number; problem: { code?: string } }, op: PracticeOp): Reply {
  const code = f.problem.code ?? '';
  if (f.status === 404) return NOT_FOUND;                                  // ENTRY_NOT_FOUND：与不存在同一个 404
  if (f.status === 400 && code === 'SUBJECT_NOT_SUPPORTED') return NOT_FOUND;  // 学科没开（语文默认关）
  if (f.status === 409 && code === 'PRACTICE_NOT_DUE') return op === 'queue' ? PRACTICE_UNAVAILABLE : NOT_DUE;
  if (op !== 'answer') return PRACTICE_UNAVAILABLE;                        // 列表与取题：其余确定失败都不是学生能改的
  if (f.status === 403) return TICKET_INVALID;                             // 伪造、篡改、跨学生、跨学科（学习服务验签）
  if (f.status === 410) return TICKET_EXPIRED;
  if (f.status === 409 && (code === 'TICKET_USED' || code === 'IDEMPOTENCY_CONFLICT')) return TICKET_USED;  // 同一张票据换作答
  if (f.status === 409 && code === 'IDEMPOTENCY_KEY_SEALED') return KEY_SEALED;
  if (f.status === 422 && code === 'INVALID_ANSWER') return INVALID_ANSWER;
  // 401（令牌配错）、501（没配再练或题面）、422 CONTENT_NOT_REGISTERED（内容没登记）、其余 4xx：不是学生能改的
  return PRACTICE_UNAVAILABLE;
}

const replayHeader = (replayed: boolean) => ({ 'idempotent-replayed': replayed ? 'true' : 'false' });

// ---------- 响应白名单 ----------
// 前端 apps/web/src/practice/view.ts 的白名单与这几组逐字核对（tests/enum-drift.integration.test.ts，specs/web-bff-enum-drift）
export const ENTRY_STATUSES = ['due', 'waiting', 'closed', 'exhausted', 'unavailable'] as const;
/** 作答后的条目状态（不会是 unavailable） */
export const AFTER_STATUSES = ['due', 'waiting', 'closed', 'exhausted'] as const;
export const SOURCES = ['wrong', 'hint_followup'] as const;
const LEVELS = ['know', 'understand', 'apply', 'synthesize'];
const oneOf = (xs: readonly string[], v: unknown): boolean => typeof v === 'string' && xs.includes(v);

/** 条目：只挑这些字段；原题引用、复测标记、同族限定标记与任何多出来的字段不转 */
export function pickEntry(e: unknown): Record<string, unknown> | null {
  if (!isRecord(e)) return null;
  const { entry_id, node_id, cognitive_level, source, status, round, max_rounds, next_round_at, empty_reason } = e;
  if (typeof entry_id !== 'string' || !UUID.test(entry_id) || typeof node_id !== 'string' || node_id === '' || node_id.length > 128
      || typeof cognitive_level !== 'string' || !LEVELS.includes(cognitive_level) || !oneOf(SOURCES, source)
      || !oneOf(ENTRY_STATUSES, status) || !isInt(round, 1, 1000) || !isInt(max_rounds, 1, 1000)
      || (next_round_at !== null && !isTime(next_round_at))
      || (empty_reason !== null && (typeof empty_reason !== 'string' || !CODE.test(empty_reason)))) {
    return null;
  }
  return { entry_id, node_id, cognitive_level, source, status, round, max_rounds, next_round_at, empty_reason };
}

interface Ready {
  entry_id: string; round: number; item: { format: ItemFormat; stem: string; options?: { key: string; text: string }[]; rich?: RichItem };
  ticket: string; expires_at: string;
}

export function readReady(v: Record<string, unknown>, entry: string): Ready | null {
  const { entry_id, round, item, ticket, expires_at } = v;
  if (entry_id !== entry || !isInt(round, 1, 1000) || typeof ticket !== 'string' || ticket === '' || ticket.length > 1024
      || !isTime(expires_at) || !isRecord(item)) {
    return null;
  }
  const { format, stem, options } = item;
  if ((format !== 'single' && format !== 'multi' && format !== 'fill') || typeof stem !== 'string' || stem === '') return null;
  let opts: { key: string; text: string }[] | undefined;
  if (options !== undefined) {
    if (!Array.isArray(options) || !options.every((o) => isRecord(o) && typeof o['key'] === 'string' && CHOICE_KEY.test(o['key'])
                                                      && typeof o['text'] === 'string')) return null;
    opts = options.map((o) => ({ key: (o as { key: string }).key, text: (o as { text: string }).text }));
  }
  if ((format === 'fill') !== (opts === undefined || opts.length === 0)) return null;
  // PLT-12：受限富文本题面（学习服务开了 LEARNING_PRESENT_ITEM_SOURCE=adapter 时才有）。按白名单重建；不合格就丢掉 rich，
  // 退回纯文字题面照常出题（与诊断同一口径，审查建议 10；纯文字题面由 React 按文本转义）
  let rich: RichItem | undefined;
  if (item['rich'] !== undefined) {
    rich = pickRich(item['rich'], (opts ?? []).map((o) => o.key)) ?? undefined;
  }
  return { entry_id: entry, round, ticket, expires_at,
           item: { format, stem, ...(opts && opts.length > 0 ? { options: opts } : {}), ...(rich ? { rich } : {}) } };
}

/** 作答结果：判分结论（契约枚举，来自验证器或 Rubric 的 grade 摘要）、待复核标记、条目新状态；其余一律不转 */
export function pickAnswer(v: unknown): Record<string, unknown> | null {
  if (!isRecord(v) || !(VERDICTS as readonly unknown[]).includes(v['verdict']) || !isRecord(v['entry'])) return null;
  const e = v['entry'];
  const { entry_id, status, round, next_round_at } = e;
  if (typeof entry_id !== 'string' || !UUID.test(entry_id) || !oneOf(AFTER_STATUSES, status)
      || !isInt(round, 1, 1000) || (next_round_at !== null && !isTime(next_round_at))) {
    return null;
  }
  if (v['pending_review'] !== undefined && v['pending_review'] !== true) return null;
  return { verdict: v['verdict'], ...(v['pending_review'] === true ? { pending_review: true } : {}),
           entry: { entry_id, status, round, next_round_at } };
}

// ---------- 三个操作 ----------
export async function practiceQueue(deps: PracticeDeps, account: string, student: string, code: string): Promise<Reply> {
  const subject = SUBJECTS[code];
  if (subject === undefined) return NOT_FOUND;
  if (!(await isSelf(deps.pool, account, student))) return NOT_FOUND;
  const r = await withRetry(deps, (ms) => deps.learning.queue(student, subject, ms));
  if (r.kind === 'unknown') return fromUnknown(r);
  if (r.kind === 'failure') return fromFailure(r, 'queue');
  const body = r.body;
  if (!isRecord(body) || !Array.isArray(body['entries'])) return UPSTREAM_UNAVAILABLE;
  const entries = body['entries'].map(pickEntry);
  if (entries.some((e) => e === null)) return UPSTREAM_UNAVAILABLE;  // 形状不对整页不给（不挑着给一部分）
  return json(200, { subject: code, entries });
}

export async function practiceNext(deps: PracticeDeps, account: string, student: string, code: string, entry: string): Promise<Reply> {
  const subject = SUBJECTS[code];
  if (subject === undefined) return NOT_FOUND;
  if (!(await isSelf(deps.pool, account, student))) return NOT_FOUND;
  const blocked = await curfewBlocked(deps, account, student);  // 宵禁：取题
  if (blocked) return blocked;
  // 闸门（PLT-16）：宵禁检查之后包住转发
  return withSubjectSlot(deps.gate, gateSlot(deps, subject, student), subjectBusy, () => nextAdmitted(deps, student, subject, entry));
}

async function nextAdmitted(deps: PracticeDeps, student: string, subject: string, entry: string): Promise<Reply> {
  const r = await withRetry(deps, (ms) => deps.learning.next(student, subject, entry, ms));
  if (r.kind === 'unknown') return fromUnknown(r);
  if (r.kind === 'failure') return fromFailure(r, 'next');
  const body = r.body;
  if (isRecord(body) && body['status'] === 'unavailable') {
    const reason = body['empty_reason'];
    return typeof reason === 'string' && CODE.test(reason) ? json(200, { status: 'unavailable', empty_reason: reason }) : UPSTREAM_UNAVAILABLE;
  }
  if (!isRecord(body) || body['status'] !== 'ready') return UPSTREAM_UNAVAILABLE;
  const ready = readReady(body, entry);
  if (!ready) return UPSTREAM_UNAVAILABLE;
  const sealed = sealTicket(deps.key, student, {
    ticket: ready.ticket, subject, entryId: ready.entry_id, expiresAt: ready.expires_at, format: ready.item.format,
    optionKeys: (ready.item.options ?? []).map((o) => o.key),
  });
  // 题目引用（item_ref）、学习服务票据、同族限定标记不转给前端
  return json(200, { status: 'ready', entry_id: ready.entry_id, round: ready.round, item: ready.item, ticket: sealed,
                     expires_at: ready.expires_at });
}

export async function practiceAnswer(deps: PracticeDeps, account: string, student: string, code: string, input: AnswerInput): Promise<Reply> {
  const subject = SUBJECTS[code];
  if (subject === undefined) return NOT_FOUND;
  if (!(await isSelf(deps.pool, account, student))) return NOT_FOUND;
  // 本地能判的拒绝都在计次之前（不扣冤枉次）
  const t = openTicket(deps.key, student, input.ticket);
  if (!t || t.subject !== subject) return TICKET_INVALID;
  if (!answerFits(t.format, t.optionKeys, input.answer)) return INVALID_ANSWER;
  const key = t.idempotencyKey;  // 一张票据一个键（BFF 封装时生成）：计次与学习服务都用它
  if (deps.now() >= Date.parse(t.expiresAt) - EXPIRY_MARGIN_MS && !(await alreadyCounted(deps.pool, account, student, key))) {
    deps.note?.('ticket_expired_local');
    return TICKET_EXPIRED;
  }
  const blocked = await curfewBlocked(deps, account, student);  // 宵禁：作答（计次之前）
  if (blocked) return blocked;
  // 闸门（PLT-16）：宵禁检查之后、计次之前（被拒的请求本次不计次）；同一张票据已经计过次（上一次结果未知或被拒后重试）绕过闸门，不再被拒
  const counted = deps.gate !== undefined && (await alreadyCounted(deps.pool, account, student, key));
  return withSubjectSlot(deps.gate, gateSlot(deps, subject, student, counted), subjectBusy,
                         () => answerAdmitted(deps, account, student, subject, t, key, input));
}

/** 已入闸的作答：计次，再调学习服务（名额由 withSubjectSlot 在 finally 里释放） */
async function answerAdmitted(deps: PracticeDeps, account: string, student: string, subject: string, t: OpenedTicket, key: string,
                              input: AnswerInput): Promise<Reply> {
  const usage = usageIdFor(key);
  const m = await countPractice(deps.pool, account, student, key);
  if (m === 'forbidden') return NOT_FOUND;
  if (m === 'invalid') return INVALID;
  switch (m.outcome) {
    case 'conflict':
      return KEY_REUSED;
    case 'no_entitlement': case 'daily_limit_reached':
      return quota(m.outcome);
    case 'allowed': case 'existing':
      break;
    default:
      throw new Error('unexpected meter_practice outcome');
  }
  if (m.usage_id !== usage) throw new Error('usage_id derived by the database does not match the BFF');
  deps.note?.(m.outcome === 'allowed' ? 'practice_counted' : 'practice_count_existing');
  const r = await withRetry(deps, (ms) => deps.learning.answer(student, subject, { ticket: t.ticket, answer: input.answer, idempotency_key: key,
                                                                                    usage_id: usage }, ms));
  if (r.kind === 'unknown') return fromUnknown(r);  // 已计次；同一张票据重试不再计次，学习服务按幂等找回
  if (r.kind === 'failure') return fromFailure(r, 'answer');
  const out = pickAnswer(r.body);
  if (!out || (out['entry'] as { entry_id: string }).entry_id !== t.entryId) return UPSTREAM_UNAVAILABLE;
  return json(200, out, replayHeader(r.replayed));
}
