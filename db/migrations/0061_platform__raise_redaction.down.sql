-- 0061 回滚：九个函数恢复迁移前的现行定义（guard_ingest_request 按 0007，guard_subscription、guard_usage_reservation、
-- guard_ledger_insert 按 0008，meter_check_entitlement 按 0009，review_sample_weekly_reports 按 0036，grade_dimension_requires_review、guard_supersede 按 0005，check_supersede_target 按 0037）。
-- 只换回报错文本：主消息重新带上 usage_id、幂等键与行 ID（服务端日志会再次写入这些标识），判定逻辑、SQLSTATE、属主、
-- SECURITY 属性、search_path 与授权不变（CREATE OR REPLACE 保留属主与授权）。没有数据前提。
-- 状态：草稿，由人执行，先在副本演练。请以单事务执行（psql -1）。

-- ---------- 0007 ----------
CREATE OR REPLACE FUNCTION platform.guard_ingest_request() RETURNS trigger
  LANGUAGE plpgsql SET search_path = pg_catalog, platform, pg_temp
  AS $$
BEGIN
  IF OLD.status = 'succeeded' THEN
    RAISE EXCEPTION 'ingest request %/% already succeeded', OLD.method, OLD.idempotency_key USING ERRCODE = 'check_violation';
  END IF;
  NEW.updated_at := now();
  RETURN NEW;
END $$;

-- ---------- 0008 ----------
CREATE OR REPLACE FUNCTION platform.guard_subscription() RETURNS trigger
  LANGUAGE plpgsql SET search_path = pg_catalog, platform, pg_temp
  AS $$
BEGIN
  IF TG_OP = 'UPDATE' THEN
    IF OLD.status = 'ended' THEN
      RAISE EXCEPTION 'subscription % has ended and cannot be changed', OLD.id USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.id <> OLD.id OR NEW.household_id <> OLD.household_id OR NEW.student_id <> OLD.student_id
       OR NEW.source <> OLD.source OR NEW.created_at <> OLD.created_at THEN
      RAISE EXCEPTION 'subscription owner and source cannot be changed' USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.period_start < OLD.period_start OR NEW.period_end < OLD.period_end THEN
      RAISE EXCEPTION 'subscription period can only move forward' USING ERRCODE = 'check_violation';
    END IF;
  ELSIF NOT EXISTS (SELECT 1 FROM platform.student_profile s WHERE s.id = NEW.student_id AND s.household_id = NEW.household_id) THEN
    RAISE EXCEPTION 'student does not belong to the household' USING ERRCODE = 'check_violation';
  END IF;
  -- 新建或改配置版本（升级、降级生效）时都只能指向已发布配置；配置停用不影响已有订阅的其他更新
  IF TG_OP = 'INSERT' OR NEW.config_version IS DISTINCT FROM OLD.config_version THEN
    PERFORM pg_advisory_xact_lock_shared(platform.plan_config_lock_key(NEW.config_version));
    IF NOT EXISTS (SELECT 1 FROM platform.plan_config c WHERE c.version = NEW.config_version AND c.status = 'published') THEN
      RAISE EXCEPTION 'subscription must reference a published plan_config' USING ERRCODE = 'check_violation';
    END IF;
  END IF;
  IF NEW.next_config_version IS NOT NULL
     AND (TG_OP = 'INSERT' OR NEW.next_config_version IS DISTINCT FROM OLD.next_config_version) THEN
    PERFORM pg_advisory_xact_lock_shared(platform.plan_config_lock_key(NEW.next_config_version));
    IF NOT EXISTS (SELECT 1 FROM platform.plan_config c WHERE c.version = NEW.next_config_version AND c.status = 'published') THEN
      RAISE EXCEPTION 'scheduled change must reference a published plan_config' USING ERRCODE = 'check_violation';
    END IF;
  END IF;
  RETURN NEW;
END $$;

CREATE OR REPLACE FUNCTION platform.guard_usage_reservation() RETURNS trigger
  LANGUAGE plpgsql SET search_path = pg_catalog, platform, pg_temp
  AS $$
DECLARE rt platform.resource_type;
BEGIN
  IF TG_OP = 'DELETE' THEN
    RAISE EXCEPTION 'usage_reservation is append-only' USING ERRCODE = 'insufficient_privilege';
  END IF;
  IF TG_OP = 'INSERT' THEN
    IF NEW.state <> 'reserved' OR NEW.settled_qty IS NOT NULL OR NEW.finished_at IS NOT NULL THEN
      RAISE EXCEPTION 'a reservation must start in state reserved' USING ERRCODE = 'check_violation';
    END IF;
    SELECT * INTO rt FROM platform.resource_type WHERE code = NEW.resource_type;
    IF FOUND THEN
      IF NEW.reserved_qty > rt.max_per_reservation THEN
        RAISE EXCEPTION 'reserved_qty exceeds max_per_reservation of %', rt.code USING ERRCODE = 'check_violation';
      END IF;
      IF NEW.expires_at > NEW.reserved_at + rt.reservation_ttl THEN
        RAISE EXCEPTION 'expires_at exceeds reservation_ttl of %', rt.code USING ERRCODE = 'check_violation';
      END IF;
      IF NEW.dedup_of IS NOT NULL AND (rt.dedup_window IS NULL OR NOT EXISTS (
           SELECT 1 FROM platform.usage_reservation f
            WHERE f.usage_id = NEW.dedup_of AND f.state = 'settled' AND f.settled_qty > 0
              AND f.dedup_key = NEW.dedup_key AND f.finished_at <= NEW.reserved_at
              AND f.finished_at > NEW.reserved_at - rt.dedup_window)) THEN
        RAISE EXCEPTION 'dedup_of must reference a charged reservation of the same content within the dedup window'
          USING ERRCODE = 'check_violation';
      END IF;
    END IF;
    RETURN NEW;
  END IF;
  IF OLD.state <> 'reserved' THEN
    RAISE EXCEPTION 'reservation % is already %', OLD.usage_id, OLD.state USING ERRCODE = 'check_violation';
  END IF;
  IF NEW.state = 'reserved' THEN
    RAISE EXCEPTION 'reservation update must move it to a terminal state' USING ERRCODE = 'check_violation';
  END IF;
  IF (NEW.usage_id, NEW.idempotency_key, NEW.request_fingerprint, NEW.entitlement_id, NEW.student_id, NEW.resource_type,
      NEW.reserved_qty, NEW.content_fingerprint, NEW.dedup_key, NEW.dedup_of, NEW.source_method, NEW.reserved_at, NEW.expires_at)
     IS DISTINCT FROM
     (OLD.usage_id, OLD.idempotency_key, OLD.request_fingerprint, OLD.entitlement_id, OLD.student_id, OLD.resource_type,
      OLD.reserved_qty, OLD.content_fingerprint, OLD.dedup_key, OLD.dedup_of, OLD.source_method, OLD.reserved_at, OLD.expires_at) THEN
    RAISE EXCEPTION 'only state, settled_qty and finished_at of a reservation can change' USING ERRCODE = 'check_violation';
  END IF;
  RETURN NEW;
END $$;

CREATE OR REPLACE FUNCTION platform.guard_ledger_insert() RETURNS trigger
  LANGUAGE plpgsql SET search_path = pg_catalog, platform, pg_temp
  AS $$
DECLARE
  r platform.usage_reservation;
  v_target numeric;
  v_corrected numeric;
  v_state text;
  v_qty numeric;
BEGIN
  IF NEW.kind IN ('reserve', 'settle', 'release', 'expire') AND NEW.usage_id IS NOT NULL THEN
    PERFORM pg_advisory_xact_lock(hashtextextended('platform.usage_ledger:' || NEW.usage_id, 0));
    SELECT * INTO r FROM platform.usage_reservation WHERE usage_id = NEW.usage_id AND entitlement_id = NEW.entitlement_id;
    IF NOT FOUND THEN
      RETURN NEW;  -- 交给外键报错（23503）
    END IF;
    IF NEW.kind = 'reserve' THEN
      IF NEW.quantity <> r.reserved_qty THEN
        RAISE EXCEPTION 'reserve entry quantity must equal reserved_qty' USING ERRCODE = 'check_violation';
      END IF;
      IF EXISTS (SELECT 1 FROM platform.usage_ledger l WHERE l.usage_id = NEW.usage_id AND l.kind = 'reserve') THEN
        RAISE EXCEPTION 'reservation % already has a reserve entry', NEW.usage_id USING ERRCODE = 'check_violation';
      END IF;
    ELSE
      v_state := CASE NEW.kind WHEN 'settle' THEN 'settled' WHEN 'release' THEN 'released' ELSE 'expired' END;
      v_qty := CASE WHEN NEW.kind = 'settle' THEN r.settled_qty ELSE r.reserved_qty END;
      IF r.state::text <> v_state THEN
        RAISE EXCEPTION '% entry does not match reservation state %', NEW.kind, r.state USING ERRCODE = 'check_violation';
      END IF;
      IF NEW.quantity IS DISTINCT FROM v_qty THEN
        RAISE EXCEPTION '% entry quantity does not match the reservation', NEW.kind USING ERRCODE = 'check_violation';
      END IF;
      IF EXISTS (SELECT 1 FROM platform.usage_ledger l
                  WHERE l.usage_id = NEW.usage_id AND l.kind IN ('settle', 'release', 'expire')) THEN
        RAISE EXCEPTION 'reservation % already has a terminal entry', NEW.usage_id USING ERRCODE = 'check_violation';
      END IF;
    END IF;
  ELSIF NEW.kind = 'correction' AND NEW.corrects_entry_id IS NOT NULL AND NEW.corrects_occurred_at IS NOT NULL THEN
    PERFORM pg_advisory_xact_lock(hashtextextended('platform.usage_ledger:' || NEW.corrects_entry_id, 0));
    SELECT quantity INTO v_target FROM platform.usage_ledger
     WHERE entry_id = NEW.corrects_entry_id AND occurred_at = NEW.corrects_occurred_at
       AND kind = NEW.corrects_kind AND entitlement_id = NEW.entitlement_id;
    IF NOT FOUND THEN
      RETURN NEW;  -- 交给外键报错（23503）
    END IF;
    IF sign(NEW.quantity) = sign(v_target) OR NEW.quantity = 0 THEN
      RAISE EXCEPTION 'a correction must go in the opposite direction of the corrected entry' USING ERRCODE = 'check_violation';
    END IF;
    SELECT coalesce(sum(quantity), 0) INTO v_corrected FROM platform.usage_ledger
     WHERE corrects_entry_id = NEW.corrects_entry_id AND corrects_occurred_at = NEW.corrects_occurred_at;
    IF sign(v_target + v_corrected + NEW.quantity) = -sign(v_target) THEN
      RAISE EXCEPTION 'corrections of an entry cannot exceed its quantity' USING ERRCODE = 'check_violation';
    END IF;
  END IF;
  RETURN NEW;
END $$;

-- ---------- 0009 ----------
CREATE OR REPLACE FUNCTION platform.meter_check_entitlement(p_entitlement uuid) RETURNS void
  LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, platform, pg_temp
  AS $$
DECLARE
  e platform.entitlement;
  v_granted numeric;
  v_settled numeric;
  v_reserved numeric;
BEGIN
  SELECT * INTO e FROM platform.entitlement WHERE id = p_entitlement;
  SELECT coalesce(sum(quantity) FILTER (WHERE coalesce(corrects_kind, kind) IN ('grant', 'adjust')), 0),
         coalesce(sum(quantity) FILTER (WHERE coalesce(corrects_kind, kind) = 'settle'), 0)
    INTO v_granted, v_settled
    FROM platform.usage_ledger WHERE entitlement_id = p_entitlement;
  SELECT coalesce(sum(reserved_qty), 0) INTO v_reserved
    FROM platform.usage_reservation WHERE entitlement_id = p_entitlement AND state = 'reserved';
  IF e.granted <> v_granted OR e.settled <> v_settled OR e.reserved <> v_reserved THEN
    RAISE EXCEPTION 'entitlement % counters do not match the ledger', p_entitlement
      USING ERRCODE = 'integrity_constraint_violation';
  END IF;
END $$;

-- ---------- 0036 ----------
CREATE OR REPLACE FUNCTION platform.review_sample_weekly_reports(p_week_start date, p_ratio numeric, p_min integer) RETURNS integer
  LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog, platform, pg_temp
  AS $$
DECLARE
  v_today date := (now() AT TIME ZONE 'Asia/Shanghai')::date;
  v_total integer;
  v_done integer;
  v_want integer;
  v_n integer := 0;
  w record;
  v_task uuid;
BEGIN
  IF p_week_start IS NULL OR extract(isodow FROM p_week_start) <> 1 OR p_week_start > v_today OR p_week_start < v_today - 35
     OR p_ratio IS NULL OR p_ratio <= 0 OR p_ratio > 0.5 OR p_min IS NULL OR p_min < 0 OR p_min > 100 THEN
    RAISE EXCEPTION 'week_start must be a Monday within the last 35 days, ratio in (0, 0.5], min in [0, 100]'
      USING ERRCODE = 'invalid_parameter_value';
  END IF;
  PERFORM pg_advisory_xact_lock(hashtextextended('insight.weekly_report.sample:' || p_week_start::text, 0));
  SELECT count(*), count(*) FILTER (WHERE x.spot_check_task_id IS NOT NULL) INTO v_total, v_done
    FROM insight.weekly_report x WHERE x.week_start = p_week_start AND x.status = 'generated';
  v_want := least(v_total, greatest(ceil(v_total * p_ratio)::integer, p_min, 5)) - v_done;
  IF v_want <= 0 THEN
    RETURN 0;
  END IF;
  FOR w IN SELECT x.id, x.student_id, c.subject
             FROM insight.weekly_report x
             LEFT JOIN insight.claim c ON c.id = (x.sentences -> 0 ->> 'claim_id')::uuid AND c.student_id = x.student_id
            WHERE x.week_start = p_week_start AND x.status = 'generated' AND x.spot_check_task_id IS NULL
            ORDER BY md5(x.id::text), x.id
            LIMIT v_want
              FOR UPDATE OF x LOOP
    IF w.subject IS NULL THEN
      RAISE EXCEPTION '周报 % 取不到复核任务的学科（所引判断没有学科或已不存在）', w.id USING ERRCODE = 'check_violation';
    END IF;
    INSERT INTO platform.review_task (subject, student_id, source_kind, source_id, reasons)
    VALUES (w.subject, w.student_id, 'weekly_report', w.id, ARRAY['spot_check']::platform.review_reason[])
    ON CONFLICT (source_kind, source_id) DO NOTHING
    RETURNING id INTO v_task;
    IF v_task IS NULL THEN
      SELECT t.id INTO v_task FROM platform.review_task t WHERE t.source_kind = 'weekly_report' AND t.source_id = w.id;
    ELSE
      v_n := v_n + 1;
    END IF;
    UPDATE insight.weekly_report SET spot_check_task_id = v_task WHERE id = w.id;
  END LOOP;
  RETURN v_n;
END $$;

-- ---------- 0005 ----------
CREATE OR REPLACE FUNCTION platform.grade_dimension_requires_review() RETURNS trigger
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

CREATE OR REPLACE FUNCTION platform.guard_supersede() RETURNS trigger
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

-- ---------- 0037 ----------
CREATE OR REPLACE FUNCTION platform.check_supersede_target() RETURNS trigger
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
                     AND n.skill_local_id IS NOT DISTINCT FROM OLD.skill_local_id
                     AND n.node_id IS NOT DISTINCT FROM OLD.node_id
                     AND n.cognitive_level IS NOT DISTINCT FROM OLD.cognitive_level
                     AND n.computed_at >= OLD.computed_at) INTO ok;
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
