-- 0001 platform · 基础：schema、数据库角色、公共枚举、当前账号函数
-- 状态：草稿 v0.9（specs/learner-schema-v0.9/spec.md）。只写文件，由人执行（CLAUDE.md 必停项）。
-- 只用标准 Postgres 16 能力，不依赖托管专有 schema（ADR 02 第 1 条）。

-- 请以单事务执行（psql -1）。角色断言放在建 schema 之前：断言失败时不会留下空 schema。
-- 数据库角色由 db/bootstrap/roles.sql 创建（人执行）。这里只断言角色存在且属性安全：
-- 预先存在一个带 BYPASSRLS 或 SUPERUSER 的同名角色时，RLS 会静默失效，必须拒绝（安全审查阻断 5）。
DO $$
DECLARE v_role record;
BEGIN
  FOR v_role IN SELECT n AS rolname FROM unnest(ARRAY['starbuddy_bff', 'starbuddy_learning', 'starbuddy_review', 'starbuddy_authz']) n LOOP
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = v_role.rolname) THEN
      RAISE EXCEPTION '角色 % 不存在：请先执行 db/bootstrap/roles.sql', v_role.rolname;
    END IF;
  END LOOP;
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname IN ('starbuddy_bff', 'starbuddy_learning', 'starbuddy_review')
             AND (rolsuper OR rolbypassrls)) THEN
    RAISE EXCEPTION 'starbuddy_bff / starbuddy_learning / starbuddy_review 不能是 SUPERUSER 或 BYPASSRLS';
  END IF;
  -- 成员关系按递归判断（pg_has_role），多层继承也算（安全审查 N1）。超级用户天然是所有角色的成员，排除。
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'starbuddy_authz' AND (rolcanlogin OR rolsuper))
     OR EXISTS (SELECT 1 FROM pg_roles r
                 WHERE NOT r.rolsuper AND r.rolname <> 'starbuddy_authz'
                   AND pg_has_role(r.oid, 'starbuddy_authz', 'MEMBER')) THEN
    RAISE EXCEPTION 'starbuddy_authz 必须 NOLOGIN、非 SUPERUSER，且不能直接或间接授予任何角色';
  END IF;
  IF EXISTS (SELECT 1 FROM pg_roles r
              WHERE NOT r.rolsuper
                AND (pg_has_role(r.oid, 'starbuddy_bff', 'MEMBER')::int
                     + pg_has_role(r.oid, 'starbuddy_learning', 'MEMBER')::int
                     + pg_has_role(r.oid, 'starbuddy_review', 'MEMBER')::int) > 1) THEN
    RAISE EXCEPTION '同一个角色不能直接或间接同时继承 starbuddy_bff、starbuddy_learning、starbuddy_review 中的两个';
  END IF;
END $$;


CREATE SCHEMA platform;
COMMENT ON SCHEMA platform IS '平台 schema：家庭关系、学习者模型（唯一真相源，CLAUDE.md 规则 2）';

REVOKE ALL ON SCHEMA platform FROM PUBLIC;
GRANT USAGE ON SCHEMA platform TO starbuddy_bff, starbuddy_learning, starbuddy_review, starbuddy_authz;

-- 公共枚举：取值与契约 packages/contracts/openapi/plugin-api.v0.9.yaml 保持一致
CREATE TYPE platform.subject AS ENUM ('wenli', 'aya', 'zhixing', 'echo');
CREATE TYPE platform.account_kind AS ENUM ('guardian', 'student', 'staff');
CREATE TYPE platform.admission AS ENUM ('admitted', 'pending_review', 'source_unverified', 'rejected');
CREATE TYPE platform.semantics AS ENUM ('formal', 'practice');
CREATE TYPE platform.cross_subject_error AS ENUM (
  'reading_the_question', 'locating_information', 'thinking_method', 'computation_or_grammar',
  'expression_convention', 'strategy_and_time', 'knowledge'
);
-- 五道门槛 + unseen（完整方案 5.5）：transfer = 星亮，retain = 外圈
CREATE TYPE platform.mastery_state AS ENUM ('unseen', 'understand', 'execute', 'stable', 'transfer', 'retain');
CREATE TYPE platform.review_reason AS ENUM (
  'subjective_dimension', 'low_confidence', 'verifier_boundary_case', 'content_not_admitted',
  'ocr_low_confidence', 'dimension_unavailable', 'student_appeal', 'spot_check',
  'double_grading_disagreement', 'full_score_second_review'
);

-- 当前登录账号：BFF 在每个事务开头 SET LOCAL app.account_id（spec 待人确认 #1）。
-- 没有设置时返回 NULL，所有按账号的策略都不放行。
CREATE FUNCTION platform.current_account_id() RETURNS uuid
  LANGUAGE sql STABLE PARALLEL SAFE
  AS $$ SELECT NULLIF(current_setting('app.account_id', true), '')::uuid $$;
GRANT EXECUTE ON FUNCTION platform.current_account_id() TO starbuddy_bff, starbuddy_learning, starbuddy_review, starbuddy_authz;

-- 带版本名称的格式（契约 VersionedName / ModelVersion）
CREATE DOMAIN platform.versioned_name AS text
  CHECK (VALUE ~ '^[a-z0-9][a-z0-9\-]{0,47}@[A-Za-z0-9][A-Za-z0-9._\-]{0,31}$');
CREATE DOMAIN platform.model_version AS text
  CHECK (VALUE ~ '^(model:[a-z0-9\-]+/[A-Za-z0-9._\-]+@[A-Za-z0-9._\-]+|algo:[A-Za-z0-9._\-]+@[A-Za-z0-9._\-]+)$');
CREATE DOMAIN platform.skill_local_id AS text
  CHECK (VALUE ~ '^[A-Za-z0-9][A-Za-z0-9_.\-]{0,63}$');
CREATE DOMAIN platform.confidence AS numeric(4, 3)
  CHECK (VALUE >= 0 AND VALUE <= 1);
