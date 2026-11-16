// 畸形请求不得让 BFF 进程退出（内测前整改 P1）：`GET //[/` 这类路径会让 new URL(...) 抛异常；原来解析在 try 之外、
// 外层 Promise 链也没有 catch，未处理的拒绝让进程以状态 1 退出。修复后：解析不了的路径按表外处理（与“不存在”同一个 404），
// 处理链里任何未预期的异常一律回统一的 500，进程照常服务下一个请求。
// 在子进程里起服务再发原始请求：修复前进程会退出，放在子进程里免得把测试本身拖垮。
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { connect } from 'node:net';
import { test } from 'node:test';

// 子进程用 import 加载：给完整的 file: URL（不是文件系统路径，也不是 URL 的 pathname），目录名含中文或 % # 时都能解析
const SERVER = new URL('../src/http/server.ts', import.meta.url).href;
const CONFIG = new URL('../src/http/config.ts', import.meta.url).href;

// 子进程：起 createShellServer（连接池是不连库的桩），把端口打到 stdout
const child = (log: string) => `
import { createShellServer } from ${JSON.stringify(SERVER)};
import { loadAuthConfig } from ${JSON.stringify(CONFIG)};
const pool = { connect: async () => ({ query: async () => ({ rows: [{ ok: 1 }] }), release: () => {} }), query: async () => ({ rows: [] }) };
const deps = { pool, config: loadAuthConfig(), log: ${log} };
const server = createShellServer(deps, { readyCacheMs: 0 });
server.listen(0, '127.0.0.1', () => process.stdout.write('PORT ' + server.address().port + '\\n'));
`;

/** 用原始 TCP 发一行请求，拿回状态行（连接被对端关闭时返回 null） */
function raw(port: number, requestLine: string): Promise<string | null> {
  return new Promise((resolve) => {
    const s = connect(port, '127.0.0.1');
    let buf = '';
    s.on('data', (d) => { buf += d.toString('latin1'); });
    s.on('close', () => resolve(buf ? buf.split('\r\n')[0]! : null));
    s.on('error', () => resolve(null));
    s.write(`${requestLine}\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n`);
  });
}

async function startChild(log: string) {
  const proc = spawn(process.execPath, ['--no-warnings', '--input-type=module', '-e', child(log)], { stdio: ['ignore', 'pipe', 'pipe'] });
  const state = { exited: null as number | null };
  proc.on('exit', (code) => { state.exited = code ?? -1; });
  const port = await new Promise<number>((resolve, reject) => {
    let out = '';
    proc.stdout.on('data', (d) => { out += d; const m = /PORT (\d+)/.exec(out); if (m) resolve(Number(m[1])); });
    proc.on('exit', () => reject(new Error('子进程没起来')));
  });
  return { proc, state, port };
}

test('畸形路径（//[/ 等）回 404，进程不退出，之后的请求照常应答', { timeout: 30_000 }, async () => {
  const { proc, state, port } = await startChild('() => {}');
  const exitedNow = () => state.exited;
  try {
    for (const path of ['//[/', '//[', '//a:b@[/', '/%', '//[::1/']) {
      const line = await raw(port, `GET ${path} HTTP/1.1`);
      await new Promise((r) => setTimeout(r, 100));
      assert.equal(exitedNow(), null, `发 ${path} 之后 BFF 进程退出了（状态 ${exitedNow()}）`);
      assert.ok(line === null || /^HTTP\/1\.1 (404|400) /.test(line), `${path}：状态行 ${line}`);
    }
    const ok = await raw(port, 'GET /healthz HTTP/1.1');
    assert.match(ok ?? '', /^HTTP\/1\.1 200 /, '畸形请求之后，健康端点仍然应答');
  } finally {
    proc.kill();
  }
});

test('处理链里未预期的异常（这里让访问日志抛错）不会让进程退出，之后的请求照常应答', { timeout: 30_000 }, async () => {
  const { proc, state, port } = await startChild("() => { throw new Error('synthetic log failure'); }");
  try {
    const first = await raw(port, 'GET /bff/v0/me HTTP/1.1');
    await new Promise((r) => setTimeout(r, 100));
    assert.equal(state.exited, null, `请求之后 BFF 进程退出了（状态 ${state.exited}）`);
    assert.match(first ?? '', /^HTTP\/1\.1 401 /, '没带会话仍回 401（响应在写日志之前已发出）');
    const again = await raw(port, 'GET /healthz HTTP/1.1');
    assert.match(again ?? '', /^HTTP\/1\.1 200 /);
  } finally {
    proc.kill();
  }
});
