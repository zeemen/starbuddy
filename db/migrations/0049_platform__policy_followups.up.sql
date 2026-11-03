-- 0049 platform · 考情 v0.9 待人确认事项按默认处理：platform 部分（specs/policy-decisions-v0.9.1/spec.md“PR3”，lessons“待人确认”#1、#2/#11、#12）
-- 状态：草稿。只写文件，由人先在副本演练再执行（CLAUDE.md 必停项：迁移、RLS 与权限、计量与权益、画像可见范围）。请以单事务执行（psql -1）。
-- 只改 platform（ADR 02）；policy 部分（来源清单的输入核对、url_host、来源文件守卫的共享锁）在 0050。
-- 依赖 0009（计量函数）、0027（diagnosis_session）、0035（个人层表清单与导出）、0045（correct_exam_cohort、exam_cohort_change）、
-- 0048（platform.staff_permission）。
-- 要点：
--   1. #1 客服更正收口：platform.correct_exam_cohort 的客服分支（审核台签字连接）除 active staff 外还要 staff 权限 cohort_correction
--      （platform.staff_permission，与 policy_source_admin 同表，只由 DBA 授予；应用角色不能写权限表）。顺序是先查权限、后查学生：
--      没有权限、不是 active staff、学生不存在、档案已删除一律同一个错误（42501 'not allowed'，与家长分支相同），没有权限的人
--      探测不到学生是否存在。家长分支不变；
--   2. #2/#11 家长导出（0035 platform.export_student_data 按清单导出）：profile 主题加 exam_cohort、exam_cohort_source；
--      新增主题 cohort_changes（platform.exam_cohort_change）：该学生的全部更正（家长与客服做的都算），只导旧值、新值、来源、时间，
--      不导操作者账号（changed_by）。更正记录随删除请求删除（0045 已登记 delete），删除覆盖不变；
--   3. #12 诊断会话：
--      - start_grade：建立时用于选题的年级（会话参数，不是学生年级的事实；事实仍是届别）。学习服务续做时按会话建立时刻、当前届别重算，
--        与它不同（中途届别被改）回 409 SESSION_INCONSISTENT，并在问题体里带这个会话的 ID，前端据此“重新开始”（放弃后再开始）。
--        0049 之前建立的会话为空。学习服务对这张表有整表 INSERT，没有这一列的 UPDATE（建立后不能改）；
--      - abandon_reason：放弃的原因，目前只有 grade_changed（学习服务放弃时核对到建立时刻的年级变了才写；只能与 abandoned 同时写，
--        之后不能改——0027 的守卫已让结束的会话不可改）；
--      - 写入守卫 platform.diagnosis_session_write_guard（BEFORE INSERT OR UPDATE，退回额度安全审查 B1）：
--        · 新建只能 status = active、abandon_reason 为空、finished_at 为空（选择触发器而不是把学习服务的整表 INSERT 收成列级：
--          列级授权要改 0027 的授权并动快照里每一列，触发器只加一条规则，超级用户的直接写入也拦）；
--        · 写入 abandon_reason = grade_changed 时库里自己核实届别确实变过：start_grade 不为空，且按当前届别重算会话建立时刻的年级
--          （platform.current_grade(exam_cohort, created_at)）与 start_grade 不同。选这个判据而不是“created_at 之后有 exam_cohort_change
--          记录”：更正记录可能来回改（+1 再 −1，年级没变），迁移执行者直接改届别也不留更正记录；与学习服务判定 409 的规则同一个判据。
--          0049 之前建立的会话（start_grade 为空）不能标 grade_changed，按普通放弃处理（不退）；
--   4. 因届别变化而放弃的会话退回额度（用户 2026-10-08 确认，即使已答过题；其余放弃规则不变）：
--      platform.meter_refund_grade_changed(usage_id)（属主 authz，只授 BFF）：只认学生本人（同 meter_settle 的 meter_authorize_usage），
--      且只退诊断预留（退回额度安全审查 B1）：预留的来源方法是 diagnose、所属额度桶是 diagnosis_full，挂在同一学生、同一幂等键
--      （BFF 开始诊断时预留与会话用同一个小写 UUID 键）的诊断会话上，会话已放弃、abandon_reason = grade_changed，且退回时再核一次
--      届别确实变过（同上面的守卫）；
--      - 预留还没结算（reserved）→ 释放（meter_release，原因 grade_changed；释放不受下面的次数上限）；
--      - 已结算 → 写一条 adjust 流水把余量加回额度桶（actor system，原因 grade_changed_refund，带这笔预留的幂等键以便回溯与去重），
--        granted 同步加回并核对计数（meter_check_entitlement）。不用 correction：0008 规定更正只由人工（ledger_actor_by_kind），
--        这是按库里记录自动判定的退回，记为系统调整；
--      - 余量（S3）= settle 流水的数量减去这条 settle 已有的更正（与 guard_ledger_insert 的累计规则一致）；余量 ≤ 0 不退；
--      - 上限（S1）：每个学生每个额度桶（即每个额度周期）最多退 1 次，超出返回 conflict / limit（BFF 不退，放弃照常成功）；
--      - 幂等：同一笔预留只退一次（锁住预留行后按幂等键查已有的退回流水），重复调用返回 idempotent；
--      - 已释放、已过期、结算数为 0 的不退（nothing_to_refund，没有扣过）。

-- ---------- 1. staff 权限 cohort_correction 与客服更正（#1） ----------
ALTER TABLE platform.staff_permission DROP CONSTRAINT staff_permission_permission_check;
ALTER TABLE platform.staff_permission ADD CONSTRAINT staff_permission_permission_check
  CHECK (permission IN ('policy_source_admin', 'cohort_correction'));
COMMENT ON TABLE platform.staff_permission IS
  'staff 的附加权限（0048）：只由迁移或 DBA 授予与收回；审核台签字身份只读自己的权限。policy_source_admin（维护官方来源清单，0048）、'
  'cohort_correction（客服更正学生届别，0049）';

CREATE OR REPLACE FUNCTION platform.correct_exam_cohort(p_student uuid, p_new_cohort smallint) RETURNS smallint
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
    -- 客服（审核台签字连接）：active staff 且有 staff 权限 cohort_correction（0049，只由 DBA 授予）。
    -- 先查权限、后查学生：没有权限、不是 active staff、学生不存在、档案已删除一律同一个错误（与家长分支相同），不暴露学生是否存在
    v_actor := platform.current_account_id();
    IF p_student IS NULL OR v_actor IS NULL
       OR NOT EXISTS (SELECT 1 FROM platform.account a JOIN platform.staff_permission p ON p.staff_id = a.id
                       WHERE a.id = v_actor AND a.kind = 'staff' AND a.status = 'active' AND p.permission = 'cohort_correction')
       OR NOT EXISTS (SELECT 1 FROM platform.student_profile s WHERE s.id = p_student AND s.erased_at IS NULL) THEN
      RAISE EXCEPTION 'not allowed' USING ERRCODE = 'insufficient_privilege';
    END IF;
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
COMMENT ON FUNCTION platform.correct_exam_cohort(uuid, smallint) IS
  '更正学生档案届别（0045，0049 收口）：经 BFF 时须是有效绑定家长（来源 guardian_report），经审核台签字连接时须是 active staff 且有 staff 权限 '
  'cohort_correction（来源 staff_correction）；无权与学生不存在同一个 42501（先查权限、后查学生）；每次 ±1，更正后当前年级须在 7–9；'
  '锁档案行；写 platform.exam_cohort_change';

-- ---------- 2. 家长导出：届别、来源与更正记录（#2/#11） ----------
UPDATE platform.personal_table_registry
   SET export_columns = ARRAY['grade', 'region', 'birth_year_month', 'created_at', 'exam_cohort', 'exam_cohort_source']
 WHERE nspname = 'platform' AND relname = 'student_profile';
UPDATE platform.personal_table_registry
   SET export_topic = 'cohort_changes', export_columns = ARRAY['old_cohort', 'new_cohort', 'source', 'changed_at']
 WHERE nspname = 'platform' AND relname = 'exam_cohort_change';
DO $$
BEGIN
  IF (SELECT count(*) FROM platform.personal_table_registry
       WHERE (nspname, relname) IN (('platform', 'student_profile'), ('platform', 'exam_cohort_change')) AND export_topic IS NOT NULL) <> 2 THEN
    RAISE EXCEPTION '0049 中止：个人层表清单里没有学生档案或届别更正记录的登记（0035、0045 未执行？）' USING ERRCODE = 'object_not_in_prerequisite_state';
  END IF;
END $$;

-- ---------- 3. 诊断会话的建立年级与放弃原因（#12） ----------
ALTER TABLE platform.diagnosis_session
  ADD COLUMN start_grade smallint CONSTRAINT diagnosis_session_start_grade_check CHECK (start_grade BETWEEN 7 AND 9),
  ADD COLUMN abandon_reason text CONSTRAINT diagnosis_session_abandon_reason_check CHECK (abandon_reason IN ('grade_changed')),
  ADD CONSTRAINT diagnosis_session_abandon_reason_status CHECK (abandon_reason IS NULL OR status = 'abandoned');
COMMENT ON COLUMN platform.diagnosis_session.start_grade IS
  '会话建立时用于选题的年级（0049）：platform.current_grade(届别, created_at) 在建立时的值，是会话参数，不是学生年级的事实（事实是届别）。'
  '续做时按当前届别重算，与它不同（中途届别被改）学习服务回 409；0049 之前建立的会话为空。学习服务只在建立时写入';
COMMENT ON COLUMN platform.diagnosis_session.abandon_reason IS
  '放弃原因（0049）：grade_changed = 会话中途届别被改、建立时刻的年级变了（学习服务放弃时核对后写入）；这类放弃退回额度'
  '（platform.meter_refund_grade_changed）。其余放弃为空';
GRANT UPDATE (abandon_reason) ON platform.diagnosis_session TO starbuddy_learning;
-- 退回额度的函数（属主 authz）按预留找会话并再核一次届别
GRANT SELECT (usage_id, student_id, status, abandon_reason, idempotency_key, start_grade, created_at) ON platform.diagnosis_session TO starbuddy_authz;

-- 届别确实变过：按学生当前的届别重算会话建立时刻的年级，与建立时记下的选题年级不同（建立时一定在 7–9，所以重算为空也算变过）。
-- start_grade 为空（0049 之前的会话）一律不算。写入守卫与退回函数共用这一个判据（与学习服务判定 409 的规则相同）
CREATE FUNCTION platform.diagnosis_grade_changed(p_student uuid, p_created_at timestamptz, p_start_grade smallint) RETURNS boolean
  LANGUAGE sql STABLE SET search_path = pg_catalog, platform, pg_temp
  AS $$ SELECT p_start_grade IS NOT NULL
               AND platform.current_grade((SELECT s.exam_cohort FROM platform.student_profile s WHERE s.id = p_student), p_created_at)
                   IS DISTINCT FROM p_start_grade $$;
REVOKE EXECUTE ON FUNCTION platform.diagnosis_grade_changed(uuid, timestamptz, smallint) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION platform.diagnosis_grade_changed(uuid, timestamptz, smallint) TO starbuddy_learning, starbuddy_authz;
COMMENT ON FUNCTION platform.diagnosis_grade_changed(uuid, timestamptz, smallint) IS
  '诊断会话中途届别是否确实变过（0049）：start_grade 不为空，且按当前届别重算 created_at 时刻的年级与它不同。写入守卫与退回额度共用';

-- 写入守卫（退回额度安全审查 B1）：新建只能是进行中、没有放弃原因与结束时间；写 grade_changed 时库里自己核实届别确实变过
CREATE FUNCTION platform.diagnosis_session_write_guard() RETURNS trigger
  LANGUAGE plpgsql SET search_path = pg_catalog, platform, pg_temp
  AS $$
BEGIN
  IF TG_OP = 'INSERT' AND (NEW.status <> 'active' OR NEW.abandon_reason IS NOT NULL OR NEW.finished_at IS NOT NULL) THEN
    RAISE EXCEPTION '诊断会话只能以进行中新建（不能带放弃原因或结束时间）' USING ERRCODE = 'check_violation';
  END IF;
  IF NEW.abandon_reason = 'grade_changed' AND (TG_OP = 'INSERT' OR OLD.abandon_reason IS DISTINCT FROM NEW.abandon_reason)
     AND NOT platform.diagnosis_grade_changed(NEW.student_id, NEW.created_at, NEW.start_grade) THEN
    RAISE EXCEPTION '放弃原因 grade_changed 与库里的届别不符（建立时刻的年级没有变过，或会话没有记下选题年级）' USING ERRCODE = 'check_violation';
  END IF;
  RETURN NEW;
END $$;
REVOKE EXECUTE ON FUNCTION platform.diagnosis_session_write_guard() FROM PUBLIC;
CREATE TRIGGER diagnosis_session_write_guard BEFORE INSERT OR UPDATE ON platform.diagnosis_session
  FOR EACH ROW EXECUTE FUNCTION platform.diagnosis_session_write_guard();

-- ---------- 4. 因届别变化放弃的诊断退回额度 ----------
CREATE FUNCTION platform.meter_refund_grade_changed(p_usage uuid) RETURNS jsonb
  LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, platform, pg_temp
  AS $$
DECLARE
  v_now timestamptz := platform.meter_now();
  r platform.usage_reservation;
  v_settle record;
  v_left numeric;
BEGIN
  PERFORM platform.meter_authorize_usage(p_usage);
  r := platform.meter_lock_reservation(p_usage);
  -- 只退诊断预留：来源方法 diagnose、额度桶 diagnosis_full，挂在同一学生、同一幂等键的已放弃会话上，放弃原因 grade_changed，
  -- 且此刻库里仍核实得到届别确实变过
  IF r.source_method IS DISTINCT FROM 'diagnose'
     OR NOT EXISTS (SELECT 1 FROM platform.entitlement e WHERE e.id = r.entitlement_id AND e.resource_type = 'diagnosis_full')
     OR NOT EXISTS (SELECT 1 FROM platform.diagnosis_session s
                     WHERE s.usage_id = p_usage AND s.student_id = r.student_id AND s.idempotency_key::text = r.idempotency_key
                       AND s.status = 'abandoned' AND s.abandon_reason = 'grade_changed'
                       AND platform.diagnosis_grade_changed(s.student_id, s.created_at, s.start_grade)) THEN
    RETURN jsonb_build_object('outcome', 'conflict', 'usage_id', r.usage_id, 'state', r.state, 'reason', 'not_refundable');
  END IF;
  IF r.state = 'reserved' THEN
    RETURN platform.meter_release(p_usage, 'grade_changed');
  ELSIF r.state <> 'settled' OR r.settled_qty = 0 THEN
    RETURN jsonb_build_object('outcome', 'conflict', 'usage_id', r.usage_id, 'state', r.state, 'reason', 'nothing_to_refund');
  END IF;
  IF EXISTS (SELECT 1 FROM platform.usage_ledger l
              WHERE l.entitlement_id = r.entitlement_id AND l.kind = 'adjust' AND l.reason_code = 'grade_changed_refund'
                AND l.source_method = r.source_method AND l.source_idempotency_key = r.idempotency_key) THEN
    RETURN jsonb_build_object('outcome', 'refunded', 'usage_id', r.usage_id, 'state', r.state, 'idempotent', true);
  END IF;
  -- 余量（S3）：settle 流水减去这条流水已有的更正（与 guard_ledger_insert 的累计规则一致）
  SELECT l.entry_id, l.occurred_at, l.quantity INTO v_settle FROM platform.usage_ledger l
   WHERE l.usage_id = p_usage AND l.entitlement_id = r.entitlement_id AND l.kind = 'settle';
  v_left := v_settle.quantity + coalesce((SELECT sum(c.quantity) FROM platform.usage_ledger c
                                          WHERE c.corrects_entry_id = v_settle.entry_id AND c.corrects_occurred_at = v_settle.occurred_at), 0);
  IF v_left IS NULL OR v_left <= 0 THEN
    RETURN jsonb_build_object('outcome', 'conflict', 'usage_id', r.usage_id, 'state', r.state, 'reason', 'nothing_to_refund');
  END IF;
  -- 上限（S1）：每个学生每个额度桶（每个额度周期）最多退 1 次
  IF EXISTS (SELECT 1 FROM platform.usage_ledger l
              WHERE l.entitlement_id = r.entitlement_id AND l.student_id = r.student_id AND l.kind = 'adjust'
                AND l.reason_code = 'grade_changed_refund') THEN
    RETURN jsonb_build_object('outcome', 'conflict', 'usage_id', r.usage_id, 'state', r.state, 'reason', 'limit');
  END IF;
  INSERT INTO platform.usage_ledger (occurred_at, entitlement_id, student_id, usage_id, kind, quantity, actor, reason_code,
    source_method, source_idempotency_key)
  VALUES (v_now, r.entitlement_id, r.student_id, NULL, 'adjust', v_left, 'system', 'grade_changed_refund',
          r.source_method, r.idempotency_key);
  UPDATE platform.entitlement SET granted = granted + v_left WHERE id = r.entitlement_id;
  PERFORM platform.meter_check_entitlement(r.entitlement_id);
  RETURN jsonb_build_object('outcome', 'refunded', 'usage_id', r.usage_id, 'state', r.state, 'quantity', v_left, 'idempotent', false);
END $$;
ALTER FUNCTION platform.meter_refund_grade_changed(uuid) OWNER TO starbuddy_authz;
REVOKE EXECUTE ON FUNCTION platform.meter_refund_grade_changed(uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION platform.meter_refund_grade_changed(uuid) TO starbuddy_bff;
COMMENT ON FUNCTION platform.meter_refund_grade_changed(uuid) IS
  '因届别变化放弃的诊断退回额度（0049）：只认学生本人、只退诊断预留（diagnose / diagnosis_full，同一学生同一幂等键的会话，'
  'abandon_reason = grade_changed 且库里再核实届别变过）；未结算的释放，已结算的写一条 adjust 流水（actor system，原因 grade_changed_refund）'
  '加回余量（settle 减去已有更正）；每个学生每个额度桶最多退 1 次；同一笔预留只退一次（幂等）';
