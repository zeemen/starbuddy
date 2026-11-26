-- 0064 讲评课会话 platform.lesson_session 与课内提问放行 platform.lesson_session_ask（specs/stage-lesson-session ③b-3 验收：
-- 会话表不存讲解文字与问答；RLS 有测试；删除与导出覆盖新表；计量按会话的那一笔预留核对）。
-- 全部合成数据（规则 8）：本文件自建两户（家长 gA → 学生 S1，家长 gB → 学生 S2）、一份测试额度配置与三个额度桶；
-- 预留以超级用户直写（触发器与约束照常生效），时间取 70 号换上的计量时钟（platform.meter_now() 读 t.clock）。
-- 整个文件在一个事务里，结束回滚。删除请求的覆盖（会话随删除请求删除、回执计数）在 79q 的全库扫描里。
\set gA '''f0640000-0000-4000-8000-0000000000a1'''
\set gB '''f0640000-0000-4000-8000-0000000000a2'''
\set aS1 '''f0640000-0000-4000-8000-0000000000a3'''
\set aS2 '''f0640000-0000-4000-8000-0000000000a4'''
\set H1 '''f0640000-0000-4000-8000-0000000000b1'''
\set H2 '''f0640000-0000-4000-8000-0000000000b2'''
\set S1 '''f0640000-0000-4000-8000-0000000000c1'''
\set S2 '''f0640000-0000-4000-8000-0000000000c2'''
\set SUB '''f0640000-0000-4000-8000-0000000000d1'''
\set SUB2 '''f0640000-0000-4000-8000-0000000000d2'''
\set EW '''f0640000-0000-4000-8000-0000000000e1'''
\set EG '''f0640000-0000-4000-8000-0000000000e2'''
\set EW2 '''f0640000-0000-4000-8000-0000000000e3'''
\set U1 '''f0640000-0000-4000-8000-000000000101'''
\set U2 '''f0640000-0000-4000-8000-000000000102'''
\set U3 '''f0640000-0000-4000-8000-000000000103'''
\set U4 '''f0640000-0000-4000-8000-000000000104'''
\set U5 '''f0640000-0000-4000-8000-000000000105'''
\set U6 '''f0640000-0000-4000-8000-000000000106'''
\set U7 '''f0640000-0000-4000-8000-000000000107'''
\set U8 '''f0640000-0000-4000-8000-000000000108'''
\set U9 '''f0640000-0000-4000-8000-000000000109'''
\set K1 '''f0640000-0000-4000-8000-000000000201'''
\set K2 '''f0640000-0000-4000-8000-000000000202'''
\set K3 '''f0640000-0000-4000-8000-000000000203'''
\set K4 '''f0640000-0000-4000-8000-000000000204'''
\set K5 '''f0640000-0000-4000-8000-000000000205'''
\set K6 '''f0640000-0000-4000-8000-000000000206'''
\set K7 '''f0640000-0000-4000-8000-000000000207'''
\set K8 '''f0640000-0000-4000-8000-000000000208'''
\set K9 '''f0640000-0000-4000-8000-000000000209'''
\set U10 '''f0640000-0000-4000-8000-000000000110'''
\set K10 '''f0640000-0000-4000-8000-000000000210'''
\set U11 '''f0640000-0000-4000-8000-000000000111'''
\set K11 '''f0640000-0000-4000-8000-000000000211'''
\set U12 '''f0640000-0000-4000-8000-000000000112'''
\set K12 '''f0640000-0000-4000-8000-000000000212'''
\set EV '''f0640000-0000-4000-8000-0000000000e4'''
\set A1 '''f0640000-0000-4000-8000-000000000301'''
\set A2 '''f0640000-0000-4000-8000-000000000302'''
\set A3 '''f0640000-0000-4000-8000-000000000303'''
\set A4 '''f0640000-0000-4000-8000-000000000304'''
\set FP '''0000000000000000000000000000000000000000000000000000000000000064'''
BEGIN;

-- ================= 夹具：S1 的测试额度（讲评课与错因分析两个桶），S2 一个讲评课桶 =================
INSERT INTO platform.account (id, kind) VALUES (:gA, 'guardian'), (:gB, 'guardian'), (:aS1, 'student'), (:aS2, 'student');
INSERT INTO platform.household (id, created_by_account) VALUES (:H1, :gA), (:H2, :gB);
INSERT INTO platform.student_profile (id, account_id, household_id, grade, region)
VALUES (:S1, :aS1, :H1, 9, 'nanjing'), (:S2, :aS2, :H2, 8, 'nanjing');
INSERT INTO platform.guardian_student_link (guardian_account_id, student_id, granted_via)
VALUES (:gA, :S1, 'household_creator'), (:gB, :S2, 'household_creator');
INSERT INTO platform.plan_config (version, note_code) VALUES ('lsn-test@1', 'db_test');
INSERT INTO platform.plan_allowance (config_version, tier, resource_type, quantity, fair_use)
SELECT 'lsn-test@1', tier, resource_type, quantity, fair_use FROM platform.plan_allowance WHERE config_version = 'ch8-hypothesis@1';
INSERT INTO platform.plan_feature (config_version, tier, feature_code, value_code)
SELECT 'lsn-test@1', tier, feature_code, value_code FROM platform.plan_feature WHERE config_version = 'ch8-hypothesis@1';
UPDATE platform.plan_config SET status = 'published', published_at = now() WHERE version = 'lsn-test@1';
INSERT INTO platform.subscription (id, household_id, student_id, tier, config_version, source, period_start, period_end)
SELECT x.id, s.household_id, s.id, 'pro', 'lsn-test@1', 'pilot_virtual', platform.meter_now() - interval '1 day', platform.meter_now() + interval '20 days'
  FROM (VALUES (:SUB::uuid, :S1::uuid), (:SUB2, :S2)) x(id, student) JOIN platform.student_profile s ON s.id = x.student;
INSERT INTO platform.entitlement (id, subscription_id, student_id, resource_type, period_start, period_end, granted)
SELECT x.id, x.sub, x.student, x.res, s.period_start, s.period_end, 10
  FROM (VALUES (:EW::uuid, :SUB::uuid, :S1::uuid, 'lesson_whiteboard'), (:EG, :SUB, :S1, 'mistake_analysis'),
               (:EW2, :SUB2, :S2, 'lesson_whiteboard'), (:EV, :SUB, :S1, 'voice_realtime')) x(id, sub, student, res)
  JOIN platform.subscription s ON s.id = x.sub;

-- 预留（讲评课：teach / lesson_whiteboard，有效 80 分钟）：
-- U1 S1 预留中（主会话）；U2 S1 预留中（下发失败的会话）；U3 S1 grade / mistake_analysis 预留中（不是讲评课）；
-- U4 S1 预留中但已过期（过期任务还没跑）；U5 S1 已结算；U6 S2 预留中；U7 S1 预留中（过期测试）；U8 S1 预留中（上限测试）；
-- U9 S1 预留中（之后释放）；U10 S1 预留中（之后标成 expired）；
-- U11 S1 teach 但资源是 voice_realtime；U12 S1 lesson_whiteboard 但来源方法是 diagnose（超级用户直写的构造数据，meter_reserve 不会产生）
INSERT INTO platform.usage_reservation (usage_id, idempotency_key, request_fingerprint, entitlement_id, student_id, resource_type,
                                        reserved_qty, source_method, reserved_at, expires_at)
SELECT u, k, :FP, e, st, res, 1, m, platform.meter_now() - interval '9 minutes', platform.meter_now() + ttl
  FROM (VALUES (:U1::uuid, :K1::text, :EW::uuid, :S1::uuid, 'lesson_whiteboard', 'teach', interval '80 minutes'),
               (:U2, :K2, :EW, :S1, 'lesson_whiteboard', 'teach', interval '80 minutes'),
               (:U3, :K3, :EG, :S1, 'mistake_analysis', 'grade', interval '1 minute'),
               (:U4, :K4, :EW, :S1, 'lesson_whiteboard', 'teach', interval '-1 minute'),
               (:U5, :K5, :EW, :S1, 'lesson_whiteboard', 'teach', interval '80 minutes'),
               (:U6, :K6, :EW2, :S2, 'lesson_whiteboard', 'teach', interval '80 minutes'),
               (:U7, :K7, :EW, :S1, 'lesson_whiteboard', 'teach', interval '30 minutes'),
               (:U8, :K8, :EW, :S1, 'lesson_whiteboard', 'teach', interval '80 minutes'),
               (:U9, :K9, :EW, :S1, 'lesson_whiteboard', 'teach', interval '80 minutes'),
               (:U10, :K10, :EW, :S1, 'lesson_whiteboard', 'teach', interval '80 minutes'),
               (:U11, :K11, :EV, :S1, 'voice_realtime', 'teach', interval '20 minutes'),
               (:U12, :K12, :EW, :S1, 'lesson_whiteboard', 'diagnose', interval '80 minutes')) x(u, k, e, st, res, m, ttl);
UPDATE platform.usage_reservation SET state = 'settled', settled_qty = 1, finished_at = platform.meter_now() WHERE usage_id = :U5;

-- 学习服务新建会话（返回会话 ID）；p_extra 是附加列（逗号开头的“列 = 值”拼接成的列表与值，见调用处）
CREATE FUNCTION t.ls_open(p_student uuid, p_usage uuid, p_key uuid, p_cols text DEFAULT '', p_vals text DEFAULT '') RETURNS uuid
  LANGUAGE sql AS $$
  SELECT t.m_as('starbuddy_learning', NULL, format($f$INSERT INTO platform.lesson_session (student_id, subject, lesson_id, lesson_version,
      usage_id, idempotency_key%s) VALUES (%L, 'aya', 'syn.lesson.open_a', '0.1.0', %L, %L%s) RETURNING id::text$f$,
      p_cols, p_student, p_usage, p_key, p_vals))::uuid $$;
-- 学习服务改会话状态
CREATE FUNCTION t.ls_status(p_session uuid, p_status text) RETURNS text LANGUAGE sql AS $$
  SELECT t.m_as('starbuddy_learning', NULL, format($f$UPDATE platform.lesson_session SET status = %L WHERE id = %L RETURNING status$f$,
                                                   p_status, p_session)) $$;
-- 以某个角色调用提问放行（返回 jsonb 文本）
CREATE FUNCTION t.ls_ask_as(p_role text, p_session uuid, p_student uuid, p_key uuid) RETURNS jsonb LANGUAGE sql AS $$
  SELECT t.m_as(p_role, NULL, format('SELECT platform.lesson_session_ask(%L, %L, %L)::text', p_session, p_student, p_key))::jsonb $$;
CREATE FUNCTION t.ls_ask(p_session uuid, p_student uuid, p_key uuid) RETURNS jsonb LANGUAGE sql AS $$
  SELECT t.ls_ask_as('starbuddy_learning', p_session, p_student, p_key) $$;
CREATE FUNCTION t.ls_outcome(p_session uuid, p_student uuid, p_key uuid) RETURNS text LANGUAGE sql AS $$
  SELECT t.ls_ask(p_session, p_student, p_key) ->> 'outcome' $$;

-- ================= 一、表结构：不存讲解文字、提问与回答 =================
SELECT t.ok((SELECT array_agg(column_name::text ORDER BY column_name) FROM information_schema.columns
              WHERE table_schema = 'platform' AND table_name = 'lesson_session')
            = '{created_at,expires_at,id,idempotency_key,last_ask_key,lesson_id,lesson_version,question_count,question_limit,status,student_id,subject,updated_at,usage_id}',
            '会话表的列：学生、学科、讲评课引用、预留的 usage_id 与幂等键、状态、提问上限与次数、最近一问的键、时间（没有讲解、台词、提问、回答列）');
SELECT t.ok(NOT EXISTS (SELECT 1 FROM information_schema.columns
                         WHERE table_schema = 'platform' AND table_name = 'lesson_session'
                           AND data_type IN ('jsonb', 'json', 'bytea', 'ARRAY') ),
            '没有 json、二进制或数组列（放不进讲解 DSL、台词稿与问答）');
SELECT t.ok((SELECT count(*) FROM pg_constraint k WHERE k.conrelid = 'platform.lesson_session'::regclass AND k.contype = 'f'
               AND k.confrelid = 'platform.student_profile'::regclass AND k.confdeltype = 'c') = 1,
            '会话经外键挂在学生档案上（删除档案时级联）');
SELECT t.ok((SELECT relrowsecurity AND relforcerowsecurity FROM pg_class WHERE oid = 'platform.lesson_session'::regclass),
            '会话表开启并强制 RLS');
SELECT t.throws(format($$INSERT INTO platform.lesson_session (student_id, subject, lesson_id, lesson_version, usage_id, idempotency_key)
                         VALUES (%L, 'aya', 'Syn.Lesson', '0.1.0', %L, %L)$$, :S1, :U1, :K1), '23514', 'lesson_id 不合 LessonRef 规则（大写）被拒');
SELECT t.throws(format($$INSERT INTO platform.lesson_session (student_id, subject, lesson_id, lesson_version, usage_id, idempotency_key)
                         VALUES (%L, 'aya', 'syn.lesson', 'v1', %L, %L)$$, :S1, :U1, :K1), '23514', 'lesson_version 不是版本号被拒');
SELECT t.throws(format($$INSERT INTO platform.lesson_session (student_id, subject, lesson_id, lesson_version, usage_id, idempotency_key)
                         VALUES (%L, 'echo', 'syn.lesson', '0.1.0', %L, %L)$$, :S1, :U1, :K1), '23514', '学科只能是三个适配器学科（搁置的 echo 不行）');
SELECT t.throws(format($$INSERT INTO platform.lesson_session (student_id, subject, lesson_id, lesson_version, usage_id, idempotency_key, question_limit)
                         VALUES (%L, 'aya', 'syn.lesson', '0.1.0', %L, %L, 21)$$, :S1, :U1, :K1), '23514', '提问上限最多 20');

-- ================= 二、新建：必须对得上一笔未过期的讲评课预留 =================
SELECT t.ls_open(:S1, :U1, :K1, ', created_at, expires_at', format(', %L, %L', now() + interval '3 days', now() + interval '9 days'))::text AS main \gset
\set MAIN '''':main''''
SELECT t.ok((SELECT status = 'opening' AND question_count = 0 AND question_limit = 20 AND last_ask_key IS NULL
                    AND created_at = platform.meter_now()
                    AND expires_at = (SELECT r.expires_at FROM platform.usage_reservation r WHERE r.usage_id = :U1)
               FROM platform.lesson_session WHERE id = :MAIN),
            '学习服务新建会话：opening、0 问、上限 20；创建时间取计量时钟、过期时间取那笔预留的过期时间（调用方传的值被覆盖）');
SELECT t.throws(format($$SELECT t.ls_open(%L, %L, %L)$$, :S1, :U2, :K1), '42501', '幂等键与预留对不上（换成另一笔的键）被拒');
SELECT t.throws(format($$SELECT t.ls_open(%L, %L, %L)$$, :S2, :U2, :K2), '42501', '学生与预留对不上（另一户的学生）被拒');
SELECT t.throws(format($$SELECT t.ls_open(%L, %L, %L)$$, :S1, :U3, :K3), '42501', '不是讲评课的预留（grade / mistake_analysis）被拒');
SELECT t.throws(format($$SELECT t.ls_open(%L, %L, %L)$$, :S1, :U11, :K11), '42501', '资源不是讲评课（teach / voice_realtime）被拒');
SELECT t.throws(format($$SELECT t.ls_open(%L, %L, %L)$$, :S1, :U12, :K12), '42501', '来源方法不是 teach（lesson_whiteboard / diagnose）被拒');
SELECT t.throws(format($$SELECT t.ls_open(%L, %L, %L)$$, :S1, :U4, :K4), '42501', '预留已过期（过期任务还没跑）被拒');
SELECT t.throws(format($$SELECT t.ls_open(%L, %L, %L)$$, :S1, :U5, :K5), '42501', '预留已结算（不能拿结算过的预留再开一节）被拒');
SELECT t.throws(format($$SELECT t.ls_open(%L, %L, %L)$$, :S1, gen_random_uuid(), gen_random_uuid()), '42501', '没有预留被拒');
SELECT t.throws(format($$SELECT t.ls_open(%L, %L, %L)$$, :S1, :U1, :K1), '23505', '同一笔预留只能开一个会话');
SELECT t.throws(format($$SELECT t.ls_open(%L, %L, %L, ', status', ', ''open''')$$, :S1, :U2, :K2), '23514', '不能以 open 新建');
SELECT t.throws(format($$SELECT t.ls_open(%L, %L, %L, ', question_count, last_ask_key', format(', 1, %%L', gen_random_uuid()))$$, :S1, :U2, :K2),
                '23514', '不能带提问次数与最近一问的键新建');
SELECT t.throws(format($$SELECT t.m_as('starbuddy_bff', %L, %L)$$, :aS1,
                       format($f$INSERT INTO platform.lesson_session (student_id, subject, lesson_id, lesson_version, usage_id, idempotency_key)
                                VALUES (%L, 'aya', 'syn.lesson', '0.1.0', %L, %L) RETURNING 'x'$f$, :S1, :U2, :K2)),
                '42501', 'BFF 不能新建会话（学生本人身份也不行）');
SELECT t.throws(format($$SELECT t.m_as('starbuddy_insight', NULL, %L)$$,
                       format($f$INSERT INTO platform.lesson_session (student_id, subject, lesson_id, lesson_version, usage_id, idempotency_key)
                                VALUES (%L, 'aya', 'syn.lesson', '0.1.0', %L, %L) RETURNING 'x'$f$, :S1, :U2, :K2)),
                '42501', '洞察服务不能新建会话');
SAVEPOINT erased;
UPDATE platform.student_profile SET erased_at = now() WHERE id = :S2;
SELECT t.throws(format($$SELECT t.ls_open(%L, %L, %L)$$, :S2, :U6, :K6), '42501', '档案已执行删除的学生不能再建会话');
ROLLBACK TO SAVEPOINT erased;
SELECT t.ok(t.ls_open(:S2, :U6, :K6) IS NOT NULL, '另一户学生按自己的预留可以建会话');

-- ================= 三、状态：opening → open / failed，之后不再变；学习服务只能改状态 =================
SELECT t.ls_open(:S1, :U2, :K2)::text AS failed \gset
\set FAILED '''':failed''''
SELECT t.ok(t.ls_status(:MAIN, 'open') = 'open' AND t.ls_status(:FAILED, 'failed') = 'failed', '学习服务把会话改成 open（已下发）或 failed（不下发）');
SELECT t.throws(format($$SELECT t.ls_status(%L, 'failed')$$, :MAIN), '22023', 'open 不能再改成 failed');
SELECT t.throws(format($$SELECT t.ls_status(%L, 'opening')$$, :MAIN), '22023', 'open 不能退回 opening');
SELECT t.throws(format($$SELECT t.ls_status(%L, 'open')$$, :FAILED), '22023', 'failed 不能再改成 open');
SELECT t.throws(format($$SELECT t.ls_status(%L, 'closed')$$, :FAILED), '22023', 'failed 也不能改成别的状态');
SELECT t.throws(format($$SELECT t.m_as('starbuddy_learning', NULL, %L)$$,
                       format('UPDATE platform.lesson_session SET question_count = 1 WHERE id = %L RETURNING ''x''', :MAIN)), '42501', '学习服务不能直接改提问次数（只经放行函数）');
SELECT t.throws(format($$SELECT t.m_as('starbuddy_learning', NULL, %L)$$,
                       format('UPDATE platform.lesson_session SET last_ask_key = gen_random_uuid() WHERE id = %L RETURNING ''x''', :MAIN)), '42501', '学习服务不能直接改最近一问的键');
SELECT t.throws(format($$SELECT t.m_as('starbuddy_learning', NULL, %L)$$,
                       format('UPDATE platform.lesson_session SET expires_at = now() + interval ''1 day'' WHERE id = %L RETURNING ''x''', :MAIN)), '42501', '学习服务不能改过期时间');
SELECT t.throws(format($$SELECT t.m_as('starbuddy_learning', NULL, %L)$$,
                       format('DELETE FROM platform.lesson_session WHERE id = %L RETURNING ''x''', :MAIN)), '42501', '学习服务不能删除会话（只随删除请求或档案级联删除）');
-- 守卫（不靠列权限）：超级用户也改不了身份、引用、计量与有效期
SELECT t.throws(format($$UPDATE platform.lesson_session SET lesson_id = 'syn.lesson.other' WHERE id = %L$$, :MAIN), '22023', '守卫：讲评课引用不能改');
SELECT t.throws(format($$UPDATE platform.lesson_session SET expires_at = expires_at + interval '1 hour' WHERE id = %L$$, :MAIN), '22023', '守卫：有效期不能延长');
SELECT t.throws(format($$UPDATE platform.lesson_session SET usage_id = %L WHERE id = %L$$, :U9, :MAIN), '22023', '守卫：计量 usage_id 不能换');
SELECT t.throws(format($$UPDATE platform.lesson_session SET question_limit = 5 WHERE id = %L$$, :MAIN), '22023', '守卫：提问上限不能改');
-- 过期以后不能再下发
SELECT t.ls_open(:S1, :U7, :K7)::text AS late \gset
\set LATE '''':late''''
SAVEPOINT clock_late;
UPDATE t.clock SET now = now + interval '31 minutes';
SELECT t.throws(format($$SELECT t.ls_status(%L, 'open')$$, :LATE), '22023', '过了有效期的会话不能再改成 open');
SELECT t.ok(t.ls_status(:LATE, 'failed') = 'failed', '过了有效期仍可标为 failed（BFF 释放或任其过期）');
ROLLBACK TO SAVEPOINT clock_late;

-- ================= 四、课内提问的放行 =================
SELECT t.ok((SELECT p.prosecdef AND pg_get_userbyid(p.proowner) = 'starbuddy_authz'
                    AND coalesce('search_path=pg_catalog, platform, pg_temp' = ANY (p.proconfig), false)
               FROM pg_proc p WHERE p.oid = 'platform.lesson_session_ask(uuid, uuid, uuid)'::regprocedure)
            AND (SELECT p.prosecdef AND pg_get_userbyid(p.proowner) = 'starbuddy_authz'
                        AND coalesce('search_path=pg_catalog, platform, pg_temp' = ANY (p.proconfig), false)
                   FROM pg_proc p WHERE p.oid = 'platform.lesson_session_guard()'::regprocedure),
            '放行函数与守卫是 SECURITY DEFINER、属主 starbuddy_authz、search_path 固定且以 pg_temp 结尾');
SELECT t.ok(has_function_privilege('starbuddy_learning', 'platform.lesson_session_ask(uuid, uuid, uuid)', 'EXECUTE')
            AND NOT EXISTS (SELECT 1 FROM unnest(ARRAY['starbuddy_bff', 'starbuddy_review', 'starbuddy_scheduler', 'starbuddy_insight',
                                                       'starbuddy_kge', 'starbuddy_kge_read', 'starbuddy_report']) r(role)
                             WHERE has_function_privilege(r.role, 'platform.lesson_session_ask(uuid, uuid, uuid)', 'EXECUTE'))
            AND NOT EXISTS (SELECT 1 FROM pg_proc p, aclexplode(p.proacl) a
                             WHERE p.oid IN ('platform.lesson_session_ask(uuid, uuid, uuid)'::regprocedure, 'platform.lesson_session_guard()'::regprocedure)
                               AND a.grantee = 0),
            '放行函数只授学习服务：BFF、复核、定时任务、洞察、知识服务、报表角色与 PUBLIC 都不能执行；守卫对 PUBLIC 不开放');
SELECT t.throws(format($$SELECT t.ls_ask_as('starbuddy_bff', %L, %L, %L)$$, :MAIN, :S1, :A1), '42501', 'BFF 调用放行函数被拒');
SELECT t.throws(format($$SELECT t.ls_ask_as('starbuddy_insight', %L, %L, %L)$$, :MAIN, :S1, :A1), '42501', '洞察服务调用放行函数被拒');

SELECT t.ok(t.ls_ask(:MAIN, :S1, :A1) = '{"outcome": "admitted", "question_seq": 1}', '第一问放行：admitted，第 1 问');
-- 有副作用的调用各占一条语句（同一个布尔表达式里的求值顺序不固定）
SELECT t.ls_ask(:MAIN, :S1, :A1)::text AS replay1 \gset
SELECT t.ok(:'replay1'::jsonb = '{"outcome": "replay", "question_seq": 1}'
            AND (SELECT question_count FROM platform.lesson_session WHERE id = :MAIN) = 1,
            '同一个键再来是重放：返回同一个第几问，次数不加');
SELECT t.ok(t.ls_ask(:MAIN, :S1, :A2) = '{"outcome": "admitted", "question_seq": 2}', '换一个键是新的一问：第 2 问');
SELECT t.ok(t.ls_ask(:MAIN, :S1, :A1) = '{"outcome": "admitted", "question_seq": 3}',
            '只认最近一问的键：更早的键再来按新的一问计（学习服务按最近一问重放，见 lessons）');
-- 预留已结算（下发后 BFF 结算）照样放行
UPDATE platform.usage_reservation SET state = 'settled', settled_qty = 1, finished_at = platform.meter_now() WHERE usage_id = :U1;
SELECT t.ok(t.ls_outcome(:MAIN, :S1, :A3) = 'admitted', '预留已结算 → 照样放行（提问不另扣）');
SELECT t.ok((SELECT question_count = 4 AND last_ask_key = :A3 FROM platform.lesson_session WHERE id = :MAIN), '会话上只记次数与最近一问的键');
SAVEPOINT shape;
SELECT t.ok(NOT EXISTS (SELECT 1 FROM (SELECT t.ls_ask(s, st, k) AS v
                                         FROM unnest(ARRAY[:MAIN, :FAILED, gen_random_uuid()]::uuid[]) s,
                                              unnest(ARRAY[:S1, :S2]::uuid[]) st, unnest(ARRAY[:A3, :A4]::uuid[]) k) x
                         WHERE (SELECT array_agg(k ORDER BY k) FROM jsonb_object_keys(x.v) k) <> '{outcome,question_seq}'),
            '返回值只有结果编码与第几问两个键（不返回预留状态、额度、数量与时间）');
ROLLBACK TO SAVEPOINT shape;

-- 不放行的各种情形
SELECT t.ok(t.ls_outcome(gen_random_uuid(), :S1, :A4) = 'none', '会话不存在 → none');
SELECT t.ok(t.ls_outcome(:MAIN, :S2, :A4) = 'none', '会话不属于该生（另一户）→ none，与不存在同一返回');
SELECT t.ok(t.ls_outcome(NULL, :S1, :A4) = 'none' AND t.ls_outcome(:MAIN, NULL, :A4) = 'none' AND t.ls_outcome(:MAIN, :S1, NULL) = 'none',
            '任一参数为空 → none');
SELECT t.ok(t.ls_outcome(:FAILED, :S1, :A4) = 'not_open', '下发失败的会话 → not_open');
SELECT t.ok(t.ls_outcome(:LATE, :S1, :A4) = 'not_open', '还没下发的会话（opening）→ not_open');
-- 上限：上限 2 的会话问两次后第三问 limit；最近一问的重放仍是 replay
SELECT t.ls_open(:S1, :U8, :K8, ', question_limit', ', 2')::text AS capped \gset
\set CAPPED '''':capped''''
SELECT t.ls_status(:CAPPED, 'open');
SELECT t.ls_outcome(:CAPPED, :S1, :A1) AS cap1 \gset
SELECT t.ls_outcome(:CAPPED, :S1, :A2) AS cap2 \gset
SELECT t.ls_ask(:CAPPED, :S1, :A3)::text AS cap3 \gset
SELECT t.ok(:'cap1' = 'admitted' AND :'cap2' = 'admitted' AND :'cap3'::jsonb = '{"outcome": "limit", "question_seq": null}'
            AND (SELECT question_count FROM platform.lesson_session WHERE id = :CAPPED) = 2,
            '到上限后 → limit，次数不再加（超了直接回“不答”，不调适配器）');
SELECT t.ok(t.ls_ask(:CAPPED, :S1, :A2) = '{"outcome": "replay", "question_seq": 2}', '到上限后最近一问的重放仍是 replay（同一个第几问）');
-- 预留已释放（构造的不一致：会话已下发、预留却被释放）→ unpaid
SELECT t.ls_open(:S1, :U9, :K9)::text AS released \gset
\set RELEASED '''':released''''
SELECT t.ls_status(:RELEASED, 'open');
UPDATE platform.usage_reservation SET state = 'released', finished_at = platform.meter_now() WHERE usage_id = :U9;
SELECT t.ls_outcome(:RELEASED, :S1, :A1) AS rel1 \gset
SELECT t.ok(:'rel1' = 'unpaid' AND (SELECT question_count FROM platform.lesson_session WHERE id = :RELEASED) = 0,
            '那笔预留已释放 → unpaid，次数不加');
-- 过了有效期：不放行，重放也不行
SAVEPOINT clock_expired;
UPDATE t.clock SET now = now + interval '81 minutes';
SELECT t.ok(t.ls_outcome(:MAIN, :S1, :A4) = 'expired' AND t.ls_outcome(:MAIN, :S1, :A3) = 'expired',
            '过了有效期（与预留一致）→ expired，最近一问的重放也是 expired');
SELECT t.throws(format($$UPDATE platform.lesson_session SET question_count = question_count + 1, last_ask_key = %L WHERE id = %L$$, :A4, :MAIN),
                '22023', '守卫：过了有效期不能再加次数');
ROLLBACK TO SAVEPOINT clock_expired;
-- 预留中但已过期（过期任务还没跑）在有效期内不会出现（会话过期时间就是预留过期时间）；预留被标成 expired 时 → unpaid
SELECT t.ls_open(:S1, :U10, :K10)::text AS resexp \gset
\set RESEXP '''':resexp''''
SELECT t.ls_status(:RESEXP, 'open');
UPDATE platform.usage_reservation SET state = 'expired', finished_at = platform.meter_now() WHERE usage_id = :U10;
SELECT t.ok(t.ls_outcome(:RESEXP, :S1, :A1) = 'unpaid', '那笔预留被标成 expired（额度已退）→ unpaid');
-- 守卫（不靠放行函数）：次数只能每次加 1、同时换键、只在 open 时
SELECT t.throws(format($$UPDATE platform.lesson_session SET question_count = question_count + 2, last_ask_key = %L WHERE id = %L$$, :A4, :MAIN),
                '22023', '守卫：次数一次不能加 2');
SELECT t.throws(format($$UPDATE platform.lesson_session SET question_count = question_count + 1 WHERE id = %L$$, :MAIN),
                '22023', '守卫：加次数时必须换最近一问的键');
SELECT t.throws(format($$UPDATE platform.lesson_session SET last_ask_key = %L WHERE id = %L$$, :A4, :MAIN),
                '22023', '守卫：不加次数不能换键');
SELECT t.throws(format($$UPDATE platform.lesson_session SET question_count = question_count - 1 WHERE id = %L$$, :MAIN),
                '22023', '守卫：次数不能减');
SELECT t.throws(format($$UPDATE platform.lesson_session SET question_count = 1, last_ask_key = %L WHERE id = %L$$, :A4, :LATE),
                '22023', '守卫：没下发的会话不能加次数');

-- ================= 五、可见性：只有学习服务读写；BFF 与其他应用角色读不到 =================
SELECT t.ok(NOT has_table_privilege(r, 'platform.lesson_session', 'SELECT') AND NOT has_any_column_privilege(r, 'platform.lesson_session', 'SELECT')
            AND NOT has_any_column_privilege(r, 'platform.lesson_session', 'INSERT') AND NOT has_any_column_privilege(r, 'platform.lesson_session', 'UPDATE')
            AND NOT has_table_privilege(r, 'platform.lesson_session', 'DELETE'),
            format('%s 对会话表没有任何读写权限', r))
  FROM unnest(ARRAY['starbuddy_bff', 'starbuddy_review', 'starbuddy_scheduler', 'starbuddy_insight', 'starbuddy_kge', 'starbuddy_kge_read',
                    'starbuddy_report']) r;
SELECT t.throws(format($$SELECT t.m_as('starbuddy_bff', %L, 'SELECT count(*)::text FROM platform.lesson_session')$$, :aS1), '42501',
                'BFF（学生本人身份）读会话表被拒');
SELECT t.throws(format($$SELECT t.m_as('starbuddy_bff', %L, 'SELECT count(*)::text FROM platform.lesson_session')$$, :gA), '42501',
                'BFF（家长身份）读会话表被拒');
SELECT t.ok(t.m_as('starbuddy_learning', NULL, 'SELECT count(*)::text FROM platform.lesson_session')::int
            = (SELECT count(*) FROM platform.lesson_session), '学习服务读得到全部会话（RLS 放行）');
SELECT t.ok(has_column_privilege('starbuddy_eraser', 'platform.lesson_session', 'student_id', 'SELECT')
            AND NOT has_column_privilege('starbuddy_eraser', 'platform.lesson_session', 'lesson_id', 'SELECT')
            AND has_table_privilege('starbuddy_eraser', 'platform.lesson_session', 'DELETE'),
            '删除执行身份只读学生键列、能删');

-- ================= 六、删除与导出：登记进个人层表清单；家长导出拿到讲评课引用与次数，不拿计量键 =================
SELECT t.ok(EXISTS (SELECT 1 FROM platform.personal_table_registry
                     WHERE nspname = 'platform' AND relname = 'lesson_session' AND strategy = 'delete' AND student_predicate = 'student_id = $1'
                       AND erase_scope = 'evidence' AND export_topic = 'lesson_sessions'
                       AND export_columns = ARRAY['subject', 'lesson_id', 'lesson_version', 'status', 'question_count', 'created_at']
                       AND export_filter IS NULL),
            '个人层表清单登记了会话表：随删除请求删除（evidence），导出主题 lesson_sessions');
SELECT t.p_bff(:gA, format('SELECT platform.export_student_data(%L)::text', :S1))::jsonb -> 'topics' -> 'lesson_sessions' AS ex \gset
SELECT t.ok(jsonb_array_length(:'ex'::jsonb) = (SELECT count(*) FROM platform.lesson_session WHERE student_id = :S1)
            AND NOT EXISTS (SELECT 1 FROM jsonb_array_elements(:'ex'::jsonb) e
                             WHERE (SELECT array_agg(k ORDER BY k) FROM jsonb_object_keys(e) k)
                                   <> '{created_at,lesson_id,lesson_version,question_count,status,subject}')
            AND :'ex'::jsonb @> '[{"lesson_id": "syn.lesson.open_a", "status": "open", "question_count": 4}]',
            '家长导出：S1 的每个会话一行，只有学科、讲评课引用、状态、次数与创建时间（没有 usage_id、幂等键、最近一问的键与过期时间）');
SELECT t.ok(jsonb_array_length(t.p_bff(:gB, format('SELECT platform.export_student_data(%L)::text', :S2))::jsonb -> 'topics' -> 'lesson_sessions')
            = (SELECT count(*) FROM platform.lesson_session WHERE student_id = :S2),
            '另一户家长导出自己的学生，只拿到自己学生的会话');

ROLLBACK;
DROP FUNCTION IF EXISTS t.ls_outcome(uuid, uuid, uuid);
DROP FUNCTION IF EXISTS t.ls_ask(uuid, uuid, uuid);
DROP FUNCTION IF EXISTS t.ls_ask_as(text, uuid, uuid, uuid);
DROP FUNCTION IF EXISTS t.ls_status(uuid, text);
DROP FUNCTION IF EXISTS t.ls_open(uuid, uuid, uuid, text, text);
