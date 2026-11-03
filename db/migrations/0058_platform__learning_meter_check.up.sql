-- 0058 platform · 学习服务写证据前核对计量（只读）（specs/launch-lt04-learning-internal-v1/spec.md 第二节第 2 条；签字与决定记录“服务身份限定 + 计量核对”；
-- 决定记录（再续）第 2 条：诊断会话在预留有效期内开始的，预留过期后仍放行）
-- 状态：草稿。只写文件，由人先在副本演练再执行（CLAUDE.md 必停项：迁移、权限、计量）。不在任何共享环境执行。
-- 依赖：0008（usage_reservation）、0009（meter_now）、0015（practice_log）、0027 / 0049（diagnosis_session 及 starbuddy_authz 对它的列级读权限）、
-- 0007（starbuddy_learning 的 schema 使用权）。
--
-- 为什么要新函数：学习服务的登录角色（starbuddy_learning）对计量账本没有任何读权限（0008 只授 BFF 列级 SELECT，RLS 只放 BFF），
-- learning-internal v1 签字后“裸 /internal/v1/grade 不经 BFF 预留即可写证据”（specs/contract-v1.0-signoff/lessons.md 安全审查阻断）
-- 须由学习服务在写证据前核对计量记录。不给学习服务开表级 SELECT（会让它读到全部学生的预留、指纹与额度），只开这一个函数。
--
-- 只读：不预留、不结算、不回滚、不封存，不改 0008 / 0009 / 0015 的任何规则、数值与扣次口径。
-- 判据（全部字段一致才算“对得上”，任一不一致与“没有记录”同一返回，不透露 usage_id 是否存在）：
--   1. 按次 / 按量预留（usage_reservation，BFF 经 meter_reserve 建）：usage_id、idempotency_key、student_id、source_method 都一致；
--      返回预留状态：reserved 且未过期（expires_at > meter_now()）→ 'reserved'；reserved 但已过期（过期任务还没跑）→ 'lapsed'；
--      其余原样返回 'settled' / 'released' / 'expired'。
--      例外（决定记录（再续）第 2 条，计量规则变更，待人确认）：方法是 diagnose、预留已过期（lapsed 或 expired），且有一个诊断会话的
--      usage_id、幂等键、学生与这笔预留一致、会话创建时间不晚于预留过期时间（在预留有效期内开始）→ 'started_in_time'。
--      会话创建时间由学习服务建会话时写入（diagnosis_session.created_at）；不比预留创建时间（BFF 先预留再建会话）。
--   2. 题库自练计次（practice_log，BFF 经 meter_practice 建；再练与复测作答用）：usage_id、idempotency_key、student_id 一致 → 'counted'
--      （没有释放，计次即有效；p_method 必须是 grade，自练只有判分）。
--   3. 都没有 → 'none'。
-- 学习服务的用法（待人确认）：/internal/v1 的 grade、diagnose（BFF meteredCall，一次作答一笔预留）只有 'reserved' 允许新执行；
-- 再练、复测作答只认 'counted'；其余拒绝（幂等重放在核对之前返回已存结果，不受影响）。
-- 诊断会话的步骤作答：一个会话一笔 diagnosis_full 预留（source_method = diagnose），BFF 在第一道题判分成功后即结算，
-- 所以学习服务传会话自己的 usage_id 与预留键（diagnosis_session.idempotency_key）、p_method = 'diagnose'，且会话进行中时
-- 'reserved'、'settled' 与 'started_in_time' 都允许（会话已结束或放弃由学习服务自己的会话状态挡住）。
--
-- 授权：属主 starbuddy_authz（BYPASSRLS，与其他 meter_* 函数同一属主；0049 已授它读 diagnosis_session 的 usage_id、学生、幂等键、
-- 创建时间等列），search_path 固定且以 pg_temp 结尾；只授 starbuddy_learning，不授 BFF（BFF 已能按 RLS 读自己的预留）与其他角色。
-- 函数不返回额度、数量、指纹与时间，只返回状态编码；时间比较只用库里已存的两个时间，不接受调用方传入时间（不做时间探测的预言机）。

CREATE FUNCTION platform.learning_meter_check(p_usage uuid, p_key text, p_student uuid, p_method text) RETURNS text
  LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = pg_catalog, platform, pg_temp
  AS $$
DECLARE
  r platform.usage_reservation;
BEGIN
  IF p_usage IS NULL OR p_key IS NULL OR p_student IS NULL OR p_method IS NULL OR p_method NOT IN ('grade', 'diagnose') THEN
    RETURN 'none';
  END IF;
  SELECT * INTO r FROM platform.usage_reservation
   WHERE usage_id = p_usage AND idempotency_key = p_key AND student_id = p_student AND source_method = p_method;
  IF FOUND THEN
    IF r.state = 'reserved' AND r.expires_at > platform.meter_now() THEN
      RETURN 'reserved';
    END IF;
    IF p_method = 'diagnose' AND r.state IN ('reserved', 'expired')
       AND EXISTS (SELECT 1 FROM platform.diagnosis_session s
                    WHERE s.usage_id = p_usage AND s.idempotency_key::text = p_key AND s.student_id = p_student
                      AND s.created_at <= r.expires_at) THEN
      RETURN 'started_in_time';
    END IF;
    IF r.state = 'reserved' THEN
      RETURN 'lapsed';
    END IF;
    RETURN r.state::text;
  END IF;
  IF p_method = 'grade' AND EXISTS (SELECT 1 FROM platform.practice_log l
                                     WHERE l.usage_id = p_usage AND l.idempotency_key = p_key AND l.student_id = p_student) THEN
    RETURN 'counted';
  END IF;
  RETURN 'none';
END $$;

ALTER FUNCTION platform.learning_meter_check(uuid, text, uuid, text) OWNER TO starbuddy_authz;
REVOKE EXECUTE ON FUNCTION platform.learning_meter_check(uuid, text, uuid, text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION platform.learning_meter_check(uuid, text, uuid, text) TO starbuddy_learning;
COMMENT ON FUNCTION platform.learning_meter_check(uuid, text, uuid, text) IS
  '学习服务写证据前的计量核对（0058，LT-04，只读）：按 usage_id + 幂等键 + 学生 + 方法找 BFF 的预留（reserved / lapsed / settled / released / '
  'expired；诊断会话在预留有效期内开始、预留已过期时为 started_in_time）或题库自练计次（counted），任一字段不一致与没有记录同为 none。'
  '不返回额度、数量、指纹与时间；只授 starbuddy_learning';
