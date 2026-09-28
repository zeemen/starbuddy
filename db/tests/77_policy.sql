-- 考情政策库与招录库（0013）、学生档案届别（0014）：specs/policy-schema-v0.9/spec.md 第 7 节。
-- 夹具全部是合成数据：合成城市 synthcity、合成学校、合成分数，不是任何真实城市的考试规则或分数线（规则 8；v1.3 附录 A 的数值不进仓库）。

\set doc1 '''a1000000-0000-4000-8000-0000000000d1'''
\set doc2 '''a2000000-0000-4000-8000-0000000000d2'''
\set sch1 '''a3000000-0000-4000-8000-0000000000e1'''
\set sch2 '''a4000000-0000-4000-8000-0000000000e2'''
\set sch3 '''a5000000-0000-4000-8000-0000000000e3'''
\set school '''a6000000-0000-4000-8000-0000000000f1'''

-- 官方来源主机名由人登记（超级用户身份模拟人工登记；应用角色都不能写）
INSERT INTO policy.source_domain (host, issuer, added_by) VALUES ('jyj.synthcity.example', '合成教育局', 'staff-001');

CREATE FUNCTION t.policy_issues(p_scheme uuid, p_severity text) RETURNS text[] LANGUAGE sql AS $$
  SELECT coalesce(array_agg(c.check_name ORDER BY c.check_name), '{}') FROM policy.validate_scheme(p_scheme) c WHERE c.severity = p_severity $$;
GRANT EXECUTE ON FUNCTION t.policy_issues(uuid, text) TO PUBLIC;

SET ROLE starbuddy_kge;
-- ================= 来源文件 =================
SELECT t.throws($$INSERT INTO policy.source_domain (host, issuer, added_by) VALUES ('evil.example', 'x', 'x')$$, '42501', '应用角色不能登记官方来源主机名');
INSERT INTO policy.source_document (id, city, issuer, doc_number, title, published_on, url, applies_from_cohort, applies_to_cohort, last_verified_on) VALUES
  (:doc1, 'synthcity', '合成教育局', '合成〔2026〕1号', '合成中考方案（非真实数据）', '2026-03-01', 'https://jyj.synthcity.example/doc/1', 2026, 2027, '2026-09-01'),
  (:doc2, 'synthcity', '合成教育局', '合成〔2026〕2号', '合成招生计划（非真实数据）', '2026-05-01', 'https://jyj.synthcity.example/doc/2', 2026, NULL, '2026-09-01');
SELECT t.throws_like($$INSERT INTO policy.source_document (city, issuer, doc_number, title, published_on, url, applies_from_cohort, last_verified_on)
                       VALUES ('synthcity', '合成', 'x1', '合成', '2026-01-01', 'https://rumor.example/x', 2026, '2026-02-01')$$,
                     '23514', '官方来源清单', '来源链接必须来自登记过的官方主机名');
SELECT t.throws_like($$INSERT INTO policy.source_document (city, issuer, doc_number, title, published_on, url, applies_from_cohort, last_verified_on)
                  VALUES ('synthcity', '合成', 'x2', '合成', '2026-01-01', 'http://jyj.synthcity.example/x', 2026, '2026-02-01')$$,
                '23514', '官方来源清单', '来源链接必须是 https（非 https 取不到主机名，由触发器先拒绝；CHECK 兜底）');
SELECT t.throws_like($$INSERT INTO policy.source_document (city, issuer, doc_number, title, published_on, url, applies_from_cohort, applies_to_cohort, last_verified_on)
                       VALUES ('synthcity', '合成', 'x3', '合成', '2026-01-01', 'https://jyj.synthcity.example/x', 2027, 2026, '2026-02-01')$$,
                     '23514', 'source_document_cohort_range', '适用届别止不能早于起');
SELECT t.throws_like($$UPDATE policy.source_document SET status = 'published', reviewers = '{staff-001}', reviewed_at = now() WHERE id = 'a1000000-0000-4000-8000-0000000000d1'$$,
                     '23514', 'source_document_two_reviewers', '来源文件发布须两位复核人');
SELECT t.throws_like($$UPDATE policy.source_document SET status = 'published', reviewers = '{staff-001,staff-001}', reviewed_at = now() WHERE id = 'a1000000-0000-4000-8000-0000000000d1'$$,
                     '23514', 'source_document_two_reviewers', '两位复核人不能是同一人');
UPDATE policy.source_document SET status = 'published', reviewers = '{staff-001,staff-002}', reviewed_at = now() WHERE id IN (:doc1, :doc2);
SELECT t.throws_like($$UPDATE policy.source_document SET title = '改标题' WHERE id = 'a1000000-0000-4000-8000-0000000000d1'$$,
                     '23514', '只能更新核对日期或撤下', '已发布的来源文件不能改内容');
UPDATE policy.source_document SET last_verified_on = '2026-09-20' WHERE id = :doc1;
SELECT t.ok((SELECT last_verified_on = '2026-09-20' FROM policy.source_document WHERE id = :doc1), '已发布的来源文件可以更新核对日期');

-- ================= 方案、科目配置、规则 =================
INSERT INTO policy.exam_scheme (id, city, cohort, version, source_document_id) VALUES
  (:sch1, 'synthcity', 2026, '1.0.0', :doc1), (:sch2, 'synthcity', 2027, '1.0.0', :doc1), (:sch3, 'synthcity', 2027, '1.1.0', :doc1);
SELECT t.throws_like($$INSERT INTO policy.exam_scheme (city, cohort, version, source_document_id, status, published_at)
                       VALUES ('synthcity', 2028, '1.0.0', 'a1000000-0000-4000-8000-0000000000d1', 'published', now())$$,
                     '23514', '只能以草稿新建', '方案不能直接以已发布新建');
SELECT t.throws_like($$UPDATE policy.exam_scheme SET status = 'published' WHERE id = 'a3000000-0000-4000-8000-0000000000e1'$$,
                     '42501', '只能经 policy.publish_scheme 发布', '写入方不能绕过发布函数');
SELECT t.throws($$UPDATE policy.exam_scheme SET published_at = now() WHERE id = 'a3000000-0000-4000-8000-0000000000e1'$$, '42501', '写入方不能写发布时间');
SELECT t.throws($$INSERT INTO policy.exam_scheme (city, cohort, version, source_document_id) VALUES ('Synth City', 2027, '2.0.0', 'a1000000-0000-4000-8000-0000000000d1')$$,
                '23514', '城市码与平台地区码同一套编码');
SELECT t.throws($$INSERT INTO policy.exam_scheme (city, cohort, version, source_document_id) VALUES ('synthcity', 1999, '2.0.0', 'a1000000-0000-4000-8000-0000000000d1')$$,
                '23514', '届别范围');

INSERT INTO policy.subject_config (scheme_id, subject, scored, full_score, proposition, exam_form, duration_minutes, combined_group, taken_in_grade) VALUES
  (:sch1, 'chinese', true, 100, 'city', 'closed_book', 120, NULL, 9),
  (:sch1, 'math', true, 100, 'city', 'closed_book', 120, NULL, 9),
  (:sch1, 'geography', false, NULL, 'city', 'assessment', 60, NULL, 8),
  (:sch2, 'chinese', true, 120, 'city', 'closed_book', 150, NULL, 9),
  (:sch2, 'math', true, 120, 'city', 'closed_book', 120, NULL, 9),
  (:sch2, 'morality_law', true, 60, 'province', 'open_book', 60, 'open_book_pair', 9),
  (:sch2, 'history', true, 60, 'province', 'open_book', 60, 'open_book_pair', 9),
  (:sch3, 'chinese', true, 120, 'city', 'closed_book', 150, NULL, 9),
  (:sch3, 'math', true, 120, 'city', 'closed_book', 120, NULL, 9),
  (:sch3, 'morality_law', true, 60, 'province', 'open_book', 60, 'open_book_pair', 9);
SELECT t.throws_like($$INSERT INTO policy.subject_config (scheme_id, subject, scored, proposition, exam_form, taken_in_grade)
                       VALUES ('a3000000-0000-4000-8000-0000000000e1', 'english', true, 'city', 'closed_book', 9)$$,
                     '23514', 'subject_config_scored_full_score', '计分科目必须写满分');
SELECT t.throws_like($$INSERT INTO policy.subject_config (scheme_id, subject, scored, full_score, proposition, exam_form, taken_in_grade)
                       VALUES ('a3000000-0000-4000-8000-0000000000e1', 'biology', false, 50, 'city', 'assessment', 8)$$,
                     '23514', 'subject_config_scored_full_score', '非计分科目不写满分');
INSERT INTO policy.admission_rule (scheme_id, batch_code, batch_order, track, control_line_basis) VALUES
  (:sch1, 'batch_1', 1, 'unified', 'none'), (:sch2, 'batch_1', 1, 'unified', 'none'), (:sch2, 'batch_1', 1, 'quota_allocation', 'total_score'),
  (:sch3, 'batch_1', 1, 'unified', 'none');
SELECT t.throws_like($$INSERT INTO policy.admission_rule (scheme_id, batch_code, batch_order, track, control_line_basis)
                       VALUES ('a3000000-0000-4000-8000-0000000000e1', 'batch_2', 2, 'unified', 'total_score')$$,
                     '23514', 'admission_rule_control_line', '控制线只对指标生有意义');
INSERT INTO policy.transition_rule (scheme_id, change_kind, replaces_scheme_id) VALUES (:sch2, 'total_score', :sch1);

-- ================= 发布前校验与发布 =================
SELECT t.ok(t.policy_issues(:sch1, 'error') = '{reviewers}', '复核人不足时校验报阻断（实际：' || array_to_string(t.policy_issues(:sch1, 'error'), ',') || '）');
SELECT t.throws_like($$SELECT policy.publish_scheme('a3000000-0000-4000-8000-0000000000e1')$$, '23514', '阻断问题', '有阻断问题时拒绝发布');
UPDATE policy.exam_scheme SET reviewers = '{staff-001,staff-002}', reviewed_at = now() WHERE id IN (:sch1, :sch2, :sch3);
SELECT t.ok(t.policy_issues(:sch1, 'error') = '{}', '修好后没有阻断问题');
SELECT t.ok(policy.publish_scheme(:sch1) IS NOT NULL, '2026 届方案可以发布');
SELECT t.ok((SELECT policy.scheme_total(:sch1)) = 200, '计分总分由库计算，非计分科目不计入');
-- sch3：合场分组只有一门、与上一届相比总分变化且没有过渡规则
SELECT t.ok(t.policy_issues(:sch3, 'error') = '{combined_group_single}', '合场分组只有一门时阻断（实际：' || array_to_string(t.policy_issues(:sch3, 'error'), ',') || '）');
SELECT t.ok(t.policy_issues(:sch3, 'warning') = '{missing_transition}', '总分变化却没有过渡规则时报警告');
SELECT t.ok(t.policy_issues(:sch2, 'warning') = '{}', '登记了过渡规则就不报警告');
SELECT t.ok(policy.publish_scheme(:sch2) IS NOT NULL, '2027 届方案可以发布');
SELECT t.ok((SELECT policy.scheme_total(:sch2)) = 360, '2027 届计分总分');
INSERT INTO policy.subject_config (scheme_id, subject, scored, full_score, proposition, exam_form, duration_minutes, combined_group, taken_in_grade)
  VALUES (:sch3, 'history', true, 60, 'province', 'open_book', 60, 'open_book_pair', 9);
INSERT INTO policy.transition_rule (scheme_id, change_kind, replaces_scheme_id) VALUES (:sch3, 'total_score', :sch1);
SELECT t.ok(policy.publish_scheme(:sch3) IS NOT NULL, '修好后同届新版本可以发布');
SELECT t.ok((SELECT status FROM policy.exam_scheme WHERE id = :sch2) = 'retired' AND (SELECT status FROM policy.exam_scheme WHERE id = :sch3) = 'published',
            '同城市同届别发布新版本时旧版本自动撤下');
SELECT t.ok((SELECT count(*) FROM policy.exam_scheme WHERE city = 'synthcity' AND cohort = 2027 AND status = 'published') = 1,
            '同城市同届别同一时刻只有一个已发布方案');
-- 冻结
SELECT t.throws_like($$UPDATE policy.subject_config SET full_score = 150 WHERE scheme_id = 'a3000000-0000-4000-8000-0000000000e1' AND subject = 'chinese'$$,
                     '23514', '已发布或已撤下', '已发布方案的科目配置不能改');
SELECT t.throws_like($$INSERT INTO policy.admission_rule (scheme_id, batch_code, batch_order, track) VALUES ('a5000000-0000-4000-8000-0000000000e3', 'batch_9', 9, 'other')$$,
                     '23514', '已发布或已撤下', '已发布方案不能加招录规则');
SELECT t.throws_like($$UPDATE policy.transition_rule SET change_kind = 'other' WHERE scheme_id = 'a4000000-0000-4000-8000-0000000000e2'$$,
                     '23514', '已发布或已撤下', '已撤下方案同样冻结');
SELECT t.throws_like($$UPDATE policy.exam_scheme SET version = '9.9.9' WHERE id = 'a5000000-0000-4000-8000-0000000000e3'$$,
                     '23514', '只能改状态', '已发布方案只能改状态');
SELECT t.throws_like($$UPDATE policy.exam_scheme SET status = 'draft' WHERE id = 'a4000000-0000-4000-8000-0000000000e2'$$,
                     '23514', '不允许的方案状态变化', '已撤下方案不能回到草稿');
SELECT t.throws($$SELECT policy.validate_scheme('a0000000-0000-4000-8000-000000000000')$$, 'P0002', '校验不存在的方案时报错');
INSERT INTO policy.exam_scheme (id, city, cohort, version, source_document_id, reviewers, reviewed_at)
  VALUES ('a7000000-0000-4000-8000-0000000000e4', 'synthcity', 2027, '1.2.0', :doc1, '{staff-001,staff-002}', now());
RESET ROLE;
-- 唯一已发布：即使以发布函数属主身份直接改，同城市同届别也不能有第二个已发布方案
SET ROLE starbuddy_authz;
SELECT t.throws($$UPDATE policy.exam_scheme SET status = 'published', published_at = now() WHERE id = 'a7000000-0000-4000-8000-0000000000e4'$$,
                '23505', '同城市同届别只能有一个已发布方案（唯一索引兜底）');
RESET ROLE;
SET ROLE starbuddy_kge;

-- ================= 招录库 =================
INSERT INTO policy.school (id, city, code, public_name, level_code) VALUES (:school, 'synthcity', 'synth-hs-1', '合成高级中学（非真实）', 'level_a');
INSERT INTO policy.admission_line (school_id, year, batch_code, track, line_score, plan_count, control_line, source_document_id) VALUES
  (:school, 2026, 'batch_1', 'unified', 180, 300, NULL, :doc2),
  (:school, 2027, 'batch_1', 'quota_allocation', NULL, 50, 300, :doc2);
SELECT t.throws_like($$INSERT INTO policy.admission_line (school_id, year, batch_code, track, line_score, source_document_id)
                       VALUES ('a6000000-0000-4000-8000-0000000000f1', 2026, 'batch_1', 'unified', 201, 'a2000000-0000-4000-8000-0000000000d2')$$,
                     '23514', '超过该年方案的计分总分', '投档线不能超过该年方案的计分总分');
SELECT t.throws_like($$INSERT INTO policy.admission_line (school_id, year, batch_code, track, line_score, source_document_id)
                       VALUES ('a6000000-0000-4000-8000-0000000000f1', 2028, 'batch_1', 'unified', 100, 'a1000000-0000-4000-8000-0000000000d1')$$,
                     '23514', '不覆盖该年', '来源文件必须覆盖该年');
SELECT t.throws_like($$INSERT INTO policy.admission_line (school_id, year, batch_code, track, line_score, source_document_id)
                       VALUES ('a6000000-0000-4000-8000-0000000000f1', 2029, 'batch_1', 'unified', 100, 'a2000000-0000-4000-8000-0000000000d2')$$,
                     '23514', '没有已发布的中考方案', '该年没有已发布方案时不能录招录行');
SELECT t.throws_like($$INSERT INTO policy.admission_line (school_id, year, batch_code, track, line_score, control_line, source_document_id)
                       VALUES ('a6000000-0000-4000-8000-0000000000f1', 2026, 'batch_1', 'unified', 100, 90, 'a2000000-0000-4000-8000-0000000000d2')$$,
                     '23514', 'admission_line_control_line', '统招行不写控制线');
SELECT t.throws_like($$INSERT INTO policy.admission_line (school_id, year, batch_code, track, source_document_id)
                       VALUES ('a6000000-0000-4000-8000-0000000000f1', 2026, 'batch_1', 'other', 'a2000000-0000-4000-8000-0000000000d2')$$,
                     '23514', 'admission_line_has_value', '招录行至少有分数线或计划数');
SELECT t.throws_like($$UPDATE policy.admission_line SET status = 'published', reviewers = '{staff-001}', reviewed_at = now() WHERE year = 2026$$,
                     '23514', 'admission_line_two_reviewers', '招录行发布须两位复核人');
UPDATE policy.admission_line SET status = 'published', reviewers = '{staff-001,staff-002}', reviewed_at = now();
SELECT t.throws_like($$UPDATE policy.admission_line SET line_score = 181 WHERE year = 2026$$, '23514', '只能撤下', '已发布的招录行不能改数值');
-- 更正：新行 + 撤下旧行
INSERT INTO policy.admission_line (school_id, year, batch_code, track, line_score, plan_count, source_document_id) VALUES
  (:school, 2026, 'batch_1', 'unified', 181, 300, :doc2);
SELECT t.throws($$UPDATE policy.admission_line SET status = 'published', reviewers = '{staff-001,staff-002}', reviewed_at = now() WHERE year = 2026 AND line_score = 181$$,
                '23505', '同一学校同一年同一批次同一类别只能有一条已发布行');
UPDATE policy.admission_line SET status = 'retired' WHERE year = 2026 AND line_score = 180;
UPDATE policy.admission_line SET status = 'published', reviewers = '{staff-001,staff-002}', reviewed_at = now() WHERE year = 2026 AND line_score = 181;
SELECT t.ok((SELECT count(*) FROM policy.admission_line WHERE year = 2026) = 2 AND (SELECT line_score FROM policy.admission_line WHERE year = 2026 AND status = 'published') = 181,
            '更正走新行加撤下旧行，历史保留');
SELECT t.throws($$DELETE FROM policy.admission_line$$, '42501', '写入方也不能删除招录行');
RESET ROLE;

-- ================= 读取范围 =================
SELECT t.ok(NOT EXISTS (
  SELECT 1 FROM pg_class c CROSS JOIN unnest(ARRAY['starbuddy_bff', 'starbuddy_learning', 'starbuddy_review', 'starbuddy_scheduler',
                                                   'starbuddy_insight', 'starbuddy_kge']) r(role)
   WHERE c.relnamespace = 'policy'::regnamespace AND c.relkind = 'r'
     AND (has_table_privilege(r.role, c.oid, 'DELETE') OR has_table_privilege(r.role, c.oid, 'TRUNCATE')
          OR (r.role <> 'starbuddy_kge' AND (has_any_column_privilege(r.role, c.oid, 'INSERT') OR has_any_column_privilege(r.role, c.oid, 'UPDATE'))))),
  '考情库：只有写入方能写，任何应用角色都不能删除或清空');
SELECT t.ok(NOT has_schema_privilege('starbuddy_insight', 'policy', 'USAGE'), '洞察服务暂不读考情库');
SET ROLE starbuddy_bff;
SELECT t.ok((SELECT array_agg(id ORDER BY id) FROM policy.exam_scheme) = ARRAY[:sch1::uuid, :sch3::uuid], 'BFF 只看得到已发布方案');
SELECT t.ok((SELECT count(DISTINCT scheme_id) FROM policy.subject_config) = 2, 'BFF 只看得到已发布方案的科目配置');
SELECT t.ok((SELECT count(*) FROM policy.admission_line) = 2 AND NOT EXISTS (SELECT 1 FROM policy.admission_line WHERE status <> 'published'),
            'BFF 只看得到已发布的招录行');
SELECT t.throws($$SELECT reviewers FROM policy.exam_scheme$$, '42501', 'BFF 看不到方案复核人');
SELECT t.throws($$SELECT reviewers FROM policy.admission_line$$, '42501', 'BFF 看不到招录行复核人');
SELECT t.throws($$SELECT count(*) FROM policy.source_domain$$, '42501', 'BFF 不读官方来源清单');
SELECT t.throws($$SELECT policy.publish_scheme('a3000000-0000-4000-8000-0000000000e1')$$, '42501', 'BFF 不能发布方案');
RESET ROLE;
SET ROLE starbuddy_learning;
SELECT t.ok((SELECT count(*) FROM policy.exam_scheme) = 4, '学科服务读得到全部方案（含已撤下与草稿）');
SELECT t.throws($$SELECT policy.publish_scheme('a3000000-0000-4000-8000-0000000000e1')$$, '42501', '学科服务不能发布方案');
RESET ROLE;

-- ================= kge 地图校验读政策库 =================
SELECT t.ok(EXISTS (SELECT 1 FROM kge.validate_map_version('b1000000-0000-4000-8000-0000000000b1') c
                     WHERE c.check_name = 'policy_scheme_missing' AND c.object_id = 'nanjing:2027' AND c.severity = 'warning'),
            '地区投影的城市届别在政策库没有已发布方案时，地图校验报警告');

-- ================= 学生档案届别（0014） =================
SELECT t.ok(NOT EXISTS (SELECT 1 FROM platform.student_profile WHERE exam_cohort IS NULL), '已有学生档案的届别全部回填');
SELECT t.ok((SELECT exam_cohort FROM platform.student_profile WHERE id = 'c1000000-0000-4000-8000-0000000000c1')
            = platform.expected_cohort((SELECT grade FROM platform.student_profile WHERE id = 'c1000000-0000-4000-8000-0000000000c1'), now()),
            '建档时按年级与当前学年推算届别');
SELECT t.ok(platform.expected_cohort(9::smallint, '2026-10-01T00:00:00+08:00') = 2027 AND platform.expected_cohort(9::smallint, '2027-03-01T00:00:00+08:00') = 2027
            AND platform.expected_cohort(7::smallint, '2026-09-01T00:00:00+08:00') = 2029 AND platform.expected_cohort(9::smallint, '2026-08-31T12:00:00+08:00') = 2026,
            '届别推算：学年以 9 月 1 日切换');
SELECT t.throws_like($$UPDATE platform.student_profile SET exam_cohort = exam_cohort + 3 WHERE id = 'c1000000-0000-4000-8000-0000000000c1'$$,
                     '23514', '与年级', '届别与年级推算值相差超过 1 时拒绝');
UPDATE platform.student_profile SET exam_cohort = exam_cohort + 1 WHERE id = 'c1000000-0000-4000-8000-0000000000c1';
SELECT t.ok(true, '届别可以在推算值 ±1 内更正（留级、跳级）');
UPDATE platform.student_profile SET exam_cohort = exam_cohort - 1 WHERE id = 'c1000000-0000-4000-8000-0000000000c1';
