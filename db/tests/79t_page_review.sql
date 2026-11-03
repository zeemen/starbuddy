-- 0038 编译页面审核记录（specs/release-gate-sprint4/spec.md 冲刺 3 验收：“content_review 的页面记录 page_commit 必须是 40 位十六进制；
-- 同一页面同一提交不能记两次”）。合成数据（规则 8）：合成页面 ID 与合成提交号；staff 账号 sf1（10 号夹具）、sf2（76 号）、
-- 已停用的 staff（79b 新建）。用 76 号测试定义的 t.as_staff(账号, SQL)。
\set sf1 '''5f000000-0000-4000-8000-00000000005f'''
\set sf2 '''5e000000-0000-4000-8000-00000000005e'''
\set c1 '''1111111111111111111111111111111111111111'''
\set c2 '''2222222222222222222222222222222222222222'''
RESET ROLE;
SELECT t.as_account(NULL);

CREATE FUNCTION t.pg_sign(p_staff uuid, p_page text, p_commit text, p_decision text DEFAULT 'approved', p_risk text DEFAULT 'high')
  RETURNS text LANGUAGE sql AS $$
  SELECT t.as_staff(p_staff, format('SELECT kge.sign_page_review(%L, %L, %L, %L, ''mr_approved'')::text', p_page, p_commit, p_decision, p_risk)) $$;
GRANT EXECUTE ON FUNCTION t.pg_sign(uuid, text, text, text, text) TO PUBLIC;

-- ================= 谁能签页面 =================
SELECT t.throws(format('SELECT t.pg_sign(%L, ''MATH.PGREV.T.A'', %L)', 'a0000000-0000-4000-8000-00000000000a', :c1),
                '42501', '页面审核：家长账号不能签页面');
SELECT t.throws(format('SELECT t.pg_sign(%L, ''MATH.PGREV.T.A'', %L)', '5d000000-0000-4000-8000-00000000005d', :c1),
                '42501', '页面审核：已停用的 staff 不能签页面');
SELECT t.throws(format('SELECT t.pg_sign(NULL, ''MATH.PGREV.T.A'', %L)', :c1), '42501', '页面审核：没有设当前账号时不能签页面');
SET ROLE starbuddy_kge;
SELECT t.as_account('5f000000-0000-4000-8000-00000000005f');
SELECT t.throws(format('SELECT kge.sign_page_review(''MATH.PGREV.T.A'', %L, ''approved'', ''high'', ''t'')', :c1), '42501',
                '页面审核：知识服务即使带 staff 账号也不能签页面');
SELECT t.as_account(NULL);
RESET ROLE;
SET ROLE starbuddy_learning;
SELECT t.throws(format('SELECT kge.sign_page_review(''MATH.PGREV.T.A'', %L, ''approved'', ''high'', ''t'')', :c1), '42501', '页面审核：学习服务不能签页面');
SELECT t.throws('SELECT * FROM kge.page_review_record(NULL)', '42501', '页面审核：学习服务不能读页面审核记录');
RESET ROLE;
SET ROLE starbuddy_bff;
SELECT t.throws('SELECT * FROM kge.page_review_record(NULL)', '42501', '页面审核：BFF 不能读页面审核记录');
RESET ROLE;
SET ROLE starbuddy_kge_read;
SELECT t.throws('SELECT * FROM kge.page_review_record(NULL)', '42501', '页面审核：知识服务只读角色不能读页面审核记录');
RESET ROLE;

-- ================= 参数核对 =================
SELECT t.throws_like(format('SELECT t.pg_sign(%L, ''MATH.PGREV.T.A'', %L)', :sf1, upper(:c1::text || 'a')), '22023', '40 位小写十六进制',
                     '页面审核：签字函数拒绝 41 位提交号');
SELECT t.throws_like(format('SELECT t.pg_sign(%L, ''MATH.PGREV.T.A'', %L)', :sf1, 'ABCDEF1111111111111111111111111111111111'), '22023', '40 位小写十六进制',
                     '页面审核：签字函数拒绝大写十六进制提交号');
SELECT t.throws_like(format('SELECT t.pg_sign(%L, ''MATH.PGREV.T.A'', %L)', :sf1, left(:c1::text, 39)), '22023', '40 位小写十六进制',
                     '页面审核：签字函数拒绝 39 位提交号');
SELECT t.throws_like(format('SELECT t.pg_sign(%L, ''math.pgrev.t.a'', %L)', :sf1, :c1), '22023', '页面 ID', '页面审核：页面 ID 必须是节点 ID 写法');
SELECT t.throws(format('SELECT t.pg_sign(%L, ''MATH.PGREV.T.A'', %L, ''pending'')', :sf1, :c1), '22023', '页面审核：结论不能是待审');
SELECT t.throws(format('SELECT t.pg_sign(%L, ''MATH.PGREV.T.A'', %L, ''approved'', ''urgent'')', :sf1, :c1), '22023', '页面审核：风险级别只能是三级之一');

-- ================= 签字与“同一页面同一提交不能记两次” =================
SELECT t.ok(t.pg_sign(:sf1, 'MATH.PGREV.T.A', :c1) IS NOT NULL, '页面审核：staff 可以对页面的一个提交签字批准');
SELECT t.ok((SELECT s.account_id FROM kge.content_review r JOIN platform.content_signoff s ON s.content_review_id = r.id
              WHERE r.object_type = 'page' AND r.object_id = 'MATH.PGREV.T.A' AND r.page_commit = :c1) = :sf1::uuid,
            '页面审核：签字人是当前登录的 staff 账号，由函数写入');
SELECT t.throws_like(format('SELECT t.pg_sign(%L, ''MATH.PGREV.T.A'', %L)', :sf1, :c1), '23505', '只能记一次',
                     '页面审核：同一账号对同一页面同一提交不能记两次');
SELECT t.throws_like(format('SELECT t.pg_sign(%L, ''MATH.PGREV.T.A'', %L, ''rejected'')', :sf1, :c1), '23505', '只能记一次',
                     '页面审核：同一账号对同一页面同一提交批准后也不能再记驳回');
SELECT t.ok(t.pg_sign(:sf2, 'MATH.PGREV.T.A', :c1) IS NOT NULL, '页面审核：第二名 staff 可以对同一页面同一提交签字（高风险两人）');
SELECT t.ok(t.pg_sign(:sf1, 'MATH.PGREV.T.A', :c2, 'approved', 'low') IS NOT NULL, '页面审核：同一账号可以签同一页面的另一个提交');
SELECT t.ok(t.pg_sign(:sf1, 'MATH.PGREV.T.B', :c1, 'rejected', 'medium') IS NOT NULL, '页面审核：同一账号可以签另一页面的同一提交（驳回）');
SELECT t.throws_like(format('SELECT t.pg_sign(%L, ''MATH.PGREV.T.B'', %L, ''approved'', ''high'')', :sf2, :c1), '23514', '第一条签字不一致',
                     '页面审核：同一页面同一提交的后续签字风险级别必须与第一条一致');
SELECT t.ok(t.pg_sign(:sf2, 'MATH.PGREV.T.B', :c1, 'approved', 'medium') IS NOT NULL, '页面审核：风险级别一致时后续签字可以记');
SET ROLE starbuddy_kge;
SELECT t.ok((SELECT count(*) FROM kge.page_review_record('aya')) = 5 AND (SELECT count(*) FROM kge.page_review_record('wenli')) = 0,
            '页面审核：导出按学科前缀过滤（数学页面只在数学下）');
SELECT t.ok((SELECT bool_and(digest ~ '^[0-9a-f]{64}$') FROM kge.page_review_record(NULL)), '页面审核：导出带签字时的摘要');
RESET ROLE;
SELECT t.ok((SELECT array_agg(page_signer_no ORDER BY page_signer_no) FROM kge.content_review
              WHERE object_type = 'page' AND object_id = 'MATH.PGREV.T.A' AND page_commit = :c1) = '{1,2}',
            '页面审核：同一页面同一提交的签字序号按顺序分配');

-- ================= 表约束：直接写入（超级用户也受约束） =================
SELECT t.throws(format($f$INSERT INTO kge.content_review (object_type, object_id, risk_level, decision, decided_at, digest, page_commit, page_signer_no)
                          VALUES ('page', 'MATH.PGREV.T.A', 'high', 'approved', now(), %L, %L, 1)$f$, repeat('a', 64), :c1),
                '23505', '页面审核：同一页面同一提交同一签字序号不能记两次（唯一约束）');
SELECT t.throws_like(format($f$INSERT INTO kge.content_review (object_type, object_id, risk_level, decision, decided_at, digest, page_commit, page_signer_no)
                          VALUES ('page', 'MATH.PGREV.T.C', 'high', 'approved', now(), %L, %L, 1)$f$, repeat('a', 64), upper(:c1::text) || 'Z'),
                '23514', 'content_review_page_commit', '页面审核：页面记录的提交号不是 40 位十六进制被拒（表约束）');
SELECT t.throws_like(format($f$INSERT INTO kge.content_review (object_type, object_id, risk_level, decision, decided_at, digest, page_commit, page_signer_no)
                          VALUES ('page', 'MATH.PGREV.T.C', 'high', 'approved', now(), %L, %L, 1)$f$, repeat('a', 64), 'G' || right(:c1::text, 39)),
                '23514', 'content_review_page_commit', '页面审核：页面记录的提交号含非十六进制字符被拒（表约束）');
SELECT t.throws_like(format($f$INSERT INTO kge.content_review (object_type, object_id, risk_level, decision, decided_at, digest, page_signer_no)
                          VALUES ('page', 'MATH.PGREV.T.C', 'high', 'approved', now(), %L, 1)$f$, repeat('a', 64)),
                '23514', 'content_review_page_commit', '页面审核：页面记录缺提交号被拒');
SELECT t.throws_like(format($f$INSERT INTO kge.content_review (object_type, object_id, risk_level, decision, decided_at, digest, page_commit)
                          VALUES ('page', 'MATH.PGREV.T.C', 'high', 'approved', now(), %L, %L)$f$, repeat('a', 64), :c1),
                '23514', 'content_review_page_commit', '页面审核：页面记录缺签字序号被拒');
SELECT t.throws_like(format($f$INSERT INTO kge.content_review (object_type, object_id, risk_level, page_commit, page_signer_no)
                          VALUES ('page', 'MATH.PGREV.T.C', 'high', %L, 1)$f$, :c1),
                '23514', 'content_review_page_commit', '页面审核：页面记录不能是待审');
SELECT t.throws_like(format($f$INSERT INTO kge.content_review (object_type, object_id, risk_level, decision, decided_at, digest, page_commit, page_signer_no)
                          VALUES ('page', 'not a page', 'high', 'approved', now(), %L, %L, 1)$f$, repeat('a', 64), :c1),
                '23514', 'content_review_page_commit', '页面审核：页面 ID 不合写法被拒（表约束）');
SELECT t.throws_like(format($f$INSERT INTO kge.content_review (object_type, object_id, risk_level, page_commit)
                          VALUES ('map_version', 'x', 'high', %L)$f$, :c1),
                '23514', 'content_review_page_commit', '页面审核：非页面记录不能带提交号');
SELECT t.throws_like($f$INSERT INTO kge.content_review (object_type, object_id, risk_level) VALUES ('wiki_page', 'x', 'high')$f$,
                '23514', 'content_review_object_type_check', '页面审核：对象类型仍是白名单');

-- 知识服务（审核台后端）只能提交待审记录，页面记录写不进去
SET ROLE starbuddy_kge;
SELECT t.throws(format($f$INSERT INTO kge.content_review (object_type, object_id, risk_level, decision, decided_at, digest, page_commit, page_signer_no)
                          VALUES ('page', 'MATH.PGREV.T.C', 'high', 'approved', now(), %L, %L, 1)$f$, repeat('a', 64), :c1),
                '42501', '页面审核：知识服务不能直接插入页面批准记录');
SELECT t.throws($$UPDATE kge.content_review SET page_commit = '3333333333333333333333333333333333333333' WHERE object_type = 'page'$$,
                '42501', '页面审核：知识服务不能改页面记录的提交号');
SELECT t.ok((SELECT count(*) FROM kge.page_review_record(NULL)) = 5, '页面审核：知识服务可经导出函数读页面审核记录（只含页面）');
SELECT t.ok((SELECT signer FROM kge.page_review_record(NULL) WHERE page_id = 'MATH.PGREV.T.A' AND page_commit = '1111111111111111111111111111111111111111'
              AND signer_no = 2) = '5e000000-0000-4000-8000-00000000005e',
            '页面审核：导出带签字序号与签字人账号');
RESET ROLE;
SELECT t.throws($$UPDATE kge.content_review SET page_commit = '3333333333333333333333333333333333333333' WHERE object_type = 'page'$$,
                '42501', '页面审核：超级用户也不能改页面记录（只追加）');
SELECT t.throws($$DELETE FROM kge.content_review WHERE object_type = 'page'$$, '42501', '页面审核：页面记录不能删除');
SELECT t.ok(t.as_staff(:sf1, 'SELECT count(*)::text FROM kge.page_review_record(NULL)') = '5', '页面审核：签字身份可读页面审核记录导出');
SELECT t.ok(NOT EXISTS (SELECT 1 FROM kge.version_record('aya') WHERE map_version_id IS NULL), '页面审核：地图版本记录不混入页面记录');
