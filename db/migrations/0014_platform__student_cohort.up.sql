-- 0014 platform · 学生档案加中考届别（specs/policy-schema-v0.9/spec.md 设计要点 1）
-- 状态：草稿 v0.9。只写文件，由人执行（CLAUDE.md 必停项；数据迁移先在副本演练）。按 ADR 02 第 2 条与 0013 分开。
-- 规则一律按学生的届别读，不按当前年份读（平台规划 v1.3 §6.1）。
-- 推算：学年以 9 月 1 日（Asia/Shanghai）切换；届别 = 学年起始年 + 1 + (9 − 年级)。
-- 年级是建档时的年级（没有任何应用角色能改 student_profile），所以按建档时间 created_at 推算，不按当前时间。
-- 建档时未给届别就按推算值填；给了则与推算值相差不能超过 1（留级、跳级由家长更正，spec 待人确认 #6）。
-- 请以单事务执行（psql -1）。

CREATE FUNCTION platform.expected_cohort(p_grade smallint, p_at timestamptz) RETURNS smallint
  LANGUAGE sql STABLE SET search_path = pg_catalog, platform, pg_temp
  AS $$ SELECT (extract(year FROM (p_at AT TIME ZONE 'Asia/Shanghai') - interval '8 months')::int + 1 + (9 - p_grade))::smallint $$;
REVOKE EXECUTE ON FUNCTION platform.expected_cohort(smallint, timestamptz) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION platform.expected_cohort(smallint, timestamptz) TO starbuddy_bff, starbuddy_learning, starbuddy_authz;

ALTER TABLE platform.student_profile ADD COLUMN exam_cohort smallint CHECK (exam_cohort BETWEEN 2015 AND 2100);
COMMENT ON COLUMN platform.student_profile.exam_cohort IS '中考届别（中考年份）。考情规则按届别读取（policy.exam_scheme.cohort）';

-- 已有行回填（先在副本演练），然后改为必填
UPDATE platform.student_profile SET exam_cohort = platform.expected_cohort(grade, created_at) WHERE exam_cohort IS NULL;
ALTER TABLE platform.student_profile ALTER COLUMN exam_cohort SET NOT NULL;

CREATE FUNCTION platform.guard_student_cohort() RETURNS trigger LANGUAGE plpgsql
  SET search_path = pg_catalog, platform, pg_temp
  AS $$
DECLARE v_expected smallint := platform.expected_cohort(NEW.grade, coalesce(NEW.created_at, now()));
BEGIN
  IF NEW.exam_cohort IS NULL THEN
    NEW.exam_cohort := v_expected;
  ELSIF abs(NEW.exam_cohort - v_expected) > 1
        AND (TG_OP = 'INSERT' OR NEW.exam_cohort IS DISTINCT FROM OLD.exam_cohort OR NEW.grade IS DISTINCT FROM OLD.grade) THEN
    RAISE EXCEPTION '中考届别 % 与年级 % 不符（推算为 %）', NEW.exam_cohort, NEW.grade, v_expected USING ERRCODE = 'check_violation';
  END IF;
  RETURN NEW;
END $$;
REVOKE EXECUTE ON FUNCTION platform.guard_student_cohort() FROM PUBLIC;
CREATE TRIGGER guard_student_cohort BEFORE INSERT OR UPDATE OF grade, exam_cohort ON platform.student_profile
  FOR EACH ROW EXECUTE FUNCTION platform.guard_student_cohort();
