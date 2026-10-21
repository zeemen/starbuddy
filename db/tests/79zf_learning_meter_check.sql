-- 0058 学习服务写证据前的计量核对 platform.learning_meter_check（specs/launch-lt04-learning-internal-v1 决定记录（续）第 1、2 条）。
-- 全部合成数据（规则 8）：本文件自建一户两名学生、一份测试额度配置与三个额度桶；预留与自练计次以超级用户直写（触发器与约束照常生效），
-- 时间取 70 号换上的计量时钟（platform.meter_now() 读 t.clock）。整个文件在一个事务里，结束回滚。
-- 判据：usage_id、幂等键、学生、方法全部一致才返回状态，任一不一致与没有记录同为 none；只返回状态编码，不返回额度、数量与时间；
-- 只授 starbuddy_learning（越权调用 42501）；只读（不改预留状态、额度桶与流水）。
\set G '''f0580000-0000-4000-8000-0000000000a1'''
\set aS '''f0580000-0000-4000-8000-0000000000a2'''
\set aT '''f0580000-0000-4000-8000-0000000000a3'''
\set H '''f0580000-0000-4000-8000-0000000000b1'''
\set S '''f0580000-0000-4000-8000-0000000000c1'''
\set T '''f0580000-0000-4000-8000-0000000000c2'''
\set SUB '''f0580000-0000-4000-8000-0000000000d1'''
\set EG '''f0580000-0000-4000-8000-0000000000e1'''
\set ED '''f0580000-0000-4000-8000-0000000000e2'''
\set EW '''f0580000-0000-4000-8000-0000000000e3'''
\set U1 '''f0580000-0000-4000-8000-000000000101'''
\set U2 '''f0580000-0000-4000-8000-000000000102'''
\set U3 '''f0580000-0000-4000-8000-000000000103'''
\set U4 '''f0580000-0000-4000-8000-000000000104'''
\set U5 '''f0580000-0000-4000-8000-000000000105'''
\set U6 '''f0580000-0000-4000-8000-000000000106'''
\set U7 '''f0580000-0000-4000-8000-000000000107'''
\set U8 '''f0580000-0000-4000-8000-000000000108'''
\set U9 '''f0580000-0000-4000-8000-000000000109'''
\set K1 '''f0580000-0000-4000-8000-000000000201'''
\set K2 '''f0580000-0000-4000-8000-000000000202'''
\set K3 '''f0580000-0000-4000-8000-000000000203'''
\set K4 '''f0580000-0000-4000-8000-000000000204'''
\set K5 '''f0580000-0000-4000-8000-000000000205'''
\set K6 '''f0580000-0000-4000-8000-000000000206'''
\set K7 '''f0580000-0000-4000-8000-000000000207'''
\set K8 '''f0580000-0000-4000-8000-000000000208'''
\set K9 '''f0580000-0000-4000-8000-000000000209'''
\set U10 '''f0580000-0000-4000-8000-000000000110'''
\set U11 '''f0580000-0000-4000-8000-000000000111'''
\set U12 '''f0580000-0000-4000-8000-000000000112'''
\set U13 '''f0580000-0000-4000-8000-000000000113'''
\set U14 '''f0580000-0000-4000-8000-000000000114'''
\set U15 '''f0580000-0000-4000-8000-000000000115'''
\set U16 '''f0580000-0000-4000-8000-000000000116'''
\set K10 '''f0580000-0000-4000-8000-000000000210'''
\set K11 '''f0580000-0000-4000-8000-000000000211'''
\set K12 '''f0580000-0000-4000-8000-000000000212'''
\set K13 '''f0580000-0000-4000-8000-000000000213'''
\set K14 '''f0580000-0000-4000-8000-000000000214'''
\set K15 '''f0580000-0000-4000-8000-000000000215'''
\set K16 '''f0580000-0000-4000-8000-000000000216'''
\set FP '''0000000000000000000000000000000000000000000000000000000000000058'''
BEGIN;

-- ================= 夹具：一户两名学生、测试额度配置、三个额度桶 =================
INSERT INTO platform.account (id, kind) VALUES (:G, 'guardian'), (:aS, 'student'), (:aT, 'student');
INSERT INTO platform.household (id, created_by_account) VALUES (:H, :G);
INSERT INTO platform.student_profile (id, account_id, household_id, grade, region)
VALUES (:S, :aS, :H, 9, 'nanjing'), (:T, :aT, :H, 8, 'nanjing');
INSERT INTO platform.plan_config (version, note_code) VALUES ('lmc-test@1', 'db_test');
INSERT INTO platform.plan_allowance (config_version, tier, resource_type, quantity, fair_use)
SELECT 'lmc-test@1', tier, resource_type, quantity, fair_use FROM platform.plan_allowance WHERE config_version = 'ch8-hypothesis@1';
INSERT INTO platform.plan_feature (config_version, tier, feature_code, value_code)
SELECT 'lmc-test@1', tier, feature_code, value_code FROM platform.plan_feature WHERE config_version = 'ch8-hypothesis@1';
UPDATE platform.plan_config SET status = 'published', published_at = now() WHERE version = 'lmc-test@1';
INSERT INTO platform.subscription (id, household_id, student_id, tier, config_version, source, period_start, period_end)
VALUES (:SUB, :H, :S, 'pro', 'lmc-test@1', 'pilot_virtual', platform.meter_now() - interval '1 day', platform.meter_now() + interval '20 days');
INSERT INTO platform.entitlement (id, subscription_id, student_id, resource_type, period_start, period_end, granted)
SELECT x.id, :SUB, :S, x.res, s.period_start, s.period_end, 10
  FROM platform.subscription s,
       (VALUES (:EG::uuid, 'mistake_analysis'), (:ED::uuid, 'diagnosis_full'), (:EW::uuid, 'lesson_whiteboard')) x(id, res)
 WHERE s.id = :SUB;

-- 预留：U1 grade 预留中；U2 grade 已过期但过期任务还没跑（lapsed）；U3 grade 已结算；U4 diagnose 已释放；U5 grade 已过期（expired）；
-- U6 diagnose 预留中；U9 teach 预留中（不是学习服务的方法）
INSERT INTO platform.usage_reservation (usage_id, idempotency_key, request_fingerprint, entitlement_id, student_id, resource_type,
                                        reserved_qty, source_method, reserved_at, expires_at)
VALUES (:U1, :K1, :FP, :EG, :S, 'mistake_analysis', 1, 'grade', platform.meter_now() - interval '1 minute', platform.meter_now() + interval '5 minutes'),
       (:U2, :K2, :FP, :EG, :S, 'mistake_analysis', 1, 'grade', platform.meter_now() - interval '9 minutes', platform.meter_now() - interval '1 minute'),
       (:U3, :K3, :FP, :EG, :S, 'mistake_analysis', 1, 'grade', platform.meter_now() - interval '2 minutes', platform.meter_now() + interval '5 minutes'),
       (:U4, :K4, :FP, :ED, :S, 'diagnosis_full', 1, 'diagnose', platform.meter_now() - interval '2 minutes', platform.meter_now() + interval '5 minutes'),
       (:U5, :K5, :FP, :EG, :S, 'mistake_analysis', 1, 'grade', platform.meter_now() - interval '9 minutes', platform.meter_now() - interval '1 minute'),
       (:U6, :K6, :FP, :ED, :S, 'diagnosis_full', 1, 'diagnose', platform.meter_now() - interval '1 minute', platform.meter_now() + interval '5 minutes'),
       (:U9, :K9, :FP, :EW, :S, 'lesson_whiteboard', 1, 'teach', platform.meter_now() - interval '1 minute', platform.meter_now() + interval '5 minutes');
UPDATE platform.usage_reservation SET state = 'settled', settled_qty = 1, finished_at = platform.meter_now() WHERE usage_id = :U3;
UPDATE platform.usage_reservation SET state = 'released', finished_at = platform.meter_now() WHERE usage_id = :U4;
UPDATE platform.usage_reservation SET state = 'expired', finished_at = platform.meter_now() WHERE usage_id = :U5;
-- 题库自练计次：U7 学生 S，U8 学生 T
INSERT INTO platform.practice_log (usage_id, idempotency_key, student_id, occurred_at)
VALUES (:U7, :K7, :S, platform.meter_now()), (:U8, :K8, :T, platform.meter_now());

-- 诊断会话的过期放行（决定记录（再续）第 2 条）：diagnose 预留，有效期 9 分钟前到 1 分钟前
-- U10 已过期未处理（lapsed），会话 8 分钟前开始（有效期内）；U11 已过期（expired），会话 8 分钟前开始；
-- U12 已过期未处理，会话 1 分钟后才开始（晚于过期时间）；U13 已释放，会话在有效期内开始；
-- U14 已过期未处理，会话的幂等键是另一个；U15 已过期未处理，会话属于另一名学生；U16 grade 已过期未处理（grade 没有会话放行）
INSERT INTO platform.usage_reservation (usage_id, idempotency_key, request_fingerprint, entitlement_id, student_id, resource_type,
                                        reserved_qty, source_method, reserved_at, expires_at)
SELECT u, k, :FP, :ED, :S, 'diagnosis_full', 1, 'diagnose', platform.meter_now() - interval '9 minutes', platform.meter_now() - interval '1 minute'
  FROM (VALUES (:U10::uuid, :K10::text), (:U11, :K11), (:U12, :K12), (:U13, :K13), (:U14, :K14), (:U15, :K15)) x(u, k);
INSERT INTO platform.usage_reservation (usage_id, idempotency_key, request_fingerprint, entitlement_id, student_id, resource_type,
                                        reserved_qty, source_method, reserved_at, expires_at)
VALUES (:U16, :K16, :FP, :EG, :S, 'mistake_analysis', 1, 'grade', platform.meter_now() - interval '9 minutes', platform.meter_now() - interval '1 minute');
UPDATE platform.usage_reservation SET state = 'expired', finished_at = platform.meter_now() WHERE usage_id = :U11;
UPDATE platform.usage_reservation SET state = 'released', finished_at = platform.meter_now() WHERE usage_id = :U13;
-- 会话（只能以进行中新建，同一学生同一学科只能有一个未结束的会话：逐个新建、结束，最后只留 U10 进行中；
-- 核对函数不看会话状态，已结束或放弃由学习服务挡）
CREATE FUNCTION pg_temp.lmc_session(s uuid, u uuid, k uuid, started timestamptz, finish boolean) RETURNS void LANGUAGE plpgsql AS $$
BEGIN
  INSERT INTO platform.diagnosis_session (student_id, subject, algorithm_version, map_version, usage_id, idempotency_key, created_at)
  VALUES (s, 'aya', 'aya-diag-trace@0.1.0', '0.1.0', u, k, started);
  IF finish THEN
    UPDATE platform.diagnosis_session SET status = 'finished', finished_at = now() WHERE usage_id = u;
  END IF;
END $$;
SELECT pg_temp.lmc_session(:S, :U11, :K11, platform.meter_now() - interval '8 minutes', true);
SELECT pg_temp.lmc_session(:S, :U12, :K12, platform.meter_now() + interval '1 minute', true);
SELECT pg_temp.lmc_session(:S, :U13, :K13, platform.meter_now() - interval '8 minutes', true);
SELECT pg_temp.lmc_session(:S, :U14, gen_random_uuid(), platform.meter_now() - interval '8 minutes', true);
-- 与 grade 预留 U16 同 usage_id 与键的会话（构造的不一致数据）：grade 不因会话放行
SELECT pg_temp.lmc_session(:S, :U16, :K16, platform.meter_now() - interval '8 minutes', true);
SELECT pg_temp.lmc_session(:T, :U15, :K15, platform.meter_now() - interval '8 minutes', false);
SELECT pg_temp.lmc_session(:S, :U10, :K10, platform.meter_now() - interval '8 minutes', false);

-- 调用辅助：以某个角色调用核对函数（SET LOCAL ROLE 在 t.throws 的子事务里失败时随之回滚）
CREATE FUNCTION t.lmc_as(r text, u uuid, k text, s uuid, m text) RETURNS text LANGUAGE plpgsql AS $$
DECLARE v text;
BEGIN
  EXECUTE format('SET LOCAL ROLE %I', r);
  v := platform.learning_meter_check(u, k, s, m);
  RESET ROLE;
  RETURN v;
END $$;
CREATE FUNCTION t.lmc(u uuid, k text, s uuid, m text) RETURNS text LANGUAGE sql AS $$ SELECT t.lmc_as('starbuddy_learning', u, k, s, m) $$;

-- 只读前的计数（之后核对核对函数没有改任何东西）
SELECT (SELECT count(*) FROM platform.usage_ledger) AS ledger_before,
       (SELECT string_agg(usage_id::text || ':' || state::text, ',' ORDER BY usage_id) FROM platform.usage_reservation WHERE student_id = :S) AS states_before,
       (SELECT string_agg(id::text || ':' || reserved || ':' || settled, ',' ORDER BY id) FROM platform.entitlement WHERE subscription_id = :SUB) AS buckets_before \gset

-- ================= 一、函数属性与授权 =================
SELECT t.ok((SELECT p.prosecdef AND pg_get_userbyid(p.proowner) = 'starbuddy_authz' AND p.provolatile = 's'
                    AND coalesce('search_path=pg_catalog, platform, pg_temp' = ANY (p.proconfig), false)
               FROM pg_proc p WHERE p.oid = 'platform.learning_meter_check(uuid, text, uuid, text)'::regprocedure),
            '核对函数是 SECURITY DEFINER、属主 starbuddy_authz、STABLE（只读）、search_path 固定且以 pg_temp 结尾');
SELECT t.ok(pg_get_function_result('platform.learning_meter_check(uuid, text, uuid, text)'::regprocedure) = 'text'
            AND pg_get_function_arguments('platform.learning_meter_check(uuid, text, uuid, text)'::regprocedure)
                = 'p_usage uuid, p_key text, p_student uuid, p_method text',
            '只返回一个文本状态编码（没有额度、数量、指纹、时间的输出参数）');
SELECT t.ok(has_function_privilege('starbuddy_learning', 'platform.learning_meter_check(uuid, text, uuid, text)', 'EXECUTE')
            AND NOT EXISTS (SELECT 1 FROM unnest(ARRAY['starbuddy_bff', 'starbuddy_review', 'starbuddy_scheduler', 'starbuddy_insight',
                                                       'starbuddy_kge', 'starbuddy_report']) r(role)
                             WHERE EXISTS (SELECT 1 FROM pg_roles WHERE rolname = r.role)
                               AND has_function_privilege(r.role, 'platform.learning_meter_check(uuid, text, uuid, text)', 'EXECUTE')),
            '只授学习服务：BFF、复核、定时任务、洞察、知识服务、报表角色都不能执行');
SELECT t.ok(NOT EXISTS (SELECT 1 FROM pg_proc p, aclexplode(p.proacl) a
                         WHERE p.oid = 'platform.learning_meter_check(uuid, text, uuid, text)'::regprocedure AND a.grantee = 0),
            'PUBLIC 不能执行核对函数');
SELECT t.throws(format($$SELECT t.lmc_as('starbuddy_bff', %L, %L, %L, 'grade')$$, :U1, :K1, :S), '42501', 'BFF 角色调用核对函数被拒（越权）');
SELECT t.throws(format($$SELECT t.lmc_as('starbuddy_insight', %L, %L, %L, 'grade')$$, :U1, :K1, :S), '42501', '洞察服务角色调用核对函数被拒');
SELECT t.throws(format($$SELECT t.lmc_as('starbuddy_review', %L, %L, %L, 'grade')$$, :U1, :K1, :S), '42501', '复核角色调用核对函数被拒');
SELECT t.ok(NOT has_table_privilege('starbuddy_learning', 'platform.usage_reservation', 'SELECT')
            AND NOT has_any_column_privilege('starbuddy_learning', 'platform.usage_reservation', 'SELECT')
            AND NOT has_table_privilege('starbuddy_learning', 'platform.practice_log', 'SELECT')
            AND NOT has_any_column_privilege('starbuddy_learning', 'platform.practice_log', 'SELECT')
            AND NOT has_table_privilege('starbuddy_learning', 'platform.entitlement', 'SELECT')
            AND NOT has_table_privilege('starbuddy_learning', 'platform.usage_ledger', 'SELECT'),
            '学习服务对预留、自练计次、额度桶、流水仍没有表级或列级读权限（只开这一个函数）');
SELECT t.throws($$SET LOCAL ROLE starbuddy_learning; SELECT count(*) FROM platform.usage_reservation$$, '42501',
                '学习服务直接读预留表被拒');
-- 学习服务测试夹具（services/learning/tests/fixtures/meter_provision.sql）的代建函数只在一次性测试库里由超级用户建；
-- 迁移本身绝不能带出这个 schema（否则任何持有它执行权的角色都能凭空造预留）
SELECT t.ok(to_regnamespace('learningtest') IS NULL
            AND NOT EXISTS (SELECT 1 FROM pg_proc WHERE proname IN ('provision_meter', 'set_reservation')),
            '迁移后的库里没有测试夹具 schema learningtest，也没有代建预留的函数');

-- ================= 二、各状态编码 =================
SELECT t.ok(t.lmc(:U1, :K1, :S, 'grade') = 'reserved', '预留中且未过期 → reserved');
SELECT t.ok(t.lmc(:U2, :K2, :S, 'grade') = 'lapsed', '预留中但已过期（过期任务还没跑）→ lapsed，不当作 reserved');
SELECT t.ok(t.lmc(:U3, :K3, :S, 'grade') = 'settled', '已结算 → settled');
SELECT t.ok(t.lmc(:U4, :K4, :S, 'diagnose') = 'released', '已释放 → released');
SELECT t.ok(t.lmc(:U5, :K5, :S, 'grade') = 'expired', '已过期 → expired');
SELECT t.ok(t.lmc(:U6, :K6, :S, 'diagnose') = 'reserved', 'diagnose 预留中 → reserved');
SELECT t.ok(t.lmc(:U7, :K7, :S, 'grade') = 'counted', '题库自练计次（再练、复测作答）→ counted');
SELECT t.ok(t.lmc(gen_random_uuid(), gen_random_uuid()::text, :S, 'grade') = 'none', '没有记录 → none');

-- ================= 二之二、诊断会话在预留有效期内开始的过期放行（决定记录（再续）第 2 条） =================
SELECT t.ok(t.lmc(:U10, :K10, :S, 'diagnose') = 'started_in_time', '预留已过期未处理、会话在有效期内开始 → started_in_time');
SELECT t.ok(t.lmc(:U11, :K11, :S, 'diagnose') = 'started_in_time', '预留已过期（过期任务已跑）、会话在有效期内开始 → started_in_time');
SELECT t.ok(t.lmc(:U12, :K12, :S, 'diagnose') = 'lapsed', '会话在预留过期之后才开始 → 仍是 lapsed（不放行）');
SELECT t.ok(t.lmc(:U13, :K13, :S, 'diagnose') = 'released', '已释放的预留不因会话放行 → released');
SELECT t.ok(t.lmc(:U14, :K14, :S, 'diagnose') = 'lapsed', '会话的幂等键与预留不一致 → 不放行（lapsed）');
SELECT t.ok(t.lmc(:U15, :K15, :S, 'diagnose') = 'lapsed', '会话属于另一名学生 → 不放行（lapsed）');
SELECT t.ok(t.lmc(:U15, :K15, :T, 'diagnose') = 'none', '按会话的学生核对，预留学生对不上 → none');
SELECT t.ok(t.lmc(:U16, :K16, :S, 'grade') = 'lapsed' AND t.lmc(:U10, :K10, :S, 'grade') = 'none',
            'grade 没有会话放行：即使有同 usage_id 与键、在有效期内开始的会话，过期未处理仍是 lapsed；diagnose 的预留按 grade 核对 → none');

-- ================= 三、任一字段不一致返回 none（不透露 usage_id 或幂等键是否存在） =================
SELECT t.ok(t.lmc(:U1, :K3, :S, 'grade') = 'none', '幂等键对不上（换成同一学生另一笔的键）→ none');
SELECT t.ok(t.lmc(:U3, :K1, :S, 'grade') = 'none', 'usage_id 对不上（换成同一学生另一笔的 usage_id）→ none');
SELECT t.ok(t.lmc(:U1, :K1, :T, 'grade') = 'none', '学生对不上（同一户另一名学生）→ none');
SELECT t.ok(t.lmc(:U1, :K1, :S, 'diagnose') = 'none', '方法对不上（grade 的预留按 diagnose 核对）→ none');
SELECT t.ok(t.lmc(:U6, :K6, :S, 'grade') = 'none', '方法对不上（diagnose 的预留按 grade 核对）→ none');
SELECT t.ok(t.lmc(:U9, :K9, :S, 'teach') = 'none', '学习服务只有 grade、diagnose：teach 的预留一律 none');
SELECT t.ok(t.lmc(:U1, :K1, :S, 'GRADE') = 'none' AND t.lmc(:U1, :K1, :S, '') = 'none', '方法大小写或空串不同 → none');
SELECT t.ok(t.lmc(:U7, :K7, :S, 'diagnose') = 'none', '自练计次只认 grade（自练只有判分）');
SELECT t.ok(t.lmc(:U7, :K7, :T, 'grade') = 'none' AND t.lmc(:U8, :K8, :S, 'grade') = 'none',
            '自练计次的学生对不上 → none（两个方向）');
SELECT t.ok(t.lmc(:U7, :K8, :S, 'grade') = 'none' AND t.lmc(:U8, :K7, :T, 'grade') = 'none',
            '自练计次的幂等键对不上 → none');
SELECT t.ok(t.lmc(:U8, :K7, :S, 'grade') = 'none' AND t.lmc(gen_random_uuid(), :K7, :S, 'grade') = 'none',
            '自练计次的 usage_id 对不上 → none');
SELECT t.ok(t.lmc(NULL, :K1, :S, 'grade') = 'none' AND t.lmc(:U1, NULL, :S, 'grade') = 'none'
            AND t.lmc(:U1, :K1, NULL, 'grade') = 'none' AND t.lmc(:U1, :K1, :S, NULL) = 'none',
            '任一参数为空 → none（缺 usage_id 不放行）');
SELECT t.ok(t.lmc(:U1, upper(:K1), :S, 'grade') = 'none', '幂等键按原文比较（大小写不同 → none）');

-- ================= 四、不泄露额度与时间、只读 =================
SELECT t.ok(NOT EXISTS (
  SELECT 1 FROM (SELECT t.lmc(u, k, s, m) AS v
                   FROM unnest(ARRAY[:U1, :U2, :U3, :U4, :U5, :U6, :U7, :U8, :U9]::uuid[]) u,
                        unnest(ARRAY[:K1, :K2, :K3, :K4, :K5, :K6, :K7, :K8, :K9]::text[]) k,
                        unnest(ARRAY[:S, :T]::uuid[]) s,
                        unnest(ARRAY['grade', 'diagnose', 'teach']) m) x
   WHERE x.v IS NULL OR x.v NOT IN ('reserved', 'lapsed', 'started_in_time', 'settled', 'released', 'expired', 'counted', 'none')),
  '9 × 9 × 2 × 3 种组合的返回值都只是八个状态编码之一（没有数量、时间或空值）');
SELECT t.ok(NOT EXISTS (
  SELECT 1 FROM (SELECT t.lmc(u, k, s, m) AS v
                   FROM unnest(ARRAY[:U10, :U11, :U12, :U13, :U14, :U15, :U16]::uuid[]) u,
                        unnest(ARRAY[:K10, :K11, :K12, :K13, :K14, :K15, :K16]::text[]) k,
                        unnest(ARRAY[:S, :T]::uuid[]) s,
                        unnest(ARRAY['grade', 'diagnose']) m) x
   WHERE x.v IS NULL OR x.v NOT IN ('reserved', 'lapsed', 'started_in_time', 'settled', 'released', 'expired', 'counted', 'none')),
  '会话放行的各组合同样只返回状态编码（不返回会话或预留的时间）');
SELECT t.ok((SELECT count(*) FROM (SELECT t.lmc(u, k, s, m) AS v
                                     FROM unnest(ARRAY[:U1, :U2, :U3, :U4, :U5, :U6, :U7, :U8, :U9]::uuid[]) WITH ORDINALITY a(u, i)
                                     JOIN unnest(ARRAY[:K1, :K2, :K3, :K4, :K5, :K6, :K7, :K8, :K9]::text[]) WITH ORDINALITY b(k, j) ON true,
                                          unnest(ARRAY[:S, :T]::uuid[]) s, unnest(ARRAY['grade', 'diagnose', 'teach']) m
                                    WHERE i <> j) x WHERE x.v <> 'none') = 0,
            '交叉配对（usage_id 与幂等键来自不同记录）一律 none');
SELECT t.ok((SELECT count(*) FROM platform.usage_ledger) = :'ledger_before'::bigint
            AND (SELECT string_agg(usage_id::text || ':' || state::text, ',' ORDER BY usage_id)
                   FROM platform.usage_reservation WHERE student_id = :S) = :'states_before'
            AND (SELECT string_agg(id::text || ':' || reserved || ':' || settled, ',' ORDER BY id)
                   FROM platform.entitlement WHERE subscription_id = :SUB) = :'buckets_before',
            '核对只读：预留状态（lapsed 仍是 reserved，不替过期任务改状态）、额度桶计数、流水都没有变化');

ROLLBACK;
DROP FUNCTION IF EXISTS t.lmc(uuid, text, uuid, text);
DROP FUNCTION IF EXISTS t.lmc_as(text, uuid, text, uuid, text);
