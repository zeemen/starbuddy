-- 攻击面自动化回归（specs/household-pentest-sprint3/spec.md 第 7 节；攻击面清单 docs/security/attack-surface.md）
-- 按系统目录生成检查项：新表、新函数、新分区不用改这个文件就会被检查到。
-- 放在最后执行：前面各测试文件留下的合成数据（10–70）就是可见性矩阵的数据。
-- 需要 run.sh 传入 -v testdir=<仓库>/db/tests（快照查询的位置）与 -v expecteddir=<期望快照目录>（通常是 db/tests/expected）。

-- 被检查的 schema：除系统 schema 与测试辅助 schema t 以外的全部，含 public（托管 Postgres 经 PostgREST 暴露 public）
CREATE FUNCTION t.app_schemas() RETURNS SETOF oid LANGUAGE sql STABLE AS $$
  SELECT oid FROM pg_namespace
   WHERE nspname NOT IN ('pg_catalog', 'information_schema', 't', 'pg_toast')
     AND nspname NOT LIKE 'pg_temp%' AND nspname NOT LIKE 'pg_toast_temp%' $$;
CREATE FUNCTION t.app_roles() RETURNS SETOF text LANGUAGE sql IMMUTABLE AS $$
  SELECT unnest(ARRAY['starbuddy_bff', 'starbuddy_learning', 'starbuddy_review', 'starbuddy_scheduler', 'starbuddy_insight',
                       'starbuddy_kge', 'starbuddy_authz']) $$;

-- ================= 一、每张表都 ENABLE + FORCE RLS；子分区对应用角色没有任何权限（I1、J1） =================
SELECT t.ok(NOT EXISTS (
  SELECT 1 FROM pg_class c
   WHERE c.relnamespace IN (SELECT t.app_schemas()) AND c.relkind IN ('r', 'p') AND NOT c.relispartition
     AND NOT (c.relrowsecurity AND c.relforcerowsecurity)),
  '攻击面：每张表（含分区父表）都 ENABLE + FORCE RLS');
SELECT t.ok(NOT EXISTS (
  SELECT 1 FROM pg_class c CROSS JOIN t.app_roles() r(role)
   WHERE c.relnamespace IN (SELECT t.app_schemas()) AND c.relispartition
     AND (has_any_column_privilege(r.role, c.oid, 'SELECT') OR has_any_column_privilege(r.role, c.oid, 'INSERT')
          OR has_any_column_privilege(r.role, c.oid, 'UPDATE') OR has_table_privilege(r.role, c.oid, 'DELETE')
          OR has_table_privilege(r.role, c.oid, 'TRUNCATE'))),
  '攻击面：子分区对任何应用角色都没有权限（不能绕过父表的 RLS 直读分区）');
-- 物化视图、外部表开不了 RLS：应用角色对它们不能有任何权限
SELECT t.ok(NOT EXISTS (
  SELECT 1 FROM pg_class c CROSS JOIN t.app_roles() r(role)
   WHERE c.relnamespace IN (SELECT t.app_schemas()) AND c.relkind IN ('m', 'f')
     AND (has_any_column_privilege(r.role, c.oid, 'SELECT') OR has_any_column_privilege(r.role, c.oid, 'INSERT')
          OR has_any_column_privilege(r.role, c.oid, 'UPDATE') OR has_table_privilege(r.role, c.oid, 'DELETE'))),
  '攻击面：物化视图与外部表对任何应用角色都没有权限（它们开不了 RLS）');

-- 公共知识层不含学生数据（CLAUDE.md 规则 13；specs/kge-schema-v0.9 验收）：kge 下任何表与视图都没有指向学生、账号、家庭的列
SELECT t.ok(NOT EXISTS (
  SELECT 1 FROM pg_attribute a JOIN pg_class c ON c.oid = a.attrelid
   WHERE c.relnamespace = 'kge'::regnamespace AND c.relkind IN ('r', 'p', 'v', 'm') AND a.attnum > 0 AND NOT a.attisdropped
     AND a.attname ~ '(student|account|household|guardian|phone|learner|attempt|evidence_event)'),
  '攻击面：公共知识层（kge）没有学生、账号、家庭等个人层的键');
SELECT t.ok(NOT EXISTS (
  SELECT 1 FROM pg_constraint k JOIN pg_class c ON c.oid = k.conrelid
   WHERE c.relnamespace = 'kge'::regnamespace AND k.contype = 'f'
     AND k.confrelid NOT IN (SELECT oid FROM pg_class WHERE relnamespace = 'kge'::regnamespace
                             UNION ALL SELECT 'platform.item'::regclass UNION ALL SELECT 'platform.skill_node'::regclass)),
  '攻击面：公共知识层的外键只指向 kge 自身与 platform 的内容登记（题目、能力视图）');

-- ================= 二、SECURITY DEFINER 函数（I1） =================
SELECT t.ok(NOT EXISTS (
  SELECT 1 FROM pg_proc p
   WHERE p.pronamespace IN (SELECT t.app_schemas()) AND p.prosecdef
     AND (pg_get_userbyid(p.proowner) <> 'starbuddy_authz'
          OR NOT coalesce((SELECT bool_or(cfg ~ '^search_path=.*pg_temp$') FROM unnest(p.proconfig) cfg), false)
          OR has_function_privilege('public', p.oid, 'EXECUTE'))),
  '攻击面：每个 SECURITY DEFINER 函数属主是 authz、search_path 固定且以 pg_temp 结尾、PUBLIC 不能执行');

-- ================= 三、应用角色（I2、I5） =================
SELECT t.ok(NOT EXISTS (
  SELECT 1 FROM pg_namespace n CROSS JOIN t.app_roles() r(role)
   WHERE n.nspname NOT IN ('pg_catalog', 'information_schema', 't') AND n.nspname NOT LIKE 'pg\_%'
     AND has_schema_privilege(r.role, n.oid, 'CREATE')),
  '攻击面：应用角色在任何 schema（含 public、platform、starbuddy_ext）上都没有 CREATE');
SELECT t.ok(NOT EXISTS (
  SELECT 1 FROM pg_default_acl d, aclexplode(d.defaclacl) a
   WHERE a.grantee = 0 OR pg_get_userbyid(a.grantee) IN (SELECT t.app_roles())),
  '攻击面：没有让新建对象自动授权给应用角色或 PUBLIC 的默认授权');
SELECT t.ok(NOT EXISTS (
  SELECT 1 FROM pg_auth_members m WHERE pg_get_userbyid(m.member) IN (SELECT t.app_roles())),
  '攻击面：应用角色不是任何其他角色的成员');
SELECT t.ok(NOT EXISTS (
  SELECT 1 FROM pg_roles r
   WHERE r.rolname IN (SELECT t.app_roles())
     AND (r.rolsuper OR (r.rolbypassrls AND r.rolname <> 'starbuddy_authz') OR r.rolcreaterole OR r.rolcreatedb
          OR r.rolreplication OR r.rolcanlogin)),
  '攻击面：应用角色没有危险属性（SUPERUSER、BYPASSRLS、CREATEROLE、CREATEDB、REPLICATION、LOGIN；只有 authz 按设计带 BYPASSRLS）');
-- H2：任何非超级用户角色（含经多层中间角色）都不能同时是两个应用角色的成员
SELECT t.ok(NOT EXISTS (
  SELECT 1 FROM pg_roles r, t.app_roles() a1(role), t.app_roles() a2(role)
   WHERE NOT r.rolsuper AND a1.role < a2.role AND r.rolname NOT IN (SELECT t.app_roles())
     AND pg_has_role(r.oid, a1.role, 'MEMBER') AND pg_has_role(r.oid, a2.role, 'MEMBER')),
  '攻击面：没有角色同时继承两个应用角色（含 scheduler，经中间角色也算）');

-- ================= 四、授权与策略快照（任何授权或策略变动都要同步更新 db/tests/expected/） =================
-- 不一致时打印第一处差异；重新生成：bash db/tests/update-snapshots.sh
CREATE FUNCTION t.first_diff(actual text, expected text) RETURNS text LANGUAGE sql IMMUTABLE AS $$
  WITH a AS (SELECT x FROM unnest(string_to_array(actual, E'\n')) x),
       e AS (SELECT x FROM unnest(string_to_array(expected, E'\n')) x)
  SELECT coalesce((SELECT '多出：' || x FROM a EXCEPT SELECT '多出：' || x FROM e ORDER BY 1 LIMIT 1),
                  (SELECT '缺少：' || x FROM e EXCEPT SELECT '缺少：' || x FROM a ORDER BY 1 LIMIT 1), '无差异') $$;
\i :testdir/snapshot/privileges.sql
\i :testdir/snapshot/policies.sql
\set expected_privileges `cat :expecteddir/privileges.txt`
\set expected_policies `cat :expecteddir/policies.txt`
-- \gset 遇到 NULL 会删除变量，所以没有差异时写空串
SELECT CASE WHEN :'snap_privileges' <> :'expected_privileges'
            THEN t.first_diff(:'snap_privileges', :'expected_privileges') ELSE '' END AS privileges_diff \gset
SELECT CASE WHEN :'snap_policies' <> :'expected_policies'
            THEN t.first_diff(:'snap_policies', :'expected_policies') ELSE '' END AS policies_diff \gset
SELECT t.ok(:'snap_privileges' = :'expected_privileges',
            '攻击面：授权快照与 db/tests/expected/privileges.txt 一致' || coalesce('（' || nullif(:'privileges_diff', '') || '）', ''));
SELECT t.ok(:'snap_policies' = :'expected_policies',
            '攻击面：策略快照与 db/tests/expected/policies.txt 一致' || coalesce('（' || nullif(:'policies_diff', '') || '）', ''));

-- ================= 五、跨家庭可见性矩阵（A1、A4、B1、C1、E1、E2、E3） =================
-- 对 BFF 可读的每张表和视图（所有应用 schema）、每个账号（含未登录、不存在、已禁用、staff），断言看得到的集合 = 预期集合。
-- 做法：以 BFF 身份只取可见行的原始键（raw_expr，只用本表的列，不能再经过别的受 RLS 保护的表，否则别家的行
-- 会被映射成 NULL 而空转，安全审查阻断 1）；RESET ROLE 之后再以超级用户身份把原始键映射到学生 / 账号 / 家庭（map_expr，%s 是原始键）。
-- 预期集合由账号状态、guardian_student_link、学生本人关系算出（不手写），再与表里实际有数据的对象取交集。
-- domain：student（默认）、account、household。mode：equal（默认，看得到 = 能读的学生）、link、author、self、household、
-- claim（判断账本：学生本人看可见范围含 student 的、有效绑定家长看含 guardian 的，且只看非终态）。
CREATE TABLE t.visibility_rule (rel text PRIMARY KEY, raw_expr text NOT NULL, map_expr text NOT NULL, domain text NOT NULL,
                                mode text NOT NULL, reason text NOT NULL);
INSERT INTO t.visibility_rule VALUES
  ('platform.student_profile', 'id', '%s', 'student', 'equal', '学生键是 id'),
  ('platform.grade_dimension', 'grade_result_id', '(SELECT g.student_id FROM platform.grade_result g WHERE g.id = %s)', 'student', 'equal',
   '经 grade_result 关联到学生；映射在超级用户身份下做'),
  ('platform.guardian_report', 'student_id', '%s', 'student', 'author', '家长自述只有写它的有效绑定家长看得到，学生本人看不到（0006 greport_own）'),
  ('platform.guardian_student_link', 'student_id', '%s', 'student', 'link',
   '家长看得到自己名下的全部绑定（含已解除），学生看得到自己的绑定（0002 link_read）'),
  ('platform.account', 'id', '%s', 'account', 'self', '只能读自己的账号行，禁用的账号也能读自己（0002 account_self_read）'),
  ('insight.claim', 'student_id', '%s', 'student', 'claim',
   '判断账本按可见范围与状态过滤（0010 claim_front_read）；逐行的可见性断言在 75_claim_ledger'),
  ('platform.household', 'id', '%s', 'household', 'household',
   '有效家长读自己创建的家庭，或能读家庭内任一学生（0002 can_read_household；解除绑定的创建者仍可读是已登记风险 A4）');
-- 可以没有两户数据的关系（写明理由；其余的数据不足两户就失败）
CREATE TABLE t.visibility_thin_ok (rel text PRIMARY KEY, reason text NOT NULL);
INSERT INTO t.visibility_thin_ok VALUES
  ('platform.entitlement_balance', '余额视图按真实时钟取当前周期，测试订阅用的是推进过的计量时钟，可能没有当前周期；底表 entitlement 已逐户检查');
-- BFF 可读但不含任何家庭数据的表：逐张写明理由；新表不在这里也不在矩阵里，测试就失败，逼着人判断
CREATE TABLE t.visibility_exempt (rel text PRIMARY KEY, reason text NOT NULL);
INSERT INTO t.visibility_exempt VALUES
  ('platform.curriculum_version', '内容登记，公开'), ('platform.skill_node', '内容登记，公开'), ('platform.item', '内容登记，公开'),
  ('platform.resource_type', '计量配置，公开'), ('platform.plan_config', '额度配置，只看非草稿'),
  ('platform.plan_allowance', '额度配置，只看非草稿'), ('platform.plan_feature', '额度配置，只看非草稿'),
  ('insight.claim_template', '判断模板登记，只看已审核的，不含任何学生数据'),
  ('kge.map_version', '公共知识层，只看已发布版本，没有学生键（本文件第一节断言）'),
  ('kge.knowledge_node', '公共知识层，只看已发布版本的有效节点'), ('kge.knowledge_edge', '公共知识层，只看已发布版本的有效边'),
  ('kge.textbook_projection', '公共知识层，只看已发布版本；核对人列不授予'), ('kge.region_projection', '公共知识层，只看已发布版本');
-- BFF 可执行、但不进函数越权矩阵的 SECURITY DEFINER 函数：没有“另一户的对象”可以作为参数，逐个写明由哪里覆盖
CREATE TABLE t.function_matrix_exempt (sig text PRIMARY KEY, reason text NOT NULL);
INSERT INTO t.function_matrix_exempt VALUES
  ('platform.is_active_guardian()', '没有参数，只看当前账号自身状态'),
  ('platform.signup_guardian(text)', '参数是手机号，只给当前身份注册；20_rls_household 的注册与账号接管断言'),
  ('platform.lookup_accounts_by_phone(text)', '登录查号，只返回账号 ID；攻击面 C3（第二轮）'),
  ('platform.redeem_guardian_invite(text)', '参数是邀请码；20_rls_household 的兑换断言（攻击面 D1、D2、D4）');

CREATE FUNCTION t.readable_students(acc uuid) RETURNS uuid[] LANGUAGE sql STABLE AS $$
  SELECT coalesce(array_agg(DISTINCT s.id ORDER BY s.id), '{}')
    FROM platform.student_profile s
   WHERE EXISTS (SELECT 1 FROM platform.account a WHERE a.id = acc AND a.kind = 'student' AND a.status = 'active'
                   AND s.account_id = a.id)
      OR EXISTS (SELECT 1 FROM platform.account a JOIN platform.guardian_student_link l ON l.guardian_account_id = a.id
                  WHERE a.id = acc AND a.kind = 'guardian' AND a.status = 'active' AND l.status = 'active' AND l.student_id = s.id) $$;

-- 把一组原始键映射成排好序、去重、去 NULL 的 uuid 数组（以调用者身份执行；矩阵在 RESET ROLE 之后调用）
CREATE FUNCTION t.map_keys(raw uuid[], map_expr text) RETURNS uuid[] LANGUAGE plpgsql AS $$
DECLARE v uuid[];
BEGIN
  EXECUTE format('SELECT coalesce(array_agg(DISTINCT m ORDER BY m), ''{}'') FROM (SELECT %s AS m FROM unnest($1) r) x WHERE m IS NOT NULL',
                 format(map_expr, 'r')) USING raw INTO v;
  RETURN v;
END $$;

DO $$
DECLARE
  rel record;
  acc uuid;
  r t.visibility_rule%ROWTYPE;
  v_raw uuid[];
  v_present uuid[];
  v_expected uuid[];
  v_visible uuid[];
  v_fail text[] := '{}';
  v_unjudged text[] := '{}';
  v_thin text[] := '{}';
  v_accounts uuid[];
  v_rels integer := 0;
BEGIN
  SELECT array_agg(id ORDER BY id) || ARRAY[NULL::uuid, '00000000-0000-4000-8000-000000000000'::uuid]
    INTO v_accounts FROM platform.account;
  FOR rel IN SELECT c.oid, format('%s.%s', n.nspname, c.relname) AS qname,
                    EXISTS (SELECT 1 FROM pg_attribute a WHERE a.attrelid = c.oid AND a.attname = 'student_id' AND NOT a.attisdropped) AS has_sid
               FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
              WHERE c.relnamespace IN (SELECT t.app_schemas()) AND c.relkind IN ('r', 'p', 'v', 'm', 'f') AND NOT c.relispartition
                AND has_any_column_privilege('starbuddy_bff', c.oid, 'SELECT')
              ORDER BY 2 LOOP
    SELECT * INTO r FROM t.visibility_rule WHERE t.visibility_rule.rel = rel.qname;
    IF NOT FOUND THEN
      IF NOT rel.has_sid THEN
        IF NOT EXISTS (SELECT 1 FROM t.visibility_exempt e WHERE e.rel = rel.qname) THEN
          v_unjudged := v_unjudged || rel.qname;
        END IF;
        CONTINUE;
      END IF;
      r := ROW(rel.qname, 'student_id', '%s', 'student', 'equal', '默认');
    END IF;
    v_rels := v_rels + 1;
    EXECUTE format('SELECT coalesce(array_agg(DISTINCT k), ''{}'') FROM (SELECT (%s)::uuid AS k FROM %s) x WHERE k IS NOT NULL',
                   r.raw_expr, rel.oid::regclass) INTO v_raw;
    v_present := t.map_keys(v_raw, r.map_expr);
    -- 数据要来自至少两户，否则“看不到别人的”会空转（1-E lessons）
    IF r.domain <> 'account' AND (CASE r.domain
         WHEN 'student' THEN (SELECT count(DISTINCT s.household_id) FROM platform.student_profile s WHERE s.id = ANY (v_present))
         ELSE cardinality(v_present) END) < 2
       AND NOT EXISTS (SELECT 1 FROM t.visibility_thin_ok o WHERE o.rel = rel.qname) THEN
      v_thin := v_thin || rel.qname;
    END IF;
    FOREACH acc IN ARRAY v_accounts LOOP
      v_expected := CASE r.mode
        WHEN 'equal' THEN t.readable_students(acc)
        WHEN 'link' THEN (SELECT coalesce(array_agg(DISTINCT x), '{}') FROM (
                            SELECT unnest(t.readable_students(acc)) x WHERE EXISTS (
                              SELECT 1 FROM platform.account a WHERE a.id = acc AND a.kind = 'student')
                            UNION
                            SELECT l.student_id FROM platform.guardian_student_link l JOIN platform.account a ON a.id = l.guardian_account_id
                             WHERE a.id = acc AND a.kind = 'guardian' AND a.status = 'active') y)
        WHEN 'author' THEN (SELECT coalesce(array_agg(DISTINCT gr.student_id), '{}')
                              FROM platform.guardian_report gr
                             WHERE gr.guardian_account_id = acc AND gr.student_id = ANY (t.readable_students(acc))
                               AND EXISTS (SELECT 1 FROM platform.account a WHERE a.id = acc AND a.kind = 'guardian'))
        WHEN 'claim' THEN (SELECT coalesce(array_agg(DISTINCT c.student_id), '{}') FROM insight.claim c
                            WHERE c.status IN ('hypothesis', 'established', 'to_verify') AND c.expires_at > now()
                              AND EXISTS (SELECT 1 FROM insight.claim_template tp WHERE tp.template_key = c.template_key
                                            AND tp.version = c.template_version AND tp.status = 'approved')
                              AND c.student_id = ANY (t.readable_students(acc))
                              AND ((EXISTS (SELECT 1 FROM platform.account a JOIN platform.student_profile s ON s.account_id = a.id
                                             WHERE a.id = acc AND a.kind = 'student' AND s.id = c.student_id)
                                    AND 'student' = ANY (c.visibility))
                                   OR (EXISTS (SELECT 1 FROM platform.account a WHERE a.id = acc AND a.kind = 'guardian')
                                       AND 'guardian' = ANY (c.visibility))))
        WHEN 'self' THEN (SELECT coalesce(array_agg(a.id), '{}') FROM platform.account a WHERE a.id = acc)
        WHEN 'household' THEN (SELECT coalesce(array_agg(DISTINCT h.id), '{}') FROM platform.household h
                                WHERE EXISTS (SELECT 1 FROM platform.account a WHERE a.id = acc AND a.kind = 'guardian'
                                                AND a.status = 'active' AND h.created_by_account = a.id)
                                   OR EXISTS (SELECT 1 FROM platform.student_profile s
                                               WHERE s.household_id = h.id AND s.id = ANY (t.readable_students(acc))))
      END;
      SELECT coalesce(array_agg(x ORDER BY x), '{}') INTO v_expected FROM unnest(v_expected) x WHERE x = ANY (v_present);
      PERFORM set_config('app.account_id', coalesce(acc::text, ''), false);
      EXECUTE 'SET ROLE starbuddy_bff';
      EXECUTE format('SELECT coalesce(array_agg(DISTINCT k), ''{}'') FROM (SELECT (%s)::uuid AS k FROM %s) x WHERE k IS NOT NULL',
                     r.raw_expr, rel.oid::regclass) INTO v_raw;
      EXECUTE 'RESET ROLE';
      v_visible := t.map_keys(v_raw, r.map_expr);
      IF v_visible IS DISTINCT FROM v_expected THEN
        v_fail := v_fail || format('%s 账号 %s：看到 %s，预期 %s', rel.qname, coalesce(acc::text, '未登录'), v_visible, v_expected);
      END IF;
    END LOOP;
  END LOOP;
  PERFORM set_config('app.account_id', '', false);
  IF cardinality(v_fail) > 0 THEN RAISE NOTICE '可见性矩阵不一致：%', array_to_string(v_fail[1:5], E'\n'); END IF;
  -- 最小覆盖：筛选条件坏掉、一张表都没选上时不能“全部通过”（安全审查阻断 2）
  PERFORM t.ok(v_rels >= 19 AND cardinality(v_accounts) >= 8,
               format('攻击面：可见性矩阵覆盖了足够多的表与账号（%s 张表、%s 个账号）', v_rels, cardinality(v_accounts)));
  PERFORM t.ok(cardinality(v_unjudged) = 0, '攻击面：BFF 可读但没有学生键的表都登记了豁免理由' ||
                                            coalesce('（未登记：' || array_to_string(v_unjudged, '、') || '）', ''));
  PERFORM t.ok(cardinality(v_thin) = 0, '攻击面：可见性矩阵的每张表都有至少两户的数据（不空转）' ||
                                        coalesce('（数据不足：' || array_to_string(v_thin, '、') || '）', ''));
  PERFORM t.ok(cardinality(v_fail) = 0, format('攻击面：可见性矩阵——BFF 可读的每张表，每个账号看到的集合 = 预期集合（%s 处不一致）',
                                             cardinality(v_fail)));
END $$;

-- 身份设成非法字符串：直接报错，不返回任何数据（E2）
SELECT set_config('app.account_id', 'not-a-uuid', false);
SET ROLE starbuddy_bff;
SELECT t.throws($$SELECT count(*) FROM platform.student_profile$$, '22P02', '攻击面：身份不是合法 UUID 时报错，不返回数据');
RESET ROLE;
SELECT set_config('app.account_id', '', false);

-- ================= 六、函数越权矩阵（A2、A3、B2、G1） =================
-- 授予 BFF 的每个 SECURITY DEFINER 函数，只要第一个参数是学生、预留（usage）或家庭：
-- 用“另一户的对象”调用与用“不存在的对象”调用，结果必须完全相同（不泄露存在性）；
-- 非布尔函数必须以 insufficient_privilege 拒绝，布尔谓词必须返回 false。调用在子事务里执行，一律回滚。
CREATE FUNCTION t.dummy_arg(typ regtype) RETURNS text LANGUAGE sql IMMUTABLE AS $$
  SELECT CASE typ::text
    WHEN 'uuid' THEN quote_literal(gen_random_uuid()) || '::uuid'
    WHEN 'text' THEN quote_literal('synthetic') || '::text'
    WHEN 'numeric' THEN '1::numeric'
    WHEN 'smallint' THEN '9::smallint'
    WHEN 'integer' THEN '1'
    WHEN 'date' THEN quote_literal('2012-03-01') || '::date'
    WHEN 'interval' THEN quote_literal('1 day') || '::interval'
    WHEN 'platform.subject' THEN quote_literal('aya') || '::platform.subject'
    ELSE NULL END $$;

-- 结果含 SQLSTATE、消息、DETAIL、HINT：任何一项因“对象存在”而不同，都算泄露存在性（安全审查建议 2）
CREATE FUNCTION t.try_call(sql text) RETURNS text LANGUAGE plpgsql AS $$
DECLARE v text; v_detail text; v_hint text;
BEGIN
  BEGIN
    EXECUTE sql INTO v;
    RAISE EXCEPTION USING ERRCODE = 'P0999', MESSAGE = 'returned:' || coalesce(v, 'null');
  EXCEPTION WHEN OTHERS THEN
    GET STACKED DIAGNOSTICS v_detail = PG_EXCEPTION_DETAIL, v_hint = PG_EXCEPTION_HINT;
    RETURN CASE WHEN SQLSTATE = 'P0999' THEN SQLERRM
                ELSE SQLSTATE || ':' || SQLERRM || coalesce(' detail=' || nullif(v_detail, ''), '')
                     || coalesce(' hint=' || nullif(v_hint, ''), '') END;
  END;
END $$;

DO $$
DECLARE
  f record;
  attacker record;
  v_target uuid;
  v_args text[];
  v_other text;
  v_missing text;
  v_fail text[] := '{}';
  v_calls integer := 0;
  i integer;
BEGIN
  FOR attacker IN SELECT a.id, a.kind FROM platform.account a
                   WHERE a.status = 'active'  -- 所有有效账号，含没有任何绑定的、同号第二账号、staff（安全审查建议 2）
                   ORDER BY a.id LOOP
    FOR f IN SELECT p.oid, p.oid::regprocedure AS sig, p.proname, p.proargnames[1] AS first_arg, p.proargtypes,
                    p.prorettype = 'boolean'::regtype AS is_bool
               FROM pg_proc p
              WHERE p.pronamespace = 'platform'::regnamespace AND p.prosecdef AND p.pronargs >= 1
                AND has_function_privilege('starbuddy_bff', p.oid, 'EXECUTE')
                AND p.proargnames[1] IN ('p_student', 'p_usage', 'p_household')
              ORDER BY p.proname LOOP
      v_target := CASE f.first_arg
        WHEN 'p_student' THEN (SELECT s.id FROM platform.student_profile s
                                WHERE NOT s.id = ANY (t.readable_students(attacker.id)) ORDER BY s.id LIMIT 1)
        WHEN 'p_usage' THEN (SELECT r.usage_id FROM platform.usage_reservation r
                              WHERE NOT r.student_id = ANY (t.readable_students(attacker.id)) ORDER BY r.usage_id LIMIT 1)
        WHEN 'p_household' THEN (SELECT h.id FROM platform.household h
                                  WHERE h.created_by_account <> attacker.id
                                    AND NOT EXISTS (SELECT 1 FROM platform.student_profile s
                                                     WHERE s.household_id = h.id AND s.id = ANY (t.readable_students(attacker.id)))
                                  ORDER BY h.id LIMIT 1)
      END;
      CONTINUE WHEN v_target IS NULL;
      v_args := '{}';
      FOR i IN 1 .. array_length(f.proargtypes::oid[], 1) - 1 LOOP
        v_args := v_args || t.dummy_arg((f.proargtypes::oid[])[i]::regtype);
      END LOOP;
      IF array_position(v_args, NULL) IS NOT NULL THEN
        v_fail := v_fail || format('%s：有无法构造的参数类型', f.sig);
        CONTINUE;
      END IF;
      PERFORM set_config('app.account_id', attacker.id::text, false);
      EXECUTE 'SET ROLE starbuddy_bff';
      v_other := t.try_call(format('SELECT %s(%s)::text', f.oid::regproc,
                                   array_to_string(ARRAY[quote_literal(v_target) || '::uuid'] || v_args, ', ')));
      v_missing := t.try_call(format('SELECT %s(%s)::text', f.oid::regproc,
                                     array_to_string(ARRAY[quote_literal(gen_random_uuid()) || '::uuid'] || v_args, ', ')));
      EXECUTE 'RESET ROLE';
      v_calls := v_calls + 1;
      IF v_other IS DISTINCT FROM v_missing THEN
        v_fail := v_fail || format('%s 以 %s 调用：另一户 → %s；不存在 → %s', f.sig, attacker.id, v_other, v_missing);
      ELSIF f.is_bool AND v_other <> 'returned:false' THEN
        v_fail := v_fail || format('%s 以 %s 调用另一户：%s（应为 false）', f.sig, attacker.id, v_other);
      ELSIF NOT f.is_bool AND v_other NOT LIKE '42501:%' THEN
        v_fail := v_fail || format('%s 以 %s 调用另一户：%s（应为 42501）', f.sig, attacker.id, v_other);
      END IF;
    END LOOP;
  END LOOP;
  PERFORM set_config('app.account_id', '', false);
  IF cardinality(v_fail) > 0 THEN RAISE NOTICE '函数越权矩阵：%', array_to_string(v_fail[1:5], E'\n'); END IF;
  PERFORM t.ok(v_calls >= 20, format('攻击面：函数越权矩阵覆盖了足够多的（攻击者 × 函数）组合（%s 组）', v_calls));
  -- 不能靠参数名静默漏掉函数：BFF 可执行的每个 SECURITY DEFINER 函数，要么完整进矩阵
  -- （第一个参数是学生、使用或家庭，而且没有别的 uuid 参数），要么登记在 t.function_matrix_exempt 里写明理由
  SELECT coalesce(string_agg(p.oid::regprocedure::text, '、' ORDER BY p.oid::regprocedure::text), '') INTO v_missing
    FROM pg_proc p
   WHERE p.pronamespace IN (SELECT t.app_schemas()) AND p.prosecdef
     AND has_function_privilege('starbuddy_bff', p.oid, 'EXECUTE')
     AND NOT (p.pronargs >= 1 AND p.proargnames[1] IN ('p_student', 'p_usage', 'p_household')
              AND p.proargtypes[0] = 'uuid'::regtype
              AND (SELECT count(*) FROM unnest(p.proargtypes::oid[]) u WHERE u = 'uuid'::regtype) = 1)
     AND NOT EXISTS (SELECT 1 FROM t.function_matrix_exempt e WHERE e.sig = p.oid::regprocedure::text);
  PERFORM t.ok(v_missing = '', format('攻击面：BFF 可执行的 SECURITY DEFINER 函数要么在函数越权矩阵里，要么登记了豁免理由（未登记：%s）',
                                      v_missing));
  PERFORM t.ok(cardinality(v_fail) = 0, format('攻击面：函数越权矩阵——对另一户的对象与不存在的对象结果相同且被拒绝（%s 处不一致）',
                                             cardinality(v_fail)));
END $$;
