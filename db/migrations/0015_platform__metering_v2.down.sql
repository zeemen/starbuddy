-- 0015 回滚：恢复 0008 / 0009 的函数正文与约束，删除新表、新列、新资源与 v12-pilot@1 草稿配置。
-- 订阅状态值没有改名，不需要映射回去；已按新规则结束的试用保持 ended。
-- 注意：回滚前若已有引用新资源的额度桶或流水（上传、存储、深度报告），DELETE resource_type 会因外键失败，需先由人处理。

REVOKE SELECT ON platform.subject_catalog, platform.student_time_guard, platform.practice_log FROM starbuddy_bff;
REVOKE UPDATE (paused_at) ON platform.subject_enrollment FROM starbuddy_authz;

-- 函数正文恢复（meter_reserve 从 SQL 包装恢复为 0009 的 plpgsql 正文；授权与属主不变）
CREATE OR REPLACE FUNCTION platform.guard_plan_config() RETURNS trigger
  LANGUAGE plpgsql SET search_path = pg_catalog, platform, pg_temp
  AS $$
BEGIN
  PERFORM pg_advisory_xact_lock(platform.plan_config_lock_key(OLD.version));
  IF TG_OP = 'DELETE' THEN
    IF OLD.status <> 'draft' THEN
      RAISE EXCEPTION 'plan_config % is not a draft', OLD.version USING ERRCODE = 'check_violation';
    END IF;
    RETURN OLD;
  END IF;
  IF OLD.status = 'retired'
     OR (OLD.status = 'published' AND NOT (NEW.status = 'retired' AND NEW.version = OLD.version AND NEW.note_code = OLD.note_code
                                          AND NEW.published_at = OLD.published_at)) THEN
    RAISE EXCEPTION 'plan_config % is % and cannot be changed', OLD.version, OLD.status USING ERRCODE = 'check_violation';
  END IF;
  IF OLD.status = 'draft' AND NEW.version <> OLD.version THEN
    RAISE EXCEPTION 'plan_config version cannot be renamed' USING ERRCODE = 'check_violation';
  END IF;
  IF OLD.status = 'draft' AND NEW.status <> 'draft' THEN
    IF (SELECT count(*) FROM platform.plan_allowance a WHERE a.config_version = OLD.version)
         <> 4 * (SELECT count(*) FROM platform.resource_type)
       OR (SELECT count(*) FROM platform.plan_feature f WHERE f.config_version = OLD.version) <> 4 * 4 THEN
      RAISE EXCEPTION 'plan_config % is incomplete: every tier needs every resource and feature', OLD.version
        USING ERRCODE = 'check_violation';
    END IF;
  END IF;
  RETURN NEW;
END $$;

CREATE OR REPLACE FUNCTION platform.meter_reserve(p_student uuid, p_resource text, p_key text, p_fingerprint text, p_quantity numeric,
                                       p_content_fingerprint text, p_source_method text) RETURNS jsonb
  LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, platform, pg_temp
  AS $$
DECLARE
  v_now timestamptz := platform.meter_now();
  v_usage uuid;
  rt platform.resource_type;
  r platform.usage_reservation;
  e platform.entitlement;
  v_first uuid;
  v_target uuid;
  v_dedup_key text;
  v_remaining numeric := 0;
BEGIN
  IF p_student IS NULL OR NOT platform.is_self_student(p_student) THEN
    RAISE EXCEPTION 'not permitted' USING ERRCODE = 'insufficient_privilege';
  END IF;
  IF p_key IS NULL OR p_key !~ '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$' THEN
    RAISE EXCEPTION 'idempotency_key must be a lowercase UUIDv4' USING ERRCODE = 'invalid_parameter_value';
  END IF;
  IF p_fingerprint IS NULL OR p_fingerprint !~ '^[0-9a-f]{64}$' THEN
    RAISE EXCEPTION 'request_fingerprint must be a sha256 hex digest' USING ERRCODE = 'invalid_parameter_value';
  END IF;
  SELECT * INTO rt FROM platform.resource_type WHERE code = p_resource;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'unknown resource_type' USING ERRCODE = 'invalid_parameter_value';
  END IF;
  IF p_source_method IS NULL OR NOT platform.meter_method_allowed(p_resource, p_source_method) THEN
    RAISE EXCEPTION '% cannot be metered for method %', p_resource, coalesce(p_source_method, 'null')
      USING ERRCODE = 'invalid_parameter_value';
  END IF;
  IF p_quantity IS NULL OR p_quantity < 1 OR p_quantity <> trunc(p_quantity) OR p_quantity > rt.max_per_reservation THEN
    RAISE EXCEPTION 'quantity must be an integer between 1 and %', rt.max_per_reservation USING ERRCODE = 'invalid_parameter_value';
  END IF;
  IF rt.dedup_window IS NOT NULL AND p_content_fingerprint IS NULL THEN
    RAISE EXCEPTION '% requires a content fingerprint', p_resource USING ERRCODE = 'invalid_parameter_value';
  END IF;
  v_usage := platform.meter_usage_id(p_key);

  -- 同 key：先不加锁看一眼（额度桶可能已经换周期），加锁后再看一次
  SELECT * INTO r FROM platform.usage_reservation WHERE idempotency_key = p_key;
  IF FOUND THEN
    RETURN platform.meter_same_key(r, p_student, p_resource, p_fingerprint, p_content_fingerprint, p_source_method);
  END IF;

  -- 锁住该学生该资源当前周期的全部额度桶：按 id 升序加锁（全局统一顺序）；扣减时主订阅优先（spec 待人确认 #4）
  PERFORM 1 FROM platform.entitlement en JOIN platform.subscription s ON s.id = en.subscription_id
   WHERE en.student_id = p_student AND en.resource_type = p_resource
     AND s.status IN ('active', 'grace') AND v_now >= en.period_start AND v_now < en.period_end
   ORDER BY en.id FOR UPDATE OF en;
  FOR e IN SELECT en.* FROM platform.entitlement en
             JOIN platform.subscription s ON s.id = en.subscription_id
            WHERE en.student_id = p_student AND en.resource_type = p_resource
              AND s.status IN ('active', 'grace') AND v_now >= en.period_start AND v_now < en.period_end
            ORDER BY (s.source = 'addon'), en.period_start, en.id LOOP
    v_first := coalesce(v_first, e.id);
    IF v_target IS NULL AND e.granted + e.fair_use_margin - e.reserved - e.settled >= p_quantity THEN
      v_target := e.id;
    END IF;
    v_remaining := v_remaining + greatest(e.granted + e.fair_use_margin - e.reserved - e.settled, 0);
  END LOOP;
  IF v_first IS NULL THEN
    RETURN jsonb_build_object('outcome', 'no_entitlement');
  END IF;

  SELECT * INTO r FROM platform.usage_reservation WHERE idempotency_key = p_key;
  IF FOUND THEN
    RETURN platform.meter_same_key(r, p_student, p_resource, p_fingerprint, p_content_fingerprint, p_source_method);
  END IF;

  -- 24 小时复看（设计文档场景 6）：按学生去重。额度桶锁在周期边界上会分成新旧两组，所以再按去重键加 advisory 锁。
  -- 首次还在处理（预留中且未过期）时返回 dedup_in_progress；已过期但过期任务还没跑到的不算处理中。
  IF rt.dedup_window IS NOT NULL THEN
    v_dedup_key := encode(sha256(convert_to(p_student::text || '|' || p_resource || '|' || p_content_fingerprint, 'UTF8')), 'hex');
    PERFORM pg_advisory_xact_lock(hashtextextended('platform.meter_dedup:' || v_dedup_key, 0));
    IF EXISTS (SELECT 1 FROM platform.usage_reservation x
                WHERE x.dedup_key = v_dedup_key AND x.state = 'reserved' AND x.expires_at > v_now) THEN
      RETURN jsonb_build_object('outcome', 'dedup_in_progress');
    END IF;
    SELECT x.* INTO r FROM platform.usage_reservation x
     WHERE x.dedup_key = v_dedup_key AND x.state = 'settled' AND x.settled_qty > 0
       AND x.finished_at <= v_now AND x.finished_at > v_now - rt.dedup_window
     ORDER BY x.finished_at DESC LIMIT 1;
    IF FOUND THEN
      BEGIN
        INSERT INTO platform.usage_reservation (usage_id, idempotency_key, request_fingerprint, entitlement_id, student_id,
          resource_type, reserved_qty, content_fingerprint, dedup_key, dedup_of, source_method, reserved_at, expires_at)
        VALUES (v_usage, p_key, p_fingerprint, v_first, p_student, p_resource, 0, p_content_fingerprint,
                v_dedup_key, r.usage_id, p_source_method, v_now, v_now + rt.reservation_ttl);
      EXCEPTION WHEN unique_violation THEN
        RETURN jsonb_build_object('outcome', 'conflict');
      END;
      INSERT INTO platform.usage_ledger (occurred_at, entitlement_id, student_id, usage_id, kind, quantity, actor, reason_code,
        source_method, source_idempotency_key)
      VALUES (v_now, v_first, p_student, v_usage, 'reserve', 0, 'bff', 'dedup_24h', p_source_method, p_key);
      PERFORM platform.meter_check_entitlement(v_first);
      RETURN jsonb_build_object('outcome', 'dedup', 'usage_id', v_usage, 'state', 'reserved', 'reserved_qty', 0,
                                'expires_at', v_now + rt.reservation_ttl, 'dedup_of', r.usage_id);
    END IF;
  END IF;

  -- 多个额度桶各自不够、合计够时也返回 insufficient（一笔预留只落在一个桶上）；remaining 是合计，仅供展示
  IF v_target IS NULL THEN
    RETURN jsonb_build_object('outcome', 'insufficient', 'remaining', v_remaining);
  END IF;
  BEGIN
    INSERT INTO platform.usage_reservation (usage_id, idempotency_key, request_fingerprint, entitlement_id, student_id,
      resource_type, reserved_qty, content_fingerprint, dedup_key, source_method, reserved_at, expires_at)
    VALUES (v_usage, p_key, p_fingerprint, v_target, p_student, p_resource, p_quantity, p_content_fingerprint,
            v_dedup_key, p_source_method, v_now, v_now + rt.reservation_ttl);
  EXCEPTION WHEN unique_violation THEN
    -- 同一个 key 被另一个学生或另一种资源并发占用：不透露对方的任何信息
    RETURN jsonb_build_object('outcome', 'conflict');
  END;
  INSERT INTO platform.usage_ledger (occurred_at, entitlement_id, student_id, usage_id, kind, quantity, actor, reason_code,
    source_method, source_idempotency_key)
  VALUES (v_now, v_target, p_student, v_usage, 'reserve', p_quantity, 'bff', 'reserve', p_source_method, p_key);
  UPDATE platform.entitlement SET reserved = reserved + p_quantity WHERE id = v_target;
  PERFORM platform.meter_check_entitlement(v_target);
  RETURN jsonb_build_object('outcome', 'reserved', 'usage_id', v_usage, 'state', 'reserved', 'reserved_qty', p_quantity,
                            'expires_at', v_now + rt.reservation_ttl, 'remaining', v_remaining - p_quantity);
END $$;

CREATE OR REPLACE FUNCTION platform.meter_renew_due(p_batch integer) RETURNS integer
  LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, platform, pg_temp
  AS $$
DECLARE
  v_now timestamptz := platform.meter_now();
  s platform.subscription;
  v_start timestamptz;
  v_limit integer;
  v_count integer := 0;
BEGIN
  IF p_batch IS NULL OR p_batch < 1 OR p_batch > 1000 THEN
    RAISE EXCEPTION 'batch must be between 1 and 1000' USING ERRCODE = 'invalid_parameter_value';
  END IF;
  FOR s IN SELECT * FROM platform.subscription
            WHERE status IN ('active', 'grace') AND source IN ('trial', 'pilot_virtual') AND period_end <= v_now
            ORDER BY period_end, id LIMIT p_batch FOR UPDATE SKIP LOCKED LOOP
    IF s.source = 'pilot_virtual' AND NOT EXISTS (
         SELECT 1 FROM platform.pilot_household p
          WHERE p.household_id = s.household_id AND (p.ends_at IS NULL OR p.ends_at > v_now)) THEN
      UPDATE platform.subscription SET status = 'ended' WHERE id = s.id;
      CONTINUE;
    END IF;
    v_start := s.period_end + floor(extract(epoch FROM v_now - s.period_end) / (30 * 86400)) * interval '30 days';
    UPDATE platform.subscription
       SET period_start = v_start, period_end = v_start + interval '30 days', status = 'active',
           tier = coalesce(s.next_tier, s.tier), config_version = coalesce(s.next_config_version, s.config_version),
           next_tier = NULL, next_config_version = NULL
     WHERE id = s.id;
    PERFORM platform.meter_grant_period(s.id);
    IF s.next_tier IS NOT NULL THEN
      PERFORM pg_advisory_xact_lock(hashtextextended('platform.enroll_subject:' || s.student_id, 0));
      SELECT platform.meter_subject_limit(f.value_code) INTO v_limit
        FROM platform.plan_feature f
       WHERE f.config_version = s.next_config_version AND f.tier = s.next_tier AND f.feature_code = 'max_active_subjects';
      UPDATE platform.subject_enrollment SET status = 'paused'
       WHERE student_id = s.student_id AND status = 'active'
         AND subject IN (SELECT subject FROM platform.subject_enrollment
                          WHERE student_id = s.student_id AND status = 'active'
                          ORDER BY enrolled_at, subject OFFSET coalesce(v_limit, 0));
    END IF;
    v_count := v_count + 1;
  END LOOP;
  RETURN v_count;
END $$;

CREATE OR REPLACE FUNCTION platform.meter_open_subscription(p_student uuid, p_source text) RETURNS jsonb
  LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, platform, pg_temp
  AS $$
DECLARE
  v_now timestamptz := platform.meter_now();
  v_household uuid;
  v_config text := platform.meter_current_config();
  v_id uuid;
BEGIN
  IF p_student IS NULL OR NOT platform.is_linked_guardian(p_student) THEN
    RAISE EXCEPTION 'not permitted' USING ERRCODE = 'insufficient_privilege';
  END IF;
  IF p_source IS NULL OR p_source NOT IN ('trial', 'pilot_virtual') THEN
    RAISE EXCEPTION 'only trial and pilot_virtual subscriptions can be opened here' USING ERRCODE = 'invalid_parameter_value';
  END IF;
  SELECT household_id INTO v_household FROM platform.student_profile WHERE id = p_student;
  IF p_source = 'pilot_virtual' AND NOT EXISTS (
       SELECT 1 FROM platform.pilot_household p
        WHERE p.household_id = v_household AND (p.ends_at IS NULL OR p.ends_at > v_now)) THEN
    RETURN jsonb_build_object('outcome', 'not_in_pilot');
  END IF;
  IF v_config IS NULL THEN
    RETURN jsonb_build_object('outcome', 'no_published_config');
  END IF;
  BEGIN
    INSERT INTO platform.subscription (household_id, student_id, tier, config_version, source, period_start, period_end)
    VALUES (v_household, p_student, CASE WHEN p_source = 'trial' THEN 'basic' ELSE 'pro' END::platform.plan_tier,
            v_config, p_source, v_now, v_now + interval '30 days')
    RETURNING id INTO v_id;
  EXCEPTION WHEN unique_violation THEN
    RETURN jsonb_build_object('outcome', 'already_active');
  END;
  PERFORM platform.meter_grant_period(v_id);
  RETURN jsonb_build_object('outcome', 'opened', 'subscription_id', v_id, 'config_version', v_config,
                            'period_end', v_now + interval '30 days');
END $$;

CREATE OR REPLACE FUNCTION platform.enroll_subject(p_student uuid, p_subject platform.subject) RETURNS jsonb
  LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, platform, pg_temp
  AS $$
DECLARE
  v_now timestamptz := platform.meter_now();
  v_value text;
  v_max integer;
  v_status text;
  v_active integer;
BEGIN
  IF p_student IS NULL OR p_subject IS NULL OR NOT platform.can_read_student(p_student) THEN
    RAISE EXCEPTION 'not permitted' USING ERRCODE = 'insufficient_privilege';
  END IF;
  PERFORM pg_advisory_xact_lock(hashtextextended('platform.enroll_subject:' || p_student, 0));
  IF NOT EXISTS (SELECT 1 FROM platform.curriculum_version WHERE subject = p_subject AND status = 'published') THEN
    RETURN jsonb_build_object('outcome', 'not_launched');
  END IF;
  SELECT f.value_code INTO v_value
    FROM platform.subscription s
    JOIN platform.plan_feature f ON f.config_version = s.config_version AND f.tier = s.tier AND f.feature_code = 'max_active_subjects'
   WHERE s.student_id = p_student AND s.status IN ('active', 'grace') AND s.source <> 'addon'
     AND v_now >= s.period_start AND v_now < s.period_end;
  IF NOT FOUND THEN
    RETURN jsonb_build_object('outcome', 'no_subscription');
  END IF;
  v_max := platform.meter_subject_limit(v_value);
  SELECT status INTO v_status FROM platform.subject_enrollment WHERE student_id = p_student AND subject = p_subject;
  IF v_status = 'active' THEN
    RETURN jsonb_build_object('outcome', 'already_active');
  END IF;
  SELECT count(*) INTO v_active FROM platform.subject_enrollment WHERE student_id = p_student AND status = 'active';
  IF v_active >= v_max THEN
    RETURN jsonb_build_object('outcome', 'limit_reached', 'max_active_subjects', v_max);
  END IF;
  IF v_status IS NULL THEN
    INSERT INTO platform.subject_enrollment (student_id, subject) VALUES (p_student, p_subject);
  ELSE
    UPDATE platform.subject_enrollment SET status = 'active' WHERE student_id = p_student AND subject = p_subject;
  END IF;
  RETURN jsonb_build_object('outcome', 'enrolled', 'max_active_subjects', v_max);
END $$;

CREATE OR REPLACE FUNCTION platform.pause_subject(p_student uuid, p_subject platform.subject) RETURNS jsonb
  LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, platform, pg_temp
  AS $$
BEGIN
  IF p_student IS NULL OR p_subject IS NULL OR NOT platform.can_read_student(p_student) THEN
    RAISE EXCEPTION 'not permitted' USING ERRCODE = 'insufficient_privilege';
  END IF;
  UPDATE platform.subject_enrollment SET status = 'paused' WHERE student_id = p_student AND subject = p_subject AND status = 'active';
  RETURN jsonb_build_object('outcome', CASE WHEN FOUND THEN 'paused' ELSE 'not_active' END);
END $$;

DROP FUNCTION platform.meter_upgrade(uuid, platform.plan_tier);
DROP FUNCTION platform.meter_apply_subject_limits(uuid, text, platform.plan_tier);
DROP FUNCTION platform.meter_uses_slots(text, platform.plan_tier);
DROP FUNCTION platform.meter_slot_limit(text, platform.plan_tier, boolean);
DROP FUNCTION platform.set_time_guard(uuid, integer, time, time);
DROP FUNCTION platform.meter_practice(uuid, text);
DROP FUNCTION platform.meter_reserve_upload(uuid, text, text, text, integer);
DROP FUNCTION platform.meter_reserve_impl(uuid, text, text, text, numeric, text, text, boolean);
DROP FUNCTION platform.meter_minutes_today(uuid, timestamptz);
DROP FUNCTION platform.meter_in_curfew(uuid, timestamptz);
DROP FUNCTION platform.meter_in_window(time, time, time);
DROP FUNCTION platform.meter_daily_cap(uuid);
DROP FUNCTION platform.meter_plan_daily_cap(uuid);
DROP FUNCTION platform.meter_student_state(uuid);
DROP FUNCTION platform.meter_state_of(uuid);
DROP FUNCTION platform.meter_main_subscription(uuid);

DROP TABLE platform.practice_log;
DROP FUNCTION platform.reject_practice_log_change();
DROP TABLE platform.student_time_guard;
DROP TABLE platform.trial_claim_phone;
DROP TABLE platform.trial_claim;
DROP FUNCTION platform.meter_phone_digest(text);
DROP TABLE platform.meter_secret;
ALTER TABLE platform.subject_enrollment DROP COLUMN paused_at;
DROP TABLE platform.subject_catalog;

DELETE FROM platform.plan_feature WHERE config_version = 'v12-pilot@1';
DELETE FROM platform.plan_allowance WHERE config_version = 'v12-pilot@1';
DELETE FROM platform.plan_config WHERE version = 'v12-pilot@1';
ALTER TABLE platform.plan_feature DROP CONSTRAINT plan_feature_value_ok;
ALTER TABLE platform.plan_feature DROP CONSTRAINT plan_feature_feature_code_check;
ALTER TABLE platform.plan_feature ADD CONSTRAINT plan_feature_feature_code_check
  CHECK (feature_code IN ('max_active_subjects', 'lesson_mode', 'weekly_report', 'appeal_priority'));

DELETE FROM platform.resource_type WHERE code IN ('upload', 'storage_bytes', 'deep_report');
ALTER TABLE platform.resource_type DROP COLUMN bff_only;
ALTER TABLE platform.resource_type DROP COLUMN curfew_applies;
ALTER TABLE platform.resource_type DROP CONSTRAINT resource_type_unit_check;
ALTER TABLE platform.resource_type ADD CONSTRAINT resource_type_unit_check CHECK (unit IN ('count', 'minute'));
ALTER TABLE platform.resource_type ADD CONSTRAINT resource_dedup_single CHECK (dedup_window IS NULL OR max_per_reservation = 1);
