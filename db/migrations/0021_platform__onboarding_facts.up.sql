-- 0021 platform · 建档的既有事实扩展：各科教材版本（specs/sie-01-onboarding/spec.md 设计要点 1；字段字典 outer.g1.textbook_version）
-- 状态：草稿。只写文件，由人先在副本演练再执行（CLAUDE.md 必停项：迁移、RLS 与权限）。只依赖 0002。
-- 对照文档决定 #3：已有个人层事实留在 platform.*。
-- 优先级（规则 2）：student_profile.textbook_version 只作建档前的默认值（已有读取方）；某科只要本表有行，以本表为准。
-- 规则 6：学生与家长的说法各存一行（按填写账号），不互相覆盖、不取平均；同一账号再填只改自己那一行。
-- 学生填“不清楚”存 NULL，不影响家长那一行。两边都填了而说法不同时，由洞察服务或教研按来源取舍（不在库里合并）。

CREATE TABLE platform.student_subject_textbook (
  student_id        uuid NOT NULL REFERENCES platform.student_profile (id) ON DELETE CASCADE,
  subject           platform.subject NOT NULL,
  textbook_version  text CHECK (textbook_version ~ '^[a-z0-9][a-z0-9\-]{1,31}$'),  -- 与 curriculum_version.textbook_version 同一套编码；NULL = 不清楚
  source_type       text NOT NULL CHECK (source_type IN ('self', 'guardian')),     -- 填写人身份（规则 6：标来源）
  updated_by        uuid NOT NULL REFERENCES platform.account (id),                 -- 填写账号；每个账号每科一行
  updated_at        timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (student_id, subject, updated_by)
);
COMMENT ON TABLE platform.student_subject_textbook IS
  '各科教材版本（0021，SIE-01）：学生本人或已绑定家长经 platform.set_subject_textbook 写入，各填各的；BFF 只经 insight.onboarding_view 读自己填的';

ALTER TABLE platform.student_subject_textbook ENABLE ROW LEVEL SECURITY;
ALTER TABLE platform.student_subject_textbook FORCE ROW LEVEL SECURITY;
REVOKE ALL ON platform.student_subject_textbook FROM PUBLIC;
-- BFF 没有表授权（两方互不可见，读只经 insight.onboarding_view）；学习服务与洞察服务读全部（按学生隔离由调用方负责，与既有策略一致）
GRANT SELECT ON platform.student_subject_textbook TO starbuddy_learning, starbuddy_insight;
GRANT SELECT, INSERT, UPDATE ON platform.student_subject_textbook TO starbuddy_authz;
CREATE POLICY textbook_service_read ON platform.student_subject_textbook FOR SELECT TO starbuddy_learning, starbuddy_insight USING (true);

-- 写入只经函数：学生本人或已绑定家长
CREATE FUNCTION platform.set_subject_textbook(p_student uuid, p_subject platform.subject, p_version text) RETURNS void
  LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog, platform, pg_temp
  AS $$
DECLARE
  v_role text;
BEGIN
  IF p_student IS NULL OR p_subject IS NULL THEN
    RAISE EXCEPTION '缺少学生或学科' USING ERRCODE = 'invalid_parameter_value';
  END IF;
  IF platform.is_self_student(p_student) THEN
    v_role := 'self';
  ELSIF platform.is_linked_guardian(p_student) THEN
    v_role := 'guardian';
  ELSE
    RAISE EXCEPTION 'not allowed' USING ERRCODE = 'insufficient_privilege';
  END IF;
  IF p_version IS NOT NULL AND p_version !~ '^[a-z0-9][a-z0-9\-]{1,31}$' THEN
    RAISE EXCEPTION '教材版本编码不合格式' USING ERRCODE = 'invalid_parameter_value';
  END IF;
  INSERT INTO platform.student_subject_textbook (student_id, subject, textbook_version, source_type, updated_by, updated_at)
  VALUES (p_student, p_subject, p_version, v_role, platform.current_account_id(), now())
  ON CONFLICT (student_id, subject, updated_by) DO UPDATE
     SET textbook_version = EXCLUDED.textbook_version, source_type = EXCLUDED.source_type, updated_at = EXCLUDED.updated_at;
END $$;
ALTER FUNCTION platform.set_subject_textbook(uuid, platform.subject, text) OWNER TO starbuddy_authz;
REVOKE EXECUTE ON FUNCTION platform.set_subject_textbook(uuid, platform.subject, text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION platform.set_subject_textbook(uuid, platform.subject, text) TO starbuddy_bff;
