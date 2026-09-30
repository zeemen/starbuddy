-- 0035 platform · 个人层表清单、家长删除请求（冷静期）与导出（specs/sie-03-dispute-reconcile/spec.md 设计要点 4，待人确认 #3、#4、#5 按默认）
-- 状态：草稿。只写文件，由人先在副本演练再执行（CLAUDE.md 必停项：迁移、RLS 与权限、数据库角色、删除的合规时限与冷静期、画像可见范围）。
-- 依赖 0002 … 0034（清单登记全部带学生键的表）与集群角色 starbuddy_eraser（db/bootstrap/roles.sql，本任务新增）。
-- 依据：SIE V1.1 第九章接口 insight.export / insight.erase、指标“家长删除请求生效；删除覆盖个人层全部表与档案页”；CLAUDE.md 规则 5、6、13；
--       契约 insight-service.v0.9.yaml 的 export（任务、下载凭据）与 erase（回执 scopes），差异见 lessons。
-- 做法：
--   - 个人层表清单 platform.personal_table_registry：逐表写学生键谓词（$1 = 学生档案 ID）、策略（delete / exempt + 理由）、删除顺序、
--     回执范围（erase_scope，对应契约 scopes）、导出主题键（export_topic，导出包用它，不暴露 schema.table）与导出列白名单、行过滤。
--     清单只能由迁移（人）写。db/tests/79p 自动扫描全库，断言与清单一致：新增表不登记，测试就失败；
--   - 删除请求 platform.erasure_request：有效绑定家长发起 → 发起人 24 小时内二次确认 → 冷静期 7 天（可撤回）→
--     starbuddy_scheduler 调 platform.execute_due_erasures，在一个事务里：锁住学生档案行（挡住并发写入）→ 按清单删除该学生全部 delete 行
--     （含推翻与撤回的判断，待人确认 #4）→ 逐表复核为零 → 停用学生账号、档案标记已删除 → 写回执（表名与行数、scopes，不含被删内容）；
--     执行时发起人已不是有效绑定家长则作废；
--   - 执行身份：删除函数的属主是专用角色 starbuddy_eraser（BYPASSRLS、NOLOGIN、没有成员），只拥有这一个函数；清单里 33 张表的 DELETE
--     只授它。只追加与冻结类守卫的删除分支只在 platform.erasure_in_progress（0032）为真时放行：当前角色是 eraser 且本事务变量等于该行学生；
--     0022 的两个守卫在这里改成同样写法；判断历史（0010）随判断级联删除，不用改；
--   - 导出 platform.export_student_data（属主 authz，只授 BFF）：有效绑定家长按学生导出，建一条导出任务 platform.export_job
--     （状态、一次性下载凭据的摘要、24 小时失效）；导出包按主题键组织，只含清单里有列白名单的表与白名单列，不含别的学生、审核员身份、
--     推断内部参数、学生自述与只给 Agent 的记录（互不可见，待人确认 #5）；判断只导出对家长可见、模板已审核、非终态、未过期的；
--   - 公共层（kge.*、policy.*）没有学生键，不在清单里，不受删除影响（规则 13）；档案页（KGE-04 第二步）建表时须登记进清单。
-- 请以单事务执行（psql -1）。

-- ---------- 角色：删除执行身份（由人在 db/bootstrap/roles.sql 创建；这里只断言属性安全） ----------
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'starbuddy_eraser') THEN
    RAISE EXCEPTION '角色 starbuddy_eraser 不存在：请先执行 db/bootstrap/roles.sql';
  END IF;
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'starbuddy_eraser'
              AND (rolsuper OR rolcanlogin OR rolcreaterole OR rolcreatedb OR rolreplication OR NOT rolbypassrls)) THEN
    RAISE EXCEPTION 'starbuddy_eraser 必须是 NOLOGIN、非 SUPERUSER、BYPASSRLS，且不能建角色、建库、复制';
  END IF;
  IF EXISTS (SELECT 1 FROM pg_auth_members WHERE roleid = 'starbuddy_eraser'::regrole) THEN
    RAISE EXCEPTION 'starbuddy_eraser 不能有任何成员（删除守卫的 current_user 判断依赖这一点）';
  END IF;
  IF EXISTS (SELECT 1 FROM pg_auth_members WHERE member = 'starbuddy_eraser'::regrole) THEN
    RAISE EXCEPTION 'starbuddy_eraser 不能是任何角色的成员';
  END IF;
END $$;
GRANT USAGE ON SCHEMA platform, insight TO starbuddy_eraser;

-- ---------- 档案“已删除”标记（删除执行后置位；账号同时停用） ----------
ALTER TABLE platform.student_profile ADD COLUMN erased_at timestamptz;
COMMENT ON COLUMN platform.student_profile.erased_at IS '删除请求执行时间（0035）：个人层数据已按清单删除，学生账号已停用';

-- ---------- 个人层表清单 ----------
CREATE TABLE platform.personal_table_registry (
  nspname            name NOT NULL,
  relname            name NOT NULL,
  key_path           text NOT NULL CHECK (char_length(key_path) BETWEEN 1 AND 200),         -- 人读：这张表经什么关联到学生
  student_predicate  text NOT NULL CHECK (position('$1' IN student_predicate) > 0 AND char_length(student_predicate) <= 400),
  strategy           text NOT NULL CHECK (strategy IN ('delete', 'exempt')),
  exempt_reason      text CHECK (char_length(exempt_reason) BETWEEN 1 AND 300),
  delete_order       smallint CHECK (delete_order BETWEEN 1 AND 999),
  -- 回执范围（契约 InsightEraseResponse.scopes）
  erase_scope        text CHECK (erase_scope IN ('claim_ledger', 'facts', 'evidence', 'inference', 'reports', 'uploads')),
  -- 导出：主题键（导出包里的稳定名字）、列白名单（小写标识符）、行过滤（如只导出本人写的）
  export_topic       text UNIQUE CHECK (export_topic ~ '^[a-z][a-z_]{1,39}$'),
  export_columns     text[] CHECK (export_columns IS NULL OR (cardinality(export_columns) BETWEEN 1 AND 32 AND array_position(export_columns, NULL) IS NULL
                                   AND array_to_string(export_columns, ',') ~ '^[a-z_][a-z0-9_]{0,62}(,[a-z_][a-z0-9_]{0,62})*$')),
  export_filter      text CHECK (char_length(export_filter) <= 400),
  PRIMARY KEY (nspname, relname),
  CONSTRAINT registry_exempt_reason CHECK ((strategy = 'exempt') = (exempt_reason IS NOT NULL)),
  CONSTRAINT registry_delete_order CHECK ((strategy = 'delete') = (delete_order IS NOT NULL AND erase_scope IS NOT NULL)),
  CONSTRAINT registry_export_pair CHECK ((export_topic IS NULL) = (export_columns IS NULL)),
  CONSTRAINT registry_filter_needs_columns CHECK (export_filter IS NULL OR export_columns IS NOT NULL)
);
COMMENT ON TABLE platform.personal_table_registry IS
  '个人层表清单（0035，SIE-03）：删除与导出都按它执行；只由迁移写。db/tests/79p 断言它与库里全部带学生键的表一致';

-- 学生键谓词：S = 本表 student_id；A = 学生账号
INSERT INTO platform.personal_table_registry (nspname, relname, key_path, student_predicate, strategy, exempt_reason, delete_order,
                                              erase_scope, export_topic, export_columns, export_filter) VALUES
  -- ===== 洞察层（先删引用判断与考试记录的表） =====
  ('insight', 'exam_reconciliation', 'student_id', 'student_id = $1', 'delete', NULL, 10, 'reports', NULL, NULL, NULL),
  ('insight', 'exam_prediction', 'student_id', 'student_id = $1', 'delete', NULL, 11, 'inference', NULL, NULL, NULL),
  ('insight', 'dispute', 'student_id', 'student_id = $1', 'delete', NULL, 13, 'claim_ledger', 'disputes',
   ARRAY['kind', 'status', 'result_note_code', 'created_at', 'resolved_at'], 'raised_by = platform.current_account_id()'),
  ('insight', 'intervention_log', 'student_id', 'student_id = $1', 'delete', NULL, 14, 'claim_ledger', NULL, NULL, NULL),
  ('insight', 'claim', 'student_id（含推翻、撤回的判断，待人确认 #4）', 'student_id = $1', 'delete', NULL, 20, 'claim_ledger', 'statements',
   ARRAY['subject', 'target_type', 'target_id', 'template_key', 'template_version', 'value', 'status', 'created_at', 'expires_at'],
   '''guardian'' = ANY (visibility) AND status IN (''hypothesis'', ''established'', ''to_verify'') AND expires_at > now()'
   || ' AND EXISTS (SELECT 1 FROM insight.claim_template t WHERE t.template_key = claim.template_key AND t.version = claim.template_version'
   || ' AND t.status = ''approved'')'),
  ('insight', 'claim_history', 'claim_id → insight.claim.student_id（随判断级联删除）',
   'claim_id IN (SELECT c.id FROM insight.claim c WHERE c.student_id = $1)', 'delete', NULL, 21, 'claim_ledger', NULL, NULL, NULL),
  ('insight', 'behavior_event', 'student_id', 'student_id = $1', 'delete', NULL, 30, 'facts', NULL, NULL, NULL),
  ('insight', 'confidence_probe', 'student_id', 'student_id = $1', 'delete', NULL, 31, 'facts', NULL, NULL, NULL),
  ('insight', 'self_report', 'student_id', 'student_id = $1', 'delete', NULL, 32, 'facts', NULL, NULL, NULL),
  ('insight', 'guardian_report', 'student_id', 'student_id = $1', 'delete', NULL, 33, 'facts', 'guardian_observations',
   ARRAY['field_id', 'value', 'created_at', 'superseded_at'], 'guardian_account_id = platform.current_account_id()'),
  ('insight', 'onboarding_progress', 'student_id', 'student_id = $1', 'delete', NULL, 34, 'facts', NULL, NULL, NULL),
  ('insight', 'exam_record', 'student_id', 'student_id = $1', 'delete', NULL, 35, 'facts', 'exams',
   ARRAY['subject', 'exam_type', 'exam_date', 'full_score', 'raw_score', 'scope_node_ids', 'entered_by_role', 'created_at', 'superseded_at'], NULL),
  -- ===== 平台个人层 =====
  ('platform', 'review_task', 'student_id', 'student_id = $1', 'delete', NULL, 40, 'evidence', NULL, NULL, NULL),
  ('platform', 'diagnosis_step', 'session_id → platform.diagnosis_session.student_id',
   'session_id IN (SELECT s.id FROM platform.diagnosis_session s WHERE s.student_id = $1)', 'delete', NULL, 41, 'evidence', NULL, NULL, NULL),
  ('platform', 'diagnosis_session', 'student_id', 'student_id = $1', 'delete', NULL, 42, 'evidence', NULL, NULL, NULL),
  ('platform', 'grade_dimension', 'grade_result_id → platform.grade_result.student_id',
   'grade_result_id IN (SELECT g.id FROM platform.grade_result g WHERE g.student_id = $1)', 'delete', NULL, 43, 'evidence', NULL, NULL, NULL),
  ('platform', 'grade_result', 'student_id', 'student_id = $1', 'delete', NULL, 44, 'evidence', NULL, NULL, NULL),
  ('platform', 'mastery_estimate', 'student_id', 'student_id = $1', 'delete', NULL, 45, 'inference', 'mastery',
   ARRAY['subject', 'skill_local_id', 'state', 'next_retest_at', 'computed_at'], NULL),
  ('platform', 'skill_observation', 'student_id', 'student_id = $1', 'delete', NULL, 46, 'inference', NULL, NULL, NULL),
  ('platform', 'error_attribution', 'student_id', 'student_id = $1', 'delete', NULL, 47, 'inference', NULL, NULL, NULL),
  ('platform', 'attempt', 'student_id', 'student_id = $1', 'delete', NULL, 48, 'evidence', NULL, NULL, NULL),
  ('platform', 'evidence_event', 'student_id', 'student_id = $1', 'delete', NULL, 49, 'evidence', NULL, NULL, NULL),
  ('platform', 'evidence_registry', 'student_id', 'student_id = $1', 'delete', NULL, 50, 'evidence', NULL, NULL, NULL),
  ('platform', 'ingest_request', 'student_id', 'student_id = $1', 'delete', NULL, 51, 'evidence', NULL, NULL, NULL),
  ('platform', 'replay_seal', 'student_id', 'student_id = $1', 'delete', NULL, 52, 'evidence', NULL, NULL, NULL),
  ('platform', 'guardian_report', 'student_id', 'student_id = $1', 'delete', NULL, 60, 'facts', 'guardian_notes',
   ARRAY['topic', 'content', 'created_at'], 'guardian_account_id = platform.current_account_id()'),
  ('platform', 'student_subject_textbook', 'student_id', 'student_id = $1', 'delete', NULL, 61, 'facts', 'textbooks',
   ARRAY['subject', 'textbook_version', 'source_type', 'updated_at'], 'updated_by = platform.current_account_id()'),
  ('platform', 'subject_enrollment', 'student_id', 'student_id = $1', 'delete', NULL, 62, 'facts', 'subjects',
   ARRAY['subject', 'status', 'enrolled_at'], NULL),
  ('platform', 'student_time_guard', 'student_id', 'student_id = $1', 'delete', NULL, 63, 'facts', NULL, NULL, NULL),
  ('platform', 'guardian_invite', 'student_id', 'student_id = $1', 'delete', NULL, 64, 'facts', NULL, NULL, NULL),
  ('platform', 'auth_session', 'account_id → 学生账号（platform.student_profile.account_id）',
   'account_id = (SELECT sp.account_id FROM platform.student_profile sp WHERE sp.id = $1)', 'delete', NULL, 70, 'facts', NULL, NULL, NULL),
  ('platform', 'account_contact', 'account_id → 学生账号',
   'account_id = (SELECT sp.account_id FROM platform.student_profile sp WHERE sp.id = $1)', 'delete', NULL, 71, 'facts', NULL, NULL, NULL),
  -- ===== 豁免（逐张写明理由；都待合规确认） =====
  ('platform', 'student_profile', 'id', 'id = $1', 'exempt',
   '档案骨架（年级、地区、出生年月）：计量订阅经外键引用它；删除执行时标记 erased_at 并停用学生账号，档案注销与去标识随 PLT 账号注销流程（待人确认）',
   NULL, NULL, 'profile', ARRAY['grade', 'region', 'birth_year_month', 'created_at'], NULL),
  ('platform', 'account', '学生账号（platform.student_profile.account_id）',
   'id = (SELECT sp.account_id FROM platform.student_profile sp WHERE sp.id = $1)', 'exempt',
   '账号行只含类型与状态；删除执行时停用，账号注销随 PLT 账号注销流程（待人确认）', NULL, NULL, NULL, NULL, NULL),
  ('platform', 'guardian_student_link', 'student_id', 'student_id = $1', 'exempt',
   '监护授权关系：家长要凭它查看删除回执与撤回；随账号注销流程处理（待人确认）', NULL, NULL, NULL, NULL, NULL),
  ('platform', 'consent_record', 'student_id', 'student_id = $1', 'exempt',
   '监护人同意记录：证明处理未成年人数据时已取得同意，法定留存（期限待合规确认）', NULL, NULL, 'consents',
   ARRAY['consent_type', 'document_version', 'decision', 'decided_at'], NULL),
  ('platform', 'subscription', 'student_id', 'student_id = $1', 'exempt',
   '计费与权益记录：只含档位、周期、状态，不含学习内容；会计留存与去标识方式待合规与计量负责人确认（计量必停项，本迁移不改）', NULL, NULL,
   'subscriptions', ARRAY['tier', 'source', 'status', 'period_start', 'period_end'], NULL),
  ('platform', 'entitlement', 'student_id', 'student_id = $1', 'exempt',
   '计量额度：只含数量；账本只追加（0008），留存方式同订阅（待人确认）', NULL, NULL, NULL, NULL, NULL),
  ('platform', 'usage_reservation', 'student_id', 'student_id = $1', 'exempt',
   '计量预留：只含数量与指纹；账务留存方式同订阅（待人确认）', NULL, NULL, NULL, NULL, NULL),
  ('platform', 'usage_ledger', 'student_id', 'student_id = $1', 'exempt',
   '计量账本：只追加的账务记录（0008），留存方式同订阅（待人确认）', NULL, NULL, NULL, NULL, NULL),
  ('platform', 'practice_log', 'student_id', 'student_id = $1', 'exempt',
   '自练计次（计量，0015 只追加）：只含时间与幂等键；删了会让当天计次清零，留存方式同订阅（待人确认）', NULL, NULL, NULL, NULL, NULL),
  ('platform', 'trial_claim', 'student_id（按家庭一行）', 'student_id = $1', 'exempt',
   '试用领取记录按家庭防重复领取，只含时间；档案删除时学生键置空（0015）', NULL, NULL, NULL, NULL, NULL),
  ('platform', 'trial_claim_phone', 'household_id → 学生所在家庭',
   'household_id = (SELECT sp.household_id FROM platform.student_profile sp WHERE sp.id = $1)', 'exempt',
   '试用防重复：只存家长手机号摘要与家庭，属于家长与家庭，不是学生个人层', NULL, NULL, NULL, NULL, NULL),
  ('platform', 'household', 'id → 学生所在家庭',
   'id = (SELECT sp.household_id FROM platform.student_profile sp WHERE sp.id = $1)', 'exempt',
   '家庭行只含创建人账号；同一家庭可能还有别的孩子', NULL, NULL, NULL, NULL, NULL),
  ('platform', 'pilot_household', 'household_id → 学生所在家庭',
   'household_id = (SELECT sp.household_id FROM platform.student_profile sp WHERE sp.id = $1)', 'exempt',
   '试点名单：只含家庭与批次码，由人工维护', NULL, NULL, NULL, NULL, NULL),
  ('platform', 'staff_credential', 'account_id（只有教研账号）',
   'account_id = (SELECT sp.account_id FROM platform.student_profile sp WHERE sp.id = $1)', 'exempt',
   '只存教研账号的登录凭据，学生账号不会出现', NULL, NULL, NULL, NULL, NULL),
  ('platform', 'staff_session', 'account_id（只有教研账号）',
   'account_id = (SELECT sp.account_id FROM platform.student_profile sp WHERE sp.id = $1)', 'exempt',
   '只存教研账号的会话，学生账号不会出现', NULL, NULL, NULL, NULL, NULL),
  ('platform', 'content_signoff', 'account_id（只有教研账号）',
   'account_id = (SELECT sp.account_id FROM platform.student_profile sp WHERE sp.id = $1)', 'exempt',
   '公共内容的审核签字，只含教研账号', NULL, NULL, NULL, NULL, NULL),
  ('platform', 'map_change_signoff', 'account_id（只有教研账号）',
   'account_id = (SELECT sp.account_id FROM platform.student_profile sp WHERE sp.id = $1)', 'exempt',
   '知识地图变更单签字，只含教研账号', NULL, NULL, NULL, NULL, NULL),
  ('platform', 'tag_candidate_signoff', 'account_id（只有教研账号）',
   'account_id = (SELECT sp.account_id FROM platform.student_profile sp WHERE sp.id = $1)', 'exempt',
   '标签候选签字，只含教研账号', NULL, NULL, NULL, NULL, NULL),
  ('platform', 'erasure_request', 'student_id', 'student_id = $1', 'exempt',
   '删除回执：证明删除已执行，只含表名、行数与范围，不含被删内容', NULL, NULL, NULL, NULL, NULL),
  ('platform', 'export_job', 'student_id', 'student_id = $1', 'exempt',
   '导出任务与审计：谁在何时导出了哪个学生、哪些主题、多少行，只存下载凭据的摘要，不含内容；留存期待合规确认', NULL, NULL, NULL, NULL, NULL);

-- ---------- 删除请求 ----------
CREATE TABLE platform.erasure_request (
  id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  student_id         uuid NOT NULL REFERENCES platform.student_profile (id),
  requested_by       uuid NOT NULL REFERENCES platform.account (id),
  requested_at       timestamptz NOT NULL DEFAULT now(),
  confirmed_at       timestamptz,
  cooling_off_until  timestamptz,
  status             text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'cancelled', 'executed')),
  cancelled_at       timestamptz,
  cancelled_by       uuid REFERENCES platform.account (id),
  cancel_reason      text CHECK (cancel_reason IN ('guardian', 'link_revoked', 'confirm_expired')),
  executed_at        timestamptz,
  -- 回执：{"schema.table": 行数, …}，只含表名与行数；scopes 是契约的回执范围
  receipt            jsonb,
  scopes             text[] CHECK (scopes IS NULL OR (cardinality(scopes) >= 1
                                   AND scopes <@ ARRAY['claim_ledger', 'facts', 'evidence', 'inference', 'reports', 'uploads'])),
  CONSTRAINT erasure_confirm_pair CHECK ((confirmed_at IS NULL) = (cooling_off_until IS NULL)),
  CONSTRAINT erasure_cooling_off CHECK (cooling_off_until IS NULL
                                        OR cooling_off_until - confirmed_at BETWEEN interval '1 day' AND interval '30 days'),
  CONSTRAINT erasure_cancelled CHECK ((status = 'cancelled') = (cancelled_at IS NOT NULL AND cancel_reason IS NOT NULL)
                                      AND (cancelled_by IS NULL OR cancel_reason = 'guardian')
                                      AND (cancel_reason <> 'guardian' OR cancelled_by IS NOT NULL)),
  CONSTRAINT erasure_executed CHECK ((status = 'executed') = (executed_at IS NOT NULL AND receipt IS NOT NULL AND scopes IS NOT NULL)
                                     AND (status <> 'executed' OR (confirmed_at IS NOT NULL AND executed_at >= cooling_off_until))),
  CONSTRAINT erasure_receipt CHECK (receipt IS NULL OR (jsonb_typeof(receipt) = 'object' AND NOT jsonb_path_exists(receipt,
                                      '$.* ? (@.type() != "number")')))
);
CREATE UNIQUE INDEX erasure_request_open_uq ON platform.erasure_request (student_id) WHERE status = 'pending';
COMMENT ON TABLE platform.erasure_request IS
  '家长删除请求（0035，SIE-03）：发起 → 24 小时内二次确认 → 冷静期 7 天（可撤回）→ 定时任务执行；回执只含表名与行数。只经 platform.* 函数写';

-- 终态不可改、不能删；回执的表名必须是清单里的表
CREATE FUNCTION platform.erasure_request_guard() RETURNS trigger LANGUAGE plpgsql
  SET search_path = pg_catalog, platform, pg_temp
  AS $$
BEGIN
  IF TG_OP IN ('DELETE', 'TRUNCATE') THEN
    RAISE EXCEPTION '删除请求与回执不能删除' USING ERRCODE = 'insufficient_privilege';
  END IF;
  IF TG_OP = 'UPDATE' THEN
    IF OLD.status <> 'pending' THEN
      RAISE EXCEPTION '已结束的删除请求不能再修改' USING ERRCODE = 'check_violation';
    END IF;
    IF (NEW.id, NEW.student_id, NEW.requested_by, NEW.requested_at) IS DISTINCT FROM (OLD.id, OLD.student_id, OLD.requested_by, OLD.requested_at)
       OR (OLD.confirmed_at IS NOT NULL AND (NEW.confirmed_at, NEW.cooling_off_until) IS DISTINCT FROM (OLD.confirmed_at, OLD.cooling_off_until)) THEN
      RAISE EXCEPTION '删除请求的学生、发起人与确认时间不能修改' USING ERRCODE = 'check_violation';
    END IF;
  END IF;
  IF NEW.receipt IS NOT NULL AND EXISTS (
       SELECT 1 FROM jsonb_object_keys(NEW.receipt) k
        WHERE NOT EXISTS (SELECT 1 FROM platform.personal_table_registry r WHERE r.nspname || '.' || r.relname = k)) THEN
    RAISE EXCEPTION '回执只能列清单里的表' USING ERRCODE = 'check_violation';
  END IF;
  RETURN NEW;
END $$;
REVOKE EXECUTE ON FUNCTION platform.erasure_request_guard() FROM PUBLIC;
CREATE TRIGGER erasure_request_guard BEFORE INSERT OR UPDATE OR DELETE ON platform.erasure_request
  FOR EACH ROW EXECUTE FUNCTION platform.erasure_request_guard();
CREATE TRIGGER erasure_request_truncate BEFORE TRUNCATE ON platform.erasure_request
  FOR EACH STATEMENT EXECUTE FUNCTION platform.erasure_request_guard();

-- ---------- 导出任务（契约 insight.export 的 export_job_id / status / download_ref / expires_at）与审计 ----------
CREATE TABLE platform.export_job (
  id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),   -- export_job_id
  student_id           uuid NOT NULL REFERENCES platform.student_profile (id),
  requested_by         uuid NOT NULL REFERENCES platform.account (id),
  status               text NOT NULL CHECK (status IN ('queued', 'running', 'ready', 'failed')),
  download_ref_digest  bytea CHECK (octet_length(download_ref_digest) = 32),  -- sha256(下载凭据)；凭据本身只返回一次，不入库
  expires_at           timestamptz,
  topics               text[] NOT NULL,
  row_count            integer NOT NULL CHECK (row_count >= 0),
  created_at           timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT export_job_ready CHECK ((status = 'ready') = (download_ref_digest IS NOT NULL AND expires_at IS NOT NULL)
                                     AND (expires_at IS NULL OR expires_at <= created_at + interval '24 hours'))
);
CREATE INDEX export_job_student_idx ON platform.export_job (student_id, created_at);
COMMENT ON TABLE platform.export_job IS '导出任务与审计（0035）：只追加；只含主题、行数与下载凭据的摘要，不含导出内容';
CREATE FUNCTION platform.reject_export_job_change() RETURNS trigger LANGUAGE plpgsql SET search_path = pg_catalog
  AS $$ BEGIN RAISE EXCEPTION '导出任务记录只追加' USING ERRCODE = 'insufficient_privilege'; END $$;
REVOKE EXECUTE ON FUNCTION platform.reject_export_job_change() FROM PUBLIC;
CREATE TRIGGER reject_export_job_change BEFORE UPDATE OR DELETE ON platform.export_job
  FOR EACH ROW EXECUTE FUNCTION platform.reject_export_job_change();
CREATE TRIGGER reject_export_job_truncate BEFORE TRUNCATE ON platform.export_job
  FOR EACH STATEMENT EXECUTE FUNCTION platform.reject_export_job_change();

-- ---------- 0022 的只追加守卫：删除分支只对删除请求放行 ----------
CREATE OR REPLACE FUNCTION insight.guard_fact_update() RETURNS trigger LANGUAGE plpgsql SET search_path = pg_catalog
  AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN  -- 分两层：条件里的函数调用不会短路，只在删除时才调用
    IF platform.erasure_in_progress(OLD.student_id) THEN
      RETURN OLD;
    END IF;
  END IF;
  IF TG_OP = 'DELETE' OR TG_OP = 'TRUNCATE' THEN
    RAISE EXCEPTION '建档事实只追加，不能删除' USING ERRCODE = 'insufficient_privilege';
  END IF;
  IF OLD.superseded_at IS NOT NULL OR NEW.superseded_at IS NULL
     OR (to_jsonb(NEW) - 'superseded_at') IS DISTINCT FROM (to_jsonb(OLD) - 'superseded_at') THEN
    RAISE EXCEPTION '建档事实只能把当前行标为已被替换' USING ERRCODE = 'insufficient_privilege';
  END IF;
  RETURN NEW;
END $$;
CREATE OR REPLACE FUNCTION insight.reject_progress_change() RETURNS trigger LANGUAGE plpgsql SET search_path = pg_catalog
  AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN  -- 分两层：条件里的函数调用不会短路，只在删除时才调用
    IF platform.erasure_in_progress(OLD.student_id) THEN
      RETURN OLD;
    END IF;
  END IF;
  RAISE EXCEPTION '建档进度只追加' USING ERRCODE = 'insufficient_privilege';
END $$;

-- ---------- 家长侧函数（属主 authz，只授 BFF） ----------
-- 发起删除：只有该学生的有效绑定家长（有效家长账号）；已删除过的档案拒绝；已有未结束的请求时返回它（幂等）；同一学生 24 小时内最多 5 次
CREATE FUNCTION platform.request_erasure(p_student uuid) RETURNS uuid
  LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog, platform, pg_temp
  AS $$
DECLARE v_id uuid;
BEGIN
  IF p_student IS NULL OR NOT platform.is_linked_guardian(p_student) THEN
    RAISE EXCEPTION 'not allowed' USING ERRCODE = 'insufficient_privilege';
  END IF;
  IF EXISTS (SELECT 1 FROM platform.student_profile s WHERE s.id = p_student AND s.erased_at IS NOT NULL) THEN
    RAISE EXCEPTION '该学生的数据已删除' USING ERRCODE = 'check_violation';
  END IF;
  PERFORM pg_advisory_xact_lock(hashtextextended('platform.erasure_request:' || p_student::text, 0));
  SELECT r.id INTO v_id FROM platform.erasure_request r WHERE r.student_id = p_student AND r.status = 'pending';
  IF FOUND THEN
    RETURN v_id;
  END IF;
  IF (SELECT count(*) FROM platform.erasure_request r WHERE r.student_id = p_student AND r.requested_at > now() - interval '24 hours') >= 5 THEN
    RAISE EXCEPTION '删除请求过多' USING ERRCODE = 'program_limit_exceeded';
  END IF;
  INSERT INTO platform.erasure_request (student_id, requested_by) VALUES (p_student, platform.current_account_id())
  RETURNING id INTO v_id;
  RETURN v_id;
END $$;

-- 二次确认：只有发起人本人，且仍是有效绑定家长；发起后 24 小时内；确认后冷静期 7 天（待人确认 #3）。重复确认返回原冷静期（幂等）。
-- 请求不存在、不是本人发起、已无有效绑定一律报同一个错（不泄露存在性）
CREATE FUNCTION platform.confirm_erasure(p_request uuid) RETURNS timestamptz
  LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog, platform, pg_temp
  AS $$
DECLARE r platform.erasure_request;
BEGIN
  SELECT * INTO r FROM platform.erasure_request x WHERE x.id = p_request FOR UPDATE;
  IF NOT FOUND OR r.requested_by IS DISTINCT FROM platform.current_account_id() OR NOT platform.is_linked_guardian(r.student_id) THEN
    RAISE EXCEPTION 'not allowed' USING ERRCODE = 'insufficient_privilege';
  END IF;
  IF r.status <> 'pending' THEN
    RAISE EXCEPTION '删除请求已结束' USING ERRCODE = 'check_violation';
  END IF;
  IF r.confirmed_at IS NOT NULL THEN
    RETURN r.cooling_off_until;
  END IF;
  IF r.requested_at < now() - interval '24 hours' THEN
    RAISE EXCEPTION '确认已超时，请重新发起' USING ERRCODE = 'check_violation';
  END IF;
  UPDATE platform.erasure_request SET confirmed_at = now(), cooling_off_until = now() + interval '7 days' WHERE id = p_request
  RETURNING cooling_off_until INTO r.cooling_off_until;
  RETURN r.cooling_off_until;
END $$;

-- 撤回：该学生任一有效绑定家长，执行前都能撤回；已撤回的再撤回是幂等的
CREATE FUNCTION platform.cancel_erasure(p_request uuid) RETURNS void
  LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog, platform, pg_temp
  AS $$
DECLARE r platform.erasure_request;
BEGIN
  SELECT * INTO r FROM platform.erasure_request x WHERE x.id = p_request FOR UPDATE;
  IF NOT FOUND OR NOT platform.is_linked_guardian(r.student_id) THEN
    RAISE EXCEPTION 'not allowed' USING ERRCODE = 'insufficient_privilege';
  END IF;
  IF r.status = 'executed' THEN
    RAISE EXCEPTION '删除已执行，不能撤回' USING ERRCODE = 'check_violation';
  END IF;
  IF r.status = 'cancelled' THEN
    RETURN;
  END IF;
  UPDATE platform.erasure_request
     SET status = 'cancelled', cancelled_at = now(), cancelled_by = platform.current_account_id(), cancel_reason = 'guardian'
   WHERE id = p_request;
END $$;

-- 导出：只认有效绑定家长；按主题键组织，只含清单里有列白名单的表、白名单列与行过滤；同一学生 24 小时内最多 10 次。
-- 建一条导出任务（ready）：一次性下载凭据只在这里返回一次，库里只存 sha256，24 小时失效（契约 download_ref / expires_at）
CREATE FUNCTION platform.export_student_data(p_student uuid) RETURNS jsonb
  LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog, platform, pg_temp
  AS $$
DECLARE
  t platform.personal_table_registry;
  v_rows jsonb;
  v_topics jsonb := '{}';
  v_names text[] := '{}';
  v_total integer := 0;
  v_ref text;
  v_job uuid;
  v_expires timestamptz := now() + interval '24 hours';
BEGIN
  IF p_student IS NULL OR NOT platform.is_linked_guardian(p_student) THEN
    RAISE EXCEPTION 'not allowed' USING ERRCODE = 'insufficient_privilege';
  END IF;
  PERFORM pg_advisory_xact_lock(hashtextextended('platform.export_job:' || p_student::text, 0));
  IF (SELECT count(*) FROM platform.export_job j WHERE j.student_id = p_student AND j.created_at > now() - interval '24 hours') >= 10 THEN
    RAISE EXCEPTION '导出过于频繁' USING ERRCODE = 'program_limit_exceeded';
  END IF;
  FOR t IN SELECT * FROM platform.personal_table_registry WHERE export_topic IS NOT NULL ORDER BY export_topic LOOP
    EXECUTE format('SELECT coalesce(jsonb_agg(to_jsonb(x) ORDER BY to_jsonb(x)::text), ''[]'') FROM (SELECT %s FROM %I.%I WHERE (%s) AND (%s)) x',
                   (SELECT string_agg(quote_ident(c), ', ') FROM unnest(t.export_columns) c),
                   t.nspname, t.relname, t.student_predicate, coalesce(t.export_filter, 'true'))
      USING p_student INTO v_rows;
    v_topics := v_topics || jsonb_build_object(t.export_topic, v_rows);
    v_names := v_names || t.export_topic;
    v_total := v_total + jsonb_array_length(v_rows);
  END LOOP;
  v_ref := 'dl_' || encode(platform.auth_random_bytes(32), 'hex');
  INSERT INTO platform.export_job (student_id, requested_by, status, download_ref_digest, expires_at, topics, row_count)
  VALUES (p_student, platform.current_account_id(), 'ready', sha256(convert_to(v_ref, 'UTF8')), v_expires, v_names, v_total)
  RETURNING id INTO v_job;
  RETURN jsonb_build_object('format', 'starbuddy-export@1', 'export_job_id', v_job, 'status', 'ready', 'download_ref', v_ref,
                            'expires_at', v_expires, 'student_id', p_student, 'generated_at', now(), 'topics', v_topics);
END $$;

DO $$
DECLARE f regprocedure;
BEGIN
  FOREACH f IN ARRAY ARRAY['platform.request_erasure(uuid)', 'platform.confirm_erasure(uuid)', 'platform.cancel_erasure(uuid)',
                           'platform.export_student_data(uuid)']::regprocedure[] LOOP
    EXECUTE format('ALTER FUNCTION %s OWNER TO starbuddy_authz', f);
    EXECUTE format('REVOKE EXECUTE ON FUNCTION %s FROM PUBLIC', f);
    EXECUTE format('GRANT EXECUTE ON FUNCTION %s TO starbuddy_bff', f);
  END LOOP;
END $$;

-- ---------- 执行到期的删除（属主 starbuddy_eraser，只授 starbuddy_scheduler） ----------
-- p_now 不能晚于现在（不能提前结束冷静期），最多往前 1 天。
-- 1. 发起后 24 小时仍未确认的请求作废（confirm_expired）；
-- 2. 冷静期已满的请求逐个执行（SKIP LOCKED，多个调度实例不重复执行）：发起人已不是有效绑定家长 → 作废（link_revoked）；否则在本事务里：
--    锁住学生档案行（挡住带外键的并发写入）→ 设 starbuddy.erasure_student → 按清单先数、再按 delete_order 删 → 逐表复核为零
--    （并发写入会让整个事务失败、下次重试）→ 停用学生账号、档案标记 erased_at → 清掉变量 → 写回执与 scopes。返回执行条数。
CREATE FUNCTION platform.execute_due_erasures(p_now timestamptz) RETURNS integer
  LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog, platform, pg_temp
  AS $$
DECLARE
  r platform.erasure_request;
  t platform.personal_table_registry;
  v_n integer := 0;
  v_count bigint;
  v_receipt jsonb;
  v_account uuid;
BEGIN
  IF p_now IS NULL OR p_now > now() OR p_now < now() - interval '1 day' THEN
    RAISE EXCEPTION 'p_now must be within the last day and not in the future' USING ERRCODE = 'invalid_parameter_value';
  END IF;
  UPDATE platform.erasure_request SET status = 'cancelled', cancelled_at = p_now, cancel_reason = 'confirm_expired'
   WHERE status = 'pending' AND confirmed_at IS NULL AND requested_at < p_now - interval '24 hours';
  FOR r IN SELECT * FROM platform.erasure_request x
            WHERE x.status = 'pending' AND x.confirmed_at IS NOT NULL AND x.cooling_off_until <= p_now
            ORDER BY x.cooling_off_until, x.id FOR UPDATE SKIP LOCKED LOOP
    IF NOT EXISTS (SELECT 1 FROM platform.guardian_student_link l JOIN platform.account a ON a.id = l.guardian_account_id
                    WHERE l.student_id = r.student_id AND l.guardian_account_id = r.requested_by AND l.status = 'active'
                      AND a.kind = 'guardian' AND a.status = 'active') THEN
      UPDATE platform.erasure_request SET status = 'cancelled', cancelled_at = p_now, cancel_reason = 'link_revoked' WHERE id = r.id;
      CONTINUE;
    END IF;
    SELECT s.account_id INTO v_account FROM platform.student_profile s WHERE s.id = r.student_id FOR UPDATE;
    PERFORM set_config('starbuddy.erasure_student', r.student_id::text, true);
    v_receipt := '{}';
    FOR t IN SELECT * FROM platform.personal_table_registry WHERE strategy = 'delete' ORDER BY delete_order LOOP
      EXECUTE format('SELECT count(*) FROM %I.%I WHERE %s', t.nspname, t.relname, t.student_predicate) USING r.student_id INTO v_count;
      v_receipt := v_receipt || jsonb_build_object(t.nspname || '.' || t.relname, v_count);
    END LOOP;
    FOR t IN SELECT * FROM platform.personal_table_registry WHERE strategy = 'delete' ORDER BY delete_order LOOP
      EXECUTE format('DELETE FROM %I.%I WHERE %s', t.nspname, t.relname, t.student_predicate) USING r.student_id;
    END LOOP;
    FOR t IN SELECT * FROM platform.personal_table_registry WHERE strategy = 'delete' ORDER BY delete_order LOOP
      EXECUTE format('SELECT count(*) FROM %I.%I WHERE %s', t.nspname, t.relname, t.student_predicate) USING r.student_id INTO v_count;
      IF v_count <> 0 THEN
        RAISE EXCEPTION '删除后 %.% 仍有 % 行', t.nspname, t.relname, v_count USING ERRCODE = 'internal_error';
      END IF;
    END LOOP;
    UPDATE platform.account SET status = 'disabled' WHERE id = v_account;
    UPDATE platform.student_profile SET erased_at = p_now WHERE id = r.student_id;
    PERFORM set_config('starbuddy.erasure_student', '', true);
    UPDATE platform.erasure_request
       SET status = 'executed', executed_at = p_now, receipt = v_receipt,
           scopes = (SELECT array_agg(DISTINCT g.erase_scope ORDER BY g.erase_scope) FROM platform.personal_table_registry g WHERE g.strategy = 'delete')
     WHERE id = r.id;
    v_n := v_n + 1;
  END LOOP;
  RETURN v_n;
END $$;
ALTER FUNCTION platform.execute_due_erasures(timestamptz) OWNER TO starbuddy_eraser;
REVOKE EXECUTE ON FUNCTION platform.execute_due_erasures(timestamptz) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION platform.execute_due_erasures(timestamptz) TO starbuddy_scheduler;

-- ---------- 权限 ----------
ALTER TABLE platform.personal_table_registry ENABLE ROW LEVEL SECURITY;
ALTER TABLE platform.personal_table_registry FORCE ROW LEVEL SECURITY;
ALTER TABLE platform.erasure_request ENABLE ROW LEVEL SECURITY;
ALTER TABLE platform.erasure_request FORCE ROW LEVEL SECURITY;
ALTER TABLE platform.export_job ENABLE ROW LEVEL SECURITY;
ALTER TABLE platform.export_job FORCE ROW LEVEL SECURITY;
REVOKE ALL ON platform.personal_table_registry, platform.erasure_request, platform.export_job FROM PUBLIC;
-- 清单与导出任务：应用角色都读不到（只有函数属主）
GRANT SELECT ON platform.personal_table_registry TO starbuddy_authz, starbuddy_eraser;
GRANT SELECT, INSERT ON platform.export_job TO starbuddy_authz;
GRANT SELECT, INSERT, UPDATE ON platform.erasure_request TO starbuddy_authz;
GRANT SELECT, UPDATE ON platform.erasure_request TO starbuddy_eraser;
-- 家长经 BFF 读自己有效绑定的孩子的删除请求与回执；学生本人看不到
GRANT SELECT (id, student_id, requested_at, confirmed_at, cooling_off_until, status, cancelled_at, cancel_reason, executed_at, receipt, scopes)
  ON platform.erasure_request TO starbuddy_bff;
CREATE POLICY erasure_request_guardian_read ON platform.erasure_request FOR SELECT TO starbuddy_bff
  USING (platform.is_linked_guardian(student_id));
-- 导出函数属主（authz）：此前读不到的导出列
GRANT SELECT (student_id, guardian_account_id, topic, content, created_at) ON platform.guardian_report TO starbuddy_authz;

-- 删除执行身份（eraser）：清单 delete 表的 DELETE 与学生键列的 SELECT（不授原文列）；档案锁与标记、账号停用、发起人绑定核对
GRANT EXECUTE ON FUNCTION platform.erasure_in_progress(uuid) TO starbuddy_eraser;
GRANT DELETE ON insight.exam_reconciliation, insight.exam_prediction, insight.dispute, insight.intervention_log,
  insight.claim, insight.claim_history, insight.behavior_event, insight.confidence_probe, insight.self_report, insight.guardian_report,
  insight.onboarding_progress, insight.exam_record, platform.review_task, platform.diagnosis_step, platform.diagnosis_session,
  platform.grade_dimension, platform.grade_result, platform.mastery_estimate, platform.skill_observation, platform.error_attribution,
  platform.attempt, platform.evidence_event, platform.evidence_registry, platform.ingest_request, platform.replay_seal,
  platform.guardian_report, platform.student_subject_textbook, platform.subject_enrollment, platform.student_time_guard,
  platform.guardian_invite, platform.auth_session, platform.account_contact TO starbuddy_eraser;
GRANT SELECT (student_id) ON insight.exam_reconciliation, insight.exam_prediction, insight.dispute, insight.intervention_log,
  insight.behavior_event, insight.confidence_probe, insight.self_report, insight.guardian_report, insight.onboarding_progress,
  insight.exam_record, platform.review_task, platform.mastery_estimate, platform.skill_observation, platform.error_attribution,
  platform.attempt, platform.evidence_event, platform.evidence_registry, platform.ingest_request, platform.replay_seal,
  platform.guardian_report, platform.student_subject_textbook, platform.subject_enrollment, platform.student_time_guard,
  platform.guardian_invite TO starbuddy_eraser;
GRANT SELECT (id, student_id) ON insight.claim, platform.diagnosis_session, platform.grade_result TO starbuddy_eraser;
GRANT SELECT (claim_id) ON insight.claim_history TO starbuddy_eraser;
GRANT SELECT (session_id) ON platform.diagnosis_step TO starbuddy_eraser;
GRANT SELECT (grade_result_id) ON platform.grade_dimension TO starbuddy_eraser;
GRANT SELECT (account_id) ON platform.auth_session, platform.account_contact TO starbuddy_eraser;
GRANT SELECT (id, account_id, household_id, erased_at) ON platform.student_profile TO starbuddy_eraser;
GRANT UPDATE (erased_at) ON platform.student_profile TO starbuddy_eraser;
GRANT SELECT (id, kind, status) ON platform.account TO starbuddy_eraser;
GRANT UPDATE (status) ON platform.account TO starbuddy_eraser;
GRANT SELECT (student_id, guardian_account_id, status) ON platform.guardian_student_link TO starbuddy_eraser;
