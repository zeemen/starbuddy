// 端到端用的合成洞察服务桩（SIE-02b 第二步 PR 2：首份报告开发演示页，e2e/first-report.spec.ts）：只在 scripts/e2e.sh 的一次性环境里起，
// 只监听 127.0.0.1，只认 BFF 的服务间令牌（FAKE_INSIGHT_TOKEN，每次随机生成、只经环境变量传）。
// 只实现 insight.report、insight.explain 与 insight.dispute 三个工具，返回契约（packages/contracts/openapi/insight-service.v0.9.yaml）形状的统一信封：
// 学生 R1（db/seeds/dev/first-report-synthetic.sql）的四条合成判断（报告与解释）；学生 GT（db/seeds/dev/weekly-synthetic.sql）的六条合成判断
// （解释与异议，specs/why-page-e2e：f1 成立、f2 假设、f3 已推翻、f4 已撤回、f5 待验证；f6 的解释故意回 503，核对读取不可用的降级）。
// 其余学生、判断一律 404（与“没有报告”“无权”同一个），其他工具 501。异议只按契约形状受理（不落库、不模拟洞察服务的二次授权核对）。
// 信封外层故意带上 confidence、evidence_ids、source_version、model_version：端到端断言它们不出现在页面上（BFF 只转发 result）。
// 不是洞察服务的实现：真实的报告生成、两道检查与授权再核对（0044）在 services/insight，有它自己的测试；文字全是明显占位。
// 桩不模拟 0044 insight.verify_actor（不查库、不核对 actor 与学生的绑定是否有效）：只核对请求形状——student_ref 必须是 UUID；
// 受众 guardian 时 actor.link_id 必须是 UUID（BFF 按档案授权后才带上），不合一律 400。授权本身由 BFF 与数据库（RLS）把关，端到端只验这一侧。
import { createServer, type IncomingMessage } from 'node:http';

const PORT = Number(process.env['FAKE_INSIGHT_PORT'] ?? 0);
const TOKEN = process.env['FAKE_INSIGHT_TOKEN'] ?? '';
if (!PORT || TOKEN.length < 32) {
  console.error('fake-insight: 需要 FAKE_INSIGHT_PORT 与至少 32 个字符的 FAKE_INSIGHT_TOKEN');
  process.exit(2);
}

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const R1 = '5c000000-0000-4000-8000-0000000000c1';
const TPL_M = 'syn.report.node_mastery';
const TPL_T = 'syn.report.node_transfer';
interface Claim { id: string; template: string; status: 'hypothesis' | 'established' | 'to_verify'; band: 'few_evidence' | 'moderate' | 'strong'; node: string; text: string }
const CLAIMS: Claim[] = [
  { id: '5c000000-0000-4000-8000-000000000201', template: TPL_M, status: 'established', band: 'strong', node: 'MATH.SYN.R1.N1',
    text: '【占位模板·未审核】MATH.SYN.R1.N1｜知识掌握' },
  { id: '5c000000-0000-4000-8000-000000000202', template: TPL_M, status: 'hypothesis', band: 'few_evidence', node: 'MATH.SYN.R1.N2',
    text: '【占位模板·未审核】MATH.SYN.R1.N2｜知识掌握' },
  { id: '5c000000-0000-4000-8000-000000000203', template: TPL_T, status: 'established', band: 'moderate', node: 'MATH.SYN.R1.N1',
    text: '【占位模板·未审核】MATH.SYN.R1.N1｜知识迁移' },
  { id: '5c000000-0000-4000-8000-000000000204', template: TPL_M, status: 'to_verify', band: 'moderate', node: 'MATH.SYN.R1.N3',
    text: '【占位模板·未审核】MATH.SYN.R1.N3｜知识掌握' },
];
const EVIDENCE = '5c000000-0000-4000-8000-000000000301';

// GT 的六条（与周报种子同一组判断 ID、模板与状态；状态以库为准，BFF 会再核一次，不一致即 503）
const GT = '5d000000-0000-4000-8000-0000000000c1';
const TPL_W = 'syn.weekly.node_mastery';
const GT_CLAIMS: (Omit<Claim, 'status'> & { status: Claim['status'] | 'refuted' | 'withdrawn' })[] = [
  { id: '5d000000-0000-4000-8000-0000000000f1', template: TPL_W, status: 'established', band: 'strong', node: 'MATH.FUNC.QUAD.GRAPH',
    text: '【占位模板·未审核】MATH.FUNC.QUAD.GRAPH｜transfer' },
  { id: '5d000000-0000-4000-8000-0000000000f2', template: TPL_W, status: 'hypothesis', band: 'few_evidence', node: 'MATH.FUNC.QUAD.AXIS',
    text: '【占位模板·未审核】MATH.FUNC.QUAD.AXIS｜execute' },
  { id: '5d000000-0000-4000-8000-0000000000f3', template: TPL_W, status: 'refuted', band: 'moderate', node: 'MATH.FUNC.QUAD.ROOTS',
    text: '【占位模板·未审核】MATH.FUNC.QUAD.ROOTS｜transfer' },
  { id: '5d000000-0000-4000-8000-0000000000f4', template: TPL_W, status: 'withdrawn', band: 'moderate', node: 'MATH.FUNC.QUAD.VERTEX',
    text: '【占位模板·未审核】MATH.FUNC.QUAD.VERTEX｜execute' },
  { id: '5d000000-0000-4000-8000-0000000000f5', template: TPL_W, status: 'to_verify', band: 'moderate', node: 'MATH.FUNC.QUAD.SHIFT',
    text: '【占位模板·未审核】MATH.FUNC.QUAD.SHIFT｜transfer' },
  { id: '5d000000-0000-4000-8000-0000000000f6', template: TPL_W, status: 'established', band: 'moderate', node: 'MATH.FUNC.QUAD.DOMAIN',
    text: '【占位模板·未审核】MATH.FUNC.QUAD.DOMAIN｜execute' },
];
const GT_OUTAGE = '5d000000-0000-4000-8000-0000000000f6';
const DISPUTABLE = new Set(['established', 'hypothesis']);
const NEXT_STEP: Record<string, string> = { i_know: 'verification_scheduled', one_off_slip: 'observe_followups', data_error: 'open_fact_for_correction' };
let disputeSeq = 0;

const envelope = (result: unknown) => ({
  contract_version: '0.9.0', result, evidence_ids: [EVIDENCE], confidence: 0.4,
  source_version: 'algo:sie-report-fake@0.1.0', model_version: 'algo:sie-report-fake@0.1.0', human_review_required: false,
});

function report(audience: 'student' | 'guardian') {
  const sentence = (c: Claim) => ({
    claim_id: c.id, template_key: c.template, template_version: 1, status: c.status, display: 'solid', text: c.text,
    ...(c.status === 'hypothesis' ? { label: 'preliminary' } : {}), ...(audience === 'guardian' ? { confidence_band: c.band } : {}),
  });
  const mastery = CLAIMS.filter((c) => c.template === TPL_M && c.status !== 'to_verify');
  const transfer = CLAIMS.filter((c) => c.template === TPL_T && c.status !== 'to_verify');
  return {
    format: 'sie-first-report/0.1', audience, policy_version: 'syn-fake-0.1.0',
    notice: '【占位·待人审】首份报告顶部提示语', title: `【占位·待人审】首份报告标题（${audience === 'guardian' ? '家长版' : '学生版'}）`,
    sections: [{ key: 'node_mastery', connective: '【占位衔接·甲】', sentences: mastery.map(sentence) },
               { key: 'node_transfer', connective: '【占位衔接·乙】', sentences: transfer.map(sentence) }],
    claim_ids: [...mastery, ...transfer].map((c) => c.id),
  };
}

function explain(c: Pick<Claim, 'id' | 'template' | 'status' | 'band' | 'node' | 'text'>) {
  return {
    statement: { claim_id: c.id, template_key: c.template, template_version: 1, status: c.status, display: c.status === 'to_verify' ? 'dashed' : 'solid',
                 text: c.text, confidence_band: c.band },
    evidence_chain: [{ evidence_id: EVIDENCE, occurred_at: '2026-10-01T08:00:00+08:00', source_type: 'objective', evidence_level: 'A', node_id: c.node }],
    confidence_band: c.band, expires_at: '2026-11-01T00:00:00+08:00',
  };
}

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

const problem = (status: number, code: string) => ({ status, body: { type: `/problems/${code.toLowerCase()}`, title: code, status, code } });

createServer((req, res) => {
  void (async () => {
    let out: { status: number; body: unknown };
    if (req.headers['authorization'] !== `Bearer ${TOKEN}`) out = problem(401, 'UNAUTHENTICATED');
    else if (req.method !== 'POST' || !['/v0/insight.report', '/v0/insight.explain', '/v0/insight.dispute'].includes(req.url ?? '')) {
      out = problem(501, 'NOT_IMPLEMENTED');
    }
    else {
      const b = await readBody(req);
      const ctx = b?.['context'] as Record<string, unknown> | undefined;
      const actor = ctx?.['actor'] as Record<string, unknown> | undefined;
      const audience = ctx?.['audience'];
      if (!ctx || ctx['caller'] !== 'bff' || ctx['contract_version'] !== '0.9.0' || (audience !== 'student' && audience !== 'guardian')
          || !actor || actor['role'] !== audience || typeof actor['actor_ref'] !== 'string'
          || typeof ctx['student_ref'] !== 'string' || !UUID.test(ctx['student_ref'])
          || (audience === 'guardian' && (typeof actor['link_id'] !== 'string' || !UUID.test(actor['link_id'])))) {
        out = problem(400, 'INVALID_REQUEST');
      } else if (ctx['student_ref'] === GT) {
        const c = GT_CLAIMS.find((x) => x.id === b?.['claim_id']);
        if (req.url === '/v0/insight.report' || !c) out = problem(404, 'NOT_FOUND');
        else if (req.url === '/v0/insight.explain') {
          // 已推翻、已撤回与不存在同一个 404（BFF 在库里先挡，到不了这里）；f6 回 503
          out = c.id === GT_OUTAGE ? problem(503, 'UPSTREAM_UNAVAILABLE')
            : c.status === 'refuted' || c.status === 'withdrawn' ? problem(404, 'NOT_FOUND')
            : { status: 200, body: envelope(explain(c as Claim)) };
        } else {
          const kind = b?.['kind'];
          const step = typeof kind === 'string' ? NEXT_STEP[kind] : undefined;
          if (!step || typeof ctx['idempotency_key'] !== 'string') out = problem(400, 'INVALID_REQUEST');
          else if (!DISPUTABLE.has(c.status)) out = problem(409, 'STATE_CONFLICT');
          else {
            disputeSeq += 1;
            const result: Record<string, string> = {
              dispute_id: `5d0d0000-0000-4000-8000-${String(disputeSeq).padStart(12, '0')}`, next_step: step };
            if (step === 'verification_scheduled') result['verify_by'] = new Date(Date.now() + 7 * 86_400_000).toISOString();
            if (step === 'open_fact_for_correction') result['fact_ref'] = 'attempt:syn-fact-000001';
            out = { status: 200, body: envelope(result) };
          }
        }
      } else if (ctx['student_ref'] !== R1 || req.url === '/v0/insight.dispute') {
        out = problem(404, 'NOT_FOUND');
      } else if (req.url === '/v0/insight.report') {
        out = { status: 200, body: envelope(report(audience)) };
      } else {
        const c = CLAIMS.find((x) => x.id === b?.['claim_id']);
        out = c ? { status: 200, body: envelope(explain(c)) } : problem(404, 'NOT_FOUND');
      }
    }
    res.writeHead(out.status, { 'content-type': out.status === 200 ? 'application/json' : 'application/problem+json' });
    res.end(JSON.stringify(out.body));
  })();
}).listen(PORT, '127.0.0.1');
