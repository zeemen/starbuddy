-- 0024 回滚：删掉打标候选队列与决定函数，item_tag 去掉打标来源列（教研接受候选写回的 item_tag 行保留，只丢来源信息）
DROP FUNCTION kge.decide_tag_candidate(uuid, text);
REVOKE INSERT, UPDATE (node_ids) ON kge.item_tag FROM starbuddy_authz;
DROP TABLE platform.tag_candidate_signoff;
DROP TABLE kge.tag_candidate;
ALTER TABLE kge.item_tag DROP CONSTRAINT item_tag_model_version;
ALTER TABLE kge.item_tag DROP CONSTRAINT item_tag_source_complete;
ALTER TABLE kge.item_tag DROP COLUMN tag_model_version, DROP COLUMN tagger_version, DROP COLUMN tag_source, DROP COLUMN tag_confidence;
