-- 0044 洞察服务的授权再核对 insight.verify_actor（specs/sie-02b-claims-first-report/spec.md“第二步补充”默认方案第 1 条）。
-- 全部合成数据（CLAUDE.md 规则 8），夹具自建，在一个事务里，结束回滚：
--   家庭 hV：家长 gV，学生 V1（账号 sV1）、V2（账号 sV2，兄妹）；家庭 hW：家长 gW，学生 W1（账号 sW1）；
--   gX：没有任何绑定的家长；sT：staff 账号。
\set hV '''ad440000-0000-4000-8000-0000000000d1'''
\set hW '''ad440000-0000-4000-8000-0000000000d2'''
\set gV '''ad440000-0000-4000-8000-0000000000a1'''
\set gW '''ad440000-0000-4000-8000-0000000000a2'''
\set gX '''ad440000-0000-4000-8000-0000000000a3'''
\set sT '''ad440000-0000-4000-8000-0000000000a4'''
\set sV1 '''ad440000-0000-4000-8000-0000000000b1'''
\set sV2 '''ad440000-0000-4000-8000-0000000000b2'''
\set sW1 '''ad440000-0000-4000-8000-0000000000b3'''
\set V1 '''ad440000-0000-4000-8000-0000000000c1'''
\set V2 '''ad440000-0000-4000-8000-0000000000c2'''
\set W1 '''ad440000-0000-4000-8000-0000000000c3'''
\set V9 '''ad440000-0000-4000-8000-0000000000c9'''

-- 以某个角色调用核对函数（SET LOCAL：出错时随子事务回滚）。用动态 SQL：plpgsql 的简单表达式在事务内缓存执行状态，
-- 函数的执行权限只在第一次初始化时检查，换角色后不会重查
CREATE FUNCTION t.va(p_role text, p_actor uuid, p_audience text, p_student uuid) RETURNS boolean LANGUAGE plpgsql AS $$
DECLARE v_prev text := current_user; v boolean;
BEGIN
  EXECUTE format('SET LOCAL ROLE %I', p_role);
  EXECUTE 'SELECT insight.verify_actor($1, $2, $3)' INTO v USING p_actor, p_audience, p_student;
  EXECUTE format('SET LOCAL ROLE %I', v_prev);
  RETURN v;
END $$;
-- 洞察服务身份调用
CREATE FUNCTION t.vi(p_actor uuid, p_audience text, p_student uuid) RETURNS boolean LANGUAGE sql AS $$
  SELECT t.va('starbuddy_insight', p_actor, p_audience, p_student) $$;

BEGIN;
INSERT INTO platform.account (id, kind) VALUES
  (:gV, 'guardian'), (:gW, 'guardian'), (:gX, 'guardian'), (:sT, 'staff'), (:sV1, 'student'), (:sV2, 'student'), (:sW1, 'student');
INSERT INTO platform.household (id, created_by_account) VALUES (:hV, :gV), (:hW, :gW);
INSERT INTO platform.student_profile (id, account_id, household_id, grade, region, textbook_version, birth_year_month) VALUES
  (:V1, :sV1, :hV, 8, 'nanjing', 'bubian-2024', '2013-03-01'),
  (:V2, :sV2, :hV, 7, 'nanjing', 'bubian-2024', '2014-03-01'),
  (:W1, :sW1, :hW, 8, 'nanjing', 'bubian-2024', '2013-05-01');
INSERT INTO platform.guardian_student_link (guardian_account_id, student_id, granted_via) VALUES
  (:gV, :V1, 'household_creator'), (:gV, :V2, 'household_creator'), (:gW, :W1, 'household_creator');

-- ================= 一、本人与已绑定家长 =================
SELECT t.ok(t.vi(:sV1, 'student', :V1), '授权再核对：学生本人（active 学生账号，就是该档案的账号）为真');
SELECT t.ok(t.vi(:gV, 'guardian', :V1) AND t.vi(:gV, 'guardian', :V2), '授权再核对：有效绑定的家长对每个已绑定的孩子为真');

-- ================= 二、兄妹 =================
SELECT t.ok(NOT t.vi(:sV1, 'student', :V2) AND NOT t.vi(:sV2, 'student', :V1),
            '授权再核对：兄妹之间以学生身份互看为假（同一家庭也只认本人档案）');
SELECT t.ok(NOT t.vi(:sV1, 'guardian', :V2), '授权再核对：学生账号不能以家长身份看兄妹');

-- ================= 三、另一户 =================
SELECT t.ok(NOT t.vi(:gW, 'guardian', :V1) AND NOT t.vi(:gV, 'guardian', :W1),
            '授权再核对：另一户的家长为假（两个方向）');
SELECT t.ok(NOT t.vi(:sW1, 'student', :V1) AND NOT t.vi(:sV1, 'student', :W1), '授权再核对：另一户的学生为假');

-- ================= 四、没有绑定、撤销绑定 =================
SELECT t.ok(NOT t.vi(:gX, 'guardian', :V1), '授权再核对：没有绑定的家长为假');
UPDATE platform.guardian_student_link SET status = 'revoked', revoked_at = now() WHERE guardian_account_id = :gV AND student_id = :V2;
SELECT t.ok(NOT t.vi(:gV, 'guardian', :V2) AND t.vi(:gV, 'guardian', :V1),
            '授权再核对：撤销绑定后该家长对这个孩子为假，对仍有效绑定的孩子不受影响');

-- ================= 五、停用账号、已删除档案、账号类型不符 =================
UPDATE platform.account SET status = 'disabled' WHERE id = :sV1;
SELECT t.ok(NOT t.vi(:sV1, 'student', :V1), '授权再核对：停用的学生账号为假');
-- 与 BFF is_linked_guardian 一致：家长的授权只看绑定与家长账号，不看学生账号状态
SELECT t.ok(t.vi(:gV, 'guardian', :V1), '授权再核对：学生账号停用但档案未删时，有效绑定的家长仍为真');
UPDATE platform.account SET status = 'active' WHERE id = :sV1;
UPDATE platform.account SET status = 'disabled' WHERE id = :gV;
SELECT t.ok(NOT t.vi(:gV, 'guardian', :V1), '授权再核对：停用的家长账号为假');
UPDATE platform.account SET status = 'active' WHERE id = :gV;
UPDATE platform.student_profile SET erased_at = now() WHERE id = :V1;
SELECT t.ok(NOT t.vi(:sV1, 'student', :V1) AND NOT t.vi(:gV, 'guardian', :V1), '授权再核对：已执行删除的档案（0035 erased_at）对本人与家长都为假');
UPDATE platform.student_profile SET erased_at = NULL WHERE id = :V1;
INSERT INTO platform.guardian_student_link (guardian_account_id, student_id, granted_via) VALUES (:sT, :V1, 'invite');
SELECT t.ok(NOT t.vi(:sT, 'guardian', :V1) AND NOT t.vi(:sT, 'student', :V1), '授权再核对：绑定指向非家长账号（staff）为假');
INSERT INTO platform.household (id, created_by_account) VALUES ('ad440000-0000-4000-8000-0000000000d9', :gX);
INSERT INTO platform.student_profile (id, account_id, household_id, grade, region, textbook_version, birth_year_month)
VALUES (:V9, :gX, 'ad440000-0000-4000-8000-0000000000d9', 8, 'nanjing', 'bubian-2024', '2013-03-01');
SELECT t.ok(NOT t.vi(:gX, 'student', :V9), '授权再核对：档案指向非学生账号时以学生身份为假');

-- ================= 六、受众不匹配、未知受众与空参数：一律 false（不是 NULL，也不报错） =================
SELECT t.ok(NOT t.vi(:sV1, 'guardian', :V1) AND NOT t.vi(:gV, 'student', :V1), '授权再核对：受众与身份不匹配为假（学生当家长、家长当学生）');
SELECT t.ok(NOT t.vi(:sV1, 'agent', :V1) AND NOT t.vi(:gV, 'agent', :V1) AND NOT t.vi(:sV1, 'Student', :V1)
            AND NOT t.vi(:gV, NULL, :V1), '授权再核对：未知受众（agent、大小写变形、空）为假，不返回 NULL');
SELECT t.ok(NOT t.vi(NULL, 'student', :V1) AND NOT t.vi(:sV1, 'student', NULL) AND NOT t.vi(NULL, 'guardian', NULL),
            '授权再核对：账号或学生为空为假，不返回 NULL');
SELECT t.ok(NOT t.vi(gen_random_uuid(), 'student', :V1) AND NOT t.vi(:sV1, 'student', gen_random_uuid())
            AND NOT t.vi(:gV, 'guardian', gen_random_uuid()),
            '授权再核对：不存在的账号或学生与无权同样为假（不报错、不暴露存在性）');

-- ================= 七、只有洞察服务能调用 =================
SELECT t.throws(format('SELECT t.va(%L, %L, ''student'', %L)', r, :sV1, :V1), '42501', format('授权再核对：%s 不能调用核对函数', r))
  FROM unnest(ARRAY['starbuddy_bff', 'starbuddy_learning', 'starbuddy_review', 'starbuddy_scheduler', 'starbuddy_report',
                    'starbuddy_eraser', 'starbuddy_kge', 'starbuddy_kge_read']) r;
SELECT t.ok((SELECT p.prosecdef AND p.provolatile = 's' AND p.prorettype = 'boolean'::regtype
                    AND pg_get_userbyid(p.proowner) = 'starbuddy_authz'
                    AND p.proconfig = ARRAY['search_path=pg_catalog, platform, pg_temp']
               FROM pg_proc p WHERE p.oid = 'insight.verify_actor(uuid, text, uuid)'::regprocedure)
            AND NOT has_function_privilege('public', 'insight.verify_actor(uuid, text, uuid)', 'EXECUTE')
            AND (SELECT array_agg(r ORDER BY r) FROM unnest(ARRAY['starbuddy_bff', 'starbuddy_learning', 'starbuddy_review', 'starbuddy_scheduler',
                   'starbuddy_insight', 'starbuddy_kge', 'starbuddy_kge_read', 'starbuddy_report', 'starbuddy_eraser']) r
                  WHERE has_function_privilege(r, 'insight.verify_actor(uuid, text, uuid)', 'EXECUTE')) = ARRAY['starbuddy_insight'],
            '授权再核对：函数 SECURITY DEFINER、属主 authz、search_path 固定、只返回布尔；PUBLIC 不能执行，应用角色里只有洞察服务能执行');
ROLLBACK;
