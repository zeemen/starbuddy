-- 0001 回滚：只删除 platform schema。角色是集群级对象，可能被同集群其他库或其他 schema 使用，
-- 迁移不删除角色（安全审查阻断 5）；需要时由人按 db/bootstrap/roles.sql 的反向操作处理。
DROP SCHEMA IF EXISTS platform CASCADE;
