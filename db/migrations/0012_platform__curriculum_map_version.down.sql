-- 0012 回滚：去掉内容版本上的地图版本引用
ALTER TABLE platform.curriculum_version DROP COLUMN map_version_id;
