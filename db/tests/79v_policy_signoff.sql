-- 0040–0041 考情政策线签字收口（specs/release-gate-sprint4/spec.md 第 8 节、冲刺 4 验收“考情（DB 测试，合成城市）”）：
-- 方案、来源文件、招录行、学校——非 staff 签字被拒；同一账号签两次只算一人；kge 角色直接改为已发布被拒；policy.publish_scheme 只能由签字身份调用。
-- 合成数据（规则 8；docs/design/policy/data-entry-rules.md 第 6 节）：合成城市 synthcity 的 2030 届合成方案、合成学校、合成分数，非真实数据。
-- staff：sf1、sf2（10 号夹具）、sf3（本文件新建）、已停用的 staff（79b）；家长 gA（10 号夹具）。
-- 用 76 号的 t.as_staff，77 号的 t.pol_sign / t.pol_publish（官方来源主机名 jyj.synthcity.example 由 77 号登记）。
\set sf1 '''5f000000-0000-4000-8000-00000000005f'''
\set sf2 '''f5000000-0000-4000-8000-0000000000f5'''
\set sf3 '''d7000000-0000-4000-8000-0000000000a3'''
\set gA '''a0000000-0000-4000-8000-00000000000a'''
\set pd '''d7100000-0000-4000-8000-0000000000d1'''
\set ps '''d7200000-0000-4000-8000-0000000000e1'''
\set ph '''d7300000-0000-4000-8000-0000000000f1'''
\set pl '''d7400000-0000-4000-8000-0000000000b1'''
RESET ROLE;
SELECT t.as_account(NULL);
INSERT INTO platform.account (id, kind) VALUES (:sf3, 'staff');  -- 第三名合成教研（驳回、发布人）

-- ---------- 辅助 ----------
-- 签字语句（方案用 sign_scheme，其余用 sign_record）；p_digest 为空时回传当前摘要
CREATE FUNCTION t.pol_sign_sql(p_type text, p_id uuid, p_decision text DEFAULT 'approved', p_digest text DEFAULT NULL) RETURNS text
  LANGUAGE sql AS $$
  SELECT CASE WHEN p_type = 'exam_scheme'
    THEN format('SELECT policy.sign_scheme(%L, %L, %s)::text', p_id, p_decision,
                coalesce(quote_literal(p_digest), format('policy.object_digest(''exam_scheme'', %L)', p_id)))
    ELSE format('SELECT policy.sign_record(%L, %L, %L, %s)', p_type, p_id, p_decision,
                coalesce(quote_literal(p_digest), format('policy.object_digest(%L, %L)', p_type, p_id))) END $$;
-- 以某个应用角色、某个账号执行一条返回单值的语句
CREATE FUNCTION t.pol_as(p_role text, p_acct uuid, p_sql text) RETURNS text LANGUAGE plpgsql AS $$
DECLARE v_prev text := current_user; v_out text;
BEGIN
  PERFORM t.as_account(p_acct);
  EXECUTE format('SET LOCAL ROLE %I', p_role);
  EXECUTE p_sql INTO v_out;
  EXECUTE format('SET LOCAL ROLE %I', v_prev);
  PERFORM t.as_account(NULL);
  RETURN v_out;
END $$;
-- 写入方（kge）带 staff 账号签字的语句（摘要先算好，只测签字函数本身的授权）；断言在各段里逐条写出完整名字（攻击面清单按全名引用）
CREATE FUNCTION t.pol_kge_sign_sql(p_type text, p_id uuid) RETURNS text LANGUAGE sql AS $$
  SELECT format('SELECT t.pol_as(''starbuddy_kge'', %L, %L)', '5f000000-0000-4000-8000-00000000005f',
                t.pol_sign_sql(p_type, p_id, 'approved', policy.object_digest(p_type, p_id))) $$;
-- 每类对象都要过的签字身份检查：家长、未设账号、已停用的 staff、学习服务（即使带 staff 账号）、回传摘要不符
CREATE FUNCTION t.pol_refusals(p_type text, p_id uuid, p_label text) RETURNS void LANGUAGE plpgsql AS $$
BEGIN
  PERFORM t.throws(format('SELECT t.as_staff(%L, %L)', 'a0000000-0000-4000-8000-00000000000a', t.pol_sign_sql(p_type, p_id)),
                   '42501', p_label || '：家长账号不能签字');
  PERFORM t.throws(format('SELECT t.as_staff(NULL, %L)', t.pol_sign_sql(p_type, p_id)), '42501', p_label || '：未设账号不能签字');
  PERFORM t.throws(format('SELECT t.as_staff(%L, %L)', '5d000000-0000-4000-8000-00000000005d', t.pol_sign_sql(p_type, p_id)),
                   '42501', p_label || '：已停用的 staff 不能签字');
  -- 摘要在这里先算好（学习服务没有摘要函数的执行权），只测签字函数本身的授权
  PERFORM t.throws(format('SELECT t.pol_as(''starbuddy_learning'', %L, %L)', '5f000000-0000-4000-8000-00000000005f',
                          t.pol_sign_sql(p_type, p_id, 'approved', policy.object_digest(p_type, p_id))),
                   '42501', p_label || '：学习服务即使带 staff 账号也不能签字');
  PERFORM t.throws_like(format('SELECT t.as_staff(%L, %L)', '5f000000-0000-4000-8000-00000000005f',
                               t.pol_sign_sql(p_type, p_id, 'approved', repeat('0', 64))),
                        '23514', '摘要不符', p_label || '：回传的摘要与当前内容不符被拒（看到的就是签的）');
END $$;
-- 同一账号签两次只算一人：第二次被拒，有效批准人数仍为 1，对象仍是草稿
CREATE FUNCTION t.pol_twice(p_type text, p_id uuid, p_label text) RETURNS void LANGUAGE plpgsql AS $$
BEGIN
  PERFORM t.ok(t.as_staff('5f000000-0000-4000-8000-00000000005f', t.pol_sign_sql(p_type, p_id)) IN ('draft', '1'),
               p_label || '：第一名 staff 批准后仍是草稿');
  PERFORM t.throws_like(format('SELECT t.as_staff(%L, %L)', '5f000000-0000-4000-8000-00000000005f', t.pol_sign_sql(p_type, p_id)),
                        '23505', '只能签一次', p_label || '：同一账号对同一内容签第二次被拒');
  PERFORM t.ok(policy.approval_count(p_type, p_id) = 1, p_label || '：同一账号签两次只算一人');
END $$;
GRANT EXECUTE ON FUNCTION t.pol_sign_sql(text, uuid, text, text), t.pol_as(text, uuid, text), t.pol_refusals(text, uuid, text),
  t.pol_twice(text, uuid, text), t.pol_kge_sign_sql(text, uuid) TO PUBLIC;

-- ================= 一、来源文件 =================
SET ROLE starbuddy_kge;
SELECT t.throws_like($$INSERT INTO policy.source_document (city, issuer, doc_number, title, published_on, url, applies_from_cohort, last_verified_on, status)
                       VALUES ('synthcity', '合成教育局', '合成〔2029〕9号', '合成文件', '2029-03-01', 'https://jyj.synthcity.example/doc/9', 2030, '2029-09-01', 'published')$$,
                     '23514', '只能以草稿新建', '考情·来源文件：kge 角色不能直接以已发布新建');
SELECT t.throws_like($$INSERT INTO policy.source_document (city, issuer, doc_number, title, published_on, url, applies_from_cohort, last_verified_on, reviewed_at)
                       VALUES ('synthcity', '合成教育局', '合成〔2029〕9号', '合成文件', '2029-03-01', 'https://jyj.synthcity.example/doc/9', 2030, '2029-09-01', now())$$,
                     '23514', '只能以草稿新建', '考情·来源文件：新建时不能自带复核时间');
INSERT INTO policy.source_document (id, city, issuer, doc_number, title, published_on, url, applies_from_cohort, last_verified_on, reviewers) VALUES
  (:pd, 'synthcity', '合成教育局', '合成〔2029〕1号', '合成 2030 届中考方案（非真实数据）', '2029-03-01', 'https://jyj.synthcity.example/doc/2030', 2030,
   '2029-09-01', '{staff-001,staff-002}');
SELECT t.throws($$UPDATE policy.source_document SET reviewed_at = now() WHERE id = 'd7100000-0000-4000-8000-0000000000d1'$$,
                '42501', '考情·来源文件：kge 角色不能写复核时间');
SELECT t.throws_like($$UPDATE policy.source_document SET status = 'published' WHERE id = 'd7100000-0000-4000-8000-0000000000d1'$$,
                     '42501', '只能经签字函数发布', '考情·来源文件：kge 角色直接改为已发布被拒（自报两位复核人也不算）');
SELECT t.throws($$UPDATE policy.source_document SET reviewers = '{staff-009}' WHERE id = 'd7100000-0000-4000-8000-0000000000d1'$$,
                '42501', '考情·来源文件：kge 角色事后不能改自报复核人');
RESET ROLE;
SELECT t.throws(t.pol_kge_sign_sql('source_document', :pd), '42501', '考情·来源文件：写入方（starbuddy_kge）即使带 staff 账号也不能签字');
SELECT t.throws_like($$UPDATE policy.source_document SET reviewed_at = now() WHERE id = 'd7100000-0000-4000-8000-0000000000d1'$$,
                     '42501', '复核时间只能由签字函数写入', '考情·来源文件：复核时间连有列权限的身份（超级用户）也不能直接写');
SELECT t.pol_refusals('source_document', :pd, '考情·来源文件');
SELECT t.pol_twice('source_document', :pd, '考情·来源文件');
SELECT t.ok((SELECT status = 'draft' AND reviewed_at IS NULL FROM policy.source_document WHERE id = :pd), '考情·来源文件：一名 staff 批准不发布');
SELECT t.ok(t.as_staff(:sf2, t.pol_sign_sql('source_document', :pd)) = 'published', '考情·来源文件：第二名不同 staff 批准后发布');
SELECT t.ok((SELECT status = 'published' AND reviewed_at IS NOT NULL FROM policy.source_document WHERE id = :pd),
            '考情·来源文件：发布时由签字函数写入复核时间');
SELECT t.throws_like(format('SELECT t.as_staff(%L, %L)', :sf3, t.pol_sign_sql('source_document', :pd)), '23514', '只能对草稿签字',
                     '考情·来源文件：已发布的不能再签');

-- ================= 二、方案 =================
SET ROLE starbuddy_kge;
INSERT INTO policy.exam_scheme (id, city, cohort, version, source_document_id) VALUES (:ps, 'synthcity', 2030, '1.0.0', :pd);
SELECT t.throws_like($$INSERT INTO policy.exam_scheme (city, cohort, version, source_document_id, reviewed_at)
                       VALUES ('synthcity', 2031, '1.0.0', 'd7100000-0000-4000-8000-0000000000d1', now())$$,
                     '23514', '只能以草稿新建', '考情·方案：新建时不能自带复核时间');
INSERT INTO policy.subject_config (scheme_id, subject, scored, full_score, proposition, exam_form, duration_minutes, taken_in_grade) VALUES
  (:ps, 'chinese', true, 100, 'city', 'closed_book', 120, 9), (:ps, 'math', true, 100, 'city', 'closed_book', 120, 9);
INSERT INTO policy.admission_rule (scheme_id, batch_code, batch_order, track) VALUES (:ps, 'batch_1', 1, 'unified');
SELECT t.throws_like($$UPDATE policy.exam_scheme SET status = 'published' WHERE id = 'd7200000-0000-4000-8000-0000000000e1'$$,
                     '42501', '只能经 policy.publish_scheme 发布', '考情·方案：kge 角色直接改为已发布被拒');
SELECT t.throws($$UPDATE policy.exam_scheme SET reviewers = '{staff-001,staff-002}' WHERE id = 'd7200000-0000-4000-8000-0000000000e1'$$,
                '42501', '考情·方案：kge 角色事后不能改自报复核人');
SELECT t.throws($$SELECT policy.publish_scheme('d7200000-0000-4000-8000-0000000000e1')$$, '42501', '考情·方案：kge 角色不能调 policy.publish_scheme');
SELECT t.as_account(:sf1);
SELECT t.throws($$SELECT policy.publish_scheme('d7200000-0000-4000-8000-0000000000e1')$$, '42501',
                '考情·方案：kge 角色即使带 staff 账号也不能调 policy.publish_scheme');
SELECT t.as_account(NULL);
RESET ROLE;
SELECT t.throws(format('SELECT t.pol_as(''starbuddy_learning'', %L, %L)', :sf1, format('SELECT policy.publish_scheme(%L)::text', :ps)),
                '42501', '考情·方案：学习服务不能调 policy.publish_scheme');
SELECT t.throws(format('SELECT t.pol_as(''starbuddy_bff'', %L, %L)', :gA, format('SELECT policy.publish_scheme(%L)::text', :ps)),
                '42501', '考情·方案：BFF 不能调 policy.publish_scheme');
SELECT t.throws(format('SELECT t.pol_publish(%L, %L)', :gA, :ps), '42501', '考情·方案：签字身份带家长账号不能发布');
SELECT t.throws(format('SELECT t.pol_publish(NULL, %L)', :ps), '42501', '考情·方案：签字身份未设账号不能发布');
SELECT t.throws(format('SELECT t.pol_publish(%L, %L)', '5d000000-0000-4000-8000-00000000005d', :ps), '42501', '考情·方案：已停用的 staff 不能发布');
SELECT t.throws_like(format('SELECT t.pol_publish(%L, %L)', :sf1, :ps), '23514', '阻断问题', '考情·方案：没有签字时 staff 发布被拒');
SELECT t.throws(t.pol_kge_sign_sql('exam_scheme', :ps), '42501', '考情·方案：写入方（starbuddy_kge）即使带 staff 账号也不能签字');
SELECT t.pol_refusals('exam_scheme', :ps, '考情·方案');
SELECT t.pol_twice('exam_scheme', :ps, '考情·方案');
SELECT t.ok('reviewers' = ANY (SELECT check_name FROM policy.validate_scheme(:ps) WHERE severity = 'error'), '考情·方案：一人批准时校验报复核人不足');
SELECT t.throws_like(format('SELECT t.pol_publish(%L, %L)', :sf1, :ps), '23514', '阻断问题', '考情·方案：同一账号签两次仍不能发布');
-- 签字后草稿又改了：旧签字不再计数
SET ROLE starbuddy_kge;
INSERT INTO policy.subject_config (scheme_id, subject, scored, full_score, proposition, exam_form, duration_minutes, taken_in_grade)
  VALUES (:ps, 'english', true, 100, 'city', 'closed_book', 120, 9);
RESET ROLE;
SELECT t.ok(policy.approval_count('exam_scheme', :ps) = 0, '考情·方案：签字后改了科目配置，旧签字不再计数');
SELECT t.ok(t.as_staff(:sf1, t.pol_sign_sql('exam_scheme', :ps)) = '1' AND t.as_staff(:sf2, t.pol_sign_sql('exam_scheme', :ps)) = '2',
            '考情·方案：原签字人可以对新内容再签，两名不同 staff 批准');
-- 当前内容被驳回：清零，改了再送审
SELECT t.ok(t.as_staff(:sf3, t.pol_sign_sql('exam_scheme', :ps, 'rejected')) = '0', '考情·方案：第三名 staff 驳回当前内容后有效批准人数为 0');
SELECT t.throws_like(format('SELECT t.pol_publish(%L, %L)', :sf1, :ps), '23514', '阻断问题', '考情·方案：当前内容被驳回时不能发布');
SELECT t.throws_like(format('SELECT t.as_staff(%L, %L)', :sf3, t.pol_sign_sql('exam_scheme', :ps)), '23505', '只能签一次',
                     '考情·方案：驳回人不能对同一内容改签批准（改了内容再送审）');
SET ROLE starbuddy_kge;
INSERT INTO policy.admission_rule (scheme_id, batch_code, batch_order, track) VALUES (:ps, 'batch_2', 2, 'unified');
RESET ROLE;
SELECT t.ok(t.as_staff(:sf1, t.pol_sign_sql('exam_scheme', :ps)) = '1' AND t.as_staff(:sf2, t.pol_sign_sql('exam_scheme', :ps)) = '2',
            '考情·方案：改过的内容两名不同 staff 重新批准');
SELECT t.ok(t.pol_publish(:sf3, :ps) IS NOT NULL, '考情·方案：签字身份（第三名 staff）经 policy.publish_scheme 发布');
SELECT t.ok((SELECT status = 'published' AND reviewed_at = (SELECT max(x.signed_at) FROM platform.policy_signoff x
                                                             WHERE x.exam_scheme_id = :ps AND x.action = 'review' AND x.decision = 'approved'
                                                               AND x.digest = policy.object_digest('exam_scheme', :ps))
               FROM policy.exam_scheme WHERE id = :ps),
            '考情·方案：复核时间是对发布内容批准的最后一个签字时间');
SELECT t.ok((SELECT account_id = :sf3 FROM platform.policy_signoff WHERE exam_scheme_id = :ps AND action = 'publish'),
            '考情·方案：发布记录的签字人是发布人（取当前账号）');
SELECT t.throws_like(format('SELECT t.as_staff(%L, %L)', :sf3, t.pol_sign_sql('exam_scheme', :ps)), '23514', '只能对草稿方案签字',
                     '考情·方案：已发布的方案不能再签');
SELECT t.throws(format('SELECT t.as_staff(%L, %L)', :sf1, format('SELECT policy.sign_record(''exam_scheme'', %L, ''approved'', policy.object_digest(''exam_scheme'', %L))', :ps, :ps)),
                '22023', '考情·方案：不能经 sign_record 签字发布（只能 sign_scheme + publish_scheme）');

-- ================= 三、学校 =================
SET ROLE starbuddy_kge;
SELECT t.throws_like($$INSERT INTO policy.school (city, code, public_name, level_code, source_document_id, review_status)
                       VALUES ('synthcity', 'synth-hs-2030b', '合成高中乙（非真实）', 'level_a', 'd7100000-0000-4000-8000-0000000000d1', 'published')$$,
                     '23514', '只能以草稿新建', '考情·学校：kge 角色不能直接以已发布新建');
INSERT INTO policy.school (id, city, code, public_name, level_code, source_document_id)
  VALUES (:ph, 'synthcity', 'synth-hs-2030', '合成高中甲（非真实）', 'level_a', :pd);
SELECT t.throws_like($$UPDATE policy.school SET review_status = 'published' WHERE id = 'd7300000-0000-4000-8000-0000000000f1'$$,
                     '42501', '只能经签字函数发布', '考情·学校：kge 角色直接改为已发布被拒');
SELECT t.throws($$UPDATE policy.school SET reviewed_at = now() WHERE id = 'd7300000-0000-4000-8000-0000000000f1'$$,
                '42501', '考情·学校：kge 角色不能写复核时间');
SELECT t.throws($$UPDATE policy.school SET reviewers = '{staff-001,staff-002}' WHERE id = 'd7300000-0000-4000-8000-0000000000f1'$$,
                '42501', '考情·学校：kge 角色事后不能改自报复核人');
RESET ROLE;
SELECT t.throws(t.pol_kge_sign_sql('school', :ph), '42501', '考情·学校：写入方（starbuddy_kge）即使带 staff 账号也不能签字');
SELECT t.pol_refusals('school', :ph, '考情·学校');
SELECT t.pol_twice('school', :ph, '考情·学校');
SELECT t.as_staff(:sf2, t.pol_sign_sql('school', :ph)) AS school_signed \gset
SELECT t.ok(:'school_signed' = 'published' AND (SELECT review_status = 'published' AND reviewed_at IS NOT NULL FROM policy.school WHERE id = :ph),
            '考情·学校：第二名不同 staff 批准后发布（实际：' || :'school_signed' || '）');

-- ================= 四、招录行 =================
SET ROLE starbuddy_kge;
SELECT t.throws_like($$INSERT INTO policy.admission_line (school_id, year, batch_code, track, line_score, source_document_id, status)
                       VALUES ('d7300000-0000-4000-8000-0000000000f1', 2030, 'batch_1', 'unified', 150, 'd7100000-0000-4000-8000-0000000000d1', 'published')$$,
                     '23514', '只能以草稿新建', '考情·招录行：kge 角色不能直接以已发布新建');
INSERT INTO policy.admission_line (id, school_id, year, batch_code, track, line_score, plan_count, source_document_id)
  VALUES (:pl, :ph, 2030, 'batch_1', 'unified', 150, 200, :pd);
SELECT t.throws_like($$UPDATE policy.admission_line SET status = 'published' WHERE id = 'd7400000-0000-4000-8000-0000000000b1'$$,
                     '42501', '只能经签字函数发布', '考情·招录行：kge 角色直接改为已发布被拒');
SELECT t.throws($$UPDATE policy.admission_line SET reviewers = '{staff-001,staff-002}' WHERE id = 'd7400000-0000-4000-8000-0000000000b1'$$,
                '42501', '考情·招录行：kge 角色事后不能改自报复核人');
RESET ROLE;
SELECT t.throws(t.pol_kge_sign_sql('admission_line', :pl), '42501', '考情·招录行：写入方（starbuddy_kge）即使带 staff 账号也不能签字');
SELECT t.pol_refusals('admission_line', :pl, '考情·招录行');
SELECT t.pol_twice('admission_line', :pl, '考情·招录行');
-- 签字与查状态分两条语句（同一条语句里的子查询看不到函数刚做的修改）
SELECT t.as_staff(:sf2, t.pol_sign_sql('admission_line', :pl)) AS line_signed \gset
SELECT t.ok(:'line_signed' = 'published' AND (SELECT status = 'published' AND reviewed_at IS NOT NULL FROM policy.admission_line WHERE id = :pl),
            '考情·招录行：第二名不同 staff 批准后发布（实际：' || :'line_signed' || '）');

-- ================= 五、签字人表与版本记录导出 =================
SELECT t.ok(NOT EXISTS (SELECT 1 FROM unnest(ARRAY['starbuddy_bff', 'starbuddy_learning', 'starbuddy_review', 'starbuddy_scheduler', 'starbuddy_insight',
                                                   'starbuddy_kge', 'starbuddy_kge_read', 'starbuddy_report', 'starbuddy_eraser']) r(role)
                         WHERE has_table_privilege(r.role, 'platform.policy_signoff', 'SELECT')
                            OR has_table_privilege(r.role, 'platform.policy_signoff', 'INSERT')),
            '考情签字：签字人表对任何应用角色都不可读写（只有函数属主 authz）');
SELECT t.throws($$UPDATE platform.policy_signoff SET decision = 'rejected'$$, '42501', '考情签字：签字记录不能改（只追加，连超级用户也拦）');
SELECT t.throws($$DELETE FROM platform.policy_signoff$$, '42501', '考情签字：签字记录不能删');
SELECT t.throws($$TRUNCATE platform.policy_signoff$$, '42501', '考情签字：签字记录不能清空');
SELECT t.ok((SELECT count(*) FROM platform.policy_signoff x WHERE x.object_id IN (:pd, :ps, :ph, :pl) AND x.action = 'publish') = 4,
            '考情签字：四类对象各有一条发布记录');
SELECT t.ok((SELECT bool_and(cardinality(reviewers) = 0) FROM policy.exam_scheme WHERE id = :ps)
            AND (SELECT reviewers = '{staff-001,staff-002}' FROM policy.source_document WHERE id = :pd),
            '考情签字：签字人不写进 policy 表（自报工号列只作展示，签字前后不变）');
SET ROLE starbuddy_kge;
SELECT t.ok((SELECT count(*) FROM platform.policy_signoff_record() r WHERE r.object_id = 'd7200000-0000-4000-8000-0000000000e1') = 7
            AND (SELECT r.signer = '5f000000-0000-4000-8000-00000000005f' FROM platform.policy_signoff_record() r
                  WHERE r.object_id = 'd7200000-0000-4000-8000-0000000000e1' ORDER BY r.signed_at LIMIT 1)
            AND (SELECT r.city = 'synthcity' AND r.version = '1.0.0' AND r.cohort = 2030 AND r.signer = 'd7000000-0000-4000-8000-0000000000a3'
                   FROM platform.policy_signoff_record() r WHERE r.object_id = 'd7200000-0000-4000-8000-0000000000e1' AND r.action = 'publish'),
            '考情签字：审核台后端（kge）经 platform.policy_signoff_record 读到方案的每次签字与发布（城市、版本、届别、签字人）');
SELECT t.ok((SELECT r.cohort = 2030 AND r.city = 'synthcity' AND r.version IS NULL FROM platform.policy_signoff_record() r
              WHERE r.object_id = 'd7400000-0000-4000-8000-0000000000b1' AND r.action = 'publish')
            AND (SELECT r.city = 'synthcity' FROM platform.policy_signoff_record() r WHERE r.object_id = 'd7300000-0000-4000-8000-0000000000f1' AND r.action = 'publish'),
            '考情签字：招录行的导出行带城市与年份，学校带城市');
SELECT t.ok((SELECT array_agg(r.signed_at) = array_agg(r.signed_at ORDER BY r.signed_at) FROM platform.policy_signoff_record() r),
            '考情签字：导出按签字时间排序');
RESET ROLE;
SELECT t.throws(format('SELECT t.pol_as(''starbuddy_learning'', %L, %L)', :sf1, 'SELECT count(*)::text FROM platform.policy_signoff_record()'),
                '42501', '考情签字：学习服务读不到签字导出');
SELECT t.throws(format('SELECT t.pol_as(''starbuddy_bff'', %L, %L)', :gA, 'SELECT count(*)::text FROM platform.policy_signoff_record()'),
                '42501', '考情签字：BFF 读不到签字导出');
SELECT t.ok(t.pol_as('starbuddy_bff', :gA, format('SELECT count(*)::text FROM policy.exam_scheme WHERE id = %L', :ps)) = '1'
            AND t.pol_as('starbuddy_bff', :gA, format('SELECT count(*)::text FROM policy.admission_line WHERE id = %L', :pl)) = '1'
            AND t.pol_as('starbuddy_bff', :gA, format('SELECT count(*)::text FROM policy.school WHERE id = %L', :ph)) = '1',
            '考情签字：经签字发布的方案、学校、招录行对 BFF 可见');
