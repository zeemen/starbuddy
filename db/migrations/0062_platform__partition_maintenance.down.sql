-- 0062 回滚：删掉分区续建与分区水位两个函数。已经由 ensure_partitions_ahead 建出的分区保留（是数据的一部分，删分区要人确认）。
DROP FUNCTION platform.partition_horizon();
DROP FUNCTION platform.ensure_partitions_ahead(timestamptz);
