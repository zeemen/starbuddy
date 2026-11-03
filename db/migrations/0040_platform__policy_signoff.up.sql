-- 0040 platform · 考情政策线的签字人表（specs/release-gate-sprint4/spec.md 第 3、8 节、冲刺 4 段；第二部分 PR C）
-- 状态：草稿。只写文件，由人先在副本演练再执行（CLAUDE.md 必停项：迁移、RLS 与权限）。
-- 依赖 0001（账号）、0013（policy 表）、0018（只追加触发器函数 platform.reject_signoff_change）、0035（个人层表清单）；
-- 0041（policy 签字与发布函数）依赖本迁移。请以单事务执行（psql -1）。
-- 为什么签字人不放在 policy 表里：签字人是 staff 账号，属于平台个人层；policy 是公共基础数据，不能存任何账号引用
-- （规则 13；db/tests/80_attack_surface.sql 的列名、外键、uuid 列与金丝雀四条断言对 kge、policy 都生效）。
-- 与 0018 / 0019 同一做法：签字人记在 platform，按对象 ID 引用 policy；按 ADR 02“每个迁移只改一个 schema”拆成 0040（platform）与 0041（policy）。
-- 考情的复核人仍不在 kge.content_review 里记（规则 2，spec 第 8 节）；policy 表上的自报工号列 reviewers 保留为展示字段（spec 待人确认 #4 默认）。
-- 只追加：签字写入后不能改、不能删、不能清空；只有签字函数的属主 starbuddy_authz 能读写（BYPASSRLS）。其他应用角色没有任何权限，
-- 只能经只读导出函数 platform.policy_signoff_record()（授 kge、review）读签字记录。

CREATE TABLE platform.policy_signoff (
  id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  object_type         text NOT NULL CHECK (object_type IN ('source_document', 'exam_scheme', 'admission_line', 'school')),
  source_document_id  uuid REFERENCES policy.source_document (id),
  exam_scheme_id      uuid REFERENCES policy.exam_scheme (id),
  admission_line_id   uuid REFERENCES policy.admission_line (id),
  -- 高中学校（policy.school）。列名不用 school_id：platform 里不得出现姓名或学校类的列名（渗透测试夹具自检，db/pentest/fixtures.sql）
  institution_id      uuid REFERENCES policy.school (id),
  object_id           uuid GENERATED ALWAYS AS (coalesce(source_document_id, exam_scheme_id, admission_line_id, institution_id)) STORED,
  -- review：一名 staff 对对象当前内容的结论；publish：发布（方案由 policy.publish_scheme，其余三类在第二名批准时由签字函数发布）
  action              text NOT NULL CHECK (action IN ('review', 'publish')),
  decision            text NOT NULL CHECK (decision IN ('approved', 'rejected')),
  digest              text NOT NULL CHECK (digest ~ '^[0-9a-f]{64}$'),  -- 签字时对象内容的摘要（policy.object_digest）：内容改过后旧签字不再计数
  account_id          uuid NOT NULL REFERENCES platform.account (id),
  signed_at           timestamptz NOT NULL,
  CONSTRAINT policy_signoff_one_object CHECK (
    num_nonnulls(source_document_id, exam_scheme_id, admission_line_id, institution_id) = 1
    AND CASE object_type
          WHEN 'source_document' THEN source_document_id IS NOT NULL
          WHEN 'exam_scheme' THEN exam_scheme_id IS NOT NULL
          WHEN 'admission_line' THEN admission_line_id IS NOT NULL
          ELSE institution_id IS NOT NULL
        END),
  CONSTRAINT policy_signoff_publish_approved CHECK (action = 'review' OR decision = 'approved'),
  -- 同一账号对同一对象的同一内容只能签一次（“同一账号签两次只算一人”由此与去重计数共同保证）
  CONSTRAINT policy_signoff_once UNIQUE (object_type, object_id, action, account_id, digest)
);
COMMENT ON TABLE platform.policy_signoff IS
  '考情政策线（来源文件、方案、招录行、学校）的签字人：只由 policy.sign_scheme / sign_record / publish_scheme 写入（0041）；'
  '两名不同 staff 对当前内容批准才能发布；导出经 platform.policy_signoff_record()';
CREATE INDEX policy_signoff_account_idx ON platform.policy_signoff (account_id);

CREATE TRIGGER reject_policy_signoff_change BEFORE UPDATE OR DELETE ON platform.policy_signoff
  FOR EACH ROW EXECUTE FUNCTION platform.reject_signoff_change();
CREATE TRIGGER reject_policy_signoff_truncate BEFORE TRUNCATE ON platform.policy_signoff
  FOR EACH STATEMENT EXECUTE FUNCTION platform.reject_signoff_change();

GRANT SELECT, INSERT ON platform.policy_signoff TO starbuddy_authz;
ALTER TABLE platform.policy_signoff ENABLE ROW LEVEL SECURITY;
ALTER TABLE platform.policy_signoff FORCE ROW LEVEL SECURITY;
-- 没有任何策略：只有 BYPASSRLS 的 starbuddy_authz（签字函数属主）能读写；BFF、学习服务、知识服务、签字身份都读不到签字人表本身

-- ---------- 版本记录导出（spec 第 8、11 节“版本记录导出时从两处合并”）：每行一次签字或发布，带签字人 ----------
-- 签字人表只有 authz 能读，所以经这个只读函数给审核台后端（kge 连接）与签字身份（review）；只返回考情对象的 ID、城市、方案版本、届别与 staff 账号，不含内容。
-- 放在本迁移（platform）而不是 0041（policy）：函数读的是 platform 的签字人表，按 ADR 02 跟表走；它对 policy 表的引用与上面的外键一样只依赖 0013。
-- 执行时属主 authz 还要读 policy.school、policy.admission_line（0041 授予）；0040 与 0041 一起执行，0040 单独存在时签字人表为空，这个函数也用不到。
CREATE FUNCTION platform.policy_signoff_record()
  RETURNS TABLE (object_type text, object_id uuid, city text, version text, cohort integer, action text, decision text,
                 digest text, signed_at timestamptz, signer uuid)
  LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, platform, pg_temp
  AS $$
  SELECT x.object_type, x.object_id, coalesce(d.city, e.city, lh.city, h.city)::text, e.version,
         coalesce(e.cohort, l.year)::integer, x.action, x.decision, x.digest, x.signed_at, x.account_id
    FROM platform.policy_signoff x
    LEFT JOIN policy.source_document d ON d.id = x.source_document_id
    LEFT JOIN policy.exam_scheme e ON e.id = x.exam_scheme_id
    LEFT JOIN policy.admission_line l ON l.id = x.admission_line_id
    LEFT JOIN policy.school lh ON lh.id = l.school_id
    LEFT JOIN policy.school h ON h.id = x.institution_id
   ORDER BY x.signed_at, x.action = 'publish', x.id $$;
ALTER FUNCTION platform.policy_signoff_record() OWNER TO starbuddy_authz;
REVOKE EXECUTE ON FUNCTION platform.policy_signoff_record() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION platform.policy_signoff_record() TO starbuddy_kge, starbuddy_review;

-- 个人层表清单（0035）：签字人是账号键，但只会是教研账号，按豁免登记（同 content_signoff、release_record）
INSERT INTO platform.personal_table_registry (nspname, relname, key_path, student_predicate, strategy, exempt_reason)
VALUES ('platform', 'policy_signoff', 'account_id（只有教研账号）',
        'account_id = (SELECT sp.account_id FROM platform.student_profile sp WHERE sp.id = $1)', 'exempt',
        '考情政策线的审核签字，只含教研账号');
