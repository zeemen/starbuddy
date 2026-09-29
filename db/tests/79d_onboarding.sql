-- 0021 / 0022 建档事实层（specs/sie-01-onboarding/spec.md 验收）。全部合成：夹具 10_fixtures 的 S1（gA 的孩子）、S2（gB 的孩子）。
-- 以 BFF 身份、按账号调用（与 20_rls_household.sql 同一做法）。

CREATE FUNCTION t.ob(p_acct uuid, p_sql text) RETURNS text LANGUAGE plpgsql AS $$
DECLARE v_prev text := current_user; v_out text;
BEGIN
  PERFORM t.as_account(p_acct);
  SET LOCAL ROLE starbuddy_bff;
  EXECUTE p_sql INTO v_out;
  EXECUTE format('SET LOCAL ROLE %I', v_prev);
  PERFORM t.as_account(NULL);
  RETURN v_out;
END $$;
GRANT EXECUTE ON FUNCTION t.ob(uuid, text) TO PUBLIC;

\set S1 '''c1000000-0000-4000-8000-0000000000c1'''
\set S2 '''c2000000-0000-4000-8000-0000000000c2'''
\set aS1 '''a1000000-0000-4000-8000-0000000000a1'''
\set aS2 '''b1000000-0000-4000-8000-0000000000b1'''
\set gA '''a0000000-0000-4000-8000-00000000000a'''
\set gB '''b0000000-0000-4000-8000-00000000000b'''
\set gC '''cc000000-0000-4000-8000-0000000000cc'''

-- ================= 学生自述：只有学生本人能写 =================
SELECT t.ok(t.ob(:aS1, format($$SELECT insight.submit_self_report(%L, 'through.g9.focus_subject', '{"subject": "aya"}')::text$$, :S1)) IS NOT NULL,
            '学生本人可以写自述');
SELECT t.throws(format('SELECT t.ob(%L, %L)', :aS1, format($$SELECT insight.submit_self_report(%L, 'through.g9.focus_subject', '{"subject": "aya"}')::text$$, :S2)),
                '42501', '学生不能给别的学生写自述');
SELECT t.throws(format('SELECT t.ob(%L, %L)', :gA, format($$SELECT insight.submit_self_report(%L, 'through.g9.near_term_goal', '{"text": "合成目标"}')::text$$, :S1)),
                '42501', '家长不能冒充学生写自述');
SELECT t.throws(format('SELECT t.ob(%L, %L)', :aS1, format($$SELECT insight.submit_self_report(%L, 'outer.g8.cooperation_capacity', '{"level": "regular"}')::text$$, :S1)),
                '22023', '家长观察字段不能由学生写');
SELECT t.throws(format('SELECT t.ob(%L, %L)', :aS1, format($$SELECT insight.submit_self_report(%L, 'middle.g6.personality', '{"text": "x"}')::text$$, :S1)),
                '22023', '字典以外的字段一律拒绝');

-- ================= 取值形状 =================
SELECT t.ok(insight.valid_report_value('outer.g1.weekly_available_time', '{"minutes": 300}')
            AND NOT insight.valid_report_value('outer.g1.weekly_available_time', '{"minutes": 300, "extra": 1}')
            AND NOT insight.valid_report_value('outer.g1.weekly_available_time', '{"minutes": 5000}')
            AND NOT insight.valid_report_value('outer.g1.weekly_available_time', '{"minutes": 30.5}')
            AND NOT insight.valid_report_value('outer.g1.weekly_available_time', '{"minutes": "300"}'), '可用时间：整数分钟 0–4200，不收多余的键');
SELECT t.ok(insight.valid_report_value('outer.g1.devices', '{"devices": ["phone", "computer"]}')
            AND NOT insight.valid_report_value('outer.g1.devices', '{"devices": []}')
            AND NOT insight.valid_report_value('outer.g1.devices', '{"devices": ["phone", "phone"]}')
            AND NOT insight.valid_report_value('outer.g1.devices', '{"devices": ["camera"]}'), '学习设备：非空、不重复、只收列出的取值');
SELECT t.ok(insight.valid_report_value('through.g9.near_term_goal', jsonb_build_object('text', repeat('目', 200)))
            AND NOT insight.valid_report_value('through.g9.near_term_goal', jsonb_build_object('text', repeat('目', 201)))
            AND NOT insight.valid_report_value('through.g9.near_term_goal', jsonb_build_object('text', E'换行\n'))
            AND NOT insight.valid_report_value('through.g9.near_term_goal', '{"text": ""}'), '自由文本：1–200 字，不含控制字符');
SELECT t.ok(insight.valid_report_value('middle.g6.self_pain_points', '{"points": ["concept", "time"], "note": "合成说明"}')
            AND insight.valid_report_value('middle.g6.self_pain_points', '{"points": []}')
            AND NOT insight.valid_report_value('middle.g6.self_pain_points', '{"points": ["lazy"]}'), '自评痛点：五类之内，可附说明');
SELECT t.ok(insight.valid_report_value('middle.g6.stated_interests', '{"text": "合成兴趣"}'), '兴趣：自由文本');
SELECT t.throws(format('SELECT t.ob(%L, %L)', :aS1, format($$SELECT insight.submit_self_report(%L, 'middle.g6.learning_mode_preference', '{"mode": "fast"}')::text$$, :S1)),
                '22023', '取值不合形状被拒');

-- ================= 家长观察：只有已绑定家长能写 =================
SELECT t.ok(t.ob(:gA, format($$SELECT insight.submit_guardian_report(%L, 'outer.g1.weekly_available_time', '{"minutes": 120}')::text$$, :S1)) IS NOT NULL,
            '已绑定家长可以写家长观察');
SELECT t.throws(format('SELECT t.ob(%L, %L)', :gB, format($$SELECT insight.submit_guardian_report(%L, 'outer.g1.weekly_available_time', '{"minutes": 120}')::text$$, :S1)),
                '42501', '别家的家长不能写');
SELECT t.throws(format('SELECT t.ob(%L, %L)', :gC, format($$SELECT insight.submit_guardian_report(%L, 'outer.g1.devices', '{"devices": ["phone"]}')::text$$, :S1)),
                '42501', '没有绑定的家长不能写');
SELECT t.throws(format('SELECT t.ob(%L, %L)', :aS1, format($$SELECT insight.submit_guardian_report(%L, 'outer.g1.devices', '{"devices": ["phone"]}')::text$$, :S1)),
                '42501', '学生不能冒充家长写家长观察');
SELECT t.throws(format('SELECT t.ob(%L, %L)', :gA, format($$SELECT insight.submit_guardian_report(%L, 'through.g9.near_term_goal', '{"text": "x"}')::text$$, :S1)),
                '22023', '学生自述字段不能由家长写');

-- ================= 两边说法不同都存；改答案保留轨迹 =================
SELECT t.ob(:aS1, format($$SELECT insight.submit_self_report(%L, 'outer.g1.weekly_available_time', '{"minutes": 300}')::text$$, :S1));
SELECT t.ok((SELECT count(*) FROM insight.self_report WHERE student_id = :S1 AND field_id = 'outer.g1.weekly_available_time') = 1
            AND (SELECT count(*) FROM insight.guardian_report WHERE student_id = :S1 AND field_id = 'outer.g1.weekly_available_time') = 1,
            '同一字段学生说 300 分钟、家长说 120 分钟：两边都存，不合并');
SELECT t.ob(:aS1, format($$SELECT insight.submit_self_report(%L, 'through.g9.near_term_goal', '{"text": "合成目标一"}')::text$$, :S1));
SELECT t.ob(:aS1, format($$SELECT insight.submit_self_report(%L, 'through.g9.near_term_goal', '{"text": "合成目标二"}')::text$$, :S1));
SELECT t.ok((SELECT count(*) FROM insight.self_report WHERE student_id = :S1 AND field_id = 'through.g9.near_term_goal') = 2
            AND (SELECT value ->> 'text' FROM insight.self_report WHERE student_id = :S1 AND field_id = 'through.g9.near_term_goal' AND superseded_at IS NULL) = '合成目标二',
            '改答案：旧行保留（标为已替换），新行为当前');
SELECT t.ok((SELECT bool_and(source_type = 'self') FROM insight.self_report) AND (SELECT bool_and(source_type = 'guardian') FROM insight.guardian_report),
            '每行都带来源类型');
SELECT t.throws($$UPDATE insight.self_report SET value = '{"text": "偷改"}' WHERE field_id = 'through.g9.near_term_goal'$$, '42501', '超级用户也不能改自述内容');
SELECT t.throws($$DELETE FROM insight.guardian_report$$, '42501', '家长观察不能删（删除随导出与删除流程另做）');
SELECT t.throws($$TRUNCATE insight.self_report$$, '42501', '自述不能清空');

-- ================= 互不可见 =================
SELECT t.ok((t.ob(:aS1, format('SELECT insight.onboarding_view(%L)::text', :S1))::jsonb ->> 'role') = 'self'
            AND (t.ob(:aS1, format('SELECT insight.onboarding_view(%L)::text', :S1))::jsonb #>> '{reports,outer.g1.weekly_available_time,minutes}') = '300',
            '学生看到自己的自述（自己说的 300 分钟）');
SELECT t.ok(position('120' IN t.ob(:aS1, format('SELECT insight.onboarding_view(%L)::text', :S1))) = 0,
            '学生看不到家长说的 120 分钟（家长观察对学生不可见）');
SELECT t.ok((t.ob(:gA, format('SELECT insight.onboarding_view(%L)::text', :S1))::jsonb #>> '{reports,outer.g1.weekly_available_time,minutes}') = '120'
            AND position('合成目标' IN t.ob(:gA, format('SELECT insight.onboarding_view(%L)::text', :S1))) = 0
            AND (t.ob(:gA, format('SELECT insight.onboarding_view(%L)::text', :S1))::jsonb #> '{reports}') ? 'through.g9.focus_subject' = false,
            '家长只看到自己写的观察，看不到学生自述原文（目标、最想改善的学科）');
SELECT t.ok(t.ob(:gB, format('SELECT insight.onboarding_view(%L)::text', :S1)) IS NULL
            AND t.ob(:aS2, format('SELECT insight.onboarding_view(%L)::text', :S1)) IS NULL
            AND t.ob(:gC, format('SELECT insight.onboarding_view(%L)::text', :S1)) IS NULL, '别家家长、别的学生、没有绑定的家长：看不到（与不存在相同）');
SELECT t.throws(format('SELECT t.ob(%L, %L)', :aS1, 'SELECT count(*)::text FROM insight.self_report'), '42501', 'BFF 不能直接读自述表');
SELECT t.throws(format('SELECT t.ob(%L, %L)', :gA, 'SELECT count(*)::text FROM insight.guardian_report'), '42501', 'BFF 不能直接读家长观察表');
SELECT t.throws(format('SELECT t.ob(%L, %L)', :gA, 'SELECT count(*)::text FROM insight.exam_record'), '42501', 'BFF 不能直接读考试记录表');

-- ================= 考试记录 =================
SELECT t.throws(format('SELECT t.ob(%L, %L)', :aS1, format($$SELECT insight.submit_exam_record(%L, 'aya', 'midterm', '2026-11-10', 100, 95, 'top10', NULL, NULL)::text$$, :S1)),
                '23514', '学生不能录排名区间（只收家长录入，表约束）');
SELECT t.ok(t.ob(:aS1, format($$SELECT insight.submit_exam_record(%L, 'aya', 'midterm', '2026-11-10', 100, 95, NULL, '合成范围', '合成难度说明')::text$$, :S1)) IS NOT NULL
            AND t.ob(:gA, format($$SELECT insight.submit_exam_record(%L, 'aya', 'midterm', '2026-11-10', 100, 90, 'top30', NULL, NULL)::text$$, :S1)) IS NOT NULL,
            '学生与家长各录各的（同一场考试分数不同）');
SELECT t.ok((SELECT count(*) FROM insight.exam_record WHERE student_id = :S1 AND exam_date = '2026-11-10') = 2
            AND (SELECT array_agg(entered_by_role ORDER BY entered_by_role) FROM insight.exam_record WHERE student_id = :S1) = '{guardian,self}',
            '两条都在，各带录入方');
SELECT t.ok(jsonb_array_length(t.ob(:aS1, format('SELECT insight.onboarding_view(%L)::text', :S1))::jsonb -> 'exams') = 1
            AND position('rank_band' IN t.ob(:aS1, format('SELECT insight.onboarding_view(%L)::text', :S1))) = 0
            AND position('difficulty_note' IN t.ob(:aS1, format('SELECT insight.onboarding_view(%L)::text', :S1))) = 0,
            '学生只看到自己录的，且看不到排名区间与难度说明');
SELECT t.ok(jsonb_array_length(t.ob(:gA, format('SELECT insight.onboarding_view(%L)::text', :S1))::jsonb -> 'exams') = 2
            AND position('top30' IN t.ob(:gA, format('SELECT insight.onboarding_view(%L)::text', :S1))) > 0, '家长看到孩子全部考试记录（含排名区间）');
SELECT t.throws(format('SELECT t.ob(%L, %L)', :gA, format($$SELECT insight.submit_exam_record(%L, 'aya', 'final', '2026-12-10', 100, 101, NULL, NULL, NULL)::text$$, :S1)),
                '23514', '分数不能超过满分');
SELECT t.throws(format('SELECT t.ob(%L, %L)', :gA, format($$SELECT insight.submit_exam_record(%L, 'aya', 'final', '2026-12-10', 100, 80, 'rank_3', NULL, NULL)::text$$, :S1)),
                '23514', '排名只收五档，不收具体名次');
SELECT t.throws(format('SELECT t.ob(%L, %L)', :gB, format($$SELECT insight.submit_exam_record(%L, 'aya', 'final', '2026-12-10', 100, 80, NULL, NULL, NULL)::text$$, :S1)),
                '42501', '别家的家长不能录');

-- ================= 各科教材版本（platform） =================
SELECT t.ob(:aS1, format($$SELECT platform.set_subject_textbook(%L, 'aya', 'suke-2024')::text$$, :S1));
SELECT t.ob(:gA, format($$SELECT platform.set_subject_textbook(%L, 'zhixing', NULL)::text$$, :S1));
-- 第二户（gB 的孩子 S2）也留一行，供攻击面可见性矩阵逐户检查
SELECT t.ob(:gB, format($$SELECT platform.set_subject_textbook(%L, 'wenli', 'bubian-2024')::text$$, :S2));
SELECT t.ok((SELECT textbook_version = 'suke-2024' AND source_type = 'self' FROM platform.student_subject_textbook WHERE student_id = :S1 AND subject = 'aya')
            AND (SELECT textbook_version IS NULL AND source_type = 'guardian' FROM platform.student_subject_textbook WHERE student_id = :S1 AND subject = 'zhixing'),
            '学生与家长都能填各科教材版本；“不清楚”存 NULL；记下由谁填');
SELECT t.throws(format('SELECT t.ob(%L, %L)', :gB, format($$SELECT platform.set_subject_textbook(%L, 'aya', 'suke-2024')::text$$, :S1)),
                '42501', '别家的家长不能改教材版本');
SELECT t.throws(format('SELECT t.ob(%L, %L)', :aS1, format($$SELECT platform.set_subject_textbook(%L, 'aya', 'Bad Code')::text$$, :S1)),
                '22023', '教材版本编码不合格式被拒');
SELECT t.ok(t.ob(:gA, format('SELECT count(*)::text FROM platform.student_subject_textbook WHERE student_id = %L', :S1)) = '2'
            AND t.ob(:gB, format('SELECT count(*)::text FROM platform.student_subject_textbook WHERE student_id = %L', :S1)) = '0',
            'BFF 按档案授权读教材版本：本家看得到，别家看不到');

-- ================= 建档进度与统计 =================
SELECT t.ob(:aS1, format($$SELECT insight.record_onboarding_step(%L, 'onboarding', 'started')::text$$, :S1));
SELECT t.ob(:aS1, format($$SELECT insight.record_onboarding_step(%L, 'pain_points', 'skipped')::text$$, :S1));
SELECT t.ob(:aS1, format($$SELECT insight.record_onboarding_step(%L, 'onboarding', 'completed')::text$$, :S1));
SELECT t.ob(:gA, format($$SELECT insight.record_onboarding_step(%L, 'onboarding', 'started')::text$$, :S1));
SELECT t.throws(format('SELECT t.ob(%L, %L)', :gB, format($$SELECT insight.record_onboarding_step(%L, 'onboarding', 'started')::text$$, :S1)),
                '42501', '别家的家长不能记进度');
SELECT t.throws($$UPDATE insight.onboarding_progress SET step = 'x'$$, '42501', '建档进度只追加');
SELECT t.throws($$TRUNCATE insight.onboarding_progress$$, '42501', '建档进度不能清空');
SELECT t.throws(format('SELECT t.ob(%L, %L)', :aS1, format($$SELECT insight.record_onboarding_step(%L, 'Bad Step', 'started')::text$$, :S1)),
                '23514', '步骤名不合格式被拒');
SELECT t.ok((SELECT started = 1 AND completed = 1 AND completion_rate = 1 AND skipped_steps = '{"pain_points": 1}'
               FROM insight.onboarding_stats(now() - interval '1 hour', now() + interval '1 hour') WHERE respondent_role = 'self')
            AND (SELECT started = 1 AND completed = 0 AND median_seconds IS NULL
               FROM insight.onboarding_stats(now() - interval '1 hour', now() + interval '1 hour') WHERE respondent_role = 'guardian'),
            '统计：学生开始 1、完成 1，跳过步骤按步骤汇总；家长开始 1、未完成');
SELECT t.throws(format('SELECT t.ob(%L, %L)', :gA, 'SELECT count(*)::text FROM insight.onboarding_stats(now() - interval ''1 day'', now())'),
                '42501', 'BFF 不能看统计');
SET ROLE starbuddy_report;
SELECT t.ok((SELECT count(*) FROM insight.onboarding_stats(now() - interval '1 hour', now() + interval '1 hour')) = 2, '运营报表身份可以看聚合统计');
SELECT t.throws('SELECT count(*) FROM insight.onboarding_progress', '42501', '运营报表身份读不到进度明细');
SELECT t.throws('SELECT count(*) FROM insight.self_report', '42501', '运营报表身份读不到自述');
RESET ROLE;
SET ROLE starbuddy_insight;
SELECT t.ok((SELECT count(*) FROM insight.self_report) >= 3 AND (SELECT count(*) FROM insight.guardian_report) >= 1, '洞察服务读全部事实');
SELECT t.throws($$INSERT INTO insight.self_report (student_id, field_id, value, respondent_account_id) VALUES ('c1000000-0000-4000-8000-0000000000c1', 'through.g9.focus_subject', '{"subject": "aya"}', 'a1000000-0000-4000-8000-0000000000a1')$$,
                '42501', '洞察服务也不能直接写自述（只经函数，由本人写）');
RESET ROLE;

-- ================= 同一学生的两位家长：各看各的观察 =================
-- 临时把 gC 绑定到 S1（超级用户身份），写一条 gA 没写过的字段，再解除
INSERT INTO platform.guardian_student_link (guardian_account_id, student_id, granted_via)
VALUES ('cc000000-0000-4000-8000-0000000000cc', 'c1000000-0000-4000-8000-0000000000c1', 'invite');
SELECT t.ok(t.ob(:gC, format($$SELECT insight.submit_guardian_report(%L, 'outer.g8.cooperation_capacity', '{"level": "regular"}')::text$$, :S1)) IS NOT NULL,
            '第二位家长也能写观察');
SELECT t.ok((t.ob(:gA, format('SELECT insight.onboarding_view(%L)::text', :S1))::jsonb #> '{reports}') ? 'outer.g8.cooperation_capacity' = false
            AND (t.ob(:gC, format('SELECT insight.onboarding_view(%L)::text', :S1))::jsonb #> '{reports}') ? 'outer.g1.weekly_available_time' = false,
            '两位家长互相看不到对方写的观察');
UPDATE platform.guardian_student_link SET status = 'revoked'
 WHERE guardian_account_id = 'cc000000-0000-4000-8000-0000000000cc' AND student_id = 'c1000000-0000-4000-8000-0000000000c1';
