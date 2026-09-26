// 星图 BFF 的开发启动入口（本冲刺只有星图一个路由）。只监听 loopback；不连接任何共享或生产库（规则 9）。
//   BFF_DATABASE_URL  连接串，登录角色只继承 starbuddy_bff（例如 postgresql://bff_app@/starbuddy_dev?host=/tmp/x）
//   PORT              默认 8787
//   STARBUDDY_DEV_AUTH=1  启用开发身份桩（x-dev-account-id）；不设时所有请求都是 401
import pg from 'pg';
import { createStarMapServer } from './server.ts';

const url = process.env['BFF_DATABASE_URL'];
if (!url) {
  console.error('star-map: 需要 BFF_DATABASE_URL');
  process.exit(2);
}
const port = Number(process.env['PORT'] ?? 8787);
const devAuth = process.env['STARBUDDY_DEV_AUTH'] === '1';
const pool = new pg.Pool({ connectionString: url, max: 10 });
const server = createStarMapServer({ pool, devAuth });
server.listen(port, '127.0.0.1', () => {
  console.log(JSON.stringify({ event: 'listening', port, dev_auth: devAuth }));
});
const stop = () => server.close(() => void pool.end());
process.on('SIGTERM', stop);
process.on('SIGINT', stop);
