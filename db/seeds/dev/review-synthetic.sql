-- 审核台原型的合成种子（specs/release-gate-sprint4 冲刺 1 · PR 3）。只给开发库与一次性测试库用，不由迁移执行，
-- 不能用于 pilot 或生产（CLAUDE.md 规则 8、9）。全部是合成的公共层内容与合成 staff 账号，没有任何学生数据。
--
-- 用法（以迁移执行者身份；只能执行一次；文件自带事务，不要再加 -1）：
--   PGOPTIONS='-c starbuddy.allow_synthetic_seed=on' psql -X -v ON_ERROR_STOP=1 -f db/seeds/dev/review-synthetic.sql
--
-- 内容：
--   合成教研 T1、T2（staff，带审核台登录凭据：登录名 teacher.t1 / teacher.t2）；
--   语文（文砺，CHN.RC.*）地图：1.0.0 经 T1、T2 两人签字后由 T1 发布（走签字与发布函数，不直接改状态）；
--   1.0.1 草稿，内容与 1.0.0 相同（端到端测试在它上面改“不包含”、签字、发布）。
BEGIN;

DO $$
BEGIN
  IF coalesce(current_setting('starbuddy.allow_synthetic_seed', true), '') <> 'on' THEN
    RAISE EXCEPTION '合成种子只能用于开发库与一次性测试库：执行方须显式设置 starbuddy.allow_synthetic_seed=on';
  END IF;
  IF current_database() !~ '^starbuddy_(dev|test)(_[a-z0-9_]+)?$' THEN
    RAISE EXCEPTION '合成种子只能用于开发库与测试库（库名 starbuddy_dev* 或 starbuddy_test*），当前库：%', current_database();
  END IF;
  IF EXISTS (SELECT 1 FROM platform.account WHERE id = '5f0e0000-0000-4000-8000-0000000000e1') THEN
    RAISE EXCEPTION '审核台合成种子已经执行过（只能执行一次）';
  END IF;
END $$;

INSERT INTO platform.account (id, kind) VALUES
  ('5f0e0000-0000-4000-8000-0000000000e1', 'staff'),   -- T1
  ('5f0e0000-0000-4000-8000-0000000000e2', 'staff');   -- T2
-- 审核台登录凭据（迁移 0020，PR 4）：合成口令与合成 TOTP 密钥，只用于开发库与一次性测试库，apps/web/e2e/review.spec.ts 用同样的值登录
SELECT platform.staff_enroll('5f0e0000-0000-4000-8000-0000000000e1', 'teacher.t1', 'synthetic-teacher-t1', '\x1111111111111111111111111111111111111111'::bytea);
SELECT platform.staff_enroll('5f0e0000-0000-4000-8000-0000000000e2', 'teacher.t2', 'synthetic-teacher-t2', '\x2222222222222222222222222222222222222222'::bytea);

-- 草稿由知识服务身份写入
SET LOCAL ROLE starbuddy_kge;
CREATE TEMP TABLE _rv(id uuid, version text) ON COMMIT DROP;
INSERT INTO _rv VALUES ('5f0e0000-0000-4000-8000-0000000001a1', '1.0.0'), ('5f0e0000-0000-4000-8000-0000000001a2', '1.0.1');
INSERT INTO kge.map_version (id, subject, standard_year, version) SELECT id, 'wenli', 2022, version FROM _rv WHERE version = '1.0.0';
INSERT INTO kge.knowledge_node (map_version_id, id, level, parent_id, kind, name, includes, excludes, examples, cognitive_levels)
SELECT r.id, n.id, n.level, n.parent_id, n.kind, n.name, n.includes, n.excludes, n.examples, n.cognitive_levels
  FROM _rv r, (VALUES
    ('CHN.RC', 'domain', NULL, NULL, '合成领域：阅读', '{}'::text[], '{}'::text[], '{}'::text[], '{}'::text[]),
    ('CHN.RC.T', 'topic', 'CHN.RC', NULL, '合成主题：记叙文阅读', '{}', '{}', '{}', '{}'),
    ('CHN.RC.T.A', 'node', 'CHN.RC.T', 'kp', '合成节点：概括段意', '{按段落概括}', '{整篇主旨}', '{合成例题 1}', '{understand}'),
    ('CHN.RC.T.B', 'node', 'CHN.RC.T', 'kp', '合成节点：理解词句', '{词语在语境中的含义}', '{古诗文}', '{合成例题 2}', '{understand}')
  ) AS n(id, level, parent_id, kind, name, includes, excludes, examples, cognitive_levels)
 WHERE r.version = '1.0.0';
INSERT INTO kge.knowledge_edge (map_version_id, from_id, to_map_version_id, to_id, type)
SELECT id, 'CHN.RC.T.A', id, 'CHN.RC.T.B', 'prerequisite' FROM _rv WHERE version = '1.0.0';

-- 1.0.0：两名 staff 签字，由 T1 发布（签字身份；app.account_id 只在本事务内有效）
SET LOCAL ROLE starbuddy_review;
SELECT set_config('app.account_id', '5f0e0000-0000-4000-8000-0000000000e1', true);
SELECT kge.sign_review('map_version', '5f0e0000-0000-4000-8000-0000000001a1', 'approved', 'high', 'seed');
SELECT set_config('app.account_id', '5f0e0000-0000-4000-8000-0000000000e2', true);
SELECT kge.sign_review('map_version', '5f0e0000-0000-4000-8000-0000000001a1', 'approved', 'high', 'seed');
SELECT set_config('app.account_id', '5f0e0000-0000-4000-8000-0000000000e1', true);
SELECT kge.publish_map_version('5f0e0000-0000-4000-8000-0000000001a1');
SELECT set_config('app.account_id', '', true);

-- 1.0.1：内容相同的草稿
SET LOCAL ROLE starbuddy_kge;
INSERT INTO kge.map_version (id, subject, standard_year, version) SELECT id, 'wenli', 2022, version FROM _rv WHERE version = '1.0.1';
INSERT INTO kge.knowledge_node (map_version_id, id, level, parent_id, kind, name, includes, excludes, examples, cognitive_levels)
SELECT '5f0e0000-0000-4000-8000-0000000001a2', id, level, parent_id, kind, name, includes, excludes, examples, cognitive_levels
  FROM kge.knowledge_node WHERE map_version_id = '5f0e0000-0000-4000-8000-0000000001a1';
INSERT INTO kge.knowledge_edge (map_version_id, from_id, to_map_version_id, to_id, type)
VALUES ('5f0e0000-0000-4000-8000-0000000001a2', 'CHN.RC.T.A', '5f0e0000-0000-4000-8000-0000000001a2', 'CHN.RC.T.B', 'prerequisite');
RESET ROLE;

COMMIT;
