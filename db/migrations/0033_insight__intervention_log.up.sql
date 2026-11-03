-- 0033 insight · 触发规则 v1 的动作记录（specs/sie-03-dispute-reconcile/spec.md 设计要点 3，待人确认 #7 按默认）
-- 状态：草稿。只写文件，由人先在副本演练再执行（CLAUDE.md 必停项：迁移、RLS 与权限、画像可见范围）。依赖 0002、0010。
-- 依据：SIE V1.1 第七章触发规则（提醒、证据化鼓励、降负）、第九章 intervention_log。
-- 规则：
--   - 每条动作必须引用判断账本里同一学生、未过期的成立或假设判断（规则 11：判断先于展示；claim_ids 非空，逐个核对）；
--     下发时再核一次，其间已终结、已过期或转为待验证的拒绝下发（证据化鼓励只能基于仍然成立的判断）；
--   - 每条记下规则参数版本 params_version；下发要求签字登记里的同一规则、同一参数版本；
--   - 签字前只能是“建议”（suggested）：规则参数由 SIE 负责人与教研签字（待人确认 #7），签字登记在 insight.intervention_rule_signoff，
--     这张表没有任何应用角色能写，默认为空，所以默认不可能出现 dispatched；
--   - 只有洞察服务写；学科服务（编排层、今日计划）可读；学生、家长看不到（Agent 可见范围）；
--   - 只追加：除“建议 → 已下发”与补结果引用外不可改；只随 0035 的删除请求删除。
-- 请以单事务执行（psql -1）。

-- ---------- 规则签字登记（由人执行 SQL 登记，应用角色都不能写） ----------
CREATE TABLE insight.intervention_rule_signoff (
  rule_key        text PRIMARY KEY CHECK (rule_key ~ '^(reminder|encourage|reduce_load)\.[a-z][a-z0-9_]{1,47}$'),
  params_version  text NOT NULL CHECK (params_version ~ '^[a-z0-9][a-z0-9_.\-]{0,31}@[0-9]+\.[0-9]+\.[0-9]+$'),
  signed_by       text NOT NULL CHECK (char_length(signed_by) BETWEEN 1 AND 64),  -- 签字人工号或角色名，不是账号 ID
  signed_at       timestamptz NOT NULL
);
COMMENT ON TABLE insight.intervention_rule_signoff IS
  '触发规则签字登记（0033）：参数由 SIE 负责人与教研签字后由人登记；没有登记的规则只能产生建议（suggested），不能下发';

-- ---------- 动作记录 ----------
CREATE TABLE insight.intervention_log (
  id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  student_id     uuid NOT NULL REFERENCES platform.student_profile (id) ON DELETE CASCADE,
  rule_key       text NOT NULL CHECK (rule_key ~ '^(reminder|encourage|reduce_load)\.[a-z][a-z0-9_]{1,47}$'),
  -- 动作只是给编排层与今日计划的建议：提醒放进今日计划（不单独推送）、证据化鼓励、降负（缩短、插熟悉题、询问原因；不追加任务）
  action         text NOT NULL CHECK (action IN ('plan_review_node', 'plan_verification_item', 'encourage_with_evidence',
                                                 'shorten_task', 'insert_familiar_item', 'ask_reason')),
  claim_ids      uuid[] NOT NULL CHECK (cardinality(claim_ids) BETWEEN 1 AND 16 AND array_position(claim_ids, NULL) IS NULL),
  params_version text NOT NULL CHECK (params_version ~ '^[a-z0-9][a-z0-9_.\-]{0,31}@[0-9]+\.[0-9]+\.[0-9]+$'),  -- 规则参数版本（待签字）
  result_ref     uuid,  -- 下发后编排层的计划任务等引用（跨服务，不设外键）
  status         text NOT NULL DEFAULT 'suggested' CHECK (status IN ('suggested', 'dispatched')),
  created_at     timestamptz NOT NULL DEFAULT now(),
  dispatched_at  timestamptz,
  CONSTRAINT intervention_dispatched CHECK ((status = 'dispatched') = (dispatched_at IS NOT NULL)),
  -- 动作与规则族对应
  CONSTRAINT intervention_action_family CHECK (
    (rule_key LIKE 'reminder.%' AND action IN ('plan_review_node', 'plan_verification_item'))
    OR (rule_key LIKE 'encourage.%' AND action = 'encourage_with_evidence')
    OR (rule_key LIKE 'reduce_load.%' AND action IN ('shorten_task', 'insert_familiar_item', 'ask_reason')))
);
CREATE INDEX intervention_log_student_idx ON insight.intervention_log (student_id, created_at);
COMMENT ON TABLE insight.intervention_log IS
  '触发规则的动作记录（0033，SIE-03）：每条引用判断；签字前只能是建议。只由洞察服务写，学科服务可读，学生与家长不可见';

-- 引用核对：每个 ID 都是同一学生、未过期的成立或假设判断
CREATE FUNCTION insight.intervention_claims_ok(p_student uuid, p_claims uuid[]) RETURNS boolean
  LANGUAGE sql STABLE SET search_path = pg_catalog, insight, pg_temp
  AS $$ SELECT (SELECT count(DISTINCT c.id) FROM insight.claim c
                 WHERE c.id = ANY (p_claims) AND c.student_id = p_student
                   AND c.status IN ('hypothesis', 'established') AND c.expires_at > now())
               = (SELECT count(DISTINCT x) FROM unnest(p_claims) x) $$;
REVOKE EXECUTE ON FUNCTION insight.intervention_claims_ok(uuid, uuid[]) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION insight.intervention_claims_ok(uuid, uuid[]) TO starbuddy_insight;

CREATE FUNCTION insight.intervention_guard() RETURNS trigger LANGUAGE plpgsql
  SET search_path = pg_catalog, insight, platform, pg_temp
  AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN
    IF platform.erasure_in_progress(OLD.student_id) THEN
      RETURN OLD;
    END IF;
    RAISE EXCEPTION '动作记录只随删除请求删除' USING ERRCODE = 'insufficient_privilege';
  END IF;
  IF TG_OP = 'TRUNCATE' THEN
    RAISE EXCEPTION '动作记录不能清空' USING ERRCODE = 'insufficient_privilege';
  END IF;
  IF TG_OP = 'INSERT' THEN
    IF NEW.status <> 'suggested' OR NEW.dispatched_at IS NOT NULL THEN
      RAISE EXCEPTION '动作记录新建时只能是建议' USING ERRCODE = 'check_violation';
    END IF;
    -- 每个引用都必须是同一学生、未过期的成立或假设判断（规则 11）
    IF NOT insight.intervention_claims_ok(NEW.student_id, NEW.claim_ids) THEN
      RAISE EXCEPTION '动作引用的判断不存在、不属于该学生、已过期或不是成立 / 假设状态' USING ERRCODE = 'check_violation';
    END IF;
    NEW.created_at := now();
    RETURN NEW;
  END IF;
  -- UPDATE：只允许“建议 → 已下发”（规则已签字）与补一次结果引用
  IF (NEW.id, NEW.student_id, NEW.rule_key, NEW.action, NEW.claim_ids, NEW.params_version, NEW.created_at)
     IS DISTINCT FROM (OLD.id, OLD.student_id, OLD.rule_key, OLD.action, OLD.claim_ids, OLD.params_version, OLD.created_at) THEN
    RAISE EXCEPTION '动作记录只追加，内容不能修改' USING ERRCODE = 'check_violation';
  END IF;
  IF OLD.result_ref IS NOT NULL AND NEW.result_ref IS DISTINCT FROM OLD.result_ref THEN
    RAISE EXCEPTION '结果引用只能写一次' USING ERRCODE = 'check_violation';
  END IF;
  IF NEW.status <> OLD.status THEN
    IF NOT (OLD.status = 'suggested' AND NEW.status = 'dispatched') THEN
      RAISE EXCEPTION '已下发的动作不能改回' USING ERRCODE = 'check_violation';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM insight.intervention_rule_signoff s WHERE s.rule_key = NEW.rule_key AND s.params_version = NEW.params_version) THEN
      RAISE EXCEPTION '规则 %（参数 %）未签字，只能生成建议，不能下发', NEW.rule_key, NEW.params_version USING ERRCODE = 'insufficient_privilege';
    END IF;
    -- 下发时再核一次：引用的判断仍须成立或假设、未过期
    IF NOT insight.intervention_claims_ok(NEW.student_id, NEW.claim_ids) THEN
      RAISE EXCEPTION '引用的判断已终结、过期或转为待验证，不能下发' USING ERRCODE = 'check_violation';
    END IF;
    NEW.dispatched_at := now();
  ELSE
    NEW.dispatched_at := OLD.dispatched_at;
  END IF;
  RETURN NEW;
END $$;
REVOKE EXECUTE ON FUNCTION insight.intervention_guard() FROM PUBLIC;
CREATE TRIGGER intervention_guard BEFORE INSERT OR UPDATE OR DELETE ON insight.intervention_log
  FOR EACH ROW EXECUTE FUNCTION insight.intervention_guard();
CREATE TRIGGER intervention_truncate BEFORE TRUNCATE ON insight.intervention_log
  FOR EACH STATEMENT EXECUTE FUNCTION insight.intervention_guard();

-- ---------- 权限 ----------
ALTER TABLE insight.intervention_rule_signoff ENABLE ROW LEVEL SECURITY;
ALTER TABLE insight.intervention_rule_signoff FORCE ROW LEVEL SECURITY;
ALTER TABLE insight.intervention_log ENABLE ROW LEVEL SECURITY;
ALTER TABLE insight.intervention_log FORCE ROW LEVEL SECURITY;
REVOKE ALL ON insight.intervention_rule_signoff, insight.intervention_log FROM PUBLIC;
GRANT SELECT ON insight.intervention_rule_signoff TO starbuddy_insight;
CREATE POLICY rule_signoff_insight_read ON insight.intervention_rule_signoff FOR SELECT TO starbuddy_insight USING (true);
GRANT SELECT, INSERT (student_id, rule_key, action, claim_ids, params_version, result_ref) ON insight.intervention_log TO starbuddy_insight;
GRANT UPDATE (status, result_ref) ON insight.intervention_log TO starbuddy_insight;
CREATE POLICY intervention_insight ON insight.intervention_log FOR ALL TO starbuddy_insight USING (true) WITH CHECK (true);
-- 学科服务（编排层、今日计划）只读
GRANT SELECT ON insight.intervention_log TO starbuddy_learning;
CREATE POLICY intervention_learning_read ON insight.intervention_log FOR SELECT TO starbuddy_learning USING (true);
