-- 授权快照（3-B spec 第 7 节第 4 条）：把 角色 × 对象 × 权限 导出成排好序的文本，存进 psql 变量 snap_privileges。
-- 覆盖 schema（含 public）、表 / 视图 / 分区 / 序列、列级授权、函数的 EXECUTE、类型的 USAGE、默认授权、
-- starbuddy_* 角色的属性与成员关系，以及 starbuddy_* 角色持有的对象的属主。
-- 每条授权都带授权人与是否可转授（WITH GRANT OPTION，安全审查阻断 4）。超级用户统一写成 SUPERUSER：
-- 迁移执行者的名字随环境不同，它自己得到的权限也不导出（不影响应用角色）。
-- 用法：80_attack_surface.sql 用 \i 读入后与 db/tests/expected/privileges.txt 比对；
--       db/tests/update-snapshots.sh 用同一份查询重新生成期望文件。
SELECT coalesce(string_agg(line, E'\n' ORDER BY line), '') AS snap_privileges FROM (
  WITH nsp AS (
    SELECT oid, nspname FROM pg_namespace
     WHERE nspname NOT IN ('pg_catalog', 'information_schema', 't', 'pg_toast') AND nspname NOT LIKE 'pg_temp%'
       AND nspname NOT LIKE 'pg_toast_temp%'
  ), runner AS (
    SELECT oid FROM pg_roles WHERE rolsuper
  ), who AS (  -- 角色 oid → 快照里的名字
    SELECT 0::oid AS oid, 'PUBLIC' AS name
    UNION ALL SELECT oid, CASE WHEN rolsuper THEN 'SUPERUSER' ELSE rolname END FROM pg_roles
  )
  -- schema 权限
  SELECT format('schema|%s|%s|%s|%s%s', n.nspname, g.name, a.privilege_type, gr.name, CASE WHEN a.is_grantable THEN '|grantable' ELSE '' END) AS line
    FROM nsp n JOIN pg_namespace x ON x.oid = n.oid, aclexplode(coalesce(x.nspacl, acldefault('n', x.nspowner))) a
    JOIN who g ON g.oid = a.grantee JOIN who gr ON gr.oid = a.grantor
   WHERE a.grantee NOT IN (SELECT oid FROM runner)
  UNION ALL
  -- 表、视图、分区、序列
  SELECT format('rel|%s.%s|%s|%s|%s%s', n.nspname, c.relname, g.name, a.privilege_type, gr.name,
                CASE WHEN a.is_grantable THEN '|grantable' ELSE '' END)
    FROM pg_class c JOIN nsp n ON n.oid = c.relnamespace, aclexplode(coalesce(c.relacl, acldefault('r', c.relowner))) a
    JOIN who g ON g.oid = a.grantee JOIN who gr ON gr.oid = a.grantor
   WHERE c.relkind IN ('r', 'p', 'v', 'm', 'S', 'f') AND a.grantee NOT IN (SELECT oid FROM runner)
  UNION ALL
  -- 列级授权
  SELECT format('col|%s.%s.%s|%s|%s|%s%s', n.nspname, c.relname, att.attname, g.name, a.privilege_type, gr.name,
                CASE WHEN a.is_grantable THEN '|grantable' ELSE '' END)
    FROM pg_attribute att JOIN pg_class c ON c.oid = att.attrelid JOIN nsp n ON n.oid = c.relnamespace, aclexplode(att.attacl) a
    JOIN who g ON g.oid = a.grantee JOIN who gr ON gr.oid = a.grantor
   WHERE att.attacl IS NOT NULL AND NOT att.attisdropped AND a.grantee NOT IN (SELECT oid FROM runner)
  UNION ALL
  -- 函数 EXECUTE（proacl 为空表示默认：PUBLIC 可执行）
  SELECT format('func|%s|%s|%s|%s%s', p.oid::regprocedure, g.name, a.privilege_type, gr.name,
                CASE WHEN a.is_grantable THEN '|grantable' ELSE '' END)
    FROM pg_proc p JOIN nsp n ON n.oid = p.pronamespace, aclexplode(coalesce(p.proacl, acldefault('f', p.proowner))) a
    JOIN who g ON g.oid = a.grantee JOIN who gr ON gr.oid = a.grantor
   WHERE a.grantee NOT IN (SELECT oid FROM runner)
     AND NOT EXISTS (SELECT 1 FROM pg_depend d WHERE d.objid = p.oid AND d.deptype = 'e')  -- 扩展自带的函数不导出
  UNION ALL
  -- 类型的 USAGE（只导出显式授过权的类型；默认是 PUBLIC 可用）
  SELECT format('type|%s|%s|%s|%s%s', ty.oid::regtype, g.name, a.privilege_type, gr.name,
                CASE WHEN a.is_grantable THEN '|grantable' ELSE '' END)
    FROM pg_type ty JOIN nsp n ON n.oid = ty.typnamespace, aclexplode(ty.typacl) a
    JOIN who g ON g.oid = a.grantee JOIN who gr ON gr.oid = a.grantor
   WHERE ty.typacl IS NOT NULL AND a.grantee NOT IN (SELECT oid FROM runner)
  UNION ALL
  -- 默认授权（ALTER DEFAULT PRIVILEGES），不论授给谁
  SELECT format('defacl|%s|%s|%s|%s|%s%s', g0.name, coalesce(dn.nspname, '*'), d.defaclobjtype, g.name, a.privilege_type,
                CASE WHEN a.is_grantable THEN '|grantable' ELSE '' END)
    FROM pg_default_acl d LEFT JOIN pg_namespace dn ON dn.oid = d.defaclnamespace
    JOIN who g0 ON g0.oid = d.defaclrole, aclexplode(d.defaclacl) a JOIN who g ON g.oid = a.grantee
  UNION ALL
  -- starbuddy_* 角色的属性
  SELECT format('role|%s|super=%s|bypassrls=%s|createrole=%s|createdb=%s|replication=%s|login=%s|inherit=%s',
                r.rolname, r.rolsuper, r.rolbypassrls, r.rolcreaterole, r.rolcreatedb, r.rolreplication, r.rolcanlogin, r.rolinherit)
    FROM pg_roles r WHERE r.rolname LIKE 'starbuddy\_%'
  UNION ALL
  -- starbuddy_* 角色作为成员的成员关系（登录角色继承应用角色随部署而不同，不进快照；由 80 的 H2 断言检查）
  SELECT format('member|%s|%s|admin=%s', gm.name, gr.name, m.admin_option)
    FROM pg_auth_members m JOIN who gm ON gm.oid = m.member JOIN who gr ON gr.oid = m.roleid
   WHERE pg_get_userbyid(m.member) LIKE 'starbuddy\_%'
  UNION ALL
  -- 属主是 starbuddy_* 角色的对象（SECURITY DEFINER 以属主身份执行，属主就是权限）
  SELECT format('owner|%s|%s', p.oid::regprocedure, pg_get_userbyid(p.proowner))
    FROM pg_proc p JOIN nsp n ON n.oid = p.pronamespace
   WHERE pg_get_userbyid(p.proowner) LIKE 'starbuddy\_%'
  UNION ALL
  -- SECURITY DEFINER 函数清单（docs/security/pentest/scope.md 的被测函数表由 docs/security/check_attack_surface.py 与这里核对）
  SELECT format('secdef|%s', p.oid::regprocedure)
    FROM pg_proc p JOIN nsp n ON n.oid = p.pronamespace
   WHERE p.prosecdef
  UNION ALL
  SELECT format('owner|%s.%s|%s', n.nspname, c.relname, pg_get_userbyid(c.relowner))
    FROM pg_class c JOIN nsp n ON n.oid = c.relnamespace
   WHERE pg_get_userbyid(c.relowner) LIKE 'starbuddy\_%'
) q \gset
