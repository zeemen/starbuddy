-- 0061 platform · 报错主消息不带个人行标识（specs/plt-db-raise-redaction/spec.md 范围第 1 条；来源 specs/plt-db-log-redaction/lessons.md 待人确认第 3 条）
-- 状态：草稿。只写文件，由人先在副本演练再执行（CLAUDE.md 必停项：迁移；其中三个函数属计量触发器，只改报错文本，不改判定逻辑）。
-- 不在任何共享环境执行。只改 platform schema。
-- 依赖：0005（grade_dimension_requires_review、guard_supersede）、0007（guard_ingest_request）、
--       0008（guard_subscription、guard_usage_reservation、guard_ledger_insert）、0009（meter_check_entitlement）、
--       0036（review_sample_weekly_reports）、0037（check_supersede_target 的现行定义）。
--       安全审查补入的两个函数（0009、0036）都是 SECURITY DEFINER、属主 starbuddy_authz，CREATE OR REPLACE 后不变。
--
-- 背景：RDS 设了 log_error_verbosity = terse，DETAIL 不进服务端日志，但 RAISE 的主消息照写。下列七个触发器函数与两个计量 / 抽检函数把 usage_id、额度桶 ID、
--   幂等键或个人层行 ID 拼进了主消息。本迁移对它们 CREATE OR REPLACE：函数体从现行最新定义逐字复制，只改 RAISE——
--   主消息去掉标识，标识移到 USING DETAIL（客户端照样收得到，服务端日志被 terse 去掉）。
--   SQLSTATE、判定条件、属主、SECURITY 属性（check_supersede_target 仍是 SECURITY DEFINER、属主 starbuddy_authz）、search_path、
--   LANGUAGE、授权全部不变（CREATE OR REPLACE 保留属主与授权）。
--   主消息保留现有测试与调用方依赖的子串：already has a reserve entry、already has a terminal entry、already <状态>、has ended、
--   invalid supersede target。表名（TG_TABLE_NAME）、预留状态、写入方法不是行标识，留在主消息里。
--
--   函数                                     旧主消息                                              新主消息（DETAIL 带标识）
--   platform.guard_ingest_request           ingest request <method>/<幂等键> already succeeded    ingest request already succeeded
--   platform.guard_subscription             subscription <id> has ended and cannot be changed     subscription has ended and cannot be changed
--   platform.guard_usage_reservation        reservation <usage_id> is already <状态>              reservation is already <状态>
--   platform.guard_ledger_insert（两处）     reservation <usage_id> already has a reserve/terminal entry
--                                                                                                 reservation already has a reserve/terminal entry
--   platform.grade_dimension_requires_review grade <grade_result_id> has a subjective … review   grade has a subjective … review
--   platform.guard_supersede                <表> <id> has already been superseded                <表> has already been superseded
--   platform.check_supersede_target         invalid supersede target for <表> <id>                invalid supersede target for <表>
--   platform.meter_check_entitlement        entitlement <id> counters do not match the ledger     entitlement counters do not match the ledger
--   platform.review_sample_weekly_reports   周报 <id> 取不到复核任务的学科（…）                     周报取不到复核任务的学科（…）
--
-- db/tests/79zi_raise_redaction.sql 逐个触发并断言主消息不含标识、DETAIL 含之、SQLSTATE 不变；另有静态断言：
--   platform / insight / kge / policy 下现行函数体里 RAISE 主消息的参数不得含个人层标识（任意 x.id、x.*_id、x.*_key，
--   名字带 id / key / entitlement / student / household / usage / account 的 p_* / v_* 变量，整行 NEW / OLD 或 row_to_json），
--   也不得用 USING MESSAGE（公共层标识显式豁免）。
-- 请以单事务执行（psql -1）。

-- ---------- 0007：写入幂等 ----------
CREATE OR REPLACE FUNCTION platform.guard_ingest_request() RETURNS trigger
  LANGUAGE plpgsql SET search_path = pg_catalog, platform, pg_temp
  AS $$
BEGIN
  IF OLD.status = 'succeeded' THEN
    RAISE EXCEPTION 'ingest request already succeeded'
      USING ERRCODE = 'check_violation', DETAIL = format('ingest request %s/%s', OLD.method, OLD.idempotency_key);
  END IF;
  NEW.updated_at := now();
  RETURN NEW;
END $$;

-- ---------- 0008：计量（只改报错文本，判定逻辑不变） ----------
CREATE OR REPLACE FUNCTION platform.guard_subscription() RETURNS trigger
  LANGUAGE plpgsql SET search_path = pg_catalog, platform, pg_temp
  AS $$
BEGIN
  IF TG_OP = 'UPDATE' THEN
    IF OLD.status = 'ended' THEN
      RAISE EXCEPTION 'subscription has ended and cannot be changed'
        USING ERRCODE = 'check_violation', DETAIL = format('subscription %s', OLD.id);
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
    RAISE EXCEPTION 'reservation is already %', OLD.state
      USING ERRCODE = 'check_violation', DETAIL = format('reservation %s', OLD.usage_id);
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
        RAISE EXCEPTION 'reservation already has a reserve entry'
          USING ERRCODE = 'check_violation', DETAIL = format('reservation %s', NEW.usage_id);
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
        RAISE EXCEPTION 'reservation already has a terminal entry'
          USING ERRCODE = 'check_violation', DETAIL = format('reservation %s', NEW.usage_id);
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

-- 0009：只核对一个额度桶（安全审查补入；判定不变）
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
    RAISE EXCEPTION 'entitlement counters do not match the ledger'
      USING ERRCODE = 'integrity_constraint_violation', DETAIL = format('entitlement %s', p_entitlement);
  END IF;
END $$;

-- ---------- 0036：周报抽检（安全审查补入；主消息保留 79r 依赖的“取不到复核任务的学科”） ----------
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
      RAISE EXCEPTION '周报取不到复核任务的学科（所引判断没有学科或已不存在）'
        USING ERRCODE = 'check_violation', DETAIL = format('周报 %s', w.id);
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

-- ---------- 0005 / 0037：推断记录的复核与更正 ----------
CREATE OR REPLACE FUNCTION platform.grade_dimension_requires_review() RETURNS trigger
  LANGUAGE plpgsql SET search_path = pg_catalog, platform, pg_temp
  AS $$
BEGIN
  IF (NEW.subjective OR NEW.score IS NULL) AND NOT EXISTS (
       SELECT 1 FROM platform.grade_result g
        WHERE g.id = NEW.grade_result_id AND g.human_review_required
          AND (NOT NEW.subjective OR ('subjective_dimension' = ANY (g.review_reasons) AND g.confidence <= 0.5))
          AND (NEW.score IS NOT NULL OR 'dimension_unavailable' = ANY (g.review_reasons))) THEN
    RAISE EXCEPTION 'grade has a subjective or unavailable dimension but is not flagged for review'
      USING ERRCODE = 'check_violation', DETAIL = format('grade_result %s', NEW.grade_result_id);
  END IF;
  RETURN NULL;
END $$;

CREATE OR REPLACE FUNCTION platform.guard_supersede() RETURNS trigger
  LANGUAGE plpgsql SET search_path = pg_catalog, platform, pg_temp
  AS $$
BEGIN
  IF OLD.superseded_by IS NOT NULL THEN
    RAISE EXCEPTION '% has already been superseded', TG_TABLE_NAME
      USING ERRCODE = 'check_violation', DETAIL = format('%s %s', TG_TABLE_NAME, OLD.id);
  END IF;
  IF NEW.superseded_by IS NULL OR NEW.superseded_by = OLD.id THEN
    RAISE EXCEPTION 'superseded_by must point to another record' USING ERRCODE = 'check_violation';
  END IF;
  NEW.superseded_at := now();
  RETURN NEW;
END $$;

-- 现行定义在 0037（mastery_estimate 分支按两种键比较）；SECURITY DEFINER 与属主 starbuddy_authz 不变
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
    RAISE EXCEPTION 'invalid supersede target for %', TG_TABLE_NAME
      USING ERRCODE = 'check_violation', DETAIL = format('%s %s', TG_TABLE_NAME, OLD.id);
  END IF;
  RETURN NULL;
END $$;
