-- 审核台“编译页面”入口的合成种子（specs/release-gate-sprint4 冲刺 3 段；第二部分 PR A）。只给开发库与一次性测试库用，
-- 不由迁移执行，不能用于 pilot 或生产（CLAUDE.md 规则 8、9）。全部是合成页面 ID、合成提交号与合成 staff，没有任何学生数据。
-- 依赖 db/seeds/dev/review-synthetic.sql（合成教研 T1、T2）与迁移 0038。
--
-- 用法（以迁移执行者身份；只能执行一次；文件自带事务，不要再加 -1）：
--   PGOPTIONS='-c starbuddy.allow_synthetic_seed=on' psql -X -v ON_ERROR_STOP=1 -f db/seeds/dev/review-pages-synthetic.sql
--
-- 内容与 services/learning/tests/fixtures/pages-reconcile/records.json 一致（审核台后端配上同目录的 export.json、staff-map.json 时对账通过）：
--   MATH.SYN.T.A@a1…（高风险，T1、T2）；MATH.SYN.T.B、MATH.SYN.T.C@a2…（中风险，T1、T2）；MATH.SYN.T.E@a4…（待审合并请求，T1）。
BEGIN;

DO $$
BEGIN
  IF coalesce(current_setting('starbuddy.allow_synthetic_seed', true), '') <> 'on' THEN
    RAISE EXCEPTION '合成种子只能用于开发库与一次性测试库：执行方须显式设置 starbuddy.allow_synthetic_seed=on';
  END IF;
  IF current_database() !~ '^starbuddy_(dev|test)(_[a-z0-9_]+)?$' THEN
    RAISE EXCEPTION '合成种子只能用于开发库与测试库（库名 starbuddy_dev* 或 starbuddy_test*），当前库：%', current_database();
  END IF;
  IF NOT EXISTS (SELECT 1 FROM platform.account WHERE id = '5f0e0000-0000-4000-8000-0000000000e1') THEN
    RAISE EXCEPTION '先执行 db/seeds/dev/review-synthetic.sql（合成教研 T1、T2）';
  END IF;
  IF EXISTS (SELECT 1 FROM kge.content_review WHERE object_type = 'page' AND object_id LIKE 'MATH.SYN.T.%') THEN
    RAISE EXCEPTION '编译页面合成种子已经执行过（只能执行一次）';
  END IF;
END $$;

-- 签字身份；app.account_id 只在本事务内有效
SET LOCAL ROLE starbuddy_review;
SELECT set_config('app.account_id', '5f0e0000-0000-4000-8000-0000000000e1', true);
SELECT kge.sign_page_review('MATH.SYN.T.A', 'a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1', 'approved', 'high', 'mr_approved');
SELECT kge.sign_page_review('MATH.SYN.T.B', 'a2a2a2a2a2a2a2a2a2a2a2a2a2a2a2a2a2a2a2a2', 'approved', 'medium', 'mr_approved');
SELECT kge.sign_page_review('MATH.SYN.T.C', 'a2a2a2a2a2a2a2a2a2a2a2a2a2a2a2a2a2a2a2a2', 'approved', 'medium', 'mr_approved');
SELECT kge.sign_page_review('MATH.SYN.T.E', 'a4a4a4a4a4a4a4a4a4a4a4a4a4a4a4a4a4a4a4a4', 'approved', 'high', 'mr_approved');
SELECT set_config('app.account_id', '5f0e0000-0000-4000-8000-0000000000e2', true);
SELECT kge.sign_page_review('MATH.SYN.T.A', 'a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1', 'approved', 'high', 'mr_approved');
SELECT kge.sign_page_review('MATH.SYN.T.B', 'a2a2a2a2a2a2a2a2a2a2a2a2a2a2a2a2a2a2a2a2', 'approved', 'medium', 'mr_approved');
SELECT kge.sign_page_review('MATH.SYN.T.C', 'a2a2a2a2a2a2a2a2a2a2a2a2a2a2a2a2a2a2a2a2', 'approved', 'medium', 'mr_approved');
SELECT set_config('app.account_id', '', true);
RESET ROLE;

COMMIT;
