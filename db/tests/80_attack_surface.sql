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
                       'starbuddy_kge', 'starbuddy_kge_read', 'starbuddy_report', 'starbuddy_authz', 'starbuddy_eraser']) $$;

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
   WHERE c.relnamespace IN ('kge'::regnamespace, 'policy'::regnamespace) AND c.relkind IN ('r', 'p', 'v', 'm') AND a.attnum > 0 AND NOT a.attisdropped
     AND a.attname ~ '(student|account|household|guardian|phone|learner|attempt|evidence_event|user|profile|child|parent_account|session|email|openid|device|wechat)'),
  '攻击面：公共知识层与考情库（kge、policy）没有学生、账号、家庭等个人层的键');
SELECT t.ok(NOT EXISTS (
  SELECT 1 FROM pg_constraint k JOIN pg_class c ON c.oid = k.conrelid
   WHERE c.relnamespace IN ('kge'::regnamespace, 'policy'::regnamespace) AND k.contype = 'f'
     AND k.confrelid NOT IN (SELECT oid FROM pg_class WHERE relnamespace = c.relnamespace
                             UNION ALL SELECT 'platform.item'::regclass UNION ALL SELECT 'platform.skill_node'::regclass)),
  '攻击面：公共知识层与考情库的外键只指向本 schema 与 platform 的内容登记（题目、能力视图）');
-- 没有外键的 uuid 列同样能存学生 ID：kge 的每个 uuid / uuid[] 列要么是本表主键 id，要么被指向 kge 自身的外键覆盖（安全审查建议 5）
SELECT t.ok(NOT EXISTS (
  SELECT 1 FROM pg_attribute a JOIN pg_class c ON c.oid = a.attrelid
   WHERE c.relnamespace IN ('kge'::regnamespace, 'policy'::regnamespace) AND c.relkind IN ('r', 'p') AND a.attnum > 0 AND NOT a.attisdropped
     AND a.atttypid IN ('uuid'::regtype, 'uuid[]'::regtype)
     AND NOT (a.attname = 'id' AND EXISTS (SELECT 1 FROM pg_constraint k WHERE k.conrelid = c.oid AND k.contype = 'p' AND k.conkey = ARRAY[a.attnum]))
     AND NOT EXISTS (SELECT 1 FROM pg_constraint k WHERE k.conrelid = c.oid AND k.contype = 'f' AND a.attnum = ANY (k.conkey)
                       AND k.confrelid IN (SELECT oid FROM pg_class WHERE relnamespace = c.relnamespace))),
  '攻击面：公共知识层与考情库的 uuid 列只能是本表主键或指向本 schema 的外键（不能挂学生 ID）');
-- 金丝雀：夹具里每个学生、账号的 ID 都不能出现在 kge 任何文本、数组或 jsonb 列里
DO $$
DECLARE col record; v_hits text[] := '{}'; v_n bigint;
BEGIN
  FOR col IN SELECT c.oid::regclass AS rel, a.attname
               FROM pg_attribute a JOIN pg_class c ON c.oid = a.attrelid
              WHERE c.relnamespace IN ('kge'::regnamespace, 'policy'::regnamespace) AND c.relkind = 'r' AND a.attnum > 0 AND NOT a.attisdropped
                AND a.atttypid NOT IN ('uuid'::regtype, 'uuid[]'::regtype, 'bytea'::regtype) LOOP
    EXECUTE format('SELECT count(*) FROM %s x WHERE EXISTS (SELECT 1 FROM (SELECT id::text AS k FROM platform.student_profile
                      UNION ALL SELECT id::text FROM platform.account) ids WHERE position(ids.k IN x.%I::text) > 0)', col.rel, col.attname)
      INTO v_n;
    IF v_n > 0 THEN v_hits := v_hits || format('%s.%s', col.rel, col.attname); END IF;
  END LOOP;
  PERFORM t.ok(cardinality(v_hits) = 0, '攻击面：公共知识层与考情库的文本与 jsonb 列里没有任何学生或账号 ID（金丝雀）'
                                       || coalesce('（命中：' || array_to_string(v_hits, '、') || '）', ''));
END $$;

-- ================= 二、SECURITY DEFINER 函数（I1） =================
SELECT t.ok(NOT EXISTS (
  SELECT 1 FROM pg_proc p
   WHERE p.pronamespace IN (SELECT t.app_schemas()) AND p.prosecdef
     AND (NOT (pg_get_userbyid(p.proowner) = 'starbuddy_authz'
               OR (pg_get_userbyid(p.proowner) = 'starbuddy_eraser' AND p.oid = 'platform.execute_due_erasures(timestamptz)'::regprocedure))
          OR NOT coalesce((SELECT bool_or(cfg ~ '^search_path=.*pg_temp$') FROM unnest(p.proconfig) cfg), false)
          OR has_function_privilege('public', p.oid, 'EXECUTE'))),
  '攻击面：每个 SECURITY DEFINER 函数属主是 authz（删除执行函数属主是 eraser）、search_path 固定且以 pg_temp 结尾、PUBLIC 不能执行');
SELECT t.ok((SELECT count(*) FROM pg_proc p WHERE p.proowner = 'starbuddy_eraser'::regrole) = 1
            AND NOT EXISTS (SELECT 1 FROM pg_class c WHERE c.relowner = 'starbuddy_eraser'::regrole),
            '攻击面：删除执行身份 eraser 只拥有删除执行函数一个对象');
-- 只有删除执行函数能删个人层清单里的表：其他函数体里不能出现对清单表的 DELETE，也不能有拼表名的动态 DELETE（SIE-03 安全审查）
SELECT t.ok(NOT EXISTS (
  SELECT 1 FROM pg_proc p, platform.personal_table_registry g
   WHERE p.pronamespace IN (SELECT t.app_schemas()) AND p.oid <> 'platform.execute_due_erasures(timestamptz)'::regprocedure
     AND g.strategy = 'delete'
     AND (p.prosrc ~* ('delete\s+from\s+(only\s+)?"?' || g.nspname || '"?\s*\.\s*"?' || g.relname || '"?(\s|;|$)')
          OR p.prosrc ~* 'delete\s+from\s+%[is]')),
  '攻击面：除删除执行函数外，没有函数删除个人层清单里的表');

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
     AND (r.rolsuper OR (r.rolbypassrls AND r.rolname NOT IN ('starbuddy_authz', 'starbuddy_eraser')) OR r.rolcreaterole OR r.rolcreatedb
          OR r.rolreplication OR r.rolcanlogin)),
  '攻击面：应用角色没有危险属性（SUPERUSER、BYPASSRLS、CREATEROLE、CREATEDB、REPLICATION、LOGIN；只有 authz 与 eraser 按设计带 BYPASSRLS）');
SELECT t.ok(NOT EXISTS (SELECT 1 FROM pg_auth_members WHERE roleid = 'starbuddy_eraser'::regrole),
  '攻击面：starbuddy_eraser 没有任何成员（删除守卫的 current_user 判断依赖这一点）');
SELECT t.ok(NOT EXISTS (SELECT 1 FROM pg_auth_members WHERE roleid = 'starbuddy_authz'::regrole),
  '攻击面：starbuddy_authz 没有任何成员（发布函数的 current_user 信任判断依赖这一点）');
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
-- claim（判断账本：学生本人看可见范围含 student 的、有效绑定家长看含 guardian 的，且只看非终态）、
-- student_self（只有学生本人看得到，家长看不到）、raiser（只看自己提出的异议）、guardian_link（只有有效绑定家长看得到，学生本人看不到）。
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
  ('platform.diagnosis_session', 'student_id', '%s', 'student', 'student_self',
   '诊断会话只给学生本人看进度，家长看不到（0027 diagnosis_session_self）'),
  ('platform.diagnosis_step', 'session_id', '(SELECT s.student_id FROM platform.diagnosis_session s WHERE s.id = %s)', 'student', 'student_self',
   '经 diagnosis_session 关联到学生；只给学生本人（0027 diagnosis_step_self）；映射在超级用户身份下做'),
  ('insight.dispute', 'student_id', '%s', 'student', 'raiser',
   '判断异议只给提出人自己看（学生本人提的只给学生，家长提的只给该家长；0032 dispute_raiser_read）。预期集合与策略同构（按 raised_by 推），'
   '这里只防跨户越权；“看不到家长替自己提的”等逐行断言在 79m_dispute'),
  ('platform.erasure_request', 'student_id', '%s', 'student', 'guardian_link',
   '删除请求与回执只给有效绑定家长看，学生本人看不到（0035 erasure_request_guardian_read）；逐行断言在 79p_erasure'),
  ('insight.weekly_report', 'student_id', '%s', 'student', 'equal',
   '周报按受众读：学生本人读自己的学生版、有效绑定家长读家长版（0036 weekly_report_front_read）；两个受众的周报都有，所以按学生看与 equal 同构，'
   '这里只防跨户越权；“学生看不到家长版”“家长看不到学生版”“不出的周报不可见”等逐行断言在 79r_weekly_report'),
  ('platform.household', 'id', '%s', 'household', 'household',
   '有效家长读自己创建的家庭，或能读家庭内任一学生（0002 can_read_household；解除绑定的创建者仍可读是已登记风险 A4）');
-- 视图自己按调用者身份过滤（属主视图，超级用户读出来是空的）：“表里实际有数据的对象”改由这里的 SQL 按底表算（返回 uuid[]）。
-- 其余关系仍按超级用户直读。预期集合与可见集合的算法不变
CREATE TABLE t.visibility_present_override (rel text PRIMARY KEY, present_sql text NOT NULL, reason text NOT NULL);
INSERT INTO t.visibility_present_override VALUES
  ('platform.profile_page_statement_v',
   $q$SELECT coalesce(array_agg(DISTINCT p.student_id ORDER BY p.student_id), '{}') FROM platform.profile_page p,
        jsonb_array_elements(p.sections) s, jsonb_array_elements(s -> 'statements') i, insight.claim c
       WHERE c.id = (i ->> 'claim_id')::uuid AND c.student_id = p.student_id AND c.status IN ('hypothesis', 'established')
         AND c.expires_at > now()$q$,
   '档案页陈述视图（0037）属主是 authz，按 can_read_student 与判断前端可见范围过滤，超级用户直读为空；按底表算有陈述的学生。'
   '矩阵按 equal 比较，要求夹具里的陈述判断对学生与家长都可见；按可见范围逐行的断言在 79s_mastery_step2');
-- 按行比较（安全审查建议 3）：除了“看得到哪些学生”，还要比“看得到哪些行”。预期的行 = 映射到预期学生（账号、家庭）的行，
-- 再按这里登记的行级条件过滤（$2 是当前账号，x 是本表的行）；没有登记的表，行级条件就是“学生可读”本身。
-- 行键：表用主键，视图用整行文本。present_override 里的视图（超级用户直读为空）只按学生集合比较
CREATE TABLE t.visibility_row_filter (rel text PRIMARY KEY, filter_sql text NOT NULL, reason text NOT NULL);
INSERT INTO t.visibility_row_filter VALUES
  ('insight.claim',
   $q$x.status IN ('hypothesis', 'established', 'to_verify') AND x.expires_at > now()
      AND EXISTS (SELECT 1 FROM insight.claim_template tp WHERE tp.template_key = x.template_key AND tp.version = x.template_version
                    AND tp.status = 'approved')
      AND ((EXISTS (SELECT 1 FROM platform.account a JOIN platform.student_profile s ON s.account_id = a.id
                     WHERE a.id = $2 AND a.kind = 'student' AND s.id = x.student_id) AND 'student' = ANY (x.visibility))
           OR (EXISTS (SELECT 1 FROM platform.account a WHERE a.id = $2 AND a.kind = 'guardian') AND 'guardian' = ANY (x.visibility)))$q$,
   '逐条判断按状态、有效期、模板已审核与可见范围（学生本人看 student、家长看 guardian）'),
  ('insight.weekly_report',
   $q$x.status = 'generated'
      AND ((x.audience = 'student' AND EXISTS (SELECT 1 FROM platform.account a JOIN platform.student_profile s ON s.account_id = a.id
                                                WHERE a.id = $2 AND a.kind = 'student' AND s.id = x.student_id))
           OR (x.audience = 'guardian' AND EXISTS (SELECT 1 FROM platform.account a WHERE a.id = $2 AND a.kind = 'guardian')))$q$,
   '学生本人只看学生版、家长只看家长版，都只看已出的'),
  ('insight.dispute', 'x.raised_by = $2', '只看自己提出的异议'),
  ('platform.guardian_report', 'x.guardian_account_id = $2', '只看自己写的家长自述'),
  ('platform.guardian_student_link',
   $q$x.guardian_account_id = $2
      OR EXISTS (SELECT 1 FROM platform.account a JOIN platform.student_profile s ON s.account_id = a.id
                  WHERE a.id = $2 AND a.kind = 'student' AND s.id = x.student_id)$q$,
   '家长只看自己名下的绑定（看不到同一学生的其他家长的绑定行），学生看自己的全部绑定');
-- 可以没有两户数据的关系（写明理由；其余的数据不足两户就失败）。
-- 余额视图原来登记在这里（按真实时钟取当前周期，而计量测试推进过时钟）；现在下面先把计量时钟固定到真实时钟并补足两户当前周期，取消豁免
CREATE TABLE t.visibility_thin_ok (rel text PRIMARY KEY, reason text NOT NULL);

-- 固定计量时钟（安全审查建议 3）：余额视图用 now() 取当前周期，70 号测试把 meter_now() 换成可推进的 t.clock。
-- 有 t.clock 时（db-test）把它固定到本事务的 now()，再给“余额视图里不足两户”的情况补附加权益订阅（addon 不占唯一有效订阅），
-- 周期按真实时钟覆盖 now()，经 meter_grant_period 发放（与正式路径一致）。渗透测试夹具本来就按 now() 开订阅，不会补
DO $$
DECLARE h record; v_sub uuid; v_cfg record;
BEGIN
  IF to_regclass('t.clock') IS NOT NULL THEN
    UPDATE t.clock SET now = now();
  END IF;
  SELECT a.config_version, a.tier INTO v_cfg FROM platform.plan_allowance a JOIN platform.plan_config c ON c.version = a.config_version
   WHERE c.status = 'published' AND a.quantity > 0 ORDER BY c.published_at, a.config_version, a.tier LIMIT 1;
  FOR h IN SELECT DISTINCT ON (s.household_id) s.household_id, s.id AS student_id
             FROM platform.student_profile s
            WHERE NOT EXISTS (SELECT 1 FROM platform.entitlement_balance b JOIN platform.student_profile s2 ON s2.id = b.student_id
                               WHERE s2.household_id = s.household_id)
              AND s.erased_at IS NULL
            ORDER BY s.household_id, s.id LOOP
    EXIT WHEN (SELECT count(DISTINCT s.household_id) FROM platform.entitlement_balance b
                 JOIN platform.student_profile s ON s.id = b.student_id) >= 2;
    INSERT INTO platform.subscription (household_id, student_id, tier, config_version, source, period_start, period_end)
    VALUES (h.household_id, h.student_id, v_cfg.tier, v_cfg.config_version, 'addon', now() - interval '1 day', now() + interval '29 days')
    RETURNING id INTO v_sub;
    PERFORM platform.meter_grant_period(v_sub);
  END LOOP;
END $$;
-- BFF 可读但不含任何家庭数据的表：逐张写明理由；新表不在这里也不在矩阵里，测试就失败，逼着人判断
CREATE TABLE t.visibility_exempt (rel text PRIMARY KEY, reason text NOT NULL);
INSERT INTO t.visibility_exempt VALUES
  ('platform.curriculum_version', '内容登记，公开'), ('platform.skill_node', '内容登记，公开'), ('platform.item', '内容登记，公开'),
  ('platform.resource_type', '计量配置，公开'), ('platform.plan_config', '额度配置，只看非草稿'),
  ('platform.plan_allowance', '额度配置，只看非草稿'), ('platform.plan_feature', '额度配置，只看非草稿'),
  ('platform.subject_catalog', '学科目录（学科码、上线状态、年级），公开信息'),
  ('insight.claim_template', '判断模板登记，只看已审核的，不含任何学生数据'),
  ('kge.map_version', '公共知识层，只看已发布版本，没有学生键（本文件第一节断言）'),
  ('kge.knowledge_node', '公共知识层，只看已发布版本的有效节点'), ('kge.knowledge_edge', '公共知识层，只看已发布版本的有效边'),
  ('kge.textbook_projection', '公共知识层，只看已发布版本；核对人列不授予'), ('kge.region_projection', '公共知识层，只看已发布版本'),
  ('policy.source_document', '考情库，只看已发布；复核人列不授予'), ('policy.exam_scheme', '考情库，只看已发布方案；复核人列不授予'),
  ('policy.subject_config', '考情库，只看已发布方案的内容'), ('policy.transition_rule', '考情库，只看已发布方案的内容'),
  ('policy.admission_rule', '考情库，只看已发布方案的内容'), ('policy.school', '招录库，高中学校公开信息'),
  ('policy.admission_line', '招录库，只看已发布行；复核人列不授予');
-- BFF 可执行、但不进函数越权矩阵的 SECURITY DEFINER 函数：没有“另一户的对象”可以作为参数，逐个写明由哪里覆盖
CREATE TABLE t.function_matrix_exempt (sig text PRIMARY KEY, reason text NOT NULL);
INSERT INTO t.function_matrix_exempt VALUES
  ('platform.is_active_guardian()', '没有参数，只看当前账号自身状态'),
  ('platform.redeem_guardian_invite(text)', '参数是邀请码；20_rls_household 的兑换断言（攻击面 D1、D2、D4）'),
  ('platform.auth_otp_issue(text,bytea,integer,integer)', '家庭登录（0029）：参数是手机号与验证码 MAC，不涉及学生；79k_auth_session 的“申请结果相同”'),
  ('platform.auth_otp_login(text,bytea,integer,integer,text)',
   '家庭登录（0029）：只按家长账号匹配手机号，同号多账号拒绝；79k_auth_session 的登录、换号、同号多账号断言'),
  ('platform.auth_session_resolve(text)', '家庭会话解析（0029）：参数是令牌，只返回令牌所属账号；79k_auth_session 的会话断言'),
  ('platform.auth_session_revoke(text)', '家庭会话登出（0029）：参数是令牌；79k_auth_session 的登出断言'),
  ('platform.auth_session_revoke_all(text)', '退出所有设备（0029）：只作废令牌所属账号的会话；79k_auth_session 的“别的账号不受影响”'),
  ('platform.auth_rate_hit(text,bytea,integer,integer)', '限流计数（0029）：键是摘要，不涉及学生；79k_auth_session 的限流断言'),
  ('insight.record_behavior_event(uuid,text,jsonb,timestamp with time zone,uuid)',
   '第二个 uuid 是幂等键（按学生隔离）；只认学生本人：79g_behavior_events 的“学生不能给别的学生写事件”“家长不能冒充学生写事件”'),
  ('insight.record_confidence_probe(uuid,platform.subject,text,text,timestamp with time zone,uuid)',
   '第二个 uuid 是幂等键（按学生隔离）；只认学生本人：79g_behavior_events 的“学生不能给别的学生写探针”'),
  ('platform.confirm_erasure(uuid)',
   '参数是删除请求 ID：只有发起人本人且仍是有效绑定家长能确认，别人的请求与不存在的请求同样 42501：79p_erasure'),
  ('insight.submit_exam_score(uuid,uuid,numeric)',
   '第二个 uuid 是考试安排 ID（须属于该学生）：另一户与不存在的学生同样 42501：79o_exam_prediction'),
  ('platform.cancel_erasure(uuid)',
   '参数是删除请求 ID：只有该学生的有效绑定家长能撤回，别户与不存在的请求同样 42501：79p_erasure'),
  ('insight.link_probe_attempt(uuid,uuid,uuid)',
   '探针与作答都按学生本人核对，别人的探针与“不存在”同样报错：79g_behavior_events 的“不能关联别人的探针”');

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
  v_row_rels integer := 0;
  v_row_fail text[] := '{}';
  v_key_expr text;
  v_filter text;
  v_rows_expected text[];
  v_rows_visible text[];
BEGIN
  SELECT array_agg(id ORDER BY id) || ARRAY[NULL::uuid, '00000000-0000-4000-8000-000000000000'::uuid]
    INTO v_accounts FROM platform.account;
  FOR rel IN SELECT c.oid, format('%s.%s', n.nspname, c.relname) AS qname,
                    EXISTS (SELECT 1 FROM pg_attribute a WHERE a.attrelid = c.oid AND a.attname = 'student_id' AND NOT a.attisdropped) AS has_sid,
                    -- 行键：表用主键列，视图用整行文本；表没有主键时为 NULL（按行比较报“没有行键”）
                    CASE WHEN c.relkind IN ('r', 'p') THEN
                      (SELECT 'row(' || string_agg('x.' || quote_ident(a.attname), ', ' ORDER BY a.attnum) || ')::text'
                         FROM pg_constraint k JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum = ANY (k.conkey)
                        WHERE k.conrelid = c.oid AND k.contype = 'p')
                    ELSE 'x::text' END AS key_expr
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
    v_key_expr := rel.key_expr;
    IF EXISTS (SELECT 1 FROM t.visibility_present_override o WHERE o.rel = rel.qname) THEN
      EXECUTE (SELECT o.present_sql FROM t.visibility_present_override o WHERE o.rel = rel.qname) INTO v_present;
      v_key_expr := NULL;  -- 超级用户直读为空，算不出预期的行，只按学生集合比较
    ELSIF v_key_expr IS NULL THEN
      v_row_fail := v_row_fail || format('%s：没有主键，无法按行比较', rel.qname);
    ELSE
      v_row_rels := v_row_rels + 1;
    END IF;
    v_filter := (SELECT f.filter_sql FROM t.visibility_row_filter f WHERE f.rel = rel.qname);
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
        WHEN 'raiser' THEN (SELECT coalesce(array_agg(DISTINCT d.student_id), '{}') FROM insight.dispute d
                             WHERE d.raised_by = acc AND d.student_id = ANY (t.readable_students(acc)))
        WHEN 'guardian_link' THEN (SELECT coalesce(array_agg(x), '{}') FROM unnest(t.readable_students(acc)) x
                                    WHERE EXISTS (SELECT 1 FROM platform.account a WHERE a.id = acc AND a.kind = 'guardian'))
        WHEN 'student_self' THEN (SELECT coalesce(array_agg(sp.id), '{}') FROM platform.student_profile sp
                                    JOIN platform.account a ON a.id = sp.account_id
                                   WHERE a.id = acc AND a.kind = 'student' AND a.status = 'active')
        WHEN 'household' THEN (SELECT coalesce(array_agg(DISTINCT h.id), '{}') FROM platform.household h
                                WHERE EXISTS (SELECT 1 FROM platform.account a WHERE a.id = acc AND a.kind = 'guardian'
                                                AND a.status = 'active' AND h.created_by_account = a.id)
                                   OR EXISTS (SELECT 1 FROM platform.student_profile s
                                               WHERE s.household_id = h.id AND s.id = ANY (t.readable_students(acc))))
      END;
      SELECT coalesce(array_agg(x ORDER BY x), '{}') INTO v_expected FROM unnest(v_expected) x WHERE x = ANY (v_present);
      -- 预期的行：以超级用户身份，取映射到预期集合的行，再按行级条件过滤（原始键是本表的列，加 x. 限定，免得映射子查询里同名列遮蔽）
      IF v_key_expr IS NOT NULL THEN
        EXECUTE format('SELECT coalesce(array_agg(k ORDER BY k), ''{}'') FROM (SELECT %s AS k FROM %s x WHERE (%s) = ANY ($1) AND (%s)) y',
                       v_key_expr, rel.oid::regclass, format(r.map_expr, 'x.' || quote_ident(r.raw_expr)), coalesce(v_filter, 'true'))
          USING v_expected, acc INTO v_rows_expected;
      END IF;
      PERFORM set_config('app.account_id', coalesce(acc::text, ''), false);
      EXECUTE 'SET ROLE starbuddy_bff';
      EXECUTE format('SELECT coalesce(array_agg(DISTINCT k), ''{}'') FROM (SELECT (%s)::uuid AS k FROM %s) x WHERE k IS NOT NULL',
                     r.raw_expr, rel.oid::regclass) INTO v_raw;
      IF v_key_expr IS NOT NULL THEN
        EXECUTE format('SELECT coalesce(array_agg(k ORDER BY k), ''{}'') FROM (SELECT %s AS k FROM %s x) y', v_key_expr, rel.oid::regclass)
          INTO v_rows_visible;
      END IF;
      EXECUTE 'RESET ROLE';
      v_visible := t.map_keys(v_raw, r.map_expr);
      IF v_visible IS DISTINCT FROM v_expected THEN
        v_fail := v_fail || format('%s 账号 %s：看到 %s，预期 %s', rel.qname, coalesce(acc::text, '未登录'), v_visible, v_expected);
      END IF;
      IF v_key_expr IS NOT NULL AND v_rows_visible IS DISTINCT FROM v_rows_expected THEN
        v_row_fail := v_row_fail || format('%s 账号 %s：多看到 %s 行（如 %s），少看到 %s 行（如 %s）', rel.qname, coalesce(acc::text, '未登录'),
          (SELECT count(*) FROM unnest(v_rows_visible) k WHERE NOT k = ANY (v_rows_expected)),
          (SELECT min(k) FROM unnest(v_rows_visible) k WHERE NOT k = ANY (v_rows_expected)),
          (SELECT count(*) FROM unnest(v_rows_expected) k WHERE NOT k = ANY (v_rows_visible)),
          (SELECT min(k) FROM unnest(v_rows_expected) k WHERE NOT k = ANY (v_rows_visible)));
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
  IF cardinality(v_row_fail) > 0 THEN RAISE NOTICE '可见性矩阵（按行）不一致：%', array_to_string(v_row_fail[1:5], E'\n'); END IF;
  PERFORM t.ok(v_row_rels >= 18 AND cardinality(v_row_fail) = 0,
               format('攻击面：可见性矩阵（按行）——每个账号看到的行 = 预期的行（%s 张表按行比较，%s 处不一致）',
                      v_row_rels, cardinality(v_row_fail)));
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
    WHEN 'uuid[]' THEN 'ARRAY[' || quote_literal(gen_random_uuid()) || '::uuid]'
    WHEN 'timestamp with time zone' THEN 'now()'
    WHEN 'platform.admission' THEN quote_literal('pending_review') || '::platform.admission'
    WHEN 'text' THEN quote_literal('synthetic') || '::text'
    WHEN 'numeric' THEN '1::numeric'
    WHEN 'smallint' THEN '9::smallint'
    WHEN 'integer' THEN '1'
    WHEN 'date' THEN quote_literal('2012-03-01') || '::date'
    WHEN 'interval' THEN quote_literal('1 day') || '::interval'
    WHEN 'platform.subject' THEN quote_literal('aya') || '::platform.subject'
    WHEN 'time without time zone' THEN quote_literal('22:00') || '::time'
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

-- ================= 七、跨家庭写矩阵（A2、A5；安全审查建议 3） =================
-- BFF 有 UPDATE（任一列）或 DELETE 权限的每张表：以每个账号（含未登录、不存在的 UUID）发不带 WHERE 的 UPDATE / DELETE，
-- 别家的行（映射到的学生不在该账号可读范围内）必须一行不变。不带 WHERE 是有意的：带 WHERE 时 SELECT 策略也会生效，测不出写策略本身。
-- 语句在子事务里执行，一律回滚；别家的行以超级用户身份按整行文本在执行前后比较。
-- update_set：UPDATE 用的 SET 子句，要能通过 WITH CHECK（否则只测到 WITH CHECK，测不到 USING）；
-- known_exclude：已登记、待人判断的遗留（$1 是账号，x 是本表的行），从“别家的行”里去掉。新表不在这里，测试就失败
CREATE TABLE t.write_rule (rel text PRIMARY KEY, update_set text, known_exclude text, reason text NOT NULL);
INSERT INTO t.write_rule VALUES
  ('platform.guardian_student_link', $q$status = 'revoked'$q$, NULL,
   '解除绑定（0002 link_revoke）：只能解除自己名下的、或本人作为学生的有效绑定'),
  ('platform.guardian_report', NULL, 'x.guardian_account_id = $1',
   '家长自述只能删自己写的（0006 greport_delete）；已解除绑定的作者仍能删自己写的，是攻击面 A5 登记的待判断遗留，这里不算越权');

DO $$
DECLARE
  rel record;
  acc uuid;
  r t.visibility_rule%ROWTYPE;
  w t.write_rule%ROWTYPE;
  v_accounts uuid[];
  v_ops text[];
  v_op text;
  v_readable uuid[];
  v_others text;
  v_before text[];
  v_after text[];
  v_err text;
  v_fail text[] := '{}';
  v_unjudged text[] := '{}';
  v_checked integer := 0;
  v_targets bigint := 0;
BEGIN
  SELECT array_agg(id ORDER BY id) || ARRAY[NULL::uuid, '00000000-0000-4000-8000-000000000000'::uuid]
    INTO v_accounts FROM platform.account;
  FOR rel IN SELECT c.oid, format('%s.%s', n.nspname, c.relname) AS qname,
                    has_any_column_privilege('starbuddy_bff', c.oid, 'UPDATE') AS can_update,
                    has_table_privilege('starbuddy_bff', c.oid, 'DELETE') AS can_delete
               FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
              WHERE c.relnamespace IN (SELECT t.app_schemas()) AND c.relkind IN ('r', 'p', 'v', 'f') AND NOT c.relispartition
                AND (has_any_column_privilege('starbuddy_bff', c.oid, 'UPDATE') OR has_table_privilege('starbuddy_bff', c.oid, 'DELETE'))
              ORDER BY 2 LOOP
    SELECT * INTO w FROM t.write_rule WHERE t.write_rule.rel = rel.qname;
    IF NOT FOUND OR (rel.can_update AND w.update_set IS NULL) THEN
      v_unjudged := v_unjudged || rel.qname;
      CONTINUE;
    END IF;
    SELECT * INTO r FROM t.visibility_rule WHERE t.visibility_rule.rel = rel.qname;
    IF NOT FOUND THEN
      r := ROW(rel.qname, 'student_id', '%s', 'student', 'equal', '默认');
    END IF;
    IF r.domain <> 'student' THEN  -- 账号表、家庭表现在没有写权限；将来有了要先在这里补上“别家的行”的算法
      v_unjudged := v_unjudged || rel.qname;
      CONTINUE;
    END IF;
    v_ops := '{}';
    IF rel.can_update THEN v_ops := v_ops || format('UPDATE %s SET %s', rel.oid::regclass, w.update_set); END IF;
    IF rel.can_delete THEN v_ops := v_ops || format('DELETE FROM %s', rel.oid::regclass); END IF;
    -- 别家的行：$1 是账号，$2 是该账号可读的学生
    v_others := format('SELECT coalesce(array_agg(x::text ORDER BY x::text), ''{}'') FROM %s x '
                       'WHERE NOT coalesce((%s) = ANY ($2), false) AND NOT coalesce(%s, false)',
                       rel.oid::regclass, format(r.map_expr, 'x.' || quote_ident(r.raw_expr)), coalesce(w.known_exclude, 'false'));
    FOREACH acc IN ARRAY v_accounts LOOP
      v_readable := t.readable_students(acc);  -- 执行前算一次：解除自己的绑定之后可读范围会变，不能拿变化后的范围判断“别家”
      EXECUTE v_others USING acc, v_readable INTO v_before;
      v_targets := v_targets + cardinality(v_before);
      FOREACH v_op IN ARRAY v_ops LOOP
        v_err := NULL;
        v_after := NULL;
        BEGIN
          PERFORM set_config('app.account_id', coalesce(acc::text, ''), true);
          EXECUTE 'SET LOCAL ROLE starbuddy_bff';
          EXECUTE v_op;
          EXECUTE 'RESET ROLE';
          EXECUTE v_others USING acc, v_readable INTO v_after;
          RAISE EXCEPTION USING ERRCODE = 'P0998', MESSAGE = 'rollback';
        EXCEPTION WHEN OTHERS THEN
          IF SQLSTATE <> 'P0998' THEN v_err := SQLSTATE || ':' || SQLERRM; END IF;
        END;
        v_checked := v_checked + 1;
        IF v_err IS NOT NULL THEN
          v_fail := v_fail || format('%s 账号 %s：%s 报错 %s', rel.qname, coalesce(acc::text, '未登录'), v_op, v_err);
        ELSIF v_after IS DISTINCT FROM v_before THEN
          v_fail := v_fail || format('%s 账号 %s：%s 改动或删掉了 %s 行别家的行', rel.qname, coalesce(acc::text, '未登录'), v_op,
                                     (SELECT count(*) FROM unnest(v_before) k WHERE NOT k = ANY (v_after)));
        END IF;
      END LOOP;
    END LOOP;
  END LOOP;
  PERFORM set_config('app.account_id', '', false);
  IF cardinality(v_fail) > 0 THEN RAISE NOTICE '写矩阵：%', array_to_string(v_fail[1:5], E'\n'); END IF;
  PERFORM t.ok(cardinality(v_unjudged) = 0, '攻击面：BFF 能改、能删的每张表都登记了写矩阵规则' ||
                                            coalesce('（未登记：' || nullif(array_to_string(v_unjudged, '、'), '') || '）', ''));
  PERFORM t.ok(v_checked >= 2 * cardinality(v_accounts) AND v_targets > 0,
               format('攻击面：写矩阵覆盖了足够多的（账号 × 语句）组合（%s 组，别家的行累计 %s 行）', v_checked, v_targets));
  PERFORM t.ok(cardinality(v_fail) = 0, format('攻击面：写矩阵——BFF 不带条件的 UPDATE / DELETE 改不到、删不掉别家的任何一行（%s 处不一致）',
                                             cardinality(v_fail)));
END $$;

-- ================= 八、复核角色与定时任务角色的函数越权矩阵（E3、H1、H3；安全审查建议 2 第三项） =================
-- 复核角色（审核台后端，starbuddy_review）可执行的每个 SECURITY DEFINER 函数：以非 staff 身份（未登录、不存在的账号、
-- 每个家长与学生账号（含停用的）、停用的 staff）调用，参数一律是合成的不存在对象，必须以 insufficient_privilege 拒绝，
-- 而且各身份的结果（SQLSTATE、消息、DETAIL、HINT）完全相同。不核对 staff 身份的函数登记在下面，逐个写明理由
CREATE TABLE t.review_matrix_exempt (sig text PRIMARY KEY, reason text NOT NULL);
INSERT INTO t.review_matrix_exempt VALUES
  ('platform.staff_login(text,text,text)', '审核台登录：登录之前没有身份，参数是账号、口令与验证码；79c_staff_auth 的登录断言'),
  ('platform.staff_session_resolve(text)', '审核台会话解析：参数是令牌，只返回令牌所属的 staff；79c_staff_auth'),
  ('platform.staff_logout(text)', '审核台登出：参数是令牌；79c_staff_auth'),
  ('kge.map_version_digest(uuid)', '地图版本摘要：只读，只含公共知识层内容，签字前核对用；按角色授权，不核对 staff 身份'),
  ('kge.map_change_digest_of(uuid)', '变更单摘要：只读，只含公共知识层内容；按角色授权，不核对 staff 身份'),
  ('platform.content_version_digest(uuid)', '内容版本摘要（0039）：只读，只含内容登记；按角色授权，不核对 staff 身份'),
  ('platform.item_admission_digest(platform.subject,text)', '题目准入摘要（0039）：只读，只含题目登记；按角色授权，不核对 staff 身份'),
  ('policy.object_digest(text,uuid)', '考情对象摘要（0041）：只读，只含考情库内容；按角色授权，不核对 staff 身份'),
  ('policy.approval_count(text,uuid)', '考情批准计数（0041）：只读，只返回计数；按角色授权，不核对 staff 身份'),
  ('kge.version_record(platform.subject)', '地图版本审核记录导出：含签字人账号 ID，只授 review 与 kge，不核对 staff 身份；79b_kge_review_signoff'),
  ('kge.page_review_record(platform.subject)', '页面审核记录导出（0038）：含签字人账号 ID，只授 review 与 kge，不核对 staff 身份；79t_page_review'),
  ('platform.policy_signoff_record()', '考情签字人导出（0040）：含签字人账号 ID，只授 review 与 kge，不核对 staff 身份；79v_policy_signoff');
-- 定时任务角色（starbuddy_scheduler）可执行的每个 SECURITY DEFINER 函数：没有身份，只靠参数界限防越权。
-- 每个参数为 NULL、每个时间参数伪造成未来（+1 天）或很久以前（-400 天）、每个整数参数越界（0、1000000），都必须以 22023 拒绝；
-- 合法参数下，未设身份与带任一账号身份的结果完全相同（结果与调用身份无关）。不适用的函数登记在下面（目前没有）
CREATE TABLE t.scheduler_matrix_exempt (sig text PRIMARY KEY, reason text NOT NULL);

DO $$
DECLARE
  f record;
  acc uuid;
  v_ids uuid[];
  v_args text[];
  v_first text;
  v text;
  v_fail text[] := '{}';
  v_calls integer := 0;
  v_funcs integer := 0;
  i integer;
BEGIN
  SELECT ARRAY[NULL::uuid, '00000000-0000-4000-8000-000000000000'::uuid] || array_agg(a.id ORDER BY a.id)
    INTO v_ids FROM platform.account a WHERE NOT (a.kind = 'staff' AND a.status = 'active');
  FOR f IN SELECT p.oid, p.oid::regprocedure AS sig, p.proargtypes, p.pronargs
             FROM pg_proc p
            WHERE p.pronamespace IN (SELECT t.app_schemas()) AND p.prosecdef
              AND has_function_privilege('starbuddy_review', p.oid, 'EXECUTE')
              AND NOT EXISTS (SELECT 1 FROM t.review_matrix_exempt e WHERE e.sig = p.oid::regprocedure::text)
            ORDER BY p.oid::regprocedure::text LOOP
    v_args := '{}';
    FOR i IN 0 .. f.pronargs - 1 LOOP
      v_args := v_args || t.dummy_arg((f.proargtypes::oid[])[i]::regtype);
    END LOOP;
    IF array_position(v_args, NULL) IS NOT NULL THEN
      v_fail := v_fail || format('%s：有无法构造的参数类型', f.sig);
      CONTINUE;
    END IF;
    v_funcs := v_funcs + 1;
    v_first := NULL;
    FOREACH acc IN ARRAY v_ids LOOP
      PERFORM set_config('app.account_id', coalesce(acc::text, ''), false);
      EXECUTE 'SET ROLE starbuddy_review';
      v := t.try_call(format('SELECT %s(%s)::text', f.oid::regproc, array_to_string(v_args, ', ')));
      EXECUTE 'RESET ROLE';
      v_calls := v_calls + 1;
      v_first := coalesce(v_first, v);
      IF v NOT LIKE '42501:%' THEN
        v_fail := v_fail || format('%s 以 %s 调用：%s（应为 42501）', f.sig, coalesce(acc::text, '未登录'), v);
      ELSIF v IS DISTINCT FROM v_first THEN
        v_fail := v_fail || format('%s 以 %s 调用：%s；未登录时：%s（应相同）', f.sig, coalesce(acc::text, '未登录'), v, v_first);
      END IF;
    END LOOP;
  END LOOP;
  PERFORM set_config('app.account_id', '', false);
  IF cardinality(v_fail) > 0 THEN RAISE NOTICE '复核角色函数矩阵：%', array_to_string(v_fail[1:5], E'\n'); END IF;
  PERFORM t.ok(v_funcs >= 15 AND v_calls >= 100,
               format('攻击面：复核角色函数矩阵覆盖了足够多的函数与身份（%s 个函数、%s 组调用）', v_funcs, v_calls));
  PERFORM t.ok(cardinality(v_fail) = 0,
               format('攻击面：复核角色函数矩阵——非 staff 身份调用复核角色可执行的每个函数，都以 42501 拒绝且结果相同（%s 处不一致）',
                      cardinality(v_fail)));
END $$;

-- 定时任务函数的合法参数（只用于“结果与身份无关”的对照与越界时的其余参数）
CREATE FUNCTION t.scheduler_arg(typ regtype) RETURNS text LANGUAGE sql IMMUTABLE AS $$
  SELECT CASE typ::text
    WHEN 'timestamp with time zone' THEN 'now() - interval ''1 hour'''
    WHEN 'integer' THEN '10'
    WHEN 'numeric' THEN '0.1::numeric'
    WHEN 'platform.subject' THEN quote_literal('aya') || '::platform.subject'
    ELSE NULL END $$;

DO $$
DECLARE
  f record;
  acc uuid;
  v_args text[];
  v_call text[];
  v_bad text[];
  v_bad_one text;
  v_typ text;
  v_base text;
  v text;
  v_fail text[] := '{}';
  v_checks integer := 0;
  v_funcs integer := 0;
  i integer;
BEGIN
  FOR f IN SELECT p.oid, p.oid::regprocedure AS sig, p.proargtypes, p.pronargs
             FROM pg_proc p
            WHERE p.pronamespace IN (SELECT t.app_schemas()) AND p.prosecdef
              AND has_function_privilege('starbuddy_scheduler', p.oid, 'EXECUTE')
              AND NOT EXISTS (SELECT 1 FROM t.scheduler_matrix_exempt e WHERE e.sig = p.oid::regprocedure::text)
            ORDER BY p.oid::regprocedure::text LOOP
    v_args := '{}';
    FOR i IN 0 .. f.pronargs - 1 LOOP
      v_args := v_args || t.scheduler_arg((f.proargtypes::oid[])[i]::regtype);
    END LOOP;
    IF f.pronargs = 0 OR array_position(v_args, NULL) IS NOT NULL THEN
      v_fail := v_fail || format('%s：没有参数或有无法构造的参数类型（没有可检查的界限时请登记豁免）', f.sig);
      CONTINUE;
    END IF;
    v_funcs := v_funcs + 1;
    -- 越界：逐个参数替换成越界值，其余参数保持合法
    FOR i IN 1 .. f.pronargs LOOP
      v_typ := format_type((f.proargtypes::oid[])[i - 1], NULL);
      v_bad := ARRAY['NULL::' || v_typ];
      IF v_typ = 'timestamp with time zone' THEN
        v_bad := v_bad || ARRAY['now() + interval ''1 day''', 'now() - interval ''400 days'''];
      ELSIF v_typ = 'integer' THEN
        v_bad := v_bad || ARRAY['0', '1000000'];
      END IF;
      FOREACH v_bad_one IN ARRAY v_bad LOOP
        v_call := v_args;
        v_call[i] := v_bad_one;
        PERFORM set_config('app.account_id', '', false);
        EXECUTE 'SET ROLE starbuddy_scheduler';
        v := t.try_call(format('SELECT %s(%s)::text', f.oid::regproc, array_to_string(v_call, ', ')));
        EXECUTE 'RESET ROLE';
        v_checks := v_checks + 1;
        IF v NOT LIKE '22023:%' THEN
          v_fail := v_fail || format('%s 第 %s 个参数为 %s：%s（应为 22023）', f.sig, i, v_bad_one, v);
        END IF;
      END LOOP;
    END LOOP;
    -- 与身份无关：合法参数下，未设身份与带任一有效账号身份的结果相同（调用在子事务里，一律回滚）
    EXECUTE 'SET ROLE starbuddy_scheduler';
    v_base := t.try_call(format('SELECT %s(%s)::text', f.oid::regproc, array_to_string(v_args, ', ')));
    EXECUTE 'RESET ROLE';
    IF v_base NOT LIKE 'returned:%' THEN
      v_fail := v_fail || format('%s 合法参数调用失败：%s', f.sig, v_base);
    END IF;
    FOR acc IN SELECT a.id FROM platform.account a WHERE a.status = 'active' ORDER BY a.id LOOP
      PERFORM set_config('app.account_id', acc::text, false);
      EXECUTE 'SET ROLE starbuddy_scheduler';
      v := t.try_call(format('SELECT %s(%s)::text', f.oid::regproc, array_to_string(v_args, ', ')));
      EXECUTE 'RESET ROLE';
      v_checks := v_checks + 1;
      IF v IS DISTINCT FROM v_base THEN
        v_fail := v_fail || format('%s 以 %s 身份调用：%s；未设身份时：%s（应相同）', f.sig, acc, v, v_base);
      END IF;
    END LOOP;
  END LOOP;
  PERFORM set_config('app.account_id', '', false);
  IF cardinality(v_fail) > 0 THEN RAISE NOTICE '定时任务函数矩阵：%', array_to_string(v_fail[1:5], E'\n'); END IF;
  PERFORM t.ok(v_funcs >= 5 AND v_checks >= 30,
               format('攻击面：定时任务函数矩阵覆盖了足够多的函数与检查（%s 个函数、%s 项检查）', v_funcs, v_checks));
  PERFORM t.ok(cardinality(v_fail) = 0,
               format('攻击面：定时任务函数矩阵——空参数、伪造时钟、越界批量一律 22023，结果与调用身份无关（%s 处不一致）',
                      cardinality(v_fail)));
END $$;

-- 两张豁免表的每一行都要对应一个现有的、该角色可执行的 SECURITY DEFINER 函数（函数改名或收回授权后不留过期登记）
SELECT t.ok(NOT EXISTS (
  SELECT 1 FROM (SELECT sig, 'starbuddy_review' AS role FROM t.review_matrix_exempt
                 UNION ALL SELECT sig, 'starbuddy_scheduler' FROM t.scheduler_matrix_exempt
                 UNION ALL SELECT sig, 'starbuddy_bff' FROM t.function_matrix_exempt) e
   WHERE NOT EXISTS (SELECT 1 FROM pg_proc p WHERE p.prosecdef AND p.oid::regprocedure::text = e.sig
                       AND has_function_privilege(e.role, p.oid, 'EXECUTE'))),
  '攻击面：函数矩阵的豁免登记都对应该角色可执行的现有 SECURITY DEFINER 函数');
