// H5 星图 v1 读模块（specs/star-map-v1-h5/spec.md；统一壳 spec 第 7.2、8 节）：星 = 当前已发布地图里 active、level = node 的知识节点，
// 判断先于展示。挂在开关 starmap_v1 后（默认关，src/star-map/features.ts）；v0（src/star-map/query.ts）不动。
//
// 一次请求分四步，持有事务时不等待任何 HTTP（lessons PR 1 给 PR 2 的约束 4）：
//   1. withAccount 只读事务：platform.can_read_student 确认可读（不可见 → null，调用方回 404，且不调学习服务）；
//      取在读学科（subject_enrollment.status = 'active'，英语与 planned 不取）、BFF 能读到的已发布地图版本与节点（领域、主题、知识节点）；
//      家长另取绑定关系 ID（insight.explain 的 actor.link_id）。
//   2. 事务外：只为“在读且 BFF 看得到已发布地图”的学科调学习服务 star-map/nodes（student_ref 只来自已校验的路径参数，短超时）。
//   3. 第二个 withAccount 只读事务：再确认一次可读；按 RLS（claim_front_read）读这些节点上的判断（受众可见、成立 / 假设
//      [虚线开关开时另含待验证]、未到期、模板已审核、依据是掌握记录）；虚线开关开时读待验证星的下一次验证任务（只取日期）。
//   4. 事务外：画星（paintSky，纯函数）；星数说明、迁移摘要的文字只取 insight.explain 的 RenderedStatement.text，
//      再过禁用推断词表（规则 12，做法同周报：命中即不显示该字段，词表读不到则一句都不显示）。
//
// 画星（7.2 节三条件 + lessons 约束 5）：一颗星只有当它上面有一条“知识掌握”维度的判断——模板在对外白名单里、受众可见、
// 成立或假设、basis_id 等于学习服务给的当前掌握记录——才按 state 画三态与“保持”外圈并带 claim_id；否则是中性底星，
// 响应里只有 node_id、name、display = neutral（不出现 state、star_lit、basis_id）。basis_id 永不下发。
// “知识迁移”维度的判断不画星，只用于该科的迁移摘要位。
// 学习服务 409 MASTERY_UNAVAILABLE、不可用、地图版本或星集合与 BFF 看到的不一致：该科星全部中性（不下发任何星态）。
// no_map 或 BFF 看不到已发布地图：“地图建设中”（status = building，不调学习服务 / 不画星）。
import { withAccount } from '../metering/db.ts';
import type { Query, ShellDeps } from '../http/routes.ts';
import type { ShellConfig } from '../shell/config.ts';
import type { Audience } from '../shell/insight-client.ts';
import { linkIdFor, resolveStatements, templateRef, type ShownStatement, type StatementJob } from '../shell/statements.ts';
import { forbiddenHits, lexicon } from '../trust/lexicon.ts';
import { toDisplay, type Display } from './display-state.ts';
import type { StarMapSubject, StarNode, StarNodesOutcome } from './nodes-client.ts';

/** 学科显示顺序（与选科、今日计划同一顺序；英语搁置） */
const ORDER = ['math', 'chinese', 'politics'];
const SOLID = new Set(['hypothesis', 'established']);

export interface ClaimRow {
  id: string;
  subject: string;
  target_id: string;
  template_key: string;
  template_version: number;
  status: 'hypothesis' | 'established' | 'to_verify';
  basis_id: string | null;
}

export type PaintedStar =
  | { node_id: string; display: 'neutral' }
  | { node_id: string; display: Display; retained: boolean; claim_id: string }
  | { node_id: string; display: 'dashed'; claim_id: string };

export interface PaintRules {
  /** 对外模板白名单（config/shell.json external_claim_templates） */
  external: ReadonlySet<string>;
  /** “知识掌握”维度的模板（画星、星数说明） */
  mastery: ReadonlySet<string>;
  /** “知识迁移”维度的模板（只用于迁移摘要位） */
  transfer: ReadonlySet<string>;
  /** starmap_dashed */
  dashed: boolean;
}

export interface SkyPaint {
  stars: PaintedStar[];
  /** 画了星的“知识掌握”判断（按 claims 的顺序，即最近更新在前）：星数说明的候选 */
  masteryClaims: ClaimRow[];
  /** 依据对得上当前掌握记录的“知识迁移”判断：迁移摘要的候选 */
  transferClaims: ClaimRow[];
}

/**
 * 按 7.2 节三条件给一科的星上色（纯函数）。nodes 来自学习服务；claims 已在库里按受众、状态、到期、模板审核过滤，最近更新在前。
 * 这里再核：模板在白名单里且属于对应维度、状态、basis_id 等于该节点当前掌握记录。
 */
export function paintSky(nodes: readonly StarNode[], claims: readonly ClaimRow[], rules: PaintRules): SkyPaint {
  const byNode = new Map<string, ClaimRow[]>();
  for (const c of claims) {
    const list = byNode.get(c.target_id) ?? [];
    list.push(c);
    byNode.set(c.target_id, list);
  }
  const stars: PaintedStar[] = [];
  const masteryClaims: ClaimRow[] = [];
  const transferClaims: ClaimRow[] = [];
  for (const node of nodes) {
    // 依据必须是学习服务给的当前掌握记录：掌握已更新、判断还没跟上时不画旧状态（条件 3）
    const matching = (byNode.get(node.node_id) ?? []).filter((c) => node.basis_id !== null && c.basis_id === node.basis_id
      && rules.external.has(templateRef(c.template_key, c.template_version)));
    const mastery = matching.filter((c) => rules.mastery.has(templateRef(c.template_key, c.template_version)));
    const solid = mastery.find((c) => SOLID.has(c.status));
    for (const c of matching) {
      if (rules.transfer.has(templateRef(c.template_key, c.template_version)) && SOLID.has(c.status)) transferClaims.push(c);
    }
    if (solid) {
      const d = toDisplay(node.state, null);
      stars.push({ node_id: node.node_id, display: d.display, retained: d.retained, claim_id: solid.id });
      masteryClaims.push(solid);
      continue;
    }
    const pending = rules.dashed ? mastery.find((c) => c.status === 'to_verify') : undefined;
    if (pending) {
      stars.push({ node_id: node.node_id, display: 'dashed', claim_id: pending.id });
      continue;
    }
    stars.push({ node_id: node.node_id, display: 'neutral' });
  }
  return { stars, masteryClaims, transferClaims };
}

// ---------------------------------------------------------------- 响应形状（openapi/star-map.v1.yaml）

export type OutStar =
  | { node_id: string; name: string; display: 'neutral' }
  | { node_id: string; name: string; display: Display; retained: boolean; claim_id: string }
  | { node_id: string; name: string; display: 'dashed'; claim_id: string; next_check: { due_on: string } | null };
interface OutTopic { topic_id: string; name: string | null; stars: OutStar[] }
interface OutDomain { domain_id: string; name: string | null; topics: OutTopic[] }
type OutSky =
  | { subject: string; status: 'building' }
  | { subject: string; status: 'ok'; map_version: string; domains: OutDomain[]; summary?: ShownStatement; transfer_summary?: ShownStatement };
export interface StarMapV1 {
  student_id: string;
  generated_at: string;
  /** 能力视图侧栏：签字前只有占位（统一壳 #8；字段字典 inner.g4.skill_view 只给 agent），不发数据请求 */
  skill_view: { status: 'coming_soon' };
  skies: OutSky[];
}

interface MapRow { subject: string; id: string; level: 'domain' | 'topic' | 'node'; name: string; parent_id: string | null }
interface Enrolled { code: string; product: StarMapSubject }

/** 上海时区的日期（下一次验证任务只给日期） */
const shanghaiDay = (d: Date): string => new Date(d.getTime() + 8 * 3600_000).toISOString().slice(0, 10);

async function readable(q: Query, id: string): Promise<boolean> {
  const { rows } = await q<{ ok: boolean }>('SELECT platform.can_read_student($1::uuid) AS ok', [id]);
  return rows[0]?.ok === true;
}

/** 星（level = node）按主题、领域分组；分组名取 BFF 读到的已发布地图（读不到的分组名为 null） */
function group(stars: OutStar[], rows: Map<string, MapRow>): OutDomain[] {
  const domains = new Map<string, OutDomain>();
  for (const s of [...stars].sort((a, b) => (a.node_id < b.node_id ? -1 : a.node_id > b.node_id ? 1 : 0))) {
    const topicId = rows.get(s.node_id)?.parent_id ?? s.node_id.split('.').slice(0, 3).join('.');
    const domainId = rows.get(topicId)?.parent_id ?? topicId.split('.').slice(0, 2).join('.');
    let d = domains.get(domainId);
    if (!d) {
      d = { domain_id: domainId, name: rows.get(domainId)?.name ?? null, topics: [] };
      domains.set(domainId, d);
    }
    let t = d.topics.find((x) => x.topic_id === topicId);
    if (!t) {
      t = { topic_id: topicId, name: rows.get(topicId)?.name ?? null, stars: [] };
      d.topics.push(t);
    }
    t.stars.push(s);
  }
  return [...domains.values()];
}

/**
 * 读星图 v1。学生不存在或当前会话无权读：返回 null（调用方回 404），此时没有调用学习服务与洞察服务。
 * note：记日志的额外字段（只放枚举值）。
 */
export async function getStarMapV1(deps: ShellDeps, cfg: ShellConfig, session: { accountId: string; token: string; kind: 'guardian' | 'student' },
                                   studentId: string, dashed: boolean, note: (k: string, v: string) => void): Promise<StarMapV1 | null> {
  const audience: Audience = session.kind === 'student' ? 'student' : 'guardian';
  // 一、授权与地图（只读事务；不在这里等 HTTP）
  const first = await withAccount(deps.pool, session.accountId, async (c) => {
    const q: Query = (sql, p) => c.query(sql, p);
    await c.query('SET TRANSACTION READ ONLY');
    if (!(await readable(q, studentId))) return null;
    const { rows: enrolled } = await q<Enrolled>(
      `SELECT c.code, c.product::text AS product FROM platform.subject_enrollment e
         JOIN platform.subject_catalog c ON c.product = e.subject
        WHERE e.student_id = $1 AND e.status = 'active' AND c.status <> 'planned' AND c.product IN ('aya', 'wenli', 'zhixing')
        ORDER BY array_position($2::text[], c.code) NULLS LAST, c.code`, [studentId, ORDER]);
    const products = enrolled.map((e) => e.product);
    const { rows: versions } = products.length === 0 ? { rows: [] as { subject: string; version: string }[] }
      : await q<{ subject: string; version: string }>(
        `SELECT v.subject::text AS subject, v.version FROM kge.map_version v
          WHERE v.status = 'published' AND v.subject = ANY ($1::platform.subject[])`, [products]);
    const { rows: mapRows } = versions.length === 0 ? { rows: [] as MapRow[] } : await q<MapRow>(
      `SELECT v.subject::text AS subject, n.id, n.level, n.name, n.parent_id
         FROM kge.map_version v JOIN kge.knowledge_node n ON n.map_version_id = v.id
        WHERE v.status = 'published' AND n.status = 'active' AND n.level IN ('domain', 'topic', 'node')
          AND v.subject = ANY ($1::platform.subject[])
        ORDER BY n.id`, [versions.map((v) => v.subject)]);
    return { enrolled, versions: new Map(versions.map((v) => [v.subject, v.version])), mapRows,
             linkId: audience === 'guardian' ? await linkIdFor(q, studentId) : undefined };
  });
  if (!first || (audience === 'guardian' && !first.linkId)) return null;

  // 二、学习服务的星态：只为在读、且 BFF 看得到已发布地图的学科调用（事务外，短超时，不重试、不缓存）
  const mapped = first.enrolled.filter((e) => first.versions.has(e.product));
  const client = deps.starMap?.nodes;
  const outcomes = new Map<string, StarNodesOutcome>(await Promise.all(mapped.map(async (e) =>
    [e.product, client ? await client.nodes(studentId, e.product) : { kind: 'unavailable' as const }] as [string, StarNodesOutcome])));
  note('star_nodes', mapped.map((e) => `${e.product}:${outcomes.get(e.product)!.kind}`).join(',') || 'none');

  // 星集合以学习服务为准，并与 BFF 读到的已发布地图核对（版本号与节点集合都要一致，否则该科全部中性）
  const rowsBy = new Map(first.mapRows.map((r) => [`${r.subject}:${r.id}`, r]));
  const bffNodes = (subject: string) => first.mapRows.filter((r) => r.subject === subject && r.level === 'node').map((r) => r.id).sort();
  const learned = new Map<string, StarNode[]>();
  for (const e of mapped) {
    const o = outcomes.get(e.product)!;
    if (o.kind !== 'ok') continue;
    const ours = bffNodes(e.product);
    const theirs = o.nodes.map((n) => n.node_id).sort();
    if (o.mapVersion !== first.versions.get(e.product) || ours.length !== theirs.length || ours.some((id, i) => id !== theirs[i])) {
      note('star_nodes_mismatch', e.product);
      continue;
    }
    learned.set(e.product, o.nodes);
  }

  // 三、判断（第二个只读事务；RLS claim_front_read，再显式按受众、状态、到期、模板审核过滤）与待验证星的下一次验证任务
  const withBasis = [...learned.entries()].flatMap(([, nodes]) => nodes.filter((n) => n.basis_id !== null).map((n) => n.node_id));
  const statuses = dashed ? ['hypothesis', 'established', 'to_verify'] : ['hypothesis', 'established'];
  const second = await withAccount(deps.pool, session.accountId, async (c) => {
    const q: Query = (sql, p) => c.query(sql, p);
    await c.query('SET TRANSACTION READ ONLY');
    if (!(await readable(q, studentId))) return null;
    const { rows: claims } = withBasis.length === 0 ? { rows: [] as ClaimRow[] } : await q<ClaimRow>(
      `SELECT c.id, c.subject::text AS subject, c.target_id, c.template_key, c.template_version, c.status::text AS status, c.basis_id
         FROM insight.claim c
         JOIN insight.claim_template t ON t.template_key = c.template_key AND t.version = c.template_version AND t.status = 'approved'
        WHERE c.student_id = $1 AND c.target_type = 'node' AND c.basis_kind = 'mastery_estimate'
          AND c.subject = ANY ($2::platform.subject[]) AND c.target_id = ANY ($3::text[])
          AND c.status::text = ANY ($4::text[]) AND $5 = ANY (c.visibility) AND c.expires_at > now()
        ORDER BY c.updated_at DESC, c.id`, [studentId, [...learned.keys()], [...new Set(withBasis)], statuses, audience]);
    const pendingNodes = dashed ? [...new Set(claims.filter((x) => x.status === 'to_verify').map((x) => x.target_id))] : [];
    const { rows: checks } = pendingNodes.length === 0 ? { rows: [] as { subject: string; node_id: string; due_at: Date }[] }
      : await q<{ subject: string; node_id: string; due_at: Date }>(
        `SELECT r.subject::text AS subject, r.node_id, min(r.due_at) AS due_at FROM platform.retest_task r
          WHERE r.student_id = $1 AND r.status = 'open' AND r.subject = ANY ($2::platform.subject[]) AND r.node_id = ANY ($3::text[])
          GROUP BY r.subject, r.node_id`, [studentId, [...learned.keys()], pendingNodes]);
    return { claims, checks: new Map(checks.map((r) => [`${r.subject}:${r.node_id}`, shanghaiDay(r.due_at)])) };
  });
  if (!second) return null;

  // 四、画星与分组；星数说明、迁移摘要的文字经 insight.explain（每页上限、整页时限、每会话限频，超时不显示）
  const rules: PaintRules = { external: cfg.externalTemplates, mastery: cfg.starMap.masteryTemplates, transfer: cfg.starMap.transferTemplates, dashed };
  const jobs: StatementJob[] = [];
  const toJob = (list: ClaimRow[]): number | null => {
    if (list.length === 0) return null;
    jobs.push({ key: jobs.length, claims: list.slice(0, 3).map((x) => ({ id: x.id, target_id: x.target_id, template_key: x.template_key,
      template_version: x.template_version, status: x.status as 'hypothesis' | 'established', basis_id: x.basis_id })) });
    return jobs.length - 1;
  };
  const built: { sky: OutSky; summaryJob: number | null; transferJob: number | null }[] = [];
  for (const e of first.enrolled) {
    const version = first.versions.get(e.product);
    const o = outcomes.get(e.product);
    if (version === undefined || !o || o.kind === 'no_map') {
      built.push({ sky: { subject: e.code, status: 'building' }, summaryJob: null, transferJob: null });
      continue;
    }
    const nodes = learned.get(e.product);
    const nameOf = (id: string): string => rowsBy.get(`${e.product}:${id}`)?.name ?? id;
    let stars: OutStar[];
    let summaryJob: number | null = null;
    let transferJob: number | null = null;
    if (!nodes) {
      // 409 MASTERY_UNAVAILABLE、学习服务不可用、与 BFF 看到的地图不一致：全部中性底星（不下发任何星态）
      stars = bffNodes(e.product).map((id) => ({ node_id: id, name: nameOf(id), display: 'neutral' as const }));
    } else {
      const paint = paintSky(nodes, second.claims.filter((c) => c.subject === e.product), rules);
      stars = paint.stars.map((s): OutStar => {
        const name = nameOf(s.node_id);
        if (s.display === 'neutral') return { node_id: s.node_id, name, display: 'neutral' };
        if (s.display === 'dashed') {
          const due = second.checks.get(`${e.product}:${s.node_id}`);
          return { node_id: s.node_id, name, display: 'dashed', claim_id: s.claim_id, next_check: due ? { due_on: due } : null };
        }
        return { node_id: s.node_id, name, display: s.display, retained: s.retained, claim_id: s.claim_id };
      });
      summaryJob = toJob(paint.masteryClaims);
      transferJob = toJob(paint.transferClaims);
    }
    const subjectRows = new Map([...rowsBy].filter(([k]) => k.startsWith(`${e.product}:`)).map(([k, v]) => [k.slice(e.product.length + 1), v]));
    built.push({ sky: { subject: e.code, status: 'ok', map_version: version, domains: group(stars, subjectRows) }, summaryJob, transferJob });
  }
  const shown = await resolveStatements(deps, cfg, deps.shell?.insight, session.token,
    { student: studentId, audience, actor: session.accountId, ...(first.linkId ? { linkId: first.linkId } : {}) }, jobs);
  // 禁用推断词表（规则 12；做法同周报）：陈述文字再过一次词表，命中就不显示该字段；词表读不到时一句都不显示（fail closed）
  let clean: (text: string) => boolean;
  try {
    const lex = lexicon();
    clean = (text) => forbiddenHits(text, lex).length === 0;
  } catch {
    note('star_statements', 'lexicon_unavailable');
    clean = () => false;
  }
  let dropped = 0;
  const kept = (job: number | null): ShownStatement | undefined => {
    const st = job === null ? undefined : shown.get(job);
    if (st && !clean(st.text)) {
      dropped += 1;
      return undefined;
    }
    return st;
  };
  const skies = built.map(({ sky, summaryJob, transferJob }) => {
    if (sky.status !== 'ok') return sky;
    const summary = kept(summaryJob);
    const transfer = kept(transferJob);
    return { ...sky, ...(summary ? { summary } : {}), ...(transfer ? { transfer_summary: transfer } : {}) };
  });
  if (dropped > 0) note('dropped_statements', String(dropped));
  return { student_id: studentId, generated_at: (deps.now ? deps.now() : new Date()).toISOString(), skill_view: { status: 'coming_soon' }, skies };
}
