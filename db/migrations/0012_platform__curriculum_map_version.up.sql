-- 0012 platform · 内容版本引用地图版本（specs/kge-schema-v0.9/spec.md 设计要点 6）
-- 状态：草稿 v0.9。只写文件，由人执行（CLAUDE.md 必停项）。与 0011 一起评审；按 ADR 02 第 2 条“每个迁移只改一个 schema”从 0011 拆出。
-- 对 platform 的唯一改动：加一列可空的地图版本引用。现有行为空，发布新内容版本时由发布流程（4-G）填写。
-- 按 (subject, map_version_id) 引用 kge.map_version (subject, id)，保证内容版本与地图版本同学科。其余 platform 表不动。
-- 请以单事务执行（psql -1）。

ALTER TABLE platform.curriculum_version ADD COLUMN map_version_id uuid;
ALTER TABLE platform.curriculum_version ADD CONSTRAINT curriculum_version_map_version_fk
  FOREIGN KEY (subject, map_version_id) REFERENCES kge.map_version (subject, id);
COMMENT ON COLUMN platform.curriculum_version.map_version_id IS '本内容版本引用的地图版本（kge.map_version）；内容版本与地图版本是两回事';
