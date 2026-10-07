-- 0045 platform · 届别以 exam_cohort 为准、届别更正渠道（specs/policy-decisions-v0.9.1/spec.md 第 1 节，PR1）
-- 状态：草稿。只写文件，由人先在副本演练再执行（CLAUDE.md 必停项：迁移、RLS 与权限）。请以单事务执行（psql -1）。只改 platform（ADR 02）。
-- 依赖 0002（账号、档案、绑定、is_linked_guardian）、0014（届别列、expected_cohort、保护触发器）、
-- 0023（platform.review_current_staff）、0035（个人层表清单与删除执行身份）。
-- 要点：
--   1. 届别以存下的 exam_cohort 为准（用户结论 1）；年级改为推算值：platform.current_grade(届别, 时刻) 是 expected_cohort 的逆，
--      9 月 1 日（Asia/Shanghai）切学年，结果不在 7–9 返回 NULL。时刻由调用方给（读点不用 now()，BFF 读点改造在 PR2）；
--   2. grade 列保留，含义改为“建档时年级”，只在建档时推算默认届别；0014 的保护触发器换成只在 INSERT 时核对 ±1，之后改届别不再拿 grade 比对；
--   3. 届别来源列 exam_cohort_source（规则 6）：derived（按建档年级推算）、guardian_report（家长自述）、staff_correction（客服更正）；
--      已有行回填 derived；建档时来源只能是 derived 或 guardian_report（客服更正只经更正函数），标 derived 的届别必须等于推算值；
--      建档之后改届别必须同时改来源列（BEFORE UPDATE OF exam_cohort 守卫）；更正函数（属主 authz）内部照常（同一来源的连续更正）；
--   4. 更正渠道只有一个 SECURITY DEFINER 函数 platform.correct_exam_cohort（属主 authz，授 bff 与 review）：
--      - 经 BFF（调用角色是 starbuddy_bff 的成员）：当前账号须是该学生的有效绑定家长（guardian_student_link，同 0002 is_linked_guardian），
--        来源记 guardian_report；
--      - 经审核台签字连接（调用角色是 starbuddy_review 的成员）：当前账号须是 active staff（同 0023 platform.review_current_staff），
--        来源记 staff_correction；
--      - 调用角色按 SET ROLE 设置的角色判断，没有 SET ROLE 时按登录角色（0001、0041 保证没有角色同时继承 bff 与 review）；
--        两者都是或都不是（例如超级用户、学习服务）一律拒绝。这样被攻破的 BFF 即使把 app.account_id 设成 staff 账号，也走不到客服更正分支；
--      - 每次只能 ±1（相对当前存下的值），更正后的届别在当前时刻对应的年级必须在 7–9；锁住档案行，同一学生的更正串行；
--      - 写一条最小记录 platform.exam_cohort_change（旧值、新值、来源、操作者、时间）。审计总表随 PLT-01 修订（spec“不做”），
--        这张表登记进个人层表清单（delete，随删除请求删除）。
-- 往年方案对 BFF 开放（spec 第 5 节）只改 policy，在 0046。

-- ---------- 1. 年级推算（expected_cohort 的逆） ----------
CREATE FUNCTION platform.current_grade(p_exam_cohort smallint, p_at timestamptz) RETURNS smallint
  LANGUAGE sql STABLE STRICT SET search_path = pg_catalog, platform, pg_temp
  AS $$ SELECT g FROM (SELECT (extract(year FROM (p_at AT TIME ZONE 'Asia/Shanghai') - interval '8 months')::int + 10 - p_exam_cohort)::smallint AS g) x
         WHERE g BETWEEN 7 AND 9 $$;
REVOKE EXECUTE ON FUNCTION platform.current_grade(smallint, timestamptz) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION platform.current_grade(smallint, timestamptz) TO starbuddy_bff, starbuddy_learning, starbuddy_authz;
COMMENT ON FUNCTION platform.current_grade(smallint, timestamptz) IS
  '按届别推算某一时刻的年级（0045）：expected_cohort 的逆，9 月 1 日（Asia/Shanghai）切学年；不在 7–9（未入学或已毕业）返回 NULL。时刻由调用方给';

-- ---------- 2. 年级列的含义、届别来源列 ----------
COMMENT ON COLUMN platform.student_profile.grade IS '建档时年级（0045 起不是事实）：只在建档时推算默认届别；当前年级由 platform.current_grade(exam_cohort, 时刻) 推算';
COMMENT ON COLUMN platform.student_profile.exam_cohort IS '中考届别（中考年份），学生档案的届别真相源（0045）。考情规则按届别读取（policy.exam_scheme.cohort）；更正只经 platform.correct_exam_cohort';
ALTER TABLE platform.student_profile
  ADD COLUMN exam_cohort_source text NOT NULL DEFAULT 'derived'
    CONSTRAINT student_profile_exam_cohort_source CHECK (exam_cohort_source IN ('derived', 'guardian_report', 'staff_correction'));
COMMENT ON COLUMN platform.student_profile.exam_cohort_source IS
  '届别来源（规则 6）：derived 按建档年级推算、guardian_report 家长自述、staff_correction 客服更正；0045 之前的行回填 derived';

-- ---------- 3. 保护触发器：只在建档时核对 ±1 ----------
CREATE OR REPLACE FUNCTION platform.guard_student_cohort() RETURNS trigger LANGUAGE plpgsql
  SET search_path = pg_catalog, platform, pg_temp
  AS $$
DECLARE v_expected smallint := platform.expected_cohort(NEW.grade, coalesce(NEW.created_at, now()));
BEGIN
  IF NEW.exam_cohort IS NULL THEN
    NEW.exam_cohort := v_expected;
  ELSIF abs(NEW.exam_cohort - v_expected) > 1 THEN
    RAISE EXCEPTION '中考届别 % 与建档年级 % 不符（推算为 %）', NEW.exam_cohort, NEW.grade, v_expected USING ERRCODE = 'check_violation';
  END IF;
  -- 建档时来源只能是推算或家长自述；客服更正只经 platform.correct_exam_cohort
  IF NEW.exam_cohort_source NOT IN ('derived', 'guardian_report') THEN
    RAISE EXCEPTION '建档时届别来源只能是 derived 或 guardian_report（客服更正经 platform.correct_exam_cohort）' USING ERRCODE = 'check_violation';
  END IF;
  IF NEW.exam_cohort_source = 'derived' AND NEW.exam_cohort <> v_expected THEN
    RAISE EXCEPTION '标为按建档年级推算（derived）的届别必须等于推算值 %', v_expected USING ERRCODE = 'check_violation';
  END IF;
  RETURN NEW;
END $$;
DROP TRIGGER guard_student_cohort ON platform.student_profile;
CREATE TRIGGER guard_student_cohort BEFORE INSERT ON platform.student_profile
  FOR EACH ROW EXECUTE FUNCTION platform.guard_student_cohort();

-- 建档之后改届别必须同时改来源列（规则 6：值变了，来源要如实）。更正函数（属主 authz；authz 没有成员，0013 断言）内部照常：
-- 同一来源的连续更正（家长两次自述）来源列不变
CREATE FUNCTION platform.guard_student_cohort_update() RETURNS trigger LANGUAGE plpgsql
  SET search_path = pg_catalog, platform, pg_temp
  AS $$
BEGIN
  IF NEW.exam_cohort IS DISTINCT FROM OLD.exam_cohort AND NEW.exam_cohort_source IS NOT DISTINCT FROM OLD.exam_cohort_source
     AND current_user <> 'starbuddy_authz' THEN
    RAISE EXCEPTION '改届别必须同时改届别来源（更正请经 platform.correct_exam_cohort）' USING ERRCODE = 'check_violation';
  END IF;
  RETURN NEW;
END $$;
REVOKE EXECUTE ON FUNCTION platform.guard_student_cohort_update() FROM PUBLIC;
CREATE TRIGGER guard_student_cohort_update BEFORE UPDATE OF exam_cohort ON platform.student_profile
  FOR EACH ROW EXECUTE FUNCTION platform.guard_student_cohort_update();

-- ---------- 4. 届别更正记录（最小记录；审计总表随 PLT-01 修订） ----------
CREATE TABLE platform.exam_cohort_change (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  student_id  uuid NOT NULL REFERENCES platform.student_profile (id) ON DELETE CASCADE,
  old_cohort  smallint NOT NULL CHECK (old_cohort BETWEEN 2015 AND 2100),
  new_cohort  smallint NOT NULL CHECK (new_cohort BETWEEN 2015 AND 2100),
  source      text NOT NULL CHECK (source IN ('guardian_report', 'staff_correction')),
  changed_by  uuid NOT NULL REFERENCES platform.account (id),
  changed_at  timestamptz NOT NULL,
  CONSTRAINT exam_cohort_change_step CHECK (abs(new_cohort - old_cohort) = 1)
);
CREATE INDEX exam_cohort_change_student_idx ON platform.exam_cohort_change (student_id, changed_at);
COMMENT ON TABLE platform.exam_cohort_change IS
  '学生档案届别更正记录（0045）：只由 platform.correct_exam_cohort 写入（属主 authz）；应用角色没有任何权限；随删除请求删除（个人层表清单）';
ALTER TABLE platform.exam_cohort_change ENABLE ROW LEVEL SECURITY;
ALTER TABLE platform.exam_cohort_change FORCE ROW LEVEL SECURITY;
-- 没有任何策略：只有 BYPASSRLS 的函数属主 authz 与删除执行身份 eraser 能读写
REVOKE ALL ON platform.exam_cohort_change FROM PUBLIC;
GRANT SELECT, INSERT ON platform.exam_cohort_change TO starbuddy_authz;
GRANT DELETE ON platform.exam_cohort_change TO starbuddy_eraser;
GRANT SELECT (student_id) ON platform.exam_cohort_change TO starbuddy_eraser;
INSERT INTO platform.personal_table_registry (nspname, relname, key_path, student_predicate, strategy, exempt_reason, delete_order,
                                              erase_scope, export_topic, export_columns, export_filter)
VALUES ('platform', 'exam_cohort_change', 'student_id', 'student_id = $1', 'delete', NULL, 65, 'facts', NULL, NULL, NULL);

-- ---------- 5. 更正函数 ----------
-- 函数属主 authz：锁行并改届别与来源两列
GRANT UPDATE (exam_cohort, exam_cohort_source) ON platform.student_profile TO starbuddy_authz;
CREATE FUNCTION platform.correct_exam_cohort(p_student uuid, p_new_cohort smallint) RETURNS smallint
  LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog, platform, pg_temp
  AS $$
DECLARE
  -- 调用角色：SET ROLE 设置的角色；没有 SET ROLE 时是登录角色（SECURITY DEFINER 改的是 current_user，不改这两者）
  v_caller text := CASE WHEN current_setting('role') = 'none' THEN session_user::text ELSE current_setting('role') END;
  v_via_bff boolean := pg_has_role(v_caller, 'starbuddy_bff', 'MEMBER');
  v_via_review boolean := pg_has_role(v_caller, 'starbuddy_review', 'MEMBER');
  v_actor uuid;
  v_source text;
  v_old smallint;
  v_erased timestamptz;
  v_at timestamptz := clock_timestamp();
BEGIN
  IF v_via_bff AND NOT v_via_review THEN
    -- 家长端（经 BFF）：按档案授权（规则 5）；不是有效绑定家长与学生不存在同样拒绝，不暴露存在性
    IF p_student IS NULL OR NOT platform.is_linked_guardian(p_student) THEN
      RAISE EXCEPTION 'not allowed' USING ERRCODE = 'insufficient_privilege';
    END IF;
    v_actor := platform.current_account_id();
    v_source := 'guardian_report';
  ELSIF v_via_review AND NOT v_via_bff THEN
    -- 客服（审核台签字连接）：active staff
    v_actor := platform.review_current_staff();
    v_source := 'staff_correction';
  ELSE
    RAISE EXCEPTION 'not allowed' USING ERRCODE = 'insufficient_privilege';
  END IF;
  IF p_new_cohort IS NULL THEN
    RAISE EXCEPTION '更正后的届别不能为空' USING ERRCODE = 'invalid_parameter_value';
  END IF;
  -- 锁住档案行：同一学生的更正（以及别的改档案的事务）串行，旧值取提交后的当前值
  SELECT s.exam_cohort, s.erased_at INTO v_old, v_erased FROM platform.student_profile s WHERE s.id = p_student FOR UPDATE;
  IF v_old IS NULL OR v_erased IS NOT NULL THEN
    RAISE EXCEPTION '学生档案不存在或已删除' USING ERRCODE = 'no_data_found';
  END IF;
  IF abs(p_new_cohort - v_old) <> 1 THEN
    RAISE EXCEPTION '届别每次只能更正 ±1（当前 %）', v_old USING ERRCODE = 'invalid_parameter_value';
  END IF;
  IF platform.current_grade(p_new_cohort, v_at) IS NULL THEN
    RAISE EXCEPTION '更正后的届别 % 在当前对应的年级不在 7–9', p_new_cohort USING ERRCODE = 'invalid_parameter_value';
  END IF;
  UPDATE platform.student_profile SET exam_cohort = p_new_cohort, exam_cohort_source = v_source WHERE id = p_student;
  INSERT INTO platform.exam_cohort_change (student_id, old_cohort, new_cohort, source, changed_by, changed_at)
  VALUES (p_student, v_old, p_new_cohort, v_source, v_actor, v_at);
  RETURN p_new_cohort;
END $$;
ALTER FUNCTION platform.correct_exam_cohort(uuid, smallint) OWNER TO starbuddy_authz;
REVOKE EXECUTE ON FUNCTION platform.correct_exam_cohort(uuid, smallint) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION platform.correct_exam_cohort(uuid, smallint) TO starbuddy_bff, starbuddy_review;
COMMENT ON FUNCTION platform.correct_exam_cohort(uuid, smallint) IS
  '更正学生档案届别（0045）：经 BFF 时须是有效绑定家长（来源 guardian_report），经审核台签字连接时须是 active staff（来源 staff_correction）；'
  '每次 ±1，更正后当前年级须在 7–9；锁档案行；写 platform.exam_cohort_change';
