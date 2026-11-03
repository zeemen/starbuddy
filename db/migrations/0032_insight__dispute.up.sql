-- 0032 insight · 异议三路：我其实会（i_know）/ 那次只是失误（one_off_slip）/ 数据有误（data_error）
-- （specs/sie-03-dispute-reconcile/spec.md 设计要点 1，待人确认 #1、#2 按默认；类型码与 packages/contracts/openapi/insight-service.v0.9.yaml 一致）
-- 状态：草稿。只写文件，由人先在副本演练再执行（CLAUDE.md 必停项：迁移、RLS 与权限、画像可见范围、判断的流转）。
-- 依赖 0002（账号、档案、绑定）、0004（作答）、0006 / 0023（复核队列、platform.enqueue_claim_dispute）、0010（判断账本）、0022（考试记录）。
-- 依据：SIE V1.1 第一章（学生和家长能质疑判断）、第八章异议三路、第九章 dispute；ADR 13（草稿）判断只由洞察服务写。
-- 规则：
--   - 新建只经 insight.raise_dispute（属主 authz，只授 starbuddy_insight）：按库里关系再核提出人（学生本人或有效绑定家长）、判断对其可见；
--     来源按提出人记（学生 self、家长 guardian，规则 6）；学生与家长分开限额；
--   - i_know：判断转“待验证”由洞察服务在同一事务里按 0010 的状态机做（原因 dispute，历史带证据 ID 快照）；本迁移不给函数属主改判断的权限，
--     改用延迟约束触发器在提交时核对：新建的 i_know 异议所指判断已不在 hypothesis / established（没转就整笔回滚）；
--     验证任务（KGE-04 第二步的 retest_task，尚未建）先只存引用 1–5 个，不设外键；
--   - one_off_slip：学生本人说“那次只是失误”，只有学生本人能提；不改判断；可指明那次作答（须属于该学生）；来源 self；不设任何标签列；
--   - data_error：必须指明原始事实（作答 / 考试记录 / 上传），进统一复核队列（复用 0023 的 profile_dispute）；
--     以 fact_corrected 结案时，库里须已有该事实的替换或更正行（考试记录被替换、作答的判分被改判），判断须已撤回；
--   - 流转守卫：终态不可改、不可变列、每次修改都是一次流转、结论码只收枚举、结案时判断状态必须对上；
--   - 可见：学生、家长经 BFF 只读自己提出的异议（规则 5：家长须有有效绑定）；验证任务引用、复核任务、事实引用不给前端；
--   - 删除：只随 0035 的删除请求（platform.erasure_in_progress 为真时，守卫的删除分支放行）。
-- 请以单事务执行（psql -1）。

-- ---------- 删除请求执行中（0035 的 platform.execute_due_erasures 用） ----------
-- 只追加、冻结类守卫的删除分支只对“删除请求正在删这个学生”放行：当前角色必须是 starbuddy_eraser（删除函数的属主，只拥有这一个函数；
-- 应用角色都不是它的成员，80 攻击面断言），而且本事务里设了 starbuddy.erasure_student = 该学生（只有删除函数会设）。
CREATE FUNCTION platform.erasure_in_progress(p_student uuid) RETURNS boolean
  LANGUAGE sql STABLE SET search_path = pg_catalog
  AS $$ SELECT current_user = 'starbuddy_eraser' AND p_student IS NOT NULL
               AND coalesce(current_setting('starbuddy.erasure_student', true), '') = p_student::text $$;
REVOKE EXECUTE ON FUNCTION platform.erasure_in_progress(uuid) FROM PUBLIC;

-- ---------- 结论码与流转表（与 0010 的 change_allowed 同一写法） ----------
-- 每行：(类型, 原状态, 新状态, 结论码)。原状态为空表示新建。终态：resolved_supported / resolved_unsupported / fact_corrected / rejected / withdrawn。
-- resolved_supported = 验证支持学生的异议（判断转 refuted）；resolved_unsupported = 判断维持（i_know：验证不支持，判断回到 established；
-- data_error：核实数据无误；one_off_slip：只记下学生的说法，判断不变）。
CREATE FUNCTION insight.dispute_step_allowed(p_kind text, p_from text, p_to text, p_code text)
  RETURNS boolean LANGUAGE sql IMMUTABLE PARALLEL SAFE SET search_path = pg_catalog
  AS $$
    SELECT EXISTS (
      SELECT 1 FROM (VALUES
        ('i_know',       NULL::text,  'open',                 NULL::text),
        ('i_know',       'open',      'verifying',            NULL),
        ('i_know',       'verifying', 'resolved_supported',   'verification_supported'),
        ('i_know',       'verifying', 'resolved_unsupported', 'verification_not_supported'),
        ('i_know',       'open',      'rejected',             'claim_no_longer_visible'),
        ('i_know',       'open',      'withdrawn',            'withdrawn_by_raiser'),
        ('i_know',       'verifying', 'withdrawn',            'withdrawn_by_raiser'),
        ('one_off_slip', NULL,        'open',                 NULL),
        ('one_off_slip', 'open',      'resolved_unsupported', 'self_statement_recorded'),
        ('one_off_slip', 'open',      'rejected',             'claim_no_longer_visible'),
        ('one_off_slip', 'open',      'withdrawn',            'withdrawn_by_raiser'),
        ('data_error',   NULL,        'verifying',            NULL),
        ('data_error',   'verifying', 'fact_corrected',       'fact_corrected'),
        ('data_error',   'verifying', 'resolved_unsupported', 'data_confirmed'),
        ('data_error',   'verifying', 'rejected',             'claim_no_longer_visible'),
        ('data_error',   'verifying', 'withdrawn',            'withdrawn_by_raiser')
      ) t(k, f, s, c)
      WHERE t.k = p_kind AND t.f IS NOT DISTINCT FROM p_from AND t.s = p_to AND t.c IS NOT DISTINCT FROM p_code) $$;
REVOKE EXECUTE ON FUNCTION insight.dispute_step_allowed(text, text, text, text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION insight.dispute_step_allowed(text, text, text, text) TO starbuddy_insight, starbuddy_authz;

-- ---------- 异议 ----------
CREATE TABLE insight.dispute (
  id                      uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  student_id              uuid NOT NULL REFERENCES platform.student_profile (id) ON DELETE CASCADE,
  claim_id                uuid NOT NULL REFERENCES insight.claim (id) ON DELETE CASCADE,
  raised_by               uuid NOT NULL REFERENCES platform.account (id),
  raised_by_role          text NOT NULL CHECK (raised_by_role IN ('student', 'guardian')),
  -- 来源按提出人（规则 6）：学生提的是学生自述，家长提的是家长观察
  source_type             insight.source_type NOT NULL,
  kind                    text NOT NULL CHECK (kind IN ('i_know', 'one_off_slip', 'data_error')),
  status                  text NOT NULL DEFAULT 'open'
                          CHECK (status IN ('open', 'verifying', 'resolved_supported', 'resolved_unsupported', 'fact_corrected',
                                            'rejected', 'withdrawn')),
  -- one_off_slip 指明的那次作答（platform.attempt 是分区表，不设外键；函数核对属于该学生）
  attempt_id              uuid,
  -- data_error 指明的原始事实（契约 fact_ref：attempt | exam_record | upload）；跨表，不设外键，函数核对属于该学生
  fact_kind               text CHECK (fact_kind IN ('attempt', 'exam_record', 'upload')),
  fact_id                 uuid,
  -- KGE-04 第二步的验证任务（platform.retest_task 待建）：先只存引用，建表后加外键（lessons）
  verification_task_refs  uuid[] NOT NULL DEFAULT '{}'
                          CHECK (cardinality(verification_task_refs) <= 5 AND array_position(verification_task_refs, NULL) IS NULL),
  review_task_id          uuid REFERENCES platform.review_task (id),
  -- 结论码只收流转表里的枚举，不存自由文本评价
  result_note_code        text CHECK (result_note_code ~ '^[a-z_]{1,48}$'),
  created_at              timestamptz NOT NULL DEFAULT now(),
  updated_at              timestamptz NOT NULL DEFAULT now(),
  resolved_at             timestamptz,
  CONSTRAINT dispute_source_is_raiser CHECK (source_type::text = CASE raised_by_role WHEN 'student' THEN 'self' ELSE 'guardian' END),
  CONSTRAINT dispute_resolved CHECK ((status IN ('resolved_supported', 'resolved_unsupported', 'fact_corrected', 'rejected', 'withdrawn'))
                                     = (resolved_at IS NOT NULL AND result_note_code IS NOT NULL)),
  -- 验证任务只属于 i_know；进入验证后 1–5 道（含变式，SIE 第八章；契约 verification_item_refs 最多 5）
  CONSTRAINT dispute_refs_i_know CHECK (kind = 'i_know' OR cardinality(verification_task_refs) = 0),
  CONSTRAINT dispute_refs_count CHECK (NOT (kind = 'i_know' AND status IN ('verifying', 'resolved_supported', 'resolved_unsupported'))
                                       OR cardinality(verification_task_refs) BETWEEN 1 AND 5),
  -- 复核任务与原始事实只属于 data_error，而且 data_error 必须带两者
  CONSTRAINT dispute_review_data_error CHECK ((review_task_id IS NOT NULL) = (kind = 'data_error')),
  CONSTRAINT dispute_fact_data_error CHECK ((fact_kind IS NOT NULL AND fact_id IS NOT NULL) = (kind = 'data_error')
                                            AND (fact_kind IS NULL) = (fact_id IS NULL)),
  -- 作答只属于 one_off_slip；one_off_slip 只能由学生本人提出（规则 6）
  CONSTRAINT dispute_attempt_slip CHECK (attempt_id IS NULL OR kind = 'one_off_slip'),
  CONSTRAINT dispute_slip_self CHECK (kind <> 'one_off_slip' OR raised_by_role = 'student')
);
CREATE INDEX dispute_student_idx ON insight.dispute (student_id, created_at);
-- 同一人对同一判断的同一类异议，未结束的只有一条
CREATE UNIQUE INDEX dispute_active_uq ON insight.dispute (claim_id, kind, raised_by) WHERE status IN ('open', 'verifying');
COMMENT ON TABLE insight.dispute IS
  '判断异议（0032，SIE-03）：学生本人或已绑定家长对自己可见的判断提出；只经 insight.raise_dispute 新建，洞察服务推进流转。'
  '不产生任何新的评价性陈述；回应文案走模板（必停项）';

-- ---------- 守卫：流转、终态、不可变列、结案时判断与事实的状态、删除 ----------
CREATE FUNCTION insight.dispute_guard() RETURNS trigger LANGUAGE plpgsql
  SET search_path = pg_catalog, insight, platform, pg_temp
  AS $$
DECLARE v_claim insight.claim_status;
BEGIN
  IF TG_OP = 'DELETE' THEN
    IF platform.erasure_in_progress(OLD.student_id) THEN
      RETURN OLD;
    END IF;
    RAISE EXCEPTION '异议只随删除请求删除' USING ERRCODE = 'insufficient_privilege';
  END IF;
  IF TG_OP = 'TRUNCATE' THEN
    RAISE EXCEPTION '异议不能清空' USING ERRCODE = 'insufficient_privilege';
  END IF;
  IF TG_OP = 'INSERT' THEN
    IF NOT insight.dispute_step_allowed(NEW.kind, NULL, NEW.status, NEW.result_note_code) THEN
      RAISE EXCEPTION '异议新建时的状态不对：% %', NEW.kind, NEW.status USING ERRCODE = 'check_violation';
    END IF;
    NEW.created_at := now();
    NEW.updated_at := now();
    NEW.resolved_at := NULL;
    RETURN NEW;
  END IF;
  IF OLD.status IN ('resolved_supported', 'resolved_unsupported', 'fact_corrected', 'rejected', 'withdrawn') THEN
    RAISE EXCEPTION '已结束的异议（%）不能再修改', OLD.status USING ERRCODE = 'check_violation';
  END IF;
  IF (NEW.id, NEW.student_id, NEW.claim_id, NEW.raised_by, NEW.raised_by_role, NEW.source_type, NEW.kind, NEW.created_at,
      NEW.review_task_id, NEW.attempt_id, NEW.fact_kind, NEW.fact_id)
     IS DISTINCT FROM (OLD.id, OLD.student_id, OLD.claim_id, OLD.raised_by, OLD.raised_by_role, OLD.source_type, OLD.kind, OLD.created_at,
                       OLD.review_task_id, OLD.attempt_id, OLD.fact_kind, OLD.fact_id) THEN
    RAISE EXCEPTION '异议的学生、判断、提出人、来源、类型、事实引用、复核任务与创建时间不能修改' USING ERRCODE = 'check_violation';
  END IF;
  IF NEW.status = OLD.status THEN
    RAISE EXCEPTION '异议的每次修改都必须是一次流转' USING ERRCODE = 'check_violation';
  END IF;
  IF NOT insight.dispute_step_allowed(NEW.kind, OLD.status, NEW.status, NEW.result_note_code) THEN
    RAISE EXCEPTION '不允许的异议流转：% → %（%，结论码 %）', OLD.status, NEW.status, NEW.kind, NEW.result_note_code
      USING ERRCODE = 'check_violation';
  END IF;
  -- 验证任务引用只在进入验证时写一次
  IF NEW.verification_task_refs IS DISTINCT FROM OLD.verification_task_refs
     AND NOT (OLD.status = 'open' AND NEW.status = 'verifying') THEN
    RAISE EXCEPTION '验证任务引用只能在进入验证时写入' USING ERRCODE = 'check_violation';
  END IF;
  -- 结案时判断必须已经按 0010 的状态机流转到位（判断由洞察服务先改，异议后结案，同一事务）
  SELECT c.status INTO v_claim FROM insight.claim c WHERE c.id = NEW.claim_id;
  IF (NEW.kind = 'i_know' AND NEW.status = 'resolved_supported' AND v_claim IS DISTINCT FROM 'refuted')
     OR (NEW.kind = 'i_know' AND NEW.status = 'resolved_unsupported' AND v_claim IS DISTINCT FROM 'established')
     OR (NEW.kind = 'data_error' AND NEW.status = 'fact_corrected' AND v_claim IS DISTINCT FROM 'withdrawn') THEN
    RAISE EXCEPTION '异议结案与判断状态不一致（判断现在是 %）', v_claim USING ERRCODE = 'check_violation';
  END IF;
  -- 事实更正：原始事实须已有替换或更正行（考试记录被替换、作答的判分被改判）；上传管道未建，暂不能以更正结案
  IF NEW.status = 'fact_corrected' AND NOT (CASE NEW.fact_kind
       WHEN 'exam_record' THEN EXISTS (SELECT 1 FROM insight.exam_record e WHERE e.id = NEW.fact_id AND e.superseded_at IS NOT NULL)
       WHEN 'attempt' THEN EXISTS (SELECT 1 FROM platform.grade_result g WHERE g.attempt_id = NEW.fact_id AND g.superseded_by IS NOT NULL)
       ELSE false END) THEN
    RAISE EXCEPTION '原始事实还没有更正行，不能以事实更正结案' USING ERRCODE = 'check_violation';
  END IF;
  NEW.updated_at := now();
  NEW.resolved_at := CASE WHEN NEW.status IN ('resolved_supported', 'resolved_unsupported', 'fact_corrected', 'rejected', 'withdrawn')
                          THEN now() END;
  RETURN NEW;
END $$;
REVOKE EXECUTE ON FUNCTION insight.dispute_guard() FROM PUBLIC;
CREATE TRIGGER dispute_guard BEFORE INSERT OR UPDATE OR DELETE ON insight.dispute
  FOR EACH ROW EXECUTE FUNCTION insight.dispute_guard();
CREATE TRIGGER dispute_truncate BEFORE TRUNCATE ON insight.dispute
  FOR EACH STATEMENT EXECUTE FUNCTION insight.dispute_guard();

-- i_know 提出后，同一事务提交前判断必须已转待验证（或已在验证后的状态）：延迟到提交时核对，没转就整笔回滚
CREATE FUNCTION insight.dispute_i_know_moved() RETURNS trigger LANGUAGE plpgsql
  SET search_path = pg_catalog, insight, pg_temp
  AS $$
BEGIN
  IF NEW.kind = 'i_know' AND EXISTS (SELECT 1 FROM insight.claim c WHERE c.id = NEW.claim_id AND c.status IN ('hypothesis', 'established')) THEN
    RAISE EXCEPTION '“我其实会”异议提交时，判断必须已转为待验证（0010 状态机，原因 dispute）' USING ERRCODE = 'check_violation';
  END IF;
  RETURN NULL;
END $$;
REVOKE EXECUTE ON FUNCTION insight.dispute_i_know_moved() FROM PUBLIC;
CREATE CONSTRAINT TRIGGER dispute_i_know_moved AFTER INSERT ON insight.dispute
  DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION insight.dispute_i_know_moved();

-- ---------- 提出异议（属主 authz；只授洞察服务） ----------
-- p_raised_by 是洞察服务从 BFF 转来的当前账号；这里按库里关系再核一遍（学生本人 → student；有效绑定家长 → guardian；其他 → 42501）。
-- 判断必须对提出人可见（可见范围含 student / guardian、非终态、未过期、模板 approved）；不存在与不可见报同一个错。
-- 同一人对同一判断的同一类未结束异议：返回原异议（幂等）。学生与家长分开限额：各自 24 小时内最多 50 条。
CREATE FUNCTION insight.raise_dispute(p_claim uuid, p_kind text, p_raised_by uuid, p_attempt uuid, p_fact_kind text, p_fact_id uuid)
  RETURNS uuid
  LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog, insight, platform, pg_temp
  AS $$
DECLARE
  c insight.claim;
  v_role text;
  v_id uuid;
  v_task uuid;
BEGIN
  IF p_kind IS NULL OR p_kind NOT IN ('i_know', 'one_off_slip', 'data_error') OR p_raised_by IS NULL THEN
    RAISE EXCEPTION '异议类型或提出人不合要求' USING ERRCODE = 'invalid_parameter_value';
  END IF;
  SELECT * INTO c FROM insight.claim WHERE id = p_claim;
  IF FOUND THEN
    v_role := CASE
      WHEN EXISTS (SELECT 1 FROM platform.student_profile s JOIN platform.account a ON a.id = s.account_id
                    WHERE s.id = c.student_id AND a.id = p_raised_by AND a.kind = 'student' AND a.status = 'active') THEN 'student'
      WHEN EXISTS (SELECT 1 FROM platform.guardian_student_link l JOIN platform.account a ON a.id = l.guardian_account_id
                    WHERE l.student_id = c.student_id AND l.status = 'active' AND a.id = p_raised_by
                      AND a.kind = 'guardian' AND a.status = 'active') THEN 'guardian' END;
  END IF;
  IF v_role IS NULL THEN
    RAISE EXCEPTION 'not allowed' USING ERRCODE = 'insufficient_privilege';
  END IF;
  IF c.status NOT IN ('hypothesis', 'established', 'to_verify') OR c.expires_at <= now()
     OR NOT (CASE v_role WHEN 'student' THEN 'student' ELSE 'guardian' END = ANY (c.visibility))
     OR NOT EXISTS (SELECT 1 FROM insight.claim_template t
                     WHERE t.template_key = c.template_key AND t.version = c.template_version AND t.status = 'approved') THEN
    RAISE EXCEPTION 'not allowed' USING ERRCODE = 'insufficient_privilege';
  END IF;
  IF p_kind = 'one_off_slip' AND v_role <> 'student' THEN
    RAISE EXCEPTION '“那次只是失误”只能由学生本人提出' USING ERRCODE = 'invalid_parameter_value';
  END IF;
  IF (p_attempt IS NOT NULL) AND (p_kind <> 'one_off_slip'
       OR NOT EXISTS (SELECT 1 FROM platform.attempt a WHERE a.attempt_id = p_attempt AND a.student_id = c.student_id)) THEN
    RAISE EXCEPTION '作答只能由“那次只是失误”指明，且必须属于该学生' USING ERRCODE = 'invalid_parameter_value';
  END IF;
  IF (p_kind = 'data_error') <> (p_fact_kind IS NOT NULL AND p_fact_id IS NOT NULL) OR (p_fact_kind IS NULL) <> (p_fact_id IS NULL)
     OR (p_fact_kind IS NOT NULL AND NOT CASE p_fact_kind
           WHEN 'attempt' THEN EXISTS (SELECT 1 FROM platform.attempt a WHERE a.attempt_id = p_fact_id AND a.student_id = c.student_id)
           WHEN 'exam_record' THEN EXISTS (SELECT 1 FROM insight.exam_record e WHERE e.id = p_fact_id AND e.student_id = c.student_id)
           ELSE false END) THEN  -- upload：上传管道（KGE-05）建表后再放开
    RAISE EXCEPTION '“数据有误”必须指明属于该学生的原始事实（作答或考试记录）' USING ERRCODE = 'invalid_parameter_value';
  END IF;

  PERFORM pg_advisory_xact_lock(hashtextextended('insight.dispute:' || c.student_id::text, 0));
  SELECT d.id INTO v_id FROM insight.dispute d
   WHERE d.claim_id = p_claim AND d.kind = p_kind AND d.raised_by = p_raised_by AND d.status IN ('open', 'verifying');
  IF FOUND THEN
    RETURN v_id;
  END IF;
  IF (SELECT count(*) FROM insight.dispute d WHERE d.student_id = c.student_id AND d.raised_by_role = v_role
         AND d.created_at > now() - interval '24 hours') >= 50 THEN
    RAISE EXCEPTION '异议提出过多' USING ERRCODE = 'program_limit_exceeded';
  END IF;

  IF p_kind = 'data_error' THEN
    -- 进统一复核队列（0023）：跨科判断、已复核过的判断由它拒绝
    v_task := platform.enqueue_claim_dispute(p_claim);
  END IF;
  INSERT INTO insight.dispute (student_id, claim_id, raised_by, raised_by_role, source_type, kind, status, attempt_id, fact_kind, fact_id,
                               review_task_id)
  VALUES (c.student_id, p_claim, p_raised_by, v_role, CASE v_role WHEN 'student' THEN 'self' ELSE 'guardian' END::insight.source_type,
          p_kind, CASE WHEN p_kind = 'data_error' THEN 'verifying' ELSE 'open' END, p_attempt, p_fact_kind, p_fact_id, v_task)
  RETURNING id INTO v_id;
  RETURN v_id;
END $$;
ALTER FUNCTION insight.raise_dispute(uuid, text, uuid, uuid, text, uuid) OWNER TO starbuddy_authz;
REVOKE EXECUTE ON FUNCTION insight.raise_dispute(uuid, text, uuid, uuid, text, uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION insight.raise_dispute(uuid, text, uuid, uuid, text, uuid) TO starbuddy_insight;

-- ---------- 权限 ----------
ALTER TABLE insight.dispute ENABLE ROW LEVEL SECURITY;
ALTER TABLE insight.dispute FORCE ROW LEVEL SECURITY;
REVOKE ALL ON insight.dispute FROM PUBLIC;
-- 洞察服务：读全部、推进流转（没有 INSERT、DELETE：新建只经 raise_dispute）
GRANT SELECT ON insight.dispute TO starbuddy_insight;
GRANT UPDATE (status, verification_task_refs, result_note_code) ON insight.dispute TO starbuddy_insight;
CREATE POLICY dispute_insight_read ON insight.dispute FOR SELECT TO starbuddy_insight USING (true);
CREATE POLICY dispute_insight_update ON insight.dispute FOR UPDATE TO starbuddy_insight USING (true) WITH CHECK (true);
-- BFF：只读自己提出的（学生本人、有效绑定家长），不给验证任务引用、复核任务、事实与作答引用
GRANT SELECT (id, student_id, claim_id, raised_by, raised_by_role, kind, status, result_note_code, created_at, resolved_at)
  ON insight.dispute TO starbuddy_bff;
CREATE POLICY dispute_raiser_read ON insight.dispute FOR SELECT TO starbuddy_bff
  USING (raised_by = platform.current_account_id() AND platform.can_read_student(student_id));
-- 函数属主（authz）：新建异议、读模板（判断可见性）
GRANT SELECT, INSERT ON insight.dispute TO starbuddy_authz;
GRANT SELECT ON insight.claim_template TO starbuddy_authz;
