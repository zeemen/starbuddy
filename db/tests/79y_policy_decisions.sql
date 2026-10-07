-- 考情 v0.9 待定事项落地 PR1（0045–0048，specs/policy-decisions-v0.9.1/spec.md 第 1、2、4、5 节）：
--   一、年级推算 platform.current_grade；二、建档时的届别与来源；三、届别更正 platform.correct_exam_cohort（渠道、授权、±1、记录）；
--   四、往年方案对 BFF 开放（草稿仍不可见，复核人列仍不给）；五、控制线表 policy.control_line（规则、签字发布、唯一已发布、BFF 读取）；
--   六、官方来源清单由管理员维护（staff 权限、规范化、只下线不删、操作记录在 platform）。
-- 两会话并发（更正与改年级、再次更正的串行；控制线发布唯一）在 db/tests/run.sh。
-- 合成数据（规则 8）：合成城市 synthcity 的 2028 届合成方案、合成控制线分数、合成主机名 *.example，非真实数据；合成学生 Y1–Y3 建在 10 号夹具的家庭 H1。
-- 用 79m 的 t.m_as(角色, 账号, SQL)，76 号的 t.as_staff，77 号的 t.pol_sign / pol_sign2 / pol_publish，79v 号的 t.pol_sign_sql / pol_refusals / pol_twice /
-- pol_kge_sign_sql；staff：sf1、sf2（10 号夹具）、sf3（79v）、已停用的 sfx（79b）；家长 gA、gB、gC（10 号夹具）。
\set sf1 '''5f000000-0000-4000-8000-00000000005f'''
\set sf2 '''f5000000-0000-4000-8000-0000000000f5'''
\set sf3 '''d7000000-0000-4000-8000-0000000000a3'''
\set sfx '''5d000000-0000-4000-8000-00000000005d'''
\set gA '''a0000000-0000-4000-8000-00000000000a'''
\set gB '''b0000000-0000-4000-8000-00000000000b'''
\set gC '''cc000000-0000-4000-8000-0000000000cc'''
\set H1 '''d1000000-0000-4000-8000-0000000000d1'''
\set Y1 '''f7900000-0000-4000-8000-0000000000c1'''
\set Y2 '''f7900000-0000-4000-8000-0000000000c2'''
\set Y3 '''f7900000-0000-4000-8000-0000000000c3'''
\set aY1 '''f7900000-0000-4000-8000-0000000000a1'''
\set aY2 '''f7900000-0000-4000-8000-0000000000a2'''
\set aY3 '''f7900000-0000-4000-8000-0000000000a3'''
\set aY4 '''f7900000-0000-4000-8000-0000000000a4'''
\set doc2 '''a2000000-0000-4000-8000-0000000000d2'''
\set doc3 '''a8000000-0000-4000-8000-0000000000d3'''
\set sch2 '''a4000000-0000-4000-8000-0000000000e2'''
\set ycd1 '''f7910000-0000-4000-8000-0000000000d1'''
\set ycd2 '''f7910000-0000-4000-8000-0000000000d2'''
\set ycs '''f7910000-0000-4000-8000-0000000000e1'''
\set yds '''f7910000-0000-4000-8000-0000000000e9'''
\set ydd '''f7910000-0000-4000-8000-0000000000d9'''
\set ycl1 '''f7920000-0000-4000-8000-0000000000b1'''
\set ycl2 '''f7920000-0000-4000-8000-0000000000b2'''
\set ycl3 '''f7920000-0000-4000-8000-0000000000b3'''
\set ycl4 '''f7920000-0000-4000-8000-0000000000b4'''
RESET ROLE;
SELECT t.as_account(NULL);

-- 以某个应用角色、某个账号执行一条语句，返回 'ok' 或“SQLSTATE:消息”（失败时子事务回滚，角色与账号一并恢复）
CREATE FUNCTION t.y_try(p_role text, p_acct uuid, p_sql text) RETURNS text LANGUAGE plpgsql AS $$
BEGIN
  PERFORM t.m_as(p_role, p_acct, p_sql);
  RETURN 'ok';
EXCEPTION WHEN OTHERS THEN
  RETURN SQLSTATE || ':' || SQLERRM;
END $$;
CREATE FUNCTION t.y_correct_sql(p_student uuid, p_cohort integer) RETURNS text LANGUAGE sql AS $$
  SELECT format('SELECT platform.correct_exam_cohort(%L, %s::smallint)::text', p_student, coalesce(p_cohort::text, 'NULL')) $$;
GRANT EXECUTE ON FUNCTION t.y_try(text, uuid, text), t.y_correct_sql(uuid, integer) TO PUBLIC;

-- ================= 一、年级推算（0045） =================
SELECT t.ok(platform.current_grade(2027::smallint, '2026-10-01T00:00:00+08:00') = 9
            AND platform.current_grade(2027::smallint, '2027-03-01T00:00:00+08:00') = 9
            AND platform.current_grade(2029::smallint, '2026-09-01T00:00:00+08:00') = 7
            AND platform.current_grade(2027::smallint, '2026-08-31T23:59:00+08:00') = 8
            AND platform.current_grade(2027::smallint, '2026-08-31T16:30:00+00:00') = 9,
            '年级推算：按届别与调用方给的时刻推算，9 月 1 日（北京时间）切学年');
SELECT t.ok(platform.current_grade(2026::smallint, '2026-10-01T00:00:00+08:00') IS NULL
            AND platform.current_grade(2030::smallint, '2026-10-01T00:00:00+08:00') IS NULL
            AND platform.current_grade(NULL, '2026-10-01T00:00:00+08:00') IS NULL AND platform.current_grade(2027::smallint, NULL) IS NULL,
            '年级推算：已毕业（10）、未入学（6）或参数为空时返回 NULL');
SELECT t.ok(NOT EXISTS (SELECT 1 FROM generate_series(7, 9) g,
                               unnest(ARRAY['2026-09-01T00:00:00+08:00', '2027-02-28T12:00:00+08:00', '2027-08-31T23:00:00+08:00']::timestamptz[]) at
                         WHERE platform.current_grade(platform.expected_cohort(g::smallint, at), at) IS DISTINCT FROM g),
            '年级推算是 expected_cohort 的逆（7–9 年级、学年内任意时刻）');
SELECT t.ok(t.m_as('starbuddy_bff', :gA, $$SELECT platform.current_grade(2027::smallint, '2026-10-01T00:00:00+08:00')::text$$) = '9'
            AND t.m_as('starbuddy_learning', NULL, $$SELECT platform.current_grade(2027::smallint, '2026-10-01T00:00:00+08:00')::text$$) = '9',
            '年级推算：BFF 与学习服务都能调用（读点改造在 PR2）');

-- ================= 二、建档时的届别与来源（保护触发器只在 INSERT 时核对） =================
INSERT INTO platform.account (id, kind) VALUES (:aY1, 'student'), (:aY2, 'student'), (:aY3, 'student'), (:aY4, 'student');
INSERT INTO platform.student_profile (id, account_id, household_id, grade, region) VALUES (:Y1, :aY1, :H1, 9, 'nanjing');
SELECT t.ok((SELECT exam_cohort = platform.expected_cohort(grade, created_at) AND exam_cohort_source = 'derived'
               FROM platform.student_profile WHERE id = :Y1),
            '建档未给届别：按建档年级推算，来源记 derived');
SELECT t.throws_like(format($$INSERT INTO platform.student_profile (account_id, household_id, grade, region, exam_cohort, exam_cohort_source)
                              VALUES (%L, %L, 9, 'nanjing', platform.expected_cohort(9::smallint, now()) + 2, 'guardian_report')$$, :aY4, :H1),
                     '23514', '与建档年级', '建档时届别与推算值相差超过 1 时拒绝');
SELECT t.throws_like(format($$INSERT INTO platform.student_profile (account_id, household_id, grade, region, exam_cohort)
                              VALUES (%L, %L, 9, 'nanjing', platform.expected_cohort(9::smallint, now()) + 1)$$, :aY4, :H1),
                     '23514', 'derived', '建档时标 derived（默认）的届别必须等于推算值（规则 6：来源要如实）');
SELECT t.throws(format($$INSERT INTO platform.student_profile (account_id, household_id, grade, region, exam_cohort_source)
                         VALUES (%L, %L, 9, 'nanjing', 'self_report')$$, :aY4, :H1),
                '23514', '届别来源只收 derived、guardian_report、staff_correction');
SELECT t.throws_like(format($$INSERT INTO platform.student_profile (account_id, household_id, grade, region, exam_cohort_source)
                              VALUES (%L, %L, 9, 'nanjing', 'staff_correction')$$, :aY4, :H1),
                     '23514', '建档时届别来源只能是', '建档时来源不能是客服更正（客服更正只经更正函数）');
INSERT INTO platform.student_profile (id, account_id, household_id, grade, region, exam_cohort, exam_cohort_source)
VALUES (:Y2, :aY2, :H1, 8, 'nanjing', platform.expected_cohort(8::smallint, now()) + 1, 'guardian_report'),
       (:Y3, :aY3, :H1, 8, 'nanjing', NULL, 'derived');
SELECT t.ok((SELECT exam_cohort = platform.expected_cohort(grade, created_at) + 1 AND exam_cohort_source = 'guardian_report'
               FROM platform.student_profile WHERE id = :Y2),
            '建档时可以带家长自述的届别（推算值 ±1，来源 guardian_report）');
-- 建档之后改届别不再拿建档年级比对（0014 的更新时核对去掉了），但必须同时改来源列（应用角色没有改档案的权限，这里是 DBA）
SELECT t.throws_like(format('UPDATE platform.student_profile SET exam_cohort = exam_cohort + 1 WHERE id = %L', :Y2), '23514', '同时改届别来源',
                     '0045：改届别不改来源列被拒（规则 6）');
UPDATE platform.student_profile SET exam_cohort = exam_cohort + 5, exam_cohort_source = 'staff_correction' WHERE id = :Y2;
SELECT t.ok((SELECT exam_cohort = platform.expected_cohort(grade, created_at) + 6 FROM platform.student_profile WHERE id = :Y2),
            '0045：建档之后改届别（同时改来源）不再与建档年级比对');
UPDATE platform.student_profile SET exam_cohort = exam_cohort - 5, exam_cohort_source = 'guardian_report' WHERE id = :Y2;
SELECT t.throws_like(format($$INSERT INTO platform.exam_cohort_change (student_id, old_cohort, new_cohort, source, changed_by, changed_at)
                              VALUES (%L, 2027, 2029, 'staff_correction', %L, now())$$, :Y2, :sf1),
                     '23514', 'exam_cohort_change_step', '更正记录：一条记录只能是 ±1（表上的约束兜底）');
SELECT t.ok(NOT EXISTS (SELECT 1 FROM unnest(ARRAY['starbuddy_bff', 'starbuddy_learning', 'starbuddy_review', 'starbuddy_kge', 'starbuddy_insight',
                                                   'starbuddy_scheduler', 'starbuddy_eraser']) r(role)
                         WHERE has_column_privilege(r.role, 'platform.student_profile', 'exam_cohort', 'UPDATE')
                            OR has_column_privilege(r.role, 'platform.student_profile', 'exam_cohort_source', 'UPDATE')
                            OR has_column_privilege(r.role, 'platform.student_profile', 'grade', 'UPDATE')),
            '应用角色都不能直接改届别、来源与建档年级（更正只经函数）');
INSERT INTO platform.guardian_student_link (guardian_account_id, student_id, granted_via) VALUES
  (:gA, :Y1, 'household_creator'), (:gA, :Y2, 'household_creator'), (:gA, :Y3, 'household_creator');
INSERT INTO platform.guardian_student_link (guardian_account_id, student_id, granted_via, status, revoked_at) VALUES (:gC, :Y1, 'invite', 'revoked', now());

-- ================= 三、更正届别 =================
SELECT exam_cohort AS y1c FROM platform.student_profile WHERE id = :Y1 \gset
-- 家长（经 BFF）：+1，来源 guardian_report
SELECT t.ok(t.m_as('starbuddy_bff', :gA, t.y_correct_sql(:Y1, :y1c + 1)) = (:y1c + 1)::text, '更正：有效绑定家长经 BFF 更正 +1');
SELECT t.ok((SELECT exam_cohort = :y1c + 1 AND exam_cohort_source = 'guardian_report' FROM platform.student_profile WHERE id = :Y1),
            '更正：家长更正后来源记 guardian_report（家长自述）');
-- 客服（审核台签字连接，active staff）：再 +1，来源 staff_correction；累计与建档推算值相差 2 也可以（不再拿建档年级比对）
SELECT t.ok(t.m_as('starbuddy_review', :sf1, t.y_correct_sql(:Y1, :y1c + 2)) = (:y1c + 2)::text,
            '更正：active staff 经签字连接再更正 +1（累计 +2，不与建档年级比对）');
SELECT t.ok((SELECT exam_cohort = :y1c + 2 AND exam_cohort_source = 'staff_correction' FROM platform.student_profile WHERE id = :Y1),
            '更正：客服更正后来源记 staff_correction');
SELECT t.ok(t.y_try('starbuddy_bff', :gA, t.y_correct_sql(:Y1, :y1c + 3)) LIKE '22023:%7–9%', '更正：更正后当前年级不在 7–9（6 年级）时拒绝');
SELECT t.ok(t.y_try('starbuddy_bff', :gA, t.y_correct_sql(:Y1, :y1c)) LIKE '22023:%±1%', '更正：每次只能 ±1（相差 2 拒绝）');
SELECT t.ok(t.y_try('starbuddy_bff', :gA, t.y_correct_sql(:Y1, NULL)) LIKE '22023:%', '更正：届别为空时拒绝');
SELECT t.ok(t.m_as('starbuddy_bff', :gA, t.y_correct_sql(:Y1, :y1c + 1)) = (:y1c + 1)::text
            AND t.m_as('starbuddy_bff', :gA, t.y_correct_sql(:Y1, :y1c)) = (:y1c)::text,
            '更正：可以往回改（每次 -1）');
SELECT t.ok(t.y_try('starbuddy_bff', :gA, t.y_correct_sql(:Y1, :y1c - 1)) LIKE '22023:%7–9%', '更正：更正后当前年级是 10 年级（已毕业）时拒绝');
-- 记录：旧值、新值、来源、操作者、时间，按时间串成一条链
SELECT t.ok((SELECT string_agg(format('%s>%s:%s:%s', old_cohort - :y1c, new_cohort - :y1c, source,
                                      CASE changed_by WHEN :gA THEN 'gA' WHEN :sf1 THEN 'sf1' ELSE changed_by::text END), ',' ORDER BY changed_at, id)
               FROM platform.exam_cohort_change WHERE student_id = :Y1)
            = '0>1:guardian_report:gA,1>2:staff_correction:sf1,2>1:guardian_report:gA,1>0:guardian_report:gA',
            '更正记录：每次一行（旧值、新值、来源、操作者），被拒的更正不留记录');
-- 授权：不是有效绑定家长与学生不存在同样拒绝；学生本人、已解除绑定的家长、经 BFF 的 staff、经签字连接的家长、停用的 staff、未设账号都拒绝
SELECT t.y_try('starbuddy_bff', :gB, t.y_correct_sql(:Y1, :y1c + 1)) AS deny_other \gset
SELECT t.ok(:'deny_other' LIKE '42501:%' AND :'deny_other' = t.y_try('starbuddy_bff', :gB, t.y_correct_sql(gen_random_uuid(), :y1c + 1)),
            '更正：另一户的家长与“学生不存在”结果相同（42501，不暴露存在性；实际：' || :'deny_other' || '）');
SELECT t.ok(t.y_try('starbuddy_bff', :gC, t.y_correct_sql(:Y1, :y1c + 1)) = :'deny_other', '更正：已解除绑定的家长不能更正');
SELECT t.ok(t.y_try('starbuddy_bff', :aY1, t.y_correct_sql(:Y1, :y1c + 1)) = :'deny_other', '更正：学生本人不能更正（家长或客服更正）');
SELECT t.ok(t.y_try('starbuddy_bff', :sf1, t.y_correct_sql(:Y1, :y1c + 1)) = :'deny_other',
            '更正：经 BFF 带 staff 账号走不到客服分支（渠道按调用角色，不按账号类型）');
SELECT t.ok(t.y_try('starbuddy_bff', NULL, t.y_correct_sql(:Y1, :y1c + 1)) = :'deny_other', '更正：经 BFF 未登录不能更正');
SELECT t.ok(t.y_try('starbuddy_review', :gA, t.y_correct_sql(:Y1, :y1c + 1)) LIKE '42501:%', '更正：经签字连接带家长账号不能更正');
SELECT t.ok(t.y_try('starbuddy_review', :sfx, t.y_correct_sql(:Y1, :y1c + 1)) LIKE '42501:%', '更正：停用的 staff 不能更正');
SELECT t.ok(t.y_try('starbuddy_review', NULL, t.y_correct_sql(:Y1, :y1c + 1)) LIKE '42501:%', '更正：签字连接未设账号不能更正');
SELECT t.ok(t.y_try('starbuddy_learning', :sf1, t.y_correct_sql(:Y1, :y1c + 1)) LIKE '42501:%'
            AND t.y_try('starbuddy_kge', :sf1, t.y_correct_sql(:Y1, :y1c + 1)) LIKE '42501:%'
            AND t.y_try('starbuddy_insight', :gA, t.y_correct_sql(:Y1, :y1c + 1)) LIKE '42501:%',
            '更正：学习服务、知识服务、洞察服务都不能调用');
SELECT t.as_account(:sf1);
SELECT t.throws(t.y_correct_sql(:Y1, :y1c + 1), '42501', '更正：同时继承 bff 与 review 的身份（如超级用户）即使带 active staff 账号也拒绝');
SELECT t.as_account(NULL);
SELECT t.ok((SELECT exam_cohort = :y1c FROM platform.student_profile WHERE id = :Y1)
            AND (SELECT count(*) FROM platform.exam_cohort_change WHERE student_id = :Y1) = 4,
            '更正：被拒的调用不改届别、不留记录');
-- 已执行删除的档案（0035 erased_at）不能更正
BEGIN;
UPDATE platform.student_profile SET erased_at = now() WHERE id = :Y3;
SELECT t.ok(t.y_try('starbuddy_review', :sf1, t.y_correct_sql(:Y3, (SELECT exam_cohort + 1 FROM platform.student_profile WHERE id = :Y3)))
              LIKE 'P0002:%'
            AND t.y_try('starbuddy_bff', :gA, t.y_correct_sql(:Y3, (SELECT exam_cohort + 1 FROM platform.student_profile WHERE id = :Y3)))
              LIKE 'P0002:%',
            '更正：已执行删除的档案不能更正');
ROLLBACK;
-- 更正记录表：应用角色一律读不到、写不进（只有函数属主 authz；删除身份随删除请求删）
SELECT t.ok(NOT EXISTS (SELECT 1 FROM unnest(ARRAY['starbuddy_bff', 'starbuddy_learning', 'starbuddy_review', 'starbuddy_kge', 'starbuddy_insight',
                                                   'starbuddy_scheduler', 'starbuddy_kge_read', 'starbuddy_report']) r(role)
                         WHERE has_any_column_privilege(r.role, 'platform.exam_cohort_change', 'SELECT')
                            OR has_any_column_privilege(r.role, 'platform.exam_cohort_change', 'INSERT'))
            AND t.y_try('starbuddy_bff', :gA, 'SELECT count(*)::text FROM platform.exam_cohort_change') LIKE '42501:%',
            '更正记录：应用角色读不到也写不进');
SELECT t.ok((SELECT strategy = 'delete' AND erase_scope = 'facts' FROM platform.personal_table_registry
              WHERE nspname = 'platform' AND relname = 'exam_cohort_change'),
            '更正记录登记进个人层表清单，随删除请求删除');

-- ================= 四、往年方案对 BFF 开放（0046） =================
SELECT t.ok(t.m_as('starbuddy_bff', :gA, format('SELECT count(*)::text FROM policy.exam_scheme WHERE id = %L AND status = ''retired''', :sch2)) = '1'
            AND t.m_as('starbuddy_bff', :gA, format('SELECT count(*)::text FROM policy.subject_config WHERE scheme_id = %L', :sch2))::int > 0
            AND t.m_as('starbuddy_bff', :gA, format('SELECT count(*)::text FROM policy.source_document WHERE id = %L AND status = ''retired''', :doc3)) = '1',
            '往年方案：BFF 读得到已撤下的方案、它的科目配置与已撤下的来源文件');
-- 草稿方案连同它的科目配置、过渡规则、招录规则仍不可见（事务内建，结束回滚）
BEGIN;
SET LOCAL ROLE starbuddy_kge;
INSERT INTO policy.exam_scheme (id, city, cohort, version, source_document_id) VALUES (:yds, 'synthcity', 2027, '1.9.0', :doc2);
INSERT INTO policy.subject_config (scheme_id, subject, scored, full_score, proposition, exam_form, taken_in_grade)
  VALUES (:yds, 'chinese', true, 100, 'city', 'closed_book', 9);
INSERT INTO policy.admission_rule (scheme_id, batch_code, batch_order, track) VALUES (:yds, 'batch_1', 1, 'unified');
INSERT INTO policy.transition_rule (scheme_id, change_kind) VALUES (:yds, 'other');
RESET ROLE;
SELECT t.ok(t.m_as('starbuddy_bff', :gA, format($q$SELECT ((SELECT count(*) FROM policy.exam_scheme WHERE id = %1$L)
                                                           + (SELECT count(*) FROM policy.subject_config WHERE scheme_id = %1$L)
                                                           + (SELECT count(*) FROM policy.admission_rule WHERE scheme_id = %1$L)
                                                           + (SELECT count(*) FROM policy.transition_rule WHERE scheme_id = %1$L))::text$q$, :yds)) = '0'
            AND t.m_as('starbuddy_bff', :gA, 'SELECT count(*)::text FROM policy.source_document WHERE status = ''draft''') = '0',
            '往年方案：草稿方案与它的科目配置、招录规则、过渡规则、草稿来源文件 BFF 仍看不到');
ROLLBACK;
-- 草稿不能直接撤下（0046，审查阻断 1）：放宽后“已撤下”对前端可见，没经两人签字的草稿不能借撤下露出来；放弃的草稿留在草稿
BEGIN;
SET LOCAL ROLE starbuddy_kge;
INSERT INTO policy.exam_scheme (id, city, cohort, version, source_document_id) VALUES (:yds, 'synthcity', 2027, '1.9.0', :doc2);
INSERT INTO policy.source_document (id, city, issuer, doc_number, title, published_on, url, applies_from_cohort, last_verified_on)
  VALUES (:ydd, 'synthcity', '合成教育局', '合成〔2026〕77号', '合成草稿文件（非真实数据）', '2026-03-01', 'https://jyj.synthcity.example/doc/ydd', 2027, '2026-09-01');
SELECT t.throws_like(format($$UPDATE policy.exam_scheme SET status = 'retired' WHERE id = %L$$, :yds), '23514', '草稿不能直接撤下',
                     '往年方案：写入方不能把草稿方案直接改为已撤下');
SELECT t.throws_like(format($$UPDATE policy.source_document SET status = 'retired' WHERE id = %L$$, :ydd), '23514', '草稿不能直接撤下',
                     '往年方案：写入方不能把草稿来源文件直接改为已撤下');
RESET ROLE;
SELECT t.ok((SELECT status = 'draft' FROM policy.exam_scheme WHERE id = :yds) AND (SELECT status = 'draft' FROM policy.source_document WHERE id = :ydd)
            AND t.m_as('starbuddy_bff', :gA, format('SELECT ((SELECT count(*) FROM policy.exam_scheme WHERE id = %L) + (SELECT count(*) FROM policy.source_document WHERE id = %L))::text', :yds, :ydd)) = '0',
            '往年方案：被拒后仍是草稿，BFF 读不到');
-- 兜底：即使跳过触发器（超级用户的复制模式），0041 的 *_reviewed CHECK 仍拦住没有复核时间的已撤下行
SET LOCAL session_replication_role = replica;
SELECT t.throws_like(format($$UPDATE policy.exam_scheme SET status = 'retired' WHERE id = %L$$, :yds), '23514', 'exam_scheme_reviewed',
                     '往年方案：跳过守卫时，方案的复核时间 CHECK 仍拦住草稿直接撤下');
SELECT t.throws_like(format($$UPDATE policy.source_document SET status = 'retired' WHERE id = %L$$, :ydd), '23514', 'source_document_reviewed',
                     '往年方案：跳过守卫时，来源文件的复核时间 CHECK 仍拦住草稿直接撤下');
ROLLBACK;
SELECT t.ok(t.y_try('starbuddy_bff', :gA, 'SELECT reviewers FROM policy.exam_scheme') LIKE '42501:%'
            AND t.y_try('starbuddy_bff', :gA, 'SELECT reviewed_at FROM policy.exam_scheme') LIKE '42501:%'
            AND t.y_try('starbuddy_bff', :gA, 'SELECT reviewers FROM policy.source_document') LIKE '42501:%'
            AND t.y_try('starbuddy_bff', :gA, 'SELECT reviewed_at FROM policy.source_document') LIKE '42501:%',
            '往年方案：复核人与复核时间列仍不给 BFF');
SELECT t.ok((SELECT count(*) FROM policy.admission_line WHERE status = 'retired') > 0
            AND t.m_as('starbuddy_bff', :gA, 'SELECT count(*)::text FROM policy.admission_line WHERE status <> ''published''') = '0',
            '往年方案：招录行仍只读已发布（已撤下的招录行 BFF 看不到）');

-- ================= 五、控制线（0047） =================
SELECT t.ok(NOT EXISTS (SELECT 1 FROM pg_attribute WHERE attrelid = 'policy.admission_line'::regclass AND attname = 'control_line' AND NOT attisdropped),
            '控制线：招录行上的控制线列已删除（回填进 policy.control_line）');
-- 夹具：2028 届合成方案（指标生 batch_1 不设控制线、batch_2 按批次线设控制线），两份 2028 起适用的合成来源文件
SET ROLE starbuddy_kge;
INSERT INTO policy.source_document (id, city, issuer, doc_number, title, published_on, url, applies_from_cohort, last_verified_on) VALUES
  (:ycd1, 'synthcity', '合成教育局', '合成〔2027〕8号', '合成 2028 届招生办法（非真实数据）', '2027-03-01', 'https://jyj.synthcity.example/doc/2028a', 2028, '2027-09-01'),
  (:ycd2, 'synthcity', '合成教育局', '合成〔2027〕9号', '合成 2028 届控制线公告（非真实数据）', '2027-06-01', 'https://jyj.synthcity.example/doc/2028b', 2028, '2027-09-01');
INSERT INTO policy.exam_scheme (id, city, cohort, version, source_document_id) VALUES (:ycs, 'synthcity', 2028, '1.0.0', :ycd1);
INSERT INTO policy.subject_config (scheme_id, subject, scored, full_score, proposition, exam_form, taken_in_grade)
  VALUES (:ycs, 'chinese', true, 100, 'city', 'closed_book', 9);
INSERT INTO policy.admission_rule (scheme_id, batch_code, batch_order, track, control_line_basis) VALUES
  (:ycs, 'batch_1', 1, 'quota_allocation', 'none'), (:ycs, 'batch_2', 2, 'quota_allocation', 'batch_line'), (:ycs, 'batch_2', 2, 'unified', 'none');
RESET ROLE;
SELECT t.ok(t.pol_sign2('source_document', :ycd1) = 'published' AND t.pol_sign2('source_document', :ycd2) = 'published'
            AND t.pol_sign2('exam_scheme', :ycs) = '2', '控制线夹具：来源文件与 2028 届方案经两名 staff 签字');
SELECT t.ok(t.pol_publish(:sf1, :ycs) IS NOT NULL, '控制线夹具：2028 届方案发布');

SET ROLE starbuddy_kge;
-- 2027 届已发布方案 sch3（77 号）有 batch_1 指标生、控制线依据 total_score，总分 360
INSERT INTO policy.control_line (id, city, year, batch_code, track, score, source_document_id)
  VALUES (:ycl1, 'synthcity', 2027, 'batch_1', 'quota_allocation', 300, :doc2);
INSERT INTO policy.control_line (id, city, year, batch_code, score, source_document_id) VALUES (:ycl2, 'synthcity', 2028, 'batch_2', 90, :ycd1);
SELECT t.ok((SELECT track = 'quota_allocation' AND status = 'draft' AND reviewed_at IS NULL FROM policy.control_line WHERE id = :ycl2),
            '控制线：以草稿新建，类别默认指标生');
SELECT t.throws_like(format($$INSERT INTO policy.control_line (city, year, batch_code, track, score, source_document_id)
                              VALUES ('synthcity', 2028, 'batch_2', 'unified', 90, %L)$$, :ycd1), '23514', 'control_line_basis',
                     '控制线：只收指标生（统招规则的控制线依据只能是 none，守卫先拒；列上的 CHECK 兜底）');
SELECT t.throws_like(format($$INSERT INTO policy.control_line (city, year, batch_code, score, source_document_id)
                              VALUES ('synthcity', 2028, 'batch_1', 90, %L)$$, :ycd1), '23514', 'control_line_basis',
                     '控制线：该年已发布方案的招录规则里这个批次不设控制线（control_line_basis = none）时拒绝');
SELECT t.throws_like(format($$INSERT INTO policy.control_line (city, year, batch_code, score, source_document_id)
                              VALUES ('synthcity', 2028, 'batch_3', 90, %L)$$, :ycd1), '23514', 'control_line_basis',
                     '控制线：该年已发布方案里没有这个批次时拒绝');
SELECT t.throws_like(format($$INSERT INTO policy.control_line (city, year, batch_code, score, source_document_id)
                              VALUES ('synthcity', 2028, 'batch_2', 101, %L)$$, :ycd1), '23514', '超过该年方案的计分总分', '控制线：不能超过该年方案的计分总分');
SELECT t.throws_like(format($$INSERT INTO policy.control_line (city, year, batch_code, score, source_document_id)
                              VALUES ('synthcity', 2029, 'batch_2', 90, %L)$$, :ycd1), '23514', '没有已发布的中考方案', '控制线：该年没有已发布方案时拒绝');
SELECT t.throws_like(format($$INSERT INTO policy.control_line (city, year, batch_code, score, source_document_id)
                              VALUES ('synthcity', 2027, 'batch_1', 300, %L)$$, :ycd1), '23514', '来源文件', '控制线：来源文件必须覆盖该年');
SELECT t.throws_like(format($$INSERT INTO policy.control_line (city, year, batch_code, score, source_document_id)
                              VALUES ('synthcity', 2027, 'batch_1', 300, %L)$$, :doc3), '23514', '来源文件', '控制线：来源文件必须同城市且已发布');
SELECT t.throws_like(format($$INSERT INTO policy.control_line (city, year, batch_code, score, source_document_id, status)
                              VALUES ('synthcity', 2028, 'batch_2', 90, %L, 'published')$$, :ycd1), '23514', '只能以草稿新建', '控制线：不能以已发布新建');
SELECT t.throws_like(format($$INSERT INTO policy.control_line (city, year, batch_code, score, source_document_id, reviewed_at)
                              VALUES ('synthcity', 2028, 'batch_2', 90, %L, now())$$, :ycd1), '23514', '只能以草稿新建', '控制线：新建时不能自带复核时间');
SELECT t.throws(format($$INSERT INTO policy.control_line (city, year, batch_code, score, source_document_id) VALUES ('synthcity', 2028, 'batch_2', -1, %L)$$, :ycd1),
                '23514', '控制线：分数不能为负');
SELECT t.throws_like(format($$UPDATE policy.control_line SET status = 'published' WHERE id = %L$$, :ycl1), '42501', '只能经签字函数发布',
                     '控制线：写入方不能直接改为已发布');
SELECT t.throws(format($$UPDATE policy.control_line SET reviewed_at = now() WHERE id = %L$$, :ycl1), '42501', '控制线：写入方不能写复核时间');
SELECT t.ok(NOT EXISTS (SELECT 1 FROM pg_attribute WHERE attrelid = 'policy.control_line'::regclass AND attname = 'reviewers' AND NOT attisdropped),
            '控制线：没有自报复核人列（签字人只在 platform.policy_signoff）');
SELECT t.throws_like(format($$UPDATE policy.control_line SET status = 'retired' WHERE id = %L$$, :ycl2), '23514', '草稿不能直接撤下',
                     '控制线：写入方不能把草稿直接改为已撤下');
SELECT t.throws(format($$UPDATE policy.control_line SET city = 'othercity' WHERE id = %L$$, :ycl1), '42501', '控制线：城市、年份、批次写入后不能改');
SELECT t.throws(format($$DELETE FROM policy.control_line WHERE id = %L$$, :ycl1), '42501', '控制线：写入方也不能删除');
RESET ROLE;
-- 签字：非 staff、写入方、学习服务、摘要不符一律拒绝；同一账号签两次只算一人
SELECT t.throws(t.pol_kge_sign_sql('control_line', :ycl1), '42501', '控制线：写入方（starbuddy_kge）即使带 staff 账号也不能签字');
SELECT t.pol_refusals('control_line', :ycl1, '控制线');
SELECT t.pol_twice('control_line', :ycl1, '控制线');
-- 签字后分数又改了：旧签字不再计数
SET ROLE starbuddy_kge;
UPDATE policy.control_line SET score = 310 WHERE id = :ycl1;
RESET ROLE;
SELECT t.ok(policy.approval_count('control_line', :ycl1) = 0, '控制线：签字后改了分数，旧签字不再计数');
SELECT t.ok(t.as_staff(:sf1, t.pol_sign_sql('control_line', :ycl1)) = 'draft', '控制线：对新内容第一名 staff 批准后仍是草稿');
SELECT t.as_staff(:sf2, t.pol_sign_sql('control_line', :ycl1)) AS cl_signed \gset
SELECT t.ok(:'cl_signed' = 'published' AND (SELECT status = 'published' AND reviewed_at IS NOT NULL AND score = 310 FROM policy.control_line WHERE id = :ycl1),
            '控制线：第二名不同 staff 批准后发布，复核时间由签字函数写入（实际：' || :'cl_signed' || '）');
SELECT t.ok((SELECT count(*) FROM platform.policy_signoff WHERE control_line_id = :ycl1 AND object_type = 'control_line' AND action = 'publish') = 1,
            '控制线：签字人与发布人记在 platform.policy_signoff');
SET ROLE starbuddy_kge;
SELECT t.ok((SELECT r.city = 'synthcity' AND r.cohort = 2027 AND r.signer = :sf2 FROM platform.policy_signoff_record() r
              WHERE r.object_id = :ycl1 AND r.object_type = 'control_line' AND r.action = 'publish'),
            '控制线：版本记录导出带控制线的城市、年份与发布人');
SELECT t.throws_like(format($$UPDATE policy.control_line SET score = 311 WHERE id = %L$$, :ycl1), '23514', '只能撤下', '控制线：已发布的不能改分数');
RESET ROLE;
SELECT t.throws_like(format('SELECT t.as_staff(%L, %L)', :sf3, t.pol_sign_sql('control_line', :ycl1)), '23514', '只能对草稿签字', '控制线：已发布的不能再签');
-- 同一（城市、年份、批次、类别）只能有一条已发布：更正走新行 + 撤下旧行
SET ROLE starbuddy_kge;
INSERT INTO policy.control_line (id, city, year, batch_code, score, source_document_id) VALUES (:ycl3, 'synthcity', 2027, 'batch_1', 305, :doc2);
RESET ROLE;
SELECT t.throws(format('SELECT t.pol_sign2(''control_line'', %L)', :ycl3), '23505',
                '控制线：同一城市年份批次类别只能有一条已发布（第二名签字发布时被唯一索引拒绝，整笔回滚）');
SELECT t.ok(policy.approval_count('control_line', :ycl3) = 0, '控制线：发布被拒时这次签字一并回滚');
SET ROLE starbuddy_kge;
UPDATE policy.control_line SET status = 'retired' WHERE id = :ycl1;
RESET ROLE;
SELECT t.ok(t.pol_sign2('control_line', :ycl3) = 'published', '控制线：撤下旧行后新行经两名 staff 签字发布');
SET ROLE starbuddy_kge;
SELECT t.throws_like(format($$UPDATE policy.control_line SET status = 'published' WHERE id = %L$$, :ycl1), '23514', '已撤下的控制线不能再改',
                     '控制线：已撤下的不能复活');
-- 发布时再核对：草稿建好后来源文件被撤下，第二名签字发布被拒（签字回滚）
INSERT INTO policy.control_line (id, city, year, batch_code, score, source_document_id) VALUES (:ycl4, 'synthcity', 2028, 'batch_2', 95, :ycd2);
RESET ROLE;
SELECT t.ok(t.as_staff(:sf1, t.pol_sign_sql('control_line', :ycl4)) = 'draft', '控制线：草稿第一名 staff 批准');
SET ROLE starbuddy_kge;
UPDATE policy.source_document SET status = 'retired' WHERE id = :ycd2;
RESET ROLE;
SELECT t.throws_like(format('SELECT t.as_staff(%L, %L)', :sf2, t.pol_sign_sql('control_line', :ycl4)), '23514', '来源文件',
                     '控制线：发布时再核对来源文件（已撤下就拒绝发布）');
SELECT t.ok(policy.approval_count('control_line', :ycl4) = 1 AND (SELECT status = 'draft' FROM policy.control_line WHERE id = :ycl4),
            '控制线：发布被拒后仍是草稿，第二名的签字没有留下');
-- 读取：BFF 只读已发布、不给复核人列与建立时间；学习服务读全部
SELECT t.ok(t.m_as('starbuddy_bff', :gA, 'SELECT string_agg(id::text, '','' ORDER BY id) FROM policy.control_line') = (:ycl3)::text,
            '控制线：BFF 只读得到已发布的一条（草稿与已撤下的看不到）');
SELECT t.ok(t.y_try('starbuddy_bff', :gA, 'SELECT reviewed_at FROM policy.control_line') LIKE '42501:%'
            AND t.y_try('starbuddy_bff', :gA, 'SELECT created_at FROM policy.control_line') LIKE '42501:%',
            '控制线：BFF 看不到复核时间与建立时间');
-- 兜底：跳过触发器（超级用户的复制模式）时，control_line_reviewed CHECK 仍拦住没有复核时间的非草稿行
BEGIN;
SET LOCAL session_replication_role = replica;
SELECT t.throws_like(format($$UPDATE policy.control_line SET status = 'retired' WHERE id = %L$$, :ycl2), '23514', 'control_line_reviewed',
                     '控制线：跳过守卫时，复核时间 CHECK 仍拦住草稿直接撤下');
SELECT t.throws_like(format($$UPDATE policy.control_line SET status = 'published' WHERE id = %L$$, :ycl2), '23514', 'control_line_reviewed',
                     '控制线：跳过守卫时，复核时间 CHECK 仍拦住没有复核时间的发布');
ROLLBACK;
SELECT t.ok(t.m_as('starbuddy_learning', NULL, format('SELECT count(*)::text FROM policy.control_line WHERE id IN (%L, %L, %L, %L)', :ycl1, :ycl2, :ycl3, :ycl4)) = '4',
            '控制线：学习服务读得到全部（含草稿与已撤下）');
SELECT t.ok(t.y_try('starbuddy_review', :sf1, 'SELECT count(*)::text FROM policy.control_line') LIKE '42501:%'
            AND t.y_try('starbuddy_insight', NULL, 'SELECT count(*)::text FROM policy.control_line') LIKE '42501:%',
            '控制线：签字身份与洞察服务不直接读表（签字只经函数）');

-- ================= 六、官方来源清单由管理员维护（0048） =================
-- staff 权限只由迁移或 DBA 授予（这里以超级用户模拟 DBA）；sfx 是已停用的 staff，权限在也不能用
INSERT INTO platform.staff_permission (staff_id, permission, granted_by) VALUES (:sf1, 'policy_source_admin', 'synthetic-dba'), (:sfx, 'policy_source_admin', 'synthetic-dba');
SELECT t.throws_like(format($$INSERT INTO platform.staff_permission (staff_id, permission, granted_by) VALUES (%L, 'policy_source_admin', 'synthetic-dba')$$, :gA),
                     '23514', 'staff', 'staff 权限：家长账号不能有');
SELECT t.throws(format($$INSERT INTO platform.staff_permission (staff_id, permission, granted_by) VALUES (%L, 'policy_admin_all', 'synthetic-dba')$$, :sf2),
                '23514', 'staff 权限：只收登记过的权限名');
SELECT t.ok(t.y_try('starbuddy_review', :sf1, format($$INSERT INTO platform.staff_permission (staff_id, permission, granted_by) VALUES (%L, 'policy_source_admin', 'x')$$, :sf2)) LIKE '42501:%'
            AND t.y_try('starbuddy_review', :sf1, 'DELETE FROM platform.staff_permission') LIKE '42501:%'
            AND t.y_try('starbuddy_review', :sf1, $$UPDATE platform.staff_permission SET granted_by = 'x'$$) LIKE '42501:%',
            'staff 权限：签字身份不能授予、收回或改');
SELECT t.ok(t.m_as('starbuddy_review', :sf1, 'SELECT count(*)::text FROM platform.staff_permission') = '1'
            AND t.m_as('starbuddy_review', :sf2, 'SELECT count(*)::text FROM platform.staff_permission') = '0',
            'staff 权限：签字身份只读得到自己的权限');
SELECT t.ok(t.y_try('starbuddy_kge', :sf1, 'SELECT count(*)::text FROM platform.staff_permission') LIKE '42501:%'
            AND t.y_try('starbuddy_bff', :gA, 'SELECT count(*)::text FROM platform.staff_permission') LIKE '42501:%'
            AND t.y_try('starbuddy_learning', NULL, 'SELECT count(*)::text FROM platform.staff_permission') LIKE '42501:%',
            'staff 权限：知识服务、BFF、学习服务读不到');
-- 新增：要求 active staff 且有权限
SELECT t.ok(t.y_try('starbuddy_review', :sf2, $$SELECT policy.add_source_domain('jyj.newtown.example', '合成新城教育局')$$) LIKE '42501:%没有维护%',
            '来源清单：没有 policy_source_admin 的 staff 不能新增');
SELECT t.ok(t.y_try('starbuddy_review', :sfx, $$SELECT policy.add_source_domain('jyj.newtown.example', '合成新城教育局')$$) LIKE '42501:%',
            '来源清单：停用的 staff 即使有权限也不能新增');
SELECT t.ok(t.y_try('starbuddy_review', :gA, $$SELECT policy.add_source_domain('jyj.newtown.example', '合成新城教育局')$$) LIKE '42501:%'
            AND t.y_try('starbuddy_review', NULL, $$SELECT policy.add_source_domain('jyj.newtown.example', '合成新城教育局')$$) LIKE '42501:%',
            '来源清单：家长账号或未设账号不能新增');
SELECT t.ok(t.y_try('starbuddy_kge', :sf1, $$SELECT policy.add_source_domain('jyj.newtown.example', '合成新城教育局')$$) LIKE '42501:%'
            AND t.y_try('starbuddy_learning', :sf1, $$SELECT policy.add_source_domain('jyj.newtown.example', '合成新城教育局')$$) LIKE '42501:%'
            AND t.y_try('starbuddy_bff', :sf1, $$SELECT policy.retire_source_domain('jyj.synthcity.example')$$) LIKE '42501:%',
            '来源清单：知识服务、学习服务、BFF 都不能调用新增与下线');
SELECT t.ok(t.m_as('starbuddy_review', :sf1, $$SELECT policy.add_source_domain('  JYJ.NewTown.Example.  ', '合成新城教育局')$$) = 'jyj.newtown.example',
            '来源清单：新增时主机名规范化（去空白、转小写、去结尾的点）');
SELECT t.ok((SELECT status = 'active' AND issuer = '合成新城教育局' AND added_by = 'review-console' FROM policy.source_domain WHERE host = 'jyj.newtown.example'),
            '来源清单：清单行 active，说明写进发布机关；登记人写固定标记，不写账号（规则 13）');
SELECT t.ok((SELECT string_agg(format('%s:%s:%s', action, coalesce(note, '-'), actor_account_id = :sf1), ',' ORDER BY changed_at, id)
               FROM platform.policy_source_domain_change WHERE host = 'jyj.newtown.example') = 'add:合成新城教育局:t',
            '来源清单：操作记录写 platform.policy_source_domain_change（动作、说明、操作者）');
SELECT t.throws(format('SELECT t.as_staff(%L, %L)', :sf1, format('SELECT policy.add_source_domain(%L, ''合成'')', h)), '22023',
                '来源清单：拒绝不合格的主机名 ' || coalesce(quote_literal(h), 'NULL'))
  FROM unnest(ARRAY['1.2.3.4', '10.0.0.1.', '*.newtown.example', 'jyj.newtown.example:8443', '', '   ', NULL, 'https://jyj.newtown.example',
                    'jyj.newtown.example/doc', 'newtown', 'a..newtown.example', '-bad.newtown.example', '[::1]', 'jyj.newtown.example..',
                    'jyj_x.newtown.example', 'gov.cn', ' EDU.CN. ', 'com.cn', 'js.cn', 'co.uk', 'cn', 'com', 'gov']) h;
SELECT t.ok(t.m_as('starbuddy_review', :sf1, $$SELECT policy.add_source_domain('jyj.synthtown.gov.cn', '合成：公共后缀之下的具体主机可以登记')$$) = 'jyj.synthtown.gov.cn',
            '来源清单：公共后缀之下的具体主机名可以登记（只拒绝只有公共后缀的）');
SELECT t.throws(format('SELECT t.as_staff(%L, %L)', :sf1, format('SELECT policy.add_source_domain(''jyj.othertown.example'', %s)', n)), '22023',
                '来源清单：说明须为 1–64 个字（' || left(n, 12) || '）')
  FROM unnest(ARRAY['NULL', quote_literal('  '), quote_literal(repeat('合', 65))]) n;
SELECT t.throws_like(format('SELECT t.as_staff(%L, %L)', :sf1, $$SELECT policy.add_source_domain('JYJ.NEWTOWN.EXAMPLE', '合成')$$), '23505', '已在官方来源清单里',
                     '来源清单：已在清单里的主机名不能重复新增');
-- 下线：只改状态不删行；已下线的主机名不能用于新的来源文件，挂在它下面的来源文件仍能更新核对日期
SET ROLE starbuddy_kge;
INSERT INTO policy.source_document (city, issuer, doc_number, title, published_on, url, applies_from_cohort, last_verified_on)
  VALUES ('newtown', '合成新城教育局', '合成〔2027〕1号', '合成新城文件（非真实数据）', '2027-03-01', 'https://jyj.newtown.example/doc/1', 2028, '2027-09-01');
RESET ROLE;
SELECT t.ok(t.y_try('starbuddy_review', :sf2, $$SELECT policy.retire_source_domain('jyj.newtown.example')$$) LIKE '42501:%没有维护%',
            '来源清单：没有权限的 staff 不能下线');
SELECT t.ok(t.m_as('starbuddy_review', :sf1, $$SELECT policy.retire_source_domain('JYJ.NewTown.Example.')$$) = 'jyj.newtown.example',
            '来源清单：有权限的 staff 下线（主机名同样规范化）');
SELECT t.ok((SELECT count(*) = 1 AND bool_and(status = 'retired') FROM policy.source_domain WHERE host = 'jyj.newtown.example')
            AND EXISTS (SELECT 1 FROM policy.source_document WHERE url = 'https://jyj.newtown.example/doc/1'),
            '来源清单：已被来源文件引用的主机名只下线、不删行，来源文件仍在');
SET ROLE starbuddy_kge;
SELECT t.throws_like($$INSERT INTO policy.source_document (city, issuer, doc_number, title, published_on, url, applies_from_cohort, last_verified_on)
                       VALUES ('newtown', '合成新城教育局', '合成〔2027〕2号', '合成', '2027-03-01', 'https://jyj.newtown.example/doc/2', 2028, '2027-09-01')$$,
                     '23514', '官方来源清单', '来源清单：已下线的主机名不能用于新的来源文件');
UPDATE policy.source_document SET last_verified_on = '2027-09-02' WHERE url = 'https://jyj.newtown.example/doc/1';
SELECT t.ok(true, '来源清单：主机名下线后，挂在它下面的来源文件仍能更新核对日期');
RESET ROLE;
SELECT t.throws_like(format('SELECT t.as_staff(%L, %L)', :sf1, $$SELECT policy.retire_source_domain('jyj.newtown.example')$$), '23514', '已下线',
                     '来源清单：已下线的不能再下线');
SELECT t.throws(format('SELECT t.as_staff(%L, %L)', :sf1, $$SELECT policy.retire_source_domain('jyj.nowhere.example')$$), 'P0002',
                '来源清单：清单里没有的主机名不能下线');
SELECT t.throws(format('SELECT t.as_staff(%L, %L)', :sf1, $$SELECT policy.retire_source_domain('1.2.3.4')$$), '22023', '来源清单：下线时同样拒绝不合格的主机名');
-- 新增与查状态分两条语句（同一条语句里的子查询看不到函数刚做的修改）
SELECT t.ok(t.m_as('starbuddy_review', :sf1, $$SELECT policy.add_source_domain('jyj.newtown.example', '合成新城教育局（更名）')$$) = 'jyj.newtown.example',
            '来源清单：已下线的主机名可以重新启用');
SELECT t.ok((SELECT status = 'active' AND issuer = '合成新城教育局（更名）' FROM policy.source_domain WHERE host = 'jyj.newtown.example'),
            '来源清单：重新启用后 active，发布机关按新的说明');
SELECT t.ok((SELECT string_agg(format('%s:%s', action, coalesce(note, '-')), ',' ORDER BY changed_at, id)
               FROM platform.policy_source_domain_change WHERE host = 'jyj.newtown.example')
            = 'add:合成新城教育局,retire:-,reactivate:合成新城教育局（更名）'
            AND (SELECT bool_and(actor_account_id = :sf1) FROM platform.policy_source_domain_change),
            '来源清单：新增、下线、重新启用各记一条，被拒的不留记录');
-- 操作记录只追加；应用角色读不到；账号引用不进 policy
SELECT t.throws($$UPDATE platform.policy_source_domain_change SET note = 'x'$$, '42501', '来源清单操作记录：不能改（只追加，连超级用户也拦）');
SELECT t.throws($$DELETE FROM platform.policy_source_domain_change$$, '42501', '来源清单操作记录：不能删');
SELECT t.throws($$TRUNCATE platform.policy_source_domain_change$$, '42501', '来源清单操作记录：不能清空');
SELECT t.ok(NOT EXISTS (SELECT 1 FROM unnest(ARRAY['starbuddy_bff', 'starbuddy_learning', 'starbuddy_review', 'starbuddy_kge', 'starbuddy_insight',
                                                   'starbuddy_scheduler', 'starbuddy_kge_read', 'starbuddy_report', 'starbuddy_eraser']) r(role)
                         WHERE has_any_column_privilege(r.role, 'platform.policy_source_domain_change', 'SELECT')
                            OR has_any_column_privilege(r.role, 'platform.policy_source_domain_change', 'INSERT')
                            OR has_any_column_privilege(r.role, 'policy.source_domain', 'INSERT')
                            OR has_any_column_privilege(r.role, 'policy.source_domain', 'UPDATE')),
            '来源清单：应用角色读不到操作记录，也不能直接写清单（只经两个函数）');
SELECT t.ok(NOT EXISTS (SELECT 1 FROM policy.source_domain d, platform.account a WHERE position(a.id::text IN to_jsonb(d)::text) > 0),
            '来源清单：清单行里没有任何账号 ID（操作者只记在 platform，规则 13）');
SELECT t.ok((SELECT array_agg(relname || ':' || strategy ORDER BY relname) FROM platform.personal_table_registry
              WHERE nspname = 'platform' AND relname IN ('staff_permission', 'policy_source_domain_change'))
            = '{policy_source_domain_change:exempt,staff_permission:exempt}',
            'staff 权限表与来源清单操作记录登记进个人层表清单（只含教研账号，豁免）');
