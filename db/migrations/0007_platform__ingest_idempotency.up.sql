-- 0007 platform · 学习服务写入的幂等（specs/learning-ingest-sprint1/spec.md 范围第 5 条、待人确认 #3）
-- 两张表都不分区：分区表上的唯一约束只在分区内有效（ADR 05 第 2 条），跨月重试会漏；
-- 与 ADR 06 给计量账本单独建 usage_idempotency 的思路一致。
--   ingest_request     请求级幂等：(method, idempotency_key) 全局唯一，记录请求体指纹、租约与结果摘要
--   evidence_registry  证据 ID 全局唯一（schema spec 待人确认 #12）：同 ID 再来一次，内容相同跳过，不同拒绝
-- 两张表都不存学生作答原文：只有指纹、摘要里的 ID 与编码（规则 8、ADR 09）。

CREATE TABLE platform.ingest_request (
  method                text NOT NULL CHECK (method IN ('grade', 'diagnose')),
  idempotency_key       text NOT NULL CHECK (length(idempotency_key) BETWEEN 16 AND 128),
  subject               platform.subject NOT NULL,
  student_id            uuid NOT NULL REFERENCES platform.student_profile (id),
  request_fingerprint   text NOT NULL CHECK (request_fingerprint ~ '^[0-9a-f]{64}$'),
  -- 平台生成的 attempt_id，重试时沿用（阿鸦 lessons #7）
  attempt_ids           uuid[] NOT NULL CHECK (cardinality(attempt_ids) BETWEEN 1 AND 200),
  status                text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'succeeded', 'failed')),
  lease_token           uuid,
  lease_until           timestamptz,
  summary               jsonb CHECK (summary IS NULL OR jsonb_typeof(summary) = 'object'),
  error_code            text CHECK (error_code ~ '^[A-Z_]{1,48}$'),
  created_at            timestamptz NOT NULL DEFAULT now(),
  updated_at            timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (method, idempotency_key),
  CONSTRAINT ingest_succeeded_has_summary CHECK ((status = 'succeeded') = (summary IS NOT NULL)),
  CONSTRAINT ingest_pending_has_lease CHECK ((status = 'pending') = (lease_token IS NOT NULL AND lease_until IS NOT NULL)),
  CONSTRAINT ingest_failed_has_code CHECK ((status = 'failed') = (error_code IS NOT NULL))
);
CREATE INDEX ingest_request_student_idx ON platform.ingest_request (student_id, created_at);
COMMENT ON TABLE platform.ingest_request IS '学习服务写入的请求级幂等；只有 starbuddy_learning 能读写';

-- 已成功的请求是终态：结果摘要、指纹、学生、attempt_id 都不能再改（重试必须拿到同一结果）
CREATE FUNCTION platform.guard_ingest_request() RETURNS trigger
  LANGUAGE plpgsql SET search_path = pg_catalog, platform, pg_temp
  AS $$
BEGIN
  IF OLD.status = 'succeeded' THEN
    RAISE EXCEPTION 'ingest request %/% already succeeded', OLD.method, OLD.idempotency_key USING ERRCODE = 'check_violation';
  END IF;
  NEW.updated_at := now();
  RETURN NEW;
END $$;
CREATE TRIGGER guard_ingest_request BEFORE UPDATE ON platform.ingest_request
  FOR EACH ROW EXECUTE FUNCTION platform.guard_ingest_request();

CREATE TABLE platform.evidence_registry (
  evidence_id               uuid PRIMARY KEY,
  occurred_at               timestamptz NOT NULL,
  student_id                uuid NOT NULL REFERENCES platform.student_profile (id),
  -- 证据内容（落库形态，含学生 ID）的 SHA-256，用来区分“同一证据重试”与“ID 撞车”
  content_digest            text NOT NULL CHECK (content_digest ~ '^[0-9a-f]{64}$'),
  request_idempotency_key   text NOT NULL CHECK (length(request_idempotency_key) BETWEEN 16 AND 128),
  recorded_at               timestamptz NOT NULL DEFAULT now()
);
COMMENT ON TABLE platform.evidence_registry IS '证据 ID 全局唯一登记（分区表 evidence_event 做不到）；只追加';

-- 学习服务：幂等表可以建、读、按状态推进；指纹、学生、attempt_id 不能改；登记表只追加
GRANT SELECT, INSERT ON platform.ingest_request, platform.evidence_registry TO starbuddy_learning;
GRANT UPDATE (status, lease_token, lease_until, summary, error_code, updated_at) ON platform.ingest_request TO starbuddy_learning;

ALTER TABLE platform.ingest_request ENABLE ROW LEVEL SECURITY;
ALTER TABLE platform.ingest_request FORCE ROW LEVEL SECURITY;
CREATE POLICY ingest_service ON platform.ingest_request FOR ALL TO starbuddy_learning USING (true) WITH CHECK (true);

ALTER TABLE platform.evidence_registry ENABLE ROW LEVEL SECURITY;
ALTER TABLE platform.evidence_registry FORCE ROW LEVEL SECURITY;
CREATE POLICY evidence_registry_service ON platform.evidence_registry FOR ALL TO starbuddy_learning USING (true) WITH CHECK (true);
