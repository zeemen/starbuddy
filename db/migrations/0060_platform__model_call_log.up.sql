-- 0060 platform · 模型调用记录落库 platform.model_call_log（specs/plt-model-call-log/spec.md 范围第 1、3 条；决定记录（2026-10-26）第 1、2 条）
-- 状态：草稿。只写文件，由人先在副本演练再执行（CLAUDE.md 必停项：迁移、RLS 与权限）。不在任何共享环境执行。
-- 依赖：0001（platform schema、platform.subject、starbuddy_learning / starbuddy_authz）、0004（platform.ensure_monthly_partitions）、
--       0009（starbuddy_scheduler 的 schema 使用权）、0022（starbuddy_report，运营报表只读身份）。
--
-- 一、表：只存元数据（ADR 04 增补第 6 条；spec 范围第 1 条）
--   时间、环境、任务类型、数据级别、学科、供应商、模型、版本、model_version、提示词模板与版本（source_version 拆出）、结果、尝试次数、
--   是否切备、各尝试的状态类别、输入 / 输出 token、估算成本（元，无单价为空）、耗时、供应商报告的模型名与配置是否不一致、
--   usage_ref / idempotency_ref（与学习服务 ledger.digest 一致：sha256 十六进制前 16 位）。
--   **不存**提示词、对话原文、作答内容、模型输出、学生 / 账号 / 家庭 ID（没有任何 uuid 列；每列都有格式约束，自由文本进不来）。
--   主键是自增 bigint + 分区键（分区表的唯一约束必须含分区键，同 0004）。
-- 二、按月分区（决定记录第 1 条）：与 evidence_event、attempt、usage_ledger 同一写法（platform.ensure_monthly_partitions，UTC 月初切分，
--   不建 DEFAULT 分区：超出范围的写入直接失败，学习服务的 DbSink 把它计为写库失败并告警，便于发现“分区没建好”）。
--   预建 2026-10 起 15 个月（到 2027-12，与另外三张分区表同一终点）；续建随 ADR 05 的分区维护任务（与另外三张表一起，目前没有调度任务，
--   待人确认）。分区不授任何权限，只能经父表访问（攻击面 J1）。
-- 三、权限（决定记录第 1 条：运营与成本导出只经聚合函数读取，不开放行级读取）
--   - RLS ENABLE + FORCE；只有 starbuddy_learning 能 INSERT，而且是列级授权（id 与 recorded_at 只能取缺省值），WITH CHECK 只收
--     “现在前 1 天到后 5 分钟”的 occurred_at（不能补写很久以前或未来的记录来躲过或提前触发留存删除）；学习服务读不到、改不了、删不掉。
--   - 读：platform.model_call_stats(起, 止)（SECURITY DEFINER，属主 authz）按北京时间自然日 × 环境 × 任务 × 供应商 × 结果汇总
--     次数、尝试次数、切备次数、token、估算成本、无单价次数；小样本门槛（决定记录（续）第 6 条，与 insight.onboarding_stats 同口径）：
--     少于 5 次调用的格子只给次数，其余各列为空；只授 starbuddy_report（运营报表只读身份，0022；成本导出沿用它，
--     不新建角色——函数不返回任何个人层数据，与 cost-usage-export 草案的专用导出角色不同）。
--   - 留存（决定记录第 1 条，180 天）：platform.model_call_log_purge(现在, 批量)（SECURITY DEFINER，属主 authz）每次删至多一批
--     occurred_at 早于“现在 − 180 天”的行（按时间先后），只授 starbuddy_scheduler；现在不能晚于数据库 now()、最多往前 1 天，批量 1–10000，
--     越界一律 22023（定时任务函数矩阵同口径）。只删行、不删分区：删分区要表属主（迁移执行者）权限与动态 SQL，属主 authz 不持有；
--     删空的旧分区由人在分区维护时一并处理（待人确认）。调度任务定义在 services/scheduler（时间表签字后启用）。
--   - 属主 authz 只得到这张表的 SELECT 与 DELETE（两个函数要用；authz 本身 BYPASSRLS）。本表登记进个人层表清单后，这是“清单表除删除
--     执行身份外没有应用角色能 DELETE”（79p）与“除删除执行函数外没有函数删清单表”（80）的唯一例外：只删 180 天前的行、不按学生删。
--   - 报表身份 starbuddy_report 得到 platform schema 的 USAGE（0022 只授了 insight）：它在 platform 下能执行的非触发器函数恰好是
--     model_call_stats 与两个对 PUBLIC 开放的无副作用辅助函数 current_account_id()、evidence_payload_ok(text, jsonb)
--     （79zh 锁定这个集合；攻击面清单 J3 登记）。
-- 四、个人层表清单（0035；决定记录（续）第 4 条）：本表没有学生键（没有 student / account / household 列，也没有任何 uuid 列），
--   79p 的自动发现找不到它；但 usage_ref 是计量 usage_id 的摘要，能经计量记录关联到学生，所以登记进清单，strategy = delete：
--   删除请求执行时按该学生的计量预留（usage_reservation）与自练计次（practice_log）的 usage_id 重算摘要
--   （left(encode(sha256(convert_to(usage_id::text, 'UTF8')), 'hex'), 16)，与学习服务 ledger.digest 同一截断口径）匹配删除。
--   沿用 0035 的删除执行函数（谓词里的 $1 就是学生档案 ID，函数本身不用改）；删除执行身份 eraser 为此得到本表的 DELETE 与 usage_ref 的
--   SELECT、两张计量表 usage_id 与 student_id 两列的 SELECT（只读这两列，计量表仍是豁免、删不了）。回执范围记 inference（待人确认）。
--   局限：调用方传给路由的 usage_id 不是计量 usage_id 的规范文本（小写带连字符的 uuid）时摘要对不上，那几条删不到，只能等 180 天留存删除
--   （待人确认）；诊断会话等别处的 usage_id 不在匹配范围（决定记录只点名这两张计量表）。
-- 五、对账（spec 范围第 3 条）：usage_ref 与计量账本同一截断规则（left(encode(sha256(usage_id::text), 'hex'), 16)）。
--   按日、按任务与计量账本对齐的只读对账函数是草案（要跨学生读计量表，属主与授权同 cost-usage-export 草案，另起 [NEEDS-HUMAN] PR）：
--   tools/loadtest/sql/model-call-reconcile.draft.sql（db/tests/79zh 在一次性库的事务里建出并核对口径，随回滚消失）。
-- 请以单事务执行（psql -1）。

CREATE TABLE platform.model_call_log (
  id                  bigint GENERATED ALWAYS AS IDENTITY,
  occurred_at         timestamptz NOT NULL,
  recorded_at         timestamptz NOT NULL DEFAULT now(),
  env                 text NOT NULL CHECK (env IN ('dev', 'test', 'staging', 'pilot', 'prod')),
  task                text NOT NULL CHECK (task ~ '^T[1-9]$'),
  data_class          text NOT NULL CHECK (data_class IN ('L0', 'L1', 'L2', 'L3')),
  subject             platform.subject,
  -- 供应商、模型、版本：成功时取 model_version，失败时取最后一次尝试的目标；没有任何尝试（拒绝、无候选、护栏）为空
  vendor              text CHECK (vendor ~ '^[a-z0-9][a-z0-9-]{0,47}$'),
  -- 列名不用 model_name：渗透测试夹具自检禁止 platform 里出现 *_name 列（防姓名类字段）
  model               text CHECK (model ~ '^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$'),
  model_release       text CHECK (model_release ~ '^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$'),
  -- 契约 model_version（model:<供应商>/<模型>@<版本>）：只有成功的调用有
  model_version       text CHECK (model_version ~ '^model:[a-z0-9][a-z0-9-]{0,47}/[A-Za-z0-9][A-Za-z0-9._-]{0,63}@[A-Za-z0-9][A-Za-z0-9._-]{0,63}$'),
  -- 提示词模板与版本（只有名字与版本号，不是提示词本身）：source_version 合格时拆出；不合格（请求被拒），或模板不在学习服务路由层的
  -- 白名单（与提示词登记表 packages/personas/registry.yaml 一致）里时，source_version = 'invalid'、两列为空
  prompt_template     text CHECK (prompt_template ~ '^[a-z0-9][a-z0-9-]{0,47}$'),
  prompt_version      text CHECK (prompt_version ~ '^[A-Za-z0-9][A-Za-z0-9._-]{0,31}$'),
  source_version      text NOT NULL CHECK (source_version = 'invalid'
                        OR source_version ~ '^prompt:[a-z0-9][a-z0-9-]{0,47}@[A-Za-z0-9][A-Za-z0-9._-]{0,31}$'),
  outcome             text NOT NULL CHECK (outcome IN ('ok', 'unavailable', 'rejected', 'budget_exceeded', 'error')),
  attempt_count       smallint NOT NULL CHECK (attempt_count BETWEEN 0 AND 64),
  fallback            boolean NOT NULL,
  -- 各次尝试的状态类别（按先后；ok 也在里面）：只收路由的固定编码
  attempt_statuses    text[] NOT NULL CHECK (cardinality(attempt_statuses) = attempt_count AND array_position(attempt_statuses, NULL) IS NULL
                        AND attempt_statuses <@ ARRAY['ok', 'timeout', 'rate_limited', 'server', 'auth', 'client', 'bad_response',
                                                      'skipped_breaker_open']::text[]),
  input_tokens        integer NOT NULL CHECK (input_tokens >= 0),
  output_tokens       integer NOT NULL CHECK (output_tokens >= 0),
  estimated_cost_cny  numeric(14, 6) CHECK (estimated_cost_cny >= 0),
  elapsed_ms          integer NOT NULL CHECK (elapsed_ms >= 0),
  model_mismatch      boolean NOT NULL,
  usage_ref           text NOT NULL CHECK (usage_ref ~ '^[0-9a-f]{16}$'),
  idempotency_ref     text NOT NULL CHECK (idempotency_ref ~ '^[0-9a-f]{16}$'),
  PRIMARY KEY (id, occurred_at),
  CONSTRAINT model_call_log_prompt_pair CHECK ((prompt_template IS NULL) = (prompt_version IS NULL)
                                               AND (prompt_template IS NULL) = (source_version = 'invalid')),
  CONSTRAINT model_call_log_ok_has_model CHECK ((outcome = 'ok') = (model_version IS NOT NULL)),
  CONSTRAINT model_call_log_target_triple CHECK ((vendor IS NULL) = (model IS NULL) AND (vendor IS NULL) = (model_release IS NULL))
) PARTITION BY RANGE (occurred_at);
CREATE INDEX model_call_log_task_idx ON platform.model_call_log (occurred_at, task);
CREATE INDEX model_call_log_usage_idx ON platform.model_call_log (usage_ref);
COMMENT ON TABLE platform.model_call_log IS
  '模型调用记录（0060，PLT 调用记录落库）：只有元数据，不含提示词、原文、作答、输出与学生 / 账号 ID；按月分区，留存 180 天'
  '（platform.model_call_log_purge 由调度器批删）。只有 starbuddy_learning 能列级 INSERT；读取只经聚合函数 platform.model_call_stats。'
  '不是计量事实：扣次与消耗以 platform.usage_ledger 为准（这里的 token 与估算成本只供运营与成本观察，可能因队列满或写库失败缺行）。'
  'occurred_at 是调用结束（学习服务入队）的时刻，不是开始时刻：跨北京时间零点的调用归后一天。'
  '登记在个人层表清单（strategy = delete，按该学生计量记录的 usage_id 摘要匹配删除）';

SET LOCAL TIME ZONE 'UTC';
DO $$ BEGIN PERFORM platform.ensure_monthly_partitions('platform.model_call_log', DATE '2026-10-01', 15); END $$;

-- ---------- 权限：学习服务只能列级 INSERT；属主 authz 只为两个函数读与删 ----------
REVOKE ALL ON platform.model_call_log FROM PUBLIC;
GRANT INSERT (occurred_at, env, task, data_class, subject, vendor, model, model_release, model_version, prompt_template,
              prompt_version, source_version, outcome, attempt_count, fallback, attempt_statuses, input_tokens, output_tokens,
              estimated_cost_cny, elapsed_ms, model_mismatch, usage_ref, idempotency_ref)
  ON platform.model_call_log TO starbuddy_learning;
GRANT SELECT, DELETE ON platform.model_call_log TO starbuddy_authz;

ALTER TABLE platform.model_call_log ENABLE ROW LEVEL SECURITY;
ALTER TABLE platform.model_call_log FORCE ROW LEVEL SECURITY;
CREATE POLICY model_call_log_learning_insert ON platform.model_call_log FOR INSERT TO starbuddy_learning
  WITH CHECK (occurred_at >= now() - interval '1 day' AND occurred_at <= now() + interval '5 minutes');

-- ---------- 聚合读取（运营与成本导出）：按北京时间自然日 × 环境 × 任务 × 供应商 × 结果 ----------
-- 起止是北京时间的日期（含两端），跨度至多 186 天（留存 180 天加余量）；起止为空、止早于起、跨度超限一律 22023。
-- 只输出计数、token 与估算成本的合计：没有 usage_ref、幂等摘要与任何单条记录的字段。
-- 小样本门槛（决定记录（续）第 6 条，与 insight.onboarding_stats 同口径，最小 5）：一个格子少于 5 次调用时只给次数（calls），
-- 尝试、切备、token、估算成本、无单价次数一律为空（不合并成 other：格子键照常给出，只是数值不出）。本函数不输出耗时。
CREATE FUNCTION platform.model_call_stats(p_from date, p_to date)
  RETURNS TABLE (day date, env text, task text, vendor text, outcome text, calls bigint, attempts bigint, fallback_calls bigint,
                 input_tokens bigint, output_tokens bigint, estimated_cost_cny numeric, unpriced_calls bigint)
  LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = pg_catalog, platform, pg_temp
  AS $$
BEGIN
  IF p_from IS NULL OR p_to IS NULL OR p_to < p_from OR p_to - p_from > 186 THEN
    RAISE EXCEPTION 'p_from / p_to must be dates with p_from <= p_to and at most 186 days apart' USING ERRCODE = 'invalid_parameter_value';
  END IF;
  RETURN QUERY
  -- CTE 列名不用输出列的名字（plpgsql 里输出列是变量，同名会冲突）
  WITH g AS (
    SELECT (l.occurred_at AT TIME ZONE 'Asia/Shanghai')::date AS d, l.env AS e, l.task AS t, coalesce(l.vendor, 'none') AS v, l.outcome AS o,
           count(*)::bigint AS n, sum(l.attempt_count)::bigint AS n_att, count(*) FILTER (WHERE l.fallback)::bigint AS n_fb,
           sum(l.input_tokens)::bigint AS s_in, sum(l.output_tokens)::bigint AS s_out, coalesce(sum(l.estimated_cost_cny), 0)::numeric AS s_cost,
           count(*) FILTER (WHERE l.estimated_cost_cny IS NULL)::bigint AS n_unpriced
      FROM platform.model_call_log l
     WHERE l.occurred_at >= (p_from::timestamp AT TIME ZONE 'Asia/Shanghai')
       AND l.occurred_at < ((p_to + 1)::timestamp AT TIME ZONE 'Asia/Shanghai')
     GROUP BY 1, 2, 3, 4, 5
  )
  SELECT g.d, g.e, g.t, g.v, g.o, g.n,
         CASE WHEN g.n >= 5 THEN g.n_att END, CASE WHEN g.n >= 5 THEN g.n_fb END,
         CASE WHEN g.n >= 5 THEN g.s_in END, CASE WHEN g.n >= 5 THEN g.s_out END,
         CASE WHEN g.n >= 5 THEN g.s_cost END, CASE WHEN g.n >= 5 THEN g.n_unpriced END
    FROM g
   ORDER BY 1, 2, 3, 4, 5;
END $$;

-- ---------- 留存删除（只授调度器）：每次至多一批，只删 180 天前的行 ----------
CREATE FUNCTION platform.model_call_log_purge(p_now timestamptz, p_batch integer) RETURNS integer
  LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog, platform, pg_temp
  AS $$
DECLARE v_n integer;
BEGIN
  IF p_now IS NULL OR p_now > now() OR p_now < now() - interval '1 day' THEN
    RAISE EXCEPTION 'p_now must be within the last day and not in the future' USING ERRCODE = 'invalid_parameter_value';
  END IF;
  IF p_batch IS NULL OR p_batch < 1 OR p_batch > 10000 THEN
    RAISE EXCEPTION 'batch must be between 1 and 10000' USING ERRCODE = 'invalid_parameter_value';
  END IF;
  DELETE FROM platform.model_call_log l
   WHERE (l.id, l.occurred_at) IN (SELECT x.id, x.occurred_at FROM platform.model_call_log x
                                    WHERE x.occurred_at < p_now - interval '180 days'
                                    ORDER BY x.occurred_at LIMIT p_batch);
  GET DIAGNOSTICS v_n = ROW_COUNT;
  RETURN v_n;
END $$;

ALTER FUNCTION platform.model_call_stats(date, date) OWNER TO starbuddy_authz;
ALTER FUNCTION platform.model_call_log_purge(timestamptz, integer) OWNER TO starbuddy_authz;
REVOKE EXECUTE ON FUNCTION platform.model_call_stats(date, date), platform.model_call_log_purge(timestamptz, integer) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION platform.model_call_stats(date, date) TO starbuddy_report;
GRANT EXECUTE ON FUNCTION platform.model_call_log_purge(timestamptz, integer) TO starbuddy_scheduler;
-- 报表身份调用聚合函数要能进 platform schema（0022 只授了 insight 的使用权）。扩展后它在 platform 下能执行的非触发器函数恰好是
-- model_call_stats 与对 PUBLIC 开放的 current_account_id()、evidence_payload_ok(text, jsonb)（79zh 锁定；以后新函数默认对 PUBLIC
-- 开放会被它发现）；回滚（0060 down）收回这项使用权
GRANT USAGE ON SCHEMA platform TO starbuddy_report;
COMMENT ON FUNCTION platform.model_call_stats(date, date) IS
  '模型调用聚合（0060）：北京时间自然日 × 环境 × 任务 × 供应商 × 结果的次数、尝试、切备、token、估算成本与无单价次数；'
  '少于 5 次调用的格子只给次数、其余为空（小样本门槛，同 insight.onboarding_stats）；不是计量事实（以 usage_ledger 为准）；只授 starbuddy_report';

-- ---------- 个人层表清单（0035）：按该学生计量记录的 usage_id 摘要匹配删除（决定记录（续）第 4 条） ----------
-- 摘要口径与学习服务 ledger.digest 一致：sha256(usage_id 的规范文本) 十六进制前 16 位
INSERT INTO platform.personal_table_registry (nspname, relname, key_path, student_predicate, strategy, exempt_reason, delete_order,
                                              erase_scope, export_topic, export_columns, export_filter)
VALUES ('platform', 'model_call_log',
        'usage_ref → 计量预留 / 自练计次的 usage_id 摘要（platform.usage_reservation、platform.practice_log 的 student_id）',
        'usage_ref IN (SELECT left(encode(sha256(convert_to(r.usage_id::text, ''UTF8'')), ''hex''), 16) FROM platform.usage_reservation r'
        || ' WHERE r.student_id = $1 UNION SELECT left(encode(sha256(convert_to(p.usage_id::text, ''UTF8'')), ''hex''), 16)'
        || ' FROM platform.practice_log p WHERE p.student_id = $1)',
        'delete', NULL, 53, 'inference', NULL, NULL, NULL);
-- 删除执行身份：本表的 DELETE 与谓词用到的列（usage_ref；两张计量表的 usage_id、student_id）的 SELECT；计量表仍是豁免，删不了
GRANT DELETE ON platform.model_call_log TO starbuddy_eraser;
GRANT SELECT (usage_ref) ON platform.model_call_log TO starbuddy_eraser;
GRANT SELECT (usage_id, student_id) ON platform.usage_reservation, platform.practice_log TO starbuddy_eraser;
COMMENT ON FUNCTION platform.model_call_log_purge(timestamp with time zone, integer) IS
  '模型调用记录留存删除（0060）：每次至多 p_batch 行，只删 occurred_at 早于 p_now − 180 天的；只授 starbuddy_scheduler';
