-- 0053 insight · 家长诉求（specs/family-goals-monthly-plan/spec.md 第 1 步“家长诉求收集”，PLT-14）
-- 状态：草稿。只写文件，由人先在副本演练再执行（CLAUDE.md 必停项：迁移、RLS 与权限、画像可见范围）。请以单事务执行（psql -1）。
-- 依赖 0002（账号、档案、绑定、is_linked_guardian）、0010（insight.source_type）、0013（policy.school）、0022（insight.guard_fact_update 的写法）、
-- 0035（个人层表清单、删除执行身份、guard_fact_update 现行版本）、0045（platform.current_grade）、0050（policy.has_hidden_chars）。
-- 依据：spec“用户确认的口径”1、2（学生看不到；目标分按学校考试分科分）；增补篇“家长自述不进学生模型”；ADR 02“新增个人层事实表放 insight.*”；
--       CLAUDE.md 规则 5（按 guardian_student_link 授权）、规则 6（家长自述单独存、带来源、说法不同两边都存）、规则 12（不推断）。
-- 要点：
--   1. 新表 insight.guardian_goal：一行 = 某位家长对某个孩子的一次完整诉求（关注学科、分科目标分、关注学校、补充说明）。
--      与学生自述（insight.self_report）、客观成绩（insight.exam_record）分表；来源列固定 guardian（家长自述）；
--      两位家长各存各的（当前行按（学生，家长）唯一），不合并、不取平均；
--   2. 保留历史：每次提交是新的一版（version 按（学生，家长）递增），旧版只把 superseded_at 标上，其余列不能改、不能删
--      （复用 insight.guard_fact_update：只追加；删除只随 0035 的删除请求）；
--   3. 字段规则（库里兜底，BFF 先校验一遍）：
--      - 关注学科最多 2 科、不重复，且必须是该学生当前在读（platform.subject_enrollment.status = 'active'）的学科码；
--      - 分科目标分：每科一项 {subject, full_score, target_score}，学校考试口径（月考 / 期中 / 期末），满分 0 < x ≤ 300、目标 0 ≤ y ≤ 满分，
--        最多一位小数，学科同样限在读学科；可以不填；
--      - 关注学校：只能引用 policy.school 里已复核发布（review_status = 'published'）、仍在办（status = 'active'）、来源文件已发布、
--        与学生同一地区的学校；按 platform.current_grade(届别, now()) 判断，只有初三（9）可设，初一初二设了拒绝；
--      - 补充说明：1–200 字，拒绝 Unicode Cc、Cf、Zl、Zp（含换行、零宽与双向控制字符，同 0050 的 policy.has_hidden_chars）。
--        禁用推断词表的过滤在 BFF（命中 422 不存），库里不重复实现词表；
--      - 年级不在 7–9（未入学或已毕业）时整条拒绝（55000 GRADE_OUT_OF_RANGE，与 0051 选科同一个错误名）；
--   4. 可见范围（字段字典标“学生不可见”）：表对任何应用角色都没有表权限，RLS 打开且没有策略；
--      家长只经 SECURITY DEFINER 函数（属主 authz，只授 BFF）按 guardian_student_link 读写自己写的诉求；
--      学生账号调用读函数返回 NULL、写函数 42501（与无权、不存在相同）；复核、定时任务、知识、报表等角色无任何权限；
--      学习服务、洞察服务暂不授权读取（spec 第 2 步再定）；
--   5. 写入量上限：同一家长对同一孩子 24 小时内最多 20 版（54000）；
--   6. 个人层表清单（0035）登记：随删除请求删除（facts），导出只导家长本人写的（主题 guardian_goals）。
--   不计量（诉求收集不是消耗类请求，规则 4 不适用）。

-- ---------- 取值校验 ----------
-- 关注学科：不超过 2 科、不重复、没有 NULL（是否在读由写入函数按当时的选科核对）
CREATE FUNCTION insight.valid_goal_subjects(p platform.subject[]) RETURNS boolean
  LANGUAGE sql IMMUTABLE SET search_path = pg_catalog
  AS $$ SELECT p IS NOT NULL AND cardinality(p) <= 2 AND array_position(p, NULL) IS NULL
           AND (SELECT count(DISTINCT s) FROM unnest(p) s) = cardinality(p) $$;

-- 分科目标分：数组，每项恰好 {subject, full_score, target_score} 三个键；学科是平台学科码、不重复；
-- 满分 0 < x ≤ 300、目标 0 ≤ y ≤ 满分，都是最多一位小数的数（与 insight.exam_record 的 numeric(5,1) 同一口径）。
-- 逐层判断、不依赖 SQL 的求值顺序（OR / AND 不保证短路）：先确认是数组，再逐项确认是对象、键齐全、类型对，最后才取数值；
-- 任何不合要求的输入（标量、非对象元素、非数字分数）一律返回 false，不抛错
CREATE FUNCTION insight.valid_goal_targets(p jsonb) RETURNS boolean
  LANGUAGE plpgsql IMMUTABLE SET search_path = pg_catalog, pg_temp
  AS $$
DECLARE
  e jsonb;
  v_full numeric;
  v_target numeric;
  v_seen text[] := '{}';
BEGIN
  IF p IS NULL OR jsonb_typeof(p) <> 'array' THEN
    RETURN false;
  END IF;
  IF jsonb_array_length(p) > 4 THEN
    RETURN false;
  END IF;
  FOR e IN SELECT x FROM jsonb_array_elements(p) x LOOP
    IF jsonb_typeof(e) <> 'object' THEN
      RETURN false;
    END IF;
    IF (SELECT array_agg(k ORDER BY k) FROM jsonb_object_keys(e) k) IS DISTINCT FROM ARRAY['full_score', 'subject', 'target_score'] THEN
      RETURN false;
    END IF;
    IF jsonb_typeof(e -> 'subject') <> 'string' OR jsonb_typeof(e -> 'full_score') <> 'number' OR jsonb_typeof(e -> 'target_score') <> 'number' THEN
      RETURN false;
    END IF;
    IF NOT (e ->> 'subject') = ANY (enum_range(NULL::platform.subject)::text[]) OR (e ->> 'subject') = ANY (v_seen) THEN
      RETURN false;
    END IF;
    v_seen := v_seen || (e ->> 'subject');
    v_full := (e ->> 'full_score')::numeric;
    v_target := (e ->> 'target_score')::numeric;
    IF NOT (v_full > 0 AND v_full <= 300 AND (v_full * 10) % 1 = 0) THEN
      RETURN false;
    END IF;
    IF NOT (v_target >= 0 AND v_target <= v_full AND (v_target * 10) % 1 = 0) THEN
      RETURN false;
    END IF;
  END LOOP;
  RETURN true;
END $$;

-- 补充说明：1–200 字，不含 Cc、Cf、Zl、Zp。用 plpgsql 在运行时调 0050 的 policy.has_hidden_chars（同一条规则只有一份码位表；
-- plpgsql 函数体不登记依赖，0050 单独回滚时不被本表挡住——回滚期间本函数报错，写入一律失败，fail closed）
CREATE FUNCTION insight.valid_goal_note(p text) RETURNS boolean
  LANGUAGE plpgsql IMMUTABLE SET search_path = pg_catalog, pg_temp
  AS $$ BEGIN RETURN p IS NULL OR (char_length(p) BETWEEN 1 AND 200 AND NOT policy.has_hidden_chars(p)); END $$;

-- ---------- 事实表 ----------
CREATE TABLE insight.guardian_goal (
  id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  student_id           uuid NOT NULL REFERENCES platform.student_profile (id) ON DELETE CASCADE,
  guardian_account_id  uuid NOT NULL REFERENCES platform.account (id),
  version              integer NOT NULL CHECK (version BETWEEN 1 AND 1000000),
  focus_subjects       platform.subject[] NOT NULL DEFAULT '{}' CONSTRAINT guardian_goal_subjects CHECK (insight.valid_goal_subjects(focus_subjects)),
  target_scores        jsonb NOT NULL DEFAULT '[]' CONSTRAINT guardian_goal_targets CHECK (insight.valid_goal_targets(target_scores)),
  school_id            uuid REFERENCES policy.school (id),
  note                 text CONSTRAINT guardian_goal_note CHECK (insight.valid_goal_note(note)),
  source_type          insight.source_type NOT NULL DEFAULT 'guardian' CHECK (source_type = 'guardian'),
  created_at           timestamptz NOT NULL DEFAULT now(),
  superseded_at        timestamptz,
  CONSTRAINT guardian_goal_version UNIQUE (student_id, guardian_account_id, version)
);
-- 每位家长对每个孩子只有一条当前诉求；两位家长各存各的
CREATE UNIQUE INDEX guardian_goal_current ON insight.guardian_goal (student_id, guardian_account_id) WHERE superseded_at IS NULL;
COMMENT ON TABLE insight.guardian_goal IS
  '家长诉求（0053，PLT-14 第 1 步）：家长自述（来源 guardian），学生不可见；只由已绑定家长经 insight.submit_guardian_goal 写入，'
  '每次提交新增一版、旧版标 superseded_at；两位家长各存各的。学习服务、洞察服务暂无读权限（第 2 步再定）';
COMMENT ON COLUMN insight.guardian_goal.target_scores IS
  '分科目标分（学校考试口径：月考 / 期中 / 期末的分科分，不是中考总分）：[{subject, full_score, target_score}]，可为空数组';
COMMENT ON COLUMN insight.guardian_goal.school_id IS '关注学校（policy.school，已复核发布、在办、同地区）；只有初三可设';

-- 只追加：复用 0022 / 0035 的守卫（只能把当前行标为已替换；删除只随删除请求）
CREATE TRIGGER guard_fact_update BEFORE UPDATE OR DELETE ON insight.guardian_goal FOR EACH ROW EXECUTE FUNCTION insight.guard_fact_update();
CREATE TRIGGER guard_fact_truncate BEFORE TRUNCATE ON insight.guardian_goal FOR EACH STATEMENT EXECUTE FUNCTION insight.guard_fact_update();

-- ---------- 权限：应用角色没有任何表权限；只有函数属主 authz 读写，删除执行身份只删、只读学生键 ----------
ALTER TABLE insight.guardian_goal ENABLE ROW LEVEL SECURITY;
ALTER TABLE insight.guardian_goal FORCE ROW LEVEL SECURITY;
-- 没有任何策略：BYPASSRLS 的 authz（函数属主）与 eraser（删除执行）之外谁都读不到
REVOKE ALL ON insight.guardian_goal FROM PUBLIC;
GRANT SELECT, INSERT, UPDATE ON insight.guardian_goal TO starbuddy_authz;
GRANT DELETE ON insight.guardian_goal TO starbuddy_eraser;
GRANT SELECT (student_id) ON insight.guardian_goal TO starbuddy_eraser;

-- ---------- 写入（属主 authz；只授 BFF，BFF 以当前账号调用） ----------
CREATE FUNCTION insight.submit_guardian_goal(p_student uuid, p_focus_subjects platform.subject[], p_target_scores jsonb, p_school uuid,
                                             p_note text) RETURNS jsonb
  LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog, insight, platform, pg_temp
  AS $$
DECLARE
  v_me uuid := platform.current_account_id();
  v_cohort smallint;
  v_region text;
  v_erased timestamptz;
  v_grade smallint;
  v_version integer;
  v_id uuid;
BEGIN
  -- 授权先于一切取值核对：不是有效绑定家长（含学生账号、别家家长、未登录）与学生不存在同一个错误，不泄露存在性
  IF p_student IS NULL OR NOT platform.is_linked_guardian(p_student) THEN
    RAISE EXCEPTION 'not allowed' USING ERRCODE = 'insufficient_privilege';
  END IF;
  -- 同一家长对同一孩子的提交串行（版本号、当前行、写入量计数不并发）
  PERFORM pg_advisory_xact_lock(hashtextextended('insight.guardian_goal:' || p_student::text || ':' || v_me::text, 0));
  -- 档案行 FOR SHARE：与并发的届别更正（0045 对档案行 FOR UPDATE）串行，不按更正前的届别放行学校
  SELECT s.exam_cohort, s.region, s.erased_at INTO v_cohort, v_region, v_erased FROM platform.student_profile s WHERE s.id = p_student FOR SHARE;
  IF v_erased IS NOT NULL THEN
    RAISE EXCEPTION 'not allowed' USING ERRCODE = 'insufficient_privilege';  -- 已删除的档案与未授权同一个错误（同 0051）
  END IF;
  v_grade := platform.current_grade(v_cohort, now());
  IF v_grade IS NULL THEN
    RAISE EXCEPTION 'GRADE_OUT_OF_RANGE' USING ERRCODE = 'object_not_in_prerequisite_state';
  END IF;
  -- 取值形状
  IF NOT insight.valid_goal_subjects(p_focus_subjects) OR NOT insight.valid_goal_targets(p_target_scores) OR NOT insight.valid_goal_note(p_note) THEN
    RAISE EXCEPTION '诉求取值不合要求' USING ERRCODE = 'invalid_parameter_value';
  END IF;
  -- 关注学科与目标分的学科都必须在读
  IF EXISTS (SELECT 1 FROM (SELECT unnest(p_focus_subjects)::text AS s
                            UNION ALL SELECT e ->> 'subject' FROM jsonb_array_elements(p_target_scores) e) x
              WHERE NOT EXISTS (SELECT 1 FROM platform.subject_enrollment n
                                 WHERE n.student_id = p_student AND n.subject::text = x.s AND n.status = 'active')) THEN
    RAISE EXCEPTION 'SUBJECT_NOT_ENROLLED' USING ERRCODE = 'invalid_parameter_value';
  END IF;
  -- 关注学校：只有初三可设；只能引用已复核发布、在办、来源文件已发布、同地区的学校（不存在与未发布同一个错误）
  IF p_school IS NOT NULL THEN
    IF v_grade <> 9 THEN
      RAISE EXCEPTION 'SCHOOL_NOT_OPEN_FOR_GRADE' USING ERRCODE = 'object_not_in_prerequisite_state';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM policy.school h JOIN policy.source_document d ON d.id = h.source_document_id
                    WHERE h.id = p_school AND h.review_status = 'published' AND h.status = 'active'
                      AND d.status = 'published' AND h.city::text = v_region) THEN
      RAISE EXCEPTION 'SCHOOL_NOT_AVAILABLE' USING ERRCODE = 'invalid_parameter_value';
    END IF;
  END IF;
  -- 写入量上限：同一家长对同一孩子 24 小时内最多 20 版
  IF (SELECT count(*) FROM insight.guardian_goal g WHERE g.student_id = p_student AND g.guardian_account_id = v_me
         AND g.created_at > now() - interval '24 hours') >= 20 THEN
    RAISE EXCEPTION '诉求提交过多' USING ERRCODE = 'program_limit_exceeded';
  END IF;
  SELECT coalesce(max(g.version), 0) + 1 INTO v_version FROM insight.guardian_goal g
   WHERE g.student_id = p_student AND g.guardian_account_id = v_me;
  UPDATE insight.guardian_goal SET superseded_at = now()
   WHERE student_id = p_student AND guardian_account_id = v_me AND superseded_at IS NULL;
  INSERT INTO insight.guardian_goal (student_id, guardian_account_id, version, focus_subjects, target_scores, school_id, note)
  VALUES (p_student, v_me, v_version, p_focus_subjects, p_target_scores, p_school, p_note)
  RETURNING id INTO v_id;
  RETURN jsonb_build_object('id', v_id, 'version', v_version);
END $$;

-- ---------- 读取：只给有效绑定家长本人写的诉求（当前版 + 最近 20 版历史）；其他身份 NULL（BFF 按“不存在”处理） ----------
-- 另给表单需要的事实：当前年级能不能设学校、在读学科、可选学校（初三才给）。不返回另一位家长的诉求（各存各的，可见范围待人确认）
CREATE FUNCTION insight.guardian_goal_view(p_student uuid) RETURNS jsonb
  LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = pg_catalog, insight, platform, pg_temp
  AS $$
DECLARE
  v_me uuid := platform.current_account_id();
  v_cohort smallint;
  v_region text;
  v_erased timestamptz;
  v_grade smallint;
BEGIN
  IF p_student IS NULL OR NOT platform.is_linked_guardian(p_student) THEN
    RETURN NULL;
  END IF;
  SELECT s.exam_cohort, s.region, s.erased_at INTO v_cohort, v_region, v_erased FROM platform.student_profile s WHERE s.id = p_student;
  IF v_erased IS NOT NULL THEN
    RETURN NULL;
  END IF;
  v_grade := platform.current_grade(v_cohort, now());
  RETURN jsonb_build_object(
    'grade', v_grade,
    'can_set_school', coalesce(v_grade = 9, false),
    'enrolled_subjects', (SELECT coalesce(jsonb_agg(n.subject ORDER BY n.subject), '[]') FROM platform.subject_enrollment n
                           WHERE n.student_id = p_student AND n.status = 'active'),
    -- 可选学校（只在初三时给）：与写入函数同一口径——已复核发布、在办、来源文件已发布、同地区；只给 ID 与公开名称
    'school_options', CASE WHEN v_grade = 9 THEN
                        (SELECT coalesce(jsonb_agg(jsonb_build_object('id', h.id, 'public_name', h.public_name) ORDER BY h.public_name, h.id), '[]')
                           FROM (SELECT h.id, h.public_name FROM policy.school h JOIN policy.source_document d ON d.id = h.source_document_id
                                  WHERE h.review_status = 'published' AND h.status = 'active' AND d.status = 'published' AND h.city::text = v_region
                                  ORDER BY h.public_name, h.id LIMIT 300) h)
                      ELSE '[]'::jsonb END,
    'current', (SELECT jsonb_build_object('version', g.version, 'focus_subjects', to_jsonb(g.focus_subjects), 'target_scores', g.target_scores,
                                          'school', CASE WHEN g.school_id IS NULL THEN NULL ELSE jsonb_build_object('id', g.school_id,
                                            -- 名称与写入、可选名单同一口径：已复核发布、在办、来源文件已发布；之后停办或来源撤下时名称为 null
                                            'public_name', (SELECT h.public_name FROM policy.school h JOIN policy.source_document d ON d.id = h.source_document_id
                                                             WHERE h.id = g.school_id AND h.review_status = 'published' AND h.status = 'active'
                                                               AND d.status = 'published')) END,
                                          'note', g.note, 'source_type', g.source_type, 'created_at', g.created_at)
                  FROM insight.guardian_goal g
                 WHERE g.student_id = p_student AND g.guardian_account_id = v_me AND g.superseded_at IS NULL),
    'history', (SELECT coalesce(jsonb_agg(jsonb_build_object('version', x.version, 'focus_subjects', to_jsonb(x.focus_subjects),
                                                             'target_scores', x.target_scores, 'school_id', x.school_id, 'note', x.note,
                                                             'created_at', x.created_at, 'superseded_at', x.superseded_at)
                                          ORDER BY x.version DESC), '[]')
                  FROM (SELECT g.* FROM insight.guardian_goal g
                         WHERE g.student_id = p_student AND g.guardian_account_id = v_me AND g.superseded_at IS NOT NULL
                         ORDER BY g.version DESC LIMIT 20) x));
END $$;

ALTER FUNCTION insight.submit_guardian_goal(uuid, platform.subject[], jsonb, uuid, text) OWNER TO starbuddy_authz;
ALTER FUNCTION insight.guardian_goal_view(uuid) OWNER TO starbuddy_authz;
REVOKE EXECUTE ON FUNCTION insight.submit_guardian_goal(uuid, platform.subject[], jsonb, uuid, text), insight.guardian_goal_view(uuid),
  insight.valid_goal_subjects(platform.subject[]), insight.valid_goal_targets(jsonb), insight.valid_goal_note(text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION insight.submit_guardian_goal(uuid, platform.subject[], jsonb, uuid, text), insight.guardian_goal_view(uuid)
  TO starbuddy_bff;
-- 函数属主与表约束要用的内部函数
GRANT EXECUTE ON FUNCTION insight.valid_goal_subjects(platform.subject[]), insight.valid_goal_targets(jsonb), insight.valid_goal_note(text)
  TO starbuddy_authz;
COMMENT ON FUNCTION insight.submit_guardian_goal(uuid, platform.subject[], jsonb, uuid, text) IS
  '提交家长诉求（0053）：只允许有效绑定家长；关注学科与目标分限在读学科，学校只有初三可设且须已发布；每次新增一版';
COMMENT ON FUNCTION insight.guardian_goal_view(uuid) IS
  '家长读自己写的诉求（0053）：当前版与最近 20 版历史、能否设学校、在读学科；学生、别家家长、未绑定与不存在一律 NULL';

-- ---------- 个人层表清单（0035）：随删除请求删除；导出只导家长本人写的 ----------
INSERT INTO platform.personal_table_registry (nspname, relname, key_path, student_predicate, strategy, exempt_reason, delete_order,
                                              erase_scope, export_topic, export_columns, export_filter)
VALUES ('insight', 'guardian_goal', 'student_id', 'student_id = $1', 'delete', NULL, 36, 'facts', 'guardian_goals',
        ARRAY['focus_subjects', 'target_scores', 'school_id', 'note', 'source_type', 'created_at', 'superseded_at'],
        'guardian_account_id = platform.current_account_id()');
