-- 0055 platform · 今日计划快照（specs/today-plan-snapshot-plt15/spec.md PR1 范围第 1 条，PLT-15）
-- 状态：草稿。只写文件，由人先在副本演练再执行（CLAUDE.md 必停项：迁移、RLS 与权限、删除覆盖与保留期、画像可见范围）。
-- 请以单事务执行（psql -1）。
-- 依赖 0002（档案、绑定、can_read_student）、0035（个人层表清单、删除执行身份 starbuddy_eraser、platform.execute_due_erasures）。
-- 依据：spec PR1 第 1 条与“待人确认”1、3（按默认）；PLT-11 lessons 待人确认 9（计划落库另起）；CLAUDE.md 规则 2、5、6、11、13。
-- 问题：今日计划读时现算、没有任务 ID；复测、再练条目的 entry_id 跨天不变，不能当 plan_task_id。
-- 做法：
--   1. 新表 platform.today_plan_item：一行 = 某个学生某一天（北京时间日期）跨科今日计划里的一条任务；id 就是任务 ID（plan_task_id）。
--      当天首次读取今日计划时由学习服务一次性冻结（同一学生同一天只冻结一批，同日再读返回同一批 ID）；只存编排结果的结构化字段
--      （学科、类别、来源、节点、预计分钟、条目引用、计划原有的推荐理由编码），不存掌握、置信度、证据 ID、理由文本（规则 3、11）；
--      来源 source 取 plan / practice / retest / verify（复测列表里的验证题单列为 verify）；
--      同一天同一来源的同一条目只出现一次（复测、再练按 entry_ref 唯一；计划任务没有条目引用）；
--   2. 写入方只有学习服务（规则 2、11）：starbuddy_learning 只有 SELECT、INSERT，没有 UPDATE、DELETE；插入守卫（属主 authz）
--      拒绝档案不存在或已删除的学生（42501），plan_date 必须是当天的北京时间日期（不能补写别的日子）；
--      推荐理由编码收窄到 BFF 已登记的理由编码（其余由学习服务写入前置空）；
--   3. 读：BFF 只读当天的行，且只限学生本人与经 guardian_student_link 授权的有效家长（platform.can_read_student，规则 5；
--      家长可见按 spec 待人确认 3 的默认），列级授权不含节点、推荐理由编码与创建时间；
--      行为事件写入函数（0056，属主 authz）只读任务 ID、学生与日期；
--   4. 删除覆盖：登记进 0035 的个人层表清单（delete，范围 inference，不进导出包），随删除请求删除；
--   5. 保留期（spec 待人确认 1，合成值 90 天）：配置在 platform.today_plan_retention_days()；到期由已有的删除作业清理——
--      本迁移在 platform.execute_due_erasures（0035，定时任务 erasures 每小时调用）开头加一步：删掉 plan_date 早于
--      “北京时间今天 − 保留天数”（含当天）的快照行。只有这个函数能删个人层清单里的表（db/tests/80 的断言），所以不另建清理函数；
--      清理放在子事务里、锁等待最多 2 秒，失败只跳过清理，不影响到期删除请求。
--   不计量（读取今日计划不是消耗类请求，规则 4 不适用）。

-- ---------- 保留期配置（合成值，待人确认） ----------
CREATE FUNCTION platform.today_plan_retention_days() RETURNS integer
  LANGUAGE sql IMMUTABLE SET search_path = pg_catalog
  AS $$ SELECT 90 $$;
COMMENT ON FUNCTION platform.today_plan_retention_days() IS
  '今日计划快照保留天数（0055，PLT-15 待人确认 1）：合成值 90；plan_date ≤ 北京时间今天 − 该值的行由 platform.execute_due_erasures 删除';

-- ---------- 快照表 ----------
CREATE TABLE platform.today_plan_item (
  id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),                 -- 任务 ID（plan_task_id）
  student_id         uuid NOT NULL REFERENCES platform.student_profile (id) ON DELETE CASCADE,
  plan_date          date NOT NULL DEFAULT ((now() AT TIME ZONE 'Asia/Shanghai')::date),
  seq                smallint NOT NULL CHECK (seq BETWEEN 1 AND 64),
  subject            platform.subject NOT NULL CHECK (subject IN ('wenli', 'aya', 'zhixing')),
  kind               text NOT NULL CHECK (kind IN ('retest', 'verify', 'practice', 'diagnose', 'review', 'lesson', 'mock_exam')),
  source             text NOT NULL CHECK (source IN ('plan', 'practice', 'retest', 'verify')),
  node_id            text CHECK (node_id IS NULL OR char_length(node_id) BETWEEN 1 AND 200),
  estimated_minutes  smallint NOT NULL CHECK (estimated_minutes BETWEEN 1 AND 600),
  entry_ref          uuid,
  -- 推荐理由编码收窄到 BFF 已登记的理由编码（services/bff/config/shell.json 的 content 与 about_student 两表之并）：
  -- 只有这些编码可能被显示；其余编码学习服务写入前置空（不显示理由，与 BFF“两张表都没有的编码不显示”一致）。表变动随迁移
  rationale_code     text CONSTRAINT today_plan_item_rationale_code CHECK (rationale_code IN ('high_exam_weight', 'prerequisite_of_many',
                                                                                       'retest_scheduled', 'retest_due', 'weak_mastery')),
  created_at         timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT today_plan_item_seq UNIQUE (student_id, plan_date, seq),
  -- 来源与类别对得上：复测列表的条目是 retest / verify，再练是 practice，计划任务取适配器的类别
  CONSTRAINT today_plan_item_kind_source CHECK (CASE source WHEN 'retest' THEN kind = 'retest'
                                                             WHEN 'verify' THEN kind = 'verify'
                                                             WHEN 'practice' THEN kind = 'practice'
                                                             ELSE kind IN ('diagnose', 'practice', 'review', 'lesson', 'retest', 'mock_exam') END),
  -- 复测、再练条目必须带条目引用与节点；计划任务没有条目引用。推荐理由编码只有计划任务有
  CONSTRAINT today_plan_item_entry_ref CHECK ((source = 'plan') = (entry_ref IS NULL)),
  CONSTRAINT today_plan_item_entry_node CHECK (source = 'plan' OR node_id IS NOT NULL),
  CONSTRAINT today_plan_item_rationale CHECK (source = 'plan' OR rationale_code IS NULL)
);
-- 同一天同一来源的同一条目只出现一次
CREATE UNIQUE INDEX today_plan_item_entry_uq ON platform.today_plan_item (student_id, plan_date, source, entry_ref) WHERE entry_ref IS NOT NULL;
CREATE INDEX today_plan_item_date_idx ON platform.today_plan_item (plan_date);
COMMENT ON TABLE platform.today_plan_item IS
  '今日计划快照（0055，PLT-15）：当天首次读取今日计划时由学习服务冻结，id 即任务 ID；只存编排结果，不存掌握、置信度、证据 ID、'
  '理由文本。BFF 只读当天行（学生本人与有效绑定家长）；保留期见 platform.today_plan_retention_days()';
COMMENT ON COLUMN platform.today_plan_item.plan_date IS '北京时间日期；插入时必须是当天（守卫触发器）';
COMMENT ON COLUMN platform.today_plan_item.source IS '来源：plan（各科计划）/ practice（错题再练）/ retest（复测）/ verify（验证题）';
COMMENT ON COLUMN platform.today_plan_item.entry_ref IS '复测、再练条目的 entry_id（可直接去做）；计划任务为空';
COMMENT ON COLUMN platform.today_plan_item.rationale_code IS '计划任务原有的推荐理由编码（不是文本）；复测、再练为空';

-- 插入守卫（属主 authz，SECURITY DEFINER）：
--   - 学生档案行 FOR KEY SHARE 后核对未删除：档案不存在或已执行删除（0035 erased_at）一律 42501（与未授权同一错误）；
--     与删除执行（对档案行 FOR UPDATE）串行，删除执行进行中写入的快照要等它提交后按已删除拒绝（安全审查阻断 1）；
--   - plan_date 必须是当天的北京时间日期（学习服务不能补写或预写别的日子）
CREATE FUNCTION platform.today_plan_item_guard() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
  SET search_path = pg_catalog, platform, pg_temp
  AS $$
DECLARE v_erased timestamptz;
BEGIN
  SELECT s.erased_at INTO v_erased FROM platform.student_profile s WHERE s.id = NEW.student_id FOR KEY SHARE;
  IF NOT FOUND OR v_erased IS NOT NULL THEN
    RAISE EXCEPTION 'not allowed' USING ERRCODE = 'insufficient_privilege';
  END IF;
  IF NEW.plan_date IS DISTINCT FROM (now() AT TIME ZONE 'Asia/Shanghai')::date THEN
    RAISE EXCEPTION '今日计划快照只能写当天（北京时间）' USING ERRCODE = 'check_violation';
  END IF;
  RETURN NEW;
END $$;
ALTER FUNCTION platform.today_plan_item_guard() OWNER TO starbuddy_authz;
REVOKE EXECUTE ON FUNCTION platform.today_plan_item_guard() FROM PUBLIC;
CREATE TRIGGER today_plan_item_guard BEFORE INSERT ON platform.today_plan_item
  FOR EACH ROW EXECUTE FUNCTION platform.today_plan_item_guard();

-- ---------- 权限与 RLS ----------
ALTER TABLE platform.today_plan_item ENABLE ROW LEVEL SECURITY;
ALTER TABLE platform.today_plan_item FORCE ROW LEVEL SECURITY;
REVOKE ALL ON platform.today_plan_item FROM PUBLIC;
-- 学习服务：唯一写入方（规则 2、11）；只有 SELECT、INSERT（快照冻结后不改，删除只经删除执行身份）
GRANT SELECT, INSERT ON platform.today_plan_item TO starbuddy_learning;
CREATE POLICY today_plan_item_service_read ON platform.today_plan_item FOR SELECT TO starbuddy_learning USING (true);
CREATE POLICY today_plan_item_service_write ON platform.today_plan_item FOR INSERT TO starbuddy_learning WITH CHECK (true);
-- BFF：只读当天的行；学生本人与有效绑定家长（规则 5）。列级授权：不给节点、推荐理由编码与创建时间（审查建议）
GRANT SELECT (id, student_id, plan_date, seq, subject, kind, source, estimated_minutes, entry_ref) ON platform.today_plan_item TO starbuddy_bff;
CREATE POLICY today_plan_item_read ON platform.today_plan_item FOR SELECT TO starbuddy_bff
  USING (platform.can_read_student(student_id) AND plan_date = (now() AT TIME ZONE 'Asia/Shanghai')::date);
-- 行为事件写入函数的属主（authz，0056）：只读任务 ID、学生与日期
GRANT SELECT (id, student_id, plan_date) ON platform.today_plan_item TO starbuddy_authz;
-- 删除执行身份（eraser，0035）：清单 delete 表的 DELETE 与学生键列；保留期清理按日期删，另读 plan_date
GRANT DELETE ON platform.today_plan_item TO starbuddy_eraser;
GRANT SELECT (student_id, plan_date) ON platform.today_plan_item TO starbuddy_eraser;
REVOKE EXECUTE ON FUNCTION platform.today_plan_retention_days() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION platform.today_plan_retention_days() TO starbuddy_eraser;

-- ---------- 个人层表清单（0035）：随删除请求删除；不进导出包（当天计划是编排结果，导出范围待人确认） ----------
INSERT INTO platform.personal_table_registry (nspname, relname, key_path, student_predicate, strategy, exempt_reason, delete_order,
                                              erase_scope, export_topic, export_columns, export_filter)
VALUES ('platform', 'today_plan_item', 'student_id', 'student_id = $1', 'delete', NULL, 39, 'inference', NULL, NULL, NULL);

-- ---------- 已有的删除作业：开头加一步保留期清理（其余与 0035 逐字相同；属主 eraser 与授权不变） ----------
CREATE OR REPLACE FUNCTION platform.execute_due_erasures(p_now timestamptz) RETURNS integer
  LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog, platform, pg_temp
  AS $$
DECLARE
  r platform.erasure_request;
  t platform.personal_table_registry;
  v_n integer := 0;
  v_count bigint;
  v_receipt jsonb;
  v_account uuid;
  v_lock text;
BEGIN
  IF p_now IS NULL OR p_now > now() OR p_now < now() - interval '1 day' THEN
    RAISE EXCEPTION 'p_now must be within the last day and not in the future' USING ERRCODE = 'invalid_parameter_value';
  END IF;
  -- 今日计划快照保留期（0055）：plan_date ≤ 北京时间今天 − 保留天数的行到期删除（不写回执：不是删除请求）。
  -- 放在子事务里、锁等待最多 2 秒：清理失败（锁等待超时等）只回滚这一步、发一条警告，不影响下面到期删除请求的执行；
  -- 锁等待上限只在这一步内有效（子事务结束时恢复原值，出错回滚时自动恢复）
  BEGIN
    v_lock := current_setting('lock_timeout');
    PERFORM set_config('lock_timeout', '2s', true);
    DELETE FROM platform.today_plan_item
     WHERE plan_date <= (p_now AT TIME ZONE 'Asia/Shanghai')::date - platform.today_plan_retention_days();
    PERFORM set_config('lock_timeout', v_lock, true);
  EXCEPTION WHEN OTHERS THEN
    RAISE WARNING 'today_plan_item 保留期清理失败（%），本次跳过', SQLSTATE;
  END;
  UPDATE platform.erasure_request SET status = 'cancelled', cancelled_at = p_now, cancel_reason = 'confirm_expired'
   WHERE status = 'pending' AND confirmed_at IS NULL AND requested_at < p_now - interval '24 hours';
  FOR r IN SELECT * FROM platform.erasure_request x
            WHERE x.status = 'pending' AND x.confirmed_at IS NOT NULL AND x.cooling_off_until <= p_now
            ORDER BY x.cooling_off_until, x.id FOR UPDATE SKIP LOCKED LOOP
    IF NOT EXISTS (SELECT 1 FROM platform.guardian_student_link l JOIN platform.account a ON a.id = l.guardian_account_id
                    WHERE l.student_id = r.student_id AND l.guardian_account_id = r.requested_by AND l.status = 'active'
                      AND a.kind = 'guardian' AND a.status = 'active') THEN
      UPDATE platform.erasure_request SET status = 'cancelled', cancelled_at = p_now, cancel_reason = 'link_revoked' WHERE id = r.id;
      CONTINUE;
    END IF;
    SELECT s.account_id INTO v_account FROM platform.student_profile s WHERE s.id = r.student_id FOR UPDATE;
    PERFORM set_config('starbuddy.erasure_student', r.student_id::text, true);
    v_receipt := '{}';
    FOR t IN SELECT * FROM platform.personal_table_registry WHERE strategy = 'delete' ORDER BY delete_order LOOP
      EXECUTE format('SELECT count(*) FROM %I.%I WHERE %s', t.nspname, t.relname, t.student_predicate) USING r.student_id INTO v_count;
      v_receipt := v_receipt || jsonb_build_object(t.nspname || '.' || t.relname, v_count);
    END LOOP;
    FOR t IN SELECT * FROM platform.personal_table_registry WHERE strategy = 'delete' ORDER BY delete_order LOOP
      EXECUTE format('DELETE FROM %I.%I WHERE %s', t.nspname, t.relname, t.student_predicate) USING r.student_id;
    END LOOP;
    FOR t IN SELECT * FROM platform.personal_table_registry WHERE strategy = 'delete' ORDER BY delete_order LOOP
      EXECUTE format('SELECT count(*) FROM %I.%I WHERE %s', t.nspname, t.relname, t.student_predicate) USING r.student_id INTO v_count;
      IF v_count <> 0 THEN
        RAISE EXCEPTION '删除后 %.% 仍有 % 行', t.nspname, t.relname, v_count USING ERRCODE = 'internal_error';
      END IF;
    END LOOP;
    UPDATE platform.account SET status = 'disabled' WHERE id = v_account;
    UPDATE platform.student_profile SET erased_at = p_now WHERE id = r.student_id;
    PERFORM set_config('starbuddy.erasure_student', '', true);
    UPDATE platform.erasure_request
       SET status = 'executed', executed_at = p_now, receipt = v_receipt,
           scopes = (SELECT array_agg(DISTINCT g.erase_scope ORDER BY g.erase_scope) FROM platform.personal_table_registry g WHERE g.strategy = 'delete')
     WHERE id = r.id;
    v_n := v_n + 1;
  END LOOP;
  RETURN v_n;
END $$;
