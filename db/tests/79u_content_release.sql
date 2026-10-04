-- 0039 内容版本与题目准入（specs/release-gate-sprint4/spec.md 冲刺 4 验收“内容版本与准入（DB 测试）”）。
-- 合成数据（规则 8）：合成道法内容版本（教材编码 syn-gate）、合成能力点与题目、空的合成道法地图；
-- staff 账号 sf1（10 号夹具）、sf2（10 号夹具）、已停用的 staff（79b）；家长 gA（10 号夹具）；选科用的合成家庭在本文件新建。
-- 用 76 号测试定义的 t.as_staff(账号, SQL)、78 号的 t.call(账号, SQL)。
\set sf1 '''5f000000-0000-4000-8000-00000000005f'''
\set sf2 '''f5000000-0000-4000-8000-0000000000f5'''
\set gA '''a0000000-0000-4000-8000-00000000000a'''
\set zm '''c7a00000-0000-4000-8000-0000000000a1'''
\set zd '''c7a00000-0000-4000-8000-0000000000a2'''
\set zr '''c7a00000-0000-4000-8000-0000000000a3'''
\set c1 '''e7a00000-0000-4000-8000-0000000000b1'''
\set c2 '''e7a00000-0000-4000-8000-0000000000b2'''
\set c3 '''e7a00000-0000-4000-8000-0000000000b3'''
\set c4 '''e7a00000-0000-4000-8000-0000000000b4'''
\set gG '''e7a00000-0000-4000-8000-0000000000c1'''
\set sG '''e7a00000-0000-4000-8000-0000000000c2'''
\set aG '''e7a00000-0000-4000-8000-0000000000c3'''
\set hG '''e7a00000-0000-4000-8000-0000000000c4'''
RESET ROLE;
SELECT t.as_account(NULL);

-- ---------- 辅助：以 staff 身份签字、发布、撤下、准入 ----------
CREATE FUNCTION t.cr_sign(p_staff uuid, p_cv uuid, p_decision text DEFAULT 'approved') RETURNS text LANGUAGE sql AS $$
  SELECT t.as_staff(p_staff, format('SELECT platform.sign_content_version(%L, %L, platform.content_version_digest(%L), ''test'')::text',
                                    p_cv, p_decision, p_cv)) $$;
CREATE FUNCTION t.cr_publish(p_staff uuid, p_cv uuid) RETURNS text LANGUAGE sql AS $$
  SELECT t.as_staff(p_staff, format('SELECT platform.publish_content_version(%L)::text', p_cv)) $$;
CREATE FUNCTION t.cr_retire(p_staff uuid, p_cv uuid, p_basis text DEFAULT 'withdrawn') RETURNS text LANGUAGE sql AS $$
  SELECT t.as_staff(p_staff, format('SELECT platform.retire_content_version(%L, %L)::text', p_cv, p_basis)) $$;
CREATE FUNCTION t.cr_adm(p_staff uuid, p_subject text, p_item text, p_to text, p_level text DEFAULT 'Q2', p_ref text DEFAULT NULL,
                         p_type text DEFAULT NULL, p_license text DEFAULT NULL, p_decision text DEFAULT 'approved') RETURNS uuid
  LANGUAGE sql AS $$
  SELECT t.as_staff(p_staff, format('SELECT platform.sign_admission(%L, %L, %L, %L, %L, %L, %L, %L, platform.item_admission_digest(%L, %L), ''test'')::text',
                                    p_subject, p_item, p_to, p_decision, p_level, p_ref, p_type, p_license, p_subject, p_item))::uuid $$;
CREATE FUNCTION t.cr_apply(p_staff uuid, p_reviews uuid[]) RETURNS integer LANGUAGE sql AS $$
  SELECT t.as_staff(p_staff, format('SELECT platform.change_admission(%L::uuid[])::text', p_reviews))::integer $$;
-- 空的合成道法地图：草稿 → 两名 staff 签字 → 发布（首次发布算结构性）
CREATE FUNCTION t.cr_map(p_id uuid, p_version text) RETURNS void LANGUAGE sql AS $$
  INSERT INTO kge.map_version (id, subject, standard_year, version) VALUES (p_id, 'zhixing', 2022, p_version);
  SELECT t.as_staff('f5000000-0000-4000-8000-0000000000f5', format('SELECT kge.sign_review(''map_version'', %L, ''approved'', ''high'', ''test'')::text', p_id));
  SELECT t.as_staff('5f000000-0000-4000-8000-00000000005f',
                    format('SELECT kge.sign_review(''map_version'', %L, ''approved'', ''high'', ''test'')::text || kge.publish_map_version(%L)::text', p_id, p_id)) $$;
CREATE FUNCTION t.cr_bff_count(p_sql text) RETURNS bigint LANGUAGE plpgsql AS $$
DECLARE v bigint;
BEGIN
  PERFORM t.as_account('a0000000-0000-4000-8000-00000000000a');
  SET LOCAL ROLE starbuddy_bff;
  EXECUTE p_sql INTO v;
  RESET ROLE;
  PERFORM t.as_account(NULL);
  RETURN v;
END $$;
GRANT EXECUTE ON FUNCTION t.cr_sign(uuid, uuid, text), t.cr_publish(uuid, uuid), t.cr_retire(uuid, uuid, text),
  t.cr_adm(uuid, text, text, text, text, text, text, text, text), t.cr_apply(uuid, uuid[]), t.cr_map(uuid, text), t.cr_bff_count(text) TO PUBLIC;

-- 选科用的合成家庭：家长 gG、学生 sG，没有订阅（已上线时选科返回 no_subscription，未上线时返回 not_launched）
INSERT INTO platform.account (id, kind) VALUES (:gG, 'guardian'), (:aG, 'student');
INSERT INTO platform.household (id, created_by_account) VALUES (:hG, :gG);
INSERT INTO platform.student_profile (id, account_id, household_id, grade, region, textbook_version)
VALUES (:sG, :aG, :hG, 9, 'nanjing', 'bubian-2024');
INSERT INTO platform.guardian_student_link (guardian_account_id, student_id, granted_via) VALUES (:gG, :sG, 'household_creator');
SELECT t.ok(t.call(:gG, format('platform.enroll_subject(%L, ''zhixing'')', :sG)) = '{"outcome": "not_launched"}',
            '内容版本：道法没有已发布内容版本时选科返回 not_launched');

-- ================= 一、学习服务：只能登记草稿，不能改状态与准入 =================
SET ROLE starbuddy_learning;
INSERT INTO platform.curriculum_version (id, region, exam_year, grade, textbook_version, subject, content_version)
VALUES (:c1, 'nanjing', 2027, 9, 'syn-gate', 'zhixing', '0.1.0');
INSERT INTO platform.skill_node (subject, local_id, curriculum_version_id, label_code) VALUES
  ('zhixing', 'SYN-GATE-01', :c1, 'syn-gate-01'), ('zhixing', 'SYN-GATE-02', :c1, 'syn-gate-02');
INSERT INTO platform.item (subject, item_id, curriculum_version_id, skill_local_id, admission, source_ref, source_type, license_status) VALUES
  ('zhixing', 'SYN-GATE-I1', :c1, 'SYN-GATE-01', 'source_unverified', NULL, 'unknown', 'unverified'),
  ('zhixing', 'SYN-GATE-I2', :c1, 'SYN-GATE-01', 'pending_review', 'SYN-SRC-GATE-2', 'textbook', 'cleared');
UPDATE platform.skill_node SET label_code = 'syn-gate-02b' WHERE subject = 'zhixing' AND local_id = 'SYN-GATE-02';
SELECT t.ok((SELECT source_ref = 'SYN-SRC-GATE-2' AND source_type = 'textbook' AND license_status = 'unverified'
               FROM platform.item WHERE item_id = 'SYN-GATE-I2'),
            '准入：登记时自报的授权 cleared 记为 unverified（来源编号与类型留作登记元数据）');
SELECT t.ok((SELECT label_code FROM platform.skill_node WHERE local_id = 'SYN-GATE-02') = 'syn-gate-02b'
            AND (SELECT count(*) FROM platform.item WHERE curriculum_version_id = :c1) = 2,
            '内容版本：学习服务可以登记草稿内容版本、能力视图与题目，可以改草稿上的登记');
SELECT t.throws(format('UPDATE platform.curriculum_version SET status = ''published'' WHERE id = %L', :c1), '42501',
                '内容版本：学习服务直接改 status 被拒（42501）');
SELECT t.throws(format('UPDATE platform.curriculum_version SET published_at = now() WHERE id = %L', :c1), '42501',
                '内容版本：学习服务直接改 published_at 被拒（42501）');
SELECT t.throws(format('UPDATE platform.curriculum_version SET retired_at = now() WHERE id = %L', :c1), '42501',
                '内容版本：学习服务直接改 retired_at 被拒（42501）');
SELECT t.throws($$UPDATE platform.item SET admission = 'admitted' WHERE item_id = 'SYN-GATE-I2'$$, '42501',
                '内容版本：学习服务直接改 admission 被拒（42501）');
SELECT t.throws($$UPDATE platform.item SET admitted_at = now() WHERE item_id = 'SYN-GATE-I2'$$, '42501',
                '内容版本：学习服务直接改 admitted_at 被拒（42501）');
SELECT t.throws($$UPDATE platform.item SET source_ref = 'SYN-X' WHERE item_id = 'SYN-GATE-I1'$$, '42501',
                '内容版本：学习服务直接改来源编号被拒（42501）');
SELECT t.throws_like(format($$INSERT INTO platform.item (subject, item_id, curriculum_version_id, admission, admitted_at, source_ref, source_type, license_status)
                              VALUES ('zhixing', 'SYN-GATE-I9', %L, 'admitted', now(), 'SYN-SRC-9', 'textbook', 'cleared')$$, :c1),
                     '23514', '不能以 admitted 登记', '内容版本：题目 INSERT 时 admission = admitted 被拒');
SELECT t.throws_like($$INSERT INTO platform.curriculum_version (region, exam_year, grade, textbook_version, subject, content_version, status, published_at)
                       VALUES ('nanjing', 2027, 9, 'syn-gate', 'zhixing', '0.9.0', 'published', now())$$,
                     '23514', '只能以草稿登记', '内容版本：不能直接以已发布登记');
SELECT t.throws(format('SELECT platform.publish_content_version(%L)', :c1), '42501', '内容版本：学习服务不能发布');
SELECT t.throws(format('SELECT platform.sign_content_version(%L, ''approved'', platform.content_version_digest(%L), ''t'')', :c1, :c1), '42501',
                '内容版本：学习服务不能签字');
SELECT t.throws($$SELECT platform.change_admission(ARRAY[gen_random_uuid()])$$, '42501', '内容版本：学习服务不能执行准入变化');
SELECT t.throws('SELECT count(*) FROM platform.release_record', '42501', '内容版本：学习服务读不到审核与发布记录');
RESET ROLE;
SELECT t.throws_like($$INSERT INTO platform.curriculum_version (region, exam_year, grade, textbook_version, subject, content_version, status, published_at)
                       VALUES ('nanjing', 2027, 9, 'syn-gate', 'zhixing', '0.9.0', 'published', now())$$,
                     '23514', '只能以草稿登记', '内容版本：超级用户也不能直接以已发布登记');

-- ================= 二、谁能签字与发布 =================
SELECT t.throws(format('SELECT t.cr_sign(%L, %L)', :gA, :c1), '42501', '内容版本：家长账号不能签字');
SELECT t.throws(format('SELECT t.cr_sign(%L, %L)', '5d000000-0000-4000-8000-00000000005d', :c1), '42501', '内容版本：已停用的 staff 不能签字');
SELECT t.throws(format('SELECT t.cr_sign(NULL, %L)', :c1), '42501', '内容版本：没有设当前账号不能签字');
SELECT t.throws(format('SELECT t.cr_publish(%L, %L)', :gA, :c1), '42501', '内容版本：家长账号不能发布');
SELECT t.throws(format('SELECT t.cr_retire(%L, %L)', :gA, :c1), '42501', '内容版本：家长账号不能撤下');
SELECT t.throws(format('SELECT t.cr_adm(%L, ''zhixing'', ''SYN-GATE-I1'', ''pending_review'')', :gA), '42501', '内容版本：家长账号不能签准入');
SET ROLE starbuddy_kge;
SELECT t.as_account(:sf1);
SELECT t.throws(format('SELECT platform.publish_content_version(%L)', :c1), '42501', '内容版本：知识服务即使带 staff 账号也不能发布');
SELECT t.as_account(NULL);
RESET ROLE;
SET ROLE starbuddy_bff;
SELECT t.as_account(:gA);
SELECT t.throws('SELECT count(*) FROM platform.release_record', '42501', '内容版本：BFF 看不到审核与发布记录');
SELECT t.throws(format('SELECT platform.publish_content_version(%L)', :c1), '42501', '内容版本：BFF 不能发布');
SELECT t.throws(format('SELECT platform.content_version_digest(%L)', :c1), '42501', '内容版本：BFF 不能算摘要');
SELECT t.throws('SELECT * FROM platform.admission_change_log(NULL)', '42501', '内容版本：BFF 不能读准入变化记录');
SELECT t.as_account(NULL);
RESET ROLE;
SELECT t.throws_like(format('SELECT t.as_staff(%L, %L)', :sf1,
                            format('SELECT platform.sign_content_version(%L, ''approved'', repeat(''0'', 64), ''t'')::text', :c1)),
                     '23514', '摘要不符', '内容版本：签字时回传的摘要与当前不一致被拒（看到的就是签的）');

-- ================= 三、有骨架的学科：地图版本必填、须已发布、须同学科 =================
SELECT t.cr_sign(:sf1, :c1);
SELECT t.throws_like(format('SELECT t.cr_publish(%L, %L)', :sf1, :c1), '23514', '有知识地图骨架', '内容版本：道法发布时 map_version_id 为空被拒');
INSERT INTO kge.map_version (id, subject, standard_year, version) VALUES (:zd, 'zhixing', 2022, '0.0.1');
UPDATE platform.curriculum_version SET map_version_id = :zd WHERE id = :c1;
SELECT t.cr_sign(:sf1, :c1);
SELECT t.throws_like(format('SELECT t.cr_publish(%L, %L)', :sf1, :c1), '23514', '不是已发布状态', '内容版本：map_version_id 指向草稿地图版本被拒');
INSERT INTO kge.map_version (id, subject, standard_year, version) VALUES (:zr, 'zhixing', 2022, '0.0.2');
SELECT t.as_staff(:sf1, format('SELECT kge.retire_map_version(%L, ''test'')::text', :zr));
UPDATE platform.curriculum_version SET map_version_id = :zr WHERE id = :c1;
SELECT t.cr_sign(:sf1, :c1);
SELECT t.throws_like(format('SELECT t.cr_publish(%L, %L)', :sf1, :c1), '23514', '不是已发布状态', '内容版本：map_version_id 指向已撤下地图版本被拒');
SELECT t.throws(format('UPDATE platform.curriculum_version SET map_version_id = %L WHERE id = %L', 'ca200000-0000-4000-8000-00000000ca02', :c1),
                '23503', '内容版本：map_version_id 指向别的学科（数学）的地图版本被拒');
SELECT t.cr_map(:zm, '0.1.0');
UPDATE platform.curriculum_version SET map_version_id = :zm WHERE id = :c1;
-- 签的是改地图之前的内容：发布按摘要不符拒绝
SELECT t.throws_like(format('SELECT t.cr_publish(%L, %L)', :sf1, :c1), '23514', '摘要不符', '内容版本：签字后改了地图版本，发布因摘要不符被拒');

-- ================= 四、签字后追加题目登记：摘要不符；驳回后不能发布；重签后发布 =================
SELECT t.cr_sign(:sf1, :c1);
SET ROLE starbuddy_learning;
INSERT INTO platform.item (subject, item_id, curriculum_version_id, skill_local_id) VALUES ('zhixing', 'SYN-GATE-I3', :c1, 'SYN-GATE-02');
RESET ROLE;
SELECT t.throws_like(format('SELECT t.cr_publish(%L, %L)', :sf1, :c1), '23514', '摘要不符', '内容版本：签字后追加一条题目登记，发布因摘要不符被拒');
SELECT t.cr_sign(:sf2, :c1);
SELECT t.cr_sign(:sf1, :c1, 'rejected');
SELECT t.throws_like(format('SELECT t.cr_publish(%L, %L)', :sf1, :c1), '23514', '没有批准的审核记录', '内容版本：最近一条结论是驳回时不能发布');
SELECT t.cr_sign(:sf2, :c1);
SELECT t.cr_publish(:sf1, :c1);
SELECT t.ok((SELECT status = 'published' AND published_at IS NOT NULL AND retired_at IS NULL FROM platform.curriculum_version WHERE id = :c1),
            '内容版本：批准且摘要一致、地图版本已发布时，经函数发布成功');
SELECT t.ok((SELECT count(*) FROM platform.release_record WHERE curriculum_version_id = :c1 AND line = 'content_version' AND action = 'publish'
               AND signer = :sf1 AND digest = platform.content_version_digest(:c1)) = 1,
            '内容版本：发布留记录（签字人为发布人、摘要为发布时的内容）');
SELECT t.ok(t.call(:gG, format('platform.enroll_subject(%L, ''zhixing'')', :sG)) = '{"outcome": "no_subscription"}',
            '内容版本：发布后选科不再返回 not_launched（学科目录非 planned）');

-- ================= 五、已发布：只能经函数撤下；改其他字段、改回草稿、删除、追加登记全部被拒 =================
SET ROLE starbuddy_learning;
SELECT t.throws_like(format('UPDATE platform.curriculum_version SET map_version_id = NULL WHERE id = %L', :c1), '23514', '只能撤下',
                     '内容版本：已发布版本改地图版本被拒');
SELECT t.throws_like(format('UPDATE platform.curriculum_version SET region = ''suzhou'' WHERE id = %L', :c1), '23514', '只能撤下',
                     '内容版本：已发布版本改六段标识被拒');
SELECT t.throws_like(format($$INSERT INTO platform.skill_node (subject, local_id, curriculum_version_id, label_code) VALUES ('zhixing', 'SYN-GATE-03', %L, 'x')$$, :c1),
                     '23514', '只能在草稿内容版本上', '内容版本：已发布版本上不能追加能力视图');
SELECT t.throws_like(format($$INSERT INTO platform.item (subject, item_id, curriculum_version_id) VALUES ('zhixing', 'SYN-GATE-I4', %L)$$, :c1),
                     '23514', '只能在草稿内容版本上', '内容版本：已发布版本上不能追加题目登记');
SELECT t.throws_like($$UPDATE platform.skill_node SET label_code = 'y' WHERE local_id = 'SYN-GATE-01'$$, '23514', '只能在草稿内容版本上',
                     '内容版本：已发布版本上的能力视图不能改');
SELECT t.throws_like($$UPDATE platform.item SET skill_local_id = 'SYN-GATE-02' WHERE item_id = 'SYN-GATE-I1'$$, '23514', '只能在草稿内容版本上',
                     '内容版本：已发布版本上的题目登记不能改');
RESET ROLE;
SELECT t.throws(format('UPDATE platform.curriculum_version SET status = ''draft'', published_at = NULL WHERE id = %L', :c1), '42501',
                '内容版本：已发布版本改回草稿被拒（超级用户也拦）');
SELECT t.throws(format('UPDATE platform.curriculum_version SET status = ''retired'', retired_at = now() WHERE id = %L', :c1), '42501',
                '内容版本：直接改为已撤下被拒（只能经 retire_content_version）');
SELECT t.throws_like(format('UPDATE platform.curriculum_version SET content_version = ''0.1.1'' WHERE id = %L', :c1), '23514', '只能撤下',
                     '内容版本：超级用户改已发布版本的其他字段也被拒');
SELECT t.throws_like(format('DELETE FROM platform.curriculum_version WHERE id = %L', :c1), '23514', '不能删除', '内容版本：已发布版本不能删除');
SELECT t.throws_like($$DELETE FROM platform.item WHERE item_id = 'SYN-GATE-I3'$$, '23514', '只能在草稿内容版本上', '内容版本：已发布版本下的题目登记不能删除');
SELECT t.throws_like(format('SELECT t.cr_sign(%L, %L)', :sf1, :c1), '23514', '只能对草稿', '内容版本：已发布版本不能再签字');
SELECT t.throws_like(format('SELECT t.cr_publish(%L, %L)', :sf1, :c1), '23514', '只能发布草稿', '内容版本：已发布版本不能再发布');
SELECT t.throws(format('TRUNCATE platform.release_record'), '42501', '内容版本：审核与发布记录不能清空');
SELECT t.throws(format('UPDATE platform.release_record SET basis_code = ''x'' WHERE curriculum_version_id = %L', :c1), '42501',
                '内容版本：审核与发布记录不能改');
SELECT t.throws(format('DELETE FROM platform.release_record WHERE curriculum_version_id = %L', :c1), '42501', '内容版本：审核与发布记录不能删');
SET ROLE starbuddy_review;
SELECT t.throws(format($$INSERT INTO platform.release_record (line, action, curriculum_version_id, subject, decision, digest, signer, decided_at)
                         VALUES ('content_version', 'review', %L, 'zhixing', 'approved', repeat('a', 64), %L, now())$$, :c1, :sf1),
                '42501', '内容版本：签字身份也不能直接写批准记录（只能经签字函数）');
RESET ROLE;

-- ================= 六、同前五段的版本号：必须大于已发布过的最大值（按数字比较） =================
SET ROLE starbuddy_learning;
INSERT INTO platform.curriculum_version (id, region, exam_year, grade, textbook_version, subject, content_version, map_version_id) VALUES
  (:c2, 'nanjing', 2027, 9, 'syn-gate', 'zhixing', '0.0.9', 'c7a00000-0000-4000-8000-0000000000a1'),
  (:c3, 'nanjing', 2027, 9, 'syn-gate', 'zhixing', '0.10.0', 'c7a00000-0000-4000-8000-0000000000a1'),
  (:c4, 'nanjing', 2027, 8, 'syn-gate', 'zhixing', '0.0.1', 'c7a00000-0000-4000-8000-0000000000a1');
INSERT INTO platform.skill_node (subject, local_id, curriculum_version_id, label_code) VALUES
  ('zhixing', 'SYN-GATE-21', :c2, 'syn-gate-21'), ('zhixing', 'SYN-GATE-31', :c3, 'syn-gate-31');
INSERT INTO platform.item (subject, item_id, curriculum_version_id, skill_local_id, admission, source_ref, source_type, license_status) VALUES
  ('zhixing', 'SYN-GATE-I31', :c3, 'SYN-GATE-31', 'pending_review', 'SYN-SRC-GATE-31', 'exam_paper', 'cleared');
RESET ROLE;
SELECT t.cr_sign(:sf1, :c2);
SELECT t.throws_like(format('SELECT t.cr_publish(%L, %L)', :sf1, :c2), '23514', '必须大于', '内容版本：同前五段下不大于已发布最大值的版本号被拒');
SELECT t.cr_sign(:sf1, :c4);
SELECT t.ok(t.cr_publish(:sf1, :c4) IS NOT NULL, '内容版本：前五段不同（年级不同）时版本号各自比较');

-- ================= 七、准入：方向与前提 =================
-- source_unverified → admitted 跳级被拒；升 pending_review 不要求来源已核（只表示进入学科审核）
SELECT t.throws_like(format('SELECT t.cr_adm(%L, ''zhixing'', ''SYN-GATE-I1'', ''admitted'', ''Q3'', ''SYN-SRC-1'', ''textbook'', ''cleared'')', :sf1),
                     '23514', '不允许的准入变化', '准入：source_unverified → admitted 跳级被拒');
SELECT t.ok(t.cr_adm(:sf1, 'zhixing', 'SYN-GATE-I1', 'pending_review') IS NOT NULL, '准入：升 pending_review 不要求来源已核（可以签）');
SELECT t.cr_apply(:sf2, ARRAY[t.cr_adm(:sf1, 'zhixing', 'SYN-GATE-I1', 'pending_review', NULL, 'SYN-SRC-GATE-1', 'textbook', 'cleared')]);
SELECT t.ok((SELECT admission = 'pending_review' AND source_ref = 'SYN-SRC-GATE-1' AND source_type = 'textbook' AND license_status = 'cleared'
               FROM platform.item WHERE item_id = 'SYN-GATE-I1'),
            '准入：source_unverified → pending_review 经签字与执行生效，来源三列按审核记录写入');
-- 升 admitted：审核等级不够、缺来源编号都被拒
SELECT t.throws_like(format('SELECT t.cr_adm(%L, ''zhixing'', ''SYN-GATE-I2'', ''admitted'', ''Q1'', NULL, NULL, ''cleared'')', :sf1),
                     '23514', '学科审核等级', '准入：目标 admitted 审核等级低于 Q2 被拒');
SELECT t.throws_like(format('SELECT t.cr_adm(%L, ''zhixing'', ''SYN-GATE-I2'', ''admitted'', NULL, NULL, NULL, ''cleared'')', :sf1),
                     '23514', '学科审核等级', '准入：目标 admitted 没有审核等级被拒');
-- 登记时自报的授权 cleared 已记为 unverified；升 admitted 不传授权状态被拒（不沿用题目上的值），传了也只能是签字人写的
SELECT t.throws_like(format('SELECT t.cr_adm(%L, ''zhixing'', ''SYN-GATE-I2'', ''admitted'', ''Q2'')', :sf1),
                     '22023', '显式', '准入：升 admitted 不写授权状态被拒（不沿用登记时自报的值）');
-- SYN-GATE-I3 登记为 pending_review（默认）、来源未登记：升 admitted 缺来源编号被拒（方向合格，前提不合格）
SELECT t.throws_like(format('SELECT t.cr_adm(%L, ''zhixing'', ''SYN-GATE-I3'', ''admitted'', ''Q5'', NULL, NULL, ''cleared'')', :sf1),
                     '23514', '来源已核', '准入：目标 admitted 缺来源编号被拒');
SELECT t.throws_like(format('SELECT t.cr_adm(%L, ''zhixing'', ''SYN-GATE-I1'', ''admitted'', ''Q2'', NULL, ''unknown'', ''cleared'')', :sf1),
                     '23514', '来源已核', '准入：目标 admitted 来源类型未知被拒');
SELECT t.throws_like(format('SELECT t.cr_adm(%L, ''zhixing'', ''SYN-GATE-I1'', ''admitted'', ''Q2'', NULL, NULL, ''denied'')', :sf1),
                     '23514', '来源已核', '准入：目标 admitted 授权被拒（denied）不能准入');
-- 正常升级：pending_review → admitted（Q2）
SELECT t.cr_apply(:sf1, ARRAY[t.cr_adm(:sf2, 'zhixing', 'SYN-GATE-I2', 'admitted', 'Q2', NULL, NULL, 'cleared')]);
SELECT t.ok((SELECT admission = 'admitted' AND admitted_at IS NOT NULL FROM platform.item WHERE item_id = 'SYN-GATE-I2'),
            '准入：pending_review → admitted（来源已核、Q2）在已发布版本上生效');
-- admitted → source_unverified / pending_review 被拒；rejected → admitted 被拒
SELECT t.throws_like(format('SELECT t.cr_adm(%L, ''zhixing'', ''SYN-GATE-I2'', ''source_unverified'')', :sf1),
                     '23514', '不允许的准入变化', '准入：admitted → source_unverified 被拒');
SELECT t.throws_like(format('SELECT t.cr_adm(%L, ''zhixing'', ''SYN-GATE-I2'', ''pending_review'')', :sf1),
                     '23514', '不允许的准入变化', '准入：admitted → pending_review 被拒（降级只能到 rejected）');
SELECT t.cr_apply(:sf1, ARRAY[t.cr_adm(:sf1, 'zhixing', 'SYN-GATE-I3', 'rejected', NULL)]);
SELECT t.throws_like(format('SELECT t.cr_adm(%L, ''zhixing'', ''SYN-GATE-I3'', ''admitted'', ''Q5'', ''SYN-SRC-3'', ''textbook'', ''cleared'')', :sf1),
                     '23514', '不允许的准入变化', '准入：rejected → admitted 被拒');
SELECT t.throws_like(format('SELECT t.cr_adm(%L, ''zhixing'', ''SYN-GATE-I3'', ''rejected'')', :sf1),
                     '23514', '不允许的准入变化', '准入：rejected → rejected 不算变化，被拒');
-- 执行：驳回的审核不能执行；同一审核不能执行两次；签字后题目变了不能执行；之后另有审核时以最近一条为准；整批原子
SELECT t.throws_like(format('SELECT t.cr_apply(%L, ARRAY[t.cr_adm(%L, ''zhixing'', ''SYN-GATE-I31'', ''admitted'', ''Q2'', NULL, NULL, NULL, ''rejected'')])', :sf1, :sf1),
                     '23514', '不是批准', '准入：驳回的审核记录不能执行');
SELECT t.cr_adm(:sf1, 'zhixing', 'SYN-GATE-I31', 'admitted', 'Q2', NULL, NULL, 'cleared') AS r31 \gset
SELECT t.cr_adm(:sf2, 'zhixing', 'SYN-GATE-I31', 'admitted', 'Q3', NULL, NULL, 'cleared') AS r31b \gset
SELECT t.throws_like(format('SELECT t.cr_apply(%L, ARRAY[%L]::uuid[])', :sf1, :'r31'), '23514', '最近一条', '准入：之后另有审核结论时，旧的审核不能执行');
SELECT t.throws_like(format('SELECT t.cr_apply(%L, ARRAY[%L, %L]::uuid[])', :sf1, :'r31b', :'r31b'), '22023', '不能为空或重复', '准入：同一批里审核记录不能重复');
SELECT t.throws_like(format('SELECT t.cr_apply(%L, ARRAY[%L, %L]::uuid[])', :sf1, :'r31b', gen_random_uuid()), 'P0002', '不存在',
                     '准入：整批里有一条不存在，整批不生效');
SELECT t.ok((SELECT admission FROM platform.item WHERE item_id = 'SYN-GATE-I31') = 'pending_review', '准入：整批失败时前面的变化也回滚');
SELECT t.ok(t.cr_apply(:sf1, ARRAY[:'r31b']::uuid[]) = 1, '准入：最近一条批准的审核可以执行');
SELECT t.throws_like(format('SELECT t.cr_apply(%L, ARRAY[%L]::uuid[])', :sf1, :'r31b'), '23505', '已经执行过', '准入：同一条审核不能执行两次');
-- 签字后题目登记变了（草稿版本上改了所挂能力点）：摘要不符，不能执行
SET ROLE starbuddy_learning;
INSERT INTO platform.item (subject, item_id, curriculum_version_id, skill_local_id) VALUES ('zhixing', 'SYN-GATE-I21', :c2, 'SYN-GATE-21');
RESET ROLE;
SELECT t.cr_adm(:sf1, 'zhixing', 'SYN-GATE-I21', 'rejected', NULL) AS r21 \gset
SET ROLE starbuddy_learning;
UPDATE platform.item SET skill_local_id = NULL WHERE item_id = 'SYN-GATE-I21';
RESET ROLE;
SELECT t.throws_like(format('SELECT t.cr_apply(%L, ARRAY[%L]::uuid[])', :sf1, :'r21'), '23514', '摘要不符', '准入：签字后题目登记被改，执行因摘要不符被拒');
SELECT t.throws_like(format('SELECT t.as_staff(%L, %L)', :sf1,
                            $$SELECT platform.sign_admission('zhixing', 'SYN-GATE-I21', 'rejected', 'approved', NULL, NULL, NULL, NULL, repeat('0', 64), 't')::text$$),
                     '23514', '摘要不符', '准入：签字时回传的摘要与当前不一致被拒');
SELECT t.throws_like($$UPDATE platform.item SET admission = 'rejected' WHERE item_id = 'SYN-GATE-I21'$$, '42501', 'change_admission',
                     '准入：超级用户直接改 admission 也被拒（只能经 change_admission）');
SELECT t.throws_like(format($$UPDATE platform.item SET curriculum_version_id = %L WHERE item_id = 'SYN-GATE-I21'$$, :c3), '23514', '挪到别的内容版本',
                     '内容版本：题目登记不能挪到别的内容版本（两个都是草稿也不行）');
-- 即使以复制模式跳过触发器（只有超级用户能做），已准入的题仍必须来源已核（约束兜底）
BEGIN;
SET LOCAL session_replication_role = replica;
SELECT t.throws_like(format($$INSERT INTO platform.item (subject, item_id, curriculum_version_id, admission, admitted_at) VALUES ('zhixing', 'SYN-GATE-I8', %L, 'admitted', now())$$, :c3),
                     '23514', 'item_admitted_source', '准入：已准入的题必须有来源编号、已知来源类型、授权 cleared（约束，跳过触发器也拦）');
ROLLBACK;

-- ================= 八、BFF 可见性：只看已发布版本下的能力视图，已准入且所属版本已发布的题目 =================
SELECT t.ok(t.cr_bff_count($$SELECT count(*) FROM platform.skill_node WHERE local_id LIKE 'SYN-GATE-%'$$) = 2
            AND t.cr_bff_count($$SELECT count(*) FROM platform.skill_node WHERE local_id IN ('SYN-GATE-21', 'SYN-GATE-31')$$) = 0,
            '内容版本：BFF 看得到已发布版本的能力视图，看不到草稿版本的');
SELECT t.ok(t.cr_bff_count($$SELECT count(*) FROM platform.item WHERE item_id LIKE 'SYN-GATE-%'$$) = 1
            AND t.cr_bff_count($$SELECT count(*) FROM platform.item WHERE item_id = 'SYN-GATE-I2'$$) = 1,
            '内容版本：BFF 只看得到已发布版本下已准入的题（未准入、已驳回、草稿版本下已准入的都看不到）');
SELECT t.ok((SELECT admission FROM platform.item WHERE item_id = 'SYN-GATE-I31') = 'admitted', '内容版本：草稿版本下也有一道已准入的题（上一行的反例）');
SELECT t.ok(t.cr_bff_count(format('SELECT count(*) FROM platform.curriculum_version WHERE id IN (%L, %L, %L)', :c1, :c2, :c3)) = 1,
            '内容版本：BFF 只看得到已发布的内容版本');

-- ================= 九、撤下：必须写依据；撤下后只读、BFF 立即看不到、新选科回到 not_launched =================
SELECT t.throws(format('SELECT t.cr_retire(%L, %L, NULL)', :sf1, :c1), '22023', '内容版本：撤下必须写依据');
SELECT t.cr_retire(:sf1, :c1);
SELECT t.ok((SELECT status = 'retired' AND retired_at IS NOT NULL AND published_at IS NOT NULL FROM platform.curriculum_version WHERE id = :c1)
            AND (SELECT count(*) FROM platform.release_record WHERE curriculum_version_id = :c1 AND action = 'retire' AND basis_code = 'withdrawn'
                   AND signer = :sf1) = 1,
            '内容版本：经 retire_content_version 撤下，记撤下时间与撤下记录（依据、签字人）');
SELECT t.ok(t.cr_bff_count($$SELECT count(*) FROM platform.skill_node WHERE local_id LIKE 'SYN-GATE-0%'$$) = 0
            AND t.cr_bff_count($$SELECT count(*) FROM platform.item WHERE item_id = 'SYN-GATE-I2'$$) = 0,
            '内容版本：撤下后 BFF 立即看不到其下的能力视图与已准入题目');
SELECT t.cr_retire(:sf1, :c4, 'content_error');
SELECT t.ok(t.call(:gG, format('platform.enroll_subject(%L, ''zhixing'')', :sG)) = '{"outcome": "not_launched"}',
            '内容版本：该学科已发布版本都撤下后，新选科回到 not_launched');
SELECT t.throws_like(format('UPDATE platform.curriculum_version SET map_version_id = NULL WHERE id = %L', :c1), '23514', '只读', '内容版本：已撤下版本任何改动被拒');
SELECT t.throws_like(format('UPDATE platform.curriculum_version SET status = ''published'' WHERE id = %L', :c1), '23514', '只读',
                     '内容版本：已撤下版本不能改回已发布');
SELECT t.throws_like(format('DELETE FROM platform.curriculum_version WHERE id = %L', :c1), '23514', '不能删除', '内容版本：已撤下版本不能删除');
SELECT t.throws_like(format('SELECT t.cr_retire(%L, %L)', :sf1, :c1), '23514', '只能撤下草稿或已发布', '内容版本：已撤下版本不能再撤下');
SELECT t.throws_like(format('SELECT t.cr_adm(%L, ''zhixing'', ''SYN-GATE-I1'', ''admitted'', ''Q3'', NULL, NULL, ''cleared'')', :sf1), '23514', '只读',
                     '内容版本：已撤下版本下的题目不能再升准入');
SELECT t.cr_apply(:sf1, ARRAY[t.cr_adm(:sf2, 'zhixing', 'SYN-GATE-I2', 'rejected')]);
SELECT t.ok((SELECT admission FROM platform.item WHERE item_id = 'SYN-GATE-I2') = 'rejected',
            '内容版本：已撤下版本下的题仍可改为 rejected（止损）');
-- 0.1.0 已发布后撤下，0.0.9 仍不能发布（比较的是已发布过的最大值）；0.10.0 按数字比较大于 0.1.0
SELECT t.throws_like(format('SELECT t.cr_publish(%L, %L)', :sf1, :c2), '23514', '必须大于', '内容版本：撤下后旧的较小版本号仍不能发布');
SELECT t.cr_sign(:sf1, :c3);
SELECT t.ok(t.cr_publish(:sf1, :c3) IS NOT NULL, '内容版本：版本号按数字比较（0.10.0 大于 0.1.0）');
-- 草稿可以直接撤下（放弃），没有发布时间
SELECT t.cr_retire(:sf1, :c2, 'withdrawn');
SELECT t.ok((SELECT status = 'retired' AND published_at IS NULL AND retired_at IS NOT NULL FROM platform.curriculum_version WHERE id = :c2),
            '内容版本：草稿可以直接撤下');

-- ================= 十、准入降级留受影响清单；学习服务读准入变化记录 =================
BEGIN;
SELECT t.cr_apply(:sf1, ARRAY[t.cr_adm(:sf1, 'aya', 'synthetic-0301-07', 'rejected', NULL)]);
SELECT t.ok((SELECT affected_rubric_versions FROM platform.release_record
              WHERE line = 'admission' AND action = 'apply' AND item_id = 'synthetic-0301-07' AND to_admission = 'rejected')
            @> '{aya-quad-rubric@0.1.0}',
            '准入：降级（admitted → rejected）在执行记录上留受影响的 rubric_version 清单');
SELECT t.ok((SELECT affected_rubric_versions IS NULL FROM platform.release_record
              WHERE line = 'admission' AND action = 'apply' AND item_id = 'SYN-GATE-I2' AND to_admission = 'admitted'),
            '准入：升级不留受影响清单（升级不追溯）');
SET LOCAL ROLE starbuddy_learning;
SELECT t.ok(EXISTS (SELECT 1 FROM platform.admission_change_log(now() - interval '1 hour')
                     WHERE item_id = 'synthetic-0301-07' AND from_admission = 'admitted' AND to_admission = 'rejected'
                       AND affected_rubric_versions @> '{aya-quad-rubric@0.1.0}'),
            '准入：学习服务能经 admission_change_log 读到准入变化与受影响清单（KGE-04 重算用，不含签字人）');
SELECT t.throws('SELECT signer FROM platform.release_record', '42501', '准入：学习服务读不到签字人');
ROLLBACK;
SELECT t.ok(pg_get_function_result('platform.admission_change_log(timestamptz)'::regprocedure) !~ '(signer|account)',
            '准入：准入变化记录的返回列里没有签字人');

-- ================= 十一、其他学科：数学缺地图被拒；语文没有地图也能发布 =================
INSERT INTO platform.curriculum_version (id, region, exam_year, grade, textbook_version, subject, content_version) VALUES
  ('e7a00000-0000-4000-8000-0000000000b5', 'nanjing', 2027, 9, 'syn-gate', 'aya', '0.1.0'),
  ('e7a00000-0000-4000-8000-0000000000b6', 'nanjing', 2027, 9, 'syn-gate', 'wenli', '0.1.0');
SELECT t.cr_sign(:sf1, 'e7a00000-0000-4000-8000-0000000000b5');
SELECT t.throws_like(format('SELECT t.cr_publish(%L, %L)', :sf1, 'e7a00000-0000-4000-8000-0000000000b5'), '23514', '有知识地图骨架',
                     '内容版本：数学发布时 map_version_id 为空被拒');
SELECT t.cr_sign(:sf1, 'e7a00000-0000-4000-8000-0000000000b6');
SELECT t.cr_publish(:sf1, 'e7a00000-0000-4000-8000-0000000000b6');
SELECT t.ok((SELECT status = 'published' AND map_version_id IS NULL FROM platform.curriculum_version WHERE id = 'e7a00000-0000-4000-8000-0000000000b6'),
            '内容版本：语文没有地图骨架，map_version_id 为空也能发布');
SELECT t.throws(format($$INSERT INTO platform.release_record (line, action, curriculum_version_id, subject, decision, digest, signer, decided_at)
                         VALUES ('content_version', 'review', %L, 'aya', 'approved', repeat('a', 64), %L, now())$$,
                       'e7a00000-0000-4000-8000-0000000000b6', :sf1),
                '23503', '内容版本：审核记录的学科必须与内容版本一致（复合外键）');
SELECT t.ok((SELECT admission FROM platform.item WHERE subject = 'aya' AND item_id = 'synthetic-0301-07') = 'admitted', '准入：降级测试已回滚');
