-- PLT-17 成本仪表盘：伪名化消耗聚合行只读导出函数的【草案】（说明见 tools/loadtest/RUNBOOK.md 第 4 节）。
-- 本文件不执行、不是迁移：它是 RLS 与权限策略变更（SECURITY DEFINER 跨学生读计量表与学习服务的幂等表），属必停项，
-- 由另起的 [NEEDS-HUMAN] PR 落为 db/migrations 下的迁移（附 RLS / 权限测试），由人确认并在副本演练后执行。
--
-- 属主与授权（落迁移时照此做，安全审查 S5）：
--   函数属主为新建的专用角色（如 starbuddy_cost_export_owner）：NOLOGIN、非 SUPERUSER、非 BYPASSRLS；不得复用 starbuddy_authz、
--   starbuddy_report 或任何应用角色。只给它函数用到的列的 SELECT（列级授权），并在每张表上为它单独建只读策略：
--     platform.usage_ledger (student_id, entitlement_id, kind, quantity, occurred_at, source_method, source_idempotency_key,
--                            entry_id, corrects_entry_id, corrects_occurred_at, corrects_kind)
--     platform.entitlement (id, student_id, subscription_id, resource_type, period_start)
--     platform.subscription (id, student_id, tier, config_version, source, status, period_start, period_end)
--     platform.ingest_request (method, idempotency_key, student_id, subject)
--     platform.consent_record (student_id, consent_type, document_version, decision, decided_at)
--     platform.student_profile (id, erased_at)、platform.erasure_request (student_id, status)；另需 EXECUTE platform.meter_now()
--   EXECUTE 只授给专用只读导出角色（LOGIN 只在试点环境、只由执行人使用）；REVOKE ALL … FROM PUBLIC。
--   现状：计量表没有学科列；starbuddy_report 没有计量表权限；platform.ingest_request 只有 starbuddy_learning 能读写（迁移 0007）。
--
-- 函数只读：不写任何表；返回 伪名 × 周期 × 档位 × 科目 × 资源 × 消耗量，与工具的输入格式（cost-dashboard-usage/1）列一致。
-- 口径（均待人确认，spec 待人确认 1、4）：
--   窗口 = 每名学生导出时的主订阅（与 platform.meter_main_subscription 同口径：status ∈ (active, grace)、source <> 'addon'、
--     当前时刻落在周期内）的当前计费周期（滚动 30 天，导出时可能未满；与自然月不对齐）；month 列 = 周期开始时刻在 Asia/Shanghai 的月份。
--     不导出更早的周期：subscription.tier 是“当前”档位（升级在原行上改，没有历史），更早周期的档位无从得知（契约审查 B1）。
--   档位 = 该主订阅的 tier（导出时档位）；期内升降档未拆分，升级前的消耗也算在升级后的档位上。
--   额度配置 = 该主订阅的 config_version，必须等于参数 p_config_version，否则 RAISE（不静默混算两种额度，契约审查 S2）。
--   消耗 = 该周期额度桶上的结算流水（kind = 'settle'），加上更正这些结算的反向流水（kind = 'correction' 且 corrects_kind = 'settle'）；
--     授予、预留、释放、过期、调整（adjust）都不算消耗；复看（dedup，结算量 0）自然不计。
--   科目 = 结算流水 source_method ∈ (grade, diagnose) 时，经 (source_method, source_idempotency_key) 关联 platform.ingest_request
--     (method, idempotency_key) 且同一学生，取 subject；关联不上、teach、以及 bff_only 资源（上传、存储、深度报告）与分钟类资源一律 'unattributed'。
--   名单：试点参与同意（pilot_participation）的最近一次决定为 granted（同一时刻有撤回按撤回），且该决定所签文档版本在白名单
--     p_consent_versions 里（安全审查 S5）；排除已执行删除的档案（student_profile.erased_at）与删除请求进行中（含冷静期）的学生。
--   伪名：salt 在库内每次调用现场生成（gen_random_bytes），不经命令行、不进 SQL 文本、不落盘；
--     伪名 = 'pil-' + hmac(student_id, salt, sha256) 的前 24 个十六进制字符映射成字母，不同次调用不可对齐。
--   自检（任一不满足即 RAISE，不返回任何行）：参数非空、同意文档版本白名单非空；名单非空；可归因（grade / diagnose）的结算里
--     归因率不为 0（为 0 说明关联口径坏了）。窗口固定为当前计费周期，不收日期参数（日期类参数校验因此不适用）。
--   只导出消耗量 > 0 的行。最小组人数、人数扰动与取整在工具里做（输出只含聚合）；函数本身返回的是个人层数据，只在受控执行机上用完即删。
-- pgcrypto 的 schema 以库内为准（下文写 public.，落迁移时按 0009 的做法查 pg_extension 确认）。

CREATE FUNCTION platform.cost_export_usage(p_config_version text, p_consent_versions text[])
RETURNS TABLE (pseudonym text, month text, tier text, subject text, resource text, quantity numeric)
LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $fn$
DECLARE
  v_salt bytea := public.gen_random_bytes(32);
  v_roster integer;
  v_attributable integer;
  v_attributed integer;
  v_other_config integer;
BEGIN
  IF p_config_version IS NULL OR p_consent_versions IS NULL OR cardinality(p_consent_versions) = 0 THEN
    RAISE EXCEPTION 'p_config_version 与 p_consent_versions 必填（同意文档版本白名单不能为空）' USING ERRCODE = 'invalid_parameter_value';
  END IF;

  CREATE TEMP TABLE cost_roster ON COMMIT DROP AS
    SELECT c.student_id
      FROM (SELECT DISTINCT ON (cr.student_id) cr.student_id, cr.decision, cr.document_version
              FROM platform.consent_record cr
             WHERE cr.consent_type = 'pilot_participation'
             ORDER BY cr.student_id, cr.decided_at DESC, (cr.decision = 'revoked') DESC) c
      JOIN platform.student_profile sp ON sp.id = c.student_id
     WHERE c.decision = 'granted'
       AND c.document_version::text = ANY (p_consent_versions)
       AND sp.erased_at IS NULL
       AND NOT EXISTS (SELECT 1 FROM platform.erasure_request er WHERE er.student_id = c.student_id AND er.status = 'pending');
  SELECT count(*) INTO v_roster FROM cost_roster;
  IF v_roster = 0 THEN
    RAISE EXCEPTION '名单为空：停止（检查同意记录与文档版本白名单）' USING ERRCODE = 'no_data_found';
  END IF;

  -- 主订阅：与 platform.meter_main_subscription 同口径（不调用它：它按单个学生取，且属主不同）
  CREATE TEMP TABLE cost_main ON COMMIT DROP AS
    SELECT DISTINCT ON (s.student_id) s.id, s.student_id, s.tier::text AS tier, s.config_version::text AS config_version,
           s.period_start, to_char(s.period_start AT TIME ZONE 'Asia/Shanghai', 'YYYY-MM') AS month
      FROM platform.subscription s
      JOIN cost_roster r ON r.student_id = s.student_id
     WHERE s.status IN ('active', 'grace') AND s.source <> 'addon'
       AND platform.meter_now() >= s.period_start AND platform.meter_now() < s.period_end
     ORDER BY s.student_id, s.period_start DESC;
  SELECT count(*) INTO v_other_config FROM cost_main WHERE config_version <> p_config_version;
  IF v_other_config > 0 THEN
    RAISE EXCEPTION '有 % 名学生的主订阅不是额度配置 %：停止（不混算两种额度）', v_other_config, p_config_version
      USING ERRCODE = 'check_violation';
  END IF;

  CREATE TEMP TABLE cost_rows ON COMMIT DROP AS
    WITH settle AS (                              -- 当前周期额度桶上的结算流水
      SELECT l.entry_id, l.occurred_at, l.student_id, l.quantity, l.source_method, l.source_idempotency_key,
             e.resource_type AS resource, m.month, m.tier
        FROM cost_main m
        JOIN platform.entitlement e ON e.subscription_id = m.id AND e.student_id = m.student_id AND e.period_start = m.period_start
        JOIN platform.usage_ledger l ON l.entitlement_id = e.id AND l.student_id = m.student_id
       WHERE l.kind = 'settle'
    )
    SELECT s.student_id, s.month, s.tier, s.resource, s.source_method,
           (ir.subject IS NOT NULL) AS attributed,
           COALESCE(ir.subject::text, 'unattributed') AS subject,
           s.quantity + coalesce((SELECT sum(c.quantity) FROM platform.usage_ledger c
                                   WHERE c.kind = 'correction' AND c.corrects_kind = 'settle'
                                     AND c.corrects_entry_id = s.entry_id AND c.corrects_occurred_at = s.occurred_at), 0) AS quantity
      FROM settle s
      LEFT JOIN platform.ingest_request ir
        ON s.source_method IN ('grade', 'diagnose')
       AND ir.method = s.source_method AND ir.idempotency_key = s.source_idempotency_key
       AND ir.student_id = s.student_id;          -- 防御：幂等键必须属于同一学生，否则按未归因

  SELECT count(*) FILTER (WHERE source_method IN ('grade', 'diagnose')), count(*) FILTER (WHERE attributed)
    INTO v_attributable, v_attributed FROM cost_rows;
  IF v_attributable > 0 AND v_attributed = 0 THEN
    RAISE EXCEPTION '可归因的结算 % 条、归因 0 条：学科关联口径失效，停止', v_attributable USING ERRCODE = 'data_exception';
  END IF;

  RETURN QUERY
    SELECT 'pil-' || translate(substr(encode(public.hmac(convert_to(g.student_id::text, 'UTF8'), v_salt, 'sha256'), 'hex'), 1, 24),
                               '0123456789', 'ghijklmnop'),
           g.month, g.tier, g.subject, g.resource, sum(g.quantity)
      FROM cost_rows g
     GROUP BY g.student_id, g.month, g.tier, g.subject, g.resource
    HAVING sum(g.quantity) > 0
     ORDER BY 1, 2, 4, 5;
END
$fn$;
-- 落迁移时另加：ALTER FUNCTION … OWNER TO 专用 NOLOGIN 属主；REVOKE ALL ON FUNCTION … FROM PUBLIC；只 GRANT EXECUTE 给专用只读导出角色；
-- 配 RLS / 权限测试（断言：应用角色与 starbuddy_report 都不能执行；导出角色不能直接读这几张表；属主不是 BYPASSRLS；
-- 返回列只有上面六列；名单外、已删除、同意文档版本不在白名单、只有加购订阅的学生都不出现；配置版本不符、名单为空时 RAISE）。
-- 函数用了临时表，只读事务里也能建（临时表不算持久写）；若落迁移时改为纯 SQL 的 CTE 写法，自检要保留。
-- 模型调用日志的落库（model_call_log）另起任务；在那之前调用记录按 RUNBOOK 第 3 节与第 4.3 节从结构化日志导出元数据。
