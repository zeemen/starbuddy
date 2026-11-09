// 端到端用的合成学习服务桩（specs/practice-loop-stage1 PR 4：错题再练学生端页面，e2e/practice.spec.ts）：只在 scripts/e2e.sh 的一次性环境里起，
// 只监听 127.0.0.1，只认 BFF 的服务间令牌（会话令牌 FAKE_LEARNING_TOKEN、写入令牌 FAKE_LEARNING_WRITE_TOKEN，PLT-24；每次随机生成、只经环境变量传）。
// 只实现错题再练的三个内部接口（services/learning/openapi/practice-internal.v0.yaml）：queue、next、answer，按该文件的响应形状返回；
// 另实现复测与验证题的内部接口（retest-internal.v0.yaml，specs/retest-delivery-kge05：queue、next、answer 与只读的 check、replay，
// 剧本见文件后部）与今日计划跨科编排的只读接口（today-internal.v0.yaml，PLT-11：/internal/v1/today，剧本见文件后部）；
// 其余路径（今日计划、诊断、星图等）一律 501（BFF 按“暂无安排 / 暂时无法获取”处理）。
// 不是学习服务的实现：错题判定、选题、票据验签、经 grade 写入落库与掌握重算在 services/learning，有它自己的单测与库上集成测试；
// 这里的条目、题面、判分全是合成的固定剧本（学生 P1、P2、P3 见 db/seeds/dev/practice-synthetic.sql），状态只在进程内存里。
// 响应故意带上内部接口合法的内部字段（原题与再练题的题目引用、复测标记、同族限定标记、空原因编码）：端到端断言它们不出现在页面上。
//
// 剧本（学生 P1，数学 aya；道法 zhixing 一律空列表；语文 wenli 400 SUBJECT_NOT_SUPPORTED，与学习服务默认配置一致）：
//   E1 due 单选：B 判对 → closed；其余判错 → waiting（下一轮 48 小时后）    E2 waiting    E3 closed    E4 exhausted
//   E5 unavailable（no_admitted_candidate）    E6 hint_followup 填空：判分待复核（pending_review），条目仍 due
//   E7 due 多选：作答 409 PRACTICE_NOT_DUE（条目在出题之后变了）    E8 due：票据 10 秒后过期（BFF 本地判 410，不计次）
//   E9 due：取题 501（BFF 503 PRACTICE_UNAVAILABLE）    E10 due：取题 200 unavailable（all_excluded）
//   E11 due 填空：同一个键前 4 次作答 503（BFF 自己重试 3 次后回 UPSTREAM_UNAVAILABLE），第 5 次起判错 → waiting；
//       同一个键换了作答 409 IDEMPOTENCY_CONFLICT（页面再提交必须原样发首次作答）
// 学生 P2（宵禁 09:00–06:00）、P3（没有权益）各一个 due 单选条目：BFF 在调桩之前就回 402，所以桩的作答剧本只是兜底。
// 请求记录（做法同短信捕获桩的收件箱）：每收到一个请求（不论令牌对不对、路径认不认）就往 FAKE_LEARNING_REQUEST_LOG 追加一行
// {"method","path"}（不记请求体、令牌与票据）；端到端读行数，核对开关关时桩收到 0 个请求（e2e/practice-off.spec.ts）。
import { appendFileSync } from 'node:fs';
import { createServer, type IncomingMessage } from 'node:http';
import { randomBytes } from 'node:crypto';

const PORT = Number(process.env['FAKE_LEARNING_PORT'] ?? 0);
// PLT-24：同真实学习服务，再练、复测、今日编排只认会话令牌（FAKE_LEARNING_TOKEN）；写入令牌（FAKE_LEARNING_WRITE_TOKEN）只到其余路径（一律 501）
const TOKEN = process.env['FAKE_LEARNING_TOKEN'] ?? '';
const WRITE_TOKEN = process.env['FAKE_LEARNING_WRITE_TOKEN'] ?? '';
const REQUEST_LOG = process.env['FAKE_LEARNING_REQUEST_LOG'] ?? '';
if (!PORT || TOKEN.length < 32 || WRITE_TOKEN.length < 32 || WRITE_TOKEN === TOKEN) {
  console.error('fake-learning-practice: 需要 FAKE_LEARNING_PORT 与两枚互不相同、至少 32 个字符的 FAKE_LEARNING_TOKEN、FAKE_LEARNING_WRITE_TOKEN');
  process.exit(2);
}

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const UUID_V4 = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
const P1 = '59000000-0000-4000-8000-0000000000c1';
const P2 = '59000000-0000-4000-8000-0000000000c2';
const P3 = '59000000-0000-4000-8000-0000000000c3';
const id = (n: string) => `59000000-0000-4000-8000-000000000${n}`;

type Format = 'single' | 'multi' | 'fill';
type Status = 'due' | 'waiting' | 'closed' | 'exhausted' | 'unavailable';
interface Entry {
  entry_id: string; node_id: string; cognitive_level: 'know' | 'understand' | 'apply' | 'synthesize'; source: 'wrong' | 'hint_followup';
  status: Status; round: number; max_rounds: number; next_round_at: string | null; empty_reason: string | null;
  /** 剧本 */
  play: { format?: Format; correct?: string[]; next?: 'ready' | 'unavailable' | 'error501'; nextReason?: string; answer?: 'grade' | 'pending' | 'not_due' | 'flaky';
          ttlMs?: number };
}

const OPTIONS = [{ key: 'A', text: '【合成选项】甲' }, { key: 'B', text: '【合成选项】乙' }, { key: 'C', text: '【合成选项】丙' }, { key: 'D', text: '【合成选项】丁' }];
const LATER = '2026-12-01T10:00:00+08:00';

function entry(n: string, node: string, status: Status, play: Entry['play'], extra: Partial<Entry> = {}): Entry {
  return { entry_id: id(n), node_id: node, cognitive_level: 'apply', source: 'wrong', status, round: 1, max_rounds: 3,
           next_round_at: null, empty_reason: null, play, ...extra };
}

const STATE: Record<string, Entry[]> = {
  [P1]: [
    entry('101', 'MATH.SYN.P1.N1', 'due', { format: 'single', correct: ['B'], next: 'ready', answer: 'grade' }),
    entry('102', 'MATH.SYN.P1.N2', 'waiting', {}, { round: 2, next_round_at: LATER }),
    entry('103', 'MATH.SYN.P1.N3', 'closed', {}, { cognitive_level: 'understand' }),
    entry('104', 'MATH.SYN.P1.N4', 'exhausted', {}, { round: 3 }),
    entry('105', 'MATH.SYN.P1.N5', 'unavailable', {}, { empty_reason: 'no_admitted_candidate', cognitive_level: 'know' }),
    entry('106', 'MATH.SYN.P1.N6', 'due', { format: 'fill', next: 'ready', answer: 'pending' }, { source: 'hint_followup', cognitive_level: 'synthesize' }),
    entry('107', 'MATH.SYN.P1.N7', 'due', { format: 'multi', correct: ['A', 'C'], next: 'ready', answer: 'not_due' }),
    entry('108', 'MATH.SYN.P1.N8', 'due', { format: 'single', correct: ['A'], next: 'ready', answer: 'grade', ttlMs: 10_000 }),
    entry('109', 'MATH.SYN.P1.N9', 'due', { next: 'error501' }),
    entry('10a', 'MATH.SYN.P1.NA', 'due', { next: 'unavailable', nextReason: 'all_excluded' }),
    entry('10b', 'MATH.SYN.P1.NB', 'due', { format: 'fill', correct: ['42'], next: 'ready', answer: 'flaky' }),
  ],
  [P2]: [entry('201', 'MATH.SYN.P2.N1', 'due', { format: 'single', correct: ['B'], next: 'ready', answer: 'grade' })],
  [P3]: [entry('301', 'MATH.SYN.P3.N1', 'due', { format: 'single', correct: ['B'], next: 'ready', answer: 'grade' })],
};

interface Ticket { student: string; subject: string; entry: string; expiresAt: number; round: number }
const TICKETS = new Map<string, Ticket>();
interface Done { student: string; answer: string; status: number; body: unknown }
const KEYS = new Map<string, Done>();
const FLAKY = new Map<string, number>();
const FLAKY_ANSWER = new Map<string, string>();

const problem = (status: number, code: string, retryable = false) => ({ status, body: { type: `/problems/${code.toLowerCase()}`, title: code, status, code, retryable } });
const wire = (e: Entry) => ({
  entry_id: e.entry_id, node_id: e.node_id, cognitive_level: e.cognitive_level, source: e.source, status: e.status, round: e.round,
  max_rounds: e.max_rounds, source_item_ref: `aya:item-SYN-${e.entry_id.slice(-3)}-src`, next_round_at: e.next_round_at,
  retest_due: e.entry_id === id('101'), same_family_only: e.entry_id === id('106'), empty_reason: e.empty_reason,
});
const short = (e: Entry) => ({ entry_id: e.entry_id, status: e.status as Exclude<Status, 'unavailable'>, round: e.round, next_round_at: e.next_round_at });

async function readBody(req: IncomingMessage): Promise<Record<string, unknown> | null> {
  const chunks: Buffer[] = [];
  let size = 0;
  for await (const c of req) {
    size += (c as Buffer).length;
    if (size > 65_536) return null;
    chunks.push(c as Buffer);
  }
  try {
    const v = JSON.parse(Buffer.concat(chunks).toString('utf8')) as unknown;
    return v && typeof v === 'object' && !Array.isArray(v) ? v as Record<string, unknown> : null;
  } catch {
    return null;
  }
}

const exact = (b: Record<string, unknown>, keys: string[]) => keys.every((k) => k in b) && Object.keys(b).every((k) => keys.includes(k));

function subjectCheck(b: Record<string, unknown>): { status: number; body: unknown } | null {
  if (typeof b['student_ref'] !== 'string' || !UUID.test(b['student_ref'])) return problem(400, 'INVALID_REQUEST');
  if (b['subject'] === 'wenli') return problem(400, 'SUBJECT_NOT_SUPPORTED');
  if (b['subject'] !== 'aya' && b['subject'] !== 'zhixing') return problem(400, 'INVALID_REQUEST');
  return null;
}

const entriesOf = (b: Record<string, unknown>): Entry[] => (b['subject'] === 'aya' ? STATE[b['student_ref'] as string] ?? [] : []);

function queue(b: Record<string, unknown>) {
  if (!exact(b, ['student_ref', 'subject'])) return problem(400, 'INVALID_REQUEST');
  return subjectCheck(b) ?? { status: 200, body: { subject: b['subject'], entries: entriesOf(b).map(wire) } };
}

function next(b: Record<string, unknown>) {
  if (!exact(b, ['student_ref', 'subject', 'entry_id'])) return problem(400, 'INVALID_REQUEST');
  const bad = subjectCheck(b);
  if (bad) return bad;
  const e = entriesOf(b).find((x) => x.entry_id === b['entry_id']);
  if (!e) return problem(404, 'ENTRY_NOT_FOUND');
  if (e.status !== 'due') return problem(409, 'PRACTICE_NOT_DUE');
  if (e.play.next === 'error501') return problem(501, 'PRACTICE_UNAVAILABLE');
  if (e.play.next === 'unavailable') return { status: 200, body: { status: 'unavailable', empty_reason: e.play.nextReason ?? 'no_candidate' } };
  const ticket = `pt1.${randomBytes(24).toString('base64url')}.${randomBytes(16).toString('base64url')}`;
  const expiresAt = Date.now() + (e.play.ttlMs ?? 3_600_000);
  TICKETS.set(ticket, { student: b['student_ref'] as string, subject: b['subject'] as string, entry: e.entry_id, expiresAt, round: e.round });
  const format = e.play.format ?? 'single';
  return {
    status: 200,
    body: {
      status: 'ready', entry_id: e.entry_id, round: e.round, item_ref: `aya:item-SYN-${e.entry_id.slice(-3)}-var`,
      item: { format, stem: `【合成题面】${e.node_id} 的一道同类占位题`, ...(format === 'fill' ? {} : { options: OPTIONS }) },
      ticket, expires_at: new Date(expiresAt).toISOString(), same_family_only: false,
    },
  };
}

function answer(b: Record<string, unknown>) {
  if (!exact(b, ['student_ref', 'subject', 'ticket', 'answer', 'idempotency_key', 'usage_id'])) return problem(400, 'INVALID_REQUEST');
  const bad = subjectCheck(b);
  if (bad) return bad;
  const key = b['idempotency_key'];
  if (typeof key !== 'string' || !UUID_V4.test(key) || typeof b['usage_id'] !== 'string' || !UUID.test(b['usage_id'])) return problem(400, 'INVALID_REQUEST');
  const student = b['student_ref'] as string;
  const answerJson = JSON.stringify(b['answer']);
  const done = KEYS.get(key);
  if (done) {
    if (done.student !== student || done.answer !== answerJson) return problem(409, 'IDEMPOTENCY_CONFLICT');
    return { ...done, replayed: true };
  }
  const t = typeof b['ticket'] === 'string' ? TICKETS.get(b['ticket']) : undefined;
  if (!t || t.student !== student || t.subject !== b['subject']) return problem(403, 'TICKET_INVALID');
  if (Date.now() >= t.expiresAt) return problem(410, 'TICKET_EXPIRED');
  const e = entriesOf(b).find((x) => x.entry_id === t.entry);
  if (!e) return problem(403, 'TICKET_INVALID');
  if (e.status !== 'due' || e.round !== t.round) return problem(409, 'PRACTICE_NOT_DUE');
  if (e.play.answer === 'not_due') return problem(409, 'PRACTICE_NOT_DUE');
  if (e.play.answer === 'flaky') {
    // 同一个键第一次带来的作答记下来：之后（页面“再提交一次”）换了作答就按幂等冲突拒绝，端到端借此核对页面原样重发首次作答
    const first = FLAKY_ANSWER.get(key) ?? answerJson;
    FLAKY_ANSWER.set(key, first);
    if (first !== answerJson) return problem(409, 'IDEMPOTENCY_CONFLICT');
    const n = (FLAKY.get(key) ?? 0) + 1;
    FLAKY.set(key, n);
    if (n <= 4) return problem(503, 'UPSTREAM_UNAVAILABLE', true);
  }
  const a = b['answer'] as Record<string, unknown>;
  const given = Array.isArray(a?.['choice']) ? [...(a['choice'] as string[])].sort() : typeof a?.['text'] === 'string' ? [String(a['text']).trim()] : null;
  if (!given) return problem(422, 'INVALID_ANSWER');
  let out: Record<string, unknown>;
  if (e.play.answer === 'pending') {
    out = { verdict: 'correct', pending_review: true, entry: short(e) };
  } else {
    const correct = JSON.stringify(given) === JSON.stringify([...(e.play.correct ?? [])].sort());
    if (correct) {
      e.status = 'closed';
    } else if (e.round >= e.max_rounds) {
      e.status = 'exhausted';
    } else {
      e.status = 'waiting';
      e.round += 1;
      e.next_round_at = new Date(Date.now() + 48 * 3_600_000).toISOString();
    }
    out = { verdict: correct ? 'correct' : 'incorrect', entry: short(e) };
  }
  const res = { status: 200, body: out };
  KEYS.set(key, { student, answer: answerJson, ...res });
  return res;
}

// ---------- 复测与验证题（specs/retest-delivery-kge05；e2e/retest.spec.ts）：retest 的三个内部接口（retest-internal.v0.yaml 的响应形状） ----------
// 剧本（学生 P1，数学 aya；道法空列表；语文 400）：
//   R1 复测 due 单选：作答（对错都一样）→ done（KGE-04 关闭复测轮）    R2 验证 due 填空：判分待复核，条目仍 due
//   R3 验证 unavailable（no_new_family）    R4 复测 due：取题 200 unavailable（no_source_item）
// 响应故意带上任务原因（reason）、作答用途（purpose）、题目引用与空原因编码：端到端断言它们不出现在页面上。
interface RetestEntry { entry_id: string; node_id: string; kind: 'retest' | 'verify'; status: 'due' | 'unavailable'; reason: string; empty_reason: string | null;
                        play: { format?: Format; next?: 'ready' | 'unavailable'; answer?: 'done' | 'pending' } }
const RETEST: Record<string, RetestEntry[]> = {
  [P1]: [
    { entry_id: id('401'), node_id: 'MATH.SYN.P1.R1', kind: 'retest', status: 'due', reason: 'scheduled', empty_reason: null,
      play: { format: 'single', next: 'ready', answer: 'done' } },
    { entry_id: id('402'), node_id: 'MATH.SYN.P1.R2', kind: 'verify', status: 'due', reason: 'verify_self_report', empty_reason: null,
      play: { format: 'fill', next: 'ready', answer: 'pending' } },
    { entry_id: id('403'), node_id: 'MATH.SYN.P1.R3', kind: 'verify', status: 'unavailable', reason: 'verify_in_platform', empty_reason: 'no_new_family', play: {} },
    { entry_id: id('404'), node_id: 'MATH.SYN.P1.R4', kind: 'retest', status: 'due', reason: 'decay', empty_reason: null, play: { next: 'unavailable' } },
  ],
};
const RETEST_TICKETS = new Map<string, { student: string; subject: string; entry: string; expiresAt: number }>();
const retestOf = (b: Record<string, unknown>): RetestEntry[] => (b['subject'] === 'aya' ? RETEST[b['student_ref'] as string] ?? [] : []);
const DONE_RETEST = new Set<string>();

function rqueue(b: Record<string, unknown>) {
  if (!exact(b, ['student_ref', 'subject'])) return problem(400, 'INVALID_REQUEST');
  return subjectCheck(b) ?? { status: 200, body: { subject: b['subject'], entries: retestOf(b).filter((e) => !DONE_RETEST.has(e.entry_id)).map((e) => ({
    entry_id: e.entry_id, node_id: e.node_id, cognitive_level: 'apply', kind: e.kind, status: e.status, empty_reason: e.empty_reason, reason: e.reason })) } };
}

function rnext(b: Record<string, unknown>) {
  if (!exact(b, ['student_ref', 'subject', 'entry_id'])) return problem(400, 'INVALID_REQUEST');
  const bad = subjectCheck(b);
  if (bad) return bad;
  const e = retestOf(b).find((x) => x.entry_id === b['entry_id']);
  if (!e) return problem(404, 'ENTRY_NOT_FOUND');
  if (DONE_RETEST.has(e.entry_id)) return problem(409, 'RETEST_NOT_DUE');
  if (e.status !== 'due' || e.play.next === 'unavailable') return { status: 200, body: { status: 'unavailable', empty_reason: e.empty_reason ?? 'no_source_item' } };
  const ticket = `rt1.${randomBytes(24).toString('base64url')}.${randomBytes(16).toString('base64url')}`;
  const expiresAt = Date.now() + 3_600_000;
  RETEST_TICKETS.set(ticket, { student: b['student_ref'] as string, subject: b['subject'] as string, entry: e.entry_id, expiresAt });
  const format = e.play.format ?? 'single';
  return { status: 200, body: {
    status: 'ready', entry_id: e.entry_id, kind: e.kind, reason: e.reason, item_ref: `aya:item-SYN-${e.entry_id.slice(-3)}-rt`,
    item: { format, stem: `【合成题面】${e.node_id} 的一道复测占位题`, ...(format === 'fill' ? {} : { options: OPTIONS }) },
    ticket, expires_at: new Date(expiresAt).toISOString() } };
}

function ranswer(b: Record<string, unknown>) {
  if (!exact(b, ['student_ref', 'subject', 'ticket', 'answer', 'idempotency_key', 'usage_id'])) return problem(400, 'INVALID_REQUEST');
  const bad = subjectCheck(b);
  if (bad) return bad;
  const key = b['idempotency_key'];
  if (typeof key !== 'string' || !UUID_V4.test(key) || typeof b['usage_id'] !== 'string' || !UUID.test(b['usage_id'])) return problem(400, 'INVALID_REQUEST');
  const student = b['student_ref'] as string;
  const answerJson = JSON.stringify(b['answer']);
  const done = KEYS.get(key);
  if (done) {
    if (done.student !== student || done.answer !== answerJson) return problem(409, 'IDEMPOTENCY_CONFLICT');
    return { ...done, replayed: true };
  }
  const t = typeof b['ticket'] === 'string' ? RETEST_TICKETS.get(b['ticket']) : undefined;
  if (!t || t.student !== student || t.subject !== b['subject']) return problem(403, 'TICKET_INVALID');
  if (Date.now() >= t.expiresAt) return problem(410, 'TICKET_EXPIRED');
  const e = retestOf(b).find((x) => x.entry_id === t.entry);
  if (!e || DONE_RETEST.has(e.entry_id)) return problem(409, 'RETEST_NOT_DUE');
  const a = b['answer'] as Record<string, unknown>;
  const given = Array.isArray(a?.['choice']) ? [...(a['choice'] as string[])].sort() : typeof a?.['text'] === 'string' ? [String(a['text']).trim()] : null;
  if (!given) return problem(422, 'INVALID_ANSWER');
  let out: Record<string, unknown>;
  if (e.play.answer === 'pending') {
    out = { verdict: 'correct', pending_review: true, entry: { entry_id: e.entry_id, status: 'due' }, purpose: 'transfer_check' };
  } else {
    DONE_RETEST.add(e.entry_id);
    out = { verdict: JSON.stringify(given) === '["B"]' ? 'correct' : 'incorrect', entry: { entry_id: e.entry_id, status: 'done' }, purpose: 'retest' };
  }
  const res = { status: 200, body: out };
  KEYS.set(key, { student, answer: answerJson, ...res });
  return res;
}

/** 计次前的只读预检：票据是这个学生这一科的、没过期、条目还没做完 */
function rcheck(b: Record<string, unknown>) {
  if (!exact(b, ['student_ref', 'subject', 'ticket'])) return problem(400, 'INVALID_REQUEST');
  const bad = subjectCheck(b);
  if (bad) return bad;
  const t = typeof b['ticket'] === 'string' ? RETEST_TICKETS.get(b['ticket']) : undefined;
  if (!t || t.student !== b['student_ref'] || t.subject !== b['subject']) return problem(403, 'TICKET_INVALID');
  if (Date.now() >= t.expiresAt) return problem(410, 'TICKET_EXPIRED');
  if (DONE_RETEST.has(t.entry)) return problem(409, 'RETEST_NOT_DUE');
  return { status: 200, body: { status: 'ok' } };
}

/** 宵禁时只找回原结果：这个键已有结果就原样回，否则 404（不执行） */
function rreplay(b: Record<string, unknown>) {
  if (!exact(b, ['student_ref', 'subject', 'ticket', 'idempotency_key', 'usage_id'])) return problem(400, 'INVALID_REQUEST');
  const done = typeof b['idempotency_key'] === 'string' ? KEYS.get(b['idempotency_key']) : undefined;
  if (!done || done.student !== b['student_ref']) return problem(404, 'REPLAY_NOT_FOUND');
  return { ...done, replayed: true };
}

// ---------- 今日计划跨科编排（PLT-11；e2e/today-orch.spec.ts）：POST /internal/v1/today（today-internal.v0.yaml 的响应形状） ----------
// 剧本（学生 P1；其余学生空列表）：R2 验证 due（复测页）、E6 再练 due（再练页）、一条没有节点的计划任务（科目页）；截掉 2 条；
// 有一个来源不可用（partial）。条目引用对得上上面两份剧本里仍可作答的条目（R2、E6 作答后仍是 due）。
// 响应故意带上内部字段（优先级、单元层次、掌握、置信度、证据 ID、任务原因、预算与来源状态编码）：端到端断言它们不出现在页面上。
function today(b: Record<string, unknown>) {
  const keys = Object.keys(b).sort().join(',');
  if (keys !== 'request_id,student_ref,subjects' || typeof b['student_ref'] !== 'string' || !UUID.test(b['student_ref'])
      || !Array.isArray(b['subjects'])) return problem(400, 'INVALID_REQUEST');
  const internal = { priority: 0.9, cognitive_level: 'apply', p_mastery: 0.42, confidence: 0.77, evidence_ids: [id('9e1')], reason: 'verify_self_report' };
  // 今日计划快照（PLT-15 PR2，e2e/today-orch.spec.ts）：P1 当天快照已冻结，每条带任务 ID（7a1–7a3；端到端在库里以超级用户写同 ID 的快照行，
  // 写入方本是学习服务）；P3 有一条计划任务但没冻结（not_frozen，不带任务 ID，页面不显示“完成”“先跳过”）；P2 没有条目、没冻结
  const items = b['student_ref'] === P1 ? [
    { subject: 'aya', kind: 'verify', node_id: 'MATH.SYN.P1.R2', estimated_minutes: 5, source: 'retest', entry_id: id('402'), rationale_code: null, task_id: id('7a1'), ...internal },
    { subject: 'aya', kind: 'practice', node_id: 'MATH.SYN.P1.N6', estimated_minutes: 8, source: 'practice', entry_id: id('106'), rationale_code: null, task_id: id('7a2'), ...internal },
    { subject: 'aya', kind: 'lesson', node_id: null, estimated_minutes: 5, source: 'plan', entry_id: null, rationale_code: 'high_exam_weight', task_id: id('7a3'), ...internal },
  ] : b['student_ref'] === P3 ? [
    { subject: 'aya', kind: 'lesson', node_id: null, estimated_minutes: 5, source: 'plan', entry_id: null, rationale_code: 'high_exam_weight', ...internal },
  ] : [];
  const snapshot = b['student_ref'] === P1 ? 'frozen' : 'not_frozen';
  // 各科计划（与 /internal/v1/plan 同形状）：P1 的数学有一条没有节点的微课任务（BFF 用它排按科 sections，跨科列表里那条沿用它的理由）
  const plan = b['student_ref'] !== P1 ? null : { subjects: [{ subject: 'aya', status: 'ok', tasks: [
    { kind: 'lesson', target_node_ids: [], priority: 0.3, estimated_minutes: 5, rationale_code: 'high_exam_weight' }] }] };
  // P1：有一个启用了但失败的来源（partial）；其余学生只有未启用的来源（source_disabled，不算 partial）
  const sources = b['student_ref'] === P1
    ? [{ source: 'retest', subject: 'aya', status: 'ok' }, { source: 'plan', subject: 'aya', status: 'source_unavailable' }]
    : [{ source: 'retest', subject: 'aya', status: 'source_disabled' }, { source: 'plan', subject: 'aya', status: 'ok' }];
  return { status: 200, body: { snapshot, items, truncated_count: b['student_ref'] === P1 ? 2 : 0, budget_minutes: 30, sources, plan } };
}

const PRACTICE_PATHS = ['/internal/v1/practice/queue', '/internal/v1/practice/next', '/internal/v1/practice/answer'];
const RETEST_PATHS = ['/internal/v1/retest/queue', '/internal/v1/retest/next', '/internal/v1/retest/answer', '/internal/v1/retest/check',
                      '/internal/v1/retest/replay'];

createServer((req, res) => {
  void (async () => {
    let out: { status: number; body: unknown; replayed?: boolean };
    const path = req.url ?? '';
    if (REQUEST_LOG) appendFileSync(REQUEST_LOG, `${JSON.stringify({ method: req.method ?? '', path: path.slice(0, 200) })}\n`);
    const sessionPath = /^\/internal\/v1\/(diagnosis|practice|retest|today)(\/|\?|$)/.test(path);  // 与学习服务的四组会话接口一致
    const auth = req.headers['authorization'];
    if (auth !== `Bearer ${sessionPath ? TOKEN : WRITE_TOKEN}`) out = problem(401, 'UNAUTHENTICATED');
    else if (req.method === 'POST' && path === '/internal/v1/today') {
      const b = await readBody(req);
      out = b ? today(b) : problem(400, 'INVALID_REQUEST');
    } else if (req.method !== 'POST' || ![...PRACTICE_PATHS, ...RETEST_PATHS].includes(path)) {
      out = problem(501, 'NOT_IMPLEMENTED');
    } else {
      const b = await readBody(req);
      const retest = RETEST_PATHS.includes(path);
      out = !b ? problem(400, 'INVALID_REQUEST')
        : path.endsWith('/check') ? rcheck(b) : path.endsWith('/replay') ? rreplay(b)
        : path.endsWith('/queue') ? (retest ? rqueue(b) : queue(b)) : path.endsWith('/next') ? (retest ? rnext(b) : next(b)) : (retest ? ranswer(b) : answer(b));
    }
    const headers: Record<string, string> = { 'content-type': out.status === 200 ? 'application/json' : 'application/problem+json' };
    if ((path.endsWith('/answer') || path.endsWith('/replay')) && out.status === 200) headers['idempotent-replayed'] = out.replayed ? 'true' : 'false';
    res.writeHead(out.status, headers);
    res.end(JSON.stringify(out.body));
  })();
}).listen(PORT, '127.0.0.1');
