-- 0004 platform · 原始证据：evidence_event、attempt（按月分区，ADR 05）
-- 原始证据、模型推断、家长自述分开存（CLAUDE.md 规则 6）：本迁移只放“观察到的事实”。
-- 语音只存评测结果与特征，不存原始音频（完整方案 5.5）。
--
-- 幂等（spec 待人确认 #2）：契约要求适配器重试时返回同一个 evidence_id / attempt_id，
-- 所以以它们作为幂等身份，主键 (id, occurred_at) 同时满足“唯一约束含分区键”（ADR 05 第 2 条）。
-- 请求级 idempotency_key 另存一列并建索引，用于对账。

-- payload 字段白名单，与契约 EvidencePayload* 对应。禁止游戏化数值与掌握度（echo #12；规则 2）。
CREATE FUNCTION platform.evidence_payload_ok(p_kind text, p_payload jsonb) RETURNS boolean
  LANGUAGE sql IMMUTABLE PARALLEL SAFE
  AS $$
  SELECT jsonb_typeof(p_payload) = 'object'
     AND NOT EXISTS (
       SELECT 1 FROM jsonb_object_keys(p_payload) k
        WHERE k <> ALL (CASE
          WHEN p_kind IN ('response_text', 'ocr_transcription', 'asr_transcription') THEN ARRAY['text']
          WHEN p_kind = 'scoring_point_span' THEN ARRAY['source_evidence_id', 'span_start', 'span_end']
          WHEN p_kind = 'response_choice' THEN ARRAY['choice']
          WHEN p_kind = 'verifier_trace' THEN ARRAY['sampled_points', 'agreeing_points', 'reason_codes']
          WHEN p_kind IN ('interaction_signal', 'speech_features') THEN ARRAY['signal_code', 'value']
          ELSE ARRAY[]::text[] END))
     AND (p_kind NOT IN ('interaction_signal', 'speech_features')
          OR (p_payload ->> 'signal_code') ~ '^(?!xp_|achievement_|mastery_)[a-z0-9_]{1,48}$')
  $$;

CREATE TABLE platform.evidence_event (
  evidence_id               uuid NOT NULL,
  occurred_at               timestamptz NOT NULL,
  student_id                uuid NOT NULL REFERENCES platform.student_profile (id),
  subject                   platform.subject NOT NULL,
  kind                      text NOT NULL CHECK (kind IN (
                              'response_text', 'response_choice', 'ocr_transcription', 'asr_transcription',
                              'scoring_point_span', 'verifier_trace', 'interaction_signal', 'speech_features')),
  item_id                   platform.skill_local_id,
  skill_local_id            platform.skill_local_id,
  admission                 platform.admission NOT NULL,
  payload                   jsonb NOT NULL CONSTRAINT evidence_payload_shape CHECK (platform.evidence_payload_ok(kind, payload)),
  media_ref                 text CHECK (media_ref ~ '^objstore://[a-z0-9][a-z0-9/_.\-]{0,255}$'),
  source_method             text NOT NULL CHECK (source_method IN (
                              'diagnose', 'plan', 'teach', 'grade', 'generate_variation', 'report')),
  request_id                uuid NOT NULL,
  request_idempotency_key   text NOT NULL CHECK (length(request_idempotency_key) BETWEEN 16 AND 128),
  recorded_at               timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (evidence_id, occurred_at)
) PARTITION BY RANGE (occurred_at);
CREATE INDEX evidence_event_student_idx ON platform.evidence_event (student_id, occurred_at);
CREATE INDEX evidence_event_skill_idx ON platform.evidence_event (subject, skill_local_id, occurred_at);
CREATE INDEX evidence_event_request_idx ON platform.evidence_event (request_idempotency_key);
COMMENT ON TABLE platform.evidence_event IS '原始证据。只有 starbuddy_learning 能写（ADR 01）；未准入内容的证据可以记录，但不能推动星亮门槛';

CREATE TABLE platform.attempt (
  attempt_id                uuid NOT NULL,
  occurred_at               timestamptz NOT NULL,
  student_id                uuid NOT NULL REFERENCES platform.student_profile (id),
  subject                   platform.subject NOT NULL,
  item_id                   platform.skill_local_id NOT NULL,
  response_evidence_id      uuid NOT NULL,
  hint_level_used           smallint CHECK (hint_level_used BETWEEN 0 AND 6),
  elapsed_sec               integer CHECK (elapsed_sec >= 0),
  self_report               text CHECK (self_report IN ('confident', 'unsure', 'guessed')),
  request_idempotency_key   text NOT NULL CHECK (length(request_idempotency_key) BETWEEN 16 AND 128),
  recorded_at               timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (attempt_id, occurred_at)
) PARTITION BY RANGE (occurred_at);
CREATE INDEX attempt_student_idx ON platform.attempt (student_id, occurred_at);
CREATE INDEX attempt_item_idx ON platform.attempt (subject, item_id, occurred_at);

-- 分区维护（ADR 05 第 4 条）：建好 [p_from 所在月, +p_months) 的月分区，已存在则跳过。
-- 分区上不授予任何角色权限：只能经父表访问，父表的 RLS 才会生效。
CREATE FUNCTION platform.ensure_monthly_partitions(p_parent regclass, p_from date, p_months integer)
  RETURNS integer
  LANGUAGE plpgsql VOLATILE SET search_path = pg_catalog, platform
  AS $$
DECLARE
  v_start date := date_trunc('month', p_from)::date;
  v_created integer := 0;
  v_name text;
  v_rel text := (SELECT c.relname FROM pg_class c WHERE c.oid = p_parent);
BEGIN
  IF p_months < 1 OR p_months > 36 THEN
    RAISE EXCEPTION 'p_months out of range' USING ERRCODE = 'invalid_parameter_value';
  END IF;
  FOR i IN 0 .. p_months - 1 LOOP
    v_name := format('%s_%s', v_rel, to_char(v_start + make_interval(months => i), 'YYYYMM'));
    IF to_regclass(format('platform.%I', v_name)) IS NULL THEN
      EXECUTE format('CREATE TABLE platform.%I PARTITION OF %s FOR VALUES FROM (%L) TO (%L)',
                     v_name, p_parent,
                     (v_start + make_interval(months => i))::timestamptz,
                     (v_start + make_interval(months => i + 1))::timestamptz);
      v_created := v_created + 1;
    END IF;
  END LOOP;
  RETURN v_created;
END $$;
REVOKE EXECUTE ON FUNCTION platform.ensure_monthly_partitions(regclass, date, integer) FROM PUBLIC;

-- 初始分区：2026-09 起 16 个月，覆盖阶段 0 与阶段 1。不建 DEFAULT 分区：
-- 超出范围的写入直接失败，便于发现“分区没建好”（ADR 05 第 4 条要求告警）。
-- 时区按 UTC 切分：分区边界是 UTC 月初。
SET LOCAL TIME ZONE 'UTC';
DO $$
BEGIN
  PERFORM platform.ensure_monthly_partitions('platform.evidence_event', DATE '2026-09-01', 16);
  PERFORM platform.ensure_monthly_partitions('platform.attempt', DATE '2026-09-01', 16);
END $$;

GRANT SELECT, INSERT ON platform.evidence_event, platform.attempt TO starbuddy_learning;
GRANT SELECT ON platform.evidence_event, platform.attempt TO starbuddy_bff;

ALTER TABLE platform.evidence_event ENABLE ROW LEVEL SECURITY;
ALTER TABLE platform.evidence_event FORCE ROW LEVEL SECURITY;
CREATE POLICY evidence_read ON platform.evidence_event FOR SELECT TO starbuddy_bff
  USING (platform.can_read_student(student_id));
CREATE POLICY evidence_service ON platform.evidence_event FOR ALL TO starbuddy_learning USING (true) WITH CHECK (true);

ALTER TABLE platform.attempt ENABLE ROW LEVEL SECURITY;
ALTER TABLE platform.attempt FORCE ROW LEVEL SECURITY;
CREATE POLICY attempt_read ON platform.attempt FOR SELECT TO starbuddy_bff
  USING (platform.can_read_student(student_id));
CREATE POLICY attempt_service ON platform.attempt FOR ALL TO starbuddy_learning USING (true) WITH CHECK (true);
