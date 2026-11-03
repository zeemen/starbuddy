-- 0040 回滚：删除考情签字人表（先回滚 0041）。
-- 状态：草稿，由人执行，先在副本演练。请以单事务执行（psql -1）。
-- 签字记录只追加（触发器连超级用户也拦删除），回滚不能也不应静默删掉签字记录：表里有记录时中止，由人决定是否放弃回滚。
-- 先锁表：检查与删表之间不会有新的签字插进来
LOCK TABLE platform.policy_signoff IN ACCESS EXCLUSIVE MODE;
DO $$
DECLARE v_n bigint;
BEGIN
  SELECT count(*) INTO v_n FROM platform.policy_signoff;
  IF v_n > 0 THEN
    RAISE EXCEPTION '0040 回滚中止：platform.policy_signoff 有 % 条考情签字记录（只追加，不能删除），回滚会丢失这些记录', v_n
      USING ERRCODE = 'object_not_in_prerequisite_state';
  END IF;
END $$;

DELETE FROM platform.personal_table_registry WHERE nspname = 'platform' AND relname = 'policy_signoff';
DROP FUNCTION platform.policy_signoff_record();
DROP TABLE platform.policy_signoff;
