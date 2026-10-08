// 统一错误体（spec 第 16 节：错误体只有错误码，不回显堆栈、SQL、内部地址）。全部预先序列化：
// 同一类拒绝在任何路由、任何原因下逐字节相同（未授权与不存在同一个 404；限流不分原因同一个 429；登录失败不分原因同一个 401）。
const problem = (status: number, code: string, title: string) =>
  JSON.stringify({ type: `/problems/${code.toLowerCase()}`, title, status, code });

export const P = {
  UNAUTHENTICATED: problem(401, 'UNAUTHENTICATED', 'sign-in required'),
  LOGIN_FAILED: problem(401, 'LOGIN_FAILED', 'sign-in failed'),
  FORBIDDEN: problem(403, 'FORBIDDEN', 'not allowed'),
  CSRF: problem(403, 'CSRF', 'request origin not accepted'),
  NOT_FOUND: problem(404, 'NOT_FOUND', 'not found'),
  METHOD_NOT_ALLOWED: problem(405, 'METHOD_NOT_ALLOWED', 'method not allowed'),
  CONTACT_SUPPORT: problem(409, 'CONTACT_SUPPORT', 'this number cannot sign in here, contact support'),
  CONSENT_REQUIRED: problem(409, 'CONSENT_REQUIRED', 'required consent is missing'),
  TOO_LARGE: problem(413, 'TOO_LARGE', 'request body too large'),
  UNSUPPORTED: problem(415, 'UNSUPPORTED_MEDIA_TYPE', 'content-type must be application/json'),
  INVALID: problem(422, 'INVALID', 'invalid request'),
  TOO_MANY: problem(429, 'TOO_MANY_REQUESTS', 'too many requests'),
  INTERNAL: problem(500, 'INTERNAL', 'internal error'),
  // 星图、建档、事件、诊断迁进路由表前就有的错误码（H2，接口形状不变）
  CONFLICT: problem(409, 'CONFLICT', 'concurrent update, retry'),
  KEY_REUSED: problem(409, 'IDEMPOTENCY_KEY_REUSED', 'idempotency key reused with different content'),
  PROBE_LINKED: problem(409, 'PROBE_ALREADY_LINKED', 'probe already linked to a different attempt'),
  WRITE_CAP: problem(429, 'TOO_MANY', 'too many writes'),  // 库里的 24 小时写入上限（0025），不是限流
  DIAGNOSIS_UNAVAILABLE: problem(503, 'DIAGNOSIS_UNAVAILABLE', 'diagnosis is not available'),
  // 届别 PR3（specs/policy-decisions-v0.9.1 #10）：当前年级不在 7–9（未入学或已毕业）时不能新选学科，只能暂停已开通的
  GRADE_OUT_OF_RANGE: problem(409, 'GRADE_OUT_OF_RANGE', 'not in a junior-high grade: subjects can only be paused'),
  // SIE-03 PR③ 信任界面：状态不允许（已结束、确认超时、已删除等）、会话内二次确认未通过、洞察服务或读取通道不可用
  STATE_CONFLICT: problem(409, 'STATE_CONFLICT', 'request conflicts with current state'),
  CONFIRM_REQUIRED: problem(403, 'CONFIRMATION_REQUIRED', 'confirmation not accepted'),
  REAUTH_REQUIRED: problem(403, 'REAUTH_REQUIRED', 'sign in again to confirm'),
  TRUST_UNAVAILABLE: problem(503, 'UNAVAILABLE', 'not available right now'),
} as const;

export type ProblemBody = (typeof P)[keyof typeof P];
