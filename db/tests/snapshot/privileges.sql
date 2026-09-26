-- 授权快照（3-B spec 第 7 节第 4 条）：把 角色 × 对象 × 权限 导出成排好序的文本，存进 psql 变量 snap_privileges。
-- 覆盖 schema、表 / 视图 / 分区、列级授权、函数的 EXECUTE，以及 starbuddy_* 角色持有的对象的属主。
-- 迁移执行者（超级用户）自己的权限不导出：它随环境不同，也不影响应用角色。
-- 用法：80_attack_surface.sql 用 \i 读入后与 db/tests/expected/privileges.txt 比对；
--       db/tests/update-snapshots.sh 用同一份查询重新生成期望文件。
SELECT coalesce(string_agg(line, E'\n' ORDER BY line), '') AS snap_privileges FROM (
  WITH nsp AS (
    SELECT oid, nspname FROM pg_namespace
     WHERE nspname NOT IN ('pg_catalog', 'information_schema', 't', 'pg_toast') AND nspname NOT LIKE 'pg_temp%'
       AND nspname NOT LIKE 'pg_toast_temp%'
  ), runner AS (
    SELECT oid FROM pg_roles WHERE rolsuper
  )
  -- schema 权限
  SELECT format('schema|%s|%s|%s', n.nspname, CASE a.grantee WHEN 0 THEN 'PUBLIC' ELSE pg_get_userbyid(a.grantee) END, a.privilege_type) AS line
    FROM nsp n JOIN pg_namespace x ON x.oid = n.oid, aclexplode(coalesce(x.nspacl, acldefault('n', x.nspowner))) a
   WHERE a.grantee NOT IN (SELECT oid FROM runner) AND n.nspname <> 'public'
  UNION ALL
  -- 表、视图、分区、序列
  SELECT format('rel|%s.%s|%s|%s', n.nspname, c.relname, CASE a.grantee WHEN 0 THEN 'PUBLIC' ELSE pg_get_userbyid(a.grantee) END,
                a.privilege_type)
    FROM pg_class c JOIN nsp n ON n.oid = c.relnamespace, aclexplode(coalesce(c.relacl, acldefault('r', c.relowner))) a
   WHERE c.relkind IN ('r', 'p', 'v', 'm', 'S', 'f') AND a.grantee NOT IN (SELECT oid FROM runner)
  UNION ALL
  -- 列级授权
  SELECT format('col|%s.%s.%s|%s|%s', n.nspname, c.relname, att.attname,
                CASE a.grantee WHEN 0 THEN 'PUBLIC' ELSE pg_get_userbyid(a.grantee) END, a.privilege_type)
    FROM pg_attribute att JOIN pg_class c ON c.oid = att.attrelid JOIN nsp n ON n.oid = c.relnamespace, aclexplode(att.attacl) a
   WHERE att.attacl IS NOT NULL AND NOT att.attisdropped AND a.grantee NOT IN (SELECT oid FROM runner)
  UNION ALL
  -- 函数 EXECUTE（proacl 为空表示默认：PUBLIC 可执行）
  SELECT format('func|%s|%s|%s', p.oid::regprocedure, CASE a.grantee WHEN 0 THEN 'PUBLIC' ELSE pg_get_userbyid(a.grantee) END,
                a.privilege_type)
    FROM pg_proc p JOIN nsp n ON n.oid = p.pronamespace, aclexplode(coalesce(p.proacl, acldefault('f', p.proowner))) a
   WHERE a.grantee NOT IN (SELECT oid FROM runner)
     AND NOT EXISTS (SELECT 1 FROM pg_depend d WHERE d.objid = p.oid AND d.deptype = 'e')  -- 扩展自带的函数不导出
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
