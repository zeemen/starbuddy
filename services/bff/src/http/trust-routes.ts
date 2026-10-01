// SIE-03 PR③：信任界面的 BFF 读写接口（specs/sie-03-dispute-reconcile/spec.md 范围 3；页面随冲刺 5-H）。
// 每条路由挂在一个功能开关上（src/trust/features.ts：dispute / reconcile / weekly / data_rights），默认关闭：关闭时不注册，
// 与表外路径同一个 404（4-H“信任界面预留”验收继续成立）。
//   异议（dispute）：提出 → 洞察服务 insight.dispute；查看自己提出的异议与状态（0032 RLS：只读自己提出的，家长须有效绑定）。
//   考试（reconcile）：录入考试安排（insight.submit_exam_plan）、按安排录成绩（insight.submit_exam_score）；读预测与对账卡。
//   周报（weekly，冲刺 5-H）：读最近一周已生成的周报（0036 insight.weekly_report，按受众只读），每句在库里再核判断。
//   数据权利（data_rights，只给家长）：导出（0035 platform.export_student_data，BFF 暂存、一次性兑换下载）；
//     删除申请 → 会话内二次确认 → 冷静期内撤回；查看状态与回执（回执只有表名与行数）。
// 与其他路由同一套规矩：身份只来自会话；路径里的 student_id 只作查询条件，授权在库里（RLS、SECURITY DEFINER 函数），
// 无权与不存在同一个 404；改变状态的一律 Origin + 会话 CSRF；限流桶在 config/auth.json；响应一律 no-store（server.ts）。
// 禁用推断（规则 11、12）：BFF 不生成任何关于学生的文字。异议类型、结论、偏差方向、调整只传编码；前端能看到的句子只来自判断
// （每句带 claim_id，并在库里核对该判断对当前受众可见、成立或假设、未过期、模板在对外白名单里），其余句子丢弃并记计数。
import { withAccount } from '../metering/db.ts';
import { loadShellConfig, type ShellConfig } from '../shell/config.ts';
import type { Audience } from '../shell/insight-client.ts';
import { linkIdFor, templateRef } from '../shell/statements.ts';
import { DISPUTE_KINDS, IDEMPOTENCY_KEY, pickPrediction, pickReconciliation, type DisputeKind, type Who } from '../trust/clients.ts';
import { MemoryExportStore, type ExportStore } from '../trust/exports.ts';
import { forbiddenHits, lexicon } from '../trust/lexicon.ts';
import { pickObjective, pickWeeklySentence, WEEKLY_SECTIONS, type WeeklySentence } from '../trust/weekly.ts';
import { recentLogins, trustMac } from '../trust/proof.ts';
import { P } from './problems.ts';
import type { Ctx, Handler, Query, Reply, Route, ShellDeps } from './routes.ts';
import { logHash, safeEqual } from './secrets.ts';

const json = (status: number, v: unknown, headers?: Record<string, string>): Reply => ({ status, body: JSON.stringify(v), ...(headers ? { headers } : {}) });
const problem = (body: string): Reply => ({ status: (JSON.parse(body) as { status: number }).status, body });
const pgCode = (e: unknown): string => (e && typeof e === 'object' && 'code' in e ? String((e as { code: unknown }).code) : '');
const student = (ctx: Ctx): string => ctx.params['student_id']!;
const audienceOf = (ctx: Ctx): Audience => (ctx.session!.kind === 'student' ? 'student' : 'guardian');
/** 请求体只能恰好是这些键（未知键 422，不静默忽略） */
const exactKeys = (b: Record<string, unknown> | null, keys: string[]): b is Record<string, unknown> =>
  b !== null && Object.keys(b).sort().join(',') === [...keys].sort().join(',');
/** 洞察服务单次调用的时限（与 insight.explain 的单次时限同一处配置） */
let defaultConfig: ShellConfig | null = null;
const shellConfig = (deps: ShellDeps): ShellConfig => deps.shell?.config ?? (defaultConfig ??= loadShellConfig());
const timeoutOf = (deps: ShellDeps): number => shellConfig(deps).explain.timeoutMs;
const nowMs = (deps: ShellDeps): number => (deps.now ? deps.now() : new Date()).getTime();

/** 取值不合要求的库错误码（与建档同一组） */
const INVALID_CODES = new Set(['22023', '23514', '22P02', '22007', '22008', '23502', '22003']);

async function readable(q: Query, id: string): Promise<boolean> {
  const { rows } = await q('SELECT 1 FROM platform.student_profile s WHERE s.id = $1', [id]);
  return rows.length > 0;
}

/** 当前会话、受众下可见的判断（RLS 之外再显式要求：属于该学生、可见范围含受众、未过期、模板已审核），带模板引用 */
async function visibleClaims(q: Query, id: string, audience: Audience, claimIds: string[], statuses: string[])
  : Promise<Map<string, string>> {
  if (claimIds.length === 0) return new Map();
  const { rows } = await q<{ id: string; template_key: string; template_version: number }>(
    `SELECT c.id, c.template_key, c.template_version FROM insight.claim c
       JOIN insight.claim_template t ON t.template_key = c.template_key AND t.version = c.template_version AND t.status = 'approved'
      WHERE c.id = ANY ($1::uuid[]) AND c.student_id = $2 AND $3 = ANY (c.visibility) AND c.expires_at > now()
        AND c.status::text = ANY ($4::text[])`, [[...new Set(claimIds)], id, audience, statuses]);
  return new Map(rows.map((r) => [r.id, templateRef(r.template_key, r.template_version)]));
}

// ---------------------------------------------------------------- 异议（dispute）

/** 与 insight.raise_dispute 同一条件：对提出人可见、非终态、未过期、模板已审核 */
const DISPUTABLE = ['hypothesis', 'established', 'to_verify'];

const disputeCreate: Handler = async (ctx) => {
  const b = ctx.body;
  if (!exactKeys(b, ['kind', 'idempotency_key'])) return problem(P.INVALID);
  const kind = b['kind'];
  const key = b['idempotency_key'];
  if (typeof kind !== 'string' || !(DISPUTE_KINDS as readonly string[]).includes(kind) || typeof key !== 'string' || !IDEMPOTENCY_KEY.test(key)) {
    return problem(P.INVALID);
  }
  const s = ctx.session!;
  // “那次只是失误”只能由学生本人提出（规则 6，0032 dispute_slip_self）：家长会话在任何查询之前拒绝
  if (kind === 'one_off_slip' && s.kind !== 'student') return problem(P.FORBIDDEN);
  const id = student(ctx);
  const claimId = ctx.params['claim_id']!;
  const audience = audienceOf(ctx);
  const pre = await withAccount(ctx.deps.pool, s.accountId, async (c) => {
    const q: Query = (sql, p) => c.query(sql, p);
    if (!(await readable(q, id))) return null;
    if (!(await visibleClaims(q, id, audience, [claimId], DISPUTABLE)).has(claimId)) return null;
    return { linkId: audience === 'guardian' ? await linkIdFor(q, id) : undefined };
  });
  if (!pre || (audience === 'guardian' && !pre.linkId)) return problem(P.NOT_FOUND);
  const client = ctx.deps.trust?.dispute;
  if (!client) return problem(P.TRUST_UNAVAILABLE);
  // 幂等键按账号派生（信任界面专用密钥的 HMAC）：不同账号用同一个键不会撞上洞察服务里别人的缓存；原键不出 BFF
  const idem = `bff.${trustMac(ctx.deps.keys, 'dispute-idem', `${s.accountId}:${key}`).slice(0, 48)}`;
  const who: Who = { studentRef: id, audience, actorRef: s.accountId, ...(pre.linkId ? { linkId: pre.linkId } : {}) };
  const out = await client.dispute({ ...who, claimId, kind: kind as DisputeKind, idempotencyKey: idem }, timeoutOf(ctx.deps));
  ctx.note('outcome', out.kind);
  switch (out.kind) {
    case 'ok':
      // 只有编码与时间：不转发 fact_ref（原始事实的内部引用）与验证题引用（接口桩的占位）
      return json(200, { dispute_id: out.value.dispute_id, kind, next_step: out.value.next_step, verify_by: out.value.verify_by });
    case 'not_found': return problem(P.NOT_FOUND);
    case 'conflict': return problem(P.STATE_CONFLICT);
    case 'too_many': return problem(P.WRITE_CAP);
    case 'invalid': return problem(P.INVALID);
    default: return problem(P.TRUST_UNAVAILABLE);
  }
};

const disputeList: Handler = async (ctx) => {
  const id = student(ctx);
  if (!(await readable(ctx.q, id))) return problem(P.NOT_FOUND);
  // 0032：BFF 只有这几列的读权限，RLS 只放行自己提出的（家长须有效绑定）；这里再显式按提出人过滤
  const { rows } = await ctx.q<{ dispute_id: string; claim_id: string; kind: string; status: string; result_code: string | null;
                                 created_at: Date; resolved_at: Date | null }>(
    `SELECT d.id AS dispute_id, d.claim_id, d.kind, d.status, d.result_note_code AS result_code, d.created_at, d.resolved_at
       FROM insight.dispute d
      WHERE d.student_id = $1 AND d.raised_by = platform.current_account_id()
      ORDER BY d.created_at DESC, d.id LIMIT 100`, [id]);
  return json(200, {
    student_id: id,
    disputes: rows.map((r) => ({ dispute_id: r.dispute_id, claim_id: r.claim_id, kind: r.kind, status: r.status, result_code: r.result_code,
                                 created_at: r.created_at.toISOString(), resolved_at: r.resolved_at ? r.resolved_at.toISOString() : null })),
  });
};

// ---------------------------------------------------------------- 考试录入、预测与对账卡（reconcile）

const DAY = /^\d{4}-\d{2}-\d{2}$/;
const SUBJECT = /^[a-z]{2,16}$/;
const EXAM_TYPES = ['monthly', 'midterm', 'final', 'mock1', 'mock2', 'other'];
const NODE = /^[A-Z][A-Z0-9]{1,9}(\.[A-Z0-9][A-Z0-9_]{0,31}){1,4}$/;

const writeError = (e: unknown): Reply => {
  const code = pgCode(e);
  if (code === '42501') return problem(P.NOT_FOUND);
  if (INVALID_CODES.has(code)) return problem(P.INVALID);
  if (code === '54000') return problem(P.WRITE_CAP);
  if (code === '23505') return problem(P.CONFLICT);
  throw e;
};

const examPlan: Handler = async (ctx) => {
  const b = ctx.body;
  if (!exactKeys(b, ['subject', 'exam_type', 'exam_date', 'full_score', 'scope_node_ids'])) return problem(P.INVALID);
  const nodes = b['scope_node_ids'];
  if (typeof b['subject'] !== 'string' || !SUBJECT.test(b['subject']) || typeof b['exam_type'] !== 'string' || !EXAM_TYPES.includes(b['exam_type'])
      || typeof b['exam_date'] !== 'string' || !DAY.test(b['exam_date'])
      || typeof b['full_score'] !== 'number' || !Number.isInteger(b['full_score']) || b['full_score'] < 1 || b['full_score'] > 300
      || !Array.isArray(nodes) || nodes.length < 1 || nodes.length > 64 || !nodes.every((n) => typeof n === 'string' && NODE.test(n))
      || new Set(nodes).size !== nodes.length) {
    return problem(P.INVALID);
  }
  try {
    // 库函数核对学生本人 / 有效绑定家长（无权 42501）、日期一年内、满分、范围节点；同一场考试已有安排时返回原安排
    const { rows } = await ctx.q<{ id: string }>(
      'SELECT insight.submit_exam_plan($1, $2::platform.subject, $3, $4::date, $5, $6::text[]) AS id',
      [student(ctx), b['subject'], b['exam_type'], b['exam_date'], b['full_score'], nodes]);
    return json(201, { exam_id: rows[0]!.id });
  } catch (e) {
    return writeError(e);
  }
};

const examScore: Handler = async (ctx) => {
  const b = ctx.body;
  if (!exactKeys(b, ['raw_score'])) return problem(P.INVALID);
  const score = b['raw_score'];
  if (typeof score !== 'number' || !Number.isFinite(score) || score < 0 || score > 300) return problem(P.INVALID);
  try {
    const { rows } = await ctx.q<{ id: string }>('SELECT insight.submit_exam_score($1, $2, $3) AS id', [student(ctx), ctx.params['exam_id'], score]);
    return json(201, { score_id: rows[0]!.id });
  } catch (e) {
    return writeError(e);
  }
};

/** 预测与对账卡的前置：能读到这个学生、家长的绑定关系 */
async function examWho(ctx: Ctx): Promise<Who | null> {
  const s = ctx.session!;
  const id = student(ctx);
  const audience = audienceOf(ctx);
  const linkId = await withAccount(ctx.deps.pool, s.accountId, async (c) => {
    const q: Query = (sql, p) => c.query(sql, p);
    if (!(await readable(q, id))) return null;
    return audience === 'guardian' ? (await linkIdFor(q, id)) ?? null : '';
  });
  if (linkId === null) return null;
  return { studentRef: id, audience, actorRef: s.accountId, ...(linkId ? { linkId } : {}) };
}

/** 判断先于展示：库里核对这些判断对当前受众可见、成立或假设、未过期、模板已审核，且模板在对外白名单里 */
async function shownClaims(ctx: Ctx, claimIds: string[]): Promise<Map<string, string>> {
  const cfg = shellConfig(ctx.deps);
  const vis = await withAccount(ctx.deps.pool, ctx.session!.accountId, (c) =>
    visibleClaims((sql, p) => c.query(sql, p), student(ctx), audienceOf(ctx), claimIds, ['hypothesis', 'established']));
  return new Map([...vis].filter(([, ref]) => cfg.externalTemplates.has(ref)));
}

const prediction: Handler = async (ctx) => {
  const who = await examWho(ctx);
  if (!who) return problem(P.NOT_FOUND);
  const client = ctx.deps.trust?.exams;
  if (!client) return problem(P.TRUST_UNAVAILABLE);
  const examId = ctx.params['exam_id']!;
  const p = pickPrediction(await client.prediction({ ...who, examRecordId: examId }, timeoutOf(ctx.deps)).catch(() => null));
  // 预测对应一条考试类判断：该判断对当前受众不可见（例如只给 agent）时不显示区间
  const shown = p ? await shownClaims(ctx, [p.claim_id]) : new Map<string, string>();
  if (p && !shown.has(p.claim_id)) ctx.note('prediction', 'claim_not_shown');
  const view = p && shown.has(p.claim_id) ? p : null;
  return json(200, {
    student_id: student(ctx), exam_id: examId,
    // 冻结（考试当天零点，上海）之后区间只读；BFF 没有任何改预测的入口
    prediction: view ? { ...view, frozen: Date.parse(view.frozen_at) <= nowMs(ctx.deps) } : null,
  });
};

const reconciliation: Handler = async (ctx) => {
  const who = await examWho(ctx);
  if (!who) return problem(P.NOT_FOUND);
  const client = ctx.deps.trust?.exams;
  if (!client) return problem(P.TRUST_UNAVAILABLE);
  const examId = ctx.params['exam_id']!;
  const req = { ...who, examRecordId: examId };
  const [rawCard, rawPrediction] = await Promise.all([client.reconciliation(req, timeoutOf(ctx.deps)).catch(() => null),
                                                      client.prediction(req, timeoutOf(ctx.deps)).catch(() => null)]);
  const picked = pickReconciliation(rawCard);
  const pred = pickPrediction(rawPrediction);
  const none = json(200, { student_id: student(ctx), exam_id: examId, reconciliation: null });
  if (!picked) return none;
  const { card } = picked;
  let dropped = picked.dropped;
  // 每句都要对上一条可见、成立或假设、白名单内的判断，模板也要对上；实线才显示。
  // 卡片本身依据的预测（prediction_id → 预测所引的考试类判断）也要对当前受众可见，否则整张卡不显示（安全审查 M3）
  const shown = await shownClaims(ctx, [...card.statements.map((s) => s.claim_id), ...(pred ? [pred.claim_id] : [])]);
  if (!pred || pred.prediction_id !== card.prediction_id || !shown.has(pred.claim_id)) {
    ctx.note('reconciliation', 'prediction_claim_not_shown');
    return none;
  }
  const statements = card.statements.filter((s) => shown.get(s.claim_id) === templateRef(s.template_key, s.template_version) && s.display === 'solid');
  dropped += card.statements.length - statements.length;
  if (dropped > 0) ctx.note('dropped_statements', String(dropped));
  return json(200, {
    student_id: student(ctx), exam_id: examId,
    reconciliation: {
      prediction_id: card.prediction_id, actual_score: card.actual_score, within_interval: card.within_interval,
      deviation_nodes: card.deviation_nodes, deviation_directions: card.deviation_directions, adjustments: card.adjustments,
      statements: statements.map((s) => ({ claim_id: s.claim_id, text: s.text, confidence_band: s.confidence_band })),
    },
  });
};

// ---------------------------------------------------------------- 周报（weekly，冲刺 5-H 跨科周报 v0）

/**
 * 周报句子引用的判断：属于该学生、对当前受众可见、成立或假设、未过期、知识节点、模板已审核、学生版不收来源为家长观察的，
 * 而且判断的 updated_at 等于句子的修订标记（生成之后被修订过的判断，它的句子丢弃，安全审查 F1）。返回当前状态与来源，
 * “初步判断”标注按当前状态推导；置信度分档沿用快照里的（修订标记对得上 = 置信度没变；分档阈值在洞察服务的策略里，BFF 不另存一份）
 */
interface WeeklyClaim { ref: string; status: string; source_type: string }
async function weeklyClaims(q: Query, id: string, audience: Audience, sentences: WeeklySentence[]): Promise<Map<string, WeeklyClaim>> {
  if (sentences.length === 0) return new Map();
  const { rows } = await q<{ id: string; template_key: string; template_version: number; status: string; source_type: string }>(
    `SELECT c.id, c.template_key, c.template_version, c.status::text AS status, c.source_type::text AS source_type
       FROM unnest($1::uuid[], $2::timestamptz[]) AS x(id, rev)
       JOIN insight.claim c ON c.id = x.id AND c.updated_at = x.rev
       JOIN insight.claim_template t ON t.template_key = c.template_key AND t.version = c.template_version AND t.status = 'approved'
      WHERE c.student_id = $3 AND $4 = ANY (c.visibility) AND c.expires_at > now()
        AND c.status IN ('hypothesis', 'established') AND c.target_type = 'node'
        AND ($4 <> 'student' OR c.source_type <> 'guardian')`,
    [sentences.map((s) => s.claim_id), sentences.map((s) => s.claim_revision), id, audience]);
  return new Map(rows.map((r) => [r.id, { ref: templateRef(r.template_key, r.template_version), status: r.status, source_type: r.source_type }]));
}

const weeklyReport: Handler = async (ctx) => {
  const id = student(ctx);
  if (!(await readable(ctx.q, id))) return problem(P.NOT_FOUND);
  const audience = audienceOf(ctx);
  // 0036 RLS：学生本人只读学生版、有效绑定家长只读家长版，只读已生成的；这里再显式按受众与状态过滤
  const { rows } = await ctx.q<{ week_start: string; week_end: string; sentences: unknown; objective: unknown; generated_at: Date }>(
    `SELECT to_char(w.week_start, 'YYYY-MM-DD') AS week_start, to_char(w.week_end, 'YYYY-MM-DD') AS week_end, w.sentences, w.objective,
            w.generated_at
       FROM insight.weekly_report w
      WHERE w.student_id = $1 AND w.audience = $2 AND w.status = 'generated'
      ORDER BY w.week_start DESC LIMIT 1`, [id, audience]);
  const none = json(200, { student_id: id, report: null });
  const w = rows[0];
  if (!w) return none;
  const raws = Array.isArray(w.sentences) ? w.sentences.slice(0, 40) : [];
  const picked = raws.map((r) => pickWeeklySentence(r, audience)).filter((x): x is WeeklySentence => x !== null);
  // 判断先于展示（规则 11）：每句在库里再核判断（含修订标记），再过对外模板白名单，模板键与版本也要对得上；文字再过禁用推断词表
  const cfg = shellConfig(ctx.deps);
  let clean: (text: string) => boolean;
  try {
    const lex = lexicon();
    clean = (text) => forbiddenHits(text, lex).length === 0;
  } catch {
    ctx.note('weekly', 'lexicon_unavailable');  // 词表读不到：一句都不显示（fail closed）
    return none;
  }
  const shown = await weeklyClaims(ctx.q, id, audience, picked);
  const kept = picked.filter((s) => {
    const c = shown.get(s.claim_id);
    const ref = templateRef(s.template_key, s.template_version);
    return c !== undefined && c.ref === ref && cfg.externalTemplates.has(ref) && clean(s.text);
  });
  const dropped = raws.length - kept.length;
  if (dropped > 0) ctx.note('dropped_statements', String(dropped));
  if (kept.length === 0) return none;
  const sections = WEEKLY_SECTIONS[audience]
    .map((key) => ({ key, sentences: kept.filter((s) => s.section === key).slice(0, 10).map((s) => {
      const c = shown.get(s.claim_id)!;
      return { claim_id: s.claim_id, text: s.text, source_type: c.source_type, ...(c.status === 'hypothesis' ? { label: 'preliminary' } : {}),
               ...(s.confidence_band ? { confidence_band: s.confidence_band } : {}) };
    }) }))
    .filter((sec) => sec.sentences.length > 0);
  return json(200, {
    student_id: id,
    report: { week_start: w.week_start, week_end: w.week_end, generated_at: w.generated_at.toISOString(), sections, objective: pickObjective(w.objective) },
  });
};

// ---------------------------------------------------------------- 导出与删除（data_rights，只给家长）

const exportStores = new WeakMap<ShellDeps, ExportStore>();
const exportStore = (deps: ShellDeps): ExportStore => {
  if (deps.trust?.exports) return deps.trust.exports;
  let s = exportStores.get(deps);
  if (!s) {
    s = new MemoryExportStore();
    exportStores.set(deps, s);
  }
  return s;
};

const DOWNLOAD_REF = /^dl_[0-9a-f]{64}$/;

/** 删除请求已确认（冷静期中）或已执行、学生已删除：不再导出、不再兑换（安全审查 M1） */
async function erasureBlocked(q: Query, id: string): Promise<boolean> {
  const { rows } = await q<{ blocked: boolean }>(
    `SELECT EXISTS (SELECT 1 FROM platform.student_profile s WHERE s.id = $1 AND s.erased_at IS NOT NULL)
         OR EXISTS (SELECT 1 FROM platform.erasure_request r
                     WHERE r.student_id = $1 AND (r.status = 'executed' OR (r.status = 'pending' AND r.confirmed_at IS NOT NULL))) AS blocked`, [id]);
  return rows[0]?.blocked === true;
}

const exportCreate: Handler = async (ctx) => {
  const id = student(ctx);
  const s = ctx.session!;
  ctx.note('guardian_op', 'export');
  ctx.note('account', logHash(ctx.deps.keys, s.accountId));
  let out: { jobId: string; ref: string; expiresAt: string; body: string } | 'blocked';
  try {
    out = await withAccount(ctx.deps.pool, s.accountId, async (c) => {
      const q: Query = (sql, p) => c.query(sql, p);
      if (await erasureBlocked(q, id)) return 'blocked' as const;
      // 库函数只认有效绑定家长（规则 5）：建导出任务（审计）、按清单列白名单取数、返回一次性下载凭据
      const { rows } = await c.query<{ r: Record<string, unknown> }>('SELECT platform.export_student_data($1) AS r', [id]);
      const r = rows[0]!.r;
      const { download_ref: _ref, ...pkg } = r;  // 凭据不进下载内容
      return { jobId: String(r['export_job_id']), ref: String(r['download_ref']), expiresAt: String(r['expires_at']), body: JSON.stringify(pkg) };
    });
  } catch (e) {
    if (pgCode(e) === '42501') return problem(P.NOT_FOUND);
    if (pgCode(e) === '54000') return problem(P.WRITE_CAP);
    throw e;
  }
  if (out === 'blocked') return problem(P.STATE_CONFLICT);
  ctx.note('export_job', out.jobId);
  // 事务提交之后才暂存（契约审查 M1）：放不进（条数、单包、总量上限）回 503，库里的导出任务照样留作审计
  if (!exportStore(ctx.deps).put(out.ref, { jobId: out.jobId, studentId: id, accountId: s.accountId, expiresAt: Date.parse(out.expiresAt), body: out.body },
                                 nowMs(ctx.deps))) {
    ctx.note('export_store', 'full');
    return problem(P.TRUST_UNAVAILABLE);
  }
  return json(201, { export_job_id: out.jobId, status: 'ready', expires_at: new Date(out.expiresAt).toISOString(), download_ref: out.ref });
};

const exportDownload: Handler = async (ctx) => {
  const b = ctx.body;
  if (!exactKeys(b, ['download_ref']) || typeof b['download_ref'] !== 'string' || !DOWNLOAD_REF.test(b['download_ref'])) return problem(P.INVALID);
  const id = student(ctx);
  const account = ctx.session!.accountId;
  const jobId = ctx.params['export_job_id']!;
  ctx.note('guardian_op', 'export_download');
  ctx.note('account', logHash(ctx.deps.keys, account));
  ctx.note('export_job', jobId);
  // 兑换时再核一次（安全审查 M1、L2）：仍是这个孩子的有效绑定家长；学生未删除、没有已确认或已执行的删除请求。
  // 不满足时清掉暂存（解除绑定：只清这位家长发起的；删除：清这个学生全部），与凭据不对同一个 404
  const { rows } = await ctx.q<{ linked: boolean }>('SELECT platform.is_linked_guardian($1) AS linked', [id]);
  if (rows[0]?.linked !== true) {
    exportStore(ctx.deps).purgeStudent(id, account);
    return problem(P.NOT_FOUND);
  }
  if (await erasureBlocked(ctx.q, id)) {
    exportStore(ctx.deps).purgeStudent(id);
    return problem(P.NOT_FOUND);
  }
  // 兑换留痕：库里的 export_job 对 BFF 不可读、也没有兑换记录列（迁移，必停，lessons）；访问日志记账号摘要与任务 ID
  const body = exportStore(ctx.deps).take(b['download_ref'], { jobId, studentId: id, accountId: account }, nowMs(ctx.deps));
  if (body === null) return problem(P.NOT_FOUND);  // 凭据不对、已用过、过期、不是本人发起：同一个 404
  return { status: 200, body, headers: { 'content-disposition': `attachment; filename="starbuddy-export-${jobId}.json"` } };
};

/** 删除的会话内二次确认凭据：绑定本次会话与这条删除请求（带密钥 HMAC）；换了会话、换了请求都对不上 */
const confirmTokenFor = (ctx: Ctx, requestId: string): string => trustMac(ctx.deps.keys, 'erasure-confirm', `${ctx.session!.token}:${requestId}`);
const CONFIRM_TOKEN = /^[0-9a-f]{64}$/;

const erasureError = (e: unknown): Reply => {
  const code = pgCode(e);
  if (code === '42501') return problem(P.NOT_FOUND);
  if (code === '23514') return problem(P.STATE_CONFLICT);  // 已删除过、已结束、确认超时、已执行不能撤回
  if (code === '54000') return problem(P.WRITE_CAP);
  throw e;
};

async function requestOf(q: Query, id: string, requestId: string): Promise<boolean> {
  const { rows } = await q('SELECT 1 FROM platform.erasure_request r WHERE r.id = $1 AND r.student_id = $2', [requestId, id]);
  return rows.length > 0;
}

const erasureRequest: Handler = async (ctx) => {
  ctx.note('guardian_op', 'erasure_request');
  try {
    const { rows } = await ctx.q<{ id: string }>('SELECT platform.request_erasure($1) AS id', [student(ctx)]);
    const requestId = rows[0]!.id;
    // 二次确认：默认要求在本会话内提交这张凭据（短信验证码复核现有接口不支持，lessons PR③ 待人确认）
    return json(201, { request_id: requestId, confirm_token: confirmTokenFor(ctx, requestId) });
  } catch (e) {
    return erasureError(e);
  }
};

const erasureConfirm: Handler = async (ctx) => {
  const b = ctx.body;
  if (!exactKeys(b, ['confirm_token']) || typeof b['confirm_token'] !== 'string' || !CONFIRM_TOKEN.test(b['confirm_token'])) return problem(P.INVALID);
  const requestId = ctx.params['request_id']!;
  ctx.note('guardian_op', 'erasure_confirm');
  if (!(await requestOf(ctx.q, student(ctx), requestId))) return problem(P.NOT_FOUND);
  // 本会话须在 10 分钟内经验证码登录过（安全审查 H1；短信复核需新库函数，必停）
  if (!recentLogins(ctx.deps).fresh(ctx.deps.keys, ctx.session!.token, nowMs(ctx.deps))) return problem(P.REAUTH_REQUIRED);
  if (!safeEqual(b['confirm_token'], confirmTokenFor(ctx, requestId))) return problem(P.CONFIRM_REQUIRED);
  try {
    // 库函数：只有发起人本人、仍有效绑定、发起后 24 小时内；确认后冷静期 7 天
    const { rows } = await ctx.q<{ until: Date }>('SELECT platform.confirm_erasure($1) AS until', [requestId]);
    exportStore(ctx.deps).purgeStudent(student(ctx));  // 删除已确认：清掉该学生未兑换的导出包
    return json(200, { request_id: requestId, cooling_off_until: rows[0]!.until.toISOString() });
  } catch (e) {
    return erasureError(e);
  }
};

const erasureCancel: Handler = async (ctx) => {
  const requestId = ctx.params['request_id']!;
  ctx.note('guardian_op', 'erasure_cancel');
  if (!(await requestOf(ctx.q, student(ctx), requestId))) return problem(P.NOT_FOUND);
  try {
    await ctx.q('SELECT platform.cancel_erasure($1)', [requestId]);
    return json(200, { request_id: requestId, status: 'cancelled' });
  } catch (e) {
    return erasureError(e);
  }
};

const iso = (d: Date | null): string | null => (d ? d.toISOString() : null);

const erasureList: Handler = async (ctx) => {
  const id = student(ctx);
  if (!(await readable(ctx.q, id))) return problem(P.NOT_FOUND);
  // 0035：BFF 只有这几列（没有发起人、撤回人）；RLS 只放行有效绑定家长。回执只有“表名 → 行数”与范围编码，不含被删内容
  const { rows } = await ctx.q<{ id: string; requested_at: Date; confirmed_at: Date | null; cooling_off_until: Date | null; status: string;
                                 cancelled_at: Date | null; cancel_reason: string | null; executed_at: Date | null;
                                 receipt: Record<string, number> | null; scopes: string[] | null }>(
    `SELECT r.id, r.requested_at, r.confirmed_at, r.cooling_off_until, r.status, r.cancelled_at, r.cancel_reason, r.executed_at, r.receipt, r.scopes
       FROM platform.erasure_request r WHERE r.student_id = $1 ORDER BY r.requested_at DESC, r.id LIMIT 20`, [id]);
  return json(200, {
    student_id: id,
    requests: rows.map((r) => ({
      request_id: r.id, status: r.status, requested_at: iso(r.requested_at), confirmed_at: iso(r.confirmed_at),
      cooling_off_until: iso(r.cooling_off_until), cancelled_at: iso(r.cancelled_at), cancel_reason: r.cancel_reason,
      executed_at: iso(r.executed_at), receipt: r.status === 'executed' ? { tables: r.receipt, scopes: r.scopes } : null,
    })),
  });
};

// ---------------------------------------------------------------- 路由

const S = '/students/{student_id}';
type Feature = NonNullable<Route['feature']>;
const read = (feature: Feature, path: string, auth: Route['auth'], handler: Handler, rateLimits: Route['rateLimits'] = [], ownTx = false): Route =>
  ({ feature, method: 'GET', path, auth, csrf: 'none', body: false, handler, rateLimits, ...(ownTx ? { ownTx } : {}) });
const write = (feature: Feature, path: string, auth: Route['auth'], body: boolean, handler: Handler, rateLimits: Route['rateLimits'], ownTx = false): Route =>
  ({ feature, method: 'POST', path, auth, csrf: 'session', body, handler, rateLimits, ...(ownTx ? { ownTx } : {}) });

export const TRUST_ROUTES: readonly Route[] = [
  write('dispute', `${S}/claims/{claim_id}/disputes`, 'session', true, disputeCreate, [{ bucket: 'dispute.account.hour', dim: 'account' }], true),
  read('dispute', `${S}/disputes`, 'session', disputeList, [{ bucket: 'trust_read.session.minute', dim: 'session' }]),
  write('reconcile', `${S}/exams`, 'session', true, examPlan, [{ bucket: 'exam_write.account.hour', dim: 'account' }]),
  write('reconcile', `${S}/exams/{exam_id}/score`, 'session', true, examScore, [{ bucket: 'exam_write.account.hour', dim: 'account' }]),
  read('reconcile', `${S}/exams/{exam_id}/prediction`, 'session', prediction, [{ bucket: 'exam_view.session.minute', dim: 'session' }], true),
  read('reconcile', `${S}/exams/{exam_id}/reconciliation`, 'session', reconciliation, [{ bucket: 'exam_view.session.minute', dim: 'session' }], true),
  read('weekly', `${S}/weekly-report`, 'session', weeklyReport, [{ bucket: 'trust_read.session.minute', dim: 'session' }]),
  write('data_rights', `${S}/exports`, 'guardian', false, exportCreate, [{ bucket: 'export.account.hour', dim: 'account' }], true),
  write('data_rights', `${S}/exports/{export_job_id}/download`, 'guardian', true, exportDownload, [{ bucket: 'export_download.account.hour', dim: 'account' }]),
  read('data_rights', `${S}/erasure`, 'guardian', erasureList, [{ bucket: 'trust_read.session.minute', dim: 'session' }]),
  write('data_rights', `${S}/erasure`, 'guardian', false, erasureRequest, [{ bucket: 'erasure.account.hour', dim: 'account' }]),
  write('data_rights', `${S}/erasure/{request_id}/confirm`, 'guardian', true, erasureConfirm, [{ bucket: 'erasure.account.hour', dim: 'account' }]),
  write('data_rights', `${S}/erasure/{request_id}/cancel`, 'guardian', false, erasureCancel, [{ bucket: 'erasure.account.hour', dim: 'account' }]),
];
