// 就绪端点（LT-03 范围第 4 条，specs/launch-lt03-cloud-staging）：GET /readyz。
// 前端没有自己的库；就绪 = 启动包装 scripts/serve.ts 的同一套核对（src/lib/serve-env.ts：运行环境、镜像标记、监听地址、端口、
// staging 的两份同意版本与开发开关）在当前进程环境下仍然通过 → 200 {"status":"ready"}，否则 503 {"status":"not_ready"}。
// 不调 BFF（BFF 有自己的探针，不把下游故障级联成前端摘流）；无需登录；响应只含状态（不回显核对失败的原因），no-store。
import { checkServeEnv } from '../../lib/serve-env.ts';

export const dynamic = 'force-dynamic';

export function GET(): Response {
  let ok = true;
  try {
    checkServeEnv(process.env);
  } catch {
    ok = false;
  }
  return Response.json({ status: ok ? 'ready' : 'not_ready' }, { status: ok ? 200 : 503, headers: { 'cache-control': 'no-store' } });
}
