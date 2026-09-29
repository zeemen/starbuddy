// 审核台（specs/release-gate-sprint4 冲刺 1 · PR 3）的后端客户端：只在服务端调用（服务端组件与 server action）。
// 浏览器只和本前端通信，不直连审核台后端（services/learning/review_console）或数据库。
// 身份：PR 4 之前是开发身份桩（x-dev-staff-id，后端只在 loopback、开关打开时认）；签字、发布、撤下由库里的函数再核对 staff。
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
  payload: unknown;
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
  digest: string;
  nodes: NodeRow[];
  summary: { level: string; review_level: string; structural: boolean; required_signers: number; counts: Record<string, number> };
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

async function call<T>(staff: string, method: string, path: string, body?: unknown): Promise<Result<T>> {
  const headers: Record<string, string> = { 'x-dev-staff-id': staff };
  if (body !== undefined) headers['content-type'] = 'application/json';
  const r = await fetch(`${reviewBaseUrl()}${path}`, {
    method,
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
    cache: 'no-store',
  });
  const data = (await r.json().catch(() => ({}))) as unknown;
  if (!r.ok) {
    const p = data as Partial<Problem>;
    return {
      ok: false,
      problem: { status: r.status, code: typeof p.code === 'string' ? p.code : 'INTERNAL', detail: typeof p.detail === 'string' ? p.detail : undefined },
    };
  }
  return { ok: true, value: data as T };
}

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
export const ACTION_LABEL: Record<string, string> = { review: '审核', publish: '发布', retire: '撤下' };
export const DECISION_LABEL: Record<string, string> = { approved: '批准', rejected: '驳回' };
export const CHANGE_STATUS_LABEL: Record<string, string> = { proposed: '待审', approved: '已批准', rejected: '已驳回', applied: '已应用' };
/** 结果提示：成功或错误码对应的中文；规则类错误另显示后端给的说明（库里写好的，只含公共层对象） */
export const RESULT_LABEL: Record<string, string> = {
  saved: '已保存', signed: '已签字', published: '已发布', retired: '已撤下', change_signed: '变更单已签字',
  DIGEST_CHANGED: '内容已被改过，请刷新后重新核对', RULE_VIOLATION: '不符合发布规则', FORBIDDEN: '没有权限（需要在职的教研账号）',
  NOT_FOUND: '对象不存在', INVALID_PARAMETER: '填写的内容不合格式', INVALID_REFERENCE: '引用的对象不存在', UNAUTHENTICATED: '请先登录', CONFLICT: '重复操作',
  INTERNAL: '出错了，请稍后再试',
};
export const RETIRE_BASIS = [
  { code: 'wrong_version', label: '版本有误' },
  { code: 'content_error', label: '内容错误' },
  { code: 'withdrawn', label: '暂停使用' },
] as const;
