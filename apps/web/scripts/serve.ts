// 前端容器的启动包装（LT-01 安全审查 A1）：先按 src/lib/serve-env.ts 核对环境（监听地址、运行环境、镜像标记、staging 的同意版本），
// 不合格以退出码 2 拒绝启动；通过后以同一进程组起 next start（-H 监听地址 -p 端口），信号原样转交，退出码原样返回。
// 用法（镜像里）：node --no-warnings scripts/serve.ts
import { spawn } from 'node:child_process';
import { checkServeEnv } from '../src/lib/serve-env.ts';

let target: { host: string; port: number };
try {
  target = checkServeEnv(process.env);
} catch (e) {
  console.error(`web: ${(e as Error).message}`);
  process.exit(2);
}
const child = spawn(process.execPath, ['node_modules/next/dist/bin/next', 'start', '-H', target.host, '-p', String(target.port)],
                    { stdio: 'inherit', env: process.env });
for (const sig of ['SIGTERM', 'SIGINT'] as const) process.on(sig, () => child.kill(sig));
child.on('exit', (code, signal) => process.exit(code ?? (signal ? 1 : 0)));
