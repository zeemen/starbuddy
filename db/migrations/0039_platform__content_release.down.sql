-- 0039 回滚：删除内容版本与准入的发布闸门（函数、触发器、审核与发布记录表、来源三列、撤下时间），恢复 0003 的授权、BFF 策略与状态约束。
-- 状态：草稿，由人执行，先在副本演练。请以单事务执行（psql -1）。
-- 审核与发布记录只追加（触发器连超级用户也拦删除），回滚不能也不应静默删掉签字记录：表里有记录时中止，由人决定是否放弃回滚。
-- 先锁表：检查与改表之间不会有新的签字插进来
LOCK TABLE platform.release_record IN ACCESS EXCLUSIVE MODE;
DO $$
DECLARE v_n bigint;
BEGIN
  SELECT count(*) INTO v_n FROM platform.release_record;
  IF v_n > 0 THEN
    RAISE EXCEPTION '0039 回滚中止：platform.release_record 有 % 条审核与发布记录（只追加，不能删除），回滚会丢失这些记录', v_n
      USING ERRCODE = 'object_not_in_prerequisite_state';
  END IF;
END $$;

-- BFF 可见性恢复 0003
DROP POLICY item_read ON platform.item;
CREATE POLICY item_read ON platform.item FOR SELECT TO starbuddy_bff USING (admission = 'admitted');
DROP POLICY skill_node_read ON platform.skill_node;
CREATE POLICY skill_node_read ON platform.skill_node FOR SELECT TO starbuddy_bff USING (true);

-- 授权恢复 0003（学习服务整表 UPDATE）；收回函数属主的新增授权
REVOKE UPDATE ON platform.curriculum_version, platform.skill_node, platform.item FROM starbuddy_learning;
GRANT UPDATE ON platform.curriculum_version, platform.skill_node, platform.item TO starbuddy_learning;
REVOKE UPDATE (admission, admitted_at, source_ref, source_type, license_status) ON platform.item FROM starbuddy_authz;
REVOKE UPDATE (status, published_at, retired_at) ON platform.curriculum_version FROM starbuddy_authz;
REVOKE SELECT ON platform.skill_node FROM starbuddy_authz;

DROP FUNCTION platform.admission_change_log(timestamptz);
DROP FUNCTION platform.change_admission(uuid[]);
DROP FUNCTION platform.sign_admission(platform.subject, text, platform.admission, text, text, text, text, text, text, text);
DROP FUNCTION platform.check_admission_target(platform.subject, platform.admission, text, text, text, text);
DROP FUNCTION platform.retire_content_version(uuid, text);
DROP FUNCTION platform.publish_content_version(uuid);
DROP FUNCTION platform.sign_content_version(uuid, text, text, text);
DROP FUNCTION platform.item_admission_digest(platform.subject, text);
DROP FUNCTION platform.content_version_digest(uuid);

DROP TRIGGER guard_content_registration ON platform.item;
DROP TRIGGER guard_content_registration ON platform.skill_node;
DROP TRIGGER guard_curriculum_version ON platform.curriculum_version;
DROP FUNCTION platform.guard_content_registration();
DROP FUNCTION platform.guard_curriculum_version();

DELETE FROM platform.personal_table_registry WHERE nspname = 'platform' AND relname = 'release_record';
DROP TABLE platform.release_record;
ALTER TABLE platform.curriculum_version DROP CONSTRAINT curriculum_version_id_subject_uq;

ALTER TABLE platform.item
  DROP CONSTRAINT item_admitted_source,
  DROP COLUMN license_status,
  DROP COLUMN source_type,
  DROP COLUMN source_ref;
-- 0003 的状态约束以 NOT VALID 恢复：已撤下（status = retired）的行在 0003 口径下不合格，不能让回滚被这些行卡住（同 0019 的做法）
ALTER TABLE platform.curriculum_version
  DROP CONSTRAINT curriculum_version_status_times,
  DROP COLUMN retired_at,
  ADD CONSTRAINT curriculum_version_check CHECK ((status = 'draft') = (published_at IS NULL)) NOT VALID;

DROP FUNCTION platform.admission_transition_ok(platform.admission, platform.admission);
DROP FUNCTION platform.admission_min_review_rank(platform.subject);
DROP FUNCTION platform.content_requires_map(platform.subject);
