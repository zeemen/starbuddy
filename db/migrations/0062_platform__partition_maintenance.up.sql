-- 0062 platform · 分区续建（PLT-23，specs/plt23-data-maintenance/spec.md 第二节 A；W13 技术债 T18）
-- 状态：草稿。只写文件，由人先在副本演练再执行（CLAUDE.md 必停项：迁移、权限）。不在任何共享环境执行。
-- 依赖：0004（platform.ensure_monthly_partitions、evidence_event、attempt）、0008（usage_ledger）、0060（model_call_log）、
--       0009（starbuddy_scheduler 的 schema 使用权）。
--
-- 为什么：四张按月分区表只预建到 2027-12，没有 DEFAULT 分区（有意：超出范围的写入直接失败，便于发现），续建没有任何定时任务；
-- ensure_monthly_partitions 按调用者身份执行，建分区要父表属主权限，调度身份做不到。不处理的话 2028-01-01（UTC）起证据、作答、
-- 计量流水与调用记录的写入全部失败。
--
-- 一、platform.ensure_partitions_ahead(p_now)：SECURITY DEFINER，**属主是执行本迁移的迁移执行者**（四张父表的属主；建分区必须是父表属主）。
--   这是全库唯一不属 authz / eraser 的 SECURITY DEFINER 函数（80 号测试登记为唯一例外，并核对它的属主就是四张父表的属主），所以做得尽量窄：
--   - 父表写死为四张（不收表名参数）；只建“p_now 所在的 UTC 月起共 7 个月”（当月 + 往后 6 个月）里缺的分区，不删、不改已有分区；
--   - p_now 只能在数据库 now() 往前 1 天之内、不能晚于 now()（与 0060 留存函数同口径），越界 22023；
--   - 时区固定 UTC（分区边界是 UTC 月初，同 0004 / 0008 / 0060 的初始分区）；search_path 固定、以 pg_temp 结尾；
--   - 建出的分区不授任何权限（只经父表访问；父表的 RLS 与授权照旧，攻击面 J1）；
--   - 只授 starbuddy_scheduler；返回新建分区数。
--   待人确认（lessons 第 1 条）：是否改为专用的“分区属主”角色（只拥有四张父表），把这个函数的权限收窄到这四张表。
-- 二、platform.partition_horizon()：只读，SECURITY INVOKER（只读系统目录，不读任何数据），返回四张表各自已建到的最后一个月；
--   只授 starbuddy_scheduler（定时任务据此判断“剩余不足 3 个月”并以非零退出码告警；不授报表身份，79zh 锁定了它在 platform 下能执行的函数）。
-- 请以单事务执行（psql -1）。

CREATE FUNCTION platform.ensure_partitions_ahead(p_now timestamptz) RETURNS integer
  LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog, platform, pg_temp SET TimeZone = 'UTC'
  AS $$
DECLARE
  v_from date;
  v_n integer := 0;
BEGIN
  IF p_now IS NULL OR p_now > now() OR p_now < now() - interval '1 day' THEN
    RAISE EXCEPTION 'p_now must be within the last day and not in the future' USING ERRCODE = 'invalid_parameter_value';
  END IF;
  v_from := date_trunc('month', p_now AT TIME ZONE 'UTC')::date;
  v_n := v_n + platform.ensure_monthly_partitions('platform.evidence_event'::regclass, v_from, 7);
  v_n := v_n + platform.ensure_monthly_partitions('platform.attempt'::regclass, v_from, 7);
  v_n := v_n + platform.ensure_monthly_partitions('platform.usage_ledger'::regclass, v_from, 7);
  v_n := v_n + platform.ensure_monthly_partitions('platform.model_call_log'::regclass, v_from, 7);
  RETURN v_n;
END $$;

CREATE FUNCTION platform.partition_horizon() RETURNS TABLE (parent text, last_month date)
  LANGUAGE sql STABLE SET search_path = pg_catalog, platform, pg_temp
  AS $$
  SELECT p.relname::text,
         max(to_date(substring(c.relname FROM '_(\d{6})$'), 'YYYYMM'))
    FROM pg_class p
    JOIN pg_namespace n ON n.oid = p.relnamespace AND n.nspname = 'platform'
    LEFT JOIN pg_inherits i ON i.inhparent = p.oid
    LEFT JOIN pg_class c ON c.oid = i.inhrelid AND c.relname ~ ('^' || p.relname || '_\d{6}$')
   WHERE p.relname IN ('evidence_event', 'attempt', 'usage_ledger', 'model_call_log') AND p.relkind = 'p'
   GROUP BY p.relname
   ORDER BY p.relname
$$;

REVOKE EXECUTE ON FUNCTION platform.ensure_partitions_ahead(timestamptz), platform.partition_horizon() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION platform.ensure_partitions_ahead(timestamptz), platform.partition_horizon() TO starbuddy_scheduler;
COMMENT ON FUNCTION platform.ensure_partitions_ahead(timestamp with time zone) IS
  '分区续建（0062，PLT-23）：给四张按月分区表（evidence_event、attempt、usage_ledger、model_call_log）补建 p_now 所在 UTC 月起 7 个月里缺的分区；'
  '不删不改；属主是四张父表的属主（迁移执行者），全库唯一不属 authz / eraser 的 SECURITY DEFINER 函数；只授 starbuddy_scheduler';
COMMENT ON FUNCTION platform.partition_horizon() IS
  '分区水位（0062，PLT-23）：四张按月分区表各自已建到的最后一个月（只读系统目录）；只授 starbuddy_scheduler';
