-- 0024 回滚：删掉打标候选队列与决定函数，item_tag 去掉打标来源列（教研接受候选写回的 item_tag 行保留，只丢来源信息）
DROP FUNCTION kge.decide_tag_candidate(uuid, text);
DROP TRIGGER guard_human_tag ON kge.item_tag;
DROP FUNCTION kge.guard_human_tag();
REVOKE INSERT, UPDATE (node_ids, tag_source, tag_confidence, tag_model_version, tagger_version, tag_basis) ON kge.item_tag FROM starbuddy_authz;
DROP TABLE platform.tag_candidate_signoff;
DROP TABLE kge.tag_candidate;
ALTER TABLE kge.item_tag DROP CONSTRAINT item_tag_model_version;
ALTER TABLE kge.item_tag DROP CONSTRAINT item_tag_source_complete;
-- 回滚前 source_kind = 'legacy' 的打标要先由人处理（改成别的来源或删除），否则恢复原约束会失败（不静默改数据）
ALTER TABLE kge.item_tag DROP CONSTRAINT item_tag_source_kind_check;
ALTER TABLE kge.item_tag ADD CONSTRAINT item_tag_source_kind_check
  CHECK (source_kind IN ('textbook', 'exam_paper', 'original', 'adapted', 'third_party'));
ALTER TABLE kge.item_tag DROP COLUMN tag_basis, DROP COLUMN tag_model_version, DROP COLUMN tagger_version, DROP COLUMN tag_source, DROP COLUMN tag_confidence;
