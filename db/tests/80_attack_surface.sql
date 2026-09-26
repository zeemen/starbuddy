-- 攻击面自动化回归（specs/household-pentest-sprint3/spec.md 第 7 节；攻击面清单 docs/security/attack-surface.md）
-- 按系统目录生成检查项：新表、新函数、新分区不用改这个文件就会被检查到。
-- 放在最后执行：前面各测试文件留下的合成数据（10–70）就是可见性矩阵的数据。
-- 需要 run.sh 传入 -v testdir=<仓库>/db/tests（快照文件的位置）。

-- 被检查的 schema：除系统 schema 与测试辅助 schema t 以外的全部
CREATE FUNCTION t.app_schemas() RETURNS SETOF oid LANGUAGE sql STABLE AS $$
  SELECT oid FROM pg_namespace
   WHERE nspname NOT IN ('pg_catalog', 'information_schema', 't', 'pg_toast', 'public')
     AND nspname NOT LIKE 'pg_temp%' AND nspname NOT LIKE 'pg_toast_temp%' $$;
CREATE FUNCTION t.app_roles() RETURNS SETOF text LANGUAGE sql IMMUTABLE AS $$
  SELECT unnest(ARRAY['starbuddy_bff', 'starbuddy_learning', 'starbuddy_review', 'starbuddy_scheduler', 'starbuddy_authz']) $$;

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

-- ================= 四、授权与策略快照（任何授权或策略变动都要同步更新 db/tests/expected/） =================
-- 不一致时打印第一处差异；重新生成：bash db/tests/update-snapshots.sh
CREATE FUNCTION t.first_diff(actual text, expected text) RETURNS text LANGUAGE sql IMMUTABLE AS $$
  WITH a AS (SELECT x FROM unnest(string_to_array(actual, E'\n')) x),
       e AS (SELECT x FROM unnest(string_to_array(expected, E'\n')) x)
  SELECT coalesce((SELECT '多出：' || x FROM a EXCEPT SELECT '多出：' || x FROM e ORDER BY 1 LIMIT 1),
                  (SELECT '缺少：' || x FROM e EXCEPT SELECT '缺少：' || x FROM a ORDER BY 1 LIMIT 1), '无差异') $$;
\i :testdir/snapshot/privileges.sql
\i :testdir/snapshot/policies.sql
\set expected_privileges `cat :testdir/expected/privileges.txt`
\set expected_policies `cat :testdir/expected/policies.txt`
-- \gset 遇到 NULL 会删除变量，所以没有差异时写空串
SELECT CASE WHEN :'snap_privileges' <> :'expected_privileges'
            THEN t.first_diff(:'snap_privileges', :'expected_privileges') ELSE '' END AS privileges_diff \gset
SELECT CASE WHEN :'snap_policies' <> :'expected_policies'
            THEN t.first_diff(:'snap_policies', :'expected_policies') ELSE '' END AS policies_diff \gset
SELECT t.ok(:'snap_privileges' = :'expected_privileges',
            '攻击面：授权快照与 db/tests/expected/privileges.txt 一致' || coalesce('（' || nullif(:'privileges_diff', '') || '）', ''));
SELECT t.ok(:'snap_policies' = :'expected_policies',
            '攻击面：策略快照与 db/tests/expected/policies.txt 一致' || coalesce('（' || nullif(:'policies_diff', '') || '）', ''));

-- ================= 五、跨家庭可见性矩阵（A1、B1、C1、E1、E2） =================
-- 对 BFF 可读的每张表和视图、夹具里的每个账号（含未登录、不存在、已禁用），断言看得到的学生集合 = 预期集合。
-- 预期集合由账号状态、guardian_student_link 与学生本人关系算出（不手写），再与表里实际有数据的学生取交集。
-- 默认规则 equal：看得到 = 能读的学生（学生本人或有效绑定的有效家长）。少数表规则不同，登记在下面：
CREATE TABLE t.visibility_rule (relname text PRIMARY KEY, key_expr text NOT NULL, mode text NOT NULL, reason text NOT NULL);
INSERT INTO t.visibility_rule VALUES
  ('student_profile', 'id', 'equal', '学生键是 id'),
  ('grade_dimension', '(SELECT g.student_id FROM platform.grade_result g WHERE g.id = grade_result_id)', 'equal',
   '经 grade_result 关联到学生'),
  ('guardian_report', 'student_id', 'author', '家长自述只有写它的有效绑定家长看得到，学生本人看不到（0006 greport_own）'),
  ('guardian_student_link', 'student_id', 'link', '家长看得到自己名下的全部绑定（含已解除），学生看得到自己的绑定（0002 link_read）');
-- 可以没有两户数据的关系（写明理由；其余的数据不足两户就失败）
CREATE TABLE t.visibility_thin_ok (relname text PRIMARY KEY, reason text NOT NULL);
INSERT INTO t.visibility_thin_ok VALUES
  ('entitlement_balance', '余额视图按真实时钟取当前周期，测试订阅用的是推进过的计量时钟，可能没有当前周期；底表 entitlement 已逐户检查');
-- BFF 可读但没有学生键的表：逐张写明理由；新表不在这里也不在矩阵里，测试就失败，逼着人判断
CREATE TABLE t.visibility_exempt (relname text PRIMARY KEY, reason text NOT NULL);
INSERT INTO t.visibility_exempt VALUES
  ('account', '只能读自己的账号行（account_self_read），20_rls_household 已断言'),
  ('household', '按 can_read_household，20_rls_household 已断言；被解除绑定的创建者仍可读是已登记风险 A4'),
  ('curriculum_version', '内容登记，公开'), ('skill_node', '内容登记，公开'), ('item', '内容登记，公开'),
  ('resource_type', '计量配置，公开'), ('plan_config', '额度配置，只看非草稿'), ('plan_allowance', '额度配置，只看非草稿'),
  ('plan_feature', '额度配置，只看非草稿');

CREATE FUNCTION t.readable_students(acc uuid) RETURNS uuid[] LANGUAGE sql STABLE AS $$
  SELECT coalesce(array_agg(DISTINCT s.id ORDER BY s.id), '{}')
    FROM platform.student_profile s
   WHERE EXISTS (SELECT 1 FROM platform.account a WHERE a.id = acc AND a.kind = 'student' AND a.status = 'active'
                   AND s.account_id = a.id)
      OR EXISTS (SELECT 1 FROM platform.account a JOIN platform.guardian_student_link l ON l.guardian_account_id = a.id
                  WHERE a.id = acc AND a.kind = 'guardian' AND a.status = 'active' AND l.status = 'active' AND l.student_id = s.id) $$;

DO $$
DECLARE
  rel record;
  acc uuid;
  v_key text;
  v_mode text;
  v_present uuid[];
  v_expected uuid[];
  v_visible uuid[];
  v_fail text[] := '{}';
  v_unjudged text[] := '{}';
  v_thin text[] := '{}';
  v_accounts uuid[];
BEGIN
  SELECT array_agg(id ORDER BY id) || ARRAY[NULL::uuid, '00000000-0000-4000-8000-000000000000'::uuid]
    INTO v_accounts FROM platform.account;
  FOR rel IN SELECT c.oid, c.relname,
                    EXISTS (SELECT 1 FROM pg_attribute a WHERE a.attrelid = c.oid AND a.attname = 'student_id' AND NOT a.attisdropped) AS has_sid
               FROM pg_class c
              WHERE c.relnamespace = 'platform'::regnamespace AND c.relkind IN ('r', 'p', 'v') AND NOT c.relispartition
                AND has_any_column_privilege('starbuddy_bff', c.oid, 'SELECT')
              ORDER BY c.relname LOOP
    SELECT key_expr, mode INTO v_key, v_mode FROM t.visibility_rule WHERE relname = rel.relname;
    IF NOT FOUND THEN
      IF rel.has_sid THEN
        v_key := 'student_id'; v_mode := 'equal';
      ELSE
        IF NOT EXISTS (SELECT 1 FROM t.visibility_exempt e WHERE e.relname = rel.relname) THEN
          v_unjudged := v_unjudged || rel.relname::text;
        END IF;
        CONTINUE;
      END IF;
    END IF;
    EXECUTE format('SELECT coalesce(array_agg(DISTINCT k ORDER BY k), ''{}'') FROM (SELECT %s AS k FROM platform.%I) x WHERE k IS NOT NULL',
                   v_key, rel.relname) INTO v_present;
    -- 数据要来自至少两户，否则“看不到别人的”会空转（1-E lessons）
    IF (SELECT count(DISTINCT s.household_id) FROM platform.student_profile s WHERE s.id = ANY (v_present)) < 2
       AND NOT EXISTS (SELECT 1 FROM t.visibility_thin_ok o WHERE o.relname = rel.relname) THEN
      v_thin := v_thin || rel.relname::text;
    END IF;
    FOREACH acc IN ARRAY v_accounts LOOP
      v_expected := CASE v_mode
        WHEN 'equal' THEN t.readable_students(acc)
        WHEN 'link' THEN (SELECT coalesce(array_agg(DISTINCT x ORDER BY x), '{}') FROM (
                            SELECT unnest(t.readable_students(acc)) x WHERE EXISTS (
                              SELECT 1 FROM platform.account a WHERE a.id = acc AND a.kind = 'student')
                            UNION
                            SELECT l.student_id FROM platform.guardian_student_link l JOIN platform.account a ON a.id = l.guardian_account_id
                             WHERE a.id = acc AND a.kind = 'guardian' AND a.status = 'active') y)
        WHEN 'author' THEN (SELECT coalesce(array_agg(DISTINCT r.student_id ORDER BY r.student_id), '{}')
                              FROM platform.guardian_report r
                             WHERE r.guardian_account_id = acc AND r.student_id = ANY (t.readable_students(acc))
                               AND EXISTS (SELECT 1 FROM platform.account a WHERE a.id = acc AND a.kind = 'guardian'))
      END;
      SELECT coalesce(array_agg(x ORDER BY x), '{}') INTO v_expected FROM unnest(v_expected) x WHERE x = ANY (v_present);
      PERFORM set_config('app.account_id', coalesce(acc::text, ''), false);
      EXECUTE 'SET ROLE starbuddy_bff';
      EXECUTE format('SELECT coalesce(array_agg(DISTINCT k ORDER BY k), ''{}'') FROM (SELECT %s AS k FROM platform.%I) x WHERE k IS NOT NULL',
                     v_key, rel.relname) INTO v_visible;
      EXECUTE 'RESET ROLE';
      IF v_visible IS DISTINCT FROM v_expected THEN
        v_fail := v_fail || format('%s 账号 %s：看到 %s，预期 %s', rel.relname, coalesce(acc::text, '未登录'), v_visible, v_expected);
      END IF;
    END LOOP;
  END LOOP;
  PERFORM set_config('app.account_id', '', false);
  IF cardinality(v_fail) > 0 THEN RAISE NOTICE '可见性矩阵不一致：%', array_to_string(v_fail[1:5], E'\n'); END IF;
  PERFORM t.ok(cardinality(v_fail) = 0, format('攻击面：可见性矩阵——BFF 可读的每张表，每个账号看到的学生集合 = 预期集合（%s 处不一致）',
                                             cardinality(v_fail)));
  PERFORM t.ok(cardinality(v_unjudged) = 0, '攻击面：BFF 可读但没有学生键的表都登记了豁免理由' ||
                                            coalesce('（未登记：' || array_to_string(v_unjudged, '、') || '）', ''));
  PERFORM t.ok(cardinality(v_thin) = 0, '攻击面：可见性矩阵的每张表都有至少两户的数据（不空转）' ||
                                        coalesce('（数据不足：' || array_to_string(v_thin, '、') || '）', ''));
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

CREATE FUNCTION t.try_call(sql text) RETURNS text LANGUAGE plpgsql AS $$
DECLARE v text;
BEGIN
  BEGIN
    EXECUTE sql INTO v;
    RAISE EXCEPTION USING ERRCODE = 'P0999', MESSAGE = 'returned:' || coalesce(v, 'null');
  EXCEPTION WHEN OTHERS THEN
    RETURN CASE WHEN SQLSTATE = 'P0999' THEN SQLERRM ELSE SQLSTATE || ':' || SQLERRM END;
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
                   WHERE a.status = 'active' AND a.kind IN ('guardian', 'student') AND cardinality(t.readable_students(a.id)) > 0
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
  PERFORM t.ok(cardinality(v_fail) = 0, format('攻击面：函数越权矩阵——对另一户的对象与不存在的对象结果相同且被拒绝（%s 处不一致）',
                                             cardinality(v_fail)));
END $$;
