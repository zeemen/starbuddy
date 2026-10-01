-- 0037 回滚：删除复测任务、学情档案页与阻断记录；个人层表清单去掉三张表并还原 mastery_estimate 的导出列；
-- mastery_estimate 去掉节点键，skill_local_id 恢复 NOT NULL，check_supersede_target 恢复 0005 的写法。
-- 状态：草稿，由人执行，先在副本演练。请以单事务执行（psql -1）。
-- 注意：
--   - 回滚会删除全部复测任务、档案页与阻断记录（档案页可由判断账本重新编译；复测任务可由对账任务按当前掌握记录重建）；
--   - skill_local_id 恢复 NOT NULL 之前，库里不能有节点键的掌握记录：节点键行没有能力点键，既不能补一个假的能力点键，
--     也不应在回滚里静默删掉学习者模型的唯一真相源（规则 2）。有这种行时回滚失败并报明确错误，由人决定先导出再删除、还是放弃回滚。
DO $$
DECLARE v_n bigint;
BEGIN
  SELECT count(*) INTO v_n FROM platform.mastery_estimate WHERE node_id IS NOT NULL;
  IF v_n > 0 THEN
    RAISE EXCEPTION '0037 回滚中止：platform.mastery_estimate 有 % 条节点键记录（skill_local_id 为空），恢复 NOT NULL 会失败。请先导出并由人决定如何处理这些记录', v_n
      USING ERRCODE = 'object_not_in_prerequisite_state';
  END IF;
END $$;

-- ---------- 个人层表清单 ----------
UPDATE platform.personal_table_registry
   SET export_columns = ARRAY['subject', 'skill_local_id', 'state', 'next_retest_at', 'computed_at']
 WHERE nspname = 'platform' AND relname = 'mastery_estimate';
DELETE FROM platform.personal_table_registry
 WHERE nspname = 'platform' AND relname IN ('profile_page_check', 'profile_page', 'retest_task');

-- ---------- 三张新表（授权与策略随表删除） ----------
DROP VIEW platform.profile_page_statement_v;
DROP TABLE platform.profile_page_check;
DROP FUNCTION platform.profile_page_check_guard();
DROP TABLE platform.profile_page;
DROP TABLE platform.retest_task;
DROP FUNCTION platform.valid_profile_violations(jsonb);
DROP FUNCTION platform.profile_page_guard();
DROP FUNCTION platform.valid_profile_sections(jsonb, text);
DROP FUNCTION platform.retest_task_guard();

-- ---------- 更正目标核对恢复 0005 的写法（属主与授权不变） ----------
CREATE OR REPLACE FUNCTION platform.check_supersede_target() RETURNS trigger
  LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, platform, pg_temp
  AS $$
DECLARE ok boolean;
BEGIN
  IF TG_TABLE_NAME = 'skill_observation' THEN
    SELECT EXISTS (SELECT 1 FROM platform.skill_observation n WHERE n.id = NEW.superseded_by AND n.superseded_by IS NULL
                     AND n.student_id = OLD.student_id AND n.subject = OLD.subject
                     AND n.skill_local_id = OLD.skill_local_id AND n.created_at >= OLD.created_at) INTO ok;
  ELSIF TG_TABLE_NAME = 'error_attribution' THEN
    SELECT EXISTS (SELECT 1 FROM platform.error_attribution n WHERE n.id = NEW.superseded_by AND n.superseded_by IS NULL
                     AND n.student_id = OLD.student_id AND n.subject = OLD.subject
                     AND n.skill_local_id IS NOT DISTINCT FROM OLD.skill_local_id
                     AND n.created_at >= OLD.created_at) INTO ok;
  ELSIF TG_TABLE_NAME = 'mastery_estimate' THEN
    SELECT EXISTS (SELECT 1 FROM platform.mastery_estimate n WHERE n.id = NEW.superseded_by AND n.is_current
                     AND n.student_id = OLD.student_id AND n.subject = OLD.subject
                     AND n.skill_local_id = OLD.skill_local_id AND n.computed_at >= OLD.computed_at) INTO ok;
  ELSIF TG_TABLE_NAME = 'grade_result' THEN
    SELECT EXISTS (SELECT 1 FROM platform.grade_result n WHERE n.id = NEW.superseded_by AND n.superseded_by IS NULL
                     AND n.student_id = OLD.student_id AND n.subject = OLD.subject
                     AND n.attempt_id = OLD.attempt_id AND n.created_at >= OLD.created_at
                     AND (NOT OLD.human_review_required OR n.human_review_required
                          OR EXISTS (SELECT 1 FROM platform.review_task r
                                      JOIN platform.account a ON a.id = r.resolved_by_account AND a.kind = 'staff'
                                      WHERE r.source_kind = 'grade_result' AND r.source_id = OLD.id
                                        AND r.status = 'resolved'))) INTO ok;
  END IF;
  IF NOT coalesce(ok, false) THEN
    RAISE EXCEPTION 'invalid supersede target for % %', TG_TABLE_NAME, OLD.id USING ERRCODE = 'check_violation';
  END IF;
  RETURN NULL;
END $$;

-- ---------- mastery_estimate：去掉节点键 ----------
DROP INDEX platform.mastery_estimate_unit_ref_uq;
DROP INDEX platform.mastery_estimate_node_current_uq;
ALTER TABLE platform.mastery_estimate DROP CONSTRAINT mastery_estimate_one_key;
ALTER TABLE platform.mastery_estimate DROP CONSTRAINT mastery_estimate_node_key_complete;
ALTER TABLE platform.mastery_estimate ALTER COLUMN skill_local_id SET NOT NULL;
ALTER TABLE platform.mastery_estimate DROP COLUMN map_version_id, DROP COLUMN cognitive_level, DROP COLUMN node_id;
