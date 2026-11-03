// 宵禁判断的共享读取（诊断：安全审查 B2；错题再练：PR 3 审查 B1）。
// 只读：以学生本人身份调计量只读函数 platform.meter_guard_status（迁移 0030，4-H H3），取它的 in_curfew——
// 与 meter_reserve 同一个函数、同一个计量时钟（meter_now），BFF 不自算宵禁（SIE-02a lessons #15）。不改任何计量函数与额度。
// 调用方各自决定宵禁中怎么回（诊断先暂停会话再 402 curfew；再练直接 402 curfew，不计次、不调学习服务）。
import type { Pool } from 'pg';
import { withAccount } from './db.ts';

/** 授权与 0015 相同：本人或已绑定家长，否则 42501（→ 'forbidden'，调用方按自己的“不是本人”口径回） */
export async function inCurfew(pool: Pool, account: string, student: string): Promise<boolean | 'forbidden'> {
  try {
    return await withAccount(pool, account, async (c) => {
      const { rows } = await c.query<{ s: { in_curfew: boolean } }>('SELECT platform.meter_guard_status($1::uuid) AS s', [student]);
      return rows[0]!.s.in_curfew === true;
    });
  } catch (e) {
    if ((e as { code?: string }).code === '42501') return 'forbidden';  // 调用前已核对过本人；绑定或账号状态刚变时
    throw e;
  }
}
