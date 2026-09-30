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
} as const;

export type ProblemBody = (typeof P)[keyof typeof P];
