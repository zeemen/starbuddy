#!/usr/bin/env bash
# 数据库变异测试：逐条放宽一条 RLS 策略或约束，断言 run.sh 会失败。
# 证明 RLS 测试和约束测试确实在守这些规则，而不是恰好通过。
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"

declare -A M=(
  ["学习服务可以自己结案"]="GRANT UPDATE (status, resolved_at, resolved_by_account, resolution_code) ON platform.review_task TO starbuddy_learning; CREATE POLICY rt_upd ON platform.review_task FOR UPDATE TO starbuddy_learning USING (true) WITH CHECK (true);"
  ["学习服务可以插入已结案任务"]="DROP POLICY review_service_open ON platform.review_task; CREATE POLICY review_service_open ON platform.review_task FOR INSERT TO starbuddy_learning WITH CHECK (true);"
  ["结案不校验教研身份"]="DO \$\$ DECLARE d text := pg_get_functiondef('platform.resolve_review_task(uuid, text)'::regprocedure); BEGIN IF position('AND a.kind = ''staff''' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, 'AND a.kind = ''staff''', ''); END \$\$;"
  ["更正放行不看结案人身份"]="DO \$\$ DECLARE d text := pg_get_functiondef('platform.check_supersede_target()'::regprocedure); BEGIN IF position('AND a.kind = ''staff''' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, 'AND a.kind = ''staff''', ''); END \$\$;"
  ["插入时可以是已被取代"]="DROP TRIGGER guard_inference_insert ON platform.grade_result; DROP TRIGGER guard_inference_insert ON platform.mastery_estimate; DROP TRIGGER guard_inference_insert ON platform.error_attribution;"
  ["更正可以成环"]="DO \$\$ DECLARE d text := pg_get_functiondef('platform.check_supersede_target()'::regprocedure); BEGIN IF position('WHERE n.id = NEW.superseded_by AND n.superseded_by IS NULL
                     AND n.student_id = OLD.student_id AND n.subject = OLD.subject
                     AND n.attempt_id' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, 'WHERE n.id = NEW.superseded_by AND n.superseded_by IS NULL
                     AND n.student_id = OLD.student_id AND n.subject = OLD.subject
                     AND n.attempt_id', 'WHERE n.id = NEW.superseded_by
                     AND n.student_id = OLD.student_id AND n.subject = OLD.subject
                     AND n.attempt_id'); END \$\$;"
  ["学生档案对所有 BFF 请求可读"]="DROP POLICY student_profile_read ON platform.student_profile; CREATE POLICY student_profile_read ON platform.student_profile FOR SELECT TO starbuddy_bff USING (true);"
  ["授权改成按手机号关联"]="CREATE OR REPLACE FUNCTION platform.is_linked_guardian(p_student uuid) RETURNS boolean
     LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, platform, pg_temp AS \$\$
     SELECT EXISTS (SELECT 1 FROM platform.guardian_student_link l WHERE l.student_id = p_student AND l.status = 'active'
                     AND l.guardian_account_id = platform.current_account_id())
         OR EXISTS (SELECT 1 FROM platform.student_profile s JOIN platform.account_contact c1 ON c1.account_id = s.account_id
                    JOIN platform.account_contact c2 ON c2.phone_e164 = c1.phone_e164
                    WHERE s.id = p_student AND c2.account_id = platform.current_account_id()) \$\$;"
  ["学生可以写掌握度"]="GRANT INSERT, UPDATE ON platform.mastery_estimate TO starbuddy_bff;
     CREATE POLICY mastery_self_write ON platform.mastery_estimate FOR ALL TO starbuddy_bff
       USING (platform.is_self_student(student_id)) WITH CHECK (platform.is_self_student(student_id));"
  ["学生可以读家长自述"]="CREATE POLICY greport_student ON platform.guardian_report FOR SELECT TO starbuddy_bff USING (platform.is_self_student(student_id));"
  ["解除绑定后仍可访问"]="CREATE OR REPLACE FUNCTION platform.is_linked_guardian(p_student uuid) RETURNS boolean
     LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, platform, pg_temp AS \$\$
     SELECT EXISTS (SELECT 1 FROM platform.guardian_student_link l WHERE l.student_id = p_student
                     AND l.guardian_account_id = platform.current_account_id()) \$\$;"
  ["学习服务可以读联系方式"]="GRANT SELECT ON platform.account_contact TO starbuddy_learning; CREATE POLICY contact_service ON platform.account_contact FOR SELECT TO starbuddy_learning USING (true);"
  ["写入幂等表成功后仍可修改"]="DROP TRIGGER guard_ingest_request ON platform.ingest_request;"
  ["写入幂等表的指纹可以改"]="GRANT UPDATE (request_fingerprint) ON platform.ingest_request TO starbuddy_learning;"
  ["BFF 可以读写入幂等表"]="GRANT SELECT ON platform.ingest_request TO starbuddy_bff; CREATE POLICY ingest_bff ON platform.ingest_request FOR SELECT TO starbuddy_bff USING (true);"
  ["证据登记可以修改"]="GRANT UPDATE ON platform.evidence_registry TO starbuddy_learning;"
  ["证据 ID 不再全局唯一"]="ALTER TABLE platform.evidence_registry DROP CONSTRAINT evidence_registry_pkey;"
  ["证据 payload 不校验"]="ALTER TABLE platform.evidence_event DROP CONSTRAINT evidence_payload_shape;"
  ["总分可以超过满分"]="ALTER TABLE platform.grade_result DROP CONSTRAINT grade_total_le_max;"
  ["主观维度可以高置信"]="ALTER TABLE platform.grade_dimension DROP CONSTRAINT dim_subjective_low_conf;"
  ["主观维度不要求父判分复核"]="DROP TRIGGER grade_dimension_requires_review ON platform.grade_dimension;"
  ["推断可以不带证据"]="ALTER TABLE platform.error_attribution DROP CONSTRAINT error_attribution_evidence_ids_check;"
  ["BFF 可以读全部手机号"]="GRANT SELECT ON platform.account_contact TO starbuddy_bff; CREATE POLICY c ON platform.account_contact FOR SELECT TO starbuddy_bff USING (true);"
  ["BFF 可以直接写联系方式"]="GRANT INSERT ON platform.account_contact TO starbuddy_bff; CREATE POLICY c ON platform.account_contact FOR INSERT TO starbuddy_bff WITH CHECK (true);"
  ["BFF 可以直接建账号"]="GRANT INSERT ON platform.account TO starbuddy_bff; CREATE POLICY a ON platform.account FOR INSERT TO starbuddy_bff WITH CHECK (true);"
  ["任何账号都算家长"]="CREATE OR REPLACE FUNCTION platform.is_active_guardian() RETURNS boolean
     LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, platform, pg_temp AS \$\$
     SELECT platform.current_account_id() IS NOT NULL \$\$;"
  ["BFF 可以直接建学生档案"]="GRANT INSERT ON platform.student_profile TO starbuddy_bff; CREATE POLICY p ON platform.student_profile FOR INSERT TO starbuddy_bff WITH CHECK (true);"
  ["BFF 可以直接建家庭"]="GRANT INSERT ON platform.household TO starbuddy_bff; CREATE POLICY h ON platform.household FOR INSERT TO starbuddy_bff WITH CHECK (true);"
  ["家长可以直接插入绑定"]="GRANT INSERT ON platform.guardian_student_link TO starbuddy_bff; CREATE POLICY l ON platform.guardian_student_link FOR INSERT TO starbuddy_bff WITH CHECK (guardian_account_id = platform.current_account_id());"
  ["解除绑定不再作废邀请、不再锁定 revoked_at"]="DROP TRIGGER guard_link_update ON platform.guardian_student_link; GRANT UPDATE (revoked_at) ON platform.guardian_student_link TO starbuddy_bff;"
  ["禁用账号仍可读"]="CREATE OR REPLACE FUNCTION platform.is_self_student(p_student uuid) RETURNS boolean
     LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, platform, pg_temp AS \$\$
     SELECT EXISTS (SELECT 1 FROM platform.student_profile s WHERE s.id = p_student AND s.account_id = platform.current_account_id()) \$\$;"
  ["更正不再单向"]="DROP TRIGGER guard_supersede ON platform.skill_observation; DROP TRIGGER guard_supersede ON platform.mastery_estimate; DROP TRIGGER guard_supersede ON platform.grade_result;"
  ["更正不校验目标"]="DROP TRIGGER check_supersede_target ON platform.skill_observation; DROP TRIGGER check_supersede_target ON platform.grade_result;"
  ["同意记录任意写"]="DROP POLICY consent_write ON platform.consent_record; CREATE POLICY consent_write ON platform.consent_record FOR INSERT TO starbuddy_bff WITH CHECK (true);"
  ["同意记录全表可读"]="DROP POLICY consent_read ON platform.consent_record; CREATE POLICY consent_read ON platform.consent_record FOR SELECT TO starbuddy_bff USING (true);"
  ["选科任意写"]="DROP POLICY enrollment_write ON platform.subject_enrollment; CREATE POLICY enrollment_write ON platform.subject_enrollment FOR INSERT TO starbuddy_bff WITH CHECK (true);"
  ["选科全表可读"]="DROP POLICY enrollment_read ON platform.subject_enrollment; CREATE POLICY enrollment_read ON platform.subject_enrollment FOR SELECT TO starbuddy_bff USING (true);"
  ["任意解除绑定"]="DROP POLICY link_revoke ON platform.guardian_student_link; CREATE POLICY link_revoke ON platform.guardian_student_link FOR UPDATE TO starbuddy_bff USING (true) WITH CHECK (true);"
  ["绑定全表可读"]="DROP POLICY link_read ON platform.guardian_student_link; CREATE POLICY link_read ON platform.guardian_student_link FOR SELECT TO starbuddy_bff USING (true);"
  ["账号全表可读"]="DROP POLICY account_self_read ON platform.account; CREATE POLICY account_self_read ON platform.account FOR SELECT TO starbuddy_bff USING (true);"
  ["未发布课程可见"]="DROP POLICY cv_public_read ON platform.curriculum_version; CREATE POLICY cv_public_read ON platform.curriculum_version FOR SELECT TO starbuddy_bff USING (true);"
  ["未准入题目可见"]="DROP POLICY item_read ON platform.item; CREATE POLICY item_read ON platform.item FOR SELECT TO starbuddy_bff USING (true);"
  ["作答全表可读"]="DROP POLICY attempt_read ON platform.attempt; CREATE POLICY attempt_read ON platform.attempt FOR SELECT TO starbuddy_bff USING (true);"
  ["观测全表可读"]="DROP POLICY obs_read ON platform.skill_observation; CREATE POLICY obs_read ON platform.skill_observation FOR SELECT TO starbuddy_bff USING (true);"
  ["错因全表可读"]="DROP POLICY attr_read ON platform.error_attribution; CREATE POLICY attr_read ON platform.error_attribution FOR SELECT TO starbuddy_bff USING (true);"
  ["判分全表可读"]="DROP POLICY grade_read ON platform.grade_result; CREATE POLICY grade_read ON platform.grade_result FOR SELECT TO starbuddy_bff USING (true);"
  ["判分维度全表可读"]="DROP POLICY dim_read ON platform.grade_dimension; CREATE POLICY dim_read ON platform.grade_dimension FOR SELECT TO starbuddy_bff USING (true);"
  ["家长自述任意删除"]="DROP POLICY greport_delete ON platform.guardian_report; CREATE POLICY greport_delete ON platform.guardian_report FOR DELETE TO starbuddy_bff USING (true);"
  ["家长自述别的家长可读"]="DROP POLICY greport_own ON platform.guardian_report; CREATE POLICY greport_own ON platform.guardian_report FOR SELECT TO starbuddy_bff USING (platform.is_linked_guardian(student_id));"
  ["学习服务可以改掌握状态"]="GRANT UPDATE (state) ON platform.mastery_estimate TO starbuddy_learning;"
  ["学习服务可以改复核标记"]="GRANT UPDATE (human_review_required, review_reasons) ON platform.grade_result TO starbuddy_learning;"
  ["学习服务可以改证据"]="GRANT UPDATE (payload, admission) ON platform.evidence_event TO starbuddy_learning;"
  ["BFF 可以写观测与错因"]="GRANT INSERT ON platform.skill_observation, platform.error_attribution TO starbuddy_bff; CREATE POLICY x ON platform.skill_observation FOR INSERT TO starbuddy_bff WITH CHECK (true); CREATE POLICY y ON platform.error_attribution FOR INSERT TO starbuddy_bff WITH CHECK (true);"
  ["BFF 可以读复核队列"]="GRANT SELECT ON platform.review_task TO starbuddy_bff; CREATE POLICY r ON platform.review_task FOR SELECT TO starbuddy_bff USING (true);"
  ["兑换不校验过期"]="DO \$\$ DECLARE d text := pg_get_functiondef('platform.redeem_guardian_invite(text)'::regprocedure); BEGIN IF position('OR v_invite.expires_at <= now()' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, 'OR v_invite.expires_at <= now()', ''); END \$\$;"
  ["兑换不校验签发人是否仍有权限"]="DO \$\$ DECLARE d text := pg_get_functiondef('platform.redeem_guardian_invite(text)'::regprocedure); BEGIN IF position('OR NOT (' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, 'OR NOT (', 'OR false AND NOT ('); END \$\$;"
  ["签发人可以兑换自己的邀请"]="DO \$\$ DECLARE d text := pg_get_functiondef('platform.redeem_guardian_invite(text)'::regprocedure); BEGIN IF position('OR v_invite.created_by_account = v_me' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, 'OR v_invite.created_by_account = v_me', ''); END \$\$;"
  ["create_student 不检查家庭创建者"]="DO \$\$ DECLARE d text := pg_get_functiondef('platform.create_student(uuid, smallint, text, text, date)'::regprocedure); BEGIN IF position('ELSIF NOT EXISTS' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, 'ELSIF NOT EXISTS', 'ELSIF false AND NOT EXISTS'); END \$\$;"
  ["邀请有效期不设上限"]="DO \$\$ DECLARE d text := pg_get_functiondef('platform.create_guardian_invite(uuid, interval)'::regprocedure); BEGIN IF position('OR p_ttl > interval ''7 days''' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, 'OR p_ttl > interval ''7 days''', ''); END \$\$;"
  ["needs_review 约束删除"]="ALTER TABLE platform.grade_result DROP CONSTRAINT grade_needs_review;"
  ["验证器原因码约束删除"]="ALTER TABLE platform.grade_result DROP CONSTRAINT grade_verifier_boundary;"
  ["原因码不限取值"]="ALTER TABLE platform.grade_result DROP CONSTRAINT grade_reason_codes_known;"
  ["同一能力点可有多条当前掌握度"]="DROP INDEX platform.mastery_estimate_current_uq;"
  ["证据加 DEFAULT 分区"]="CREATE TABLE platform.evidence_event_default PARTITION OF platform.evidence_event DEFAULT;"
  ["对象存储引用不限格式"]="ALTER TABLE platform.evidence_event DROP CONSTRAINT evidence_event_media_ref_check;"
  ["分区授权给 BFF"]="GRANT SELECT ON platform.evidence_event_202610 TO starbuddy_bff;"
  ["判分不关联作答"]="ALTER TABLE platform.grade_result DROP CONSTRAINT grade_result_attempt_id_attempt_occurred_at_fkey;"
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
