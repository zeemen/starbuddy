-- 0006 platform · 家长自述与统一复核队列
-- 家长自述与证据、推断分开存（CLAUDE.md 规则 6）；家长可见可删，学生端不可见（完整方案第四章）。
-- 心理与情绪只记学习相关状态，不记诊断与倾诉（完整方案第四章），所以话题限定为家庭与学习环境。

CREATE TABLE platform.guardian_report (
  id                    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  student_id            uuid NOT NULL REFERENCES platform.student_profile (id) ON DELETE CASCADE,
  guardian_account_id   uuid NOT NULL REFERENCES platform.account (id),
  topic                 text NOT NULL CHECK (topic IN (
                          'study_routine', 'sleep_schedule', 'accompany_style', 'expectation', 'school_communication')),
  content               text NOT NULL CHECK (char_length(content) BETWEEN 1 AND 2000),
  created_at            timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX guardian_report_student_idx ON platform.guardian_report (student_id, created_at);

-- 统一复核队列：四科的 human_review_required 结果都进这里（完整方案 5.4；开发计划 冲刺 2-G）
CREATE TABLE platform.review_task (
  id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  subject           platform.subject NOT NULL,
  student_id        uuid NOT NULL REFERENCES platform.student_profile (id),
  source_kind       text NOT NULL CHECK (source_kind IN ('grade_result', 'skill_observation', 'error_attribution')),
  source_id         uuid NOT NULL,
  reasons           platform.review_reason[] NOT NULL CHECK (cardinality(reasons) >= 1),
  status            text NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'in_progress', 'resolved')),
  created_at        timestamptz NOT NULL DEFAULT now(),
  resolved_at           timestamptz,
  resolved_by_account   uuid REFERENCES platform.account (id),
  resolution_code       text CHECK (resolution_code ~ '^[a-z0-9_]{1,48}$'),
  CHECK ((status = 'resolved') = (resolved_at IS NOT NULL AND resolution_code IS NOT NULL AND resolved_by_account IS NOT NULL)),
  UNIQUE (source_kind, source_id)
);
CREATE INDEX review_task_open_idx ON platform.review_task (subject, created_at) WHERE status <> 'resolved';

GRANT SELECT, INSERT, DELETE ON platform.guardian_report TO starbuddy_bff;
GRANT SELECT ON platform.guardian_report TO starbuddy_learning;
-- 学习服务只能新建“待处理”的复核任务，不能改状态、不能结案（安全审查 R6a：被复核的一方不能自己签字）
GRANT SELECT, INSERT ON platform.review_task TO starbuddy_learning;
GRANT SELECT ON platform.review_task TO starbuddy_review, starbuddy_authz;
GRANT UPDATE (status, resolved_at, resolved_by_account, resolution_code) ON platform.review_task TO starbuddy_authz;

-- 结案只能由教研人员（staff 账号）经 starbuddy_review 调用，记录结案人
CREATE FUNCTION platform.resolve_review_task(p_task uuid, p_resolution_code text) RETURNS void
  LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog, platform, pg_temp
  AS $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM platform.account a
                  WHERE a.id = platform.current_account_id() AND a.kind = 'staff' AND a.status = 'active') THEN
    RAISE EXCEPTION 'only an active staff account can resolve review tasks' USING ERRCODE = 'insufficient_privilege';
  END IF;
  UPDATE platform.review_task
     SET status = 'resolved', resolved_at = now(), resolved_by_account = platform.current_account_id(),
         resolution_code = p_resolution_code
   WHERE id = p_task AND status <> 'resolved';
  IF NOT FOUND THEN
    RAISE EXCEPTION 'review task not found or already resolved' USING ERRCODE = 'invalid_parameter_value';
  END IF;
END $$;
ALTER FUNCTION platform.resolve_review_task(uuid, text) OWNER TO starbuddy_authz;
REVOKE EXECUTE ON FUNCTION platform.resolve_review_task(uuid, text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION platform.resolve_review_task(uuid, text) TO starbuddy_review;

ALTER TABLE platform.guardian_report ENABLE ROW LEVEL SECURITY;
ALTER TABLE platform.guardian_report FORCE ROW LEVEL SECURITY;
-- 只有写这条自述的家长能读、能删；学生本人读不到。
CREATE POLICY greport_own ON platform.guardian_report FOR SELECT TO starbuddy_bff
  USING (guardian_account_id = platform.current_account_id() AND platform.is_linked_guardian(student_id));
CREATE POLICY greport_write ON platform.guardian_report FOR INSERT TO starbuddy_bff
  WITH CHECK (guardian_account_id = platform.current_account_id() AND platform.is_linked_guardian(student_id));
CREATE POLICY greport_delete ON platform.guardian_report FOR DELETE TO starbuddy_bff
  USING (guardian_account_id = platform.current_account_id());
-- 学习服务只读，用于报告的外层（家庭与环境）字段
CREATE POLICY greport_service_read ON platform.guardian_report FOR SELECT TO starbuddy_learning USING (true);

ALTER TABLE platform.review_task ENABLE ROW LEVEL SECURITY;
ALTER TABLE platform.review_task FORCE ROW LEVEL SECURITY;
CREATE POLICY review_service_read ON platform.review_task FOR SELECT TO starbuddy_learning USING (true);
CREATE POLICY review_service_open ON platform.review_task FOR INSERT TO starbuddy_learning
  WITH CHECK (status = 'open' AND resolved_at IS NULL AND resolved_by_account IS NULL);
CREATE POLICY review_staff_read ON platform.review_task FOR SELECT TO starbuddy_review USING (true);
-- BFF 不能读复核队列。
