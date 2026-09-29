-- 0025 insight · 行为事件与“能做对吗”探针（specs/behavior-events-sprint2/spec.md，冲刺 2-L）
-- 状态：草稿。只写文件，由人先在副本演练再执行（CLAUDE.md 必停项：迁移、RLS 与权限、画像可见范围、禁用推断）。依赖 0002、0004、0010、0022。
--   - 两张表放 insight.*（对照 specs/plan-v2-alignment #3）：行为事件的来源是客观记录（objective），探针的来源是学生自述（self），规则 6；
--   - 探针不存实际对错：按关联的作答 ID 去判分表取（规则 2，同一事实不存两份）；
--   - 只有学生本人经 BFF 调写入函数；payload 只收白名单键（不收自由文本，不留任何可用于推断情绪、性格的字段，规则 12）；
--   - 只给 Agent 用：洞察服务、学习服务可读；BFF 没有任何表权限，学生、家长界面都不显示；随学生档案删除级联；
--   - 保留期未定（spec 待人确认 #4 默认）：先不自动删除。

CREATE TABLE insight.behavior_event (
  id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  student_id       uuid NOT NULL REFERENCES platform.student_profile (id) ON DELETE CASCADE,
  kind             text NOT NULL CHECK (kind IN ('session_start', 'session_end', 'plan_done', 'plan_skip', 'exit_after_errors', 'appeal')),
  payload          jsonb NOT NULL DEFAULT '{}',
  source_type      insight.source_type NOT NULL DEFAULT 'objective' CHECK (source_type = 'objective'),
  occurred_at      timestamptz NOT NULL,
  idempotency_key  uuid NOT NULL,
  created_at       timestamptz NOT NULL DEFAULT now(),
  UNIQUE (student_id, idempotency_key)
);
CREATE INDEX behavior_event_student_idx ON insight.behavior_event (student_id, occurred_at);
COMMENT ON TABLE insight.behavior_event IS
  '行为事件（0025，冲刺 2-L）：客观记录，只给 Agent 用。session_* 与 plan_* 由学生本人经 insight.record_behavior_event 写入；'
  'appeal、exit_after_errors 由后端与编排层按规则写（未实现）。不从这些事件推断情绪、性格、专注力（规则 12）';

-- payload 白名单（spec 待人确认 #2）：每种事件只收固定的键，取值有界
CREATE FUNCTION insight.valid_behavior_payload(p_kind text, p jsonb) RETURNS boolean LANGUAGE sql IMMUTABLE SET search_path = pg_catalog
  AS $$ SELECT jsonb_typeof(p) = 'object' AND CASE p_kind
    WHEN 'session_start' THEN (SELECT array_agg(k ORDER BY k) FROM jsonb_object_keys(p) k) = ARRAY['entry']
                              AND p->>'entry' IN ('today', 'star_map', 'subject', 'onboarding', 'other')
    WHEN 'session_end' THEN (SELECT array_agg(k ORDER BY k) FROM jsonb_object_keys(p) k) = ARRAY['duration_sec']
                            AND jsonb_typeof(p->'duration_sec') = 'number'
                            AND (p->>'duration_sec') ~ '^[0-9]{1,5}$' AND (p->>'duration_sec')::integer BETWEEN 0 AND 86400
    WHEN 'plan_done' THEN (SELECT array_agg(k ORDER BY k) FROM jsonb_object_keys(p) k) = ARRAY['plan_task_id']
                          AND p->>'plan_task_id' ~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
    WHEN 'plan_skip' THEN (SELECT array_agg(k ORDER BY k) FROM jsonb_object_keys(p) k) = ARRAY['plan_task_id']
                          AND p->>'plan_task_id' ~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
    ELSE p = '{}'::jsonb END $$;
ALTER TABLE insight.behavior_event ADD CONSTRAINT behavior_event_payload CHECK (coalesce(insight.valid_behavior_payload(kind, payload), false));

CREATE TABLE insight.confidence_probe (
  id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  student_id       uuid NOT NULL REFERENCES platform.student_profile (id) ON DELETE CASCADE,
  subject          platform.subject NOT NULL,
  item_id          platform.skill_local_id NOT NULL,
  prediction       text NOT NULL CHECK (prediction IN ('yes', 'no', 'unsure')),
  source_type      insight.source_type NOT NULL DEFAULT 'self' CHECK (source_type = 'self'),
  asked_at         timestamptz NOT NULL,
  -- 作答提交后回填；实际对错按它去 platform.grade_result 取，这里不存
  attempt_id       uuid,
  idempotency_key  uuid NOT NULL,
  created_at       timestamptz NOT NULL DEFAULT now(),
  FOREIGN KEY (subject, item_id) REFERENCES platform.item (subject, item_id),
  UNIQUE (student_id, idempotency_key)
);
CREATE INDEX confidence_probe_student_idx ON insight.confidence_probe (student_id, asked_at);
CREATE UNIQUE INDEX confidence_probe_attempt ON insight.confidence_probe (attempt_id) WHERE attempt_id IS NOT NULL;
COMMENT ON TABLE insight.confidence_probe IS
  '“能做对吗”探针（0025，冲刺 2-L）：学生自述，只给 Agent 用；与作答时的把握度（platform.attempt.self_report）时机不同，不合并。实际对错不在这里存';

-- ---------- 权限：BFF 没有表权限（只经函数）；洞察服务、学习服务只读 ----------
ALTER TABLE insight.behavior_event ENABLE ROW LEVEL SECURITY;
ALTER TABLE insight.behavior_event FORCE ROW LEVEL SECURITY;
ALTER TABLE insight.confidence_probe ENABLE ROW LEVEL SECURITY;
ALTER TABLE insight.confidence_probe FORCE ROW LEVEL SECURITY;
REVOKE ALL ON insight.behavior_event, insight.confidence_probe FROM PUBLIC;
GRANT SELECT ON insight.behavior_event, insight.confidence_probe TO starbuddy_insight, starbuddy_learning;
GRANT SELECT, INSERT ON insight.behavior_event TO starbuddy_authz;
GRANT SELECT, INSERT ON insight.confidence_probe TO starbuddy_authz;
GRANT UPDATE (attempt_id) ON insight.confidence_probe TO starbuddy_authz;
GRANT SELECT ON platform.attempt, platform.item TO starbuddy_authz;
CREATE POLICY behavior_event_service_read ON insight.behavior_event FOR SELECT TO starbuddy_insight, starbuddy_learning USING (true);
CREATE POLICY confidence_probe_service_read ON insight.confidence_probe FOR SELECT TO starbuddy_insight, starbuddy_learning USING (true);

-- ---------- 写入函数（属主 authz；只授 BFF，只认学生本人） ----------
-- 发生时间只收最近 1 天内、不晚于现在 5 分钟（客户端时钟误差）；同一幂等键重放返回原记录，内容不同回冲突
CREATE FUNCTION insight.record_behavior_event(p_student uuid, p_kind text, p_payload jsonb, p_occurred_at timestamptz, p_key uuid)
  RETURNS uuid
  LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog, insight, platform, pg_temp
  AS $$
DECLARE v_id uuid; e insight.behavior_event;
BEGIN
  IF p_student IS NULL OR NOT platform.is_self_student(p_student) THEN
    RAISE EXCEPTION 'not allowed' USING ERRCODE = 'insufficient_privilege';
  END IF;
  IF p_kind IS NULL OR p_kind NOT IN ('session_start', 'session_end', 'plan_done', 'plan_skip')
     OR p_payload IS NULL OR NOT coalesce(insight.valid_behavior_payload(p_kind, p_payload), false)
     OR p_occurred_at IS NULL OR p_occurred_at < now() - interval '1 day' OR p_occurred_at > now() + interval '5 minutes'
     OR p_key IS NULL THEN
    RAISE EXCEPTION '事件类型、内容或时间不合要求' USING ERRCODE = 'invalid_parameter_value';
  END IF;
  INSERT INTO insight.behavior_event (student_id, kind, payload, occurred_at, idempotency_key)
  VALUES (p_student, p_kind, p_payload, p_occurred_at, p_key)
  ON CONFLICT (student_id, idempotency_key) DO NOTHING
  RETURNING id INTO v_id;
  IF v_id IS NOT NULL THEN
    RETURN v_id;
  END IF;
  SELECT * INTO e FROM insight.behavior_event WHERE student_id = p_student AND idempotency_key = p_key;
  IF e.kind <> p_kind OR e.payload <> p_payload OR e.occurred_at <> p_occurred_at THEN
    RAISE EXCEPTION '同一幂等键对应的事件内容不同' USING ERRCODE = 'unique_violation';
  END IF;
  RETURN e.id;
END $$;

CREATE FUNCTION insight.record_confidence_probe(p_student uuid, p_subject platform.subject, p_item_id text, p_prediction text,
                                                p_asked_at timestamptz, p_key uuid) RETURNS uuid
  LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog, insight, platform, pg_temp
  AS $$
DECLARE v_id uuid; e insight.confidence_probe;
BEGIN
  IF p_student IS NULL OR NOT platform.is_self_student(p_student) THEN
    RAISE EXCEPTION 'not allowed' USING ERRCODE = 'insufficient_privilege';
  END IF;
  IF p_subject IS NULL OR p_prediction IS NULL OR p_prediction NOT IN ('yes', 'no', 'unsure')
     OR p_asked_at IS NULL OR p_asked_at < now() - interval '1 day' OR p_asked_at > now() + interval '5 minutes' OR p_key IS NULL
     OR NOT EXISTS (SELECT 1 FROM platform.item i WHERE i.subject = p_subject AND i.item_id = p_item_id) THEN
    RAISE EXCEPTION '题目、预测或时间不合要求' USING ERRCODE = 'invalid_parameter_value';
  END IF;
  INSERT INTO insight.confidence_probe (student_id, subject, item_id, prediction, asked_at, idempotency_key)
  VALUES (p_student, p_subject, p_item_id, p_prediction, p_asked_at, p_key)
  ON CONFLICT (student_id, idempotency_key) DO NOTHING
  RETURNING id INTO v_id;
  IF v_id IS NOT NULL THEN
    RETURN v_id;
  END IF;
  SELECT * INTO e FROM insight.confidence_probe WHERE student_id = p_student AND idempotency_key = p_key;
  IF e.subject <> p_subject OR e.item_id <> p_item_id OR e.prediction <> p_prediction OR e.asked_at <> p_asked_at THEN
    RAISE EXCEPTION '同一幂等键对应的探针内容不同' USING ERRCODE = 'unique_violation';
  END IF;
  RETURN e.id;
END $$;

-- 作答提交后把探针与作答关联：作答必须是这个学生的、同一道题、在提问之后；一条探针只关联一次（同一作答重复关联是幂等的）
CREATE FUNCTION insight.link_probe_attempt(p_student uuid, p_probe uuid, p_attempt uuid) RETURNS void
  LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog, insight, platform, pg_temp
  AS $$
DECLARE p insight.confidence_probe;
BEGIN
  IF p_student IS NULL OR NOT platform.is_self_student(p_student) THEN
    RAISE EXCEPTION 'not allowed' USING ERRCODE = 'insufficient_privilege';
  END IF;
  SELECT * INTO p FROM insight.confidence_probe WHERE id = p_probe AND student_id = p_student FOR UPDATE;
  IF NOT FOUND THEN
    RAISE EXCEPTION '探针不存在' USING ERRCODE = 'invalid_parameter_value';
  END IF;
  IF p.attempt_id IS NOT NULL THEN
    IF p.attempt_id = p_attempt THEN RETURN; END IF;
    RAISE EXCEPTION '探针已关联别的作答' USING ERRCODE = 'unique_violation';
  END IF;
  IF NOT EXISTS (SELECT 1 FROM platform.attempt a WHERE a.attempt_id = p_attempt AND a.student_id = p_student
                   AND a.subject = p.subject AND a.item_id = p.item_id AND a.occurred_at >= p.asked_at - interval '5 minutes') THEN
    RAISE EXCEPTION '作答不存在或与探针不对应' USING ERRCODE = 'invalid_parameter_value';
  END IF;
  UPDATE insight.confidence_probe SET attempt_id = p_attempt WHERE id = p_probe;
END $$;

ALTER FUNCTION insight.record_behavior_event(uuid, text, jsonb, timestamptz, uuid) OWNER TO starbuddy_authz;
ALTER FUNCTION insight.record_confidence_probe(uuid, platform.subject, text, text, timestamptz, uuid) OWNER TO starbuddy_authz;
ALTER FUNCTION insight.link_probe_attempt(uuid, uuid, uuid) OWNER TO starbuddy_authz;
REVOKE EXECUTE ON FUNCTION insight.record_behavior_event(uuid, text, jsonb, timestamptz, uuid),
  insight.record_confidence_probe(uuid, platform.subject, text, text, timestamptz, uuid), insight.link_probe_attempt(uuid, uuid, uuid),
  insight.valid_behavior_payload(text, jsonb) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION insight.record_behavior_event(uuid, text, jsonb, timestamptz, uuid),
  insight.record_confidence_probe(uuid, platform.subject, text, text, timestamptz, uuid), insight.link_probe_attempt(uuid, uuid, uuid)
  TO starbuddy_bff;
GRANT EXECUTE ON FUNCTION insight.valid_behavior_payload(text, jsonb) TO starbuddy_authz;
