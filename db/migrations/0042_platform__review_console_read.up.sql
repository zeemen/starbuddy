-- 0042 platform · 审核台读内容版本与发布记录（specs/release-gate-sprint4/spec.md 第 7、11 节、冲刺 4 段；第二部分 PR D）
-- 状态：草稿。只写文件，由人先在副本演练再执行（CLAUDE.md 必停项：迁移、权限）。依赖 0003、0039。请以单事务执行（psql -1）。
-- 只改 platform schema（ADR 02）。
-- 为什么需要：审核台后端的两个连接身份（starbuddy_kge 读与改草稿、starbuddy_review 签字）都读不到 platform.curriculum_version、
-- skill_node、item（0003 只授 bff 与 learning），也读不到 platform.release_record（0039：强制 RLS、无策略、只 authz）。
-- 内容版本的差异视图（spec 第 7 节）、签字时回传的摘要、版本记录导出的内容版本行（第 11 节）都要读这些。
-- 做法与 0038 kge.page_review_record、0040 platform.policy_signoff_record 相同：只读的 SECURITY DEFINER 函数，属主 authz，
-- 只授 starbuddy_kge（审核台后端的读连接；签字身份 starbuddy_review 不授）；不放宽任何表的授权与 RLS，不给学习服务、BFF、洞察服务。
-- 返回的都是公共层内容登记（六段标识、能力点编码、题目 ID 与准入、来源编号与枚举）与 staff 账号 ID，不含任何学生数据（规则 13）。
--   1. platform.content_version_list(学科)：内容版本列表（六段、状态、时间、所指地图版本、登记计数）；
--   2. platform.content_version_snapshot(内容版本)：一个内容版本的完整登记（jsonb），附内容版本摘要（0039 content_version_digest）
--      与每道题的准入摘要（0039 item_admission_digest），审核台签字时回传；
--   3. platform.release_record_export(学科)：内容版本与题目准入的审核、发布、撤下、执行记录与签字人（版本记录导出与签字进度）。

CREATE FUNCTION platform.content_version_list(p_subject platform.subject)
  RETURNS TABLE (id uuid, region text, exam_year integer, grade integer, textbook_version text, subject platform.subject,
                 content_version text, status text, map_version_id uuid, published_at timestamptz, retired_at timestamptz,
                 skill_nodes bigint, items bigint)
  LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, platform, pg_temp
  AS $$
  SELECT v.id, v.region, v.exam_year::integer, v.grade::integer, v.textbook_version, v.subject, v.content_version, v.status,
         v.map_version_id, v.published_at, v.retired_at,
         (SELECT count(*) FROM platform.skill_node n WHERE n.curriculum_version_id = v.id),
         (SELECT count(*) FROM platform.item i WHERE i.curriculum_version_id = v.id)
    FROM platform.curriculum_version v
   WHERE v.subject = p_subject
   ORDER BY v.region, v.exam_year, v.grade, v.textbook_version, string_to_array(v.content_version, '.')::numeric[], v.id $$;

-- 一个内容版本的登记快照：版本（六段、状态、所指地图版本）、能力点、题目（含准入与来源）、摘要。不存在时返回 NULL
CREATE FUNCTION platform.content_version_snapshot(p_cv uuid) RETURNS jsonb
  LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, platform, pg_temp SET TimeZone = 'UTC'
  AS $$
  SELECT jsonb_build_object(
    'version', jsonb_build_object('id', v.id, 'region', v.region, 'exam_year', v.exam_year, 'grade', v.grade,
                                  'textbook_version', v.textbook_version, 'subject', v.subject, 'content_version', v.content_version,
                                  'status', v.status, 'map_version_id', v.map_version_id, 'published_at', v.published_at,
                                  'retired_at', v.retired_at),
    'digest', platform.content_version_digest(v.id),
    'skill_nodes', (SELECT coalesce(jsonb_agg(jsonb_build_object('local_id', n.local_id, 'parent_local_id', n.parent_local_id,
                                                                 'label_code', n.label_code) ORDER BY n.local_id), '[]')
                      FROM platform.skill_node n WHERE n.curriculum_version_id = v.id),
    'items', (SELECT coalesce(jsonb_agg(jsonb_build_object('item_id', i.item_id, 'skill_local_id', i.skill_local_id,
                                                           'admission', i.admission, 'source_ref', i.source_ref,
                                                           'source_type', i.source_type, 'license_status', i.license_status,
                                                           'admission_digest', platform.item_admission_digest(i.subject, i.item_id))
                                        ORDER BY i.item_id), '[]')
                FROM platform.item i WHERE i.curriculum_version_id = v.id))
    FROM platform.curriculum_version v WHERE v.id = p_cv $$;

-- 发布记录导出：每行一次内容版本的审核、发布、撤下，或一次题目准入的审核、执行；带所属内容版本的六段与签字人
CREATE FUNCTION platform.release_record_export(p_subject platform.subject)
  RETURNS TABLE (id uuid, line text, action text, curriculum_version_id uuid, region text, exam_year integer, grade integer,
                 textbook_version text, content_version text, map_version_id uuid, item_id text, decision text, digest text,
                 basis_code text, from_admission platform.admission, to_admission platform.admission, review_level text,
                 applies_review_id uuid, affected_rubric_versions text[], decided_at timestamptz, signer uuid)
  LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, platform, pg_temp
  AS $$
  SELECT r.id, r.line, r.action, r.curriculum_version_id, v.region, v.exam_year::integer, v.grade::integer, v.textbook_version,
         v.content_version, v.map_version_id, r.item_id::text, r.decision, r.digest, r.basis_code, r.from_admission, r.to_admission,
         r.review_level, r.applies_review_id, r.affected_rubric_versions, r.decided_at, r.signer
    FROM platform.release_record r
    JOIN platform.curriculum_version v ON v.id = r.curriculum_version_id
   WHERE r.subject = p_subject
   ORDER BY r.decided_at, r.id $$;

ALTER FUNCTION platform.content_version_list(platform.subject) OWNER TO starbuddy_authz;
ALTER FUNCTION platform.content_version_snapshot(uuid) OWNER TO starbuddy_authz;
ALTER FUNCTION platform.release_record_export(platform.subject) OWNER TO starbuddy_authz;
REVOKE EXECUTE ON FUNCTION platform.content_version_list(platform.subject), platform.content_version_snapshot(uuid),
  platform.release_record_export(platform.subject) FROM PUBLIC;
-- 只给审核台后端的读身份 starbuddy_kge（审核台的读全走 kge 连接，store.KgeStore）；签字身份 starbuddy_review 不需要读，
-- 按最小授权不授（契约审查：0038、0040 的导出函数另有用途，这里不照搬）。学习服务、BFF、洞察服务都不授
GRANT EXECUTE ON FUNCTION platform.content_version_list(platform.subject), platform.content_version_snapshot(uuid),
  platform.release_record_export(platform.subject) TO starbuddy_kge;
-- 快照里调用的两个摘要函数（0039）只授 learning 与 review；属主 authz 是它们的属主，可以直接调用，无需另授
