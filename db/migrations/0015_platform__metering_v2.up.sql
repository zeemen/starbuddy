-- 0015 platform · 计量与权益 v2（specs/metering-v2/spec.md；规格书 V1.2 §3–§7、§12.2）
-- 在 0008 / 0009 的账本之上加：试用 14 天与一次性领取、留存态、学科目录与“核心 + 选修槽”、上传 / 存储 / 深度报告计量、
-- 单日上限与宵禁、题库自练、升级补差。订阅状态值不改名（active / grace / ended），规格的 trialing / retained
-- 由函数 platform.meter_student_state 推出，不另存（规则 2）。已发布的 ch8-hypothesis@1 不动；新配置 v12-pilot@1 为草稿，由人发布。
-- 写入一律经 SECURITY DEFINER 函数（属主 starbuddy_authz，固定 search_path），与 0009 相同的加锁顺序：
--   学生级 advisory 锁（单日上限、自练、选科）→ 额度桶（按 id 升序）→ 预留。

-- ---------- 一、资源类型：上传、存储、深度报告 ----------
-- 上传按页折算后一笔可以大于 1，仍按内容去重（同一文件 24 小时内不重复扣），所以去掉“可去重只能单次 1”的约束
ALTER TABLE platform.resource_type DROP CONSTRAINT resource_dedup_single;
ALTER TABLE platform.resource_type DROP CONSTRAINT resource_type_unit_check;
ALTER TABLE platform.resource_type ADD CONSTRAINT resource_type_unit_check CHECK (unit IN ('count', 'minute', 'byte'));
-- curfew_applies：宵禁时段内不提供（AI 互动类）；bff_only：不对应插件方法，由 BFF 自己发起（source_method 为空）
ALTER TABLE platform.resource_type ADD COLUMN curfew_applies boolean NOT NULL DEFAULT true;
ALTER TABLE platform.resource_type ADD COLUMN bff_only boolean NOT NULL DEFAULT false;
COMMENT ON COLUMN platform.resource_type.curfew_applies IS '宵禁时段是否拒绝（规格 §7、§12.2：22:00–6:00 默认不提供 AI 互动）';
COMMENT ON COLUMN platform.resource_type.bff_only IS '不对应学科插件方法（上传、存储、深度报告），预留时 source_method 必须为空';

INSERT INTO platform.resource_type (code, unit, rounding, dedup_window, pool, reservation_ttl, max_per_reservation, curfew_applies, bff_only) VALUES
  -- 1 次 = 1 个文件；PDF 按 ceil(页数 / 4) 计，单份最多 32 页 → 8 次（规格 §4、§7）
  ('upload',        'count', 'none', interval '24 hours', 'general', interval '30 minutes', 8,        true,  true),
  -- 周期内累计上传的字节数（周期计数，删文件不退回；spec 待人确认，见 lessons）；单文件最大 50MB
  ('storage_bytes', 'byte',  'none', NULL,                'general', interval '30 minutes', 52428800, false, true),
  -- 按需深度报告：家长或学生发起；证据不足由调用方释放，不扣次
  ('deep_report',   'count', 'none', NULL,                'general', interval '30 minutes', 1,        false, true);

-- ---------- 二、权益代码：核心科目、选修槽、单日上限 ----------
ALTER TABLE platform.plan_feature DROP CONSTRAINT plan_feature_feature_code_check;
ALTER TABLE platform.plan_feature ADD CONSTRAINT plan_feature_feature_code_check CHECK (feature_code IN (
  'max_active_subjects', 'lesson_mode', 'weekly_report', 'appeal_priority',
  'core_subjects', 'elective_slots', 'daily_minutes_cap'));
ALTER TABLE platform.plan_feature ADD CONSTRAINT plan_feature_value_ok CHECK (
  CASE feature_code
    WHEN 'core_subjects' THEN value_code IN ('pick_one', 'all_launched')
    WHEN 'elective_slots' THEN value_code ~ '^([0-9]|all_launched)$'
    WHEN 'daily_minutes_cap' THEN value_code ~ '^([1-9][0-9]{0,2}|1[0-3][0-9]{2}|14[0-3][0-9]|1440)$'
    ELSE true END);

-- 发布时的齐全检查（替换 0008）：
--   额度：四档的资源集合相同且非空（配置可以不含某个资源，不含即不可用）；
--   权益：每档都有 lesson_mode、weekly_report、appeal_priority，以及选科规则（旧的 max_active_subjects，或新的 core_subjects + elective_slots）；
--   daily_minutes_cap 可缺省，缺省时按 180 分钟（规格 §4 的最高档）。
CREATE OR REPLACE FUNCTION platform.guard_plan_config() RETURNS trigger
  LANGUAGE plpgsql SET search_path = pg_catalog, platform, pg_temp
  AS $$
DECLARE v_resources integer;
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
    SELECT count(DISTINCT resource_type) INTO v_resources FROM platform.plan_allowance WHERE config_version = OLD.version;
    IF v_resources = 0
       OR (SELECT count(*) FROM platform.plan_allowance a WHERE a.config_version = OLD.version) <> 4 * v_resources
       OR (SELECT count(*) FROM unnest(enum_range(NULL::platform.plan_tier)) t(tier)
            WHERE (SELECT count(*) FROM platform.plan_feature f
                    WHERE f.config_version = OLD.version AND f.tier = t.tier
                      AND f.feature_code IN ('lesson_mode', 'weekly_report', 'appeal_priority')) = 3
              AND (EXISTS (SELECT 1 FROM platform.plan_feature f
                            WHERE f.config_version = OLD.version AND f.tier = t.tier AND f.feature_code = 'max_active_subjects')
                   OR (SELECT count(*) FROM platform.plan_feature f
                        WHERE f.config_version = OLD.version AND f.tier = t.tier
                          AND f.feature_code IN ('core_subjects', 'elective_slots')) = 2)) <> 4 THEN
      RAISE EXCEPTION 'plan_config % is incomplete: every tier needs the same resources and every required feature', OLD.version
        USING ERRCODE = 'check_violation';
    END IF;
  END IF;
  RETURN NEW;
END $$;

-- ---------- 三、学科目录（规格 §5；不改 platform.subject 产品命名空间的语义） ----------
-- 上线状态由人维护（迁移或 DBA）；应用角色只读。planned 不可选；beta 只对试点订阅开放；live 对所有付费与试用开放。
CREATE TABLE platform.subject_catalog (
  code           text PRIMARY KEY CHECK (code IN ('chinese', 'math', 'english', 'politics', 'history',
                                                  'physics', 'chemistry', 'biology', 'geography')),
  teacher_code   text CHECK (teacher_code ~ '^[a-z][a-z0-9_]{1,31}$'),
  status         text NOT NULL CHECK (status IN ('planned', 'beta', 'live')),
  grades         smallint[] NOT NULL CHECK (cardinality(grades) BETWEEN 1 AND 3 AND grades <@ ARRAY[7, 8, 9]::smallint[]),
  is_core        boolean NOT NULL,
  product        platform.subject UNIQUE
);
COMMENT ON TABLE platform.subject_catalog IS '学科目录（规格书 V1.2 §5）。老师角色码为空表示未定义（人设属必停项）；product 对应现有产品命名空间';
INSERT INTO platform.subject_catalog (code, teacher_code, status, grades, is_core, product) VALUES
  ('chinese',   'miaobi',   'beta',    '{7,8,9}', true,  'wenli'),
  ('math',      'aya',      'beta',    '{7,8,9}', true,  'aya'),
  -- 英语搁置（对照文档决定 #1）：planned，不售卖
  ('english',   'athena',   'planned', '{7,8,9}', true,  'echo'),
  ('politics',  'fangyuan', 'beta',    '{7,8,9}', false, 'zhixing'),
  ('history',   'chunqiu',  'planned', '{7,8,9}', false, NULL),
  ('physics',   'aji',      'planned', '{8,9}',   false, NULL),
  ('chemistry', 'dr_x',     'planned', '{9}',     false, NULL),
  -- 生物、地理：规格 §13 第 3 项“未立项”，不建老师角色
  ('biology',   NULL,       'planned', '{7,8}',   false, NULL),
  ('geography', NULL,       'planned', '{7,8}',   false, NULL);

-- 暂停时间：用来实现“换科下个周期生效、每周期换 1 次”（规格 §5）。系统因降级暂停时为空，不算换科。
ALTER TABLE platform.subject_enrollment ADD COLUMN paused_at timestamptz;

-- ---------- 四、试用一次性领取（规格 §3、§8 trial_claim） ----------
-- 手机号只存带密钥的摘要（HMAC-SHA256，密钥只有函数属主能读），不存明文，也不用于关联学生数据（规则 5）
CREATE TABLE platform.meter_secret (
  id       boolean PRIMARY KEY DEFAULT true CHECK (id),
  pepper   bytea NOT NULL CHECK (length(pepper) = 32)
);
DO $$
DECLARE v_schema text;
BEGIN
  SELECT n.nspname INTO v_schema FROM pg_extension e JOIN pg_namespace n ON n.oid = e.extnamespace WHERE e.extname = 'pgcrypto';
  IF v_schema IS NULL OR to_regprocedure(format('%I.hmac(bytea, bytea, text)', v_schema)) IS NULL THEN
    RAISE EXCEPTION 'pgcrypto 未安装（0009 应已安装）';
  END IF;
  EXECUTE format('INSERT INTO platform.meter_secret (pepper) VALUES (%I.gen_random_bytes(32))', v_schema);
  EXECUTE format($f$CREATE FUNCTION platform.meter_phone_digest(p text) RETURNS text
                   LANGUAGE sql STABLE STRICT SET search_path = pg_catalog
                   AS 'SELECT encode(%I.hmac(convert_to(p, ''UTF8''), (SELECT pepper FROM platform.meter_secret), ''sha256''), ''hex'')'$f$,
                 v_schema);
END $$;
ALTER TABLE platform.meter_secret ENABLE ROW LEVEL SECURITY;
ALTER TABLE platform.meter_secret FORCE ROW LEVEL SECURITY;

CREATE TABLE platform.trial_claim (
  household_id   uuid PRIMARY KEY REFERENCES platform.household (id) ON DELETE CASCADE,
  student_id     uuid REFERENCES platform.student_profile (id) ON DELETE SET NULL,
  claimed_at     timestamptz NOT NULL
);
CREATE TABLE platform.trial_claim_phone (
  phone_digest   text PRIMARY KEY CHECK (phone_digest ~ '^[0-9a-f]{64}$'),
  household_id   uuid NOT NULL REFERENCES platform.trial_claim (household_id) ON DELETE CASCADE
);
COMMENT ON TABLE platform.trial_claim IS '试用一次性领取：同一家庭、同一手机号（摘要）各一次。设备指纹随 PLT-01 的设备表';
ALTER TABLE platform.trial_claim ENABLE ROW LEVEL SECURITY;
ALTER TABLE platform.trial_claim FORCE ROW LEVEL SECURITY;
ALTER TABLE platform.trial_claim_phone ENABLE ROW LEVEL SECURITY;
ALTER TABLE platform.trial_claim_phone FORCE ROW LEVEL SECURITY;

-- ---------- 五、单日上限与宵禁（规格 §7、§12.2） ----------
-- 家长只能调低上限、只能收紧宵禁（宵禁时段必须覆盖默认的 22:00–6:00，Asia/Shanghai）。没有行 = 全部取默认。
CREATE TABLE platform.student_time_guard (
  student_id          uuid PRIMARY KEY REFERENCES platform.student_profile (id) ON DELETE CASCADE,
  daily_cap_minutes   smallint CHECK (daily_cap_minutes BETWEEN 0 AND 1440),
  curfew_start        time,
  curfew_end          time,
  updated_at          timestamptz NOT NULL,
  updated_by          uuid NOT NULL REFERENCES platform.account (id),
  CHECK ((curfew_start IS NULL) = (curfew_end IS NULL)),
  CHECK (curfew_start IS DISTINCT FROM curfew_end OR curfew_start IS NULL)
);
ALTER TABLE platform.student_time_guard ENABLE ROW LEVEL SECURITY;
ALTER TABLE platform.student_time_guard FORCE ROW LEVEL SECURITY;
CREATE POLICY student_time_guard_read ON platform.student_time_guard FOR SELECT TO starbuddy_bff
  USING (platform.can_read_student(student_id));

-- ---------- 六、题库自练记录（留存用户每天 5 次；不调用 AI，不进额度账本） ----------
CREATE TABLE platform.practice_log (
  usage_id          uuid PRIMARY KEY,
  idempotency_key   text NOT NULL UNIQUE CHECK (length(idempotency_key) BETWEEN 16 AND 128),
  student_id        uuid NOT NULL REFERENCES platform.student_profile (id) ON DELETE CASCADE,
  occurred_at       timestamptz NOT NULL
);
CREATE INDEX practice_log_student_idx ON platform.practice_log (student_id, occurred_at);
ALTER TABLE platform.practice_log ENABLE ROW LEVEL SECURITY;
ALTER TABLE platform.practice_log FORCE ROW LEVEL SECURITY;
CREATE POLICY practice_log_read ON platform.practice_log FOR SELECT TO starbuddy_bff USING (platform.can_read_student(student_id));
CREATE FUNCTION platform.reject_practice_log_change() RETURNS trigger
  LANGUAGE plpgsql SET search_path = pg_catalog, platform, pg_temp
  AS $$
BEGIN
  RAISE EXCEPTION 'practice_log is append-only' USING ERRCODE = 'insufficient_privilege';
END $$;
CREATE TRIGGER reject_practice_log_change BEFORE UPDATE ON platform.practice_log
  FOR EACH ROW EXECUTE FUNCTION platform.reject_practice_log_change();

-- ---------- 七、推导函数 ----------
-- 学生当前的主订阅（有效、非附加、计量时钟落在周期内）
CREATE FUNCTION platform.meter_main_subscription(p_student uuid) RETURNS platform.subscription
  LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, platform, pg_temp
  AS $$ SELECT s.* FROM platform.subscription s
         WHERE s.student_id = p_student AND s.status IN ('active', 'grace') AND s.source <> 'addon'
           AND platform.meter_now() >= s.period_start AND platform.meter_now() < s.period_end
         ORDER BY s.period_start DESC LIMIT 1 $$;

-- 规格 §3 的状态：trialing（试用中）、active（付费或试点）、grace（宽限期）、retained（曾有订阅、现无有效订阅）、none（从未开通）
-- 按订阅状态判断，不看计量时钟是否落在周期内：周期刚结束、续期任务还没跑到时仍是原状态，不算留存
CREATE FUNCTION platform.meter_state_of(p_student uuid) RETURNS text
  LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = pg_catalog, platform, pg_temp
  AS $$
DECLARE s platform.subscription;
BEGIN
  SELECT * INTO s FROM platform.subscription x
   WHERE x.student_id = p_student AND x.status IN ('active', 'grace') AND x.source <> 'addon'
   ORDER BY x.period_start DESC LIMIT 1;
  IF s.id IS NOT NULL THEN
    RETURN CASE WHEN s.status = 'grace' THEN 'grace' WHEN s.source = 'trial' THEN 'trialing' ELSE 'active' END;
  END IF;
  RETURN CASE WHEN EXISTS (SELECT 1 FROM platform.subscription x WHERE x.student_id = p_student AND x.source <> 'addon')
              THEN 'retained' ELSE 'none' END;
END $$;

-- 给前端看：只有学生本人或已绑定家长能查
CREATE FUNCTION platform.meter_student_state(p_student uuid) RETURNS text
  LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = pg_catalog, platform, pg_temp
  AS $$
BEGIN
  IF p_student IS NULL OR NOT platform.can_read_student(p_student) THEN
    RAISE EXCEPTION 'not permitted' USING ERRCODE = 'insufficient_privilege';
  END IF;
  RETURN platform.meter_state_of(p_student);
END $$;

-- 档位的单日上限（分钟）：取当前主订阅配置的 daily_minutes_cap；没有订阅或配置缺省时 180
CREATE FUNCTION platform.meter_plan_daily_cap(p_student uuid) RETURNS integer
  LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, platform, pg_temp
  AS $$ SELECT coalesce((SELECT f.value_code::integer FROM platform.plan_feature f
                          JOIN platform.meter_main_subscription(p_student) s ON f.config_version = s.config_version AND f.tier = s.tier
                         WHERE f.feature_code = 'daily_minutes_cap'), 180) $$;

-- 生效的单日上限：家长设定与档位上限取小
CREATE FUNCTION platform.meter_daily_cap(p_student uuid) RETURNS integer
  LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, platform, pg_temp
  AS $$ SELECT least(platform.meter_plan_daily_cap(p_student),
                     coalesce((SELECT g.daily_cap_minutes FROM platform.student_time_guard g WHERE g.student_id = p_student), 1440)) $$;

-- 时间窗口（可跨午夜；start > end 表示跨午夜）
CREATE FUNCTION platform.meter_in_window(p_t time, p_start time, p_end time) RETURNS boolean
  LANGUAGE sql IMMUTABLE SET search_path = pg_catalog
  AS $$ SELECT CASE WHEN p_start < p_end THEN p_t >= p_start AND p_t < p_end ELSE p_t >= p_start OR p_t < p_end END $$;

CREATE FUNCTION platform.meter_in_curfew(p_student uuid, p_at timestamptz) RETURNS boolean
  LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, platform, pg_temp
  AS $$ SELECT platform.meter_in_window((p_at AT TIME ZONE 'Asia/Shanghai')::time,
                                        coalesce(g.curfew_start, time '22:00'), coalesce(g.curfew_end, time '06:00'))
          FROM (SELECT 1) one LEFT JOIN platform.student_time_guard g ON g.student_id = p_student $$;

-- 当日（Asia/Shanghai）已用分钟：分钟类资源的已结算 + 预留中（释放、过期的不算）
CREATE FUNCTION platform.meter_minutes_today(p_student uuid, p_at timestamptz) RETURNS numeric
  LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, platform, pg_temp
  AS $$ SELECT coalesce(sum(CASE r.state WHEN 'settled' THEN r.settled_qty ELSE r.reserved_qty END), 0)
          FROM platform.usage_reservation r JOIN platform.resource_type rt ON rt.code = r.resource_type
         WHERE r.student_id = p_student AND rt.unit = 'minute' AND r.state IN ('reserved', 'settled')
           AND r.reserved_at >= ((p_at AT TIME ZONE 'Asia/Shanghai')::date::timestamp AT TIME ZONE 'Asia/Shanghai')
           AND r.reserved_at < ((p_at AT TIME ZONE 'Asia/Shanghai')::date::timestamp AT TIME ZONE 'Asia/Shanghai') + interval '1 day' $$;

-- ---------- 八、预留（替换 0009 的 meter_reserve；主体移到 meter_reserve_impl） ----------
-- 新增的拒绝（都在同 key 检查之后，重试已有预留不受影响）：
--   retained_no_ai：留存态（无有效订阅）；curfew：宵禁时段；daily_cap_reached：分钟类资源超过当日上限。
-- 上传只能经 meter_reserve_upload（按页折算次数），不能直接指定数量。
CREATE FUNCTION platform.meter_reserve_impl(p_student uuid, p_resource text, p_key text, p_fingerprint text, p_quantity numeric,
                                            p_content_fingerprint text, p_source_method text, p_via_upload boolean) RETURNS jsonb
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
  v_cap integer;
  v_used numeric;
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
  IF (p_resource = 'upload') <> p_via_upload THEN
    RAISE EXCEPTION 'uploads must be reserved through meter_reserve_upload' USING ERRCODE = 'invalid_parameter_value';
  END IF;
  IF rt.bff_only THEN
    IF p_source_method IS NOT NULL THEN
      RAISE EXCEPTION '% is not tied to a plugin method', p_resource USING ERRCODE = 'invalid_parameter_value';
    END IF;
  ELSIF p_source_method IS NULL OR NOT platform.meter_method_allowed(p_resource, p_source_method) THEN
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

  SELECT * INTO r FROM platform.usage_reservation WHERE idempotency_key = p_key;
  IF FOUND THEN
    RETURN platform.meter_same_key(r, p_student, p_resource, p_fingerprint, p_content_fingerprint, p_source_method);
  END IF;

  -- 留存态、宵禁、单日上限（规格 §3、§7、§12.2）
  IF platform.meter_state_of(p_student) = 'retained' THEN
    RETURN jsonb_build_object('outcome', 'retained_no_ai');
  END IF;
  IF rt.curfew_applies AND platform.meter_in_curfew(p_student, v_now) THEN
    RETURN jsonb_build_object('outcome', 'curfew');
  END IF;
  IF rt.unit = 'minute' THEN
    -- 同一学生的分钟类预留串行，挡住并发的两笔各自没超、合计超过上限
    PERFORM pg_advisory_xact_lock(hashtextextended('platform.meter_daily:' || p_student, 0));
    v_cap := platform.meter_daily_cap(p_student);
    v_used := platform.meter_minutes_today(p_student, v_now);
    IF v_used + p_quantity > v_cap THEN
      RETURN jsonb_build_object('outcome', 'daily_cap_reached', 'remaining_today', greatest(v_cap - v_used, 0));
    END IF;
  END IF;

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
      VALUES (v_now, v_first, p_student, v_usage, 'reserve', 0, 'bff', 'dedup_24h', p_source_method,
              CASE WHEN p_source_method IS NOT NULL THEN p_key END);
      PERFORM platform.meter_check_entitlement(v_first);
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
    RETURN jsonb_build_object('outcome', 'conflict');
  END;
  INSERT INTO platform.usage_ledger (occurred_at, entitlement_id, student_id, usage_id, kind, quantity, actor, reason_code,
    source_method, source_idempotency_key)
  VALUES (v_now, v_target, p_student, v_usage, 'reserve', p_quantity, 'bff', 'reserve', p_source_method,
          CASE WHEN p_source_method IS NOT NULL THEN p_key END);
  UPDATE platform.entitlement SET reserved = reserved + p_quantity WHERE id = v_target;
  PERFORM platform.meter_check_entitlement(v_target);
  RETURN jsonb_build_object('outcome', 'reserved', 'usage_id', v_usage, 'state', 'reserved', 'reserved_qty', p_quantity,
                            'expires_at', v_now + rt.reservation_ttl, 'remaining', v_remaining - p_quantity);
END $$;

CREATE OR REPLACE FUNCTION platform.meter_reserve(p_student uuid, p_resource text, p_key text, p_fingerprint text, p_quantity numeric,
                                                  p_content_fingerprint text, p_source_method text) RETURNS jsonb
  LANGUAGE sql SECURITY DEFINER SET search_path = pg_catalog, platform, pg_temp
  AS $$ SELECT platform.meter_reserve_impl(p_student, p_resource, p_key, p_fingerprint, p_quantity,
                                           p_content_fingerprint, p_source_method, false) $$;

-- 上传：1 个文件 1 次；PDF 按 ceil(页数 / 4)（规格 §7）。p_pages 为空表示图片。页数上限 32（规格 §4 最高档），按档位的页数上限由 BFF 在签发上传地址前核对。
CREATE FUNCTION platform.meter_reserve_upload(p_student uuid, p_key text, p_fingerprint text, p_content_fingerprint text,
                                              p_pages integer) RETURNS jsonb
  LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, platform, pg_temp
  AS $$
BEGIN
  IF p_pages IS NOT NULL AND (p_pages < 1 OR p_pages > 32) THEN
    RAISE EXCEPTION 'pages must be between 1 and 32' USING ERRCODE = 'invalid_parameter_value';
  END IF;
  RETURN platform.meter_reserve_impl(p_student, 'upload', p_key, p_fingerprint,
                                     CASE WHEN p_pages IS NULL THEN 1 ELSE ceil(p_pages / 4.0) END,
                                     p_content_fingerprint, NULL, true);
END $$;

-- ---------- 九、题库自练（留存态每天 5 次；试用、付费、宽限不限） ----------
CREATE FUNCTION platform.meter_practice(p_student uuid, p_key text) RETURNS jsonb
  LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, platform, pg_temp
  AS $$
DECLARE
  v_now timestamptz := platform.meter_now();
  v_state text;
  v_used integer;
  v_existing platform.practice_log;
  v_day_start timestamptz;
BEGIN
  IF p_student IS NULL OR NOT platform.is_self_student(p_student) THEN
    RAISE EXCEPTION 'not permitted' USING ERRCODE = 'insufficient_privilege';
  END IF;
  IF p_key IS NULL OR p_key !~ '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$' THEN
    RAISE EXCEPTION 'idempotency_key must be a lowercase UUIDv4' USING ERRCODE = 'invalid_parameter_value';
  END IF;
  PERFORM pg_advisory_xact_lock(hashtextextended('platform.meter_practice:' || p_student, 0));
  SELECT * INTO v_existing FROM platform.practice_log WHERE idempotency_key = p_key;
  IF FOUND THEN
    RETURN CASE WHEN v_existing.student_id = p_student THEN jsonb_build_object('outcome', 'existing', 'usage_id', v_existing.usage_id)
                ELSE jsonb_build_object('outcome', 'conflict') END;
  END IF;
  v_state := platform.meter_state_of(p_student);
  IF v_state = 'none' THEN
    RETURN jsonb_build_object('outcome', 'no_entitlement');
  END IF;
  IF v_state = 'retained' THEN
    v_day_start := (v_now AT TIME ZONE 'Asia/Shanghai')::date::timestamp AT TIME ZONE 'Asia/Shanghai';
    SELECT count(*) INTO v_used FROM platform.practice_log
     WHERE student_id = p_student AND occurred_at >= v_day_start AND occurred_at < v_day_start + interval '1 day';
    IF v_used >= 5 THEN
      RETURN jsonb_build_object('outcome', 'daily_limit_reached', 'remaining_today', 0);
    END IF;
  END IF;
  BEGIN
    INSERT INTO platform.practice_log (usage_id, idempotency_key, student_id, occurred_at)
    VALUES (platform.meter_usage_id(p_key), p_key, p_student, v_now);
  EXCEPTION WHEN unique_violation THEN
    RETURN jsonb_build_object('outcome', 'conflict');
  END;
  RETURN jsonb_build_object('outcome', 'allowed', 'usage_id', platform.meter_usage_id(p_key),
                            'remaining_today', CASE WHEN v_state = 'retained' THEN 4 - v_used END);
END $$;

-- ---------- 十、家长设置单日上限与宵禁（只能调低、只能收紧） ----------
CREATE FUNCTION platform.set_time_guard(p_student uuid, p_daily_cap integer, p_curfew_start time, p_curfew_end time) RETURNS jsonb
  LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, platform, pg_temp
  AS $$
DECLARE
  v_plan_cap integer;
  m integer;
BEGIN
  IF p_student IS NULL OR NOT platform.is_linked_guardian(p_student) THEN
    RAISE EXCEPTION 'not permitted' USING ERRCODE = 'insufficient_privilege';
  END IF;
  v_plan_cap := platform.meter_plan_daily_cap(p_student);
  IF p_daily_cap IS NOT NULL AND (p_daily_cap < 0 OR p_daily_cap > v_plan_cap) THEN
    RETURN jsonb_build_object('outcome', 'cap_above_plan', 'plan_cap', v_plan_cap);
  END IF;
  IF (p_curfew_start IS NULL) <> (p_curfew_end IS NULL) OR (p_curfew_start IS NOT NULL AND p_curfew_start = p_curfew_end) THEN
    RAISE EXCEPTION 'curfew needs both a start and a different end' USING ERRCODE = 'invalid_parameter_value';
  END IF;
  IF p_curfew_start IS NOT NULL THEN
    -- 新时段必须覆盖默认时段的每一分钟
    FOR m IN 0..1439 LOOP
      IF platform.meter_in_window(time '00:00' + m * interval '1 minute', time '22:00', time '06:00')
         AND NOT platform.meter_in_window(time '00:00' + m * interval '1 minute', p_curfew_start, p_curfew_end) THEN
        RETURN jsonb_build_object('outcome', 'curfew_must_cover_default');
      END IF;
    END LOOP;
  END IF;
  INSERT INTO platform.student_time_guard (student_id, daily_cap_minutes, curfew_start, curfew_end, updated_at, updated_by)
  VALUES (p_student, p_daily_cap, p_curfew_start, p_curfew_end, platform.meter_now(), platform.current_account_id())
  ON CONFLICT (student_id) DO UPDATE
    SET daily_cap_minutes = EXCLUDED.daily_cap_minutes, curfew_start = EXCLUDED.curfew_start,
        curfew_end = EXCLUDED.curfew_end, updated_at = EXCLUDED.updated_at, updated_by = EXCLUDED.updated_by;
  RETURN jsonb_build_object('outcome', 'saved', 'daily_cap', least(coalesce(p_daily_cap, v_plan_cap), v_plan_cap));
END $$;

-- ---------- 十一、选科：核心 + 选修槽（替换 0009 的 enroll_subject / pause_subject） ----------
-- 配置里有 core_subjects 时按新规则；否则沿用 max_active_subjects（ch8-hypothesis@1 与已有测试不变）。
-- 某一类（核心 / 选修）的上限；NULL = 不限（all_launched）
CREATE FUNCTION platform.meter_slot_limit(p_config text, p_tier platform.plan_tier, p_core boolean) RETURNS integer
  LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, platform, pg_temp
  AS $$ SELECT CASE WHEN f.value_code IN ('all_launched') THEN NULL
                    WHEN f.value_code = 'pick_one' THEN 1
                    ELSE f.value_code::integer END
          FROM platform.plan_feature f
         WHERE f.config_version = p_config AND f.tier = p_tier
           AND f.feature_code = CASE WHEN p_core THEN 'core_subjects' ELSE 'elective_slots' END $$;

CREATE FUNCTION platform.meter_uses_slots(p_config text, p_tier platform.plan_tier) RETURNS boolean
  LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, platform, pg_temp
  AS $$ SELECT EXISTS (SELECT 1 FROM platform.plan_feature f
                        WHERE f.config_version = p_config AND f.tier = p_tier AND f.feature_code = 'core_subjects') $$;

CREATE OR REPLACE FUNCTION platform.enroll_subject(p_student uuid, p_subject platform.subject) RETURNS jsonb
  LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, platform, pg_temp
  AS $$
DECLARE
  s platform.subscription;
  c platform.subject_catalog;
  v_value text;
  v_max integer;
  v_status text;
  v_active integer;
  v_frozen integer;
  v_limit integer;
BEGIN
  IF p_student IS NULL OR p_subject IS NULL OR NOT platform.can_read_student(p_student) THEN
    RAISE EXCEPTION 'not permitted' USING ERRCODE = 'insufficient_privilege';
  END IF;
  PERFORM pg_advisory_xact_lock(hashtextextended('platform.enroll_subject:' || p_student, 0));
  s := platform.meter_main_subscription(p_student);
  SELECT * INTO c FROM platform.subject_catalog WHERE product = p_subject;
  -- 上线门控（规格 §5）：planned 不可选，另须有已发布的课程版本；beta 只对试点开放
  IF c.code IS NULL OR c.status = 'planned'
     OR NOT EXISTS (SELECT 1 FROM platform.curriculum_version WHERE subject = p_subject AND status = 'published') THEN
    RETURN jsonb_build_object('outcome', 'not_launched');
  END IF;
  IF s.id IS NULL THEN
    RETURN jsonb_build_object('outcome', 'no_subscription');
  END IF;
  IF c.status = 'beta' AND s.source <> 'pilot_virtual' THEN
    RETURN jsonb_build_object('outcome', 'not_launched');
  END IF;
  SELECT status INTO v_status FROM platform.subject_enrollment WHERE student_id = p_student AND subject = p_subject;
  IF v_status = 'active' THEN
    RETURN jsonb_build_object('outcome', 'already_active');
  END IF;

  IF platform.meter_uses_slots(s.config_version, s.tier) THEN
    v_limit := platform.meter_slot_limit(s.config_version, s.tier, c.is_core);
    SELECT count(*) FILTER (WHERE e.status = 'active'),
           count(*) FILTER (WHERE e.status = 'paused' AND e.paused_at >= s.period_start AND e.subject <> p_subject)
      INTO v_active, v_frozen
      FROM platform.subject_enrollment e JOIN platform.subject_catalog k ON k.product = e.subject
     WHERE e.student_id = p_student AND k.is_core = c.is_core;
    IF v_limit IS NOT NULL AND v_active >= v_limit THEN
      RETURN jsonb_build_object('outcome', 'limit_reached', 'slot', CASE WHEN c.is_core THEN 'core' ELSE 'elective' END,
                                'limit', v_limit);
    END IF;
    -- 本周期换下的科目仍占着槽，换上的新科目下个周期才能开（规格 §5“下个周期生效”）
    IF v_limit IS NOT NULL AND v_active + v_frozen >= v_limit THEN
      RETURN jsonb_build_object('outcome', 'change_next_period', 'slot', CASE WHEN c.is_core THEN 'core' ELSE 'elective' END);
    END IF;
  ELSE
    SELECT f.value_code INTO v_value FROM platform.plan_feature f
     WHERE f.config_version = s.config_version AND f.tier = s.tier AND f.feature_code = 'max_active_subjects';
    v_max := platform.meter_subject_limit(v_value);
    SELECT count(*) INTO v_active FROM platform.subject_enrollment WHERE student_id = p_student AND status = 'active';
    IF v_active >= v_max THEN
      RETURN jsonb_build_object('outcome', 'limit_reached', 'max_active_subjects', v_max);
    END IF;
  END IF;

  IF v_status IS NULL THEN
    INSERT INTO platform.subject_enrollment (student_id, subject) VALUES (p_student, p_subject);
  ELSE
    UPDATE platform.subject_enrollment SET status = 'active', paused_at = NULL WHERE student_id = p_student AND subject = p_subject;
  END IF;
  IF platform.meter_uses_slots(s.config_version, s.tier) THEN
    RETURN jsonb_build_object('outcome', 'enrolled', 'slot', CASE WHEN c.is_core THEN 'core' ELSE 'elective' END, 'limit', v_limit);
  END IF;
  RETURN jsonb_build_object('outcome', 'enrolled', 'max_active_subjects', v_max);
END $$;

-- 暂停（换下）：新规则下每个周期只能换下 1 科（规格 §5“每个 30 天周期可更换选修科目 1 次”）
CREATE OR REPLACE FUNCTION platform.pause_subject(p_student uuid, p_subject platform.subject) RETURNS jsonb
  LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, platform, pg_temp
  AS $$
DECLARE s platform.subscription;
BEGIN
  IF p_student IS NULL OR p_subject IS NULL OR NOT platform.can_read_student(p_student) THEN
    RAISE EXCEPTION 'not permitted' USING ERRCODE = 'insufficient_privilege';
  END IF;
  PERFORM pg_advisory_xact_lock(hashtextextended('platform.enroll_subject:' || p_student, 0));
  s := platform.meter_main_subscription(p_student);
  IF s.id IS NOT NULL AND platform.meter_uses_slots(s.config_version, s.tier)
     AND EXISTS (SELECT 1 FROM platform.subject_enrollment e
                  WHERE e.student_id = p_student AND e.status = 'active' AND e.subject = p_subject)
     AND EXISTS (SELECT 1 FROM platform.subject_enrollment e
                  WHERE e.student_id = p_student AND e.status = 'paused' AND e.paused_at >= s.period_start) THEN
    RETURN jsonb_build_object('outcome', 'change_limit_reached');
  END IF;
  UPDATE platform.subject_enrollment SET status = 'paused', paused_at = platform.meter_now()
   WHERE student_id = p_student AND subject = p_subject AND status = 'active';
  RETURN jsonb_build_object('outcome', CASE WHEN FOUND THEN 'paused' ELSE 'not_active' END);
END $$;

-- 降级或换配置后按新上限保留最早开通的科目，其余暂停（系统暂停不记 paused_at，不算换科）
CREATE FUNCTION platform.meter_apply_subject_limits(p_student uuid, p_config text, p_tier platform.plan_tier) RETURNS void
  LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, platform, pg_temp
  AS $$
DECLARE
  v_limit integer;
  v_core boolean;
BEGIN
  PERFORM pg_advisory_xact_lock(hashtextextended('platform.enroll_subject:' || p_student, 0));
  IF platform.meter_uses_slots(p_config, p_tier) THEN
    FOREACH v_core IN ARRAY ARRAY[true, false] LOOP
      v_limit := platform.meter_slot_limit(p_config, p_tier, v_core);
      CONTINUE WHEN v_limit IS NULL;
      UPDATE platform.subject_enrollment SET status = 'paused', paused_at = NULL
       WHERE student_id = p_student AND status = 'active'
         AND subject IN (SELECT e.subject FROM platform.subject_enrollment e JOIN platform.subject_catalog k ON k.product = e.subject
                          WHERE e.student_id = p_student AND e.status = 'active' AND k.is_core = v_core
                          ORDER BY e.enrolled_at, e.subject OFFSET v_limit);
    END LOOP;
  ELSE
    SELECT platform.meter_subject_limit(f.value_code) INTO v_limit FROM platform.plan_feature f
     WHERE f.config_version = p_config AND f.tier = p_tier AND f.feature_code = 'max_active_subjects';
    UPDATE platform.subject_enrollment SET status = 'paused', paused_at = NULL
     WHERE student_id = p_student AND status = 'active'
       AND subject IN (SELECT subject FROM platform.subject_enrollment
                        WHERE student_id = p_student AND status = 'active'
                        ORDER BY enrolled_at, subject OFFSET coalesce(v_limit, 0));
  END IF;
END $$;

-- ---------- 十二、开通：试用 14 天、一次性（替换 0009） ----------
CREATE OR REPLACE FUNCTION platform.meter_open_subscription(p_student uuid, p_source text) RETURNS jsonb
  LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, platform, pg_temp
  AS $$
DECLARE
  v_now timestamptz := platform.meter_now();
  v_household uuid;
  v_config text := platform.meter_current_config();
  v_id uuid;
  v_end timestamptz;
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
  IF p_source = 'trial' THEN
    -- 一次性：同一家庭、或当前家长的任一已验证手机号，领过就不能再领（规格 §3、§8）
    PERFORM pg_advisory_xact_lock(hashtextextended('platform.trial_claim', 0));
    IF EXISTS (SELECT 1 FROM platform.trial_claim WHERE household_id = v_household)
       OR EXISTS (SELECT 1 FROM platform.account_contact ac
                   JOIN platform.trial_claim_phone p ON p.phone_digest = platform.meter_phone_digest(ac.phone_e164)
                  WHERE ac.account_id = platform.current_account_id()) THEN
      RETURN jsonb_build_object('outcome', 'trial_already_claimed');
    END IF;
  END IF;
  -- 试用 14 天，发放基本档完整的当期额度（用户决定 2026-09-28）；试点 30 天一期
  v_end := v_now + CASE WHEN p_source = 'trial' THEN interval '14 days' ELSE interval '30 days' END;
  BEGIN
    INSERT INTO platform.subscription (household_id, student_id, tier, config_version, source, period_start, period_end)
    VALUES (v_household, p_student, CASE WHEN p_source = 'trial' THEN 'basic' ELSE 'pro' END::platform.plan_tier,
            v_config, p_source, v_now, v_end)
    RETURNING id INTO v_id;
  EXCEPTION WHEN unique_violation THEN
    RETURN jsonb_build_object('outcome', 'already_active');
  END;
  IF p_source = 'trial' THEN
    INSERT INTO platform.trial_claim (household_id, student_id, claimed_at) VALUES (v_household, p_student, v_now);
    INSERT INTO platform.trial_claim_phone (phone_digest, household_id)
    SELECT DISTINCT platform.meter_phone_digest(ac.phone_e164), v_household
      FROM platform.account_contact ac WHERE ac.account_id = platform.current_account_id()
    ON CONFLICT (phone_digest) DO NOTHING;
  END IF;
  PERFORM platform.meter_grant_period(v_id);
  RETURN jsonb_build_object('outcome', 'opened', 'subscription_id', v_id, 'config_version', v_config, 'period_end', v_end);
END $$;

-- ---------- 十三、续期与到期（替换 0009） ----------
--   trial：到期结束，不续期，学生转留存态（规格 §3）；
--   pilot_virtual：同 0009（名单内续 30 天，否则结束）；
--   paid：到期转宽限期，宽限 7 天后结束（付费续期随支付接入；宽限期内的额度见 lessons 偏差）。
CREATE OR REPLACE FUNCTION platform.meter_renew_due(p_batch integer) RETURNS integer
  LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, platform, pg_temp
  AS $$
DECLARE
  v_now timestamptz := platform.meter_now();
  s platform.subscription;
  v_start timestamptz;
  v_count integer := 0;
BEGIN
  IF p_batch IS NULL OR p_batch < 1 OR p_batch > 1000 THEN
    RAISE EXCEPTION 'batch must be between 1 and 1000' USING ERRCODE = 'invalid_parameter_value';
  END IF;
  FOR s IN SELECT * FROM platform.subscription
            WHERE source <> 'addon'
              AND ((status = 'active' AND period_end <= v_now)
                   OR (status = 'grace' AND source = 'paid' AND period_end + interval '7 days' <= v_now)
                   OR (status = 'grace' AND source <> 'paid' AND period_end <= v_now))
            ORDER BY period_end, id LIMIT p_batch FOR UPDATE SKIP LOCKED LOOP
    IF s.source = 'trial' THEN
      UPDATE platform.subscription SET status = 'ended' WHERE id = s.id;
    ELSIF s.source = 'paid' THEN
      UPDATE platform.subscription SET status = CASE WHEN s.status = 'active' THEN 'grace' ELSE 'ended' END WHERE id = s.id;
    ELSIF NOT EXISTS (
         SELECT 1 FROM platform.pilot_household p
          WHERE p.household_id = s.household_id AND (p.ends_at IS NULL OR p.ends_at > v_now)) THEN
      UPDATE platform.subscription SET status = 'ended' WHERE id = s.id;
    ELSE
      v_start := s.period_end + floor(extract(epoch FROM v_now - s.period_end) / (30 * 86400)) * interval '30 days';
      UPDATE platform.subscription
         SET period_start = v_start, period_end = v_start + interval '30 days', status = 'active',
             tier = coalesce(s.next_tier, s.tier), config_version = coalesce(s.next_config_version, s.config_version),
             next_tier = NULL, next_config_version = NULL
       WHERE id = s.id;
      PERFORM platform.meter_grant_period(s.id);
      IF s.next_tier IS NOT NULL THEN
        PERFORM platform.meter_apply_subject_limits(s.student_id, s.next_config_version, s.next_tier);
      END IF;
    END IF;
    v_count := v_count + 1;
  END LOOP;
  RETURN v_count;
END $$;

-- ---------- 十四、升级补差（规格 §6：即时生效，当期额度补足差额，已用量保留） ----------
-- 只由支付回调调用（支付接入前不授权给任何应用角色）。重复调用同一档位返回 already_at_tier，不重复发放。
CREATE FUNCTION platform.meter_upgrade(p_subscription uuid, p_tier platform.plan_tier) RETURNS jsonb
  LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, platform, pg_temp
  AS $$
DECLARE
  v_now timestamptz := platform.meter_now();
  s platform.subscription;
  v_config text := platform.meter_current_config();
  a record;
  v_old numeric;
  e platform.entitlement;
  v_topup numeric;
BEGIN
  SELECT * INTO s FROM platform.subscription WHERE id = p_subscription FOR UPDATE;
  IF NOT FOUND OR s.source <> 'paid' OR s.status <> 'active' OR v_now < s.period_start OR v_now >= s.period_end THEN
    RETURN jsonb_build_object('outcome', 'not_upgradable');
  END IF;
  IF p_tier = s.tier THEN
    RETURN jsonb_build_object('outcome', 'already_at_tier');
  ELSIF p_tier < s.tier THEN
    RETURN jsonb_build_object('outcome', 'not_an_upgrade');
  END IF;
  IF v_config IS NULL THEN
    RETURN jsonb_build_object('outcome', 'no_published_config');
  END IF;
  PERFORM 1 FROM platform.entitlement WHERE subscription_id = s.id AND period_start = s.period_start ORDER BY id FOR UPDATE;
  FOR a IN SELECT resource_type, quantity FROM platform.plan_allowance
            WHERE config_version = v_config AND tier = p_tier ORDER BY resource_type LOOP
    SELECT coalesce((SELECT quantity FROM platform.plan_allowance
                      WHERE config_version = s.config_version AND tier = s.tier AND resource_type = a.resource_type), 0)
      INTO v_old;
    SELECT * INTO e FROM platform.entitlement
     WHERE subscription_id = s.id AND resource_type = a.resource_type AND period_start = s.period_start;
    IF e.id IS NULL THEN
      INSERT INTO platform.entitlement (subscription_id, student_id, resource_type, period_start, period_end, granted)
      VALUES (s.id, s.student_id, a.resource_type, s.period_start, s.period_end, 0) RETURNING * INTO e;
      v_topup := a.quantity;
    ELSE
      v_topup := greatest(a.quantity - v_old, 0);
    END IF;
    IF v_topup > 0 THEN
      INSERT INTO platform.usage_ledger (occurred_at, entitlement_id, student_id, kind, quantity, actor, reason_code)
      VALUES (v_now, e.id, s.student_id, 'grant', v_topup, 'system', 'upgrade_topup');
      UPDATE platform.entitlement SET granted = granted + v_topup WHERE id = e.id;
    END IF;
    PERFORM platform.meter_check_entitlement(e.id);
  END LOOP;
  UPDATE platform.subscription SET tier = p_tier, config_version = v_config, next_tier = NULL, next_config_version = NULL
   WHERE id = s.id;
  RETURN jsonb_build_object('outcome', 'upgraded', 'tier', p_tier, 'config_version', v_config);
END $$;

-- ---------- 十五、额度配置 v12-pilot@1（规格书 V1.2 §4；草稿，由人发布） ----------
-- 试用不单列（用户决定：试用 = 基本档）。分钟、次数、字节均为每个学生每 30 天；Max 的学时是公平使用上限。
-- diagnosis_screen 规格未单列（spec 待人确认 #4：初筛近似不计次，先给 30 次）；mistake_analysis 与上传同额度，BFF 迁到上传前继续可用。
INSERT INTO platform.plan_config (version, note_code) VALUES ('v12-pilot@1', 'user_system_spec_v1_2_matrix');
INSERT INTO platform.plan_allowance (config_version, tier, resource_type, quantity, fair_use)
SELECT 'v12-pilot@1', v.tier::platform.plan_tier, v.res, v.qty, v.fair
  FROM (VALUES
    ('basic', 'ai_active_time', 600, false),  ('plus', 'ai_active_time', 1200, false),
    ('pro', 'ai_active_time', 2400, false),   ('max', 'ai_active_time', 3960, true),
    ('basic', 'voice_realtime', 60, false),   ('plus', 'voice_realtime', 120, false),
    ('pro', 'voice_realtime', 300, false),    ('max', 'voice_realtime', 600, false),
    ('basic', 'lesson_whiteboard', 0, false), ('plus', 'lesson_whiteboard', 10, false),
    ('pro', 'lesson_whiteboard', 40, false),  ('max', 'lesson_whiteboard', 100, false),
    ('basic', 'diagnosis_full', 3, false),    ('plus', 'diagnosis_full', 5, false),
    ('pro', 'diagnosis_full', 10, false),     ('max', 'diagnosis_full', 20, false),
    ('basic', 'diagnosis_screen', 30, false), ('plus', 'diagnosis_screen', 30, false),
    ('pro', 'diagnosis_screen', 30, false),   ('max', 'diagnosis_screen', 30, false),
    ('basic', 'mock_exam', 1, false),         ('plus', 'mock_exam', 2, false),
    ('pro', 'mock_exam', 6, false),           ('max', 'mock_exam', 12, false),
    ('basic', 'upload', 60, false),           ('plus', 'upload', 150, false),
    ('pro', 'upload', 400, false),            ('max', 'upload', 800, false),
    ('basic', 'mistake_analysis', 60, false), ('plus', 'mistake_analysis', 150, false),
    ('pro', 'mistake_analysis', 400, false),  ('max', 'mistake_analysis', 800, false),
    ('basic', 'storage_bytes', 524288000, false),  ('plus', 'storage_bytes', 1610612736, false),
    ('pro', 'storage_bytes', 4294967296, false),   ('max', 'storage_bytes', 8589934592, false),
    ('basic', 'deep_report', 1, false),       ('plus', 'deep_report', 2, false),
    ('pro', 'deep_report', 4, false),         ('max', 'deep_report', 8, false)
  ) AS v(tier, res, qty, fair);
INSERT INTO platform.plan_feature (config_version, tier, feature_code, value_code)
SELECT 'v12-pilot@1', v.tier::platform.plan_tier, v.feature, v.val
  FROM (VALUES
    -- 英语搁置期间核心包可拆：基本档任选 1 科；Plus 及以上开放全部已上线核心科目（spec 待人确认 #1 默认）
    ('basic', 'core_subjects', 'pick_one'), ('plus', 'core_subjects', 'all_launched'),
    ('pro', 'core_subjects', 'all_launched'), ('max', 'core_subjects', 'all_launched'),
    ('basic', 'elective_slots', '0'), ('plus', 'elective_slots', '2'),
    ('pro', 'elective_slots', '4'), ('max', 'elective_slots', 'all_launched'),
    ('basic', 'daily_minutes_cap', '90'), ('plus', 'daily_minutes_cap', '120'),
    ('pro', 'daily_minutes_cap', '150'), ('max', 'daily_minutes_cap', '180'),
    ('basic', 'lesson_mode', 'static_steps'), ('plus', 'lesson_mode', 'whiteboard'),
    ('pro', 'lesson_mode', 'whiteboard'), ('max', 'lesson_mode', 'whiteboard'),
    ('basic', 'weekly_report', 'basic'), ('plus', 'weekly_report', 'full'),
    ('pro', 'weekly_report', 'full_and_monthly'), ('max', 'weekly_report', 'full_and_monthly'),
    ('basic', 'appeal_priority', 'standard'), ('plus', 'appeal_priority', 'standard'),
    ('pro', 'appeal_priority', 'standard'), ('max', 'appeal_priority', 'priority_human')
  ) AS v(tier, feature, val);

-- ---------- 十六、属主与授权 ----------
ALTER FUNCTION platform.meter_main_subscription(uuid) OWNER TO starbuddy_authz;
ALTER FUNCTION platform.meter_state_of(uuid) OWNER TO starbuddy_authz;
ALTER FUNCTION platform.meter_student_state(uuid) OWNER TO starbuddy_authz;
ALTER FUNCTION platform.meter_plan_daily_cap(uuid) OWNER TO starbuddy_authz;
ALTER FUNCTION platform.meter_daily_cap(uuid) OWNER TO starbuddy_authz;
ALTER FUNCTION platform.meter_in_curfew(uuid, timestamptz) OWNER TO starbuddy_authz;
ALTER FUNCTION platform.meter_minutes_today(uuid, timestamptz) OWNER TO starbuddy_authz;
ALTER FUNCTION platform.meter_reserve_impl(uuid, text, text, text, numeric, text, text, boolean) OWNER TO starbuddy_authz;
ALTER FUNCTION platform.meter_reserve_upload(uuid, text, text, text, integer) OWNER TO starbuddy_authz;
ALTER FUNCTION platform.meter_practice(uuid, text) OWNER TO starbuddy_authz;
ALTER FUNCTION platform.set_time_guard(uuid, integer, time, time) OWNER TO starbuddy_authz;
ALTER FUNCTION platform.meter_slot_limit(text, platform.plan_tier, boolean) OWNER TO starbuddy_authz;
ALTER FUNCTION platform.meter_uses_slots(text, platform.plan_tier) OWNER TO starbuddy_authz;
ALTER FUNCTION platform.meter_apply_subject_limits(uuid, text, platform.plan_tier) OWNER TO starbuddy_authz;
ALTER FUNCTION platform.meter_upgrade(uuid, platform.plan_tier) OWNER TO starbuddy_authz;

REVOKE EXECUTE ON FUNCTION platform.meter_phone_digest(text), platform.reject_practice_log_change(),
  platform.meter_main_subscription(uuid), platform.meter_state_of(uuid), platform.meter_student_state(uuid),
  platform.meter_plan_daily_cap(uuid), platform.meter_daily_cap(uuid), platform.meter_in_window(time, time, time),
  platform.meter_in_curfew(uuid, timestamptz), platform.meter_minutes_today(uuid, timestamptz),
  platform.meter_reserve_impl(uuid, text, text, text, numeric, text, text, boolean),
  platform.meter_reserve_upload(uuid, text, text, text, integer), platform.meter_practice(uuid, text),
  platform.set_time_guard(uuid, integer, time, time), platform.meter_slot_limit(text, platform.plan_tier, boolean),
  platform.meter_uses_slots(text, platform.plan_tier), platform.meter_apply_subject_limits(uuid, text, platform.plan_tier),
  platform.meter_upgrade(uuid, platform.plan_tier) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION platform.meter_phone_digest(text), platform.meter_in_window(time, time, time) TO starbuddy_authz;
GRANT EXECUTE ON FUNCTION platform.meter_reserve_upload(uuid, text, text, text, integer), platform.meter_practice(uuid, text),
  platform.set_time_guard(uuid, integer, time, time), platform.meter_student_state(uuid) TO starbuddy_bff;

GRANT SELECT ON platform.subject_catalog, platform.meter_secret, platform.trial_claim, platform.trial_claim_phone,
  platform.student_time_guard, platform.practice_log TO starbuddy_authz;
GRANT INSERT ON platform.trial_claim, platform.trial_claim_phone, platform.student_time_guard, platform.practice_log TO starbuddy_authz;
GRANT UPDATE (daily_cap_minutes, curfew_start, curfew_end, updated_at, updated_by) ON platform.student_time_guard TO starbuddy_authz;
GRANT UPDATE (paused_at) ON platform.subject_enrollment TO starbuddy_authz;

ALTER TABLE platform.subject_catalog ENABLE ROW LEVEL SECURITY;
ALTER TABLE platform.subject_catalog FORCE ROW LEVEL SECURITY;
CREATE POLICY subject_catalog_read ON platform.subject_catalog FOR SELECT TO starbuddy_bff USING (true);
GRANT SELECT ON platform.subject_catalog, platform.student_time_guard, platform.practice_log TO starbuddy_bff;
