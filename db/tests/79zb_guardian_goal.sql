-- 0053 家长诉求（specs/family-goals-monthly-plan/spec.md 第 1 步，PLT-14）：存储、可见范围（学生不可见）、按绑定授权、保留历史、
-- 两位家长各存各的、字段规则（在读学科、学校只给初三且须已发布、补充说明长度与隐形字符）、写入量上限、删除与导出清单。
-- 全部合成数据（规则 8）：本文件自建一户（两位家长、初三与初二各一名学生）与另一户，不依赖前面测试对 S1、S2 的改动。
-- 以 BFF 身份、按账号调用（同 79d 的做法）。

CREATE FUNCTION t.gg(p_acct uuid, p_sql text) RETURNS text LANGUAGE plpgsql AS $$
DECLARE v_prev text := current_user; v_out text;
BEGIN
  PERFORM t.as_account(p_acct);
  SET LOCAL ROLE starbuddy_bff;
  EXECUTE p_sql INTO v_out;
  EXECUTE format('SET LOCAL ROLE %I', v_prev);
  PERFORM t.as_account(NULL);
  RETURN v_out;
END $$;
GRANT EXECUTE ON FUNCTION t.gg(uuid, text) TO PUBLIC;
-- 提交一版诉求的 SQL（关注学科、目标分、学校、补充说明）
CREATE FUNCTION t.gg_submit(p_student uuid, p_subjects text, p_targets text, p_school uuid, p_note text) RETURNS text LANGUAGE sql AS $$
  SELECT format('SELECT insight.submit_guardian_goal(%L, %L::platform.subject[], %L::jsonb, %L::uuid, %L)::text',
                p_student, p_subjects, p_targets, p_school, p_note) $$;
GRANT EXECUTE ON FUNCTION t.gg_submit(uuid, text, text, uuid, text) TO PUBLIC;

\set G1 '''9a530000-0000-4000-8000-000000000001'''
\set G2 '''9a530000-0000-4000-8000-000000000002'''
\set GO '''9a530000-0000-4000-8000-000000000003'''
\set GR '''9a530000-0000-4000-8000-000000000004'''
\set A9 '''9a530000-0000-4000-8000-000000000009'''
\set A8 '''9a530000-0000-4000-8000-000000000008'''
\set AO '''9a530000-0000-4000-8000-00000000000a'''
\set H1 '''9a530000-0000-4000-8000-0000000000a1'''
\set H2 '''9a530000-0000-4000-8000-0000000000a2'''
\set X9 '''9a530000-0000-4000-8000-000000000c09'''
\set X8 '''9a530000-0000-4000-8000-000000000c08'''
\set XO '''9a530000-0000-4000-8000-000000000c0a'''
\set SCH '''9a530000-0000-4000-8000-0000000005c1'''
\set SCHD '''9a530000-0000-4000-8000-0000000005c2'''
\set SCHC '''9a530000-0000-4000-8000-0000000005c3'''
\set SCHX '''9a530000-0000-4000-8000-0000000005c4'''
\set DOC '''9a530000-0000-4000-8000-0000000005d1'''
\set DOCX '''9a530000-0000-4000-8000-0000000005d2'''

-- ================= 夹具（超级用户，同 10 号夹具的写法） =================
-- 户一：家长 G1（创建者）与 G2（第二位家长），初三学生 X9、初二学生 X8；户二：家长 GO、学生 XO；GR：已解除绑定的家长
INSERT INTO platform.account (id, kind) VALUES (:G1, 'guardian'), (:G2, 'guardian'), (:GO, 'guardian'), (:GR, 'guardian'),
  (:A9, 'student'), (:A8, 'student'), (:AO, 'student');
INSERT INTO platform.household (id, created_by_account) VALUES (:H1, :G1), (:H2, :GO);
INSERT INTO platform.student_profile (id, account_id, household_id, grade, region, textbook_version, birth_year_month) VALUES
  (:X9, :A9, :H1, 9, 'nanjing', 'bubian-2024', '2012-02-01'),
  (:X8, :A8, :H1, 8, 'nanjing', 'bubian-2024', '2013-02-01'),
  (:XO, :AO, :H2, 9, 'nanjing', 'bubian-2024', '2012-03-01');
INSERT INTO platform.guardian_student_link (guardian_account_id, student_id, granted_via) VALUES
  (:G1, :X9, 'household_creator'), (:G1, :X8, 'household_creator'), (:G2, :X9, 'invite'), (:GO, :XO, 'household_creator'),
  (:GR, :X9, 'invite');
UPDATE platform.guardian_student_link SET status = 'revoked', revoked_at = now() WHERE guardian_account_id = :GR;
INSERT INTO platform.subject_enrollment (student_id, subject, status) VALUES
  (:X9, 'aya', 'active'), (:X9, 'wenli', 'active'), (:X9, 'zhixing', 'paused'), (:X8, 'aya', 'active'), (:XO, 'aya', 'active');
SELECT t.ok(platform.current_grade(exam_cohort, now()) = 9, '夹具：X9 当前是初三') FROM platform.student_profile WHERE id = :X9;
SELECT t.ok(platform.current_grade(exam_cohort, now()) = 8, '夹具：X8 当前是初二') FROM platform.student_profile WHERE id = :X8;
-- 招录库：南京（与学生同地区）的已发布学校、未发布学校、停办学校，另一城市的已发布学校（来源文件经两名 staff 签字发布，同 77 号）
INSERT INTO policy.source_domain (host, issuer, added_by) VALUES ('jyj.nanjing-synth.example', '合成教育局（南京编码）', 'staff-001');
SET ROLE starbuddy_kge;
INSERT INTO policy.source_document (id, city, issuer, doc_number, title, published_on, url, applies_from_cohort, last_verified_on) VALUES
  (:DOC, 'nanjing', '合成教育局', '合成〔2026〕53号', '合成招生学校名单（非真实数据）', '2026-05-01', 'https://jyj.nanjing-synth.example/doc/53', 2026, '2026-09-01');
RESET ROLE;
SELECT t.ok(t.pol_sign2('source_document', :DOC) = 'published', '夹具：南京编码的合成来源文件经两名 staff 签字发布');
SET ROLE starbuddy_kge;
INSERT INTO policy.school (id, city, code, public_name, level_code, source_document_id) VALUES
  (:SCH, 'nanjing', 'synth-goal-1', '合成高中甲（非真实）', 'level_a', :DOC),
  (:SCHD, 'nanjing', 'synth-goal-2', '合成高中乙（未发布）', 'level_a', :DOC),
  (:SCHC, 'nanjing', 'synth-goal-3', '合成高中丙（停办）', 'level_a', :DOC),
  (:SCHX, 'synthcity', 'synth-goal-4', '合成外市高中', 'level_a', 'a2000000-0000-4000-8000-0000000000d2');
RESET ROLE;
SELECT t.ok(t.pol_sign2('school', :SCH) = 'published' AND t.pol_sign2('school', :SCHC) = 'published' AND t.pol_sign2('school', :SCHX) = 'published',
            '夹具：三所合成学校经签字发布（乙保持草稿）');
SET ROLE starbuddy_kge;
UPDATE policy.school SET status = 'closed' WHERE id = :SCHC;
RESET ROLE;

-- ================= 一、按绑定授权：只有有效绑定家长能写 =================
SELECT t.ok((t.gg(:G1, t.gg_submit(:X9, '{aya}', '[{"subject": "aya", "full_score": 120, "target_score": 105}]', :SCH, '合成说明：想先补函数'))::jsonb ->> 'version') = '1',
            '已绑定家长可以提交诉求（第 1 版）');
SELECT t.throws(format('SELECT t.gg(%L, %L)', :GO, t.gg_submit(:X9, '{}', '[]', NULL, NULL)), '42501', '别家的家长不能写');
SELECT t.throws(format('SELECT t.gg(%L, %L)', :GR, t.gg_submit(:X9, '{}', '[]', NULL, NULL)), '42501', '已解除绑定的家长不能写');
SELECT t.throws(format('SELECT t.gg(%L, %L)', :A9, t.gg_submit(:X9, '{}', '[]', NULL, NULL)), '42501', '学生本人不能写家长诉求');
SELECT t.throws(format('SELECT t.gg(%L, %L)', '00000000-0000-4000-8000-000000000000', t.gg_submit(:X9, '{}', '[]', NULL, NULL)), '42501',
                '不存在的账号不能写');
SELECT t.throws(format('SELECT t.gg(%L, %L)', :G1, t.gg_submit(gen_random_uuid(), '{}', '[]', NULL, NULL)), '42501',
                '不存在的学生与无权同一个错误');
-- 授权先于取值核对：别家家长带非法取值也只得到 42501（不泄露学生的在读学科、年级）
SELECT t.throws(format('SELECT t.gg(%L, %L)', :GO, t.gg_submit(:X9, '{echo}', '[]', :SCHD, repeat('x', 300))), '42501',
                '别家家长带非法取值：先按授权拒绝');

-- ================= 二、可见范围：家长只读自己写的；学生、别家、未绑定一律 NULL =================
SELECT t.ok((t.gg(:G1, format('SELECT insight.guardian_goal_view(%L)::text', :X9))::jsonb #>> '{current,note}') = '合成说明：想先补函数'
            AND (t.gg(:G1, format('SELECT insight.guardian_goal_view(%L)::text', :X9))::jsonb #>> '{current,school,public_name}') = '合成高中甲（非真实）'
            AND (t.gg(:G1, format('SELECT insight.guardian_goal_view(%L)::text', :X9))::jsonb #>> '{current,source_type}') = 'guardian'
            AND (t.gg(:G1, format('SELECT insight.guardian_goal_view(%L)::text', :X9))::jsonb ->> 'can_set_school') = 'true'
            AND (t.gg(:G1, format('SELECT insight.guardian_goal_view(%L)::text', :X9))::jsonb -> 'enrolled_subjects') = '["wenli", "aya"]'::jsonb,
            '家长读到自己写的当前诉求（来源 guardian）、学校名称、能否设学校与在读学科（暂停的不算）');
SELECT t.ok((t.gg(:G1, format('SELECT insight.guardian_goal_view(%L)::text', :X9))::jsonb -> 'school_options')
              = jsonb_build_array(jsonb_build_object('id', :SCH::uuid, 'public_name', '合成高中甲（非真实）'))
            AND (t.gg(:G1, format('SELECT insight.guardian_goal_view(%L)::text', :X8))::jsonb -> 'school_options') = '[]'::jsonb,
            '可选学校只给初三，且只有已发布、在办、同地区的（草稿、停办、外地区都不在里面）');
-- 当前版里学校的名称与写入、可选名单同一口径：学校之后停办，名称为 null（只留 ID），可选名单里也没有它；恢复在办后名称回来
SET ROLE starbuddy_kge;
UPDATE policy.school SET status = 'closed' WHERE id = :SCH;
RESET ROLE;
SELECT t.ok((t.gg(:G1, format('SELECT insight.guardian_goal_view(%L)::text', :X9))::jsonb #> '{current,school}')
              = jsonb_build_object('id', :SCH::uuid, 'public_name', NULL)
            AND (t.gg(:G1, format('SELECT insight.guardian_goal_view(%L)::text', :X9))::jsonb -> 'school_options') = '[]'::jsonb,
            '当前版的学校停办后：名称不再显示（与写入同一口径），可选名单里也没有');
SET ROLE starbuddy_kge;
UPDATE policy.school SET status = 'active' WHERE id = :SCH;
RESET ROLE;
SELECT t.ok((t.gg(:G1, format('SELECT insight.guardian_goal_view(%L)::text', :X9))::jsonb #>> '{current,school,public_name}') = '合成高中甲（非真实）',
            '恢复在办后名称回来');
SELECT t.ok(t.gg(:A9, format('SELECT insight.guardian_goal_view(%L)::text', :X9)) IS NULL
            AND t.gg(:A8, format('SELECT insight.guardian_goal_view(%L)::text', :X9)) IS NULL
            AND t.gg(:GO, format('SELECT insight.guardian_goal_view(%L)::text', :X9)) IS NULL
            AND t.gg(:GR, format('SELECT insight.guardian_goal_view(%L)::text', :X9)) IS NULL
            AND t.gg('00000000-0000-4000-8000-000000000000', format('SELECT insight.guardian_goal_view(%L)::text', :X9)) IS NULL
            AND t.gg(:G1, format('SELECT insight.guardian_goal_view(%L)::text', gen_random_uuid())) IS NULL,
            '学生本人、兄弟姐妹、别家家长、已解除绑定的家长、不存在的账号读不到（与不存在的学生同为 NULL）');
-- 表权限：任何应用角色都没有表权限（只有函数属主 authz；删除执行身份只读学生键）
SELECT t.ok(NOT EXISTS (SELECT 1 FROM unnest(ARRAY['starbuddy_bff', 'starbuddy_learning', 'starbuddy_insight', 'starbuddy_review',
                                                   'starbuddy_scheduler', 'starbuddy_kge', 'starbuddy_kge_read', 'starbuddy_report']) r
                         WHERE has_any_column_privilege(r, 'insight.guardian_goal', 'SELECT')
                            OR has_any_column_privilege(r, 'insight.guardian_goal', 'INSERT')
                            OR has_any_column_privilege(r, 'insight.guardian_goal', 'UPDATE')
                            OR has_table_privilege(r, 'insight.guardian_goal', 'DELETE')
                            OR has_table_privilege(r, 'insight.guardian_goal', 'TRUNCATE'))
            AND NOT has_column_privilege('starbuddy_eraser', 'insight.guardian_goal', 'note', 'SELECT')
            AND NOT has_column_privilege('starbuddy_eraser', 'insight.guardian_goal', 'target_scores', 'SELECT'),
            'BFF、学习服务、洞察服务、复核（教师）、定时任务、知识、报表角色对诉求表都没有任何权限；删除执行身份读不到内容列');
SELECT t.throws($$SELECT t.gg('9a530000-0000-4000-8000-000000000001', 'SELECT count(*)::text FROM insight.guardian_goal')$$, '42501',
                'BFF 以家长身份也不能直读诉求表（只经函数）');
DO $$
DECLARE r text;
BEGIN
  FOREACH r IN ARRAY ARRAY['starbuddy_learning', 'starbuddy_insight', 'starbuddy_review', 'starbuddy_scheduler', 'starbuddy_kge', 'starbuddy_report'] LOOP
    PERFORM t.throws(format('SET LOCAL ROLE %I; SELECT count(*) FROM insight.guardian_goal', r), '42501', format('%s 直读诉求表被拒', r));
    RESET ROLE;
  END LOOP;
END $$;
SELECT t.ok((SELECT array_agg(r ORDER BY r) FROM unnest(ARRAY['starbuddy_bff', 'starbuddy_learning', 'starbuddy_insight', 'starbuddy_review',
                                                              'starbuddy_scheduler', 'starbuddy_kge', 'starbuddy_report', 'starbuddy_authz']) r
              WHERE has_function_privilege(r, 'insight.guardian_goal_view(uuid)', 'EXECUTE')
                 OR has_function_privilege(r, 'insight.submit_guardian_goal(uuid, platform.subject[], jsonb, uuid, text)', 'EXECUTE'))
            = '{starbuddy_authz,starbuddy_bff}',
            '读写函数只授 BFF（学习服务、洞察服务暂不授权，第 2 步再定）');
SELECT t.ok(NOT EXISTS (SELECT 1 FROM pg_policies WHERE schemaname = 'insight' AND tablename = 'guardian_goal')
            AND (SELECT relrowsecurity AND relforcerowsecurity FROM pg_class WHERE oid = 'insight.guardian_goal'::regclass),
            '诉求表 ENABLE + FORCE RLS 且没有任何策略');

-- ================= 三、保留历史；两位家长各存各的 =================
SELECT t.gg(:G1, t.gg_submit(:X9, '{aya,wenli}', '[{"subject": "aya", "full_score": 120, "target_score": 110}]', NULL, NULL));
SELECT t.ok((SELECT count(*) FROM insight.guardian_goal WHERE student_id = :X9 AND guardian_account_id = :G1) = 2
            AND (SELECT version FROM insight.guardian_goal WHERE student_id = :X9 AND guardian_account_id = :G1 AND superseded_at IS NULL) = 2
            AND (SELECT note = '合成说明：想先补函数' AND school_id = :SCH FROM insight.guardian_goal
                  WHERE student_id = :X9 AND guardian_account_id = :G1 AND version = 1 AND superseded_at IS NOT NULL),
            '更正是新的一版：旧版原样保留（标为已替换），新版为当前');
SELECT t.ok(jsonb_array_length(t.gg(:G1, format('SELECT insight.guardian_goal_view(%L)::text', :X9))::jsonb -> 'history') = 1
            AND (t.gg(:G1, format('SELECT insight.guardian_goal_view(%L)::text', :X9))::jsonb #> '{current,school}') = 'null'::jsonb,
            '家长能看到自己的历史版本；当前版没有学校');
SELECT t.gg(:G2, t.gg_submit(:X9, '{wenli}', '[{"subject": "wenli", "full_score": 120, "target_score": 90}]', NULL, '合成说明：第二位家长'));
SELECT t.ok((SELECT count(*) FROM insight.guardian_goal WHERE student_id = :X9 AND superseded_at IS NULL) = 2
            AND (SELECT version FROM insight.guardian_goal WHERE student_id = :X9 AND guardian_account_id = :G2) = 1,
            '两位家长说法不同都存（各自的当前版，不合并、不覆盖对方）');
SELECT t.ok(position('第二位家长' IN t.gg(:G1, format('SELECT insight.guardian_goal_view(%L)::text', :X9))) = 0
            AND position('想先补函数' IN t.gg(:G2, format('SELECT insight.guardian_goal_view(%L)::text', :X9))) = 0,
            '每位家长只读自己写的诉求（看不到另一位家长的）');
SELECT t.ok((SELECT bool_and(source_type = 'guardian') FROM insight.guardian_goal), '每行都带来源类型 guardian（家长自述）');
SELECT t.throws(format($$UPDATE insight.guardian_goal SET note = '改写' WHERE student_id = %L AND version = 1$$, :X9), '42501',
                '历史版本不能改写（只追加）');
SELECT t.throws(format($$DELETE FROM insight.guardian_goal WHERE student_id = %L$$, :X9), '42501', '诉求不能删除（只随删除请求）');
SELECT t.throws('TRUNCATE insight.guardian_goal', '42501', '诉求表不能清空');

-- ================= 四、字段规则 =================
SELECT t.throws(format('SELECT t.gg(%L, %L)', :G1, t.gg_submit(:X9, '{aya,wenli,zhixing}', '[]', NULL, NULL)), '22023', '关注学科最多 2 科');
SELECT t.throws(format('SELECT t.gg(%L, %L)', :G1, t.gg_submit(:X9, '{aya,aya}', '[]', NULL, NULL)), '22023', '关注学科不能重复');
SELECT t.throws_like(format('SELECT t.gg(%L, %L)', :G1, t.gg_submit(:X9, '{zhixing}', '[]', NULL, NULL)), '22023', 'SUBJECT_NOT_ENROLLED',
                     '关注学科必须在读（已暂停的不算）');
SELECT t.throws_like(format('SELECT t.gg(%L, %L)', :G1, t.gg_submit(:X9, '{echo}', '[]', NULL, NULL)), '22023', 'SUBJECT_NOT_ENROLLED',
                     '关注学科必须在读（没选的不算）');
SELECT t.throws_like(format('SELECT t.gg(%L, %L)', :G1, t.gg_submit(:X9, '{}', '[{"subject": "zhixing", "full_score": 100, "target_score": 80}]', NULL, NULL)),
                     '22023', 'SUBJECT_NOT_ENROLLED', '目标分的学科同样必须在读');
SELECT t.ok(insight.valid_goal_targets('[]') AND insight.valid_goal_targets('[{"subject": "aya", "full_score": 120, "target_score": 0}]')
            AND insight.valid_goal_targets('[{"subject": "aya", "full_score": 150, "target_score": 150}]')
            AND insight.valid_goal_targets('[{"subject": "aya", "full_score": 100, "target_score": 95.5}]')
            AND NOT insight.valid_goal_targets('[{"subject": "aya", "full_score": 100, "target_score": 101}]')
            AND NOT insight.valid_goal_targets('[{"subject": "aya", "full_score": 100, "target_score": -1}]')
            AND NOT insight.valid_goal_targets('[{"subject": "aya", "full_score": 0, "target_score": 0}]')
            AND NOT insight.valid_goal_targets('[{"subject": "aya", "full_score": 301, "target_score": 1}]')
            AND NOT insight.valid_goal_targets('[{"subject": "aya", "full_score": 100, "target_score": 95.25}]')
            AND NOT insight.valid_goal_targets('[{"subject": "aya", "full_score": 100, "target_score": "95"}]')
            AND NOT insight.valid_goal_targets('[{"subject": "aya", "full_score": 100}]')
            AND NOT insight.valid_goal_targets('[{"subject": "aya", "full_score": 100, "target_score": 90, "rank": 1}]')
            AND NOT insight.valid_goal_targets('[{"subject": "math", "full_score": 100, "target_score": 90}]')
            AND NOT insight.valid_goal_targets('[{"subject": "aya", "full_score": 100, "target_score": 90}, {"subject": "aya", "full_score": 100, "target_score": 80}]')
            AND NOT insight.valid_goal_targets('{"subject": "aya"}'),
            '目标分：0 ≤ 目标 ≤ 满分、0 < 满分 ≤ 300、最多一位小数、恰好三个键、学科码合法且不重复');
-- 不依赖短路：标量、非对象元素、非数字或非字符串的取值一律返回 false，不抛错（逐个单独调用，任何一个抛错本断言就失败）
SELECT t.ok(bool_and(insight.valid_goal_targets(v::jsonb) IS FALSE), '目标分：非数组、非对象元素、非数字分数一律 false，不抛错')
  FROM unnest(ARRAY['5', '"x"', 'null', 'true', '{}', '[1]', '["aya"]', '[null]', '[[]]',
                    '[{"subject": "aya", "full_score": "abc", "target_score": 1}]',
                    '[{"subject": "aya", "full_score": 100, "target_score": "x"}]',
                    '[{"subject": "aya", "full_score": {"v": 1}, "target_score": 1}]',
                    '[{"subject": 1, "full_score": 100, "target_score": 90}]',
                    '[{"subject": null, "full_score": 100, "target_score": 90}]',
                    '[{"subject": "aya", "full_score": 100, "target_score": 90}, 7]']) v;
SELECT t.ok(insight.valid_goal_targets(NULL) IS FALSE, '目标分：NULL 返回 false');
SELECT t.throws(format('SELECT t.gg(%L, %L)', :G1, t.gg_submit(:X9, '{}', '[{"subject": "aya", "full_score": "abc", "target_score": 1}]', NULL, NULL)),
                '22023', '目标分非数字：写入函数以取值不合要求拒绝（不是类型转换错误）');
-- 学校：只有初三；已发布、在办、同地区
SELECT t.throws_like(format('SELECT t.gg(%L, %L)', :G1, t.gg_submit(:X8, '{}', '[]', :SCH, NULL)), '55000', 'SCHOOL_NOT_OPEN_FOR_GRADE',
                     '初二不能设关注学校（按 platform.current_grade 推算）');
SELECT t.ok((t.gg(:G1, format('SELECT insight.guardian_goal_view(%L)::text', :X8))::jsonb ->> 'can_set_school') = 'false',
            '初二的读视图标明不能设学校');
SELECT t.ok((t.gg(:G1, t.gg_submit(:X8, '{aya}', '[{"subject": "aya", "full_score": 100, "target_score": 85}]', NULL, NULL))::jsonb ->> 'version') = '1',
            '初二可以设关注学科与目标分（不设学校）');
SELECT t.throws_like(format('SELECT t.gg(%L, %L)', :G1, t.gg_submit(:X9, '{}', '[]', :SCHD, NULL)), '22023', 'SCHOOL_NOT_AVAILABLE', '未发布的学校不能引用');
SELECT t.throws_like(format('SELECT t.gg(%L, %L)', :G1, t.gg_submit(:X9, '{}', '[]', :SCHC, NULL)), '22023', 'SCHOOL_NOT_AVAILABLE', '停办的学校不能引用');
SELECT t.throws_like(format('SELECT t.gg(%L, %L)', :G1, t.gg_submit(:X9, '{}', '[]', :SCHX, NULL)), '22023', 'SCHOOL_NOT_AVAILABLE', '外地区的学校不能引用');
SELECT t.throws_like(format('SELECT t.gg(%L, %L)', :G1, t.gg_submit(:X9, '{}', '[]', gen_random_uuid(), NULL)), '22023', 'SCHOOL_NOT_AVAILABLE',
                     '不存在的学校与未发布同一个错误');
-- 补充说明：1–200 字，拒绝隐形字符（Cc、Cf、Zl、Zp）
SELECT t.ok(insight.valid_goal_note(NULL) AND insight.valid_goal_note(repeat('说', 200)) AND NOT insight.valid_goal_note(repeat('说', 201))
            AND NOT insight.valid_goal_note('') AND NOT insight.valid_goal_note(E'换行\n') AND NOT insight.valid_goal_note('零宽' || chr(8203))
            AND NOT insight.valid_goal_note('方向' || chr(8238) || '控制') AND NOT insight.valid_goal_note('软连字' || chr(173))
            AND NOT insight.valid_goal_note('字' || chr(65279)) AND NOT insight.valid_goal_note('行' || chr(8232)),
            '补充说明：1–200 字，换行、零宽、双向控制、软连字符、BOM、行分隔符一律拒绝');
SELECT t.throws(format('SELECT t.gg(%L, %L)', :G1, t.gg_submit(:X9, '{}', '[]', NULL, repeat('说', 201))), '22023', '补充说明超长被拒');
SELECT t.throws(format('SELECT t.gg(%L, %L)', :G1, t.gg_submit(:X9, '{}', '[]', NULL, '零宽' || chr(8203))), '22023', '补充说明含隐形字符被拒');
-- 表约束兜底（迁移或 DBA 直接写也拦）
SELECT t.throws(format($$INSERT INTO insight.guardian_goal (student_id, guardian_account_id, version, note) VALUES (%L, %L, 99, %L)$$,
                       :X9, :G1, '零宽' || chr(8203)), '23514', '表约束：补充说明含隐形字符被拒');
SELECT t.throws(format($$INSERT INTO insight.guardian_goal (student_id, guardian_account_id, version, focus_subjects) VALUES (%L, %L, 99, '{aya,wenli,zhixing}')$$,
                       :X9, :G1), '23514', '表约束：关注学科最多 2 科');
SELECT t.throws(format($$INSERT INTO insight.guardian_goal (student_id, guardian_account_id, version, source_type) VALUES (%L, %L, 99, 'self')$$,
                       :X9, :G1), '23514', '表约束：来源只能是 guardian');
SELECT t.throws(format($$INSERT INTO insight.guardian_goal (student_id, guardian_account_id, version) VALUES (%L, %L, 77)$$, :X9, :G1), '23505',
                '表约束：同一家长对同一孩子只有一条当前诉求');

-- ================= 五、年级不在 7–9 时整条拒绝；写入量上限 =================
UPDATE platform.student_profile SET exam_cohort = exam_cohort - 1, exam_cohort_source = 'staff_correction' WHERE id = :XO;  -- 模拟已毕业（超级用户）
SELECT t.throws_like(format('SELECT t.gg(%L, %L)', :GO, t.gg_submit(:XO, '{}', '[]', NULL, NULL)), '55000', 'GRADE_OUT_OF_RANGE',
                     '已毕业（当前年级不在 7–9）的学生不能设诉求');
UPDATE platform.student_profile SET exam_cohort = exam_cohort + 1, exam_cohort_source = 'derived' WHERE id = :XO;
DO $$
DECLARE i integer;
BEGIN
  FOR i IN 1 .. 19 LOOP
    PERFORM t.gg('9a530000-0000-4000-8000-000000000003', t.gg_submit('9a530000-0000-4000-8000-000000000c0a', '{aya}', '[]', NULL, NULL));
  END LOOP;
END $$;
SELECT t.ok((SELECT count(*) FROM insight.guardian_goal WHERE student_id = :XO) = 19
            AND (SELECT max(version) FROM insight.guardian_goal WHERE student_id = :XO) = 19, '连续 19 次提交：版本号递增到 19');
SELECT t.gg(:GO, t.gg_submit(:XO, '{aya}', '[]', NULL, NULL));
SELECT t.throws(format('SELECT t.gg(%L, %L)', :GO, t.gg_submit(:XO, '{aya}', '[]', NULL, NULL)), '54000', '24 小时内第 21 版被拒（写入量上限 20）');
SELECT t.ok((t.gg(:G1, t.gg_submit(:X8, '{}', '[]', NULL, NULL))::jsonb ->> 'version') = '2',
            '写入量按（家长，孩子）计数：别家达到上限不影响本家');

-- ================= 六、个人层表清单与删除 =================
SELECT t.ok((SELECT strategy = 'delete' AND erase_scope = 'facts' AND export_topic = 'guardian_goals'
                    AND export_filter = 'guardian_account_id = platform.current_account_id()' AND NOT 'guardian_account_id' = ANY (export_columns)
               FROM platform.personal_table_registry WHERE nspname = 'insight' AND relname = 'guardian_goal'),
            '诉求表登记进个人层表清单：随删除请求删除（facts），导出只导家长本人写的、不导账号列');
-- 导出行为（0035 platform.export_student_data）：X9 有 G1 与 G2 各自写的诉求；G1 导出只拿到自己写的行，看不到 G2 的补充说明
SELECT t.gg(:G1, format('SELECT platform.export_student_data(%L)::text', :X9)) AS gg_export \gset
SELECT t.ok(jsonb_array_length(:'gg_export'::jsonb -> 'topics' -> 'guardian_goals')
              = (SELECT count(*) FROM insight.guardian_goal WHERE student_id = :X9 AND guardian_account_id = :G1)
            AND position('合成说明：想先补函数' IN (:'gg_export'::jsonb -> 'topics' -> 'guardian_goals')::text) > 0
            AND position('合成说明：第二位家长' IN (:'gg_export'::jsonb -> 'topics' -> 'guardian_goals')::text) = 0
            AND NOT EXISTS (SELECT 1 FROM jsonb_array_elements(:'gg_export'::jsonb -> 'topics' -> 'guardian_goals') r WHERE r ? 'guardian_account_id'),
            '导出：G1 只拿到自己写的诉求（含历史版），没有 G2 的补充说明，也不导出账号列');
SELECT t.ok(has_table_privilege('starbuddy_eraser', 'insight.guardian_goal', 'DELETE')
            AND has_column_privilege('starbuddy_eraser', 'insight.guardian_goal', 'student_id', 'SELECT'),
            '删除执行身份能删诉求表、只读学生键');
