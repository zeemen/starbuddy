-- 0042 审核台读内容版本与发布记录、0043 时政过期下架（specs/release-gate-sprint4/spec.md 第 7、9、11 节；第二部分 PR D）。
-- 合成数据（规则 8）：合成语文内容版本（教材编码 syn-rcr，地区 synth-rcr）、合成能力点与题目；合成时政挂在 76 号的已发布数学地图 kb 上；
-- staff 账号 sf1（10 号夹具）、sf2（79u 用的合成 staff）。用 76 号的 t.as_staff(账号, SQL)、79u 号的 t.cr_sign / cr_publish / cr_retire / cr_adm / cr_apply。
\set sf1 '''5f000000-0000-4000-8000-00000000005f'''
\set sf2 '''f5000000-0000-4000-8000-0000000000f5'''
\set w1 '''e7b00000-0000-4000-8000-0000000000d1'''
\set w2 '''e7b00000-0000-4000-8000-0000000000d2'''
\set kb '''ca200000-0000-4000-8000-00000000ca02'''
\set ca1 '''e7b00000-0000-4000-8000-0000000000e1'''
\set ca2 '''e7b00000-0000-4000-8000-0000000000e2'''
\set ca3 '''e7b00000-0000-4000-8000-0000000000e3'''
\set ca4 '''e7b00000-0000-4000-8000-0000000000e4'''
RESET ROLE;
SELECT t.as_account(NULL);

-- ---------- 夹具：合成语文内容版本 1.0.0（发布）与 1.1.0（草稿） ----------
SET ROLE starbuddy_learning;
INSERT INTO platform.curriculum_version (id, region, exam_year, grade, textbook_version, subject, content_version)
VALUES (:w1, 'synth-rcr', 2031, 8, 'syn-rcr', 'wenli', '1.0.0'), (:w2, 'synth-rcr', 2031, 8, 'syn-rcr', 'wenli', '1.1.0');
INSERT INTO platform.skill_node (subject, local_id, curriculum_version_id, label_code) VALUES
  ('wenli', 'SYN-RCR-01', :w1, 'syn-rcr-01'), ('wenli', 'SYN-RCR-11', :w2, 'syn-rcr-11');
INSERT INTO platform.item (subject, item_id, curriculum_version_id, skill_local_id, admission, source_ref, source_type) VALUES
  ('wenli', 'SYN-RCR-I1', :w1, 'SYN-RCR-01', 'pending_review', 'SYN-SRC-RCR-1', 'textbook'),
  ('wenli', 'SYN-RCR-I11', :w2, 'SYN-RCR-11', 'source_unverified', NULL, 'unknown');
RESET ROLE;
SELECT t.cr_sign(:sf1, :w1);
SELECT t.cr_publish(:sf1, :w1);
SELECT t.cr_adm(:sf2, 'wenli', 'SYN-RCR-I1', 'admitted', 'Q3', 'SYN-SRC-RCR-1', 'textbook', 'cleared') AS rcr_adm \gset
SELECT t.cr_apply(:sf1, ARRAY[:'rcr_adm']::uuid[]);

-- ================= 一、谁能读 =================
SET ROLE starbuddy_learning;
SELECT t.throws($$SELECT * FROM platform.content_version_list('wenli')$$, '42501', '审核台读：学习服务不能调内容版本列表');
SELECT t.throws(format('SELECT platform.content_version_snapshot(%L)', :w1), '42501', '审核台读：学习服务不能调内容版本快照');
SELECT t.throws($$SELECT * FROM platform.release_record_export('wenli')$$, '42501', '审核台读：学习服务不能读发布记录导出');
RESET ROLE;
SET ROLE starbuddy_bff;
SELECT t.throws($$SELECT * FROM platform.content_version_list('wenli')$$, '42501', '审核台读：BFF 不能调内容版本列表');
SELECT t.throws(format('SELECT platform.content_version_snapshot(%L)', :w1), '42501', '审核台读：BFF 不能调内容版本快照');
SELECT t.throws($$SELECT * FROM platform.release_record_export('wenli')$$, '42501', '审核台读：BFF 不能读发布记录导出');
RESET ROLE;
SET ROLE starbuddy_insight;
SELECT t.throws($$SELECT * FROM platform.release_record_export('wenli')$$, '42501', '审核台读：洞察服务不能读发布记录导出');
RESET ROLE;
SET ROLE starbuddy_kge_read;
SELECT t.throws($$SELECT * FROM platform.release_record_export('wenli')$$, '42501', '审核台读：知识服务只读角色不能读发布记录导出');
RESET ROLE;
-- 只有审核台后端的读身份（kge）能读；签字身份不授（最小授权）；表本身仍然读不到（不放宽表授权与 RLS）
SET ROLE starbuddy_kge;
SELECT t.ok((SELECT count(*) FROM platform.content_version_list('wenli') WHERE region = 'synth-rcr') = 2,
            '审核台读：知识服务身份（审核台后端）能列内容版本');
SELECT t.ok((SELECT count(*) FROM platform.release_record_export('wenli') WHERE region = 'synth-rcr') = 4,
            '审核台读：知识服务身份能读发布记录导出（签字、发布、准入审核、准入执行）');
SELECT t.throws('SELECT count(*) FROM platform.curriculum_version', '42501', '审核台读：知识服务身份仍然不能直接读内容版本表');
SELECT t.throws('SELECT count(*) FROM platform.release_record', '42501', '审核台读：知识服务身份仍然不能直接读发布记录表');
RESET ROLE;
SET ROLE starbuddy_review;
SELECT t.throws($$SELECT * FROM platform.content_version_list('wenli')$$, '42501', '审核台读：签字身份不能调内容版本列表');
SELECT t.throws(format('SELECT platform.content_version_snapshot(%L)', :w1), '42501', '审核台读：签字身份不能调内容版本快照');
SELECT t.throws($$SELECT * FROM platform.release_record_export('wenli')$$, '42501', '审核台读：签字身份不能读发布记录导出');
SELECT t.throws('SELECT count(*) FROM platform.item', '42501', '审核台读：签字身份仍然不能直接读题目表');
RESET ROLE;

-- ================= 二、内容 =================
SET ROLE starbuddy_kge;
SELECT t.ok((SELECT row(status, content_version, skill_nodes, items)::text FROM platform.content_version_list('wenli') WHERE id = :w1)
            = '(published,1.0.0,1,1)', '审核台读：列表带状态、版本号与登记计数');
SELECT t.ok((SELECT array_agg(content_version ORDER BY content_version) FROM platform.content_version_list('wenli') WHERE region = 'synth-rcr')
            = '{1.0.0,1.1.0}', '审核台读：列表只含该学科');
SELECT t.ok((SELECT count(*) FROM platform.content_version_list('aya') WHERE region = 'synth-rcr') = 0, '审核台读：别的学科列不出语文版本');
SELECT t.ok((SELECT count(*) FROM platform.release_record_export('aya') WHERE region = 'synth-rcr') = 0
            AND (SELECT bool_and(subject_ok) FROM (SELECT v.subject = 'wenli' AS subject_ok FROM platform.release_record_export('wenli') x
                                                     JOIN platform.content_version_list('wenli') v ON v.id = x.curriculum_version_id) s),
            '审核台读：发布记录导出只含该学科');
SELECT t.ok(platform.content_version_snapshot(:w2) -> 'version' ->> 'status' = 'draft'
            AND jsonb_array_length(platform.content_version_snapshot(:w2) -> 'items') = 1
            AND platform.content_version_snapshot(:w2) -> 'items' -> 0 ->> 'item_id' = 'SYN-RCR-I11',
            '审核台读：快照带版本与题目登记');
SELECT t.ok(platform.content_version_snapshot(gen_random_uuid()) IS NULL, '审核台读：不存在的内容版本快照为空');
SELECT t.ok((SELECT row(action, decision, to_admission, review_level)::text FROM platform.release_record_export('wenli')
              WHERE region = 'synth-rcr' AND line = 'admission' AND action = 'apply') = '(apply,approved,admitted,Q3)',
            '审核台读：导出带准入执行与审核等级');
SELECT t.ok((SELECT bool_and(signer IS NOT NULL) FROM platform.release_record_export('wenli') WHERE region = 'synth-rcr'),
            '审核台读：导出带签字人');
RESET ROLE;
-- 快照里的摘要就是签字与发布用的摘要（0039 的两个摘要函数；kge 调不了摘要函数、review 调不了快照，以超级用户比对）
SELECT t.ok((SELECT platform.content_version_snapshot(:w2) ->> 'digest') = platform.content_version_digest(:w2),
            '审核台读：快照的摘要与 content_version_digest 相同（签字回传即此值）');
SELECT t.ok((SELECT platform.content_version_snapshot(:w1) -> 'items' -> 0 ->> 'admission_digest') = platform.item_admission_digest('wenli', 'SYN-RCR-I1'),
            '审核台读：快照的准入摘要与 item_admission_digest 相同');
RESET ROLE;

-- ================= 三、时政过期下架（0043） =================
-- 夹具：已发布地图 kb 上直接写已上线的合成时政（以超级用户临时关掉触发器，同 79h）
BEGIN;
SET LOCAL session_replication_role = replica;
INSERT INTO kge.current_affair (id, map_version_id, node_ids, source, valid_from, valid_until, review_status) VALUES
  (:ca1, :kb, '{MATH.ALG.T.X}', '合成来源：已过期（下架）', '2019-01-01', '2019-03-01', 'published'),
  (:ca2, :kb, '{MATH.ALG.T.X}', '合成来源：当天到期（不下架）', '2019-01-01', '2019-06-01', 'published'),
  (:ca3, :kb, '{MATH.ALG.T.X}', '合成来源：仍有效', '2019-01-01', '2100-01-01', 'published'),
  (:ca4, :kb, '{MATH.ALG.T.X}', '合成来源：已过期但待审', '2019-01-01', '2019-02-01', 'pending');
COMMIT;
-- ca1 上线前有一条真实的批准审核（摘要与当前内容一致）：没有“下架不可逆”守卫时，下架后写入方能凭它把 ca1 改回 published
SELECT t.as_staff(:sf1, format($$SELECT kge.sign_review('current_affair', %L, 'approved', 'medium', 't')::text$$, :ca1));
SET ROLE starbuddy_review;
SELECT t.throws($$SELECT kge.expire_current_affairs('2019-06-01')$$, '42501', '时政下架：签字身份不能调下架函数');
RESET ROLE;
SET ROLE starbuddy_learning;
SELECT t.throws($$SELECT kge.expire_current_affairs('2019-06-01')$$, '42501', '时政下架：学习服务不能调下架函数');
RESET ROLE;
SET ROLE starbuddy_bff;
SELECT t.throws($$SELECT kge.expire_current_affairs('2019-06-01')$$, '42501', '时政下架：BFF 不能调下架函数');
RESET ROLE;
SET ROLE starbuddy_kge;
SELECT t.throws_like('SELECT kge.expire_current_affairs(NULL)', '22023', '当天日期', '时政下架：当天日期不能为空');
SELECT t.throws_like($$SELECT kge.expire_current_affairs(((now() AT TIME ZONE 'Asia/Shanghai')::date + 1))$$, '22023', '不能提前下架',
                     '时政下架：当天日期不能晚于北京时间的今天');
SELECT t.ok(kge.expire_current_affairs('2019-06-01') = 1, '时政下架：只下架 valid_until 早于当天且已上线的一条');
SELECT t.ok((SELECT string_agg(review_status, ',' ORDER BY id) FROM kge.current_affair WHERE id IN (:ca1, :ca2, :ca3, :ca4))
            = 'retired,published,published,pending', '时政下架：当天到期、仍有效、待审的都不受影响');
SELECT t.ok((SELECT row(risk_level, decision, action, basis_code, digest ~ '^[0-9a-f]{64}$', decided_at IS NOT NULL)::text
               FROM kge.content_review WHERE object_type = 'current_affair' AND object_id = :ca1::text AND action = 'retire')
            = '(low,approved,retire,expired_auto,t,t)', '时政下架：追加一条系统动作审核记录（低风险、依据 expired_auto、带摘要）');
SELECT t.ok(kge.expire_current_affairs('2019-06-01') = 0, '时政下架：同一天重复执行不重复下架');
SELECT t.ok((SELECT count(*) FROM kge.content_review WHERE object_type = 'current_affair' AND object_id = :ca1::text AND action = 'retire') = 1,
            '时政下架：同一天重复执行不重复记录');
-- 下架不可逆（契约审查）：下架前的批准仍是最近一条 review 结论，但写入方不能凭它把时政改回上线，也不能退回待审再上线
SELECT t.throws_like(format($$UPDATE kge.current_affair SET review_status = 'published' WHERE id = %L$$, :ca1), '23514', '已下架',
                     '时政下架：已下架的时政不能改回 published');
SELECT t.throws_like(format($$UPDATE kge.current_affair SET review_status = 'pending' WHERE id = %L$$, :ca1), '23514', '已下架',
                     '时政下架：已下架的时政不能退回待审');
SELECT t.ok((SELECT review_status FROM kge.current_affair WHERE id = :ca1) = 'retired', '时政下架：改回失败后仍为 retired');
-- 写入方仍然不能自己插带结论的审核记录（下架记录只能经函数）
SELECT t.throws($$INSERT INTO kge.content_review (object_type, object_id, risk_level, decision, decided_at, basis_code, action)
                  VALUES ('current_affair', 'e7b00000-0000-4000-8000-0000000000e3', 'low', 'approved', now(), 'expired_auto', 'retire')$$,
                '42501', '时政下架：知识服务不能直接插入“过期下架”记录（为未过期的时政伪造）');
RESET ROLE;
SELECT t.ok(NOT EXISTS (SELECT 1 FROM kge.content_review r JOIN platform.content_signoff s ON s.content_review_id = r.id
                         WHERE r.object_type = 'current_affair' AND r.object_id = :ca1::text AND r.action = 'retire'),
            '时政下架：系统动作不写签字人（审核人为空）');
-- 同一条语句里函数刚做的修改对子查询不可见，分两条语句断言
SELECT t.ok(kge.expire_current_affairs('2019-06-02') = 1, '时政下架：第二天再跑下架一条');
SELECT t.ok((SELECT review_status FROM kge.current_affair WHERE id = :ca2) = 'retired', '时政下架：第二天当天到期的那条下架');
