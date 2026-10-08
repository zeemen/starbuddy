-- 0050 policy · 考情 v0.9 待人确认事项按默认处理：policy 部分（specs/policy-decisions-v0.9.1/spec.md“PR3”，lessons“待人确认”#9、#13）
-- 状态：草稿。只写文件，由人先在副本演练再执行（CLAUDE.md 必停项：迁移、RLS 与权限）。请以单事务执行（psql -1）。
-- 依赖 0013（policy.url_host、source_domain）、0046（guard_source_document 现行版本）、0048（normalize_source_host、add_source_domain、
-- platform.policy_source_domain_change）。与 0049（platform 部分）互不依赖。
-- 跨 schema：platform.policy_source_domain_change.note 的 CHECK 引用 policy.has_hidden_chars——归入 0048 已登记的“来源清单”例外
-- （操作记录只能在 platform，说明的核对规则在 policy，两边必须是同一条规则；ADR 02）。
-- 要点：
--   1. #9 来源说明（add_source_domain 的 note，写进清单行的 issuer 与操作记录的 note）拒绝 Unicode Cc、Cf、Zl、Zp：
--      policy.is_hidden_codepoint 按码位区间判断（Unicode 15.1 的这四类，不含 U+0000：text 存不了；以后 Unicode 新增的格式字符要人补表），
--      policy.has_hidden_chars 逐字判断。函数里先以 22023 拒绝（审核台给固定说明），policy.source_domain.issuer 与
--      platform.policy_source_domain_change.note 加 CHECK 兜底（迁移或 DBA 直接写也拦）。源码里不嵌入任何不可见字符，码位一律写十六进制数；
--   2. #9 policy.normalize_source_host 另外拒绝不能往返解码的 xn-- 标签：policy.idn_label_ok 按 RFC 3492 在 plpgsql 里解码再编码，
--      与原标签逐字相同才收；标签超过 63 个字符一律不收；前缀不分大小写（XN-- 也按 A 标签核对）；解码出的码位必须至少一个非 ASCII，
--      不能是代理区、超出 U+10FFFF、控制与格式字符、空白或句号的变体。
--      局限：Postgres 没有内置的 IDNA；这里不做 IDNA2008 的码位表、NFC、双向文字规则与同形字判断（同形字由审核台显示成 Unicode 并警告，PR2）；
--      xn-- 以外的保留前缀（如 ab--）不拦；
--   3. #13 policy.url_host 拒绝显式端口（主机名后面紧跟 : 时返回 NULL；来源文件的 url CHECK 本来就不收端口，这里让函数自身也不收）；
--      policy.guard_source_document 核对主机名时对清单行加 FOR SHARE：与并发的下线（retire_source_domain 对同一行 FOR UPDATE）串行，
--      下线提交后不会再进来挂在它下面的新来源文件。锁要 UPDATE 权限，写入方 kge 没有（应用角色不能写清单），所以经属主 authz 的
--      SECURITY DEFINER 函数 policy.lock_active_source_host 取锁，只授 kge；
--   4. 数据前提：已有的清单说明与操作记录不含要拒绝的字符；已有的清单主机名都已是规范形式（等于 normalize_source_host(host)，
--      含新的 xn-- 核对）。不满足就中止并给出明确信息，由人先处理。

-- ---------- 1. 不可见字符（Unicode Cc、Cf、Zl、Zp）的码位表 ----------
CREATE FUNCTION policy.is_hidden_codepoint(p integer) RETURNS boolean
  LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE SET search_path = pg_catalog, pg_temp
  AS $$ SELECT p BETWEEN 0x1 AND 0x1F OR p BETWEEN 0x7F AND 0x9F                         -- Cc（含 C1）
            OR p = 0xAD OR p BETWEEN 0x600 AND 0x605 OR p = 0x61C OR p = 0x6DD OR p = 0x70F  -- Cf
            OR p BETWEEN 0x890 AND 0x891 OR p = 0x8E2 OR p = 0x180E OR p BETWEEN 0x200B AND 0x200F
            OR p BETWEEN 0x2028 AND 0x2029                                               -- Zl、Zp
            OR p BETWEEN 0x202A AND 0x202E OR p BETWEEN 0x2060 AND 0x2064 OR p BETWEEN 0x2066 AND 0x206F
            OR p = 0xFEFF OR p BETWEEN 0xFFF9 AND 0xFFFB OR p = 0x110BD OR p = 0x110CD OR p BETWEEN 0x13430 AND 0x1343F
            OR p BETWEEN 0x1BCA0 AND 0x1BCA3 OR p BETWEEN 0x1D173 AND 0x1D17A OR p = 0xE0001 OR p BETWEEN 0xE0020 AND 0xE007F $$;
CREATE FUNCTION policy.has_hidden_chars(p text) RETURNS boolean
  LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE SET search_path = pg_catalog, pg_temp
  AS $$ SELECT EXISTS (SELECT 1 FROM regexp_split_to_table(p, '') c WHERE policy.is_hidden_codepoint(ascii(c))) $$;
REVOKE EXECUTE ON FUNCTION policy.is_hidden_codepoint(integer), policy.has_hidden_chars(text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION policy.is_hidden_codepoint(integer), policy.has_hidden_chars(text) TO starbuddy_authz;
COMMENT ON FUNCTION policy.is_hidden_codepoint(integer) IS
  '码位是不是 Unicode 15.1 的 Cc、Cf、Zl、Zp（0050；不含 U+0000）。来源说明与国际化域名核对共用；新版 Unicode 增加的格式字符要人补表';
COMMENT ON FUNCTION policy.has_hidden_chars(text) IS '文本里有没有 Cc、Cf、Zl、Zp 字符（0050，逐字按 policy.is_hidden_codepoint）';

-- ---------- 2. 国际化域名标签与主机名规范化（#9） ----------
CREATE FUNCTION policy.idn_label_ok(p_label text) RETURNS boolean
  LANGUAGE plpgsql IMMUTABLE STRICT SET search_path = pg_catalog, pg_temp
  AS $$
DECLARE
  c_base constant integer := 36; c_tmin constant integer := 1; c_tmax constant integer := 26;
  c_skew constant integer := 38; c_damp constant integer := 700;
  v_in text; v_len integer; v_last integer; v_pos integer;
  v_out integer[] := '{}'; v_n bigint := 128; v_i bigint := 0; v_bias integer := 72;
  v_oldi bigint; v_w bigint; v_k integer; v_t integer; v_digit integer; v_ch integer; v_outlen integer; v_d bigint; v_kk integer;
  v_enc text; v_basic integer; v_h integer; v_m bigint; v_q bigint; v_delta bigint := 0; v_cp integer;
BEGIN
  IF char_length(p_label) > 63 THEN
    RETURN false;  -- DNS 标签最长 63 个字符（也挡住超长输入的解码开销）
  END IF;
  IF lower(left(p_label, 4)) <> 'xn--' THEN
    RETURN true;
  END IF;
  v_in := substr(p_label, 5);
  v_len := char_length(v_in);
  IF v_len = 0 OR v_in !~ '^[a-z0-9-]+$' THEN
    RETURN false;
  END IF;
  -- 解码（RFC 3492 6.2）：最后一个连字符之前是原样的基本码位
  v_last := CASE WHEN strpos(v_in, '-') = 0 THEN 0 ELSE v_len - strpos(reverse(v_in), '-') + 1 END;
  FOR v_pos IN 1 .. v_last - 1 LOOP
    v_out := v_out || ascii(substr(v_in, v_pos, 1));
  END LOOP;
  v_pos := CASE WHEN v_last > 0 THEN v_last + 1 ELSE 1 END;
  WHILE v_pos <= v_len LOOP
    v_oldi := v_i; v_w := 1; v_k := c_base;
    LOOP
      IF v_pos > v_len THEN RETURN false; END IF;
      v_ch := ascii(substr(v_in, v_pos, 1));
      v_digit := CASE WHEN v_ch BETWEEN 97 AND 122 THEN v_ch - 97 WHEN v_ch BETWEEN 48 AND 57 THEN v_ch - 22 END;
      IF v_digit IS NULL THEN RETURN false; END IF;
      v_pos := v_pos + 1;
      v_i := v_i + v_digit * v_w;
      IF v_i > 2147483647 THEN RETURN false; END IF;
      v_t := CASE WHEN v_k <= v_bias THEN c_tmin WHEN v_k >= v_bias + c_tmax THEN c_tmax ELSE v_k - v_bias END;
      EXIT WHEN v_digit < v_t;
      v_w := v_w * (c_base - v_t);
      IF v_w > 2147483647 THEN RETURN false; END IF;
      v_k := v_k + c_base;
    END LOOP;
    v_outlen := cardinality(v_out) + 1;
    v_d := CASE WHEN v_oldi = 0 THEN (v_i - v_oldi) / c_damp ELSE (v_i - v_oldi) / 2 END;
    v_d := v_d + v_d / v_outlen;
    v_kk := 0;
    WHILE v_d > ((c_base - c_tmin) * c_tmax) / 2 LOOP
      v_d := v_d / (c_base - c_tmin);
      v_kk := v_kk + c_base;
    END LOOP;
    v_bias := v_kk + ((c_base - c_tmin + 1) * v_d) / (v_d + c_skew);
    v_n := v_n + v_i / v_outlen;
    v_i := v_i % v_outlen;
    IF v_n > 0x10FFFF OR v_n BETWEEN 0xD800 AND 0xDFFF THEN RETURN false; END IF;
    v_out := v_out[1:v_i] || v_n::integer || v_out[v_i + 1:];
    v_i := v_i + 1;
  END LOOP;
  IF NOT EXISTS (SELECT 1 FROM unnest(v_out) c WHERE c >= 128) THEN
    RETURN false;  -- 全是 ASCII 的 xn-- 标签不是合法的 A 标签
  END IF;
  -- 解码出的码位：不收控制与格式字符、行段分隔符（同来源说明），空白（Zs），以及 IDNA 里等于点的句号变体（。．｡﹒）
  IF EXISTS (SELECT 1 FROM unnest(v_out) c
              WHERE policy.is_hidden_codepoint(c)
                 OR c IN (0x20, 0xA0, 0x1680, 0x202F, 0x205F, 0x3000) OR c BETWEEN 0x2000 AND 0x200A
                 OR c IN (0x3002, 0xFE52, 0xFF0E, 0xFF61)) THEN
    RETURN false;
  END IF;
  -- 重新编码（RFC 3492 6.3），必须与原标签逐字相同
  SELECT coalesce(string_agg(chr(c), '' ORDER BY o), ''), count(*) INTO v_enc, v_basic
    FROM unnest(v_out) WITH ORDINALITY u(c, o) WHERE c < 128;
  IF v_basic > 0 THEN v_enc := v_enc || '-'; END IF;
  v_n := 128; v_bias := 72; v_h := v_basic;
  WHILE v_h < cardinality(v_out) LOOP
    SELECT min(c) INTO v_m FROM unnest(v_out) c WHERE c >= v_n;
    v_delta := v_delta + (v_m - v_n) * (v_h + 1);
    v_n := v_m;
    FOREACH v_cp IN ARRAY v_out LOOP
      IF v_cp < v_n THEN v_delta := v_delta + 1; END IF;
      IF v_cp = v_n THEN
        v_q := v_delta; v_k := c_base;
        LOOP
          v_t := CASE WHEN v_k <= v_bias THEN c_tmin WHEN v_k >= v_bias + c_tmax THEN c_tmax ELSE v_k - v_bias END;
          EXIT WHEN v_q < v_t;
          v_d := v_t + (v_q - v_t) % (c_base - v_t);
          v_enc := v_enc || chr((CASE WHEN v_d < 26 THEN v_d + 97 ELSE v_d + 22 END)::integer);
          v_q := (v_q - v_t) / (c_base - v_t);
          v_k := v_k + c_base;
        END LOOP;
        v_enc := v_enc || chr((CASE WHEN v_q < 26 THEN v_q + 97 ELSE v_q + 22 END)::integer);
        v_d := CASE WHEN v_h = v_basic THEN v_delta / c_damp ELSE v_delta / 2 END;
        v_d := v_d + v_d / (v_h + 1);
        v_kk := 0;
        WHILE v_d > ((c_base - c_tmin) * c_tmax) / 2 LOOP
          v_d := v_d / (c_base - c_tmin);
          v_kk := v_kk + c_base;
        END LOOP;
        v_bias := v_kk + ((c_base - c_tmin + 1) * v_d) / (v_d + c_skew);
        v_delta := 0;
        v_h := v_h + 1;
      END IF;
    END LOOP;
    v_delta := v_delta + 1;
    v_n := v_n + 1;
  END LOOP;
  RETURN v_enc = v_in;
END $$;
REVOKE EXECUTE ON FUNCTION policy.idn_label_ok(text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION policy.idn_label_ok(text) TO starbuddy_authz;
COMMENT ON FUNCTION policy.idn_label_ok(text) IS
  '主机名标签的国际化域名核对（0050）：超过 63 个字符不收；xn-- 标签（不分大小写）按 RFC 3492 解码再编码须逐字相同，解码出的码位'
  '至少一个非 ASCII，不含代理区、控制与格式字符、空白与句号变体；其余标签返回 true。不做 IDNA2008 码位表、NFC、双向文字规则与同形字判断';

-- 规范化：与 0048 相同，另加“每个 xn-- 标签都能往返解码”
CREATE OR REPLACE FUNCTION policy.normalize_source_host(p_host text) RETURNS text
  LANGUAGE sql IMMUTABLE SET search_path = pg_catalog, pg_temp
  AS $$ SELECT h FROM (SELECT regexp_replace(lower(btrim(p_host)), '\.$', '') AS h) x
         WHERE char_length(h) BETWEEN 4 AND 253
           AND h ~ '^([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]([a-z0-9-]{0,61}[a-z0-9])?$'
           AND h <> ALL (ARRAY['gov.cn', 'com.cn', 'edu.cn', 'org.cn', 'net.cn', 'ac.cn', 'mil.cn', 'js.cn', 'sh.cn', 'bj.cn',
                               'com.hk', 'edu.hk', 'gov.hk', 'org.hk', 'net.hk', 'co.uk', 'ac.uk', 'gov.uk', 'org.uk',
                               'com.tw', 'edu.tw', 'gov.tw', 'org.tw'])
           AND NOT EXISTS (SELECT 1 FROM unnest(string_to_array(h, '.')) l WHERE NOT policy.idn_label_ok(l)) $$;
COMMENT ON FUNCTION policy.normalize_source_host(text) IS
  '官方来源主机名规范化（0048，0050 加国际化域名核对）：去首尾空白、小写、去一个结尾的点；只收至少两段的域名，末段以字母开头；'
  'IP、通配符、端口、路径、空值、只有公共后缀（gov.cn、com.cn 等）、不能往返解码的 xn-- 标签返回 NULL';

-- 来源说明：拒绝 Cc、Cf、Zl、Zp（函数先拒，表上 CHECK 兜底）
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
  IF policy.has_hidden_chars(v_note) THEN
    RAISE EXCEPTION '说明（发布机关）不能含控制字符、格式字符或行段分隔符' USING ERRCODE = 'invalid_parameter_value';
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
  '新增或重新启用官方来源主机名（0048，0050 加输入核对）：active staff 且有 policy_source_admin；主机名规范化（含 xn-- 往返解码）；'
  '说明不能含 Cc、Cf、Zl、Zp；操作记录写 platform.policy_source_domain_change';

-- ---------- 3. url_host 拒绝显式端口；来源文件核对主机名时锁清单行（#13） ----------
CREATE OR REPLACE FUNCTION policy.url_host(p text) RETURNS text
  LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$ SELECT lower(substring(p FROM '^https://([^/:?#]+)(?:[/?#]|$)')) $$;
COMMENT ON FUNCTION policy.url_host(text) IS
  '从 https 链接取出主机名（小写；0013，0050 起带显式端口的返回 NULL）';

-- 清单行的共享锁：FOR SHARE 要 UPDATE 权限，写入方 kge 没有（应用角色不能写清单），由属主 authz 代取；锁持有到调用方事务结束
CREATE FUNCTION policy.lock_active_source_host(p_host text) RETURNS boolean
  LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog, policy, pg_temp
  AS $$
BEGIN
  PERFORM 1 FROM policy.source_domain d WHERE d.host = p_host AND d.status = 'active' FOR SHARE;
  RETURN FOUND;
END $$;
ALTER FUNCTION policy.lock_active_source_host(text) OWNER TO starbuddy_authz;
REVOKE EXECUTE ON FUNCTION policy.lock_active_source_host(text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION policy.lock_active_source_host(text) TO starbuddy_kge;
COMMENT ON FUNCTION policy.lock_active_source_host(text) IS
  '来源文件守卫用（0050）：主机名在官方来源清单里且 active 时对该行加共享锁并返回 true（与并发的下线串行）；只授写入方 kge';

CREATE OR REPLACE FUNCTION policy.guard_source_document() RETURNS trigger LANGUAGE plpgsql
  SET search_path = pg_catalog, policy, pg_temp
  AS $$
BEGIN
  -- 只在新建或改链接时核对主机名：主机名下线后，挂在它下面的文件仍要能更新核对日期或撤下。
  -- 核对时对清单行加共享锁（0050）：并发的下线（retire_source_domain 对同一行 FOR UPDATE）提交后才判断，下线后不会再进来新文件
  IF (TG_OP = 'INSERT' OR NEW.url IS DISTINCT FROM OLD.url)
     AND NOT coalesce(policy.lock_active_source_host(policy.url_host(NEW.url)), false) THEN
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

-- ---------- 4. 数据前提，然后加 CHECK ----------
DO $$
DECLARE v_domain bigint; v_change bigint; v_hosts text;
BEGIN
  SELECT count(*) INTO v_domain FROM policy.source_domain WHERE policy.has_hidden_chars(issuer);
  SELECT count(*) INTO v_change FROM platform.policy_source_domain_change WHERE policy.has_hidden_chars(note);
  IF v_domain + v_change > 0 THEN
    RAISE EXCEPTION '0050 中止：官方来源清单有 % 行说明、操作记录有 % 条说明含控制或格式字符（Cc、Cf、Zl、Zp）；请人先处理', v_domain, v_change
      USING ERRCODE = 'object_not_in_prerequisite_state';
  END IF;
  SELECT string_agg(host, '、' ORDER BY host) INTO v_hosts FROM policy.source_domain
   WHERE policy.normalize_source_host(host) IS DISTINCT FROM host;
  IF v_hosts IS NOT NULL THEN
    RAISE EXCEPTION '0050 中止：官方来源清单里这些主机名不是规范形式（不等于 normalize_source_host 的结果，含 xn-- 往返核对）：%；请人先处理', left(v_hosts, 500)
      USING ERRCODE = 'object_not_in_prerequisite_state';
  END IF;
END $$;
ALTER TABLE policy.source_domain ADD CONSTRAINT source_domain_issuer_visible CHECK (NOT policy.has_hidden_chars(issuer));
ALTER TABLE platform.policy_source_domain_change ADD CONSTRAINT policy_source_domain_change_note_visible
  CHECK (note IS NULL OR NOT policy.has_hidden_chars(note));
