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
  ["判断可以用未审核模板"]="DO \$\$ DECLARE d text := pg_get_functiondef('insight.claim_guard()'::regprocedure); BEGIN IF position('IF NOT FOUND OR v_tpl.status <> ''approved'' THEN' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, 'IF NOT FOUND OR v_tpl.status <> ''approved'' THEN', 'IF NOT FOUND THEN'); END \$\$; DROP POLICY template_read ON insight.claim_template; CREATE POLICY template_read ON insight.claim_template FOR SELECT TO starbuddy_insight, starbuddy_learning, starbuddy_bff USING (true);"
  ["判断可见范围不受模板上限"]="DO \$\$ DECLARE d text := pg_get_functiondef('insight.claim_guard()'::regprocedure); BEGIN IF position('IF NOT (NEW.visibility <@ v_tpl.max_visibility) THEN' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, 'IF NOT (NEW.visibility <@ v_tpl.max_visibility) THEN', 'IF false THEN'); END \$\$;"
  ["终态判断可以修改"]="DO \$\$ DECLARE d text := pg_get_functiondef('insight.claim_guard()'::regprocedure); BEGIN IF position('IF TG_OP = ''UPDATE'' AND OLD.status IN (''refuted'', ''withdrawn'') THEN' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, 'IF TG_OP = ''UPDATE'' AND OLD.status IN (''refuted'', ''withdrawn'') THEN', 'IF false THEN'); END \$\$;"
  ["判断状态流转不校验"]="CREATE OR REPLACE FUNCTION insight.transition_allowed(p_from insight.claim_status, p_to insight.claim_status, p_reason text) RETURNS boolean LANGUAGE sql IMMUTABLE AS \$\$ SELECT true \$\$;"
  ["推断判断可以不带证据"]="ALTER TABLE insight.claim DROP CONSTRAINT claim_inferred_has_evidence;"
  ["判断可以不设到期"]="ALTER TABLE insight.claim ALTER COLUMN expires_at DROP NOT NULL;"
  ["洞察服务可以自己写历史"]="GRANT INSERT ON insight.claim_history TO starbuddy_insight; CREATE POLICY h ON insight.claim_history FOR INSERT TO starbuddy_insight WITH CHECK (true);"
  ["判断历史可以修改"]="DROP TRIGGER claim_history_guard ON insight.claim_history;"
  ["新建判断不写历史"]="DROP TRIGGER claim_log_insert ON insight.claim;"
  ["学科服务可以写判断"]="GRANT INSERT ON insight.claim TO starbuddy_learning; CREATE POLICY l ON insight.claim FOR INSERT TO starbuddy_learning WITH CHECK (true);"
  ["前端能看到推翻与撤回的判断"]="DROP POLICY claim_front_read ON insight.claim; CREATE POLICY claim_front_read ON insight.claim FOR SELECT TO starbuddy_bff USING ((platform.is_self_student(student_id) AND 'student' = ANY (visibility)) OR (platform.is_linked_guardian(student_id) AND 'guardian' = ANY (visibility)));"
  ["家长能看到仅学生可见的判断"]="DROP POLICY claim_front_read ON insight.claim; CREATE POLICY claim_front_read ON insight.claim FOR SELECT TO starbuddy_bff USING (status IN ('hypothesis', 'established', 'to_verify') AND platform.can_read_student(student_id));"
  ["删除档案不删判断"]="ALTER TABLE insight.claim DROP CONSTRAINT claim_student_id_fkey;"
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
  ["选科任意写"]="GRANT INSERT ON platform.subject_enrollment TO starbuddy_bff; CREATE POLICY enrollment_write ON platform.subject_enrollment FOR INSERT TO starbuddy_bff WITH CHECK (true);"
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
  ["预留不校验学生本人"]="DO \$\$ DECLARE d text := pg_get_functiondef('platform.meter_reserve(uuid, text, text, text, numeric, text, text)'::regprocedure); BEGIN IF position('IF p_student IS NULL OR NOT platform.is_self_student(p_student) THEN' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, 'IF p_student IS NULL OR NOT platform.is_self_student(p_student) THEN', 'IF false THEN'); END \$\$;"
  ["复看不看时间窗口"]="DO \$\$ DECLARE d text := pg_get_functiondef('platform.meter_reserve(uuid, text, text, text, numeric, text, text)'::regprocedure); BEGIN IF position('AND x.finished_at > v_now - rt.dedup_window' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, 'AND x.finished_at > v_now - rt.dedup_window', ''); END \$\$;"
  ["去重键不含学生"]="DO \$\$ DECLARE d text := pg_get_functiondef('platform.meter_reserve(uuid, text, text, text, numeric, text, text)'::regprocedure); BEGIN IF position('p_student::text || ''|'' || ' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, 'p_student::text || ''|'' || ', ''); END \$\$;"
  ["重复结算不幂等"]="DO \$\$ DECLARE d text := pg_get_functiondef('platform.meter_settle(uuid, numeric)'::regprocedure); BEGIN IF position('IF r.state = ''settled'' THEN' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, 'IF r.state = ''settled'' THEN', 'IF false THEN'); END \$\$;"
  ["额度不足也能预留"]="DO \$\$ DECLARE d text := pg_get_functiondef('platform.meter_reserve(uuid, text, text, text, numeric, text, text)'::regprocedure); BEGIN IF position('e.granted + e.fair_use_margin - e.reserved - e.settled >= p_quantity THEN' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, 'e.granted + e.fair_use_margin - e.reserved - e.settled >= p_quantity THEN', 'true THEN'); END \$\$;"
  ["key 不校验格式"]="DO \$\$ DECLARE d text := pg_get_functiondef('platform.meter_reserve(uuid, text, text, text, numeric, text, text)'::regprocedure); BEGIN IF position('IF p_key IS NULL OR p_key !~' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, 'IF p_key IS NULL OR p_key !~', 'IF p_key IS NULL AND p_key !~'); END \$\$;"
  ["续期不应用降级"]="DO \$\$ DECLARE d text := pg_get_functiondef('platform.meter_renew_due(integer)'::regprocedure); BEGIN IF position('tier = coalesce(s.next_tier, s.tier)' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, 'tier = coalesce(s.next_tier, s.tier)', 'tier = s.tier'); END \$\$;"
  ["写入后不核对本桶"]="DO \$\$ DECLARE d text := pg_get_functiondef('platform.meter_check_entitlement(uuid)'::regprocedure); BEGIN IF position('IF e.granted <> v_granted' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, 'IF e.granted <> v_granted', 'IF false AND e.granted <> v_granted'); END \$\$;"
  ["选科不校验上限"]="DO \$\$ DECLARE d text := pg_get_functiondef('platform.enroll_subject(uuid, platform.subject)'::regprocedure); BEGIN IF position('IF v_active >= v_max THEN' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, 'IF v_active >= v_max THEN', 'IF false THEN'); END \$\$;"
  ["选科不校验授权"]="DO \$\$ DECLARE d text := pg_get_functiondef('platform.enroll_subject(uuid, platform.subject)'::regprocedure); BEGIN IF position('IF p_student IS NULL OR p_subject IS NULL OR NOT platform.can_read_student(p_student) THEN' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, 'IF p_student IS NULL OR p_subject IS NULL OR NOT platform.can_read_student(p_student) THEN', 'IF false THEN'); END \$\$;"
  ["暂停选科不校验授权"]="DO \$\$ DECLARE d text := pg_get_functiondef('platform.pause_subject(uuid, platform.subject)'::regprocedure); BEGIN IF position('IF p_student IS NULL OR p_subject IS NULL OR NOT platform.can_read_student(p_student) THEN' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, 'IF p_student IS NULL OR p_subject IS NULL OR NOT platform.can_read_student(p_student) THEN', 'IF false THEN'); END \$\$;"
  ["BFF 可以调用过期任务"]="GRANT EXECUTE ON FUNCTION platform.meter_expire_due(integer) TO starbuddy_bff;"
  ["学习服务可以预留"]="GRANT EXECUTE ON FUNCTION platform.meter_reserve(uuid, text, text, text, numeric, text, text) TO starbuddy_learning;"
  ["函数属主可以改公平使用余量"]="GRANT UPDATE (fair_use_margin) ON platform.entitlement TO starbuddy_authz;"
  ["计量函数不固定 search_path"]="ALTER FUNCTION platform.meter_settle(uuid, numeric) RESET search_path;"
  ["结算释放不校验学生本人"]="DO \$\$ DECLARE d text := pg_get_functiondef('platform.meter_authorize_usage(uuid)'::regprocedure); BEGIN IF position('IF v_student IS NULL OR NOT platform.is_self_student(v_student) THEN' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, 'IF v_student IS NULL OR NOT platform.is_self_student(v_student) THEN', 'IF v_student IS NULL THEN'); END \$\$;"
  ["开通不校验家长"]="DO \$\$ DECLARE d text := pg_get_functiondef('platform.meter_open_subscription(uuid, text)'::regprocedure); BEGIN IF position('IF p_student IS NULL OR NOT platform.is_linked_guardian(p_student) THEN' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, 'IF p_student IS NULL OR NOT platform.is_linked_guardian(p_student) THEN', 'IF false THEN'); END \$\$;"
  ["试点不校验名单"]="DO \$\$ DECLARE d text := pg_get_functiondef('platform.meter_open_subscription(uuid, text)'::regprocedure); BEGIN IF position('IF p_source = ''pilot_virtual'' AND NOT EXISTS (' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, 'IF p_source = ''pilot_virtual'' AND NOT EXISTS (', 'IF false AND NOT EXISTS ('); END \$\$;"
  ["试点档位不固定"]="DO \$\$ DECLARE d text := pg_get_functiondef('platform.meter_open_subscription(uuid, text)'::regprocedure); BEGIN IF position('CASE WHEN p_source = ''trial'' THEN ''basic'' ELSE ''pro'' END' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, 'CASE WHEN p_source = ''trial'' THEN ''basic'' ELSE ''pro'' END', '''max'''); END \$\$;"
  ["同 key 不比请求指纹"]="DO \$\$ DECLARE d text := pg_get_functiondef('platform.meter_same_key(platform.usage_reservation, uuid, text, text, text, text)'::regprocedure); BEGIN IF position(' OR r.request_fingerprint <> p_fingerprint' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, ' OR r.request_fingerprint <> p_fingerprint', ''); END \$\$;"
  ["同 key 不比学生"]="DO \$\$ DECLARE d text := pg_get_functiondef('platform.meter_same_key(platform.usage_reservation, uuid, text, text, text, text)'::regprocedure); BEGIN IF position('r.student_id <> p_student OR ' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, 'r.student_id <> p_student OR ', ''); END \$\$;"
  ["同 key 不比内容指纹"]="DO \$\$ DECLARE d text := pg_get_functiondef('platform.meter_same_key(platform.usage_reservation, uuid, text, text, text, text)'::regprocedure); BEGIN IF position(' OR r.content_fingerprint IS DISTINCT FROM p_content_fingerprint' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, ' OR r.content_fingerprint IS DISTINCT FROM p_content_fingerprint', ''); END \$\$;"
  ["复看不看处理中的首次"]="DO \$\$ DECLARE d text := pg_get_functiondef('platform.meter_reserve(uuid, text, text, text, numeric, text, text)'::regprocedure); BEGIN IF position('AND x.expires_at > v_now) THEN' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, 'AND x.expires_at > v_now) THEN', 'AND false) THEN'); END \$\$;"
  ["资源与方法不校验对应"]="DO \$\$ DECLARE d text := pg_get_functiondef('platform.meter_reserve(uuid, text, text, text, numeric, text, text)'::regprocedure); BEGIN IF position('IF p_source_method IS NULL OR NOT platform.meter_method_allowed(p_resource, p_source_method) THEN' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, 'IF p_source_method IS NULL OR NOT platform.meter_method_allowed(p_resource, p_source_method) THEN', 'IF false THEN'); END \$\$;"
  ["过期后仍可结算"]="DO \$\$ DECLARE d text := pg_get_functiondef('platform.meter_settle(uuid, numeric)'::regprocedure); BEGIN IF position('IF r.expires_at <= v_now THEN' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, 'IF r.expires_at <= v_now THEN', 'IF false THEN'); END \$\$;"
  ["重复释放不幂等"]="DO \$\$ DECLARE d text := pg_get_functiondef('platform.meter_release(uuid, text)'::regprocedure); BEGIN IF position('IF r.state = ''released'' THEN' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, 'IF r.state = ''released'' THEN', 'IF false THEN'); END \$\$;"
  ["续期降级不暂停超额科目"]="DO \$\$ DECLARE d text := pg_get_functiondef('platform.meter_renew_due(integer)'::regprocedure); BEGIN IF position('UPDATE platform.subject_enrollment SET status = ''paused''' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, 'UPDATE platform.subject_enrollment SET status = ''paused''', 'UPDATE platform.subject_enrollment SET status = status'); END \$\$;"
  ["选科不校验学科已上线"]="DO \$\$ DECLARE d text := pg_get_functiondef('platform.enroll_subject(uuid, platform.subject)'::regprocedure); BEGIN IF position('IF NOT EXISTS (SELECT 1 FROM platform.curriculum_version WHERE subject = p_subject AND status = ''published'') THEN' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, 'IF NOT EXISTS (SELECT 1 FROM platform.curriculum_version WHERE subject = p_subject AND status = ''published'') THEN', 'IF false THEN'); END \$\$;"
  ["BFF 可以读流水来源 key"]="GRANT SELECT (source_idempotency_key) ON platform.usage_ledger TO starbuddy_bff;"
  ["BFF 可以读试点名单"]="GRANT SELECT ON platform.pilot_household TO starbuddy_bff; CREATE POLICY pilot_bff ON platform.pilot_household FOR SELECT TO starbuddy_bff USING (true);"
  ["攻击面：新表不开 RLS"]="CREATE TABLE platform.pentest_open (student_id uuid NOT NULL); GRANT SELECT ON platform.pentest_open TO starbuddy_bff; INSERT INTO platform.pentest_open VALUES ('c1000000-0000-4000-8000-0000000000c1'), ('c2000000-0000-4000-8000-0000000000c2');"
  ["攻击面：新表开了 RLS 却没有策略"]="CREATE TABLE platform.pentest_nopolicy (student_id uuid NOT NULL); ALTER TABLE platform.pentest_nopolicy ENABLE ROW LEVEL SECURITY; ALTER TABLE platform.pentest_nopolicy FORCE ROW LEVEL SECURITY; GRANT SELECT ON platform.pentest_nopolicy TO starbuddy_bff; INSERT INTO platform.pentest_nopolicy VALUES ('c1000000-0000-4000-8000-0000000000c1'), ('c2000000-0000-4000-8000-0000000000c2');"
  ["攻击面：新分区授权给 BFF"]="SET TIME ZONE 'UTC'; SELECT platform.ensure_monthly_partitions('platform.evidence_event', DATE '2028-01-01', 1); GRANT SELECT ON platform.evidence_event_202801 TO starbuddy_bff;"
  ["攻击面：SECURITY DEFINER 函数不固定 pg_temp"]="CREATE FUNCTION platform.pentest_definer() RETURNS integer LANGUAGE sql SECURITY DEFINER SET search_path = platform AS \$\$ SELECT 1 \$\$; ALTER FUNCTION platform.pentest_definer() OWNER TO starbuddy_authz; REVOKE EXECUTE ON FUNCTION platform.pentest_definer() FROM PUBLIC;"
  ["攻击面：计量函数对 PUBLIC 开放"]="GRANT EXECUTE ON FUNCTION platform.meter_reserve(uuid, text, text, text, numeric, text, text) TO PUBLIC;"
  ["攻击面：BFF 多了一条 USING (true) 策略"]="CREATE POLICY pentest_open ON platform.mastery_estimate FOR SELECT TO starbuddy_bff USING (true);"
  ["攻击面：BFF 能在 platform 上建对象"]="GRANT CREATE ON SCHEMA platform TO starbuddy_bff;"
  ["攻击面：默认授权给 BFF"]="ALTER DEFAULT PRIVILEGES IN SCHEMA platform GRANT SELECT ON TABLES TO starbuddy_bff;"
  ["攻击面：维度表对 BFF 全开"]="DROP POLICY dim_read ON platform.grade_dimension; CREATE POLICY dim_read ON platform.grade_dimension FOR SELECT TO starbuddy_bff USING (true);"
  ["攻击面：账号表对登录用户全开"]="CREATE POLICY pentest_account ON platform.account FOR SELECT TO starbuddy_bff USING (platform.current_account_id() IS NOT NULL);"
  ["攻击面：家庭表对登录用户全开"]="CREATE POLICY pentest_household ON platform.household FOR SELECT TO starbuddy_bff USING (platform.current_account_id() IS NOT NULL);"
  ["攻击面：可转授的授权"]="GRANT SELECT ON platform.student_profile TO starbuddy_bff WITH GRANT OPTION;"
  ["攻击面：应用角色带 BYPASSRLS"]="ALTER ROLE starbuddy_review BYPASSRLS;"
  ["攻击面：登录角色同时继承定时任务与 BFF"]="CREATE ROLE pentest_both LOGIN IN ROLE starbuddy_scheduler, starbuddy_bff;"
  ["攻击面：函数经 DETAIL 泄露学生是否存在"]="CREATE FUNCTION platform.peek_exists(p_student uuid) RETURNS jsonb
     LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = pg_catalog, platform, pg_temp AS \$\$
     BEGIN
       IF NOT platform.can_read_student(p_student) THEN
         RAISE EXCEPTION 'not permitted' USING ERRCODE = '42501',
           DETAIL = CASE WHEN EXISTS (SELECT 1 FROM platform.student_profile WHERE id = p_student) THEN 'e' ELSE 'n' END;
       END IF;
       RETURN '{}'::jsonb;
     END \$\$;
     ALTER FUNCTION platform.peek_exists(uuid) OWNER TO starbuddy_authz;
     REVOKE ALL ON FUNCTION platform.peek_exists(uuid) FROM PUBLIC;
     GRANT EXECUTE ON FUNCTION platform.peek_exists(uuid) TO starbuddy_bff;"
  ["攻击面：物化视图授权给 BFF"]="CREATE MATERIALIZED VIEW platform.pentest_mv AS SELECT id FROM platform.student_profile; GRANT SELECT ON platform.pentest_mv TO starbuddy_bff;"
  ["攻击面：新函数的学生参数换个名字躲过越权矩阵"]="CREATE FUNCTION platform.peek_student(p_student_id uuid) RETURNS smallint
     LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, platform, pg_temp AS \$\$
     SELECT grade FROM platform.student_profile WHERE id = p_student_id \$\$;
     ALTER FUNCTION platform.peek_student(uuid) OWNER TO starbuddy_authz;
     REVOKE ALL ON FUNCTION platform.peek_student(uuid) FROM PUBLIC;
     GRANT EXECUTE ON FUNCTION platform.peek_student(uuid) TO starbuddy_bff;"
  ["判分不关联作答"]="ALTER TABLE platform.grade_result DROP CONSTRAINT grade_result_attempt_id_attempt_occurred_at_fkey;"
  ["预留对所有登录用户可读"]="DROP POLICY usage_reservation_read ON platform.usage_reservation; CREATE POLICY usage_reservation_read ON platform.usage_reservation FOR SELECT TO starbuddy_bff USING (platform.can_read_student(student_id) OR platform.current_account_id() IS NOT NULL);"
  ["流水不按预留核对"]="DROP TRIGGER guard_ledger_insert ON platform.usage_ledger;"
  ["更正方向不校验"]="DO \$\$ DECLARE d text := pg_get_functiondef('platform.guard_ledger_insert()'::regprocedure); BEGIN IF position('IF sign(NEW.quantity) = sign(v_target) OR NEW.quantity = 0 THEN' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, 'IF sign(NEW.quantity) = sign(v_target) OR NEW.quantity = 0 THEN', 'IF false THEN'); END \$\$;"
  ["更正累计不校验（可重复退款）"]="DO \$\$ DECLARE d text := pg_get_functiondef('platform.guard_ledger_insert()'::regprocedure); BEGIN IF position('IF sign(v_target + v_corrected + NEW.quantity) = -sign(v_target) THEN' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, 'IF sign(v_target + v_corrected + NEW.quantity) = -sign(v_target) THEN', 'IF false THEN'); END \$\$;"
  ["更正目标没有外键"]="ALTER TABLE platform.usage_ledger DROP CONSTRAINT ledger_correction_fk;"
  ["额度桶身份与周期可改"]="DROP TRIGGER guard_entitlement ON platform.entitlement;"
  ["订阅结束后可重新激活"]="DO \$\$ DECLARE d text := pg_get_functiondef('platform.guard_subscription()'::regprocedure); BEGIN IF position('IF OLD.status = ''ended'' THEN' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, 'IF OLD.status = ''ended'' THEN', 'IF false THEN'); END \$\$;"
  ["订阅周期可以回退"]="DO \$\$ DECLARE d text := pg_get_functiondef('platform.guard_subscription()'::regprocedure); BEGIN IF position('IF NEW.period_start < OLD.period_start OR NEW.period_end < OLD.period_end THEN' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, 'IF NEW.period_start < OLD.period_start OR NEW.period_end < OLD.period_end THEN', 'IF false THEN'); END \$\$;"
  ["复看可以跨学生引用"]="ALTER TABLE platform.usage_reservation DROP CONSTRAINT reservation_dedup_same_student_fk;"
  ["复看可以指向没扣费的记录"]="DO \$\$ DECLARE d text := pg_get_functiondef('platform.guard_usage_reservation()'::regprocedure); BEGIN IF position('AND f.settled_qty > 0' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, 'AND f.settled_qty > 0', ''); END \$\$;"
  ["预留数量不受单次上限"]="DO \$\$ DECLARE d text := pg_get_functiondef('platform.guard_usage_reservation()'::regprocedure); BEGIN IF position('IF NEW.reserved_qty > rt.max_per_reservation THEN' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, 'IF NEW.reserved_qty > rt.max_per_reservation THEN', 'IF false THEN'); END \$\$;"
  ["预留过期时间不受配置限制"]="DO \$\$ DECLARE d text := pg_get_functiondef('platform.guard_usage_reservation()'::regprocedure); BEGIN IF position('IF NEW.expires_at > NEW.reserved_at + rt.reservation_ttl THEN' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, 'IF NEW.expires_at > NEW.reserved_at + rt.reservation_ttl THEN', 'IF false THEN'); END \$\$;"
  ["预留可以直接以终态写入"]="DO \$\$ DECLARE d text := pg_get_functiondef('platform.guard_usage_reservation()'::regprocedure); BEGIN IF position('IF NEW.state <> ''reserved'' OR NEW.settled_qty IS NOT NULL OR NEW.finished_at IS NOT NULL THEN' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, 'IF NEW.state <> ''reserved'' OR NEW.settled_qty IS NOT NULL OR NEW.finished_at IS NOT NULL THEN', 'IF false THEN'); END \$\$;"
  ["推进终态时可换额度桶"]="DO \$\$ DECLARE d text := pg_get_functiondef('platform.guard_usage_reservation()'::regprocedure); BEGIN IF position('NEW.request_fingerprint, NEW.entitlement_id,' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, 'NEW.request_fingerprint, NEW.entitlement_id,', 'NEW.request_fingerprint, OLD.entitlement_id,'); END \$\$;"
  ["推进终态时可改过期时间"]="DO \$\$ DECLARE d text := pg_get_functiondef('platform.guard_usage_reservation()'::regprocedure); BEGIN IF position('NEW.reserved_at, NEW.expires_at)' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, 'NEW.reserved_at, NEW.expires_at)', 'NEW.reserved_at, OLD.expires_at)'); END \$\$;"
  ["额度行可在配置间挪动"]="DO \$\$ DECLARE d text := pg_get_functiondef('platform.guard_plan_rows()'::regprocedure); BEGIN IF position('IF TG_OP = ''UPDATE'' AND NEW.config_version <> OLD.config_version THEN' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, 'IF TG_OP = ''UPDATE'' AND NEW.config_version <> OLD.config_version THEN', 'IF false THEN'); END \$\$;"
  ["不完整配置可以发布"]="DO \$\$ DECLARE d text := pg_get_functiondef('platform.guard_plan_config()'::regprocedure); BEGIN IF position('IF OLD.status = ''draft'' AND NEW.status <> ''draft'' THEN' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, 'IF OLD.status = ''draft'' AND NEW.status <> ''draft'' THEN', 'IF false THEN'); END \$\$;"
  ["流水不按种类限定操作者"]="ALTER TABLE platform.usage_ledger DROP CONSTRAINT ledger_actor_by_kind;"
  ["流水可以 TRUNCATE"]="DROP TRIGGER reject_ledger_truncate ON platform.usage_ledger;"
  ["BFF 可以读幂等键"]="GRANT SELECT (idempotency_key) ON platform.usage_reservation TO starbuddy_bff;"
  ["预留对账视图授权给 BFF"]="GRANT SELECT ON platform.reservation_ledger_mismatch TO starbuddy_bff;"
  ["预留对账只数条数不核数量"]="CREATE OR REPLACE VIEW platform.reservation_ledger_mismatch WITH (security_invoker = true) AS SELECT r.usage_id, r.state, count(*) FILTER (WHERE l.kind = 'reserve') AS reserve_entries, count(*) FILTER (WHERE l.kind IN ('settle', 'release', 'expire')) AS terminal_entries FROM platform.usage_reservation r LEFT JOIN platform.usage_ledger l ON l.usage_id = r.usage_id GROUP BY r.usage_id, r.state HAVING count(*) FILTER (WHERE l.kind = 'reserve') <> 1 OR count(*) FILTER (WHERE l.kind IN ('settle', 'release', 'expire')) <> CASE WHEN r.state = 'reserved' THEN 0 ELSE 1 END;"
  ["保护触发器改成 SECURITY DEFINER"]="ALTER FUNCTION platform.guard_entitlement() SECURITY DEFINER;"
  ["流水数量可以是小数"]="ALTER TABLE platform.usage_ledger DROP CONSTRAINT usage_ledger_quantity_check;"
  ["计量流水可以修改"]="DROP TRIGGER reject_ledger_change ON platform.usage_ledger;"
  ["预留终态可以再改"]="DROP TRIGGER guard_usage_reservation ON platform.usage_reservation;"
  ["已发布额度可以改"]="DROP TRIGGER guard_plan_allowance ON platform.plan_allowance;"
  ["已发布配置可以改说明"]="DROP TRIGGER guard_plan_config ON platform.plan_config;"
  ["订阅可以引用草稿配置"]="DO \$\$ DECLARE d text := pg_get_functiondef('platform.guard_subscription()'::regprocedure); BEGIN IF position('AND c.status = ''published''' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, 'AND c.status = ''published''', ''); END \$\$;"
  ["订阅不校验学生属于家庭"]="DO \$\$ DECLARE d text := pg_get_functiondef('platform.guard_subscription()'::regprocedure); BEGIN IF position('AND s.household_id = NEW.household_id' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, 'AND s.household_id = NEW.household_id', ''); END \$\$;"
  ["改订阅可以指向草稿配置"]="DO \$\$ DECLARE d text := pg_get_functiondef('platform.guard_subscription()'::regprocedure); BEGIN IF position('OR NEW.config_version IS DISTINCT FROM OLD.config_version' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, 'OR NEW.config_version IS DISTINCT FROM OLD.config_version', ''); END \$\$;"
  ["一个学生可有多个有效订阅"]="DROP INDEX platform.subscription_one_active_per_student;"
  ["额度可以透支"]="ALTER TABLE platform.entitlement DROP CONSTRAINT entitlement_not_overdrawn;"
  ["额度桶学生可与订阅不一致"]="ALTER TABLE platform.entitlement DROP CONSTRAINT entitlement_subscription_id_student_id_fkey;"
  ["幂等键可以重复"]="ALTER TABLE platform.usage_reservation DROP CONSTRAINT usage_reservation_idempotency_key_key;"
  ["预留可以扣别的资源或别的学生"]="ALTER TABLE platform.usage_reservation DROP CONSTRAINT usage_reservation_entitlement_id_student_id_resource_type_fkey;"
  ["结算可以超过预留"]="ALTER TABLE platform.usage_reservation DROP CONSTRAINT reservation_settle_within_reserved;"
  ["流水可以不带 usage_id"]="ALTER TABLE platform.usage_ledger DROP CONSTRAINT ledger_usage_required;"
  ["更正可以不指目标"]="ALTER TABLE platform.usage_ledger DROP CONSTRAINT ledger_correction_target;"
  ["流水 usage 可属于别的额度桶"]="ALTER TABLE platform.usage_ledger DROP CONSTRAINT usage_ledger_usage_id_entitlement_id_fkey;"
  ["流水加 DEFAULT 分区"]="CREATE TABLE platform.usage_ledger_default PARTITION OF platform.usage_ledger DEFAULT;"
  ["BFF 可以直接写流水"]="GRANT INSERT ON platform.usage_ledger TO starbuddy_bff; CREATE POLICY ledger_bff_ins ON platform.usage_ledger FOR INSERT TO starbuddy_bff WITH CHECK (true);"
  ["BFF 可以直接改额度"]="GRANT UPDATE ON platform.entitlement TO starbuddy_bff; CREATE POLICY ent_bff_upd ON platform.entitlement FOR UPDATE TO starbuddy_bff USING (true) WITH CHECK (true);"
  ["家长可以看所有流水"]="DROP POLICY usage_ledger_read ON platform.usage_ledger; CREATE POLICY usage_ledger_read ON platform.usage_ledger FOR SELECT TO starbuddy_bff USING (true);"
  ["家长可以看所有额度桶"]="DROP POLICY entitlement_read ON platform.entitlement; CREATE POLICY entitlement_read ON platform.entitlement FOR SELECT TO starbuddy_bff USING (true);"
  ["余额视图以属主身份绕过 RLS"]="ALTER VIEW platform.entitlement_balance SET (security_invoker = false);"
  ["BFF 可以读草稿配置"]="DROP POLICY plan_config_read ON platform.plan_config; CREATE POLICY plan_config_read ON platform.plan_config FOR SELECT TO starbuddy_bff USING (true);"
  ["学习服务可以读流水"]="GRANT SELECT ON platform.usage_ledger TO starbuddy_learning; CREATE POLICY ledger_learning ON platform.usage_ledger FOR SELECT TO starbuddy_learning USING (true);"
  ["流水分区授权给 BFF"]="GRANT SELECT ON platform.usage_ledger_202610 TO starbuddy_bff;"
  ["对账不看预留"]="DO \$\$ DECLARE d text := pg_get_viewdef('platform.entitlement_reconciliation'::regclass); BEGIN IF position('(e.reserved <> COALESCE(r.reserved, (0)::numeric)) OR ' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE 'CREATE OR REPLACE VIEW platform.entitlement_reconciliation WITH (security_invoker = true) AS ' || replace(d, '(e.reserved <> COALESCE(r.reserved, (0)::numeric)) OR ', ''); END \$\$;"
  ["对账不认更正"]="DO \$\$ DECLARE d text := pg_get_viewdef('platform.entitlement_reconciliation'::regclass); BEGIN IF position('COALESCE(usage_ledger.corrects_kind, usage_ledger.kind)' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE 'CREATE OR REPLACE VIEW platform.entitlement_reconciliation WITH (security_invoker = true) AS ' || replace(d, 'COALESCE(usage_ledger.corrects_kind, usage_ledger.kind)', 'usage_ledger.kind'); END \$\$;"
)

# 并行执行（每个变异各起一个一次性 Postgres，互不影响）；DBMUT_JOBS 控制并发数，默认等于 CPU 核数
JOBS="${DBMUT_JOBS:-$(nproc 2>/dev/null || echo 2)}"
RES="$(mktemp -d "${TMPDIR:-/tmp}/starbuddy-dbmut.XXXXXX")"
trap 'rm -rf "$RES"' EXIT
# 攻击面变异：只跑 80（在渗透测试夹具上），而且第一条失败的必须是这里登记的断言（安全审查阻断 2）。
# 这样才能证明是 80 本身发现了变异，而不是 20–70 的逐表断言或快照先拦下。
# 除 KEEP_SNAPSHOT 里的以外，快照都按变异后的状态重新生成，不让快照先拦截。
declare -A EXPECT=(
  ["攻击面：新表不开 RLS"]="攻击面：每张表（含分区父表）都 ENABLE + FORCE RLS"
  ["攻击面：新表开了 RLS 却没有策略"]="攻击面：可见性矩阵——"
  ["攻击面：新分区授权给 BFF"]="攻击面：子分区对任何应用角色都没有权限"
  ["攻击面：SECURITY DEFINER 函数不固定 pg_temp"]="攻击面：每个 SECURITY DEFINER 函数"
  ["攻击面：计量函数对 PUBLIC 开放"]="攻击面：每个 SECURITY DEFINER 函数"
  ["攻击面：BFF 多了一条 USING (true) 策略"]="攻击面：可见性矩阵——"
  ["攻击面：BFF 能在 platform 上建对象"]="攻击面：应用角色在任何 schema"
  ["攻击面：默认授权给 BFF"]="攻击面：没有让新建对象自动授权"
  ["攻击面：维度表对 BFF 全开"]="攻击面：可见性矩阵——"
  ["攻击面：账号表对登录用户全开"]="攻击面：可见性矩阵——"
  ["攻击面：家庭表对登录用户全开"]="攻击面：可见性矩阵——"
  ["攻击面：可转授的授权"]="攻击面：授权快照"
  ["攻击面：应用角色带 BYPASSRLS"]="攻击面：应用角色没有危险属性"
  ["攻击面：登录角色同时继承定时任务与 BFF"]="攻击面：没有角色同时继承两个应用角色"
  ["攻击面：函数经 DETAIL 泄露学生是否存在"]="攻击面：函数越权矩阵——"
  ["攻击面：物化视图授权给 BFF"]="攻击面：物化视图与外部表"
  ["攻击面：新函数的学生参数换个名字躲过越权矩阵"]="攻击面：BFF 可执行的 SECURITY DEFINER 函数要么在函数越权矩阵里"
)
declare -A KEEP_SNAPSHOT=(["攻击面：可转授的授权"]=1)
for name in "${!EXPECT[@]}"; do [ -n "${M[$name]+x}" ] || { echo "db-mutation: EXPECT 里的 $name 不是变异"; exit 1; }; done
for name in "${!M[@]}"; do
  case "$name" in 攻击面：*) [ -n "${EXPECT[$name]+x}" ] || { echo "db-mutation: 攻击面变异 $name 没有登记预期断言"; exit 1; } ;; esac
done

run_one() {  # $1 序号，$2 名字
  local out rc first
  if [ -n "${EXPECT[$2]+x}" ]; then
    out=$(DBTEST_ATTACK_ONLY=1 DBTEST_KEEP_SNAPSHOT="${KEEP_SNAPSHOT[$2]:-0}" DBTEST_MUTATION_SQL="${M[$2]}" bash "$HERE/run.sh" 2>&1); rc=$?
    first="$(printf '%s\n' "$out" | grep -m1 'FAIL' || true)"
    if [ $rc -ne 0 ] && [ $rc -ne 3 ] && [[ "$first" != *"FAIL: ${EXPECT[$2]}"* ]]; then
      rc=98; printf '%s\n' "${first:-$(printf '%s\n' "$out" | tail -3)}" > "$RES/why.$1"
    fi
  else
    out=$(DBTEST_MUTATION_SQL="${M[$2]}" bash "$HERE/run.sh" 2>&1); rc=$?
  fi
  if echo "$out" | grep -q '变异 SQL 执行失败'; then rc=97; fi
  printf '%s\t%s\t%s\n' "$rc" "$2" "$1" > "$RES/$1"
}
# DBMUT_FILTER=<前缀>：只跑名字以这个前缀开头的变异（本地调试用；make check 不设）
if [ -n "${DBMUT_FILTER:-}" ]; then
  for name in "${!M[@]}"; do [[ "$name" == "$DBMUT_FILTER"* ]] || unset "M[$name]"; done
fi
i=0
for name in "${!M[@]}"; do
  i=$((i + 1))
  while [ "$(jobs -rp | wc -l)" -ge "$JOBS" ]; do wait -n; done
  run_one "$i" "$name" &
done
wait
missed=0; total=0
while IFS=$'\t' read -r rc name idx; do
  total=$((total + 1))
  if [ "$rc" = "3" ]; then echo "db-mutation: 跳过（本机没有 Postgres）"; exit 3; fi
  if [ "$rc" = "0" ]; then echo "  漏检：$name"; missed=$((missed + 1)); fi
  if [ "$rc" = "97" ]; then echo "  变异本身写错：$name"; missed=$((missed + 1)); fi
  if [ "$rc" = "98" ]; then echo "  不是被预期的断言发现：$name（预期“${EXPECT[$name]}”，实际：$(cat "$RES/why.$idx")）"; missed=$((missed + 1)); fi
done < <(cat "$RES"/[0-9]*)
if [ "$total" -ne "${#M[@]}" ]; then echo "db-mutation: 失败（只收到 $total/${#M[@]} 个结果）"; exit 1; fi
if [ $missed -gt 0 ]; then echo "db-mutation: 失败，$missed/$total 个变异没有被发现"; exit 1; fi
echo "db-mutation: 通过（$total 个变异全部被发现）"
