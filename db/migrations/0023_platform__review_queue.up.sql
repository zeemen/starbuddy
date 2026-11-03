-- 0023 platform · 统一复核队列：领取、放回、结案码白名单、抽检、画像异议入队（specs/review-queue-sprint2/spec.md，冲刺 2-G）
-- 状态：草稿。只写文件，由人先在副本演练再执行（CLAUDE.md 必停项：迁移、RLS 与权限、判分复核口径、画像可见范围）。
-- 依赖 0006（review_task、resolve_review_task）、0009（starbuddy_scheduler）、0010（insight.claim）、0020（staff 账号登录）。
--   - 四类进同一张表：学习服务写判分时进队（低置信、主观、申诉）；调度角色抽检；洞察服务把画像异议放进来；
--   - 教研人员（active staff，经 starbuddy_review）领取 → 结案，只有领取人能结案、放回；结案码只收白名单；
--   - 审核台只经两个读函数看队列与详情：学生只给不透明指针，不给 student_id，不给作答原文（spec 待人确认 #4）；
--   - 本迁移不改分、不改掌握、不改判断（spec 设计要点 3）。
-- 枚举新值：ALTER TYPE … ADD VALUE 在同一事务里不能被使用，所以本文件里凡是比较 profile_dispute 的地方都按 text 比较；
-- 回滚删不掉枚举值（Postgres 不支持），down 只删用到它的对象，值留在类型里无害。

ALTER TYPE platform.review_reason ADD VALUE IF NOT EXISTS 'profile_dispute';

-- ---------- 结案码白名单（spec 待人确认 #5） ----------
CREATE TABLE platform.review_resolution_code (
  code         text PRIMARY KEY CHECK (code ~ '^[a-z0-9_]{1,48}$'),
  label        text NOT NULL CHECK (char_length(label) BETWEEN 1 AND 40),
  applies_to   text[] NOT NULL CHECK (cardinality(applies_to) >= 1
                 AND applies_to <@ ARRAY['grade_result', 'skill_observation', 'error_attribution', 'claim'])
);
COMMENT ON TABLE platform.review_resolution_code IS
  '复核结案码白名单（冲刺 2-G）。结案只写码，不改分、不改掌握、不改判断；改判由判分侧另写一条 grade_result（0005 supersede）';
INSERT INTO platform.review_resolution_code (code, label, applies_to) VALUES
  ('upheld',            '维持原结果',           ARRAY['grade_result', 'skill_observation', 'error_attribution']),
  ('regrade_required',  '需要改判',             ARRAY['grade_result']),
  ('content_issue',     '题目或 Rubric 有问题', ARRAY['grade_result', 'skill_observation', 'error_attribution']),
  ('dispute_forwarded', '已转洞察服务',         ARRAY['claim']),
  ('not_actionable',    '无需处理',             ARRAY['grade_result', 'skill_observation', 'error_attribution']);
-- 画像异议只能“转洞察服务”结案：判断的结论与流转只由洞察服务做（规则 11；契约审查 B4），审核台不在账本外裁决判断
ALTER TABLE platform.review_resolution_code ENABLE ROW LEVEL SECURITY;
ALTER TABLE platform.review_resolution_code FORCE ROW LEVEL SECURITY;
GRANT SELECT ON platform.review_resolution_code TO starbuddy_review, starbuddy_authz;
CREATE POLICY resolution_code_read ON platform.review_resolution_code FOR SELECT TO starbuddy_review USING (true);

-- ---------- review_task：画像异议、领取人 ----------
ALTER TABLE platform.review_task DROP CONSTRAINT review_task_source_kind_check;
ALTER TABLE platform.review_task ADD CONSTRAINT review_task_source_kind_check
  CHECK (source_kind IN ('grade_result', 'skill_observation', 'error_attribution', 'claim'));
-- 画像异议只指向判断，指向判断的只能是画像异议
ALTER TABLE platform.review_task ADD CONSTRAINT review_task_claim_is_dispute
  CHECK ((source_kind = 'claim') = ('profile_dispute' = ANY (reasons::text[])));
ALTER TABLE platform.review_task
  ADD COLUMN assigned_to  uuid REFERENCES platform.account (id),
  ADD COLUMN assigned_at  timestamptz;
-- 待处理没有领取人；处理中必须有；已结案的保留领取人（0023 之前结案的没有，所以不要求）
ALTER TABLE platform.review_task ADD CONSTRAINT review_task_assignment CHECK (CASE status
  WHEN 'open' THEN assigned_to IS NULL AND assigned_at IS NULL
  WHEN 'in_progress' THEN assigned_to IS NOT NULL AND assigned_at IS NOT NULL
  ELSE (assigned_to IS NULL) = (assigned_at IS NULL) END);
GRANT UPDATE (assigned_to, assigned_at) ON platform.review_task TO starbuddy_authz;
GRANT INSERT ON platform.review_task TO starbuddy_authz;
-- 抽检与详情要读判分与维度；画像异议要读判断（0010 已授 authz SELECT insight.claim）
-- （grade_result 的 SELECT 在 0005 已授给 authz）
GRANT SELECT ON platform.grade_dimension TO starbuddy_authz;
COMMENT ON TABLE platform.review_task IS
  '统一复核队列（0006、0023）。student_id、subject、reasons 是进队时的路由快照，事实以源对象（grade_result、insight.claim 等）为准';

-- 学习服务本冲刺只为判分进队（spec 待人确认 #1；观测与错因的复核随 KGE-04，安全审查 S3）；画像异议不能由它放进来。
-- 判分必须真实存在、学生与学科一致，原因必须是该判分 review_reasons 的子集——既不能凭空造原因，也不会丢掉适配器声明的原因
-- （契约里 spot_check 是合法的判分原因，适配器声明的抽检照常随判分进队；契约审查 A1、B1）。
-- 列级 INSERT：id、状态、时间、领取与结案列都只能取默认值（不能伪造创建时间来排到队首或队尾）。
REVOKE INSERT ON platform.review_task FROM starbuddy_learning;
GRANT INSERT (subject, student_id, source_kind, source_id, reasons) ON platform.review_task TO starbuddy_learning;
DROP POLICY review_service_open ON platform.review_task;
CREATE POLICY review_service_open ON platform.review_task FOR INSERT TO starbuddy_learning
  WITH CHECK (status = 'open' AND resolved_at IS NULL AND resolved_by_account IS NULL
              AND assigned_to IS NULL AND assigned_at IS NULL
              AND NOT ('profile_dispute' = ANY (reasons::text[]))
              AND source_kind = 'grade_result' AND EXISTS (
                     SELECT 1 FROM platform.grade_result g
                      WHERE g.id = source_id AND g.student_id = review_task.student_id AND g.subject = review_task.subject
                        AND review_task.reasons::text[] <@ g.review_reasons::text[]));

-- 审核台不再直接读队列表（表里有 student_id）；只经下面两个读函数（spec 待人确认 #4）
DROP POLICY review_staff_read ON platform.review_task;
REVOKE SELECT ON platform.review_task FROM starbuddy_review;

-- ---------- 当前账号必须是 active staff（内部函数，只给 authz 用） ----------
CREATE FUNCTION platform.review_current_staff() RETURNS uuid
  LANGUAGE plpgsql STABLE SET search_path = pg_catalog, platform, pg_temp
  AS $$
DECLARE v uuid := platform.current_account_id();
BEGIN
  IF v IS NULL OR NOT EXISTS (SELECT 1 FROM platform.account a WHERE a.id = v AND a.kind = 'staff' AND a.status = 'active') THEN
    RAISE EXCEPTION 'only an active staff account can work the review queue' USING ERRCODE = 'insufficient_privilege';
  END IF;
  RETURN v;
END $$;
REVOKE EXECUTE ON FUNCTION platform.review_current_staff() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION platform.review_current_staff() TO starbuddy_authz;

-- 学生的不透明指针：只用来在队列里把同一个学生的任务归在一起，不能反查（spec 设计要点 2）
CREATE FUNCTION platform.review_student_ref(p_student uuid) RETURNS text
  LANGUAGE sql IMMUTABLE STRICT SET search_path = pg_catalog
  AS $$ SELECT 's-' || substr(encode(sha256(convert_to('starbuddy-review:' || p_student::text, 'UTF8')), 'hex'), 1, 12) $$;
REVOKE EXECUTE ON FUNCTION platform.review_student_ref(uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION platform.review_student_ref(uuid) TO starbuddy_authz;

-- ---------- 领取、放回、结案（只授 starbuddy_review） ----------
CREATE FUNCTION platform.take_review_task(p_task uuid) RETURNS void
  LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog, platform, pg_temp
  AS $$
DECLARE v_staff uuid := platform.review_current_staff(); r platform.review_task;
BEGIN
  SELECT * INTO r FROM platform.review_task WHERE id = p_task FOR UPDATE;
  IF NOT FOUND THEN
    RAISE EXCEPTION '复核任务不存在' USING ERRCODE = 'no_data_found';
  ELSIF r.status = 'resolved' THEN
    RAISE EXCEPTION '复核任务已结案' USING ERRCODE = 'check_violation';
  ELSIF r.status = 'in_progress' AND r.assigned_to = v_staff THEN
    RETURN;  -- 重复领取自己的任务：幂等
  ELSIF r.status = 'in_progress' THEN
    RAISE EXCEPTION '复核任务已被其他人领取' USING ERRCODE = 'check_violation';
  END IF;
  UPDATE platform.review_task SET status = 'in_progress', assigned_to = v_staff, assigned_at = now() WHERE id = p_task;
END $$;

CREATE FUNCTION platform.release_review_task(p_task uuid) RETURNS void
  LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog, platform, pg_temp
  AS $$
DECLARE v_staff uuid := platform.review_current_staff();
BEGIN
  UPDATE platform.review_task SET status = 'open', assigned_to = NULL, assigned_at = NULL
   WHERE id = p_task AND status = 'in_progress' AND assigned_to = v_staff;
  IF NOT FOUND THEN
    RAISE EXCEPTION '只能放回自己领取、尚未结案的复核任务' USING ERRCODE = 'check_violation';
  END IF;
END $$;

-- 替换 0006 的结案函数：先领取、只有领取人能结案、结案码在白名单里且适用于这类任务
CREATE OR REPLACE FUNCTION platform.resolve_review_task(p_task uuid, p_resolution_code text) RETURNS void
  LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog, platform, pg_temp
  AS $$
DECLARE v_staff uuid := platform.review_current_staff(); r platform.review_task;
BEGIN
  SELECT * INTO r FROM platform.review_task WHERE id = p_task FOR UPDATE;
  IF NOT FOUND THEN
    RAISE EXCEPTION '复核任务不存在' USING ERRCODE = 'no_data_found';
  ELSIF r.status <> 'in_progress' OR r.assigned_to IS DISTINCT FROM v_staff THEN
    RAISE EXCEPTION '只能给自己领取、尚未结案的复核任务结案' USING ERRCODE = 'check_violation';
  ELSIF NOT EXISTS (SELECT 1 FROM platform.review_resolution_code c
                     WHERE c.code = p_resolution_code AND r.source_kind = ANY (c.applies_to)) THEN
    RAISE EXCEPTION '结案码不在白名单里，或不适用于这类任务' USING ERRCODE = 'invalid_parameter_value';
  END IF;
  UPDATE platform.review_task
     SET status = 'resolved', resolved_at = now(), resolved_by_account = v_staff, resolution_code = p_resolution_code
   WHERE id = p_task;
END $$;

-- ---------- 审核台的两个读函数（只授 starbuddy_review） ----------
-- 队列：按学科、状态、原因筛，按 (创建时间, id) 倒序，(p_before, p_before_id) 作游标翻页（同一刻批量进队的抽检任务不会被跳过，
-- 安全审查 S2）；不返回 student_id、领取人 ID
CREATE FUNCTION platform.review_queue(p_subject platform.subject, p_status text, p_reason text, p_limit integer,
                                      p_before timestamptz, p_before_id uuid)
  RETURNS TABLE (id uuid, subject platform.subject, student_ref text, source_kind text, source_id uuid, reasons text[],
                 status text, created_at timestamptz, assigned boolean, assigned_to_me boolean, assigned_at timestamptz,
                 resolution_code text, resolved_at timestamptz)
  LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = pg_catalog, platform, pg_temp
  AS $$
DECLARE v_staff uuid := platform.review_current_staff();
BEGIN
  IF p_status IS NOT NULL AND p_status NOT IN ('open', 'in_progress', 'resolved') THEN
    RAISE EXCEPTION 'unknown status' USING ERRCODE = 'invalid_parameter_value';
  END IF;
  IF p_limit IS NULL OR p_limit < 1 OR p_limit > 200 THEN
    RAISE EXCEPTION 'limit must be 1..200' USING ERRCODE = 'invalid_parameter_value';
  END IF;
  RETURN QUERY
    SELECT t.id, t.subject, platform.review_student_ref(t.student_id), t.source_kind, t.source_id, t.reasons::text[],
           t.status, t.created_at, t.assigned_to IS NOT NULL, t.assigned_to IS NOT DISTINCT FROM v_staff AND t.assigned_to IS NOT NULL,
           t.assigned_at, t.resolution_code, t.resolved_at
      FROM platform.review_task t
     WHERE (p_subject IS NULL OR t.subject = p_subject)
       AND (p_status IS NULL OR t.status = p_status)
       AND (p_reason IS NULL OR p_reason = ANY (t.reasons::text[]))
       AND (p_before IS NULL OR (t.created_at, t.id) < (p_before, coalesce(p_before_id, 'ffffffff-ffff-ffff-ffff-ffffffffffff'::uuid)))
     ORDER BY t.created_at DESC, t.id DESC
     LIMIT p_limit;
END $$;

-- 详情：任务本身 + 被复核对象的结构化字段（判分：结论、总分、置信度、版本、证据 ID、逐维分；判断：模板、状态、置信度、证据 ID）。
-- 不返回作答原文、自由文本、判断的 value（spec 待人确认 #4）
CREATE FUNCTION platform.review_task_detail(p_task uuid) RETURNS jsonb
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
  ELSIF r.source_kind = 'claim' THEN
    SELECT jsonb_build_object(
             'template_key', c.template_key, 'template_version', c.template_version, 'target_type', c.target_type,
             'status', c.status, 'confidence', c.confidence, 'source_type', c.source_type, 'model_version', c.model_version,
             'evidence_ids', c.evidence_ids, 'created_at', c.created_at)
      INTO v_source FROM insight.claim c WHERE c.id = r.source_id;
  END IF;
  RETURN jsonb_build_object(
    'id', r.id, 'subject', r.subject, 'student_ref', platform.review_student_ref(r.student_id), 'source_kind', r.source_kind,
    'source_id', r.source_id, 'reasons', r.reasons::text[], 'status', r.status, 'created_at', r.created_at,
    'assigned', r.assigned_to IS NOT NULL, 'assigned_to_me', r.assigned_to IS NOT NULL AND r.assigned_to = v_staff,
    'assigned_at', r.assigned_at, 'resolution_code', r.resolution_code, 'resolved_at', r.resolved_at,
    'resolved_by_me', r.resolved_by_account IS NOT NULL AND r.resolved_by_account = v_staff,
    'source', v_source);
END $$;

-- ---------- 抽检（只授 starbuddy_scheduler；spec 待人确认 #2） ----------
-- 按 grade_result id 的哈希确定性抽样：同一批判分无论跑几次，抽中的都一样；已有复核任务的不再进队。
-- 只抽没被取代的判分；p_since 最多往前 31 天（限制一次扫描的范围）；比例 (0, 0.5]。返回新进队条数。
CREATE FUNCTION platform.review_sample_spot_checks(p_subject platform.subject, p_since timestamptz, p_ratio numeric)
  RETURNS integer
  LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog, platform, pg_temp
  AS $$
DECLARE v_n integer;
BEGIN
  IF p_subject IS NULL OR p_since IS NULL OR p_ratio IS NULL OR p_ratio <= 0 OR p_ratio > 0.5 THEN
    RAISE EXCEPTION 'subject, since and a ratio in (0, 0.5] are required' USING ERRCODE = 'invalid_parameter_value';
  END IF;
  IF p_since < now() - interval '31 days' OR p_since > now() THEN
    RAISE EXCEPTION 'since must be within the last 31 days' USING ERRCODE = 'invalid_parameter_value';
  END IF;
  INSERT INTO platform.review_task (subject, student_id, source_kind, source_id, reasons)
  SELECT g.subject, g.student_id, 'grade_result', g.id, ARRAY['spot_check']::platform.review_reason[]
    FROM platform.grade_result g
   WHERE g.subject = p_subject AND g.created_at >= p_since AND g.superseded_by IS NULL
     AND ('x' || substr(md5(g.id::text), 1, 8))::bit(32)::bigint < floor(p_ratio * 4294967296)
     AND NOT EXISTS (SELECT 1 FROM platform.review_task t WHERE t.source_kind = 'grade_result' AND t.source_id = g.id)
  ON CONFLICT (source_kind, source_id) DO NOTHING;
  GET DIAGNOSTICS v_n = ROW_COUNT;
  RETURN v_n;
END $$;

-- ---------- 画像异议入队（只授 starbuddy_insight；spec 待人确认 #3） ----------
-- 判断必须存在、属于某一科、不是终态（推翻、撤回）。同一条判断只有一条复核任务：未结案时返回原任务；已结案的再入队拒绝
-- （判断的流转由洞察服务做，规则 11；这里不改判断）。
CREATE FUNCTION platform.enqueue_claim_dispute(p_claim uuid) RETURNS uuid
  LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog, platform, pg_temp
  AS $$
DECLARE c insight.claim; v_task uuid; v_status text;
BEGIN
  SELECT * INTO c FROM insight.claim WHERE id = p_claim;
  IF NOT FOUND OR c.status::text IN ('refuted', 'withdrawn') THEN
    RAISE EXCEPTION 'claim not found or already final' USING ERRCODE = 'invalid_parameter_value';
  END IF;
  IF c.subject IS NULL THEN
    RAISE EXCEPTION 'cross-subject claims cannot be queued yet' USING ERRCODE = 'invalid_parameter_value';
  END IF;
  -- 并发的两次异议：ON CONFLICT 让后到的等先到的提交，再返回同一条任务（安全审查 S5）
  INSERT INTO platform.review_task (subject, student_id, source_kind, source_id, reasons)
  VALUES (c.subject, c.student_id, 'claim', p_claim, ARRAY['profile_dispute']::text[]::platform.review_reason[])
  ON CONFLICT (source_kind, source_id) DO NOTHING
  RETURNING id INTO v_task;
  IF v_task IS NOT NULL THEN
    RETURN v_task;
  END IF;
  SELECT t.id, t.status INTO v_task, v_status FROM platform.review_task t WHERE t.source_kind = 'claim' AND t.source_id = p_claim;
  IF v_status = 'resolved' THEN
    RAISE EXCEPTION 'dispute on this claim was already reviewed' USING ERRCODE = 'check_violation';
  END IF;
  RETURN v_task;
END $$;

DO $$
DECLARE f regprocedure;
BEGIN
  FOREACH f IN ARRAY ARRAY['platform.take_review_task(uuid)', 'platform.release_review_task(uuid)',
                           'platform.review_queue(platform.subject, text, text, integer, timestamptz, uuid)',
                           'platform.review_task_detail(uuid)', 'platform.review_sample_spot_checks(platform.subject, timestamptz, numeric)',
                           'platform.enqueue_claim_dispute(uuid)']::regprocedure[] LOOP
    EXECUTE format('ALTER FUNCTION %s OWNER TO starbuddy_authz', f);
    EXECUTE format('REVOKE EXECUTE ON FUNCTION %s FROM PUBLIC', f);
  END LOOP;
END $$;
GRANT EXECUTE ON FUNCTION platform.take_review_task(uuid), platform.release_review_task(uuid),
  platform.review_queue(platform.subject, text, text, integer, timestamptz, uuid), platform.review_task_detail(uuid) TO starbuddy_review;
GRANT EXECUTE ON FUNCTION platform.review_sample_spot_checks(platform.subject, timestamptz, numeric) TO starbuddy_scheduler;
GRANT EXECUTE ON FUNCTION platform.enqueue_claim_dispute(uuid) TO starbuddy_insight;
