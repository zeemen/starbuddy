-- 0036 insight · 跨科周报 v0 快照（学生版“成长手记”、家长版周报）与周报抽检进统一复核队列
-- （specs/trust-ui-sprint5/spec.md 设计要点 4、范围 3；待人确认 #2、#3、#4、#5 按默认）
-- 状态：草稿。只写文件，由人先在副本演练再执行（CLAUDE.md 必停项：迁移、RLS 与权限、画像可见范围、复核口径）。
-- 依赖 0002（账号、档案、绑定）、0006 / 0023 / 0027（复核队列）、0010（判断账本）、0032（platform.erasure_in_progress）、
--       0035（个人层表清单与删除执行身份 starbuddy_eraser）。
-- 依据：SIE V1.1 第六章节奏（每周学生成长手记与家长周报：定时任务 + 审核抽检）、第八章信任界面（学生、家长各看到什么）；
--       CLAUDE.md 规则 5、6、11、12、13。
-- 做法：
--   - insight.weekly_report：每个学生、每周（周一到周日，上海时区）、每个受众（student / guardian）一份快照，只追加；
--     只由洞察服务写（starbuddy_insight，列级 INSERT，没有 UPDATE / DELETE）；
--   - 句子数组 sentences：每句 {claim_id, template_key, template_version, section, text, claim_revision[, label][, confidence_band]}——
--     只由判断渲染（规则 11）。写入时守卫逐句核对：同一学生、成立或假设、未过期、目标是知识节点、模板已审核且键与版本对得上、
--     可见范围含这份周报的受众；学生版不收来源为家长观察的判断（学生版不含家长问卷与家长评价）；label = preliminary 当且仅当判断是假设；
--     家长版每句带置信度分档编码，学生版不带；段落只收该受众的固定段落编码；
--     判断修订标记 claim_revision = 写入时判断的 updated_at（由守卫盖上；调用方给了就必须与库里一致）：BFF 读取时判断被修订过的句子丢弃；
--   - 只收已结束的周（周一 + 7 天 ≤ 今天，上海时区）；
--   - 客观栏 objective：使用时长、计划完成与各科练习时长，固定标来源 objective（规则 6）——是客观记录，不是判断，单独一栏；
--   - 状态 generated / withheld：一致性检查或禁用推断检查任一不过即 withheld（不存句子、不存客观栏，只存阻断编码）；
--     withheld 对学生、家长不可见；
--   - 读：学生本人只读自己的学生版、有效绑定家长只读家长版（RLS，规则 5），只读 generated；BFF 读不到阻断编码、算法版本与抽检标记；
--   - 抽检（待人确认 #4 默认）：platform.review_sample_weekly_reports 按周抽 10%（至少 5 份）进统一复核队列（source_kind = weekly_report，
--     原因 spot_check），确定性抽样（按 id 的哈希），重复调用不重复进队；抽检不阻断周报可见；抽中的周报记下复核任务 ID（抽检标记）；
--   - 删除：登记进 0035 的个人层表清单（delete，回执范围 reports）；守卫的删除分支只对删除请求放行。
-- 请以单事务执行（psql -1）。

-- ---------- 句子与客观栏的形状 ----------
-- 句子：键只能是下面这些；section 按受众取固定段落编码；家长版必须带置信度分档，学生版不能带；label 只能是 preliminary（假设）
CREATE FUNCTION insight.valid_weekly_sentences(p jsonb, p_audience text) RETURNS boolean LANGUAGE sql IMMUTABLE SET search_path = pg_catalog
  AS $$ SELECT jsonb_typeof(p) = 'array' AND jsonb_array_length(p) <= 40 AND p_audience IN ('student', 'guardian')
           AND NOT EXISTS (
             SELECT 1 FROM jsonb_array_elements(p) s
              WHERE jsonb_typeof(s) <> 'object'
                 OR EXISTS (SELECT 1 FROM jsonb_object_keys(s) k
                             WHERE k NOT IN ('claim_id', 'template_key', 'template_version', 'section', 'text', 'label', 'confidence_band',
                                             'claim_revision'))
                 OR jsonb_typeof(s -> 'claim_id') IS DISTINCT FROM 'string'
                 OR (s ->> 'claim_id') !~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
                 OR jsonb_typeof(s -> 'template_key') IS DISTINCT FROM 'string'
                 OR (s ->> 'template_key') !~ '^[a-z][a-z0-9_]*(\.[a-z0-9_]+){1,4}$'
                 OR jsonb_typeof(s -> 'template_version') IS DISTINCT FROM 'number'
                 OR (s ->> 'template_version') !~ '^[1-9][0-9]{0,4}$'
                 OR jsonb_typeof(s -> 'text') IS DISTINCT FROM 'string'
                 OR char_length(btrim(s ->> 'text')) NOT BETWEEN 1 AND 300
                 OR (s ->> 'text') ~ '[{}]'
                 OR (s ? 'label' AND (s -> 'label') IS DISTINCT FROM '"preliminary"'::jsonb)
                 OR (s ? 'claim_revision' AND (jsonb_typeof(s -> 'claim_revision') IS DISTINCT FROM 'string'
                     OR (s ->> 'claim_revision') !~ '^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(\.\d{1,6})?(Z|[+-]\d{2}(:?\d{2})?)$'))
                 OR (p_audience = 'student' AND ((s ->> 'section') IS NULL OR (s ->> 'section') NOT IN ('lit', 'working_on', 'next')))
                 OR (p_audience = 'guardian' AND ((s ->> 'section') IS NULL OR (s ->> 'section') NOT IN ('progress', 'stuck', 'suggestion', 'prediction')))
                 OR (p_audience = 'student' AND s ? 'confidence_band')
                 OR (p_audience = 'guardian' AND ((s ->> 'confidence_band') IS NULL
                                                  OR (s ->> 'confidence_band') NOT IN ('few_evidence', 'moderate', 'strong'))))
           AND (SELECT count(DISTINCT s ->> 'claim_id') FROM jsonb_array_elements(p) s) = jsonb_array_length(p) $$;

-- 客观栏：{"source_type": "objective", "usage_minutes": n, "plan_done": n, "plan_skipped": n,
--          "subjects": [{"subject": 学科编码, "practice_minutes": n, "attempts": n}, …]}，数值是有界非负整数，学科不重复、不含英语
CREATE FUNCTION insight.valid_weekly_objective(p jsonb) RETURNS boolean LANGUAGE sql IMMUTABLE SET search_path = pg_catalog
  AS $$ SELECT jsonb_typeof(p) = 'object'
           AND (SELECT array_agg(k ORDER BY k) FROM jsonb_object_keys(p) k) = ARRAY['plan_done', 'plan_skipped', 'source_type', 'subjects', 'usage_minutes']
           AND p -> 'source_type' = '"objective"'::jsonb
           AND jsonb_typeof(p -> 'usage_minutes') = 'number' AND (p ->> 'usage_minutes') ~ '^[0-9]{1,5}$' AND (p ->> 'usage_minutes')::integer <= 10080
           AND jsonb_typeof(p -> 'plan_done') = 'number' AND (p ->> 'plan_done') ~ '^[0-9]{1,4}$'
           AND jsonb_typeof(p -> 'plan_skipped') = 'number' AND (p ->> 'plan_skipped') ~ '^[0-9]{1,4}$'
           AND jsonb_typeof(p -> 'subjects') = 'array' AND jsonb_array_length(p -> 'subjects') <= 8
           AND NOT EXISTS (
             SELECT 1 FROM jsonb_array_elements(p -> 'subjects') s
              WHERE jsonb_typeof(s) <> 'object'
                 OR (SELECT array_agg(k ORDER BY k) FROM jsonb_object_keys(s) k) IS DISTINCT FROM ARRAY['attempts', 'practice_minutes', 'subject']
                 OR (s ->> 'subject') IS NULL OR (s ->> 'subject') NOT IN ('aya', 'zhixing', 'wenli')  -- 英语搁置，不出现
                 OR jsonb_typeof(s -> 'practice_minutes') IS DISTINCT FROM 'number' OR (s ->> 'practice_minutes') !~ '^[0-9]{1,5}$'
                 OR (s ->> 'practice_minutes')::integer > 10080
                 OR jsonb_typeof(s -> 'attempts') IS DISTINCT FROM 'number' OR (s ->> 'attempts') !~ '^[0-9]{1,5}$')
           AND (SELECT count(DISTINCT s ->> 'subject') FROM jsonb_array_elements(p -> 'subjects') s) = jsonb_array_length(p -> 'subjects') $$;
REVOKE EXECUTE ON FUNCTION insight.valid_weekly_sentences(jsonb, text), insight.valid_weekly_objective(jsonb) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION insight.valid_weekly_sentences(jsonb, text), insight.valid_weekly_objective(jsonb) TO starbuddy_insight, starbuddy_authz;

-- ---------- 周报快照 ----------
CREATE TABLE insight.weekly_report (
  id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  student_id          uuid NOT NULL REFERENCES platform.student_profile (id) ON DELETE CASCADE,
  week_start          date NOT NULL CHECK (extract(isodow FROM week_start) = 1),   -- 周一（上海时区的日历周）
  week_end            date NOT NULL,
  audience            text NOT NULL CHECK (audience IN ('student', 'guardian')),
  status              text NOT NULL CHECK (status IN ('generated', 'withheld')),
  sentences           jsonb NOT NULL DEFAULT '[]',
  objective           jsonb,
  withheld_reasons    text[] NOT NULL DEFAULT '{}'
                      CHECK (cardinality(withheld_reasons) <= 16 AND array_position(withheld_reasons, NULL) IS NULL
                             AND array_to_string(withheld_reasons, ',') ~ '^([a-z][a-z0-9_]{0,47}(,[a-z][a-z0-9_]{0,47})*)?$'),
  algorithm_version   platform.versioned_name NOT NULL,
  policy_version      text NOT NULL CHECK (policy_version ~ '^[A-Za-z0-9][A-Za-z0-9._\-]{0,63}$'),
  spot_check_task_id  uuid REFERENCES platform.review_task (id),                   -- 抽检标记：抽中进复核队列的任务
  generated_at        timestamptz NOT NULL DEFAULT now(),
  UNIQUE (student_id, week_start, audience),
  CONSTRAINT weekly_report_week CHECK (week_end = week_start + 6),
  CONSTRAINT weekly_report_sentences CHECK (insight.valid_weekly_sentences(sentences, audience)),
  CONSTRAINT weekly_report_objective CHECK (objective IS NULL OR insight.valid_weekly_objective(objective)),
  -- 生成：至少一句、客观栏齐全、没有阻断编码；不出：没有句子、没有客观栏、至少一个阻断编码，也不抽检
  CONSTRAINT weekly_report_status CHECK (CASE status
    WHEN 'generated' THEN jsonb_array_length(sentences) >= 1 AND objective IS NOT NULL AND cardinality(withheld_reasons) = 0
    ELSE jsonb_array_length(sentences) = 0 AND objective IS NULL AND cardinality(withheld_reasons) >= 1 AND spot_check_task_id IS NULL END)
);
CREATE INDEX weekly_report_week_idx ON insight.weekly_report (week_start, status);
COMMENT ON TABLE insight.weekly_report IS
  '跨科周报 v0 快照（0036，冲刺 5-H）：学生版成长手记与家长版周报；只追加；每句只来自判断（带 claim_id，规则 11），客观栏标来源 objective；'
  '检查不过为 withheld（对学生家长不可见）；抽检进统一复核队列（spot_check_task_id）';

-- 守卫：只追加（抽检标记只能由抽样函数从空写一次）；只收已结束的周；写入时逐句核对判断并盖上判断修订标记；删除只随删除请求
CREATE FUNCTION insight.weekly_report_guard() RETURNS trigger LANGUAGE plpgsql
  SET search_path = pg_catalog, insight, platform, pg_temp
  AS $$
BEGIN
  IF TG_OP = 'TRUNCATE' THEN
    RAISE EXCEPTION '周报不能清空' USING ERRCODE = 'insufficient_privilege';
  END IF;
  IF TG_OP = 'DELETE' THEN
    IF platform.erasure_in_progress(OLD.student_id) THEN
      RETURN OLD;
    END IF;
    RAISE EXCEPTION '周报只随删除请求删除' USING ERRCODE = 'insufficient_privilege';
  END IF;
  IF TG_OP = 'UPDATE' THEN
    IF OLD.spot_check_task_id IS NOT NULL OR NEW.spot_check_task_id IS NULL OR OLD.status <> 'generated'
       OR (to_jsonb(NEW) - 'spot_check_task_id') IS DISTINCT FROM (to_jsonb(OLD) - 'spot_check_task_id') THEN
      RAISE EXCEPTION '周报只追加：只能由抽检记下一次复核任务' USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN NEW;
  END IF;
  -- 先核句子形状（守卫在约束之前执行，形状不对时下面的类型转换会先报别的错）
  IF NOT coalesce(insight.valid_weekly_sentences(NEW.sentences, NEW.audience), false) THEN
    RAISE EXCEPTION '周报句子的形状不对' USING ERRCODE = 'check_violation';
  END IF;
  IF NEW.week_start + 7 > (now() AT TIME ZONE 'Asia/Shanghai')::date THEN
    RAISE EXCEPTION '周报只收已结束的周' USING ERRCODE = 'check_violation';
  END IF;
  -- 每句引用的判断：同一学生、成立或假设、未过期、知识节点、模板已审核且键与版本对得上、可见范围含受众；学生版不收家长观察来源（规则 11）；
  -- “初步判断”标注当且仅当判断是假设；调用方给了修订标记就必须与库里一致（渲染之后判断又被改过的不收）
  IF EXISTS (SELECT 1 FROM jsonb_array_elements(NEW.sentences) s
              WHERE NOT EXISTS (SELECT 1 FROM insight.claim c JOIN insight.claim_template t
                                         ON t.template_key = c.template_key AND t.version = c.template_version AND t.status = 'approved'
                                 WHERE c.id = (s ->> 'claim_id')::uuid AND c.student_id = NEW.student_id AND c.target_type = 'node'
                                   AND c.template_key = s ->> 'template_key' AND c.template_version = (s ->> 'template_version')::integer
                                   AND c.status IN ('hypothesis', 'established') AND c.expires_at > now()
                                   AND NEW.audience = ANY (c.visibility)
                                   AND (NEW.audience <> 'student' OR c.source_type <> 'guardian')
                                   AND (s ? 'label') = (c.status = 'hypothesis')
                                   AND (NOT s ? 'claim_revision' OR c.updated_at = (s ->> 'claim_revision')::timestamptz))) THEN
    RAISE EXCEPTION '周报的每句都必须引用该学生对这份周报的读者可见、成立或假设状态的判断' USING ERRCODE = 'check_violation';
  END IF;
  NEW.sentences := coalesce((SELECT jsonb_agg(x.s || jsonb_build_object('claim_revision', c.updated_at) ORDER BY x.o)
                               FROM jsonb_array_elements(NEW.sentences) WITH ORDINALITY x(s, o)
                               JOIN insight.claim c ON c.id = (x.s ->> 'claim_id')::uuid), '[]'::jsonb);
  NEW.generated_at := now();
  NEW.spot_check_task_id := NULL;
  RETURN NEW;
END $$;
REVOKE EXECUTE ON FUNCTION insight.weekly_report_guard() FROM PUBLIC;
CREATE TRIGGER weekly_report_guard BEFORE INSERT OR UPDATE OR DELETE ON insight.weekly_report
  FOR EACH ROW EXECUTE FUNCTION insight.weekly_report_guard();
CREATE TRIGGER weekly_report_truncate BEFORE TRUNCATE ON insight.weekly_report
  FOR EACH STATEMENT EXECUTE FUNCTION insight.weekly_report_guard();

-- ---------- 权限 ----------
ALTER TABLE insight.weekly_report ENABLE ROW LEVEL SECURITY;
ALTER TABLE insight.weekly_report FORCE ROW LEVEL SECURITY;
REVOKE ALL ON insight.weekly_report FROM PUBLIC;
-- 洞察服务：唯一写入方（列级 INSERT；没有 UPDATE、DELETE）
GRANT SELECT ON insight.weekly_report TO starbuddy_insight;
GRANT INSERT (student_id, week_start, week_end, audience, status, sentences, objective, withheld_reasons, algorithm_version, policy_version)
  ON insight.weekly_report TO starbuddy_insight;
CREATE POLICY weekly_report_insight ON insight.weekly_report FOR ALL TO starbuddy_insight USING (true) WITH CHECK (true);
-- BFF：学生本人读自己的学生版、有效绑定家长读家长版（规则 5）；只读已生成的；读不到阻断编码、算法与策略版本、抽检标记
GRANT SELECT (id, student_id, week_start, week_end, audience, status, sentences, objective, generated_at) ON insight.weekly_report TO starbuddy_bff;
CREATE POLICY weekly_report_front_read ON insight.weekly_report FOR SELECT TO starbuddy_bff
  USING (status = 'generated'
         AND ((audience = 'student' AND platform.is_self_student(student_id))
              OR (audience = 'guardian' AND platform.is_linked_guardian(student_id))));
-- 抽样与审核台详情函数的属主（authz）：读周报、只写抽检标记
GRANT SELECT ON insight.weekly_report TO starbuddy_authz;
GRANT UPDATE (spot_check_task_id) ON insight.weekly_report TO starbuddy_authz;
-- 删除执行身份（0035）：清单 delete 表的 DELETE 与学生键列
GRANT DELETE ON insight.weekly_report TO starbuddy_eraser;
GRANT SELECT (student_id) ON insight.weekly_report TO starbuddy_eraser;

-- ---------- 个人层表清单（0035）：周报随删除请求删除，回执范围 reports；不进导出包（可见范围待人确认） ----------
INSERT INTO platform.personal_table_registry (nspname, relname, key_path, student_predicate, strategy, exempt_reason, delete_order,
                                              erase_scope, export_topic, export_columns, export_filter)
VALUES ('insight', 'weekly_report', 'student_id', 'student_id = $1', 'delete', NULL, 12, 'reports', NULL, NULL, NULL);

-- ---------- 统一复核队列：周报抽检（0023 / 0027 之上） ----------
ALTER TABLE platform.review_task DROP CONSTRAINT review_task_source_kind_check;
ALTER TABLE platform.review_task ADD CONSTRAINT review_task_source_kind_check
  CHECK (source_kind IN ('grade_result', 'skill_observation', 'error_attribution', 'claim', 'diagnosis_step', 'weekly_report'));
-- 周报只以抽检进队（复用已有原因 spot_check，不新增枚举值）
ALTER TABLE platform.review_task ADD CONSTRAINT review_task_weekly_report
  CHECK (source_kind <> 'weekly_report' OR reasons::text[] = ARRAY['spot_check']);
ALTER TABLE platform.review_resolution_code DROP CONSTRAINT review_resolution_code_applies_to_check;
ALTER TABLE platform.review_resolution_code ADD CONSTRAINT review_resolution_code_applies_to_check
  CHECK (cardinality(applies_to) >= 1
         AND applies_to <@ ARRAY['grade_result', 'skill_observation', 'error_attribution', 'claim', 'diagnosis_step', 'weekly_report']);
-- 结案只记结论码：周报是判断的视图，抽检发现问题时由洞察服务按判断处理（改模板、撤回判断），审核台不改周报（规则 11）
INSERT INTO platform.review_resolution_code (code, label, applies_to) VALUES
  ('weekly_report_ok',    '周报无问题',             ARRAY['weekly_report']),
  ('weekly_report_issue', '周报有问题，已转洞察服务', ARRAY['weekly_report']);

-- 审核台详情：加上周报（其余分支与 0027 相同）。给段落、判断 ID、模板与渲染句；不给客观栏、不给学生 ID（只给不透明指针）
CREATE OR REPLACE FUNCTION platform.review_task_detail(p_task uuid) RETURNS jsonb
  LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = pg_catalog, platform, pg_temp
  AS $$
DECLARE v_staff uuid := platform.review_current_staff(); r platform.review_task; v_source jsonb;
BEGIN
  SELECT * INTO r FROM platform.review_task WHERE id = p_task;
  IF NOT FOUND THEN
    RAISE EXCEPTION '复核任务不存在' USING ERRCODE = 'no_data_found';
  END IF;
  IF r.source_kind = 'grade_result' THEN
    SELECT jsonb_build_object(
             'verdict', g.verdict, 'score_total', g.score_total, 'max_total', g.max_total, 'confidence', g.confidence,
             'calibrated', g.calibrated, 'rubric_version', g.rubric_version, 'source_version', g.source_version,
             'model_version', g.model_version, 'review_reasons', g.review_reasons::text[], 'evidence_ids', g.evidence_ids,
             'superseded', g.superseded_by IS NOT NULL, 'created_at', g.created_at,
             'dimensions', coalesce((SELECT jsonb_agg(jsonb_build_object(
                 'dimension_id', d.dimension_id, 'score', d.score, 'max_score', d.max_score, 'judged_by', d.judged_by,
                 'subjective', d.subjective, 'rule_ref', d.rule_ref, 'confidence', d.confidence, 'evidence_ids', d.evidence_ids)
                 ORDER BY d.dimension_id) FROM platform.grade_dimension d WHERE d.grade_result_id = g.id), '[]'::jsonb))
      INTO v_source FROM platform.grade_result g WHERE g.id = r.source_id;
  ELSIF r.source_kind = 'diagnosis_step' THEN
    -- 过程观察（0027）：给选题引用、判分引用与过程原文证据的引用（审核台按 ID 另取原文）；不给选题理由
    SELECT jsonb_build_object(
             'kind', st.kind, 'node_id', st.node_id, 'item_id', st.item_id, 'grade_result_id', st.grade_result_id,
             'work_evidence_id', st.work_evidence_id,
             'evidence_ids', g.evidence_ids, 'verdict', g.verdict, 'answered_at', st.answered_at)
      INTO v_source FROM platform.diagnosis_step st LEFT JOIN platform.grade_result g ON g.id = st.grade_result_id
     WHERE st.id = r.source_id;
  ELSIF r.source_kind = 'claim' THEN
    SELECT jsonb_build_object(
             'template_key', c.template_key, 'template_version', c.template_version, 'target_type', c.target_type,
             'status', c.status, 'confidence', c.confidence, 'source_type', c.source_type, 'model_version', c.model_version,
             'evidence_ids', c.evidence_ids, 'created_at', c.created_at)
      INTO v_source FROM insight.claim c WHERE c.id = r.source_id;
  ELSIF r.source_kind = 'weekly_report' THEN
    -- 周报抽检（0036）：受众、周、渲染句（每句带判断 ID 与模板）、算法与策略版本；不给客观栏（使用记录，抽检不需要，是否要给待人确认）
    SELECT jsonb_build_object(
             'audience', w.audience, 'week_start', w.week_start, 'week_end', w.week_end, 'status', w.status,
             'sentences', w.sentences, 'algorithm_version', w.algorithm_version,
             'policy_version', w.policy_version, 'generated_at', w.generated_at)
      INTO v_source FROM insight.weekly_report w WHERE w.id = r.source_id;
  END IF;
  RETURN jsonb_build_object(
    'id', r.id, 'subject', r.subject, 'student_ref', platform.review_student_ref(r.student_id), 'source_kind', r.source_kind,
    'source_id', r.source_id, 'reasons', r.reasons::text[], 'status', r.status, 'created_at', r.created_at,
    'assigned', r.assigned_to IS NOT NULL, 'assigned_to_me', r.assigned_to IS NOT NULL AND r.assigned_to = v_staff,
    'assigned_at', r.assigned_at, 'resolution_code', r.resolution_code, 'resolved_at', r.resolved_at,
    'resolved_by_me', r.resolved_by_account IS NOT NULL AND r.resolved_by_account = v_staff,
    'source', v_source);
END $$;

-- 周报抽检（只授 starbuddy_insight：周报任务生成完一周后调用）：该周已生成的周报按比例抽样，至少 max(p_min, 5) 份（不超过总数，
-- 待人确认 #4 的“至少 5 份”在库里兜底，调用方传更小的数也不会更少）；
-- 按周报 id 的哈希确定性排序，已抽过的计入份数，重复调用不重复进队；不阻断周报可见。
-- 复核任务的学科取周报第一句所引判断的学科（路由快照；review_task.subject 必填）；取不到学科时报错（不静默跳过）。返回新进队份数。
-- 参数：p_week_start 是周一、不晚于本周、最多往前 35 天；p_ratio ∈ (0, 0.5]；p_min ∈ [0, 100]
CREATE FUNCTION platform.review_sample_weekly_reports(p_week_start date, p_ratio numeric, p_min integer) RETURNS integer
  LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog, platform, pg_temp
  AS $$
DECLARE
  v_today date := (now() AT TIME ZONE 'Asia/Shanghai')::date;
  v_total integer;
  v_done integer;
  v_want integer;
  v_n integer := 0;
  w record;
  v_task uuid;
BEGIN
  IF p_week_start IS NULL OR extract(isodow FROM p_week_start) <> 1 OR p_week_start > v_today OR p_week_start < v_today - 35
     OR p_ratio IS NULL OR p_ratio <= 0 OR p_ratio > 0.5 OR p_min IS NULL OR p_min < 0 OR p_min > 100 THEN
    RAISE EXCEPTION 'week_start must be a Monday within the last 35 days, ratio in (0, 0.5], min in [0, 100]'
      USING ERRCODE = 'invalid_parameter_value';
  END IF;
  PERFORM pg_advisory_xact_lock(hashtextextended('insight.weekly_report.sample:' || p_week_start::text, 0));
  SELECT count(*), count(*) FILTER (WHERE x.spot_check_task_id IS NOT NULL) INTO v_total, v_done
    FROM insight.weekly_report x WHERE x.week_start = p_week_start AND x.status = 'generated';
  v_want := least(v_total, greatest(ceil(v_total * p_ratio)::integer, p_min, 5)) - v_done;
  IF v_want <= 0 THEN
    RETURN 0;
  END IF;
  FOR w IN SELECT x.id, x.student_id, c.subject
             FROM insight.weekly_report x
             LEFT JOIN insight.claim c ON c.id = (x.sentences -> 0 ->> 'claim_id')::uuid AND c.student_id = x.student_id
            WHERE x.week_start = p_week_start AND x.status = 'generated' AND x.spot_check_task_id IS NULL
            ORDER BY md5(x.id::text), x.id
            LIMIT v_want
              FOR UPDATE OF x LOOP
    IF w.subject IS NULL THEN
      RAISE EXCEPTION '周报 % 取不到复核任务的学科（所引判断没有学科或已不存在）', w.id USING ERRCODE = 'check_violation';
    END IF;
    INSERT INTO platform.review_task (subject, student_id, source_kind, source_id, reasons)
    VALUES (w.subject, w.student_id, 'weekly_report', w.id, ARRAY['spot_check']::platform.review_reason[])
    ON CONFLICT (source_kind, source_id) DO NOTHING
    RETURNING id INTO v_task;
    IF v_task IS NULL THEN
      SELECT t.id INTO v_task FROM platform.review_task t WHERE t.source_kind = 'weekly_report' AND t.source_id = w.id;
    ELSE
      v_n := v_n + 1;
    END IF;
    UPDATE insight.weekly_report SET spot_check_task_id = v_task WHERE id = w.id;
  END LOOP;
  RETURN v_n;
END $$;
ALTER FUNCTION platform.review_sample_weekly_reports(date, numeric, integer) OWNER TO starbuddy_authz;
REVOKE EXECUTE ON FUNCTION platform.review_sample_weekly_reports(date, numeric, integer) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION platform.review_sample_weekly_reports(date, numeric, integer) TO starbuddy_insight;
