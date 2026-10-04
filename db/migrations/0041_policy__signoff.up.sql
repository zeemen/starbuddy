-- 0041 policy · 考情政策线签字收口（specs/release-gate-sprint4/spec.md 第 3、8 节、冲刺 4 段“考情”；第二部分 PR C）
-- 状态：草稿。只写文件，由人先在副本演练再执行（CLAUDE.md 必停项：迁移、RLS 与权限）。
-- 依赖 0013（policy）、0023（platform.review_current_staff）、0040（签字人表 platform.policy_signoff）。请以单事务执行（psql -1）。
-- 只改 policy schema（ADR 02）；对 platform 只读当前账号、写签字人表（0040 已授权给函数属主 authz，同 0019 对 0018 的做法）。
-- 要点：
--   1. 签字：policy.sign_scheme（方案）与 policy.sign_record（来源文件、招录行、学校）：SECURITY DEFINER，属主 authz，只授 starbuddy_review，
--      要求 active staff；签字人取当前账号，调用方不能传；调用方回传看到的内容摘要，与当前不符就拒绝（沿用 0039 的做法）；
--      同一账号对同一内容只能签一次，计数按不同账号去重，所以同一账号签两次只算一人；
--   2. 两人：两名不同 staff 对“当前内容”批准才算数（内容改过后旧签字不再计数；当前内容有驳回则清零，改了再送审）；
--   3. 发布收口：方案只能由签字身份经 policy.publish_scheme 发布（收回 starbuddy_kge 的 EXECUTE）；来源文件、招录行、学校在第二名批准时
--      由 policy.sign_record 发布；starbuddy_kge 直接改为已发布、以已发布新建、写复核时间都被拒；
--   4. 签字人记在 platform.policy_signoff（0040），policy 表上不存账号引用（规则 13）；自报工号列 reviewers 保留为展示字段，不再计数
--      （spec 待人确认 #4 默认）；写入方只能在新建草稿时填，之后不能改（收回 UPDATE (reviewers)）；
--      复核时间 reviewed_at 由发布时写入（最后一个计数的批准签字时间）；不在 kge.content_review 里再记（规则 2）；
--   5. 版本记录导出用只读函数 platform.policy_signoff_record()（0040，授 kge、review），审核台后端与 kge.version_record 的行合并；
--      本迁移给函数属主 authz 补读 policy.school、policy.admission_line 的权限，导出才能带上学校与招录行的城市、年份。
-- 不在本迁移：撤下仍由写入方直接改状态（spec 第 8 节只要求发布收口，见 lessons 待人确认）；考情差异视图与签字界面（PR D）。

-- 数据前提：库里已有已发布或已撤下的考情数据时，其复核人是自报工号，没有 staff 账号签字记录可补，以明确信息中止，由人处理
DO $$
DECLARE v_doc bigint; v_scheme bigint; v_line bigint; v_school bigint;
BEGIN
  SELECT count(*) INTO v_doc FROM policy.source_document WHERE status <> 'draft';
  SELECT count(*) INTO v_scheme FROM policy.exam_scheme WHERE status <> 'draft';
  SELECT count(*) INTO v_line FROM policy.admission_line WHERE status <> 'draft';
  SELECT count(*) INTO v_school FROM policy.school WHERE review_status <> 'draft';
  IF v_doc + v_scheme + v_line + v_school > 0 THEN
    RAISE EXCEPTION '0041 中止：已有已发布或已撤下的考情数据（来源文件 %、方案 %、招录行 %、学校 %），复核人是自报工号，没有 staff 账号签字记录可补；'
                    '请人先决定处理办法（例如由两名 staff 按新流程重录重签）再执行', v_doc, v_scheme, v_line, v_school
      USING ERRCODE = 'object_not_in_prerequisite_state';
  END IF;
END $$;

-- 角色前提（同 0019）：守卫里的 current_user = 'starbuddy_authz' 依赖 authz 没有成员；签字身份（review）不能与任何能写考情或面向家庭的
-- 应用角色共存：任何角色都不能同时继承 review 与写入方（kge）、学习服务（learning，能跑 validate_scheme 与读全部草稿）或 BFF（bff）
DO $$
DECLARE v_role text;
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'starbuddy_authz' AND (rolcanlogin OR rolsuper))
     OR EXISTS (SELECT 1 FROM pg_roles r
                 WHERE NOT r.rolsuper AND r.rolname <> 'starbuddy_authz' AND pg_has_role(r.oid, 'starbuddy_authz', 'MEMBER')) THEN
    RAISE EXCEPTION 'starbuddy_authz 必须 NOLOGIN、非 SUPERUSER，且不能直接或间接授予任何角色';
  END IF;
  FOREACH v_role IN ARRAY ARRAY['starbuddy_kge', 'starbuddy_learning', 'starbuddy_bff'] LOOP
    IF EXISTS (SELECT 1 FROM pg_roles r WHERE NOT r.rolsuper
                 AND pg_has_role(r.oid, v_role, 'MEMBER') AND pg_has_role(r.oid, 'starbuddy_review', 'MEMBER')) THEN
      RAISE EXCEPTION '0041 中止：同一个角色不能同时继承 %（写入或服务身份）与 starbuddy_review（签字）', v_role
        USING ERRCODE = 'object_not_in_prerequisite_state';
    END IF;
  END LOOP;
END $$;

GRANT USAGE ON SCHEMA policy TO starbuddy_review;

-- ---------- 所需签字人数（考情政策线一律两人：v1.3 增补篇第六章；0013 的两人 CHECK 改由签字人表判断） ----------
CREATE FUNCTION policy.signers_required() RETURNS integer LANGUAGE sql IMMUTABLE SET search_path = pg_catalog, pg_temp
  AS $$ SELECT 2 $$;
REVOKE EXECUTE ON FUNCTION policy.signers_required() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION policy.signers_required() TO starbuddy_kge, starbuddy_learning, starbuddy_review, starbuddy_authz;

-- ---------- 函数属主（authz）：读四类对象、锁行、写状态与复核时间（列级） ----------
-- school、admission_line：签字锁行，也供 0040 的导出函数 platform.policy_signoff_record() 读城市与年份；
-- source_domain：来源文件的保护触发器（guard_source_document）在发布的 UPDATE 里按执行者身份读主机名清单（表达式整体做权限检查）
GRANT SELECT ON policy.school, policy.admission_line, policy.source_domain TO starbuddy_authz;
GRANT UPDATE (status, reviewed_at) ON policy.source_document, policy.admission_line TO starbuddy_authz;
GRANT UPDATE (review_status, reviewed_at) ON policy.school TO starbuddy_authz;
GRANT UPDATE (reviewed_at) ON policy.exam_scheme TO starbuddy_authz;

-- ---------- 复核人：自报工号不再计数，写入方事后不能改；复核时间只由发布写入 ----------
-- reviewers 只收回 UPDATE：写入方的 INSERT 是表级授权，PostgreSQL 不能从表级授权里单独收回一列（要收就得改成逐列 INSERT 授权，
-- 以后每加一列都要记得补授）；新建草稿时填的自报工号只作展示，不进摘要、不计数，所以保留 INSERT。
REVOKE UPDATE (reviewed_at, reviewers) ON policy.source_document, policy.exam_scheme, policy.admission_line, policy.school FROM starbuddy_kge;
ALTER TABLE policy.source_document
  DROP CONSTRAINT source_document_two_reviewers,
  ADD CONSTRAINT source_document_reviewed CHECK (status = 'draft' OR reviewed_at IS NOT NULL);
ALTER TABLE policy.exam_scheme
  DROP CONSTRAINT exam_scheme_two_reviewers,
  ADD CONSTRAINT exam_scheme_reviewed CHECK (status = 'draft' OR reviewed_at IS NOT NULL);
ALTER TABLE policy.admission_line
  DROP CONSTRAINT admission_line_two_reviewers,
  ADD CONSTRAINT admission_line_reviewed CHECK (status = 'draft' OR reviewed_at IS NOT NULL);
ALTER TABLE policy.school
  DROP CONSTRAINT school_two_reviewers,
  ADD CONSTRAINT school_reviewed CHECK (review_status = 'draft' OR reviewed_at IS NOT NULL);
COMMENT ON COLUMN policy.source_document.reviewers IS '自报工号，仅作展示，新建草稿时填、之后不能改（0041 起签字人以 platform.policy_signoff 为准）';
COMMENT ON COLUMN policy.exam_scheme.reviewers IS '自报工号，仅作展示，新建草稿时填、之后不能改（0041 起签字人以 platform.policy_signoff 为准）';
COMMENT ON COLUMN policy.admission_line.reviewers IS '自报工号，仅作展示，新建草稿时填、之后不能改（0041 起签字人以 platform.policy_signoff 为准）';
COMMENT ON COLUMN policy.school.reviewers IS '自报工号，仅作展示，新建草稿时填、之后不能改（0041 起签字人以 platform.policy_signoff 为准）';
COMMENT ON COLUMN policy.exam_scheme.reviewed_at IS '复核完成时间：发布时由 policy.publish_scheme 写入（两名 staff 对发布内容的批准中最后一个的时间）';

-- ---------- 发布收口：只能以草稿新建（不带复核时间）；改为已发布、写复核时间只能经签字与发布函数（属主 authz） ----------
-- 四张表共用一个守卫（SECURITY INVOKER）；学校的发布状态列是 review_status
CREATE FUNCTION policy.guard_signoff_publish() RETURNS trigger LANGUAGE plpgsql
  SET search_path = pg_catalog, policy, pg_temp
  AS $$
DECLARE
  v_col text := CASE TG_TABLE_NAME WHEN 'school' THEN 'review_status' ELSE 'status' END;
  v_new text := to_jsonb(NEW) ->> v_col;
BEGIN
  IF TG_OP = 'INSERT' THEN
    IF v_new <> 'draft' OR NEW.reviewed_at IS NOT NULL THEN
      RAISE EXCEPTION '% 只能以草稿新建；发布与复核时间只能经签字函数（两名不同 staff 签字）', TG_TABLE_NAME USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
  END IF;
  IF v_new = 'published' AND (to_jsonb(OLD) ->> v_col) IS DISTINCT FROM 'published' AND current_user <> 'starbuddy_authz' THEN
    RAISE EXCEPTION '% 只能经签字函数发布（两名不同 staff 签字）', TG_TABLE_NAME USING ERRCODE = 'insufficient_privilege';
  END IF;
  IF NEW.reviewed_at IS DISTINCT FROM OLD.reviewed_at AND current_user <> 'starbuddy_authz' THEN
    RAISE EXCEPTION '% 的复核时间只能由签字函数写入', TG_TABLE_NAME USING ERRCODE = 'insufficient_privilege';
  END IF;
  RETURN NEW;
END $$;
REVOKE EXECUTE ON FUNCTION policy.guard_signoff_publish() FROM PUBLIC;
CREATE TRIGGER guard_signoff_publish BEFORE INSERT OR UPDATE ON policy.source_document FOR EACH ROW EXECUTE FUNCTION policy.guard_signoff_publish();
CREATE TRIGGER guard_signoff_publish BEFORE INSERT OR UPDATE ON policy.exam_scheme FOR EACH ROW EXECUTE FUNCTION policy.guard_signoff_publish();
CREATE TRIGGER guard_signoff_publish BEFORE INSERT OR UPDATE ON policy.admission_line FOR EACH ROW EXECUTE FUNCTION policy.guard_signoff_publish();
CREATE TRIGGER guard_signoff_publish BEFORE INSERT OR UPDATE ON policy.school FOR EACH ROW EXECUTE FUNCTION policy.guard_signoff_publish();

-- ---------- 内容摘要：只返回 SHA-256，不返回内容；不含状态、复核人、复核与发布时间、建立时间 ----------
-- 方案的摘要含科目配置、过渡规则、招录规则（发布后随方案冻结的全部内容）。固定按 UTC 序列化
CREATE FUNCTION policy.object_digest(p_object_type text, p_id uuid) RETURNS text
  LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, policy, pg_temp SET TimeZone = 'UTC'
  AS $$
  SELECT encode(sha256(convert_to(q.content::text, 'UTF8')), 'hex') FROM (
    SELECT to_jsonb(d) - 'status' - 'reviewers' - 'reviewed_at' - 'created_at' AS content
      FROM policy.source_document d WHERE p_object_type = 'source_document' AND d.id = p_id
    UNION ALL
    SELECT jsonb_build_object(
             'scheme', to_jsonb(s) - 'status' - 'reviewers' - 'reviewed_at' - 'published_at' - 'created_at',
             'subjects', (SELECT coalesce(jsonb_agg(to_jsonb(c) - 'scheme_id' ORDER BY c.subject), '[]')
                            FROM policy.subject_config c WHERE c.scheme_id = s.id),
             'transitions', (SELECT coalesce(jsonb_agg(to_jsonb(t) - 'id' - 'scheme_id' ORDER BY t.change_kind), '[]')
                               FROM policy.transition_rule t WHERE t.scheme_id = s.id),
             'admission_rules', (SELECT coalesce(jsonb_agg(to_jsonb(r) - 'scheme_id' ORDER BY r.batch_code, r.track), '[]')
                                   FROM policy.admission_rule r WHERE r.scheme_id = s.id))
      FROM policy.exam_scheme s WHERE p_object_type = 'exam_scheme' AND s.id = p_id
    UNION ALL
    SELECT to_jsonb(l) - 'status' - 'reviewers' - 'reviewed_at' - 'created_at'
      FROM policy.admission_line l WHERE p_object_type = 'admission_line' AND l.id = p_id
    UNION ALL
    SELECT to_jsonb(h) - 'review_status' - 'reviewers' - 'reviewed_at'
      FROM policy.school h WHERE p_object_type = 'school' AND h.id = p_id
  ) q $$;

-- ---------- 当前内容的有效批准人数：对当前摘要批准的不同 staff 账号数；当前内容有驳回则为 0（改了内容再送审） ----------
-- 只返回人数，不返回签字人（写入方与学习服务经 validate_scheme 用到；签字人表本身它们读不到）
CREATE FUNCTION policy.approval_count(p_object_type text, p_id uuid) RETURNS integer
  LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = pg_catalog, policy, pg_temp
  AS $$
DECLARE v_digest text := policy.object_digest(p_object_type, p_id);
BEGIN
  IF EXISTS (SELECT 1 FROM platform.policy_signoff s
              WHERE s.object_type = p_object_type AND s.object_id = p_id AND s.action = 'review'
                AND s.decision = 'rejected' AND s.digest = v_digest) THEN
    RETURN 0;
  END IF;
  RETURN (SELECT count(DISTINCT s.account_id)::integer FROM platform.policy_signoff s
           WHERE s.object_type = p_object_type AND s.object_id = p_id AND s.action = 'review'
             AND s.decision = 'approved' AND s.digest = v_digest);
END $$;

-- ---------- 签字：方案（只记签字；发布由签字身份另调 policy.publish_scheme）。返回当前有效批准人数 ----------
CREATE FUNCTION policy.sign_scheme(p_scheme uuid, p_decision text, p_expected_digest text) RETURNS integer
  LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog, policy, pg_temp SET TimeZone = 'UTC'
  AS $$
DECLARE
  v_staff uuid := platform.review_current_staff();
  v_status text;
  v_digest text;
BEGIN
  IF p_decision IS NULL OR p_decision NOT IN ('approved', 'rejected') THEN
    RAISE EXCEPTION '审核结论只能是 approved 或 rejected' USING ERRCODE = 'invalid_parameter_value';
  END IF;
  -- 锁住方案行：与发布的 FOR UPDATE、写入方改科目配置与规则的 FOR SHARE（guard_scheme_frozen）互斥
  SELECT s.status::text INTO v_status FROM policy.exam_scheme s WHERE s.id = p_scheme FOR UPDATE;
  IF v_status IS NULL THEN
    RAISE EXCEPTION '方案不存在' USING ERRCODE = 'no_data_found';
  END IF;
  IF v_status <> 'draft' THEN
    RAISE EXCEPTION '只能对草稿方案签字（当前 %）', v_status USING ERRCODE = 'check_violation';
  END IF;
  v_digest := policy.object_digest('exam_scheme', p_scheme);
  IF p_expected_digest IS DISTINCT FROM v_digest THEN
    RAISE EXCEPTION '你看到的方案与当前不一致（摘要不符），请刷新后再签' USING ERRCODE = 'check_violation';
  END IF;
  BEGIN
    INSERT INTO platform.policy_signoff (object_type, exam_scheme_id, action, decision, digest, account_id, signed_at)
    VALUES ('exam_scheme', p_scheme, 'review', p_decision, v_digest, v_staff, clock_timestamp());
  EXCEPTION WHEN unique_violation THEN
    RAISE EXCEPTION '同一账号对同一内容只能签一次' USING ERRCODE = 'unique_violation';
  END;
  RETURN policy.approval_count('exam_scheme', p_scheme);
END $$;

-- ---------- 签字：来源文件、招录行、学校（第二名不同 staff 对当前内容批准时发布）。返回签字后的状态 ----------
CREATE FUNCTION policy.sign_record(p_object_type text, p_id uuid, p_decision text, p_expected_digest text) RETURNS text
  LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog, policy, pg_temp SET TimeZone = 'UTC'
  AS $$
DECLARE
  v_staff uuid := platform.review_current_staff();
  v_status text;
  v_digest text;
  v_ts timestamptz := clock_timestamp();
BEGIN
  IF p_decision IS NULL OR p_decision NOT IN ('approved', 'rejected') THEN
    RAISE EXCEPTION '审核结论只能是 approved 或 rejected' USING ERRCODE = 'invalid_parameter_value';
  END IF;
  -- 锁住对象行：同一对象的签字串行（两名 staff 同时签时，后一个等前一个提交后再计数），也与写入方的修改互斥
  IF p_object_type = 'source_document' THEN
    SELECT d.status::text INTO v_status FROM policy.source_document d WHERE d.id = p_id FOR UPDATE;
  ELSIF p_object_type = 'admission_line' THEN
    SELECT l.status::text INTO v_status FROM policy.admission_line l WHERE l.id = p_id FOR UPDATE;
  ELSIF p_object_type = 'school' THEN
    SELECT h.review_status::text INTO v_status FROM policy.school h WHERE h.id = p_id FOR UPDATE;
  ELSE
    RAISE EXCEPTION '对象类型只能是 source_document、admission_line、school（方案用 policy.sign_scheme 签字、policy.publish_scheme 发布）'
      USING ERRCODE = 'invalid_parameter_value';
  END IF;
  IF v_status IS NULL THEN
    RAISE EXCEPTION '签字对象不存在' USING ERRCODE = 'no_data_found';
  END IF;
  IF v_status <> 'draft' THEN
    RAISE EXCEPTION '只能对草稿签字（当前 %）', v_status USING ERRCODE = 'check_violation';
  END IF;
  v_digest := policy.object_digest(p_object_type, p_id);
  IF p_expected_digest IS DISTINCT FROM v_digest THEN
    RAISE EXCEPTION '你看到的内容与当前不一致（摘要不符），请刷新后再签' USING ERRCODE = 'check_violation';
  END IF;
  BEGIN
    INSERT INTO platform.policy_signoff (object_type, source_document_id, admission_line_id, institution_id, action, decision, digest,
                                         account_id, signed_at)
    VALUES (p_object_type, CASE WHEN p_object_type = 'source_document' THEN p_id END, CASE WHEN p_object_type = 'admission_line' THEN p_id END,
            CASE WHEN p_object_type = 'school' THEN p_id END, 'review', p_decision, v_digest, v_staff, v_ts);
  EXCEPTION WHEN unique_violation THEN
    RAISE EXCEPTION '同一账号对同一内容只能签一次' USING ERRCODE = 'unique_violation';
  END;
  IF p_decision = 'approved' AND policy.approval_count(p_object_type, p_id) >= policy.signers_required() THEN
    -- 发布：表上的保护触发器照常执行（招录行再核对学校、来源、方案与总分；同一行只能有一条已发布）
    IF p_object_type = 'source_document' THEN
      UPDATE policy.source_document SET status = 'published', reviewed_at = v_ts WHERE id = p_id;
    ELSIF p_object_type = 'admission_line' THEN
      UPDATE policy.admission_line SET status = 'published', reviewed_at = v_ts WHERE id = p_id;
    ELSE
      UPDATE policy.school SET review_status = 'published', reviewed_at = v_ts WHERE id = p_id;
    END IF;
    INSERT INTO platform.policy_signoff (object_type, source_document_id, admission_line_id, institution_id, action, decision, digest,
                                         account_id, signed_at)
    VALUES (p_object_type, CASE WHEN p_object_type = 'source_document' THEN p_id END, CASE WHEN p_object_type = 'admission_line' THEN p_id END,
            CASE WHEN p_object_type = 'school' THEN p_id END, 'publish', 'approved', v_digest, v_staff, clock_timestamp());
    RETURN 'published';
  END IF;
  RETURN 'draft';
END $$;

-- ---------- 发布前校验：第 5 项改为数签字人表（其余与 0013 相同） ----------
CREATE OR REPLACE FUNCTION policy.validate_scheme(p_scheme uuid)
  RETURNS TABLE (check_name text, severity text, object_id text, detail text)
  LANGUAGE plpgsql STABLE
  SET search_path = pg_catalog, policy, pg_temp
  AS $$
DECLARE s policy.exam_scheme%ROWTYPE;
BEGIN
  SELECT * INTO s FROM policy.exam_scheme WHERE id = p_scheme;
  IF NOT FOUND THEN
    RAISE EXCEPTION '方案不存在' USING ERRCODE = 'no_data_found';
  END IF;
  -- 1. 来源文件已发布、同城市、覆盖该届别
  RETURN QUERY
    SELECT 'source_document', 'error', s.source_document_id::text, '来源文件未发布、不是同一城市或不覆盖该届别'
     WHERE NOT EXISTS (SELECT 1 FROM policy.source_document d
                        WHERE d.id = s.source_document_id AND d.status = 'published' AND d.city = s.city
                          AND s.cohort BETWEEN d.applies_from_cohort AND coalesce(d.applies_to_cohort, s.cohort));
  -- 2. 至少有一门计分科目
  RETURN QUERY
    SELECT 'no_scored_subject', 'error', p_scheme::text, '没有计分科目'
     WHERE NOT EXISTS (SELECT 1 FROM policy.subject_config c WHERE c.scheme_id = p_scheme AND c.scored);
  -- 3. 合场分组至少两门
  RETURN QUERY
    SELECT 'combined_group_single', 'error', c.combined_group::text, '合场分组只有一门科目'
      FROM policy.subject_config c WHERE c.scheme_id = p_scheme AND c.combined_group IS NOT NULL
     GROUP BY c.combined_group HAVING count(*) < 2;
  -- 4. 至少有一个批次
  RETURN QUERY
    SELECT 'no_admission_rule', 'error', p_scheme::text, '没有招录规则'
     WHERE NOT EXISTS (SELECT 1 FROM policy.admission_rule r WHERE r.scheme_id = p_scheme);
  -- 5. 两名不同 staff 对当前内容批准（0041：签字人以 platform.policy_signoff 为准，自报工号不再计数；这里提前报出来）
  RETURN QUERY
    SELECT 'reviewers', 'error', p_scheme::text, '两名不同 staff 对当前内容批准的签字不足（或当前内容被驳回）'
     WHERE policy.approval_count('exam_scheme', p_scheme) < policy.signers_required();
  -- 5a. 版本号必须高于当前已发布版本（不能用旧版本撤下新版本）
  RETURN QUERY
    SELECT 'version_not_newer', 'error', p_scheme::text, '版本号不高于当前已发布版本 ' || cur.version
      FROM policy.exam_scheme cur
     WHERE cur.city = s.city AND cur.cohort = s.cohort AND cur.status = 'published'
       AND string_to_array(s.version, '.')::int[] <= string_to_array(cur.version, '.')::int[];
  -- 6. 与上一届相比总分或计分科目变了却没有过渡规则（警告）
  RETURN QUERY
    SELECT 'missing_transition', 'warning', p_scheme::text, '与上一届相比计分总分或计分科目变化，未登记过渡规则'
      FROM policy.exam_scheme prev
     WHERE prev.city = s.city AND prev.cohort = s.cohort - 1 AND prev.status = 'published'
       AND (policy.scheme_total(prev.id) IS DISTINCT FROM policy.scheme_total(p_scheme)
            OR (SELECT array_agg(subject ORDER BY subject) FROM policy.subject_config WHERE scheme_id = prev.id AND scored)
               IS DISTINCT FROM (SELECT array_agg(subject ORDER BY subject) FROM policy.subject_config WHERE scheme_id = p_scheme AND scored))
       AND NOT EXISTS (SELECT 1 FROM policy.transition_rule t WHERE t.scheme_id = p_scheme);
END $$;

-- ---------- 发布：方案（改由签字身份执行，要求 active staff；校验含两人批准；写复核时间与发布记录） ----------
CREATE OR REPLACE FUNCTION policy.publish_scheme(p_scheme uuid) RETURNS timestamptz
  LANGUAGE plpgsql SECURITY DEFINER
  SET search_path = pg_catalog, policy, pg_temp SET TimeZone = 'UTC'
  AS $$
DECLARE
  v_staff uuid := platform.review_current_staff();
  s policy.exam_scheme%ROWTYPE;
  v_errors integer;
  v_now timestamptz := now();
  v_digest text;
  v_reviewed timestamptz;
BEGIN
  SELECT * INTO s FROM policy.exam_scheme WHERE id = p_scheme FOR UPDATE;
  IF NOT FOUND OR s.status <> 'draft' THEN
    RAISE EXCEPTION '只能发布草稿方案' USING ERRCODE = 'check_violation';
  END IF;
  -- 同一城市同一届别的发布串行执行
  PERFORM pg_advisory_xact_lock(hashtext('policy.publish_scheme:' || s.city || ':' || s.cohort));
  PERFORM 1 FROM policy.exam_scheme WHERE city = s.city AND cohort = s.cohort AND status = 'published' FOR UPDATE;
  -- 锁住来源文件，校验通过后提交前它不能被并发撤下
  PERFORM 1 FROM policy.source_document WHERE id = s.source_document_id FOR SHARE;
  SELECT count(*) INTO v_errors FROM policy.validate_scheme(p_scheme) c WHERE c.severity = 'error';
  IF v_errors > 0 THEN
    RAISE EXCEPTION '发布校验有 % 个阻断问题，拒绝发布', v_errors USING ERRCODE = 'check_violation';
  END IF;
  -- 复核时间：对发布内容批准的签字里最后一个的时间（校验已保证至少两名不同 staff、当前内容没有驳回）
  v_digest := policy.object_digest('exam_scheme', p_scheme);
  SELECT max(x.signed_at) INTO v_reviewed FROM platform.policy_signoff x
   WHERE x.object_type = 'exam_scheme' AND x.object_id = p_scheme AND x.action = 'review' AND x.decision = 'approved' AND x.digest = v_digest;
  UPDATE policy.exam_scheme SET status = 'retired' WHERE city = s.city AND cohort = s.cohort AND status = 'published';
  UPDATE policy.exam_scheme SET status = 'published', published_at = v_now, reviewed_at = v_reviewed WHERE id = p_scheme;
  INSERT INTO platform.policy_signoff (object_type, exam_scheme_id, action, decision, digest, account_id, signed_at)
  VALUES ('exam_scheme', p_scheme, 'publish', 'approved', v_digest, v_staff, clock_timestamp());
  RETURN v_now;
END $$;

-- ---------- 函数属主与授权 ----------
ALTER FUNCTION policy.object_digest(text, uuid) OWNER TO starbuddy_authz;
ALTER FUNCTION policy.approval_count(text, uuid) OWNER TO starbuddy_authz;
ALTER FUNCTION policy.sign_scheme(uuid, text, text) OWNER TO starbuddy_authz;
ALTER FUNCTION policy.sign_record(text, uuid, text, text) OWNER TO starbuddy_authz;
REVOKE EXECUTE ON FUNCTION policy.object_digest(text, uuid), policy.approval_count(text, uuid), policy.sign_scheme(uuid, text, text),
  policy.sign_record(text, uuid, text, text) FROM PUBLIC;
-- 摘要：写入方（审核台后端展示）与签字身份（回传）都能算
GRANT EXECUTE ON FUNCTION policy.object_digest(text, uuid) TO starbuddy_kge, starbuddy_review;
-- 有效批准人数：validate_scheme 的调用方（写入方、学习服务）与签字身份
GRANT EXECUTE ON FUNCTION policy.approval_count(text, uuid) TO starbuddy_kge, starbuddy_learning, starbuddy_review;
-- 签字与发布：只授签字身份（spec 第 3 节：发布也由签字身份执行，写入方只能提交）
GRANT EXECUTE ON FUNCTION policy.sign_scheme(uuid, text, text), policy.sign_record(text, uuid, text, text) TO starbuddy_review;
REVOKE EXECUTE ON FUNCTION policy.publish_scheme(uuid) FROM starbuddy_kge;
GRANT EXECUTE ON FUNCTION policy.publish_scheme(uuid) TO starbuddy_review;
