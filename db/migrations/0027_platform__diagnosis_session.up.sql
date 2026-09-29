-- 0027 platform · 数学自适应诊断的会话与步骤（specs/sie-02a-math-diagnosis/spec.md 设计要点 1、3，待人确认 #5 按默认）
-- 状态：草稿。只写文件，由人执行（CLAUDE.md 必停项：迁移、RLS 与权限）。只改 platform schema（ADR 02 第 2 条）。
-- 过渡期学习服务是唯一写个人层的服务：会话与步骤只由 starbuddy_learning 写；学生经 BFF 只读自己会话的进度（不含选题理由与算法版本）；
-- 家长、其他学生读不到。步骤只存选题决策与引用（节点、题目、判分、探针），不存题干、不存对错副本（对错以 grade_result 为准，规则 2），
-- 也不存任何对学生的描述性结论（规则 11：诊断不产生可见陈述）。
-- 请以单事务执行（psql -1）。ALTER TYPE ... ADD VALUE 在同一事务里不能被使用，所以下面凡涉及新值的地方都按 text 比较。

ALTER TYPE platform.review_reason ADD VALUE IF NOT EXISTS 'process_observation';
-- 0027 的回滚会加这条约束挡住残留的新枚举值；重新执行时先去掉
ALTER TABLE platform.review_task DROP CONSTRAINT IF EXISTS review_task_no_process_observation;

-- ---------- 会话 ----------
CREATE TABLE platform.diagnosis_session (
  id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  student_id         uuid NOT NULL REFERENCES platform.student_profile (id) ON DELETE CASCADE,
  subject            platform.subject NOT NULL,
  status             text NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'paused', 'finished', 'abandoned')),
  algorithm_version  platform.versioned_name NOT NULL,              -- 选题规则版本（可复算）
  map_version        text NOT NULL CHECK (map_version ~ '^\d{1,3}\.\d{1,3}\.\d{1,4}$'),  -- 所用地图版本号
  usage_id           uuid NOT NULL,                                 -- 计量（规则 4）：本次诊断的额度占用
  time_budget_sec    integer NOT NULL DEFAULT 2100 CHECK (time_budget_sec BETWEEN 60 AND 7200),
  item_limit         smallint NOT NULL DEFAULT 20 CHECK (item_limit BETWEEN 1 AND 60),
  elapsed_sec        integer NOT NULL DEFAULT 0 CHECK (elapsed_sec >= 0),
  item_count         smallint NOT NULL DEFAULT 0 CHECK (item_count >= 0),
  idempotency_key    uuid NOT NULL,
  created_at         timestamptz NOT NULL DEFAULT now(),
  updated_at         timestamptz NOT NULL DEFAULT now(),
  finished_at        timestamptz,
  UNIQUE (student_id, idempotency_key),
  UNIQUE (usage_id),
  CONSTRAINT diagnosis_session_item_limit CHECK (item_count <= item_limit),
  CONSTRAINT diagnosis_session_finished CHECK ((status IN ('finished', 'abandoned')) = (finished_at IS NOT NULL))
);
-- 同一学生同一学科最多一个未结束的会话（可分次：暂停后续做同一个会话）
CREATE UNIQUE INDEX diagnosis_session_open_uq ON platform.diagnosis_session (student_id, subject) WHERE status IN ('active', 'paused');
COMMENT ON TABLE platform.diagnosis_session IS
  '自适应诊断会话（0027，SIE-02a）。只由学习服务写；学生只读自己的进度列。不存结论，诊断结果以证据与判分为准';

-- ---------- 步骤 ----------
CREATE TABLE platform.diagnosis_step (
  id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  session_id         uuid NOT NULL REFERENCES platform.diagnosis_session (id) ON DELETE CASCADE,
  seq                smallint NOT NULL CHECK (seq BETWEEN 1 AND 100),
  kind               text NOT NULL CHECK (kind IN ('adaptive', 'process', 'explain')),
  node_id            text NOT NULL CHECK (node_id ~ '^[A-Z][A-Z0-9]{1,9}(\.[A-Z0-9][A-Z0-9_]{0,31}){1,4}$'),
  item_id            platform.skill_local_id,                        -- 无题可测时为空
  reason_code        text NOT NULL CHECK (reason_code IN ('target', 'prereq_trace', 'confirm', 'no_item', 'process_near_gap', 'explain')),
  status             text NOT NULL DEFAULT 'presented' CHECK (status IN ('presented', 'answered', 'skipped', 'no_item')),
  probe_id           uuid,                                           -- insight.confidence_probe（跨 schema 不设外键，由学习服务核对）
  grade_result_id    uuid REFERENCES platform.grade_result (id),      -- 对错只以判分为准，这里只存引用
  elapsed_sec        integer CHECK (elapsed_sec BETWEEN 0 AND 7200),
  idempotency_key    uuid NOT NULL,
  created_at         timestamptz NOT NULL DEFAULT now(),
  answered_at        timestamptz,
  UNIQUE (session_id, seq),
  UNIQUE (session_id, idempotency_key),
  CONSTRAINT diagnosis_step_no_item CHECK ((reason_code = 'no_item') = (status = 'no_item') AND (status <> 'no_item' OR item_id IS NULL)),
  CONSTRAINT diagnosis_step_has_item CHECK (status = 'no_item' OR item_id IS NOT NULL),
  CONSTRAINT diagnosis_step_answered CHECK ((status = 'answered') = (answered_at IS NOT NULL AND grade_result_id IS NOT NULL)),
  CONSTRAINT diagnosis_step_kind_reason CHECK ((kind = 'adaptive' AND reason_code IN ('target', 'prereq_trace', 'confirm', 'no_item'))
                                               OR (kind = 'process' AND reason_code = 'process_near_gap')
                                               OR (kind = 'explain' AND reason_code = 'explain'))
);
COMMENT ON TABLE platform.diagnosis_step IS
  '诊断步骤（0027）：选题决策与引用。对错以 grade_result 为准，不在这里另存；不存题干';

-- 判分必须属于同一学生、同一学科
CREATE FUNCTION platform.diagnosis_step_guard() RETURNS trigger
  LANGUAGE plpgsql SET search_path = pg_catalog, platform, pg_temp AS $$
DECLARE s record;
BEGIN
  SELECT student_id, subject, status INTO s FROM platform.diagnosis_session WHERE id = NEW.session_id;
  IF TG_OP = 'INSERT' AND s.status <> 'active' THEN
    RAISE EXCEPTION '会话不在进行中，不能新增步骤' USING ERRCODE = '22023';
  END IF;
  IF NEW.grade_result_id IS NOT NULL AND NOT EXISTS (
       SELECT 1 FROM platform.grade_result g WHERE g.id = NEW.grade_result_id AND g.student_id = s.student_id AND g.subject = s.subject) THEN
    RAISE EXCEPTION '判分不属于该会话的学生与学科' USING ERRCODE = '22023';
  END IF;
  IF TG_OP = 'UPDATE' AND OLD.status IN ('answered', 'skipped', 'no_item') AND NEW IS DISTINCT FROM OLD THEN
    RAISE EXCEPTION '已结束的步骤不能再改' USING ERRCODE = '22023';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER diagnosis_step_guard BEFORE INSERT OR UPDATE ON platform.diagnosis_step
  FOR EACH ROW EXECUTE FUNCTION platform.diagnosis_step_guard();

-- 会话：结束状态不可逆；用时与题数只增不减
CREATE FUNCTION platform.diagnosis_session_guard() RETURNS trigger
  LANGUAGE plpgsql SET search_path = pg_catalog, platform, pg_temp AS $$
BEGIN
  IF OLD.status IN ('finished', 'abandoned') AND NEW IS DISTINCT FROM OLD THEN
    RAISE EXCEPTION '已结束的会话不能再改' USING ERRCODE = '22023';
  END IF;
  IF NEW.elapsed_sec < OLD.elapsed_sec OR NEW.item_count < OLD.item_count THEN
    RAISE EXCEPTION '用时与题数只能增加' USING ERRCODE = '22023';
  END IF;
  IF (NEW.student_id, NEW.subject, NEW.algorithm_version, NEW.map_version, NEW.usage_id, NEW.idempotency_key, NEW.created_at,
      NEW.time_budget_sec, NEW.item_limit)
     IS DISTINCT FROM (OLD.student_id, OLD.subject, OLD.algorithm_version, OLD.map_version, OLD.usage_id, OLD.idempotency_key,
                       OLD.created_at, OLD.time_budget_sec, OLD.item_limit) THEN
    RAISE EXCEPTION '会话的身份与配置不能改' USING ERRCODE = '22023';
  END IF;
  NEW.updated_at := now();
  RETURN NEW;
END $$;
CREATE TRIGGER diagnosis_session_guard BEFORE UPDATE ON platform.diagnosis_session
  FOR EACH ROW EXECUTE FUNCTION platform.diagnosis_session_guard();

-- ---------- 权限：学习服务写；BFF 只读学生本人进度列；没有任何角色能 DELETE（随学生档案级联删除） ----------
REVOKE ALL ON platform.diagnosis_session, platform.diagnosis_step FROM PUBLIC;
GRANT SELECT, INSERT ON platform.diagnosis_session, platform.diagnosis_step TO starbuddy_learning;
GRANT UPDATE (status, elapsed_sec, item_count, finished_at) ON platform.diagnosis_session TO starbuddy_learning;
GRANT UPDATE (status, probe_id, grade_result_id, elapsed_sec, answered_at) ON platform.diagnosis_step TO starbuddy_learning;
GRANT SELECT (id, student_id, subject, status, time_budget_sec, item_limit, elapsed_sec, item_count, created_at, finished_at)
  ON platform.diagnosis_session TO starbuddy_bff;
GRANT SELECT (id, session_id, seq, kind, item_id, status) ON platform.diagnosis_step TO starbuddy_bff;
-- 审核台详情函数（属主 authz）要读步骤
GRANT SELECT ON platform.diagnosis_step TO starbuddy_authz;

ALTER TABLE platform.diagnosis_session ENABLE ROW LEVEL SECURITY;
ALTER TABLE platform.diagnosis_session FORCE ROW LEVEL SECURITY;
ALTER TABLE platform.diagnosis_step ENABLE ROW LEVEL SECURITY;
ALTER TABLE platform.diagnosis_step FORCE ROW LEVEL SECURITY;
CREATE POLICY diagnosis_session_learning ON platform.diagnosis_session FOR ALL TO starbuddy_learning USING (true) WITH CHECK (true);
CREATE POLICY diagnosis_step_learning ON platform.diagnosis_step FOR ALL TO starbuddy_learning USING (true) WITH CHECK (true);
-- 只有学生本人能看自己的进度；家长不行（诊断中的逐题情况不给家长看，结论随 SIE-02b 经判断账本）
CREATE POLICY diagnosis_session_self ON platform.diagnosis_session FOR SELECT TO starbuddy_bff USING (platform.is_self_student(student_id));
CREATE POLICY diagnosis_step_self ON platform.diagnosis_step FOR SELECT TO starbuddy_bff
  USING (EXISTS (SELECT 1 FROM platform.diagnosis_session s WHERE s.id = session_id AND platform.is_self_student(s.student_id)));

-- ---------- 过程观察进统一复核队列 ----------
ALTER TABLE platform.review_task DROP CONSTRAINT review_task_source_kind_check;
ALTER TABLE platform.review_task ADD CONSTRAINT review_task_source_kind_check
  CHECK (source_kind IN ('grade_result', 'skill_observation', 'error_attribution', 'claim', 'diagnosis_step'));
-- 过程观察只能挂在诊断步骤上，诊断步骤只能以过程观察进队
ALTER TABLE platform.review_task ADD CONSTRAINT review_task_diagnosis_step
  CHECK ((source_kind = 'diagnosis_step') = ('process_observation' = ANY (reasons::text[]))
         AND (source_kind <> 'diagnosis_step' OR reasons::text[] = ARRAY['process_observation']));
DROP POLICY review_service_open ON platform.review_task;
CREATE POLICY review_service_open ON platform.review_task FOR INSERT TO starbuddy_learning
  WITH CHECK (status = 'open' AND resolved_at IS NULL AND resolved_by_account IS NULL
              AND assigned_to IS NULL AND assigned_at IS NULL
              AND NOT ('profile_dispute' = ANY (reasons::text[]))
              AND ((source_kind = 'grade_result' AND NOT ('process_observation' = ANY (reasons::text[])) AND EXISTS (
                      SELECT 1 FROM platform.grade_result g
                       WHERE g.id = source_id AND g.student_id = review_task.student_id AND g.subject = review_task.subject
                         AND review_task.reasons::text[] <@ g.review_reasons::text[]))
                   OR (source_kind = 'diagnosis_step' AND reasons::text[] = ARRAY['process_observation'] AND EXISTS (
                      SELECT 1 FROM platform.diagnosis_step st JOIN platform.diagnosis_session s ON s.id = st.session_id
                       WHERE st.id = source_id AND st.kind IN ('process', 'explain') AND st.status = 'answered'
                         AND s.student_id = review_task.student_id AND s.subject = review_task.subject))));
-- 结案码：过程观察只记“已看过”或“无需处理”，不改分、不改掌握（结论随 SIE-02b 经判断账本）
ALTER TABLE platform.review_resolution_code DROP CONSTRAINT review_resolution_code_applies_to_check;
ALTER TABLE platform.review_resolution_code ADD CONSTRAINT review_resolution_code_applies_to_check
  CHECK (cardinality(applies_to) >= 1 AND applies_to <@ ARRAY['grade_result', 'skill_observation', 'error_attribution', 'claim', 'diagnosis_step']);
INSERT INTO platform.review_resolution_code (code, label, applies_to) VALUES
  ('process_noted', '已看过程并记录', ARRAY['diagnosis_step']);
UPDATE platform.review_resolution_code SET applies_to = applies_to || ARRAY['diagnosis_step'] WHERE code = 'not_actionable';

-- 审核台详情：加上诊断步骤的引用（其余分支与 0023 相同）
CREATE OR REPLACE FUNCTION platform.review_task_detail(p_task uuid) RETURNS jsonb
  LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = pg_catalog, platform, pg_temp
  AS $$
DECLARE v_staff uuid := platform.review_current_staff(); r platform.review_task; v_source jsonb;
BEGIN
  SELECT * INTO r FROM platform.review_task WHERE id = p_task;
  IF NOT FOUND THEN
    RAISE EXCEPTION '复核任务不存在' USING ERRCODE = 'no_data_found';
  END IF;
  IF r.source_kind = 'grade_result' THEN
    SELECT jsonb_build_object(
             'verdict', g.verdict, 'score_total', g.score_total, 'max_total', g.max_total, 'confidence', g.confidence,
             'calibrated', g.calibrated, 'rubric_version', g.rubric_version, 'source_version', g.source_version,
             'model_version', g.model_version, 'review_reasons', g.review_reasons::text[], 'evidence_ids', g.evidence_ids,
             'superseded', g.superseded_by IS NOT NULL, 'created_at', g.created_at,
             'dimensions', coalesce((SELECT jsonb_agg(jsonb_build_object(
                 'dimension_id', d.dimension_id, 'score', d.score, 'max_score', d.max_score, 'judged_by', d.judged_by,
                 'subjective', d.subjective, 'rule_ref', d.rule_ref, 'confidence', d.confidence, 'evidence_ids', d.evidence_ids)
                 ORDER BY d.dimension_id) FROM platform.grade_dimension d WHERE d.grade_result_id = g.id), '[]'::jsonb))
      INTO v_source FROM platform.grade_result g WHERE g.id = r.source_id;
  ELSIF r.source_kind = 'diagnosis_step' THEN
    -- 过程观察（0027）：只给选题引用与判分引用，作答原文由审核台按判分的 evidence_ids 另取；不给选题理由以外的推断
    SELECT jsonb_build_object(
             'kind', st.kind, 'node_id', st.node_id, 'item_id', st.item_id, 'grade_result_id', st.grade_result_id,
             'evidence_ids', g.evidence_ids, 'verdict', g.verdict, 'answered_at', st.answered_at)
      INTO v_source FROM platform.diagnosis_step st LEFT JOIN platform.grade_result g ON g.id = st.grade_result_id
     WHERE st.id = r.source_id;
  ELSIF r.source_kind = 'claim' THEN
    SELECT jsonb_build_object(
             'template_key', c.template_key, 'template_version', c.template_version, 'target_type', c.target_type,
             'status', c.status, 'confidence', c.confidence, 'source_type', c.source_type, 'model_version', c.model_version,
             'evidence_ids', c.evidence_ids, 'created_at', c.created_at)
      INTO v_source FROM insight.claim c WHERE c.id = r.source_id;
  END IF;
  RETURN jsonb_build_object(
    'id', r.id, 'subject', r.subject, 'student_ref', platform.review_student_ref(r.student_id), 'source_kind', r.source_kind,
    'source_id', r.source_id, 'reasons', r.reasons::text[], 'status', r.status, 'created_at', r.created_at,
    'assigned', r.assigned_to IS NOT NULL, 'assigned_to_me', r.assigned_to IS NOT NULL AND r.assigned_to = v_staff,
    'assigned_at', r.assigned_at, 'resolution_code', r.resolution_code, 'resolved_at', r.resolved_at,
    'resolved_by_me', r.resolved_by_account IS NOT NULL AND r.resolved_by_account = v_staff,
    'source', v_source);
END $$;
