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

export interface ChangeRequest {
  id: string;
  change_type: string;
  status: 'proposed' | 'approved' | 'rejected' | 'applied';
  /** 签字覆盖的全部内容（与库里 kge.map_change_digest 的输入一致），页面原样显示 */
  signed_content: Record<string, unknown>;
  digest: string;
  risk: Risk;
}

export interface NodeRow {
  id: string;
  level: string;
  kind: string | null;
  name: string;
  includes: string[];
  excludes: string[];
  examples: string[];
  cognitive_levels: string[];
  scope: string;
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

export interface RecordRow extends Omit<Signoff, 'map_version_id' | 'version'> {
  /** map：地图版本的签字、发布、撤下；page：编译页面的签字（冲刺 3） */
  line: 'map' | 'page';
  map_version_id: string | null;
  version: string | null;
  rubric_versions: string;
  validation_errors: number | null;
  validation_warnings: number | null;
  page_id: string | null;
  page_commit: string | null;
}

// ---------- 编译页面（冲刺 3，spec 第 6 节）：待审合并请求与对账状态；审核在托管平台网页上做，这里只读、只给链接 ----------
export interface PageEntry {
  page_id: string;
  mr_id: string;
  state: 'merged' | 'open';
  head_commit: string;
  merge_commit?: string | null;
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
  repository?: string;
  branch?: string;
  exported_at?: string;
  ok?: boolean;
  pending?: PageEntry[];
  merged?: PageEntry[];
  /** 同一页面更早的合并（只按最近一次合并判定） */
  superseded?: { page_id: string; mr_id: string; head_commit: string; merge_commit: string }[];
  orphans?: PageOrphan[];
  held_pages?: string[];
}

export const getPages = (staff: string) => call<PagesView>(staff, 'GET', '/review/v0/pages');

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
};
export const pageProblemLabel = (p: string): string => (Object.hasOwn(PAGE_PROBLEM_LABEL, p) ? PAGE_PROBLEM_LABEL[p]! : p);

export interface Problem {
  status: number;
  code: string;
  detail?: string;
}

export type Result<T> = { ok: true; value: T } | { ok: false; problem: Problem };

/** 审核台后端只能是 loopback 上的 http（开发原型；PR 4 定部署拓扑） */
export function reviewBaseUrl(env: Record<string, string | undefined> = process.env): string {
  const raw = env['STARBUDDY_REVIEW_URL'] ?? 'http://127.0.0.1:8790';
  const u = new URL(raw);
  if (u.protocol !== 'http:' || !['127.0.0.1', 'localhost', '[::1]'].includes(u.hostname)) {
    throw new Error('STARBUDDY_REVIEW_URL 只能指向本机 loopback（开发原型）');
  }
  return u.origin;
}

/** 只有这几类错误显示后端给的说明（库里写好的规则文字，只含公共层对象），并截到 200 字；其余只显示错误码对应的中文 */
const DETAIL_CODES = new Set(['RULE_VIOLATION', 'DIGEST_CHANGED', 'INVALID_PARAMETER', 'NOT_FOUND']);
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
  call<{ token: string }>(null, 'POST', '/review/v0/auth/login', { login_name, password, code });
export const logoutRequest = (session: string) => call<{ status: string }>(session, 'POST', '/review/v0/auth/logout');
export const whoAmI = (session: string) => call<{ account_id: string; login_name: string | null }>(session, 'GET', '/review/v0/auth/me');
export const listVersions = (staff: string, subject: SubjectCode) =>
  call<{ items: MapVersion[] }>(staff, 'GET', `/review/v0/map-versions?subject=${subject}`);
export const getDiff = (staff: string, id: string) => call<DiffView>(staff, 'GET', `/review/v0/map-versions/${id}/diff`);
export const getRecords = (staff: string, subject: SubjectCode) =>
  call<{ items: RecordRow[] }>(staff, 'GET', `/review/v0/records?subject=${subject}`);
export const editNode = (staff: string, id: string, node: string, body: Record<string, unknown>) =>
  call<{ digest: string }>(staff, 'PATCH', `/review/v0/map-versions/${id}/nodes/${node}`, body);
export const signVersion = (staff: string, id: string, body: { decision: string; basis_code?: string; expected_digest: string }) =>
  call<{ review_id: string }>(staff, 'POST', `/review/v0/map-versions/${id}/sign`, body);
export const signChange = (staff: string, change: string, body: { decision: string; expected_digest: string }) =>
  call<{ status: string }>(staff, 'POST', `/review/v0/map-changes/${change}/sign`, body);
export const publishVersion = (staff: string, id: string, body: { expected_digest: string }) =>
  call<{ published_at: string }>(staff, 'POST', `/review/v0/map-versions/${id}/publish`, body);
export const retireVersion = (staff: string, id: string, body: { basis_code: string }) =>
  call<{ status: string }>(staff, 'POST', `/review/v0/map-versions/${id}/retire`, body);

/** 表单里一行一条（包含、不包含、典型题例）：去掉首尾空白与空行 */
export const lines = (s: string): string[] => s.split(/\r?\n/).map((x) => x.trim()).filter((x) => x.length > 0);

export const LEVEL_LABEL: Record<string, string> = { low: '低', medium: '中', high: '高' };
export const STATUS_LABEL: Record<string, string> = { draft: '草稿', published: '已发布', retired: '已撤下' };
export const OP_LABEL: Record<string, string> = { added: '新增', removed: '删除', changed: '修改', deprecated: '停用', reactivated: '恢复' };
export const OBJECT_LABEL: Record<string, string> = {
  node: '节点', edge: '边', textbook: '教材投影', region: '地区投影', skill_link: '能力视图挂接', id_migration: '旧编码迁移',
  item_family: '题目族', item_tag: '题目标签', scoring_point: '采分点', current_affair: '时政',
};
/** 风险理由（后端 risk.py 的理由编码） */
export const REASON_LABEL: Record<string, string> = {
  curriculum_standard: '课标年份', node_added: '新增节点', node_removed: '删除节点', node_deprecated: '停用节点', node_reactivated: '恢复节点',
  boundary_supplement: '边界补充', curriculum_requirement: '认知层次（课标要求）', definition: '定义类字段',
  prerequisite_structure: '前置 / 从属结构', prerequisite_statement: '前置关系表述', weak_edge: '非结构性边',
  textbook_quote_page: '教材原句与页码', scoring_basis: '评分依据', id_retire_retest: '旧编码撤销（对应掌握度待复测）',
  id_migration: '旧编码迁移', item_family_ref: '题目族引用', current_affair: '时政', unclassified: '无法归类（按高风险）',
};
/** 签字人显示后 12 位：合成教研的前缀相同，前 8 位分不出（PR 4 后显示姓名） */
export const signerTail = (id: string): string => `…${id.slice(-12)}`;
export const ACTION_LABEL: Record<string, string> = { review: '审核', publish: '发布', retire: '撤下' };
export const DECISION_LABEL: Record<string, string> = { approved: '批准', rejected: '驳回' };
export const CHANGE_STATUS_LABEL: Record<string, string> = { proposed: '待审', approved: '已批准', rejected: '已驳回', applied: '已应用' };
/** 结果提示：成功或错误码对应的中文；规则类错误另显示后端给的说明（库里写好的，只含公共层对象） */
export const RESULT_LABEL: Record<string, string> = {
  saved: '已保存', signed: '已签字', published: '已发布', retired: '已撤下', change_signed: '变更单已签字',
  DIGEST_CHANGED: '内容已被改过，请刷新后重新核对', RULE_VIOLATION: '不符合发布规则', FORBIDDEN: '没有权限（需要在职的教研账号）',
  NOT_FOUND: '对象不存在', INVALID_PARAMETER: '填写的内容不合格式', INVALID_REFERENCE: '引用的对象不存在', UNAUTHENTICATED: '请先登录', CONFLICT: '重复操作',
  INTERNAL: '出错了，请稍后再试', LOGIN_FAILED: '登录失败：登录名、口令或 6 位码不对，或账号暂时锁定（连错 5 次锁 15 分钟）',
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
  call<{ items: QueueItem[]; next: QueueCursor | null }>(staff, 'GET', `/review/v0/queue?${queueQuery({ limit: 50, ...f })}`);
export const queueTask = (staff: string, id: string) => call<QueueTask>(staff, 'GET', `/review/v0/queue/${id}`);
export const resolutionCodes = (staff: string) => call<{ items: ResolutionCode[] }>(staff, 'GET', '/review/v0/queue/resolution-codes');
export const takeTask = (staff: string, id: string) => call<{ status: string }>(staff, 'POST', `/review/v0/queue/${id}/take`);
export const releaseTask = (staff: string, id: string) => call<{ status: string }>(staff, 'POST', `/review/v0/queue/${id}/release`);
export const resolveTask = (staff: string, id: string, resolution_code: string) =>
  call<{ status: string }>(staff, 'POST', `/review/v0/queue/${id}/resolve`, { resolution_code });

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
