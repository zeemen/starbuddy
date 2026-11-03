-- 0036 跨科周报 v0 快照与周报抽检（specs/trust-ui-sprint5/spec.md 验收“周报”的库层部分；待人确认 #3、#4）。全部合成数据（规则 8）。
-- 学生 S1（c1，账号 a1，家长 gA）、S2（c2，账号 b1，家长 gB）；gC 没有绑定；staff 5f。
-- 用 79m 的 t.m_as(角色, 账号, SQL) 与 76 的 t.as_staff(staff, SQL)。前半在一个事务里，结束回滚；
-- 最后一段提交两户、两个受众各一份周报，留给 80 的可见性矩阵。
\set S1 '''c1000000-0000-4000-8000-0000000000c1'''
\set S2 '''c2000000-0000-4000-8000-0000000000c2'''
\set aS1 '''a1000000-0000-4000-8000-0000000000a1'''
\set aS2 '''b1000000-0000-4000-8000-0000000000b1'''
\set gA '''a0000000-0000-4000-8000-00000000000a'''
\set gB '''b0000000-0000-4000-8000-00000000000b'''
\set gC '''cc000000-0000-4000-8000-0000000000cc'''
\set sf '''5f000000-0000-4000-8000-00000000005f'''
\set C1 '''e7100000-0000-4000-8000-000000000001'''
\set C2 '''e7100000-0000-4000-8000-000000000002'''
\set C3 '''e7100000-0000-4000-8000-000000000003'''
\set C4 '''e7100000-0000-4000-8000-000000000004'''
\set C5 '''e7100000-0000-4000-8000-000000000005'''
\set C6 '''e7100000-0000-4000-8000-000000000006'''
\set C7 '''e7100000-0000-4000-8000-000000000007'''
\set C8 '''e7100000-0000-4000-8000-000000000008'''
\set C9 '''e7100000-0000-4000-8000-000000000009'''
\set C10 '''e7100000-0000-4000-8000-00000000000a'''
\set C11 '''e7100000-0000-4000-8000-00000000000b'''
\set C12 '''e7100000-0000-4000-8000-00000000000c'''
\set C13 '''e7100000-0000-4000-8000-00000000000d'''

-- 一句（受众决定是否带置信度分档）
CREATE FUNCTION t.r_sent(p_claim uuid, p_section text, p_audience text, p_version integer DEFAULT 1, p_key text DEFAULT 'test.weekly.node')
  RETURNS jsonb LANGUAGE sql IMMUTABLE AS $$
  SELECT jsonb_build_object('claim_id', p_claim, 'template_key', p_key, 'template_version', p_version, 'section', p_section, 'text', '合成渲染句')
         || CASE WHEN p_audience = 'guardian' THEN '{"confidence_band": "moderate"}'::jsonb ELSE '{}'::jsonb END $$;
CREATE FUNCTION t.r_obj() RETURNS jsonb LANGUAGE sql IMMUTABLE AS $$
  SELECT '{"source_type": "objective", "usage_minutes": 95, "plan_done": 4, "plan_skipped": 1,
           "subjects": [{"subject": "aya", "practice_minutes": 60, "attempts": 18}]}'::jsonb $$;
-- 洞察服务写一份周报，返回 id
CREATE FUNCTION t.r_ins(p_student uuid, p_week date, p_audience text, p_sentences jsonb, p_objective jsonb DEFAULT t.r_obj(),
                        p_status text DEFAULT 'generated', p_reasons text[] DEFAULT '{}') RETURNS uuid LANGUAGE sql AS $$
  SELECT t.m_as('starbuddy_insight', NULL, format(
    'INSERT INTO insight.weekly_report (student_id, week_start, week_end, audience, status, sentences, objective, withheld_reasons,
                                        algorithm_version, policy_version)
     VALUES (%L, %L::date, %L::date + 6, %L, %L, %L::jsonb, %L::jsonb, %L::text[], ''sie-weekly@0.1.0'', ''syn-weekly-0.1.0'') RETURNING id::text',
    p_student, p_week, p_week, p_audience, p_status, p_sentences, p_objective, p_reasons))::uuid $$;
-- 以 BFF 身份按某个账号数能看到的周报
CREATE FUNCTION t.r_seen(p_acct uuid, p_where text DEFAULT 'true') RETURNS integer LANGUAGE sql AS $$
  SELECT t.m_as('starbuddy_bff', p_acct, format('SELECT count(*)::text FROM insight.weekly_report WHERE %s', p_where))::integer $$;

BEGIN;
SELECT date_trunc('week', (now() AT TIME ZONE 'Asia/Shanghai')::date)::date - 7 AS wk \gset
INSERT INTO insight.claim_template (template_key, version, target_type, max_visibility, max_ttl, text_template, status, reviewed_by, reviewed_at) VALUES
  ('test.weekly.node', 1, 'node', '{agent,student,guardian}', '60 days', '合成模板：周报句', 'approved', 'test-reviewer', now()),
  ('test.weekly.draft', 1, 'node', '{agent,student,guardian}', '60 days', '合成模板：未审核', 'draft', NULL, NULL),
  ('test.weekly.retired', 1, 'node', '{agent,student,guardian}', '60 days', '合成模板：已退役', 'retired', 'test-reviewer', now());
SET LOCAL ROLE starbuddy_insight;
INSERT INTO insight.claim (id, student_id, subject, target_type, target_id, template_key, template_version, confidence, source_type,
                           status, change_reason, visibility, expires_at) VALUES
  (:C1, :S1, 'aya', 'node', 'MATH.FUNC.QUAD.GRAPH', 'test.weekly.node', 1, 0.8, 'objective', 'established', 'created', '{agent,student,guardian}', now() + interval '30 days'),
  (:C2, :S1, 'aya', 'node', 'MATH.FUNC.QUAD.AXIS', 'test.weekly.node', 1, 0.5, 'objective', 'hypothesis', 'created', '{agent,student,guardian}', now() + interval '30 days'),
  (:C3, :S1, 'aya', 'node', 'MATH.FUNC.QUAD.ROOT', 'test.weekly.node', 1, 0.8, 'objective', 'established', 'created', '{agent,student}', now() + interval '30 days'),
  (:C4, :S1, 'aya', 'node', 'MATH.FUNC.QUAD.VERTEX_FORM', 'test.weekly.node', 1, 0.8, 'objective', 'established', 'created', '{agent,guardian}', now() + interval '30 days'),
  (:C5, :S2, 'aya', 'node', 'MATH.FUNC.QUAD.GRAPH', 'test.weekly.node', 1, 0.8, 'objective', 'established', 'created', '{agent,student,guardian}', now() + interval '30 days'),
  (:C6, :S1, 'aya', 'node', 'MATH.FUNC.LIN.SLOPE', 'test.weekly.node', 1, 0.8, 'objective', 'established', 'created', '{agent,student,guardian}', now() + interval '30 days'),
  (:C11, :S2, 'aya', 'node', 'MATH.FUNC.QUAD.AXIS', 'test.weekly.node', 1, 0.6, 'objective', 'hypothesis', 'created', '{agent,student,guardian}', now() + interval '30 days');
UPDATE insight.claim SET status = 'to_verify', change_reason = 'exam_deviation' WHERE id = :C6;
RESET ROLE;
-- 过期的、来源为家长观察的、模板未审核的、指标类的判断：以超级用户在复制模式下直接写（只为造出这些状态）
SET LOCAL session_replication_role = replica;
INSERT INTO insight.claim (id, student_id, subject, target_type, target_id, template_key, template_version, confidence, source_type,
                           status, change_reason, visibility, expires_at, created_at, updated_at, evidence_ids) VALUES
  (:C7, :S1, 'aya', 'node', 'MATH.FUNC.LIN.GRAPH', 'test.weekly.node', 1, 0.8, 'objective', 'established', 'created', '{agent,student,guardian}',
   now() - interval '1 hour', now() - interval '2 days', now() - interval '2 days', '{}'),
  (:C8, :S1, 'aya', 'node', 'MATH.FUNC.LIN.ROOT', 'test.weekly.node', 1, 0.8, 'guardian', 'established', 'created', '{agent,student,guardian}',
   now() + interval '30 days', now(), now(), '{}'),
  (:C9, :S1, 'aya', 'node', 'MATH.FUNC.LIN.AXIS', 'test.weekly.draft', 1, 0.8, 'objective', 'established', 'created', '{agent,student,guardian}',
   now() + interval '30 days', now(), now(), '{}'),
  (:C12, :S1, 'aya', 'node', 'MATH.FUNC.LIN.SLOPE_B', 'test.weekly.retired', 1, 0.8, 'objective', 'established', 'created', '{agent,student,guardian}',
   now() + interval '30 days', now(), now(), '{}'),
  (:C13, :S1, 'aya', 'node', 'MATH.FUNC.LIN.NOSUBJ', 'test.weekly.node', 1, 0.8, 'objective', 'established', 'created', '{agent,student,guardian}',
   now() + interval '30 days', now(), now(), '{}'),
  (:C10, :S1, NULL, 'metric', 'independent_solve_rate', 'test.weekly.node', 1, 0.8, 'objective', 'hypothesis', 'created', '{agent,student,guardian}',
   now() + interval '30 days', now(), now(), '{e1000000-0000-4000-8000-000000000001}');
SET LOCAL session_replication_role = origin;

-- ================= 一、表、清单与权限 =================
SELECT t.ok((SELECT relrowsecurity AND relforcerowsecurity FROM pg_class WHERE oid = 'insight.weekly_report'::regclass),
            '周报表 ENABLE + FORCE RLS');
SELECT t.ok(EXISTS (SELECT 1 FROM platform.personal_table_registry WHERE nspname = 'insight' AND relname = 'weekly_report'
                     AND strategy = 'delete' AND erase_scope = 'reports' AND export_topic IS NULL AND student_predicate = 'student_id = $1'),
            '周报登记进个人层表清单：随删除请求删除（回执范围 reports），不进导出包');
SELECT t.ok(t.p_unregistered() = '', '个人层表清单与库一致（未登记：' || t.p_unregistered() || '）');
SELECT t.ok(has_any_column_privilege('starbuddy_insight', 'insight.weekly_report', 'INSERT')
            AND NOT has_table_privilege('starbuddy_insight', 'insight.weekly_report', 'UPDATE')
            AND NOT has_table_privilege('starbuddy_insight', 'insight.weekly_report', 'DELETE')
            AND NOT has_column_privilege('starbuddy_insight', 'insight.weekly_report', 'spot_check_task_id', 'INSERT')
            AND NOT has_column_privilege('starbuddy_insight', 'insight.weekly_report', 'generated_at', 'INSERT'),
            '洞察服务只能新建（不能改、不能删，不能自己写抽检标记与生成时间）');
SELECT t.ok(NOT has_table_privilege(r, 'insight.weekly_report', 'INSERT') AND NOT has_table_privilege(r, 'insight.weekly_report', 'UPDATE')
            AND NOT has_table_privilege(r, 'insight.weekly_report', 'DELETE'),
            format('%s 不能写周报', r))
  FROM unnest(ARRAY['starbuddy_bff', 'starbuddy_learning', 'starbuddy_review', 'starbuddy_scheduler']) r;
SELECT t.ok(NOT has_table_privilege('starbuddy_learning', 'insight.weekly_report', 'SELECT')
            AND NOT has_table_privilege('starbuddy_review', 'insight.weekly_report', 'SELECT'),
            '学习服务与审核台读不到周报表（审核台只经复核详情函数看被抽检的周报）');
SELECT t.ok(NOT has_column_privilege('starbuddy_bff', 'insight.weekly_report', c, 'SELECT'), format('BFF 读不到 %s', c))
  FROM unnest(ARRAY['withheld_reasons', 'algorithm_version', 'policy_version', 'spot_check_task_id']) c;
SELECT t.ok(has_table_privilege('starbuddy_eraser', 'insight.weekly_report', 'DELETE'), '删除执行身份能删周报');

-- ================= 二、写入：每句只来自判断 =================
SELECT t.r_ins(:S1, :'wk', 'student', jsonb_build_array(t.r_sent(:C1, 'lit', 'student'), t.r_sent(:C2, 'working_on', 'student') || '{"label": "preliminary"}'))
  AS w_s1 \gset
SELECT t.ok((SELECT status = 'generated' AND week_end = :'wk'::date + 6 AND spot_check_task_id IS NULL AND generated_at = now()
                    AND jsonb_array_length(sentences) = 2 AND objective ->> 'source_type' = 'objective'
               FROM insight.weekly_report WHERE id = :'w_s1'),
            '学生版成长手记：两句都引用可见判断；客观栏标来源 objective；生成时间由库写');
SELECT t.throws(format('SELECT t.r_ins(%L, %L, ''student'', %L)', :S1, :'wk', jsonb_build_array(t.r_sent(:C3, 'lit', 'student'))),
                '23505', '同一学生、同一周、同一受众只有一份（快照只追加）');
SELECT t.r_ins(:S1, :'wk', 'guardian', jsonb_build_array(t.r_sent(:C1, 'progress', 'guardian'), t.r_sent(:C4, 'stuck', 'guardian'))) AS w_g1 \gset
SELECT t.ok(:'w_g1' IS NOT NULL, '家长版周报：每句带置信度分档');
SELECT t.throws(format('SELECT t.r_ins(%L, %L, %L, %L)', :S1, :'wk'::date - 7, a, jsonb_build_array(t.r_sent(c, s, a))), '23514', m)
  FROM (VALUES
    (:C4::uuid, 'lit', 'student', '学生版不能引用只对家长可见的判断'),
    (:C3::uuid, 'progress', 'guardian', '家长版不能引用只对学生可见的判断'),
    (:C5::uuid, 'lit', 'student', '不能引用别的学生的判断'),
    (:C6::uuid, 'lit', 'student', '不能引用待验证的判断（规则 11：只有成立或假设能作陈述）'),
    (:C7::uuid, 'lit', 'student', '不能引用已过期的判断'),
    (:C8::uuid, 'lit', 'student', '学生版不含家长观察来源的判断（学生版不含家长问卷与家长评价）'),
    (:C10::uuid, 'lit', 'student', '不能引用指标类判断（中圈指标不对学生、家长展示）'),
    ('e7100000-0000-4000-8000-0000000000ff'::uuid, 'lit', 'student', '不能引用不存在的判断')) AS x(c, s, a, m);
SELECT t.throws(format('SELECT t.r_ins(%L, %L, ''student'', %L)', :S1, :'wk'::date - 7,
                       jsonb_build_array(t.r_sent(:C9, 'lit', 'student', 1, 'test.weekly.draft'))), '23514', '不能引用模板未审核的判断');
SELECT t.throws(format('SELECT t.r_ins(%L, %L, ''student'', %L)', :S1, :'wk'::date - 7,
                       jsonb_build_array(t.r_sent(:C12, 'lit', 'student', 1, 'test.weekly.retired'))), '23514', '不能引用模板已退役的判断（洞察服务看得到退役模板，守卫另核审核状态）');
-- “初步判断”标注当且仅当判断是假设（安全审查 F3）
SELECT t.throws(format('SELECT t.r_ins(%L, %L, ''student'', %L)', :S1, :'wk'::date - 7, jsonb_build_array(t.r_sent(:C1, 'lit', 'student') || '{"label": "preliminary"}')),
                '23514', '成立的判断不能标“初步判断”');
SELECT t.throws(format('SELECT t.r_ins(%L, %L, ''student'', %L)', :S1, :'wk'::date - 7, jsonb_build_array(t.r_sent(:C2, 'working_on', 'student'))),
                '23514', '假设状态的判断必须标“初步判断”');
-- 判断修订标记（安全审查 F1）：守卫盖上写入时判断的 updated_at；调用方给了就必须一致
SELECT t.ok((SELECT bool_and((x ->> 'claim_revision')::timestamptz = c.updated_at) AND count(*) = 2
               FROM insight.weekly_report w, jsonb_array_elements(w.sentences) x JOIN insight.claim c ON c.id = (x ->> 'claim_id')::uuid
              WHERE w.id = :'w_s1'),
            '每句都盖上了所引判断写入时的修订标记（updated_at）');
SELECT t.throws(format('SELECT t.r_ins(%L, %L, ''student'', %L)', :S1, :'wk'::date - 7,
                       jsonb_build_array(t.r_sent(:C1, 'lit', 'student') || '{"claim_revision": "2020-01-01T00:00:00+00:00"}')),
                '23514', '修订标记与库里的判断对不上（渲染之后判断又被改过）：不收');
SELECT t.throws(format('SELECT t.r_ins(%L, %L, ''student'', %L)', :S1, :'wk'::date - 7,
                       jsonb_build_array(t.r_sent(:C1, 'lit', 'student') || '{"claim_revision": "yesterday"}')),
                '23514', '修订标记必须是时间戳');
-- 只收已结束的周（契约审查 L3）
SELECT t.throws(format('SELECT t.r_ins(%L, %L, ''student'', %L)', :S1, :'wk'::date + 7, jsonb_build_array(t.r_sent(:C1, 'lit', 'student'))),
                '23514', '本周还没结束：不收');
SELECT t.throws(format('SELECT t.r_ins(%L, %L, ''student'', ''[]'', NULL, ''withheld'', ''{forbidden_inference}'')', :S1, :'wk'::date + 7),
                '23514', '本周还没结束：不出的也不收');
SELECT t.throws(format('SELECT t.r_ins(%L, %L, ''student'', %L)', :S1, :'wk'::date - 7, jsonb_build_array(t.r_sent(:C1, 'lit', 'student', 2))),
                '23514', '句子的模板版本必须与判断一致');
SELECT t.throws(format('SELECT t.r_ins(%L, %L, ''student'', %L)', :S1, :'wk'::date - 7,
                       jsonb_build_array(t.r_sent(:C1, 'lit', 'student', 1, 'test.weekly.other'))), '23514', '句子的模板键必须与判断一致');
SELECT t.ok(t.r_ins(:S1, :'wk'::date - 7, 'guardian', jsonb_build_array(t.r_sent(:C8, 'progress', 'guardian'))) IS NOT NULL,
            '家长版可以引用家长观察来源的判断（学生与家长说法分开存，家长看得到自己的）');
-- 句子形状
SELECT t.throws(format('SELECT t.r_ins(%L, %L, %L, %L)', :S1, :'wk'::date - 14, a, jsonb_build_array(s)), '23514', m)
  FROM (VALUES
    (t.r_sent(:C1, 'lit', 'student') - 'claim_id', 'student', '没有 claim_id 的句子写不进周报'),
    (t.r_sent(:C1, 'lit', 'student') || '{"claim_id": "not-a-uuid"}', 'student', 'claim_id 必须是 UUID'),
    (t.r_sent(:C1, 'lit', 'student') || '{"confidence_band": "strong"}', 'student', '学生版不带置信度分档'),
    (t.r_sent(:C1, 'progress', 'guardian') - 'confidence_band', 'guardian', '家长版每句都带置信度分档'),
    (t.r_sent(:C1, 'progress', 'guardian') || '{"confidence_band": "high"}', 'guardian', '置信度分档只收三个编码'),
    (t.r_sent(:C1, 'progress', 'student'), 'student', '学生版只收学生的固定段落'),
    (t.r_sent(:C1, 'lit', 'guardian'), 'guardian', '家长版只收家长的固定段落'),
    (t.r_sent(:C1, 'lit', 'student') || '{"dialogue": "逐句对话"}', 'student', '句子不能带别的键（没有对话原文的位置）'),
    (t.r_sent(:C1, 'lit', 'student') || '{"peer_rank": 3}', 'student', '句子不能带排名、同伴比较'),
    (t.r_sent(:C1, 'lit', 'student') || '{"text": "{node_id} 未渲染"}', 'student', '未渲染完的模板变量写不进周报'),
    (t.r_sent(:C1, 'lit', 'student') || '{"text": "  "}', 'student', '句子文字不能为空'),
    (t.r_sent(:C1, 'lit', 'student') || '{"label": "weak"}', 'student', '标注只能是 preliminary')) AS x(s, a, m);
SELECT t.throws(format('SELECT t.r_ins(%L, %L, ''student'', %L)', :S1, :'wk'::date - 14,
                       jsonb_build_array(t.r_sent(:C1, 'lit', 'student'), t.r_sent(:C1, 'working_on', 'student'))), '23514', '同一条判断在一份周报里只出现一次');
-- 状态
SELECT t.throws(format('SELECT t.r_ins(%L, %L, ''student'', ''[]'')', :S1, :'wk'::date - 14), '23514', '已生成的周报至少一句');
SELECT t.throws(format('SELECT t.r_ins(%L, %L, ''student'', %L, NULL)', :S1, :'wk'::date - 14, jsonb_build_array(t.r_sent(:C1, 'lit', 'student'))),
                '23514', '已生成的周报必须有客观栏');
SELECT t.throws(format('SELECT t.r_ins(%L, %L, ''student'', %L, NULL, ''withheld'', ''{forbidden_inference}'')', :S1, :'wk'::date - 14,
                       jsonb_build_array(t.r_sent(:C1, 'lit', 'student'))), '23514', '不出的周报不存任何句子');
SELECT t.throws(format('SELECT t.r_ins(%L, %L, ''student'', ''[]'', NULL, ''withheld'')', :S1, :'wk'::date - 14), '23514', '不出的周报必须写阻断编码');
SELECT t.throws(format('SELECT t.r_ins(%L, %L, ''student'', ''[]'', %L, ''withheld'', ''{forbidden_inference}'')', :S1, :'wk'::date - 14, t.r_obj()),
                '23514', '不出的周报也不存客观栏');
SELECT t.r_ins(:S1, :'wk'::date - 14, 'student', '[]', NULL, 'withheld', '{forbidden_inference,degree_escalation}') AS w_held \gset
SELECT t.ok(:'w_held' IS NOT NULL, '一致性或禁用推断任一不过：记为 withheld（只有阻断编码）');
-- 客观栏与周
SELECT t.throws(format('SELECT t.r_ins(%L, %L, ''guardian'', %L, %L)', :S1, :'wk'::date - 21, jsonb_build_array(t.r_sent(:C1, 'progress', 'guardian')), o),
                '23514', m)
  FROM (VALUES
    (t.r_obj() || '{"source_type": "inferred"}', '客观栏只能标来源 objective'),
    (t.r_obj() - 'source_type', '客观栏必须标来源'),
    (t.r_obj() || '{"rank": 3}', '客观栏不能带别的键（没有排名的位置）'),
    (t.r_obj() || '{"usage_minutes": -5}', '使用时长是非负整数'),
    (t.r_obj() || '{"usage_minutes": 20000}', '一周的使用时长有上限'),
    (t.r_obj() || '{"subjects": [{"subject": "echo", "practice_minutes": 1, "attempts": 1}]}', '英语不出现在周报里'),
    (t.r_obj() || '{"subjects": [{"subject": "aya", "practice_minutes": 1, "attempts": 1}, {"subject": "aya", "practice_minutes": 2, "attempts": 1}]}',
     '客观栏的学科不重复')) AS x(o, m);
SELECT t.throws(format('SELECT t.r_ins(%L, %L, ''student'', %L)', :S1, :'wk'::date - 20, jsonb_build_array(t.r_sent(:C1, 'lit', 'student'))),
                '23514', '周报的周从周一开始');
SELECT t.throws(format($$SELECT t.m_as('starbuddy_insight', NULL, %L)$$, format(
                  $q$INSERT INTO insight.weekly_report (student_id, week_start, week_end, audience, status, sentences, objective, algorithm_version, policy_version)
                     VALUES (%L, %L, %L, 'student', 'generated', %L, %L, 'sie-weekly@0.1.0', 'syn') RETURNING id::text$q$,
                  :S1, :'wk'::date - 28, :'wk'::date - 20, jsonb_build_array(t.r_sent(:C1, 'lit', 'student')), t.r_obj())),
                '23514', '周报的周是周一到周日');
SELECT t.throws(format($$SELECT t.m_as('starbuddy_insight', NULL, %L)$$, format(
                  $q$INSERT INTO insight.weekly_report (student_id, week_start, week_end, audience, status, sentences, objective, algorithm_version, policy_version,
                                                        spot_check_task_id)
                     VALUES (%L, %L, %L, 'student', 'generated', %L, %L, 'sie-weekly@0.1.0', 'syn', NULL) RETURNING id::text$q$,
                  :S1, :'wk'::date - 28, :'wk'::date - 22, jsonb_build_array(t.r_sent(:C1, 'lit', 'student')), t.r_obj())),
                '42501', '洞察服务不能自己写抽检标记');

-- ================= 三、只追加；删除只随删除请求 =================
SELECT t.throws(format($$SELECT t.m_as('starbuddy_insight', NULL, 'UPDATE insight.weekly_report SET sentences = ''[]'' WHERE id = ''%s'' RETURNING id::text')$$, :'w_s1'),
                '42501', '洞察服务不能改周报');
SELECT t.throws(format($$SELECT t.m_as('starbuddy_insight', NULL, 'DELETE FROM insight.weekly_report WHERE id = ''%s'' RETURNING id::text')$$, :'w_s1'),
                '42501', '洞察服务不能删周报');
SELECT t.throws_like(format($$UPDATE insight.weekly_report SET objective = %L WHERE id = %L$$, t.r_obj() || '{"usage_minutes": 1}', :'w_s1'),
                     '42501', '只追加', '周报只追加：内容写入后不能改（守卫，超级用户也一样）');
SELECT t.throws_like(format($$DELETE FROM insight.weekly_report WHERE id = %L$$, :'w_s1'), '42501', '只随删除请求', '周报只随删除请求删除（守卫）');
SELECT t.throws_like('TRUNCATE insight.weekly_report CASCADE', '42501', '不能清空', '周报不能清空');
SAVEPOINT erase;
SET LOCAL ROLE starbuddy_eraser;
SELECT set_config('starbuddy.erasure_student', :S2, true);
SELECT t.throws_like(format($$DELETE FROM insight.weekly_report WHERE student_id = %L$$, :S1), '42501', '只随删除请求',
                     '删除执行身份也只能删正在删除的那个学生的周报');
SELECT set_config('starbuddy.erasure_student', :S1, true);
DELETE FROM insight.weekly_report WHERE student_id = :S1;
RESET ROLE;
SELECT t.ok(NOT EXISTS (SELECT 1 FROM insight.weekly_report WHERE student_id = :S1), '删除请求执行中（eraser + 本事务学生）能删该学生的周报');
ROLLBACK TO SAVEPOINT erase;

-- ================= 四、读：学生本人读学生版、有效绑定家长读家长版（规则 5） =================
SELECT t.r_ins(:S2, :'wk', 'student', jsonb_build_array(t.r_sent(:C5, 'lit', 'student'))) AS w_s2 \gset
SELECT t.r_ins(:S2, :'wk', 'guardian', jsonb_build_array(t.r_sent(:C5, 'progress', 'guardian'), t.r_sent(:C11, 'stuck', 'guardian') || '{"label": "preliminary"}')) AS w_g2 \gset
SELECT t.ok(t.r_seen(:aS1) = 1 AND t.r_seen(:aS1, format('id = %L', :'w_s1')) = 1, '学生本人只看到自己的学生版（已生成的）');
SELECT t.ok(t.r_seen(:aS1, format('student_id = %L AND audience = ''guardian''', :S1)) = 0, '学生本人看不到家长版');
SELECT t.ok(t.r_seen(:aS1, format('id = %L', :'w_held')) = 0, '不出（withheld）的周报对学生本人不可见');
SELECT t.ok(t.r_seen(:gA) = 2 AND t.r_seen(:gA, 'audience = ''guardian''') = 2, '有效绑定家长只看到家长版（本周与上周两份）');
SELECT t.ok(t.r_seen(:gA, format('student_id = %L', :S2)) = 0 AND t.r_seen(:gB, format('student_id = %L', :S1)) = 0,
            '另一户的家长看不到这个孩子的周报');
SELECT t.ok(t.r_seen(:gC) = 0, '没有绑定的家长看不到任何周报');
SELECT t.ok(t.r_seen(NULL) = 0, '没有登录身份看不到任何周报');
SELECT t.ok(t.r_seen(:aS2) = 1 AND t.r_seen(:aS2, format('id = %L', :'w_s2')) = 1, '另一户的学生只看到自己的学生版');
SELECT t.throws(format($$SELECT t.m_as('starbuddy_bff', %L, 'SELECT withheld_reasons::text FROM insight.weekly_report LIMIT 1')$$, :aS1), '42501',
                'BFF 读不到阻断编码');
SELECT t.throws(format($$SELECT t.m_as('starbuddy_bff', %L, 'SELECT spot_check_task_id::text FROM insight.weekly_report LIMIT 1')$$, :gA), '42501',
                'BFF 读不到抽检标记');
SELECT t.throws(format($$SELECT t.m_as('starbuddy_bff', %L, %L)$$, :aS1, format(
                  'INSERT INTO insight.weekly_report (student_id, week_start, week_end, audience, status, sentences, objective, algorithm_version, policy_version) '
                  'VALUES (%L, %L, %L, ''student'', ''generated'', %L, %L, ''sie-weekly@0.1.0'', ''syn'') RETURNING id::text',
                  :S1, :'wk'::date - 35, :'wk'::date - 29, jsonb_build_array(t.r_sent(:C1, 'lit', 'student')), t.r_obj())),
                '42501', 'BFF 不能写周报');
SAVEPOINT revoke_link;
UPDATE platform.guardian_student_link SET status = 'revoked', revoked_at = now() WHERE guardian_account_id = :gA AND student_id = :S1;
SELECT t.ok(t.r_seen(:gA) = 0, '绑定解除后家长看不到这个孩子的周报');
ROLLBACK TO SAVEPOINT revoke_link;
SAVEPOINT disable_student;
UPDATE platform.account SET status = 'disabled' WHERE id = :aS1;
SELECT t.ok(t.r_seen(:aS1) = 0, '学生账号停用后看不到周报');
ROLLBACK TO SAVEPOINT disable_student;

-- ================= 五、抽检进统一复核队列（待人确认 #4：每周 10%、至少 5 份；不阻断） =================
SELECT t.ok(has_function_privilege('starbuddy_insight', 'platform.review_sample_weekly_reports(date, numeric, integer)', 'EXECUTE')
            AND NOT has_function_privilege('starbuddy_bff', 'platform.review_sample_weekly_reports(date, numeric, integer)', 'EXECUTE')
            AND NOT has_function_privilege('starbuddy_learning', 'platform.review_sample_weekly_reports(date, numeric, integer)', 'EXECUTE')
            AND NOT has_function_privilege('starbuddy_review', 'platform.review_sample_weekly_reports(date, numeric, integer)', 'EXECUTE')
            AND NOT has_function_privilege('public', 'platform.review_sample_weekly_reports(date, numeric, integer)', 'EXECUTE'),
            '周报抽样只授洞察服务');
SELECT t.throws(format('SELECT t.m_as(''starbuddy_insight'', NULL, %L)', format('SELECT platform.review_sample_weekly_reports(%L::date, %s, %s)::text', w, r, m)),
                '22023',
                format('抽样参数不合要求：%s', d))
  FROM (VALUES (:'wk'::date + 1, '0.1', '5', '周不是周一'), (:'wk'::date + 14, '0.1', '5', '将来的周'),
               (:'wk'::date - 42, '0.1', '5', '超过 35 天前的周'), (:'wk'::date, '0', '5', '比例为 0'),
               (:'wk'::date, '0.6', '5', '比例超过 0.5'), (:'wk'::date, '0.1', '-1', '最少份数为负'),
               (:'wk'::date, '0.1', '101', '最少份数过大')) AS x(w, r, m, d);
-- 上周 4 份已生成（S1、S2 各两个受众）：调用方传最少 0 份，库里兜底至少 5 份（不超过总数）→ 4 份全部进队（安全审查 F4）
SELECT t.ok(t.m_as('starbuddy_insight', NULL, format('SELECT platform.review_sample_weekly_reports(%L, 0.1, 0)::text', :'wk'))::integer = 4,
            '上周 4 份周报、库里兜底每周至少 5 份：4 份全部进队');
SELECT t.ok((SELECT count(*) = 4 AND bool_and(t.source_kind = 'weekly_report' AND t.reasons::text[] = ARRAY['spot_check'] AND t.subject = 'aya'
                                               AND t.status = 'open' AND t.student_id = w.student_id AND w.spot_check_task_id = t.id)
               FROM insight.weekly_report w JOIN platform.review_task t ON t.source_id = w.id
              WHERE w.week_start = :'wk'),
            '抽检任务：来源周报、原因 spot_check、学科取所引判断的学科；周报记下复核任务（抽检标记）');
SELECT t.ok(t.m_as('starbuddy_insight', NULL, format('SELECT platform.review_sample_weekly_reports(%L, 0.1, 5)::text', :'wk'))::integer = 0,
            '重复抽样不重复进队');
SELECT t.ok(t.r_seen(:aS1, format('id = %L', :'w_s1')) = 1, '抽检不阻断：被抽中的周报学生照样看得到');
-- 上周：S1 家长版一份已生成；再补 S2 两个受众，共 3 份已生成（两周前 S1 学生版不出）
SELECT t.r_ins(:S2, :'wk'::date - 7, 'student', jsonb_build_array(t.r_sent(:C5, 'lit', 'student'))) AS w_s2p \gset
SELECT t.r_ins(:S2, :'wk'::date - 7, 'guardian', jsonb_build_array(t.r_sent(:C5, 'progress', 'guardian'))) AS w_g2p \gset
SELECT t.ok(t.m_as('starbuddy_insight', NULL, format('SELECT platform.review_sample_weekly_reports(%L, 0.1, 1)::text', :'wk'::date - 7))::integer = 3,
            '最少份数传 1 也按 5 兜底：3 份全部进队');
-- 三周前：再加三名合成学生，共 10 份——比例、最少份数与“已抽的计入份数”
INSERT INTO platform.account (id, kind) VALUES ('e7a00000-0000-4000-8000-0000000000a3', 'student'), ('e7a00000-0000-4000-8000-0000000000a4', 'student'),
  ('e7a00000-0000-4000-8000-0000000000a5', 'student'), ('e7a00000-0000-4000-8000-0000000000a9', 'guardian');
INSERT INTO platform.household (id, created_by_account) VALUES ('e7d00000-0000-4000-8000-0000000000d9', 'e7a00000-0000-4000-8000-0000000000a9');
INSERT INTO platform.student_profile (id, account_id, household_id, grade, region, textbook_version, birth_year_month)
SELECT ('e7c00000-0000-4000-8000-0000000000c' || n)::uuid, ('e7a00000-0000-4000-8000-0000000000a' || n)::uuid, 'e7d00000-0000-4000-8000-0000000000d9',
       8, 'nanjing', 'bubian-2024', '2013-04-01' FROM generate_series(3, 5) n;
SET LOCAL ROLE starbuddy_insight;
INSERT INTO insight.claim (id, student_id, subject, target_type, target_id, template_key, template_version, confidence, source_type,
                           status, change_reason, visibility, expires_at)
SELECT ('e7e00000-0000-4000-8000-0000000000e' || n)::uuid, ('e7c00000-0000-4000-8000-0000000000c' || n)::uuid, 'aya', 'node', 'MATH.FUNC.QUAD.GRAPH',
       'test.weekly.node', 1, 0.8, 'objective', 'established', 'created', '{agent,student,guardian}', now() + interval '30 days' FROM generate_series(3, 5) n;
RESET ROLE;
SELECT t.r_ins(s, :'wk'::date - 21, a, jsonb_build_array(t.r_sent(c, CASE a WHEN 'student' THEN 'lit' ELSE 'progress' END, a)))
  FROM (SELECT :S1::uuid, :C1::uuid UNION ALL SELECT :S2::uuid, :C5::uuid
        UNION ALL SELECT ('e7c00000-0000-4000-8000-0000000000c' || n)::uuid, ('e7e00000-0000-4000-8000-0000000000e' || n)::uuid
                    FROM generate_series(3, 5) n) AS x(s, c), unnest(ARRAY['student', 'guardian']) a;
SELECT t.ok(t.m_as('starbuddy_insight', NULL, format('SELECT platform.review_sample_weekly_reports(%L, 0.1, 5)::text', :'wk'::date - 21))::integer = 5,
            '10 份：10% 向上取整 1 份、至少 5 份 → 5 份');
SELECT t.ok(t.m_as('starbuddy_insight', NULL, format('SELECT platform.review_sample_weekly_reports(%L, 0.1, 5)::text', :'wk'::date - 21))::integer = 0,
            '已抽过的计入份数：同样的参数不再进队');
SELECT t.ok(t.m_as('starbuddy_insight', NULL, format('SELECT platform.review_sample_weekly_reports(%L, 0.5, 6)::text', :'wk'::date - 21))::integer = 1,
            '最少份数提高时只补差额');
SELECT t.ok(t.m_as('starbuddy_insight', NULL, format('SELECT platform.review_sample_weekly_reports(%L, 0.5, 10)::text', :'wk'::date - 14))::integer = 0
            AND NOT EXISTS (SELECT 1 FROM platform.review_task WHERE source_id = :'w_held'),
            '不出（withheld）的周报不进抽检');
SELECT t.r_ins(:S1, :'wk'::date - 14, 'guardian', jsonb_build_array(t.r_sent(:C13, 'stuck', 'guardian'))) AS w_nosubj \gset
-- 所引判断已不在（只有超级用户在复制模式下能造出来：判断只随删除请求删除，周报也同时删）
SAVEPOINT nosubj;
SET LOCAL session_replication_role = replica;
DELETE FROM insight.claim WHERE id = :C13;
SET LOCAL session_replication_role = origin;
SELECT t.throws_like(format($$SELECT t.m_as('starbuddy_insight', NULL, 'SELECT platform.review_sample_weekly_reports(''%s'', 0.5, 10)::text')$$, :'wk'::date - 14),
                     '23514', '取不到复核任务的学科', '所引判断取不到学科：抽样报错，不静默跳过（安全审查 F4）');
ROLLBACK TO SAVEPOINT nosubj;
SELECT t.throws_like(format($$UPDATE insight.weekly_report SET spot_check_task_id = (SELECT id FROM platform.review_task WHERE source_id = %L)
                              WHERE id = %L$$, :'w_g1', :'w_s1'),
                     '42501', '只追加', '抽检标记只能写一次');
SELECT t.r_ins(:S2, :'wk'::date - 28, 'student', jsonb_build_array(t.r_sent(:C5, 'lit', 'student'))) AS w_s2old \gset
SELECT t.throws_like(format($$UPDATE insight.weekly_report SET spot_check_task_id = (SELECT id FROM platform.review_task WHERE source_id = %L),
                                     objective = %L WHERE id = %L$$, :'w_g1', t.r_obj() || '{"usage_minutes": 1}', :'w_s2old'),
                     '42501', '只追加', '写抽检标记时不能顺带改内容');
SELECT t.throws(format($$SELECT t.m_as('starbuddy_learning', NULL, %L)$$, format(
                  'INSERT INTO platform.review_task (subject, student_id, source_kind, source_id, reasons) VALUES (''aya'', %L, ''weekly_report'', %L, ''{spot_check}'') RETURNING id::text',
                  :S1, :'w_held')), '42501', '学习服务不能把周报放进复核队列');
SELECT t.throws(format($$INSERT INTO platform.review_task (subject, student_id, source_kind, source_id, reasons)
                         VALUES ('aya', %L, 'weekly_report', %L, '{student_appeal}')$$, :S1, :'w_held'), '23514', '周报只以抽检（spot_check）进队');
SELECT t.ok(NOT has_table_privilege('starbuddy_insight', 'platform.review_task', 'INSERT'), '洞察服务不能直接写复核队列（只经抽样函数）');
-- 审核台：详情给受众、周、渲染句（带判断 ID）与客观栏，不给学生 ID；结案码只收周报的两个
SELECT id AS task_s1 FROM platform.review_task WHERE source_id = :'w_s1' \gset
SELECT t.as_staff(:sf, format('SELECT platform.take_review_task(%L)::text', :'task_s1'));
SELECT t.ok(t.as_staff(:sf, format($$SELECT (d -> 'source' ->> 'audience') || '|' || (d -> 'source' ->> 'week_start') || '|'
                                          || (d -> 'source' -> 'sentences' -> 0 ->> 'claim_id') || '|' || (d -> 'source' ? 'objective')::text
                                     FROM platform.review_task_detail(%L) d$$, :'task_s1'))
            = 'student|' || :'wk' || '|' || :C1 || '|false'
            AND position(:S1 IN t.as_staff(:sf, format('SELECT platform.review_task_detail(%L)::text', :'task_s1'))) = 0,
            '审核台详情：受众、周、每句的判断 ID；不给客观栏（安全审查 F5）、不给学生 ID');
SELECT t.throws(format($$SELECT t.as_staff(%L, 'SELECT platform.resolve_review_task(''%s'', ''upheld'')::text')$$, :sf, :'task_s1'), '22023',
                '周报抽检不能用判分的结案码');
SELECT t.throws(format($$SELECT t.as_staff(%L, 'SELECT platform.resolve_review_task(''%s'', ''dispute_forwarded'')::text')$$, :sf, :'task_s1'), '22023',
                '周报抽检不能用画像异议的结案码');
SELECT t.as_staff(:sf, format('SELECT platform.resolve_review_task(%L, ''weekly_report_ok'')::text', :'task_s1'));
SELECT t.ok((SELECT status = 'resolved' AND resolution_code = 'weekly_report_ok' FROM platform.review_task WHERE id = :'task_s1'),
            '周报抽检以 weekly_report_ok / weekly_report_issue 结案');
SELECT t.ok(t.as_staff(:sf, $$SELECT count(*)::text FROM platform.review_queue(NULL, NULL, 'spot_check', 200, NULL, NULL) WHERE source_kind = 'weekly_report'$$)::integer = 13,
            '周报抽检任务出现在统一复核队列里（按原因 spot_check 可筛）');
ROLLBACK;

-- ================= 留给 80 的可见性矩阵：两户、两个受众各一份已生成的周报 =================
BEGIN;
SELECT date_trunc('week', (now() AT TIME ZONE 'Asia/Shanghai')::date)::date - 7 AS wk \gset
INSERT INTO insight.claim_template (template_key, version, target_type, max_visibility, max_ttl, text_template, status, reviewed_by, reviewed_at)
VALUES ('test.weekly.node', 1, 'node', '{agent,student,guardian}', '60 days', '合成模板：周报句', 'approved', 'test-reviewer', now());
SET LOCAL ROLE starbuddy_insight;
INSERT INTO insight.claim (id, student_id, subject, target_type, target_id, template_key, template_version, confidence, source_type,
                           status, change_reason, visibility, expires_at) VALUES
  (:C1, :S1, 'aya', 'node', 'MATH.FUNC.QUAD.GRAPH', 'test.weekly.node', 1, 0.8, 'objective', 'established', 'created', '{agent,student,guardian}', now() + interval '30 days'),
  (:C5, :S2, 'aya', 'node', 'MATH.FUNC.QUAD.GRAPH', 'test.weekly.node', 1, 0.8, 'objective', 'established', 'created', '{agent,student,guardian}', now() + interval '30 days');
RESET ROLE;
SELECT t.r_ins(s, :'wk', a, jsonb_build_array(t.r_sent(c, CASE a WHEN 'student' THEN 'lit' ELSE 'progress' END, a)))
  FROM (VALUES (:S1::uuid, :C1::uuid), (:S2::uuid, :C5::uuid)) AS x(s, c), unnest(ARRAY['student', 'guardian']) a;
COMMIT;
SELECT t.ok((SELECT count(DISTINCT student_id) = 2 AND count(*) = 4 FROM insight.weekly_report WHERE status = 'generated'),
            '可见性矩阵的周报数据来自两户、两个受众');
