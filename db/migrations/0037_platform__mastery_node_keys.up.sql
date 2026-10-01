-- 0037 platform · 掌握估计改按“知识节点 × 认知层次”记（节点键）；复测任务、学情档案页与档案页阻断记录
-- （specs/kge-04-mastery-step2/spec.md 设计要点 1；设计正文 specs/mastery-estimate-sprint4/spec.md 第 7、9、14、15 节；待人确认 #1、#7、#10 按默认）
-- 状态：草稿，由人执行，先在副本演练（CLAUDE.md 必停项：迁移、RLS 与权限、掌握计算的落库形状、画像可见范围）。只改 platform schema。
-- 依赖 0002（档案、can_read_student）、0005（mastery_estimate 与更正守卫）、0010（insight.claim，档案页引用核对）、
--       0035（个人层表清单与删除执行身份 starbuddy_eraser）。不依赖 kge schema（见下“节点键”说明）。
-- 做法：
--   - platform.mastery_estimate 加节点键 node_id、cognitive_level、map_version_id（三列同空同填）；skill_local_id 改可空；
--     “两种键恰好一种”；节点键的当前记录部分唯一；check_supersede_target 按两种键比较（同学生、同学科、同键），更正链不能跨键。
--     现有 RLS 不改（0005 的 BFF / learning 策略、0028 的洞察只读策略照旧生效，新列随表级授权可读）。
--     旧的 skill_local_id 行不迁移、不删除（待人确认 #10：只剩 3-H 合成种子；生产库不应有这种行，演练时由人核对）。
--   - 节点键不设跨 schema 外键，只做格式 CHECK（与 0027 diagnosis_step.node_id 同一写法），理由：
--       1. kge.knowledge_node 的主键是 (map_version_id, id)，掌握记录要跨地图版本延续（id_migration 改名、合并），外键会把个人层钉死在某一版本上；
--       2. 公共层与个人层分离（规则 13）：个人层不应约束公共层的发布与撤下，公共层的版本治理也不应因个人层引用而受阻；
--       3. run.sh 的 kge 角色断言测试库不执行 0011，迁移不能依赖 kge schema。
--     节点是否存在、层次是否在该节点的 cognitive_levels 里，由学习服务写入前核对（第一步 spec 第 1 节）。
--   - 新表 platform.retest_task（复测与验证任务）、platform.profile_page（学情档案页，每生每科一页）、platform.profile_page_check（编译阻断记录）：
--     ENABLE + FORCE RLS；student_id 外键 ON DELETE CASCADE；starbuddy_learning 读写（不能删）；starbuddy_bff 按 can_read_student
--     只读复测任务与档案页元数据列，档案页陈述只经视图 platform.profile_page_statement_v（按判断前端可见范围过滤、不含待办），阻断记录无权限；
--     其他应用角色无表权限；例外只有 0035 机制需要的两个函数属主：删除执行身份 eraser（DELETE 与学生键列）、导出函数属主 authz（导出白名单列；它同时是档案页陈述视图的属主，读档案页的键与 sections）。
--     不存题目、答案、作答内容与陈述文字：档案页只存判断引用（claim_id、模板键与版本）与固定衔接短语，阻断记录只存编码、引用与短片段。
--   - 个人层表清单（0035）：三张新表按 delete 登记（档案页与阻断记录回执范围 reports，复测任务 inference）；
--     复测任务与档案页进导出包，只导 ID、状态与时间（待人确认 #7：档案页不导渲染文字，也不导引用列表）；
--     mastery_estimate 的导出列补 node_id、cognitive_level。
-- 请以单事务执行（psql -1）。

-- ---------- mastery_estimate：节点键 ----------
ALTER TABLE platform.mastery_estimate
  ADD COLUMN node_id text CONSTRAINT mastery_estimate_node_id_format
    CHECK (node_id ~ '^[A-Z][A-Z0-9]{1,9}(\.[A-Z0-9][A-Z0-9_]{0,31}){1,4}$'),
  ADD COLUMN cognitive_level text CONSTRAINT mastery_estimate_cognitive_level_check
    CHECK (cognitive_level IN ('know', 'understand', 'apply', 'synthesize')),   -- 与 kge.knowledge_node.cognitive_levels 取值一致（0011）
  ADD COLUMN map_version_id uuid;                                              -- 计算时的地图版本（kge.map_version.id；不设外键，理由见文件头）
ALTER TABLE platform.mastery_estimate ALTER COLUMN skill_local_id DROP NOT NULL;
-- 节点键三列同空同填；能力点键与节点键恰好一种
ALTER TABLE platform.mastery_estimate ADD CONSTRAINT mastery_estimate_node_key_complete
  CHECK ((node_id IS NULL) = (cognitive_level IS NULL) AND (node_id IS NULL) = (map_version_id IS NULL));
ALTER TABLE platform.mastery_estimate ADD CONSTRAINT mastery_estimate_one_key
  CHECK ((skill_local_id IS NULL) <> (node_id IS NULL));
-- 每个学生每个单元（学科 × 节点 × 层次）只有一条当前记录
CREATE UNIQUE INDEX mastery_estimate_node_current_uq
  ON platform.mastery_estimate (student_id, subject, node_id, cognitive_level) WHERE is_current;
-- 供复测任务按“同学生、同单元”引用掌握记录（复合外键需要唯一索引；id 本身已唯一）
CREATE UNIQUE INDEX mastery_estimate_unit_ref_uq
  ON platform.mastery_estimate (id, student_id, subject, node_id, cognitive_level);
COMMENT ON COLUMN platform.mastery_estimate.node_id IS
  '节点键（0037，KGE-04 第二步）：kge.knowledge_node.id；与 cognitive_level、map_version_id 同空同填，与 skill_local_id 恰好一种';

-- 更正目标核对：mastery_estimate 分支按两种键比较（每行恰好一种键非空，所以 IS NOT DISTINCT FROM 同时保证“同一种键、同一个值”）。
-- map_version_id 不参与比较：地图版本切换后同一单元的新记录带新版本（第一步 spec 第 11 节）。其余分支与 0005 相同。
CREATE OR REPLACE FUNCTION platform.check_supersede_target() RETURNS trigger
  LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, platform, pg_temp
  AS $$
DECLARE ok boolean;
BEGIN
  IF TG_TABLE_NAME = 'skill_observation' THEN
    SELECT EXISTS (SELECT 1 FROM platform.skill_observation n WHERE n.id = NEW.superseded_by AND n.superseded_by IS NULL
                     AND n.student_id = OLD.student_id AND n.subject = OLD.subject
                     AND n.skill_local_id = OLD.skill_local_id AND n.created_at >= OLD.created_at) INTO ok;
  ELSIF TG_TABLE_NAME = 'error_attribution' THEN
    SELECT EXISTS (SELECT 1 FROM platform.error_attribution n WHERE n.id = NEW.superseded_by AND n.superseded_by IS NULL
                     AND n.student_id = OLD.student_id AND n.subject = OLD.subject
                     AND n.skill_local_id IS NOT DISTINCT FROM OLD.skill_local_id
                     AND n.created_at >= OLD.created_at) INTO ok;
  ELSIF TG_TABLE_NAME = 'mastery_estimate' THEN
    SELECT EXISTS (SELECT 1 FROM platform.mastery_estimate n WHERE n.id = NEW.superseded_by AND n.is_current
                     AND n.student_id = OLD.student_id AND n.subject = OLD.subject
                     AND n.skill_local_id IS NOT DISTINCT FROM OLD.skill_local_id
                     AND n.node_id IS NOT DISTINCT FROM OLD.node_id
                     AND n.cognitive_level IS NOT DISTINCT FROM OLD.cognitive_level
                     AND n.computed_at >= OLD.computed_at) INTO ok;
  ELSIF TG_TABLE_NAME = 'grade_result' THEN
    SELECT EXISTS (SELECT 1 FROM platform.grade_result n WHERE n.id = NEW.superseded_by AND n.superseded_by IS NULL
                     AND n.student_id = OLD.student_id AND n.subject = OLD.subject
                     AND n.attempt_id = OLD.attempt_id AND n.created_at >= OLD.created_at
                     AND (NOT OLD.human_review_required OR n.human_review_required
                          OR EXISTS (SELECT 1 FROM platform.review_task r
                                      JOIN platform.account a ON a.id = r.resolved_by_account AND a.kind = 'staff'
                                      WHERE r.source_kind = 'grade_result' AND r.source_id = OLD.id
                                        AND r.status = 'resolved'))) INTO ok;
  END IF;
  IF NOT coalesce(ok, false) THEN
    RAISE EXCEPTION 'invalid supersede target for % %', TG_TABLE_NAME, OLD.id USING ERRCODE = 'check_violation';
  END IF;
  RETURN NULL;
END $$;

-- ---------- 复测与验证任务（第一步 spec 第 7 节；枚举与 services/learning/learning/mastery/gates.py 一致） ----------
CREATE TABLE platform.retest_task (
  id                        uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  student_id                uuid NOT NULL REFERENCES platform.student_profile (id) ON DELETE CASCADE,
  subject                   platform.subject NOT NULL,
  node_id                   text NOT NULL CHECK (node_id ~ '^[A-Z][A-Z0-9]{1,9}(\.[A-Z0-9][A-Z0-9_]{0,31}){1,4}$'),
  cognitive_level           text NOT NULL CHECK (cognitive_level IN ('know', 'understand', 'apply', 'synthesize')),
  rung                      text CHECK (rung IN ('rung0', 'rung1', 'rung2')),
  reason                    text NOT NULL CHECK (reason IN ('scheduled', 'early', 'decay', 'hint_followup', 'verify_in_platform', 'verify_self_report')),
  due_at                    timestamptz NOT NULL,
  status                    text NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'passed', 'failed', 'cancelled')),
  scheduled_by_estimate_id  uuid,
  closed_at                 timestamptz,
  closing_attempt_id        uuid,            -- platform.attempt.attempt_id（分区表不设外键，与 0027 同；由学习服务核对）
  created_at                timestamptz NOT NULL DEFAULT now(),
  -- 复测轮（scheduled / early）必有轮次；其余原因（衰减、提示后变式、平台内验证、自述验证）不属于任何轮次（gates.Task.rung 为 None）
  CONSTRAINT retest_task_rung_reason CHECK ((reason IN ('scheduled', 'early')) = (rung IS NOT NULL)),
  -- 提前复测只对第三轮（已亮的星，#13）
  CONSTRAINT retest_task_early_rung2 CHECK (reason <> 'early' OR rung = 'rung2'),
  -- 复测轮由写入带 next_retest_at 的掌握记录时建，必须指向那条记录
  CONSTRAINT retest_task_scheduled_by CHECK (reason NOT IN ('scheduled', 'early') OR scheduled_by_estimate_id IS NOT NULL),
  CONSTRAINT retest_task_closed CHECK ((status = 'open') = (closed_at IS NULL)),
  -- 通过或失败记下关闭它的作答；撤销与开着的没有
  CONSTRAINT retest_task_closing_attempt CHECK ((status IN ('passed', 'failed')) = (closing_attempt_id IS NOT NULL)),
  -- 引用的掌握记录必须是同一学生、同一单元（节点键行；旧能力点键行的 node_id 为空，引用不到）
  CONSTRAINT retest_task_estimate_fk FOREIGN KEY (scheduled_by_estimate_id, student_id, subject, node_id, cognitive_level)
    REFERENCES platform.mastery_estimate (id, student_id, subject, node_id, cognitive_level)
);
-- 每个单元每种原因最多一条开着的任务（对账“缺补多撤”的依据）
CREATE UNIQUE INDEX retest_task_open_uq
  ON platform.retest_task (student_id, subject, node_id, cognitive_level, reason) WHERE status = 'open';
CREATE INDEX retest_task_due_idx ON platform.retest_task (student_id, due_at) WHERE status = 'open';
COMMENT ON TABLE platform.retest_task IS
  '复测与验证任务（0037，KGE-04 第二步）：只由学习服务写；不存题目、答案与作答内容；逾期不降级；关闭只能一次';

COMMENT ON COLUMN platform.retest_task.due_at IS
  '到期时间。复测轮（scheduled / early）的到期时间的真相源是所引掌握记录的 next_retest_at，建任务时必须与它相等（守卫核对）';
COMMENT ON COLUMN platform.retest_task.scheduled_by_estimate_id IS
  '建任务时的掌握记录（同学生、同单元）。不随更正链更新：掌握记录被更正后，旧任务由重算撤销或关闭，新的复测轮另建任务';

-- 新任务一律开着、创建时间由库写；只能从 open 关闭一次，关闭时间由库写；任务内容（学生、单元、轮次、原因、到期、引用）不能改。
-- 复测轮（scheduled / early）：到期时间必须等于所引掌握记录的 next_retest_at，轮次必须与该记录的门槛对应
-- （services/learning/learning/mastery/gates.py 的 RUNG_OF_GATE：execute → rung0，stable → rung1，transfer / retain → rung2）。
-- 所引记录不是同学生、同单元的节点键记录时不在这里报，由复合外键 retest_task_estimate_fk 拒绝；没给轮次的由 retest_task_rung_reason 拒绝。
CREATE FUNCTION platform.retest_task_guard() RETURNS trigger LANGUAGE plpgsql
  SET search_path = pg_catalog, platform, pg_temp
  AS $$
DECLARE m record;
BEGIN
  IF TG_OP = 'INSERT' THEN
    IF NEW.status <> 'open' OR NEW.closed_at IS NOT NULL OR NEW.closing_attempt_id IS NOT NULL THEN
      RAISE EXCEPTION '新的复测任务必须是开着的' USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.reason IN ('scheduled', 'early') AND NEW.scheduled_by_estimate_id IS NOT NULL THEN
      SELECT e.state::text AS state, e.next_retest_at INTO m FROM platform.mastery_estimate e
       WHERE e.id = NEW.scheduled_by_estimate_id AND e.student_id = NEW.student_id AND e.subject = NEW.subject
         AND e.node_id = NEW.node_id AND e.cognitive_level = NEW.cognitive_level;
      IF FOUND THEN
        IF m.next_retest_at IS DISTINCT FROM NEW.due_at THEN
          RAISE EXCEPTION '复测轮的到期时间必须等于所引掌握记录的 next_retest_at' USING ERRCODE = 'check_violation';
        END IF;
        IF NEW.rung IS NOT NULL AND NEW.rung IS DISTINCT FROM (CASE m.state WHEN 'execute' THEN 'rung0' WHEN 'stable' THEN 'rung1'
                                                                            WHEN 'transfer' THEN 'rung2' WHEN 'retain' THEN 'rung2' END) THEN
          RAISE EXCEPTION '复测轮的轮次与所引掌握记录的门槛不对应' USING ERRCODE = 'check_violation';
        END IF;
      END IF;
    END IF;
    NEW.created_at := now();
    RETURN NEW;
  END IF;
  IF OLD.status <> 'open' THEN
    RAISE EXCEPTION '已关闭的复测任务不能再修改' USING ERRCODE = 'check_violation';
  END IF;
  IF (to_jsonb(NEW) - ARRAY['status', 'closed_at', 'closing_attempt_id'])
     IS DISTINCT FROM (to_jsonb(OLD) - ARRAY['status', 'closed_at', 'closing_attempt_id']) THEN
    RAISE EXCEPTION '复测任务只能改状态与关闭作答' USING ERRCODE = 'check_violation';
  END IF;
  NEW.closed_at := CASE WHEN NEW.status = 'open' THEN NULL ELSE now() END;
  RETURN NEW;
END $$;
REVOKE EXECUTE ON FUNCTION platform.retest_task_guard() FROM PUBLIC;
CREATE TRIGGER retest_task_guard BEFORE INSERT OR UPDATE ON platform.retest_task
  FOR EACH ROW EXECUTE FUNCTION platform.retest_task_guard();

-- ---------- 学情档案页（第一步 spec 第 14 节；每生每科一页） ----------
-- sections：固定六节，每节 {key, connective, statements, todo}；
--   statements / todo 的每项只有 {claim_id, template_key, template_version}（陈述文字不落库，读取时按判断现状渲染）；
--   todo 只放“待验证”判断（Agent 版待办，不作陈述，规则 11）；connective 是固定衔接短语表里的一条（不含数字，≤ 60 字），
--   降级的纯模板版本（blocked_fallback）没有衔接语。
CREATE FUNCTION platform.valid_profile_sections(p jsonb, p_check_status text) RETURNS boolean
  LANGUAGE sql IMMUTABLE SET search_path = pg_catalog
  AS $$ SELECT jsonb_typeof(p) = 'array' AND jsonb_array_length(p) <= 6 AND p_check_status IN ('passed', 'blocked_fallback')
           AND NOT EXISTS (
             SELECT 1 FROM jsonb_array_elements(p) s
              WHERE jsonb_typeof(s) <> 'object'
                 OR EXISTS (SELECT 1 FROM jsonb_object_keys(s) k WHERE k NOT IN ('key', 'connective', 'statements', 'todo'))
                 OR NOT (s ? 'key' AND s ? 'connective' AND s ? 'statements' AND s ? 'todo')
                 OR jsonb_typeof(s -> 'key') <> 'string'
                 OR (s ->> 'key') NOT IN ('current_stage', 'mastered', 'working_on', 'root_cause', 'recent_progress', 'next_step')
                 OR jsonb_typeof(s -> 'connective') <> 'string'
                 OR char_length(s ->> 'connective') > 60
                 OR (s ->> 'connective') ~ '[0-9０-９]'
                 OR (p_check_status = 'blocked_fallback' AND (s ->> 'connective') <> '')
                 OR jsonb_typeof(s -> 'statements') <> 'array' OR jsonb_array_length(s -> 'statements') > 30
                 OR jsonb_typeof(s -> 'todo') <> 'array' OR jsonb_array_length(s -> 'todo') > 30
                 OR EXISTS (SELECT 1 FROM (SELECT x FROM jsonb_array_elements(s -> 'statements') x
                                           UNION ALL SELECT x FROM jsonb_array_elements(s -> 'todo') x) i
                             WHERE jsonb_typeof(i.x) <> 'object'
                                OR EXISTS (SELECT 1 FROM jsonb_object_keys(i.x) k WHERE k NOT IN ('claim_id', 'template_key', 'template_version'))
                                OR jsonb_typeof(i.x -> 'claim_id') IS DISTINCT FROM 'string'
                                OR (i.x ->> 'claim_id') !~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
                                OR jsonb_typeof(i.x -> 'template_key') IS DISTINCT FROM 'string'
                                OR char_length(i.x ->> 'template_key') NOT BETWEEN 1 AND 64
                                OR jsonb_typeof(i.x -> 'template_version') IS DISTINCT FROM 'number'
                                OR (i.x ->> 'template_version') !~ '^[1-9][0-9]{0,5}$'))
           AND (SELECT count(*) FROM jsonb_array_elements(p) s) = (SELECT count(DISTINCT s ->> 'key') FROM jsonb_array_elements(p) s) $$;

CREATE TABLE platform.profile_page (
  id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  student_id        uuid NOT NULL REFERENCES platform.student_profile (id) ON DELETE CASCADE,
  subject           platform.subject NOT NULL,
  map_version_id    uuid NOT NULL,                              -- 编译时的地图版本（kge.map_version.id；不设外键，理由见文件头）
  sections          jsonb NOT NULL,
  claims_digest     text NOT NULL CHECK (claims_digest ~ '^[0-9a-f]{64}$'),   -- 所引判断 id + updated_at 的 sha256（惰性编译比较用）
  compiled_at       timestamptz NOT NULL DEFAULT now(),
  compiler_version  platform.versioned_name NOT NULL,
  model_version     platform.model_version,                     -- v0 不调模型，为空（第一步 #19）
  check_status      text NOT NULL CHECK (check_status IN ('passed', 'blocked_fallback')),
  UNIQUE (student_id, subject),
  UNIQUE (id, student_id),                                      -- 阻断记录按“同一学生”引用
  CONSTRAINT profile_page_sections CHECK (platform.valid_profile_sections(sections, check_status))
);
COMMENT ON TABLE platform.profile_page IS
  '学情档案页（0037，KGE-04 第二步）：判断账本的可读视图，每生每科一页；只存判断引用与固定衔接短语，陈述文字不落库；只由学习服务写';

-- 编译时间由库写；引用的判断必须是本学生的、同学科（或不分学科）的、模板键与版本对得上；陈述只能引用成立或假设，待办只能引用待验证（规则 11）。
-- 以调用者身份执行：学习服务经 0010 claim_agent_read 只读得到 Agent 可见、未过期、非终态的判断，读不到的判断写不进页。
CREATE FUNCTION platform.profile_page_guard() RETURNS trigger LANGUAGE plpgsql
  SET search_path = pg_catalog, platform, pg_temp
  AS $$
DECLARE v_bad integer;
BEGIN
  IF TG_OP = 'UPDATE' AND (NEW.id, NEW.student_id, NEW.subject) IS DISTINCT FROM (OLD.id, OLD.student_id, OLD.subject) THEN
    RAISE EXCEPTION '档案页的学生与学科不能修改' USING ERRCODE = 'check_violation';
  END IF;
  SELECT count(*) INTO v_bad
    FROM jsonb_array_elements(NEW.sections) s,
         LATERAL (SELECT x, 'statement' AS kind FROM jsonb_array_elements(s -> 'statements') x
                  UNION ALL SELECT x, 'todo' FROM jsonb_array_elements(s -> 'todo') x) i
   WHERE NOT EXISTS (SELECT 1 FROM insight.claim c
                      WHERE c.id = (i.x ->> 'claim_id')::uuid AND c.student_id = NEW.student_id
                        AND (c.subject = NEW.subject OR c.subject IS NULL)
                        AND c.template_key = i.x ->> 'template_key' AND c.template_version = (i.x ->> 'template_version')::integer
                        AND CASE i.kind WHEN 'statement' THEN c.status IN ('hypothesis', 'established') ELSE c.status = 'to_verify' END);
  IF v_bad > 0 THEN
    RAISE EXCEPTION '档案页引用的判断不属于该学生、模板对不上、读不到或状态不合规（% 处）', v_bad USING ERRCODE = 'check_violation';
  END IF;
  NEW.compiled_at := now();
  RETURN NEW;
END $$;
REVOKE EXECUTE ON FUNCTION platform.profile_page_guard() FROM PUBLIC;
CREATE TRIGGER profile_page_guard BEFORE INSERT OR UPDATE ON platform.profile_page
  FOR EACH ROW EXECUTE FUNCTION platform.profile_page_guard();

-- ---------- 档案页阻断记录（一致性检查不过时写一条；只追加） ----------
-- violations：[{code, section, claim_ids, fragment}]，编码与 services/learning/learning/mastery/consistency.py 的 Violation.code 一致；
-- fragment 是命中的词或衔接短语（≤ 60 字，不含数字）；禁用推断命中不存片段（规则 12：命中词不进个人层）
CREATE FUNCTION platform.valid_profile_violations(p jsonb) RETURNS boolean
  LANGUAGE sql IMMUTABLE SET search_path = pg_catalog
  AS $$ SELECT jsonb_typeof(p) = 'array' AND jsonb_array_length(p) BETWEEN 1 AND 60
           AND NOT EXISTS (
             SELECT 1 FROM jsonb_array_elements(p) v
              WHERE jsonb_typeof(v) <> 'object'
                 OR EXISTS (SELECT 1 FROM jsonb_object_keys(v) k WHERE k NOT IN ('code', 'section', 'claim_ids', 'fragment'))
                 OR NOT (v ? 'code' AND v ? 'section' AND v ? 'claim_ids' AND v ? 'fragment')
                 OR jsonb_typeof(v -> 'code') <> 'string'
                 OR (v ->> 'code') NOT IN ('no_claim', 'claim_unreadable', 'claim_status', 'connective_unapproved', 'degree_escalation',
                                           'new_conclusion', 'number_in_connective', 'persona_language', 'forbidden_inference')
                 OR jsonb_typeof(v -> 'section') <> 'string'
                 OR (v ->> 'section') NOT IN ('*', 'current_stage', 'mastered', 'working_on', 'root_cause', 'recent_progress', 'next_step')
                 OR jsonb_typeof(v -> 'fragment') <> 'string' OR char_length(v ->> 'fragment') > 60
                 OR (v ->> 'fragment') ~ '[0-9０-９]'
                 OR ((v ->> 'code') = 'forbidden_inference' AND (v ->> 'fragment') <> '')
                 OR jsonb_typeof(v -> 'claim_ids') <> 'array' OR jsonb_array_length(v -> 'claim_ids') > 30
                 OR EXISTS (SELECT 1 FROM jsonb_array_elements(v -> 'claim_ids') c
                             WHERE jsonb_typeof(c) <> 'string'
                                OR (c #>> '{}') !~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$')) $$;

CREATE TABLE platform.profile_page_check (
  id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  student_id        uuid NOT NULL REFERENCES platform.student_profile (id) ON DELETE CASCADE,
  subject           platform.subject NOT NULL,
  profile_page_id   uuid,                                       -- 降级后发布的那一页（编译失败、页还不存在时为空）
  checked_at        timestamptz NOT NULL DEFAULT now(),
  compiler_version  platform.versioned_name NOT NULL,
  violations        jsonb NOT NULL CONSTRAINT profile_page_check_violations CHECK (platform.valid_profile_violations(violations)),
  CONSTRAINT profile_page_check_page_fk FOREIGN KEY (profile_page_id, student_id)
    REFERENCES platform.profile_page (id, student_id) ON DELETE CASCADE
);
CREATE INDEX profile_page_check_student_idx ON platform.profile_page_check (student_id, subject, checked_at);

-- 阻断记录里的判断引用与档案页同一口径的属主核对（调用者身份）：
--   - 一般编码：每个 claim_id 都必须是学习服务读得到的、本学生的判断；
--   - claim_unreadable：这个判断按定义读不到，库里无法核对存在；只要求它不是学习服务读得到的别的学生的判断。
CREATE FUNCTION platform.profile_page_check_guard() RETURNS trigger LANGUAGE plpgsql
  SET search_path = pg_catalog, platform, pg_temp
  AS $$
DECLARE v_bad integer;
BEGIN
  SELECT count(*) INTO v_bad
    FROM jsonb_array_elements(NEW.violations) v, jsonb_array_elements_text(v -> 'claim_ids') x(claim_id)
   WHERE CASE WHEN v ->> 'code' = 'claim_unreadable'
              THEN EXISTS (SELECT 1 FROM insight.claim c WHERE c.id = x.claim_id::uuid AND c.student_id <> NEW.student_id)
              ELSE NOT EXISTS (SELECT 1 FROM insight.claim c WHERE c.id = x.claim_id::uuid AND c.student_id = NEW.student_id) END;
  IF v_bad > 0 THEN
    RAISE EXCEPTION '阻断记录引用的判断不属于该学生或读不到（% 处）', v_bad USING ERRCODE = 'check_violation';
  END IF;
  NEW.checked_at := now();
  RETURN NEW;
END $$;
REVOKE EXECUTE ON FUNCTION platform.profile_page_check_guard() FROM PUBLIC;
CREATE TRIGGER profile_page_check_guard BEFORE INSERT ON platform.profile_page_check
  FOR EACH ROW EXECUTE FUNCTION platform.profile_page_check_guard();

-- ---------- 档案页的陈述视图（BFF 读取用） ----------
-- BFF 对 profile_page 只有元数据列（读不到 sections：里面有 Agent 版待办与对本人不可见的判断引用）。陈述经这个视图读：
--   - 只展开 statements，todo 一律不暴露；
--   - 行过滤：档案页按 can_read_student（本人、有效绑定家长），判断按 0010 claim_front_read 的同一条件
--     （学生本人只看可见范围含 student 的，有效绑定家长只看含 guardian 的；未过期、模板仍在）；陈述只取成立或假设。
-- 为什么不用 security_invoker：调用者身份的视图要求 BFF 对 sections 列有 SELECT，等于把待办与不可见引用整列交出去。
-- 所以视图属主是 starbuddy_authz（BYPASSRLS），按 security_barrier 防止调用方函数下推，并把上面两条策略的条件写进视图；
-- db/tests/79s 逐账号断言“视图里的判断 = 该账号经 claim_front_read 能读到的判断 ∩ 页里的陈述”，策略与视图分叉即失败。
CREATE VIEW platform.profile_page_statement_v WITH (security_barrier = true) AS
  SELECT p.id AS profile_page_id, p.student_id, p.subject, s.sec ->> 'key' AS section, s.ord AS section_ord, i.ord AS item_ord,
         c.id AS claim_id, c.template_key, c.template_version, c.status::text AS claim_status
    FROM platform.profile_page p
    CROSS JOIN LATERAL jsonb_array_elements(p.sections) WITH ORDINALITY s(sec, ord)
    CROSS JOIN LATERAL jsonb_array_elements(s.sec -> 'statements') WITH ORDINALITY i(item, ord)
    JOIN insight.claim c ON c.id = (i.item ->> 'claim_id')::uuid AND c.student_id = p.student_id
                        AND c.template_key = i.item ->> 'template_key' AND c.template_version = (i.item ->> 'template_version')::integer
   WHERE platform.can_read_student(p.student_id)
     AND c.status IN ('hypothesis', 'established') AND c.expires_at > now()
     AND EXISTS (SELECT 1 FROM insight.claim_template t WHERE t.template_key = c.template_key AND t.version = c.template_version)
     AND ((platform.is_self_student(c.student_id) AND 'student' = ANY (c.visibility))
          OR (platform.is_linked_guardian(c.student_id) AND 'guardian' = ANY (c.visibility)));
ALTER VIEW platform.profile_page_statement_v OWNER TO starbuddy_authz;
COMMENT ON VIEW platform.profile_page_statement_v IS
  '档案页陈述（0037）：BFF 按调用者身份只读成立或假设、对本人可见的判断引用；不含待办；文字由 BFF 按判断模板渲染';
COMMENT ON TABLE platform.profile_page_check IS
  '档案页编译阻断记录（0037）：编译时间、阻断编码、涉及的判断引用与短片段；只追加，只存个人层，只由学习服务写';

REVOKE EXECUTE ON FUNCTION platform.valid_profile_sections(jsonb, text), platform.valid_profile_violations(jsonb) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION platform.valid_profile_sections(jsonb, text), platform.valid_profile_violations(jsonb) TO starbuddy_learning;

-- ---------- 权限与 RLS（第一步 spec 第 15 节） ----------
ALTER TABLE platform.retest_task ENABLE ROW LEVEL SECURITY;
ALTER TABLE platform.retest_task FORCE ROW LEVEL SECURITY;
ALTER TABLE platform.profile_page ENABLE ROW LEVEL SECURITY;
ALTER TABLE platform.profile_page FORCE ROW LEVEL SECURITY;
ALTER TABLE platform.profile_page_check ENABLE ROW LEVEL SECURITY;
ALTER TABLE platform.profile_page_check FORCE ROW LEVEL SECURITY;
REVOKE ALL ON platform.retest_task, platform.profile_page, platform.profile_page_check FROM PUBLIC;
-- 学习服务：读与写（与其他 learning 策略一致，按学生隔离由调用方负责）；没有 DELETE（删除只经删除执行身份）
GRANT SELECT, INSERT ON platform.retest_task, platform.profile_page, platform.profile_page_check TO starbuddy_learning;
GRANT UPDATE (status, closing_attempt_id) ON platform.retest_task TO starbuddy_learning;
GRANT UPDATE (map_version_id, sections, claims_digest, compiler_version, model_version, check_status) ON platform.profile_page TO starbuddy_learning;
CREATE POLICY retest_task_service ON platform.retest_task FOR ALL TO starbuddy_learning USING (true) WITH CHECK (true);
CREATE POLICY profile_page_service ON platform.profile_page FOR ALL TO starbuddy_learning USING (true) WITH CHECK (true);
CREATE POLICY profile_page_check_service ON platform.profile_page_check FOR ALL TO starbuddy_learning USING (true) WITH CHECK (true);
-- BFF：本人与有效绑定家长只读（规则 5）；档案页对学生、家长按判断可见范围的过滤在 BFF（第一步待人确认 #18）
-- 档案页只给元数据列，陈述经 profile_page_statement_v；阻断记录 BFF 无权限（审查意见：界面不展示阻断记录）
GRANT SELECT ON platform.retest_task TO starbuddy_bff;
GRANT SELECT (id, student_id, subject, map_version_id, compiled_at, check_status) ON platform.profile_page TO starbuddy_bff;
GRANT SELECT ON platform.profile_page_statement_v TO starbuddy_bff;
CREATE POLICY retest_task_read ON platform.retest_task FOR SELECT TO starbuddy_bff USING (platform.can_read_student(student_id));
CREATE POLICY profile_page_read ON platform.profile_page FOR SELECT TO starbuddy_bff USING (platform.can_read_student(student_id));
-- 陈述视图的属主（authz）：读档案页的键与 sections（insight.claim 与 claim_template 的读权限 0010、0032 已有）
GRANT SELECT (id, student_id, subject, sections) ON platform.profile_page TO starbuddy_authz;
-- 导出函数属主（authz，0035）：只读学生键与导出白名单列
GRANT SELECT (student_id, subject, node_id, cognitive_level, status, due_at, created_at, closed_at) ON platform.retest_task TO starbuddy_authz;
GRANT SELECT (check_status, compiled_at) ON platform.profile_page TO starbuddy_authz;
-- 删除执行身份（eraser，0035）：清单 delete 表的 DELETE 与学生键列
GRANT DELETE ON platform.retest_task, platform.profile_page, platform.profile_page_check TO starbuddy_eraser;
GRANT SELECT (student_id) ON platform.retest_task, platform.profile_page, platform.profile_page_check TO starbuddy_eraser;

-- ---------- 个人层表清单（0035） ----------
-- 删除顺序：阻断记录 → 档案页 → 复测任务（引用掌握记录，须先于 mastery_estimate 的 45）
INSERT INTO platform.personal_table_registry (nspname, relname, key_path, student_predicate, strategy, exempt_reason, delete_order,
                                              erase_scope, export_topic, export_columns, export_filter) VALUES
  ('platform', 'profile_page_check', 'student_id', 'student_id = $1', 'delete', NULL, 36, 'reports', NULL, NULL, NULL),
  ('platform', 'profile_page', 'student_id', 'student_id = $1', 'delete', NULL, 37, 'reports', 'profile_pages',
   ARRAY['subject', 'check_status', 'compiled_at'], NULL),
  ('platform', 'retest_task', 'student_id', 'student_id = $1', 'delete', NULL, 38, 'inference', 'retest_tasks',
   ARRAY['subject', 'node_id', 'cognitive_level', 'status', 'due_at', 'created_at', 'closed_at'], NULL);
UPDATE platform.personal_table_registry
   SET export_columns = ARRAY['subject', 'skill_local_id', 'node_id', 'cognitive_level', 'state', 'next_retest_at', 'computed_at']
 WHERE nspname = 'platform' AND relname = 'mastery_estimate';
