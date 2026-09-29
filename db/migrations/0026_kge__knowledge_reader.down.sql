-- 0026 回滚：撤掉知识服务只读角色的策略与授权（角色本身由 bootstrap 管理，不在这里删除）
DROP POLICY current_affair_reader ON kge.current_affair;
DROP POLICY scoring_point_reader ON kge.scoring_point;
DROP POLICY item_tag_reader ON kge.item_tag;
DROP POLICY item_family_reader ON kge.item_family;
DROP POLICY skill_link_reader ON kge.skill_link;
DROP POLICY curriculum_item_reader ON kge.curriculum_item;
DROP POLICY region_reader ON kge.region_projection;
DROP POLICY textbook_reader ON kge.textbook_projection;
DROP POLICY edge_reader ON kge.knowledge_edge;
DROP POLICY node_reader ON kge.knowledge_node;
DROP POLICY map_version_reader ON kge.map_version;
DROP FUNCTION kge.reader_version_published(uuid);
REVOKE SELECT ON kge.textbook_projection FROM starbuddy_kge_read;
REVOKE SELECT ON kge.map_version, kge.knowledge_node, kge.knowledge_edge, kge.region_projection, kge.curriculum_item,
                 kge.skill_link, kge.item_family, kge.item_tag, kge.scoring_point, kge.current_affair
  FROM starbuddy_kge_read;
REVOKE USAGE ON SCHEMA platform FROM starbuddy_kge_read;
REVOKE USAGE ON SCHEMA kge FROM starbuddy_kge_read;
