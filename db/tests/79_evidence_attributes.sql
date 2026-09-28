-- 0017 证据补列（specs/evidence-attributes-sprint1/spec.md）：取值约束、来源类型由载荷类型决定、提示与作答情境一致、地图版本同学科。
-- 合成数据（规则 8）：沿用 10 号夹具的学生 c1、题目 synthetic-0301-07；地图版本用 76 号测试建的 b1（aya）与 b3（wenli）。
RESET ROLE;
-- 以学习服务身份插一条证据；cols / vals 追加列
CREATE FUNCTION t.ev(n integer, kind text, cols text DEFAULT '', vals text DEFAULT '') RETURNS text LANGUAGE sql AS $$
  SELECT format($f$INSERT INTO platform.evidence_event (evidence_id, occurred_at, student_id, subject, kind, item_id, admission, payload,
                   source_method, request_id, request_idempotency_key%s) VALUES
                   ('e1700000-0000-4000-8000-%s', '2026-10-11 12:00:00+00', 'c1000000-0000-4000-8000-0000000000c1', 'aya', %L,
                    'synthetic-0301-07', 'admitted', %L, 'grade', 'f1700000-0000-4000-8000-000000000001', 'synthetic-0017-key-0001'%s)$f$,
                cols, lpad(n::text, 12, '0'), kind,
                CASE kind WHEN 'verifier_trace' THEN '{"sampled_points": 8, "agreeing_points": 8}'
                          WHEN 'interaction_signal' THEN '{"signal_code": "hint_opened", "value": 1}'
                          ELSE '{"text": "（合成）42"}' END, vals) $$;
CREATE FUNCTION t.at(n integer, cols text DEFAULT '', vals text DEFAULT '') RETURNS text LANGUAGE sql AS $$
  SELECT format($f$INSERT INTO platform.attempt (attempt_id, occurred_at, student_id, subject, item_id, response_evidence_id,
                   request_idempotency_key%s) VALUES
                   ('a1700000-0000-4000-8000-%s', '2026-10-11 12:00:00+00', 'c1000000-0000-4000-8000-0000000000c1', 'aya',
                    'synthetic-0301-07', 'e1700000-0000-4000-8000-000000000001', 'synthetic-0017-key-0001'%s)$f$,
                cols, lpad(n::text, 12, '0'), vals) $$;

-- 每种载荷类型都有来源类型（契约审查：新增载荷类型时必须同步 platform.evidence_source_type），取值从 0004 的约束里读
SELECT t.ok(NOT EXISTS (
  SELECT 1 FROM regexp_matches(pg_get_constraintdef((SELECT oid FROM pg_constraint
                 WHERE conrelid = 'platform.evidence_event'::regclass AND contype = 'c' AND pg_get_constraintdef(oid) ~ 'response_text'
                   AND pg_get_constraintdef(oid) ~ 'speech_features' AND pg_get_constraintdef(oid) !~ 'evidence_source_type' LIMIT 1)),
               '''([a-z_]+)''::text', 'g') m
   WHERE platform.evidence_source_type(m[1]) IS NULL),
  '每种载荷类型都能推出来源类型');
SELECT t.ok((SELECT count(*) FROM regexp_matches(pg_get_constraintdef((SELECT oid FROM pg_constraint
                 WHERE conrelid = 'platform.evidence_event'::regclass AND contype = 'c' AND pg_get_constraintdef(oid) ~ 'response_text'
                   AND pg_get_constraintdef(oid) ~ 'speech_features' AND pg_get_constraintdef(oid) !~ 'evidence_source_type' LIMIT 1)),
               '''([a-z_]+)''::text', 'g')) = 8, '对照：从约束里读到 8 种载荷类型');

SET ROLE starbuddy_learning;
-- 来源类型：不给时按载荷类型补齐；给了必须一致
DO $$ BEGIN EXECUTE t.ev(1, 'response_text', ', evidence_kind, evidence_level, channel, map_version_id',
                          ', ''independent'', ''A'', ''keypad'', ''b1000000-0000-4000-8000-0000000000b1'''); END $$;
SELECT t.ok((SELECT source_type FROM platform.evidence_event WHERE evidence_id = 'e1700000-0000-4000-8000-000000000001') = 'objective',
            '作答原文的来源类型自动补为客观记录');
DO $$ BEGIN EXECUTE t.ev(2, 'verifier_trace'); END $$;
SELECT t.ok((SELECT evidence_kind IS NULL AND evidence_level IS NULL AND channel IS NULL
               FROM platform.evidence_event WHERE evidence_id = 'e1700000-0000-4000-8000-000000000002'),
            '补列都可空（v0 路径与旧证据）');
SELECT t.throws(t.ev(3, 'response_text', ', source_type', ', ''model_inference'''), '23514', '来源类型必须与载荷类型一致');
SELECT t.throws(t.ev(4, 'response_text', ', source_type', ', ''guardian_observation'''), '23514',
                '证据表不收家长观察（规则 6：分表存）');
SELECT t.throws(t.ev(5, 'response_text', ', evidence_kind', ', ''retest'''), '23514', '作答情境只有四种取值');
SELECT t.throws(t.ev(6, 'response_text', ', evidence_level', ', ''D'''), '23514', '证据等级只有 A / B / C');
SELECT t.throws(t.ev(7, 'response_text', ', channel', ', ''camera'''), '23514', '输入通道只有四种取值');
SELECT t.throws(t.ev(8, 'interaction_signal', ', evidence_kind', ', ''independent'''), '23514', '交互信号不带作答情境');
SELECT t.throws(t.ev(9, 'response_text', ', map_version_id', ', ''b3000000-0000-4000-8000-0000000000b3'''), '23503',
                '数学证据不能引用语文的地图版本');
SELECT t.throws(t.ev(10, 'response_text', ', map_version_id', ', ''b9000000-0000-4000-8000-0000000000b9'''), '23503',
                '地图版本必须存在');

-- 拍照最高 B；键盘不会有识别转写
SELECT t.throws_like(t.ev(12, 'response_text', ', channel, evidence_level', ', ''photo'', ''A'''), '23514', 'evidence_event_photo_level', '拍照证据不能是 A 级');
DO $$ BEGIN EXECUTE t.ev(13, 'response_text', ', channel, evidence_level', ', ''photo'', ''B'''); END $$;
SELECT t.ok(true, '拍照证据可以是 B 级');
SELECT t.throws_like(t.ev(14, 'ocr_transcription', ', channel', ', ''keypad'''), '23514', 'evidence_event_keypad_kind', '键盘作答不能带识别转写');

-- 作答：来源类型只能是客观记录；用过提示的不能记成独立、迁移或复测；补列必须与作答证据一致
DO $$ BEGIN EXECUTE t.ev(16, 'response_text', ', evidence_kind, evidence_level, channel', ', ''hinted'', ''A'', ''keypad'''); END $$;
DO $$ BEGIN EXECUTE replace(t.at(1, ', hint_level_used, evidence_kind, evidence_level, channel', ', 2, ''hinted'', ''A'', ''keypad'''),
                            'e1700000-0000-4000-8000-000000000001', 'e1700000-0000-4000-8000-000000000016'); END $$;
SELECT t.ok((SELECT source_type FROM platform.attempt WHERE attempt_id = 'a1700000-0000-4000-8000-000000000001') = 'objective',
            '作答的来源类型默认客观记录');
-- 以下每条都让作答与它的证据补列一致，确保拦下它的是被测的那条约束而不是一致性触发器
SELECT t.throws_like(t.at(2, ', hint_level_used, evidence_kind, evidence_level, channel, map_version_id',
                     ', 1, ''independent'', ''A'', ''keypad'', ''b1000000-0000-4000-8000-0000000000b1'''),
                     '23514', 'attempt_hinted_kind', '用过提示的作答不能记成独立');
DO $$ BEGIN EXECUTE t.ev(17, 'response_text', ', evidence_kind, evidence_level, channel', ', ''delayed_retest'', ''A'', ''keypad'''); END $$;
SELECT t.throws_like(replace(t.at(3, ', hint_level_used, evidence_kind, evidence_level, channel', ', 3, ''delayed_retest'', ''A'', ''keypad'''),
                             'e1700000-0000-4000-8000-000000000001', 'e1700000-0000-4000-8000-000000000017'),
                     '23514', 'attempt_hinted_kind', '用过提示的作答不能记成延迟复测');
SELECT t.throws_like(replace(t.at(4, ', source_type', ', ''student_self_report'''),
                             'e1700000-0000-4000-8000-000000000001', 'e1700000-0000-4000-8000-000000000002'),
                     '23514', 'attempt_source_type_check', '作答表不收学生自述');
SELECT t.throws_like(t.at(5, ', evidence_kind, evidence_level, channel, map_version_id',
                ', ''independent'', ''B'', ''keypad'', ''b1000000-0000-4000-8000-0000000000b1'''), '23514', 'differ from its response evidence',
                '作答的证据等级必须与它的作答证据一致');
DO $$ BEGIN EXECUTE t.at(6, ', hint_level_used, evidence_kind, evidence_level, channel, map_version_id',
                          ', 0, ''independent'', ''A'', ''keypad'', ''b1000000-0000-4000-8000-0000000000b1'''); END $$;
SELECT t.ok(true, '与作答证据一致的作答可以写入');
-- 作答的拍照约束是第二道闸：一致性触发器先于 CHECK 执行，而证据本身不能是拍照 A 级，所以这里只断言被拒
SELECT t.throws(t.at(7, ', channel, evidence_level', ', ''photo'', ''A'''), '23514', '拍照作答不能是 A 级');
RESET ROLE;

-- BFF 能读新列（星图等读模块随 RLS 可见）；不能写
SET ROLE starbuddy_bff;
SELECT t.throws(t.ev(11, 'response_text'), '42501', 'BFF 不能写证据');
RESET ROLE;
