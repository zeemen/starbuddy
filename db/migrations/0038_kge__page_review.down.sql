-- 0038 回滚：删除页面签字与导出函数、唯一索引、page_commit / page_signer_no 列，object_type 恢复 0011 的取值。
-- 状态：草稿，由人执行，先在副本演练。请以单事务执行（psql -1）。
-- 审核记录只追加（0019 触发器连超级用户也拦删除），回滚不能也不应静默删掉页面审核记录：有页面记录时中止，由人决定是否放弃回滚。
-- 先锁表：检查与改表之间不会有新的页面签字插进来
LOCK TABLE kge.content_review IN ACCESS EXCLUSIVE MODE;
DO $$
DECLARE v_n bigint;
BEGIN
  SELECT count(*) INTO v_n FROM kge.content_review WHERE object_type = 'page';
  IF v_n > 0 THEN
    RAISE EXCEPTION '0038 回滚中止：kge.content_review 有 % 条页面审核记录（只追加，不能删除），去掉 page 取值与提交号列会丢失这些记录', v_n
      USING ERRCODE = 'object_not_in_prerequisite_state';
  END IF;
END $$;

DROP FUNCTION kge.page_review_record(platform.subject);
DROP FUNCTION kge.sign_page_review(text, text, text, text, text);
DROP INDEX kge.content_review_page_uq;
ALTER TABLE kge.content_review
  DROP CONSTRAINT content_review_page_commit,
  DROP COLUMN page_signer_no,
  DROP COLUMN page_commit,
  DROP CONSTRAINT content_review_object_type_check,
  ADD CONSTRAINT content_review_object_type_check CHECK (object_type IN ('map_version', 'knowledge_node', 'knowledge_edge', 'textbook_projection',
                                                                       'region_projection', 'item_tag', 'scoring_point', 'current_affair',
                                                                       'map_change'));
