// 以某个账号的身份在一个事务里调用计量函数：app.account_id 只在本事务内有效（SET LOCAL 语义），
// 数据库函数按它判断授权（platform.current_account_id()）。
import type { Pool, PoolClient } from 'pg';

export async function withAccount<T>(pool: Pool, accountId: string, fn: (client: PoolClient) => Promise<T>): Promise<T> {
  const client = await pool.connect();
  let broken = false;
  // 借出的连接，pg-pool 不再替它监听 error。库端断开（事务闲置超时 25P03、主备切换、网络重置）时，没有监听的 error 事件
  // 会成为未捕获异常、整个进程退出（PR 28 合并前安全审查第 1 条，本机复现过）。这里只标记：之后的语句照常报错走 catch，release 时销毁
  const onError = (): void => { broken = true; };
  client.on?.('error', onError);  // 真实连接一定有；单测的假连接没有
  try {
    await client.query('BEGIN');
    await client.query("SELECT set_config('app.account_id', $1, true)", [accountId]);
    const result = await fn(client);
    await client.query('COMMIT');
    return result;
  } catch (err) {
    try {
      await client.query('ROLLBACK');
    } catch {
      broken = true;  // 连接已断：交还连接池时销毁，不复用
    }
    throw err;
  } finally {
    // 连接还好时摘掉监听再还回池里（否则每借一次多挂一个）；坏了的连接要销毁，留着监听接住销毁途中可能再来的 error
    if (!broken) client.off?.('error', onError);
    client.release(broken);
  }
}
