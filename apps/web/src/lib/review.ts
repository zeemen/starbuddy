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

export interface RecordRow extends Signoff {
  line: string;
  rubric_versions: string;
  validation_errors: number;
  validation_warnings: number;
}

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
