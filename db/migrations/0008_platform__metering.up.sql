-- 0008 platform · 计量账本与权益（specs/metering-schema-sprint1/spec.md；设计文档 docs/design/metering-schema.md）
-- 依据：ADR 06（Postgres 单库事务 + 追加式流水）、ADR 05（按月分区）、完整方案 5.8 与第八章。
--
-- 本迁移只建表、约束、只追加保护、视图、RLS 与额度配置种子；预留 / 结算 / 释放 / 过期的函数在冲刺 2-E。
-- 写入边界：BFF 只能读（经 RLS）；写入一律经 SECURITY DEFINER 函数（2-E，属主 starbuddy_authz）；
-- 学习服务与复核角色对计量表没有任何权限（ADR 01：计量在 BFF 侧）。
-- 不存价格：第八章“价格不在核算单位经济性与监管路径前给出”。

CREATE TYPE platform.plan_tier AS ENUM ('basic', 'plus', 'pro', 'max');
CREATE TYPE platform.reservation_state AS ENUM ('reserved', 'settled', 'released', 'expired');
CREATE TYPE platform.ledger_kind AS ENUM ('grant', 'reserve', 'settle', 'release', 'expire', 'adjust', 'correction');

-- ---------- 资源类型（完整方案 5.8 的计量动作） ----------
CREATE TABLE platform.resource_type (
  code                  text PRIMARY KEY CHECK (code ~ '^[a-z][a-z0-9_]{1,47}$'),
  unit                  text NOT NULL CHECK (unit IN ('count', 'minute')),
  rounding              text NOT NULL CHECK (rounding IN ('none', 'ceil')),
  -- 同一内容在这个时间窗口内再次发起不重复扣（5.8“同题 24 小时内复看不重复扣次”）；NULL 表示不去重
  dedup_window          interval CHECK (dedup_window IS NULL OR dedup_window > interval '0'),
  pool                  text NOT NULL CHECK (pool IN ('general', 'voice')),
  reservation_ttl       interval NOT NULL CHECK (reservation_ttl > interval '0'),
  max_per_reservation   numeric NOT NULL CHECK (max_per_reservation > 0),
  CONSTRAINT resource_minute_rounds_up CHECK ((unit = 'minute') = (rounding = 'ceil'))
);
COMMENT ON TABLE platform.resource_type IS '计量资源类型。过期时间、单次上限为草案值，待产品确认（spec 待人确认 #4）';

-- ---------- 四级额度配置（第八章；带版本，已发布只读） ----------
CREATE TABLE platform.plan_config (
  version        platform.versioned_name PRIMARY KEY,
  status         text NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'published', 'retired')),
  note_code      text NOT NULL CHECK (note_code ~ '^[a-z0-9_]{1,48}$'),
  published_at   timestamptz,
  retired_at     timestamptz,
  CHECK ((status = 'draft') = (published_at IS NULL)),
  CHECK ((status = 'retired') = (retired_at IS NOT NULL))
);

CREATE TABLE platform.plan_allowance (
  config_version   platform.versioned_name NOT NULL REFERENCES platform.plan_config (version),
  tier             platform.plan_tier NOT NULL,
  resource_type    text NOT NULL REFERENCES platform.resource_type (code),
  -- 每个学生席位每 30 天的额度，单位同 resource_type.unit
  quantity         numeric NOT NULL CHECK (quantity >= 0),
  -- 公平使用上限（第八章脚注 2：MAX 的上限不是无限）
  fair_use         boolean NOT NULL DEFAULT false,
  PRIMARY KEY (config_version, tier, resource_type)
);

CREATE TABLE platform.plan_feature (
  config_version   platform.versioned_name NOT NULL REFERENCES platform.plan_config (version),
  tier             platform.plan_tier NOT NULL,
  feature_code     text NOT NULL CHECK (feature_code IN ('max_active_subjects', 'lesson_mode', 'weekly_report', 'appeal_priority')),
  value_code       text NOT NULL CHECK (value_code ~ '^[a-z0-9_]{1,48}$'),
  PRIMARY KEY (config_version, tier, feature_code)
);

-- 已发布或已停用的配置只读；已发布只能改成已停用。额度与权益行只能挂在草稿配置上改动。
CREATE FUNCTION platform.guard_plan_config() RETURNS trigger
  LANGUAGE plpgsql SET search_path = pg_catalog, platform, pg_temp
  AS $$
BEGIN
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
  RETURN NEW;
END $$;
CREATE TRIGGER guard_plan_config BEFORE UPDATE OR DELETE ON platform.plan_config
  FOR EACH ROW EXECUTE FUNCTION platform.guard_plan_config();

CREATE FUNCTION platform.guard_plan_rows() RETURNS trigger
  LANGUAGE plpgsql SET search_path = pg_catalog, platform, pg_temp
  AS $$
DECLARE v_status text;
BEGIN
  SELECT status INTO v_status FROM platform.plan_config
   WHERE version = CASE WHEN TG_OP = 'INSERT' THEN NEW.config_version ELSE OLD.config_version END;
  IF v_status IS DISTINCT FROM 'draft' THEN
    RAISE EXCEPTION 'rows of a % plan_config cannot be changed', coalesce(v_status, 'missing') USING ERRCODE = 'check_violation';
  END IF;
  IF TG_OP = 'UPDATE' AND NEW.config_version <> OLD.config_version THEN
    RAISE EXCEPTION 'config_version cannot be changed' USING ERRCODE = 'check_violation';
  END IF;
  RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
END $$;
CREATE TRIGGER guard_plan_allowance BEFORE INSERT OR UPDATE OR DELETE ON platform.plan_allowance
  FOR EACH ROW EXECUTE FUNCTION platform.guard_plan_rows();
CREATE TRIGGER guard_plan_feature BEFORE INSERT OR UPDATE OR DELETE ON platform.plan_feature
  FOR EACH ROW EXECUTE FUNCTION platform.guard_plan_rows();

-- ---------- 订阅（挂在家庭，按学生席位；家长是支付主体，第十一章） ----------
CREATE TABLE platform.subscription (
  id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  household_id     uuid NOT NULL REFERENCES platform.household (id),
  student_id       uuid NOT NULL REFERENCES platform.student_profile (id),
  tier             platform.plan_tier NOT NULL,
  config_version   platform.versioned_name NOT NULL REFERENCES platform.plan_config (version),
  -- trial：注册后的基础会员；pilot_virtual：阶段 1 试点虚拟额度（不收费，全量走账本）；paid：支付接入后；addon：附加权益（预留）
  source           text NOT NULL CHECK (source IN ('trial', 'pilot_virtual', 'paid', 'addon')),
  status           text NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'grace', 'ended')),
  period_start     timestamptz NOT NULL,
  period_end       timestamptz NOT NULL,
  created_at       timestamptz NOT NULL DEFAULT now(),
  CHECK (period_end > period_start),
  UNIQUE (id, student_id)
);
-- 一个学生席位同时只有一个有效订阅（附加权益除外）
CREATE UNIQUE INDEX subscription_one_active_per_student ON platform.subscription (student_id)
  WHERE status IN ('active', 'grace') AND source <> 'addon';

-- 学生必须属于该家庭；只能引用已发布的额度配置
CREATE FUNCTION platform.guard_subscription() RETURNS trigger
  LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, platform, pg_temp
  AS $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM platform.student_profile s WHERE s.id = NEW.student_id AND s.household_id = NEW.household_id) THEN
    RAISE EXCEPTION 'student does not belong to the household' USING ERRCODE = 'check_violation';
  END IF;
  -- 新建或改配置版本（升级、降级生效）时都只能指向已发布配置；配置停用不影响已有订阅的其他更新
  IF (TG_OP = 'INSERT' OR NEW.config_version IS DISTINCT FROM OLD.config_version)
     AND NOT EXISTS (SELECT 1 FROM platform.plan_config c WHERE c.version = NEW.config_version AND c.status = 'published') THEN
    RAISE EXCEPTION 'subscription must reference a published plan_config' USING ERRCODE = 'check_violation';
  END IF;
  IF TG_OP = 'UPDATE' AND (NEW.household_id <> OLD.household_id OR NEW.student_id <> OLD.student_id) THEN
    RAISE EXCEPTION 'subscription owner cannot be changed' USING ERRCODE = 'check_violation';
  END IF;
  RETURN NEW;
END $$;
ALTER FUNCTION platform.guard_subscription() OWNER TO starbuddy_authz;
REVOKE EXECUTE ON FUNCTION platform.guard_subscription() FROM PUBLIC;
GRANT SELECT ON platform.student_profile, platform.plan_config TO starbuddy_authz;
CREATE TRIGGER guard_subscription BEFORE INSERT OR UPDATE ON platform.subscription
  FOR EACH ROW EXECUTE FUNCTION platform.guard_subscription();

-- ---------- 额度桶（学生席位 × 资源类型 × 30 天周期） ----------
-- 计数供 ADR 06 的行锁与快速余额使用；真相源是流水与预留表，对账视图断言两者一致。
CREATE TABLE platform.entitlement (
  id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  subscription_id    uuid NOT NULL,
  student_id         uuid NOT NULL,
  resource_type      text NOT NULL REFERENCES platform.resource_type (code),
  period_start       timestamptz NOT NULL,
  period_end         timestamptz NOT NULL,
  granted            numeric NOT NULL DEFAULT 0 CHECK (granted >= 0),
  reserved           numeric NOT NULL DEFAULT 0 CHECK (reserved >= 0),
  settled            numeric NOT NULL DEFAULT 0 CHECK (settled >= 0),
  fair_use_margin    numeric NOT NULL DEFAULT 0 CHECK (fair_use_margin >= 0),
  CHECK (period_end > period_start),
  CONSTRAINT entitlement_not_overdrawn CHECK (reserved + settled <= granted + fair_use_margin),
  FOREIGN KEY (subscription_id, student_id) REFERENCES platform.subscription (id, student_id),
  UNIQUE (subscription_id, resource_type, period_start),
  UNIQUE (id, student_id),
  UNIQUE (id, student_id, resource_type)
);
CREATE INDEX entitlement_student_idx ON platform.entitlement (student_id, resource_type, period_end);

-- ---------- 预留与幂等（不分区；ADR 06 第 2 条的 usage_idempotency） ----------
CREATE TABLE platform.usage_reservation (
  usage_id              uuid PRIMARY KEY,
  -- 全局唯一：跨月重试也能挡住。与学习服务写入共用同一个 key（spec 待人确认 #5）
  idempotency_key       text NOT NULL UNIQUE CHECK (length(idempotency_key) BETWEEN 16 AND 128),
  request_fingerprint   text NOT NULL CHECK (request_fingerprint ~ '^[0-9a-f]{64}$'),
  entitlement_id        uuid NOT NULL,
  student_id            uuid NOT NULL,
  resource_type         text NOT NULL,
  state                 platform.reservation_state NOT NULL DEFAULT 'reserved',
  reserved_qty          numeric NOT NULL CHECK (reserved_qty >= 0),
  settled_qty           numeric CHECK (settled_qty >= 0),
  -- 24 小时复看：各科适配器提供的内容指纹，dedup_key = (家庭, 资源类型, 指纹) 的摘要
  content_fingerprint   text CHECK (content_fingerprint ~ '^[A-Za-z0-9:_\-]{8,128}$'),
  dedup_key             text CHECK (dedup_key ~ '^[0-9a-f]{64}$'),
  dedup_of              uuid REFERENCES platform.usage_reservation (usage_id),
  source_method         text CHECK (source_method IN ('diagnose', 'plan', 'teach', 'grade', 'generate_variation', 'report')),
  reserved_at           timestamptz NOT NULL DEFAULT now(),
  expires_at            timestamptz NOT NULL,
  finished_at           timestamptz,
  FOREIGN KEY (entitlement_id, student_id, resource_type) REFERENCES platform.entitlement (id, student_id, resource_type),
  UNIQUE (usage_id, entitlement_id),
  CHECK (expires_at > reserved_at),
  CONSTRAINT reservation_finished_iff_terminal CHECK ((state = 'reserved') = (finished_at IS NULL)),
  CONSTRAINT reservation_settled_has_qty CHECK ((state = 'settled') = (settled_qty IS NOT NULL)),
  CONSTRAINT reservation_settle_within_reserved CHECK (settled_qty IS NULL OR settled_qty <= reserved_qty),
  CONSTRAINT reservation_dedup_needs_key CHECK (dedup_of IS NULL OR dedup_key IS NOT NULL)
);
CREATE INDEX usage_reservation_dedup_idx ON platform.usage_reservation (dedup_key, finished_at) WHERE state = 'settled';
CREATE INDEX usage_reservation_expiry_idx ON platform.usage_reservation (expires_at) WHERE state = 'reserved';
CREATE INDEX usage_reservation_student_idx ON platform.usage_reservation (student_id, reserved_at);

-- 状态机：reserved → settled | released | expired；终态不能再改。归属、数量、指纹等一旦写入不能改。
CREATE FUNCTION platform.guard_usage_reservation() RETURNS trigger
  LANGUAGE plpgsql SET search_path = pg_catalog, platform, pg_temp
  AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN
    RAISE EXCEPTION 'usage_reservation is append-only' USING ERRCODE = 'insufficient_privilege';
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
CREATE TRIGGER guard_usage_reservation BEFORE UPDATE OR DELETE ON platform.usage_reservation
  FOR EACH ROW EXECUTE FUNCTION platform.guard_usage_reservation();

-- ---------- 流水（只追加，按月分区；ADR 05、06） ----------
CREATE TABLE platform.usage_ledger (
  entry_id                  uuid NOT NULL DEFAULT gen_random_uuid(),
  occurred_at               timestamptz NOT NULL DEFAULT now(),
  entitlement_id            uuid NOT NULL,
  student_id                uuid NOT NULL,
  usage_id                  uuid,
  kind                      platform.ledger_kind NOT NULL,
  -- grant / reserve / settle / release / expire 为非负；adjust、correction 可以为负（反向流水）
  quantity                  numeric NOT NULL,
  -- 更正只能追加反向流水，并指出更正的是哪一条（ADR 06 第 1 条）
  corrects_entry_id         uuid,
  corrects_occurred_at      timestamptz,
  corrects_kind             platform.ledger_kind,
  actor                     text NOT NULL CHECK (actor IN ('bff', 'system', 'staff')),
  reason_code               text NOT NULL CHECK (reason_code ~ '^[a-z0-9_]{1,48}$'),
  -- 业务来源：家长查“这次扣了什么”时回溯到具体的判分或诊断（学习服务写入请求的 method + key）
  source_method             text CHECK (source_method IN ('diagnose', 'plan', 'teach', 'grade', 'generate_variation', 'report')),
  source_idempotency_key    text CHECK (length(source_idempotency_key) BETWEEN 16 AND 128),
  PRIMARY KEY (entry_id, occurred_at),
  FOREIGN KEY (entitlement_id, student_id) REFERENCES platform.entitlement (id, student_id),
  FOREIGN KEY (usage_id, entitlement_id) REFERENCES platform.usage_reservation (usage_id, entitlement_id),
  CONSTRAINT ledger_nonnegative CHECK (kind IN ('adjust', 'correction') OR quantity >= 0),
  CONSTRAINT ledger_usage_required CHECK ((kind IN ('reserve', 'settle', 'release', 'expire')) = (usage_id IS NOT NULL)
                                          OR (kind = 'correction')),
  CONSTRAINT ledger_correction_target CHECK ((kind = 'correction') =
                                             (corrects_entry_id IS NOT NULL AND corrects_occurred_at IS NOT NULL AND corrects_kind IS NOT NULL)),
  CONSTRAINT ledger_correction_not_of_correction CHECK (corrects_kind IS DISTINCT FROM 'correction')
) PARTITION BY RANGE (occurred_at);
CREATE INDEX usage_ledger_entitlement_idx ON platform.usage_ledger (entitlement_id, occurred_at);
CREATE INDEX usage_ledger_student_idx ON platform.usage_ledger (student_id, occurred_at);
CREATE INDEX usage_ledger_usage_idx ON platform.usage_ledger (usage_id);
COMMENT ON TABLE platform.usage_ledger IS '计量流水：只追加；更正追加反向流水。保留期待合规意见（spec 待人确认 #8）';

CREATE FUNCTION platform.reject_ledger_change() RETURNS trigger
  LANGUAGE plpgsql SET search_path = pg_catalog, platform, pg_temp
  AS $$
BEGIN
  RAISE EXCEPTION 'usage_ledger is append-only; add a correction entry instead' USING ERRCODE = 'insufficient_privilege';
END $$;
CREATE TRIGGER reject_ledger_change BEFORE UPDATE OR DELETE ON platform.usage_ledger
  FOR EACH ROW EXECUTE FUNCTION platform.reject_ledger_change();

SET LOCAL TIME ZONE 'UTC';
DO $$ BEGIN PERFORM platform.ensure_monthly_partitions('platform.usage_ledger', DATE '2026-09-01', 16); END $$;

-- ---------- 视图 ----------
-- 对账：计数必须等于流水与预留表的聚合（spec 设计要点 4）。正常情况下返回零行；2-E 的函数每次写入后都应保持零行。
CREATE VIEW platform.entitlement_reconciliation WITH (security_invoker = true) AS
WITH ledger AS (
  SELECT entitlement_id,
         sum(quantity) FILTER (WHERE coalesce(corrects_kind, kind) IN ('grant', 'adjust')) AS granted,
         sum(quantity) FILTER (WHERE coalesce(corrects_kind, kind) = 'settle') AS settled
    FROM platform.usage_ledger GROUP BY entitlement_id
), open_res AS (
  SELECT entitlement_id, sum(reserved_qty) AS reserved
    FROM platform.usage_reservation WHERE state = 'reserved' GROUP BY entitlement_id
)
SELECT e.id AS entitlement_id, e.student_id,
       e.granted, coalesce(l.granted, 0) AS ledger_granted,
       e.reserved, coalesce(r.reserved, 0) AS open_reserved,
       e.settled, coalesce(l.settled, 0) AS ledger_settled
  FROM platform.entitlement e
  LEFT JOIN ledger l ON l.entitlement_id = e.id
  LEFT JOIN open_res r ON r.entitlement_id = e.id
 WHERE e.granted <> coalesce(l.granted, 0) OR e.reserved <> coalesce(r.reserved, 0) OR e.settled <> coalesce(l.settled, 0);

-- 每个终态预留都必须恰好有一条对应的终态流水，每个预留都必须恰好有一条 reserve 流水
CREATE VIEW platform.reservation_ledger_mismatch WITH (security_invoker = true) AS
SELECT r.usage_id, r.state,
       count(*) FILTER (WHERE l.kind = 'reserve') AS reserve_entries,
       count(*) FILTER (WHERE l.kind IN ('settle', 'release', 'expire')) AS terminal_entries
  FROM platform.usage_reservation r
  LEFT JOIN platform.usage_ledger l ON l.usage_id = r.usage_id
 GROUP BY r.usage_id, r.state
HAVING count(*) FILTER (WHERE l.kind = 'reserve') <> 1
    OR count(*) FILTER (WHERE l.kind IN ('settle', 'release', 'expire')) <> CASE WHEN r.state = 'reserved' THEN 0 ELSE 1 END;

-- 余额（完整方案 5.9“额度可见”）：security_invoker，调用者的 RLS 生效；家长看本家庭，学生看自己
CREATE VIEW platform.entitlement_balance WITH (security_invoker = true) AS
SELECT e.student_id, e.resource_type, rt.unit, e.period_start, e.period_end,
       e.granted, e.reserved, e.settled,
       greatest(e.granted - e.reserved - e.settled, 0) AS remaining
  FROM platform.entitlement e
  JOIN platform.resource_type rt ON rt.code = e.resource_type
  JOIN platform.subscription s ON s.id = e.subscription_id
 WHERE s.status IN ('active', 'grace') AND now() >= e.period_start AND now() < e.period_end;

-- ---------- 权限与 RLS ----------
-- 配置是公开信息（展示档位与额度）；账本类只读，经 RLS 按学生授权。任何角色都没有直接写权限（2-E 经函数写入）。
GRANT SELECT ON platform.resource_type, platform.plan_config, platform.plan_allowance, platform.plan_feature TO starbuddy_bff;
GRANT SELECT ON platform.subscription, platform.entitlement, platform.usage_reservation, platform.usage_ledger,
  platform.entitlement_balance TO starbuddy_bff;

ALTER TABLE platform.resource_type ENABLE ROW LEVEL SECURITY;
ALTER TABLE platform.resource_type FORCE ROW LEVEL SECURITY;
CREATE POLICY resource_type_read ON platform.resource_type FOR SELECT TO starbuddy_bff USING (true);

ALTER TABLE platform.plan_config ENABLE ROW LEVEL SECURITY;
ALTER TABLE platform.plan_config FORCE ROW LEVEL SECURITY;
CREATE POLICY plan_config_read ON platform.plan_config FOR SELECT TO starbuddy_bff USING (status <> 'draft');
CREATE POLICY plan_config_authz ON platform.plan_config FOR SELECT TO starbuddy_authz USING (true);

ALTER TABLE platform.plan_allowance ENABLE ROW LEVEL SECURITY;
ALTER TABLE platform.plan_allowance FORCE ROW LEVEL SECURITY;
CREATE POLICY plan_allowance_read ON platform.plan_allowance FOR SELECT TO starbuddy_bff
  USING (EXISTS (SELECT 1 FROM platform.plan_config c WHERE c.version = config_version AND c.status <> 'draft'));

ALTER TABLE platform.plan_feature ENABLE ROW LEVEL SECURITY;
ALTER TABLE platform.plan_feature FORCE ROW LEVEL SECURITY;
CREATE POLICY plan_feature_read ON platform.plan_feature FOR SELECT TO starbuddy_bff
  USING (EXISTS (SELECT 1 FROM platform.plan_config c WHERE c.version = config_version AND c.status <> 'draft'));

ALTER TABLE platform.subscription ENABLE ROW LEVEL SECURITY;
ALTER TABLE platform.subscription FORCE ROW LEVEL SECURITY;
CREATE POLICY subscription_read ON platform.subscription FOR SELECT TO starbuddy_bff USING (platform.can_read_student(student_id));

ALTER TABLE platform.entitlement ENABLE ROW LEVEL SECURITY;
ALTER TABLE platform.entitlement FORCE ROW LEVEL SECURITY;
CREATE POLICY entitlement_read ON platform.entitlement FOR SELECT TO starbuddy_bff USING (platform.can_read_student(student_id));

ALTER TABLE platform.usage_reservation ENABLE ROW LEVEL SECURITY;
ALTER TABLE platform.usage_reservation FORCE ROW LEVEL SECURITY;
CREATE POLICY usage_reservation_read ON platform.usage_reservation FOR SELECT TO starbuddy_bff
  USING (platform.can_read_student(student_id));

ALTER TABLE platform.usage_ledger ENABLE ROW LEVEL SECURITY;
ALTER TABLE platform.usage_ledger FORCE ROW LEVEL SECURITY;
CREATE POLICY usage_ledger_read ON platform.usage_ledger FOR SELECT TO starbuddy_bff USING (platform.can_read_student(student_id));

-- ---------- 种子：资源类型与第八章四级额度（草稿版本 ch8-hypothesis@1） ----------
-- 数值照抄完整方案第八章表格（“首版供验证的配额假设”）。过期时间与单次上限为草案值，待产品确认。
INSERT INTO platform.resource_type (code, unit, rounding, dedup_window, pool, reservation_ttl, max_per_reservation) VALUES
  ('diagnosis_screen',  'count',  'none', NULL,               'general', interval '10 minutes',  1),
  ('diagnosis_full',    'count',  'none', NULL,               'general', interval '10 minutes',  1),
  ('mock_exam',         'count',  'none', NULL,               'general', interval '150 minutes', 1),
  ('mistake_analysis',  'count',  'none', interval '24 hours', 'general', interval '10 minutes',  20),
  ('lesson_whiteboard', 'count',  'none', NULL,               'general', interval '90 minutes',  1),
  ('voice_realtime',    'minute', 'ceil', NULL,               'voice',   interval '30 minutes',  30),
  ('ai_active_time',    'minute', 'ceil', NULL,               'general', interval '30 minutes',  30);

INSERT INTO platform.plan_config (version, note_code) VALUES ('ch8-hypothesis@1', 'full_plan_v1_ch8_quota_hypothesis');

INSERT INTO platform.plan_allowance (config_version, tier, resource_type, quantity, fair_use)
SELECT 'ch8-hypothesis@1', v.tier::platform.plan_tier, v.res, v.qty, v.fair
  FROM (VALUES
    -- 普通会员：“1 次筛查 + 1 次完整”；讲评课为“文字与静态步骤”（额度 0，见 lesson_mode）；语音“试听 10 分钟”
    ('basic', 'diagnosis_screen', 1, false), ('basic', 'diagnosis_full', 1, false), ('basic', 'mock_exam', 0, false),
    ('basic', 'mistake_analysis', 20, false), ('basic', 'lesson_whiteboard', 0, false), ('basic', 'voice_realtime', 10, false),
    ('basic', 'ai_active_time', 120, false),
    ('plus', 'diagnosis_screen', 0, false), ('plus', 'diagnosis_full', 2, false), ('plus', 'mock_exam', 1, false),
    ('plus', 'mistake_analysis', 100, false), ('plus', 'lesson_whiteboard', 10, false), ('plus', 'voice_realtime', 60, false),
    ('plus', 'ai_active_time', 600, false),
    ('pro', 'diagnosis_screen', 0, false), ('pro', 'diagnosis_full', 6, false), ('pro', 'mock_exam', 4, false),
    ('pro', 'mistake_analysis', 300, false), ('pro', 'lesson_whiteboard', 40, false), ('pro', 'voice_realtime', 240, false),
    ('pro', 'ai_active_time', 1800, false),
    -- MAX：AI 互动有效时长 4,000 分钟是公平使用上限（第八章脚注 2）
    ('max', 'diagnosis_screen', 0, false), ('max', 'diagnosis_full', 12, false), ('max', 'mock_exam', 10, false),
    ('max', 'mistake_analysis', 800, false), ('max', 'lesson_whiteboard', 100, false), ('max', 'voice_realtime', 600, false),
    ('max', 'ai_active_time', 4000, true)
  ) AS v(tier, res, qty, fair);

INSERT INTO platform.plan_feature (config_version, tier, feature_code, value_code)
SELECT 'ch8-hypothesis@1', v.tier::platform.plan_tier, v.feature, v.val
  FROM (VALUES
    ('basic', 'max_active_subjects', '1'), ('plus', 'max_active_subjects', '2'),
    ('pro', 'max_active_subjects', '4'), ('max', 'max_active_subjects', 'all_launched'),
    ('basic', 'lesson_mode', 'static_steps'), ('plus', 'lesson_mode', 'whiteboard'),
    ('pro', 'lesson_mode', 'whiteboard'), ('max', 'lesson_mode', 'whiteboard'),
    ('basic', 'weekly_report', 'basic_and_public_courses'), ('plus', 'weekly_report', 'full'),
    ('pro', 'weekly_report', 'cross_subject_and_monthly'), ('max', 'weekly_report', 'cross_subject_deep'),
    ('basic', 'appeal_priority', 'standard'), ('plus', 'appeal_priority', 'standard'),
    ('pro', 'appeal_priority', 'standard'), ('max', 'appeal_priority', 'priority_human')
  ) AS v(tier, feature, val);
