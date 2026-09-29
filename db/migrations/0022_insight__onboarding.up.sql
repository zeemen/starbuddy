-- 0022 insight · 建档事实层：学生自述、家长观察、考试记录、建档进度（specs/sie-01-onboarding/spec.md 设计要点 1、2、4）
-- 状态：草稿。只写文件，由人先在副本演练再执行（CLAUDE.md 必停项：迁移、RLS 与权限、画像可见范围）。依赖 0002、0010、0021。
-- 依据：SIE V1.1 第三章采集清单与“三层数据、四种来源，永不混算”，第九章 exam_record / self_report / guardian_report；
--       字段字典 docs/design/sie/field-dictionary.v0.9.yaml（存储位置、来源类型、可见范围以它为准）；对照文档决定 #3（insight.* 只放新增对象）。
-- 规则：
--   - 本迁移只建事实层（学生自述、家长观察、客观记录），不产生任何判断（规则 11），不做任何推断（规则 12）；
--   - 来源分表：学生自述只在 self_report，家长观察只在 guardian_report；同一字段两边说法不同都存，不合并（规则 6）；
--   - 改答案不覆盖：旧行标 superseded_at，新增一行；
--   - 互不可见（v2.0 验收）：学生读不到任何家长观察，家长读不到学生自述原文；BFF 对这些表没有任何表权限，只经 insight.onboarding_view 读（按调用者身份裁剪）；
--   - 写入只经 SECURITY DEFINER 函数（属主 authz），按当前账号核对：学生本人写自述，已绑定家长写家长观察，两者都能录考试记录与建档进度；
--   - 字段白名单 insight.report_field 与字典一致（docs/design/sie/check_field_dictionary.py 核对），取值形状由 insight.valid_report_value 校验。

-- ---------- 角色：运营报表只读身份（待人确认 #6 默认；由人在 db/bootstrap/roles.sql 创建） ----------
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'starbuddy_report') THEN
    RAISE EXCEPTION '角色 starbuddy_report 不存在：请先执行 db/bootstrap/roles.sql';
  END IF;
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'starbuddy_report' AND (rolsuper OR rolbypassrls OR rolcanlogin)) THEN
    RAISE EXCEPTION 'starbuddy_report 必须是 NOLOGIN、非 SUPERUSER、非 BYPASSRLS';
  END IF;
  IF EXISTS (SELECT 1 FROM pg_roles r
              WHERE NOT r.rolsuper AND pg_has_role(r.oid, 'starbuddy_report', 'MEMBER')
                AND (pg_has_role(r.oid, 'starbuddy_bff', 'MEMBER') OR pg_has_role(r.oid, 'starbuddy_learning', 'MEMBER')
                     OR pg_has_role(r.oid, 'starbuddy_review', 'MEMBER') OR pg_has_role(r.oid, 'starbuddy_scheduler', 'MEMBER')
                     OR pg_has_role(r.oid, 'starbuddy_insight', 'MEMBER') OR pg_has_role(r.oid, 'starbuddy_kge', 'MEMBER'))) THEN
    RAISE EXCEPTION '同一个角色不能同时继承 starbuddy_report 与其他应用角色';
  END IF;
END $$;
GRANT USAGE ON SCHEMA insight TO starbuddy_report;

-- ---------- 字段白名单：来自字段字典（storage 为 insight.self_report.value / insight.guardian_report.value 的字段） ----------
CREATE TABLE insight.report_field (
  field_id    text NOT NULL,
  respondent  text NOT NULL CHECK (respondent IN ('self', 'guardian')),
  PRIMARY KEY (field_id, respondent)
);
COMMENT ON TABLE insight.report_field IS '自述与家长观察的字段白名单（0022）：与字段字典一致，由 check_field_dictionary.py 核对';
INSERT INTO insight.report_field (field_id, respondent) VALUES
  ('outer.g1.weekly_available_time', 'self'), ('outer.g1.weekly_available_time', 'guardian'),
  ('outer.g1.devices', 'self'), ('outer.g1.devices', 'guardian'),
  ('outer.g1.tutoring', 'self'), ('outer.g1.tutoring', 'guardian'),
  ('outer.g8.cooperation_capacity', 'guardian'),
  ('middle.g6.learning_mode_preference', 'self'),
  ('middle.g6.self_pain_points', 'self'),
  ('middle.g6.stated_interests', 'self'),
  ('through.g9.focus_subject', 'self'),
  ('through.g9.near_term_goal', 'self');
ALTER TABLE insight.report_field ENABLE ROW LEVEL SECURITY;
ALTER TABLE insight.report_field FORCE ROW LEVEL SECURITY;
REVOKE ALL ON insight.report_field FROM PUBLIC;
GRANT SELECT ON insight.report_field TO starbuddy_authz;

-- 自由文本：1–200 字，不含控制字符
CREATE FUNCTION insight.valid_text(p jsonb) RETURNS boolean LANGUAGE sql IMMUTABLE SET search_path = pg_catalog
  AS $$ SELECT jsonb_typeof(p) = 'string' AND char_length(p #>> '{}') BETWEEN 1 AND 200 AND (p #>> '{}') !~ '[\x01-\x1f\x7f]' $$;
-- 字符串数组：元素都在给定集合里、不重复
CREATE FUNCTION insight.valid_set(p jsonb, p_allowed text[], p_min integer) RETURNS boolean LANGUAGE sql IMMUTABLE SET search_path = pg_catalog
  AS $$ SELECT jsonb_typeof(p) = 'array' AND jsonb_array_length(p) >= p_min
           AND NOT EXISTS (SELECT 1 FROM jsonb_array_elements(p) e WHERE jsonb_typeof(e) <> 'string' OR NOT (e #>> '{}') = ANY (p_allowed))
           AND (SELECT count(DISTINCT e) FROM jsonb_array_elements(p) e) = jsonb_array_length(p) $$;

-- 取值形状（spec 设计要点 3 的选项；文案待教研定稿，编码不变）。只接受列出的键，多一个键也拒绝
CREATE FUNCTION insight.valid_report_value(p_field text, p jsonb) RETURNS boolean
  LANGUAGE sql IMMUTABLE SET search_path = pg_catalog, insight
  AS $$
  SELECT jsonb_typeof(p) = 'object' AND CASE p_field
    WHEN 'outer.g1.weekly_available_time' THEN
      (SELECT array_agg(k ORDER BY k) FROM jsonb_object_keys(p) k) = ARRAY['minutes']
      AND jsonb_typeof(p -> 'minutes') = 'number' AND (p ->> 'minutes')::numeric BETWEEN 0 AND 4200 AND (p ->> 'minutes')::numeric % 1 = 0
    WHEN 'outer.g1.devices' THEN
      (SELECT array_agg(k ORDER BY k) FROM jsonb_object_keys(p) k) = ARRAY['devices']
      AND insight.valid_set(p -> 'devices', ARRAY['phone', 'tablet', 'computer', 'none'], 1)
    WHEN 'outer.g1.tutoring' THEN
      (SELECT array_agg(k ORDER BY k) FROM jsonb_object_keys(p) k) = ARRAY['subjects']
      AND insight.valid_set(p -> 'subjects', ARRAY['wenli', 'aya', 'zhixing', 'echo', 'other'], 0)
    WHEN 'outer.g8.cooperation_capacity' THEN
      (SELECT array_agg(k ORDER BY k) FROM jsonb_object_keys(p) k) = ARRAY['level']
      AND (p ->> 'level') IN ('none', 'occasional', 'regular')
    WHEN 'middle.g6.learning_mode_preference' THEN
      (SELECT array_agg(k ORDER BY k) FROM jsonb_object_keys(p) k) = ARRAY['mode']
      AND (p ->> 'mode') IN ('step_by_step', 'explain_then_practice', 'independent', 'challenge')
    WHEN 'middle.g6.self_pain_points' THEN
      (SELECT array_agg(k ORDER BY k) FROM jsonb_object_keys(p) k) IN ('{points}', '{note,points}')
      AND insight.valid_set(p -> 'points', ARRAY['concept', 'decompose', 'read_question', 'time', 'writing'], 0)
      AND (NOT p ? 'note' OR insight.valid_text(p -> 'note'))
    WHEN 'middle.g6.stated_interests' THEN
      (SELECT array_agg(k ORDER BY k) FROM jsonb_object_keys(p) k) = ARRAY['text']
      AND insight.valid_text(p -> 'text')
    WHEN 'through.g9.focus_subject' THEN
      (SELECT array_agg(k ORDER BY k) FROM jsonb_object_keys(p) k) = ARRAY['subject']
      AND (p ->> 'subject') IN ('wenli', 'aya', 'zhixing', 'echo')
    WHEN 'through.g9.near_term_goal' THEN
      (SELECT array_agg(k ORDER BY k) FROM jsonb_object_keys(p) k) = ARRAY['text']
      AND insight.valid_text(p -> 'text')
    ELSE false
  END $$;

-- ---------- 事实表 ----------
CREATE TABLE insight.self_report (
  id                     uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  student_id             uuid NOT NULL REFERENCES platform.student_profile (id) ON DELETE CASCADE,
  field_id               text NOT NULL,
  value                  jsonb NOT NULL CHECK (pg_column_size(value) <= 2048),
  respondent_account_id  uuid NOT NULL REFERENCES platform.account (id),
  source_type            insight.source_type NOT NULL DEFAULT 'self' CHECK (source_type = 'self'),
  created_at             timestamptz NOT NULL DEFAULT now(),
  superseded_at          timestamptz,
  CONSTRAINT self_report_value CHECK (insight.valid_report_value(field_id, value))
);
CREATE UNIQUE INDEX self_report_current ON insight.self_report (student_id, field_id) WHERE superseded_at IS NULL;
COMMENT ON TABLE insight.self_report IS '学生自述（0022，SIE-01）：只由学生本人经 insight.submit_self_report 写入；家长读不到原文';

CREATE TABLE insight.guardian_report (
  id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  student_id           uuid NOT NULL REFERENCES platform.student_profile (id) ON DELETE CASCADE,
  guardian_account_id  uuid NOT NULL REFERENCES platform.account (id),
  field_id             text NOT NULL,
  value                jsonb NOT NULL CHECK (pg_column_size(value) <= 2048),
  source_type          insight.source_type NOT NULL DEFAULT 'guardian' CHECK (source_type = 'guardian'),
  created_at           timestamptz NOT NULL DEFAULT now(),
  superseded_at        timestamptz,
  CONSTRAINT guardian_report_value CHECK (insight.valid_report_value(field_id, value))
);
-- 多个监护人各存各的，不合并
CREATE UNIQUE INDEX guardian_report_current ON insight.guardian_report (student_id, guardian_account_id, field_id) WHERE superseded_at IS NULL;
COMMENT ON TABLE insight.guardian_report IS
  '家长观察（结构化字段，0022，SIE-01）：只由已绑定家长经 insight.submit_guardian_report 写入；学生读不到。五个既有自由文本主题仍在 platform.guardian_report';

CREATE TABLE insight.exam_record (
  id                     uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  student_id             uuid NOT NULL REFERENCES platform.student_profile (id) ON DELETE CASCADE,
  subject                platform.subject NOT NULL,
  exam_type              text NOT NULL CHECK (exam_type IN ('monthly', 'midterm', 'final', 'mock1', 'mock2', 'other')),
  exam_date              date NOT NULL CHECK (exam_date BETWEEN DATE '2015-01-01' AND DATE '2100-01-01'),
  full_score             numeric(5, 1) NOT NULL CHECK (full_score > 0 AND full_score <= 300),
  raw_score              numeric(5, 1) NOT NULL CHECK (raw_score >= 0),
  scope_node_ids         text[] NOT NULL DEFAULT '{}' CHECK (cardinality(scope_node_ids) <= 64),  -- KGE 节点 ID；知识地图发布前为空
  scope_note             text CHECK (char_length(scope_note) BETWEEN 1 AND 200 AND scope_note !~ '[\x01-\x1f\x7f]'),
  difficulty_note        text CHECK (char_length(difficulty_note) BETWEEN 1 AND 200 AND difficulty_note !~ '[\x01-\x1f\x7f]'),
  rank_band              text CHECK (rank_band IN ('top10', 'top30', 'middle', 'bottom30', 'unknown')),  -- 只收五档，不收具体名次
  source                 text NOT NULL DEFAULT 'manual' CHECK (source = 'manual'),                    -- 上传随 KGE 上传管道
  entered_by_role        text NOT NULL CHECK (entered_by_role IN ('self', 'guardian')),
  entered_by_account_id  uuid NOT NULL REFERENCES platform.account (id),
  confirmed_by           text CHECK (confirmed_by IN ('self', 'guardian')),
  source_type            insight.source_type NOT NULL DEFAULT 'objective' CHECK (source_type = 'objective'),
  created_at             timestamptz NOT NULL DEFAULT now(),
  superseded_at          timestamptz,
  CONSTRAINT exam_record_score CHECK (raw_score <= full_score),
  -- 排名区间只收家长录入（学生读不到，也不让学生录；视图里的裁剪是第二道）
  CONSTRAINT exam_record_rank_guardian CHECK (rank_band IS NULL OR entered_by_role = 'guardian')
);
CREATE INDEX exam_record_student_idx ON insight.exam_record (student_id, exam_date);
COMMENT ON TABLE insight.exam_record IS
  '学校考试记录（0022，SIE-01）：学生或已绑定家长手工录入，各录各的，同一场考试两边分数不同都存；排名区间与难度说明学生看不到（字典可见范围）';

CREATE TABLE insight.onboarding_progress (
  id                     bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  student_id             uuid NOT NULL REFERENCES platform.student_profile (id) ON DELETE CASCADE,
  respondent_role        text NOT NULL CHECK (respondent_role IN ('self', 'guardian')),
  respondent_account_id  uuid NOT NULL REFERENCES platform.account (id),
  step                   text NOT NULL CHECK (step ~ '^[a-z_]{1,32}$'),
  status                 text NOT NULL CHECK (status IN ('started', 'completed', 'skipped')),
  at                     timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX onboarding_progress_student_idx ON insight.onboarding_progress (student_id, respondent_role, at);
COMMENT ON TABLE insight.onboarding_progress IS '建档进度（0022，SIE-01）：只追加，不存答案；用于完成率与时长';

-- 只追加：改答案只能把旧行标为 superseded（一次），其余列都不能改；不能删除（删除随 SIE 导出与删除流程另做）
CREATE FUNCTION insight.guard_fact_update() RETURNS trigger LANGUAGE plpgsql SET search_path = pg_catalog
  AS $$
BEGIN
  IF TG_OP = 'DELETE' OR TG_OP = 'TRUNCATE' THEN
    RAISE EXCEPTION '建档事实只追加，不能删除' USING ERRCODE = 'insufficient_privilege';
  END IF;
  IF OLD.superseded_at IS NOT NULL OR NEW.superseded_at IS NULL
     OR (to_jsonb(NEW) - 'superseded_at') IS DISTINCT FROM (to_jsonb(OLD) - 'superseded_at') THEN
    RAISE EXCEPTION '建档事实只能把当前行标为已被替换' USING ERRCODE = 'insufficient_privilege';
  END IF;
  RETURN NEW;
END $$;
REVOKE EXECUTE ON FUNCTION insight.guard_fact_update() FROM PUBLIC;
CREATE TRIGGER guard_fact_update BEFORE UPDATE OR DELETE ON insight.self_report FOR EACH ROW EXECUTE FUNCTION insight.guard_fact_update();
CREATE TRIGGER guard_fact_update BEFORE UPDATE OR DELETE ON insight.guardian_report FOR EACH ROW EXECUTE FUNCTION insight.guard_fact_update();
CREATE TRIGGER guard_fact_update BEFORE UPDATE OR DELETE ON insight.exam_record FOR EACH ROW EXECUTE FUNCTION insight.guard_fact_update();
CREATE TRIGGER guard_fact_truncate BEFORE TRUNCATE ON insight.self_report FOR EACH STATEMENT EXECUTE FUNCTION insight.guard_fact_update();
CREATE TRIGGER guard_fact_truncate BEFORE TRUNCATE ON insight.guardian_report FOR EACH STATEMENT EXECUTE FUNCTION insight.guard_fact_update();
CREATE TRIGGER guard_fact_truncate BEFORE TRUNCATE ON insight.exam_record FOR EACH STATEMENT EXECUTE FUNCTION insight.guard_fact_update();
CREATE FUNCTION insight.reject_progress_change() RETURNS trigger LANGUAGE plpgsql SET search_path = pg_catalog
  AS $$ BEGIN RAISE EXCEPTION '建档进度只追加' USING ERRCODE = 'insufficient_privilege'; END $$;
REVOKE EXECUTE ON FUNCTION insight.reject_progress_change() FROM PUBLIC;
CREATE TRIGGER reject_progress_change BEFORE UPDATE OR DELETE ON insight.onboarding_progress FOR EACH ROW EXECUTE FUNCTION insight.reject_progress_change();
CREATE TRIGGER reject_progress_truncate BEFORE TRUNCATE ON insight.onboarding_progress FOR EACH STATEMENT EXECUTE FUNCTION insight.reject_progress_change();

-- ---------- 权限：BFF 对这四张表没有任何表权限（只经函数）；洞察服务与学习服务读全部 ----------
ALTER TABLE insight.self_report ENABLE ROW LEVEL SECURITY;
ALTER TABLE insight.self_report FORCE ROW LEVEL SECURITY;
ALTER TABLE insight.guardian_report ENABLE ROW LEVEL SECURITY;
ALTER TABLE insight.guardian_report FORCE ROW LEVEL SECURITY;
ALTER TABLE insight.exam_record ENABLE ROW LEVEL SECURITY;
ALTER TABLE insight.exam_record FORCE ROW LEVEL SECURITY;
ALTER TABLE insight.onboarding_progress ENABLE ROW LEVEL SECURITY;
ALTER TABLE insight.onboarding_progress FORCE ROW LEVEL SECURITY;
REVOKE ALL ON insight.self_report, insight.guardian_report, insight.exam_record, insight.onboarding_progress FROM PUBLIC;
GRANT SELECT ON insight.self_report, insight.guardian_report, insight.exam_record TO starbuddy_insight, starbuddy_learning;
GRANT SELECT ON insight.onboarding_progress TO starbuddy_insight;
GRANT SELECT, INSERT, UPDATE ON insight.self_report, insight.guardian_report, insight.exam_record TO starbuddy_authz;
GRANT SELECT, INSERT ON insight.onboarding_progress TO starbuddy_authz;
CREATE POLICY self_report_service_read ON insight.self_report FOR SELECT TO starbuddy_insight, starbuddy_learning USING (true);
CREATE POLICY guardian_report_service_read ON insight.guardian_report FOR SELECT TO starbuddy_insight, starbuddy_learning USING (true);
CREATE POLICY exam_record_service_read ON insight.exam_record FOR SELECT TO starbuddy_insight, starbuddy_learning USING (true);
CREATE POLICY onboarding_progress_service_read ON insight.onboarding_progress FOR SELECT TO starbuddy_insight USING (true);

-- ---------- 写入函数（属主 authz；只授 BFF，BFF 以当前账号调用） ----------
CREATE FUNCTION insight.submit_self_report(p_student uuid, p_field text, p_value jsonb) RETURNS uuid
  LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog, insight, platform, pg_temp
  AS $$
DECLARE v_id uuid;
BEGIN
  IF p_student IS NULL OR NOT platform.is_self_student(p_student) THEN
    RAISE EXCEPTION 'not allowed' USING ERRCODE = 'insufficient_privilege';
  END IF;
  IF NOT EXISTS (SELECT 1 FROM insight.report_field WHERE field_id = p_field AND respondent = 'self')
     OR p_value IS NULL OR NOT insight.valid_report_value(p_field, p_value) THEN
    RAISE EXCEPTION '字段或取值不合要求' USING ERRCODE = 'invalid_parameter_value';
  END IF;
  UPDATE insight.self_report SET superseded_at = now() WHERE student_id = p_student AND field_id = p_field AND superseded_at IS NULL;
  INSERT INTO insight.self_report (student_id, field_id, value, respondent_account_id)
  VALUES (p_student, p_field, p_value, platform.current_account_id()) RETURNING id INTO v_id;
  RETURN v_id;
END $$;

CREATE FUNCTION insight.submit_guardian_report(p_student uuid, p_field text, p_value jsonb) RETURNS uuid
  LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog, insight, platform, pg_temp
  AS $$
DECLARE v_id uuid;
BEGIN
  IF p_student IS NULL OR NOT platform.is_linked_guardian(p_student) THEN
    RAISE EXCEPTION 'not allowed' USING ERRCODE = 'insufficient_privilege';
  END IF;
  IF NOT EXISTS (SELECT 1 FROM insight.report_field WHERE field_id = p_field AND respondent = 'guardian')
     OR p_value IS NULL OR NOT insight.valid_report_value(p_field, p_value) THEN
    RAISE EXCEPTION '字段或取值不合要求' USING ERRCODE = 'invalid_parameter_value';
  END IF;
  UPDATE insight.guardian_report SET superseded_at = now()
   WHERE student_id = p_student AND guardian_account_id = platform.current_account_id() AND field_id = p_field AND superseded_at IS NULL;
  INSERT INTO insight.guardian_report (student_id, guardian_account_id, field_id, value)
  VALUES (p_student, platform.current_account_id(), p_field, p_value) RETURNING id INTO v_id;
  RETURN v_id;
END $$;

-- 调用者身份：学生本人 → self；已绑定家长 → guardian；其他 → 拒绝
CREATE FUNCTION insight.onboarding_role(p_student uuid) RETURNS text
  LANGUAGE sql STABLE SET search_path = pg_catalog, platform
  AS $$ SELECT CASE WHEN p_student IS NULL THEN NULL WHEN platform.is_self_student(p_student) THEN 'self'
                    WHEN platform.is_linked_guardian(p_student) THEN 'guardian' END $$;

CREATE FUNCTION insight.submit_exam_record(p_student uuid, p_subject platform.subject, p_exam_type text, p_exam_date date,
                                           p_full_score numeric, p_raw_score numeric, p_rank_band text, p_scope_note text,
                                           p_difficulty_note text) RETURNS uuid
  LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog, insight, platform, pg_temp
  AS $$
DECLARE
  v_role text := insight.onboarding_role(p_student);
  v_id uuid;
BEGIN
  IF v_role IS NULL THEN
    RAISE EXCEPTION 'not allowed' USING ERRCODE = 'insufficient_privilege';
  END IF;
  INSERT INTO insight.exam_record (student_id, subject, exam_type, exam_date, full_score, raw_score, rank_band, scope_note,
                                   difficulty_note, entered_by_role, entered_by_account_id, confirmed_by)
  VALUES (p_student, p_subject, p_exam_type, p_exam_date, p_full_score, p_raw_score, p_rank_band, p_scope_note,
          p_difficulty_note, v_role, platform.current_account_id(), v_role)
  RETURNING id INTO v_id;
  RETURN v_id;
END $$;

CREATE FUNCTION insight.record_onboarding_step(p_student uuid, p_step text, p_status text) RETURNS void
  LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog, insight, platform, pg_temp
  AS $$
DECLARE v_role text := insight.onboarding_role(p_student);
BEGIN
  IF v_role IS NULL THEN
    RAISE EXCEPTION 'not allowed' USING ERRCODE = 'insufficient_privilege';
  END IF;
  INSERT INTO insight.onboarding_progress (student_id, respondent_role, respondent_account_id, step, status)
  VALUES (p_student, v_role, platform.current_account_id(), p_step, p_status);
END $$;

-- ---------- 读取：按调用者身份裁剪（互不可见） ----------
-- 学生：自己的当前自述、自己录的考试记录（不含排名区间与难度说明）、各科教材版本、自己的进度
-- 家长：自己写的当前家长观察、该孩子全部考试记录（含排名区间与难度说明）、各科教材版本、自己的进度
-- 其他：NULL（BFF 按“不存在”处理）
CREATE FUNCTION insight.onboarding_view(p_student uuid) RETURNS jsonb
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
              WHERE e.student_id = p_student AND e.superseded_at IS NULL AND (v_role = 'guardian' OR e.entered_by_account_id = v_me)),
    'textbooks', (SELECT coalesce(jsonb_object_agg(t.subject, t.textbook_version), '{}') FROM platform.student_subject_textbook t
                   WHERE t.student_id = p_student),
    'progress', (SELECT coalesce(jsonb_agg(jsonb_build_object('step', p.step, 'status', p.status) ORDER BY p.at, p.id), '[]')
                   FROM insight.onboarding_progress p WHERE p.student_id = p_student AND p.respondent_account_id = v_me));
END $$;

-- ---------- 完成率与时长（只出聚合数；授洞察服务与运营报表身份） ----------
-- 每个（学生，填写方）第一次 started 到第一次 completed；completed 之前的跳过次数按步骤汇总
CREATE FUNCTION insight.onboarding_stats(p_from timestamptz, p_to timestamptz)
  RETURNS TABLE (respondent_role text, started bigint, completed bigint, completion_rate numeric, median_seconds numeric, skipped_steps jsonb)
  LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, insight, pg_temp
  AS $$
  WITH s AS (
    SELECT p.student_id, p.respondent_role, min(p.at) FILTER (WHERE p.step = 'onboarding' AND p.status = 'started') AS t0,
           min(p.at) FILTER (WHERE p.step = 'onboarding' AND p.status = 'completed') AS t1
      FROM insight.onboarding_progress p
     GROUP BY p.student_id, p.respondent_role
  ), w AS (SELECT * FROM s WHERE t0 >= p_from AND t0 < p_to),
  k AS (
    SELECT p.respondent_role, p.step, count(*) AS n
      FROM insight.onboarding_progress p JOIN w ON w.student_id = p.student_id AND w.respondent_role = p.respondent_role
     WHERE p.status = 'skipped'
     GROUP BY p.respondent_role, p.step
  )
  SELECT r.role, count(w.t0), count(w.t1),
         CASE WHEN count(w.t0) = 0 THEN NULL ELSE round(count(w.t1)::numeric / count(w.t0), 3) END,
         percentile_cont(0.5) WITHIN GROUP (ORDER BY extract(epoch FROM w.t1 - w.t0)) FILTER (WHERE w.t1 IS NOT NULL)::numeric,
         coalesce((SELECT jsonb_object_agg(k.step, k.n) FROM k WHERE k.respondent_role = r.role), '{}')
    FROM unnest(ARRAY['self', 'guardian']) r(role) LEFT JOIN w ON w.respondent_role = r.role
   GROUP BY r.role
   ORDER BY r.role $$;

ALTER FUNCTION insight.submit_self_report(uuid, text, jsonb) OWNER TO starbuddy_authz;
ALTER FUNCTION insight.submit_guardian_report(uuid, text, jsonb) OWNER TO starbuddy_authz;
ALTER FUNCTION insight.submit_exam_record(uuid, platform.subject, text, date, numeric, numeric, text, text, text) OWNER TO starbuddy_authz;
ALTER FUNCTION insight.record_onboarding_step(uuid, text, text) OWNER TO starbuddy_authz;
ALTER FUNCTION insight.onboarding_view(uuid) OWNER TO starbuddy_authz;
ALTER FUNCTION insight.onboarding_stats(timestamptz, timestamptz) OWNER TO starbuddy_authz;
REVOKE EXECUTE ON FUNCTION insight.submit_self_report(uuid, text, jsonb), insight.submit_guardian_report(uuid, text, jsonb),
  insight.submit_exam_record(uuid, platform.subject, text, date, numeric, numeric, text, text, text),
  insight.record_onboarding_step(uuid, text, text), insight.onboarding_view(uuid), insight.onboarding_stats(timestamptz, timestamptz),
  insight.onboarding_role(uuid), insight.valid_report_value(text, jsonb), insight.valid_text(jsonb), insight.valid_set(jsonb, text[], integer)
  FROM PUBLIC;
GRANT EXECUTE ON FUNCTION insight.submit_self_report(uuid, text, jsonb), insight.submit_guardian_report(uuid, text, jsonb),
  insight.submit_exam_record(uuid, platform.subject, text, date, numeric, numeric, text, text, text),
  insight.record_onboarding_step(uuid, text, text), insight.onboarding_view(uuid) TO starbuddy_bff;
GRANT EXECUTE ON FUNCTION insight.onboarding_stats(timestamptz, timestamptz) TO starbuddy_insight, starbuddy_report;
-- 函数属主与表约束要用的内部函数
GRANT EXECUTE ON FUNCTION insight.onboarding_role(uuid), insight.valid_report_value(text, jsonb), insight.valid_text(jsonb),
  insight.valid_set(jsonb, text[], integer) TO starbuddy_authz;
