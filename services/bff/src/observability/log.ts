// BFF 的结构化日志与集中脱敏（LT-05 范围第 2 条，specs/launch-lt05-observability）：与 Python 服务（services/*/obs.py）同一口径。
// - 一行一个 JSON：ts、level、service（bff），再并入记录里的字段（访问日志：request_id、route、status、duration_ms 等）；
// - 字段白名单（安全审查 B）：键名先由驼峰归一为下划线小写，只留 LOG_FIELDS（信封字段与登记的元数据键；与 Python obs.py 的 LOG_FIELDS
//   逐项相同，IaC 日志索引字段只能从这份清单里选，deploy/tests/test_obs_shared.py 核对），其余键整项丢掉；
//   BFF 另有 HASHED_FIELDS（student、account）：只收带密钥 HMAC 截断的 16 位十六进制（secrets.ts logHash），别的取值整项丢掉；
// - 嵌套结构里的禁止字段整项丢掉（作答、内容、手机号、邮箱、身份证、令牌 / 口令 / 密钥 / 会话、姓名、文本、地址、标识等）；
// - 字符串里的手机号（允许空格、连字符分隔）、邮箱、身份证号、Bearer / JWT / 密钥样式串、连接串替换为 [redacted]，UUID 替换为 [uuid]
//   （request_id 字段除外：只收 UUID 原样）；超长文本截断；结构限深限长。
// 只记元数据的既有约束不变：调用方本来就只放元数据，这里是最后一道防线。
export const REDACTED = '[redacted]';
export const UUID_MARK = '[uuid]';
export const MAX_TEXT = 300;
export const ENVELOPE = ['ts', 'level', 'service', 'logger', 'route', 'request_id', 'msg', 'exc_type'] as const;
export const LOG_FIELDS: ReadonlySet<string> = new Set([...ENVELOPE,
  'event', 'method', 'status', 'code', 'duration_ms', 'replayed', 'error_class', 'reason', 'detail', 'subject', 'subjects', 'task',
  'data_class', 'outcome', 'tool', 'path', 'env', 'usage_ref', 'idempotency_ref', 'source_version', 'model_version', 'attempts',
  'input_tokens', 'output_tokens', 'elapsed_ms', 'model_mismatch', 'reported_model', 'estimated_cost_cny', 'trigger', 'items',
  'truncated', 'unavailable', 'snapshot', 'sqlstate', 'counts', 'purpose', 'verdict', 'verdict_label', 'pending_review',
  'evidence_level', 'learner_model_updated', 'response_contract_errors', 'report_blocked', 'cache', 'cache_stats', 'bytes', 'entries',
  'generation', 'requests', 'categories', 'note', 'approvals', 'latest_decision', 'latest_matches', 'required_signers', 'structural',
  'ready', 'job', 'child_exit', 'exit_code', 'started_at', 'finished_at', 'port', 'audit_event', 'account_kind', 'dropped_statements',
  'explain', 'export_job', 'export_store', 'generate', 'guardian_op', 'human_review_required', 'lexicon', 'lexicon_hits',
  'monthly_plan', 'policy_version', 'prediction', 'reconciliation', 'report', 'route_group', 'sms', 'star_nodes',
  'star_nodes_mismatch', 'star_statements', 'template_fingerprint', 'weekly']);
/** BFF 访问日志里的学生、账号：带密钥 HMAC 截断（不是标识原文）；只收 16 位小写十六进制 */
export const HASHED_FIELDS: ReadonlySet<string> = new Set(['student', 'account']);
const HASHED_VALUE = /^[0-9a-f]{16}$/;
const FORBIDDEN_KEY = /^(?:.*_)?(password|passwd|secret|token|api_?key|authorization|cookie|credential|phone|mobile|email|id_?card|id_?number|otp|dsn|session|name|nickname|texts?|messages?|content|body|raw|answers?|prompt|output|stem|essay|transcript|address|ip|url|photo|image|audio|video|file|student|guardian|account|user)(?:_(?:id|ref))?$/i;
const HEX_OK_KEY = /(digest|sha256|_ref|commit|fingerprint|hash)$/i;
const UUID_RE = /[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}/g;
const UUID_FULL = /^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$/;
const TEXT_RULES: RegExp[] = [
  /postgres(?:ql)?:\/\/\S+/gi,
  /\bbearer\s+\S+/gi,
  /\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{4,}/g,
  /\b(?:sk|ak|pk|rk)-[A-Za-z0-9_-]{16,}/g,
  /LTAI[0-9A-Za-z]{12,}/g,
  /[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}/g,
  /(?<![0-9A-Za-z])\d{17}[\dXx](?![0-9A-Za-z])/g,
  /(?<!\d)(?:\+?86[- ]?)?1[3-9]\d[ -]?\d{4}[ -]?\d{4}(?!\d)/g,
];
// 密钥样式长串：40 个以上的 base64 / 十六进制字符、同时含数字与字母（只有字母和下划线的长编码名不算，例如告警原因编码）
const LONG_SECRET = /(?<![A-Za-z0-9+/=_])(?=[A-Za-z0-9+/=_]*\d)(?=[A-Za-z0-9+/=_]*[A-Za-z])[A-Za-z0-9+/=_]{40,}(?![A-Za-z0-9+/=_])/g;

/** studentId → student_id、StudentName → student_name：驼峰先归一为下划线小写（与 Python normalize_key 同一规则） */
export function normalizeKey(key: string): string {
  return String(key).replace(/(?<=[a-z0-9])([A-Z])/g, '_$1').replace(/-/g, '_').toLowerCase();
}

export function forbiddenKey(key: string): boolean {
  return FORBIDDEN_KEY.test(normalizeKey(key));
}

export function scrubText(s: string, key?: string): string {
  let out = s;
  for (const rule of TEXT_RULES) out = out.replace(rule, REDACTED);
  out = out.replace(UUID_RE, UUID_MARK);
  if (key === undefined || !HEX_OK_KEY.test(key)) out = out.replace(LONG_SECRET, REDACTED);
  return out.length <= MAX_TEXT ? out : `${out.slice(0, MAX_TEXT)}…[truncated]`;
}

export function scrub(value: unknown, key?: string, depth = 0): unknown {
  if (typeof value === 'string') return scrubText(value, key);
  if (value === null || value === undefined || typeof value === 'number' || typeof value === 'boolean') return value;
  if (depth >= 4) return REDACTED;
  if (Array.isArray(value)) return value.slice(0, 50).map((v) => scrub(v, key, depth + 1));
  if (typeof value === 'object') {
    const out: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(value as Record<string, unknown>).slice(0, 64)) {
      const nk = normalizeKey(k);
      if (!forbiddenKey(nk)) out[nk] = scrub(v, nk, depth + 1);
    }
    return out;
  }
  return scrubText(String(value), key);
}

/** 顶层字段白名单：键名驼峰归一后只留 LOG_FIELDS 与 HASHED_FIELDS（后者只收 16 位十六进制），其余整项丢掉；取值再脱敏
 *  （request_id 只收 UUID 原样，别的值按文本处理） */
export function logFields(record: Record<string, unknown>): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(record).slice(0, 64)) {
    const nk = normalizeKey(k);
    if (HASHED_FIELDS.has(nk)) {
      if (typeof v === 'string' && HASHED_VALUE.test(v)) out[nk] = v;
      continue;
    }
    if (!LOG_FIELDS.has(nk)) continue;
    out[nk] = nk === 'request_id' && typeof v === 'string' && UUID_FULL.test(v) ? v : scrub(v, nk, 1);
  }
  return out;
}

export type Level = 'info' | 'warn' | 'error';

/** 一行 JSON（ts、level、service 在前，记录里同名字段不覆盖它们） */
export function format(level: Level, record: Record<string, unknown>, now: () => Date = () => new Date()): string {
  const out: Record<string, unknown> = { ts: now().toISOString(), level, service: 'bff' };
  for (const [k, v] of Object.entries(logFields(record))) if (!(k in out)) out[k] = v;
  return JSON.stringify(out);
}

/** 写到标准输出（info）或标准错误（warn、error）；SLS 两路都采 */
export function emit(level: Level, record: Record<string, unknown>): void {
  const line = format(level, record);
  if (level === 'info') console.log(line);
  else if (level === 'warn') console.warn(line);
  else console.error(line);
}
