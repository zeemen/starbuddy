-- 0043 kge · 时政过期下架（specs/release-gate-sprint4/spec.md 第 9 节、冲刺 4 验收“时政”；第二部分 PR D）
-- 状态：草稿。只写文件，由人先在副本演练再执行（CLAUDE.md 必停项：迁移、权限）。依赖 0011、0019、0038。请以单事务执行（psql -1）。
-- 只改 kge schema（ADR 02）。
-- 要点：
--   1. kge.expire_current_affairs(p_today date)：把 valid_until < p_today 且仍为 published 的时政改为 retired，每条追加一条审核记录
--      （object_type = current_affair、action = retire、decision = approved、risk_level = low、basis_code = expired_auto、摘要为该行内容）；
--      审核人为空：不写 platform.content_signoff（系统动作，KGE 第七章“过期由体检自动下架”，spec 第 9 节“审核人为空、标明系统动作”）；
--   2. 为什么要函数：知识服务（starbuddy_kge）能直接把时政改为 retired，但它只能往 kge.content_review 插“待审”记录（0019 策略
--      content_review_kge_submit），写不进带结论的系统记录；结论只能由属主 authz 的函数写入。函数只写它本次实际下架的行，
--      不能借它为未过期的时政伪造“过期下架”记录；
--   3. p_today 不能晚于北京时间的今天（不能提前下架）；可以早于今天（补跑某一天的任务，只会下架更少）。同一天重复执行是幂等的
--      （已 retired 的不再处理）。返回本次下架条数；
--   4. 授权：只授 starbuddy_kge（每日体检由知识服务身份执行；命令行入口 python -m review_console expire-affairs 用 kge 连接）。
--      函数属主 authz 补授 kge.current_affair 的 UPDATE (review_status)（列级）。
--   5. 下架不可逆（契约审查）：已发布、已撤下地图版本上的时政一旦 retired，不能再改回 published 或 pending（触发器
--      kge.guard_affair_retired）。否则 0019 的 guard_atom_approval 只认最近一条 action = review 的结论，下架前那条批准仍在，
--      写入方就能不经审核把过期时政重新上线。要重新使用同一素材，新建一行（新 ID）走待审与签字。草稿版本上的原子随整个版本签字，
--      不受此限（与 guard_atom_approval 同口径）；
--   6. expired_auto 记录不是人工批准（契约审查 #7）：decision = approved 只是因为 0019 的约束要求系统动作带结论，
--      它没有签字人（不写 platform.content_signoff），不能当作任何人审核过该时政的证据；见函数上的 COMMENT。

CREATE FUNCTION kge.expire_current_affairs(p_today date) RETURNS integer
  LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog, kge, platform, pg_temp SET TimeZone = 'UTC'
  AS $$
DECLARE
  c kge.current_affair%ROWTYPE;
  v_n integer := 0;
BEGIN
  IF p_today IS NULL THEN
    RAISE EXCEPTION '必须给出当天日期' USING ERRCODE = 'invalid_parameter_value';
  END IF;
  IF p_today > (now() AT TIME ZONE 'Asia/Shanghai')::date THEN
    RAISE EXCEPTION '当天日期不能晚于北京时间的今天（不能提前下架）' USING ERRCODE = 'invalid_parameter_value';
  END IF;
  -- 同一时刻只有一个下架任务在跑（咨询锁）；逐行加锁，与写入方的修改互斥
  PERFORM pg_advisory_xact_lock(hashtext('kge.expire_current_affairs'));
  FOR c IN SELECT * FROM kge.current_affair x
            WHERE x.review_status = 'published' AND x.valid_until < p_today
            ORDER BY x.id FOR UPDATE LOOP
    UPDATE kge.current_affair SET review_status = 'retired' WHERE id = c.id;
    INSERT INTO kge.content_review (object_type, object_id, risk_level, decision, decided_at, digest, basis_code, action)
    VALUES ('current_affair', c.id::text, 'low', 'approved', clock_timestamp(), kge.row_digest(to_jsonb(c) - 'review_status'),
            'expired_auto', 'retire');
    v_n := v_n + 1;
  END LOOP;
  RETURN v_n;
END $$;
ALTER FUNCTION kge.expire_current_affairs(date) OWNER TO starbuddy_authz;
REVOKE EXECUTE ON FUNCTION kge.expire_current_affairs(date) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION kge.expire_current_affairs(date) TO starbuddy_kge;
GRANT UPDATE (review_status) ON kge.current_affair TO starbuddy_authz;
COMMENT ON FUNCTION kge.expire_current_affairs(date) IS
  '时政过期下架（0043）：valid_until < p_today 且 published 的改为 retired，并追加系统动作审核记录（basis_code = expired_auto，无签字人）。'
  'expired_auto 记录不是人工批准：decision = approved 只为满足审核记录的结论约束，没有签字人，不能当作任何人审核过该时政的证据';

-- 下架不可逆：已发布、已撤下地图版本上的时政从 retired 改成别的状态一律拒绝（草稿版本随整个版本签字，不受此限）
CREATE FUNCTION kge.guard_affair_retired() RETURNS trigger LANGUAGE plpgsql
  SET search_path = pg_catalog, kge, pg_temp
  AS $$
DECLARE
  v_status text;
BEGIN
  IF OLD.review_status = 'retired' AND NEW.review_status IS DISTINCT FROM 'retired' THEN
    SELECT v.status INTO v_status FROM kge.map_version v WHERE v.id = OLD.map_version_id FOR SHARE;
    IF v_status IS DISTINCT FROM 'draft' THEN
      RAISE EXCEPTION '已下架的时政不能重新上线或退回待审；要重新使用请新建一条走审核' USING ERRCODE = 'check_violation';
    END IF;
  END IF;
  RETURN NEW;
END $$;
REVOKE EXECUTE ON FUNCTION kge.guard_affair_retired() FROM PUBLIC;
CREATE TRIGGER guard_affair_retired BEFORE UPDATE ON kge.current_affair
  FOR EACH ROW EXECUTE FUNCTION kge.guard_affair_retired();
COMMENT ON FUNCTION kge.guard_affair_retired() IS
  '时政下架不可逆（0043）：已发布、已撤下地图版本上 retired 的时政不能改回 published 或 pending；重新使用须新建一行走审核';
