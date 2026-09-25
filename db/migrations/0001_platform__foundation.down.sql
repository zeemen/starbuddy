-- 0001 回滚。角色是集群级对象：只有在不再被任何对象引用时才删除。
DROP SCHEMA IF EXISTS platform CASCADE;
DO $$
DECLARE r text;
BEGIN
  FOREACH r IN ARRAY ARRAY['starbuddy_bff', 'starbuddy_learning', 'starbuddy_authz'] LOOP
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = r) THEN
      EXECUTE format('DROP OWNED BY %I', r);
      EXECUTE format('DROP ROLE %I', r);
    END IF;
  END LOOP;
END $$;
