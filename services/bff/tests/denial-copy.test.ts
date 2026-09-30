// 拒绝提示组件的穷举检查（spec 第 9 节、验收“拒绝提示组件”）：计量模块每个拒绝 outcome 与自练的 daily_limit_reached
// 在 apps/web/src/shell/denials.ts 都有文案。类型层：下面的赋值在缺键时编译失败（bff-test 先跑 tsc）；运行层：再核对一遍键集合，
// 并查学生端文案不含购买、升级、营销类词。
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { DENIAL_COPY } from '../../../apps/web/src/shell/denials.ts';
import type { MeteredCallResult, ReserveResult } from '../src/metering/meter.ts';

type ReserveRejected = Exclude<ReserveResult['outcome'], 'reserved' | 'dedup' | 'existing'>;
type CallRejected = Exclude<MeteredCallResult['status'], 'ok' | 'failed' | 'failed_previously' | 'expired_retry_with_new_key' | 'unknown'>;
/** 计量新增拒绝原因而 denials.ts 没有文案：这一行编译失败 */
const EXHAUSTIVE: Record<ReserveRejected | CallRejected | 'daily_limit_reached', string> = DENIAL_COPY;

test('拒绝提示：每个拒绝原因都有文案；学生端文案不含购买、升级、营销类词', () => {
  const want = ['retained_no_ai', 'daily_cap_reached', 'curfew', 'curfew_soon', 'no_entitlement', 'insufficient', 'conflict',
                'dedup_in_progress', 'daily_limit_reached'];
  assert.deepEqual(Object.keys(EXHAUSTIVE).sort(), want.sort());
  for (const s of Object.values(DENIAL_COPY)) {
    assert.ok(s.length > 0);
    assert.ok(!/购买|升级|会员|优惠|续费|充值|付费|限时/.test(s), s);
  }
});
