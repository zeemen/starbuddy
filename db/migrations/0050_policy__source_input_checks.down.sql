-- 0050 回滚（policy 部分）：来源文件守卫（0046 原文）、url_host（0013 原文）、来源说明的核对与 CHECK、主机名的国际化域名核对
-- （add_source_domain、normalize_source_host 回到 0048 原文）。
-- 状态：草稿，由人执行，先在副本演练。请以单事务执行（psql -1）。没有数据前提：已写进清单与操作记录的说明不变（回滚只去掉 CHECK）。

-- ---------- 4. CHECK ----------
ALTER TABLE platform.policy_source_domain_change DROP CONSTRAINT policy_source_domain_change_note_visible;
ALTER TABLE policy.source_domain DROP CONSTRAINT source_domain_issuer_visible;

-- ---------- 3. 来源文件守卫（0046 原文）与 url_host（0013 原文） ----------
CREATE OR REPLACE FUNCTION policy.guard_source_document() RETURNS trigger LANGUAGE plpgsql
  SET search_path = pg_catalog, policy, pg_temp
  AS $$
BEGIN
  -- 只在新建或改链接时核对主机名：主机名下线后，挂在它下面的文件仍要能更新核对日期或撤下
  IF (TG_OP = 'INSERT' OR NEW.url IS DISTINCT FROM OLD.url)
     AND NOT EXISTS (SELECT 1 FROM policy.source_domain d WHERE d.host = policy.url_host(NEW.url) AND d.status = 'active') THEN
    RAISE EXCEPTION '来源链接的主机名不在官方来源清单里' USING ERRCODE = 'check_violation';
  END IF;
  IF TG_OP = 'INSERT' THEN
    NEW.created_at := now();
    RETURN NEW;
  END IF;
  IF OLD.status = 'retired' THEN
    RAISE EXCEPTION '已撤下的来源文件不能再改' USING ERRCODE = 'check_violation';
  END IF;
  IF OLD.status = 'draft' AND NEW.status = 'retired' THEN
    RAISE EXCEPTION '草稿不能直接撤下（放弃的草稿留在草稿；已撤下的来源文件对前端可见）' USING ERRCODE = 'check_violation';
  END IF;
  IF OLD.status = 'published' AND (NEW.city, NEW.issuer, NEW.doc_number, NEW.title, NEW.published_on, NEW.url, NEW.applies_from_cohort,
                                   NEW.applies_to_cohort, NEW.reviewers, NEW.reviewed_at, NEW.created_at)
                                  IS DISTINCT FROM
                                  (OLD.city, OLD.issuer, OLD.doc_number, OLD.title, OLD.published_on, OLD.url, OLD.applies_from_cohort,
                                   OLD.applies_to_cohort, OLD.reviewers, OLD.reviewed_at, OLD.created_at) THEN
    RAISE EXCEPTION '已发布的来源文件只能更新核对日期或撤下' USING ERRCODE = 'check_violation';
  END IF;
  IF NEW.status = 'draft' AND OLD.status <> 'draft' THEN
    RAISE EXCEPTION '来源文件不能退回草稿' USING ERRCODE = 'check_violation';
  END IF;
  RETURN NEW;
END $$;
DROP FUNCTION policy.lock_active_source_host(text);
CREATE OR REPLACE FUNCTION policy.url_host(p text) RETURNS text
  LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$ SELECT lower(substring(p FROM '^https://([^/:?#]+)')) $$;
COMMENT ON FUNCTION policy.url_host(text) IS NULL;

-- ---------- 2. 来源说明与主机名（0048 原文） ----------
CREATE OR REPLACE FUNCTION policy.add_source_domain(p_host text, p_note text) RETURNS text
  LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog, policy, pg_temp
  AS $$
DECLARE
  v_staff uuid := platform.review_current_staff();
  v_host text;
  v_note text := btrim(p_note);
  v_status text;
  v_action text;
BEGIN
  IF NOT EXISTS (SELECT 1 FROM platform.staff_permission p WHERE p.staff_id = v_staff AND p.permission = 'policy_source_admin') THEN
    RAISE EXCEPTION '没有维护官方来源清单的权限（policy_source_admin）' USING ERRCODE = 'insufficient_privilege';
  END IF;
  v_host := policy.normalize_source_host(p_host);
  IF v_host IS NULL THEN
    RAISE EXCEPTION '主机名不合格：只收域名（至少两段），不收 IP、通配符、端口、路径、空值或只有公共后缀的主机' USING ERRCODE = 'invalid_parameter_value';
  END IF;
  IF v_note IS NULL OR char_length(v_note) NOT BETWEEN 1 AND 64 THEN
    RAISE EXCEPTION '说明（发布机关）须为 1–64 个字' USING ERRCODE = 'invalid_parameter_value';
  END IF;
  SELECT d.status INTO v_status FROM policy.source_domain d WHERE d.host = v_host FOR UPDATE;
  IF v_status = 'active' THEN
    RAISE EXCEPTION '主机名 % 已在官方来源清单里', v_host USING ERRCODE = 'unique_violation';
  ELSIF v_status = 'retired' THEN
    UPDATE policy.source_domain SET status = 'active', issuer = v_note WHERE host = v_host;
    v_action := 'reactivate';
  ELSE
    INSERT INTO policy.source_domain (host, issuer, added_by) VALUES (v_host, v_note, 'review-console');
    v_action := 'add';
  END IF;
  INSERT INTO platform.policy_source_domain_change (host, action, note, actor_account_id, changed_at)
  VALUES (v_host, v_action, v_note, v_staff, clock_timestamp());
  RETURN v_host;
END $$;
COMMENT ON FUNCTION policy.add_source_domain(text, text) IS
  '新增或重新启用官方来源主机名（0048）：active staff 且有 policy_source_admin；主机名规范化；操作记录写 platform.policy_source_domain_change';
CREATE OR REPLACE FUNCTION policy.normalize_source_host(p_host text) RETURNS text
  LANGUAGE sql IMMUTABLE SET search_path = pg_catalog, pg_temp
  AS $$ SELECT h FROM (SELECT regexp_replace(lower(btrim(p_host)), '\.$', '') AS h) x
         WHERE char_length(h) BETWEEN 4 AND 253
           AND h ~ '^([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]([a-z0-9-]{0,61}[a-z0-9])?$'
           AND h <> ALL (ARRAY['gov.cn', 'com.cn', 'edu.cn', 'org.cn', 'net.cn', 'ac.cn', 'mil.cn', 'js.cn', 'sh.cn', 'bj.cn',
                               'com.hk', 'edu.hk', 'gov.hk', 'org.hk', 'net.hk', 'co.uk', 'ac.uk', 'gov.uk', 'org.uk',
                               'com.tw', 'edu.tw', 'gov.tw', 'org.tw']) $$;
COMMENT ON FUNCTION policy.normalize_source_host(text) IS
  '官方来源主机名规范化（0048）：去首尾空白、小写、去一个结尾的点；只收至少两段的域名，末段以字母开头；'
  'IP、通配符、端口、路径、空值、只有公共后缀（gov.cn、com.cn 等）的返回 NULL';
DROP FUNCTION policy.idn_label_ok(text);

-- ---------- 1. 不可见字符码位表 ----------
DROP FUNCTION policy.has_hidden_chars(text);
DROP FUNCTION policy.is_hidden_codepoint(integer);
