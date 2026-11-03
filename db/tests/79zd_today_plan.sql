-- 0055 今日计划快照、0056 计划完成 / 跳过事件核对任务 ID（specs/today-plan-snapshot-plt15/spec.md PR1 验收：RLS 与删除覆盖测试）。
-- 全部合成数据（规则 8）：10 号夹具的 S1（家长 gA）、S2（家长 gB），gC 没有绑定。用 79d 的 t.ob(账号, SQL)（BFF 身份）与
-- 79m 的 t.m_as(角色, 账号, SQL)。前几段在一个事务里，结束回滚；最后一段提交两户各两条当天的快照行，留给 80 的可见性矩阵。
-- 删除请求的覆盖（快照随删除请求删除、回执计数）在 79q 的全库扫描里（t.q_seed 给两名学生各写两条）。
\set S1 '''c1000000-0000-4000-8000-0000000000c1'''
\set S2 '''c2000000-0000-4000-8000-0000000000c2'''
\set aS1 '''a1000000-0000-4000-8000-0000000000a1'''
\set aS2 '''b1000000-0000-4000-8000-0000000000b1'''
\set gA '''a0000000-0000-4000-8000-00000000000a'''
\set gB '''b0000000-0000-4000-8000-00000000000b'''
\set gC '''cc000000-0000-4000-8000-0000000000cc'''
\set T1 '''7d150000-0000-4000-8000-000000000001'''
\set T2 '''7d150000-0000-4000-8000-000000000002'''
\set T3 '''7d150000-0000-4000-8000-000000000003'''
\set T4 '''7d150000-0000-4000-8000-000000000004'''
\set TS2 '''7d150000-0000-4000-8000-0000000000b1'''
\set E1 '''7d150000-0000-4000-8000-0000000000e1'''
\set E2 '''7d150000-0000-4000-8000-0000000000e2'''

BEGIN;
-- 学习服务写入一条（任务行；不指定 plan_date 时取当天的北京时间日期）
CREATE FUNCTION t.tp_ins(p_id uuid, p_student uuid, p_seq int, p_kind text, p_source text, p_node text, p_entry uuid,
                         p_code text DEFAULT NULL) RETURNS text LANGUAGE sql AS $$
  SELECT t.m_as('starbuddy_learning', NULL, format($f$INSERT INTO platform.today_plan_item (id, student_id, seq, subject, kind, source, node_id,
      estimated_minutes, entry_ref, rationale_code) VALUES (%L, %L, %s, 'aya', %L, %L, %L, 5, %L, %L) RETURNING id::text$f$,
      p_id, p_student, p_seq, p_kind, p_source, p_node, p_entry, p_code)) $$;
CREATE FUNCTION t.tp_today() RETURNS date LANGUAGE sql STABLE AS $$ SELECT (now() AT TIME ZONE 'Asia/Shanghai')::date $$;
-- 应用角色（与 80 的 t.app_roles 相同；80 在本文件之后才建）
CREATE FUNCTION t.tp_roles() RETURNS SETOF text LANGUAGE sql IMMUTABLE AS $$
  SELECT unnest(ARRAY['starbuddy_bff', 'starbuddy_learning', 'starbuddy_review', 'starbuddy_scheduler', 'starbuddy_insight',
                       'starbuddy_kge', 'starbuddy_kge_read', 'starbuddy_report', 'starbuddy_authz', 'starbuddy_eraser']) $$;
-- 以 BFF 身份按账号读：看得到的任务 ID（排好序，逗号连接；看不到为空串）
CREATE FUNCTION t.tp_seen(p_acct uuid) RETURNS text LANGUAGE sql AS $$
  SELECT coalesce(t.ob(p_acct, 'SELECT string_agg(id::text, '','' ORDER BY id) FROM platform.today_plan_item'), '') $$;
-- 学生本人写计划事件（返回事件 ID）
CREATE FUNCTION t.tp_event(p_acct uuid, p_student uuid, p_kind text, p_task uuid, p_at timestamptz DEFAULT now(),
                           p_key uuid DEFAULT gen_random_uuid()) RETURNS text LANGUAGE sql AS $$
  SELECT t.ob(p_acct, format($f$SELECT (insight.record_behavior_event(%L, %L, %L, %L, %L)).id::text$f$, p_student, p_kind,
                             jsonb_build_object('plan_task_id', p_task), p_at, p_key)) $$;

-- ================= 一、表结构：只存编排结果，不存掌握、置信度、证据 ID、理由文本 =================
SELECT t.ok((SELECT array_agg(column_name::text ORDER BY column_name) FROM information_schema.columns
              WHERE table_schema = 'platform' AND table_name = 'today_plan_item')
            = '{created_at,entry_ref,estimated_minutes,id,kind,node_id,plan_date,rationale_code,seq,source,student_id,subject}',
            '快照表的列：任务 ID、学生、计划日、序号、学科、类别、来源、节点、预计分钟、条目引用、推荐理由编码、创建时间（没有掌握、置信度、证据、文本列）');
SELECT t.ok((SELECT count(*) FROM pg_constraint k WHERE k.conrelid = 'platform.today_plan_item'::regclass AND k.contype = 'f'
               AND k.confrelid = 'platform.student_profile'::regclass AND k.confdeltype = 'c') = 1,
            '快照行经外键挂在学生档案上（删除档案时级联）');

-- ================= 二、写入方只有学习服务；只能写当天；结构约束 =================
SELECT t.ok(t.tp_ins(:T1, :S1, 1, 'retest', 'retest', 'MATH.FUNC.QUAD.GRAPH', :E1) = :T1
            AND t.tp_ins(:T2, :S1, 2, 'lesson', 'plan', NULL, NULL, 'prerequisite_of_many') = :T2
            AND t.tp_ins(:T3, :S1, 3, 'verify', 'verify', 'MATH.FUNC.QUAD.GRAPH', :E2) = :T3
            AND t.tp_ins(:TS2, :S2, 1, 'practice', 'practice', 'MATH.FUNC.QUAD.ROOT', :E1) = :TS2,
            '学习服务能写当天的快照行（复测、计划、验证题、再练四种来源）');
SELECT t.ok((SELECT bool_and(plan_date = t.tp_today()) FROM platform.today_plan_item WHERE id IN (:T1, :T2, :T3, :TS2)),
            '不给计划日时取当天的北京时间日期');
SELECT t.throws_like(format($$SELECT t.m_as('starbuddy_learning', NULL, %L)$$,
                            format($f$INSERT INTO platform.today_plan_item (student_id, plan_date, seq, subject, kind, source, estimated_minutes)
                                     VALUES (%L, %L, 9, 'aya', 'lesson', 'plan', 5) RETURNING 'x'$f$, :S1, t.tp_today() - 1)),
                     '23514', '只能写当天', '学习服务不能补写昨天的快照');
SELECT t.throws_like(format($$SELECT t.m_as('starbuddy_learning', NULL, %L)$$,
                            format($f$INSERT INTO platform.today_plan_item (student_id, plan_date, seq, subject, kind, source, estimated_minutes)
                                     VALUES (%L, %L, 9, 'aya', 'lesson', 'plan', 5) RETURNING 'x'$f$, :S1, t.tp_today() + 1)),
                     '23514', '只能写当天', '学习服务不能预写明天的快照');
SELECT t.throws(format($$SELECT t.m_as('starbuddy_learning', NULL, %L)$$,
                       format($f$UPDATE platform.today_plan_item SET seq = 9 WHERE id = %L RETURNING 'x'$f$, :T1)),
                '42501', '学习服务不能改已冻结的快照');
SELECT t.throws(format($$SELECT t.m_as('starbuddy_learning', NULL, %L)$$,
                       format($f$DELETE FROM platform.today_plan_item WHERE id = %L RETURNING 'x'$f$, :T1)),
                '42501', '学习服务不能删快照（删除只经删除执行身份）');
SELECT t.throws_like(format($$SELECT t.tp_ins(gen_random_uuid(), %L, 1, 'lesson', 'plan', NULL, NULL)$$, :S1),
                     '23505', 'today_plan_item_seq', '同一学生同一天同一序号只有一条');
SELECT t.throws_like(format($$SELECT t.tp_ins(gen_random_uuid(), %L, 8, 'retest', 'retest', 'MATH.FUNC.QUAD.GRAPH', %L)$$, :S1, :E1),
                     '23505', 'today_plan_item_entry_uq', '同一天同一来源的同一条目只出现一次');
SELECT t.ok(t.tp_ins(:T4, :S1, 4, 'practice', 'practice', 'MATH.FUNC.QUAD.GRAPH', :E1) = :T4,
            '同一条目引用在不同来源下各自成行（复测与再练的条目 ID 互不相干）');
SELECT t.throws_like(format($$SELECT t.tp_ins(gen_random_uuid(), %L, 8, 'lesson', 'retest', 'MATH.FUNC.QUAD.GRAPH', gen_random_uuid())$$, :S1),
                     '23514', 'today_plan_item_kind_source', '来源与类别对不上（复测列表里出现 lesson）被拒');
SELECT t.throws_like(format($$SELECT t.tp_ins(gen_random_uuid(), %L, 8, 'lesson', 'plan', NULL, gen_random_uuid())$$, :S1),
                     '23514', 'today_plan_item_entry_ref', '计划任务不能带条目引用');
SELECT t.throws_like(format($$SELECT t.tp_ins(gen_random_uuid(), %L, 8, 'practice', 'practice', 'MATH.FUNC.QUAD.GRAPH', NULL)$$, :S1),
                     '23514', 'today_plan_item_entry_ref', '再练条目必须带条目引用');
SELECT t.throws_like(format($$SELECT t.tp_ins(gen_random_uuid(), %L, 8, 'retest', 'retest', NULL, gen_random_uuid())$$, :S1),
                     '23514', 'today_plan_item_entry_node', '复测条目必须带节点');
SELECT t.throws_like(format($$SELECT t.tp_ins(gen_random_uuid(), %L, 8, 'retest', 'retest', 'MATH.FUNC.QUAD.GRAPH', gen_random_uuid(), 'retest_scheduled')$$, :S1),
                     '23514', 'today_plan_item_rationale', '复测、再练条目不带推荐理由编码');
SELECT t.throws(format($$SELECT t.tp_ins(gen_random_uuid(), %L, 8, 'lesson', 'plan', NULL, NULL, '理由文本')$$, :S1),
                '23514', '推荐理由只能是编码，不收文本');
SELECT t.throws_like(format($$SELECT t.tp_ins(gen_random_uuid(), %L, 8, 'lesson', 'plan', NULL, NULL, 'syn_unlisted_code')$$, :S1),
                     '23514', 'today_plan_item_rationale_code', '推荐理由编码只收 BFF 已登记的理由编码（未登记的由学习服务写入前置空）');
SAVEPOINT tp_code;
SELECT t.ok(t.tp_ins(gen_random_uuid(), :S1, 8, 'lesson', 'plan', NULL, NULL, 'weak_mastery') IS NOT NULL,
            '已登记的关于学生类理由编码可以写');
ROLLBACK TO SAVEPOINT tp_code;
-- 已删除或不存在的学生：守卫按 42501 拒绝（与未授权同一错误；安全审查阻断 1）
UPDATE platform.student_profile SET erased_at = now() WHERE id = :S2;
SELECT t.throws_like(format($$SELECT t.tp_ins(gen_random_uuid(), %L, 9, 'lesson', 'plan', NULL, NULL)$$, :S2),
                     '42501', 'not allowed', '已执行删除的学生不能再写快照');
UPDATE platform.student_profile SET erased_at = NULL WHERE id = :S2;
SELECT t.throws_like($$SELECT t.tp_ins(gen_random_uuid(), '00000000-0000-4000-8000-0000000000aa', 1, 'lesson', 'plan', NULL, NULL)$$,
                     '42501', 'not allowed', '不存在的学生与已删除同一个错误');
SELECT t.ok((SELECT p.prosecdef AND pg_get_userbyid(p.proowner) = 'starbuddy_authz'
                    AND 'search_path=pg_catalog, platform, pg_temp' = ANY (p.proconfig)
               FROM pg_proc p WHERE p.oid = 'platform.today_plan_item_guard()'::regprocedure)
            AND position('FOR KEY SHARE' IN (SELECT prosrc FROM pg_proc WHERE oid = 'platform.today_plan_item_guard()'::regprocedure)) > 0,
            '插入守卫属主 authz、SECURITY DEFINER、search_path 固定；对档案行 FOR KEY SHARE（与删除执行串行）');
SELECT t.throws(format($$SELECT t.tp_ins(gen_random_uuid(), %L, 65, 'lesson', 'plan', NULL, NULL)$$, :S1), '23514', '序号有上限');
SELECT t.throws(format($$SELECT t.ob(%L, %L)$$, :aS1, format($f$INSERT INTO platform.today_plan_item (student_id, seq, subject, kind, source,
                       estimated_minutes) VALUES (%L, 9, 'aya', 'lesson', 'plan', 5) RETURNING 'x'$f$, :S1)),
                '42501', 'BFF（学生本人）不能写快照');
SELECT t.ok(NOT EXISTS (SELECT 1 FROM t.tp_roles() r(role)
                         WHERE r.role NOT IN ('starbuddy_learning') AND has_table_privilege(r.role, 'platform.today_plan_item', 'INSERT'))
            AND NOT EXISTS (SELECT 1 FROM t.tp_roles() r(role)
                             WHERE has_table_privilege(r.role, 'platform.today_plan_item', 'UPDATE')
                                OR has_any_column_privilege(r.role, 'platform.today_plan_item', 'UPDATE'))
            AND (SELECT array_agg(r.role ORDER BY r.role) FROM t.tp_roles() r(role)
                  WHERE has_table_privilege(r.role, 'platform.today_plan_item', 'DELETE')) = '{starbuddy_eraser}',
            '只有学习服务能写；任何应用角色都不能改；只有删除执行身份能删');
SELECT t.ok((SELECT array_agg(r.role ORDER BY r.role) FROM t.tp_roles() r(role)
              WHERE has_any_column_privilege(r.role, 'platform.today_plan_item', 'SELECT'))
            = '{starbuddy_authz,starbuddy_bff,starbuddy_eraser,starbuddy_learning}'
            AND NOT has_column_privilege('starbuddy_authz', 'platform.today_plan_item', 'node_id', 'SELECT')
            AND NOT has_column_privilege('starbuddy_eraser', 'platform.today_plan_item', 'entry_ref', 'SELECT')
            AND NOT has_table_privilege('starbuddy_bff', 'platform.today_plan_item', 'SELECT')
            AND NOT has_column_privilege('starbuddy_bff', 'platform.today_plan_item', 'node_id', 'SELECT')
            AND NOT has_column_privilege('starbuddy_bff', 'platform.today_plan_item', 'rationale_code', 'SELECT')
            AND NOT has_column_privilege('starbuddy_bff', 'platform.today_plan_item', 'created_at', 'SELECT'),
            '读：学习服务、BFF（按 RLS，列级：不含节点、推荐理由编码、创建时间）、行为事件写入函数属主（只读任务 ID、学生、日期）、'
            '删除执行身份（只读学生键与日期）；洞察服务等无权');

-- ================= 三、BFF：学生本人与有效绑定家长只读当天的行 =================
-- 昨天的一行（以超级用户把计划日改到昨天：模拟跨日后仍留在表里的快照）
UPDATE platform.today_plan_item SET plan_date = t.tp_today() - 1 WHERE id = :T4;
SELECT t.ok(t.tp_seen(:aS1) = concat_ws(',', :T1, :T2, :T3), '学生本人只看到自己当天的快照行（看不到别人的，也看不到昨天的）');
SELECT t.ok(t.tp_seen(:gA) = concat_ws(',', :T1, :T2, :T3), '有效绑定家长看到孩子当天的快照行（spec 待人确认 3 的默认）');
SELECT t.ok(t.tp_seen(:aS2) = :TS2 AND t.tp_seen(:gB) = :TS2, '另一户的学生与家长只看到自己户的');
SELECT t.ok(t.tp_seen(:gC) = '' AND t.tp_seen(NULL) = '' AND t.tp_seen('00000000-0000-4000-8000-000000000000') = '',
            '没有绑定的家长、未登录、不存在的账号一行都看不到');
UPDATE platform.guardian_student_link SET status = 'revoked', revoked_at = now() WHERE guardian_account_id = :gA AND student_id = :S1;
SELECT t.ok(t.tp_seen(:gA) = '', '解除绑定后家长看不到');
ROLLBACK;

BEGIN;
\set T1 '''7d150000-0000-4000-8000-000000000011'''
\set TS2 '''7d150000-0000-4000-8000-0000000000b2'''
CREATE FUNCTION t.tp_ins(p_id uuid, p_student uuid, p_seq int, p_kind text, p_source text, p_node text, p_entry uuid,
                         p_code text DEFAULT NULL) RETURNS text LANGUAGE sql AS $$
  SELECT t.m_as('starbuddy_learning', NULL, format($f$INSERT INTO platform.today_plan_item (id, student_id, seq, subject, kind, source, node_id,
      estimated_minutes, entry_ref, rationale_code) VALUES (%L, %L, %s, 'aya', %L, %L, %L, 5, %L, %L) RETURNING id::text$f$,
      p_id, p_student, p_seq, p_kind, p_source, p_node, p_entry, p_code)) $$;
CREATE FUNCTION t.tp_today() RETURNS date LANGUAGE sql STABLE AS $$ SELECT (now() AT TIME ZONE 'Asia/Shanghai')::date $$;
CREATE FUNCTION t.tp_event(p_acct uuid, p_student uuid, p_kind text, p_task uuid, p_at timestamptz DEFAULT now(),
                           p_key uuid DEFAULT gen_random_uuid()) RETURNS text LANGUAGE sql AS $$
  SELECT t.ob(p_acct, format($f$SELECT (insight.record_behavior_event(%L, %L, %L, %L, %L)).id::text$f$, p_student, p_kind,
                             jsonb_build_object('plan_task_id', p_task), p_at, p_key)) $$;
-- 以超级用户把某一行的计划日挪到指定日期，返回它的 ID
CREATE FUNCTION t.tp_task_on(p_student uuid, p_seq int, p_day date) RETURNS uuid LANGUAGE plpgsql AS $$
DECLARE v uuid := gen_random_uuid();
BEGIN
  PERFORM t.tp_ins(v, p_student, p_seq, 'lesson', 'plan', NULL, NULL);
  UPDATE platform.today_plan_item SET plan_date = p_day WHERE id = v;
  RETURN v;
END $$;

-- ================= 四、行为事件：plan_done / plan_skip 的任务 ID 必须是该生的快照行、计划日在事件日 ±1 天内 =================
SELECT t.ok(t.tp_ins(:T1, :S1, 1, 'lesson', 'plan', NULL, NULL) = :T1 AND t.tp_ins(:TS2, :S2, 1, 'lesson', 'plan', NULL, NULL) = :TS2,
            '夹具：两名学生各一条当天的快照行');
SELECT t.ok(t.tp_event(:aS1, :S1, 'plan_done', :T1) IS NOT NULL AND t.tp_event(:aS1, :S1, 'plan_skip', :T1) IS NOT NULL,
            '学生本人对自己当天的任务写完成、跳过');
SELECT t.throws_like(format('SELECT t.tp_event(%L, %L, ''plan_done'', %L)', :aS1, :S1, :TS2), '22023', 'PLAN_TASK_NOT_FOUND',
                     '别人的任务 ID 被拒');
SELECT t.throws_like(format('SELECT t.tp_event(%L, %L, ''plan_skip'', gen_random_uuid())', :aS1, :S1), '22023', 'PLAN_TASK_NOT_FOUND',
                     '不存在的任务 ID 被拒（与别人的任务同一个错误）');
SELECT t.throws(format('SELECT t.tp_event(%L, %L, ''plan_done'', %L)', :gA, :S1, :T1), '42501', '家长不能替孩子写完成');
SELECT t.throws(format('SELECT t.tp_event(%L, %L, ''plan_done'', %L)', :aS2, :S1, :T1), '42501', '别的学生不能写');
-- 计划日只能是事件日当天或前一天（按事件发生时间的北京时间日期；审查建议收紧，不收计划日晚于事件日的）
SELECT t.ok(t.tp_event(:aS1, :S1, 'plan_done', t.tp_task_on(:S1, 11, t.tp_today() - 1)) IS NOT NULL,
            '计划日是事件日前一天的任务可以记（跨零点完成昨天的任务）');
SELECT t.throws_like(format('SELECT t.tp_event(%L, %L, ''plan_skip'', %L)', :aS1, :S1, t.tp_task_on(:S1, 12, t.tp_today() + 1)),
                     '22023', 'PLAN_TASK_NOT_FOUND', '计划日晚于事件日一天的任务被拒');
SELECT t.throws_like(format('SELECT t.tp_event(%L, %L, ''plan_done'', %L)', :aS1, :S1, t.tp_task_on(:S1, 13, t.tp_today() - 2)),
                     '22023', 'PLAN_TASK_NOT_FOUND', '计划日早于事件日两天的任务被拒（越日）');
SELECT t.throws_like(format('SELECT t.tp_event(%L, %L, ''plan_done'', %L)', :aS1, :S1, t.tp_task_on(:S1, 14, t.tp_today() + 2)),
                     '22023', 'PLAN_TASK_NOT_FOUND', '计划日晚于事件日两天的任务被拒');
-- 按北京时间换日，不按 UTC：取最近 24 小时内 UTC 22:00 的时刻（北京时间已是 UTC 日期的次日 06:00）
SELECT CASE WHEN now() >= (date_trunc('day', now() AT TIME ZONE 'UTC') + interval '22 hours') AT TIME ZONE 'UTC'
            THEN (date_trunc('day', now() AT TIME ZONE 'UTC') + interval '22 hours') AT TIME ZONE 'UTC'
            ELSE (date_trunc('day', now() AT TIME ZONE 'UTC') - interval '2 hours') AT TIME ZONE 'UTC' END AS utc22 \gset
SELECT (:'utc22'::timestamptz AT TIME ZONE 'UTC')::date AS utc_day \gset
SELECT t.ok(t.tp_event(:aS1, :S1, 'plan_done', t.tp_task_on(:S1, 21, :'utc_day'::date + 1), :'utc22'::timestamptz) IS NOT NULL,
            '事件日按北京时间算：UTC 22:00 的事件，计划日是 UTC 日期后一天（北京时间当天）的任务可以记');
SELECT t.throws_like(format('SELECT t.tp_event(%L, %L, ''plan_done'', %L, %L)', :aS1, :S1, t.tp_task_on(:S1, 22, :'utc_day'::date - 1), :'utc22'),
                     '22023', 'PLAN_TASK_NOT_FOUND', '事件日按北京时间算：同一事件，计划日是 UTC 日期前一天（北京时间前两天）的任务被拒');
-- 重放先于核对：快照行被清掉之后，同一幂等键的重放仍返回原记录
SELECT t.tp_event(:aS1, :S1, 'plan_skip', :T1, now(), '7d150000-0000-4000-8000-0000000000f1') AS ev_keep \gset
SELECT occurred_at AS ev_keep_at FROM insight.behavior_event WHERE id = :'ev_keep' \gset
DELETE FROM platform.today_plan_item WHERE id = :T1;
SELECT t.ok(t.tp_event(:aS1, :S1, 'plan_skip', :T1, :'ev_keep_at', '7d150000-0000-4000-8000-0000000000f1') = :'ev_keep',
            '快照行不在了，同一幂等键的重放仍返回原记录（重放先于任务核对）');
SELECT t.throws_like(format('SELECT t.tp_event(%L, %L, ''plan_done'', %L)', :aS1, :S1, :T1), '22023', 'PLAN_TASK_NOT_FOUND',
                     '快照行不在了，新的完成事件被拒');
-- 会话事件不受影响；payload 形状仍由 0025 的白名单管
SELECT t.ok(t.ob(:aS1, format($$SELECT (insight.record_behavior_event(%L, 'session_start', '{"entry": "today"}', now(), gen_random_uuid())).id::text$$, :S1)) IS NOT NULL,
            '会话事件照常写');
SELECT t.throws(format('SELECT t.ob(%L, %L)', :aS1, format($$SELECT (insight.record_behavior_event(%L, 'plan_done', '{"plan_task_id": "x"}', now(), gen_random_uuid())).id::text$$, :S1)),
                '22023', '计划任务 ID 不是 UUID 仍按 payload 白名单拒绝');

-- ================= 五、个人层表清单与保留期 =================
SELECT t.ok(EXISTS (SELECT 1 FROM platform.personal_table_registry WHERE nspname = 'platform' AND relname = 'today_plan_item'
                      AND strategy = 'delete' AND student_predicate = 'student_id = $1' AND erase_scope = 'inference' AND export_topic IS NULL),
            '快照表登记在个人层表清单里：随删除请求删除，不进导出包');
SELECT t.ok(platform.today_plan_retention_days() = 90, '保留期配置（合成值 90 天）');
SELECT t.ok(NOT has_function_privilege('starbuddy_bff', 'platform.today_plan_retention_days()', 'EXECUTE')
            AND NOT has_function_privilege('starbuddy_learning', 'platform.today_plan_retention_days()', 'EXECUTE')
            AND has_function_privilege('starbuddy_eraser', 'platform.today_plan_retention_days()', 'EXECUTE'),
            '保留期配置只给删除作业');
-- 两名学生各有：保留期边界上的一行（今天 − 90 天，到期）、边界内的一行（今天 − 89 天）、当天的一行
SELECT t.tp_task_on(:S1, 31, t.tp_today() - 90) AS s1_old, t.tp_task_on(:S1, 32, t.tp_today() - 89) AS s1_keep,
       t.tp_task_on(:S2, 31, t.tp_today() - 120) AS s2_old, t.tp_task_on(:S2, 32, t.tp_today() - 89) AS s2_keep \gset
SELECT count(*) AS n_before FROM platform.today_plan_item \gset
SELECT count(*) AS other_before FROM platform.erasure_request \gset
SELECT t.ok(t.m_as('starbuddy_scheduler', NULL, 'SELECT platform.execute_due_erasures(now())::text')::int = 0,
            '定时任务的删除作业照常执行（没有到期的删除请求）');
SELECT t.ok(NOT EXISTS (SELECT 1 FROM platform.today_plan_item WHERE id IN (:'s1_old', :'s2_old'))
            AND (SELECT count(*) FROM platform.today_plan_item WHERE id IN (:'s1_keep', :'s2_keep', :TS2)) = 3
            AND (SELECT count(*) FROM platform.today_plan_item) = :n_before - 2,
            '删除作业清掉保留期满（计划日 ≤ 今天 − 90 天）的快照行，边界内与当天的保留，两名学生同样处理');
SELECT t.ok((SELECT count(*) FROM platform.erasure_request) = :other_before AND NOT EXISTS (
              SELECT 1 FROM platform.erasure_request WHERE receipt ? 'platform.today_plan_item' AND executed_at > now() - interval '1 minute'),
            '保留期清理不产生删除请求与回执');
SELECT t.ok(NOT EXISTS (SELECT 1 FROM pg_proc p WHERE p.pronamespace IN ('platform'::regnamespace, 'insight'::regnamespace)
                          AND p.oid <> 'platform.execute_due_erasures(timestamptz)'::regprocedure
                          AND p.prosrc ~* 'delete\s+from\s+(only\s+)?"?platform"?\s*\.\s*"?today_plan_item'),
            '只有已有的删除作业（execute_due_erasures）删快照行，不另建清理函数');
-- 清理失败只跳过清理（子事务隔离）：以超级用户在快照表上临时挂一个删除时报错的触发器，让清理这一步失败；
-- 删除作业照常完成其余工作（这里是把超时未确认的删除请求作废），到期快照行留到下次
SELECT t.tp_task_on(:S1, 41, t.tp_today() - 100) AS s1_stale \gset
INSERT INTO platform.erasure_request (student_id, requested_by, requested_at) VALUES (:S1, :gA, now() - interval '2 days') RETURNING id AS stale_req \gset
CREATE FUNCTION t.tp_boom() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'synthetic purge failure'; END $$;
CREATE TRIGGER t_tp_boom BEFORE DELETE ON platform.today_plan_item FOR EACH ROW EXECUTE FUNCTION t.tp_boom();
SELECT t.ok(t.m_as('starbuddy_scheduler', NULL, 'SELECT platform.execute_due_erasures(now())::text')::int = 0,
            '保留期清理失败时删除作业不报错');
SELECT t.ok(EXISTS (SELECT 1 FROM platform.today_plan_item WHERE id = :'s1_stale'), '清理失败：到期快照行留到下次');
SELECT t.ok((SELECT status || ':' || cancel_reason FROM platform.erasure_request WHERE id = :'stale_req') = 'cancelled:confirm_expired',
            '清理失败不影响删除请求的处理（超时未确认的作废）');
DROP TRIGGER t_tp_boom ON platform.today_plan_item;
ROLLBACK;

-- ================= 六、提交两户各两条当天的快照行（学习服务写入），留给 80 的可见性矩阵 =================
BEGIN;
SELECT t.m_as('starbuddy_learning', NULL, format($f$INSERT INTO platform.today_plan_item (student_id, seq, subject, kind, source, node_id,
    estimated_minutes, entry_ref, rationale_code) VALUES
    (%L, 1, 'aya', 'retest', 'retest', 'MATH.FUNC.QUAD.GRAPH', 5, '7d150000-0000-4000-8000-0000000000c1', NULL),
    (%L, 2, 'aya', 'lesson', 'plan', NULL, 5, NULL, 'prerequisite_of_many'),
    (%L, 1, 'aya', 'practice', 'practice', 'MATH.FUNC.QUAD.ROOT', 8, '7d150000-0000-4000-8000-0000000000c2', NULL),
    (%L, 2, 'aya', 'review', 'plan', NULL, 10, NULL, NULL) RETURNING 'ok'$f$, :S1, :S1, :S2, :S2));
COMMIT;
SELECT t.ok((SELECT count(DISTINCT s.household_id) FROM platform.today_plan_item i JOIN platform.student_profile s ON s.id = i.student_id) = 2,
            '两户各有当天的快照行（80 的可见性矩阵不空转）');
