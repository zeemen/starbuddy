-- 模型调用记录与计量账本的只读对账函数【草案】（specs/plt-model-call-log/spec.md 范围第 3 条）。属主与授权同 cost-usage-export.draft.sql；
-- 对账口径与它不同（这里按调用记录的 usage_ref 对齐预留与自练计次，那边按 usage_ledger 汇总消耗）；消耗事实以 platform.usage_ledger 为准，
-- 本函数的 token、成本与对上 / 对不上的计数只用于发现差异，不参与扣次与结算。
-- 本文件不在任何共享环境执行、不是迁移：它跨学生读计量表（usage_reservation、practice_log），属 RLS 与权限变更（必停项），
-- 由另起的 [NEEDS-HUMAN] PR 落为 db/migrations 下的迁移（附 RLS / 权限测试），由人确认并在副本演练后执行。
-- db/tests/79zh_model_call_log.sql 在一次性库的事务里以超级用户建出它、用合成数据核对口径，随回滚消失（不留在库里）。
--
-- 属主与授权（落迁移时照此做，与 cost-usage-export 草案的安全审查 S5 同口径）：
--   属主为专用角色（如 starbuddy_cost_export_owner）：NOLOGIN、非 SUPERUSER、非 BYPASSRLS；不得复用 starbuddy_authz、starbuddy_report
--   或任何应用角色。只给它函数用到的列的 SELECT（列级授权），并在每张表上为它单独建只读策略：
--     platform.model_call_log (occurred_at, task, outcome, input_tokens, output_tokens, estimated_cost_cny, usage_ref)
--     platform.usage_reservation (usage_id, reserved_at, state)
--     platform.practice_log (usage_id, occurred_at)
--   EXECUTE 只授给专用只读导出角色；REVOKE ALL … FROM PUBLIC。
--
-- 口径：
--   - 窗口：北京时间自然日 [p_from, p_to]（含两端），跨度至多 31 天；同 platform.model_call_stats，按调用记录的 occurred_at 归日。
--   - 摘要规则：usage_ref = left(encode(sha256(convert_to(usage_id::text, 'UTF8')), 'hex'), 16)，与学习服务 ledger.digest 一致
--     （sha256 十六进制前 16 位）。前提：调用方传给模型路由的 usage_id 就是计量账本 usage_id 的规范文本（小写、带连字符的 uuid）；
--     大小写或格式不同的对不上，计入 unmatched（待人确认）。
--   - 计量侧只看窗口前后各 1 天内建立的预留（reserved_at）与自练计次（occurred_at）：调用发生在预留之后、结算之前，
--     预留时长最长也在分钟级，前后各 1 天足够覆盖，又不必对全部计量行算摘要。
--   - 输出按 日 × 任务：调用次数、成功次数、输入 / 输出 token、估算成本合计、不同 usage_ref 数、对上预留的、对上自练计次的、
--     都对不上的 usage_ref 数，以及对上的预留里已结算（结算量大于 0）/ 复看（已结算但结算量为 0：24 小时内同题复看不重复扣，0008）/
--     已释放或过期 / 仍在预留中的数目（同一 usage_ref 多次调用只算一次）。复看单独计数：它调了模型却不扣次，混进“已结算”会让
--     “调用次数 ≈ 扣次”的核对看起来对上了。
--   - 只输出聚合：没有 usage_ref、usage_id、学生、幂等键本身；任务只是 T1–T9。
--   - 对不上的常见原因：重放命中幂等（不预留）、管理后台与离线批量（T8，不经计量）、BFF 预留失败后调用方仍调了路由（应当为 0，告警）。

CREATE FUNCTION platform.model_call_reconcile(p_from date, p_to date)
RETURNS TABLE (day date, task text, calls bigint, ok_calls bigint, input_tokens bigint, output_tokens bigint, estimated_cost_cny numeric,
               usage_refs bigint, matched_reservation bigint, matched_practice bigint, unmatched bigint,
               reservation_settled bigint, reservation_review bigint, reservation_released bigint, reservation_open bigint)
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $fn$
DECLARE
  v_lo timestamptz;
  v_hi timestamptz;
BEGIN
  IF p_from IS NULL OR p_to IS NULL OR p_to < p_from OR p_to - p_from > 31 THEN
    RAISE EXCEPTION 'p_from / p_to must be dates with p_from <= p_to and at most 31 days apart' USING ERRCODE = 'invalid_parameter_value';
  END IF;
  v_lo := p_from::timestamp AT TIME ZONE 'Asia/Shanghai';
  v_hi := (p_to + 1)::timestamp AT TIME ZONE 'Asia/Shanghai';
  RETURN QUERY
  -- CTE 与列别名都不用输出列的名字（plpgsql 里输出列是变量，同名会冲突）
  WITH cl AS (
    SELECT (l.occurred_at AT TIME ZONE 'Asia/Shanghai')::date AS d, l.task AS t, l.outcome AS o, l.input_tokens AS tin, l.output_tokens AS tout,
           l.estimated_cost_cny AS cost, l.usage_ref AS ref
      FROM platform.model_call_log l
     WHERE l.occurred_at >= v_lo AND l.occurred_at < v_hi
  ), res AS (
    SELECT left(encode(sha256(convert_to(r.usage_id::text, 'UTF8')), 'hex'), 16) AS ref,
           CASE WHEN r.state = 'settled' AND r.settled_qty = 0 THEN 'review' ELSE r.state::text END AS state
      FROM platform.usage_reservation r
     WHERE r.reserved_at >= v_lo - interval '1 day' AND r.reserved_at < v_hi + interval '1 day'
  ), prac AS (
    SELECT DISTINCT left(encode(sha256(convert_to(p.usage_id::text, 'UTF8')), 'hex'), 16) AS ref
      FROM platform.practice_log p
     WHERE p.occurred_at >= v_lo - interval '1 day' AND p.occurred_at < v_hi + interval '1 day'
  ), rf AS (                                   -- 每个 日 × 任务 × usage_ref 一行，带上对账结果
    SELECT c.d, c.t, c.ref, (SELECT min(res.state) FROM res WHERE res.ref = c.ref) AS res_state,
           EXISTS (SELECT 1 FROM prac WHERE prac.ref = c.ref) AS in_practice
      FROM (SELECT DISTINCT cl.d, cl.t, cl.ref FROM cl) c
  ), agg AS (
    SELECT cl.d, cl.t, count(*)::bigint AS n, count(*) FILTER (WHERE cl.o = 'ok')::bigint AS n_ok,
           sum(cl.tin)::bigint AS s_in, sum(cl.tout)::bigint AS s_out, coalesce(sum(cl.cost), 0)::numeric AS s_cost
      FROM cl GROUP BY cl.d, cl.t
  )
  SELECT a.d, a.t, a.n, a.n_ok, a.s_in, a.s_out, a.s_cost,
         count(r.ref)::bigint,
         count(r.ref) FILTER (WHERE r.res_state IS NOT NULL)::bigint,
         count(r.ref) FILTER (WHERE r.res_state IS NULL AND r.in_practice)::bigint,
         count(r.ref) FILTER (WHERE r.res_state IS NULL AND NOT r.in_practice)::bigint,
         count(r.ref) FILTER (WHERE r.res_state = 'settled')::bigint,
         count(r.ref) FILTER (WHERE r.res_state = 'review')::bigint,
         count(r.ref) FILTER (WHERE r.res_state IN ('released', 'expired'))::bigint,
         count(r.ref) FILTER (WHERE r.res_state = 'reserved')::bigint
    FROM agg a JOIN rf r ON r.d = a.d AND r.t = a.t
   GROUP BY a.d, a.t, a.n, a.n_ok, a.s_in, a.s_out, a.s_cost
   ORDER BY a.d, a.t;
END
$fn$;
-- 落迁移时另加：ALTER FUNCTION … OWNER TO 专用 NOLOGIN 属主；REVOKE ALL ON FUNCTION … FROM PUBLIC；只 GRANT EXECUTE 给专用只读导出角色；
-- 配 RLS / 权限测试（断言：应用角色与 starbuddy_report 都不能执行；导出角色不能直接读这几张表；属主不是 BYPASSRLS；返回列只有上面十四列；
-- 窗口越界 22023；对上 / 对不上 / 自练计次 / 各预留状态的计数与手算一致——db/tests/79zh 的合成用例可直接搬过去）。
