// 以某个账号的身份在一个事务里调用计量函数：app.account_id 只在本事务内有效（SET LOCAL 语义），
// 数据库函数按它判断授权（platform.current_account_id()）。
import type { Pool, PoolClient } from 'pg';

export async function withAccount<T>(pool: Pool, accountId: string, fn: (client: PoolClient) => Promise<T>): Promise<T> {
  const client = await pool.connect();
  let broken = false;
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
    client.release(broken);
  }
}
