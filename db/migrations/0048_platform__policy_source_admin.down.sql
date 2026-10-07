-- 0048 回滚：删除官方来源清单的新增、下线函数，staff 权限表与来源清单操作记录。官方来源清单回到只由迁移或 DBA 维护（0013）。
-- 状态：草稿，由人执行，先在副本演练。请以单事务执行（psql -1）。
-- 操作记录只追加：已有记录时中止（回滚会丢失谁在何时改过清单），由人决定是否放弃回滚。清单本身（policy.source_domain）的行不动。
-- staff 权限表随回滚删除（由 DBA 授予的配置，回滚后不再有用途）。
LOCK TABLE platform.policy_source_domain_change IN ACCESS EXCLUSIVE MODE;
DO $$
DECLARE v_n bigint;
BEGIN
  SELECT count(*) INTO v_n FROM platform.policy_source_domain_change;
  IF v_n > 0 THEN
    RAISE EXCEPTION '0048 回滚中止：platform.policy_source_domain_change 有 % 条来源清单操作记录（只追加），回滚会丢失这些记录', v_n
      USING ERRCODE = 'object_not_in_prerequisite_state';
  END IF;
END $$;

DROP FUNCTION policy.retire_source_domain(text);
DROP FUNCTION policy.add_source_domain(text, text);
DROP FUNCTION policy.normalize_source_host(text);
REVOKE INSERT, UPDATE (status, issuer) ON policy.source_domain FROM starbuddy_authz;

DELETE FROM platform.personal_table_registry WHERE nspname = 'platform' AND relname IN ('staff_permission', 'policy_source_domain_change');
DROP TABLE platform.policy_source_domain_change;
DROP TABLE platform.staff_permission;
DROP FUNCTION platform.guard_staff_permission();
