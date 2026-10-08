-- 考情 v0.9 待人确认事项按默认处理 PR3（0049 platform、0050 policy，specs/policy-decisions-v0.9.1/spec.md“PR3”）：
--   一、客服更正要 staff 权限 cohort_correction；无权限、学生不存在、档案已删除同一个错误（0049）；
--   二、家长导出：profile 带届别与来源，新增更正记录主题（不带操作者账号，0049）；
--   三、来源清单的库层输入核对：xn-- 标签往返解码与对拍用例、不可见字符码位表、来源说明拒绝 Cc / Cf / Zl / Zp（函数与表上 CHECK，0050）；
--   四、url_host 拒绝显式端口；来源文件守卫经属主函数对清单行加共享锁（0050；并发场景在 db/tests/run.sh 场景 14）；
--   五、诊断会话的建立年级 start_grade 与放弃原因 abandon_reason（0049）；
--   六、因届别变化放弃的诊断退回额度 platform.meter_refund_grade_changed（0049）。
-- 合成数据（规则 8）：79y 的合成学生 Y1–Y3（家庭 H1，家长 gA）与合成主机名 *.example；staff 沿用 10 号夹具与 79v、79b 的合成账号。
-- 用 79m 的 t.m_as、76 号的 t.as_staff、79p 的 t.p_bff、79y 的 t.y_try / t.y_correct_sql。
\set sf1 '''5f000000-0000-4000-8000-00000000005f'''
\set sf2 '''f5000000-0000-4000-8000-0000000000f5'''
\set sf3 '''d7000000-0000-4000-8000-0000000000a3'''
\set sfx '''5d000000-0000-4000-8000-00000000005d'''
\set gA '''a0000000-0000-4000-8000-00000000000a'''
\set gB '''b0000000-0000-4000-8000-00000000000b'''
\set Y1 '''f7900000-0000-4000-8000-0000000000c1'''
\set Y2 '''f7900000-0000-4000-8000-0000000000c2'''
\set Y3 '''f7900000-0000-4000-8000-0000000000c3'''
RESET ROLE;
SELECT t.as_account(NULL);

-- ================= 一、客服更正要 cohort_correction（#1） =================
-- 79y 已给 sf1、sfx（停用）授予 cohort_correction；这里给 sf3 只授 policy_source_admin（有别的权限不等于能更正）
INSERT INTO platform.staff_permission (staff_id, permission, granted_by) VALUES (:sf3, 'policy_source_admin', 'synthetic-dba');
SELECT t.throws(format($$INSERT INTO platform.staff_permission (staff_id, permission, granted_by) VALUES (%L, 'cohort_correction', 'synthetic-dba')$$, :gA),
                '23514', 'cohort_correction：家长账号不能有（库层守卫只认 staff）');
SELECT t.ok(t.y_try('starbuddy_review', :sf1, format($$INSERT INTO platform.staff_permission (staff_id, permission, granted_by) VALUES (%L, 'cohort_correction', 'x')$$, :sf2)) LIKE '42501:%',
            'cohort_correction：签字身份不能授予（只由 DBA 授予）');
SELECT exam_cohort AS y2c FROM platform.student_profile WHERE id = :Y2 \gset
-- 同一个错误：没有权限的 active staff（sf2 什么权限都没有、sf3 只有 policy_source_admin）、停用的 staff、家长账号、未设账号对存在的学生；
-- 有权限的 staff 对不存在的学生、空学生；全部与“学生不存在”逐字相同
SELECT t.y_try('starbuddy_review', :sf1, t.y_correct_sql(gen_random_uuid(), :y2c + 1)) AS deny_staff \gset
SELECT t.ok(:'deny_staff' = '42501:not allowed', '客服更正：有权限的 staff 对不存在的学生 → 42501 not allowed（实际：' || :'deny_staff' || '）');
SELECT t.ok(t.y_try('starbuddy_review', :sf2, t.y_correct_sql(:Y2, :y2c + 1)) = :'deny_staff',
            '客服更正：没有 cohort_correction 的 active staff 与“学生不存在”结果相同（消除存在性探测）');
SELECT t.ok(t.y_try('starbuddy_review', :sf3, t.y_correct_sql(:Y2, :y2c + 1)) = :'deny_staff',
            '客服更正：只有 policy_source_admin 的 staff 不能更正（权限按名字核对）');
SELECT t.ok(t.y_try('starbuddy_review', :sfx, t.y_correct_sql(:Y2, :y2c + 1)) = :'deny_staff'
            AND t.y_try('starbuddy_review', :gA, t.y_correct_sql(:Y2, :y2c + 1)) = :'deny_staff'
            AND t.y_try('starbuddy_review', NULL, t.y_correct_sql(:Y2, :y2c + 1)) = :'deny_staff'
            AND t.y_try('starbuddy_review', :sf1, t.y_correct_sql(NULL, :y2c + 1)) = :'deny_staff',
            '客服更正：停用的 staff（即使有权限）、家长账号、未设账号、空学生都是同一个错误');
SELECT t.ok(t.y_try('starbuddy_review', :sf2, t.y_correct_sql(gen_random_uuid(), :y2c + 1)) = :'deny_staff',
            '客服更正：没有权限的 staff 对不存在的学生也是同一个错误');
SELECT count(*) AS y2n FROM platform.exam_cohort_change WHERE student_id = :Y2 \gset
SELECT t.ok((SELECT exam_cohort = :y2c FROM platform.student_profile WHERE id = :Y2) AND :y2n = 0, '客服更正：被拒的调用不改届别、不留记录');
SELECT t.ok(t.m_as('starbuddy_review', :sf1, t.y_correct_sql(:Y2, :y2c - 1)) = (:y2c - 1)::text, '客服更正：有 cohort_correction 的 active staff 能更正');
SELECT t.ok((SELECT exam_cohort = :y2c - 1 AND exam_cohort_source = 'staff_correction' FROM platform.student_profile WHERE id = :Y2)
            AND (SELECT count(*) = 1 AND bool_and(source = 'staff_correction' AND changed_by = :sf1) FROM platform.exam_cohort_change WHERE student_id = :Y2),
            '客服更正：来源记 staff_correction，记录操作者');
SELECT t.ok(t.m_as('starbuddy_bff', :gA, t.y_correct_sql(:Y2, :y2c)) = (:y2c)::text, '家长分支不受 0049 影响：有效绑定家长照常更正');

-- ================= 二、家长导出带届别、来源与更正记录（#2/#11） =================
SELECT t.ok((SELECT export_columns FROM platform.personal_table_registry WHERE nspname = 'platform' AND relname = 'student_profile')
              = ARRAY['grade', 'region', 'birth_year_month', 'created_at', 'exam_cohort', 'exam_cohort_source']
            AND (SELECT export_topic = 'cohort_changes' AND export_columns = ARRAY['old_cohort', 'new_cohort', 'source', 'changed_at']
                        AND export_filter IS NULL AND strategy = 'delete'
                   FROM platform.personal_table_registry WHERE nspname = 'platform' AND relname = 'exam_cohort_change'),
            '导出清单：profile 加届别与来源；更正记录主题只含旧值、新值、来源、时间，不过滤操作者，仍随删除请求删除');
SELECT t.p_bff(:gA, format('SELECT platform.export_student_data(%L)::text', :Y1))::jsonb AS ex \gset
SELECT t.ok((SELECT (p ->> 'exam_cohort')::int = s.exam_cohort AND p ->> 'exam_cohort_source' = s.exam_cohort_source
               FROM jsonb_array_elements(:'ex'::jsonb -> 'topics' -> 'profile') p, platform.student_profile s WHERE s.id = :Y1),
            '导出：profile 主题带当前届别与届别来源');
SELECT t.ok(jsonb_array_length(:'ex'::jsonb -> 'topics' -> 'cohort_changes') = (SELECT count(*) FROM platform.exam_cohort_change WHERE student_id = :Y1)
            AND (SELECT count(DISTINCT source) FROM platform.exam_cohort_change WHERE student_id = :Y1) = 2
            AND (SELECT count(*) FROM platform.exam_cohort_change WHERE student_id = :Y1 AND changed_by <> :gA) > 0,
            '导出：该学生的全部更正都在（家长与客服做的都算，不只本人做的）');
SELECT t.ok(NOT EXISTS (SELECT 1 FROM jsonb_array_elements(:'ex'::jsonb -> 'topics' -> 'cohort_changes') r
                         WHERE (SELECT array_agg(k ORDER BY k) FROM jsonb_object_keys(r) k) <> ARRAY['changed_at', 'new_cohort', 'old_cohort', 'source']),
            '导出：更正记录每行只有旧值、新值、来源、时间');
SELECT t.ok(position(:sf1 IN (:'ex'::jsonb -> 'topics' ->> 'cohort_changes')) = 0 AND position(:gA IN (:'ex'::jsonb -> 'topics' ->> 'cohort_changes')) = 0
            AND position(:sf1 IN :'ex') = 0 AND position('changed_by' IN :'ex') = 0,
            '导出：不含操作者账号（客服与家长的账号 ID 都不出现）');
SELECT t.ok((SELECT string_agg(format('%s>%s:%s', r ->> 'old_cohort', r ->> 'new_cohort', r ->> 'source'), ',' ORDER BY r ->> 'changed_at')
               FROM jsonb_array_elements(:'ex'::jsonb -> 'topics' -> 'cohort_changes') r)
            = (SELECT string_agg(format('%s>%s:%s', old_cohort, new_cohort, source), ',' ORDER BY changed_at) FROM platform.exam_cohort_change WHERE student_id = :Y1),
            '导出：更正记录的值与库里一致');
SELECT t.p_bff(:gA, format('SELECT platform.export_student_data(%L)::text', :Y3))::jsonb AS ex3 \gset
SELECT t.ok(jsonb_array_length(:'ex3'::jsonb -> 'topics' -> 'cohort_changes') = 0, '导出：别的学生的更正记录不进来（Y3 没有更正）');
SELECT t.ok(t.y_try('starbuddy_bff', :gB, format('SELECT platform.export_student_data(%L)::text', :Y1)) LIKE '42501:%',
            '导出：别家的家长仍然导不出（更正记录不因新主题外泄）');

-- ================= 三、来源清单的库层输入核对（#9） =================
SELECT t.ok(policy.normalize_source_host('jyj.xn--fiqs8s.example') = 'jyj.xn--fiqs8s.example'
            AND policy.normalize_source_host('XN--55QX5D.Example.') = 'xn--55qx5d.example'
            AND policy.normalize_source_host('jyj.xn--9ca.example') = 'jyj.xn--9ca.example',
            '主机名：能往返解码的 xn-- 标签照收（中国、公司、é）');
SELECT t.ok(policy.normalize_source_host(h) IS NULL, '主机名：拒绝不能往返解码的 xn-- 标签 ' || h)
  FROM unnest(ARRAY['xn--zzzzzzzzzzzz.example',   -- 一直没有结束数字
                    'xn--99999a.example',         -- 解码出的码位超出 U+10FFFF
                    'xn--a1.example',             -- 解码到一半就没有数字了
                    'xn--abc-9.example',          -- 同上
                    'jyj.xn---9ca.example',       -- 开头的连字符：能解码，但重新编码不同（不是规范形式）
                    'xn--abc.example',            -- 解码出 C1 控制字符
                    'xn--ab-g1t.example',         -- 解码出零宽空格（Cf）
                    'xn--xy-r13a.example']) h;    -- 解码出全角句号（IDNA 里等于点）
SELECT t.ok(NOT policy.idn_label_ok('xn--abc-') AND NOT policy.idn_label_ok('xn--') AND policy.idn_label_ok('jyj'),
            '国际化域名核对：只有基本码位的 xn-- 标签与空标签不收，非 xn-- 标签照收');
-- 对拍边界用例（期望值由 Python 的 punycode 编解码器按同一规则算出后固化在这里；0050 审查意见 6）：
-- 能往返解码的各种书写系统、只有基本码位、开头连字符、截断、溢出、超出 U+10FFFF、零宽与句号变体、大小写前缀、63 / 64 个字符
CREATE TABLE t.z_idn (label text PRIMARY KEY, ok boolean NOT NULL, why text NOT NULL);
INSERT INTO t.z_idn VALUES
  ('xn--fiqs8s', true, '中国'), ('xn--55qx5d', true, '公司'), ('xn--1lqq7i', true, '南京'), ('xn--dgtp5sd2w', true, '教育局'),
  ('xn--9ca', true, 'é'), ('xn--tda', true, 'ü'), ('xn--and-6ma2c', true, 'ñandú（基本码位在前）'), ('xn--a-qga', true, 'ña'),
  ('xn--12-u80dt49a', true, '数学12'), ('xn--ab-qy2c', true, 'a中b'), ('xn--zrich-kva', true, 'zürich'), ('xn--wgv71a119e', true, '日本語'),
  ('xn--3e0b707e', true, '한국'), ('xn--h1ahn', true, 'мир'), ('xn--hxakic4aa', true, 'ελλάδα'), ('xn--mgbcd4a2b0d2b', true, 'العربية'),
  ('xn--ls8h', true, '补充平面码位'), ('xn--x-lq6aaaaaaaaaaaaaaaaaaaa', true, '一个基本码位加 20 个相同汉字'), ('xn--1ca0a', true, '两个拉丁字母'),
  ('XN--fiqs8s', true, '前缀不分大小写'), ('Xn--fiqs8s', true, '前缀不分大小写'),
  ('XN--FIQS8S', false, '编码部分必须是小写（调用方先规范化）'),
  ('xn--abc', false, '解码出 C1 控制字符'), ('xn--abc-', false, '只有基本码位'), ('xn--', false, '空'), ('xn---9ca', false, '开头连字符：重新编码不同'),
  ('xn--9', false, '数字后没有结束位'), ('xn--a1', false, '截断'), ('xn--abc-9', false, '截断'), ('xn--zzzzzzzzzzzz', false, '一直没有结束位'),
  ('xn--99999a', false, '超出 U+10FFFF'), ('xn--ab-g1t', false, '零宽空格'), ('xn--xy-r13a', false, '全角句号'), ('xn--55qx5d-', false, '结尾连字符'),
  ('xn--0', false, '截断'), ('xn--a', false, '截断'),
  ('jyj', true, '非 xn-- 标签'), ('abc-def', true, '非 xn-- 标签'),
  (repeat('a', 63), true, '63 个字符'), (repeat('a', 64), false, '64 个字符'), ('xn--' || repeat('a', 60), false, '64 个字符的 xn-- 标签');
SELECT t.ok(policy.idn_label_ok(label) = ok, format('国际化域名核对对拍：%s → %s（%s）', left(label, 24), ok, why)) FROM t.z_idn ORDER BY label;
-- 不可见字符码位表（0050 审查意见 7）：policy.is_hidden_codepoint 覆盖的码位与 Unicode 15.1 的 Cc、Cf、Zl、Zp 逐个一致（U+0001–U+10FFFF 全量，
-- 跳过代理区；U+0000 text 存不了）。期望清单由 Python unicodedata（14.0）生成，再补 Unicode 15.0 新增的 Cf：U+13439–U+1343F
SELECT range_agg(int4range(lo, hi, '[]')) AS hidden FROM (VALUES
  (0x1, 0x1F), (0x7F, 0x9F), (0xAD, 0xAD), (0x600, 0x605), (0x61C, 0x61C), (0x6DD, 0x6DD), (0x70F, 0x70F), (0x890, 0x891), (0x8E2, 0x8E2),
  (0x180E, 0x180E), (0x200B, 0x200F), (0x2028, 0x202E), (0x2060, 0x2064), (0x2066, 0x206F), (0xFEFF, 0xFEFF), (0xFFF9, 0xFFFB),
  (0x110BD, 0x110BD), (0x110CD, 0x110CD), (0x13430, 0x1343F), (0x1BCA0, 0x1BCA3), (0x1D173, 0x1D17A), (0xE0001, 0xE0001), (0xE0020, 0xE007F)
) v(lo, hi) \gset
SELECT t.ok(NOT EXISTS (SELECT 1 FROM generate_series(1, 0x10FFFF) cp
                         WHERE (cp < 0xD800 OR cp > 0xDFFF) AND policy.is_hidden_codepoint(cp) IS DISTINCT FROM (cp <@ :'hidden'::int4multirange)),
            '不可见字符码位表与 Unicode Cc、Cf、Zl、Zp 逐个一致（全量）');
SELECT t.ok(NOT EXISTS (SELECT 1 FROM (SELECT DISTINCT b FROM unnest(:'hidden'::int4multirange) r,
                                       LATERAL unnest(ARRAY[lower(r) - 1, lower(r), upper(r) - 1, upper(r)]) b) x
                         WHERE b BETWEEN 1 AND 0x10FFFF AND (b < 0xD800 OR b > 0xDFFF)
                           AND policy.has_hidden_chars('合' || chr(b) || '成') IS DISTINCT FROM (b <@ :'hidden'::int4multirange)),
            '来源说明的核对按字符生效：每个区间的两端与两端之外一个码位都判对');
SELECT t.ok(NOT policy.has_hidden_chars('') AND NOT policy.has_hidden_chars('合成 教育局（Ａ）·二〇二六'),
            '空串与普通中文、空格、全角字母、中点不算不可见字符');
SELECT t.ok(NOT EXISTS (SELECT 1 FROM unnest(ARRAY['starbuddy_bff', 'starbuddy_learning', 'starbuddy_review', 'starbuddy_kge', 'starbuddy_insight']) r(role)
                         WHERE has_function_privilege(r.role, 'policy.idn_label_ok(text)', 'EXECUTE')),
            '国际化域名核对函数只授属主 authz');
SELECT t.ok(t.y_try('starbuddy_review', :sf1, $$SELECT policy.add_source_domain('xn--ab-g1t.example', '合成')$$) LIKE '22023:%',
            '来源清单：新增时拒绝不能往返解码的主机名（22023）');
SELECT t.ok(t.m_as('starbuddy_review', :sf1, $$SELECT policy.add_source_domain('jyj.xn--fiqs8s.example', '合成：国际化域名')$$) = 'jyj.xn--fiqs8s.example',
            '来源清单：能往返解码的国际化域名可以登记');
-- 来源说明：Cc（含 C1）、Cf（零宽、方向覆盖、软连字符）、Zl、Zp 一律 22023，不写清单也不留记录
SELECT t.ok(t.y_try('starbuddy_review', :sf1, format('SELECT policy.add_source_domain(''jyj.notes.example'', %L)', n)) LIKE '22023:%不能含控制字符%',
            '来源说明：拒绝 U+' || upper(to_hex(ascii(c))) || '（' || cat || '）')
  FROM (VALUES (chr(10), 'Cc'), (chr(127), 'Cc'), (chr(133), 'Cc C1'), (chr(173), 'Cf 软连字符'), (chr(8203), 'Cf 零宽空格'),
               (chr(8238), 'Cf 方向覆盖'), (chr(8294), 'Cf 方向隔离'), (chr(65279), 'Cf BOM'), (chr(917505), 'Cf 语言标记'),
               (chr(8232), 'Zl'), (chr(8233), 'Zp')) v(c, cat),
       LATERAL (SELECT '合成' || c || '教育局' AS n) x;
SELECT t.ok(NOT EXISTS (SELECT 1 FROM policy.source_domain WHERE host = 'jyj.notes.example')
            AND NOT EXISTS (SELECT 1 FROM platform.policy_source_domain_change WHERE host = 'jyj.notes.example'),
            '来源说明：被拒的新增不写清单、不留记录');
SELECT t.ok(t.m_as('starbuddy_review', :sf1, $$SELECT policy.add_source_domain('jyj.notes.example', '合成 教育局（Ａ）·二〇二六')$$) = 'jyj.notes.example',
            '来源说明：普通空格、全角字母、中点照收');
-- 兜底：迁移或 DBA 直接写（绕过函数）也被表上的 CHECK 拦住
SELECT t.throws_like(format($$INSERT INTO policy.source_domain (host, issuer, added_by) VALUES ('jyj.dba.example', %L, 'synthetic-dba')$$, '合成' || chr(8238) || '局'),
                     '23514', 'source_domain_issuer_visible', '来源说明：直接写清单也拦（CHECK 兜底）');
SELECT t.throws_like(format($$UPDATE policy.source_domain SET issuer = %L WHERE host = 'jyj.notes.example'$$, '合成' || chr(8203)),
                     '23514', 'source_domain_issuer_visible', '来源说明：直接改清单行也拦');
SELECT t.throws_like(format($$INSERT INTO platform.policy_source_domain_change (host, action, note, actor_account_id, changed_at)
                              VALUES ('jyj.dba.example', 'add', %L, %L, now())$$, '合成' || chr(8233), :sf1),
                     '23514', 'policy_source_domain_change_note_visible', '来源说明：直接写操作记录也拦（CHECK 兜底）');

-- ================= 四、url_host 拒绝端口；来源文件守卫的共享锁（#13） =================
SELECT t.ok(policy.url_host('https://jyj.synthcity.example:443/doc/1') IS NULL
            AND policy.url_host('https://jyj.synthcity.example:/doc') IS NULL
            AND policy.url_host('https://jyj.synthcity.example:8443') IS NULL,
            'url_host：带显式端口（含空端口）返回 NULL');
SELECT t.ok(policy.url_host('https://JYJ.Synthcity.Example/doc/1') = 'jyj.synthcity.example'
            AND policy.url_host('https://jyj.synthcity.example') = 'jyj.synthcity.example'
            AND policy.url_host('https://jyj.synthcity.example?x=1') = 'jyj.synthcity.example'
            AND policy.url_host('https://jyj.synthcity.example#a') = 'jyj.synthcity.example'
            AND policy.url_host('http://jyj.synthcity.example/doc') IS NULL,
            'url_host：没有端口时照旧（小写；路径、查询、片段之前为止；只认 https）');
SET ROLE starbuddy_kge;
SELECT t.throws_like($$INSERT INTO policy.source_document (city, issuer, doc_number, title, published_on, url, applies_from_cohort, last_verified_on)
                       VALUES ('synthcity', '合成教育局', '合成〔2027〕90号', '合成', '2027-03-01', 'https://jyj.synthcity.example:443/doc/90', 2028, '2027-09-01')$$,
                     '23514', '官方来源清单', '来源文件：带端口的链接被守卫拒绝（主机名取不出）');
RESET ROLE;
SELECT t.ok((SELECT array_agg(r.role ORDER BY r.role) FROM unnest(ARRAY['starbuddy_bff', 'starbuddy_learning', 'starbuddy_review', 'starbuddy_kge',
                                                                      'starbuddy_insight', 'starbuddy_scheduler', 'starbuddy_authz']) r(role)
              WHERE has_function_privilege(r.role, 'policy.lock_active_source_host(text)', 'EXECUTE')) = '{starbuddy_authz,starbuddy_kge}'
            AND (SELECT prosecdef AND proowner = 'starbuddy_authz'::regrole FROM pg_proc WHERE oid = 'policy.lock_active_source_host(text)'::regprocedure),
            '清单行共享锁：属主 authz 的 SECURITY DEFINER 函数，只授写入方 kge');
SELECT t.ok(NOT has_table_privilege('starbuddy_kge', 'policy.source_domain', 'UPDATE')
            AND NOT has_any_column_privilege('starbuddy_kge', 'policy.source_domain', 'UPDATE'),
            '清单行共享锁：写入方仍然没有清单的 UPDATE 权限（锁经函数取）');
SELECT t.ok(t.m_as('starbuddy_kge', NULL, $$SELECT policy.lock_active_source_host('jyj.synthcity.example')::text$$) = 'true'
            AND t.m_as('starbuddy_kge', NULL, $$SELECT policy.lock_active_source_host('jyj.nowhere.example')::text$$) = 'false',
            '清单行共享锁：在清单里且 active 返回 true，否则 false');

-- ================= 五、诊断会话的建立年级（#12） =================
SELECT t.ok(has_column_privilege('starbuddy_learning', 'platform.diagnosis_session', 'start_grade', 'INSERT')
            AND NOT has_column_privilege('starbuddy_learning', 'platform.diagnosis_session', 'start_grade', 'UPDATE')
            AND NOT has_column_privilege('starbuddy_bff', 'platform.diagnosis_session', 'start_grade', 'SELECT'),
            '诊断会话 start_grade：学习服务只在建立时写，不能改；BFF 读不到');
BEGIN;
SELECT t.throws(format($$INSERT INTO platform.diagnosis_session (student_id, subject, algorithm_version, map_version, usage_id, idempotency_key, start_grade)
                         VALUES (%L, 'aya', 'aya-diag-trace@0.1.0', '0.2.0', gen_random_uuid(), gen_random_uuid(), 6)$$, :Y2),
                '23514', '诊断会话 start_grade：只收 7–9');
ROLLBACK;
SELECT t.ok(has_column_privilege('starbuddy_learning', 'platform.diagnosis_session', 'abandon_reason', 'UPDATE')
            AND NOT has_column_privilege('starbuddy_bff', 'platform.diagnosis_session', 'abandon_reason', 'SELECT')
            AND NOT has_column_privilege('starbuddy_bff', 'platform.diagnosis_session', 'abandon_reason', 'UPDATE'),
            '诊断会话 abandon_reason：学习服务写，BFF 读不到也改不了');
BEGIN;
INSERT INTO platform.diagnosis_session (id, student_id, subject, algorithm_version, map_version, usage_id, idempotency_key, start_grade)
VALUES ('f7990000-0000-4000-8000-0000000000a1', :Y2, 'aya', 'aya-diag-trace@0.1.0', '0.2.0', gen_random_uuid(), gen_random_uuid(), 8);
SELECT t.throws_like($$UPDATE platform.diagnosis_session SET abandon_reason = 'grade_changed' WHERE id = 'f7990000-0000-4000-8000-0000000000a1'$$,
                     '23514', 'diagnosis_session_abandon_reason_status', '诊断会话：放弃原因只能与已放弃同时写（进行中的会话不能带原因）');
SELECT t.throws($$UPDATE platform.diagnosis_session SET status = 'abandoned', finished_at = now(), abandon_reason = 'curfew'
                   WHERE id = 'f7990000-0000-4000-8000-0000000000a1'$$, '23514', '诊断会话：放弃原因只收 grade_changed');
UPDATE platform.diagnosis_session SET status = 'abandoned', finished_at = now(), abandon_reason = 'grade_changed'
 WHERE id = 'f7990000-0000-4000-8000-0000000000a1';
SELECT t.throws($$UPDATE platform.diagnosis_session SET abandon_reason = NULL WHERE id = 'f7990000-0000-4000-8000-0000000000a1'$$,
                '22023', '诊断会话：放弃之后原因不能再改（0027 的结束守卫）');
ROLLBACK;

-- ================= 六、因届别变化放弃的诊断退回额度（0049） =================
-- 70 号的合成学生 c1（学生账号 a1，有 diagnosis_full 额度）；家长 a0 不是本人。预留经 70 号的 t.reserve（BFF 角色、学生本人）
\set c1 '''c1000000-0000-4000-8000-0000000000c1'''
\set a1 '''a1000000-0000-4000-8000-0000000000a1'''
\set a0 '''a0000000-0000-4000-8000-00000000000a'''
CREATE FUNCTION t.z_refund(p_acct uuid, p_key uuid) RETURNS text LANGUAGE sql AS $$
  SELECT t.y_try('starbuddy_bff', p_acct, format('SELECT platform.meter_refund_grade_changed(platform.meter_usage_id(%L))::text', p_key)) $$;
CREATE FUNCTION t.z_refund_out(p_acct uuid, p_key uuid) RETURNS jsonb LANGUAGE sql AS $$
  SELECT t.m_as('starbuddy_bff', p_acct, format('SELECT platform.meter_refund_grade_changed(platform.meter_usage_id(%L))::text', p_key))::jsonb $$;
-- 一笔预留挂到一个诊断会话上，再放弃（学习服务写会话，这里以超级用户模拟；新建只能是进行中，放弃再单独 UPDATE）。
-- reason 为空表示不是因届别变化放弃。p_changed：建立时记下的选题年级与按当前届别重算的年级不同（届别确实变过）；
-- p_idem：会话的幂等键（缺省与预留同一个键，BFF 开始诊断时两边是同一个）
CREATE FUNCTION t.z_session(p_key uuid, p_reason text, p_changed boolean DEFAULT true, p_idem uuid DEFAULT NULL,
                            p_student uuid DEFAULT 'c1000000-0000-4000-8000-0000000000c1') RETURNS text LANGUAGE plpgsql AS $$
DECLARE v_id uuid := gen_random_uuid(); g smallint;
BEGIN
  g := platform.current_grade((SELECT exam_cohort FROM platform.student_profile WHERE id = p_student), now());
  INSERT INTO platform.diagnosis_session (id, student_id, subject, algorithm_version, map_version, usage_id, idempotency_key, start_grade)
  VALUES (v_id, p_student, 'zhixing', 'aya-diag-trace@0.1.0', '0.2.0', platform.meter_usage_id(p_key::text),
          coalesce(p_idem, p_key),
          CASE WHEN p_changed THEN (CASE WHEN g = 7 THEN 8 ELSE 7 END) ELSE coalesce(g, 9) END);
  UPDATE platform.diagnosis_session SET status = 'abandoned', finished_at = now(), abandon_reason = p_reason WHERE id = v_id;
  RETURN 'ok';
END $$;
CREATE FUNCTION t.z_bucket() RETURNS text LANGUAGE sql AS $$
  SELECT format('%s/%s/%s', e.granted, e.reserved, e.settled) FROM platform.entitlement e
   WHERE e.id = (SELECT r.entitlement_id FROM platform.usage_reservation r WHERE r.usage_id = platform.meter_usage_id('b0b00000-0000-4000-8000-000000007902')) $$;
\set k1 '''b0b00000-0000-4000-8000-000000007901'''
\set k2 '''b0b00000-0000-4000-8000-000000007902'''
\set k3 '''b0b00000-0000-4000-8000-000000007903'''
SELECT t.ok(t.reserve(:a1, :c1, 'diagnosis_full', :k1, repeat('7', 64), 1, NULL)->>'outcome' = 'reserved'
            AND t.reserve(:a1, :c1, 'diagnosis_full', :k2, repeat('7', 64), 1, NULL)->>'outcome' = 'reserved'
            AND t.reserve(:a1, :c1, 'diagnosis_full', :k3, repeat('7', 64), 1, NULL)->>'outcome' = 'reserved',
            '退回额度夹具：学生本人预留三次诊断');
SELECT t.ok(t.m_as('starbuddy_bff', :a1, format('SELECT platform.meter_settle(platform.meter_usage_id(%L), 1)->>''outcome''', :k2)) = 'settled'
            AND t.m_as('starbuddy_bff', :a1, format('SELECT platform.meter_settle(platform.meter_usage_id(%L), 1)->>''outcome''', :k3)) = 'settled',
            '退回额度夹具：第二、三笔答过题已结算');
SELECT t.z_session(:k1, 'grade_changed'), t.z_session(:k2, 'grade_changed'), t.z_session(:k3, NULL);
SELECT t.z_bucket() AS bucket0 \gset
-- 没有答过题（预留未结算）：释放
-- （调用与查结果分两条语句：同一条语句里的子查询看不到函数刚做的修改）
SELECT t.ok(t.z_refund_out(:a1, :k1) @> '{"outcome": "released"}', '退回额度：因届别变化放弃、还没结算的预留直接释放');
SELECT t.ok((SELECT state = 'released' FROM platform.usage_reservation WHERE usage_id = platform.meter_usage_id(:k1)),
            '退回额度：释放后预留状态是 released');
-- 答过题（已结算）：写一条 adjust 流水把次数加回，granted 同步；再调一次是幂等的
SELECT t.ok(t.z_refund_out(:a1, :k2) @> '{"outcome": "refunded", "idempotent": false, "quantity": 1}',
            '退回额度：因届别变化放弃、已结算的退回 1 次（即使答过题）');
SELECT t.ok(t.z_refund_out(:a1, :k2) @> '{"outcome": "refunded", "idempotent": true}'
            AND (SELECT count(*) FROM platform.usage_ledger WHERE kind = 'adjust' AND reason_code = 'grade_changed_refund'
                   AND source_idempotency_key = (:k2)::text) = 1,
            '退回额度：重复调用幂等，只有一条退回流水');
SELECT t.ok((SELECT actor = 'system' AND quantity = 1 AND usage_id IS NULL AND source_method = 'diagnose'
               FROM platform.usage_ledger WHERE kind = 'adjust' AND reason_code = 'grade_changed_refund' AND source_idempotency_key = (:k2)::text),
            '退回额度：流水是系统调整（不是人工更正），带这笔预留的来源方法与幂等键以便回溯');
SELECT t.ok(split_part(t.z_bucket(), '/', 1)::int = split_part(:'bucket0', '/', 1)::int + 1
            AND split_part(t.z_bucket(), '/', 3) = split_part(:'bucket0', '/', 3)
            AND split_part(t.z_bucket(), '/', 2)::int = split_part(:'bucket0', '/', 2)::int - 1,
            '退回额度：额度桶 granted 加回 1、已结算不变、释放的那笔不再占用（' || :'bucket0' || ' → ' || t.z_bucket() || '）');
SELECT t.ok(NOT EXISTS (SELECT 1 FROM platform.entitlement_reconciliation) AND NOT EXISTS (SELECT 1 FROM platform.reservation_ledger_mismatch),
            '退回额度：计数与流水对账一致');
-- 不是因届别变化放弃的：不退（其余放弃规则不变）；不是本人：与不存在同样 42501
SELECT t.ok(t.z_refund_out(:a1, :k3) @> '{"outcome": "conflict", "reason": "not_refundable"}',
            '退回额度：普通放弃（没有放弃原因）的已结算预留不退');
SELECT t.ok(t.z_refund(:a0, :k2) LIKE '42501:%' AND t.z_refund(:a0, :k2) = t.z_refund(:a1, gen_random_uuid()),
            '退回额度：家长不能替孩子调；与不存在的 usage_id 同样 42501');
SELECT t.ok(t.y_try('starbuddy_learning', :a1, format('SELECT platform.meter_refund_grade_changed(platform.meter_usage_id(%L))::text', :k2)) LIKE '42501:%'
            AND t.y_try('starbuddy_review', :a1, format('SELECT platform.meter_refund_grade_changed(platform.meter_usage_id(%L))::text', :k2)) LIKE '42501:%',
            '退回额度：只授 BFF（学习服务、签字身份不能调）');

-- ---- 退回额度专项安全审查（B1、S1、S3、S4） ----
-- B1：库里自己核实，不只信学习服务写的放弃原因
SELECT t.ok(policy_ok, '诊断会话：' || what) FROM (VALUES
  (t.y_try('starbuddy_learning', NULL, format($q$INSERT INTO platform.diagnosis_session (student_id, subject, status, finished_at, algorithm_version, map_version,
     usage_id, idempotency_key, start_grade, abandon_reason) VALUES (%L, 'zhixing', 'abandoned', now(), 'aya-diag-trace@0.1.0', '0.2.0', gen_random_uuid(),
     gen_random_uuid(), 9, 'grade_changed') RETURNING 'x'$q$, :c1)) LIKE '23514:%只能以进行中新建%', '新建时就是已放弃（带放弃原因）被拒'),
  (t.y_try('starbuddy_learning', NULL, format($q$INSERT INTO platform.diagnosis_session (student_id, subject, status, finished_at, algorithm_version, map_version,
     usage_id, idempotency_key) VALUES (%L, 'zhixing', 'finished', now(), 'aya-diag-trace@0.1.0', '0.2.0', gen_random_uuid(), gen_random_uuid())
     RETURNING 'x'$q$, :c1)) LIKE '23514:%只能以进行中新建%', '新建时就是已结束被拒')
) v(policy_ok, what);
\set k4 '''b0b00000-0000-4000-8000-000000007904'''
SELECT t.ok(t.y_try('postgres', NULL, format('SELECT t.z_session(%L, ''grade_changed'', false)', :k4)) LIKE '23514:%与库里的届别不符%',
            '诊断会话：届别没有变过（重算年级等于建立时记下的）就不能写 grade_changed');
BEGIN;
INSERT INTO platform.diagnosis_session (id, student_id, subject, algorithm_version, map_version, usage_id, idempotency_key)
VALUES ('f7990000-0000-4000-8000-0000000000a2', :Y2, 'zhixing', 'aya-diag-trace@0.1.0', '0.2.0', gen_random_uuid(), gen_random_uuid());
SELECT t.ok(platform.current_grade((SELECT exam_cohort FROM platform.student_profile WHERE id = :Y2), now()) IS NOT NULL,
            '诊断会话夹具：Y2 当前在 7–9（这样“没记下选题年级”才是唯一的拒绝理由）');
SELECT t.throws_like($$UPDATE platform.diagnosis_session SET status = 'abandoned', finished_at = now(), abandon_reason = 'grade_changed'
                       WHERE id = 'f7990000-0000-4000-8000-0000000000a2'$$, '23514', '与库里的届别不符',
                     '诊断会话：没有记下选题年级（0049 之前的会话）不能写 grade_changed');
ROLLBACK;
-- 非诊断预留（同一学生的 diagnosis_screen 额度桶）、会话幂等键与预留不同：都不退
\set k5 '''b0b00000-0000-4000-8000-000000007905'''
\set k6 '''b0b00000-0000-4000-8000-000000007906'''
SELECT t.ok(t.reserve(:a1, :c1, 'diagnosis_screen', :k5, repeat('7', 64), 1, NULL)->>'outcome' = 'reserved'
            AND t.reserve(:a1, :c1, 'diagnosis_full', :k6, repeat('7', 64), 1, NULL)->>'outcome' = 'reserved',
            '退回额度夹具：一笔 diagnosis_screen、一笔 diagnosis_full');
SELECT t.ok(t.m_as('starbuddy_bff', :a1, format('SELECT platform.meter_settle(platform.meter_usage_id(%L), 1)->>''outcome''', :k5)) = 'settled'
            AND t.m_as('starbuddy_bff', :a1, format('SELECT platform.meter_settle(platform.meter_usage_id(%L), 1)->>''outcome''', :k6)) = 'settled',
            '退回额度夹具：两笔都已结算');
SELECT t.z_session(:k5, 'grade_changed'), t.z_session(:k6, 'grade_changed', true, gen_random_uuid());
SELECT t.ok(t.z_refund_out(:a1, :k5) @> '{"outcome": "conflict", "reason": "not_refundable"}',
            '退回额度：挂在非诊断额度桶（diagnosis_screen）上的预留不退');
SELECT t.ok(t.z_refund_out(:a1, :k6) @> '{"outcome": "conflict", "reason": "not_refundable"}',
            '退回额度：会话的幂等键与预留不同（不是这笔预留开的会话）不退');
-- 会话是别的学生的（usage_id 对上了也不行）：c1 的预留挂到 79y 的合成学生 Y2 的会话上
\set k13 '''b0b00000-0000-4000-8000-000000007913'''
SELECT t.ok(t.reserve(:a1, :c1, 'diagnosis_full', :k13, repeat('7', 64), 1, NULL)->>'outcome' = 'reserved'
            AND t.m_as('starbuddy_bff', :a1, format('SELECT platform.meter_settle(platform.meter_usage_id(%L), 1)->>''outcome''', :k13)) = 'settled',
            '退回额度夹具：c1 一笔已结算的诊断');
SELECT t.z_session(:k13, 'grade_changed', true, NULL, :Y2);
SELECT t.ok(t.z_refund_out(:a1, :k13) @> '{"outcome": "conflict", "reason": "not_refundable"}',
            '退回额度：挂在别的学生的会话上不退（会话与预留必须是同一学生）');
-- 退回时再核一次：放弃之后届别又改回去（重算年级等于建立时的），不退
\set k7 '''b0b00000-0000-4000-8000-000000007907'''
SELECT t.ok(t.reserve(:a1, :c1, 'diagnosis_full', :k7, repeat('7', 64), 1, NULL)->>'outcome' = 'reserved', '退回额度夹具：再一笔诊断');
SELECT t.z_session(:k7, 'grade_changed');
BEGIN;
-- 让当前届别推算出的建立时刻年级等于会话记下的选题年级（模拟“届别改回来了”）：按记下的年级反推届别
UPDATE platform.student_profile SET exam_cohort = platform.expected_cohort(
         (SELECT start_grade FROM platform.diagnosis_session WHERE usage_id = platform.meter_usage_id(:k7)), now()),
       exam_cohort_source = CASE WHEN exam_cohort_source = 'staff_correction' THEN 'guardian_report' ELSE 'staff_correction' END
 WHERE id = :c1;
SELECT t.ok(t.z_refund_out(:a1, :k7) @> '{"outcome": "conflict", "reason": "not_refundable"}',
            '退回额度：退回时库里再核一次，届别已改回（建立时刻的年级没变）就不退');
ROLLBACK;
-- S4：已释放、已过期、结算数为 0 的不退（没有扣过）
\set k8 '''b0b00000-0000-4000-8000-000000007908'''
\set k9 '''b0b00000-0000-4000-8000-000000007909'''
\set k10 '''b0b00000-0000-4000-8000-000000007910'''
SELECT t.ok(t.reserve(:a1, :c1, 'diagnosis_full', :k8, repeat('7', 64), 1, NULL)->>'outcome' = 'reserved'
            AND t.reserve(:a1, :c1, 'diagnosis_full', :k9, repeat('7', 64), 1, NULL)->>'outcome' = 'reserved'
            AND t.reserve(:a1, :c1, 'diagnosis_full', :k10, repeat('7', 64), 1, NULL)->>'outcome' = 'reserved',
            '退回额度夹具：再三笔诊断');
SELECT t.ok(t.m_as('starbuddy_bff', :a1, format('SELECT platform.meter_release(platform.meter_usage_id(%L), ''abandoned'')->>''outcome''', :k8)) = 'released'
            AND t.m_as('starbuddy_bff', :a1, format('SELECT platform.meter_settle(platform.meter_usage_id(%L), 0)->>''outcome''', :k10)) = 'settled',
            '退回额度夹具：一笔已释放、一笔结算数为 0');
SELECT now AS clock0 FROM t.clock \gset
UPDATE t.clock SET now = now + interval '11 minutes';
SELECT t.m_as('starbuddy_scheduler', NULL, 'SELECT platform.meter_expire_due(50)::text') AS expired \gset
UPDATE t.clock SET now = :'clock0';
SELECT t.ok((SELECT state = 'expired' FROM platform.usage_reservation WHERE usage_id = platform.meter_usage_id(:k9)), '退回额度夹具：一笔已过期');
SELECT t.z_session(:k8, 'grade_changed'), t.z_session(:k9, 'grade_changed'), t.z_session(:k10, 'grade_changed');
SELECT t.ok(t.z_refund_out(:a1, k) @> '{"outcome": "conflict", "reason": "nothing_to_refund"}', '退回额度：' || what || '的不退（没有扣过）')
  FROM (VALUES ((:k8)::uuid, '已释放'), ((:k9)::uuid, '已过期'), ((:k10)::uuid, '结算数为 0')) v(k, what);
-- S3：余量 = settle 减去已有更正；人工已更正掉的不再退
\set k11 '''b0b00000-0000-4000-8000-000000007911'''
SELECT t.ok(t.reserve(:a1, :c1, 'diagnosis_full', :k11, repeat('7', 64), 1, NULL)->>'outcome' = 'reserved'
            AND t.m_as('starbuddy_bff', :a1, format('SELECT platform.meter_settle(platform.meter_usage_id(%L), 1)->>''outcome''', :k11)) = 'settled',
            '退回额度夹具：一笔已结算的诊断');
-- 人工更正（客服已退过这一次：0008 的更正只由人工，这里以超级用户模拟）
INSERT INTO platform.usage_ledger (occurred_at, entitlement_id, student_id, kind, quantity, corrects_entry_id, corrects_occurred_at, corrects_kind,
                                   actor, reason_code)
SELECT l.occurred_at, l.entitlement_id, l.student_id, 'correction', -l.quantity, l.entry_id, l.occurred_at, 'settle', 'staff', 'manual_refund'
  FROM platform.usage_ledger l WHERE l.usage_id = platform.meter_usage_id(:k11) AND l.kind = 'settle';
UPDATE platform.entitlement SET settled = settled - 1
 WHERE id = (SELECT entitlement_id FROM platform.usage_reservation WHERE usage_id = platform.meter_usage_id(:k11));
SELECT t.z_session(:k11, 'grade_changed');
SELECT t.ok(t.z_refund_out(:a1, :k11) @> '{"outcome": "conflict", "reason": "nothing_to_refund"}',
            '退回额度：settle 已被人工更正掉（余量 0）就不再退');
-- S1：每个学生每个额度桶最多退 1 次（k2 已在本额度桶退过）
\set k12 '''b0b00000-0000-4000-8000-000000007912'''
SELECT t.ok(t.reserve(:a1, :c1, 'diagnosis_full', :k12, repeat('7', 64), 1, NULL)->>'outcome' = 'reserved'
            AND t.m_as('starbuddy_bff', :a1, format('SELECT platform.meter_settle(platform.meter_usage_id(%L), 1)->>''outcome''', :k12)) = 'settled',
            '退回额度夹具：同一额度桶再一笔已结算的诊断');
SELECT t.z_session(:k12, 'grade_changed');
SELECT t.ok(t.z_refund_out(:a1, :k12) @> '{"outcome": "conflict", "reason": "limit"}',
            '退回额度：同一学生同一额度周期第二次因届别变化放弃，不再退（上限 1 次）');
SELECT t.ok((SELECT count(*) FROM platform.usage_ledger WHERE student_id = :c1 AND kind = 'adjust' AND reason_code = 'grade_changed_refund') = 1
            AND NOT EXISTS (SELECT 1 FROM platform.entitlement_reconciliation) AND NOT EXISTS (SELECT 1 FROM platform.reservation_ledger_mismatch),
            '退回额度：整个过程只退过 1 次，计数与流水对账一致');
