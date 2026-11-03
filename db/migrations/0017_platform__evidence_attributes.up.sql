-- 0017 platform · 证据补列（specs/evidence-attributes-sprint1/spec.md；开发计划 v2.0 冲刺 1-C）
-- 状态：草稿。只写文件，由人先在副本演练再执行（CLAUDE.md 必停项）。依赖 0011（kge.map_version）。请以单事务执行（psql -1）。
-- 给证据与作答补上：作答情境（KGE evidence_kind）、证据等级（ADR 15 的 A / B / C）、输入通道、地图版本、来源类型（规则 6）。
-- 由学习服务按请求上下文补齐，适配器的证据草稿不带这些字段（插件 API v1.0 svc-#2 / kge-#5）。
-- evidence_kind、evidence_level、channel、map_version_id 可空、不回填：旧证据由 KGE-04 按保守口径处理。
-- source_type 由载荷类型唯一决定，旧行按同一规则回填后设为非空。
-- 注意：evidence_event 已有的 kind 是载荷类型（response_text 等），与 evidence_kind（作答情境）不是一回事。

-- 载荷类型 → 来源类型（规则 6）：学生作答原文、选项、验证器轨迹、交互信号是客观记录；识别转写、采分点标注、语音特征是模型推断
CREATE FUNCTION platform.evidence_source_type(p_kind text) RETURNS text
  LANGUAGE sql IMMUTABLE STRICT SET search_path = pg_catalog, pg_temp
  AS $$ SELECT CASE WHEN p_kind IN ('response_text', 'response_choice', 'verifier_trace', 'interaction_signal') THEN 'objective'
                    WHEN p_kind IN ('ocr_transcription', 'asr_transcription', 'scoring_point_span', 'speech_features') THEN 'model_inference'
               END $$;
REVOKE EXECUTE ON FUNCTION platform.evidence_source_type(text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION platform.evidence_source_type(text) TO starbuddy_learning;

ALTER TABLE platform.evidence_event
  ADD COLUMN evidence_kind  text CHECK (evidence_kind IN ('independent', 'hinted', 'transfer', 'delayed_retest')),
  ADD COLUMN evidence_level text CHECK (evidence_level IN ('A', 'B', 'C')),
  ADD COLUMN channel        text CHECK (channel IN ('keypad', 'canvas', 'voice', 'photo')),
  ADD COLUMN map_version_id uuid,
  ADD COLUMN source_type    text;
UPDATE platform.evidence_event SET source_type = platform.evidence_source_type(kind);
-- 写入时未给 source_type 就按载荷类型补齐（给了也必须与载荷类型一致，见下方约束）
CREATE FUNCTION platform.fill_evidence_source_type() RETURNS trigger
  LANGUAGE plpgsql SET search_path = pg_catalog, platform, pg_temp
  AS $$
BEGIN
  NEW.source_type := coalesce(NEW.source_type, platform.evidence_source_type(NEW.kind));
  RETURN NEW;
END $$;
REVOKE EXECUTE ON FUNCTION platform.fill_evidence_source_type() FROM PUBLIC;
CREATE TRIGGER fill_evidence_source_type BEFORE INSERT ON platform.evidence_event
  FOR EACH ROW EXECUTE FUNCTION platform.fill_evidence_source_type();
ALTER TABLE platform.evidence_event
  ALTER COLUMN source_type SET NOT NULL,
  -- 新增载荷类型忘了改函数时函数返回空：coalesce 让约束拒绝而不是放行（安全审查建议 6）
  ADD CONSTRAINT evidence_event_source_type CHECK (coalesce(source_type = platform.evidence_source_type(kind), false)),
  -- 拍照最高 B 级（ADR 15；安全审查必须改 1）
  ADD CONSTRAINT evidence_event_photo_level CHECK (channel IS DISTINCT FROM 'photo' OR evidence_level IN ('B', 'C')),
  -- 键盘作答不会有识别转写（安全审查建议 3）
  ADD CONSTRAINT evidence_event_keypad_kind CHECK (channel IS DISTINCT FROM 'keypad' OR kind NOT IN ('ocr_transcription', 'asr_transcription')),
  -- 作答情境只描述作答：交互信号、语音特征不属于某次作答，不带
  ADD CONSTRAINT evidence_event_kind_applies CHECK (evidence_kind IS NULL OR kind NOT IN ('interaction_signal', 'speech_features')),
  ADD CONSTRAINT evidence_event_map_version_fk FOREIGN KEY (subject, map_version_id) REFERENCES kge.map_version (subject, id);
COMMENT ON COLUMN platform.evidence_event.evidence_kind IS '作答情境（KGE）：independent / hinted / transfer / delayed_retest；0017 之前的行为空';
COMMENT ON COLUMN platform.evidence_event.evidence_level IS '证据等级（ADR 15）：A 可推动星亮，B 降权不点亮，C 不进入学习者模型；0017 之前与 v0 路径为空';
COMMENT ON COLUMN platform.evidence_event.channel IS '输入通道（ADR 15）：keypad / canvas / voice / photo；0017 之前与 v0 路径为空';
COMMENT ON COLUMN platform.evidence_event.map_version_id IS '作答时内容版本绑定的知识地图版本（经 platform.curriculum_version.map_version_id）；未绑定为空';
COMMENT ON COLUMN platform.evidence_event.source_type IS '来源类型（规则 6），由载荷类型决定（platform.evidence_source_type）';

ALTER TABLE platform.attempt
  ADD COLUMN evidence_kind  text CHECK (evidence_kind IN ('independent', 'hinted', 'transfer', 'delayed_retest')),
  ADD COLUMN evidence_level text CHECK (evidence_level IN ('A', 'B', 'C')),
  ADD COLUMN channel        text CHECK (channel IN ('keypad', 'canvas', 'voice', 'photo')),
  ADD COLUMN map_version_id uuid,
  ADD COLUMN source_type    text NOT NULL DEFAULT 'objective' CHECK (source_type = 'objective'),
  -- 用过提示的作答不能记成独立、迁移或复测（最保守口径，spec 推导规则）
  ADD CONSTRAINT attempt_photo_level CHECK (channel IS DISTINCT FROM 'photo' OR evidence_level IN ('B', 'C')),
  ADD CONSTRAINT attempt_hinted_kind CHECK (coalesce(hint_level_used, 0) = 0 OR evidence_kind IS NULL OR evidence_kind = 'hinted'),
  ADD CONSTRAINT attempt_map_version_fk FOREIGN KEY (subject, map_version_id) REFERENCES kge.map_version (subject, id);
COMMENT ON COLUMN platform.attempt.source_type IS
  '来源类型（规则 6）：作答本身是客观记录。self_report 列是学生自述，按规则 6 应分表，迁出另起任务（spec 待人确认 #5）';

-- 作答行的补列必须与它的作答证据一致（安全审查建议 2）：同一事实只有一个取值，不会一处 A、一处 B
CREATE FUNCTION platform.check_attempt_attributes() RETURNS trigger
  LANGUAGE plpgsql SET search_path = pg_catalog, platform, pg_temp
  AS $$
DECLARE e record;
BEGIN
  SELECT evidence_kind, evidence_level, channel, map_version_id INTO e
    FROM platform.evidence_event WHERE evidence_id = NEW.response_evidence_id AND student_id = NEW.student_id;
  IF NOT FOUND THEN
    RETURN NEW;  -- 作答证据不存在时由学习服务的核对负责（0004 没有这条外键）
  END IF;
  IF (e.evidence_kind, e.evidence_level, e.channel, e.map_version_id)
     IS DISTINCT FROM (NEW.evidence_kind, NEW.evidence_level, NEW.channel, NEW.map_version_id) THEN
    RAISE EXCEPTION 'attempt attributes differ from its response evidence' USING ERRCODE = 'check_violation';
  END IF;
  RETURN NEW;
END $$;
REVOKE EXECUTE ON FUNCTION platform.check_attempt_attributes() FROM PUBLIC;
CREATE TRIGGER check_attempt_attributes BEFORE INSERT ON platform.attempt
  FOR EACH ROW EXECUTE FUNCTION platform.check_attempt_attributes();
