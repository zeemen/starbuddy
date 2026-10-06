// 端到端用的合成学习服务桩（specs/practice-loop-stage1 PR 4：错题再练学生端页面，e2e/practice.spec.ts）：只在 scripts/e2e.sh 的一次性环境里起，
// 只监听 127.0.0.1，只认 BFF 的服务间令牌（FAKE_LEARNING_TOKEN，每次随机生成、只经环境变量传）。
// 只实现错题再练的三个内部接口（services/learning/openapi/practice-internal.v0.yaml）：queue、next、answer，按该文件的响应形状返回；
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
const TOKEN = process.env['FAKE_LEARNING_TOKEN'] ?? '';
const REQUEST_LOG = process.env['FAKE_LEARNING_REQUEST_LOG'] ?? '';
if (!PORT || TOKEN.length < 32) {
  console.error('fake-learning-practice: 需要 FAKE_LEARNING_PORT 与至少 32 个字符的 FAKE_LEARNING_TOKEN');
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

createServer((req, res) => {
  void (async () => {
    let out: { status: number; body: unknown; replayed?: boolean };
    const path = req.url ?? '';
    if (REQUEST_LOG) appendFileSync(REQUEST_LOG, `${JSON.stringify({ method: req.method ?? '', path: path.slice(0, 200) })}\n`);
    if (req.headers['authorization'] !== `Bearer ${TOKEN}`) out = problem(401, 'UNAUTHENTICATED');
    else if (req.method !== 'POST' || !['/internal/v1/practice/queue', '/internal/v1/practice/next', '/internal/v1/practice/answer'].includes(path)) {
      out = problem(501, 'NOT_IMPLEMENTED');
    } else {
      const b = await readBody(req);
      out = !b ? problem(400, 'INVALID_REQUEST') : path.endsWith('/queue') ? queue(b) : path.endsWith('/next') ? next(b) : answer(b);
    }
    const headers: Record<string, string> = { 'content-type': out.status === 200 ? 'application/json' : 'application/problem+json' };
    if (path.endsWith('/answer') && out.status === 200) headers['idempotent-replayed'] = out.replayed ? 'true' : 'false';
    res.writeHead(out.status, headers);
    res.end(JSON.stringify(out.body));
  })();
}).listen(PORT, '127.0.0.1');
