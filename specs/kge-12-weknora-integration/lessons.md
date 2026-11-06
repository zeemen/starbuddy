# KGE-12 · lessons

## A 节点表导出（2026-11-06）
- 实现：`services/knowledge/knowledge/export/node_table.py`（`python -m knowledge.export.node_table`），测试 `tests/test_export_node_table.py`，
  变异 5 条加进 `services/knowledge/tests/mutations.sh`（未核对投影、撤下节点、教材原句、出版社缺编码、草稿版本）。
- 与需求单附录 A.2 的差异已改进需求单：多了 `level`、`kind`、`taggable`、`includes`、`excludes`、`cognitive_levels`（中台打标时用来区分相近节点）；
  `node_type` 改为 `level` + `kind`（地图本来的字段）；`aliases` 为空、`publisher` 靠对照表（决定记录第 6 条）。
- `parent_node_id` 指向已撤下的节点时输出 null，不输出悬空引用。

## 待人确认
1. **ID 迁移表怎么导出**：迁移记录在 `kge.id_migration`，不在公共层快照里；知识服务的只读角色 `starbuddy_kge_read` 没有这张表的
   SELECT 权限，而且只能看到已发布的那一个版本（看不到旧版本）。可选：
   - (a) 新迁移给只读角色授权读 `kge.id_migration`（限已发布目标版本）——迁移与权限变更，必停；
   - (b) 在审核台的发布流程里，把 ID 迁移记录随地图快照一起导出（快照格式加一段），导出工具只读快照；
   - (c) **（建议）先不做**：每科目前只有一个已发布版本，ID 迁移表只在地图升版时才需要；到第一次升版时按 (b) 做。
2. 出版社对照表（版本编码 → 出版社全称）由教研提供，放哪里、谁维护（建议跟地图快照一起放在页面仓库或审核台导出目录）。
