// 星图 BFF 的开发启动入口（本冲刺只有星图一个路由）。只监听 loopback；不连接任何共享或生产库（规则 9）。
//   BFF_DATABASE_URL  连接串，登录角色只继承 starbuddy_bff（例如 postgresql://bff_app@/starbuddy_dev?host=/tmp/x）
//   PORT              默认 8787
//   STARBUDDY_DEV_AUTH=1  启用开发身份桩（x-dev-account-id）；不设时所有请求都是 401
//   BFF_LEARNING_URL / BFF_LEARNING_TOKEN  学习服务地址（https 或本机回环）与服务间令牌（≥ 32 字符）；不设时诊断接口 503
//   STARBUDDY_DEV_METER_CLOCK=daytime  只在开发身份桩打开时认：诊断的宵禁判断用“当天北京时间 10 点 + 本小时已过的时间”，
//                    与端到端测试替换的 platform.meter_now() 同一时间轴（apps/web/scripts/e2e.sh），夜里跑端到端也不撞宵禁
import pg from 'pg';
import { httpDiagnosisLearning } from '../diagnosis/learning-client.ts';
import { assertBffRole } from './query.ts';
import { createStarMapServer } from './server.ts';

const url = process.env['BFF_DATABASE_URL'];
if (!url) {
  console.error('star-map: 需要 BFF_DATABASE_URL');
  process.exit(2);
}
const port = Number(process.env['PORT'] ?? 8787);
const devAuth = process.env['STARBUDDY_DEV_AUTH'] === '1';
const pool = new pg.Pool({ connectionString: url, max: 10 });
try {
  await assertBffRole(pool);  // 连接角色配错会让 RLS 整体失效：拒绝启动
} catch (e) {
  console.error(`star-map: ${(e as Error).message}`);
  await pool.end();
  process.exit(2);
}
const learningUrl = process.env['BFF_LEARNING_URL'];
const learningCredential = process.env['BFF_LEARNING_TOKEN'] ?? '';
let diagnosis;
if (learningUrl) {
  if (learningCredential.length < 32 || !/^(https:\/\/|http:\/\/(127\.0\.0\.1|localhost|\[::1\])(:\d+)?(\/|$))/.test(learningUrl)) {
    console.error('star-map: BFF_LEARNING_URL 必须是 https 或本机回环地址，服务间令牌至少 32 个字符');
    await pool.end();
    process.exit(2);
  }
  const devClock = process.env['STARBUDDY_DEV_METER_CLOCK'];
  if (devClock !== undefined && (!devAuth || devClock !== 'daytime')) {
    console.error('star-map: STARBUDDY_DEV_METER_CLOCK 只能是 daytime，且只在 STARBUDDY_DEV_AUTH=1 时可用');
    await pool.end();
    process.exit(2);
  }
  // 当天北京时间 10:00 + 本小时已过的时间（与 e2e.sh 里替换的 platform.meter_now() 同一公式）
  const daytime = () => {
    const n = Date.now();
    const shanghaiDay = Math.floor((n + 8 * 3600_000) / 86_400_000) * 86_400_000 - 8 * 3600_000;
    return shanghaiDay + 10 * 3600_000 + (n % 3600_000);
  };
  diagnosis = { learning: httpDiagnosisLearning({ baseUrl: learningUrl, token: learningCredential }),
                ...(devClock === 'daytime' ? { now: daytime } : {}) };
}
const server = createStarMapServer({ pool, devAuth, ...(diagnosis ? { diagnosis } : {}) });
server.listen(port, '127.0.0.1', () => {
  console.log(JSON.stringify({ event: 'listening', port, dev_auth: devAuth }));
});
const stop = () => server.close(() => void pool.end());
process.on('SIGTERM', stop);
process.on('SIGINT', stop);
