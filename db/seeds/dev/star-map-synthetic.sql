-- 星图原型的合成种子（specs/star-map-sprint3/spec.md 范围第 4 条）。只给开发库与一次性测试库用，不由迁移执行，
-- 不能用于 pilot 或生产（CLAUDE.md 规则 8、9）。全部是合成数据：没有姓名、学校、手机号。
--
-- 前提：已执行全部迁移，并已跑过阿鸦内容登记（959 个能力点）：
--   node adapters/aya/scripts/register-content.ts > /tmp/aya-register.sql && psql -f /tmp/aya-register.sql
-- 用法（以迁移执行者身份；只能执行一次；文件自带事务，不要再加 -1）：
--   PGOPTIONS='-c starbuddy.allow_synthetic_seed=on' psql -X -v ON_ERROR_STOP=1 -f db/seeds/dev/star-map-synthetic.sql
--   不设 starbuddy.allow_synthetic_seed=on、或库名不是 starbuddy_dev* / starbuddy_test* 时直接报错：防止误跑到开发库以外
--   （合成的知行能力点会出现在该库每个知行学生的星空里）
--
-- 内容：
--   家庭 HS（合成）：家长 gS，兄妹 S1、S2。S1 选了阿鸦与知行（都在读）；S2 选了阿鸦（在读）与知行（已暂停）。
--   家庭 HO（无关）：家长 gO，学生 S3，选了阿鸦。
--   知行：一个合成课程版本与 4 个合成能力点 SYN-ZX-01..04（模拟“过程四步”，不是教研坐标，spec 待人确认 #9）。
--   掌握记录以 starbuddy_learning 身份写入（模拟学习服务）；阿鸦取已登记能力点按 local_id 排序的前 8 个。
--   期望的星图计数写在文件末尾的注释里，BFF 与前端测试都按它断言。
--
-- 与真实学习服务的差异（只为原型演示，不能当作溯源或准入示例）：
--   - mastery_estimate 表注释要求“只有已准入内容的证据能推动 transfer 及以上”；这里的证据全部未准入
--     （知行 source_unverified，阿鸦登记时最高只到 pending_review），却写了 transfer / retain。真实学习服务（4-C）必须拒绝；
--   - 证据只为满足 evidence_ids 非空：每个学生每个学科一条，与具体能力点对不上，不代表推断依据；
--   - computed_at 会被 guard_inference_insert 触发器改写为 now()。
BEGIN;

DO $$
BEGIN
  IF coalesce(current_setting('starbuddy.allow_synthetic_seed', true), '') <> 'on' THEN
    RAISE EXCEPTION '合成种子只能用于开发库与一次性测试库：执行方须显式设置 starbuddy.allow_synthetic_seed=on';
  END IF;
  -- 第二道：库名必须是开发或测试库的约定名（安全审查 S7：执行方自己设的开关挡不住连错库）
  IF current_database() !~ '^starbuddy_(dev|test)(_[a-z0-9_]+)?$' THEN
    RAISE EXCEPTION '合成种子只能用于开发库与测试库（库名 starbuddy_dev* 或 starbuddy_test*），当前库：%', current_database();
  END IF;
  IF EXISTS (SELECT 1 FROM platform.household WHERE id = '5a000000-0000-4000-8000-0000000000d1') THEN
    RAISE EXCEPTION '星图合成种子已经执行过（只能执行一次）';
  END IF;
  IF (SELECT count(*) FROM platform.skill_node WHERE subject = 'aya') < 8 THEN
    RAISE EXCEPTION '没有登记阿鸦能力点：先执行 adapters/aya/scripts/register-content.ts 生成的 SQL';
  END IF;
END $$;

-- ---------- 账号、家庭、学生、绑定、选科（迁移执行者身份，同 db/tests/10_fixtures.sql） ----------
INSERT INTO platform.account (id, kind) VALUES
  ('5a000000-0000-4000-8000-0000000000a1', 'guardian'),   -- gS：HS 的家长
  ('5a000000-0000-4000-8000-0000000000b1', 'student'),    -- S1 的账号
  ('5a000000-0000-4000-8000-0000000000b2', 'student'),    -- S2 的账号
  ('5a000000-0000-4000-8000-0000000000a2', 'guardian'),   -- gO：无关家庭的家长
  ('5a000000-0000-4000-8000-0000000000b3', 'student');    -- S3 的账号
INSERT INTO platform.household (id, created_by_account) VALUES
  ('5a000000-0000-4000-8000-0000000000d1', '5a000000-0000-4000-8000-0000000000a1'),
  ('5a000000-0000-4000-8000-0000000000d2', '5a000000-0000-4000-8000-0000000000a2');
INSERT INTO platform.student_profile (id, account_id, household_id, grade, region, textbook_version, birth_year_month) VALUES
  ('5a000000-0000-4000-8000-0000000000c1', '5a000000-0000-4000-8000-0000000000b1', '5a000000-0000-4000-8000-0000000000d1', 8, 'nanjing', 'bubian-2024', '2013-04-01'),
  ('5a000000-0000-4000-8000-0000000000c2', '5a000000-0000-4000-8000-0000000000b2', '5a000000-0000-4000-8000-0000000000d1', 7, 'nanjing', 'bubian-2024', '2014-09-01'),
  ('5a000000-0000-4000-8000-0000000000c3', '5a000000-0000-4000-8000-0000000000b3', '5a000000-0000-4000-8000-0000000000d2', 8, 'nanjing', 'bubian-2024', '2013-01-01');
INSERT INTO platform.guardian_student_link (guardian_account_id, student_id, granted_via) VALUES
  ('5a000000-0000-4000-8000-0000000000a1', '5a000000-0000-4000-8000-0000000000c1', 'household_creator'),
  ('5a000000-0000-4000-8000-0000000000a1', '5a000000-0000-4000-8000-0000000000c2', 'household_creator'),
  ('5a000000-0000-4000-8000-0000000000a2', '5a000000-0000-4000-8000-0000000000c3', 'household_creator');
INSERT INTO platform.subject_enrollment (student_id, subject, status) VALUES
  ('5a000000-0000-4000-8000-0000000000c1', 'aya', 'active'),
  ('5a000000-0000-4000-8000-0000000000c1', 'zhixing', 'active'),
  ('5a000000-0000-4000-8000-0000000000c2', 'aya', 'active'),
  ('5a000000-0000-4000-8000-0000000000c2', 'zhixing', 'paused'),
  ('5a000000-0000-4000-8000-0000000000c3', 'aya', 'active');

-- ---------- 以下模拟学习服务 ----------
SET LOCAL ROLE starbuddy_learning;

-- 知行的合成课程版本、能力点、题目（编码前缀 SYN-，明确不是教研坐标）
INSERT INTO platform.curriculum_version (id, region, exam_year, grade, textbook_version, subject, content_version)
VALUES ('5a000000-0000-4000-8000-0000000000e1', 'nanjing', 2027, 8, 'syn-2024', 'zhixing', '0.0.1');
INSERT INTO platform.skill_node (subject, local_id, curriculum_version_id, label_code) VALUES
  ('zhixing', 'SYN-ZX-01', '5a000000-0000-4000-8000-0000000000e1', 'SYN-ZX-01'),
  ('zhixing', 'SYN-ZX-02', '5a000000-0000-4000-8000-0000000000e1', 'SYN-ZX-02'),
  ('zhixing', 'SYN-ZX-03', '5a000000-0000-4000-8000-0000000000e1', 'SYN-ZX-03'),
  ('zhixing', 'SYN-ZX-04', '5a000000-0000-4000-8000-0000000000e1', 'SYN-ZX-04');
INSERT INTO platform.item (subject, item_id, curriculum_version_id, skill_local_id, admission) VALUES
  ('zhixing', 'SYN-ZX-ITEM-01', '5a000000-0000-4000-8000-0000000000e1', 'SYN-ZX-01', 'source_unverified');

-- 每个学生、每个学科一条合成证据，让 evidence_ids 有实际指向
CREATE TEMP TABLE syn_aya_item ON COMMIT DROP AS
  SELECT i.item_id, i.skill_local_id, i.admission FROM platform.item i
   WHERE i.subject = 'aya' ORDER BY i.item_id LIMIT 1;
INSERT INTO platform.evidence_event (evidence_id, occurred_at, student_id, subject, kind, item_id, skill_local_id,
  admission, payload, source_method, request_id, request_idempotency_key)
SELECT v.evidence_id, '2026-10-15 10:00:00+00', v.student_id, v.subject, 'response_text', v.item_id, v.skill_local_id,
       v.admission, '{"text": "synthetic-star-map-answer"}', 'grade', gen_random_uuid(), v.idem
  FROM (SELECT '5a000000-0000-4000-8000-0000000000f1'::uuid, '5a000000-0000-4000-8000-0000000000c1'::uuid, 'aya'::platform.subject,
               a.item_id, a.skill_local_id, a.admission, 'synthetic-star-map-s1-aya' FROM syn_aya_item a
        UNION ALL
        SELECT '5a000000-0000-4000-8000-0000000000f2', '5a000000-0000-4000-8000-0000000000c1', 'zhixing',
               'SYN-ZX-ITEM-01', 'SYN-ZX-01', 'source_unverified', 'synthetic-star-map-s1-zhixing'
        UNION ALL
        SELECT '5a000000-0000-4000-8000-0000000000f3', '5a000000-0000-4000-8000-0000000000c2', 'aya',
               a.item_id, a.skill_local_id, a.admission, 'synthetic-star-map-s2-aya' FROM syn_aya_item a
        UNION ALL
        SELECT '5a000000-0000-4000-8000-0000000000f4', '5a000000-0000-4000-8000-0000000000c2', 'zhixing',
               'SYN-ZX-ITEM-01', 'SYN-ZX-01', 'source_unverified', 'synthetic-star-map-s2-zhixing'
        UNION ALL
        SELECT '5a000000-0000-4000-8000-0000000000f5', '5a000000-0000-4000-8000-0000000000c3', 'aya',
               a.item_id, a.skill_local_id, a.admission, 'synthetic-star-map-s3-aya' FROM syn_aya_item a
       ) v(evidence_id, student_id, subject, item_id, skill_local_id, admission, idem);

-- 掌握记录。n1..n8 = 阿鸦已登记能力点按 local_id 排序的前 8 个
CREATE TEMP TABLE syn_aya_node ON COMMIT DROP AS
  SELECT local_id, row_number() OVER (ORDER BY local_id) AS n FROM platform.skill_node WHERE subject = 'aya'
   ORDER BY local_id LIMIT 8;
CREATE TEMP TABLE syn_mastery (student_id uuid, subject platform.subject, n integer, local_id text,
                               state platform.mastery_state, confidence numeric, evidence_id uuid) ON COMMIT DROP;
INSERT INTO syn_mastery (student_id, subject, n, state, confidence, evidence_id) VALUES
  -- S1 阿鸦：六种 state 各一条，外加两条亮星与一条“历史亮、当前半亮”（n7，历史记录见下）
  ('5a000000-0000-4000-8000-0000000000c1', 'aya', 1, 'unseen',     0.9, '5a000000-0000-4000-8000-0000000000f1'),
  ('5a000000-0000-4000-8000-0000000000c1', 'aya', 2, 'understand', 0.9, '5a000000-0000-4000-8000-0000000000f1'),
  ('5a000000-0000-4000-8000-0000000000c1', 'aya', 3, 'execute',    0.9, '5a000000-0000-4000-8000-0000000000f1'),
  ('5a000000-0000-4000-8000-0000000000c1', 'aya', 4, 'stable',     0.3, '5a000000-0000-4000-8000-0000000000f1'),  -- 低置信：待验证
  ('5a000000-0000-4000-8000-0000000000c1', 'aya', 5, 'transfer',   0.9, '5a000000-0000-4000-8000-0000000000f1'),
  ('5a000000-0000-4000-8000-0000000000c1', 'aya', 6, 'retain',     0.9, '5a000000-0000-4000-8000-0000000000f1'),
  ('5a000000-0000-4000-8000-0000000000c1', 'aya', 7, 'transfer',   0.9, '5a000000-0000-4000-8000-0000000000f1'),  -- 历史，随后被 stable 取代
  ('5a000000-0000-4000-8000-0000000000c1', 'aya', 8, 'transfer',   0.4, '5a000000-0000-4000-8000-0000000000f1'),  -- 低置信：待验证
  -- S2 阿鸦：与 S1 不同的分布，用来发现兄妹之间串号
  ('5a000000-0000-4000-8000-0000000000c2', 'aya', 1, 'retain',     0.9, '5a000000-0000-4000-8000-0000000000f3'),
  ('5a000000-0000-4000-8000-0000000000c2', 'aya', 2, 'transfer',   0.9, '5a000000-0000-4000-8000-0000000000f3'),
  ('5a000000-0000-4000-8000-0000000000c2', 'aya', 3, 'stable',     0.9, '5a000000-0000-4000-8000-0000000000f3'),
  ('5a000000-0000-4000-8000-0000000000c2', 'aya', 4, 'execute',    0.9, '5a000000-0000-4000-8000-0000000000f3'),
  ('5a000000-0000-4000-8000-0000000000c2', 'aya', 5, 'understand', 0.9, '5a000000-0000-4000-8000-0000000000f3'),
  ('5a000000-0000-4000-8000-0000000000c2', 'aya', 6, 'unseen',     0.9, '5a000000-0000-4000-8000-0000000000f3'),
  -- S3（无关家庭）阿鸦
  ('5a000000-0000-4000-8000-0000000000c3', 'aya', 1, 'transfer',   0.9, '5a000000-0000-4000-8000-0000000000f5'),
  ('5a000000-0000-4000-8000-0000000000c3', 'aya', 2, 'transfer',   0.9, '5a000000-0000-4000-8000-0000000000f5'),
  ('5a000000-0000-4000-8000-0000000000c3', 'aya', 3, 'transfer',   0.9, '5a000000-0000-4000-8000-0000000000f5');
UPDATE syn_mastery m SET local_id = a.local_id FROM syn_aya_node a WHERE m.subject = 'aya' AND a.n = m.n;
INSERT INTO syn_mastery (student_id, subject, local_id, state, confidence, evidence_id) VALUES
  -- S1 知行：4 个合成能力点，覆盖未亮、半亮、亮、亮·保持
  ('5a000000-0000-4000-8000-0000000000c1', 'zhixing', 'SYN-ZX-01', 'understand', 0.9, '5a000000-0000-4000-8000-0000000000f2'),
  ('5a000000-0000-4000-8000-0000000000c1', 'zhixing', 'SYN-ZX-02', 'execute',    0.9, '5a000000-0000-4000-8000-0000000000f2'),
  ('5a000000-0000-4000-8000-0000000000c1', 'zhixing', 'SYN-ZX-03', 'transfer',   0.9, '5a000000-0000-4000-8000-0000000000f2'),
  ('5a000000-0000-4000-8000-0000000000c1', 'zhixing', 'SYN-ZX-04', 'retain',     0.9, '5a000000-0000-4000-8000-0000000000f2'),
  -- S2 知行（已暂停，默认不显示）
  ('5a000000-0000-4000-8000-0000000000c2', 'zhixing', 'SYN-ZX-01', 'transfer',   0.9, '5a000000-0000-4000-8000-0000000000f4');
INSERT INTO platform.mastery_estimate (student_id, subject, skill_local_id, state, p_mastery, confidence, algorithm_version,
  evidence_ids, computed_at)
SELECT student_id, subject, local_id, state, NULL, confidence, 'synthetic-star-map@0.1.0', ARRAY[evidence_id],
       '2026-10-15 10:05:00+00'
  FROM syn_mastery;

-- S1 阿鸦 n7：历史是 transfer，更正为当前 stable（显示必须跟随当前记录：半亮）
UPDATE platform.mastery_estimate SET is_current = false, superseded_by = '5a000000-0000-4000-8000-0000000000e7'
 WHERE student_id = '5a000000-0000-4000-8000-0000000000c1' AND subject = 'aya' AND is_current
   AND skill_local_id = (SELECT local_id FROM syn_aya_node WHERE n = 7);
INSERT INTO platform.mastery_estimate (id, student_id, subject, skill_local_id, state, p_mastery, confidence, algorithm_version,
  evidence_ids, computed_at)
SELECT '5a000000-0000-4000-8000-0000000000e7', '5a000000-0000-4000-8000-0000000000c1', 'aya', local_id, 'stable', NULL, 0.9,
       'synthetic-star-map@0.1.0', '{5a000000-0000-4000-8000-0000000000f1}', '2026-10-16 10:05:00+00'
  FROM syn_aya_node WHERE n = 7;

COMMIT;

-- 期望的星图（按 spec 第 2 节映射；阿鸦总数 A = 已登记的阿鸦能力点数，现为 959）：
--   S1 数学（阿鸦）：亮 3（n5、n6 保持、n8 待验证），半亮 3（n3、n4 待验证、n7），未亮 A − 6
--   S1 道法（知行）：亮 2（SYN-ZX-03、SYN-ZX-04 保持），半亮 1，未亮 1
--   S2 数学：亮 2（n1 保持、n2），半亮 2，未亮 A − 4；S2 道法已暂停，默认不显示
--   S3 数学：亮 3，半亮 0，未亮 A − 3
