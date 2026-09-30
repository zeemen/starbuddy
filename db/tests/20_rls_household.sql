-- RLS：家庭隔离与家长授权（CLAUDE.md 规则 5；开发计划 冲刺 1-B 验收）
-- 包含 security-reviewer 审查（2026-09-25）阻断 1–4 与漏测策略的复现。
SET ROLE starbuddy_bff;

-- ================= 未登录 =================
SELECT t.as_account(NULL);
SELECT t.ok((SELECT count(*) FROM platform.student_profile) = 0, '未登录：读不到任何学生档案');
SELECT t.ok((SELECT count(*) FROM platform.evidence_event) = 0, '未登录：读不到任何证据');
SELECT t.ok((SELECT count(*) FROM platform.mastery_estimate) = 0, '未登录：读不到任何掌握度');
SELECT t.ok((SELECT count(*) FROM platform.account) = 0, '未登录：读不到任何账号');

-- ================= 阻断 1：账号与联系方式只能经函数访问 =================
SELECT t.throws($$SELECT * FROM platform.account_contact$$, '42501', 'BFF 不能直接读联系方式（不能枚举手机号）');
SELECT t.throws($$INSERT INTO platform.account_contact (account_id, phone_e164, verified_at)
                  VALUES ('a1000000-0000-4000-8000-0000000000a1', '+99900000099', now())$$,
                '42501', '不能把手机号挂到别人的账号上（账号接管）');
SELECT t.throws($$INSERT INTO platform.account (kind) VALUES ('student')$$, '42501', 'BFF 不能直接建账号');
-- 0031 起 BFF 不能再调 0002 的旧登录入口（家长登录只经 0029 的 auth_otp_login；统一壳 H1 待人确认 B）
SELECT t.throws($$SELECT platform.signup_guardian('+99900000010')$$, '42501', 'BFF 不能调旧注册入口 signup_guardian（不经验证码注册）');
SELECT t.throws($$SELECT * FROM platform.lookup_accounts_by_phone('+99900000001')$$, '42501',
                'BFF 不能调旧查号入口 lookup_accounts_by_phone（号码是否注册的查询通道）');
-- 函数本身的行为不变（属主仍可执行；回滚 0031 即恢复授权）
RESET ROLE;
SELECT t.ok(platform.signup_guardian('+99900000010') IS NOT NULL, '家长注册只能经 signup_guardian');
SELECT t.ok((SELECT count(*) FROM platform.lookup_accounts_by_phone('+99900000010')) = 1, '登录按手机号只能查到账号 ID');
SELECT t.ok((SELECT count(*) FROM platform.lookup_accounts_by_phone('+99900000001')) = 2,
            '同一手机号可以对应多个账号（学生可能用家长的号）');
SET ROLE starbuddy_bff;

-- ================= 家长 gA：只看到自己绑定的学生 =================
SELECT t.as_account('a0000000-0000-4000-8000-00000000000a');
SELECT t.ok((SELECT array_agg(id) FROM platform.student_profile) = ARRAY['c1000000-0000-4000-8000-0000000000c1'::uuid],
            'gA 只看到自己绑定的学生 S1');
SELECT t.ok((SELECT count(*) FROM platform.student_profile WHERE id = 'c2000000-0000-4000-8000-0000000000c2') = 0,
            '同手机号不能跨档案：S2 的账号与 gA 同号，gA 仍看不到 S2');
SELECT t.ok((SELECT count(*) FROM platform.evidence_event WHERE student_id = 'c2000000-0000-4000-8000-0000000000c2') = 0,
            '同手机号不能跨档案：gA 看不到 S2 的证据');
SELECT t.ok((SELECT count(*) FROM platform.account) = 1, '账号只能读到自己');
SELECT t.ok((SELECT count(*) FROM platform.household) = 1, 'gA 只看到自己的家庭');
SELECT t.ok((SELECT count(*) FROM platform.guardian_student_link) = 1, 'gA 只看到自己的绑定');
-- 每张学习数据表都只能看到 S1 的
SELECT t.ok((SELECT count(*) FROM platform.mastery_estimate) = 1, 'gA 只看到 S1 的掌握度');
SELECT t.ok((SELECT count(*) FROM platform.evidence_event) = 1, 'gA 只看到 S1 的证据');
SELECT t.ok((SELECT count(DISTINCT student_id) FROM platform.attempt) = 1
            AND (SELECT bool_and(student_id = 'c1000000-0000-4000-8000-0000000000c1') FROM platform.attempt),
            'gA 只看到 S1 的作答');
SELECT t.ok((SELECT count(*) FROM platform.skill_observation WHERE student_id <> 'c1000000-0000-4000-8000-0000000000c1') = 0
            AND (SELECT count(*) FROM platform.skill_observation) = 1, 'gA 只看到 S1 的能力观测');
SELECT t.ok((SELECT count(*) FROM platform.error_attribution WHERE student_id <> 'c1000000-0000-4000-8000-0000000000c1') = 0
            AND (SELECT count(*) FROM platform.error_attribution) = 1, 'gA 只看到 S1 的错因');
SELECT t.ok((SELECT count(*) FROM platform.grade_result WHERE student_id <> 'c1000000-0000-4000-8000-0000000000c1') = 0
            AND (SELECT count(*) FROM platform.grade_result) = 1, 'gA 只看到 S1 的判分');
SELECT t.ok((SELECT count(*) FROM platform.grade_dimension) = 1, 'gA 只看到 S1 判分的维度');
SELECT t.ok((SELECT count(*) FROM platform.consent_record) = 1, 'gA 只看到 S1 的同意记录');
SELECT t.ok((SELECT count(*) FROM platform.subject_enrollment) = 1, 'gA 只看到 S1 的选科');
-- 内容：只有已发布 / 已准入的
SELECT t.ok((SELECT count(*) FROM platform.curriculum_version WHERE status <> 'published') = 0, '看不到未发布的课程版本');
SELECT t.ok((SELECT count(*) FROM platform.item WHERE admission <> 'admitted') = 0, '看不到未准入的题目');
SELECT t.ok((SELECT count(*) FROM platform.item) = 2, '能看到已准入的题目');

-- ================= 阻断 2、3：建档与绑定只能经 create_student =================
SELECT t.throws($$INSERT INTO platform.household (created_by_account) VALUES ('a0000000-0000-4000-8000-00000000000a')$$,
                '42501', 'BFF 不能直接建家庭');
SELECT t.throws($$INSERT INTO platform.student_profile (account_id, household_id, grade, region)
                  VALUES ('b1000000-0000-4000-8000-0000000000b1', 'd1000000-0000-4000-8000-0000000000d1', 8, 'nanjing')$$,
                '42501', '不能把别人的学生账号建进自己的家庭（按手机号关联学生）');
SELECT t.throws($$INSERT INTO platform.guardian_student_link (guardian_account_id, student_id, granted_via)
                  VALUES ('a0000000-0000-4000-8000-00000000000a', 'c2000000-0000-4000-8000-0000000000c2', 'household_creator')$$,
                '42501', 'gA 不能以“家庭创建者”身份绑定别家的学生');
SELECT t.throws($$INSERT INTO platform.guardian_student_link (guardian_account_id, student_id, granted_via)
                  VALUES ('a0000000-0000-4000-8000-00000000000a', 'c2000000-0000-4000-8000-0000000000c2', 'invite')$$,
                '42501', 'gA 不能伪造 invite 绑定');
SELECT t.throws($$SELECT platform.create_student('d2000000-0000-4000-8000-0000000000d2', 8::smallint, 'nanjing', NULL, NULL)$$,
                '42501', 'gA 不能在别人的家庭里建学生');
-- 正常路径：家长新建学生，自动成为创建者绑定
CREATE TEMP TABLE new_student AS
  SELECT platform.create_student('d1000000-0000-4000-8000-0000000000d1', 7::smallint, 'nanjing', 'bubian-2024', DATE '2014-06-01') AS id;
SELECT t.ok((SELECT count(*) FROM platform.student_profile WHERE id = (SELECT id FROM new_student)) = 1,
            '家长通过 create_student 新建的学生立刻可见（创建者绑定已建立）');
SELECT t.ok((SELECT count(*) FROM platform.guardian_student_link WHERE student_id = (SELECT id FROM new_student)
             AND granted_via = 'household_creator') = 1, 'create_student 同时建立创建者绑定');

SELECT t.throws($$SELECT * FROM platform.guardian_invite$$, '42501', 'BFF 不能直接读邀请表');
SELECT t.throws($$SELECT platform.create_guardian_invite('c2000000-0000-4000-8000-0000000000c2')$$,
                '42501', 'gA 不能为别家的学生签发邀请');
SELECT t.throws($$SELECT platform.create_guardian_invite('c1000000-0000-4000-8000-0000000000c1', interval '-1 hour')$$,
                '22023', '邀请有效期不能为负');
SELECT t.throws($$SELECT platform.create_guardian_invite('c1000000-0000-4000-8000-0000000000c1', interval '30 days')$$,
                '22023', '邀请有效期不能超过 7 天');

-- 学科数据只能由学习服务写
SELECT t.throws($$INSERT INTO platform.evidence_event (evidence_id, occurred_at, student_id, subject, kind, admission, payload,
                  source_method, request_id, request_idempotency_key) VALUES
                  (gen_random_uuid(), '2026-10-10 00:00:00+00', 'c1000000-0000-4000-8000-0000000000c1', 'aya', 'response_text',
                   'admitted', '{"text":"x"}', 'grade', gen_random_uuid(), 'synthetic-bff-write-0001')$$,
                '42501', 'BFF 不能写证据');
SELECT t.throws($$INSERT INTO platform.skill_observation (student_id, subject, skill_local_id, outcome, confidence, algorithm_version,
                  evidence_ids, source_request_id) VALUES ('c1000000-0000-4000-8000-0000000000c1', 'aya', 'K-QF-0301',
                  'demonstrated', 1, 'x@1.0.0', '{e1000000-0000-4000-8000-000000000001}', gen_random_uuid())$$,
                '42501', 'BFF 不能写能力观测');
SELECT t.throws($$INSERT INTO platform.error_attribution (student_id, subject, subject_category, cross_subject_category, confidence,
                  algorithm_version, evidence_ids, source_request_id) VALUES ('c1000000-0000-4000-8000-0000000000c1', 'aya',
                  'x', 'knowledge', 1, 'x@1.0.0', '{e1000000-0000-4000-8000-000000000001}', gen_random_uuid())$$,
                '42501', 'BFF 不能写错因');
SELECT t.throws($$SELECT * FROM platform.evidence_event_202610$$, '42501', '不能绕过父表直接读证据分区');
SELECT t.throws($$SELECT * FROM platform.attempt_202610$$, '42501', '不能绕过父表直接读作答分区');
SELECT t.throws($$SELECT * FROM platform.review_task$$, '42501', 'BFF 不能读复核队列');

-- 同意记录：只有已绑定的家长能写
SELECT t.throws($$INSERT INTO platform.consent_record (student_id, guardian_account_id, consent_type, document_version, decision)
                  VALUES ('c2000000-0000-4000-8000-0000000000c2', 'a0000000-0000-4000-8000-00000000000a',
                  'guardian_consent_under14', 'consent-doc@1.0.0', 'granted')$$,
                '42501', 'gA 不能给别家的学生写同意记录');
SELECT t.throws($$INSERT INTO platform.consent_record (student_id, guardian_account_id, consent_type, document_version, decision)
                  VALUES ('c1000000-0000-4000-8000-0000000000c1', 'b0000000-0000-4000-8000-00000000000b',
                  'guardian_consent_under14', 'consent-doc@1.0.0', 'granted')$$,
                '42501', 'gA 不能冒用别的家长的名义写同意记录');
INSERT INTO platform.consent_record (student_id, guardian_account_id, consent_type, document_version, decision)
VALUES ('c1000000-0000-4000-8000-0000000000c1', 'a0000000-0000-4000-8000-00000000000a', 'privacy_policy', 'privacy@1.0.0', 'granted');
SELECT t.ok(true, '已绑定家长可以写同意记录');
-- 选科：只能给自己能读的学生选
SELECT t.throws($$INSERT INTO platform.subject_enrollment (student_id, subject) VALUES ('c2000000-0000-4000-8000-0000000000c2', 'wenli')$$,
                '42501', 'gA 不能给别家的学生选科');

-- ================= 学生 S1 =================
SELECT t.as_account('a1000000-0000-4000-8000-0000000000a1');
SELECT t.ok((SELECT array_agg(id) FROM platform.student_profile) = ARRAY['c1000000-0000-4000-8000-0000000000c1'::uuid],
            '学生 S1 只看到自己的档案');
SELECT t.ok((SELECT count(*) FROM platform.guardian_student_link) = 1, '学生 S1 能看到谁绑定了自己');
SELECT t.throws($$SELECT platform.create_student(NULL, 7::smallint, 'nanjing', NULL, DATE '2014-01-01')$$,
                '42501', '学生不能自建家庭并把自己绑成家长（阻断 2）');
SELECT t.throws($$INSERT INTO platform.consent_record (student_id, guardian_account_id, consent_type, document_version, decision)
                  VALUES ('c1000000-0000-4000-8000-0000000000c1', 'a1000000-0000-4000-8000-0000000000a1',
                  'guardian_consent_under14', 'consent-doc@1.0.0', 'granted')$$,
                '42501', '学生不能伪造 14 岁以下监护人同意（阻断 2）');
SELECT t.throws($$SELECT platform.redeem_guardian_invite('whatever-token-000000')$$, '42501', '学生账号不能兑换邀请');
SELECT t.throws($$INSERT INTO platform.mastery_estimate (student_id, subject, skill_local_id, state, confidence, algorithm_version, evidence_ids)
                  VALUES ('c1000000-0000-4000-8000-0000000000c1', 'aya', 'K-QF-0301', 'transfer', 1, 'self@1.0.0', '{e1000000-0000-4000-8000-000000000001}')$$,
                '42501', '学生不能给自己写掌握度（不能自己点亮星星）');
SELECT t.throws($$UPDATE platform.mastery_estimate SET state = 'retain'$$, '42501', '学生不能改掌握度');
SELECT t.throws($$INSERT INTO platform.grade_result (student_id, subject, attempt_id, attempt_occurred_at, verdict, rubric_version,
                  score_total, max_total, calibrated, confidence, source_version, model_version, human_review_required, semantics,
                  evidence_ids, source_request_id) VALUES ('c1000000-0000-4000-8000-0000000000c1', 'aya',
                  'a7000000-0000-4000-8000-0000000000a7', '2026-10-08 11:30:00+00', 'correct', 'self@1.0.0', 1, 1, true, 1,
                  'algo:self@1.0.0', 'algo:self@1.0.0', false, 'formal', '{e1000000-0000-4000-8000-000000000001}', gen_random_uuid())$$,
                '42501', '学生不能写判分');

-- ================= 家长自述 =================
SELECT t.as_account('a0000000-0000-4000-8000-00000000000a');
INSERT INTO platform.guardian_report (student_id, guardian_account_id, topic, content)
VALUES ('c1000000-0000-4000-8000-0000000000c1', 'a0000000-0000-4000-8000-00000000000a', 'sleep_schedule', '（合成）最近晚睡');
SELECT t.ok((SELECT count(*) FROM platform.guardian_report) = 1, 'gA 能读到自己写的家长自述');
SELECT t.as_account('a1000000-0000-4000-8000-0000000000a1');
SELECT t.ok((SELECT count(*) FROM platform.guardian_report) = 0, '学生读不到家长自述');
SELECT t.as_account('b0000000-0000-4000-8000-00000000000b');
SELECT t.throws($$INSERT INTO platform.guardian_report (student_id, guardian_account_id, topic, content)
                  VALUES ('c1000000-0000-4000-8000-0000000000c1', 'b0000000-0000-4000-8000-00000000000b', 'expectation', '（合成）')$$,
                '42501', 'gB 不能给别家的学生写家长自述');
DELETE FROM platform.guardian_report;
RESET ROLE;
SELECT t.ok((SELECT count(*) FROM platform.guardian_report) = 1, 'gB 删不掉 gA 写的家长自述');
SET ROLE starbuddy_bff;

-- ================= 邀请（阻断 4）=================
-- S2 本人签发，gC 兑换
SELECT t.as_account('b1000000-0000-4000-8000-0000000000b1');
CREATE TEMP TABLE inv1 AS SELECT platform.create_guardian_invite('c2000000-0000-4000-8000-0000000000c2') AS token;
SELECT t.ok((SELECT length(token) FROM inv1) = 64, '邀请码由库内生成（64 位十六进制）');
SELECT t.as_account('cc000000-0000-4000-8000-0000000000cc');
SELECT t.ok((SELECT count(*) FROM platform.student_profile) = 0, '兑换前 gC 看不到任何学生');
SELECT t.ok(platform.redeem_guardian_invite((SELECT token FROM inv1)) IS NOT NULL, 'gC 兑换邀请成功');
SELECT t.ok((SELECT array_agg(id) FROM platform.student_profile) = ARRAY['c2000000-0000-4000-8000-0000000000c2'::uuid],
            '兑换后 gC 只看到 S2');
SELECT t.throws(format('SELECT platform.redeem_guardian_invite(%L)', (SELECT token FROM inv1)), '22023', '邀请不能重复使用');
SELECT t.throws($$SELECT platform.redeem_guardian_invite('synthetic-wrong-token-9999')$$, '22023', '错误的邀请码无效');

-- 两位家长都绑定了 S2：一位写的家长自述，另一位看不到
SELECT t.as_account('b0000000-0000-4000-8000-00000000000b');
INSERT INTO platform.guardian_report (student_id, guardian_account_id, topic, content)
VALUES ('c2000000-0000-4000-8000-0000000000c2', 'b0000000-0000-4000-8000-00000000000b', 'expectation', '（合成）希望多练阅读');
SELECT t.as_account('cc000000-0000-4000-8000-0000000000cc');
SELECT t.ok((SELECT count(*) FROM platform.guardian_report) = 0, '同一学生的另一位家长看不到这条家长自述');

-- gA 看到 gC 的绑定吗？不能：绑定只对本人与学生可见
SELECT t.as_account('a0000000-0000-4000-8000-00000000000a');
SELECT t.ok((SELECT count(*) FROM platform.guardian_student_link WHERE student_id = 'c2000000-0000-4000-8000-0000000000c2') = 0,
            'gA 看不到别家学生的绑定');
SELECT t.ok((SELECT count(*) FROM platform.guardian_student_link WHERE guardian_account_id = 'cc000000-0000-4000-8000-0000000000cc') = 0,
            'gA 看不到 gC 的绑定');

-- gC 预签一张邀请，然后被解除；预签的邀请必须失效
SELECT t.as_account('cc000000-0000-4000-8000-0000000000cc');
CREATE TEMP TABLE inv2 AS SELECT platform.create_guardian_invite('c2000000-0000-4000-8000-0000000000c2') AS token;
SELECT t.throws(format('SELECT platform.redeem_guardian_invite(%L)', (SELECT token FROM inv2)), '22023', '签发人不能兑换自己签的邀请');
-- gA 不能解除别人的绑定
SELECT t.as_account('a0000000-0000-4000-8000-00000000000a');
UPDATE platform.guardian_student_link SET status = 'revoked' WHERE guardian_account_id = 'cc000000-0000-4000-8000-0000000000cc';
RESET ROLE;
SELECT t.ok((SELECT status FROM platform.guardian_student_link WHERE guardian_account_id = 'cc000000-0000-4000-8000-0000000000cc') = 'active',
            'gA 解除不了别人的绑定');
SET ROLE starbuddy_bff;
-- 学生本人解除 gC
SELECT t.as_account('b1000000-0000-4000-8000-0000000000b1');
SELECT t.throws($$UPDATE platform.guardian_student_link SET status = 'revoked', revoked_at = '1999-01-01'
                  WHERE guardian_account_id = 'cc000000-0000-4000-8000-0000000000cc'$$, '42501', 'revoked_at 不能由 BFF 指定');
UPDATE platform.guardian_student_link SET status = 'revoked'
 WHERE guardian_account_id = 'cc000000-0000-4000-8000-0000000000cc';
RESET ROLE;
SELECT t.ok((SELECT revoked_at > now() - interval '1 minute' FROM platform.guardian_student_link
              WHERE guardian_account_id = 'cc000000-0000-4000-8000-0000000000cc'), 'revoked_at 由库写 now()，不能伪造');
SET ROLE starbuddy_bff;
UPDATE platform.guardian_student_link SET status = 'active' WHERE guardian_account_id = 'cc000000-0000-4000-8000-0000000000cc';
RESET ROLE;
SELECT t.ok((SELECT status FROM platform.guardian_student_link WHERE guardian_account_id = 'cc000000-0000-4000-8000-0000000000cc') = 'revoked',
            '解除的绑定不能恢复');
SET ROLE starbuddy_bff;
SELECT t.as_account('cc000000-0000-4000-8000-0000000000cc');
SELECT t.ok((SELECT count(*) FROM platform.student_profile) = 0, '解除绑定后 gC 看不到 S2');
SELECT t.ok((SELECT count(*) FROM platform.evidence_event) = 0, '解除绑定后 gC 看不到 S2 的证据');
SELECT t.as_account('b0000000-0000-4000-8000-00000000000b');
SELECT t.throws(format('SELECT platform.redeem_guardian_invite(%L)', (SELECT token FROM inv2)), '22023',
                '被解除的家长预签的邀请失效（阻断 4）');

RESET ROLE;
SELECT t.as_account(NULL);

-- 过期邀请无效（以超级用户造一条过期邀请）
INSERT INTO platform.guardian_invite (student_id, token_sha256, created_by_account, created_at, expires_at)
VALUES ('c2000000-0000-4000-8000-0000000000c2', sha256(convert_to('synthetic-expired-token-0001', 'UTF8')),
        'b1000000-0000-4000-8000-0000000000b1', now() - interval '2 hours', now() - interval '1 hour');
-- 签发人账号被禁用后，它签发的未用邀请不能再兑换（兑换时重新校验签发人权限）
SET ROLE starbuddy_bff;
SELECT t.as_account('b1000000-0000-4000-8000-0000000000b1');
CREATE TEMP TABLE inv3 AS SELECT platform.create_guardian_invite('c2000000-0000-4000-8000-0000000000c2') AS token;
RESET ROLE;
UPDATE platform.account SET status = 'disabled' WHERE id = 'b1000000-0000-4000-8000-0000000000b1';
SET ROLE starbuddy_bff;
SELECT t.as_account('a0000000-0000-4000-8000-00000000000a');
SELECT t.throws(format('SELECT platform.redeem_guardian_invite(%L)', (SELECT token FROM inv3)), '22023',
                '签发人已失去权限时，邀请不能兑换');
RESET ROLE;
UPDATE platform.account SET status = 'active' WHERE id = 'b1000000-0000-4000-8000-0000000000b1';
SELECT t.as_account(NULL);

-- 禁用账号（安全审查建议 3）
UPDATE platform.account SET status = 'disabled' WHERE id = 'a1000000-0000-4000-8000-0000000000a1';
SET ROLE starbuddy_bff;
SELECT t.as_account('cc000000-0000-4000-8000-0000000000cc');
SELECT t.throws($$SELECT platform.redeem_guardian_invite('synthetic-expired-token-0001')$$, '22023', '过期邀请无效');
SELECT t.as_account('a1000000-0000-4000-8000-0000000000a1');
SELECT t.ok((SELECT count(*) FROM platform.student_profile) = 0, '禁用的学生账号读不到任何数据');
RESET ROLE;
UPDATE platform.account SET status = 'active' WHERE id = 'a1000000-0000-4000-8000-0000000000a1';
SELECT t.as_account(NULL);

-- 分区上不得授予任何应用角色权限（安全审查建议 9）
SELECT t.ok(NOT EXISTS (
  SELECT 1 FROM pg_inherits i JOIN pg_class c ON c.oid = i.inhrelid
   WHERE i.inhparent IN ('platform.evidence_event'::regclass, 'platform.attempt'::regclass)
     AND (has_table_privilege('starbuddy_bff', c.oid, 'SELECT') OR has_table_privilege('starbuddy_learning', c.oid, 'SELECT'))),
  '分区表没有授予应用角色任何权限');
