-- 0030 platform · 计量只读函数：学生今日的时长与生命周期状态（specs/unified-shell-sprint4/spec.md 第 9、10 节，H3；待人确认 #10 默认）
-- 状态：草稿。只写文件，由人先在副本演练再执行（CLAUDE.md 必停项：迁移、权限、计量）。不在任何共享环境执行。
-- 依赖：0002（can_read_student）、0009（meter_now）、0015（meter_state_of、meter_plan_daily_cap、meter_daily_cap、meter_minutes_today、
--       meter_in_curfew、meter_minutes_until_curfew、student_time_guard、practice_log）。
-- 只读：不预留、不结算、不回滚，不改 0015 的任何规则与数值；只把 0015 的推导函数按同一时钟（meter_now）组合成一个结果，
-- 让 BFF 不必在代码里重算单日上限、今日已用、宵禁（第二套口径，SIE-02a lessons #15）。
-- 授权：只认学生本人或已绑定家长（can_read_student：guardian_student_link 有效绑定，规则 5）；其他人、未登录、学生不存在一律
-- 同一个 insufficient_privilege 'not permitted'（与 meter_student_state 相同，不泄露存在性；80 的函数越权矩阵覆盖）。
-- 属主 starbuddy_authz、search_path 固定且以 pg_temp 结尾、只授 starbuddy_bff。
--
-- 返回（jsonb，键固定）：
--   state                     trialing / active / grace / retained / none（meter_state_of）
--   trial_ends_at             试用中时为试用订阅的 period_end，否则 null（与 meter_state_of 取同一条订阅）
--   plan_daily_cap            档位上限（meter_plan_daily_cap）
--   daily_cap                 生效上限（meter_daily_cap：家长设定与档位上限取小）
--   minutes_used_today        今日（上海时间）已用分钟（meter_minutes_today：已结算 + 未过期的预留中）
--   minutes_remaining_today   greatest(daily_cap - minutes_used_today, 0)（与 meter_reserve 的 remaining_today 同一算法）
--   curfew_start / curfew_end 生效的宵禁起止（HH:MM；家长设定或缺省 22:00–06:00，跨午夜）
--   in_curfew                 当前是否在宵禁内（meter_in_curfew）
--   minutes_until_curfew      距宵禁开始的分钟数，已在宵禁内为 0（meter_minutes_until_curfew）；“curfew_soon”阈值由 BFF 配置
--   practice_remaining_today  留存态今日剩余题库自练次数（与 meter_practice 同一口径：上海时间换日、每天 5 次），其他状态 null
--   as_of                     计量时钟（meter_now）
-- 不返回设置人（updated_by）：学生端不应看到是哪位家长改的（0015 已不给 BFF 读该列）。

CREATE FUNCTION platform.meter_guard_status(p_student uuid) RETURNS jsonb
  LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = pg_catalog, platform, pg_temp
  AS $$
DECLARE
  v_now timestamptz := platform.meter_now();
  v_state text;
  v_trial_end timestamptz;
  v_cap integer;
  v_used numeric;
  v_start time;
  v_end time;
  v_day_start timestamptz;
  v_practice integer;
BEGIN
  IF p_student IS NULL OR NOT platform.can_read_student(p_student) THEN
    RAISE EXCEPTION 'not permitted' USING ERRCODE = 'insufficient_privilege';
  END IF;
  v_state := platform.meter_state_of(p_student);
  IF v_state = 'trialing' THEN
    -- 与 meter_state_of 取同一条订阅（有效或宽限、非附加、按开始时间取最新）
    SELECT x.period_end INTO v_trial_end FROM platform.subscription x
     WHERE x.student_id = p_student AND x.status IN ('active', 'grace') AND x.source <> 'addon'
     ORDER BY x.period_start DESC LIMIT 1;
  END IF;
  v_cap := platform.meter_daily_cap(p_student);
  v_used := platform.meter_minutes_today(p_student, v_now);
  SELECT coalesce(g.curfew_start, time '22:00'), coalesce(g.curfew_end, time '06:00') INTO v_start, v_end
    FROM (SELECT 1) one LEFT JOIN platform.student_time_guard g ON g.student_id = p_student;
  IF v_state = 'retained' THEN
    v_day_start := (v_now AT TIME ZONE 'Asia/Shanghai')::date::timestamp AT TIME ZONE 'Asia/Shanghai';
    SELECT count(*) INTO v_practice FROM platform.practice_log
     WHERE student_id = p_student AND occurred_at >= v_day_start AND occurred_at < v_day_start + interval '1 day';
  END IF;
  RETURN jsonb_build_object(
    'state', v_state,
    'trial_ends_at', v_trial_end,
    'plan_daily_cap', platform.meter_plan_daily_cap(p_student),
    'daily_cap', v_cap,
    'minutes_used_today', v_used,
    'minutes_remaining_today', greatest(v_cap - v_used, 0),
    'curfew_start', to_char(v_start, 'HH24:MI'),
    'curfew_end', to_char(v_end, 'HH24:MI'),
    'in_curfew', platform.meter_in_curfew(p_student, v_now),
    'minutes_until_curfew', platform.meter_minutes_until_curfew(p_student, v_now),
    'practice_remaining_today', CASE WHEN v_state = 'retained' THEN greatest(5 - v_practice, 0) END,
    'as_of', v_now);
END $$;

ALTER FUNCTION platform.meter_guard_status(uuid) OWNER TO starbuddy_authz;
REVOKE EXECUTE ON FUNCTION platform.meter_guard_status(uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION platform.meter_guard_status(uuid) TO starbuddy_bff;
