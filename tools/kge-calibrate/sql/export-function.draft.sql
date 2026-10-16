-- KGE-09 地图校准：伪名化掌握状态只读导出函数的【草案】（说明见 tools/kge-calibrate/RUNBOOK.md）。
-- 本文件不执行、不是迁移：它是 RLS 与权限策略变更（SECURITY DEFINER 读个人层），属必停项，
-- 由另起的 [NEEDS-HUMAN] PR 落为 db/migrations 下的迁移（附 RLS 测试、授权给专用只读导出角色），由人确认并在副本演练后执行。
-- 函数只读：不写任何表；返回 伪名 × 节点 × 星亮层次那一行的门槛状态与掌握概率。
--
-- “掌握”口径与学习服务 services/learning/learning/mastery/gates.py 的 star_basis_level / star_lit 同口径、不另立：
--   p_star_rule 就是该学科掌握配置（services/learning/config/mastery/<学科>.json）里的 star_level_rule 原文，
--   {"mode": "star_level" | "all_levels", "node_levels": {节点 ID: 认知层次}}；
--   star_level：取配置给该节点的层次，未配置取该节点最高的认知层次；配置的层次不在节点层次里 → 没有星亮层次（不导出）；
--   all_levels：取门槛最低的层次（同级取低层次；某层次没有估计按 unseen）。
--   门槛状态与掌握概率取自同一行（星亮层次那一行），不分别取最大值。
-- 伪名：salt 在库内每次调用现场生成（gen_random_bytes，CTE 物化一次），不经命令行、不进 SQL 文本、不落盘；
--   伪名 = 'pil-' + hmac(student_id, salt, sha256) 的前 24 个十六进制字符映射成字母，不同次调用不可对齐。
-- 名单：试点参与同意（pilot_participation）的最近一次决定为 granted（同一时刻有撤回按撤回）；
--   排除已执行删除的档案（student_profile.erased_at）与删除请求进行中（含冷静期，erasure_request.status = 'pending'）的学生。
-- pgcrypto 的 schema 以库内为准（下文写 public.，落迁移时按 0009 的做法查 pg_extension 确认）。

CREATE FUNCTION kge.calibration_export_mastery(p_subject platform.subject, p_star_rule jsonb)
RETURNS TABLE (pseudonym text, node_id text, gate text, p_mastery numeric)
LANGUAGE sql VOLATILE SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $fn$
  WITH salt AS MATERIALIZED (
    SELECT public.gen_random_bytes(32) AS s
  ),
  pub AS (                                        -- 当前已发布地图版本：与导出的地图快照必须是同一版本
    SELECT v.id FROM kge.map_version v WHERE v.subject = p_subject AND v.status = 'published'
  ),
  roster AS (
    SELECT c.student_id
      FROM (SELECT DISTINCT ON (cr.student_id) cr.student_id, cr.decision
              FROM platform.consent_record cr
             WHERE cr.consent_type = 'pilot_participation'
             ORDER BY cr.student_id, cr.decided_at DESC, (cr.decision = 'revoked') DESC) c
      JOIN platform.student_profile sp ON sp.id = c.student_id
     WHERE c.decision = 'granted'
       AND sp.erased_at IS NULL
       AND NOT EXISTS (SELECT 1 FROM platform.erasure_request er WHERE er.student_id = c.student_id AND er.status = 'pending')
  ),
  levels AS (                                     -- 可计算节点 × 它的认知层次（ord：由低到高，与 COGNITIVE_LEVELS 一致）
    SELECT n.id AS node_id, lv.level, lv.ord
      FROM kge.knowledge_node n
      JOIN pub ON n.map_version_id = pub.id
      JOIN (VALUES ('know', 1), ('understand', 2), ('apply', 3), ('synthesize', 4)) AS lv(level, ord)
        ON lv.level = ANY (n.cognitive_levels)
     WHERE n.level = 'node' AND n.status = 'active' AND n.kind IN ('kp', 'ability_kp', 'viewpoint')
  ),
  cur AS (                                        -- 当前掌握估计：节点键记录、在已发布版本上计算、名单内
    SELECT e.student_id, e.node_id, e.cognitive_level, e.state, e.p_mastery
      FROM platform.mastery_estimate e
      JOIN pub ON pub.id = e.map_version_id
      JOIN roster r ON r.student_id = e.student_id
     WHERE e.is_current AND e.subject = p_subject AND e.node_id IS NOT NULL
  ),
  seen AS (                                       -- 在该节点上有任何估计的 学生 × 节点
    SELECT DISTINCT student_id, node_id FROM cur
  ),
  basis_star_level AS (                           -- star_level 模式：每个节点一个星亮层次（与学生无关）
    SELECT l.node_id, l.level
      FROM (SELECT node_id, max(ord) AS top_ord FROM levels GROUP BY node_id) t
      JOIN levels l ON l.node_id = t.node_id
     WHERE l.level = COALESCE(p_star_rule -> 'node_levels' ->> t.node_id,
                              (SELECT l2.level FROM levels l2 WHERE l2.node_id = t.node_id AND l2.ord = t.top_ord))
  ),
  pick AS (
    SELECT s.student_id, s.node_id, c.state, c.p_mastery
      FROM seen s
      JOIN basis_star_level b ON b.node_id = s.node_id
      LEFT JOIN cur c ON c.student_id = s.student_id AND c.node_id = s.node_id AND c.cognitive_level = b.level
     WHERE p_star_rule ->> 'mode' = 'star_level'
    UNION ALL
    (SELECT DISTINCT ON (s.student_id, s.node_id) s.student_id, s.node_id, c.state, c.p_mastery
       FROM seen s
       JOIN levels l ON l.node_id = s.node_id
       LEFT JOIN cur c ON c.student_id = s.student_id AND c.node_id = s.node_id AND c.cognitive_level = l.level
      WHERE p_star_rule ->> 'mode' = 'all_levels'
      ORDER BY s.student_id, s.node_id, COALESCE(c.state, 'unseen'::platform.mastery_state), l.ord)
  )
  SELECT 'pil-' || translate(substr(encode(public.hmac(convert_to(p.student_id::text, 'UTF8'), salt.s, 'sha256'), 'hex'), 1, 24),
                             '0123456789', 'ghijklmnop'),
         p.node_id,
         COALESCE(p.state::text, 'unseen'),         -- 星亮层次没有估计 = unseen（工具按未观测处理）
         CASE WHEN p.p_mastery IS NULL THEN NULL ELSE round(p.p_mastery::numeric, 4) END
    FROM pick p CROSS JOIN salt
   ORDER BY 1, 2
$fn$;
-- 落迁移时另加：REVOKE ALL ON FUNCTION … FROM PUBLIC；只 GRANT EXECUTE 给专用只读导出角色；配 RLS / 权限测试。
