-- 0005 platform · 模型推断：能力观测、错因归因、掌握估计、判分结果
-- 每条推断都记录算法版本、置信度、证据 ID 与更正历史（CLAUDE.md 规则 6）。
-- 只有 starbuddy_learning 能写；学生与家长只读（ADR 02 第 4 条，修正知行 owner for all 的漏洞）。
-- 更正不改原记录：写一条新记录，再把旧记录的 superseded_by 指过去。

CREATE TABLE platform.skill_observation (
  id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  student_id          uuid NOT NULL REFERENCES platform.student_profile (id),
  subject             platform.subject NOT NULL,
  skill_local_id      platform.skill_local_id NOT NULL,
  outcome             text NOT NULL CHECK (outcome IN ('demonstrated', 'not_demonstrated', 'partial', 'inconclusive')),
  local_level         text CHECK (local_level ~ '^[A-Za-z0-9_\-]{1,16}$'),
  confidence          platform.confidence NOT NULL,
  algorithm_version   platform.versioned_name NOT NULL,
  evidence_ids        uuid[] NOT NULL CHECK (cardinality(evidence_ids) >= 1),
  source_request_id   uuid NOT NULL,
  created_at          timestamptz NOT NULL DEFAULT now(),
  superseded_by       uuid REFERENCES platform.skill_observation (id) DEFERRABLE INITIALLY DEFERRED,
  superseded_at       timestamptz,
  CHECK ((superseded_by IS NULL) = (superseded_at IS NULL))
);
CREATE INDEX skill_observation_student_idx ON platform.skill_observation (student_id, subject, skill_local_id, created_at);

CREATE TABLE platform.error_attribution (
  id                       uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  student_id               uuid NOT NULL REFERENCES platform.student_profile (id),
  subject                  platform.subject NOT NULL,
  skill_local_id           platform.skill_local_id,
  subject_category         text NOT NULL CHECK (subject_category ~ '^[a-z0-9_\-]{1,48}$'),
  cross_subject_category   platform.cross_subject_error NOT NULL,
  confidence               platform.confidence NOT NULL,
  algorithm_version        platform.versioned_name NOT NULL,
  evidence_ids             uuid[] NOT NULL CHECK (cardinality(evidence_ids) >= 1),
  source_request_id        uuid NOT NULL,
  created_at               timestamptz NOT NULL DEFAULT now(),
  superseded_by            uuid REFERENCES platform.error_attribution (id) DEFERRABLE INITIALLY DEFERRED,
  superseded_at            timestamptz,
  CHECK ((superseded_by IS NULL) = (superseded_at IS NULL))
);
CREATE INDEX error_attribution_student_idx ON platform.error_attribution (student_id, cross_subject_category, created_at);

-- 掌握估计：平台唯一真相源（规则 2）。每个学生每个能力点只有一条 is_current。
CREATE TABLE platform.mastery_estimate (
  id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  student_id          uuid NOT NULL REFERENCES platform.student_profile (id),
  subject             platform.subject NOT NULL,
  skill_local_id      platform.skill_local_id NOT NULL,
  state               platform.mastery_state NOT NULL,
  p_mastery           platform.confidence,
  confidence          platform.confidence NOT NULL,
  algorithm_version   platform.versioned_name NOT NULL,
  evidence_ids        uuid[] NOT NULL CHECK (cardinality(evidence_ids) >= 1),
  computed_at         timestamptz NOT NULL DEFAULT now(),
  next_retest_at      timestamptz,
  is_current          boolean NOT NULL DEFAULT true,
  superseded_by       uuid REFERENCES platform.mastery_estimate (id) DEFERRABLE INITIALLY DEFERRED,
  superseded_at       timestamptz,
  CHECK (is_current = (superseded_by IS NULL)),
  CHECK ((superseded_by IS NULL) = (superseded_at IS NULL))
);
CREATE UNIQUE INDEX mastery_estimate_current_uq
  ON platform.mastery_estimate (student_id, subject, skill_local_id) WHERE is_current;
COMMENT ON TABLE platform.mastery_estimate IS
  '掌握估计（规则 + BKT，冲刺 4-C）。只能由已准入内容的证据推动星亮门槛（transfer 及以上），由学习服务保证';

CREATE TABLE platform.grade_result (
  id                      uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  student_id              uuid NOT NULL REFERENCES platform.student_profile (id),
  subject                 platform.subject NOT NULL,
  attempt_id              uuid NOT NULL,
  attempt_occurred_at     timestamptz NOT NULL,
  verdict                 text NOT NULL CHECK (verdict IN ('correct', 'incorrect', 'partial', 'needs_review', 'not_applicable')),
  rubric_version          platform.versioned_name NOT NULL,
  score_total             numeric CHECK (score_total >= 0),
  max_total               numeric NOT NULL CHECK (max_total > 0),
  calibrated              boolean NOT NULL,
  confidence              platform.confidence NOT NULL,
  source_version          text NOT NULL CHECK (source_version ~ '^(rubric|verifier|prompt|items|algo|graph):[a-z0-9][a-z0-9\-]{0,47}@[A-Za-z0-9][A-Za-z0-9._\-]{0,31}(;(rubric|verifier|prompt|items|algo|graph):[a-z0-9][a-z0-9\-]{0,47}@[A-Za-z0-9][A-Za-z0-9._\-]{0,31}){0,7}$'),
  model_version           platform.model_version NOT NULL,
  verifier_id             text CHECK (verifier_id ~ '^[a-z0-9][a-z0-9\-]{0,47}$'),
  verifier_version        text CHECK (verifier_version ~ '^\d+\.\d+\.\d+$'),
  verifier_reason_codes   text[] NOT NULL DEFAULT '{}',
  human_review_required   boolean NOT NULL,
  review_reasons          platform.review_reason[] NOT NULL DEFAULT '{}',
  semantics               platform.semantics NOT NULL,
  evidence_ids            uuid[] NOT NULL CHECK (cardinality(evidence_ids) >= 1),
  source_request_id       uuid NOT NULL,
  created_at              timestamptz NOT NULL DEFAULT now(),
  superseded_by           uuid REFERENCES platform.grade_result (id) DEFERRABLE INITIALLY DEFERRED,
  superseded_at           timestamptz,
  CONSTRAINT grade_total_le_max CHECK (score_total IS NULL OR score_total <= max_total),
  CONSTRAINT grade_review_has_reason CHECK (NOT human_review_required OR cardinality(review_reasons) >= 1),
  CONSTRAINT grade_needs_review CHECK (verdict <> 'needs_review' OR (human_review_required AND confidence <= 0.5
                                        AND 'verifier_boundary_case' = ANY (review_reasons))),
  CONSTRAINT grade_verifier_boundary CHECK (cardinality(verifier_reason_codes) = 0
                                            OR (human_review_required AND 'verifier_boundary_case' = ANY (review_reasons))),
  CONSTRAINT grade_verifier_pair CHECK ((verifier_id IS NULL) = (verifier_version IS NULL)),
  CONSTRAINT grade_superseded_pair CHECK ((superseded_by IS NULL) = (superseded_at IS NULL)),
  CONSTRAINT grade_reason_codes_known CHECK (verifier_reason_codes <@ ARRAY[
    'trig_or_multiletter_function', 'equation_or_inequality_solving', 'proof_or_multi_answer',
    'parse_failure', 'sampling_insufficient', 'no_reference_answer']::text[]),
  -- 判分必须对应一次真实作答（安全审查建议 1）
  FOREIGN KEY (attempt_id, attempt_occurred_at) REFERENCES platform.attempt (attempt_id, occurred_at)
);
CREATE INDEX grade_result_student_idx ON platform.grade_result (student_id, subject, created_at);
CREATE INDEX grade_result_review_idx ON platform.grade_result (created_at) WHERE human_review_required;

CREATE TABLE platform.grade_dimension (
  grade_result_id   uuid NOT NULL REFERENCES platform.grade_result (id) ON DELETE CASCADE,
  dimension_id      text NOT NULL CHECK (dimension_id ~ '^[a-z0-9_\-]{1,48}$'),
  score             numeric CHECK (score >= 0),
  max_score         numeric NOT NULL CHECK (max_score > 0),
  judged_by         text NOT NULL CHECK (judged_by IN ('verifier', 'rubric_rule', 'rubric_model')),
  subjective        boolean NOT NULL,
  rule_ref          platform.versioned_name,
  confidence        platform.confidence NOT NULL,
  evidence_ids      uuid[] NOT NULL CHECK (cardinality(evidence_ids) >= 1),
  PRIMARY KEY (grade_result_id, dimension_id),
  CONSTRAINT dim_score_le_max CHECK (score IS NULL OR score <= max_score),
  CONSTRAINT dim_model_is_subjective CHECK (judged_by <> 'rubric_model' OR subjective),
  CONSTRAINT dim_subjective_low_conf CHECK (NOT subjective OR confidence <= 0.5),
  CONSTRAINT dim_rule_ref CHECK (judged_by = 'rubric_model' OR rule_ref IS NOT NULL)
);

-- 跨行规则（主观维度 / null 维度 → 父判分必须复核，且主观时父判分置信度 ≤ 0.5）：
-- CHECK 做不到，用约束触发器，事务提交时检查。
CREATE FUNCTION platform.grade_dimension_requires_review() RETURNS trigger
  LANGUAGE plpgsql SET search_path = pg_catalog, platform, pg_temp
  AS $$
BEGIN
  IF (NEW.subjective OR NEW.score IS NULL) AND NOT EXISTS (
       SELECT 1 FROM platform.grade_result g
        WHERE g.id = NEW.grade_result_id AND g.human_review_required
          AND (NOT NEW.subjective OR ('subjective_dimension' = ANY (g.review_reasons) AND g.confidence <= 0.5))
          AND (NEW.score IS NOT NULL OR 'dimension_unavailable' = ANY (g.review_reasons))) THEN
    RAISE EXCEPTION 'grade % has a subjective or unavailable dimension but is not flagged for review', NEW.grade_result_id
      USING ERRCODE = 'check_violation';
  END IF;
  RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER grade_dimension_requires_review
  AFTER INSERT OR UPDATE ON platform.grade_dimension
  DEFERRABLE INITIALLY DEFERRED
  FOR EACH ROW EXECUTE FUNCTION platform.grade_dimension_requires_review();

-- ---------- 更正：只能单向、一次、指向同一对象的更新记录（安全审查阻断 6） ----------
-- 第一步（BEFORE UPDATE）：superseded_by 只能从 NULL 改成别的记录，改过就不能再改；superseded_at 由库写 now()。
CREATE FUNCTION platform.guard_supersede() RETURNS trigger
  LANGUAGE plpgsql SET search_path = pg_catalog, platform, pg_temp
  AS $$
BEGIN
  IF OLD.superseded_by IS NOT NULL THEN
    RAISE EXCEPTION '% % has already been superseded', TG_TABLE_NAME, OLD.id USING ERRCODE = 'check_violation';
  END IF;
  IF NEW.superseded_by IS NULL OR NEW.superseded_by = OLD.id THEN
    RAISE EXCEPTION 'superseded_by must point to another record' USING ERRCODE = 'check_violation';
  END IF;
  NEW.superseded_at := now();
  RETURN NEW;
END $$;

-- 插入时（安全审查 R6b）：新记录不能一出生就是“已被取代”；创建时间由库写 now()，不能伪造。
CREATE FUNCTION platform.guard_inference_insert() RETURNS trigger
  LANGUAGE plpgsql SET search_path = pg_catalog, platform, pg_temp
  AS $$
BEGIN
  IF NEW.superseded_by IS NOT NULL OR NEW.superseded_at IS NOT NULL THEN
    RAISE EXCEPTION 'a new % cannot be inserted as already superseded', TG_TABLE_NAME USING ERRCODE = 'check_violation';
  END IF;
  IF TG_TABLE_NAME = 'mastery_estimate' THEN
    IF NOT NEW.is_current THEN
      RAISE EXCEPTION 'a new mastery_estimate must be current' USING ERRCODE = 'check_violation';
    END IF;
    NEW.computed_at := now();
  ELSE
    NEW.created_at := now();
  END IF;
  RETURN NEW;
END $$;

-- 第二步（提交时检查）：被指向的新记录必须是同一学生、同一对象，且不早于旧记录；
-- 被指向的新记录自己不能已被取代（防止成环，安全审查 R6b）；
-- 待复核的判分，只能被同样待复核的判分取代，除非复核已由教研人员经 resolve_review_task 结案（R6a）。
CREATE FUNCTION platform.check_supersede_target() RETURNS trigger
  LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, platform, pg_temp
  AS $$
DECLARE ok boolean;
BEGIN
  IF TG_TABLE_NAME = 'skill_observation' THEN
    SELECT EXISTS (SELECT 1 FROM platform.skill_observation n WHERE n.id = NEW.superseded_by AND n.superseded_by IS NULL
                     AND n.student_id = OLD.student_id AND n.subject = OLD.subject
                     AND n.skill_local_id = OLD.skill_local_id AND n.created_at >= OLD.created_at) INTO ok;
  ELSIF TG_TABLE_NAME = 'error_attribution' THEN
    SELECT EXISTS (SELECT 1 FROM platform.error_attribution n WHERE n.id = NEW.superseded_by AND n.superseded_by IS NULL
                     AND n.student_id = OLD.student_id AND n.subject = OLD.subject
                     AND n.skill_local_id IS NOT DISTINCT FROM OLD.skill_local_id
                     AND n.created_at >= OLD.created_at) INTO ok;
  ELSIF TG_TABLE_NAME = 'mastery_estimate' THEN
    SELECT EXISTS (SELECT 1 FROM platform.mastery_estimate n WHERE n.id = NEW.superseded_by AND n.is_current
                     AND n.student_id = OLD.student_id AND n.subject = OLD.subject
                     AND n.skill_local_id = OLD.skill_local_id AND n.computed_at >= OLD.computed_at) INTO ok;
  ELSIF TG_TABLE_NAME = 'grade_result' THEN
    SELECT EXISTS (SELECT 1 FROM platform.grade_result n WHERE n.id = NEW.superseded_by AND n.superseded_by IS NULL
                     AND n.student_id = OLD.student_id AND n.subject = OLD.subject
                     AND n.attempt_id = OLD.attempt_id AND n.created_at >= OLD.created_at
                     AND (NOT OLD.human_review_required OR n.human_review_required
                          OR EXISTS (SELECT 1 FROM platform.review_task r
                                      JOIN platform.account a ON a.id = r.resolved_by_account AND a.kind = 'staff'
                                      WHERE r.source_kind = 'grade_result' AND r.source_id = OLD.id
                                        AND r.status = 'resolved'))) INTO ok;
  END IF;
  IF NOT coalesce(ok, false) THEN
    RAISE EXCEPTION 'invalid supersede target for % %', TG_TABLE_NAME, OLD.id USING ERRCODE = 'check_violation';
  END IF;
  RETURN NULL;
END $$;

CREATE TRIGGER guard_supersede BEFORE UPDATE ON platform.skill_observation
  FOR EACH ROW EXECUTE FUNCTION platform.guard_supersede();
CREATE CONSTRAINT TRIGGER check_supersede_target AFTER UPDATE ON platform.skill_observation
  DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION platform.check_supersede_target();
CREATE TRIGGER guard_supersede BEFORE UPDATE ON platform.error_attribution
  FOR EACH ROW EXECUTE FUNCTION platform.guard_supersede();
CREATE CONSTRAINT TRIGGER check_supersede_target AFTER UPDATE ON platform.error_attribution
  DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION platform.check_supersede_target();
CREATE TRIGGER guard_supersede BEFORE UPDATE ON platform.mastery_estimate
  FOR EACH ROW EXECUTE FUNCTION platform.guard_supersede();
CREATE CONSTRAINT TRIGGER check_supersede_target AFTER UPDATE ON platform.mastery_estimate
  DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION platform.check_supersede_target();
CREATE TRIGGER guard_supersede BEFORE UPDATE ON platform.grade_result
  FOR EACH ROW EXECUTE FUNCTION platform.guard_supersede();
CREATE CONSTRAINT TRIGGER check_supersede_target AFTER UPDATE ON platform.grade_result
  DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION platform.check_supersede_target();

CREATE TRIGGER guard_inference_insert BEFORE INSERT ON platform.skill_observation
  FOR EACH ROW EXECUTE FUNCTION platform.guard_inference_insert();
CREATE TRIGGER guard_inference_insert BEFORE INSERT ON platform.error_attribution
  FOR EACH ROW EXECUTE FUNCTION platform.guard_inference_insert();
CREATE TRIGGER guard_inference_insert BEFORE INSERT ON platform.mastery_estimate
  FOR EACH ROW EXECUTE FUNCTION platform.guard_inference_insert();
CREATE TRIGGER guard_inference_insert BEFORE INSERT ON platform.grade_result
  FOR EACH ROW EXECUTE FUNCTION platform.guard_inference_insert();

ALTER FUNCTION platform.check_supersede_target() OWNER TO starbuddy_authz;
REVOKE EXECUTE ON FUNCTION platform.check_supersede_target() FROM PUBLIC;
GRANT SELECT ON platform.skill_observation, platform.error_attribution, platform.mastery_estimate,
  platform.grade_result TO starbuddy_authz;

-- ---------- 权限与 RLS ----------
GRANT SELECT, INSERT ON platform.skill_observation, platform.error_attribution, platform.mastery_estimate,
  platform.grade_result, platform.grade_dimension TO starbuddy_learning;
-- 更正只能改“被取代”相关的列，不能改推断内容本身
GRANT UPDATE (superseded_by, superseded_at) ON platform.skill_observation, platform.error_attribution,
  platform.grade_result TO starbuddy_learning;
GRANT UPDATE (is_current, superseded_by, superseded_at) ON platform.mastery_estimate TO starbuddy_learning;
GRANT SELECT ON platform.skill_observation, platform.error_attribution, platform.mastery_estimate,
  platform.grade_result, platform.grade_dimension TO starbuddy_bff;

ALTER TABLE platform.skill_observation ENABLE ROW LEVEL SECURITY;
ALTER TABLE platform.skill_observation FORCE ROW LEVEL SECURITY;
CREATE POLICY obs_read ON platform.skill_observation FOR SELECT TO starbuddy_bff USING (platform.can_read_student(student_id));
CREATE POLICY obs_service ON platform.skill_observation FOR ALL TO starbuddy_learning USING (true) WITH CHECK (true);

ALTER TABLE platform.error_attribution ENABLE ROW LEVEL SECURITY;
ALTER TABLE platform.error_attribution FORCE ROW LEVEL SECURITY;
CREATE POLICY attr_read ON platform.error_attribution FOR SELECT TO starbuddy_bff USING (platform.can_read_student(student_id));
CREATE POLICY attr_service ON platform.error_attribution FOR ALL TO starbuddy_learning USING (true) WITH CHECK (true);

ALTER TABLE platform.mastery_estimate ENABLE ROW LEVEL SECURITY;
ALTER TABLE platform.mastery_estimate FORCE ROW LEVEL SECURITY;
CREATE POLICY mastery_read ON platform.mastery_estimate FOR SELECT TO starbuddy_bff USING (platform.can_read_student(student_id));
CREATE POLICY mastery_service ON platform.mastery_estimate FOR ALL TO starbuddy_learning USING (true) WITH CHECK (true);

ALTER TABLE platform.grade_result ENABLE ROW LEVEL SECURITY;
ALTER TABLE platform.grade_result FORCE ROW LEVEL SECURITY;
CREATE POLICY grade_read ON platform.grade_result FOR SELECT TO starbuddy_bff USING (platform.can_read_student(student_id));
CREATE POLICY grade_service ON platform.grade_result FOR ALL TO starbuddy_learning USING (true) WITH CHECK (true);

ALTER TABLE platform.grade_dimension ENABLE ROW LEVEL SECURITY;
ALTER TABLE platform.grade_dimension FORCE ROW LEVEL SECURITY;
CREATE POLICY dim_read ON platform.grade_dimension FOR SELECT TO starbuddy_bff
  USING (EXISTS (SELECT 1 FROM platform.grade_result g
                 WHERE g.id = grade_result_id AND platform.can_read_student(g.student_id)));
CREATE POLICY dim_service ON platform.grade_dimension FOR ALL TO starbuddy_learning USING (true) WITH CHECK (true);
