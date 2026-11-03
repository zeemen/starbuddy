-- 0038 kge · 编译页面审核记录（specs/release-gate-sprint4/spec.md 第 6 节“审核记录对账”、冲刺 3 段；第二部分 PR A）
-- 状态：草稿。只写文件，由人先在副本演练再执行（CLAUDE.md 必停项：迁移、权限）。依赖 0011、0018、0019。请以单事务执行（psql -1）。
-- 要点：
--   1. kge.content_review.object_type 加 'page'；新列 page_commit（40 位小写十六进制），只有页面记录必填，其他对象必须为空。
--      page_commit 的口径是“合并请求的头提交”：审核人在托管平台上批准时看到的那个提交（合并进主分支的合并提交在对账时由合并记录对照给出）；
--   2. 页面记录一条 = 一名 staff 对“某页面在某个合并请求头提交”的一次签字（批准或驳回），签字人照旧记在 platform.content_signoff（0018）。
--      合并请求的批准人可能有两名（高风险），而签字人表一条审核记录只有一个签字人，所以同一页面同一提交可以有多条记录，
--      按签字序号 page_signer_no（1、2、……）区分：唯一约束 (页面, 提交号, 签字序号) 保证同一页面同一提交的同一签字位不能记两次；
--      同一 staff 对同一页面同一提交只能签一次由签字函数在咨询锁下核对（签字人在 platform，kge 不存账号引用，规则 13）；
--      同一页面同一提交的各条签字风险级别必须一致（以第一条为准）；
--   3. 只追加语义不变（0019 的触发器照旧拦 UPDATE、DELETE、TRUNCATE）；页面记录只能经 kge.sign_page_review 写入
--      （知识服务的 INSERT 策略只收“待审”，而页面记录不能是待审），授权与 RLS 不放宽；
--   4. kge.page_review_record(学科)：页面审核记录与签字人的只读导出（学科为空时全部；按页面 ID 的学科前缀过滤），给审核台后端（对账、页面入口、版本记录）。
-- 页面 ID = 页面仓库里的页面 ID（与节点 ID 同一写法，kge.node_id 域的正则）。
-- 托管平台不连库：批准人的对应、合并记录的比对在审核台后端的对账脚本里做（review_console.pages）。

ALTER TABLE kge.content_review
  DROP CONSTRAINT content_review_object_type_check,
  ADD CONSTRAINT content_review_object_type_check CHECK (object_type IN ('map_version', 'knowledge_node', 'knowledge_edge', 'textbook_projection',
                                                                       'region_projection', 'item_tag', 'scoring_point', 'current_affair',
                                                                       'map_change', 'page')),
  ADD COLUMN page_commit text,
  ADD COLUMN page_signer_no smallint,
  -- 页面记录：提交号 40 位小写十六进制、签字序号 1–16、有结论（不是待审）、动作是审核、页面 ID 合节点 ID 写法；其他对象两列都为空
  ADD CONSTRAINT content_review_page_commit CHECK (
    CASE WHEN object_type = 'page'
         THEN page_commit IS NOT NULL AND page_commit ~ '^[0-9a-f]{40}$'
              AND page_signer_no IS NOT NULL AND page_signer_no BETWEEN 1 AND 16
              AND decision <> 'pending' AND action = 'review'
              AND object_id ~ '^[A-Z][A-Z0-9]{1,9}(\.[A-Z0-9][A-Z0-9_]{0,31}){1,4}$'
         ELSE page_commit IS NULL AND page_signer_no IS NULL END);
COMMENT ON COLUMN kge.content_review.page_commit IS '页面记录：合并请求的头提交（审核人批准时看到的提交，40 位小写十六进制）；其他对象为空（0038）';
COMMENT ON COLUMN kge.content_review.page_signer_no IS '页面记录：同一页面同一提交的签字序号（1 起，签字函数按顺序分配）；其他对象为空（0038）';

-- 同一页面同一提交的同一签字位只能记一次
CREATE UNIQUE INDEX content_review_page_uq ON kge.content_review (object_id, page_commit, page_signer_no) WHERE object_type = 'page';

-- ---------- 签字：页面 ----------
-- 签字人取当前登录的 active staff（不能自报）；摘要是“页面 ID + 提交号”（提交号本身就标识了内容，Git 保证）；
-- 风险级别由调用方（审核台后端，按合并请求上的风险标注）传入；同一页面同一提交的后续签字必须与第一条一致；
-- 对账时按页面记录与合并记录中较高的一方算所需人数
CREATE FUNCTION kge.sign_page_review(p_page_id text, p_page_commit text, p_decision text, p_risk_level text, p_basis_code text)
  RETURNS uuid
  LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog, kge, platform, pg_temp SET TimeZone = 'UTC'
  AS $$
DECLARE
  v_staff uuid := kge.current_staff();
  v_id uuid;
  v_no integer;
  v_first_risk text;
  v_ts timestamptz := clock_timestamp();
BEGIN
  IF p_decision IS NULL OR p_decision NOT IN ('approved', 'rejected') THEN
    RAISE EXCEPTION '审核结论只能是 approved 或 rejected' USING ERRCODE = 'invalid_parameter_value';
  END IF;
  IF p_page_commit IS NULL OR p_page_commit !~ '^[0-9a-f]{40}$' THEN
    RAISE EXCEPTION '提交号必须是 40 位小写十六进制' USING ERRCODE = 'invalid_parameter_value';
  END IF;
  IF p_page_id IS NULL OR p_page_id !~ '^[A-Z][A-Z0-9]{1,9}(\.[A-Z0-9][A-Z0-9_]{0,31}){1,4}$' THEN
    RAISE EXCEPTION '页面 ID 写法不对' USING ERRCODE = 'invalid_parameter_value';
  END IF;
  IF p_risk_level IS NULL OR p_risk_level NOT IN ('low', 'medium', 'high') THEN
    RAISE EXCEPTION '风险级别只能是 low、medium、high' USING ERRCODE = 'invalid_parameter_value';
  END IF;
  -- 同一页面同一提交的签字串行（双参数咨询锁：命名空间 + 键）：查重、风险核对与分配签字序号不会交错
  PERFORM pg_advisory_xact_lock(hashtext('kge.sign_page_review'), hashtext(p_page_id || '@' || p_page_commit));
  IF EXISTS (SELECT 1 FROM kge.content_review r JOIN platform.content_signoff s ON s.content_review_id = r.id
              WHERE r.object_type = 'page' AND r.object_id = p_page_id AND r.page_commit = p_page_commit AND s.account_id = v_staff) THEN
    RAISE EXCEPTION '同一账号对同一页面同一提交只能记一次' USING ERRCODE = 'unique_violation';
  END IF;
  SELECT r.risk_level INTO v_first_risk FROM kge.content_review r
   WHERE r.object_type = 'page' AND r.object_id = p_page_id AND r.page_commit = p_page_commit AND r.page_signer_no = 1;
  IF v_first_risk IS NOT NULL AND v_first_risk <> p_risk_level THEN
    RAISE EXCEPTION '风险级别与该页面该提交的第一条签字不一致（第一条为 %）', v_first_risk USING ERRCODE = 'check_violation';
  END IF;
  SELECT coalesce(max(r.page_signer_no), 0) + 1 INTO v_no FROM kge.content_review r
   WHERE r.object_type = 'page' AND r.object_id = p_page_id AND r.page_commit = p_page_commit;
  INSERT INTO kge.content_review (object_type, object_id, risk_level, decision, decided_at, digest, basis_code, page_commit, page_signer_no)
  VALUES ('page', p_page_id, p_risk_level, p_decision, v_ts,
          kge.row_digest(jsonb_build_object('page_id', p_page_id, 'page_commit', p_page_commit)), p_basis_code, p_page_commit, v_no)
  RETURNING id INTO v_id;
  INSERT INTO platform.content_signoff (content_review_id, account_id, signed_at) VALUES (v_id, v_staff, v_ts);
  RETURN v_id;
END $$;
ALTER FUNCTION kge.sign_page_review(text, text, text, text, text) OWNER TO starbuddy_authz;
REVOKE EXECUTE ON FUNCTION kge.sign_page_review(text, text, text, text, text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION kge.sign_page_review(text, text, text, text, text) TO starbuddy_review;

-- ---------- 页面审核记录导出（对账、审核台页面入口、版本记录） ----------
-- 签字人表只有 authz 能读，所以经这个只读函数给审核台后端（kge）与签字身份（review）；只返回页面 ID、提交号、结论、摘要与 staff 账号。
-- p_subject 为空时返回全部；否则只返回页面 ID 以该学科前缀（kge.subject_prefix）开头的
CREATE FUNCTION kge.page_review_record(p_subject platform.subject)
  RETURNS TABLE (review_id uuid, page_id text, page_commit text, signer_no smallint, risk_level text, decision text,
                 basis_code text, digest text, decided_at timestamptz, signer uuid)
  LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, kge, platform, pg_temp
  AS $$
  SELECT r.id, r.object_id, r.page_commit, r.page_signer_no, r.risk_level, r.decision, r.basis_code, r.digest, r.decided_at, s.account_id
    FROM kge.content_review r
    JOIN platform.content_signoff s ON s.content_review_id = r.id
   WHERE r.object_type = 'page'
     AND (p_subject IS NULL OR starts_with(r.object_id, kge.subject_prefix(p_subject) || '.'))
   ORDER BY r.object_id, r.page_commit, r.page_signer_no $$;
ALTER FUNCTION kge.page_review_record(platform.subject) OWNER TO starbuddy_authz;
REVOKE EXECUTE ON FUNCTION kge.page_review_record(platform.subject) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION kge.page_review_record(platform.subject) TO starbuddy_kge, starbuddy_review;
