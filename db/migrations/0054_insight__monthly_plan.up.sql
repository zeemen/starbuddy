-- 0054 insight · 月度计划快照（specs/family-goals-monthly-plan/step2-spec.md，PLT-14 第 2 步“月度计划生成与落库”）
-- 状态：草稿。只写文件，由人先在副本演练再执行（CLAUDE.md 必停项：迁移、RLS 与权限、权益判断、画像可见范围）。
-- 请以单事务执行（psql -1）。
-- 依赖 0002（档案、绑定、is_linked_guardian / is_self_student）、0008 / 0015（plan_feature 的 weekly_report、meter_main_subscription）、
--       0010（判断账本与 claim_history）、0022 / 0034（exam_record）、0032（platform.erasure_in_progress）、0035（个人层表清单、删除执行身份）、
--       0037（platform.retest_task）、0053（insight.guardian_goal）。
-- 依据：step2-spec“默认设计”1–7 与两份审查意见（按默认处理）；spec“用户确认的口径”1、3、4；增补篇“家长自述不进学生模型”
--       “不给名次、录取概率、提分承诺”；ADR 02 决定第 2 条（一个迁移只改一个 schema：本迁移只建 insight 对象，另在
--       platform.personal_table_registry 登记一行，与 0036、0053 同一做法）；CLAUDE.md 规则 2、5、6、11、12、13。
-- 做法：
--   1. 权益：不另设套餐开关。月度计划的权益由现有权益代码 weekly_report 的取值判断——取值里含 monthly
--      （v12-pilot@1 的 full_and_monthly、ch8-hypothesis@1 的 cross_subject_and_monthly）即有权益；
--      insight.monthly_plan_entitled 按学生当前主订阅（meter_main_subscription，与单日上限同一口径）取值，属主 authz，只给 authz 的函数用。
--      自动月报不计次：本迁移不碰任何资源类型、预留、结算、回滚与扣次逻辑；
--   2. 快照表 insight.monthly_plan：每个学生、每个月（上海时区的月份第一天）一份，只追加；结构化 JSON + 模板键，不存渲染后的文字：
--      - student_plan：本月优先知识点（每项带所引“待点亮”判断；position 是计划里的先后，模板不得渲染为名次）、每周节奏、本月复测安排、
--        分科小复评安排；
--      - guardian_notes：从模板库按计划事实选出的家长注意事项（≤3 条，只有模板键与计划事实变量）；
--      - review：上月回顾（只有上月计划里、在上个月进入“迁移”的节点，每项引用迁移判断；另有上月计划节点数这一计划事实，
--        不给任何不引用判断的关于学生的计数）与分差引用（只存 exam_record_id、goal_id 与所引考试判断，不存成绩与目标分的值；
--        家长视图读时现算，以 5 分为档给区间）；
--      - plan_basis：时点审计记录（缺口、考情权重、频率、关注学科系数、优先级）；任何视图都不返回、任何计算都不读取；
--      - input_refs：生成输入的引用（判断 ID 与 claim_history 版本、掌握行、成绩记录、考试安排、诉求版本、复测任务、上月快照）与
--        配置指纹（判断策略、月度计划配置、模板库、今日计划预算配置），只供重放与审计；
--      - 版本：algorithm_version、config_version、template_version、policy_weights（default = 无已发布权重，全部为 1）、inputs_digest；
--   3. 守卫（写入时）：只收本月；逐项核对引用的判断（同一学生、成立或假设、未过期、模板已审核且键与版本对得上）：
--      优先知识点的判断须对学生与家长都可见、不是家长观察来源、对象是该节点该层次、门槛低于“迁移”；
--      上月回顾须有上月快照、计划节点数与之一致，点亮的节点须在上月快照的计划里，所引判断对家长可见、门槛进入“迁移”，
--      且按 claim_history 是在上个月（上海时区）首次进入“迁移”；
--      分差引用的考试判断须对家长可见、对象是那条成绩记录，成绩记录属于该学生、是当前版、有成绩，诉求版本属于该学生、是当前版、
--      作者仍是有效绑定家长、有同一学科同一满分的目标分；所引判断都在 input_refs 里；日期都落在本月；不能改、不能清空，删除只随删除请求；
--   4. 可见范围：RLS 打开且没有策略，应用角色都没有表权限；
--      - 洞察服务（唯一写入方）只经四个函数（属主 authz）：monthly_plan_facts（权益；当前有效绑定家长的当前版诉求里的关注学科与目标分——
--        不读补充说明与学校，不给账号——以及本月要做的复测任务）、monthly_plan_read（读快照）、monthly_plan_replay_inputs（按快照里的引用
--        取回诉求版本与复测任务，只供重放）、save_monthly_plan（写；已有快照先返回原行、不写新行，再查权益）；
--      - BFF（第 3 步接）只经两个视图函数：家长视图（只给有效绑定家长；分差只给本人当前版诉求对应的项，读时再核成绩记录与诉求仍是当前版，
--        只给 5 分一档的区间）、学生视图（只有 student_plan，读时再核判断对学生可见、不是家长观察来源）；其他身份一律 NULL；
--   5. 个人层表清单（0035）：随删除请求删除（回执范围 reports）；不进导出包（与 0036 周报同一口径，待人确认）。

-- ---------- 一、权益（不另设开关：weekly_report 的取值含 monthly 即有权益） ----------
CREATE FUNCTION insight.monthly_plan_entitled(p_student uuid) RETURNS boolean
  LANGUAGE sql STABLE SET search_path = pg_catalog, platform, pg_temp
  AS $$ SELECT coalesce((SELECT position('monthly' IN f.value_code) > 0 FROM platform.plan_feature f
                          JOIN platform.meter_main_subscription(p_student) s ON f.config_version = s.config_version AND f.tier = s.tier
                         WHERE f.feature_code = 'weekly_report'), false) $$;
ALTER FUNCTION insight.monthly_plan_entitled(uuid) OWNER TO starbuddy_authz;
REVOKE EXECUTE ON FUNCTION insight.monthly_plan_entitled(uuid) FROM PUBLIC;
COMMENT ON FUNCTION insight.monthly_plan_entitled(uuid) IS
  '月度计划权益（0054）：学生当前主订阅配置的 weekly_report 取值含 monthly 即有；没有订阅为 false。只给属主 authz 的函数用';

-- ---------- 二、取值校验（任何不合要求的输入一律返回 false，不抛错；逐层判断，不依赖 SQL 的求值顺序） ----------
CREATE FUNCTION insight.monthly_keys(p jsonb, p_keys text[]) RETURNS boolean
  LANGUAGE plpgsql IMMUTABLE SET search_path = pg_catalog, pg_temp
  AS $$
BEGIN
  IF p IS NULL OR jsonb_typeof(p) <> 'object' THEN
    RETURN false;
  END IF;
  RETURN (SELECT array_agg(k ORDER BY k) FROM jsonb_object_keys(p) k) IS NOT DISTINCT FROM (SELECT array_agg(k ORDER BY k) FROM unnest(p_keys) k);
END $$;

CREATE FUNCTION insight.monthly_int(p jsonb, p_min integer, p_max integer) RETURNS boolean
  LANGUAGE plpgsql IMMUTABLE SET search_path = pg_catalog, pg_temp
  AS $$
BEGIN
  IF p IS NULL OR jsonb_typeof(p) <> 'number' OR (p #>> '{}') !~ '^[0-9]{1,6}$' THEN
    RETURN false;
  END IF;
  RETURN (p #>> '{}')::integer BETWEEN p_min AND p_max;
END $$;

CREATE FUNCTION insight.monthly_str(p jsonb, p_re text) RETURNS boolean
  LANGUAGE plpgsql IMMUTABLE SET search_path = pg_catalog, pg_temp
  AS $$
BEGIN
  IF p IS NULL OR jsonb_typeof(p) <> 'string' THEN
    RETURN false;
  END IF;
  RETURN (p #>> '{}') ~ p_re;
END $$;

CREATE FUNCTION insight.monthly_subject(p jsonb) RETURNS boolean
  LANGUAGE plpgsql IMMUTABLE SET search_path = pg_catalog, pg_temp
  AS $$
BEGIN
  IF p IS NULL OR jsonb_typeof(p) <> 'string' THEN
    RETURN false;
  END IF;
  RETURN (p #>> '{}') = ANY (enum_range(NULL::platform.subject)::text[]);
END $$;

-- 非负数（排序依据的系数），不超过 p_max
CREATE FUNCTION insight.monthly_num(p jsonb, p_max numeric) RETURNS boolean
  LANGUAGE plpgsql IMMUTABLE SET search_path = pg_catalog, pg_temp
  AS $$
BEGIN
  IF p IS NULL OR jsonb_typeof(p) <> 'number' THEN
    RETURN false;
  END IF;
  RETURN (p #>> '{}')::numeric BETWEEN 0 AND p_max;
END $$;

-- 日期字符串 → date；格式不对或不是合法日期返回 NULL（不抛错）
CREATE FUNCTION insight.monthly_date(p jsonb) RETURNS date
  LANGUAGE plpgsql IMMUTABLE SET search_path = pg_catalog, pg_temp
  AS $$
BEGIN
  IF p IS NULL OR jsonb_typeof(p) <> 'string' OR (p #>> '{}') !~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$' THEN
    RETURN NULL;
  END IF;
  RETURN (p #>> '{}')::date;
EXCEPTION WHEN others THEN
  RETURN NULL;
END $$;

-- 小写 UUID 数组（不重复、不超过 p_max 个）
CREATE FUNCTION insight.monthly_uuids(p jsonb, p_max integer) RETURNS boolean
  LANGUAGE plpgsql IMMUTABLE SET search_path = pg_catalog, insight, pg_temp
  AS $$
DECLARE e jsonb;
BEGIN
  IF p IS NULL OR jsonb_typeof(p) <> 'array' THEN
    RETURN false;
  END IF;
  IF jsonb_array_length(p) > p_max THEN
    RETURN false;
  END IF;
  FOR e IN SELECT x FROM jsonb_array_elements(p) x LOOP
    IF NOT insight.monthly_str(e, '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$') THEN
      RETURN false;
    END IF;
  END LOOP;
  RETURN (SELECT count(DISTINCT x) FROM jsonb_array_elements(p) x) = jsonb_array_length(p);
END $$;

-- 学生学习计划：{priority_nodes, weekly_rhythm, retests, mini_reviews}
CREATE FUNCTION insight.valid_monthly_student_plan(p jsonb) RETURNS boolean
  LANGUAGE plpgsql IMMUTABLE SET search_path = pg_catalog, insight, pg_temp
  AS $$
DECLARE
  e jsonb;
  i integer := 0;
  v_seen text[] := '{}';
  v_sum integer := 0;
  v_prev text := '';
BEGIN
  IF NOT insight.monthly_keys(p, ARRAY['mini_reviews', 'priority_nodes', 'retests', 'weekly_rhythm']) THEN
    RETURN false;
  END IF;
  -- 优先知识点：position 1..n 连续（计划里的先后，不是名次）；每项带所引判断；同一节点只出现一次
  IF jsonb_typeof(p -> 'priority_nodes') <> 'array' THEN
    RETURN false;
  END IF;
  IF jsonb_array_length(p -> 'priority_nodes') > 20 THEN
    RETURN false;
  END IF;
  FOR e IN SELECT x FROM jsonb_array_elements(p -> 'priority_nodes') x LOOP
    i := i + 1;
    IF NOT insight.monthly_keys(e, ARRAY['claim_id', 'cognitive_level', 'copy_key', 'node_id', 'position', 'subject', 'template_key', 'template_version']) THEN
      RETURN false;
    END IF;
    IF NOT insight.monthly_int(e -> 'position', i, i) OR NOT insight.monthly_subject(e -> 'subject')
       OR NOT insight.monthly_str(e -> 'node_id', '^[A-Z][A-Z0-9]{1,9}(\.[A-Z0-9][A-Z0-9_]{0,31}){1,4}$')
       OR NOT insight.monthly_str(e -> 'cognitive_level', '^(know|understand|apply|synthesize)$')
       OR NOT insight.monthly_str(e -> 'claim_id', '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$')
       OR NOT insight.monthly_str(e -> 'template_key', '^[a-z][a-z0-9_]*(\.[a-z0-9_]+){1,4}$')
       OR NOT insight.monthly_int(e -> 'template_version', 1, 99999)
       OR (e -> 'copy_key') IS DISTINCT FROM '"monthly_plan.student.priority_node"'::jsonb THEN
      RETURN false;
    END IF;
    IF ((e ->> 'subject') || ':' || (e ->> 'node_id')) = ANY (v_seen) THEN
      RETURN false;
    END IF;
    v_seen := v_seen || ((e ->> 'subject') || ':' || (e ->> 'node_id'));
  END LOOP;
  -- 每周节奏
  IF NOT insight.monthly_keys(p -> 'weekly_rhythm', ARRAY['copy_key', 'minutes_per_session', 'sessions_per_week']) THEN
    RETURN false;
  END IF;
  IF NOT insight.monthly_int(p #> '{weekly_rhythm,sessions_per_week}', 1, 7) OR NOT insight.monthly_int(p #> '{weekly_rhythm,minutes_per_session}', 1, 120)
     OR (p #> '{weekly_rhythm,copy_key}') IS DISTINCT FROM '"monthly_plan.student.weekly_rhythm"'::jsonb THEN
    RETURN false;
  END IF;
  -- 本月复测：计数、日期（升序、不重复）、按学科计数（加起来等于总数）
  IF NOT insight.monthly_keys(p -> 'retests', ARRAY['by_subject', 'copy_key', 'count', 'dates']) THEN
    RETURN false;
  END IF;
  IF NOT insight.monthly_int(p #> '{retests,count}', 0, 999) OR (p #> '{retests,copy_key}') IS DISTINCT FROM '"monthly_plan.student.retests"'::jsonb
     OR jsonb_typeof(p #> '{retests,dates}') <> 'array' OR jsonb_typeof(p #> '{retests,by_subject}') <> 'array' THEN
    RETURN false;
  END IF;
  IF jsonb_array_length(p #> '{retests,dates}') > 31 OR jsonb_array_length(p #> '{retests,by_subject}') > 8 THEN
    RETURN false;
  END IF;
  FOR e IN SELECT x FROM jsonb_array_elements(p #> '{retests,dates}') x LOOP
    IF insight.monthly_date(e) IS NULL OR (e #>> '{}') <= v_prev THEN
      RETURN false;
    END IF;
    v_prev := e #>> '{}';
  END LOOP;
  v_seen := '{}';
  FOR e IN SELECT x FROM jsonb_array_elements(p #> '{retests,by_subject}') x LOOP
    IF NOT insight.monthly_keys(e, ARRAY['count', 'subject']) THEN
      RETURN false;
    END IF;
    IF NOT insight.monthly_subject(e -> 'subject') OR NOT insight.monthly_int(e -> 'count', 1, 999) OR (e ->> 'subject') = ANY (v_seen) THEN
      RETURN false;
    END IF;
    v_seen := v_seen || (e ->> 'subject');
    v_sum := v_sum + (e ->> 'count')::integer;
  END LOOP;
  IF v_sum <> (p #>> '{retests,count}')::integer THEN
    RETURN false;
  END IF;
  -- 分科小复评：每科至多一次
  IF jsonb_typeof(p -> 'mini_reviews') <> 'array' THEN
    RETURN false;
  END IF;
  IF jsonb_array_length(p -> 'mini_reviews') > 8 THEN
    RETURN false;
  END IF;
  v_seen := '{}';
  FOR e IN SELECT x FROM jsonb_array_elements(p -> 'mini_reviews') x LOOP
    IF NOT insight.monthly_keys(e, ARRAY['copy_key', 'date', 'subject']) THEN
      RETURN false;
    END IF;
    IF NOT insight.monthly_subject(e -> 'subject') OR insight.monthly_date(e -> 'date') IS NULL
       OR (e -> 'copy_key') IS DISTINCT FROM '"monthly_plan.student.mini_review"'::jsonb OR (e ->> 'subject') = ANY (v_seen) THEN
      RETURN false;
    END IF;
    v_seen := v_seen || (e ->> 'subject');
  END LOOP;
  RETURN true;
END $$;

-- 家长注意事项：≤3 条 {key, copy_key = monthly_plan.guardian.<key>, vars}；变量只有计划事实（小整数或真假），不收文字
CREATE FUNCTION insight.valid_monthly_notes(p jsonb) RETURNS boolean
  LANGUAGE plpgsql IMMUTABLE SET search_path = pg_catalog, insight, pg_temp
  AS $$
DECLARE
  e jsonb;
  v record;
  v_seen text[] := '{}';
BEGIN
  IF p IS NULL OR jsonb_typeof(p) <> 'array' THEN
    RETURN false;
  END IF;
  IF jsonb_array_length(p) > 3 THEN
    RETURN false;
  END IF;
  FOR e IN SELECT x FROM jsonb_array_elements(p) x LOOP
    IF NOT insight.monthly_keys(e, ARRAY['copy_key', 'key', 'vars']) THEN
      RETURN false;
    END IF;
    IF NOT insight.monthly_str(e -> 'key', '^[a-z][a-z0-9_]{1,47}$') OR (e ->> 'key') = ANY (v_seen)
       OR (e -> 'copy_key') IS DISTINCT FROM to_jsonb('monthly_plan.guardian.' || (e ->> 'key')) OR jsonb_typeof(e -> 'vars') <> 'object' THEN
      RETURN false;
    END IF;
    v_seen := v_seen || (e ->> 'key');
    IF (SELECT count(*) FROM jsonb_object_keys(e -> 'vars')) > 8 THEN
      RETURN false;
    END IF;
    FOR v IN SELECT * FROM jsonb_each(e -> 'vars') LOOP
      IF v.key !~ '^[a-z][a-z0-9_]{0,31}$' OR NOT (jsonb_typeof(v.value) = 'boolean' OR insight.monthly_int(v.value, 0, 9999)) THEN
        RETURN false;
      END IF;
    END LOOP;
  END LOOP;
  RETURN true;
END $$;

-- 回顾：{last_month: null | {month, planned_nodes, lit, copy_key}, score_gaps: [引用]}
CREATE FUNCTION insight.valid_monthly_review(p jsonb) RETURNS boolean
  LANGUAGE plpgsql IMMUTABLE SET search_path = pg_catalog, insight, pg_temp
  AS $$
DECLARE
  e jsonb;
  m jsonb;
  v_seen text[] := '{}';
BEGIN
  IF NOT insight.monthly_keys(p, ARRAY['last_month', 'score_gaps']) THEN
    RETURN false;
  END IF;
  m := p -> 'last_month';
  IF jsonb_typeof(m) <> 'null' THEN
    IF NOT insight.monthly_keys(m, ARRAY['copy_key', 'lit', 'month', 'planned_nodes']) THEN
      RETURN false;
    END IF;
    IF (m -> 'copy_key') IS DISTINCT FROM '"monthly_plan.review.last_month"'::jsonb OR insight.monthly_date(m -> 'month') IS NULL
       OR NOT insight.monthly_int(m -> 'planned_nodes', 0, 20) OR jsonb_typeof(m -> 'lit') <> 'array' THEN
      RETURN false;
    END IF;
    IF jsonb_array_length(m -> 'lit') > 20 THEN
      RETURN false;
    END IF;
    FOR e IN SELECT x FROM jsonb_array_elements(m -> 'lit') x LOOP
      IF NOT insight.monthly_keys(e, ARRAY['claim_id', 'copy_key', 'node_id', 'subject', 'template_key', 'template_version']) THEN
        RETURN false;
      END IF;
      IF NOT insight.monthly_subject(e -> 'subject')
         OR NOT insight.monthly_str(e -> 'node_id', '^[A-Z][A-Z0-9]{1,9}(\.[A-Z0-9][A-Z0-9_]{0,31}){1,4}$')
         OR NOT insight.monthly_str(e -> 'claim_id', '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$')
         OR NOT insight.monthly_str(e -> 'template_key', '^[a-z][a-z0-9_]*(\.[a-z0-9_]+){1,4}$')
         OR NOT insight.monthly_int(e -> 'template_version', 1, 99999)
         OR (e -> 'copy_key') IS DISTINCT FROM '"monthly_plan.review.lit"'::jsonb
         OR ((e ->> 'subject') || ':' || (e ->> 'node_id')) = ANY (v_seen) THEN
        RETURN false;
      END IF;
      v_seen := v_seen || ((e ->> 'subject') || ':' || (e ->> 'node_id'));
    END LOOP;
  END IF;
  -- 分差：只存引用（成绩记录、诉求版本、所引考试判断），不存成绩与目标分的值；家长视图读时现算
  IF jsonb_typeof(p -> 'score_gaps') <> 'array' THEN
    RETURN false;
  END IF;
  IF jsonb_array_length(p -> 'score_gaps') > 16 THEN
    RETURN false;
  END IF;
  FOR e IN SELECT x FROM jsonb_array_elements(p -> 'score_gaps') x LOOP
    IF NOT insight.monthly_keys(e, ARRAY['claim_id', 'copy_key', 'exam_record_id', 'goal_id', 'subject', 'template_key', 'template_version']) THEN
      RETURN false;
    END IF;
    IF NOT insight.monthly_subject(e -> 'subject')
       OR NOT insight.monthly_str(e -> 'claim_id', '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$')
       OR NOT insight.monthly_str(e -> 'exam_record_id', '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$')
       OR NOT insight.monthly_str(e -> 'goal_id', '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$')
       OR NOT insight.monthly_str(e -> 'template_key', '^[a-z][a-z0-9_]*(\.[a-z0-9_]+){1,4}$')
       OR NOT insight.monthly_int(e -> 'template_version', 1, 99999)
       OR (e -> 'copy_key') IS DISTINCT FROM '"monthly_plan.review.score_gap"'::jsonb THEN
      RETURN false;
    END IF;
  END LOOP;
  RETURN true;
END $$;

-- 排序依据（时点审计记录）：{items: [{subject, node_id, cognitive_level, gate, gap, weight, frequency, focus_factor, priority}]}
CREATE FUNCTION insight.valid_monthly_basis(p jsonb) RETURNS boolean
  LANGUAGE plpgsql IMMUTABLE SET search_path = pg_catalog, insight, pg_temp
  AS $$
DECLARE e jsonb;
BEGIN
  IF NOT insight.monthly_keys(p, ARRAY['items']) THEN
    RETURN false;
  END IF;
  IF jsonb_typeof(p -> 'items') <> 'array' THEN
    RETURN false;
  END IF;
  IF jsonb_array_length(p -> 'items') > 200 THEN
    RETURN false;
  END IF;
  FOR e IN SELECT x FROM jsonb_array_elements(p -> 'items') x LOOP
    IF NOT insight.monthly_keys(e, ARRAY['cognitive_level', 'focus_factor', 'frequency', 'gap', 'gate', 'node_id', 'priority', 'subject', 'weight']) THEN
      RETURN false;
    END IF;
    IF NOT insight.monthly_subject(e -> 'subject')
       OR NOT insight.monthly_str(e -> 'node_id', '^[A-Z][A-Z0-9]{1,9}(\.[A-Z0-9][A-Z0-9_]{0,31}){1,4}$')
       OR NOT insight.monthly_str(e -> 'cognitive_level', '^(know|understand|apply|synthesize)$')
       OR NOT insight.monthly_str(e -> 'gate', '^(unseen|understand|execute|stable)$')
       OR NOT insight.monthly_num(e -> 'gap', 1) OR NOT insight.monthly_num(e -> 'weight', 100) OR NOT insight.monthly_num(e -> 'frequency', 100)
       OR NOT insight.monthly_num(e -> 'focus_factor', 10) OR NOT insight.monthly_num(e -> 'priority', 100000) THEN
      RETURN false;
    END IF;
  END LOOP;
  RETURN true;
END $$;

-- 输入引用（重放与审计）：{as_of, claims: [[判断 ID, claim_history 版本 ID]], mastery, exam_records, exam_plans, goals, retest_tasks,
-- prev_plan_id, fingerprints: {claims_policy, config, templates, today}}
CREATE FUNCTION insight.valid_monthly_refs(p jsonb) RETURNS boolean
  LANGUAGE plpgsql IMMUTABLE SET search_path = pg_catalog, insight, pg_temp
  AS $$
DECLARE e jsonb;
BEGIN
  IF NOT insight.monthly_keys(p, ARRAY['as_of', 'claims', 'exam_plans', 'exam_records', 'fingerprints', 'goals', 'mastery', 'prev_plan_id',
                                       'retest_tasks']) THEN
    RETURN false;
  END IF;
  IF NOT insight.monthly_str(p -> 'as_of', '^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,6})?(Z|[+-]\d{2}:\d{2})$')
     OR NOT insight.monthly_uuids(p -> 'mastery', 2000) OR NOT insight.monthly_uuids(p -> 'exam_records', 16)
     OR NOT insight.monthly_uuids(p -> 'exam_plans', 64) OR NOT insight.monthly_uuids(p -> 'goals', 8)
     OR NOT insight.monthly_uuids(p -> 'retest_tasks', 999)
     OR NOT (jsonb_typeof(p -> 'prev_plan_id') = 'null'
             OR insight.monthly_str(p -> 'prev_plan_id', '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'))
     OR NOT insight.monthly_keys(p -> 'fingerprints', ARRAY['claims_policy', 'config', 'templates', 'today'])
     OR jsonb_typeof(p -> 'claims') <> 'array' THEN
    RETURN false;
  END IF;
  IF EXISTS (SELECT 1 FROM jsonb_each(p -> 'fingerprints') f WHERE NOT insight.monthly_str(f.value, '^[0-9a-f]{16,64}$')) THEN
    RETURN false;
  END IF;
  IF jsonb_array_length(p -> 'claims') > 2000 THEN
    RETURN false;
  END IF;
  FOR e IN SELECT x FROM jsonb_array_elements(p -> 'claims') x LOOP
    IF NOT insight.monthly_uuids(e, 2) THEN
      RETURN false;
    END IF;
    IF jsonb_array_length(e) <> 2 THEN
      RETURN false;
    END IF;
  END LOOP;
  RETURN true;
END $$;

REVOKE EXECUTE ON FUNCTION insight.monthly_keys(jsonb, text[]), insight.monthly_int(jsonb, integer, integer), insight.monthly_str(jsonb, text),
  insight.monthly_subject(jsonb), insight.monthly_num(jsonb, numeric), insight.monthly_date(jsonb), insight.monthly_uuids(jsonb, integer),
  insight.valid_monthly_student_plan(jsonb), insight.valid_monthly_notes(jsonb), insight.valid_monthly_review(jsonb),
  insight.valid_monthly_basis(jsonb), insight.valid_monthly_refs(jsonb) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION insight.monthly_keys(jsonb, text[]), insight.monthly_int(jsonb, integer, integer), insight.monthly_str(jsonb, text),
  insight.monthly_subject(jsonb), insight.monthly_num(jsonb, numeric), insight.monthly_date(jsonb), insight.monthly_uuids(jsonb, integer),
  insight.valid_monthly_student_plan(jsonb), insight.valid_monthly_notes(jsonb), insight.valid_monthly_review(jsonb),
  insight.valid_monthly_basis(jsonb), insight.valid_monthly_refs(jsonb) TO starbuddy_authz;

-- ---------- 三、快照表 ----------
CREATE TABLE insight.monthly_plan (
  id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  student_id         uuid NOT NULL REFERENCES platform.student_profile (id) ON DELETE CASCADE,
  month              date NOT NULL CONSTRAINT monthly_plan_month CHECK (extract(day FROM month) = 1),   -- 上海时区的月份第一天
  student_plan       jsonb NOT NULL CONSTRAINT monthly_plan_student_plan CHECK (insight.valid_monthly_student_plan(student_plan)),
  guardian_notes     jsonb NOT NULL DEFAULT '[]' CONSTRAINT monthly_plan_guardian_notes CHECK (insight.valid_monthly_notes(guardian_notes)),
  review             jsonb NOT NULL CONSTRAINT monthly_plan_review CHECK (insight.valid_monthly_review(review)),
  plan_basis         jsonb NOT NULL CONSTRAINT monthly_plan_basis CHECK (insight.valid_monthly_basis(plan_basis)),
  input_refs         jsonb NOT NULL CONSTRAINT monthly_plan_input_refs CHECK (insight.valid_monthly_refs(input_refs)),
  algorithm_version  platform.versioned_name NOT NULL,
  config_version     text NOT NULL CHECK (config_version ~ '^[A-Za-z0-9][A-Za-z0-9._\-]{0,63}$'),
  template_version   text NOT NULL CHECK (template_version ~ '^[A-Za-z0-9][A-Za-z0-9._\-]{0,63}$'),
  policy_weights     text NOT NULL CHECK (policy_weights IN ('default', 'published')),
  inputs_digest      text NOT NULL CHECK (inputs_digest ~ '^[0-9a-f]{64}$'),
  generated_at       timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT monthly_plan_one_per_month UNIQUE (student_id, month)
);
COMMENT ON TABLE insight.monthly_plan IS
  '月度计划快照（0054，PLT-14 第 2 步）：每个学生每月一份，只追加；结构化内容与模板键，不存渲染后的文字；关于学生的每一项都带所引判断，'
  '写入时由守卫核对；只经洞察服务的函数写入，经两个视图函数按受众读取（学生只读学习计划）';
COMMENT ON COLUMN insight.monthly_plan.plan_basis IS
  '时点审计记录（缺口、考情权重、频率、关注学科系数、优先级）：任何视图都不返回，任何计算都不读取';
COMMENT ON COLUMN insight.monthly_plan.input_refs IS
  '生成输入的引用与配置指纹：只供重放与审计，任何视图都不返回';
COMMENT ON COLUMN insight.monthly_plan.policy_weights IS 'default = 没有已发布的考情分值权重，全部按 1 计';

-- 守卫读判断历史（上月点亮按 claim_history 判断进入“迁移”的时刻）：函数属主 authz 只读
GRANT SELECT ON insight.claim_history TO starbuddy_authz;
-- 输入引用要记复测任务 ID：函数属主 authz 在 0037 已能读复测任务的学生、单元、状态与时间列，这里只补 id 一列（只读；
-- 唯一一处碰 platform 对象的授权，待人确认是否接受，见报告）
GRANT SELECT (id) ON platform.retest_task TO starbuddy_authz;

-- 守卫：只收本月；逐项核对引用；日期落在本月；只追加，删除只随删除请求
CREATE FUNCTION insight.monthly_plan_guard() RETURNS trigger LANGUAGE plpgsql
  SET search_path = pg_catalog, insight, platform, pg_temp
  AS $$
DECLARE
  v_end date;
  v_prev_nodes jsonb;
  v_prev_start timestamptz;
  v_start timestamptz;
BEGIN
  IF TG_OP = 'TRUNCATE' THEN
    RAISE EXCEPTION '月度计划不能清空' USING ERRCODE = 'insufficient_privilege';
  END IF;
  IF TG_OP = 'DELETE' THEN
    IF platform.erasure_in_progress(OLD.student_id) THEN
      RETURN OLD;
    END IF;
    RAISE EXCEPTION '月度计划只随删除请求删除' USING ERRCODE = 'insufficient_privilege';
  END IF;
  IF TG_OP = 'UPDATE' THEN
    RAISE EXCEPTION '月度计划只追加，不能修改' USING ERRCODE = 'insufficient_privilege';
  END IF;
  -- 先核形状（守卫在约束之前执行，形状不对时下面的类型转换会先报别的错）
  IF NOT (coalesce(insight.valid_monthly_student_plan(NEW.student_plan), false) AND coalesce(insight.valid_monthly_notes(NEW.guardian_notes), false)
          AND coalesce(insight.valid_monthly_review(NEW.review), false) AND coalesce(insight.valid_monthly_basis(NEW.plan_basis), false)
          AND coalesce(insight.valid_monthly_refs(NEW.input_refs), false)) THEN
    RAISE EXCEPTION '月度计划的形状不对' USING ERRCODE = 'check_violation';
  END IF;
  IF NEW.month IS DISTINCT FROM date_trunc('month', now() AT TIME ZONE 'Asia/Shanghai')::date THEN
    RAISE EXCEPTION '月度计划只收本月（上海时区）' USING ERRCODE = 'check_violation';
  END IF;
  v_end := (NEW.month + interval '1 month')::date;
  v_start := NEW.month::timestamp AT TIME ZONE 'Asia/Shanghai';
  v_prev_start := (NEW.month - interval '1 month')::timestamp AT TIME ZONE 'Asia/Shanghai';
  IF EXISTS (SELECT 1 FROM jsonb_array_elements(NEW.student_plan #> '{retests,dates}') d
              WHERE insight.monthly_date(d) < NEW.month OR insight.monthly_date(d) >= v_end)
     OR EXISTS (SELECT 1 FROM jsonb_array_elements(NEW.student_plan -> 'mini_reviews') r
                 WHERE insight.monthly_date(r -> 'date') < NEW.month OR insight.monthly_date(r -> 'date') >= v_end)
     OR (jsonb_typeof(NEW.review -> 'last_month') = 'object'
         AND insight.monthly_date(NEW.review #> '{last_month,month}') IS DISTINCT FROM (NEW.month - interval '1 month')::date) THEN
    RAISE EXCEPTION '月度计划的日期不在本月（上月回顾须是上个月）' USING ERRCODE = 'check_violation';
  END IF;
  -- 所引判断都要在输入引用里（重放能找回同一组判断）
  IF EXISTS (SELECT 1 FROM (SELECT e ->> 'claim_id' AS cid FROM jsonb_array_elements(NEW.student_plan -> 'priority_nodes') e
                            UNION ALL SELECT e ->> 'claim_id' FROM jsonb_array_elements(NEW.review -> 'score_gaps') e
                            UNION ALL SELECT e ->> 'claim_id' FROM jsonb_array_elements(coalesce(NEW.review #> '{last_month,lit}', '[]')) e) x
              WHERE NOT EXISTS (SELECT 1 FROM jsonb_array_elements(NEW.input_refs -> 'claims') r WHERE r ->> 0 = x.cid)) THEN
    RAISE EXCEPTION '所引判断必须都在输入引用里' USING ERRCODE = 'check_violation';
  END IF;
  -- 优先知识点：判断同一学生、成立或假设、未过期、模板已审核且键与版本对得上、对象是该节点该层次、门槛低于“迁移”（待点亮），
  -- 对学生与家长都可见，且不是家长观察来源（学生读得到这一段）
  IF EXISTS (SELECT 1 FROM jsonb_array_elements(NEW.student_plan -> 'priority_nodes') e
              WHERE NOT EXISTS (SELECT 1 FROM insight.claim c JOIN insight.claim_template t
                                         ON t.template_key = c.template_key AND t.version = c.template_version AND t.status = 'approved'
                                 WHERE c.id = (e ->> 'claim_id')::uuid AND c.student_id = NEW.student_id AND c.target_type = 'node'
                                   AND c.target_id = e ->> 'node_id' AND c.subject::text = e ->> 'subject'
                                   AND c.template_key = e ->> 'template_key' AND c.template_version = (e ->> 'template_version')::integer
                                   AND c.status IN ('hypothesis', 'established') AND c.expires_at > now()
                                   AND 'student' = ANY (c.visibility) AND 'guardian' = ANY (c.visibility) AND c.source_type <> 'guardian'
                                   AND c.value ->> 'level' = e ->> 'cognitive_level'
                                   AND c.value ->> 'gate' IN ('unseen', 'understand', 'execute', 'stable'))) THEN
    RAISE EXCEPTION '优先知识点必须引用该学生对学生与家长都可见、成立或假设状态的“待点亮”判断' USING ERRCODE = 'check_violation';
  END IF;
  -- 上月回顾：须有上月快照、计划节点数一致；点亮的节点在上月计划里；判断对家长可见、门槛进入“迁移”，按 claim_history 在上个月首次进入
  IF jsonb_typeof(NEW.review -> 'last_month') = 'object' THEN
    SELECT p.student_plan -> 'priority_nodes' INTO v_prev_nodes FROM insight.monthly_plan p
     WHERE p.student_id = NEW.student_id AND p.month = (NEW.month - interval '1 month')::date;
    IF v_prev_nodes IS NULL OR (NEW.review #>> '{last_month,planned_nodes}')::integer <> jsonb_array_length(v_prev_nodes) THEN
      RAISE EXCEPTION '上月回顾必须对应上月快照' USING ERRCODE = 'check_violation';
    END IF;
    IF EXISTS (SELECT 1 FROM jsonb_array_elements(NEW.review #> '{last_month,lit}') e
                WHERE NOT EXISTS (SELECT 1 FROM jsonb_array_elements(v_prev_nodes) n
                                   WHERE n ->> 'subject' = e ->> 'subject' AND n ->> 'node_id' = e ->> 'node_id')) THEN
      RAISE EXCEPTION '上月点亮的节点必须在上月快照的计划里' USING ERRCODE = 'check_violation';
    END IF;
    IF EXISTS (SELECT 1 FROM jsonb_array_elements(NEW.review #> '{last_month,lit}') e
                WHERE NOT EXISTS (SELECT 1 FROM insight.claim c JOIN insight.claim_template t
                                           ON t.template_key = c.template_key AND t.version = c.template_version AND t.status = 'approved'
                                   WHERE c.id = (e ->> 'claim_id')::uuid AND c.student_id = NEW.student_id AND c.target_type = 'node'
                                     AND c.target_id = e ->> 'node_id' AND c.subject::text = e ->> 'subject'
                                     AND c.template_key = e ->> 'template_key' AND c.template_version = (e ->> 'template_version')::integer
                                     AND c.status IN ('hypothesis', 'established') AND c.expires_at > now() AND 'guardian' = ANY (c.visibility)
                                     AND c.value ->> 'gate' IN ('transfer', 'retain')
                                     AND (SELECT min(h.at) FROM insight.claim_history h
                                           WHERE h.claim_id = c.id AND h.to_status IN ('hypothesis', 'established')
                                             AND h.value ->> 'gate' IN ('transfer', 'retain')) >= v_prev_start
                                     AND (SELECT min(h.at) FROM insight.claim_history h
                                           WHERE h.claim_id = c.id AND h.to_status IN ('hypothesis', 'established')
                                             AND h.value ->> 'gate' IN ('transfer', 'retain')) < v_start)) THEN
      RAISE EXCEPTION '上月点亮必须引用该学生对家长可见、在上个月进入“迁移”的成立或假设判断' USING ERRCODE = 'check_violation';
    END IF;
  END IF;
  -- 分差引用：考试判断对家长可见、对象是那条成绩记录；成绩记录属于该学生、是当前版、有成绩；
  -- 诉求版本属于该学生、是当前版、作者仍是有效绑定家长，且有同一学科、同一满分的目标分
  IF EXISTS (SELECT 1 FROM jsonb_array_elements(NEW.review -> 'score_gaps') e
              WHERE NOT EXISTS (SELECT 1 FROM insight.claim c JOIN insight.claim_template t
                                         ON t.template_key = c.template_key AND t.version = c.template_version AND t.status = 'approved'
                                 WHERE c.id = (e ->> 'claim_id')::uuid AND c.student_id = NEW.student_id AND c.target_type = 'exam'
                                   AND c.target_id = e ->> 'exam_record_id'
                                   AND c.template_key = e ->> 'template_key' AND c.template_version = (e ->> 'template_version')::integer
                                   AND c.status IN ('hypothesis', 'established') AND c.expires_at > now() AND 'guardian' = ANY (c.visibility))
                 OR NOT EXISTS (SELECT 1 FROM insight.exam_record r
                                  JOIN insight.guardian_goal g ON g.id = (e ->> 'goal_id')::uuid AND g.student_id = NEW.student_id
                                                              AND g.superseded_at IS NULL
                                  JOIN platform.guardian_student_link l ON l.student_id = g.student_id
                                                                       AND l.guardian_account_id = g.guardian_account_id AND l.status = 'active'
                                  JOIN platform.account a ON a.id = g.guardian_account_id AND a.kind = 'guardian' AND a.status = 'active'
                                 WHERE r.id = (e ->> 'exam_record_id')::uuid AND r.student_id = NEW.student_id AND r.superseded_at IS NULL
                                   AND r.raw_score IS NOT NULL AND r.subject::text = e ->> 'subject'
                                   AND EXISTS (SELECT 1 FROM jsonb_array_elements(g.target_scores) ts
                                                WHERE ts ->> 'subject' = e ->> 'subject' AND (ts ->> 'full_score')::numeric = r.full_score))) THEN
    RAISE EXCEPTION '分差必须引用该学生对家长可见的考试判断、当前成绩记录与有效绑定家长的当前诉求' USING ERRCODE = 'check_violation';
  END IF;
  NEW.generated_at := now();
  RETURN NEW;
END $$;
REVOKE EXECUTE ON FUNCTION insight.monthly_plan_guard() FROM PUBLIC;
CREATE TRIGGER monthly_plan_guard BEFORE INSERT OR UPDATE OR DELETE ON insight.monthly_plan
  FOR EACH ROW EXECUTE FUNCTION insight.monthly_plan_guard();
CREATE TRIGGER monthly_plan_truncate BEFORE TRUNCATE ON insight.monthly_plan
  FOR EACH STATEMENT EXECUTE FUNCTION insight.monthly_plan_guard();

-- ---------- 四、权限：应用角色没有任何表权限；函数属主 authz 读写，删除执行身份只删、只读学生键 ----------
ALTER TABLE insight.monthly_plan ENABLE ROW LEVEL SECURITY;
ALTER TABLE insight.monthly_plan FORCE ROW LEVEL SECURITY;
REVOKE ALL ON insight.monthly_plan FROM PUBLIC;
GRANT SELECT, INSERT ON insight.monthly_plan TO starbuddy_authz;
GRANT DELETE ON insight.monthly_plan TO starbuddy_eraser;
GRANT SELECT (student_id) ON insight.monthly_plan TO starbuddy_eraser;

-- 读时再核：判断仍是成立或假设、未过期、模板已审核、对该受众可见（学生受众另要求不是家长观察来源）
CREATE FUNCTION insight.monthly_live_claim(p_student uuid, p_claim text, p_key text, p_version integer, p_audience text) RETURNS boolean
  LANGUAGE sql STABLE SET search_path = pg_catalog, insight, pg_temp
  AS $$ SELECT CASE WHEN p_claim ~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$' THEN
           EXISTS (SELECT 1 FROM insight.claim c JOIN insight.claim_template t
                            ON t.template_key = c.template_key AND t.version = c.template_version AND t.status = 'approved'
                    WHERE c.id = p_claim::uuid AND c.student_id = p_student AND c.template_key = p_key AND c.template_version = p_version
                      AND c.status IN ('hypothesis', 'established') AND c.expires_at > now() AND p_audience = ANY (c.visibility)
                      AND (p_audience <> 'student' OR c.source_type <> 'guardian'))
           ELSE false END $$;

-- 学习计划按受众过滤优先知识点（其余三项是计划事实，原样给）
CREATE FUNCTION insight.monthly_live_student_plan(p_plan jsonb, p_student uuid, p_audience text) RETURNS jsonb
  LANGUAGE sql STABLE SET search_path = pg_catalog, insight, pg_temp
  AS $$ SELECT p_plan || jsonb_build_object('priority_nodes',
           (SELECT coalesce(jsonb_agg(e ORDER BY o), '[]'::jsonb) FROM jsonb_array_elements(p_plan -> 'priority_nodes') WITH ORDINALITY x(e, o)
             WHERE insight.monthly_live_claim(p_student, e ->> 'claim_id', e ->> 'template_key', (e ->> 'template_version')::integer, p_audience))) $$;

-- 分差区间：5 分一档（目标 − 成绩 ∈ (0, 5] → 0–5，(5, 10] → 5–10 …）；不高于 0 记为已达到；不给精确值、不给概率
CREATE FUNCTION insight.monthly_gap_band(p_gap numeric) RETURNS jsonb
  LANGUAGE sql IMMUTABLE SET search_path = pg_catalog
  AS $$ SELECT CASE WHEN p_gap IS NULL THEN NULL
                    WHEN p_gap <= 0 THEN jsonb_build_object('reached', true)
                    ELSE jsonb_build_object('reached', false, 'low', (ceil(p_gap / 5) * 5 - 5)::integer, 'high', (ceil(p_gap / 5) * 5)::integer) END $$;
REVOKE EXECUTE ON FUNCTION insight.monthly_live_claim(uuid, text, text, integer, text), insight.monthly_live_student_plan(jsonb, uuid, text),
  insight.monthly_gap_band(numeric) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION insight.monthly_live_claim(uuid, text, text, integer, text), insight.monthly_live_student_plan(jsonb, uuid, text),
  insight.monthly_gap_band(numeric) TO starbuddy_authz;

-- ---------- 五、洞察服务的四个函数（属主 authz，只授 starbuddy_insight） ----------
-- 生成的输入：权益；当前有效绑定（链接与家长账号都 active）家长的当前版诉求里的关注学科与目标分（不给补充说明、学校、账号）；
-- 本月要做的复测任务（开着的、到期不晚于月底；逾期的按月初计）。月份须是某月第一天
CREATE FUNCTION insight.monthly_plan_facts(p_student uuid, p_month date) RETURNS jsonb
  LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = pg_catalog, insight, platform, pg_temp
  AS $$
DECLARE
  v_end timestamptz;
BEGIN
  IF p_student IS NULL OR p_month IS NULL OR extract(day FROM p_month) <> 1 THEN
    RAISE EXCEPTION 'student and the first day of a month are required' USING ERRCODE = 'invalid_parameter_value';
  END IF;
  IF NOT EXISTS (SELECT 1 FROM platform.student_profile s WHERE s.id = p_student AND s.erased_at IS NULL)
     OR NOT insight.monthly_plan_entitled(p_student) THEN
    RETURN jsonb_build_object('entitled', false);
  END IF;
  v_end := (p_month + interval '1 month')::timestamp AT TIME ZONE 'Asia/Shanghai';
  RETURN jsonb_build_object(
    'entitled', true,
    'goals', (SELECT coalesce(jsonb_agg(jsonb_build_object('goal_id', g.id, 'focus_subjects', to_jsonb(g.focus_subjects),
                                                           'target_scores', g.target_scores) ORDER BY g.id), '[]'::jsonb)
                FROM insight.guardian_goal g
               WHERE g.student_id = p_student AND g.superseded_at IS NULL
                 AND EXISTS (SELECT 1 FROM platform.guardian_student_link l JOIN platform.account a ON a.id = l.guardian_account_id
                              WHERE l.student_id = g.student_id AND l.guardian_account_id = g.guardian_account_id AND l.status = 'active'
                                AND a.kind = 'guardian' AND a.status = 'active')),
    'retests_due', (SELECT coalesce(jsonb_agg(jsonb_build_object('task_id', r.id, 'subject', r.subject, 'node_id', r.node_id,
                                                                 'date', greatest((r.due_at AT TIME ZONE 'Asia/Shanghai')::date, p_month))
                                              ORDER BY r.due_at, r.subject, r.node_id, r.cognitive_level, r.id), '[]'::jsonb)
                      FROM platform.retest_task r WHERE r.student_id = p_student AND r.status = 'open' AND r.due_at < v_end));
END $$;

-- 读一份快照（给洞察服务做幂等返回、上月回顾与重放）。没有返回 NULL
CREATE FUNCTION insight.monthly_plan_read(p_student uuid, p_month date) RETURNS jsonb
  LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, insight, pg_temp
  AS $$ SELECT to_jsonb(m) - 'student_id' FROM insight.monthly_plan m WHERE m.student_id = p_student AND m.month = p_month $$;

-- 重放：按快照里的引用取回诉求版本（只有关注学科与目标分）与复测任务（日期按生成时的口径）。没有快照返回 NULL
CREATE FUNCTION insight.monthly_plan_replay_inputs(p_student uuid, p_month date) RETURNS jsonb
  LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, insight, platform, pg_temp
  AS $$ SELECT jsonb_build_object(
           'goals', (SELECT coalesce(jsonb_agg(jsonb_build_object('goal_id', g.id, 'focus_subjects', to_jsonb(g.focus_subjects),
                                                                  'target_scores', g.target_scores) ORDER BY g.id), '[]'::jsonb)
                       FROM insight.guardian_goal g
                      WHERE g.student_id = m.student_id AND g.id::text IN (SELECT jsonb_array_elements_text(m.input_refs -> 'goals'))),
           'retests_due', (SELECT coalesce(jsonb_agg(jsonb_build_object('task_id', r.id, 'subject', r.subject, 'node_id', r.node_id,
                                                                        'date', greatest((r.due_at AT TIME ZONE 'Asia/Shanghai')::date, m.month))
                                                     ORDER BY r.due_at, r.subject, r.node_id, r.cognitive_level, r.id), '[]'::jsonb)
                             FROM platform.retest_task r
                            WHERE r.student_id = m.student_id AND r.id::text IN (SELECT jsonb_array_elements_text(m.input_refs -> 'retest_tasks'))))
         FROM insight.monthly_plan m WHERE m.student_id = p_student AND m.month = p_month $$;

-- 写一份快照。同一学生同一月份已有就先返回原行（created = false，不写新行、不改原行），再查权益（没有权益 PLAN_NOT_ENTITLED）；
-- 档案不存在或已删除 42501。p_plan 恰好是这些键：student_plan、guardian_notes、review、plan_basis、input_refs、algorithm_version、
-- config_version、template_version、policy_weights、inputs_digest。守卫再逐项核对引用
CREATE FUNCTION insight.save_monthly_plan(p_student uuid, p_month date, p_plan jsonb) RETURNS jsonb
  LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog, insight, platform, pg_temp
  AS $$
DECLARE
  v_id uuid;
BEGIN
  IF p_student IS NULL OR p_month IS NULL
     OR NOT insight.monthly_keys(p_plan, ARRAY['algorithm_version', 'config_version', 'guardian_notes', 'input_refs', 'inputs_digest',
                                                'plan_basis', 'policy_weights', 'review', 'student_plan', 'template_version']) THEN
    RAISE EXCEPTION 'monthly plan payload is malformed' USING ERRCODE = 'invalid_parameter_value';
  END IF;
  IF NOT EXISTS (SELECT 1 FROM platform.student_profile s WHERE s.id = p_student AND s.erased_at IS NULL) THEN
    RAISE EXCEPTION 'not allowed' USING ERRCODE = 'insufficient_privilege';
  END IF;
  -- 同一学生同一月份串行：并发的两次生成只写一行
  PERFORM pg_advisory_xact_lock(hashtextextended('insight.monthly_plan:' || p_student::text || ':' || p_month::text, 0));
  SELECT m.id INTO v_id FROM insight.monthly_plan m WHERE m.student_id = p_student AND m.month = p_month;
  IF FOUND THEN
    RETURN jsonb_build_object('id', v_id, 'created', false);
  END IF;
  IF NOT insight.monthly_plan_entitled(p_student) THEN
    RAISE EXCEPTION 'PLAN_NOT_ENTITLED' USING ERRCODE = 'object_not_in_prerequisite_state';
  END IF;
  INSERT INTO insight.monthly_plan (student_id, month, student_plan, guardian_notes, review, plan_basis, input_refs, algorithm_version,
                                    config_version, template_version, policy_weights, inputs_digest)
  VALUES (p_student, p_month, p_plan -> 'student_plan', p_plan -> 'guardian_notes', p_plan -> 'review', p_plan -> 'plan_basis',
          p_plan -> 'input_refs', p_plan ->> 'algorithm_version', p_plan ->> 'config_version', p_plan ->> 'template_version',
          p_plan ->> 'policy_weights', p_plan ->> 'inputs_digest')
  RETURNING id INTO v_id;
  RETURN jsonb_build_object('id', v_id, 'created', true);
END $$;

-- ---------- 六、BFF 的两个视图函数（属主 authz，只授 BFF；第 3 步接页面） ----------
-- 家长视图：只给有效绑定家长；学习计划（读时再核判断对家长可见）、家长注意事项、回顾（上月点亮读时再核）、分差（只给本人当前版诉求
-- 对应的项，读时再核成绩记录与诉求仍是当前版，只给 5 分一档的区间，不给成绩、目标分与诉求 ID）。不返回 plan_basis 与 input_refs。
-- 学生本人、别家、已解除绑定、不存在、档案已删除一律 NULL
CREATE FUNCTION insight.monthly_plan_guardian_view(p_student uuid, p_month date) RETURNS jsonb
  LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = pg_catalog, insight, platform, pg_temp
  AS $$
DECLARE
  m insight.monthly_plan;
  v_me uuid := platform.current_account_id();
BEGIN
  IF p_student IS NULL OR p_month IS NULL OR NOT platform.is_linked_guardian(p_student) THEN
    RETURN NULL;
  END IF;
  IF EXISTS (SELECT 1 FROM platform.student_profile s WHERE s.id = p_student AND s.erased_at IS NOT NULL) THEN
    RETURN NULL;
  END IF;
  SELECT * INTO m FROM insight.monthly_plan x WHERE x.student_id = p_student AND x.month = p_month;
  IF NOT FOUND THEN
    RETURN NULL;
  END IF;
  RETURN jsonb_build_object(
    'month', m.month, 'generated_at', m.generated_at, 'config_version', m.config_version, 'template_version', m.template_version,
    'student_plan', insight.monthly_live_student_plan(m.student_plan, p_student, 'guardian'),
    'guardian_notes', m.guardian_notes,
    'review', jsonb_build_object(
      'last_month', CASE WHEN jsonb_typeof(m.review -> 'last_month') = 'object' THEN
                      (m.review -> 'last_month') || jsonb_build_object('lit',
                        (SELECT coalesce(jsonb_agg(e ORDER BY o), '[]'::jsonb)
                           FROM jsonb_array_elements(m.review #> '{last_month,lit}') WITH ORDINALITY x(e, o)
                          WHERE insight.monthly_live_claim(p_student, e ->> 'claim_id', e ->> 'template_key', (e ->> 'template_version')::integer,
                                                           'guardian')))
                    END,
      'score_gaps', (SELECT coalesce(jsonb_agg(jsonb_build_object(
                                       'subject', e ->> 'subject', 'exam_type', r.exam_type, 'exam_date', r.exam_date,
                                       'gap_band', insight.monthly_gap_band(ts.target - r.raw_score),
                                       'claim_id', e ->> 'claim_id', 'template_key', e ->> 'template_key',
                                       'template_version', (e ->> 'template_version')::integer, 'copy_key', e ->> 'copy_key') ORDER BY o), '[]'::jsonb)
                       FROM jsonb_array_elements(m.review -> 'score_gaps') WITH ORDINALITY x(e, o)
                       JOIN insight.exam_record r ON r.id = (e ->> 'exam_record_id')::uuid AND r.student_id = p_student
                                                 AND r.superseded_at IS NULL AND r.raw_score IS NOT NULL
                       JOIN insight.guardian_goal g ON g.id = (e ->> 'goal_id')::uuid AND g.student_id = p_student
                                                   AND g.guardian_account_id = v_me AND g.superseded_at IS NULL
                       CROSS JOIN LATERAL (SELECT (t ->> 'target_score')::numeric AS target FROM jsonb_array_elements(g.target_scores) t
                                            WHERE t ->> 'subject' = e ->> 'subject' AND (t ->> 'full_score')::numeric = r.full_score
                                            LIMIT 1) ts
                      WHERE insight.monthly_live_claim(p_student, e ->> 'claim_id', e ->> 'template_key', (e ->> 'template_version')::integer,
                                                       'guardian'))));
END $$;

-- 学生视图：只给学生本人；只有学习计划（读时再核判断对学生可见、不是家长观察来源）。家长、别人、不存在、档案已删除一律 NULL
CREATE FUNCTION insight.monthly_plan_student_view(p_student uuid, p_month date) RETURNS jsonb
  LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = pg_catalog, insight, platform, pg_temp
  AS $$
DECLARE m insight.monthly_plan;
BEGIN
  IF p_student IS NULL OR p_month IS NULL OR NOT platform.is_self_student(p_student) THEN
    RETURN NULL;
  END IF;
  IF EXISTS (SELECT 1 FROM platform.student_profile s WHERE s.id = p_student AND s.erased_at IS NOT NULL) THEN
    RETURN NULL;
  END IF;
  SELECT * INTO m FROM insight.monthly_plan x WHERE x.student_id = p_student AND x.month = p_month;
  IF NOT FOUND THEN
    RETURN NULL;
  END IF;
  RETURN jsonb_build_object('month', m.month, 'generated_at', m.generated_at,
                            'student_plan', insight.monthly_live_student_plan(m.student_plan, p_student, 'student'));
END $$;

ALTER FUNCTION insight.monthly_plan_facts(uuid, date) OWNER TO starbuddy_authz;
ALTER FUNCTION insight.monthly_plan_read(uuid, date) OWNER TO starbuddy_authz;
ALTER FUNCTION insight.monthly_plan_replay_inputs(uuid, date) OWNER TO starbuddy_authz;
ALTER FUNCTION insight.save_monthly_plan(uuid, date, jsonb) OWNER TO starbuddy_authz;
ALTER FUNCTION insight.monthly_plan_guardian_view(uuid, date) OWNER TO starbuddy_authz;
ALTER FUNCTION insight.monthly_plan_student_view(uuid, date) OWNER TO starbuddy_authz;
REVOKE EXECUTE ON FUNCTION insight.monthly_plan_facts(uuid, date), insight.monthly_plan_read(uuid, date),
  insight.monthly_plan_replay_inputs(uuid, date), insight.save_monthly_plan(uuid, date, jsonb), insight.monthly_plan_guardian_view(uuid, date),
  insight.monthly_plan_student_view(uuid, date) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION insight.monthly_plan_facts(uuid, date), insight.monthly_plan_read(uuid, date),
  insight.monthly_plan_replay_inputs(uuid, date), insight.save_monthly_plan(uuid, date, jsonb) TO starbuddy_insight;
GRANT EXECUTE ON FUNCTION insight.monthly_plan_guardian_view(uuid, date), insight.monthly_plan_student_view(uuid, date) TO starbuddy_bff;
COMMENT ON FUNCTION insight.save_monthly_plan(uuid, date, jsonb) IS
  '写月度计划快照（0054）：只授洞察服务；已有快照先返回原行，再查权益（weekly_report 含 monthly）；守卫逐项核对引用的判断';
COMMENT ON FUNCTION insight.monthly_plan_guardian_view(uuid, date) IS
  '家长读月度计划（0054）：只给有效绑定家长；分差只给本人当前版诉求对应的项、5 分一档的区间；引用读时再核；其他身份一律 NULL';
COMMENT ON FUNCTION insight.monthly_plan_student_view(uuid, date) IS
  '学生读自己的学习计划（0054）：只有学习计划一段，不含家长注意事项、回顾与诉求；其他身份一律 NULL';

-- ---------- 七、个人层表清单（0035）：随删除请求删除，回执范围 reports；不进导出包（待人确认） ----------
INSERT INTO platform.personal_table_registry (nspname, relname, key_path, student_predicate, strategy, exempt_reason, delete_order,
                                              erase_scope, export_topic, export_columns, export_filter)
VALUES ('insight', 'monthly_plan', 'student_id', 'student_id = $1', 'delete', NULL, 15, 'reports', NULL, NULL, NULL);
