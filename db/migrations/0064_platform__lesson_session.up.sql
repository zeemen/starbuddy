-- 0064 platform · 讲评课会话（specs/stage-lesson-session/spec.md 第二节第 3、4 条，PR ③b-3；第五节五条按默认，用户 2026-11-26 确认）
-- 状态：草稿。只写文件，由人先在副本演练再执行（CLAUDE.md 必停项：迁移、RLS 与权限、计量）。不在任何共享环境执行。
-- 请以单事务执行（psql -1）。
-- 依赖：0002（档案）、0008 / 0009（usage_reservation、meter_now）、0035（个人层表清单、删除执行身份 starbuddy_eraser、导出）。
-- 依据：spec 第二节第 1、3、4 条与第五节第 1–3、5 条；CLAUDE.md 规则 2、4、5、6、8、11、13。
--
-- 问题：一节讲评课只扣一次 lesson_whiteboard（BFF 打开时预留、内容下发即结算），课内提问不另扣，但每次提问仍要挂在这一笔计量上，
-- 且要限次（每节最多 20 问）、限时（有效期与预留一致）、可幂等重放。学习服务的登录角色读不到计量账本（0058 的理由），
-- 所以“会话对得上哪笔预留”“这一问能不能问”都放在库里判定。
-- 做法：
--   1. 新表 platform.lesson_session：一行 = 学生打开的一节讲评课。只存学生、学科、讲评课引用（lesson_id、lesson_version）、
--      预留的 usage_id 与幂等键、状态、提问上限与已提问次数、最近一问的幂等键、创建与过期时间。
--      不存讲解 DSL、台词、提问与回答（spec 第二节第 3、5 条：问答不进证据、掌握与判断；已提问次数只是会话上的计数，不进画像）。
--   2. 新建守卫（属主 authz，SECURITY DEFINER）：
--      - 学生档案存在且未删除（FOR KEY SHARE，与删除执行串行，同 0055）；
--      - 只能以 opening、0 问、没有最近一问的键新建；
--      - 必须对得上一笔 BFF 的讲评课预留：usage_id、幂等键、学生一致，来源方法 teach、资源 lesson_whiteboard、
--        状态 reserved 且未过期。对不上一律 42501（不透露是哪一项对不上）；
--      - 创建时间取计量时钟、过期时间取这笔预留的过期时间（调用方传的值一律覆盖）：会话有效期与预留一致（spec 第五节第 3 条）。
--   3. 状态：opening（已建会话、讲评课还没下发）→ open（讲解 DSL 与台词稿校验通过、已下发）或 failed（不合格或适配器失败，不下发；
--      BFF 释放预留）。open、failed 不再变；过期以后不能再变成 open。学习服务只能改 status 一列。
--   4. 课内提问：platform.lesson_session_ask(会话, 学生, 这一问的幂等键)（属主 authz，只授学习服务）锁住会话行后依次核对，
--      只返回结果编码与第几问：
--        none（会话不存在或不属于该生，两者同一返回）→ not_open（还没下发或已失败）→ expired（过了有效期）
--        → unpaid（这笔预留既不是未过期的 reserved，也不是 settled：已释放、已过期，或过期任务还没跑）
--        → replay（与最近一问的键相同：不加次数，返回同一个第几问，学习服务按（会话，第几问）派生适配器的幂等键重放）
--        → limit（已到上限，spec 第二节第 4 条：超了直接回“不答”，不调适配器）→ admitted（次数加 1，返回新的第几问）。
--      已提问次数与最近一问的键只能经这个函数改（学习服务没有这两列的 UPDATE），守卫另外核对“每次只加 1、只在 open 且未过期时加、
--      加的同时换键”。
--   5. 读：学习服务读写（RLS 放行）；BFF 不读（讲评课票据由学习服务签发、BFF 加密下发，spec 第二节第 1、4 条，BFF 不需要读表），
--      家庭经已有的导出拿到这张表（见第 6 条）。其余应用角色读不到。
--   6. 删除与导出：登记进 0035 的个人层表清单（delete，范围 evidence，与诊断会话同组；导出主题 lesson_sessions，
--      只导学科、讲评课引用、状态、已提问次数与创建时间，不导 usage_id、幂等键与过期时间）。
--   计量口径不变：不预留、不结算、不回滚（BFF 照旧经 meter_reserve / meter_settle / meter_release），只读 usage_reservation。

-- ---------- 会话 ----------
CREATE TABLE platform.lesson_session (
  id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  student_id       uuid NOT NULL REFERENCES platform.student_profile (id) ON DELETE CASCADE,
  subject          platform.subject NOT NULL CHECK (subject IN ('wenli', 'aya', 'zhixing')),
  -- 讲评课引用：与插件契约 v1.1 草稿的 LessonRef 同一规则（lesson_id、lesson_version）
  lesson_id        text NOT NULL CHECK (lesson_id ~ '^[a-z][a-z0-9_.\-]{0,63}$'),
  lesson_version   text NOT NULL CHECK (lesson_version ~ '^\d{1,3}\.\d{1,3}\.\d{1,4}$'),
  usage_id         uuid NOT NULL,                      -- 计量（规则 4）：打开这节课的那笔 lesson_whiteboard 预留
  idempotency_key  uuid NOT NULL,                      -- 那笔预留的幂等键（BFF 每次打开生成一个）
  status           text NOT NULL DEFAULT 'opening' CHECK (status IN ('opening', 'open', 'failed')),
  question_limit   smallint NOT NULL DEFAULT 20 CHECK (question_limit BETWEEN 1 AND 20),
  question_count   smallint NOT NULL DEFAULT 0 CHECK (question_count >= 0),
  last_ask_key     uuid,                               -- 最近一问的幂等键（只用来认出重放；不存提问内容）
  created_at       timestamptz NOT NULL DEFAULT now(), -- 守卫改写为计量时钟
  expires_at       timestamptz NOT NULL DEFAULT now(), -- 守卫改写为那笔预留的过期时间
  updated_at       timestamptz NOT NULL DEFAULT now(),
  UNIQUE (student_id, idempotency_key),
  UNIQUE (usage_id),
  CONSTRAINT lesson_session_question_limit CHECK (question_count <= question_limit),
  CONSTRAINT lesson_session_ask_key CHECK ((question_count = 0) = (last_ask_key IS NULL)),
  CONSTRAINT lesson_session_asked_open CHECK (question_count = 0 OR status = 'open'),
  CONSTRAINT lesson_session_window CHECK (expires_at > created_at)
);
CREATE INDEX lesson_session_student_idx ON platform.lesson_session (student_id);
COMMENT ON TABLE platform.lesson_session IS
  '讲评课会话（0064，stage-lesson-session ③b-3）：一节课一笔 lesson_whiteboard 预留，课内提问不另扣，只累计次数。'
  '只由学习服务写；不存讲解、提问与回答；有效期与预留一致';
COMMENT ON COLUMN platform.lesson_session.status IS 'opening：已建会话未下发；open：已下发；failed：不合格或适配器失败，未下发（BFF 释放预留）';
COMMENT ON COLUMN platform.lesson_session.question_count IS '已提问次数（只经 platform.lesson_session_ask 增加）；不进证据、掌握与判断';
COMMENT ON COLUMN platform.lesson_session.last_ask_key IS '最近一问的幂等键：同一个键再来按重放处理，不加次数';
COMMENT ON COLUMN platform.lesson_session.expires_at IS '会话有效期：取打开时那笔预留的过期时间（守卫写入，调用方不能指定）';

-- ---------- 守卫（属主 authz，SECURITY DEFINER：要读档案与计量预留） ----------
CREATE FUNCTION platform.lesson_session_guard() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
  SET search_path = pg_catalog, platform, pg_temp
  AS $$
DECLARE
  v_erased timestamptz;
  v_now timestamptz := platform.meter_now();
  v_expires timestamptz;
BEGIN
  IF TG_OP = 'INSERT' THEN
    SELECT s.erased_at INTO v_erased FROM platform.student_profile s WHERE s.id = NEW.student_id FOR KEY SHARE;
    IF NOT FOUND OR v_erased IS NOT NULL THEN
      RAISE EXCEPTION 'not allowed' USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF NEW.status <> 'opening' OR NEW.question_count <> 0 OR NEW.last_ask_key IS NOT NULL THEN
      RAISE EXCEPTION '讲评课会话只能以 opening、0 问新建' USING ERRCODE = 'check_violation';
    END IF;
    -- 必须对得上一笔未过期的讲评课预留（任一项对不上与没有预留同一错误）
    SELECT r.expires_at INTO v_expires FROM platform.usage_reservation r
     WHERE r.usage_id = NEW.usage_id AND r.idempotency_key = NEW.idempotency_key::text AND r.student_id = NEW.student_id
       AND r.source_method = 'teach' AND r.resource_type = 'lesson_whiteboard'
       AND r.state = 'reserved' AND r.expires_at > v_now;
    IF NOT FOUND THEN
      RAISE EXCEPTION 'no matching lesson reservation' USING ERRCODE = 'insufficient_privilege';
    END IF;
    NEW.created_at := v_now;
    NEW.expires_at := v_expires;
    NEW.updated_at := now();
    RETURN NEW;
  END IF;
  -- UPDATE
  IF (NEW.id, NEW.student_id, NEW.subject, NEW.lesson_id, NEW.lesson_version, NEW.usage_id, NEW.idempotency_key, NEW.question_limit,
      NEW.created_at, NEW.expires_at)
     IS DISTINCT FROM (OLD.id, OLD.student_id, OLD.subject, OLD.lesson_id, OLD.lesson_version, OLD.usage_id, OLD.idempotency_key,
                       OLD.question_limit, OLD.created_at, OLD.expires_at) THEN
    RAISE EXCEPTION '讲评课会话的身份、引用、计量与有效期不能改' USING ERRCODE = '22023';
  END IF;
  IF NEW.status IS DISTINCT FROM OLD.status THEN
    IF OLD.status <> 'opening' THEN
      RAISE EXCEPTION '已下发或已失败的讲评课会话不能再改状态' USING ERRCODE = '22023';
    END IF;
    IF NEW.status = 'open' AND v_now >= OLD.expires_at THEN
      RAISE EXCEPTION '讲评课会话已过期，不能再下发' USING ERRCODE = '22023';
    END IF;
  END IF;
  IF NEW.question_count IS DISTINCT FROM OLD.question_count OR NEW.last_ask_key IS DISTINCT FROM OLD.last_ask_key THEN
    IF NEW.question_count <> OLD.question_count + 1 OR OLD.status <> 'open' OR NEW.status <> 'open' OR v_now >= OLD.expires_at
       OR NEW.last_ask_key IS NULL OR NEW.last_ask_key IS NOT DISTINCT FROM OLD.last_ask_key THEN
      RAISE EXCEPTION '提问次数只能在已下发、未过期时每次加 1，且同时换最近一问的键' USING ERRCODE = '22023';
    END IF;
  END IF;
  NEW.updated_at := now();
  RETURN NEW;
END $$;
ALTER FUNCTION platform.lesson_session_guard() OWNER TO starbuddy_authz;
REVOKE EXECUTE ON FUNCTION platform.lesson_session_guard() FROM PUBLIC;
CREATE TRIGGER lesson_session_guard BEFORE INSERT OR UPDATE ON platform.lesson_session
  FOR EACH ROW EXECUTE FUNCTION platform.lesson_session_guard();

-- ---------- 课内提问的放行（属主 authz，只授学习服务） ----------
-- 返回 {"outcome": none | not_open | expired | unpaid | replay | limit | admitted, "question_seq": 第几问或 null}；
-- 不返回预留状态、额度、数量与时间。参数为空与会话不存在同为 none
CREATE FUNCTION platform.lesson_session_ask(p_session uuid, p_student uuid, p_ask_key uuid) RETURNS jsonb
  LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog, platform, pg_temp
  AS $$
DECLARE
  s platform.lesson_session;
  v_now timestamptz := platform.meter_now();
BEGIN
  IF p_session IS NULL OR p_student IS NULL OR p_ask_key IS NULL THEN
    RETURN jsonb_build_object('outcome', 'none', 'question_seq', NULL);
  END IF;
  SELECT * INTO s FROM platform.lesson_session WHERE id = p_session AND student_id = p_student FOR UPDATE;
  IF NOT FOUND THEN
    RETURN jsonb_build_object('outcome', 'none', 'question_seq', NULL);
  END IF;
  IF s.status <> 'open' THEN
    RETURN jsonb_build_object('outcome', 'not_open', 'question_seq', NULL);
  END IF;
  IF v_now >= s.expires_at THEN
    RETURN jsonb_build_object('outcome', 'expired', 'question_seq', NULL);
  END IF;
  -- 这节课的那笔预留仍然有效：未过期的 reserved（BFF 结算前）或 settled（已下发结算）；已释放、已过期一律不放行
  IF NOT EXISTS (SELECT 1 FROM platform.usage_reservation r
                  WHERE r.usage_id = s.usage_id AND r.idempotency_key = s.idempotency_key::text AND r.student_id = s.student_id
                    AND r.source_method = 'teach'
                    AND (r.state = 'settled' OR (r.state = 'reserved' AND r.expires_at > v_now))) THEN
    RETURN jsonb_build_object('outcome', 'unpaid', 'question_seq', NULL);
  END IF;
  IF s.last_ask_key = p_ask_key THEN
    RETURN jsonb_build_object('outcome', 'replay', 'question_seq', s.question_count);
  END IF;
  IF s.question_count >= s.question_limit THEN
    RETURN jsonb_build_object('outcome', 'limit', 'question_seq', NULL);
  END IF;
  UPDATE platform.lesson_session SET question_count = question_count + 1, last_ask_key = p_ask_key WHERE id = s.id;
  RETURN jsonb_build_object('outcome', 'admitted', 'question_seq', s.question_count + 1);
END $$;
ALTER FUNCTION platform.lesson_session_ask(uuid, uuid, uuid) OWNER TO starbuddy_authz;
REVOKE EXECUTE ON FUNCTION platform.lesson_session_ask(uuid, uuid, uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION platform.lesson_session_ask(uuid, uuid, uuid) TO starbuddy_learning;
COMMENT ON FUNCTION platform.lesson_session_ask(uuid, uuid, uuid) IS
  '讲评课课内提问的放行（0064）：会话属于该生、已下发、未过期、那笔预留仍有效（未过期的 reserved 或 settled）、没到上限才加 1 次；'
  '同一个键再来是重放（不加次数）。只返回结果编码与第几问；只授 starbuddy_learning';

-- ---------- 权限与 RLS ----------
ALTER TABLE platform.lesson_session ENABLE ROW LEVEL SECURITY;
ALTER TABLE platform.lesson_session FORCE ROW LEVEL SECURITY;
REVOKE ALL ON platform.lesson_session FROM PUBLIC;
-- 学习服务：唯一写入方（规则 2、11）；只能改状态。提问次数与最近一问的键只经 lesson_session_ask
GRANT SELECT, INSERT ON platform.lesson_session TO starbuddy_learning;
GRANT UPDATE (status) ON platform.lesson_session TO starbuddy_learning;
CREATE POLICY lesson_session_learning ON platform.lesson_session FOR ALL TO starbuddy_learning USING (true) WITH CHECK (true);
-- 放行函数与导出函数的属主（authz，BYPASSRLS）：读会话、改提问次数与最近一问的键
GRANT SELECT ON platform.lesson_session TO starbuddy_authz;
GRANT UPDATE (question_count, last_ask_key) ON platform.lesson_session TO starbuddy_authz;
-- 删除执行身份（eraser，0035）：清单 delete 表的 DELETE 与学生键列
GRANT DELETE ON platform.lesson_session TO starbuddy_eraser;
GRANT SELECT (student_id) ON platform.lesson_session TO starbuddy_eraser;

-- ---------- 个人层表清单（0035）：随删除请求删除；导出只导学科、讲评课引用、状态、已提问次数与创建时间 ----------
INSERT INTO platform.personal_table_registry (nspname, relname, key_path, student_predicate, strategy, exempt_reason, delete_order,
                                              erase_scope, export_topic, export_columns, export_filter)
VALUES ('platform', 'lesson_session', 'student_id', 'student_id = $1', 'delete', NULL, 43, 'evidence', 'lesson_sessions',
        ARRAY['subject', 'lesson_id', 'lesson_version', 'status', 'question_count', 'created_at'], NULL);
