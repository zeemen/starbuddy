-- 0034 insight · 考前预测冻结与对账卡（specs/sie-03-dispute-reconcile/spec.md 设计要点 2，待人确认 #1、#6 按默认）
-- 状态：草稿。只写文件，由人先在副本演练再执行（CLAUDE.md 必停项：迁移、RLS 与权限、判断模板与对账文案）。依赖 0002、0009（scheduler）、0010、0022。
-- 依据：SIE V1.1 第五章考试预测与对账（录入范围与日期后估算 80% 区间、考试当天零点冻结、成绩后对账、偏差四方向）、第九章 exam_prediction；
--       契约 packages/contracts/openapi/insight-service.v0.9.yaml 的 insight.predict / insight.reconcile（枚举与字段以它为准）。
-- 做法：
--   - 考试安排仍写 insight.exam_record（0022 已有范围节点、日期、满分、录入人，不重复加列）：raw_score 改可空，“还没考”的记录 raw_score 为空、
--     必须带范围节点；满分沿用 0022 的 (0, 300]（政策库对照由洞察服务做）；同一学生同一学科、类型、日期的当前考试安排只有一条
--     （学生与家长重复录入同一场考试时复用已有安排），所以每场考试只有一条预测；
--   - 成绩行加 plan_record_id 外键指向安排行（insight.submit_exam_score 录入）；对账按这个外键匹配成绩与预测；
--     建档读取 insight.onboarding_view 只列有成绩的记录（考试安排不混进成绩列表）；
--   - 预测 insight.exam_prediction：只由洞察服务写（推断，来源 inferred，带算法版本与证据 ID，规则 6）；对应一条 exam 类判断（claim_id 外键）；
--     区间级别固定 0.80；考试当天零点（上海时区）起不能新建、不能改；starbuddy_scheduler 调 insight.freeze_due_predictions 写 frozen_at，
--     冻结后任何修改与删除都拒绝（只有 0035 的删除请求执行时能删）；
--   - 对账 insight.exam_reconciliation：一条预测一张卡，只追加；区间内外由库按成绩计算；偏差方向为契约四值数组（去重、区间外至少 1 个、区间内为空），
--     带偏差节点、平台调整编码、来源 inferred、算法版本与证据 ID；每句只存判断 ID 与模板键（规则 11），所引判断须模板已审核、未过期、
--     成立或假设，且可见范围覆盖这张卡的读者；
--   - 学生、家长经洞察服务看区间与对账结论（待人确认 #6），BFF 对这两张表没有表权限。
-- 请以单事务执行（psql -1）。

-- ---------- 考试安排与成绩的关联 ----------
ALTER TABLE insight.exam_record ALTER COLUMN raw_score DROP NOT NULL;
ALTER TABLE insight.exam_record ADD COLUMN plan_record_id uuid REFERENCES insight.exam_record (id);
ALTER TABLE insight.exam_record ADD CONSTRAINT exam_record_plan CHECK (raw_score IS NOT NULL OR cardinality(scope_node_ids) >= 1);
ALTER TABLE insight.exam_record ADD CONSTRAINT exam_record_plan_link CHECK (plan_record_id IS NULL OR raw_score IS NOT NULL);
CREATE UNIQUE INDEX exam_record_plan_uq ON insight.exam_record (student_id, subject, exam_type, exam_date)
  WHERE raw_score IS NULL AND superseded_at IS NULL;
CREATE INDEX exam_record_plan_link_idx ON insight.exam_record (plan_record_id) WHERE plan_record_id IS NOT NULL;
COMMENT ON COLUMN insight.exam_record.raw_score IS '成绩；为空表示考试安排（0034：考前预测用，必须带范围节点）';
COMMENT ON COLUMN insight.exam_record.plan_record_id IS '成绩对应的考试安排（0034）：对账按它匹配预测';

-- KGE 节点 ID（与 0027 diagnosis_step.node_id 同一格式），1..p_max 个、不重复
CREATE FUNCTION insight.valid_node_ids(p text[], p_min integer, p_max integer) RETURNS boolean LANGUAGE sql IMMUTABLE SET search_path = pg_catalog
  AS $$ SELECT p IS NOT NULL AND cardinality(p) BETWEEN p_min AND p_max AND array_position(p, NULL) IS NULL
           AND NOT EXISTS (SELECT 1 FROM unnest(p) x WHERE x !~ '^[A-Z][A-Z0-9]{1,9}(\.[A-Z0-9][A-Z0-9_]{0,31}){1,4}$')
           AND (SELECT count(DISTINCT x) FROM unnest(p) x) = cardinality(p) $$;
-- 编码数组：都在给定集合里、不重复
CREATE FUNCTION insight.valid_code_set(p text[], p_allowed text[]) RETURNS boolean LANGUAGE sql IMMUTABLE SET search_path = pg_catalog
  AS $$ SELECT p IS NOT NULL AND array_position(p, NULL) IS NULL AND p <@ p_allowed
           AND (SELECT count(DISTINCT x) FROM unnest(p) x) = cardinality(p) $$;
REVOKE EXECUTE ON FUNCTION insight.valid_node_ids(text[], integer, integer), insight.valid_code_set(text[], text[]) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION insight.valid_node_ids(text[], integer, integer), insight.valid_code_set(text[], text[])
  TO starbuddy_authz, starbuddy_insight;

-- 学生本人或已绑定家长录入考试安排：日期从今天（上海）起一年内、满分 1–300 的整数；同一场考试已有当前安排时复用（返回原安排）。
-- 与 submit_exam_record 共用每个录入方 24 小时 50 条的上限
CREATE FUNCTION insight.submit_exam_plan(p_student uuid, p_subject platform.subject, p_exam_type text, p_exam_date date,
                                         p_full_score numeric, p_scope_node_ids text[]) RETURNS uuid
  LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog, insight, platform, pg_temp
  AS $$
DECLARE
  v_role text := insight.onboarding_role(p_student);
  v_today date := (now() AT TIME ZONE 'Asia/Shanghai')::date;
  v_id uuid;
BEGIN
  IF v_role IS NULL THEN
    RAISE EXCEPTION 'not allowed' USING ERRCODE = 'insufficient_privilege';
  END IF;
  IF p_subject IS NULL OR p_exam_type IS NULL OR p_exam_date IS NULL OR p_exam_date < v_today OR p_exam_date > v_today + 366
     OR p_full_score IS NULL OR p_full_score NOT BETWEEN 1 AND 300 OR p_full_score % 1 <> 0
     OR NOT insight.valid_node_ids(p_scope_node_ids, 1, 64) THEN
    RAISE EXCEPTION '考试日期、满分或范围节点不合要求' USING ERRCODE = 'invalid_parameter_value';
  END IF;
  PERFORM pg_advisory_xact_lock(hashtextextended('insight.exam_plan:' || p_student::text, 0));
  SELECT e.id INTO v_id FROM insight.exam_record e
   WHERE e.student_id = p_student AND e.subject = p_subject AND e.exam_type = p_exam_type AND e.exam_date = p_exam_date
     AND e.raw_score IS NULL AND e.superseded_at IS NULL;
  IF FOUND THEN
    RETURN v_id;
  END IF;
  PERFORM pg_advisory_xact_lock(hashtextextended('insight.exam_record:' || p_student::text || ':' || v_role, 0));
  IF (SELECT count(*) FROM insight.exam_record e WHERE e.student_id = p_student AND e.entered_by_role = v_role
         AND e.created_at > now() - interval '24 hours') >= 50 THEN
    RAISE EXCEPTION '考试记录写入过多' USING ERRCODE = 'program_limit_exceeded';
  END IF;
  INSERT INTO insight.exam_record (student_id, subject, exam_type, exam_date, full_score, raw_score, scope_node_ids,
                                   entered_by_role, entered_by_account_id, source_type)
  VALUES (p_student, p_subject, p_exam_type, p_exam_date, p_full_score, NULL, p_scope_node_ids,
          v_role, platform.current_account_id(), v_role::insight.source_type)
  RETURNING id INTO v_id;
  RETURN v_id;
END $$;

-- 成绩：按考试安排录入（复制安排的学科、类型、日期、满分与范围，挂 plan_record_id）；学生本人或已绑定家长，各录各的（规则 6）
CREATE FUNCTION insight.submit_exam_score(p_student uuid, p_plan uuid, p_raw_score numeric) RETURNS uuid
  LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog, insight, platform, pg_temp
  AS $$
DECLARE
  v_role text := insight.onboarding_role(p_student);
  p insight.exam_record;
  v_id uuid;
BEGIN
  IF v_role IS NULL THEN
    RAISE EXCEPTION 'not allowed' USING ERRCODE = 'insufficient_privilege';
  END IF;
  SELECT * INTO p FROM insight.exam_record e WHERE e.id = p_plan AND e.student_id = p_student AND e.raw_score IS NULL AND e.superseded_at IS NULL;
  IF NOT FOUND OR p_raw_score IS NULL OR p_raw_score < 0 OR p_raw_score > p.full_score THEN
    RAISE EXCEPTION '考试安排不存在或成绩不合要求' USING ERRCODE = 'invalid_parameter_value';
  END IF;
  PERFORM pg_advisory_xact_lock(hashtextextended('insight.exam_record:' || p_student::text || ':' || v_role, 0));
  IF (SELECT count(*) FROM insight.exam_record e WHERE e.student_id = p_student AND e.entered_by_role = v_role
         AND e.created_at > now() - interval '24 hours') >= 50 THEN
    RAISE EXCEPTION '考试记录写入过多' USING ERRCODE = 'program_limit_exceeded';
  END IF;
  INSERT INTO insight.exam_record (student_id, subject, exam_type, exam_date, full_score, raw_score, scope_node_ids, plan_record_id,
                                   entered_by_role, entered_by_account_id, source_type)
  VALUES (p_student, p.subject, p.exam_type, p.exam_date, p.full_score, p_raw_score, p.scope_node_ids, p.id,
          v_role, platform.current_account_id(), v_role::insight.source_type)
  RETURNING id INTO v_id;
  RETURN v_id;
END $$;
ALTER FUNCTION insight.submit_exam_plan(uuid, platform.subject, text, date, numeric, text[]) OWNER TO starbuddy_authz;
ALTER FUNCTION insight.submit_exam_score(uuid, uuid, numeric) OWNER TO starbuddy_authz;
REVOKE EXECUTE ON FUNCTION insight.submit_exam_plan(uuid, platform.subject, text, date, numeric, text[]),
  insight.submit_exam_score(uuid, uuid, numeric) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION insight.submit_exam_plan(uuid, platform.subject, text, date, numeric, text[]),
  insight.submit_exam_score(uuid, uuid, numeric) TO starbuddy_bff;

-- 建档读取（0022）：成绩列表只列有成绩的记录，考试安排不混进来（其余与 0022 相同）
CREATE OR REPLACE FUNCTION insight.onboarding_view(p_student uuid) RETURNS jsonb
  LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = pg_catalog, insight, platform, pg_temp
  AS $$
DECLARE
  v_role text := insight.onboarding_role(p_student);
  v_me uuid := platform.current_account_id();
BEGIN
  IF v_role IS NULL THEN
    RETURN NULL;
  END IF;
  RETURN jsonb_build_object(
    'role', v_role,
    'reports', CASE v_role
      WHEN 'self' THEN (SELECT coalesce(jsonb_object_agg(r.field_id, r.value), '{}') FROM insight.self_report r
                         WHERE r.student_id = p_student AND r.superseded_at IS NULL)
      ELSE (SELECT coalesce(jsonb_object_agg(r.field_id, r.value), '{}') FROM insight.guardian_report r
             WHERE r.student_id = p_student AND r.guardian_account_id = v_me AND r.superseded_at IS NULL) END,
    'exams', (SELECT coalesce(jsonb_agg(jsonb_strip_nulls(jsonb_build_object(
                'id', e.id, 'subject', e.subject, 'exam_type', e.exam_type, 'exam_date', e.exam_date,
                'full_score', e.full_score, 'raw_score', e.raw_score, 'scope_note', e.scope_note, 'entered_by_role', e.entered_by_role,
                'rank_band', CASE WHEN v_role = 'guardian' THEN e.rank_band END,
                'difficulty_note', CASE WHEN v_role = 'guardian' THEN e.difficulty_note END)) ORDER BY e.exam_date DESC, e.created_at DESC), '[]')
               FROM insight.exam_record e
              WHERE e.student_id = p_student AND e.superseded_at IS NULL AND e.raw_score IS NOT NULL
                AND (v_role = 'guardian' OR e.entered_by_account_id = v_me)),
    'textbooks', (SELECT coalesce(jsonb_object_agg(t.subject, t.textbook_version), '{}') FROM platform.student_subject_textbook t
                   WHERE t.student_id = p_student AND t.updated_by = v_me),
    'progress', (SELECT coalesce(jsonb_agg(jsonb_build_object('step', p.step, 'status', p.status) ORDER BY p.at, p.id), '[]')
                   FROM insight.onboarding_progress p WHERE p.student_id = p_student AND p.respondent_account_id = v_me));
END $$;

-- 考试当天零点（上海时区）
CREATE FUNCTION insight.exam_freeze_at(p_exam_date date) RETURNS timestamptz LANGUAGE sql IMMUTABLE SET search_path = pg_catalog
  AS $$ SELECT p_exam_date::timestamp AT TIME ZONE 'Asia/Shanghai' $$;
REVOKE EXECUTE ON FUNCTION insight.exam_freeze_at(date) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION insight.exam_freeze_at(date) TO starbuddy_insight, starbuddy_authz;

-- ---------- 预测 ----------
CREATE TABLE insight.exam_prediction (
  id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  student_id         uuid NOT NULL REFERENCES platform.student_profile (id) ON DELETE CASCADE,
  exam_record_id     uuid NOT NULL UNIQUE REFERENCES insight.exam_record (id) ON DELETE CASCADE,  -- 考试安排（raw_score 为空的那条）
  claim_id           uuid NOT NULL REFERENCES insight.claim (id),  -- 这份预测对应的判断（对象类型 exam；契约 claim_id）
  interval_low       numeric(5, 1) NOT NULL CHECK (interval_low >= 0),
  interval_high      numeric(5, 1) NOT NULL,
  level              numeric(3, 2) NOT NULL DEFAULT 0.80 CHECK (level = 0.80),  -- 契约 interval_level 固定 0.8（待人确认 #6）
  -- 计算依据快照（范围节点 × 掌握 × 地区考情权重 × 题型的输入与版本）；不对学生、家长展示，不导出
  basis              jsonb NOT NULL CHECK (jsonb_typeof(basis) = 'object' AND pg_column_size(basis) <= 16384),
  evidence_ids       uuid[] NOT NULL DEFAULT '{}',
  algorithm_version  platform.versioned_name NOT NULL,
  source_type        insight.source_type NOT NULL DEFAULT 'inferred' CHECK (source_type = 'inferred'),
  created_at         timestamptz NOT NULL DEFAULT now(),
  updated_at         timestamptz NOT NULL DEFAULT now(),
  frozen_at          timestamptz,
  CONSTRAINT exam_prediction_interval CHECK (interval_low <= interval_high)
);
CREATE INDEX exam_prediction_student_idx ON insight.exam_prediction (student_id);
COMMENT ON TABLE insight.exam_prediction IS
  '考前预测区间（0034，SIE-03）：洞察服务写；考试当天零点（上海）起不能新建与修改，冻结后不可改、不可删（学生删除请求除外）';

CREATE FUNCTION insight.exam_prediction_guard() RETURNS trigger LANGUAGE plpgsql
  SET search_path = pg_catalog, insight, platform, pg_temp
  AS $$
DECLARE e insight.exam_record;
BEGIN
  IF TG_OP = 'TRUNCATE' THEN
    RAISE EXCEPTION '预测不能清空' USING ERRCODE = 'insufficient_privilege';
  END IF;
  IF TG_OP = 'DELETE' THEN
    -- 冻结后也一样：只有删除请求执行时能删（0035）
    IF platform.erasure_in_progress(OLD.student_id) THEN
      RETURN OLD;
    END IF;
    RAISE EXCEPTION '预测只随删除请求删除' USING ERRCODE = 'insufficient_privilege';
  END IF;
  IF TG_OP = 'UPDATE' AND OLD.frozen_at IS NOT NULL THEN
    RAISE EXCEPTION '已冻结的预测不能修改' USING ERRCODE = 'insufficient_privilege';
  END IF;
  SELECT * INTO e FROM insight.exam_record r WHERE r.id = NEW.exam_record_id;
  IF NOT FOUND OR e.student_id <> NEW.student_id OR e.raw_score IS NOT NULL OR e.superseded_at IS NOT NULL THEN
    RAISE EXCEPTION '预测必须指向该学生当前的考试安排（未出成绩）' USING ERRCODE = 'check_violation';
  END IF;
  IF NEW.interval_high > e.full_score THEN
    RAISE EXCEPTION '预测区间超出满分 %', e.full_score USING ERRCODE = 'check_violation';
  END IF;
  IF NOT EXISTS (SELECT 1 FROM insight.claim c WHERE c.id = NEW.claim_id AND c.student_id = NEW.student_id AND c.target_type = 'exam') THEN
    RAISE EXCEPTION '预测必须对应该学生的一条考试类判断' USING ERRCODE = 'check_violation';
  END IF;
  IF TG_OP = 'INSERT' THEN
    IF NEW.frozen_at IS NOT NULL OR now() >= insight.exam_freeze_at(e.exam_date) THEN
      RAISE EXCEPTION '考试当天零点起不能再新建预测' USING ERRCODE = 'check_violation';
    END IF;
    NEW.created_at := now();
    NEW.updated_at := now();
    RETURN NEW;
  END IF;
  IF (NEW.id, NEW.student_id, NEW.exam_record_id, NEW.claim_id, NEW.created_at)
     IS DISTINCT FROM (OLD.id, OLD.student_id, OLD.exam_record_id, OLD.claim_id, OLD.created_at) THEN
    RAISE EXCEPTION '预测的学生、考试、判断与创建时间不能修改' USING ERRCODE = 'check_violation';
  END IF;
  IF NEW.frozen_at IS NOT NULL THEN
    -- 冻结：只写 frozen_at，其余不变，而且只能在考试当天零点之后
    IF (to_jsonb(NEW) - 'frozen_at') IS DISTINCT FROM (to_jsonb(OLD) - 'frozen_at') OR NEW.frozen_at < insight.exam_freeze_at(e.exam_date) THEN
      RAISE EXCEPTION '冻结只写冻结时间，且不早于考试当天零点' USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
  END IF;
  IF now() >= insight.exam_freeze_at(e.exam_date) THEN
    RAISE EXCEPTION '考试当天零点起预测不能再修改' USING ERRCODE = 'check_violation';
  END IF;
  NEW.updated_at := now();
  RETURN NEW;
END $$;
REVOKE EXECUTE ON FUNCTION insight.exam_prediction_guard() FROM PUBLIC;
CREATE TRIGGER exam_prediction_guard BEFORE INSERT OR UPDATE OR DELETE ON insight.exam_prediction
  FOR EACH ROW EXECUTE FUNCTION insight.exam_prediction_guard();
CREATE TRIGGER exam_prediction_truncate BEFORE TRUNCATE ON insight.exam_prediction
  FOR EACH STATEMENT EXECUTE FUNCTION insight.exam_prediction_guard();

-- 冻结到期的预测（只授 starbuddy_scheduler）：p_now 不能晚于现在（不能提前冻结），最多往前 1 天；返回冻结条数
CREATE FUNCTION insight.freeze_due_predictions(p_now timestamptz) RETURNS integer
  LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog, insight, platform, pg_temp
  AS $$
DECLARE v_n integer;
BEGIN
  IF p_now IS NULL OR p_now > now() OR p_now < now() - interval '1 day' THEN
    RAISE EXCEPTION 'p_now must be within the last day and not in the future' USING ERRCODE = 'invalid_parameter_value';
  END IF;
  UPDATE insight.exam_prediction p SET frozen_at = p_now
    FROM insight.exam_record e
   WHERE e.id = p.exam_record_id AND p.frozen_at IS NULL AND insight.exam_freeze_at(e.exam_date) <= p_now;
  GET DIAGNOSTICS v_n = ROW_COUNT;
  RETURN v_n;
END $$;
ALTER FUNCTION insight.freeze_due_predictions(timestamptz) OWNER TO starbuddy_authz;
REVOKE EXECUTE ON FUNCTION insight.freeze_due_predictions(timestamptz) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION insight.freeze_due_predictions(timestamptz) TO starbuddy_scheduler;
-- 定时任务要能解析 insight 里的函数名；它在 insight 下没有任何表权限
GRANT USAGE ON SCHEMA insight TO starbuddy_scheduler;

-- ---------- 对账卡 ----------
-- 每句：{"claim_id": <判断 ID>, "template_key": <判断模板键>[, "direction": <契约四方向之一>]}，1–20 句，不收别的键
CREATE FUNCTION insight.valid_reconciliation_sentences(p jsonb) RETURNS boolean LANGUAGE sql IMMUTABLE SET search_path = pg_catalog
  AS $$ SELECT jsonb_typeof(p) = 'array' AND jsonb_array_length(p) BETWEEN 1 AND 20
           AND NOT EXISTS (
             SELECT 1 FROM jsonb_array_elements(p) s
              WHERE jsonb_typeof(s) <> 'object'
                 OR (SELECT array_agg(k ORDER BY k) FROM jsonb_object_keys(s) k) NOT IN ('{claim_id,template_key}', '{claim_id,direction,template_key}')
                 OR jsonb_typeof(s -> 'claim_id') <> 'string'
                 OR (s ->> 'claim_id') !~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
                 OR jsonb_typeof(s -> 'template_key') <> 'string'
                 OR (s ->> 'template_key') !~ '^[a-z][a-z0-9_]*(\.[a-z0-9_]+){1,4}$'
                 OR (s ? 'direction' AND (jsonb_typeof(s -> 'direction') <> 'string' OR (s ->> 'direction') NOT IN
                      ('item_type_transfer', 'time_shortage', 'calculation_slip', 'diagnosis_optimistic')))) $$;
REVOKE EXECUTE ON FUNCTION insight.valid_reconciliation_sentences(jsonb) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION insight.valid_reconciliation_sentences(jsonb) TO starbuddy_insight, starbuddy_authz;

CREATE TABLE insight.exam_reconciliation (
  id                    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  student_id            uuid NOT NULL REFERENCES platform.student_profile (id) ON DELETE CASCADE,
  prediction_id         uuid NOT NULL UNIQUE REFERENCES insight.exam_prediction (id) ON DELETE CASCADE,
  exam_record_id        uuid NOT NULL REFERENCES insight.exam_record (id) ON DELETE CASCADE,  -- 成绩行（plan_record_id = 预测的考试安排）
  in_interval           boolean NOT NULL,  -- 由守卫按成绩与冻结区间计算
  -- 偏差归因固定四方向（契约 deviation_directions）：题型迁移、时间不足、计算失误、此前诊断偏乐观；去重；区间内为空、区间外至少 1 个
  deviation_directions  text[] NOT NULL DEFAULT '{}'
                        CHECK (insight.valid_code_set(deviation_directions,
                               ARRAY['item_type_transfer', 'time_shortage', 'calculation_slip', 'diagnosis_optimistic'])),
  deviation_nodes       text[] NOT NULL DEFAULT '{}' CHECK (insight.valid_node_ids(deviation_nodes, 0, 100)),
  -- 平台据此改了什么（契约 adjustments 编码）
  adjustments           text[] NOT NULL CHECK (cardinality(adjustments) >= 1 AND insight.valid_code_set(adjustments,
                               ARRAY['claims_to_verify', 'retest_scheduled', 'interval_widened', 'model_parameter_adjusted', 'none'])),
  -- 这张卡给谁看：学生版、家长版（所引判断的可见范围必须覆盖）
  audience              text[] NOT NULL CHECK (cardinality(audience) >= 1 AND insight.valid_code_set(audience, ARRAY['student', 'guardian'])),
  sentences             jsonb NOT NULL CONSTRAINT exam_reconciliation_sentences CHECK (insight.valid_reconciliation_sentences(sentences)),
  evidence_ids          uuid[] NOT NULL DEFAULT '{}',
  algorithm_version     platform.versioned_name NOT NULL,
  source_type           insight.source_type NOT NULL DEFAULT 'inferred' CHECK (source_type = 'inferred'),
  created_at            timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT exam_reconciliation_direction CHECK (in_interval = (cardinality(deviation_directions) = 0))
);
CREATE INDEX exam_reconciliation_student_idx ON insight.exam_reconciliation (student_id);
COMMENT ON TABLE insight.exam_reconciliation IS
  '考后对账卡（0034，SIE-03）：只追加；每句只存判断 ID 与模板键（只由判断渲染，规则 11）；偏差方向为契约四值';

CREATE FUNCTION insight.exam_reconciliation_guard() RETURNS trigger LANGUAGE plpgsql
  SET search_path = pg_catalog, insight, platform, pg_temp
  AS $$
DECLARE p insight.exam_prediction; e insight.exam_record;
BEGIN
  IF TG_OP = 'TRUNCATE' THEN
    RAISE EXCEPTION '对账卡不能清空' USING ERRCODE = 'insufficient_privilege';
  END IF;
  IF TG_OP = 'DELETE' THEN
    IF platform.erasure_in_progress(OLD.student_id) THEN
      RETURN OLD;
    END IF;
    RAISE EXCEPTION '对账卡只随删除请求删除' USING ERRCODE = 'insufficient_privilege';
  END IF;
  IF TG_OP = 'UPDATE' THEN
    RAISE EXCEPTION '对账卡只追加，不能修改' USING ERRCODE = 'insufficient_privilege';
  END IF;
  SELECT * INTO p FROM insight.exam_prediction x WHERE x.id = NEW.prediction_id;
  IF NOT FOUND OR p.student_id <> NEW.student_id OR p.frozen_at IS NULL THEN
    RAISE EXCEPTION '对账只能针对该学生已冻结的预测' USING ERRCODE = 'check_violation';
  END IF;
  SELECT * INTO e FROM insight.exam_record x WHERE x.id = NEW.exam_record_id;
  IF NOT FOUND OR e.student_id <> NEW.student_id OR e.raw_score IS NULL OR e.superseded_at IS NOT NULL
     OR e.plan_record_id IS DISTINCT FROM p.exam_record_id THEN
    RAISE EXCEPTION '对账的成绩必须是按这场考试安排录入的当前成绩记录' USING ERRCODE = 'check_violation';
  END IF;
  -- 每句引用的判断：同一学生、成立或假设、未过期、模板已审核，可见范围覆盖这张卡的读者（规则 11）
  IF EXISTS (SELECT 1 FROM jsonb_array_elements(NEW.sentences) s
              WHERE NOT EXISTS (SELECT 1 FROM insight.claim c JOIN insight.claim_template t
                                         ON t.template_key = c.template_key AND t.version = c.template_version AND t.status = 'approved'
                                 WHERE c.id = (s ->> 'claim_id')::uuid AND c.student_id = NEW.student_id
                                   AND c.template_key = s ->> 'template_key' AND c.expires_at > now()
                                   AND NEW.audience <@ c.visibility AND c.status IN ('hypothesis', 'established'))) THEN
    RAISE EXCEPTION '对账卡的每句都必须引用该学生对读者可见、成立或假设状态的判断' USING ERRCODE = 'check_violation';
  END IF;
  NEW.in_interval := e.raw_score BETWEEN p.interval_low AND p.interval_high;
  NEW.created_at := now();
  RETURN NEW;
END $$;
REVOKE EXECUTE ON FUNCTION insight.exam_reconciliation_guard() FROM PUBLIC;
CREATE TRIGGER exam_reconciliation_guard BEFORE INSERT OR UPDATE OR DELETE ON insight.exam_reconciliation
  FOR EACH ROW EXECUTE FUNCTION insight.exam_reconciliation_guard();
CREATE TRIGGER exam_reconciliation_truncate BEFORE TRUNCATE ON insight.exam_reconciliation
  FOR EACH STATEMENT EXECUTE FUNCTION insight.exam_reconciliation_guard();

-- ---------- 权限：洞察服务写；学习服务不读（预测是对外陈述的依据，不作计算输入）；BFF 没有表权限 ----------
ALTER TABLE insight.exam_prediction ENABLE ROW LEVEL SECURITY;
ALTER TABLE insight.exam_prediction FORCE ROW LEVEL SECURITY;
ALTER TABLE insight.exam_reconciliation ENABLE ROW LEVEL SECURITY;
ALTER TABLE insight.exam_reconciliation FORCE ROW LEVEL SECURITY;
REVOKE ALL ON insight.exam_prediction, insight.exam_reconciliation FROM PUBLIC;
GRANT SELECT ON insight.exam_prediction, insight.exam_reconciliation TO starbuddy_insight;
GRANT INSERT (student_id, exam_record_id, claim_id, interval_low, interval_high, level, basis, evidence_ids, algorithm_version)
  ON insight.exam_prediction TO starbuddy_insight;
GRANT UPDATE (interval_low, interval_high, basis, evidence_ids, algorithm_version) ON insight.exam_prediction TO starbuddy_insight;
GRANT INSERT (student_id, prediction_id, exam_record_id, in_interval, deviation_directions, deviation_nodes, adjustments, audience, sentences,
              evidence_ids, algorithm_version)
  ON insight.exam_reconciliation TO starbuddy_insight;
CREATE POLICY exam_prediction_insight ON insight.exam_prediction FOR ALL TO starbuddy_insight USING (true) WITH CHECK (true);
CREATE POLICY exam_reconciliation_insight ON insight.exam_reconciliation FOR ALL TO starbuddy_insight USING (true) WITH CHECK (true);
-- 冻结函数属主（authz）：只改冻结时间
GRANT SELECT ON insight.exam_prediction TO starbuddy_authz;
GRANT UPDATE (frozen_at) ON insight.exam_prediction TO starbuddy_authz;
