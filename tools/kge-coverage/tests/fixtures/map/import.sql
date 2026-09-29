-- 合成夹具（tools/kge-coverage 单测用）：格式照 KGE-01 导入器产物，节点、名称全部合成，不含任何真实题目或学生数据。
BEGIN;
SET LOCAL standard_conforming_strings = on;
SET LOCAL ROLE starbuddy_kge;
INSERT INTO kge.map_version (id, subject, standard_year, version) VALUES ('00000000-0000-4000-8000-00000000c0de', 'aya', 2022, '9.9.9-syn');
INSERT INTO kge.knowledge_node (map_version_id, id, level, parent_id, kind, name, includes, excludes, examples, cognitive_levels, error_type) VALUES
  ('00000000-0000-4000-8000-00000000c0de', 'MATH.SYN', 'domain', NULL, NULL, '（合成）领域', '{}'::text[], '{}'::text[], '{}'::text[], '{}'::text[], NULL),
  ('00000000-0000-4000-8000-00000000c0de', 'MATH.SYN.SYN_A', 'topic', 'MATH.SYN', NULL, '（合成）主题甲', '{}'::text[], '{}'::text[], '{}'::text[], '{}'::text[], NULL),
  ('00000000-0000-4000-8000-00000000c0de', 'MATH.SYN.SYN_B', 'topic', 'MATH.SYN', NULL, '（合成）主题乙', '{}'::text[], '{}'::text[], '{}'::text[], '{}'::text[], NULL),
  ('00000000-0000-4000-8000-00000000c0de', 'MATH.SYN.SYN_A.K_SYN_0001', 'node', 'MATH.SYN.SYN_A', 'kp', '（合成）节点一', '{"含 a, b"}'::text[], '{}'::text[], '{}'::text[], '{know,understand}'::text[], NULL),
  ('00000000-0000-4000-8000-00000000c0de', 'MATH.SYN.SYN_A.K_SYN_0002', 'node', 'MATH.SYN.SYN_A', 'kp', '（合成）节点"二", 带逗号与''单引号''', '{}'::text[], '{}'::text[], '{}'::text[], '{}'::text[], NULL),
  ('00000000-0000-4000-8000-00000000c0de', 'MATH.SYN.SYN_B.S_SYN_0003', 'node', 'MATH.SYN.SYN_B', 'ability_kp', '（合成）节点三', '{}'::text[], '{}'::text[], '{}'::text[], '{apply}'::text[], NULL),
  ('00000000-0000-4000-8000-00000000c0de', 'MATH.SYN.SYN_B.M_SYN_0004', 'node', 'MATH.SYN.SYN_B', 'method', '（合成）节点四', '{}'::text[], '{}'::text[], '{}'::text[], '{}'::text[], NULL);
COMMIT;
