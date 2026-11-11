-- 冲刺 5-H 跨科周报 v0 的合成种子：只给开发库与一次性测试库用，不由迁移执行，不能用于 pilot 或生产（CLAUDE.md 规则 8、9）。
-- apps/web/scripts/e2e.sh 在一次性测试库里执行它（端到端 trust-on 项目的周报页）。
-- 前提：已执行全部迁移（含 0036）与 db/seeds/dev/shell-synthetic.sql（家庭 HG：家长 gH，孩子 GT 八年级）。
-- 用法（以迁移执行者身份；只能执行一次；文件自带事务）：
--   PGOPTIONS='-c starbuddy.allow_synthetic_seed=on' psql -X -v ON_ERROR_STOP=1 -f db/seeds/dev/weekly-synthetic.sql
--
-- 内容（全部合成，文本是明显占位，不是真实文案）：
--   - 判断模板 syn.weekly.node_mastery@1（已审核，合成占位；以迁移执行者身份模拟人工登记）；
--   - GT 的两条知识节点判断（一条成立、一条假设，对学生、家长可见），以洞察服务身份写入（0010 的守卫照常核对）；
--   - GT 另两条判断（T21，specs/tech-debt-batch2）：周报生成时是成立，之后一条经待验证被推翻（refuted）、一条事实更正后撤回（withdrawn）。
--     周报快照里仍留着这两句：BFF 读周报时逐句在库里再核判断，这两句不得出现在任何页面（e2e/claim-binding.ts 核对）；
--     第五条 f5 生成时成立、之后到期转为待验证：库的 RLS 仍让 BFF 读到待验证的判断，只靠 BFF 的再核（周报只收成立、假设）挡掉它；
--   - GT 上一周（只收已结束的周）一份学生版成长手记、一份家长版周报（以洞察服务身份写入，0036 的守卫逐句核对判断），客观栏来源 objective，
--     计划计数在 self_marked 栏、来源 self（0057 新形状，sie-weekly@0.2.0）；再前一周同样两份旧形状（sie-weekly@0.1.0），BFF 只取最近一周。
-- 端到端的第二个 BFF 用一份把 syn.weekly.node_mastery@1 列进对外白名单的显示规则（scripts/e2e.sh 生成，只在这个一次性环境）。
BEGIN;

DO $$
BEGIN
  IF coalesce(current_setting('starbuddy.allow_synthetic_seed', true), '') <> 'on' THEN
    RAISE EXCEPTION '合成种子只能用于开发库与一次性测试库：执行方须显式设置 starbuddy.allow_synthetic_seed=on';
  END IF;
  IF current_database() !~ '^starbuddy_(dev|test)(_[a-z0-9_]+)?$' THEN
    RAISE EXCEPTION '合成种子只能用于开发库与测试库（库名 starbuddy_dev* 或 starbuddy_test*），当前库：%', current_database();
  END IF;
  IF EXISTS (SELECT 1 FROM insight.claim_template WHERE template_key = 'syn.weekly.node_mastery') THEN
    RAISE EXCEPTION '周报合成种子已经执行过（只能执行一次）';
  END IF;
END $$;

INSERT INTO insight.claim_template (template_key, version, target_type, max_visibility, max_ttl, text_template, status, reviewed_by, reviewed_at)
VALUES ('syn.weekly.node_mastery', 1, 'node', '{agent,student,guardian}', '60 days', '【占位模板·未审核】{node_id}｜{gate}', 'approved',
        'synthetic-seed', now());

SET LOCAL ROLE starbuddy_insight;
INSERT INTO insight.claim (id, student_id, subject, target_type, target_id, template_key, template_version, value, confidence, source_type,
                           status, change_reason, visibility, expires_at) VALUES
  ('5d000000-0000-4000-8000-0000000000f1', '5d000000-0000-4000-8000-0000000000c1', 'aya', 'node', 'MATH.FUNC.QUAD.GRAPH',
   'syn.weekly.node_mastery', 1, '{"gate": "transfer"}', 0.8, 'objective', 'established', 'created', '{agent,student,guardian}', now() + interval '30 days'),
  ('5d000000-0000-4000-8000-0000000000f2', '5d000000-0000-4000-8000-0000000000c1', 'aya', 'node', 'MATH.FUNC.QUAD.AXIS',
   'syn.weekly.node_mastery', 1, '{"gate": "execute"}', 0.5, 'objective', 'hypothesis', 'created', '{agent,student,guardian}', now() + interval '30 days'),
  ('5d000000-0000-4000-8000-0000000000f3', '5d000000-0000-4000-8000-0000000000c1', 'aya', 'node', 'MATH.FUNC.QUAD.ROOTS',
   'syn.weekly.node_mastery', 1, '{"gate": "transfer"}', 0.8, 'objective', 'established', 'created', '{agent,student,guardian}', now() + interval '30 days'),
  ('5d000000-0000-4000-8000-0000000000f4', '5d000000-0000-4000-8000-0000000000c1', 'aya', 'node', 'MATH.FUNC.QUAD.VERTEX',
   'syn.weekly.node_mastery', 1, '{"gate": "execute"}', 0.8, 'objective', 'established', 'created', '{agent,student,guardian}', now() + interval '30 days'),
  ('5d000000-0000-4000-8000-0000000000f5', '5d000000-0000-4000-8000-0000000000c1', 'aya', 'node', 'MATH.FUNC.QUAD.SHIFT',
   'syn.weekly.node_mastery', 1, '{"gate": "transfer"}', 0.8, 'objective', 'established', 'created', '{agent,student,guardian}', now() + interval '30 days'),
  -- f6（specs/why-page-e2e）：不进周报；端到端的假洞察服务对它的 insight.explain 回 503，核对“为什么”页的读取不可用降级
  ('5d000000-0000-4000-8000-0000000000f6', '5d000000-0000-4000-8000-0000000000c1', 'aya', 'node', 'MATH.FUNC.QUAD.DOMAIN',
   'syn.weekly.node_mastery', 1, '{"gate": "execute"}', 0.6, 'objective', 'established', 'created', '{agent,student,guardian}', now() + interval '30 days');
INSERT INTO insight.weekly_report (student_id, week_start, week_end, audience, status, sentences, objective, algorithm_version, policy_version)
SELECT '5d000000-0000-4000-8000-0000000000c1', w, w + 6, a, 'generated',
       jsonb_build_array(
         jsonb_build_object('claim_id', '5d000000-0000-4000-8000-0000000000f1', 'template_key', 'syn.weekly.node_mastery', 'template_version', 1,
                            'section', CASE a WHEN 'student' THEN 'lit' ELSE 'progress' END,
                            'text', '【占位模板·未审核】MATH.FUNC.QUAD.GRAPH｜transfer')
           || CASE a WHEN 'guardian' THEN '{"confidence_band": "strong"}'::jsonb ELSE '{}'::jsonb END,
         jsonb_build_object('claim_id', '5d000000-0000-4000-8000-0000000000f2', 'template_key', 'syn.weekly.node_mastery', 'template_version', 1,
                            'section', CASE a WHEN 'student' THEN 'working_on' ELSE 'stuck' END,
                            'text', '【占位模板·未审核】MATH.FUNC.QUAD.AXIS｜execute', 'label', 'preliminary')
           || CASE a WHEN 'guardian' THEN '{"confidence_band": "moderate"}'::jsonb ELSE '{}'::jsonb END,
         -- T21：生成时成立、之后被推翻 / 撤回的两句（快照里留着，读取时必须被再核挡掉）
         jsonb_build_object('claim_id', '5d000000-0000-4000-8000-0000000000f3', 'template_key', 'syn.weekly.node_mastery', 'template_version', 1,
                            'section', CASE a WHEN 'student' THEN 'lit' ELSE 'progress' END,
                            'text', '【占位模板·未审核】MATH.FUNC.QUAD.ROOTS｜transfer')
           || CASE a WHEN 'guardian' THEN '{"confidence_band": "strong"}'::jsonb ELSE '{}'::jsonb END,
         jsonb_build_object('claim_id', '5d000000-0000-4000-8000-0000000000f4', 'template_key', 'syn.weekly.node_mastery', 'template_version', 1,
                            'section', CASE a WHEN 'student' THEN 'lit' ELSE 'progress' END,
                            'text', '【占位模板·未审核】MATH.FUNC.QUAD.VERTEX｜execute')
           || CASE a WHEN 'guardian' THEN '{"confidence_band": "strong"}'::jsonb ELSE '{}'::jsonb END,
         jsonb_build_object('claim_id', '5d000000-0000-4000-8000-0000000000f5', 'template_key', 'syn.weekly.node_mastery', 'template_version', 1,
                            'section', CASE a WHEN 'student' THEN 'lit' ELSE 'progress' END,
                            'text', '【占位模板·未审核】MATH.FUNC.QUAD.SHIFT｜transfer')
           || CASE a WHEN 'guardian' THEN '{"confidence_band": "strong"}'::jsonb ELSE '{}'::jsonb END),
       x.objective, x.version, 'syn-weekly-seed'
  FROM (SELECT date_trunc('week', (now() AT TIME ZONE 'Asia/Shanghai')::date)::date - 7 AS w,
               -- 上一周：新形状（PLT-15 PR3，0057，sie-weekly@0.2.0）——计划完成 / 跳过在 self_marked 栏、来源 self
               '{"source_type": "objective", "usage_minutes": 95, "subjects": [{"subject": "aya", "practice_minutes": 60, "attempts": 18}],
                 "self_marked": {"source_type": "self", "plan_done": 4, "plan_skipped": 1}}'::jsonb AS objective, 'sie-weekly@0.2.0' AS version
        UNION ALL
        -- 再前一周：旧形状保留（0.1.0 的顶层计划计数；BFF 读时转进 self_marked）
        SELECT date_trunc('week', (now() AT TIME ZONE 'Asia/Shanghai')::date)::date - 14,
               '{"source_type": "objective", "usage_minutes": 95, "plan_done": 4, "plan_skipped": 1,
                 "subjects": [{"subject": "aya", "practice_minutes": 60, "attempts": 18}]}'::jsonb, 'sie-weekly@0.1.0') x,
       unnest(ARRAY['student', 'guardian']) a;
-- T21：周报生成之后，一条判断经待验证被推翻、一条事实更正后撤回（0010 的状态流转照常核对）
UPDATE insight.claim SET status = 'to_verify', change_reason = 'expired' WHERE id = '5d000000-0000-4000-8000-0000000000f3';
UPDATE insight.claim SET status = 'refuted', change_reason = 'verification_refuted' WHERE id = '5d000000-0000-4000-8000-0000000000f3';
UPDATE insight.claim SET status = 'withdrawn', change_reason = 'fact_corrected' WHERE id = '5d000000-0000-4000-8000-0000000000f4';
UPDATE insight.claim SET status = 'to_verify', change_reason = 'expired' WHERE id = '5d000000-0000-4000-8000-0000000000f5';
RESET ROLE;

COMMIT;
