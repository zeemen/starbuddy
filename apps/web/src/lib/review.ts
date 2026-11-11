// 审核台（specs/release-gate-sprint4 冲刺 1 · PR 3）的后端客户端：只在服务端调用（服务端组件与 server action）。
// 浏览器只和本前端通信，不直连审核台后端（services/learning/review_console）或数据库。
// 身份：staff 登录会话（PR 4）。第一个参数是会话令牌，放在请求头 x-review-session 转交，由后端经库函数解析；签字、发布、撤下由库里的函数再核对 staff。
export const SUBJECTS = [
  { code: 'aya', name: '数学' },
  { code: 'zhixing', name: '道法' },
  { code: 'wenli', name: '语文' },
] as const;
export type SubjectCode = (typeof SUBJECTS)[number]['code'];
export const isSubject = (s: string): s is SubjectCode => SUBJECTS.some((x) => x.code === s);

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
export const isVersionId = (s: string): boolean => UUID_RE.test(s);
/** 与 kge.node_id 域一致 */
export const isNodeId = (s: string): boolean => /^[A-Z][A-Z0-9]{1,9}(\.[A-Z0-9][A-Z0-9_]{0,31}){1,4}$/.test(s);
export const isDigest = (s: string): boolean => /^[0-9a-f]{64}$/.test(s);
export const isBasis = (s: string): boolean => /^[a-z0-9_]{1,48}$/.test(s);

export interface MapVersion {
  id: string;
  subject: string;
  version: string;
  standard_year: number;
  status: 'draft' | 'published' | 'retired';
  created_at: string;
  published_at: string | null;
}

export interface Risk {
  level: 'low' | 'medium' | 'high';
  review_level: 'low' | 'medium' | 'high';
  reason: string;
  structural: boolean;
}

export interface Issue {
  check_name: string;
  severity: 'error' | 'warning';
  object_id: string;
  detail: string;
}

export interface Change {
  object: string;
  key: string;
  op: 'added' | 'removed' | 'changed' | 'deprecated' | 'reactivated';
  field: string | null;
  old: unknown;
  new: unknown;
  risk: Risk;
  issues: Issue[];
  rubric_version?: string;
}

/** 内容版本与考情方案的差异（后端 content.py）：不挂校验问题，没有评分版本 */
export type ContentChange = Omit<Change, 'issues' | 'rubric_version'>;

export interface ChangeRequest {
  id: string;
  change_type: string;
  status: 'proposed' | 'approved' | 'rejected' | 'applied';
  created_at: string;
  /** 签字覆盖的全部内容（与库里 kge.map_change_digest 的输入一致），页面原样显示 */
  signed_content: Record<string, unknown>;
  digest: string;
  risk: Risk;
}

export interface NodeRow {
  id: string;
  level: string;
  parent_id: string | null;
  kind: string | null;
  name: string;
  includes: string[];
  excludes: string[];
  examples: string[];
  cognitive_levels: string[];
  scope: string;
  error_type: string | null;
  status: string;
}

export interface Signoff {
  map_version_id: string;
  version: string;
  action: 'review' | 'publish' | 'retire';
  decision: 'approved' | 'rejected';
  risk_level: string;
  basis_code: string | null;
  digest: string | null;
  decided_at: string;
  signer: string;
}

export interface DiffView {
  version: MapVersion;
  base_version_id: string | null;
  base_version: string | null;
  digest: string;
  nodes: NodeRow[];
  summary: {
    level: string;
    review_level: string;
    structural: boolean;
    required_signers: number;
    counts: Record<string, number>;
    /** 最近一次驳回之后、对当前内容批准的不同教研人数（后端按发布函数的口径算） */
    approvals: number;
    latest_decision: 'approved' | 'rejected' | null;
    latest_matches: boolean;
  };
  impact: { item_tags: number; scoring_points: number };
  changes: Change[];
  validation: Issue[];
  unattached_issues: Issue[];
  change_requests: ChangeRequest[];
  signoffs: Signoff[];
}

export interface RecordRow extends Omit<Signoff, 'map_version_id' | 'version' | 'action'> {
  /** map：地图版本的签字、发布、撤下；page：编译页面的签字（冲刺 3）；policy：考情政策线的签字与发布（冲刺 4，按城市与届别，不分学科）；
   *  content_version：内容版本的签字、发布、撤下；admission：题目准入的签字与执行（冲刺 4） */
  line: 'map' | 'page' | 'policy' | 'content_version' | 'admission';
  action: 'review' | 'publish' | 'retire' | 'apply';
  map_version_id: string | null;
  version: string | null;
  rubric_versions: string;
  validation_errors: number | null;
  validation_warnings: number | null;
  page_id: string | null;
  page_commit: string | null;
  /** 考情行：source_document / exam_scheme / admission_line / school / control_line（迁移 0047） */
  policy_object: string | null;
  policy_object_id: string | null;
  city: string | null;
  cohort: number | null;
  /** 考情方案行的方案版本号（考情行的 version 一律为空，version 只给地图版本号） */
  policy_version: string | null;
  /** 内容版本与准入行（冲刺 4）：内容版本 ID、六段标识、所指地图版本、题目与准入变化、准入的学科审核等级（Q0–Q5；
   *  列名带 admission_ 前缀，免得与风险分级的 review_level 混读） */
  content_version_id: string | null;
  six_segment: string | null;
  content_map_version_id: string | null;
  item_id: string | null;
  from_admission: string | null;
  to_admission: string | null;
  admission_review_level: string | null;
}

// ---------- 内容版本与题目准入（冲刺 4，spec 第 7 节）：差异、签字、发布、撤下、准入签字与执行 ----------
export interface ContentVersionRow {
  id: string;
  region: string;
  exam_year: number;
  grade: number;
  textbook_version: string;
  subject: string;
  content_version: string;
  status: 'draft' | 'published' | 'retired';
  map_version_id: string | null;
  published_at: string | null;
  retired_at: string | null;
  skill_nodes: number;
  items: number;
  six_segment: string;
}

export interface ContentItem {
  item_id: string;
  skill_local_id: string | null;
  admission: string;
  source_ref: string | null;
  source_type: string;
  license_status: string;
  /** 签字时回传的题目登记摘要（platform.item_admission_digest） */
  admission_digest: string;
}

export interface ReleaseRecord {
  id: string;
  line: 'content_version' | 'admission';
  action: 'review' | 'publish' | 'retire' | 'apply';
  /** 内容版本 ID 与六段（platform.release_record_export，迁移 0042） */
  curriculum_version_id: string;
  region: string;
  exam_year: number;
  grade: number;
  textbook_version: string;
  content_version: string;
  map_version_id: string | null;
  item_id: string | null;
  decision: 'approved' | 'rejected';
  digest: string | null;
  basis_code: string | null;
  from_admission: string | null;
  to_admission: string | null;
  review_level: string | null;
  /** 准入执行行：执行的是哪一条审核 */
  applies_review_id: string | null;
  /** 准入执行降级时受影响的评分版本（0039） */
  affected_rubric_versions: string[] | null;
  decided_at: string;
  signer: string;
}

export interface Check {
  check_name: string;
  severity: 'error' | 'warning';
  detail: string;
}

/** 内容版本快照里的版本行：没有登记计数（列表行才有） */
export type ContentVersionHead = Omit<ContentVersionRow, 'skill_nodes' | 'items'>;

export interface ContentView {
  version: ContentVersionHead;
  base_version: { id: string; content_version: string; status: 'draft' | 'published' | 'retired' } | null;
  digest: string;
  map_version: { id: string; subject: string; version: string; status: 'draft' | 'published' | 'retired' } | null;
  summary: {
    level: string;
    review_level: string;
    required_signers: number;
    counts: Record<string, number>;
    admission_changes: number;
    latest_decision: 'approved' | 'rejected' | null;
    latest_matches: boolean;
    ready: boolean;
  };
  changes: ContentChange[];
  checks: Check[];
  items: ContentItem[];
  records: ReleaseRecord[];
  /** 最近一条是批准、还没执行的准入审核 */
  pending_admissions: ReleaseRecord[];
}

export const listContentVersions = (staff: string, subject: SubjectCode) =>
  fetchRoute(staff, 'GET /review/v0/content-versions', `/review/v0/content-versions?subject=${subject}`);
export const getContentView = (staff: string, id: string) => fetchRoute(staff, 'GET /review/v0/content-versions/{cv}/diff', `/review/v0/content-versions/${id}/diff`);
export const signContentVersion = (staff: string, id: string, body: ReviewRequests['POST /review/v0/content-versions/{cv}/sign']) =>
  send(staff, 'POST /review/v0/content-versions/{cv}/sign', `/review/v0/content-versions/${id}/sign`, body);
export const publishContentVersion = (staff: string, id: string, body: ReviewRequests['POST /review/v0/content-versions/{cv}/publish']) =>
  send(staff, 'POST /review/v0/content-versions/{cv}/publish', `/review/v0/content-versions/${id}/publish`, body);
export const retireContentVersion = (staff: string, id: string, body: ReviewRequests['POST /review/v0/content-versions/{cv}/retire']) =>
  send(staff, 'POST /review/v0/content-versions/{cv}/retire', `/review/v0/content-versions/${id}/retire`, body);
export interface AdmissionSign {
  subject: SubjectCode;
  item_id: string;
  to_admission: (typeof ADMISSIONS)[number];
  decision: Decision;
  review_level?: string;
  source_ref?: string;
  source_type?: (typeof SOURCE_TYPES)[number];
  license_status?: (typeof LICENSE_STATUSES)[number];
  expected_digest: string;
  basis_code?: string;
}
export const signAdmission = (staff: string, body: AdmissionSign) =>
  send(staff, 'POST /review/v0/admission/sign', '/review/v0/admission/sign', body);
export const applyAdmission = (staff: string, review_ids: string[]) =>
  send(staff, 'POST /review/v0/admission/apply', '/review/v0/admission/apply', { review_ids });

export const ADMISSIONS = ['source_unverified', 'pending_review', 'admitted', 'rejected'] as const;
export const ADMISSION_LABEL: Record<string, string> = {
  source_unverified: '来源待核', pending_review: '待学科审核', admitted: '已准入', rejected: '已拒绝',
};
export const SOURCE_TYPES = ['unknown', 'self_authored', 'adapted', 'textbook', 'exam_paper', 'reference_book'] as const;
export const LICENSE_STATUSES = ['unverified', 'cleared', 'denied'] as const;
const oneOf = <T extends string>(list: readonly T[]) => (s: unknown): s is T => typeof s === 'string' && (list as readonly string[]).includes(s);
export const isAdmission = oneOf(ADMISSIONS);
export const isSourceType = oneOf(SOURCE_TYPES);
export const isLicenseStatus = oneOf(LICENSE_STATUSES);
/** 与迁移 0039 platform.item.source_ref、platform.skill_local_id 一致 */
export const isSourceRef = (s: string): boolean => /^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$/.test(s);
export const isItemId = (s: string): boolean => /^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$/.test(s);
export const isReviewLevel = (s: string): boolean => /^Q[0-5]$/.test(s);

// ---------- 考情政策线（冲刺 4，spec 第 8 节）：方案差异、validate_scheme、两人签字、发布；来源文件、招录行、学校第二名批准时发布 ----------
export interface SchemeRow {
  id: string;
  city: string;
  cohort: number;
  version: string;
  status: 'draft' | 'published' | 'retired';
  source_document_id: string;
  published_at: string | null;
}
/** 方案列表的一行（多一个建立时间） */
export interface SchemeListRow extends SchemeRow { created_at: string }

/** 审核台能签字的考情记录类型（方案另走签字 + 发布）；control_line：指标生控制线（迁移 0047 单独建表） */
export const POLICY_RECORD_TYPES = ['source_document', 'admission_line', 'school', 'control_line'] as const;
export const isPolicyRecordType = (s: unknown): s is (typeof POLICY_RECORD_TYPES)[number] =>
  typeof s === 'string' && (POLICY_RECORD_TYPES as readonly string[]).includes(s);

export interface PolicyDraft {
  object_type: (typeof POLICY_RECORD_TYPES)[number];
  id: string;
  city: string;
  cohort: number | null;
  label: string;
  digest: string;
  approvals: number;
}

export interface SchemeSubject {
  subject: string;
  scored: boolean;
  /** 十进制字符串 */
  full_score: string | null;
  proposition: string;
  exam_form: string;
  duration_minutes: number | null;
  combined_group: string | null;
  taken_in_grade: number;
  exam_month: number | null;
  eligibility_effect: string;
}
export interface SchemeBatch { batch_code: string; batch_order: number; track: string; control_line_basis: string }
export interface SchemeTransition { change_kind: string; replaces_scheme_id: string | null }
/** 考情签字与发布记录（platform.policy_signoff_record） */
export interface PolicyRecord {
  object_type: string;
  object_id: string;
  city: string | null;
  version: string | null;
  cohort: number | null;
  action: string;
  decision: Decision;
  digest: string | null;
  signed_at: string;
  signer: string;
}

export interface PolicyView {
  scheme: SchemeRow;
  digest: string;
  total: string | null;
  subjects: SchemeSubject[];
  batches: SchemeBatch[];
  transitions: SchemeTransition[];
  previous_version: SchemeRow | null;
  previous_cohort: SchemeRow | null;
  diff_previous_version: ContentChange[];
  diff_previous_cohort: ContentChange[];
  validation: Issue[];
  summary: { level: string; review_level: string; required_signers: number; approvals: number; counts: Record<string, number> };
  records: PolicyRecord[];
}

export const isCity = (s: string): boolean => /^[a-z][a-z0-9-]{1,31}$/.test(s);
export const listPolicy = (staff: string, city?: string) =>
  fetchRoute(staff, 'GET /review/v0/policy', `/review/v0/policy${city ? `?city=${city}` : ''}`);
export const getPolicyView = (staff: string, id: string) => fetchRoute(staff, 'GET /review/v0/policy/schemes/{scheme}', `/review/v0/policy/schemes/${id}`);
export const signScheme = (staff: string, id: string, body: ReviewRequests['POST /review/v0/policy/schemes/{scheme}/sign']) =>
  send(staff, 'POST /review/v0/policy/schemes/{scheme}/sign', `/review/v0/policy/schemes/${id}/sign`, body);
export const publishScheme = (staff: string, id: string, body: ReviewRequests['POST /review/v0/policy/schemes/{scheme}/publish']) =>
  send(staff, 'POST /review/v0/policy/schemes/{scheme}/publish', `/review/v0/policy/schemes/${id}/publish`, body);
export const signPolicyRecord = (staff: string, id: string, body: ReviewRequests['POST /review/v0/policy/records/{object_id}/sign']) =>
  send(staff, 'POST /review/v0/policy/records/{object_id}/sign', `/review/v0/policy/records/${id}/sign`, body);
export const POLICY_OBJECT_LABEL: Record<string, string> = {
  source_document: '来源文件', exam_scheme: '方案', admission_line: '招录行', school: '学校', control_line: '控制线',
  scheme: '方案', total: '计分总分', subject: '科目', batch: '批次', transition: '过渡规则',
};

// ---------- 官方来源清单（迁移 0048，specs/policy-decisions-v0.9.1 第 4 节）：列表、新增（或重新启用）、下线 ----------
// 只给有 staff 权限 policy_source_admin 的账号（后端 403 SOURCE_ADMIN_REQUIRED）。主机名的规范化与合格判断在库函数里，这里只挡格式。
export interface SourceDomain {
  host: string;
  issuer: string;
  status: 'active' | 'retired';
  added_at: string;
}
export const listSourceDomains = (staff: string) => fetchRoute(staff, 'GET /review/v0/policy/sources', '/review/v0/policy/sources');
export const addSourceDomain = (staff: string, body: ReviewRequests['POST /review/v0/policy/sources']) =>
  send(staff, 'POST /review/v0/policy/sources', '/review/v0/policy/sources', body);
export const retireSourceDomain = (staff: string, body: ReviewRequests['POST /review/v0/policy/sources/retire']) =>
  send(staff, 'POST /review/v0/policy/sources/retire', '/review/v0/policy/sources/retire', body);
/** 表单里的主机名：只收 ASCII 的域名字符（国际化域名填 punycode，xn--…），1–253 字；去首尾空白、小写、结尾的点由库里规范化 */
export const isSourceHost = (s: string): boolean => /^[A-Za-z0-9.-]{1,253}\.?$/.test(s.trim()) && s.trim().length > 0;
/** 说明里不许出现的 Unicode 类别：控制字符（Cc，含 C1）、格式字符（Cf，含 U+202E 方向覆盖与零宽字符）、行分隔符、段分隔符 */
export const NOTE_FORBIDDEN = /[\p{Cc}\p{Cf}\p{Zl}\p{Zp}]/u;
/** 说明（发布机关）：1–64 字、没有上面这几类不可见或改方向的字符；写进公共层，不得填个人信息 */
export const isSourceNote = (s: string): boolean => {
  const t = s.trim();
  return t.length >= 1 && [...t].length <= 64 && !NOTE_FORBIDDEN.test(s);  // 查原文：trim 会吃掉首尾的 U+2028、U+FEFF
};
export const SOURCE_STATUS_LABEL: Record<string, string> = { active: '在用', retired: '已下线' };

// ---------- 编译页面签字与低风险事后抽检（冲刺 4，spec 第 2、6 节） ----------
export const isCommit = (s: string): boolean => /^[0-9a-f]{40}$/.test(s);
export const signPage = (staff: string, body: ReviewRequests['POST /review/v0/pages/sign']) =>
  send(staff, 'POST /review/v0/pages/sign', '/review/v0/pages/sign', body);

export interface SpotCheckView {
  configured: boolean;
  ratio: number;
  window: string;
  day: string;
  basis_code: string;
  /** 告警：ratio_zero（抽检比例配置为 0，一页不抽） */
  warnings: string[];
  candidates?: number;
  sampled?: { page_id: string; mr_id: string; head_commit: string; url: string; checks: { decision: string; signer: string; decided_at: string }[] }[];
}
export const isDay = (s: string): boolean => /^\d{4}-\d{2}-\d{2}$/.test(s) && !Number.isNaN(Date.parse(`${s}T00:00:00Z`));
export const getSpotChecks = (staff: string, day?: string) =>
  fetchRoute(staff, 'GET /review/v0/spot-checks', `/review/v0/spot-checks${day ? `?day=${day}` : ''}`);
export const signSpotCheck = (staff: string, body: ReviewRequests['POST /review/v0/spot-checks/sign']) =>
  send(staff, 'POST /review/v0/spot-checks/sign', '/review/v0/spot-checks/sign', body);

// ---------- 编译页面（冲刺 3，spec 第 6 节）：待审合并请求与对账状态；审核在托管平台网页上做，这里只读、只给链接 ----------
export interface PageEntry {
  page_id: string;
  mr_id: string;
  state: 'merged' | 'open';
  head_commit: string;
  merge_commit: string | null;
  risk: 'low' | 'medium' | 'high';
  required: number;
  approvals_platform: number;
  approvals_review: number;
  url: string;
  status?: 'ok' | 'failed';
  problems?: string[];
}

export interface PageOrphan {
  page_id: string;
  page_commit: string;
  records: number;
  problems: string[];
  /** 是否扣页：页面仍在本次导出范围内，且最新头提交上还没有批准记录 */
  blocking: boolean;
}

export interface PagesView {
  configured: boolean;
  /** 对账报告格式版本（pages.REPORT_FORMAT） */
  format?: string;
  repository?: string;
  branch?: string;
  exported_at?: string;
  ok?: boolean;
  pending?: PageEntry[];
  merged?: PageEntry[];
  /** 同一页面更早的合并（只按最近一次合并判定） */
  superseded?: { page_id: string; mr_id: string; head_commit: string; merge_commit: string | null }[];
  orphans?: PageOrphan[];
  held_pages?: string[];
}

export const getPages = (staff: string) => fetchRoute(staff, 'GET /review/v0/pages', '/review/v0/pages');

/** 托管平台链接只认 https（地址模板在后端配置里核对过，这里再挡一次） */
export const safeHttpsUrl = (s: string): string | null => {
  try {
    const u = new URL(s);
    return u.protocol === 'https:' && !u.username && !u.password ? u.toString() : null;
  } catch {
    return null;
  }
};

export const PAGE_PROBLEM_LABEL: Record<string, string> = {
  missing_review: '合并了但没有审核记录',
  no_merge: '审核记录对不上任何合并请求（提交号不符）',
  approver_mismatch: '托管平台的批准人与审核记录不一致',
  approver_unmapped: '批准人不在 staff 对应表里',
  insufficient_approvals: '批准人数不够',
  rejected_in_review: '审核记录里有驳回',
  spot_check_rejected: '事后抽检驳回',
  self_approval: '提交方自己批准或签字',
};
export const pageProblemLabel = (p: string): string => (Object.hasOwn(PAGE_PROBLEM_LABEL, p) ? PAGE_PROBLEM_LABEL[p]! : p);

export interface Problem {
  status: number;
  code: string;
  detail?: string;
}

export type Result<T> = { ok: true; value: T } | { ok: false; problem: Problem };

const PRIVATE_V4 = /^(10\.\d{1,3}\.\d{1,3}\.\d{1,3}|192\.168\.\d{1,3}\.\d{1,3}|172\.(1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3})$/;

/** 审核台后端只能是 loopback 上的 http（开发原型；PR 4 定部署拓扑）。LT-01 决定记录第 5 条：STARBUDDY_ENV=staging 时
 *  另收内网 IPv4 字面量的 http（容器网络，与 src/lib/bff.ts 的内网口径一致）；pilot、生产仍只收 loopback */
export function reviewBaseUrl(env: Record<string, string | undefined> = process.env): string {
  const raw = env['STARBUDDY_REVIEW_URL'] ?? 'http://127.0.0.1:8790';
  const u = new URL(raw);
  const loopback = ['127.0.0.1', 'localhost', '[::1]'].includes(u.hostname);
  const stagingPrivate = env['STARBUDDY_ENV'] === 'staging' && PRIVATE_V4.test(u.hostname)
    && u.hostname.split('.').every((p) => Number(p) <= 255 && !(p.length > 1 && p.startsWith('0')));
  if (u.protocol !== 'http:' || !(loopback || stagingPrivate) || u.username || u.password) {
    throw new Error('STARBUDDY_REVIEW_URL 只能指向本机 loopback（staging 另收内网 IPv4）');
  }
  return u.origin;
}

/** 只有这几类错误显示后端给的说明（库里写好的规则文字，只含公共层对象），并截到 200 字；其余只显示错误码对应的中文 */
const DETAIL_CODES = new Set(['RULE_VIOLATION', 'DIGEST_CHANGED', 'INVALID_PARAMETER', 'NOT_FOUND', 'PUBLISH_FAILED_UNSIGNED']);
const CALL_TIMEOUT_MS = 10_000;

export function problemFrom(status: number, data: unknown): Problem {
  const p = (data ?? {}) as Partial<Problem>;
  const code = typeof p.code === 'string' && /^[A-Z_]{1,40}$/.test(p.code) ? p.code : 'INTERNAL';
  const detail = DETAIL_CODES.has(code) && typeof p.detail === 'string' ? p.detail.slice(0, 200) : undefined;
  return { status, code, detail };
}

async function call<T>(session: string | null, method: string, path: string, body?: unknown): Promise<Result<T>> {
  const headers: Record<string, string> = {};
  if (session) headers['x-review-session'] = session;
  if (body !== undefined) headers['content-type'] = 'application/json';
  let r: Response;
  try {
    r = await fetch(`${reviewBaseUrl()}${path}`, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
      cache: 'no-store',
      signal: AbortSignal.timeout(CALL_TIMEOUT_MS),
    });
  } catch {
    // 后端连不上或超时：不把地址、堆栈带到页面
    return { ok: false, problem: { status: 503, code: 'INTERNAL' } };
  }
  const data = (await r.json().catch(() => ({}))) as unknown;
  if (!r.ok) return { ok: false, problem: problemFrom(r.status, data) };
  return { ok: true, value: data as T };
}

export const loginRequest = (login_name: string, password: string, code: string) =>
  send(null, 'POST /review/v0/auth/login', '/review/v0/auth/login', { login_name, password, code });
export const logoutRequest = (session: string) => fetchRoute(session, 'POST /review/v0/auth/logout', '/review/v0/auth/logout');
export const whoAmI = (session: string) => fetchRoute(session, 'GET /review/v0/auth/me', '/review/v0/auth/me');
export const listVersions = (staff: string, subject: SubjectCode) =>
  fetchRoute(staff, 'GET /review/v0/map-versions', `/review/v0/map-versions?subject=${subject}`);
export const getDiff = (staff: string, id: string) => fetchRoute(staff, 'GET /review/v0/map-versions/{version_id}/diff', `/review/v0/map-versions/${id}/diff`);
export const getRecords = (staff: string, subject: SubjectCode) =>
  fetchRoute(staff, 'GET /review/v0/records', `/review/v0/records?subject=${subject}`);
export const editNode = (staff: string, id: string, node: string, body: NodeEditReq) =>
  send(staff, 'PATCH /review/v0/map-versions/{version_id}/nodes/{node_id}', `/review/v0/map-versions/${id}/nodes/${node}`, body);
export const signVersion = (staff: string, id: string, body: ReviewRequests['POST /review/v0/map-versions/{version_id}/sign']) =>
  send(staff, 'POST /review/v0/map-versions/{version_id}/sign', `/review/v0/map-versions/${id}/sign`, body);
export const signChange = (staff: string, change: string, body: ReviewRequests['POST /review/v0/map-changes/{change_id}/sign']) =>
  send(staff, 'POST /review/v0/map-changes/{change_id}/sign', `/review/v0/map-changes/${change}/sign`, body);
export const publishVersion = (staff: string, id: string, body: ReviewRequests['POST /review/v0/map-versions/{version_id}/publish']) =>
  send(staff, 'POST /review/v0/map-versions/{version_id}/publish', `/review/v0/map-versions/${id}/publish`, body);
export const retireVersion = (staff: string, id: string, body: ReviewRequests['POST /review/v0/map-versions/{version_id}/retire']) =>
  send(staff, 'POST /review/v0/map-versions/{version_id}/retire', `/review/v0/map-versions/${id}/retire`, body);

/** 表单里一行一条（包含、不包含、典型题例）：去掉首尾空白与空行 */
export const lines = (s: string): string[] => s.split(/\r?\n/).map((x) => x.trim()).filter((x) => x.length > 0);

export const LEVEL_LABEL: Record<string, string> = { low: '低', medium: '中', high: '高' };
export const STATUS_LABEL: Record<string, string> = { draft: '草稿', published: '已发布', retired: '已撤下' };
export const OP_LABEL: Record<string, string> = { added: '新增', removed: '删除', changed: '修改', deprecated: '停用', reactivated: '恢复' };
export const OBJECT_LABEL: Record<string, string> = {
  node: '节点', edge: '边', textbook: '教材投影', region: '地区投影', skill_link: '能力视图挂接', id_migration: '旧编码迁移',
  item_family: '题目族', item_tag: '题目标签', scoring_point: '采分点', current_affair: '时政',
  version: '版本', skill_node: '能力视图', item: '题目登记',
};
/** 风险理由（后端 risk.py 的理由编码） */
export const REASON_LABEL: Record<string, string> = {
  curriculum_standard: '课标年份', node_added: '新增节点', node_removed: '删除节点', node_deprecated: '停用节点', node_reactivated: '恢复节点',
  boundary_supplement: '边界补充', curriculum_requirement: '认知层次（课标要求）', definition: '定义类字段',
  prerequisite_structure: '前置 / 从属结构', prerequisite_statement: '前置关系表述', weak_edge: '非结构性边',
  textbook_quote_page: '教材原句与页码', scoring_basis: '评分依据', id_retire_retest: '旧编码撤销（对应掌握度待复测）',
  id_migration: '旧编码迁移', item_family_ref: '题目族引用', current_affair: '时政', unclassified: '无法归类（按高风险）',
  // 冲刺 4：编译页面与原子
  full_rebuild: '全量重编', definition_curriculum: '定义与课标要求', render_segment: '渲染段', narrative_rewrite: '叙述段改写',
  exam_stats_update: '考情数值更新', atom_hash_only: '只更新所引原子', model_draft: '模型草稿（不能按低风险）', item_tag: '题目标签',
  // 冲刺 4：内容版本与考情
  content_six_segment: '六段标识', content_map_version: '所指地图版本', skill_view_registry: '能力视图登记', item_registry: '题目登记',
  admission_change: '准入变化', item_source: '题目来源与授权', policy_subjects: '科目增减', policy_full_score: '满分与总分',
  policy_scoring: '是否计分', policy_combined: '合场', policy_exam_form: '考试形式', policy_schedule: '考查年级与月份',
  policy_batches: '批次', policy_transition: '过渡规则', policy_source: '来源文件', policy_version: '方案版本号',
};
/** 签字人显示后 12 位：合成教研的前缀相同，前 8 位分不出（PR 4 后显示姓名） */
export const signerTail = (id: string): string => `…${id.slice(-12)}`;
export const ACTION_LABEL: Record<string, string> = { review: '审核', publish: '发布', retire: '撤下', apply: '执行' };
export const DECISION_LABEL: Record<string, string> = { approved: '批准', rejected: '驳回' };
export const CHANGE_STATUS_LABEL: Record<string, string> = { proposed: '待审', approved: '已批准', rejected: '已驳回', applied: '已应用' };
/** 结果提示：成功或错误码对应的中文；规则类错误另显示后端给的说明（库里写好的，只含公共层对象） */
export const RESULT_LABEL: Record<string, string> = {
  saved: '已保存', signed: '已签字', published: '已发布', retired: '已撤下', change_signed: '变更单已签字',
  DIGEST_CHANGED: '内容已被改过，请刷新后重新核对', RULE_VIOLATION: '不符合发布规则', FORBIDDEN: '没有权限（需要在职的教研账号）',
  NOT_FOUND: '对象不存在', INVALID_PARAMETER: '填写的内容不合格式', INVALID_REFERENCE: '引用的对象不存在', UNAUTHENTICATED: '请先登录', CONFLICT: '重复操作',
  INTERNAL: '出错了，请稍后再试', LOGIN_FAILED: '登录失败：登录名、口令或 6 位码不对，或账号暂时锁定（连错 5 次锁 15 分钟）',
  // 冲刺 4
  applied: '已执行准入变化（掌握重算由学习服务按执行记录做）', recorded: '已记录',
  PUBLISH_FAILED_UNSIGNED: '第二名批准时发布失败，整笔回滚，你的签字没有保存', PAGES_EXPORT_UNAVAILABLE: '合并记录导出读不了',
  SELF_SIGN: '提交方不能给自己的合并请求签字或记抽检', AUTHOR_UNKNOWN: '合并记录里没有作者或作者不在 staff 对应表里，判不出是不是提交方，不能签',
  SPOT_CHECK_UNCONFIGURED: '审核台没有配置抽检种子，不能抽检',
  // 官方来源清单（迁移 0048）
  source_added: '已加入官方来源清单（或重新启用）', source_retired: '已下线',
  SOURCE_ADMIN_REQUIRED: '没有维护官方来源清单的权限（需要由数据库管理员授予）',
  // 等锁超时（迁移 0052：来源清单的新增与下线最多等 5 秒，例如有来源文件正在登记）
  BUSY: '正被占用，请稍后重试',
};
// ---------- KGE-06 封测周修订支持（specs/kge06-weekly-revision）：低置信打标候选工作单、修订发布预检（开关见 review-features.ts） ----------
export interface TagCandidateAlt {
  id: string;
  node_id: string;
  node_name: string | null;
  confidence: string;
  layer: string;
}
export interface TagCandidate {
  id: string;
  map_version_id: string;
  version: string;
  map_status: 'draft' | 'published' | 'retired';
  subject: string;
  item_id: string;
  node_id: string;
  node_name: string | null;
  node_level: string | null;
  role: 'main' | 'secondary';
  layer: 'rule' | 'vector' | 'model';
  /** 十进制字符串（库里 numeric(4,3)），不转浮点 */
  confidence: string;
  tagger_version: string;
  model_version: string | null;
  reason_code: string;
  grade: 'A' | 'B';
  item_source_kind: string;
  status: string;
  created_at: string;
  /** 改标可选项：同一地图版本、同一题、同一角色、不同节点的其他待处理候选 */
  alternatives: TagCandidateAlt[];
}
export interface TagWorklist {
  items: TagCandidate[];
  next: { after_confidence: string; after_id: string } | null;
  open_total: number;
}
/** 游标的置信度：0–1 的小数（最多 3 位），与后端 numeric(4,3) 一致 */
export const isConfidence = (s: string): boolean => /^(0(\.\d{1,3})?|1(\.0{1,3})?)$/.test(s);
export const TAG_DECISIONS = ['accept', 'reject', 'relabel'] as const;
export type TagDecision = (typeof TAG_DECISIONS)[number];
export const isTagDecision = (s: string): s is TagDecision => (TAG_DECISIONS as readonly string[]).includes(s);

export function worklistQuery(f: { subject: SubjectCode; map_version_id?: string; after_confidence?: string; after_id?: string }): string {
  const q = new URLSearchParams({ subject: f.subject, limit: '50' });
  if (f.map_version_id) q.set('map_version_id', f.map_version_id);
  if (f.after_confidence && f.after_id) {
    q.set('after_confidence', f.after_confidence);
    q.set('after_id', f.after_id);
  }
  return q.toString();
}
export const listTagCandidates = (staff: string, f: Parameters<typeof worklistQuery>[0]) =>
  fetchRoute(staff, 'GET /review/v0/tag-candidates', `/review/v0/tag-candidates?${worklistQuery(f)}`);
export const decideTag = (staff: string, id: string, body: ReviewRequests['POST /review/v0/tag-candidates/{candidate_id}/decide']) =>
  send(staff, 'POST /review/v0/tag-candidates/{candidate_id}/decide',
    `/review/v0/tag-candidates/${id}/decide`, body);

export const ROLE_LABEL: Record<string, string> = { main: '主考点', secondary: '次要节点' };
export const LAYER_LABEL: Record<string, string> = { rule: '规则层', vector: '向量层', model: '模型层' };
export const TAG_RESULT_LABEL: Record<string, string> = {
  ...RESULT_LABEL,
  written: '已处理：已写回草稿版本的题目标签', recorded: '已处理：已记录结论（已发布版本上的打标变更走审核记录）',
  RULE_VIOLATION: '不能这样处理', FORBIDDEN: '没有权限（需要在职的教研账号）', NOT_FOUND: '打标候选不存在',
  INVALID_PARAMETER: '处理方式不对（改标要选一个可选节点）',
};

export interface PreflightIssue { code: string; detail: string }
export interface PreflightView {
  version: MapVersion;
  base_version_id: string | null;
  base_version: string | null;
  digest: string;
  structural: boolean;
  required_signers: number;
  bump: 'patch' | 'minor' | 'initial';
  latest_version: string | null;
  suggested_version: string | null;
  draft_version: string;
  version_ok: boolean;
  problems: PreflightIssue[];
  advice: PreflightIssue[];
  note: string;
  structural_reasons: { source: 'diff' | 'change_request'; object: string; key: string; op: string; reason: string }[];
  counts: Record<string, number>;
  risk_level: string | null;
  impact: { item_tags: number; scoring_points: number };
  validation_errors: number;
  signoff: { approvals: number; required: number; latest_decision: 'approved' | 'rejected' | null; latest_matches: boolean; enough: boolean };
}
export const getPreflight = (staff: string, id: string) => fetchRoute(staff, 'GET /review/v0/map-versions/{version_id}/preflight', `/review/v0/map-versions/${id}/preflight`);
export const BUMP_LABEL: Record<string, string> = {
  patch: '修订号（按周修订）', minor: '次版本号（含结构性变更）', initial: '首次发布（没有已发布或已撤下的版本，不比较版本号）',
};

export const RETIRE_BASIS = [
  { code: 'wrong_version', label: '版本有误' },
  { code: 'content_error', label: '内容错误' },
  { code: 'withdrawn', label: '暂停使用' },
] as const;

// ---------- 统一复核队列（specs/review-queue-sprint2 设计要点 2） ----------
// 可见范围：只有不透明学生指针（student_ref，后端按学生 id 截断哈希）、学科、原因、结构化判分与证据 ID；没有作答原文与可识别信息。
export const QUEUE_STATUSES = ['open', 'in_progress', 'resolved'] as const;
export type QueueStatus = (typeof QUEUE_STATUSES)[number];
export const isQueueStatus = (s: string): s is QueueStatus => (QUEUE_STATUSES as readonly string[]).includes(s);
export const QUEUE_REASONS = [
  'subjective_dimension', 'low_confidence', 'verifier_boundary_case', 'content_not_admitted', 'ocr_low_confidence',
  'dimension_unavailable', 'student_appeal', 'spot_check', 'double_grading_disagreement', 'full_score_second_review', 'profile_dispute',
  'process_observation',
] as const;
export type QueueReason = (typeof QUEUE_REASONS)[number];
export const isQueueReason = (s: string): s is QueueReason => (QUEUE_REASONS as readonly string[]).includes(s);
/** 结案码与后端白名单表的格式一致（码是否在白名单、是否适用由库函数核对） */
export const isResolutionCode = (s: string): boolean => /^[a-z0-9_]{1,48}$/.test(s);
export type SourceKind = 'grade_result' | 'skill_observation' | 'error_attribution' | 'claim' | 'diagnosis_step';

export interface QueueItem {
  id: string;
  subject: string;
  student_ref: string;
  source_kind: SourceKind;
  source_id: string;
  reasons: string[];
  status: QueueStatus;
  created_at: string;
  assigned: boolean;
  assigned_to_me: boolean;
  assigned_at: string | null;
  resolution_code: string | null;
  resolved_at: string | null;
}

export interface GradeDimension {
  dimension_id: string;
  score: number | null;
  max_score: number;
  judged_by: 'verifier' | 'rubric_rule' | 'rubric_model';
  subjective: boolean;
  rule_ref: string | null;
  confidence: number;
  evidence_ids: string[];
}

export interface GradeSource {
  verdict: string;
  score_total: number | null;
  max_total: number;
  confidence: number;
  calibrated: boolean;
  rubric_version: string;
  source_version: string;
  model_version: string;
  review_reasons: string[];
  evidence_ids: string[];
  superseded: boolean;
  created_at: string;
  dimensions: GradeDimension[];
}

export interface ClaimSource {
  template_key: string;
  template_version: string;
  target_type: string;
  status: string;
  confidence: number;
  source_type: string;
  model_version: string | null;
  evidence_ids: string[];
  created_at: string;
}

/** 诊断步骤（0027 过程观察）：步骤引用、判分引用与过程原文证据的引用（work_evidence_id）。
 *  审核台后端的库角色目前读不到证据表，原文显示“待授权”（specs/sie-02a-math-diagnosis/lessons.md 待人确认） */
export interface DiagnosisStepSource {
  kind: 'process' | 'explain';
  node_id: string;
  item_id: string | null;
  grade_result_id: string | null;
  work_evidence_id: string | null;
  /** 过程原文（审核台后端取到时才有；授权前恒为空） */
  work_text?: string | null;
  evidence_ids: string[] | null;
  verdict: string | null;
  answered_at: string | null;
}

export interface QueueTask extends QueueItem {
  resolved_by_me: boolean;
  source: GradeSource | ClaimSource | DiagnosisStepSource | null;
}

export interface ResolutionCode {
  code: string;
  label: string;
  applies_to: string[];
}

export interface QueueFilter {
  subject: SubjectCode;
  status: QueueStatus;
  reason?: QueueReason;
  /** 翻页游标：上一页最后一条的创建时间与 id，两个一起用（后端安全审查 S2：同一时刻的多条不漏不重） */
  before?: string;
  before_id?: string;
  limit?: number;
}

export interface QueueCursor {
  before: string;
  before_id: string;
}

/** 列表查询串（也用于页面链接）：只拼经过校验的值，before 编码（ISO 时间里的“+”不能原样放进地址） */
export function queueQuery(f: QueueFilter): string {
  const q = new URLSearchParams({ subject: f.subject, status: f.status });
  if (f.reason) q.set('reason', f.reason);
  // 游标两个参数只一起出现
  if (f.before && f.before_id) {
    q.set('before', f.before);
    q.set('before_id', f.before_id);
  }
  if (f.limit !== undefined) q.set('limit', String(f.limit));
  return q.toString();
}

/** 翻页游标的时间：只收带时区、能解析的 ISO 时间（后端给的 created_at 原样回传；后端要求带时区） */
export const isCursor = (s: string): boolean =>
  s.length <= 40 && /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,6})?(Z|[+-]\d{2}:\d{2})$/.test(s) && !Number.isNaN(Date.parse(s));

export const queue = (staff: string, f: QueueFilter) =>
  fetchRoute(staff, 'GET /review/v0/queue', `/review/v0/queue?${queueQuery({ limit: 50, ...f })}`);
// ---------- 请求体（W13 T35，specs/review-console-types-t35）----------
// 审核台后端每个带请求体的接口在这里登记一次：键是“方法 路径模板”（与后端 OpenAPI 相同），值是请求体。
// tests/review-requests.test.ts 用 TypeScript 编译器 API 读这个接口，与后端导出的 review-api.requests.json 逐项比较
// （接口集合、字段集合、必填、枚举）；带请求体的调用一律经 send()，路径与模板对不上同样失败。
export const DECISIONS = ['approved', 'rejected'] as const;
export type Decision = (typeof DECISIONS)[number];
export const isDecision = (s: unknown): s is Decision => typeof s === 'string' && (DECISIONS as readonly string[]).includes(s);
export const COGNITIVE_LEVELS = ['know', 'understand', 'apply', 'synthesize'] as const;
export const NODE_SCOPES = ['required', 'elective', 'edition_ext', 'exam_ext', 'enrichment'] as const;
interface SignReq { decision: Decision; basis_code?: string; expected_digest: string }
interface DigestReq { expected_digest: string }
interface DecisionDigestReq { decision: Decision; expected_digest: string }
interface BasisReq { basis_code: string }
interface PageSignReq { page_id: string; page_commit: string; decision: Decision }
export interface NodeEditReq {
  expected_digest: string;
  name?: string;
  includes?: string[];
  excludes?: string[];
  examples?: string[];
  cognitive_levels?: (typeof COGNITIVE_LEVELS)[number][];
  scope?: (typeof NODE_SCOPES)[number];
}
export interface ReviewRequests {
  'POST /review/v0/auth/login': { login_name: string; password: string; code: string };
  'PATCH /review/v0/map-versions/{version_id}/nodes/{node_id}': NodeEditReq;
  'POST /review/v0/map-versions/{version_id}/sign': SignReq;
  'POST /review/v0/map-changes/{change_id}/sign': DecisionDigestReq;
  'POST /review/v0/map-versions/{version_id}/publish': DigestReq;
  'POST /review/v0/map-versions/{version_id}/retire': BasisReq;
  'POST /review/v0/content-versions/{cv}/sign': SignReq;
  'POST /review/v0/content-versions/{cv}/publish': DigestReq;
  'POST /review/v0/content-versions/{cv}/retire': BasisReq;
  'POST /review/v0/admission/sign': AdmissionSign;
  'POST /review/v0/admission/apply': { review_ids: string[] };
  'POST /review/v0/policy/schemes/{scheme}/sign': DecisionDigestReq;
  'POST /review/v0/policy/schemes/{scheme}/publish': DigestReq;
  'POST /review/v0/policy/records/{object_id}/sign': { object_type: (typeof POLICY_RECORD_TYPES)[number]; decision: Decision; expected_digest: string };
  'POST /review/v0/policy/sources': { host: string; note: string };
  'POST /review/v0/policy/sources/retire': { host: string };
  'POST /review/v0/pages/sign': PageSignReq;
  'POST /review/v0/spot-checks/sign': PageSignReq;
  'POST /review/v0/queue/{task_id}/resolve': { resolution_code: string };
  'POST /review/v0/tag-candidates/{candidate_id}/decide': { decision: TagDecision; target_id?: string };
  'POST /review/v0/feedback/{feedback_id}/status': { status: FeedbackStatus };
}
type Route = keyof ReviewRequests;

// 各接口 2xx 响应的类型（specs/review-console-responses）：键与 ReviewRequests 相同，是“方法 路径模板”；值与后端响应模型
// （services/learning/review_console/responses.py）逐层一致，由 tests/review-responses.test.ts 与导出的 review-api.responses.json 比较。
// 调用一律经 send()（带请求体）或 fetchRoute()（不带），返回类型由路由键决定，调用处不再写 call<T>。
interface StatusOut { status: string }
interface ReviewIdOut { review_id: string }
interface PublishedOut { published_at: string }
export interface ReviewResponses {
  'POST /review/v0/auth/login': { token: string };
  'POST /review/v0/auth/logout': { status: 'logged_out' };
  'GET /review/v0/auth/me': { account_id: string; login_name: string | null };
  'GET /review/v0/map-versions': { items: MapVersion[] };
  'GET /review/v0/map-versions/{version_id}/diff': DiffView;
  'PATCH /review/v0/map-versions/{version_id}/nodes/{node_id}': { digest: string };
  'POST /review/v0/map-versions/{version_id}/sign': ReviewIdOut;
  'POST /review/v0/map-changes/{change_id}/sign': StatusOut;
  'POST /review/v0/map-versions/{version_id}/publish': PublishedOut;
  'POST /review/v0/map-versions/{version_id}/retire': StatusOut;
  'GET /review/v0/map-versions/{version_id}/preflight': PreflightView;
  'GET /review/v0/records': { items: RecordRow[] };
  'GET /review/v0/tag-candidates': TagWorklist;
  'POST /review/v0/tag-candidates/{candidate_id}/decide': { status: 'written' | 'recorded'; decision: TagDecision; decided_by: string };
  'GET /review/v0/content-versions': { items: ContentVersionRow[] };
  'GET /review/v0/content-versions/{cv}/diff': ContentView;
  'POST /review/v0/content-versions/{cv}/sign': ReviewIdOut;
  'POST /review/v0/content-versions/{cv}/publish': PublishedOut;
  'POST /review/v0/content-versions/{cv}/retire': StatusOut;
  'POST /review/v0/admission/sign': ReviewIdOut;
  'POST /review/v0/admission/apply': { applied: number; recompute_since: string; recompute: string };
  'GET /review/v0/policy': { schemes: SchemeListRow[]; drafts: PolicyDraft[] };
  'GET /review/v0/policy/schemes/{scheme}': PolicyView;
  'POST /review/v0/policy/schemes/{scheme}/sign': { approvals: number };
  'POST /review/v0/policy/schemes/{scheme}/publish': PublishedOut;
  'POST /review/v0/policy/records/{object_id}/sign': StatusOut;
  'GET /review/v0/policy/sources': { items: SourceDomain[] };
  'POST /review/v0/policy/sources': { host: string; status: string };
  'POST /review/v0/policy/sources/retire': { host: string; status: string };
  'GET /review/v0/pages': PagesView;
  'POST /review/v0/pages/sign': { review_id: string; risk_level: string };
  'GET /review/v0/spot-checks': SpotCheckView;
  'POST /review/v0/spot-checks/sign': ReviewIdOut;
  'GET /review/v0/queue': { items: QueueItem[]; next: QueueCursor | null };
  'GET /review/v0/queue/resolution-codes': { items: ResolutionCode[] };
  'GET /review/v0/queue/{task_id}': QueueTask;
  'POST /review/v0/queue/{task_id}/take': StatusOut;
  'POST /review/v0/queue/{task_id}/release': StatusOut;
  'POST /review/v0/queue/{task_id}/resolve': StatusOut;
  'GET /review/v0/feedback': { items: FeedbackRow[]; next: QueueCursor | null };
  'POST /review/v0/feedback/{feedback_id}/status': { status: FeedbackStatus };
  'GET /review/v0/internal-test/exits': { items: InternalTestExitRow[] };
}
type ResponseRoute = keyof ReviewResponses;
/** 不带请求体的路由（GET 与 logout、take、release 这几个 POST） */
type BodylessRoute = Exclude<ResponseRoute, Route>;

/** 带请求体的调用：route 是 ReviewRequests 的键，path 是填好参数的实际路径（静态测试核对两者对得上） */
function send<K extends Route>(session: string | null, route: K, path: string, body: ReviewRequests[K]): Promise<Result<ReviewResponses[K]>> {
  return call<ReviewResponses[K]>(session, route.slice(0, route.indexOf(' ')), path, body);
}

/** 不带请求体的调用：route 是 ReviewResponses 的键，path 是填好参数的实际路径（静态测试核对两者对得上） */
function fetchRoute<K extends BodylessRoute>(session: string | null, route: K, path: string): Promise<Result<ReviewResponses[K]>> {
  return call<ReviewResponses[K]>(session, route.slice(0, route.indexOf(' ')), path);
}

export const queueTask = (staff: string, id: string) => fetchRoute(staff, 'GET /review/v0/queue/{task_id}', `/review/v0/queue/${id}`);
export const resolutionCodes = (staff: string) => fetchRoute(staff, 'GET /review/v0/queue/resolution-codes', '/review/v0/queue/resolution-codes');
export const takeTask = (staff: string, id: string) => fetchRoute(staff, 'POST /review/v0/queue/{task_id}/take', `/review/v0/queue/${id}/take`);
export const releaseTask = (staff: string, id: string) => fetchRoute(staff, 'POST /review/v0/queue/{task_id}/release', `/review/v0/queue/${id}/release`);
export const resolveTask = (staff: string, id: string, resolution_code: string) =>
  send(staff, 'POST /review/v0/queue/{task_id}/resolve', `/review/v0/queue/${id}/resolve`, { resolution_code });

/** 复核原因（platform.review_reason） */
export const QUEUE_REASON_LABEL: Record<QueueReason, string> = {
  subjective_dimension: '主观维度', low_confidence: '置信度低', verifier_boundary_case: '验证器边界情况',
  content_not_admitted: '题目未准入', ocr_low_confidence: '识别置信度低', dimension_unavailable: '维度不可判',
  student_appeal: '学生申诉', spot_check: '抽检', double_grading_disagreement: '双评不一致',
  full_score_second_review: '满分复核', profile_dispute: '画像异议', process_observation: '过程观察',
};
export const QUEUE_STATUS_LABEL: Record<QueueStatus, string> = { open: '待处理', in_progress: '处理中', resolved: '已结案' };
export const SOURCE_KIND_LABEL: Record<SourceKind, string> = {
  grade_result: '判分', skill_observation: '能力观察', error_attribution: '错因归因', claim: '判断（画像异议）',
  diagnosis_step: '诊断步骤（过程观察）',
};
export const JUDGED_BY_LABEL: Record<string, string> = { verifier: '验证器', rubric_rule: 'Rubric 规则', rubric_model: 'Rubric 模型' };
export const VERDICT_LABEL: Record<string, string> = {
  correct: '正确', incorrect: '错误', partial: '部分正确', needs_review: '待复核', not_applicable: '不适用',
};
export const reasonLabel = (r: string): string => (Object.hasOwn(QUEUE_REASON_LABEL, r) ? QUEUE_REASON_LABEL[r as QueueReason] : r);

/** 领取情况：我已领取 / 他人已领取 / 未领取 */
export function assignmentLabel(t: Pick<QueueItem, 'assigned' | 'assigned_to_me'>): string {
  return t.assigned_to_me ? '我已领取' : t.assigned ? '他人已领取' : '未领取';
}

export const formatTime = (iso: string | null): string =>
  iso ? new Date(iso).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai' }) : '—';

/** 复核队列的结果提示：规则类错误不是“发布规则”，另给文字；说明（如“复核任务已被其他人领取”）照常附在后面 */
export const QUEUE_RESULT_LABEL: Record<string, string> = {
  ...RESULT_LABEL,
  taken: '已领取', released: '已放回队列', resolved: '已结案',
  RULE_VIOLATION: '不能这样操作', FORBIDDEN: '没有权限（需要在职的 staff 账号）', NOT_FOUND: '复核任务不存在',
  INVALID_PARAMETER: '结案码不合格式、不在白名单或不适用于这类任务',
};
/** 复核任务 id 是 UUID */
export const isTaskId = isVersionId;

// ---------- 内部成员测试运营（LT-06，specs/launch-lt06-internal-test-ops；开关 STARBUDDY_INTERNAL_TEST，后端 REVIEW_CONSOLE_INTERNAL_TEST）----------
// 反馈只读列表与改状态、退出状态。学生只有不透明指针（student_ref），没有 student_id 与提交者账号。反馈文本只在页面上显示，不打日志。
export const FEEDBACK_STATUSES = ['new', 'triaged', 'closed'] as const;
export type FeedbackStatus = (typeof FEEDBACK_STATUSES)[number];
export const isFeedbackStatus = (s: unknown): s is FeedbackStatus => typeof s === 'string' && (FEEDBACK_STATUSES as readonly string[]).includes(s);
export const FEEDBACK_STATUS_LABEL: Record<FeedbackStatus, string> = { new: '新到', triaged: '已分拣', closed: '已关闭' };
export const FEEDBACK_SOURCE_LABEL: Record<string, string> = {
  student_self_report: '学生自述', guardian_observation: '家长观察', staff: '员工',
};
export const FEEDBACK_CATEGORY_NAME: Record<string, string> = {
  malfunction: '功能出错', wrong_content: '内容有误', display: '显示问题', slow: '卡顿或很慢', login: '登录问题', suggestion: '建议', other: '其他',
};

export interface FeedbackRow {
  id: string;
  source: string;
  category: string;
  body: string;
  route_template: string | null;
  status: FeedbackStatus;
  created_at: string;
  status_changed_at: string | null;
  student_ref: string | null;
}

export interface InternalTestExitRow {
  student_ref: string;
  exited_at: string;
  consents_revoked: string[];
  consent_active: boolean;
  sessions_revoked: number;
  erasure_status: string | null;
  erased: boolean;
}

export interface FeedbackFilter { status?: FeedbackStatus; before?: string; before_id?: string }

export function feedbackQuery(f: FeedbackFilter): string {
  const q = new URLSearchParams({ limit: '50' });
  if (f.status) q.set('status', f.status);
  if (f.before && f.before_id) {
    q.set('before', f.before);
    q.set('before_id', f.before_id);
  }
  return q.toString();
}

export const listFeedback = (staff: string, f: FeedbackFilter) =>
  fetchRoute(staff, 'GET /review/v0/feedback', `/review/v0/feedback?${feedbackQuery(f)}`);
export const setFeedbackStatus = (staff: string, id: string, status: FeedbackStatus) =>
  send(staff, 'POST /review/v0/feedback/{feedback_id}/status', `/review/v0/feedback/${id}/status`, { status });
export const listInternalTestExits = (staff: string) =>
  fetchRoute(staff, 'GET /review/v0/internal-test/exits', '/review/v0/internal-test/exits?limit=200');

export const FEEDBACK_RESULT_LABEL: Record<string, string> = {
  ...RESULT_LABEL,
  status_changed: '已改状态', FORBIDDEN: '没有权限（需要在职的 staff 账号）', NOT_FOUND: '反馈不存在', INVALID_PARAMETER: '状态不对',
};
