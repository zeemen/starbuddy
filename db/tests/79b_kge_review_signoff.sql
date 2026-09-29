-- 0018 / 0019 审核台 v0 的签字与发布规则（specs/release-gate-sprint4/spec.md 冲刺 1 验收“库”一节）。
-- 合成数据（规则 8）：语文（CHN）的独立地图版本，不影响 76 号测试的数学版本；staff 账号 sf1（10 号夹具）、sf2（76 号测试新建）。
-- 用 76 号测试定义的 t.as_staff(账号, SQL)：以该账号、在 starbuddy_review 角色下执行。
\set sf1 '''5f000000-0000-4000-8000-00000000005f'''
\set sf2 '''5e000000-0000-4000-8000-00000000005e'''
\set r1 '''da100000-0000-4000-8000-00000000da01'''
\set r2 '''da200000-0000-4000-8000-00000000da02'''
\set r3 '''da300000-0000-4000-8000-00000000da03'''
\set r4 '''da400000-0000-4000-8000-00000000da04'''
RESET ROLE;
SELECT t.as_account(NULL);
INSERT INTO platform.account (id, kind, status) VALUES ('5d000000-0000-4000-8000-00000000005d', 'staff', 'disabled');  -- 已停用的 staff

-- 一个能直接通过发布前校验的语文草稿版本（一个带边界的知识节点 + 一条投影，避免孤点）
CREATE FUNCTION t.rv_version(p_id uuid, p_version text) RETURNS void LANGUAGE plpgsql AS $$
BEGIN
  INSERT INTO kge.map_version (id, subject, standard_year, version) VALUES (p_id, 'wenli', 2022, p_version);
  INSERT INTO kge.knowledge_node (map_version_id, id, level, parent_id, kind, name, includes, excludes, examples, cognitive_levels) VALUES
    (p_id, 'CHN.REV', 'domain', NULL, NULL, '合成', '{}', '{}', '{}', '{}'),
    (p_id, 'CHN.REV.T', 'topic', 'CHN.REV', NULL, '合成', '{}', '{}', '{}', '{}'),
    (p_id, 'CHN.REV.T.A', 'node', 'CHN.REV.T', 'kp', '合成', '{a}', '{b}', '{c}', '{understand}');
  INSERT INTO kge.textbook_projection (map_version_id, node_id, edition, edition_year, volume, chapter, page_from, page_to)
    VALUES (p_id, 'CHN.REV.T.A', 'bubian-2024', 2024, 'v', 'c', 1, 1);
END $$;
GRANT EXECUTE ON FUNCTION t.rv_version(uuid, text) TO PUBLIC;
CREATE FUNCTION t.rv_sign(p_staff uuid, p_version uuid, p_decision text DEFAULT 'approved') RETURNS text LANGUAGE sql AS $$
  SELECT t.as_staff(p_staff, format('SELECT kge.sign_review(''map_version'', %L, %L, ''high'', ''t'')::text', p_version, p_decision)) $$;
CREATE FUNCTION t.rv_publish(p_staff uuid, p_version uuid) RETURNS text LANGUAGE sql AS $$
  SELECT t.as_staff(p_staff, format('SELECT kge.publish_map_version(%L)::text', p_version)) $$;
GRANT EXECUTE ON FUNCTION t.rv_sign(uuid, uuid, text), t.rv_publish(uuid, uuid) TO PUBLIC;

SET ROLE starbuddy_kge;
SELECT t.rv_version(:r1, '1.0.0');
SELECT t.ok(kge.map_version_digest(:r1) ~ '^[0-9a-f]{64}$', '知识服务能算草稿的内容摘要（只返回摘要）');
RESET ROLE;

-- ================= 谁能签字、发布、撤下 =================
SELECT t.throws(format('SELECT t.as_staff(%L, %L)', 'a0000000-0000-4000-8000-00000000000a',
                       format('SELECT kge.sign_review(''map_version'', %L, ''approved'', ''high'', ''t'')::text', :r1)),
                '42501', '家长账号不能签字');
SELECT t.throws(format('SELECT t.as_staff(%L, %L)', '5d000000-0000-4000-8000-00000000005d',
                       format('SELECT kge.sign_review(''map_version'', %L, ''approved'', ''high'', ''t'')::text', :r1)),
                '42501', '已停用的 staff 不能签字');
SELECT t.throws(format('SELECT t.as_staff(NULL, %L)', format('SELECT kge.publish_map_version(%L)::text', :r1)),
                '42501', '没有设当前账号时不能发布');
SELECT t.throws(format('SELECT t.as_staff(%L, %L)', '5d000000-0000-4000-8000-00000000005d',
                       format('SELECT kge.retire_map_version(%L, ''t'')::text', :r1)),
                '42501', '已停用的 staff 不能撤下');
SET ROLE starbuddy_kge;
SELECT t.as_account('5f000000-0000-4000-8000-00000000005f');
SELECT t.throws(format('SELECT kge.sign_review(''map_version'', %L, ''approved'', ''high'', ''t'')', :r1), '42501', '知识服务即使带 staff 账号也不能签字');
SELECT t.throws(format('SELECT kge.publish_map_version(%L)', :r1), '42501', '知识服务不能发布');
SELECT t.throws(format('SELECT kge.retire_map_version(%L, ''t'')', :r1), '42501', '知识服务不能撤下');
SELECT t.throws($$SELECT kge.sign_map_change('b5000000-0000-4000-8000-0000000000b5', 'approved')$$, '42501', '知识服务不能签变更单');
SELECT t.as_account(NULL);
RESET ROLE;
SET ROLE starbuddy_learning;
SELECT t.throws(format('SELECT kge.publish_map_version(%L)', :r1), '42501', '学习服务不能发布');
RESET ROLE;

-- ================= 批准记录与摘要 =================
SELECT t.throws_like(format('SELECT t.rv_publish(%L, %L)', :sf1, :r1), '23514', '没有批准的审核记录', '没有批准记录时拒绝发布');
SELECT t.ok(t.rv_sign(:sf1, :r1) IS NOT NULL, 'staff 可以对草稿签字批准');
SELECT t.ok((SELECT s.account_id FROM kge.content_review r JOIN platform.content_signoff s ON s.content_review_id = r.id
              WHERE r.object_id = :r1) = :sf1::uuid, '签字人是当前登录的 staff 账号，由函数写入');
SET ROLE starbuddy_kge;
UPDATE kge.knowledge_node SET examples = '{c2}' WHERE map_version_id = :r1 AND id = 'CHN.REV.T.A';
RESET ROLE;
SELECT t.throws_like(format('SELECT t.rv_publish(%L, %L)', :sf1, :r1), '23514', '摘要不符', '签字后草稿又被改过：发布因摘要不符被拒');
SELECT t.ok(t.rv_sign(:sf2, :r1, 'rejected') IS NOT NULL, '另一名 staff 可以驳回');
SELECT t.throws_like(format('SELECT t.rv_publish(%L, %L)', :sf1, :r1), '23514', '没有批准的审核记录', '最近一条结论是驳回时拒绝发布');
SELECT t.ok(t.rv_sign(:sf1, :r1) IS NOT NULL, '重新签字批准');
SELECT t.ok(t.rv_publish(:sf2, :r1) IS NOT NULL, '重新签字后可以发布（发布人可以不是签字人）');
SELECT t.ok((SELECT status FROM kge.map_version WHERE id = :r1) = 'published', '发布后状态为已发布');

-- ================= 审核记录只追加 =================
SET ROLE starbuddy_kge;
SELECT t.throws($$UPDATE kge.content_review SET risk_level = 'low'$$, '42501', '知识服务不能改审核记录');
SELECT t.throws($$DELETE FROM kge.content_review$$, '42501', '知识服务不能删审核记录');
SELECT t.throws(format($f$INSERT INTO kge.content_review (object_type, object_id, risk_level, decision, decided_at, digest)
                          VALUES ('map_version', %L, 'high', 'approved', now(), %L)$f$, :r1, repeat('a', 64)),
                '42501', '知识服务不能直接插入批准记录');
RESET ROLE;
SELECT t.throws($$UPDATE kge.content_review SET risk_level = 'low'$$, '42501', '超级用户也不能改审核记录（触发器）');
SELECT t.throws($$DELETE FROM kge.content_review$$, '42501', '超级用户也不能删审核记录');
SELECT t.throws($$TRUNCATE kge.content_review CASCADE$$, '42501', '审核记录不能清空');
SELECT t.throws($$UPDATE platform.content_signoff SET account_id = '5e000000-0000-4000-8000-00000000005e'$$, '42501', '签字人不能改');
SELECT t.throws($$DELETE FROM platform.map_change_signoff$$, '42501', '变更单签字人不能删');

-- ================= 版本号与结构性变更 =================
INSERT INTO platform.account (id, kind) VALUES ('5c000000-0000-4000-8000-00000000005c', 'staff');  -- sf3
SET ROLE starbuddy_kge;
SELECT t.rv_version(:r2, '0.9.0');
SELECT t.rv_version(:r3, '1.0.1');
SELECT t.rv_version(:r4, '1.1.0');
INSERT INTO kge.map_change (id, subject, change_type, payload, target_version) VALUES
  ('db300000-0000-4000-8000-00000000db03', 'wenli', 'node_add', '{"node": "CHN.REV.T.A"}', :r3),
  ('db400000-0000-4000-8000-00000000db04', 'wenli', 'node_add', '{"node": "CHN.REV.T.A"}', :r4);
RESET ROLE;
SELECT t.rv_sign(:sf1, :r2);
SELECT t.throws_like(format('SELECT t.rv_publish(%L, %L)', :sf1, :r2), '23514', '必须大于', '版本号不大于同学科已发布的最大版本：拒绝');
SELECT t.as_staff(:sf1, $$SELECT kge.sign_map_change('db300000-0000-4000-8000-00000000db03', 'approved')$$);
SELECT t.as_staff(:sf2, $$SELECT kge.sign_map_change('db300000-0000-4000-8000-00000000db03', 'approved')$$);
SELECT t.rv_sign(:sf1, :r3);
SELECT t.throws_like(format('SELECT t.rv_publish(%L, %L)', :sf1, :r3), '23514', '次版本号或主版本号必须增加', '含结构性变更只升修订号：拒绝');

-- 结构性变更单：一人签字不够；同一账号不能签两次；签字后内容改了，旧签字不再计数
SELECT t.ok(t.as_staff(:sf1, $$SELECT kge.sign_map_change('db400000-0000-4000-8000-00000000db04', 'approved')$$) = 'proposed', '一人签字后仍待审');
SELECT t.rv_sign(:sf1, :r4);
SELECT t.throws_like(format('SELECT t.rv_publish(%L, %L)', :sf1, :r4), '23514', '变更单未全部批准', '结构性变更单只有一名签字人：拒绝发布');
SELECT t.throws_like(format('SELECT t.as_staff(%L, %L)', :sf1, $$SELECT kge.sign_map_change('db400000-0000-4000-8000-00000000db04', 'approved')$$),
                     '23505', '只能签一次', '同一账号不能对同一变更单签两次');
SET ROLE starbuddy_kge;
UPDATE kge.map_change SET payload = '{"node": "CHN.REV.T.A", "note": "改过"}' WHERE id = 'db400000-0000-4000-8000-00000000db04';
RESET ROLE;
SELECT t.ok(t.as_staff(:sf2, $$SELECT kge.sign_map_change('db400000-0000-4000-8000-00000000db04', 'approved')$$) = 'proposed',
            '第一个签字后内容改了：旧签字不计数，第二人签字后仍待审');
SELECT t.throws(format($f$SELECT t.as_account(NULL); UPDATE kge.map_change SET status = 'approved' WHERE id = %L$f$, 'db400000-0000-4000-8000-00000000db04'),
                '42501', '超级用户直接改变更单状态也被拒（守卫触发器）');
SELECT t.ok(t.as_staff('5c000000-0000-4000-8000-00000000005c', $$SELECT kge.sign_map_change('db400000-0000-4000-8000-00000000db04', 'approved')$$) = 'approved',
            '两名不同 staff 对当前内容签字后转为已批准');
SET ROLE starbuddy_kge;
SELECT t.throws_like($$UPDATE kge.map_change SET payload = '{}' WHERE id = 'db400000-0000-4000-8000-00000000db04'$$, '23514', '已有签字', '批准后内容不能再改');
RESET ROLE;
SELECT t.ok(t.rv_publish(:sf2, :r4) IS NOT NULL, '签字齐全、版本号升次版本：可以发布');
SELECT t.ok((SELECT status FROM kge.map_version WHERE id = :r1) = 'retired' AND (SELECT status FROM kge.map_change WHERE id = 'db400000-0000-4000-8000-00000000db04') = 'applied',
            '发布后原版本撤下、变更单转为已应用');

-- ================= 已发布版本上新增原子：批准前要有批准记录（摘要一致） =================
SET ROLE starbuddy_kge;
SELECT t.throws_like(format($f$INSERT INTO kge.item_tag (map_version_id, subject, item_id, main_node_id, source_kind, tag_review_status)
                               VALUES (%L, 'wenli', 'synthetic-essay-01', 'CHN.REV.T.A', 'original', 'approved')$f$, :r4),
                     '23514', '需要批准的审核记录', '已发布版本上直接插入已批准的题目标签：拒绝');
INSERT INTO kge.item_tag (map_version_id, subject, item_id, main_node_id, source_kind) VALUES (:r4, 'wenli', 'synthetic-essay-01', 'CHN.REV.T.A', 'original');
SELECT t.throws_like(format($f$UPDATE kge.item_tag SET tag_review_status = 'approved' WHERE map_version_id = %L$f$, :r4),
                     '23514', '需要批准的审核记录', '没有审核记录时不能改为已批准');
RESET ROLE;
SELECT t.as_staff(:sf1, format('SELECT kge.sign_review(''item_tag'', %L, ''approved'', ''low'', ''t'')::text', :r4 || ':wenli:synthetic-essay-01'));
SET ROLE starbuddy_kge;
UPDATE kge.item_tag SET prior_difficulty = 0.4 WHERE map_version_id = :r4;
SELECT t.throws_like(format($f$UPDATE kge.item_tag SET tag_review_status = 'approved' WHERE map_version_id = %L$f$, :r4),
                     '23514', '需要批准的审核记录', '签字后标签又改过：摘要不符，不能批准');
RESET ROLE;
SELECT t.as_staff(:sf1, format('SELECT kge.sign_review(''item_tag'', %L, ''approved'', ''low'', ''t'')::text', :r4 || ':wenli:synthetic-essay-01'));
SET ROLE starbuddy_kge;
UPDATE kge.item_tag SET tag_review_status = 'approved' WHERE map_version_id = :r4;
SELECT t.ok((SELECT tag_review_status FROM kge.item_tag WHERE map_version_id = :r4) = 'approved', '重新签字后可以批准');
RESET ROLE;

-- ================= 撤下 =================
SELECT t.as_staff(:sf1, format('SELECT kge.retire_map_version(%L, ''superseded'')::text', :r2));
SELECT t.ok((SELECT status FROM kge.map_version WHERE id = :r2) = 'retired'
            AND EXISTS (SELECT 1 FROM kge.content_review r JOIN platform.content_signoff s ON s.content_review_id = r.id
                         WHERE r.object_id = :r2 AND r.action = 'retire' AND s.account_id = :sf1::uuid),
            '经函数撤下，撤下记录带签字人');
SELECT t.throws_like(format('SELECT t.as_staff(%L, %L)', :sf1, format('SELECT kge.retire_map_version(%L, ''again'')::text', :r2)),
                     '23514', '只能撤下草稿或已发布', '已撤下的版本不能再撤下');

-- ================= 读取范围 =================
SET ROLE starbuddy_bff;
SELECT t.throws($$SELECT count(*) FROM platform.content_signoff$$, '42501', 'BFF 读不到审核签字人');
SELECT t.throws($$SELECT count(*) FROM platform.map_change_signoff$$, '42501', 'BFF 读不到变更单签字人');
RESET ROLE;
SET ROLE starbuddy_review;
SELECT t.throws($$SELECT count(*) FROM kge.content_review$$, '42501', '签字身份不能直接读写审核记录，只能经函数');
SELECT t.throws($$SELECT count(*) FROM platform.content_signoff$$, '42501', '签字身份不能直接读签字人表');
RESET ROLE;
