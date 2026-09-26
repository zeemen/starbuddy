// 定时任务入口：以 starbuddy_scheduler 身份（连接池的登录角色只继承它）调用过期与续期函数。
// 本冲刺只提供一次性执行入口；接入调度器是 ADR 03 的事。
import type { Pool } from 'pg';

// 小批量循环：每批一个事务，持锁时间短（0009 的 meter_expire_due 一批在一个事务里）
export async function runExpiry(pool: Pool, batchSize = 50): Promise<number> {
  let total = 0;
  for (;;) {
    const { rows } = await pool.query('SELECT platform.meter_expire_due($1) AS n', [batchSize]);
    const n = rows[0].n as number;
    total += n;
    if (n < batchSize) return total;
  }
}

export async function runRenewal(pool: Pool, batchSize = 50): Promise<number> {
  let total = 0;
  for (;;) {
    const { rows } = await pool.query('SELECT platform.meter_renew_due($1) AS n', [batchSize]);
    const n = rows[0].n as number;
    total += n;
    if (n < batchSize) return total;
  }
}
