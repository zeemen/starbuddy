-- 0044 insight · 洞察服务的授权再核对（SIE-02b 第二步，specs/sie-02b-claims-first-report/spec.md“第二步补充”默认方案第 1 条，lessons 待人确认 C）
-- 状态：草稿。只写文件，由人先在副本演练再执行（CLAUDE.md 必停项：迁移、RLS 与权限、画像可见范围）。请以单事务执行（psql -1）。
-- 依赖 0002（账号、档案、绑定）、0010（insight schema、starbuddy_insight）、0035（student_profile.erased_at）。
-- 依据：契约 insight-service.v0.9“调用方、受众与代表谁”——caller = bff 时洞察服务按 actor 与 student_ref 再核对一次授权
-- （与 BFF 的检查重叠是有意的，规则 5）；做法同 0032 insight.raise_dispute（属主 authz、只授洞察服务、按库里关系再核）。
-- 要点：
--   1. insight.verify_actor(p_actor, p_audience, p_student) 只返回真假，不返回任何其他信息，也不报错：
--      - audience = student：p_actor 是 active 学生账号，且就是 p_student 档案的账号；
--      - audience = guardian：p_actor 是 active 家长账号，且对 p_student 有有效绑定（guardian_student_link 未撤销，按档案授权，
--        不看手机号，规则 5）；
--      - 其他受众（agent 等）、任一参数为空、账号或档案不存在、档案已执行删除（0035 erased_at）一律 false；
--      不存在与无权同样是 false（不暴露存在性）；
--   2. 只授 starbuddy_insight：BFF、学习服务、审核台、调度器等都不能调用（BFF 自己有 can_read_student）；
--   3. link_id（契约 Actor.link_id）不进函数：同一家长对同一学生的有效绑定至多一条（0002 唯一索引），按 (家长, 学生) 核对即可；
--      洞察服务也不据 link_id 取数（lessons 待人确认）。
-- 不改任何表、不新增授权给函数属主（authz 在 0002 已能读账号、档案与绑定）。

CREATE FUNCTION insight.verify_actor(p_actor uuid, p_audience text, p_student uuid) RETURNS boolean
  LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, platform, pg_temp
  AS $$
    SELECT CASE
      WHEN p_actor IS NULL OR p_student IS NULL THEN false
      WHEN p_audience = 'student' THEN EXISTS (
        SELECT 1 FROM platform.student_profile s JOIN platform.account a ON a.id = s.account_id
         WHERE s.id = p_student AND s.erased_at IS NULL
           AND a.id = p_actor AND a.kind = 'student' AND a.status = 'active')
      WHEN p_audience = 'guardian' THEN EXISTS (
        SELECT 1 FROM platform.guardian_student_link l
          JOIN platform.account a ON a.id = l.guardian_account_id
          JOIN platform.student_profile g ON g.id = l.student_id
         WHERE l.student_id = p_student AND l.status = 'active' AND l.revoked_at IS NULL AND g.erased_at IS NULL
           AND a.id = p_actor AND a.kind = 'guardian' AND a.status = 'active')
      ELSE false END $$;
ALTER FUNCTION insight.verify_actor(uuid, text, uuid) OWNER TO starbuddy_authz;
REVOKE EXECUTE ON FUNCTION insight.verify_actor(uuid, text, uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION insight.verify_actor(uuid, text, uuid) TO starbuddy_insight;
COMMENT ON FUNCTION insight.verify_actor(uuid, text, uuid) IS
  '洞察服务的授权再核对（0044）：学生本人（active 学生账号且是该档案的账号）或有效绑定的 active 家长才为真，其余一律 false；'
  '只返回真假，不存在与无权相同；只授 starbuddy_insight';
