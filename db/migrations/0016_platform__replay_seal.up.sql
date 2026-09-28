-- 0016 platform · 重放接口的 key 封存（specs/plugin-api-v1.0-step2/spec.md；契约 learning-internal.v1.yaml，评审 N17）
-- BFF 的计量预留过期后，先调学习服务的重放接口（只查不执行）。没有记录或之前确定失败时，重放接口封存这个
-- (method, idempotency_key)：之后以它到达的原请求不再执行（409 IDEMPOTENCY_KEY_SEALED），不会出现一次没有预留的执行。
-- 只有学习服务能读写；封存只追加，不能改、不能删（学生档案删除时级联删除）。
-- 重放要核对原请求的 usage_id，所以幂等表补一列（旧行为空：旧行不能被重放拿到结果，只能被封存）。

ALTER TABLE platform.ingest_request ADD COLUMN usage_id uuid;
COMMENT ON COLUMN platform.ingest_request.usage_id IS '原请求的计量预留 ID；重放接口按它与 student_id 核对（0016）';

CREATE TABLE platform.replay_seal (
  method            text NOT NULL CHECK (method IN ('grade', 'diagnose')),
  idempotency_key   text NOT NULL CHECK (length(idempotency_key) BETWEEN 16 AND 128),
  student_id        uuid NOT NULL REFERENCES platform.student_profile (id) ON DELETE CASCADE,
  usage_id          uuid NOT NULL,
  sealed_at         timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (method, idempotency_key)
);
COMMENT ON TABLE platform.replay_seal IS '重放接口封存的幂等键：封存后同 key 的原请求一律不执行（0016）';

CREATE FUNCTION platform.reject_replay_seal_change() RETURNS trigger
  LANGUAGE plpgsql SET search_path = pg_catalog, platform, pg_temp
  AS $$
BEGIN
  RAISE EXCEPTION 'replay_seal is append-only' USING ERRCODE = 'insufficient_privilege';
END $$;
CREATE TRIGGER reject_replay_seal_change BEFORE UPDATE ON platform.replay_seal
  FOR EACH ROW EXECUTE FUNCTION platform.reject_replay_seal_change();
-- TRUNCATE 也挡住；DELETE 不加触发器（学生档案删除时要级联删除），只靠不授权
CREATE TRIGGER reject_replay_seal_truncate BEFORE TRUNCATE ON platform.replay_seal
  FOR EACH STATEMENT EXECUTE FUNCTION platform.reject_replay_seal_change();
REVOKE EXECUTE ON FUNCTION platform.reject_replay_seal_change() FROM PUBLIC;

GRANT SELECT, INSERT ON platform.replay_seal TO starbuddy_learning;
ALTER TABLE platform.replay_seal ENABLE ROW LEVEL SECURITY;
ALTER TABLE platform.replay_seal FORCE ROW LEVEL SECURITY;
-- 按操作分开写：以后误授 UPDATE / DELETE 时 RLS 这一层也不放行（安全审查建议 6）
CREATE POLICY replay_seal_read ON platform.replay_seal FOR SELECT TO starbuddy_learning USING (true);
CREATE POLICY replay_seal_insert ON platform.replay_seal FOR INSERT TO starbuddy_learning WITH CHECK (true);
