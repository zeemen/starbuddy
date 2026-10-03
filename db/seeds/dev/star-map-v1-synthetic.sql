-- H5 星图 v1（specs/star-map-v1-h5，开关 starmap_v1 / starmap_dashed）的合成种子：只给开发库与一次性测试库用，不由迁移执行，
-- 不能用于 pilot 或生产（CLAUDE.md 规则 8、9）。apps/web/scripts/e2e.sh 在一次性测试库里执行它（端到端 starmap-on 项目与 v1 视觉回归），
-- 必须在 db/seeds/dev/review-synthetic.sql 之后执行（语文地图由它发布）。
-- 用法（以迁移执行者身份；只能执行一次；文件自带事务）：
--   PGOPTIONS='-c starbuddy.allow_synthetic_seed=on' psql -X -v ON_ERROR_STOP=1 -f db/seeds/dev/star-map-v1-synthetic.sql
--
-- 内容（全部合成：没有姓名、学校；号码用未分配的国家码 +999；文字是明显占位）：
--   - 合成数学（aya）地图 0.9.0：3 个领域 × 4 个主题、119 个知识节点（kp，认知层次 understand），直接写成已发布
--     （做法同 services/bff/tests/star-map-v1.integration.test.ts：以复制模式跳过发布守卫，不经签字流程，
--      审核台的签字与发布记录里不留数学的合成记录；数学的正式首个地图版本仍待人发布）；
--     119 颗：V1 一页 119 颗（不折叠），V2 再加语文 2 颗共 121 颗（按领域折叠，spec #8 的 120 颗上限）；
--   - 判断模板 syn.starmap.node_mastery@1（“知识掌握”）与 syn.starmap.node_transfer@1（“知识迁移”），已审核、合成占位
--     （以迁移执行者身份模拟人工登记；e2e.sh 给 starmap-on 的 BFF 一份把它们列进对外白名单与两维表的显示规则，只在这个一次性环境）；
--   - 家庭 HV：家长 gV（+99900000107），孩子 V1 九年级（在读数学、道法、英语——道法没有已发布地图 →“地图建设中”，英语不出现）、
--     V2 九年级（在读数学、语文）；两人都有合成同意；
--   - V1 的节点键掌握行（以学习服务身份写，认知层次 understand）与判断（以洞察服务身份写，0010 的守卫照常核对）：
--       N001 transfer + 成立的掌握判断       → 亮                N002 retain + 假设的掌握判断        → 亮 + “保持”外圈
--       N003 stable   + 成立的掌握判断       → 半亮              N004 understand + 成立的掌握判断    → 未亮（画了的未亮）
--       N005 transfer，没有判断              → 中性底星          N006 transfer，判断的依据是 N005 的记录 → 中性底星
--       N007 stable   + 待验证的掌握判断     → 虚线开关开：虚线 + 下一次验证 2026-11-03；关：中性底星
--       N008 transfer，只有迁移判断          → 中性底星（迁移只进摘要位）
--       N009 transfer + 只对家长可见的掌握判断 → 学生端中性底星，家长端亮
--     其余节点没有掌握记录 → 中性底星。没有洞察服务，星数说明与迁移摘要不出现（insight.explain 不可用时不显示文字）。
BEGIN;

DO $$
BEGIN
  IF coalesce(current_setting('starbuddy.allow_synthetic_seed', true), '') <> 'on' THEN
    RAISE EXCEPTION '合成种子只能用于开发库与一次性测试库：执行方须显式设置 starbuddy.allow_synthetic_seed=on';
  END IF;
  IF current_database() !~ '^starbuddy_(dev|test)(_[a-z0-9_]+)?$' THEN
    RAISE EXCEPTION '合成种子只能用于开发库与测试库（库名 starbuddy_dev* 或 starbuddy_test*），当前库：%', current_database();
  END IF;
  IF EXISTS (SELECT 1 FROM platform.household WHERE id = '5e000000-0000-4000-8000-0000000000d1') THEN
    RAISE EXCEPTION '星图 v1 合成种子已经执行过（只能执行一次）';
  END IF;
  IF EXISTS (SELECT 1 FROM kge.map_version WHERE subject = 'aya' AND status = 'published') THEN
    RAISE EXCEPTION '库里已有已发布的数学地图：星图 v1 合成种子只用于没有数学地图的开发库与测试库';
  END IF;
END $$;

-- ---------- 合成数学地图 0.9.0（已发布） ----------
SET LOCAL session_replication_role = replica;
INSERT INTO kge.map_version (id, subject, standard_year, version, status, published_at)
VALUES ('5e000000-0000-4000-8000-0000000000e1', 'aya', 2022, '0.9.0', 'published', now());
INSERT INTO kge.knowledge_node (map_version_id, id, level, parent_id, kind, name)
SELECT '5e000000-0000-4000-8000-0000000000e1', 'MATH.SV1' || d, 'domain', NULL, NULL, '（合成）领域 ' || d
  FROM unnest(ARRAY['A', 'B', 'C']) d;
INSERT INTO kge.knowledge_node (map_version_id, id, level, parent_id, kind, name)
SELECT '5e000000-0000-4000-8000-0000000000e1', 'MATH.SV1' || d || '.T' || t, 'topic', 'MATH.SV1' || d, NULL, '（合成）主题 ' || d || t
  FROM unnest(ARRAY['A', 'B', 'C']) d, generate_series(1, 4) t;
INSERT INTO kge.knowledge_node (map_version_id, id, level, parent_id, kind, name, cognitive_levels)
SELECT '5e000000-0000-4000-8000-0000000000e1', p || '.N' || lpad(i::text, 3, '0'), 'node', p, 'kp', '（合成）知识点 ' || lpad(i::text, 3, '0'),
       '{understand}'
  FROM generate_series(1, 119) i,
       LATERAL (SELECT 'MATH.SV1' || (ARRAY['A', 'B', 'C'])[(i - 1) / 40 + 1] || '.T' || (((i - 1) % 40) / 10 + 1) AS p) x;
SET LOCAL session_replication_role = origin;

-- ---------- 判断模板（合成占位，以迁移执行者身份模拟人工登记） ----------
-- 合成占位，永不进正式白名单：syn.* 模板只在一次性测试环境由 e2e.sh 生成的显示规则列入；正式的 services/bff/config/shell.json
-- 不得出现 syn.* 模板（apps/web/tests/star-v1.test.ts 核对）。对外白名单的登记属画像可见范围，必停，由人做。
INSERT INTO insight.claim_template (template_key, version, target_type, max_visibility, max_ttl, text_template, status, reviewed_by, reviewed_at)
VALUES ('syn.starmap.node_mastery', 1, 'node', '{agent,student,guardian}', '60 days', '【占位模板·未审核】{node_id}｜知识掌握', 'approved',
        'synthetic-seed', now()),
       ('syn.starmap.node_transfer', 1, 'node', '{agent,student,guardian}', '60 days', '【占位模板·未审核】{node_id}｜知识迁移', 'approved',
        'synthetic-seed', now());

-- ---------- 家庭 HV ----------
INSERT INTO platform.account (id, kind) VALUES
  ('5e000000-0000-4000-8000-0000000000a1', 'guardian'),
  ('5e000000-0000-4000-8000-0000000000b1', 'student'),
  ('5e000000-0000-4000-8000-0000000000b2', 'student');
INSERT INTO platform.household (id, created_by_account) VALUES
  ('5e000000-0000-4000-8000-0000000000d1', '5e000000-0000-4000-8000-0000000000a1');
INSERT INTO platform.student_profile (id, account_id, household_id, grade, region, textbook_version) VALUES
  ('5e000000-0000-4000-8000-0000000000c1', '5e000000-0000-4000-8000-0000000000b1', '5e000000-0000-4000-8000-0000000000d1', 9, 'nanjing', 'suke-2024'),
  ('5e000000-0000-4000-8000-0000000000c2', '5e000000-0000-4000-8000-0000000000b2', '5e000000-0000-4000-8000-0000000000d1', 9, 'nanjing', 'suke-2024');
INSERT INTO platform.guardian_student_link (guardian_account_id, student_id, granted_via)
SELECT '5e000000-0000-4000-8000-0000000000a1', s, 'household_creator'
  FROM unnest(ARRAY['5e000000-0000-4000-8000-0000000000c1', '5e000000-0000-4000-8000-0000000000c2']::uuid[]) s;
INSERT INTO platform.account_contact (account_id, phone_e164, verified_at) VALUES
  ('5e000000-0000-4000-8000-0000000000a1', '+99900000107', now());
INSERT INTO platform.consent_record (student_id, guardian_account_id, consent_type, document_version, decision)
SELECT s, '5e000000-0000-4000-8000-0000000000a1', t, d, 'granted'
  FROM unnest(ARRAY['5e000000-0000-4000-8000-0000000000c1', '5e000000-0000-4000-8000-0000000000c2']::uuid[]) s,
       (VALUES ('privacy_policy', 'privacy-policy@synthetic-0'), ('guardian_consent_under14', 'guardian-consent@synthetic-0')) c (t, d);
INSERT INTO platform.subject_enrollment (student_id, subject, status) VALUES
  ('5e000000-0000-4000-8000-0000000000c1', 'aya', 'active'),
  ('5e000000-0000-4000-8000-0000000000c1', 'zhixing', 'active'),
  ('5e000000-0000-4000-8000-0000000000c1', 'echo', 'active'),
  ('5e000000-0000-4000-8000-0000000000c2', 'aya', 'active'),
  ('5e000000-0000-4000-8000-0000000000c2', 'wenli', 'active');

-- ---------- V1 的节点键掌握行（以学习服务身份写；ID 固定，判断按它引用依据） ----------
-- evidence_ids 是随机生成、库里不存在的证据 ID：mastery_estimate 与 insight.claim 都只要求至少一个、不设外键，
-- 这里只为让星态与判断成立，不代表任何作答；这些行不经学习服务的掌握计算，不能当作掌握或证据链的样例。
SET LOCAL ROLE starbuddy_learning;
INSERT INTO platform.mastery_estimate (id, student_id, subject, node_id, cognitive_level, map_version_id, state, p_mastery, confidence,
                                       algorithm_version, evidence_ids)
SELECT ('5e000000-0000-4000-8000-0000000001' || lpad(n::text, 2, '0'))::uuid, '5e000000-0000-4000-8000-0000000000c1', 'aya',
       'MATH.SV1A.T1.N' || lpad(n::text, 3, '0'), 'understand', '5e000000-0000-4000-8000-0000000000e1', s::platform.mastery_state,
       0.5, 0.5, 'syn-mastery@0.1.0', ARRAY[gen_random_uuid()]
  FROM (VALUES (1, 'transfer'), (2, 'retain'), (3, 'stable'), (4, 'understand'), (5, 'transfer'), (6, 'transfer'), (7, 'stable'),
               (8, 'transfer'), (9, 'transfer')) v (n, s);
-- N007 的下一次验证任务（平台内验证，不属于复测轮；上海时区 2026-11-03）
INSERT INTO platform.retest_task (student_id, subject, node_id, cognitive_level, reason, due_at)
VALUES ('5e000000-0000-4000-8000-0000000000c1', 'aya', 'MATH.SV1A.T1.N007', 'understand', 'verify_in_platform', '2026-11-03T02:00:00Z');
RESET ROLE;

-- ---------- V1 的判断（以洞察服务身份写；0010 的守卫核对模板、可见范围、有效期与依据归属） ----------
SET LOCAL ROLE starbuddy_insight;
INSERT INTO insight.claim (id, student_id, subject, target_type, target_id, template_key, template_version, value, confidence, source_type,
                           status, change_reason, visibility, expires_at, basis_kind, basis_id, model_version, evidence_ids)
SELECT ('5e000000-0000-4000-8000-0000000002' || lpad(n::text, 2, '0'))::uuid, '5e000000-0000-4000-8000-0000000000c1', 'aya', 'node',
       'MATH.SV1A.T1.N' || lpad(n::text, 3, '0'), k, 1, '{}', 0.7, 'inferred', st::insight.claim_status, 'created', vis,
       now() + interval '30 days', 'mastery_estimate', ('5e000000-0000-4000-8000-0000000001' || lpad(b::text, 2, '0'))::uuid,
       'algo:sie-claims@0.1.0', ARRAY[gen_random_uuid()]
  FROM (VALUES
    (1, 'syn.starmap.node_mastery', 'established', 1, '{agent,student,guardian}'::text[]),
    (2, 'syn.starmap.node_mastery', 'hypothesis', 2, '{agent,student,guardian}'),
    (3, 'syn.starmap.node_mastery', 'established', 3, '{agent,student,guardian}'),
    (4, 'syn.starmap.node_mastery', 'established', 4, '{agent,student,guardian}'),
    (6, 'syn.starmap.node_mastery', 'established', 5, '{agent,student,guardian}'),   -- 依据不是 N006 自己的当前记录
    (7, 'syn.starmap.node_mastery', 'established', 7, '{agent,student,guardian}'),   -- 下面转为待验证
    (8, 'syn.starmap.node_transfer', 'established', 8, '{agent,student,guardian}'),
    (9, 'syn.starmap.node_mastery', 'established', 9, '{agent,guardian}')
  ) v (n, k, st, b, vis);
UPDATE insight.claim SET status = 'to_verify', change_reason = 'expired' WHERE id = '5e000000-0000-4000-8000-000000000207';
RESET ROLE;

COMMIT;
