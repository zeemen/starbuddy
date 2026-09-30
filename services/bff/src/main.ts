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
//   BFF_LEARNING_URL / BFF_LEARNING_TOKEN  学习服务地址（https 或本机回环）与服务间令牌（≥ 32 字符）；不设时诊断接口 503
// 诊断的宵禁判断用数据库的计量时钟（platform.meter_guard_status，迁移 0030），BFF 没有自己的时钟开关。
import pg from 'pg';
import { httpDiagnosisLearning } from './diagnosis/learning-client.ts';
import { isLocalEnv, loadAuthConfig, loadKeys, parseRunEnv } from './http/config.ts';
import type { ShellDeps } from './http/routes.ts';
import { createShellServer } from './http/server.ts';
import { createSmsSender } from './http/sms.ts';
import { assertBffRole } from './star-map/query.ts';

// 配置不全、环境不认识、非 dev / test 却没有短信供应商：一律拒绝启动（在连数据库之前）
let shellDeps: Omit<ShellDeps, 'pool'>;
try {
  const env = parseRunEnv(process.env['STARBUDDY_ENV']);
  const configPath = process.env['BFF_AUTH_CONFIG'];
  if (configPath !== undefined && !isLocalEnv(env)) throw new Error('BFF_AUTH_CONFIG 只在 dev / test 可用');
  const keys = loadKeys(env);
  const shellOrigin = process.env['BFF_SHELL_ORIGIN'] ?? (isLocalEnv(env) ? 'http://127.0.0.1:3000' : '');
  if (!/^https?:\/\/[^/]+$/.test(shellOrigin) || (!isLocalEnv(env) && !shellOrigin.startsWith('https://'))) {
    throw new Error('BFF_SHELL_ORIGIN 必须是壳的来源（scheme://host[:port]，dev / test 以外必须是 https）');
  }
  shellDeps = { config: configPath === undefined ? loadAuthConfig() : loadAuthConfig(configPath), keys, env, sms: createSmsSender(env), shellOrigin,
                trustClientIpHeader: process.env['BFF_TRUST_CLIENT_IP'] === '1' };
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
}
const server = createShellServer({ ...shellDeps, pool });
server.listen(port, '127.0.0.1', () => {
  console.log(JSON.stringify({ event: 'listening', port }));
});
const stop = () => server.close(() => void pool.end());
process.on('SIGTERM', stop);
process.on('SIGINT', stop);
