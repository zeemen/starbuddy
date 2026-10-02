// H5 星图 v1 的路由（specs/star-map-v1-h5/spec.md 第 5 节，待人确认 #4）：独立路径 GET /students/{student_id}/star-map/v1。
// 选独立路径而不是 `?v=1`：BFF 的路由表只按路径分发（server.ts 不把查询参数交给 handler，身份注入测试把查询参数一律当噪声），
// 接口定义（openapi/*.yaml）与路由表也按“方法 + 路径”一一对应；同一路径按查询参数分形状会破坏这两条不变量。
// 挂在开关 starmap_v1 上（src/star-map/features.ts），默认关闭：关闭时不注册，与表外路径同一个 404；v0 路由与响应不变。
import { loadShellConfig, type ShellConfig } from '../shell/config.ts';
import { getStarMapV1 } from '../star-map/v1.ts';
import { P } from './problems.ts';
import type { Handler, Reply, Route, ShellDeps } from './routes.ts';

let defaultConfig: ShellConfig | null = null;
const shellConfig = (deps: ShellDeps): ShellConfig => deps.shell?.config ?? (defaultConfig ??= loadShellConfig());
const problem = (body: string): Reply => ({ status: (JSON.parse(body) as { status: number }).status, body });

const starMapV1: Handler = async (ctx) => {
  const s = ctx.session!;
  const dashed = ctx.deps.starMap?.features?.has('starmap_dashed') === true;
  // student_id 只取已校验（UUID）的路径参数；授权在 getStarMapV1 的第一个事务里由 platform.can_read_student 决定
  const map = await getStarMapV1(ctx.deps, shellConfig(ctx.deps), s, ctx.params['student_id']!, dashed, ctx.note);
  return map ? { status: 200, body: JSON.stringify(map) } : problem(P.NOT_FOUND);
};

export const STAR_MAP_V1_ROUTES: readonly Route[] = [
  { method: 'GET', path: '/students/{student_id}/star-map/v1', auth: 'session', csrf: 'none', body: false, ownTx: true, handler: starMapV1, rateLimits: [] },
];
