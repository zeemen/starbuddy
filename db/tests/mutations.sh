#!/usr/bin/env bash
# 数据库变异测试：逐条放宽一条 RLS 策略或约束，断言 run.sh 会失败。
# 证明 RLS 测试和约束测试确实在守这些规则，而不是恰好通过。
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"

declare -A M=(
  ["学生档案对所有 BFF 请求可读"]="DROP POLICY student_profile_read ON platform.student_profile;
     CREATE POLICY student_profile_read ON platform.student_profile FOR SELECT TO starbuddy_bff USING (true);"
  ["授权改成按手机号关联"]="CREATE OR REPLACE FUNCTION platform.is_linked_guardian(p_student uuid) RETURNS boolean
     LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, platform AS \$\$
     SELECT EXISTS (SELECT 1 FROM platform.guardian_student_link l WHERE l.student_id = p_student AND l.status = 'active'
                     AND l.guardian_account_id = platform.current_account_id())
         OR EXISTS (SELECT 1 FROM platform.student_profile s JOIN platform.account_contact c1 ON c1.account_id = s.account_id
                    JOIN platform.account_contact c2 ON c2.phone_e164 = c1.phone_e164
                    WHERE s.id = p_student AND c2.account_id = platform.current_account_id()) \$\$;
     GRANT SELECT ON platform.account_contact TO starbuddy_authz;
     CREATE POLICY contact_authz ON platform.account_contact FOR SELECT TO starbuddy_authz USING (true);"
  ["家长可以直接插入任意绑定"]="DROP POLICY link_household_creator ON platform.guardian_student_link;
     CREATE POLICY link_household_creator ON platform.guardian_student_link FOR INSERT TO starbuddy_bff
       WITH CHECK (guardian_account_id = platform.current_account_id());"
  ["学生可以写掌握度"]="GRANT INSERT, UPDATE ON platform.mastery_estimate TO starbuddy_bff;
     CREATE POLICY mastery_self_write ON platform.mastery_estimate FOR ALL TO starbuddy_bff
       USING (platform.is_self_student(student_id)) WITH CHECK (platform.is_self_student(student_id));"
  ["学生可以读家长自述"]="CREATE POLICY greport_student ON platform.guardian_report FOR SELECT TO starbuddy_bff
       USING (platform.is_self_student(student_id));"
  ["解除绑定后仍可访问"]="CREATE OR REPLACE FUNCTION platform.is_linked_guardian(p_student uuid) RETURNS boolean
     LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, platform AS \$\$
     SELECT EXISTS (SELECT 1 FROM platform.guardian_student_link l WHERE l.student_id = p_student
                     AND l.guardian_account_id = platform.current_account_id()) \$\$;"
  ["学习服务可以读联系方式"]="GRANT SELECT ON platform.account_contact TO starbuddy_learning;
     CREATE POLICY contact_service ON platform.account_contact FOR SELECT TO starbuddy_learning USING (true);"
  ["证据 payload 不校验"]="ALTER TABLE platform.evidence_event DROP CONSTRAINT evidence_payload_shape;"
  ["总分可以超过满分"]="ALTER TABLE platform.grade_result DROP CONSTRAINT grade_total_le_max;"
  ["主观维度可以高置信"]="ALTER TABLE platform.grade_dimension DROP CONSTRAINT dim_subjective_low_conf;"
  ["主观维度不要求父判分复核"]="DROP TRIGGER grade_dimension_requires_review ON platform.grade_dimension;"
  ["推断可以不带证据"]="ALTER TABLE platform.error_attribution DROP CONSTRAINT error_attribution_evidence_ids_check;"
  ["邀请可以重复使用"]="CREATE OR REPLACE FUNCTION platform.redeem_guardian_invite(p_token text) RETURNS uuid
     LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog, platform AS \$\$
     DECLARE v platform.guardian_invite%ROWTYPE; v_link uuid;
     BEGIN
       IF NOT EXISTS (SELECT 1 FROM platform.account WHERE id = platform.current_account_id() AND kind = 'guardian') THEN
         RAISE EXCEPTION 'no' USING ERRCODE = 'insufficient_privilege'; END IF;
       SELECT * INTO v FROM platform.guardian_invite WHERE token_sha256 = sha256(convert_to(p_token, 'UTF8'));
       IF NOT FOUND THEN RAISE EXCEPTION 'no' USING ERRCODE = 'invalid_parameter_value'; END IF;
       INSERT INTO platform.guardian_student_link (guardian_account_id, student_id, granted_via)
       VALUES (platform.current_account_id(), v.student_id, 'invite')
       ON CONFLICT (guardian_account_id, student_id) WHERE status = 'active' DO NOTHING RETURNING id INTO v_link;
       RETURN coalesce(v_link, gen_random_uuid());
     END \$\$;"
)

missed=0; total=0
for name in "${!M[@]}"; do
  total=$((total + 1))
  out=$(DBTEST_MUTATION_SQL="${M[$name]}" bash "$HERE/run.sh" 2>&1); rc=$?
  if [ $rc -eq 3 ]; then echo "db-mutation: 跳过（本机没有 Postgres）"; exit 3; fi
  if [ $rc -eq 0 ]; then echo "  漏检：$name"; missed=$((missed + 1)); fi
  if echo "$out" | grep -q '变异 SQL 执行失败'; then echo "  变异本身写错：$name"; missed=$((missed + 1)); fi
done
if [ $missed -gt 0 ]; then echo "db-mutation: 失败，$missed/$total 个变异没有被发现"; exit 1; fi
echo "db-mutation: 通过（$total 个变异全部被发现）"
