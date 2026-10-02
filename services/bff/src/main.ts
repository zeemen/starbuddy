// BFF 的启动入口（specs/unified-shell-sprint4/spec.md 第 3、4 节，H2）：一个 node:http 服务，只有统一壳路由表一个分发入口
// （src/http/server.ts 的 createShellServer），表外路径一律 404。身份只来自会话（x-sb-session），开发身份桩已删除。
// 只监听 127.0.0.1（部署拓扑随 ADR 10）；不连接任何共享或生产库（规则 9）。
//   STARBUDDY_ENV     dev / test / staging / pilot / production（必填；短信捕获桩只在 dev / test，其余环境没有短信供应商，拒绝启动）
//   BFF_DATABASE_URL  连接串，登录角色只继承 starbuddy_bff（例如 postgresql://bff_app@/starbuddy_dev?host=/tmp/x）
//   PORT              默认 8787
//   BFF_SHELL_ORIGIN  壳的来源（改变状态的请求 Origin 必须等于它）；dev / test 缺省 http://127.0.0.1:3000
//   BFF_LOG_HMAC_KEY、BFF_AUTH_SECRET  十六进制、至少 32 字节；dev / test 不设时随机生成，其余环境不设拒绝启动
//   BFF_TRUST_CLIENT_IP=1  客户端 IP 取 apps/web 转交的 x-sb-client-ip（BFF 只对 apps/web 开放时才能打开）
//   BFF_SMS_CAPTURE_FILE   dev / test：捕获的验证码另写到这个文件（端到端测试读）
//   BFF_AUTH_CONFIG        dev / test：换一份阈值配置（端到端一次要登录很多次，放宽限流）；其余环境设了就拒绝启动，
//                          一律用 config/auth.json
//   BFF_LEARNING_URL / BFF_LEARNING_TOKEN  学习服务地址（https 或本机回环）与服务间令牌（≥ 32 字符）；不设时诊断接口 503、
//                          今日计划每一科“今天暂无安排”
//   BFF_INSIGHT_URL / BFF_INSIGHT_TOKEN    洞察服务地址（https 或本机回环）与 caller=bff 的令牌（≥ 32 字符）；不设时壳内不显示任何判断陈述
//   BFF_SHELL_CONFIG       dev / test：换一份壳的显示规则（config/shell.json 的格式）；其余环境设了就拒绝启动
//   STARBUDDY_TRUST_UI     dev / test：打开信任界面的 BFF 路由（dispute / reconcile / data_rights，逗号分隔；SIE-03 PR③）；
//                          缺省全关、路由不注册；其余环境设了就拒绝启动。异议经 BFF_INSIGHT_URL 调 insight.dispute；
//                          预测与对账卡的读取没有契约通道（缺省 503，lessons PR③ 待人确认）
//   STARBUDDY_STARMAP      dev / test：H5 星图 v1 的开关（starmap_v1[,starmap_dashed]；src/star-map/features.ts）；缺省全关、
//                          v1 路由不注册、v0 不变；其余环境设了就拒绝启动。星态经 BFF_LEARNING_URL 调学习服务 star-map/nodes（同一个服务间令牌），
//                          不配学习服务时 v1 的星全部是中性底星
//   BFF_TRUST_SECRET       信任界面专用密钥（删除确认凭据、异议幂等键）：十六进制 ≥ 32 字节，规则同 BFF_AUTH_SECRET
// 诊断的宵禁判断用数据库的计量时钟（platform.meter_guard_status，迁移 0030），BFF 没有自己的时钟开关。
import pg from 'pg';
import { httpDiagnosisLearning } from './diagnosis/learning-client.ts';
import { isLocalEnv, loadAuthConfig, loadKeys, parseRunEnv } from './http/config.ts';
import type { ShellDeps } from './http/routes.ts';
import { createShellServer } from './http/server.ts';
import { createSmsSender } from './http/sms.ts';
import { loadShellConfig } from './shell/config.ts';
import { httpInsightClient } from './shell/insight-client.ts';
import { httpPlanClient } from './shell/plan-client.ts';
import { httpDisputeClient } from './trust/clients.ts';
import { parseTrustFeatures } from './trust/features.ts';
import { assertBffRole } from './star-map/query.ts';
import { parseStarMapFeatures } from './star-map/features.ts';
import { httpStarMapClient } from './star-map/nodes-client.ts';

// 配置不全、环境不认识、非 dev / test 却没有短信供应商：一律拒绝启动（在连数据库之前）
let shellDeps: Omit<ShellDeps, 'pool'>;
try {
  const env = parseRunEnv(process.env['STARBUDDY_ENV']);
  const starMapFeatures = parseStarMapFeatures(env, process.env['STARBUDDY_STARMAP']);  // 非 dev / test 设了就拒绝启动
  const configPath = process.env['BFF_AUTH_CONFIG'];
  if (configPath !== undefined && !isLocalEnv(env)) throw new Error('BFF_AUTH_CONFIG 只在 dev / test 可用');
  const keys = loadKeys(env);
  const shellOrigin = process.env['BFF_SHELL_ORIGIN'] ?? (isLocalEnv(env) ? 'http://127.0.0.1:3000' : '');
  if (!/^https?:\/\/[^/]+$/.test(shellOrigin) || (!isLocalEnv(env) && !shellOrigin.startsWith('https://'))) {
    throw new Error('BFF_SHELL_ORIGIN 必须是壳的来源（scheme://host[:port]，dev / test 以外必须是 https）');
  }
  const shellConfigPath = process.env['BFF_SHELL_CONFIG'];
  if (shellConfigPath !== undefined && !isLocalEnv(env)) throw new Error('BFF_SHELL_CONFIG 只在 dev / test 可用');
  shellDeps = { config: configPath === undefined ? loadAuthConfig() : loadAuthConfig(configPath), keys, env, sms: createSmsSender(env), shellOrigin,
                trustClientIpHeader: process.env['BFF_TRUST_CLIENT_IP'] === '1',
                shell: { config: shellConfigPath === undefined ? loadShellConfig() : loadShellConfig(shellConfigPath) },
                trust: { features: parseTrustFeatures(env, process.env['STARBUDDY_TRUST_UI']) },
                starMap: { features: starMapFeatures } };
  // 连续使用提醒的计时在进程内（src/shell/rest.ts）：dev / test 以外没声明单实例部署就不启用，并在启动时告警（pilot 前须完成会话表加列迁移）
  if (!isLocalEnv(env) && !shellDeps.shell!.config!.rest.singleInstance) {
    console.warn(JSON.stringify({ event: 'rest_reminder_disabled', reason: 'in_process_timer_requires_single_instance' }));
  }
} catch (e) {
  console.error(`bff: ${(e as Error).message}`);
  process.exit(2);
}

const url = process.env['BFF_DATABASE_URL'];
if (!url) {
  console.error('bff: 需要 BFF_DATABASE_URL');
  process.exit(2);
}
const port = Number(process.env['PORT'] ?? 8787);
const pool = new pg.Pool({ connectionString: url, max: 10 });
try {
  await assertBffRole(pool);  // 连接角色配错会让 RLS 整体失效：拒绝启动
} catch (e) {
  console.error(`bff: ${(e as Error).message}`);
  await pool.end();
  process.exit(2);
}
const learningUrl = process.env['BFF_LEARNING_URL'];
const learningCredential = process.env['BFF_LEARNING_TOKEN'] ?? '';
if (learningUrl) {
  if (learningCredential.length < 32 || !/^(https:\/\/|http:\/\/(127\.0\.0\.1|localhost|\[::1\])(:\d+)?(\/|$))/.test(learningUrl)) {
    console.error('bff: BFF_LEARNING_URL 必须是 https 或本机回环地址，服务间令牌至少 32 个字符');
    await pool.end();
    process.exit(2);
  }
  shellDeps.diagnosis = { learning: httpDiagnosisLearning({ baseUrl: learningUrl, token: learningCredential }) };
  shellDeps.shell!.plan = httpPlanClient({ baseUrl: learningUrl, token: learningCredential, timeoutMs: shellDeps.shell!.config!.plan.timeoutMs });
  if (shellDeps.starMap!.features!.has('starmap_v1')) {
    shellDeps.starMap!.nodes = httpStarMapClient({ baseUrl: learningUrl, token: learningCredential, timeoutMs: shellDeps.shell!.config!.starMap.timeoutMs });
  }
}
const insightUrl = process.env['BFF_INSIGHT_URL'];
const insightCredential = process.env['BFF_INSIGHT_TOKEN'] ?? '';
if (insightUrl) {
  if (insightCredential.length < 32 || insightCredential === learningCredential
      || !/^(https:\/\/|http:\/\/(127\.0\.0\.1|localhost|\[::1\])(:\d+)?(\/|$))/.test(insightUrl)) {
    console.error('bff: BFF_INSIGHT_URL 必须是 https 或本机回环地址，令牌至少 32 个字符且与学习服务令牌不同');
    await pool.end();
    process.exit(2);
  }
  shellDeps.shell!.insight = httpInsightClient({ baseUrl: insightUrl, token: insightCredential });
  shellDeps.trust!.dispute = httpDisputeClient({ baseUrl: insightUrl, token: insightCredential });
}
const server = createShellServer({ ...shellDeps, pool });
server.listen(port, '127.0.0.1', () => {
  console.log(JSON.stringify({ event: 'listening', port }));
});
const stop = () => server.close(() => void pool.end());
process.on('SIGTERM', stop);
process.on('SIGINT', stop);
