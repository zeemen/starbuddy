-- 0059 内部成员测试运营（specs/launch-lt06-internal-test-ops 范围第 3、4 条，决定记录第 1–3 条）：问题反馈与退出测试。
-- 全部合成数据（规则 8）：本文件自建两户（户一：两位家长、兄妹两名学生；户二：一位家长、一名学生）与两名 staff（一名停用），
-- 号码用未分配的国家码 +999。整个文件在一个事务里，结束回滚（不给 80 的矩阵留数据；80 照常按目录扫描新表与新函数）。
-- 判据：
--   反馈：只有 BFF 能插入，只能以自己会话的账号为提交者；学生只能写自己的（学生自述），家长只能写已绑定孩子的（家长观察），
--         员工来源没有写入路径；状态与时间只能取缺省值；BFF 读不到、改不了、删不掉；文本 1–500 字、不含控制与格式字符；分类与路由模板
--         只收固定形状；审核台 staff 只经函数读（不透明指针，不给 student_id 与提交者）与改状态，非 staff 一律 42501；其余角色无权；
--   退出：只有有效绑定家长能发起（别户、学生本人、不存在同一个 42501）；撤回每类最新为 granted 的同意（追加 revoked、版本沿用）、
--         作废学生会话、写一行退出记录、返回需要作废白名单条目的家长账号（只含已没有别的在测孩子的有效绑定家长，不含手机号）；
--         重复发起幂等；之后反馈插入被拒、已有数据保留；internal_test_exited 对无权与不存在同为 false；审核台可核对退出状态
--         （内测专用指针 it-，与复核队列指针不互通）；清单登记（反馈 delete、退出记录 exempt）；
--   库层判据（决定记录（续）第 4 条）：student_processing_allowed 不依赖调用者，档案存在、未删除、未退出、有有效同意（不满 14 周岁另要
--         监护人同意）才为真，只授学习、洞察、调度；已退出的档案库里拒绝再签同意与签发学生会话；判分抽检不抽不允许处理的学生。
\set GA '''9a590000-0000-4000-8000-000000000001'''
\set GB '''9a590000-0000-4000-8000-000000000002'''
\set GX '''9a590000-0000-4000-8000-000000000003'''
\set GR '''9a590000-0000-4000-8000-000000000004'''
\set PR '''+99900005904'''
\set AS '''9a590000-0000-4000-8000-000000000011'''
\set AS2 '''9a590000-0000-4000-8000-000000000012'''
\set AT '''9a590000-0000-4000-8000-000000000013'''
\set ST '''9a590000-0000-4000-8000-000000000021'''
\set STX '''9a590000-0000-4000-8000-000000000022'''
\set H1 '''9a590000-0000-4000-8000-0000000000a1'''
\set H2 '''9a590000-0000-4000-8000-0000000000a2'''
\set S '''9a590000-0000-4000-8000-000000000c01'''
\set S2 '''9a590000-0000-4000-8000-000000000c02'''
\set T '''9a590000-0000-4000-8000-000000000c03'''
\set MISSING '''9a590000-0000-4000-8000-0000000000ff'''
\set AU '''9a590000-0000-4000-8000-000000000014'''
\set U '''9a590000-0000-4000-8000-000000000c04'''
\set PA '''+99900005901'''
\set PB '''+99900005902'''
\set PX '''+99900005903'''

BEGIN;
-- 以 BFF 身份写一条反馈（提交者、学生、来源、分类、文本、路由模板都由调用方给，便于构造越权用例）
CREATE FUNCTION t.zg_fb(p_acct uuid, p_submitter uuid, p_student uuid, p_source text, p_category text DEFAULT 'malfunction',
                        p_body text DEFAULT '合成反馈：按钮点了没有反应', p_route text DEFAULT NULL) RETURNS text LANGUAGE sql AS $$
  SELECT t.m_as('starbuddy_bff', p_acct, format(
    'WITH i AS (INSERT INTO platform.feedback (submitter_account_id, student_id, source, category, body, route_template) '
    'VALUES (%L, %L, %L, %L, %L, %L)) SELECT ''ok''', p_submitter, p_student, p_source, p_category, p_body, p_route)) $$;
-- 调用一句 SQL 的字符串形式（给 t.throws）
CREATE FUNCTION t.zg_fb_sql(p_acct uuid, p_submitter uuid, p_student uuid, p_source text, p_category text DEFAULT 'malfunction',
                            p_body text DEFAULT '合成反馈', p_route text DEFAULT NULL) RETURNS text LANGUAGE sql AS $$
  SELECT format('SELECT t.zg_fb(%L, %L, %L, %L, %L, %L, %L)', p_acct, p_submitter, p_student, p_source, p_category, p_body, p_route) $$;
-- 以某个角色、某个账号执行一句 SQL 的字符串形式（给 t.throws；内层 SQL 整体按字面量转义）
CREATE FUNCTION t.zg_as(p_role text, p_acct uuid, p_sql text) RETURNS text LANGUAGE sql AS $$
  SELECT format('SELECT t.m_as(%L, %L, %L)', p_role, p_acct, p_sql) $$;
-- 以某个应用角色执行一句（不设账号），返回文本
CREATE FUNCTION t.zg_role(p_role text, p_sql text) RETURNS text LANGUAGE plpgsql AS $$
DECLARE v_prev text := current_user; v_out text;
BEGIN
  EXECUTE format('SET LOCAL ROLE %I', p_role);
  EXECUTE p_sql INTO v_out;
  EXECUTE format('SET LOCAL ROLE %I', v_prev);
  RETURN v_out;
END $$;
GRANT EXECUTE ON FUNCTION t.zg_role(text, text) TO PUBLIC;
CREATE FUNCTION t.zg_cnt(p_student uuid) RETURNS bigint LANGUAGE sql AS $$
  SELECT count(*) FROM platform.feedback WHERE student_id IS NOT DISTINCT FROM p_student $$;

-- ================= 夹具（超级用户） =================
INSERT INTO platform.account (id, kind, status) VALUES (:GA, 'guardian', 'active'), (:GB, 'guardian', 'active'), (:GX, 'guardian', 'active'),
  (:GR, 'guardian', 'active'), (:AS, 'student', 'active'), (:AS2, 'student', 'active'), (:AT, 'student', 'active'), (:ST, 'staff', 'active'),
  (:STX, 'staff', 'disabled'), (:AU, 'student', 'active');
INSERT INTO platform.account_contact (account_id, phone_e164, verified_at) VALUES (:GA, :PA, now()), (:GB, :PB, now()), (:GX, :PX, now()),
  (:GR, :PR, now());
INSERT INTO platform.household (id, created_by_account) VALUES (:H1, :GA), (:H2, :GX);
INSERT INTO platform.student_profile (id, account_id, household_id, grade, region, birth_year_month) VALUES
  (:S, :AS, :H1, 8, 'nanjing', '2013-03-01'), (:S2, :AS2, :H1, 9, 'nanjing', '2012-03-01'), (:T, :AT, :H2, 8, 'nanjing', '2013-05-01'),
  (:U, :AU, :H2, 9, 'nanjing', '2010-01-01');
INSERT INTO platform.guardian_student_link (guardian_account_id, student_id, granted_via) VALUES
  (:GA, :S, 'household_creator'), (:GA, :S2, 'household_creator'), (:GB, :S, 'invite'), (:GX, :T, 'household_creator'), (:GR, :S, 'invite'),
  (:GX, :U, 'household_creator');
-- GR：曾经绑定 S、已解除（退出时不作废它的白名单条目，也不能发起退出）
UPDATE platform.guardian_student_link SET status = 'revoked', revoked_at = now() WHERE guardian_account_id = :GR;
-- S：隐私政策与监护人同意已签；语音处理签过又撤回（退出时不重复撤回）；S2、T 两份都签
INSERT INTO platform.consent_record (student_id, guardian_account_id, consent_type, document_version, decision, decided_at) VALUES
  (:S, :GA, 'privacy_policy', 'privacy-policy@syn-0059', 'granted', now() - interval '3 days'),
  (:S, :GA, 'guardian_consent_under14', 'guardian-consent@syn-0059', 'granted', now() - interval '3 days'),
  (:S, :GA, 'voice_processing', 'voice@syn-0059', 'granted', now() - interval '3 days'),
  (:S, :GA, 'voice_processing', 'voice@syn-0059', 'revoked', now() - interval '2 days'),
  (:S2, :GA, 'privacy_policy', 'privacy-policy@syn-0059', 'granted', now() - interval '3 days'),
  (:S2, :GA, 'guardian_consent_under14', 'guardian-consent@syn-0059', 'granted', now() - interval '3 days'),
  (:T, :GX, 'privacy_policy', 'privacy-policy@syn-0059', 'granted', now() - interval '3 days'),
  (:U, :GX, 'privacy_policy', 'privacy-policy@syn-0059', 'granted', now() - interval '3 days');
-- T：不满 14 周岁只签了隐私政策（缺监护人同意）；U：已满 14 周岁，只签隐私政策即可
-- S 的学生账号有一个有效会话（家长在本设备打开学生模式），S2 也有一个（不受 S 退出影响）
INSERT INTO platform.auth_session (token_hash, account_id, origin, issued_by_account, created_at, last_seen_at, expires_at, idle_timeout)
SELECT sha256(convert_to('zg-' || a::text, 'UTF8')), a, 'guardian_handoff', :GA, now(), now(), now() + interval '1 day', interval '1 hour'
  FROM unnest(ARRAY[:AS, :AS2]::uuid[]) a;

-- ================= 一、反馈：谁能写 =================
SELECT t.ok(t.zg_fb(:AS, :AS, :S, 'student_self_report', 'display', '合成反馈：星图上的字太小', '/s/[studentId]/star-map') = 'ok',
            '反馈：学生本人写自己的反馈（学生自述，带路由模板）');
SELECT t.ok(t.zg_fb(:GA, :GA, :S, 'guardian_observation', 'slow', E'合成反馈：今日计划打开很慢\n第二行说明') = 'ok',
            '反馈：有效绑定家长写孩子的反馈（家长观察，可以换行）');
SELECT t.ok(t.zg_fb(:GB, :GB, :S, 'guardian_observation') = 'ok', '反馈：同一学生的第二位家长也能写（各存各的）');
SELECT t.ok(t.zg_cnt(:S::uuid) = 3
            AND (SELECT count(*) FROM platform.feedback WHERE student_id = :S AND status = 'new' AND status_changed_at IS NULL) = 3
            AND (SELECT array_agg(source ORDER BY source) FROM platform.feedback WHERE student_id = :S)
                = '{guardian_observation,guardian_observation,student_self_report}',
            '反馈：三条都落库，状态为 new，学生自述与家长观察分开标来源');
SELECT t.throws(t.zg_fb_sql(:AS, :AS, :S2, 'student_self_report'), '42501', '反馈：学生不能替兄妹写');
SELECT t.throws(t.zg_fb_sql(:AS2, :AS2, :S, 'student_self_report'), '42501', '反馈：兄妹不能替学生写');
SELECT t.throws(t.zg_fb_sql(:AS, :AS, :S, 'guardian_observation'), '42501', '反馈：学生不能以家长观察的来源写');
SELECT t.throws(t.zg_fb_sql(:AS, :GA, :S, 'guardian_observation'), '42501', '反馈：提交者必须是当前账号（学生冒充家长）');
SELECT t.throws(t.zg_fb_sql(:GA, :AS, :S, 'student_self_report'), '42501', '反馈：家长不能以学生自述的来源写（冒充学生）');
SELECT t.throws(t.zg_fb_sql(:GX, :GX, :S, 'guardian_observation'), '42501', '反馈：另一户家长不能写别家孩子的反馈');
SELECT t.throws(t.zg_fb_sql(:GX, :GX, :MISSING, 'guardian_observation'), '42501', '反馈：不存在的学生同样拒绝');
SELECT t.throws(t.zg_fb_sql(:GR, :GR, :S, 'guardian_observation'), '42501', '反馈：已解除绑定的家长不能写');
SELECT t.throws(t.zg_fb_sql(NULL, NULL, :S, 'guardian_observation'), '42501', '反馈：没有会话账号时写不进去');
SELECT t.throws(t.zg_fb_sql(:ST, :ST, NULL, 'staff'), '42501', '反馈：员工来源经 BFF 写不进去（本迁移没有员工写入路径）');
SELECT t.throws(t.zg_fb_sql(:GA, :GA, NULL, 'guardian_observation'), '42501', '反馈：家庭来源不带学生经 BFF 写不进去');
SELECT t.throws(format($$INSERT INTO platform.feedback (submitter_account_id, student_id, source, category, body) VALUES (%L, NULL, 'guardian_observation', 'other', '合成')$$, :GA),
                '23514', '反馈：家庭来源必须带学生（库层约束）');
SELECT t.throws(format($$INSERT INTO platform.feedback (submitter_account_id, student_id, source, category, body) VALUES (%L, %L, 'staff', 'other', '合成')$$, :ST, :S),
                '23514', '反馈：员工来源不带学生（库层约束）');
SELECT t.throws(format($$SELECT t.m_as('starbuddy_bff', %L, %L)$$, :GA,
                       format('WITH i AS (INSERT INTO platform.feedback (submitter_account_id, student_id, source, category, body, status) '
                              'VALUES (%L, %L, ''guardian_observation'', ''other'', ''合成'', ''closed'')) SELECT ''x''', :GA, :S)),
                '42501', '反馈：BFF 不能自己设状态（列级 INSERT）');
SELECT t.throws(format($$SELECT t.m_as('starbuddy_bff', %L, %L)$$, :GA,
                       format('WITH i AS (INSERT INTO platform.feedback (submitter_account_id, student_id, source, category, body, created_at) '
                              'VALUES (%L, %L, ''guardian_observation'', ''other'', ''合成'', now() - interval ''1 day'')) SELECT ''x''', :GA, :S)),
                '42501', '反馈：BFF 不能伪造创建时间（列级 INSERT）');
SELECT t.throws(format($$SELECT t.m_as('starbuddy_bff', %L, 'SELECT count(*)::text FROM platform.feedback')$$, :GA), '42501',
                '反馈：BFF 读不到反馈（没有 SELECT）');
SELECT t.throws(format($$SELECT t.m_as('starbuddy_bff', %L, 'WITH u AS (UPDATE platform.feedback SET status = ''closed'') SELECT ''x''')$$, :GA),
                '42501', '反馈：BFF 改不了反馈状态');
SELECT t.throws(format($$SELECT t.m_as('starbuddy_bff', %L, 'WITH d AS (DELETE FROM platform.feedback) SELECT ''x''')$$, :GA), '42501',
                '反馈：BFF 删不掉反馈');
SELECT t.ok(NOT EXISTS (
  SELECT 1 FROM unnest(ARRAY['starbuddy_learning', 'starbuddy_insight', 'starbuddy_kge', 'starbuddy_kge_read', 'starbuddy_scheduler',
                             'starbuddy_report', 'starbuddy_review']) r(role), unnest(ARRAY['SELECT', 'INSERT', 'UPDATE', 'DELETE', 'TRUNCATE']) p(priv)
   WHERE has_table_privilege(r.role, 'platform.feedback', p.priv) OR has_any_column_privilege(r.role, 'platform.feedback', 'SELECT')
      OR has_any_column_privilege(r.role, 'platform.feedback', 'UPDATE') OR has_table_privilege(r.role, 'platform.internal_test_exit', p.priv)
      OR has_any_column_privilege(r.role, 'platform.internal_test_exit', 'SELECT')),
  '反馈与退出记录：学习、洞察、知识、调度、报表、审核台身份对两张表都没有任何表或列权限（审核台只经函数）');
SELECT t.ok(NOT has_any_column_privilege('starbuddy_bff', 'platform.internal_test_exit', 'SELECT')
            AND NOT has_any_column_privilege('starbuddy_bff', 'platform.internal_test_exit', 'INSERT')
            AND NOT has_any_column_privilege('starbuddy_bff', 'platform.feedback', 'UPDATE')
            AND NOT has_column_privilege('starbuddy_bff', 'platform.feedback', 'status', 'INSERT'),
            '退出记录：BFF 不能直接读写（只经函数）；反馈的状态列 BFF 插入不了');

-- ================= 二、反馈：字段规则 =================
SELECT t.throws(t.zg_fb_sql(:AS, :AS, :S, 'student_self_report', 'malfunction', repeat('长', 501)), '23514', '反馈：文本超过 500 字被拒');
SELECT t.ok(t.zg_fb(:AS, :AS, :S, 'student_self_report', 'other', repeat('满', 500)) = 'ok', '反馈：正好 500 字可以');
SELECT t.throws(t.zg_fb_sql(:AS, :AS, :S, 'student_self_report', 'malfunction', E' \n\t '), '23514', '反馈：只有空白的文本被拒');
SELECT t.throws(t.zg_fb_sql(:AS, :AS, :S, 'student_self_report', 'malfunction', E'合成\x01控制字符'), '23514', '反馈：控制字符被拒');
SELECT t.throws(t.zg_fb_sql(:AS, :AS, :S, 'student_self_report', 'malfunction', '合成' || chr(8203) || '零宽'), '23514', '反馈：零宽字符被拒');
SELECT t.throws(t.zg_fb_sql(:AS, :AS, :S, 'student_self_report', 'malfunction', '合成' || chr(8238) || '方向覆盖'), '23514', '反馈：方向覆盖字符被拒');
SELECT t.throws(t.zg_fb_sql(:AS, :AS, :S, 'student_self_report', 'malfunction', '合成' || chr(8232) || '行分隔'), '23514', '反馈：行分隔字符被拒');
SELECT t.throws(t.zg_fb_sql(:AS, :AS, :S, 'student_self_report', 'malfunction', '合成' || chr(133) || 'C1 控制字符'), '23514', '反馈：C1 控制字符被拒');
SELECT t.throws(t.zg_fb_sql(:AS, :AS, :S, 'student_self_report', 'free_text_category'), '23514', '反馈：分类只收固定枚举');
SELECT t.throws(t.zg_fb_sql(:AS, :AS, :S, 'student_self_report', 'display', '合成', '/s/9a590000-0000-4000-8000-000000000c01/today'), '23514',
                '反馈：路由模板不能带 UUID（只收模板形状）');
SELECT t.throws(t.zg_fb_sql(:AS, :AS, :S, 'student_self_report', 'display', '合成', '/s/[studentId]/today?x=1'), '23514', '反馈：路由模板不带查询串');
SELECT t.throws(t.zg_fb_sql(:AS, :AS, :S, 'student_self_report', 'display', '合成', 'https://example.invalid/'), '23514', '反馈：路由模板不是地址');
SELECT t.ok(t.zg_fb(:AS, :AS, :S, 'student_self_report', 'login', '合成', '/s/[studentId]/learn/[subject]/practice') = 'ok',
            '反馈：多段路由模板可以');

-- ================= 三、审核台：只经函数读与改状态 =================
SELECT t.ok((SELECT count(*) FROM (SELECT (t.m_as('starbuddy_review', :ST,
              'SELECT json_agg(r)::text FROM platform.review_feedback_list(NULL, 200, NULL, NULL) r'))::json) j) = 1, '审核台：staff 读得到反馈列表');
SELECT t.m_as('starbuddy_review', :ST, 'SELECT json_agg(r ORDER BY r.created_at, r.id)::text FROM platform.review_feedback_list(NULL, 200, NULL, NULL) r')
  AS fb_list \gset
SELECT t.ok(json_array_length(:'fb_list'::json) = 5
            AND position(:S IN :'fb_list') = 0 AND position(:AS IN :'fb_list') = 0 AND position(:GA IN :'fb_list') = 0
            AND position('合成反馈：星图上的字太小' IN :'fb_list') > 0
            AND (SELECT bool_and(e ->> 'student_ref' = platform.internal_test_ref(:S::uuid)) FROM json_array_elements(:'fb_list'::json) e)
            AND platform.internal_test_ref(:S::uuid) LIKE 'it-%'
            AND platform.internal_test_ref(:S::uuid) <> platform.review_student_ref(:S::uuid)
            AND position(substr(platform.review_student_ref(:S::uuid), 3) IN platform.internal_test_ref(:S::uuid)) = 0
            AND (SELECT array_agg(DISTINCT k ORDER BY k) FROM json_array_elements(:'fb_list'::json) e, json_object_keys(e) k)
                = '{body,category,created_at,id,route_template,source,status,status_changed_at,student_ref}',
            '审核台：列表有分类、文本、路由模板、来源与状态，学生只给不透明指针，没有 student_id 与提交者账号');
SELECT (json_array_element(:'fb_list'::json, 0) ->> 'id') AS fb1 \gset
SELECT t.ok(t.m_as('starbuddy_review', :ST, format('SELECT platform.review_feedback_set_status(%L, ''triaged'')', :'fb1')) = 'triaged',
            '审核台：staff 改状态为 triaged');
SELECT t.ok((SELECT status = 'triaged' AND status_changed_by = :ST AND status_changed_at IS NOT NULL FROM platform.feedback WHERE id = :'fb1'),
            '审核台：改状态记下改的人与时间');
SELECT t.ok(t.m_as('starbuddy_review', :ST, format('SELECT count(*)::text FROM platform.review_feedback_list(''triaged'', 50, NULL, NULL) r WHERE r.id = %L', :'fb1')) = '1'
            AND t.m_as('starbuddy_review', :ST, 'SELECT count(*)::text FROM platform.review_feedback_list(''new'', 50, NULL, NULL)') = '4',
            '审核台：按状态筛');
SELECT t.ok(t.m_as('starbuddy_review', :ST, format('SELECT platform.review_feedback_set_status(%L, ''closed'')', :'fb1')) = 'closed'
            AND t.m_as('starbuddy_review', :ST, format('SELECT platform.review_feedback_set_status(%L, ''triaged'')', :'fb1')) = 'triaged',
            '审核台：可以结束，也可以重新打开');
SELECT t.ok(t.m_as('starbuddy_review', :ST, 'SELECT count(*)::text FROM platform.review_feedback_list(NULL, 2, NULL, NULL)') = '2'
            AND t.m_as('starbuddy_review', :ST, format('SELECT count(*)::text FROM platform.review_feedback_list(NULL, 200, %L::timestamptz, %L::uuid)',
                       (json_array_element(:'fb_list'::json, 4) ->> 'created_at'), (json_array_element(:'fb_list'::json, 4) ->> 'id'))) = '4',
            '审核台：按 (创建时间, id) 游标翻页');
SELECT t.throws(t.zg_as('starbuddy_review', :ST, format('SELECT platform.review_feedback_set_status(%L, ''done'')', :'fb1')),
                '22023', '审核台：状态只收 new / triaged / closed');
SELECT t.throws(t.zg_as('starbuddy_review', :ST, 'SELECT count(*)::text FROM platform.review_feedback_list(NULL, 0, NULL, NULL)'),
                '22023', '审核台：每页条数有界');
SELECT t.throws(t.zg_as('starbuddy_review', :ST, format('SELECT platform.review_feedback_set_status(%L, ''closed'')', :MISSING)),
                'P0002', '审核台：不存在的反馈');
SELECT t.throws(t.zg_as('starbuddy_review', a, 'SELECT count(*)::text FROM platform.review_feedback_list(NULL, 10, NULL, NULL)'), '42501',
                format('审核台：非 staff（%s）读不到反馈', w))
  FROM (VALUES (:GA::uuid, '家长'), (:AS::uuid, '学生'), (:STX::uuid, '停用的 staff'), (NULL::uuid, '未登录')) v(a, w);
SELECT t.throws(t.zg_as('starbuddy_review', a, format('SELECT platform.review_feedback_set_status(%L, ''closed'')', :'fb1')),
                '42501', format('审核台：非 staff（%s）改不了状态', w))
  FROM (VALUES (:GA::uuid, '家长'), (:STX::uuid, '停用的 staff')) v(a, w);
SELECT t.ok((SELECT array_agg(r.role ORDER BY r.role) FROM unnest(ARRAY['starbuddy_bff', 'starbuddy_learning', 'starbuddy_review', 'starbuddy_scheduler',
               'starbuddy_insight', 'starbuddy_kge', 'starbuddy_report']) r(role)
              WHERE has_function_privilege(r.role, 'platform.review_feedback_list(text, integer, timestamptz, uuid)', 'EXECUTE')
                 OR has_function_privilege(r.role, 'platform.review_feedback_set_status(uuid, text)', 'EXECUTE')
                 OR has_function_privilege(r.role, 'platform.review_internal_test_exits(integer)', 'EXECUTE')) = '{starbuddy_review}'
            AND (SELECT array_agg(r.role ORDER BY r.role) FROM unnest(ARRAY['starbuddy_bff', 'starbuddy_learning', 'starbuddy_review', 'starbuddy_scheduler',
               'starbuddy_insight', 'starbuddy_kge', 'starbuddy_report']) r(role)
              WHERE has_function_privilege(r.role, 'platform.exit_internal_test(uuid)', 'EXECUTE')
                 OR has_function_privilege(r.role, 'platform.internal_test_exited(uuid)', 'EXECUTE')) = '{starbuddy_bff}',
            '函数授权：审核台三个函数只授签字身份，退出两个函数只授 BFF');

-- ================= 三·五、库层判据（退出之前） =================
SELECT t.ok(platform.student_processing_allowed(s) = want, format('库层判据（退出前）：%s', w))
  FROM (VALUES (:S::uuid, true, 'S 两份都签、未满 14 周岁'), (:S2::uuid, true, 'S2 两份都签'),
               (:T::uuid, false, 'T 未满 14 周岁、缺监护人同意'), (:U::uuid, true, 'U 已满 14 周岁、只签隐私政策'),
               (:MISSING::uuid, false, '不存在的学生')) v(s, want, w);
SELECT t.ok(t.zg_role(r, format('SELECT platform.student_processing_allowed(%L)::text', :S)) = 'true',
            format('库层判据：%s 能调用，不依赖调用者账号（不设 app.account_id）', r))
  FROM unnest(ARRAY['starbuddy_learning', 'starbuddy_insight', 'starbuddy_scheduler']) r;
SELECT t.ok(NOT EXISTS (SELECT 1 FROM unnest(ARRAY['starbuddy_bff', 'starbuddy_kge', 'starbuddy_kge_read', 'starbuddy_review', 'starbuddy_report',
                                                   'starbuddy_eraser']) r
                         WHERE has_function_privilege(r, 'platform.student_processing_allowed(uuid)', 'EXECUTE'))
            AND NOT has_function_privilege('public', 'platform.student_processing_allowed(uuid)', 'EXECUTE'),
            '库层判据：只授学习、洞察、调度三个服务角色');
-- 已删除（0035 执行删除后 erased_at 非空）：判据为假
UPDATE platform.student_profile SET erased_at = now() WHERE id = :U;
SELECT t.ok(NOT platform.student_processing_allowed(:U::uuid), '库层判据：已删除的档案为假');
UPDATE platform.student_profile SET erased_at = NULL WHERE id = :U;
-- 撤回隐私政策之后为假（有有效同意才为真）；补签又为真
INSERT INTO platform.consent_record (student_id, guardian_account_id, consent_type, document_version, decision, decided_at)
VALUES (:U, :GX, 'privacy_policy', 'privacy-policy@syn-0059', 'revoked', now() - interval '2 days');
SELECT t.ok(NOT platform.student_processing_allowed(:U::uuid), '库层判据：隐私政策最新一条是撤回时为假');
INSERT INTO platform.consent_record (student_id, guardian_account_id, consent_type, document_version, decision, decided_at)
VALUES (:U, :GX, 'privacy_policy', 'privacy-policy@syn-0059', 'granted', now() - interval '1 day');
SELECT t.ok(platform.student_processing_allowed(:U::uuid), '库层判据：补签之后为真');
-- 判分抽检用的判分（S 与 S2 各 200 条；S 退出之后再抽）
SET ROLE starbuddy_learning;
INSERT INTO platform.grade_result (student_id, subject, attempt_id, attempt_occurred_at, verdict, rubric_version, score_total, max_total,
  calibrated, confidence, source_version, model_version, verifier_id, verifier_version, human_review_required, semantics, evidence_ids,
  source_request_id)
SELECT s, 'zhixing', 'a8000000-0000-4000-8000-0000000000a8', '2026-10-09 11:30:00+00', 'correct', 'aya-symbolic-ts@0.1.0', 1, 1, true, 1,
       'rubric:aya-symbolic-ts@0.1.0', 'algo:aya-symbolic-ts@0.1.0', 'aya-symbolic-ts', '0.1.0', false, 'formal',
       '{e1000000-0000-4000-8000-000000000001}', gen_random_uuid()
  FROM unnest(ARRAY[:S, :S2]::uuid[]) s, generate_series(1, 200);
RESET ROLE;

-- ================= 四、退出测试 =================
SELECT t.throws(t.zg_as('starbuddy_bff', a, format('SELECT exit_at::text FROM platform.exit_internal_test(%L)', s)), '42501',
                format('退出：%s不能发起', w))
  FROM (VALUES (:GX::uuid, :S::uuid, '另一户家长'), (:AS::uuid, :S::uuid, '学生本人'), (:GA::uuid, :MISSING::uuid, '不存在的学生：'),
               (:GR::uuid, :S::uuid, '已解除绑定的家长'),
               (NULL::uuid, :S::uuid, '没有会话账号时')) v(a, s, w);
SELECT t.ok(NOT EXISTS (SELECT 1 FROM platform.internal_test_exit WHERE student_id = :S)
            AND (SELECT count(*) FROM platform.consent_record WHERE student_id = :S) = 4, '退出：被拒的发起什么都没写');
SELECT t.m_as('starbuddy_bff', :GA, format('SELECT row_to_json(r)::text FROM platform.exit_internal_test(%L) r', :S)) AS exit1 \gset
SELECT t.ok((:'exit1'::json ->> 'newly')::boolean
            AND (:'exit1'::json -> 'revoked_types')::jsonb = '["guardian_consent_under14", "privacy_policy"]'
            AND (:'exit1'::json -> 'revoke_accounts')::jsonb = jsonb_build_array(:GB::text)
            AND position(:PA IN :'exit1') = 0 AND position(:PB IN :'exit1') = 0
            AND position(encode(sha256(convert_to(:PB, 'UTF8')), 'hex') IN :'exit1') = 0,
            '退出：有效绑定家长发起，撤回隐私政策与监护人同意（已撤回的语音处理不重复撤回）；要作废白名单条目的只有 GB'
            '（GA 还有在测的 S2；已解除绑定的 GR 不算；不返回手机号或其摘要）');
SELECT t.ok((SELECT array_agg(consent_type || ':' || decision || ':' || document_version ORDER BY consent_type)
               FROM (SELECT DISTINCT ON (consent_type) consent_type, decision, document_version FROM platform.consent_record WHERE student_id = :S
                      ORDER BY consent_type, decided_at DESC, (decision = 'revoked') DESC) l)
            = '{guardian_consent_under14:revoked:guardian-consent@syn-0059,privacy_policy:revoked:privacy-policy@syn-0059,voice_processing:revoked:voice@syn-0059}'
            AND (SELECT count(*) FROM platform.consent_record WHERE student_id = :S) = 6
            AND (SELECT bool_and(guardian_account_id = :GA) FROM platform.consent_record WHERE student_id = :S AND decided_at > now() - interval '1 minute'),
            '退出：每类同意最新一条都是撤回，版本沿用原记录，撤回人是发起的家长；同意记录只追加');
SELECT t.ok((SELECT revoked_reason FROM platform.auth_session WHERE account_id = :AS) = 'logout_all'
            AND (SELECT revoked_at IS NULL FROM platform.auth_session WHERE account_id = :AS2),
            '退出：学生账号的会话被作废，兄妹的会话不受影响');
SELECT t.ok((SELECT requested_by = :GA AND consents_revoked = '{guardian_consent_under14,privacy_policy}' AND sessions_revoked = 1
               FROM platform.internal_test_exit WHERE student_id = :S), '退出：写一行退出记录（发起人、撤回的同意类型、作废的会话数）');
SELECT t.m_as('starbuddy_bff', :GB, format('SELECT row_to_json(r)::text FROM platform.exit_internal_test(%L) r', :S)) AS exit2 \gset
SELECT t.ok(NOT (:'exit2'::json ->> 'newly')::boolean AND (:'exit2'::json ->> 'exit_at') = (:'exit1'::json ->> 'exit_at')
            AND (:'exit2'::json -> 'revoked_types')::jsonb = (:'exit1'::json -> 'revoked_types')::jsonb
            AND (:'exit2'::json -> 'revoke_accounts')::jsonb = (:'exit1'::json -> 'revoke_accounts')::jsonb
            AND (SELECT count(*) FROM platform.consent_record WHERE student_id = :S) = 6
            AND (SELECT count(*) FROM platform.internal_test_exit WHERE student_id = :S) = 1,
            '退出：重复发起（另一位家长）幂等：同样的结果，不再追加同意记录、不再写退出记录');
SELECT t.ok(t.m_as('starbuddy_bff', a, format('SELECT platform.internal_test_exited(%L)::text', s)) = want, format('退出判据：%s', w))
  FROM (VALUES (:GA::uuid, :S::uuid, 'true', '发起的家长看到已退出'), (:GB::uuid, :S::uuid, 'true', '另一位绑定家长看到已退出'),
               (:AS::uuid, :S::uuid, 'true', '学生本人看到已退出'), (:GA::uuid, :S2::uuid, 'false', '兄妹没有退出'),
               (:GX::uuid, :S::uuid, 'false', '另一户家长：与不存在相同'), (:GX::uuid, :MISSING::uuid, 'false', '不存在的学生'),
               (NULL::uuid, :S::uuid, 'false', '没有会话账号')) v(a, s, want, w);
SELECT t.throws(t.zg_fb_sql(:AS, :AS, :S, 'student_self_report'), '42501', '退出之后：学生本人写不了新反馈');
SELECT t.throws(t.zg_fb_sql(:GA, :GA, :S, 'guardian_observation'), '42501', '退出之后：家长也写不了这个孩子的新反馈');
SELECT t.ok(t.zg_fb(:GA, :GA, :S2, 'guardian_observation') = 'ok', '退出之后：同一家长对没有退出的兄妹照常能写');
SELECT t.ok(t.zg_cnt(:S::uuid) = 5, '退出之后：已有反馈保留（测试结束统一删除）');
SELECT t.ok(t.m_as('starbuddy_review', :ST, 'SELECT json_agg(r)::text FROM platform.review_internal_test_exits(50) r')::jsonb
            = jsonb_build_array(jsonb_build_object('student_ref', platform.internal_test_ref(:S::uuid), 'exited_at', (:'exit1'::json ->> 'exit_at')::timestamptz,
                                                   'consents_revoked', '["guardian_consent_under14", "privacy_policy"]'::jsonb, 'consent_active', false,
                                                   'sessions_revoked', 1, 'erasure_status', NULL, 'erased', false)),
            '审核台：退出状态（不透明指针、时间、撤回的同意、已无有效同意、作废的会话数、还没有删除请求、未删除）');
SELECT t.throws(t.zg_as('starbuddy_review', a, 'SELECT count(*)::text FROM platform.review_internal_test_exits(10)'), '42501',
                format('审核台：非 staff（%s）看不到退出状态', w))
  FROM (VALUES (:GA::uuid, '家长'), (:STX::uuid, '停用的 staff')) v(a, w);
-- 测试结束统一删除走 0035 的删除流程：发起删除请求后审核台能看到请求状态
SELECT t.m_as('starbuddy_bff', :GA, format('SELECT platform.request_erasure(%L)::text', :S)) AS zg_req \gset
SELECT t.ok((t.m_as('starbuddy_review', :ST, 'SELECT json_agg(r)::text FROM platform.review_internal_test_exits(50) r')::json -> 0 ->> 'erasure_status') = 'pending',
            '审核台：退出的档案发起删除请求后，退出状态里能看到删除请求的状态');

-- ================= 四·五、库层判据（退出之后） =================
SELECT t.ok(NOT platform.student_processing_allowed(:S::uuid) AND platform.student_processing_allowed(:S2::uuid),
            '库层判据（退出后）：退出的 S 为假，兄妹 S2 不受影响');
-- 只有退出记录、同意仍有效（例如撤回同意的那一步之外的口径）：判据仍为假，退出记录本身就够
INSERT INTO platform.internal_test_exit (student_id, requested_by) VALUES (:U, :GX);
SELECT t.ok(NOT platform.student_processing_allowed(:U::uuid), '库层判据：有退出记录即为假（不只靠撤回同意）');
DELETE FROM platform.internal_test_exit WHERE student_id = :U;
SELECT t.zg_role('starbuddy_scheduler', $$SELECT platform.review_sample_spot_checks('zhixing', now() - interval '1 day', 0.5)::text$$) AS zg_spot \gset
SELECT t.ok(:'zg_spot'::int > 0, format('判分抽检：这次抽中了兄妹的判分（%s 条）', :'zg_spot'));
SELECT t.ok(NOT EXISTS (SELECT 1 FROM platform.review_task WHERE student_id = :S AND 'spot_check' = ANY (reasons::text[]))
            AND EXISTS (SELECT 1 FROM platform.review_task WHERE student_id = :S2 AND 'spot_check' = ANY (reasons::text[])),
            '判分抽检：已退出学生的判分不进复核队列，兄妹照常抽');
SELECT t.throws(format($$INSERT INTO platform.consent_record (student_id, guardian_account_id, consent_type, document_version, decision)
                         VALUES (%L, %L, 'privacy_policy', 'privacy-policy@syn-0059', 'granted')$$, :S, :GA),
                '42501', '退出之后：库里拒绝再签同意（超级用户插入同样被触发器拒绝）');
SELECT t.throws(t.zg_as('starbuddy_bff', :GA, format($$WITH i AS (INSERT INTO platform.consent_record (student_id, guardian_account_id, consent_type,
                         document_version, decision) VALUES (%L, platform.current_account_id(), 'guardian_consent_under14', 'guardian-consent@syn-0059',
                         'granted')) SELECT 'x'$$, :S)),
                '42501', '退出之后：家长经 BFF 也签不了同意');
SELECT t.ok(t.m_as('starbuddy_bff', :GA, format($$WITH i AS (INSERT INTO platform.consent_record (student_id, guardian_account_id, consent_type,
                     document_version, decision) VALUES (%L, platform.current_account_id(), 'pilot_participation', 'pilot@syn-0059', 'revoked'))
                     SELECT 'ok'$$, :S)) = 'ok',
            '退出之后：撤回同意照常可写（同意记录只追加）');
SELECT t.throws(format($$INSERT INTO platform.auth_session (token_hash, account_id, origin, issued_by_account, created_at, last_seen_at, expires_at,
                         idle_timeout) VALUES (sha256('zg-after-exit'::bytea), %L, 'guardian_handoff', %L, now(), now(), now() + interval '1 day',
                         interval '1 hour')$$, :AS, :GA),
                '42501', '退出之后：库里拒绝给该学生账号签发会话');
SELECT t.ok((SELECT count(*) FROM platform.auth_session WHERE account_id = :AS AND revoked_at IS NULL) = 0,
            '退出之后：该学生账号没有有效会话');
INSERT INTO platform.auth_session (token_hash, account_id, origin, issued_by_account, created_at, last_seen_at, expires_at, idle_timeout)
VALUES (sha256('zg-sibling-after-exit'::bytea), :AS2, 'guardian_handoff', :GA, now(), now(), now() + interval '1 day', interval '1 hour');
SELECT t.ok((SELECT count(*) FROM platform.auth_session WHERE account_id = :AS2 AND revoked_at IS NULL) = 2,
            '退出之后：兄妹照常能签发会话');
-- 兄妹也退出：S 已退出，GA 已没有别的在测孩子，这次返回 GA（S2 只绑定 GA）
SELECT t.m_as('starbuddy_bff', :GA, format('SELECT row_to_json(r)::text FROM platform.exit_internal_test(%L) r', :S2)) AS exit3 \gset
SELECT t.ok((:'exit3'::json -> 'revoke_accounts')::jsonb = jsonb_build_array(:GA::text),
            '退出：最后一个在测孩子退出时，这位家长的白名单条目才作废');
SELECT t.ok(has_table_privilege('starbuddy_authz', 'platform.consent_record', 'INSERT')
            AND NOT has_table_privilege('starbuddy_authz', 'platform.consent_record', 'UPDATE')
            AND NOT has_table_privilege('starbuddy_authz', 'platform.consent_record', 'DELETE'),
            '函数属主：对同意记录只有 INSERT（只为退出时追加撤回），没有 UPDATE / DELETE');
SELECT t.ok(NOT EXISTS (
  SELECT 1 FROM unnest(ARRAY['starbuddy_insight', 'starbuddy_learning', 'starbuddy_kge', 'starbuddy_scheduler']) r(role),
                unnest(ARRAY['SELECT', 'INSERT', 'UPDATE', 'DELETE', 'TRUNCATE', 'REFERENCES', 'TRIGGER']) p(priv)
   WHERE has_table_privilege(r.role, 'platform.feedback', p.priv)
      OR (p.priv IN ('SELECT', 'INSERT', 'UPDATE', 'REFERENCES') AND has_any_column_privilege(r.role, 'platform.feedback', p.priv))),
  '反馈：洞察、学习、知识、调度身份对 platform.feedback 没有任何表或列权限');

-- ================= 五、个人层表清单 =================
SELECT t.ok((SELECT strategy = 'delete' AND erase_scope = 'facts' AND export_topic IS NULL FROM platform.personal_table_registry
              WHERE nspname = 'platform' AND relname = 'feedback')
            AND (SELECT strategy = 'exempt' AND exempt_reason IS NOT NULL FROM platform.personal_table_registry
                  WHERE nspname = 'platform' AND relname = 'internal_test_exit')
            AND has_table_privilege('starbuddy_eraser', 'platform.feedback', 'DELETE')
            AND NOT has_table_privilege('starbuddy_eraser', 'platform.internal_test_exit', 'DELETE')
            AND NOT has_column_privilege('starbuddy_eraser', 'platform.feedback', 'body', 'SELECT'),
            '清单：反馈随删除请求删除（删除身份只读学生键列，读不到文本），退出记录豁免保留；反馈不进导出包（学生自述与家长观察混在一起，待人确认）');
ROLLBACK;
