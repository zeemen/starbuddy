-- RLS：家庭隔离与家长授权（CLAUDE.md 规则 5；开发计划 冲刺 1-B 验收）
SET ROLE starbuddy_bff;

-- 没有设置当前账号：什么都读不到
SELECT t.as_account(NULL);
SELECT t.ok((SELECT count(*) FROM platform.student_profile) = 0, '未登录：读不到任何学生档案');
SELECT t.ok((SELECT count(*) FROM platform.evidence_event) = 0, '未登录：读不到任何证据');
SELECT t.ok((SELECT count(*) FROM platform.mastery_estimate) = 0, '未登录：读不到任何掌握度');

-- 家长 gA：只能看到自己绑定的 S1
SELECT t.as_account('a0000000-0000-4000-8000-00000000000a');
SELECT t.ok((SELECT array_agg(id) FROM platform.student_profile) = ARRAY['c1000000-0000-4000-8000-0000000000c1'::uuid],
            'gA 只看到自己绑定的学生 S1');
SELECT t.ok((SELECT count(*) FROM platform.student_profile WHERE id = 'c2000000-0000-4000-8000-0000000000c2') = 0,
            '同手机号不能跨档案：S2 的账号与 gA 同号，gA 仍看不到 S2');
SELECT t.ok((SELECT count(*) FROM platform.evidence_event WHERE student_id = 'c2000000-0000-4000-8000-0000000000c2') = 0,
            '同手机号不能跨档案：gA 看不到 S2 的证据');
SELECT t.ok((SELECT count(*) FROM platform.mastery_estimate) = 1, 'gA 只看到 S1 的掌握度');
SELECT t.ok((SELECT count(*) FROM platform.evidence_event) = 1, 'gA 只看到 S1 的证据');
SELECT t.ok((SELECT count(*) FROM platform.household) = 1, 'gA 只看到自己的家庭');

-- 家长不能绕过邀请直接建立绑定（修正文砺 guardians 漏洞）
SELECT t.throws($$INSERT INTO platform.guardian_student_link (guardian_account_id, student_id, granted_via)
                  VALUES ('a0000000-0000-4000-8000-00000000000a', 'c2000000-0000-4000-8000-0000000000c2', 'household_creator')$$,
                '42501', 'gA 不能以“家庭创建者”身份绑定别家的学生');
SELECT t.throws($$INSERT INTO platform.guardian_student_link (guardian_account_id, student_id, granted_via)
                  VALUES ('a0000000-0000-4000-8000-00000000000a', 'c2000000-0000-4000-8000-0000000000c2', 'invite')$$,
                '42501', 'gA 不能伪造 invite 绑定');
SELECT t.throws($$SELECT * FROM platform.guardian_invite$$, '42501', 'BFF 不能直接读邀请表');
SELECT t.throws($$SELECT platform.create_guardian_invite('c2000000-0000-4000-8000-0000000000c2', 'synthetic-token-should-fail')$$,
                '42501', 'gA 不能为别家的学生签发邀请');

-- 学科数据只能由学习服务写
SELECT t.throws($$INSERT INTO platform.evidence_event (evidence_id, occurred_at, student_id, subject, kind, admission, payload,
                  source_method, request_id, request_idempotency_key) VALUES
                  (gen_random_uuid(), '2026-10-10 00:00:00+00', 'c1000000-0000-4000-8000-0000000000c1', 'aya', 'response_text',
                   'admitted', '{"text":"x"}', 'grade', gen_random_uuid(), 'synthetic-bff-write-0001')$$,
                '42501', 'BFF 不能写证据');
SELECT t.throws($$SELECT * FROM platform.evidence_event_202610$$, '42501', '不能绕过父表直接读分区');
SELECT t.throws($$SELECT * FROM platform.review_task$$, '42501', 'BFF 不能读复核队列');

-- 学生 S1：只读自己，不能写判分和掌握度（修正知行 owner for all 的漏洞）
SELECT t.as_account('a1000000-0000-4000-8000-0000000000a1');
SELECT t.ok((SELECT array_agg(id) FROM platform.student_profile) = ARRAY['c1000000-0000-4000-8000-0000000000c1'::uuid],
            '学生 S1 只看到自己的档案');
SELECT t.ok((SELECT count(*) FROM platform.guardian_student_link) = 1, '学生 S1 能看到谁绑定了自己');
SELECT t.throws($$INSERT INTO platform.mastery_estimate (student_id, subject, skill_local_id, state, confidence, algorithm_version, evidence_ids)
                  VALUES ('c1000000-0000-4000-8000-0000000000c1', 'aya', 'K-QF-0301', 'transfer', 1, 'self@1.0.0', '{e1000000-0000-4000-8000-000000000001}')$$,
                '42501', '学生不能给自己写掌握度（不能自己点亮星星）');
SELECT t.throws($$UPDATE platform.mastery_estimate SET state = 'retain'$$, '42501', '学生不能改掌握度');
SELECT t.throws($$INSERT INTO platform.grade_result (student_id, subject, attempt_id, attempt_occurred_at, verdict, rubric_version,
                  score_total, max_total, calibrated, confidence, source_version, model_version, human_review_required, semantics,
                  evidence_ids, source_request_id) VALUES ('c1000000-0000-4000-8000-0000000000c1', 'aya', gen_random_uuid(), now(),
                  'correct', 'self@1.0.0', 1, 1, true, 1, 'algo:self@1.0.0', 'algo:self@1.0.0', false, 'formal',
                  '{e1000000-0000-4000-8000-000000000001}', gen_random_uuid())$$,
                '42501', '学生不能写判分');

-- 家长自述：只有写的家长可见可删，学生不可见，其他家长写不进
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
SELECT t.as_account('a0000000-0000-4000-8000-00000000000a');
DELETE FROM platform.guardian_report;
SELECT t.ok((SELECT count(*) FROM platform.guardian_report) = 0, 'gA 能删除自己写的家长自述');

-- 邀请：S2 本人签发，gC 兑换后才能看到 S2
SELECT t.as_account('b1000000-0000-4000-8000-0000000000b1');
SELECT t.ok(platform.create_guardian_invite('c2000000-0000-4000-8000-0000000000c2', 'synthetic-invite-token-0001') IS NOT NULL,
            '学生本人可以签发邀请');
SELECT t.throws($$SELECT platform.redeem_guardian_invite('synthetic-invite-token-0001')$$, '42501', '学生账号不能兑换邀请');
SELECT t.as_account('cc000000-0000-4000-8000-0000000000cc');
SELECT t.ok((SELECT count(*) FROM platform.student_profile) = 0, '兑换前 gC 看不到任何学生');
SELECT t.ok(platform.redeem_guardian_invite('synthetic-invite-token-0001') IS NOT NULL, 'gC 兑换邀请成功');
SELECT t.ok((SELECT array_agg(id) FROM platform.student_profile) = ARRAY['c2000000-0000-4000-8000-0000000000c2'::uuid],
            '兑换后 gC 只看到 S2');
SELECT t.throws($$SELECT platform.redeem_guardian_invite('synthetic-invite-token-0001')$$, '22023', '邀请不能重复使用');
SELECT t.throws($$SELECT platform.redeem_guardian_invite('synthetic-wrong-token-9999')$$, '22023', '错误的邀请码无效');

-- 解除绑定之后立刻失去访问
UPDATE platform.guardian_student_link SET status = 'revoked', revoked_at = now()
 WHERE guardian_account_id = 'cc000000-0000-4000-8000-0000000000cc';
SELECT t.ok((SELECT count(*) FROM platform.student_profile) = 0, '解除绑定后 gC 看不到 S2');
SELECT t.ok((SELECT count(*) FROM platform.evidence_event) = 0, '解除绑定后 gC 看不到 S2 的证据');

RESET ROLE;
SELECT t.as_account(NULL);

-- 过期邀请无效（以超级用户造一条过期邀请）
INSERT INTO platform.guardian_invite (student_id, token_sha256, created_by_account, expires_at)
VALUES ('c2000000-0000-4000-8000-0000000000c2', sha256(convert_to('synthetic-expired-token-0001', 'UTF8')),
        'b1000000-0000-4000-8000-0000000000b1', now() - interval '1 hour');
SET ROLE starbuddy_bff;
SELECT t.as_account('cc000000-0000-4000-8000-0000000000cc');
SELECT t.throws($$SELECT platform.redeem_guardian_invite('synthetic-expired-token-0001')$$, '22023', '过期邀请无效');
RESET ROLE;
SELECT t.as_account(NULL);
