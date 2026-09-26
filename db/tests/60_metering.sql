-- 0008 计量账本与权益（specs/metering-schema-sprint1/spec.md 验收标准）
-- 写入以超级用户模拟 2-E 的 SECURITY DEFINER 函数（本冲刺不建函数）；触发器与约束对超级用户同样生效。
-- 流水的 occurred_at 与预留的 reserved_at 写死在 2026-10（已建分区内），不依赖运行日期。
-- 合成 ID：8e 开头为订阅，8f 为额度桶，9a 为预留，9b 为流水。
-- 凡是“会被拒绝”的断言，同一 SQLSTATE 可能来自多条规则时用 t.throws_like 核对错误信息，避免被别的规则顶替。

-- ================= 一、种子：与完整方案第八章逐格一致 =================
SELECT t.ok((SELECT count(*) FROM platform.resource_type) = 7, '资源类型 7 种');
SELECT t.ok((SELECT status FROM platform.plan_config WHERE version = 'ch8-hypothesis@1') = 'draft',
            '第八章额度以草稿版本入库（ch8-hypothesis@1，待产品发布）');
SELECT t.ok(NOT EXISTS (
  WITH expected(tier, res, qty, fair) AS (VALUES
    ('basic', 'diagnosis_screen', 1, false), ('basic', 'diagnosis_full', 1, false), ('basic', 'mock_exam', 0, false),
    ('basic', 'mistake_analysis', 20, false), ('basic', 'lesson_whiteboard', 0, false), ('basic', 'voice_realtime', 10, false),
    ('basic', 'ai_active_time', 120, false),
    ('plus', 'diagnosis_screen', 1, false), ('plus', 'diagnosis_full', 2, false), ('plus', 'mock_exam', 1, false),
    ('plus', 'mistake_analysis', 100, false), ('plus', 'lesson_whiteboard', 10, false), ('plus', 'voice_realtime', 60, false),
    ('plus', 'ai_active_time', 600, false),
    ('pro', 'diagnosis_screen', 1, false), ('pro', 'diagnosis_full', 6, false), ('pro', 'mock_exam', 4, false),
    ('pro', 'mistake_analysis', 300, false), ('pro', 'lesson_whiteboard', 40, false), ('pro', 'voice_realtime', 240, false),
    ('pro', 'ai_active_time', 1800, false),
    ('max', 'diagnosis_screen', 1, false), ('max', 'diagnosis_full', 12, false), ('max', 'mock_exam', 10, false),
    ('max', 'mistake_analysis', 800, false), ('max', 'lesson_whiteboard', 100, false), ('max', 'voice_realtime', 600, false),
    ('max', 'ai_active_time', 4000, true))
  SELECT 1 FROM expected e
  FULL JOIN (SELECT * FROM platform.plan_allowance WHERE config_version = 'ch8-hypothesis@1') a
    ON a.tier::text = e.tier AND a.resource_type = e.res
  WHERE a.tier IS NULL OR e.tier IS NULL OR a.quantity <> e.qty OR a.fair_use <> e.fair),
  '四级 × 七种资源的额度与第八章表格逐格一致（28 格，含 MAX 公平使用上限；付费档筛查取 1 待确认）');
SELECT t.ok(NOT EXISTS (
  WITH expected(tier, feature, val) AS (VALUES
    ('basic', 'max_active_subjects', '1'), ('plus', 'max_active_subjects', '2'),
    ('pro', 'max_active_subjects', '4'), ('max', 'max_active_subjects', 'all_launched'),
    ('basic', 'lesson_mode', 'static_steps'), ('plus', 'lesson_mode', 'whiteboard'),
    ('pro', 'lesson_mode', 'whiteboard'), ('max', 'lesson_mode', 'whiteboard'),
    ('basic', 'weekly_report', 'basic_and_public_courses'), ('plus', 'weekly_report', 'full'),
    ('pro', 'weekly_report', 'cross_subject_and_monthly'), ('max', 'weekly_report', 'cross_subject_deep'),
    ('basic', 'appeal_priority', 'standard'), ('plus', 'appeal_priority', 'standard'),
    ('pro', 'appeal_priority', 'standard'), ('max', 'appeal_priority', 'priority_human'))
  SELECT 1 FROM expected e
  FULL JOIN (SELECT * FROM platform.plan_feature WHERE config_version = 'ch8-hypothesis@1') f
    ON f.tier::text = e.tier AND f.feature_code = e.feature
  WHERE f.tier IS NULL OR e.tier IS NULL OR f.value_code <> e.val),
  '四级权益（可选学科数、讲评形式、报告、申诉）与第八章一致（16 格）');
SELECT t.ok((SELECT dedup_window FROM platform.resource_type WHERE code = 'mistake_analysis') = interval '24 hours'
            AND (SELECT count(*) FROM platform.resource_type WHERE dedup_window IS NOT NULL) = 1,
            '只有错题解析有 24 小时复看不重复扣');
SELECT t.ok((SELECT count(*) FROM platform.resource_type WHERE unit = 'minute' AND rounding = 'ceil') = 2
            AND (SELECT pool FROM platform.resource_type WHERE code = 'voice_realtime') = 'voice',
            '语音与 AI 互动时长按分钟向上取整；语音单独成池');
SELECT t.throws_like($$INSERT INTO platform.resource_type (code, unit, rounding, pool, reservation_ttl, max_per_reservation)
                       VALUES ('synthetic_minutes', 'minute', 'none', 'general', interval '10 minutes', 1)$$,
                     '23514', 'resource_minute_rounds_up', '按分钟计的资源必须向上取整');
SELECT t.throws_like($$INSERT INTO platform.resource_type (code, unit, rounding, dedup_window, pool, reservation_ttl, max_per_reservation)
                       VALUES ('synthetic_batch', 'count', 'none', interval '1 day', 'general', interval '10 minutes', 5)$$,
                     '23514', 'resource_dedup_single', '可去重的资源一笔预留只能对应一份内容');

-- ================= 二、额度配置：草稿可改，发布后只读 =================
INSERT INTO platform.plan_config (version, note_code) VALUES ('synthetic-plan@1', 'synthetic_test_plan');
INSERT INTO platform.plan_allowance (config_version, tier, resource_type, quantity, fair_use)
SELECT 'synthetic-plan@1', tier, resource_type, quantity, fair_use FROM platform.plan_allowance WHERE config_version = 'ch8-hypothesis@1';
INSERT INTO platform.plan_feature (config_version, tier, feature_code, value_code)
SELECT 'synthetic-plan@1', tier, feature_code, value_code FROM platform.plan_feature WHERE config_version = 'ch8-hypothesis@1';
UPDATE platform.plan_allowance SET quantity = 301 WHERE config_version = 'synthetic-plan@1' AND tier = 'pro' AND resource_type = 'mistake_analysis';
UPDATE platform.plan_allowance SET quantity = 300 WHERE config_version = 'synthetic-plan@1' AND tier = 'pro' AND resource_type = 'mistake_analysis';
SELECT t.ok(true, '草稿配置的额度可以增改');
SELECT t.throws($$UPDATE platform.plan_allowance SET quantity = 0.5 WHERE config_version = 'synthetic-plan@1' AND tier = 'pro'
                    AND resource_type = 'mistake_analysis'$$, '23514', '额度必须是整数');
SELECT t.throws_like($$INSERT INTO platform.subscription (household_id, student_id, tier, config_version, source, period_start, period_end)
                       VALUES ('d1000000-0000-4000-8000-0000000000d1', 'c1000000-0000-4000-8000-0000000000c1', 'pro', 'synthetic-plan@1',
                               'pilot_virtual', now() - interval '1 day', now() + interval '29 days')$$,
                     '23514', 'published plan_config', '订阅不能引用草稿配置');
INSERT INTO platform.plan_config (version, note_code) VALUES ('synthetic-plan@3', 'synthetic_test_plan');
SELECT t.throws_like($$UPDATE platform.plan_config SET status = 'published', published_at = now() WHERE version = 'synthetic-plan@3'$$,
                     '23514', 'incomplete', '额度或权益不齐全的配置不能发布');
UPDATE platform.plan_config SET status = 'published', published_at = now() WHERE version = 'synthetic-plan@1';
SELECT t.ok(true, '齐全的草稿可以发布');
SELECT t.throws_like($$UPDATE platform.plan_allowance SET quantity = 9999 WHERE config_version = 'synthetic-plan@1'$$,
                     '23514', 'rows of a published', '已发布配置的额度不能改');
SELECT t.throws_like($$INSERT INTO platform.plan_allowance (config_version, tier, resource_type, quantity)
                       VALUES ('synthetic-plan@1', 'basic', 'mock_exam', 1)
                       ON CONFLICT (config_version, tier, resource_type) DO UPDATE SET quantity = 1$$,
                     '23514', 'rows of a published', '已发布配置不能补插或覆盖额度');
SELECT t.throws_like($$DELETE FROM platform.plan_allowance WHERE config_version = 'synthetic-plan@1'$$,
                     '23514', 'rows of a published', '已发布配置的额度不能删');
SELECT t.throws_like($$UPDATE platform.plan_feature SET value_code = 'priority_human' WHERE config_version = 'synthetic-plan@1'$$,
                     '23514', 'rows of a published', '已发布配置的权益不能改');
SELECT t.throws_like($$UPDATE platform.plan_allowance SET config_version = 'ch8-hypothesis@1', quantity = 1
                       WHERE config_version = 'synthetic-plan@1' AND tier = 'basic' AND resource_type = 'mock_exam'$$,
                     '23514', 'config_version cannot be changed', '不能把已发布配置的额度行挪到草稿上改');
SELECT t.throws_like($$UPDATE platform.plan_allowance SET config_version = 'synthetic-plan@1', quantity = 9999
                       WHERE config_version = 'ch8-hypothesis@1' AND tier = 'basic' AND resource_type = 'mock_exam'$$,
                     '23514', 'config_version cannot be changed', '不能把草稿的额度行挪进已发布配置');
SELECT t.throws_like($$UPDATE platform.plan_config SET note_code = 'changed' WHERE version = 'synthetic-plan@1'$$,
                     '23514', 'cannot be changed', '已发布配置的说明不能改');
SELECT t.throws($$UPDATE platform.plan_config SET status = 'draft', published_at = NULL WHERE version = 'synthetic-plan@1'$$,
                '23514', '已发布配置不能退回草稿');
SELECT t.throws_like($$DELETE FROM platform.plan_config WHERE version = 'synthetic-plan@1'$$, '23514', 'not a draft', '已发布配置不能删');

-- 停用：已发布 → 已停用；停用后彻底只读，也不能再开新订阅
INSERT INTO platform.plan_config (version, note_code) VALUES ('synthetic-plan@2', 'synthetic_test_plan');
INSERT INTO platform.plan_allowance (config_version, tier, resource_type, quantity, fair_use)
SELECT 'synthetic-plan@2', tier, resource_type, quantity, fair_use FROM platform.plan_allowance WHERE config_version = 'ch8-hypothesis@1';
INSERT INTO platform.plan_feature (config_version, tier, feature_code, value_code)
SELECT 'synthetic-plan@2', tier, feature_code, value_code FROM platform.plan_feature WHERE config_version = 'ch8-hypothesis@1';
UPDATE platform.plan_config SET status = 'published', published_at = now() WHERE version = 'synthetic-plan@2';
UPDATE platform.plan_config SET status = 'retired', retired_at = now() WHERE version = 'synthetic-plan@2';
SELECT t.ok(true, '已发布配置可以停用');
SELECT t.throws_like($$UPDATE platform.plan_config SET status = 'published', retired_at = NULL WHERE version = 'synthetic-plan@2'$$,
                     '23514', 'is retired', '已停用配置不能恢复');
SELECT t.throws_like($$INSERT INTO platform.subscription (household_id, student_id, tier, config_version, source, period_start, period_end)
                       VALUES ('d1000000-0000-4000-8000-0000000000d1', 'c1000000-0000-4000-8000-0000000000c1', 'pro', 'synthetic-plan@2',
                               'pilot_virtual', now() - interval '1 day', now() + interval '29 days')$$,
                     '23514', 'published plan_config', '订阅不能引用已停用配置');

-- ================= 三、订阅：挂在家庭，按学生席位 =================
INSERT INTO platform.subscription (id, household_id, student_id, tier, config_version, source, period_start, period_end) VALUES
  ('8e000000-0000-4000-8000-0000000000c1', 'd1000000-0000-4000-8000-0000000000d1', 'c1000000-0000-4000-8000-0000000000c1',
   'pro', 'synthetic-plan@1', 'pilot_virtual', now() - interval '1 day', now() + interval '29 days'),
  ('8e000000-0000-4000-8000-0000000000c2', 'd2000000-0000-4000-8000-0000000000d2', 'c2000000-0000-4000-8000-0000000000c2',
   'basic', 'synthetic-plan@1', 'trial', now() - interval '1 day', now() + interval '29 days');
SELECT t.ok(true, '试点虚拟额度与基础会员都走订阅表');
SELECT t.throws_like($$INSERT INTO platform.subscription (household_id, student_id, tier, config_version, source, period_start, period_end)
                       VALUES ('d2000000-0000-4000-8000-0000000000d2', 'c1000000-0000-4000-8000-0000000000c1', 'pro', 'synthetic-plan@1',
                               'paid', now(), now() + interval '30 days')$$,
                     '23514', 'does not belong', '学生必须属于订阅所在的家庭');
SELECT t.throws($$INSERT INTO platform.subscription (household_id, student_id, tier, config_version, source, period_start, period_end)
                  VALUES ('d1000000-0000-4000-8000-0000000000d1', 'c1000000-0000-4000-8000-0000000000c1', 'max', 'synthetic-plan@1',
                          'paid', now(), now() + interval '30 days')$$,
                '23505', '一个学生席位同时只有一个有效订阅');
INSERT INTO platform.subscription (id, household_id, student_id, tier, config_version, source, period_start, period_end)
VALUES ('8e000000-0000-4000-8000-0000000000a1', 'd1000000-0000-4000-8000-0000000000d1', 'c1000000-0000-4000-8000-0000000000c1',
        'pro', 'synthetic-plan@1', 'addon', now() - interval '1 day', now() + interval '29 days');
SELECT t.ok(true, '附加权益不占“一个有效订阅”的名额');
SELECT t.throws_like($$UPDATE platform.subscription SET household_id = 'd2000000-0000-4000-8000-0000000000d2',
                              student_id = 'c2000000-0000-4000-8000-0000000000c2'
                       WHERE id = '8e000000-0000-4000-8000-0000000000c1'$$,
                     '23514', 'owner and source', '订阅的家庭与学生不能改');
SELECT t.throws_like($$UPDATE platform.subscription SET source = 'paid' WHERE id = '8e000000-0000-4000-8000-0000000000c1'$$,
                     '23514', 'owner and source', '订阅来源不能改（试点虚拟额度不能改记成付费）');
SELECT t.throws($$INSERT INTO platform.subscription (household_id, student_id, tier, config_version, source, period_start, period_end)
                  VALUES ('d1000000-0000-4000-8000-0000000000d1', 'c1000000-0000-4000-8000-0000000000c1', 'pro', 'synthetic-plan@1',
                          'gift', now(), now() + interval '30 days')$$,
                '23514', '订阅来源只能是 trial / pilot_virtual / paid / addon');
SELECT t.throws($$INSERT INTO platform.subscription (household_id, student_id, tier, config_version, source, period_start, period_end)
                  VALUES ('d1000000-0000-4000-8000-0000000000d1', 'c1000000-0000-4000-8000-0000000000c1', 'pro', 'synthetic-plan@1',
                          'addon', now(), now() + interval '40 days')$$,
                '23514', '订阅周期不超过 31 天（滚动 30 天）');
SELECT t.throws_like($$UPDATE platform.subscription SET config_version = 'ch8-hypothesis@1' WHERE id = '8e000000-0000-4000-8000-0000000000c1'$$,
                     '23514', 'published plan_config', '改订阅的配置版本时也只能指向已发布配置');
SELECT t.throws_like($$UPDATE platform.subscription SET period_end = period_end - interval '1 day'
                       WHERE id = '8e000000-0000-4000-8000-0000000000c1'$$,
                     '23514', 'only move forward', '订阅周期只能向后推进');
SELECT t.throws_like($$UPDATE platform.subscription SET next_tier = 'basic', next_config_version = 'ch8-hypothesis@1'
                       WHERE id = '8e000000-0000-4000-8000-0000000000c1'$$,
                     '23514', 'scheduled change', '降级计划也只能指向已发布配置');
SELECT t.throws($$UPDATE platform.subscription SET next_tier = 'basic' WHERE id = '8e000000-0000-4000-8000-0000000000c1'$$,
                '23514', '降级计划的档位与配置版本同有同无');
UPDATE platform.subscription SET next_tier = 'plus', next_config_version = 'synthetic-plan@1'
 WHERE id = '8e000000-0000-4000-8000-0000000000c1';
UPDATE platform.subscription SET status = 'grace' WHERE id = '8e000000-0000-4000-8000-0000000000c1';
UPDATE platform.subscription SET status = 'active' WHERE id = '8e000000-0000-4000-8000-0000000000c1';
SELECT t.ok(true, '可以登记下一周期降级；不改配置版本的状态流转不受配置校验影响');
BEGIN;
UPDATE platform.subscription SET status = 'ended' WHERE id = '8e000000-0000-4000-8000-0000000000c2';
SELECT t.throws_like($$UPDATE platform.subscription SET status = 'active' WHERE id = '8e000000-0000-4000-8000-0000000000c2'$$,
                     '23514', 'has ended', '已结束的订阅是终态，不能重新激活');
ROLLBACK;

-- ================= 四、额度桶 =================
INSERT INTO platform.entitlement (id, subscription_id, student_id, resource_type, period_start, period_end, granted)
SELECT v.id::uuid, s.id, s.student_id, 'mistake_analysis', s.period_start, s.period_end, v.granted
  FROM (VALUES ('8f000000-0000-4000-8000-0000000000c1', '8e000000-0000-4000-8000-0000000000c1', 300),
               ('8f000000-0000-4000-8000-0000000000c2', '8e000000-0000-4000-8000-0000000000c2', 20),
               ('8f000000-0000-4000-8000-0000000000a1', '8e000000-0000-4000-8000-0000000000a1', 0)) AS v(id, sub, granted)
  JOIN platform.subscription s ON s.id = v.sub::uuid;
SELECT t.throws($$INSERT INTO platform.entitlement (subscription_id, student_id, resource_type, period_start, period_end)
                  SELECT id, 'c2000000-0000-4000-8000-0000000000c2', 'mock_exam', period_start, period_end
                    FROM platform.subscription WHERE id = '8e000000-0000-4000-8000-0000000000c1'$$,
                '23503', '额度桶的学生必须与订阅一致');
SELECT t.throws_like($$INSERT INTO platform.entitlement (subscription_id, student_id, resource_type, period_start, period_end)
                       SELECT subscription_id, student_id, resource_type, period_start + interval '1 day', period_end
                         FROM platform.entitlement WHERE id = '8f000000-0000-4000-8000-0000000000c1'$$,
                     '23514', 'cannot overlap', '同一订阅、同一资源的额度桶周期不能重叠');
SELECT t.throws_like($$INSERT INTO platform.entitlement (subscription_id, student_id, resource_type, period_start, period_end)
                       SELECT id, student_id, 'mock_exam', period_start, period_end + interval '1 day'
                         FROM platform.subscription WHERE id = '8e000000-0000-4000-8000-0000000000c1'$$,
                     '23514', 'within the subscription', '额度桶的周期必须落在订阅周期内');
SELECT t.throws_like($$UPDATE platform.entitlement SET resource_type = 'ai_active_time' WHERE id = '8f000000-0000-4000-8000-0000000000c1'$$,
                     '23514', 'identity and period', '额度桶不能换资源类型');
SELECT t.throws_like($$UPDATE platform.entitlement SET period_end = period_end + interval '1 day'
                       WHERE id = '8f000000-0000-4000-8000-0000000000c1'$$,
                     '23514', 'identity and period', '额度桶的周期不能延长');
SELECT t.throws($$DELETE FROM platform.entitlement WHERE id = '8f000000-0000-4000-8000-0000000000a1'$$, '42501', '额度桶不能删除');
SELECT t.throws_like($$UPDATE platform.entitlement SET reserved = -1 WHERE id = '8f000000-0000-4000-8000-0000000000c1'$$,
                     '23514', 'entitlement_reserved_check', '计数不能为负');
SELECT t.throws_like($$UPDATE platform.entitlement SET granted = 300.5 WHERE id = '8f000000-0000-4000-8000-0000000000c1'$$,
                     '23514', 'entitlement_granted_check', '计数必须是整数');
SELECT t.throws_like($$UPDATE platform.entitlement SET settled = 301 WHERE id = '8f000000-0000-4000-8000-0000000000c1'$$,
                     '23514', 'entitlement_not_overdrawn', '已用 + 预留不能超过额度（MAX 公平使用上限到顶即停，没有超额）');

-- ================= 五、预留、结算、释放、过期（模拟 2-E 的函数） =================
-- 发放额度
INSERT INTO platform.usage_ledger (entry_id, occurred_at, entitlement_id, student_id, kind, quantity, actor, reason_code) VALUES
  ('9b000000-0000-4000-8000-000000000001', '2026-10-01 00:00:00+00', '8f000000-0000-4000-8000-0000000000c1',
   'c1000000-0000-4000-8000-0000000000c1', 'grant', 300, 'system', 'period_grant'),
  ('9b000000-0000-4000-8000-000000000002', '2026-10-01 00:00:00+00', '8f000000-0000-4000-8000-0000000000c2',
   'c2000000-0000-4000-8000-0000000000c2', 'grant', 20, 'system', 'period_grant');
SELECT t.ok(NOT EXISTS (SELECT 1 FROM platform.entitlement_reconciliation), '发放后计数与流水一致');

SELECT t.throws_like($$INSERT INTO platform.usage_reservation (usage_id, idempotency_key, request_fingerprint, entitlement_id, student_id,
                         resource_type, reserved_qty, state, settled_qty, finished_at, reserved_at, expires_at)
                       VALUES ('9a000000-0000-4000-8000-0000000000ff', 'synthetic-meter-key-00ff', repeat('1', 64),
                               '8f000000-0000-4000-8000-0000000000c1', 'c1000000-0000-4000-8000-0000000000c1', 'mistake_analysis', 1,
                               'settled', 1, '2026-10-08 11:00:01+00', '2026-10-08 11:00:00+00', '2026-10-08 11:05:00+00')$$,
                     '23514', 'must start in state reserved', '预留不能直接以终态写入');
SELECT t.throws_like($$INSERT INTO platform.usage_reservation (usage_id, idempotency_key, request_fingerprint, entitlement_id, student_id,
                         resource_type, reserved_qty, reserved_at, expires_at)
                       VALUES ('9a000000-0000-4000-8000-0000000000ff', 'synthetic-meter-key-00ff', repeat('1', 64),
                               '8f000000-0000-4000-8000-0000000000c1', 'c1000000-0000-4000-8000-0000000000c1', 'mistake_analysis', 2,
                               '2026-10-08 11:00:00+00', '2026-10-08 11:05:00+00')$$,
                     '23514', 'max_per_reservation', '单次预留不能超过资源的单次上限');
SELECT t.throws_like($$INSERT INTO platform.usage_reservation (usage_id, idempotency_key, request_fingerprint, entitlement_id, student_id,
                         resource_type, reserved_qty, reserved_at, expires_at)
                       VALUES ('9a000000-0000-4000-8000-0000000000ff', 'synthetic-meter-key-00ff', repeat('1', 64),
                               '8f000000-0000-4000-8000-0000000000c1', 'c1000000-0000-4000-8000-0000000000c1', 'mistake_analysis', 1,
                               '2026-10-08 11:00:00+00', '2036-10-08 11:00:00+00')$$,
                     '23514', 'reservation_ttl', '预留的过期时间不能超过资源配置');
SELECT t.throws_like($$INSERT INTO platform.usage_reservation (usage_id, idempotency_key, request_fingerprint, entitlement_id, student_id,
                         resource_type, reserved_qty, reserved_at, expires_at)
                       VALUES ('9a000000-0000-4000-8000-0000000000ff', 'synthetic-meter-key-00ff', repeat('1', 64),
                               '8f000000-0000-4000-8000-0000000000c1', 'c1000000-0000-4000-8000-0000000000c1', 'mistake_analysis', 0.5,
                               '2026-10-08 11:00:00+00', '2026-10-08 11:05:00+00')$$,
                     '23514', 'reserved_qty_check', '预留数量必须是整数');

-- u1：预留 → 结算
INSERT INTO platform.usage_reservation (usage_id, idempotency_key, request_fingerprint, entitlement_id, student_id, resource_type,
  reserved_qty, content_fingerprint, dedup_key, source_method, reserved_at, expires_at) VALUES
  ('9a000000-0000-4000-8000-000000000001', 'synthetic-meter-key-0001', repeat('1', 64), '8f000000-0000-4000-8000-0000000000c1',
   'c1000000-0000-4000-8000-0000000000c1', 'mistake_analysis', 1, 'aya:synthetic-0301-07', repeat('d', 64), 'grade',
   '2026-10-08 11:30:00+00', '2026-10-08 11:40:00+00');
SELECT t.throws_like($$INSERT INTO platform.usage_ledger (entitlement_id, student_id, usage_id, kind, quantity, actor, reason_code, occurred_at)
                       VALUES ('8f000000-0000-4000-8000-0000000000c1', 'c1000000-0000-4000-8000-0000000000c1',
                               '9a000000-0000-4000-8000-000000000001', 'reserve', 0, 'bff', 'reserve', '2026-10-08 11:30:00+00')$$,
                     '23514', 'must equal reserved_qty', 'reserve 流水的数量必须等于预留数量');
INSERT INTO platform.usage_ledger (entry_id, occurred_at, entitlement_id, student_id, usage_id, kind, quantity, actor, reason_code,
  source_method, source_idempotency_key) VALUES
  ('9b000000-0000-4000-8000-000000000011', '2026-10-08 11:30:00+00', '8f000000-0000-4000-8000-0000000000c1',
   'c1000000-0000-4000-8000-0000000000c1', '9a000000-0000-4000-8000-000000000001', 'reserve', 1, 'bff', 'reserve',
   'grade', 'synthetic-meter-key-0001');
UPDATE platform.entitlement SET reserved = reserved + 1 WHERE id = '8f000000-0000-4000-8000-0000000000c1';
SELECT t.ok(NOT EXISTS (SELECT 1 FROM platform.entitlement_reconciliation)
            AND NOT EXISTS (SELECT 1 FROM platform.reservation_ledger_mismatch), '预留后计数、预留表、流水三者一致');
SELECT t.throws_like($$INSERT INTO platform.usage_ledger (entitlement_id, student_id, usage_id, kind, quantity, actor, reason_code, occurred_at)
                       VALUES ('8f000000-0000-4000-8000-0000000000c1', 'c1000000-0000-4000-8000-0000000000c1',
                               '9a000000-0000-4000-8000-000000000001', 'reserve', 1, 'bff', 'reserve', '2026-10-08 11:30:01+00')$$,
                     '23514', 'already has a reserve entry', '一笔预留只能有一条 reserve 流水');
SELECT t.throws_like($$INSERT INTO platform.usage_ledger (entitlement_id, student_id, usage_id, kind, quantity, actor, reason_code, occurred_at)
                       VALUES ('8f000000-0000-4000-8000-0000000000c1', 'c1000000-0000-4000-8000-0000000000c1',
                               '9a000000-0000-4000-8000-000000000001', 'settle', 1, 'bff', 'settle', '2026-10-08 11:30:30+00')$$,
                     '23514', 'does not match reservation state', '预留还没结算时不能写 settle 流水');
SELECT t.throws_like($$UPDATE platform.usage_reservation SET state = 'settled', finished_at = '2026-10-08 11:31:00+00'
                       WHERE usage_id = '9a000000-0000-4000-8000-000000000001'$$,
                     '23514', 'reservation_settled_has_qty', '结算必须写结算数量');
SELECT t.throws_like($$UPDATE platform.usage_reservation SET state = 'settled', settled_qty = 2, finished_at = '2026-10-08 11:31:00+00'
                       WHERE usage_id = '9a000000-0000-4000-8000-000000000001'$$,
                     '23514', 'reservation_settle_within_reserved', '结算数量不能超过预留数量');
SELECT t.throws_like($$UPDATE platform.usage_reservation SET state = 'settled', settled_qty = 1
                       WHERE usage_id = '9a000000-0000-4000-8000-000000000001'$$,
                     '23514', 'reservation_finished_iff_terminal', '终态必须写结束时间');
SELECT t.throws_like($$UPDATE platform.usage_reservation SET state = 'settled', settled_qty = 1, finished_at = '2026-10-08 11:31:00+00',
                              reserved_qty = 0 WHERE usage_id = '9a000000-0000-4000-8000-000000000001'$$,
                     '23514', 'only state', '结算时不能顺手改预留数量');
SELECT t.throws_like($$UPDATE platform.usage_reservation SET expires_at = '2026-10-08 11:45:00+00'
                       WHERE usage_id = '9a000000-0000-4000-8000-000000000001'$$,
                     '23514', 'terminal state', '预留中的记录只能推进到终态');
UPDATE platform.usage_reservation SET state = 'settled', settled_qty = 1, finished_at = '2026-10-08 11:31:00+00'
 WHERE usage_id = '9a000000-0000-4000-8000-000000000001';
SELECT t.throws_like($$INSERT INTO platform.usage_ledger (entitlement_id, student_id, usage_id, kind, quantity, actor, reason_code, occurred_at)
                       VALUES ('8f000000-0000-4000-8000-0000000000c1', 'c1000000-0000-4000-8000-0000000000c1',
                               '9a000000-0000-4000-8000-000000000001', 'settle', 5, 'bff', 'settle', '2026-10-08 11:31:00+00')$$,
                     '23514', 'quantity does not match', 'settle 流水的数量必须等于结算数量（不能多扣）');
SELECT t.throws_like($$INSERT INTO platform.usage_ledger (entitlement_id, student_id, usage_id, kind, quantity, actor, reason_code, occurred_at)
                       VALUES ('8f000000-0000-4000-8000-0000000000c1', 'c1000000-0000-4000-8000-0000000000c1',
                               '9a000000-0000-4000-8000-000000000001', 'release', 1, 'bff', 'release', '2026-10-08 11:31:00+00')$$,
                     '23514', 'does not match reservation state', '已结算的预留不能写 release 流水');
INSERT INTO platform.usage_ledger (entry_id, occurred_at, entitlement_id, student_id, usage_id, kind, quantity, actor, reason_code) VALUES
  ('9b000000-0000-4000-8000-000000000012', '2026-10-08 11:31:00+00', '8f000000-0000-4000-8000-0000000000c1',
   'c1000000-0000-4000-8000-0000000000c1', '9a000000-0000-4000-8000-000000000001', 'settle', 1, 'bff', 'settle');
UPDATE platform.entitlement SET reserved = reserved - 1, settled = settled + 1 WHERE id = '8f000000-0000-4000-8000-0000000000c1';
SELECT t.ok(NOT EXISTS (SELECT 1 FROM platform.entitlement_reconciliation)
            AND NOT EXISTS (SELECT 1 FROM platform.reservation_ledger_mismatch), '结算后三者一致');
SELECT t.throws_like($$UPDATE platform.usage_reservation SET state = 'released', settled_qty = NULL
                       WHERE usage_id = '9a000000-0000-4000-8000-000000000001'$$,
                     '23514', 'already settled', '已结算是终态，不能再释放（成功不能改判为失败）');
SELECT t.throws_like($$UPDATE platform.usage_reservation SET settled_qty = 0 WHERE usage_id = '9a000000-0000-4000-8000-000000000001'$$,
                     '23514', 'already settled', '已结算的数量不能改');
SELECT t.throws($$DELETE FROM platform.usage_reservation WHERE usage_id = '9a000000-0000-4000-8000-000000000001'$$,
                '42501', '预留记录不能删除');

-- 归属与过期时间在推进终态时也不能顺手改（预留中的临时记录，回滚掉）
BEGIN;
INSERT INTO platform.usage_reservation (usage_id, idempotency_key, request_fingerprint, entitlement_id, student_id, resource_type,
  reserved_qty, expires_at)
VALUES ('9a000000-0000-4000-8000-0000000000dd', 'synthetic-meter-key-00dd', repeat('1', 64), '8f000000-0000-4000-8000-0000000000c1',
        'c1000000-0000-4000-8000-0000000000c1', 'mistake_analysis', 1, now() + interval '5 minutes');
SELECT t.throws_like($$UPDATE platform.usage_reservation SET state = 'released', finished_at = now(),
                              entitlement_id = '8f000000-0000-4000-8000-0000000000a1'
                       WHERE usage_id = '9a000000-0000-4000-8000-0000000000dd'$$,
                     '23514', 'only state', '推进终态时不能把预留挪到另一个额度桶');
SELECT t.throws_like($$UPDATE platform.usage_reservation SET state = 'expired', finished_at = now(), expires_at = now() + interval '1 day'
                       WHERE usage_id = '9a000000-0000-4000-8000-0000000000dd'$$,
                     '23514', 'only state', '推进终态时不能改过期时间（不能续期）');
ROLLBACK;

-- 同 key 重试：数据库只允许一条预留（2-E 的函数据此返回首次结果）
SELECT t.throws($$INSERT INTO platform.usage_reservation (usage_id, idempotency_key, request_fingerprint, entitlement_id, student_id,
                    resource_type, reserved_qty, expires_at)
                  VALUES ('9a000000-0000-4000-8000-0000000000ff', 'synthetic-meter-key-0001', repeat('1', 64),
                          '8f000000-0000-4000-8000-0000000000c1', 'c1000000-0000-4000-8000-0000000000c1', 'mistake_analysis', 1,
                          now() + interval '10 minutes')$$,
                '23505', '同一 idempotency_key 只能有一条预留（全局唯一，不看学生与资源）');
SELECT t.throws($$INSERT INTO platform.usage_reservation (usage_id, idempotency_key, request_fingerprint, entitlement_id, student_id,
                    resource_type, reserved_qty, expires_at)
                  VALUES ('9a000000-0000-4000-8000-0000000000ff', 'short-key', repeat('1', 64),
                          '8f000000-0000-4000-8000-0000000000c1', 'c1000000-0000-4000-8000-0000000000c1', 'mistake_analysis', 1,
                          now() + interval '10 minutes')$$,
                '23514', 'idempotency_key 长度 16–128');
SELECT t.throws($$INSERT INTO platform.usage_reservation (usage_id, idempotency_key, request_fingerprint, entitlement_id, student_id,
                    resource_type, reserved_qty, expires_at)
                  VALUES ('9a000000-0000-4000-8000-0000000000ff', 'synthetic-meter-key-00ff', repeat('1', 64),
                          '8f000000-0000-4000-8000-0000000000c1', 'c1000000-0000-4000-8000-0000000000c1', 'mock_exam', 1,
                          now() + interval '10 minutes')$$,
                '23503', '预留的资源类型必须与额度桶一致（不能拿错题额度扣模拟考）');
SELECT t.throws($$INSERT INTO platform.usage_reservation (usage_id, idempotency_key, request_fingerprint, entitlement_id, student_id,
                    resource_type, reserved_qty, expires_at)
                  VALUES ('9a000000-0000-4000-8000-0000000000ff', 'synthetic-meter-key-00ff', repeat('1', 64),
                          '8f000000-0000-4000-8000-0000000000c1', 'c2000000-0000-4000-8000-0000000000c2', 'mistake_analysis', 1,
                          now() + interval '10 minutes')$$,
                '23503', '预留的学生必须与额度桶一致（不能扣别人家的额度）');
SELECT t.throws($$INSERT INTO platform.usage_reservation (usage_id, idempotency_key, request_fingerprint, entitlement_id, student_id,
                    resource_type, reserved_qty, dedup_of, reserved_at, expires_at)
                  VALUES ('9a000000-0000-4000-8000-0000000000ff', 'synthetic-meter-key-00ff', repeat('1', 64),
                          '8f000000-0000-4000-8000-0000000000c1', 'c1000000-0000-4000-8000-0000000000c1', 'mistake_analysis', 0,
                          '9a000000-0000-4000-8000-000000000001', '2026-10-08 20:00:00+00', '2026-10-08 20:05:00+00')$$,
                '23514', '24 小时复看引用首次扣费时必须带去重键');

-- u2：24 小时内复看同一道题，预留 0 并指向首次（设计文档场景 6）
INSERT INTO platform.usage_reservation (usage_id, idempotency_key, request_fingerprint, entitlement_id, student_id, resource_type,
  reserved_qty, content_fingerprint, dedup_key, dedup_of, source_method, reserved_at, expires_at) VALUES
  ('9a000000-0000-4000-8000-000000000002', 'synthetic-meter-key-0002', repeat('2', 64), '8f000000-0000-4000-8000-0000000000c1',
   'c1000000-0000-4000-8000-0000000000c1', 'mistake_analysis', 0, 'aya:synthetic-0301-07', repeat('d', 64),
   '9a000000-0000-4000-8000-000000000001', 'grade', '2026-10-08 20:00:00+00', '2026-10-08 20:10:00+00');
UPDATE platform.usage_reservation SET state = 'settled', settled_qty = 0, finished_at = '2026-10-08 20:00:05+00'
 WHERE usage_id = '9a000000-0000-4000-8000-000000000002';
INSERT INTO platform.usage_ledger (entry_id, occurred_at, entitlement_id, student_id, usage_id, kind, quantity, actor, reason_code) VALUES
  ('9b000000-0000-4000-8000-000000000021', '2026-10-08 20:00:00+00', '8f000000-0000-4000-8000-0000000000c1',
   'c1000000-0000-4000-8000-0000000000c1', '9a000000-0000-4000-8000-000000000002', 'reserve', 0, 'bff', 'dedup_24h'),
  ('9b000000-0000-4000-8000-000000000022', '2026-10-08 20:00:05+00', '8f000000-0000-4000-8000-0000000000c1',
   'c1000000-0000-4000-8000-0000000000c1', '9a000000-0000-4000-8000-000000000002', 'settle', 0, 'bff', 'dedup_24h');
SELECT t.ok((SELECT settled FROM platform.entitlement WHERE id = '8f000000-0000-4000-8000-0000000000c1') = 1
            AND NOT EXISTS (SELECT 1 FROM platform.entitlement_reconciliation)
            AND NOT EXISTS (SELECT 1 FROM platform.reservation_ledger_mismatch),
            '24 小时内复看留痕但不扣次（结算 0）');
SELECT t.throws_like($$INSERT INTO platform.usage_reservation (usage_id, idempotency_key, request_fingerprint, entitlement_id, student_id,
                         resource_type, reserved_qty, dedup_key, dedup_of, reserved_at, expires_at)
                       VALUES ('9a000000-0000-4000-8000-0000000000ff', 'synthetic-meter-key-00ff', repeat('1', 64),
                               '8f000000-0000-4000-8000-0000000000c1', 'c1000000-0000-4000-8000-0000000000c1', 'mistake_analysis', 0,
                               repeat('d', 64), '9a000000-0000-4000-8000-000000000001', '2026-10-09 12:00:00+00', '2026-10-09 12:05:00+00')$$,
                     '23514', 'dedup_of must reference', '超过 24 小时不能再算复看');
SELECT t.throws_like($$INSERT INTO platform.usage_reservation (usage_id, idempotency_key, request_fingerprint, entitlement_id, student_id,
                         resource_type, reserved_qty, dedup_key, dedup_of, reserved_at, expires_at)
                       VALUES ('9a000000-0000-4000-8000-0000000000ff', 'synthetic-meter-key-00ff', repeat('1', 64),
                               '8f000000-0000-4000-8000-0000000000c1', 'c1000000-0000-4000-8000-0000000000c1', 'mistake_analysis', 0,
                               repeat('d', 64), '9a000000-0000-4000-8000-000000000002', '2026-10-08 21:00:00+00', '2026-10-08 21:05:00+00')$$,
                     '23514', 'dedup_of must reference', '复看只能指向真正扣过费的首次（不能指向另一条复看）');
SELECT t.throws_like($$INSERT INTO platform.usage_reservation (usage_id, idempotency_key, request_fingerprint, entitlement_id, student_id,
                         resource_type, reserved_qty, dedup_key, dedup_of, reserved_at, expires_at)
                       VALUES ('9a000000-0000-4000-8000-0000000000ff', 'synthetic-meter-key-00ff', repeat('1', 64),
                               '8f000000-0000-4000-8000-0000000000c1', 'c1000000-0000-4000-8000-0000000000c1', 'mistake_analysis', 0,
                               repeat('e', 64), '9a000000-0000-4000-8000-000000000001', '2026-10-08 21:00:00+00', '2026-10-08 21:05:00+00')$$,
                     '23514', 'dedup_of must reference', '复看的去重键必须与首次相同（不同内容不能免扣）');

-- u3：失败回滚 → 释放；u4：断连后预留过期
INSERT INTO platform.usage_reservation (usage_id, idempotency_key, request_fingerprint, entitlement_id, student_id, resource_type,
  reserved_qty, reserved_at, expires_at) VALUES
  ('9a000000-0000-4000-8000-000000000003', 'synthetic-meter-key-0003', repeat('3', 64), '8f000000-0000-4000-8000-0000000000c1',
   'c1000000-0000-4000-8000-0000000000c1', 'mistake_analysis', 1, '2026-10-09 10:00:00+00', '2026-10-09 10:10:00+00'),
  ('9a000000-0000-4000-8000-000000000004', 'synthetic-meter-key-0004', repeat('4', 64), '8f000000-0000-4000-8000-0000000000c1',
   'c1000000-0000-4000-8000-0000000000c1', 'mistake_analysis', 1, '2026-10-09 11:00:00+00', '2026-10-09 11:10:00+00');
INSERT INTO platform.usage_ledger (entry_id, occurred_at, entitlement_id, student_id, usage_id, kind, quantity, actor, reason_code) VALUES
  ('9b000000-0000-4000-8000-000000000031', '2026-10-09 10:00:00+00', '8f000000-0000-4000-8000-0000000000c1',
   'c1000000-0000-4000-8000-0000000000c1', '9a000000-0000-4000-8000-000000000003', 'reserve', 1, 'bff', 'reserve'),
  ('9b000000-0000-4000-8000-000000000041', '2026-10-09 11:00:00+00', '8f000000-0000-4000-8000-0000000000c1',
   'c1000000-0000-4000-8000-0000000000c1', '9a000000-0000-4000-8000-000000000004', 'reserve', 1, 'bff', 'reserve');
UPDATE platform.entitlement SET reserved = reserved + 2 WHERE id = '8f000000-0000-4000-8000-0000000000c1';
SELECT t.ok(NOT EXISTS (SELECT 1 FROM platform.entitlement_reconciliation), '两笔预留中：计数等于未结束预留之和');
UPDATE platform.usage_reservation SET state = 'released', finished_at = '2026-10-09 10:00:30+00'
 WHERE usage_id = '9a000000-0000-4000-8000-000000000003';
UPDATE platform.usage_reservation SET state = 'expired', finished_at = '2026-10-09 11:10:00+00'
 WHERE usage_id = '9a000000-0000-4000-8000-000000000004';
SELECT t.throws_like($$INSERT INTO platform.usage_ledger (entitlement_id, student_id, usage_id, kind, quantity, actor, reason_code, occurred_at)
                       VALUES ('8f000000-0000-4000-8000-0000000000c1', 'c1000000-0000-4000-8000-0000000000c1',
                               '9a000000-0000-4000-8000-000000000003', 'settle', 0, 'bff', 'settle', '2026-10-09 10:00:30+00')$$,
                     '23514', 'does not match reservation state', '已释放的预留不能写 settle 流水（失败不扣费）');
SELECT t.throws_like($$INSERT INTO platform.usage_ledger (entitlement_id, student_id, usage_id, kind, quantity, actor, reason_code, occurred_at)
                       VALUES ('8f000000-0000-4000-8000-0000000000c1', 'c1000000-0000-4000-8000-0000000000c1',
                               '9a000000-0000-4000-8000-000000000004', 'expire', 1, 'bff', 'ttl_elapsed', '2026-10-09 11:10:00+00')$$,
                     '23514', 'ledger_actor_by_kind', '过期流水只能由定时任务写');
INSERT INTO platform.usage_ledger (entry_id, occurred_at, entitlement_id, student_id, usage_id, kind, quantity, actor, reason_code) VALUES
  ('9b000000-0000-4000-8000-000000000032', '2026-10-09 10:00:30+00', '8f000000-0000-4000-8000-0000000000c1',
   'c1000000-0000-4000-8000-0000000000c1', '9a000000-0000-4000-8000-000000000003', 'release', 1, 'bff', 'upstream_failed'),
  ('9b000000-0000-4000-8000-000000000042', '2026-10-09 11:10:00+00', '8f000000-0000-4000-8000-0000000000c1',
   'c1000000-0000-4000-8000-0000000000c1', '9a000000-0000-4000-8000-000000000004', 'expire', 1, 'system', 'ttl_elapsed');
UPDATE platform.entitlement SET reserved = reserved - 2 WHERE id = '8f000000-0000-4000-8000-0000000000c1';
SELECT t.ok((SELECT reserved = 0 AND settled = 1 FROM platform.entitlement WHERE id = '8f000000-0000-4000-8000-0000000000c1')
            AND NOT EXISTS (SELECT 1 FROM platform.entitlement_reconciliation)
            AND NOT EXISTS (SELECT 1 FROM platform.reservation_ledger_mismatch),
            '失败释放与过期都退回预留，不计已用');
SELECT t.throws_like($$INSERT INTO platform.usage_ledger (entitlement_id, student_id, usage_id, kind, quantity, actor, reason_code, occurred_at)
                       VALUES ('8f000000-0000-4000-8000-0000000000c1', 'c1000000-0000-4000-8000-0000000000c1',
                               '9a000000-0000-4000-8000-000000000003', 'release', 1, 'bff', 'upstream_failed', '2026-10-11 00:00:00+00')$$,
                     '23514', 'already has a terminal entry', '一笔预留只能有一条终态流水（不能重复退回）');
SELECT t.throws_like($$UPDATE platform.usage_reservation SET state = 'settled', settled_qty = 1, finished_at = now()
                       WHERE usage_id = '9a000000-0000-4000-8000-000000000004'$$,
                     '23514', 'already expired', '已过期的预留不能再结算（迟到的成功不扣费，见设计文档场景 5）');
SELECT t.throws_like($$UPDATE platform.usage_reservation SET state = 'expired'
                       WHERE usage_id = '9a000000-0000-4000-8000-000000000003'$$,
                     '23514', 'already released', '已释放的预留不能再改成过期');
SELECT t.throws_like($$INSERT INTO platform.usage_reservation (usage_id, idempotency_key, request_fingerprint, entitlement_id, student_id,
                         resource_type, reserved_qty, dedup_key, dedup_of, reserved_at, expires_at)
                       VALUES ('9a000000-0000-4000-8000-0000000000ff', 'synthetic-meter-key-00ff', repeat('1', 64),
                               '8f000000-0000-4000-8000-0000000000c1', 'c1000000-0000-4000-8000-0000000000c1', 'mistake_analysis', 0,
                               repeat('d', 64), '9a000000-0000-4000-8000-000000000003', '2026-10-09 12:00:00+00', '2026-10-09 12:05:00+00')$$,
                     '23514', 'dedup_of must reference', '复看不能指向释放掉的（没扣费的）记录');

-- u5：另一户学生 S2 的一次扣费（给 RLS 测试用，也用来测“不能跨学生去重”）
INSERT INTO platform.usage_reservation (usage_id, idempotency_key, request_fingerprint, entitlement_id, student_id, resource_type,
  reserved_qty, content_fingerprint, dedup_key, source_method, reserved_at, expires_at) VALUES
  ('9a000000-0000-4000-8000-000000000005', 'synthetic-meter-key-0005', repeat('5', 64), '8f000000-0000-4000-8000-0000000000c2',
   'c2000000-0000-4000-8000-0000000000c2', 'mistake_analysis', 1, 'aya:synthetic-0301-07', repeat('f', 64), 'grade',
   '2026-10-10 09:00:00+00', '2026-10-10 09:10:00+00');
UPDATE platform.usage_reservation SET state = 'settled', settled_qty = 1, finished_at = '2026-10-10 09:00:10+00'
 WHERE usage_id = '9a000000-0000-4000-8000-000000000005';
INSERT INTO platform.usage_ledger (entry_id, occurred_at, entitlement_id, student_id, usage_id, kind, quantity, actor, reason_code) VALUES
  ('9b000000-0000-4000-8000-000000000051', '2026-10-10 09:00:00+00', '8f000000-0000-4000-8000-0000000000c2',
   'c2000000-0000-4000-8000-0000000000c2', '9a000000-0000-4000-8000-000000000005', 'reserve', 1, 'bff', 'reserve'),
  ('9b000000-0000-4000-8000-000000000052', '2026-10-10 09:00:10+00', '8f000000-0000-4000-8000-0000000000c2',
   'c2000000-0000-4000-8000-0000000000c2', '9a000000-0000-4000-8000-000000000005', 'settle', 1, 'bff', 'settle');
UPDATE platform.entitlement SET settled = settled + 1 WHERE id = '8f000000-0000-4000-8000-0000000000c2';
SELECT t.ok(NOT EXISTS (SELECT 1 FROM platform.entitlement_reconciliation)
            AND NOT EXISTS (SELECT 1 FROM platform.reservation_ledger_mismatch), 'S2 扣费一次，对账一致');
SELECT t.throws($$INSERT INTO platform.usage_reservation (usage_id, idempotency_key, request_fingerprint, entitlement_id, student_id,
                    resource_type, reserved_qty, dedup_key, dedup_of, reserved_at, expires_at)
                  VALUES ('9a000000-0000-4000-8000-0000000000ff', 'synthetic-meter-key-00ff', repeat('1', 64),
                          '8f000000-0000-4000-8000-0000000000c1', 'c1000000-0000-4000-8000-0000000000c1', 'mistake_analysis', 0,
                          repeat('f', 64), '9a000000-0000-4000-8000-000000000005', '2026-10-10 10:00:00+00', '2026-10-10 10:05:00+00')$$,
                '23503', '复看只能引用同一学生的首次（不能借别家的扣费免扣，也不暴露别人的使用记录）');

-- ================= 六、流水：只追加 =================
SELECT t.throws($$UPDATE platform.usage_ledger SET quantity = 0 WHERE entry_id = '9b000000-0000-4000-8000-000000000012'$$,
                '42501', '流水不能修改');
SELECT t.throws($$DELETE FROM platform.usage_ledger WHERE entry_id = '9b000000-0000-4000-8000-000000000012'$$,
                '42501', '流水不能删除');
SELECT t.throws($$UPDATE platform.usage_ledger_202610 SET quantity = 0 WHERE entry_id = '9b000000-0000-4000-8000-000000000012'$$,
                '42501', '直接改分区也不行');
SELECT t.throws_like($$TRUNCATE platform.usage_ledger$$, '42501', 'cannot be truncated', '流水不能 TRUNCATE（表属主也不行）');
SELECT t.throws($$TRUNCATE platform.usage_ledger_202610$$, '0A000', '流水分区不能单独 TRUNCATE（被自引用外键引用）');
SELECT t.throws_like($$TRUNCATE platform.usage_reservation CASCADE$$, '42501', 'cannot be truncated', '预留表不能 TRUNCATE');
SELECT t.throws_like($$INSERT INTO platform.usage_ledger (entitlement_id, student_id, usage_id, kind, quantity, actor, reason_code, occurred_at)
                       VALUES ('8f000000-0000-4000-8000-0000000000c1', 'c1000000-0000-4000-8000-0000000000c1',
                               NULL, 'grant', -1, 'system', 'period_grant', '2026-10-10 00:00:00+00')$$,
                     '23514', 'ledger_nonnegative', '发放、结算等流水不能为负（扣回、退还走更正或调整）');
SELECT t.throws_like($$INSERT INTO platform.usage_ledger (entitlement_id, student_id, kind, quantity, actor, reason_code, occurred_at)
                       VALUES ('8f000000-0000-4000-8000-0000000000c1', 'c1000000-0000-4000-8000-0000000000c1',
                               'reserve', 1, 'bff', 'reserve', '2026-10-10 00:00:00+00')$$,
                     '23514', 'ledger_usage_required', '预留、结算、释放、过期流水必须带 usage_id');
SELECT t.throws_like($$INSERT INTO platform.usage_ledger (entitlement_id, student_id, usage_id, kind, quantity, actor, reason_code, occurred_at)
                       VALUES ('8f000000-0000-4000-8000-0000000000c1', 'c1000000-0000-4000-8000-0000000000c1',
                               '9a000000-0000-4000-8000-000000000001', 'grant', 1, 'system', 'period_grant', '2026-10-10 00:00:00+00')$$,
                     '23514', 'ledger_usage_required', '发放流水不能挂在某次使用上');
SELECT t.throws_like($$INSERT INTO platform.usage_ledger (entitlement_id, student_id, kind, quantity, actor, reason_code, occurred_at)
                       VALUES ('8f000000-0000-4000-8000-0000000000c1', 'c1000000-0000-4000-8000-0000000000c1',
                               'grant', 1000000, 'bff', 'period_grant', '2026-10-10 00:00:00+00')$$,
                     '23514', 'ledger_actor_by_kind', '发放只能由系统按配置写，不能由用户请求写');
SELECT t.throws_like($$INSERT INTO platform.usage_ledger (entitlement_id, student_id, kind, quantity, actor, reason_code, occurred_at)
                       VALUES ('8f000000-0000-4000-8000-0000000000c1', 'c1000000-0000-4000-8000-0000000000c1',
                               'grant', 0.5, 'system', 'period_grant', '2026-10-10 00:00:00+00')$$,
                     '23514', 'usage_ledger_quantity_check', '流水数量必须是整数');
SELECT t.throws_like($$INSERT INTO platform.usage_ledger (entitlement_id, student_id, kind, quantity, actor, reason_code, occurred_at,
                         source_method)
                       VALUES ('8f000000-0000-4000-8000-0000000000c1', 'c1000000-0000-4000-8000-0000000000c1',
                               'grant', 1, 'system', 'period_grant', '2026-10-10 00:00:00+00', 'grade')$$,
                     '23514', 'ledger_source_pair', '业务来源的方法与 key 同有同无');
SELECT t.throws($$INSERT INTO platform.usage_ledger (entitlement_id, student_id, usage_id, kind, quantity, actor, reason_code, occurred_at)
                  VALUES ('8f000000-0000-4000-8000-0000000000c2', 'c2000000-0000-4000-8000-0000000000c2',
                          '9a000000-0000-4000-8000-000000000001', 'settle', 1, 'bff', 'settle', '2026-10-10 00:00:00+00')$$,
                '23503', '流水的 usage_id 必须属于同一额度桶');
SELECT t.throws($$INSERT INTO platform.usage_ledger (entitlement_id, student_id, kind, quantity, actor, reason_code, occurred_at)
                  VALUES ('8f000000-0000-4000-8000-0000000000c1', 'c2000000-0000-4000-8000-0000000000c2',
                          'grant', 1, 'system', 'period_grant', '2026-10-10 00:00:00+00')$$,
                '23503', '流水的学生必须与额度桶一致');
SELECT t.throws_like($$INSERT INTO platform.usage_ledger (entitlement_id, student_id, kind, quantity, actor, reason_code, occurred_at)
                       VALUES ('8f000000-0000-4000-8000-0000000000c1', 'c1000000-0000-4000-8000-0000000000c1',
                               'grant', 1, 'system', 'period_grant', '2031-01-01 00:00:00+00')$$,
                     '23514', 'no partition', '分区范围外的流水直接失败（没有 DEFAULT 分区，便于发现分区没建好）');
SELECT t.ok(to_regclass('platform.usage_ledger_202609') IS NOT NULL AND to_regclass('platform.usage_ledger_202712') IS NOT NULL
            AND NOT EXISTS (SELECT 1 FROM pg_inherits i JOIN pg_class c ON c.oid = i.inhrelid
                             WHERE i.inhparent = 'platform.usage_ledger'::regclass AND pg_get_expr(c.relpartbound, c.oid) = 'DEFAULT')
            AND (SELECT count(*) FROM pg_inherits WHERE inhparent = 'platform.usage_ledger'::regclass) = 16,
            '流水按月分区：2026-09 起 16 个月，没有 DEFAULT 分区');

-- 更正：必须指向真实存在、种类属实、同一额度桶的流水；方向相反；累计不越过原数量
SELECT t.throws_like($$INSERT INTO platform.usage_ledger (entitlement_id, student_id, kind, quantity, actor, reason_code, occurred_at)
                       VALUES ('8f000000-0000-4000-8000-0000000000c1', 'c1000000-0000-4000-8000-0000000000c1',
                               'correction', -1, 'staff', 'refund', '2026-10-10 00:00:00+00')$$,
                     '23514', 'ledger_correction_target', '更正必须指出更正的是哪一条');
SELECT t.throws($$INSERT INTO platform.usage_ledger (entitlement_id, student_id, kind, quantity, actor, reason_code, occurred_at,
                    corrects_entry_id, corrects_occurred_at, corrects_kind)
                  VALUES ('8f000000-0000-4000-8000-0000000000c1', 'c1000000-0000-4000-8000-0000000000c1',
                          'correction', -1, 'staff', 'refund', '2026-10-10 00:00:00+00',
                          '9b000000-0000-4000-8000-0000000000ee', '2026-10-08 11:31:00+00', 'settle')$$,
                '23503', '更正不能指向不存在的流水');
SELECT t.throws($$INSERT INTO platform.usage_ledger (entitlement_id, student_id, kind, quantity, actor, reason_code, occurred_at,
                    corrects_entry_id, corrects_occurred_at, corrects_kind)
                  VALUES ('8f000000-0000-4000-8000-0000000000c1', 'c1000000-0000-4000-8000-0000000000c1',
                          'correction', -1, 'staff', 'refund', '2026-10-10 00:00:00+00',
                          '9b000000-0000-4000-8000-000000000012', '2026-10-08 11:31:00+00', 'grant')$$,
                '23503', '更正声明的种类必须与原流水一致');
SELECT t.throws($$INSERT INTO platform.usage_ledger (entitlement_id, student_id, kind, quantity, actor, reason_code, occurred_at,
                    corrects_entry_id, corrects_occurred_at, corrects_kind)
                  VALUES ('8f000000-0000-4000-8000-0000000000c1', 'c1000000-0000-4000-8000-0000000000c1',
                          'correction', 50, 'staff', 'refund', '2026-10-10 00:00:00+00',
                          '9b000000-0000-4000-8000-000000000002', '2026-10-01 00:00:00+00', 'grant')$$,
                '23503', '更正不能跨额度桶（不能拿另一户的流水给自己加额度）');
SELECT t.throws_like($$INSERT INTO platform.usage_ledger (entitlement_id, student_id, kind, quantity, actor, reason_code, occurred_at,
                         corrects_entry_id, corrects_occurred_at, corrects_kind)
                       VALUES ('8f000000-0000-4000-8000-0000000000c1', 'c1000000-0000-4000-8000-0000000000c1',
                               'correction', 1, 'staff', 'refund', '2026-10-10 00:00:00+00',
                               '9b000000-0000-4000-8000-000000000012', '2026-10-08 11:31:00+00', 'settle')$$,
                     '23514', 'opposite direction', '更正必须与原流水反向（不能借更正多扣）');
SELECT t.throws($$INSERT INTO platform.usage_ledger (entitlement_id, student_id, kind, quantity, actor, reason_code, occurred_at,
                    corrects_entry_id, corrects_occurred_at, corrects_kind)
                  VALUES ('8f000000-0000-4000-8000-0000000000c1', 'c1000000-0000-4000-8000-0000000000c1',
                          'correction', -1, 'staff', 'refund', '2026-10-10 00:00:00+00',
                          '9b000000-0000-4000-8000-000000000012', '2026-10-08 11:31:00+00', 'correction')$$,
                '23514', '不能更正一条更正');
-- 退还 u1 的一次（追加反向流水，同时调整计数）
INSERT INTO platform.usage_ledger (entry_id, occurred_at, entitlement_id, student_id, usage_id, kind, quantity, actor, reason_code,
  corrects_entry_id, corrects_occurred_at, corrects_kind) VALUES
  ('9b000000-0000-4000-8000-000000000013', '2026-10-10 09:00:00+00', '8f000000-0000-4000-8000-0000000000c1',
   'c1000000-0000-4000-8000-0000000000c1', '9a000000-0000-4000-8000-000000000001', 'correction', -1, 'staff', 'appeal_upheld',
   '9b000000-0000-4000-8000-000000000012', '2026-10-08 11:31:00+00', 'settle');
UPDATE platform.entitlement SET settled = settled - 1 WHERE id = '8f000000-0000-4000-8000-0000000000c1';
SELECT t.ok(NOT EXISTS (SELECT 1 FROM platform.entitlement_reconciliation)
            AND NOT EXISTS (SELECT 1 FROM platform.reservation_ledger_mismatch),
            '更正追加反向流水后，对账仍为零行');
SELECT t.throws_like($$INSERT INTO platform.usage_ledger (entitlement_id, student_id, kind, quantity, actor, reason_code, occurred_at,
                         corrects_entry_id, corrects_occurred_at, corrects_kind)
                       VALUES ('8f000000-0000-4000-8000-0000000000c1', 'c1000000-0000-4000-8000-0000000000c1',
                               'correction', -1, 'staff', 'refund', '2026-10-10 10:00:00+00',
                               '9b000000-0000-4000-8000-000000000012', '2026-10-08 11:31:00+00', 'settle')$$,
                     '23514', 'cannot exceed', '同一条流水不能重复退还');

-- ================= 七、对账视图能发现不一致 =================
BEGIN;
UPDATE platform.entitlement SET granted = 299 WHERE id = '8f000000-0000-4000-8000-0000000000c1';
SELECT t.ok((SELECT count(*) FROM platform.entitlement_reconciliation) = 1, '计数被改（发放）→ 对账视图报出');
ROLLBACK;
BEGIN;
UPDATE platform.entitlement SET settled = 1 WHERE id = '8f000000-0000-4000-8000-0000000000c1';
SELECT t.ok((SELECT count(*) FROM platform.entitlement_reconciliation) = 1, '计数被改（已用）→ 对账视图报出');
ROLLBACK;
BEGIN;
INSERT INTO platform.usage_reservation (usage_id, idempotency_key, request_fingerprint, entitlement_id, student_id, resource_type,
  reserved_qty, expires_at)
VALUES ('9a000000-0000-4000-8000-0000000000ee', 'synthetic-meter-key-00ee', repeat('e', 64), '8f000000-0000-4000-8000-0000000000c1',
        'c1000000-0000-4000-8000-0000000000c1', 'mistake_analysis', 1, now() + interval '10 minutes');
SELECT t.ok((SELECT count(*) FROM platform.entitlement_reconciliation) = 1
            AND (SELECT count(*) FROM platform.reservation_ledger_mismatch) = 1,
            '预留了却没记流水、没加计数 → 两个对账视图都报出');
ROLLBACK;
-- 流水写入保护被绕过时（例如 2-E 的函数先写流水、后改预留状态），对账视图仍能按种类和数量发现
BEGIN;
ALTER TABLE platform.usage_ledger DISABLE TRIGGER guard_ledger_insert;
INSERT INTO platform.usage_ledger (entitlement_id, student_id, usage_id, kind, quantity, actor, reason_code, occurred_at)
VALUES ('8f000000-0000-4000-8000-0000000000c1', 'c1000000-0000-4000-8000-0000000000c1', '9a000000-0000-4000-8000-000000000003',
        'settle', 1, 'bff', 'settle', '2026-10-11 00:00:00+00');
SELECT t.ok((SELECT count(*) FROM platform.reservation_ledger_mismatch) = 1, '已释放的预留却有 settle 流水 → 对账视图报出');
ROLLBACK;
BEGIN;
ALTER TABLE platform.usage_ledger DISABLE TRIGGER guard_ledger_insert;
INSERT INTO platform.usage_reservation (usage_id, idempotency_key, request_fingerprint, entitlement_id, student_id, resource_type,
  reserved_qty, reserved_at, expires_at)
VALUES ('9a000000-0000-4000-8000-0000000000ec', 'synthetic-meter-key-00ec', repeat('e', 64), '8f000000-0000-4000-8000-0000000000c1',
        'c1000000-0000-4000-8000-0000000000c1', 'mistake_analysis', 1, '2026-10-12 00:00:00+00', '2026-10-12 00:05:00+00');
UPDATE platform.usage_reservation SET state = 'settled', settled_qty = 1, finished_at = '2026-10-12 00:00:10+00'
 WHERE usage_id = '9a000000-0000-4000-8000-0000000000ec';
INSERT INTO platform.usage_ledger (entitlement_id, student_id, usage_id, kind, quantity, actor, reason_code, occurred_at) VALUES
  ('8f000000-0000-4000-8000-0000000000c1', 'c1000000-0000-4000-8000-0000000000c1', '9a000000-0000-4000-8000-0000000000ec',
   'reserve', 7, 'bff', 'reserve', '2026-10-12 00:00:00+00'),
  ('8f000000-0000-4000-8000-0000000000c1', 'c1000000-0000-4000-8000-0000000000c1', '9a000000-0000-4000-8000-0000000000ec',
   'settle', 5, 'bff', 'settle', '2026-10-12 00:00:10+00');
UPDATE platform.entitlement SET settled = settled + 5 WHERE id = '8f000000-0000-4000-8000-0000000000c1';
SELECT t.ok((SELECT count(*) FROM platform.entitlement_reconciliation) = 0
            AND (SELECT count(*) FROM platform.reservation_ledger_mismatch) = 1,
            '流水数量与预留不一致（结算 1 却记 5）→ 预留对账视图报出');
ROLLBACK;

-- ================= 八、RLS 与权限（规则 5：RLS 变更附测试） =================
SET ROLE starbuddy_bff;
SELECT t.as_account('a0000000-0000-4000-8000-00000000000a');  -- 家长 gA：只看 S1
SELECT t.ok((SELECT count(*) FROM platform.subscription WHERE student_id = 'c2000000-0000-4000-8000-0000000000c2') = 0
            AND (SELECT count(*) FROM platform.subscription WHERE student_id = 'c1000000-0000-4000-8000-0000000000c1') = 2,
            '家长只能看到本人已授权学生的订阅');
SELECT t.ok((SELECT count(*) FROM platform.entitlement) = 2
            AND (SELECT count(*) FROM platform.usage_reservation) = 4
            AND (SELECT count(*) FROM platform.usage_reservation WHERE usage_id = '9a000000-0000-4000-8000-000000000005') = 0
            AND (SELECT count(*) FROM platform.usage_ledger WHERE student_id <> 'c1000000-0000-4000-8000-0000000000c1') = 0
            AND (SELECT count(*) FROM platform.usage_ledger) > 0,
            '家长只能看到本人已授权学生的额度桶、预留与流水（看不到另一户的扣费）');
SELECT t.ok((SELECT count(*) FROM platform.entitlement_balance) = 2
            AND (SELECT remaining FROM platform.entitlement_balance WHERE source = 'pilot_virtual') = 300,
            '余额视图随调用者的 RLS：家长看到 S1 的剩余 300 次（附加权益另列一行）');
SELECT t.ok(NOT EXISTS (SELECT 1 FROM platform.plan_config WHERE status = 'draft')
            AND NOT EXISTS (SELECT 1 FROM platform.plan_allowance WHERE config_version = 'ch8-hypothesis@1')
            AND EXISTS (SELECT 1 FROM platform.plan_allowance WHERE config_version = 'synthetic-plan@1'),
            'BFF 看得到已发布的额度配置，看不到草稿');
SELECT t.ok((SELECT count(*) FROM (SELECT usage_id, state, reserved_qty FROM platform.usage_reservation) x) = 4,
            'BFF 可以读预留的展示列');
SELECT t.throws($$SELECT idempotency_key FROM platform.usage_reservation$$, '42501', 'BFF 读不到预留的幂等键');
SELECT t.throws($$SELECT content_fingerprint, dedup_key FROM platform.usage_reservation$$, '42501', 'BFF 读不到内容指纹与去重键');
SELECT t.throws($$SELECT * FROM platform.entitlement_reconciliation$$, '42501', '对账视图只给运维，不授权给 BFF');
SELECT t.throws($$SELECT * FROM platform.reservation_ledger_mismatch$$, '42501', '预留对账视图只给运维，不授权给 BFF');
SELECT t.throws($$INSERT INTO platform.subscription (household_id, student_id, tier, config_version, source, period_start, period_end)
                  VALUES ('d1000000-0000-4000-8000-0000000000d1', 'c1000000-0000-4000-8000-0000000000c1', 'max', 'synthetic-plan@1',
                          'addon', now(), now() + interval '30 days')$$, '42501', 'BFF 不能直接开订阅');
SELECT t.throws($$UPDATE platform.subscription SET tier = 'max'$$, '42501', 'BFF 不能直接改订阅档位');
SELECT t.throws($$UPDATE platform.entitlement SET granted = 9999$$, '42501', 'BFF 不能直接改额度');
SELECT t.throws($$INSERT INTO platform.usage_reservation (usage_id, idempotency_key, request_fingerprint, entitlement_id, student_id,
                    resource_type, reserved_qty, expires_at)
                  VALUES ('9a000000-0000-4000-8000-0000000000fe', 'synthetic-meter-key-00fe', repeat('1', 64),
                          '8f000000-0000-4000-8000-0000000000c1', 'c1000000-0000-4000-8000-0000000000c1', 'mistake_analysis', 1,
                          now() + interval '10 minutes')$$, '42501', 'BFF 不能绕过函数直接预留');
SELECT t.throws($$UPDATE platform.usage_reservation SET state = 'released', finished_at = now()$$, '42501', 'BFF 不能直接改预留状态');
SELECT t.throws($$INSERT INTO platform.usage_ledger (entitlement_id, student_id, kind, quantity, actor, reason_code, occurred_at)
                  VALUES ('8f000000-0000-4000-8000-0000000000c1', 'c1000000-0000-4000-8000-0000000000c1',
                          'grant', 100, 'system', 'period_grant', '2026-10-10 00:00:00+00')$$, '42501', 'BFF 不能直接写流水');
SELECT t.throws($$UPDATE platform.plan_allowance SET quantity = 9999$$, '42501', 'BFF 不能改额度配置');
SELECT t.throws($$INSERT INTO platform.plan_config (version, note_code) VALUES ('evil-plan@1', 'x')$$, '42501', 'BFF 不能新建额度配置');
SELECT t.throws($$SELECT * FROM platform.usage_ledger_202610$$, '42501', 'BFF 不能绕过父表直接读流水分区');

SELECT t.as_account('b0000000-0000-4000-8000-00000000000b');  -- 家长 gB：只看 S2
SELECT t.ok((SELECT count(*) FROM platform.entitlement_balance) = 1
            AND (SELECT student_id FROM platform.entitlement_balance) = 'c2000000-0000-4000-8000-0000000000c2'
            AND (SELECT count(*) FROM platform.usage_reservation) = 1
            AND (SELECT count(*) FROM platform.usage_ledger WHERE student_id = 'c1000000-0000-4000-8000-0000000000c1') = 0,
            '另一户家长只看到自己学生的余额与预留，看不到 S1 的流水');
SELECT t.as_account('a1000000-0000-4000-8000-0000000000a1');  -- 学生 S1 本人
SELECT t.ok((SELECT count(*) FROM platform.entitlement_balance) = 2
            AND (SELECT count(*) FROM platform.usage_reservation) = 4
            AND (SELECT count(*) FROM platform.subscription WHERE student_id <> 'c1000000-0000-4000-8000-0000000000c1') = 0,
            '学生看到自己的额度与使用记录，看不到别人的');
SELECT t.as_account('b1000000-0000-4000-8000-0000000000b1');  -- S2 与 gA 同手机号：不能借此看到 S1
SELECT t.ok((SELECT count(*) FROM platform.usage_ledger WHERE student_id = 'c1000000-0000-4000-8000-0000000000c1') = 0
            AND (SELECT count(*) FROM platform.usage_reservation) = 1,
            '同手机号不能跨档案看计量数据');
SELECT t.as_account(NULL);
SELECT t.ok((SELECT count(*) FROM platform.subscription) + (SELECT count(*) FROM platform.entitlement)
            + (SELECT count(*) FROM platform.usage_reservation) + (SELECT count(*) FROM platform.usage_ledger) = 0,
            '未登录的 BFF 请求看不到任何计量数据');
RESET ROLE;

-- 余额视图只算有效订阅的当前周期
BEGIN;
UPDATE platform.subscription SET status = 'ended' WHERE id = '8e000000-0000-4000-8000-0000000000c2';
SET LOCAL ROLE starbuddy_bff;
SELECT t.as_account('b0000000-0000-4000-8000-00000000000b');
SELECT t.ok((SELECT count(*) FROM platform.entitlement_balance) = 0 AND (SELECT count(*) FROM platform.entitlement) = 1,
            '订阅结束后余额视图不再显示（历史额度桶仍可查）');
ROLLBACK;
SELECT t.as_account(NULL);

SET ROLE starbuddy_learning;
SELECT t.throws($$SELECT * FROM platform.entitlement$$, '42501', '学习服务读不到额度桶（计量在 BFF 侧）');
SELECT t.throws($$SELECT * FROM platform.usage_ledger$$, '42501', '学习服务读不到流水');
SELECT t.throws($$SELECT usage_id FROM platform.usage_reservation$$, '42501', '学习服务读不到预留');
SELECT t.throws($$SELECT * FROM platform.subscription$$, '42501', '学习服务读不到订阅');
SELECT t.throws($$SELECT * FROM platform.plan_config$$, '42501', '学习服务读不到额度配置');
RESET ROLE;

SET ROLE starbuddy_review;
SELECT t.throws($$SELECT * FROM platform.entitlement$$, '42501', '复核角色读不到额度桶');
SELECT t.throws($$SELECT * FROM platform.usage_ledger$$, '42501', '复核角色读不到流水');
SELECT t.throws($$SELECT * FROM platform.subscription$$, '42501', '复核角色读不到订阅');
RESET ROLE;

-- 授权函数属主（2-E 的写入函数以它的身份执行）的精确列级授权在 70_metering_functions.sql 里逐项断言；
-- 这里只断言它改不了额度配置，也删不了、清不空任何计量表
SET ROLE starbuddy_authz;
SELECT t.throws($$UPDATE platform.plan_config SET note_code = 'x'$$, '42501', '授权函数属主不能改额度配置');
SELECT t.throws($$DELETE FROM platform.usage_ledger$$, '42501', '授权函数属主不能删流水');
RESET ROLE;

SELECT t.ok(NOT EXISTS (
  SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
   WHERE n.nspname = 'platform' AND c.relkind IN ('r', 'p')
     AND c.relname IN ('resource_type', 'plan_config', 'plan_allowance', 'plan_feature', 'subscription', 'entitlement',
                       'usage_reservation', 'usage_ledger')
     AND NOT (c.relrowsecurity AND c.relforcerowsecurity)),
  '八张计量表全部 ENABLE + FORCE RLS');
-- 用 has_*_privilege 按角色逐一检查（含经由角色继承、列级授权），不依赖 information_schema 的授权人可见性
SELECT t.ok(NOT EXISTS (
  SELECT 1
    FROM unnest(ARRAY['starbuddy_bff', 'starbuddy_learning', 'starbuddy_review', 'starbuddy_scheduler']) AS r(role)
   CROSS JOIN (SELECT c.oid FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE n.nspname = 'platform'
                  AND (c.relname IN ('resource_type', 'plan_config', 'plan_allowance', 'plan_feature', 'subscription', 'entitlement',
                                     'usage_reservation', 'usage_ledger', 'entitlement_balance', 'entitlement_reconciliation',
                                     'reservation_ledger_mismatch')
                       OR c.oid IN (SELECT inhrelid FROM pg_inherits WHERE inhparent = 'platform.usage_ledger'::regclass))) t
   WHERE has_any_column_privilege(r.role, t.oid, 'INSERT') OR has_any_column_privilege(r.role, t.oid, 'UPDATE')
      OR has_table_privilege(r.role, t.oid, 'DELETE') OR has_table_privilege(r.role, t.oid, 'TRUNCATE')),
  'BFF、学习服务、复核、定时任务角色对计量表都没有直接写权限（写入只经函数）');
SELECT t.ok(NOT EXISTS (
  SELECT 1
    FROM unnest(ARRAY['starbuddy_bff', 'starbuddy_learning', 'starbuddy_review']) AS r(role)
   CROSS JOIN (SELECT inhrelid AS oid FROM pg_inherits WHERE inhparent = 'platform.usage_ledger'::regclass) p
   WHERE has_any_column_privilege(r.role, p.oid, 'SELECT')),
  '流水分区没有授权给任何应用角色');
SELECT t.ok(NOT EXISTS (
  SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
   WHERE n.nspname = 'platform'
     AND p.proname IN ('guard_plan_config', 'guard_plan_rows', 'guard_subscription', 'guard_entitlement', 'guard_usage_reservation',
                       'reject_ledger_change', 'guard_ledger_insert', 'reject_metering_truncate', 'plan_config_lock_key')
     AND (p.prosecdef OR has_function_privilege('public', p.oid, 'EXECUTE'))),
  '计量的保护函数都不是 SECURITY DEFINER，也不对 PUBLIC 开放执行');
