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

-- =====================================================================================================================
-- 第二步扩展（specs/kge09-map-calibration/spec-step2.md）：作答聚合行与错因行的只读导出函数【草案】。
-- 同样不执行、不是迁移，与上面的函数一起由另起的 [NEEDS-HUMAN] PR 落为迁移（附 RLS 测试）。
-- 一次调用同时返回两类行（record_kind = 'attempt' / 'error'）：两类行要用同一个伪名对齐同一学生（拆分节点的错因加强证据），
--   而 salt 每次调用现场生成、不出函数，所以不能分两次调用；执行人按 record_kind 拆成两个文件（RUNBOOK 第 6 节）。
--   与第一步的掌握状态导出是不同的调用，伪名不可对齐（第二步不需要掌握状态）。
-- 作答（“按掌握口径取证据”，落迁移时须与学习服务 services/learning/learning/mastery/ 的证据筛选逐条对照，待人确认）：
--   独立作答（evidence_kind = 'independent'）、证据等级 A、证据与题目都已准入、当前判分为正式语义（formal）、未用提示；
--   当前判分不需人工复核（human_review_required = false）且判分置信度 ≥ p_min_grade_confidence；
--   判分只取 correct / incorrect（partial、needs_review 等不导出，待人确认）；每名学生每道题只取第一次符合条件的作答；
--   学生自报“猜的”（self_report = 'guessed'）且判对的不导出（猜对不是掌握证据）；
--   题目 → 节点：当前已发布地图版本上、打标审核通过的 kge.item_tag.main_node_id（次要节点不导出，待人确认），节点须是可计算的有效知识节点；
--   同时导出题目的认知层次（item_tag.cognitive_level，公共层，报表随题目列出）。
-- 错因：platform.error_attribution 的当前行（superseded_by IS NULL），与作答同口径——只取挂在上面“符合条件的作答”上的错因
--   （它没有节点列，只有 skill_local_id：经 evidence_ids 找到那次作答的题目、再经 item_tag 挂到节点；挂不上的不导出；
--   经 kge.skill_link 从能力点挂节点是另一种取法，待人确认）；
--   只导出 p_usable_codes 里的学科编码（执行人用 python -m kge_calibrate codes 从错因分类表取“明确”的编码；待定与敏感编码，
--   如 xinli_xiguan，一律不出库）；库内按 学生 × 节点 × 编码 去重（置信度取最大），跨科大类原样导出（工具核对与错因分类表一致）。
-- 名单与第一步相同（试点参与同意、排除已删除与删除请求进行中的学生）。
CREATE FUNCTION kge.calibration_export_responses(p_subject platform.subject, p_usable_codes text[], p_min_grade_confidence numeric)
RETURNS TABLE (record_kind text, pseudonym text, item_id text, node_id text, correct smallint, cognitive_level text,
               subject_category text, cross_subject_category text, confidence numeric)
LANGUAGE sql VOLATILE SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $fn$
  WITH salt AS MATERIALIZED (
    SELECT public.gen_random_bytes(32) AS s
  ),
  pub AS (
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
  tag AS (                                        -- 题目 → 主节点（已发布版本、打标审核通过、可计算的有效知识节点）与认知层次
    SELECT t.item_id, t.main_node_id, t.cognitive_level
      FROM kge.item_tag t
      JOIN pub ON t.map_version_id = pub.id
      JOIN kge.knowledge_node n ON n.map_version_id = pub.id AND n.id = t.main_node_id
     WHERE t.subject = p_subject AND t.tag_review_status = 'approved'
       AND n.level = 'node' AND n.status = 'active' AND n.kind IN ('kp', 'ability_kp', 'viewpoint')
  ),
  first_attempt AS (                              -- 每名学生每道题第一次符合条件的作答（作答与错因共用这一口径）
    SELECT DISTINCT ON (a.student_id, a.item_id) a.student_id, a.item_id, a.response_evidence_id, a.self_report,
           tag.main_node_id AS node_id, tag.cognitive_level, g.verdict
      FROM platform.attempt a
      JOIN roster r ON r.student_id = a.student_id
      JOIN platform.evidence_event e ON e.evidence_id = a.response_evidence_id AND e.student_id = a.student_id AND e.subject = a.subject
      JOIN platform.item i ON i.subject = a.subject AND i.item_id = a.item_id
      JOIN tag ON tag.item_id = a.item_id
      JOIN LATERAL (SELECT g0.verdict, g0.semantics, g0.human_review_required, g0.confidence FROM platform.grade_result g0
                     WHERE g0.attempt_id = a.attempt_id AND g0.attempt_occurred_at = a.occurred_at
                       AND g0.student_id = a.student_id AND g0.superseded_by IS NULL
                     ORDER BY g0.created_at DESC, g0.id LIMIT 1) g ON true
     WHERE a.subject = p_subject
       AND e.evidence_kind = 'independent' AND e.evidence_level = 'A'
       AND e.admission = 'admitted' AND i.admission = 'admitted'
       AND g.semantics = 'formal' AND coalesce(a.hint_level_used, 0) = 0
       AND NOT g.human_review_required AND g.confidence >= p_min_grade_confidence
       AND g.verdict IN ('correct', 'incorrect')
     ORDER BY a.student_id, a.item_id, a.occurred_at, a.attempt_id
  ),
  err AS (                                        -- 挂在符合条件的作答上的当前错因行；只取可用编码；学生 × 节点 × 编码去重
    SELECT x.student_id, f.node_id, x.subject_category, min(x.cross_subject_category::text) AS cross_subject_category,
           max(x.confidence) AS confidence
      FROM platform.error_attribution x
      JOIN first_attempt f ON f.student_id = x.student_id AND f.response_evidence_id = ANY (x.evidence_ids)
     WHERE x.subject = p_subject AND x.superseded_by IS NULL
       AND x.subject_category = ANY (p_usable_codes)
     GROUP BY x.student_id, f.node_id, x.subject_category
  ),
  rows_out AS (
    SELECT 'attempt' AS record_kind, f.student_id, f.item_id::text AS item_id, f.node_id::text AS node_id,
           (CASE f.verdict WHEN 'correct' THEN 1 ELSE 0 END)::smallint AS correct, f.cognitive_level,
           NULL::text AS subject_category, NULL::text AS cross_subject_category, NULL::numeric AS confidence
      FROM first_attempt f
     WHERE NOT (f.verdict = 'correct' AND f.self_report IS NOT DISTINCT FROM 'guessed')   -- 猜对不导出
    UNION ALL
    SELECT 'error', x.student_id, NULL, x.node_id::text, NULL, NULL, x.subject_category, x.cross_subject_category,
           round(x.confidence::numeric, 3)
      FROM err x
  )
  SELECT o.record_kind,
         'pil-' || translate(substr(encode(public.hmac(convert_to(o.student_id::text, 'UTF8'), salt.s, 'sha256'), 'hex'), 1, 24),
                             '0123456789', 'ghijklmnop'),
         o.item_id, o.node_id, o.correct, o.cognitive_level, o.subject_category, o.cross_subject_category, o.confidence
    FROM rows_out o CROSS JOIN salt
   ORDER BY 1, 2, 3, 4, 7
$fn$;
-- 落迁移时同上：REVOKE ALL … FROM PUBLIC；只 GRANT EXECUTE 给专用只读导出角色；配 RLS / 权限测试。
