-- 0018 回滚：删除签字人表（先回滚 0019）
DROP TABLE platform.map_change_signoff;
DROP TABLE platform.content_signoff;
DROP FUNCTION platform.reject_signoff_change();
