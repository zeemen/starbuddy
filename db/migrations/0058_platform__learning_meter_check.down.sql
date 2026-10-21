-- 0058 回滚：删除学习服务的计量核对函数（学习服务里依赖它的写入核对须先回滚，否则 /internal/v1 写入一律被拒）
DROP FUNCTION platform.learning_meter_check(uuid, text, uuid, text);
