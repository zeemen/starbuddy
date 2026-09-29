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
SELECT t.throws($$SELECT kge.sign_map_change('b5000000-0000-4000-8000-0000000000b5', 'approved', kge.map_change_digest_of('b5000000-0000-4000-8000-0000000000b5'))$$, '42501', '知识服务不能签变更单');
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
SELECT t.throws_like(format('SELECT t.rv_publish(%L, %L)', :sf2, :r1), '23514', '两名不同 staff',
                     '首次发布（整张图都是新增，结构性）只有一名签字人：拒绝');
SELECT t.ok(t.rv_sign(:sf2, :r1) IS NOT NULL, '第二名 staff 对当前内容签字（驳回之后的批准才计数）');
SELECT t.ok(t.rv_sign(:sf2, :r1, 'rejected') IS NOT NULL AND t.rv_sign(:sf1, :r1) IS NOT NULL, '两人批准后又被驳回，再由一人批准');
SELECT t.throws_like(format('SELECT t.rv_publish(%L, %L)', :sf1, :r1), '23514', '两名不同 staff', '驳回之前的批准不计数：驳回后只有一人批准时拒绝');
SELECT t.ok(t.rv_sign(:sf2, :r1) IS NOT NULL, '驳回后第二名 staff 再批准');
SELECT t.ok(t.rv_publish(:sf2, :r1) IS NOT NULL, '两人签字后可以发布（发布人可以是签字人之一）');
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
SELECT t.throws($$TRUNCATE platform.content_signoff$$, '42501', '签字人表不能清空');
SELECT t.throws($$TRUNCATE platform.map_change_signoff$$, '42501', '变更单签字人表不能清空');

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
SELECT t.as_staff(:sf1, $$SELECT kge.sign_map_change('db300000-0000-4000-8000-00000000db03', 'approved', kge.map_change_digest_of('db300000-0000-4000-8000-00000000db03'))$$);
SELECT t.as_staff(:sf2, $$SELECT kge.sign_map_change('db300000-0000-4000-8000-00000000db03', 'approved', kge.map_change_digest_of('db300000-0000-4000-8000-00000000db03'))$$);
SELECT t.rv_sign(:sf1, :r3);
SELECT t.throws_like(format('SELECT t.rv_publish(%L, %L)', :sf1, :r3), '23514', '次版本号或主版本号必须增加', '含结构性变更只升修订号：拒绝');

-- 结构性变更单：一人签字不够；同一账号不能签两次；签字后内容改了，旧签字不再计数
SELECT t.ok(t.as_staff(:sf1, $$SELECT kge.sign_map_change('db400000-0000-4000-8000-00000000db04', 'approved', kge.map_change_digest_of('db400000-0000-4000-8000-00000000db04'))$$) = 'proposed', '一人签字后仍待审');
SELECT t.rv_sign(:sf1, :r4);
SELECT t.throws_like(format('SELECT t.rv_publish(%L, %L)', :sf1, :r4), '23514', '变更单未全部批准', '结构性变更单只有一名签字人：拒绝发布');
SELECT t.throws_like(format('SELECT t.as_staff(%L, %L)', :sf1, $$SELECT kge.sign_map_change('db400000-0000-4000-8000-00000000db04', 'approved', kge.map_change_digest_of('db400000-0000-4000-8000-00000000db04'))$$),
                     '23505', '只能签一次', '同一账号不能对同一变更单签两次');
SET ROLE starbuddy_kge;
UPDATE kge.map_change SET payload = '{"node": "CHN.REV.T.A", "note": "改过"}' WHERE id = 'db400000-0000-4000-8000-00000000db04';
RESET ROLE;
SELECT t.ok(t.as_staff(:sf2, $$SELECT kge.sign_map_change('db400000-0000-4000-8000-00000000db04', 'approved', kge.map_change_digest_of('db400000-0000-4000-8000-00000000db04'))$$) = 'proposed',
            '第一个签字后内容改了：旧签字不计数，第二人签字后仍待审');
SELECT t.throws(format($f$SELECT t.as_account(NULL); UPDATE kge.map_change SET status = 'approved' WHERE id = %L$f$, 'db400000-0000-4000-8000-00000000db04'),
                '42501', '超级用户直接改变更单状态也被拒（守卫触发器）');
SELECT t.throws_like(format('SELECT t.as_staff(%L, %L)', :sf1, $$SELECT kge.sign_map_change('db400000-0000-4000-8000-00000000db04', 'approved', repeat('0', 64))$$),
                     '23514', '摘要不符', '变更单签字带的摘要与当前内容不符：拒绝（看到的就是签的）');
SELECT t.ok(kge.map_change_digest_of('db400000-0000-4000-8000-00000000db04') ~ '^[0-9a-f]{64}$', '可以取变更单当前内容的摘要');
SELECT t.throws_like(format($f$SET LOCAL ROLE starbuddy_authz; UPDATE kge.map_change SET status = 'approved' WHERE id = %L$f$, 'db400000-0000-4000-8000-00000000db04'),
                     '23514', '签字人数不够', '以签字函数属主身份直接批准：对当前内容只有一人签字，守卫触发器仍拒绝');
SELECT t.ok(t.as_staff('5c000000-0000-4000-8000-00000000005c', $$SELECT kge.sign_map_change('db400000-0000-4000-8000-00000000db04', 'approved', kge.map_change_digest_of('db400000-0000-4000-8000-00000000db04'))$$) = 'approved',
            '两名不同 staff 对当前内容签字后转为已批准');
SET ROLE starbuddy_kge;
SELECT t.throws_like($$UPDATE kge.map_change SET payload = '{}' WHERE id = 'db400000-0000-4000-8000-00000000db04'$$, '23514', '已有签字', '批准后内容不能再改');
RESET ROLE;
SELECT t.throws_like(format('SELECT t.rv_publish(%L, %L)', :sf2, :r4), '23514', '两名不同 staff',
                     '申报了结构性变更单：版本审核也要两人');
SELECT t.rv_sign(:sf2, :r4);
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

-- 已批准的原子内容不能原地改：要先退回待审再重新审核（安全审查必须改 1）
SET ROLE starbuddy_kge;
SELECT t.throws_like(format($f$UPDATE kge.item_tag SET quality_grade = 'A' WHERE map_version_id = %L$f$, :r4),
                     '23514', '先退回待审', '已批准的题目标签不能原地改内容');
UPDATE kge.item_tag SET quality_grade = 'A', tag_review_status = 'pending' WHERE map_version_id = :r4;
SELECT t.ok((SELECT tag_review_status FROM kge.item_tag WHERE map_version_id = :r4) = 'pending', '改内容并退回待审可以');
-- 时政同一条路径
INSERT INTO kge.current_affair (id, map_version_id, node_ids, source, valid_from, valid_until)
  VALUES ('dc400000-0000-4000-8000-00000000dc04', :r4, '{CHN.REV.T.A}', '（合成）来源', '2027-03-01', '2027-06-01');
SELECT t.throws_like($$UPDATE kge.current_affair SET review_status = 'published' WHERE id = 'dc400000-0000-4000-8000-00000000dc04'$$,
                     '23514', '需要批准的审核记录', '已发布版本上的时政没有审核记录不能上线');
RESET ROLE;
SELECT t.as_staff(:sf1, $$SELECT kge.sign_review('current_affair', 'dc400000-0000-4000-8000-00000000dc04', 'approved', 'medium', 't')::text$$);
SELECT t.as_staff(:sf2, $$SELECT kge.sign_review('current_affair', 'dc400000-0000-4000-8000-00000000dc04', 'rejected', 'medium', 't')::text$$);
SET ROLE starbuddy_kge;
SELECT t.throws_like($$UPDATE kge.current_affair SET review_status = 'published' WHERE id = 'dc400000-0000-4000-8000-00000000dc04'$$,
                     '23514', '需要批准的审核记录', '先批准后驳回：只认最近一条结论，不能上线');
RESET ROLE;
SELECT t.as_staff(:sf1, $$SELECT kge.sign_review('current_affair', 'dc400000-0000-4000-8000-00000000dc04', 'approved', 'medium', 't')::text$$);
SET ROLE starbuddy_kge;
UPDATE kge.current_affair SET review_status = 'published' WHERE id = 'dc400000-0000-4000-8000-00000000dc04';
SELECT t.throws_like($$UPDATE kge.current_affair SET source = '（合成）改过', valid_until = '2099-01-01' WHERE id = 'dc400000-0000-4000-8000-00000000dc04'$$,
                     '23514', '先退回待审', '已上线的时政不能原地改来源与有效期');
-- 题目族、采分点：已发布版本上新增不要求审核（spec 待人确认 #6 默认，这里把现状钉住）
INSERT INTO kge.item_family (map_version_id, main_node_id, code) VALUES (:r4, 'CHN.REV.T.A', 'syn-family-01');
SELECT t.ok(EXISTS (SELECT 1 FROM kge.item_family WHERE map_version_id = :r4), '已发布版本上可以新增题目族（现状，待人确认 #6）');
RESET ROLE;

-- ================= 结构差异：不申报变更单也按差异判定 =================
SET ROLE starbuddy_kge;
SELECT t.rv_version('da500000-0000-4000-8000-00000000da05', '1.1.1');
INSERT INTO kge.knowledge_node (map_version_id, id, level, parent_id, kind, name, includes, excludes, examples, cognitive_levels)
  VALUES ('da500000-0000-4000-8000-00000000da05', 'CHN.REV.T.B', 'node', 'CHN.REV.T', 'kp', '合成', '{a}', '{b}', '{c}', '{understand}');
INSERT INTO kge.textbook_projection (map_version_id, node_id, edition, edition_year, volume, chapter, page_from, page_to)
  VALUES ('da500000-0000-4000-8000-00000000da05', 'CHN.REV.T.B', 'bubian-2024', 2024, 'v', 'c', 2, 2);
SELECT t.rv_version('da600000-0000-4000-8000-00000000da06', '1.2.0');
INSERT INTO kge.knowledge_node (map_version_id, id, level, parent_id, kind, name, includes, excludes, examples, cognitive_levels)
  VALUES ('da600000-0000-4000-8000-00000000da06', 'CHN.REV.T.B', 'node', 'CHN.REV.T', 'kp', '合成', '{a}', '{b}', '{c}', '{understand}');
INSERT INTO kge.textbook_projection (map_version_id, node_id, edition, edition_year, volume, chapter, page_from, page_to, verified_by, verified_at, quote_text, mapping_state)
  VALUES ('da600000-0000-4000-8000-00000000da06', 'CHN.REV.T.B', 'bubian-2024', 2024, 'v', 'c', 2, 2, 'staff-001', '2027-01-01 00:30:00+00', '（合成）原句', 'verified');
INSERT INTO kge.map_change (id, subject, change_type, payload, target_version) VALUES
  ('db600000-0000-4000-8000-00000000db06', 'wenli', 'node_revise', '{"node": "CHN.REV.T.A"}', 'da600000-0000-4000-8000-00000000da06'),
  ('db700000-0000-4000-8000-00000000db07', 'wenli', 'projection_fix', '{"node": "CHN.REV.T.A"}', 'da600000-0000-4000-8000-00000000da06');
RESET ROLE;
SELECT t.ok(kge.structural_diff('da500000-0000-4000-8000-00000000da05'), '新增节点被判为结构性差异');
SELECT t.rv_sign(:sf1, 'da500000-0000-4000-8000-00000000da05');
SELECT t.rv_sign(:sf2, 'da500000-0000-4000-8000-00000000da05');
SELECT t.throws_like(format('SELECT t.rv_publish(%L, %L)', :sf1, 'da500000-0000-4000-8000-00000000da05'), '23514', '次版本号或主版本号必须增加',
                     '不申报变更单、直接加节点：只升修订号仍被拒');
SELECT t.as_staff(:sf1, $$SELECT kge.retire_map_version('da500000-0000-4000-8000-00000000da05', 'wrong_version')::text$$);
-- 非结构性变更单一人即可；被驳回的变更单不阻塞发布
SELECT t.ok(t.as_staff(:sf1, $$SELECT kge.sign_map_change('db600000-0000-4000-8000-00000000db06', 'approved', kge.map_change_digest_of('db600000-0000-4000-8000-00000000db06'))$$) = 'approved', '非结构性变更单一人签字即批准');
SELECT t.ok(t.as_staff(:sf2, $$SELECT kge.sign_map_change('db700000-0000-4000-8000-00000000db07', 'rejected', kge.map_change_digest_of('db700000-0000-4000-8000-00000000db07'))$$) = 'rejected', '驳回变更单');
SET TimeZone = 'UTC';
SELECT t.rv_sign(:sf1, 'da600000-0000-4000-8000-00000000da06');
SELECT t.throws_like(format('SELECT t.rv_publish(%L, %L)', :sf1, 'da600000-0000-4000-8000-00000000da06'), '23514', '两名不同 staff',
                     '不申报变更单、直接加节点：一人签字仍被拒（按差异判定为结构性）');
SELECT t.rv_sign(:sf2, 'da600000-0000-4000-8000-00000000da06');
SET ROLE starbuddy_kge;
UPDATE kge.textbook_projection SET page_to = 3 WHERE map_version_id = 'da600000-0000-4000-8000-00000000da06' AND node_id = 'CHN.REV.T.B';
RESET ROLE;
SELECT t.throws_like(format('SELECT t.rv_publish(%L, %L)', :sf1, 'da600000-0000-4000-8000-00000000da06'), '23514', '摘要不符', '签字后改投影页码：摘要不符');
SET ROLE starbuddy_kge;
UPDATE kge.textbook_projection SET page_to = 2 WHERE map_version_id = 'da600000-0000-4000-8000-00000000da06' AND node_id = 'CHN.REV.T.B';
RESET ROLE;
SET TimeZone = 'Asia/Shanghai';
SELECT t.ok(t.rv_publish(:sf1, 'da600000-0000-4000-8000-00000000da06') IS NOT NULL,
            'UTC 下签字、上海时区下发布：摘要一致（摘要固定按 UTC）；驳回的变更单不阻塞');
SET TimeZone = 'UTC';
SELECT t.ok((SELECT status FROM kge.map_change WHERE id = 'db700000-0000-4000-8000-00000000db07') = 'rejected'
            AND (SELECT status FROM kge.map_change WHERE id = 'db600000-0000-4000-8000-00000000db06') = 'applied', '驳回的保持驳回，批准的转为已应用');

-- ================= 权限矩阵补齐、参数与约束 =================
SELECT t.throws(format('SELECT t.as_staff(%L, %L)', 'a0000000-0000-4000-8000-00000000000a',
                       $$SELECT kge.sign_map_change('db300000-0000-4000-8000-00000000db03', 'approved', kge.map_change_digest_of('db300000-0000-4000-8000-00000000db03'))$$), '42501', '家长不能签变更单');
SELECT t.throws(format('SELECT t.as_staff(%L, %L)', '5d000000-0000-4000-8000-00000000005d',
                       $$SELECT kge.sign_map_change('db300000-0000-4000-8000-00000000db03', 'approved', kge.map_change_digest_of('db300000-0000-4000-8000-00000000db03'))$$), '42501', '停用的 staff 不能签变更单');
SELECT t.throws(format('SELECT t.as_staff(NULL, %L)', $$SELECT kge.sign_map_change('db300000-0000-4000-8000-00000000db03', 'approved', kge.map_change_digest_of('db300000-0000-4000-8000-00000000db03'))$$),
                '42501', '未设账号不能签变更单');
SELECT t.throws(format('SELECT t.as_staff(NULL, %L)', format('SELECT kge.sign_review(''map_version'', %L, ''approved'', ''high'', ''t'')::text', :r3)),
                '42501', '未设账号不能签字');
SELECT t.throws(format('SELECT t.as_staff(%L, %L)', 'a0000000-0000-4000-8000-00000000000a', format('SELECT kge.publish_map_version(%L)::text', :r3)),
                '42501', '家长不能发布');
SELECT t.throws(format('SELECT t.as_staff(%L, %L)', '5d000000-0000-4000-8000-00000000005d', format('SELECT kge.publish_map_version(%L)::text', :r3)),
                '42501', '停用的 staff 不能发布');
SELECT t.throws(format('SELECT t.as_staff(%L, %L)', 'a0000000-0000-4000-8000-00000000000a', format('SELECT kge.retire_map_version(%L, ''t'')::text', :r3)),
                '42501', '家长不能撤下');
SELECT t.throws(format('SELECT t.as_staff(NULL, %L)', format('SELECT kge.retire_map_version(%L, ''t'')::text', :r3)), '42501', '未设账号不能撤下');
SELECT t.throws_like(format('SELECT t.as_staff(%L, %L)', :sf1, format('SELECT kge.sign_review(''map_version'', %L, ''approved'', ''high'', ''t'')::text', :r1)),
                     '23514', '只能对草稿', '不能对已撤下 / 已发布的版本签字');
SELECT t.throws(format('SELECT t.as_staff(%L, %L)', :sf1, format('SELECT kge.sign_review(''map_version'', %L, ''maybe'', ''high'', ''t'')::text', :r3)),
                '22023', '审核结论只能是批准或驳回');
SELECT t.throws_like(format('SELECT t.as_staff(%L, %L)', :sf1, format('SELECT kge.sign_review(''map_version'', %L, ''approved'', ''low'', ''t'')::text', :r3)),
                     '23514', 'content_review_version_risk', '整个地图版本不能按低风险签');
SELECT t.throws(format('SELECT t.as_staff(%L, %L)', :sf1, format('SELECT kge.retire_map_version(%L, NULL)::text', :r3)), '22023', '撤下必须写依据');
SET ROLE starbuddy_kge;
SELECT t.throws(format($f$INSERT INTO kge.content_review (object_type, object_id, risk_level, action) VALUES ('map_version', %L, 'high', 'retire')$f$, :r3),
                '42501', '知识服务不能提交撤下记录');
RESET ROLE;
SELECT t.throws_like($$INSERT INTO kge.content_review (object_type, object_id, risk_level, decision, digest) VALUES ('item_tag', 'x', 'low', 'approved', repeat('a', 64))$$,
                     '23514', 'content_review_decided', '有结论就必须有时间（超级用户也拦）');
-- 内容改过后，原签字人可以对新内容再签
SET ROLE starbuddy_kge;
INSERT INTO kge.map_change (id, subject, change_type, payload) VALUES ('db800000-0000-4000-8000-00000000db08', 'wenli', 'node_add', '{"n": 1}');
RESET ROLE;
SELECT t.as_staff(:sf1, $$SELECT kge.sign_map_change('db800000-0000-4000-8000-00000000db08', 'approved', kge.map_change_digest_of('db800000-0000-4000-8000-00000000db08'))$$);
SET ROLE starbuddy_kge;
UPDATE kge.map_change SET payload = '{"n": 2}' WHERE id = 'db800000-0000-4000-8000-00000000db08';
RESET ROLE;
SELECT t.ok(t.as_staff(:sf1, $$SELECT kge.sign_map_change('db800000-0000-4000-8000-00000000db08', 'approved', kge.map_change_digest_of('db800000-0000-4000-8000-00000000db08'))$$) = 'proposed'
            AND t.as_staff(:sf2, $$SELECT kge.sign_map_change('db800000-0000-4000-8000-00000000db08', 'approved', kge.map_change_digest_of('db800000-0000-4000-8000-00000000db08'))$$) = 'approved',
            '内容改过后原签字人可以对新内容再签；两人对新内容批准后转为已批准');
-- 版本记录：发布、签字、撤下都有签字人
SELECT t.ok((SELECT count(*) FROM kge.version_record('wenli') WHERE action = 'publish') = 3
            AND EXISTS (SELECT 1 FROM kge.version_record('wenli') WHERE action = 'retire' AND basis_code = 'superseded' AND signer IS NOT NULL),
            '版本记录里有每次发布与自动撤下，带签字人');

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
