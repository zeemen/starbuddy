// 存活端点（LT-03 范围第 4 条，specs/launch-lt03-cloud-staging）：GET /healthz → 200 {"status":"ok"}。
// 不访问任何依赖（不调 BFF、不读会话与 cookie），无需登录；响应只含状态，no-store。其他方法由 Next 回 405。
// 口径与五个服务一致（services/*/openapi/health.v0.yaml）；就绪见 ../readyz/route.ts。
export const dynamic = 'force-dynamic';

export function GET(): Response {
  return Response.json({ status: 'ok' }, { headers: { 'cache-control': 'no-store' } });
}
