-- 0030 回滚：删除计量只读函数 meter_guard_status（BFF 的时长状态读取随之不可用，须先回滚 BFF 的调用方）
DROP FUNCTION platform.meter_guard_status(uuid);
