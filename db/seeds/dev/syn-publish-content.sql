-- 合成内容版本的发布辅助（4-G 迁移 0039 起：内容版本只能先登记草稿、再经签字函数发布）。只给开发库与一次性测试库用，
-- 不由迁移执行，不能用于 pilot 或生产（CLAUDE.md 规则 8、9）。
-- 只定义一个会话级函数 pg_temp.syn_publish_content(内容版本 ID)，不写任何数据；必须与调用它的种子或语句在同一个会话里执行：
--   psql -X -v ON_ERROR_STOP=1 -f db/seeds/dev/syn-publish-content.sql -f db/seeds/dev/star-map-synthetic.sql
--   （BFF 集成测试、e2e-cohort 联调栈在同一条连接上先执行本文件，再执行种子或调用）
-- 函数做的事（以迁移执行者身份调用，内部切换到签字身份 starbuddy_review）：
--   1. 两名合成教研（固定 ID，见下）置为有效；
--   2. 有知识地图骨架的学科（数学、道法）且内容版本没指地图版本时：库里有已发布地图就用它；没有就建一个空的合成占位地图
--      （ID 由内容版本 ID 确定、版本号取该学科已有最大版本的下一个次版本），两名合成教研签字发布；
--   3. 一名合成教研对内容版本签字，再发布；
--   4. 建了占位地图的，内容版本发布后立即撤下占位地图（库里不留已发布的合成地图）；
--   5. 两名合成教研置为停用（不留可用的合成签字账号）。
-- 副作用：被发布的学科在该库里“已上线”（选科不再返回 not_launched），BFF 能读到这些版本下的能力视图。
CREATE OR REPLACE FUNCTION pg_temp.syn_publish_content(p_cv uuid) RETURNS void LANGUAGE plpgsql AS $syn$
DECLARE
  s1 constant uuid := '5f5e0000-0000-4000-8000-0000000005f1';
  s2 constant uuid := '5f5e0000-0000-4000-8000-0000000005f2';
  c platform.curriculum_version;
  v_map uuid;
  v_ver integer[];
  v_placeholder boolean := false;
BEGIN
  IF coalesce(current_setting('starbuddy.allow_synthetic_seed', true), '') <> 'on'
     AND current_database() !~ '^starbuddy_test(_[a-z0-9_]+)?$' THEN
    RAISE EXCEPTION '合成发布只能用于一次性测试库，或显式设置 starbuddy.allow_synthetic_seed=on 的开发库';
  END IF;
  IF current_database() !~ '^starbuddy_(dev|test)(_[a-z0-9_]+)?$' THEN
    RAISE EXCEPTION '合成发布只能用于开发库与测试库（库名 starbuddy_dev* 或 starbuddy_test*），当前库：%', current_database();
  END IF;
  SELECT * INTO c FROM platform.curriculum_version WHERE id = p_cv;
  IF NOT FOUND OR c.status <> 'draft' THEN RETURN; END IF;
  INSERT INTO platform.account (id, kind) VALUES (s1, 'staff'), (s2, 'staff')
  ON CONFLICT (id) DO UPDATE SET status = 'active';
  IF c.map_version_id IS NULL AND platform.content_requires_map(c.subject) THEN
    SELECT id INTO v_map FROM kge.map_version WHERE subject = c.subject AND status = 'published';
    IF v_map IS NULL THEN
      SELECT max(string_to_array(version, '.')::integer[]) INTO v_ver FROM kge.map_version WHERE subject = c.subject;
      v_map := md5('syn-placeholder-map:' || p_cv::text)::uuid;
      v_placeholder := true;
      INSERT INTO kge.map_version (id, subject, standard_year, version)
      VALUES (v_map, c.subject, 2022, coalesce(v_ver[1], 0) || '.' || (coalesce(v_ver[2], 0) + 1) || '.0');
      SET LOCAL ROLE starbuddy_review;
      PERFORM set_config('app.account_id', s2::text, true);
      PERFORM kge.sign_review('map_version', v_map::text, 'approved', 'high', 'synthetic_seed');
      PERFORM set_config('app.account_id', s1::text, true);
      PERFORM kge.sign_review('map_version', v_map::text, 'approved', 'high', 'synthetic_seed');
      PERFORM kge.publish_map_version(v_map);
      RESET ROLE;
    END IF;
    UPDATE platform.curriculum_version SET map_version_id = v_map WHERE id = p_cv;
  END IF;
  SET LOCAL ROLE starbuddy_review;
  PERFORM set_config('app.account_id', s1::text, true);
  PERFORM platform.sign_content_version(p_cv, 'approved', platform.content_version_digest(p_cv), 'synthetic_seed');
  PERFORM platform.publish_content_version(p_cv);
  IF v_placeholder THEN
    PERFORM kge.retire_map_version(v_map, 'synthetic_seed');
  END IF;
  PERFORM set_config('app.account_id', '', true);
  RESET ROLE;
  UPDATE platform.account SET status = 'disabled' WHERE id IN (s1, s2);
END $syn$;
