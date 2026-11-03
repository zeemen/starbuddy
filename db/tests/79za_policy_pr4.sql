-- 考情 v0.9 PR3 遗留的两条已知风险（0051 platform、0052 policy，specs/policy-decisions-v0.9.1/spec.md“PR4”）：
--   一、platform.enroll_subject 在库里核对当前年级（届别 + 计量时钟）在 7–9，否则 55000 GRADE_OUT_OF_RANGE；未授权先 42501；
--       暂停不受影响（0051；与届别更正的并发在 db/tests/run.sh 场景 17）；
--   二、来源清单取锁函数把持锁事务的空闲上限设为 60 秒（0052；新增、下线的 5 秒锁等待上限要两个会话，在 run.sh 场景 16）。
-- 时钟：只用计量时钟 t.clock（70 号把 meter_now() 换成读它），固定到合成时刻，结束时恢复；不依赖墙钟（lessons“经验”）。
-- 合成数据（规则 8）：新建合成家庭 HZ（家长 gZ、学生 Z1，建档 2026-10-01 八年级 → 届别 2028）与另一户家长 gO；
-- 用 79m 的 t.m_as、79y 的 t.y_try。主机名 jyj.synthcity.example 由 79y 登记（active）。
\set gZ '''f7a00000-0000-4000-8000-0000000000a1'''
\set gO '''f7a00000-0000-4000-8000-0000000000a2'''
\set aZ1 '''f7a00000-0000-4000-8000-0000000000b1'''
\set HZ '''f7a00000-0000-4000-8000-0000000000d1'''
\set Z1 '''f7a00000-0000-4000-8000-0000000000c1'''
RESET ROLE;
SELECT t.as_account(NULL);

-- ================= 一、选科在库里核对当前年级（0051） =================
INSERT INTO platform.account (id, kind) VALUES (:gZ, 'guardian'), (:gO, 'guardian'), (:aZ1, 'student');
INSERT INTO platform.household (id, created_by_account) VALUES (:HZ, :gZ);
INSERT INTO platform.student_profile (id, account_id, household_id, grade, region, created_at)
VALUES (:Z1, :aZ1, :HZ, 8, 'nanjing', '2026-10-01T10:00:00+08:00');
INSERT INTO platform.guardian_student_link (guardian_account_id, student_id, granted_via) VALUES (:gZ, :Z1, 'household_creator');
SELECT now AS za_clock0 FROM t.clock \gset
-- 北京时间 2028-08-31 23:00：届别 2028 还是九年级。试点订阅覆盖 2028-08-31 前后（只为本节）
UPDATE t.clock SET now = '2028-08-31T15:00:00+00';
INSERT INTO platform.subscription (household_id, student_id, tier, config_version, source, period_start, period_end)
VALUES (:HZ, :Z1, 'pro', platform.meter_current_config(), 'pilot_virtual', platform.meter_now() - interval '1 day', platform.meter_now() + interval '29 days');
-- 试点订阅可选的学科（beta 或 live，有已发布课程版本）subj；另一个学科 subj2 以已暂停的状态放进去（重新开通走 enroll_subject）
SELECT c.product AS subj FROM platform.subject_catalog c
 WHERE c.status IN ('beta', 'live') AND c.product IS NOT NULL
   AND EXISTS (SELECT 1 FROM platform.curriculum_version v WHERE v.subject = c.product AND v.status = 'published')
 ORDER BY c.code LIMIT 1 \gset
SELECT c.product AS subj2 FROM platform.subject_catalog c WHERE c.product IS NOT NULL AND c.product <> :'subj' ORDER BY c.code LIMIT 1 \gset
INSERT INTO platform.subject_enrollment (student_id, subject, status, paused_at) VALUES (:Z1, :'subj2', 'paused', platform.meter_now() - interval '40 days');
SELECT t.ok((SELECT exam_cohort = 2028 FROM platform.student_profile WHERE id = :Z1)
            AND platform.current_grade(2028::smallint, platform.meter_now()) = 9,
            '选科年级：前提——合成学生 Z1 届别 2028，计量时钟北京时间 2028-08-31 时是九年级');

-- 档案已删除（PR4 安全审查）：绑定家长给已删除的学生选科与未授权同一个 42501，不泄露删除状态与年级；在事务里标记删除、核对后回滚
BEGIN;
UPDATE platform.student_profile SET erased_at = platform.meter_now() WHERE id = :Z1;
SELECT t.ok(t.y_try('starbuddy_bff', :gZ, format('SELECT platform.enroll_subject(%L, %L)::text', :Z1, :'subj')) = '42501:not permitted'
            AND t.y_try('starbuddy_bff', :gZ, format('SELECT platform.enroll_subject(%L, %L)::text', :Z1, :'subj2')) = '42501:not permitted',
            '选科年级：档案已删除时绑定家长选科（含重新开通已暂停的）一律 42501，与未授权相同');
UPDATE t.clock SET now = '2028-08-31T16:00:00+00';
SELECT t.ok(t.y_try('starbuddy_bff', :gZ, format('SELECT platform.enroll_subject(%L, %L)::text', :Z1, :'subj2')) = '42501:not permitted',
            '选科年级：档案已删除且已毕业时仍是 42501（删除先于年级，不泄露年级）');
SELECT t.ok((SELECT count(*) FROM platform.subject_enrollment WHERE student_id = :Z1 AND status = 'active') = 0
            AND (SELECT status FROM platform.subject_enrollment WHERE student_id = :Z1 AND subject = :'subj2') = 'paused',
            '选科年级：已删除档案被拒的选科不改选科状态');
ROLLBACK;
SELECT t.ok((SELECT erased_at IS NULL FROM platform.student_profile WHERE id = :Z1) AND (SELECT now FROM t.clock) = '2028-08-31T15:00:00+00',
            '选科年级：删除用例已回滚（档案与计量时钟恢复）');

-- 北京时间 2028-08-31 23:59:59：最后一秒还是九年级，照常开通
UPDATE t.clock SET now = '2028-08-31T15:59:59+00';
SELECT t.ok(t.m_as('starbuddy_bff', :gZ, format('SELECT platform.enroll_subject(%L, %L)::text', :Z1, :'subj'))::jsonb ->> 'outcome' = 'enrolled',
            '选科年级：九年级最后一秒（计量时钟）照常开通');

-- 北京时间 2028-09-01 00:00：已毕业（current_grade 为 NULL）
UPDATE t.clock SET now = '2028-08-31T16:00:00+00';
SELECT t.ok(t.y_try('starbuddy_bff', :gZ, format('SELECT platform.enroll_subject(%L, %L)::text', :Z1, :'subj2')) = '55000:GRADE_OUT_OF_RANGE',
            '选科年级：已毕业时重新开通已暂停的学科被拒（55000 GRADE_OUT_OF_RANGE，与 BFF 的 409 对齐，不带年级）');
SELECT t.ok(t.y_try('starbuddy_bff', :gZ, format('SELECT platform.enroll_subject(%L, %L)::text', :Z1, :'subj')) = '55000:GRADE_OUT_OF_RANGE',
            '选科年级：已毕业时对已开通的学科再开通也是同一个拒绝（与 BFF 一致：年级为空一律不能新选）');
SELECT t.ok((SELECT string_agg(subject::text || ':' || status::text, ',' ORDER BY subject::text) FROM platform.subject_enrollment WHERE student_id = :Z1)
              = (SELECT string_agg(x, ',' ORDER BY x) FROM unnest(ARRAY[:'subj' || ':active', :'subj2' || ':paused']) x),
            '选科年级：被拒的调用不改选科状态');
-- 未授权先拒授权：别家家长、不存在的学生、空学生都是 42501，不因年级先回 55000（不泄露年级）
SELECT t.ok(t.y_try('starbuddy_bff', :gO, format('SELECT platform.enroll_subject(%L, %L)::text', :Z1, :'subj2')) = '42501:not permitted'
            AND t.y_try('starbuddy_bff', :gO, format('SELECT platform.enroll_subject(%L, %L)::text', gen_random_uuid(), :'subj2')) = '42501:not permitted'
            AND t.y_try('starbuddy_bff', :gZ, format('SELECT platform.enroll_subject(NULL, %L)::text', :'subj2')) = '42501:not permitted'
            AND t.y_try('starbuddy_bff', NULL, format('SELECT platform.enroll_subject(%L, %L)::text', :Z1, :'subj2')) = '42501:not permitted',
            '选科年级：未授权（别家家长、未设账号）与学生不存在同一个 42501，不先回年级错误');
-- 暂停不受影响：已开通的学科照常暂停
SELECT t.ok(t.m_as('starbuddy_bff', :gZ, format('SELECT platform.pause_subject(%L, %L)::text', :Z1, :'subj'))::jsonb ->> 'outcome' = 'paused',
            '选科年级：已毕业时已开通的学科照常可以暂停');
SELECT t.ok(t.y_try('starbuddy_bff', :gZ, format('SELECT platform.enroll_subject(%L, %L)::text', :Z1, :'subj')) = '55000:GRADE_OUT_OF_RANGE',
            '选科年级：暂停后已毕业时不能再开通');

-- 年级只按届别推算，不读存下的建档年级：已毕业时把建档年级改成 7 也照样拒绝
UPDATE platform.student_profile SET grade = 7 WHERE id = :Z1;
SELECT t.ok(t.y_try('starbuddy_bff', :gZ, format('SELECT platform.enroll_subject(%L, %L)::text', :Z1, :'subj')) = '55000:GRADE_OUT_OF_RANGE',
            '选科年级：不读建档年级（已毕业时建档年级改成 7 仍拒绝）');
UPDATE platform.student_profile SET grade = 8 WHERE id = :Z1;
-- 未入学：北京时间 2025-08-31 23:59:59 届别 2028 是六年级，拒绝；2025-09-01 00:00 起七年级，过了年级核对（没有覆盖那时的订阅 → no_subscription）
UPDATE t.clock SET now = '2025-08-31T15:59:59+00';
SELECT t.ok(t.y_try('starbuddy_bff', :gZ, format('SELECT platform.enroll_subject(%L, %L)::text', :Z1, :'subj')) = '55000:GRADE_OUT_OF_RANGE',
            '选科年级：未入学（六年级）时同样拒绝');
UPDATE t.clock SET now = '2025-08-31T16:00:00+00';
SELECT t.ok(t.m_as('starbuddy_bff', :gZ, format('SELECT platform.enroll_subject(%L, %L)::text', :Z1, :'subj'))::jsonb ->> 'outcome' = 'no_subscription',
            '选科年级：七年级第一秒过了年级核对（按计量时钟，不按墙钟），继续走订阅判断');
UPDATE t.clock SET now = :'za_clock0';

-- ================= 二、取锁函数设持锁事务的空闲上限（0052） =================
-- 在同一个事务里：先设调用方的空闲上限，以 kge 身份调取锁函数，再读本事务的设置
CREATE FUNCTION t.za_idle_after_lock(p_host text, p_pre text) RETURNS text LANGUAGE plpgsql AS $$
BEGIN
  PERFORM set_config('idle_in_transaction_session_timeout', p_pre, true);
  PERFORM t.m_as('starbuddy_kge', NULL, format('SELECT policy.lock_active_source_host(%L)::text', p_host));
  RETURN current_setting('idle_in_transaction_session_timeout');
END $$;
SELECT t.ok((SELECT status = 'active' FROM policy.source_domain WHERE host = 'jyj.synthcity.example'),
            '空闲上限：前提——合成主机名 jyj.synthcity.example 在清单里且 active');
SELECT t.ok(t.za_idle_after_lock('jyj.synthcity.example', '0') = '1min',
            '空闲上限：kge 取到清单行共享锁后，本事务空闲超过 60 秒即断开（调用方原来不限）');
SELECT t.ok(t.za_idle_after_lock('jyj.synthcity.example', '5min') = '1min',
            '空闲上限：调用方原来更宽（5 分钟）时收紧到 60 秒');
SELECT t.ok(t.za_idle_after_lock('jyj.synthcity.example', '10s') = '10s',
            '空闲上限：调用方已设更短的上限（10 秒）时不放宽');
SELECT t.ok(t.za_idle_after_lock('jyj.missing.example', '0') = '0',
            '空闲上限：没取到锁（主机名不在清单）时不改设置');
SELECT t.ok(current_setting('idle_in_transaction_session_timeout') = '0',
            '空闲上限：事务级设置，事务结束即恢复（不影响 kge 会话之后的事务）');
-- 新增、下线函数的锁等待上限是函数级设置：只在函数执行期间生效，返回后调用方的 lock_timeout 不变
SELECT t.ok(t.m_as('starbuddy_review', '5f000000-0000-4000-8000-00000000005f',
                   $$SELECT policy.add_source_domain('jyj.pr4.example', '合成 PR4 教育局') || '|' || current_setting('lock_timeout')$$)
              = 'jyj.pr4.example|0'
            AND t.m_as('starbuddy_review', '5f000000-0000-4000-8000-00000000005f',
                       $$SELECT policy.retire_source_domain('jyj.pr4.example') || '|' || current_setting('lock_timeout')$$)
              = 'jyj.pr4.example|0',
            '锁等待上限：新增、下线返回后调用方事务的 lock_timeout 不变（函数级设置，不像 SET LOCAL 留到事务结束）');
