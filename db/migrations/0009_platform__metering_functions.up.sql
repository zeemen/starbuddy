-- 0009 platform · 计量写入函数与选科上限（specs/metering-impl-sprint2/spec.md；设计文档 docs/design/metering-schema.md 第 4、5 节）
-- 全部写入函数：SECURITY DEFINER，属主 starbuddy_authz，固定 search_path。遵守设计文档 4.0：
--   授权从 platform.current_account_id() 推导；额度桶由函数自己查（不接受外部传入）；
--   加锁顺序固定为“先额度桶，再预留”；终态操作幂等；写完在同一事务里核对本桶。
-- 需要 pgcrypto（uuidv5 的 SHA-1）：随 Postgres 发行，PostgreSQL License；启用扩展需要超级用户（spec 待人确认 #3）。

-- 定时任务角色由人在 db/bootstrap/roles.sql 创建；这里只断言它存在且属性安全
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'starbuddy_scheduler') THEN
    RAISE EXCEPTION '角色 starbuddy_scheduler 不存在：请先执行 db/bootstrap/roles.sql';
  END IF;
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'starbuddy_scheduler' AND (rolsuper OR rolbypassrls)) THEN
    RAISE EXCEPTION 'starbuddy_scheduler 不能是 SUPERUSER 或 BYPASSRLS';
  END IF;
  IF EXISTS (SELECT 1 FROM pg_roles r
              WHERE NOT r.rolsuper AND pg_has_role(r.oid, 'starbuddy_scheduler', 'MEMBER')
                AND (pg_has_role(r.oid, 'starbuddy_bff', 'MEMBER') OR pg_has_role(r.oid, 'starbuddy_learning', 'MEMBER')
                     OR pg_has_role(r.oid, 'starbuddy_review', 'MEMBER'))) THEN
    RAISE EXCEPTION '同一个角色不能同时继承 starbuddy_scheduler 与 bff / learning / review';
  END IF;
END $$;

CREATE EXTENSION IF NOT EXISTS pgcrypto WITH SCHEMA public;

GRANT USAGE ON SCHEMA platform TO starbuddy_scheduler;

-- ---------- 工具函数 ----------
-- 计量里的“现在”。测试夹具可以替换成可推进的时钟；生产就是 now()。
CREATE FUNCTION platform.meter_now() RETURNS timestamptz
  LANGUAGE sql STABLE SET search_path = pg_catalog
  AS $$ SELECT now() $$;

-- RFC 4122 uuidv5（SHA-1）。usage_id = uuidv5(计量命名空间, idempotency_key)，BFF 可在预留前算出同一个值。
CREATE FUNCTION platform.meter_uuidv5(p_namespace uuid, p_name text) RETURNS uuid
  LANGUAGE plpgsql IMMUTABLE STRICT SET search_path = pg_catalog
  AS $$
DECLARE v bytea;
BEGIN
  v := substring(public.digest(uuid_send(p_namespace) || convert_to(p_name, 'UTF8'), 'sha1') FROM 1 FOR 16);
  v := set_byte(v, 6, (get_byte(v, 6) & 15) | 80);
  v := set_byte(v, 8, (get_byte(v, 8) & 63) | 128);
  RETURN encode(v, 'hex')::uuid;
END $$;

-- 计量命名空间（固定值，BFF 的 services/bff/src/metering/uuid.ts 用同一个）
CREATE FUNCTION platform.meter_usage_id(p_key text) RETURNS uuid
  LANGUAGE sql IMMUTABLE STRICT SET search_path = pg_catalog, platform
  AS $$ SELECT platform.meter_uuidv5('3b1e4c1a-6f0d-4d3e-9a52-7c8e2f1b5a40'::uuid, p_key) $$;

-- 只核对一个额度桶：计数 = 流水与预留的聚合（设计文档 I1）。不一致就抛错，整个事务回滚。
CREATE FUNCTION platform.meter_check_entitlement(p_entitlement uuid) RETURNS void
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

-- 按配置给一个订阅建本周期的全部额度桶并写 grant 流水（开通与续期共用）
CREATE FUNCTION platform.meter_grant_period(p_subscription uuid) RETURNS void
  LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, platform, pg_temp
  AS $$
DECLARE
  s platform.subscription;
  a record;
  v_entitlement uuid;
BEGIN
  SELECT * INTO s FROM platform.subscription WHERE id = p_subscription;
  FOR a IN SELECT resource_type, quantity FROM platform.plan_allowance
            WHERE config_version = s.config_version AND tier = s.tier ORDER BY resource_type LOOP
    INSERT INTO platform.entitlement (subscription_id, student_id, resource_type, period_start, period_end, granted)
    VALUES (s.id, s.student_id, a.resource_type, s.period_start, s.period_end, a.quantity)
    RETURNING id INTO v_entitlement;
    IF a.quantity > 0 THEN
      INSERT INTO platform.usage_ledger (occurred_at, entitlement_id, student_id, kind, quantity, actor, reason_code)
      VALUES (platform.meter_now(), v_entitlement, s.student_id, 'grant', a.quantity, 'system', 'period_grant');
    END IF;
    PERFORM platform.meter_check_entitlement(v_entitlement);
  END LOOP;
END $$;

-- ---------- 预留 ----------
-- 返回 jsonb：outcome ∈ reserved | dedup | existing | conflict | insufficient | dedup_in_progress | no_entitlement
CREATE FUNCTION platform.meter_reserve(p_student uuid, p_resource text, p_key text, p_fingerprint text, p_quantity numeric,
                                       p_content_fingerprint text, p_source_method text) RETURNS jsonb
  LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, platform, pg_temp
  AS $$
DECLARE
  v_now timestamptz := platform.meter_now();
  v_usage uuid;
  rt platform.resource_type;
  r platform.usage_reservation;
  f platform.usage_reservation;
  e platform.entitlement;
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
    RETURN platform.meter_same_key(r, p_student, p_resource, p_fingerprint);
  END IF;

  -- 锁住该学生该资源当前周期的全部额度桶：主订阅在前、附加权益在后（spec 待人确认 #4）
  FOR e IN SELECT en.* FROM platform.entitlement en
             JOIN platform.subscription s ON s.id = en.subscription_id
            WHERE en.student_id = p_student AND en.resource_type = p_resource
              AND s.status IN ('active', 'grace') AND v_now >= en.period_start AND v_now < en.period_end
            ORDER BY (s.source = 'addon'), en.period_start, en.id
            FOR UPDATE OF en LOOP
    IF v_target IS NULL AND e.granted + e.fair_use_margin - e.reserved - e.settled >= p_quantity THEN
      v_target := e.id;
    END IF;
    v_remaining := v_remaining + greatest(e.granted + e.fair_use_margin - e.reserved - e.settled, 0);
    IF v_dedup_key IS NULL THEN
      v_dedup_key := '';  -- 标记“至少有一个桶”；复看记录挂在排序第一的桶上
      f.entitlement_id := e.id;
    END IF;
  END LOOP;
  IF v_dedup_key IS NULL THEN
    RETURN jsonb_build_object('outcome', 'no_entitlement');
  END IF;

  SELECT * INTO r FROM platform.usage_reservation WHERE idempotency_key = p_key;
  IF FOUND THEN
    RETURN platform.meter_same_key(r, p_student, p_resource, p_fingerprint);
  END IF;

  -- 24 小时复看（设计文档场景 6）：按学生去重；首次还在处理时返回 dedup_in_progress。
  -- 去重键含学生与资源，同键的请求都要先锁住同一组额度桶，所以已经串行，不需要另加 advisory 锁。
  v_dedup_key := NULL;
  IF rt.dedup_window IS NOT NULL THEN
    v_dedup_key := encode(sha256(convert_to(p_student::text || '|' || p_resource || '|' || p_content_fingerprint, 'UTF8')), 'hex');
    IF EXISTS (SELECT 1 FROM platform.usage_reservation x WHERE x.dedup_key = v_dedup_key AND x.state = 'reserved') THEN
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
        VALUES (v_usage, p_key, p_fingerprint, f.entitlement_id, p_student, p_resource, 0, p_content_fingerprint,
                v_dedup_key, r.usage_id, p_source_method, v_now, v_now + rt.reservation_ttl);
      EXCEPTION WHEN unique_violation THEN
        RETURN jsonb_build_object('outcome', 'conflict');
      END;
      INSERT INTO platform.usage_ledger (occurred_at, entitlement_id, student_id, usage_id, kind, quantity, actor, reason_code,
        source_method, source_idempotency_key)
      VALUES (v_now, f.entitlement_id, p_student, v_usage, 'reserve', 0, 'bff', 'dedup_24h',
              p_source_method, CASE WHEN p_source_method IS NOT NULL THEN p_key END);
      PERFORM platform.meter_check_entitlement(f.entitlement_id);
      RETURN jsonb_build_object('outcome', 'dedup', 'usage_id', v_usage, 'state', 'reserved', 'reserved_qty', 0,
                                'expires_at', v_now + rt.reservation_ttl, 'dedup_of', r.usage_id);
    END IF;
  END IF;

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
  VALUES (v_now, v_target, p_student, v_usage, 'reserve', p_quantity, 'bff', 'reserve',
          p_source_method, CASE WHEN p_source_method IS NOT NULL THEN p_key END);
  UPDATE platform.entitlement SET reserved = reserved + p_quantity WHERE id = v_target;
  PERFORM platform.meter_check_entitlement(v_target);
  RETURN jsonb_build_object('outcome', 'reserved', 'usage_id', v_usage, 'state', 'reserved', 'reserved_qty', p_quantity,
                            'expires_at', v_now + rt.reservation_ttl, 'remaining', v_remaining - p_quantity);
END $$;

-- 同 key 的处理（设计文档场景 3）：学生、资源、指纹任一不同 → conflict，不透露已有记录；否则按已有状态返回
CREATE FUNCTION platform.meter_same_key(r platform.usage_reservation, p_student uuid, p_resource text, p_fingerprint text)
  RETURNS jsonb
  LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, platform, pg_temp
  AS $$
  SELECT CASE
    WHEN r.student_id <> p_student OR r.resource_type <> p_resource OR r.request_fingerprint <> p_fingerprint
      THEN jsonb_build_object('outcome', 'conflict')
    ELSE jsonb_build_object('outcome', 'existing', 'usage_id', r.usage_id, 'state', r.state, 'reserved_qty', r.reserved_qty,
                            'settled_qty', r.settled_qty, 'expires_at', r.expires_at, 'dedup_of', r.dedup_of)
  END $$;

-- ---------- 结算、释放 ----------
-- 按固定顺序加锁：先额度桶，再预留；加锁后重查。只有预留所属的学生本人能结算或释放。
CREATE FUNCTION platform.meter_lock_reservation(p_usage uuid) RETURNS platform.usage_reservation
  LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, platform, pg_temp
  AS $$
DECLARE
  v_entitlement uuid;
  r platform.usage_reservation;
BEGIN
  SELECT entitlement_id INTO v_entitlement FROM platform.usage_reservation WHERE usage_id = p_usage;
  IF v_entitlement IS NOT NULL THEN
    PERFORM 1 FROM platform.entitlement WHERE id = v_entitlement FOR UPDATE;
    SELECT * INTO r FROM platform.usage_reservation WHERE usage_id = p_usage FOR UPDATE;
  END IF;
  RETURN r;
END $$;

CREATE FUNCTION platform.meter_settle(p_usage uuid, p_settled_qty numeric) RETURNS jsonb
  LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, platform, pg_temp
  AS $$
DECLARE
  v_now timestamptz := platform.meter_now();
  r platform.usage_reservation;
BEGIN
  r := platform.meter_lock_reservation(p_usage);
  -- 不存在与无权限同样处理，不泄露 usage_id 是否存在
  IF r.usage_id IS NULL OR NOT platform.is_self_student(r.student_id) THEN
    RAISE EXCEPTION 'not permitted' USING ERRCODE = 'insufficient_privilege';
  END IF;
  IF r.state = 'settled' THEN
    RETURN jsonb_build_object('outcome', CASE WHEN r.settled_qty = p_settled_qty THEN 'settled' ELSE 'conflict' END,
                              'usage_id', r.usage_id, 'state', r.state, 'settled_qty', r.settled_qty, 'idempotent', true);
  ELSIF r.state <> 'reserved' THEN
    RETURN jsonb_build_object('outcome', 'conflict', 'usage_id', r.usage_id, 'state', r.state);
  END IF;
  IF p_settled_qty IS NULL OR p_settled_qty < 0 OR p_settled_qty <> trunc(p_settled_qty) OR p_settled_qty > r.reserved_qty THEN
    RAISE EXCEPTION 'settled quantity must be an integer between 0 and %', r.reserved_qty USING ERRCODE = 'invalid_parameter_value';
  END IF;
  -- 先改状态，再写终态流水（流水写入核对要求预留已是 settled）
  UPDATE platform.usage_reservation SET state = 'settled', settled_qty = p_settled_qty, finished_at = v_now
   WHERE usage_id = p_usage;
  INSERT INTO platform.usage_ledger (occurred_at, entitlement_id, student_id, usage_id, kind, quantity, actor, reason_code)
  VALUES (v_now, r.entitlement_id, r.student_id, r.usage_id, 'settle', p_settled_qty, 'bff',
          CASE WHEN r.dedup_of IS NOT NULL THEN 'dedup_24h' ELSE 'settle' END);
  UPDATE platform.entitlement SET reserved = reserved - r.reserved_qty, settled = settled + p_settled_qty
   WHERE id = r.entitlement_id;
  PERFORM platform.meter_check_entitlement(r.entitlement_id);
  RETURN jsonb_build_object('outcome', 'settled', 'usage_id', r.usage_id, 'state', 'settled', 'settled_qty', p_settled_qty,
                            'idempotent', false);
END $$;

CREATE FUNCTION platform.meter_release(p_usage uuid, p_reason text) RETURNS jsonb
  LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, platform, pg_temp
  AS $$
DECLARE
  v_now timestamptz := platform.meter_now();
  r platform.usage_reservation;
BEGIN
  r := platform.meter_lock_reservation(p_usage);
  IF r.usage_id IS NULL OR NOT platform.is_self_student(r.student_id) THEN
    RAISE EXCEPTION 'not permitted' USING ERRCODE = 'insufficient_privilege';
  END IF;
  IF r.state = 'released' THEN
    RETURN jsonb_build_object('outcome', 'released', 'usage_id', r.usage_id, 'state', r.state, 'idempotent', true);
  ELSIF r.state <> 'reserved' THEN
    RETURN jsonb_build_object('outcome', 'conflict', 'usage_id', r.usage_id, 'state', r.state);
  END IF;
  UPDATE platform.usage_reservation SET state = 'released', finished_at = v_now WHERE usage_id = p_usage;
  INSERT INTO platform.usage_ledger (occurred_at, entitlement_id, student_id, usage_id, kind, quantity, actor, reason_code)
  VALUES (v_now, r.entitlement_id, r.student_id, r.usage_id, 'release', r.reserved_qty, 'bff', coalesce(p_reason, 'release'));
  UPDATE platform.entitlement SET reserved = reserved - r.reserved_qty WHERE id = r.entitlement_id;
  PERFORM platform.meter_check_entitlement(r.entitlement_id);
  RETURN jsonb_build_object('outcome', 'released', 'usage_id', r.usage_id, 'state', 'released', 'idempotent', false);
END $$;

-- ---------- 定时任务：过期、续期（只授权给 starbuddy_scheduler） ----------
-- 扫描不加锁，只取 usage_id；逐笔按“先额度桶、再预留”加锁并重查状态（设计文档 4.0、场景 5）
CREATE FUNCTION platform.meter_expire_due(p_batch integer) RETURNS integer
  LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, platform, pg_temp
  AS $$
DECLARE
  v_now timestamptz := platform.meter_now();
  v_usage uuid;
  r platform.usage_reservation;
  v_count integer := 0;
BEGIN
  IF p_batch IS NULL OR p_batch < 1 OR p_batch > 1000 THEN
    RAISE EXCEPTION 'batch must be between 1 and 1000' USING ERRCODE = 'invalid_parameter_value';
  END IF;
  FOR v_usage IN SELECT x.usage_id FROM platform.usage_reservation x
                  WHERE x.state = 'reserved' AND x.expires_at < v_now ORDER BY x.expires_at LIMIT p_batch LOOP
    r := platform.meter_lock_reservation(v_usage);
    CONTINUE WHEN r.state <> 'reserved' OR r.expires_at >= v_now;
    UPDATE platform.usage_reservation SET state = 'expired', finished_at = v_now WHERE usage_id = v_usage;
    INSERT INTO platform.usage_ledger (occurred_at, entitlement_id, student_id, usage_id, kind, quantity, actor, reason_code)
    VALUES (v_now, r.entitlement_id, r.student_id, r.usage_id, 'expire', r.reserved_qty, 'system', 'ttl_elapsed');
    UPDATE platform.entitlement SET reserved = reserved - r.reserved_qty WHERE id = r.entitlement_id;
    PERFORM platform.meter_check_entitlement(r.entitlement_id);
    v_count := v_count + 1;
  END LOOP;
  RETURN v_count;
END $$;

-- 续期：只处理不收费的订阅（trial、pilot_virtual）；付费续期等支付接入。应用下一周期的档位与配置（降级到期生效）。
CREATE FUNCTION platform.meter_renew_due(p_batch integer) RETURNS integer
  LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, platform, pg_temp
  AS $$
DECLARE
  v_now timestamptz := platform.meter_now();
  s platform.subscription;
  v_count integer := 0;
BEGIN
  IF p_batch IS NULL OR p_batch < 1 OR p_batch > 1000 THEN
    RAISE EXCEPTION 'batch must be between 1 and 1000' USING ERRCODE = 'invalid_parameter_value';
  END IF;
  FOR s IN SELECT * FROM platform.subscription
            WHERE status IN ('active', 'grace') AND source IN ('trial', 'pilot_virtual') AND period_end <= v_now
            ORDER BY period_end LIMIT p_batch FOR UPDATE SKIP LOCKED LOOP
    UPDATE platform.subscription
       SET period_start = s.period_end, period_end = s.period_end + interval '30 days', status = 'active',
           tier = coalesce(s.next_tier, s.tier), config_version = coalesce(s.next_config_version, s.config_version),
           next_tier = NULL, next_config_version = NULL
     WHERE id = s.id;
    PERFORM platform.meter_grant_period(s.id);
    v_count := v_count + 1;
  END LOOP;
  RETURN v_count;
END $$;

-- ---------- 开通订阅（注册后的基础会员、试点虚拟额度） ----------
-- 只有已绑定该学生的家长能开通（家长是支付主体）。试点开通的名单由 BFF 的配置控制（spec 待人确认 #5）。
CREATE FUNCTION platform.meter_open_subscription(p_student uuid, p_tier platform.plan_tier, p_config text, p_source text)
  RETURNS jsonb
  LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, platform, pg_temp
  AS $$
DECLARE
  v_now timestamptz := platform.meter_now();
  v_household uuid;
  v_id uuid;
BEGIN
  IF p_student IS NULL OR NOT platform.is_linked_guardian(p_student) THEN
    RAISE EXCEPTION 'not permitted' USING ERRCODE = 'insufficient_privilege';
  END IF;
  IF p_source NOT IN ('trial', 'pilot_virtual') THEN
    RAISE EXCEPTION 'only trial and pilot_virtual subscriptions can be opened here' USING ERRCODE = 'invalid_parameter_value';
  END IF;
  IF p_source = 'trial' AND p_tier <> 'basic' THEN
    RAISE EXCEPTION 'a trial subscription is always basic' USING ERRCODE = 'invalid_parameter_value';
  END IF;
  SELECT household_id INTO v_household FROM platform.student_profile WHERE id = p_student;
  BEGIN
    INSERT INTO platform.subscription (household_id, student_id, tier, config_version, source, period_start, period_end)
    VALUES (v_household, p_student, p_tier, p_config, p_source, v_now, v_now + interval '30 days')
    RETURNING id INTO v_id;
  EXCEPTION WHEN unique_violation THEN
    RETURN jsonb_build_object('outcome', 'already_active');
  END;
  PERFORM platform.meter_grant_period(v_id);
  RETURN jsonb_build_object('outcome', 'opened', 'subscription_id', v_id, 'period_end', v_now + interval '30 days');
END $$;

-- ---------- 选科上限（1-E 待人确认 #6 的默认） ----------
-- 选科只能经函数写入：按当前有效主订阅的 max_active_subjects 校验；同一学生的并发选科按学生加锁。
CREATE FUNCTION platform.enroll_subject(p_student uuid, p_subject platform.subject) RETURNS jsonb
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
  SELECT f.value_code INTO v_value
    FROM platform.subscription s
    JOIN platform.plan_feature f ON f.config_version = s.config_version AND f.tier = s.tier AND f.feature_code = 'max_active_subjects'
   WHERE s.student_id = p_student AND s.status IN ('active', 'grace') AND s.source <> 'addon'
     AND v_now >= s.period_start AND v_now < s.period_end;
  IF NOT FOUND THEN
    RETURN jsonb_build_object('outcome', 'no_subscription');
  END IF;
  -- all_launched：按存在已发布课程版本的学科计（spec 待人确认 #7）
  v_max := CASE WHEN v_value = 'all_launched'
                THEN (SELECT count(DISTINCT subject) FROM platform.curriculum_version WHERE status = 'published')::integer
                ELSE v_value::integer END;
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

CREATE FUNCTION platform.pause_subject(p_student uuid, p_subject platform.subject) RETURNS jsonb
  LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, platform, pg_temp
  AS $$
BEGIN
  IF p_student IS NULL OR p_subject IS NULL OR NOT platform.can_read_student(p_student) THEN
    RAISE EXCEPTION 'not permitted' USING ERRCODE = 'insufficient_privilege';
  END IF;
  UPDATE platform.subject_enrollment SET status = 'paused' WHERE student_id = p_student AND subject = p_subject AND status = 'active';
  RETURN jsonb_build_object('outcome', CASE WHEN FOUND THEN 'paused' ELSE 'not_active' END);
END $$;

-- 选科改为只能经函数写入：收回 BFF 的直接 INSERT（schema spec 待人确认 #11）
DROP POLICY enrollment_write ON platform.subject_enrollment;
REVOKE INSERT ON platform.subject_enrollment FROM starbuddy_bff;

-- ---------- 属主与授权 ----------
ALTER FUNCTION platform.meter_check_entitlement(uuid) OWNER TO starbuddy_authz;
ALTER FUNCTION platform.meter_grant_period(uuid) OWNER TO starbuddy_authz;
ALTER FUNCTION platform.meter_reserve(uuid, text, text, text, numeric, text, text) OWNER TO starbuddy_authz;
ALTER FUNCTION platform.meter_same_key(platform.usage_reservation, uuid, text, text) OWNER TO starbuddy_authz;
ALTER FUNCTION platform.meter_lock_reservation(uuid) OWNER TO starbuddy_authz;
ALTER FUNCTION platform.meter_settle(uuid, numeric) OWNER TO starbuddy_authz;
ALTER FUNCTION platform.meter_release(uuid, text) OWNER TO starbuddy_authz;
ALTER FUNCTION platform.meter_expire_due(integer) OWNER TO starbuddy_authz;
ALTER FUNCTION platform.meter_renew_due(integer) OWNER TO starbuddy_authz;
ALTER FUNCTION platform.meter_open_subscription(uuid, platform.plan_tier, text, text) OWNER TO starbuddy_authz;
ALTER FUNCTION platform.enroll_subject(uuid, platform.subject) OWNER TO starbuddy_authz;
ALTER FUNCTION platform.pause_subject(uuid, platform.subject) OWNER TO starbuddy_authz;

REVOKE EXECUTE ON FUNCTION platform.meter_now(), platform.meter_uuidv5(uuid, text), platform.meter_usage_id(text),
  platform.meter_check_entitlement(uuid), platform.meter_grant_period(uuid),
  platform.meter_reserve(uuid, text, text, text, numeric, text, text),
  platform.meter_same_key(platform.usage_reservation, uuid, text, text), platform.meter_lock_reservation(uuid),
  platform.meter_settle(uuid, numeric), platform.meter_release(uuid, text), platform.meter_expire_due(integer),
  platform.meter_renew_due(integer), platform.meter_open_subscription(uuid, platform.plan_tier, text, text),
  platform.enroll_subject(uuid, platform.subject), platform.pause_subject(uuid, platform.subject) FROM PUBLIC;
-- 属主需要能调用自己用到的非 SECURITY DEFINER 工具函数
GRANT EXECUTE ON FUNCTION platform.meter_now(), platform.meter_uuidv5(uuid, text), platform.meter_usage_id(text) TO starbuddy_authz;
-- 0008 的保护触发器以调用者身份执行，要用到锁键函数
GRANT EXECUTE ON FUNCTION platform.plan_config_lock_key(text) TO starbuddy_authz;

GRANT EXECUTE ON FUNCTION platform.meter_reserve(uuid, text, text, text, numeric, text, text),
  platform.meter_settle(uuid, numeric), platform.meter_release(uuid, text),
  platform.meter_open_subscription(uuid, platform.plan_tier, text, text),
  platform.enroll_subject(uuid, platform.subject), platform.pause_subject(uuid, platform.subject),
  platform.meter_usage_id(text), platform.meter_uuidv5(uuid, text) TO starbuddy_bff;
GRANT EXECUTE ON FUNCTION platform.meter_expire_due(integer), platform.meter_renew_due(integer) TO starbuddy_scheduler;

-- 函数属主的最小授权（设计文档第 7 节）：保护触发器以调用者（属主）身份读表，所以也要 SELECT
GRANT SELECT ON platform.resource_type, platform.plan_allowance, platform.plan_feature, platform.subscription,
  platform.entitlement, platform.usage_reservation, platform.usage_ledger, platform.curriculum_version,
  platform.subject_enrollment TO starbuddy_authz;
GRANT INSERT ON platform.subscription, platform.entitlement, platform.usage_reservation, platform.usage_ledger,
  platform.subject_enrollment TO starbuddy_authz;
GRANT UPDATE (tier, config_version, status, period_start, period_end, next_tier, next_config_version)
  ON platform.subscription TO starbuddy_authz;
GRANT UPDATE (granted, reserved, settled) ON platform.entitlement TO starbuddy_authz;
GRANT UPDATE (state, settled_qty, finished_at) ON platform.usage_reservation TO starbuddy_authz;
GRANT UPDATE (status) ON platform.subject_enrollment TO starbuddy_authz;
