#!/usr/bin/env bash
# 数据库变异测试：逐条放宽一条 RLS 策略或约束，断言 run.sh 会失败。
# 证明 RLS 测试和约束测试确实在守这些规则，而不是恰好通过。
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"

declare -A M=(
  ["计量v2：分钟类可跨进宵禁"]="DO \$\$ DECLARE d text := pg_get_functiondef('platform.meter_reserve_impl(uuid, text, text, text, numeric, text, text, boolean)'::regprocedure); BEGIN IF position('AND p_quantity > platform.meter_minutes_until_curfew(p_student, v_now) THEN' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, 'AND p_quantity > platform.meter_minutes_until_curfew(p_student, v_now) THEN', 'AND false THEN'); END \$\$;"
  ["计量v2：BFF 可读时间设置的设置人"]="GRANT SELECT (updated_by) ON platform.student_time_guard TO starbuddy_bff;"
  ["计量v2：单日用量含已过期预留"]="DO \$\$ DECLARE d text := pg_get_functiondef('platform.meter_minutes_today(uuid, timestamptz)'::regprocedure); BEGIN IF position('(r.state = ''reserved'' AND r.expires_at > p_at)' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, '(r.state = ''reserved'' AND r.expires_at > p_at)', '(r.state = ''reserved'')'); END \$\$;"
  ["计量v2：选修槽不限"]="DO \$\$ DECLARE d text := pg_get_functiondef('platform.meter_slot_limit(text, platform.plan_tier, boolean)'::regprocedure); BEGIN IF position('ELSE f.value_code::integer END' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, 'ELSE f.value_code::integer END', 'ELSE NULL END'); END \$\$;"
  ["计量v2：留存态可用 AI"]="DO \$\$ DECLARE d text := pg_get_functiondef('platform.meter_reserve_impl(uuid, text, text, text, numeric, text, text, boolean)'::regprocedure); BEGIN IF position('IF platform.meter_state_of(p_student) = ''retained'' THEN' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, 'IF platform.meter_state_of(p_student) = ''retained'' THEN', 'IF false THEN'); END \$\$;"
  ["计量v2：宵禁不拦"]="DO \$\$ DECLARE d text := pg_get_functiondef('platform.meter_reserve_impl(uuid, text, text, text, numeric, text, text, boolean)'::regprocedure); BEGIN IF position('IF rt.curfew_applies AND platform.meter_in_curfew(p_student, v_now) THEN' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, 'IF rt.curfew_applies AND platform.meter_in_curfew(p_student, v_now) THEN', 'IF false THEN'); END \$\$;"
  ["计量v2：单日上限不拦"]="DO \$\$ DECLARE d text := pg_get_functiondef('platform.meter_reserve_impl(uuid, text, text, text, numeric, text, text, boolean)'::regprocedure); BEGIN IF position('IF v_used + p_quantity > v_cap THEN' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, 'IF v_used + p_quantity > v_cap THEN', 'IF false THEN'); END \$\$;"
  ["计量v2：单日用量不含预留中"]="DO \$\$ DECLARE d text := pg_get_functiondef('platform.meter_minutes_today(uuid, timestamptz)'::regprocedure); BEGIN IF position('(r.state = ''settled'' OR (r.state = ''reserved'' AND r.expires_at > p_at))' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, '(r.state = ''settled'' OR (r.state = ''reserved'' AND r.expires_at > p_at))', '(r.state = ''settled'')'); END \$\$;"
  ["计量v2：单日按 UTC 换日"]="DO \$\$ DECLARE d text := pg_get_functiondef('platform.meter_minutes_today(uuid, timestamptz)'::regprocedure); BEGIN IF position('((p_at AT TIME ZONE ''Asia/Shanghai'')::date::timestamp AT TIME ZONE ''Asia/Shanghai'')' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, '((p_at AT TIME ZONE ''Asia/Shanghai'')::date::timestamp AT TIME ZONE ''Asia/Shanghai'')', 'date_trunc(''day'', p_at)'); END \$\$;"
  ["计量v2：家长可调高上限"]="DO \$\$ DECLARE d text := pg_get_functiondef('platform.set_time_guard(uuid, integer, time, time)'::regprocedure); BEGIN IF position('OR p_daily_cap > v_plan_cap' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, 'OR p_daily_cap > v_plan_cap', 'OR false'); END \$\$;"
  ["计量v2：宵禁可放宽"]="DO \$\$ DECLARE d text := pg_get_functiondef('platform.set_time_guard(uuid, integer, time, time)'::regprocedure); BEGIN IF position('AND NOT platform.meter_in_window(time ''00:00'' + m * interval ''1 minute'', p_curfew_start, p_curfew_end) THEN' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, 'AND NOT platform.meter_in_window(time ''00:00'' + m * interval ''1 minute'', p_curfew_start, p_curfew_end) THEN', 'AND false THEN'); END \$\$;"
  ["计量v2：学生可改时间设置"]="DO \$\$ DECLARE d text := pg_get_functiondef('platform.set_time_guard(uuid, integer, time, time)'::regprocedure); BEGIN IF position('NOT platform.is_linked_guardian(p_student)' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, 'NOT platform.is_linked_guardian(p_student)', 'NOT platform.can_read_student(p_student)'); END \$\$;"
  ["计量v2：同一家庭可重复领试用"]="DO \$\$ DECLARE d text := pg_get_functiondef('platform.meter_open_subscription(uuid, text)'::regprocedure); BEGIN IF position('IF EXISTS (SELECT 1 FROM platform.trial_claim WHERE household_id = v_household)' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, 'IF EXISTS (SELECT 1 FROM platform.trial_claim WHERE household_id = v_household)', 'IF false'); END \$\$;"
  ["计量v2：同一手机号可重复领试用"]="DO \$\$ DECLARE d text := pg_get_functiondef('platform.meter_open_subscription(uuid, text)'::regprocedure); BEGIN IF position('OR EXISTS (SELECT 1 FROM platform.account_contact ac' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, 'OR EXISTS (SELECT 1 FROM platform.account_contact ac', 'OR false AND EXISTS (SELECT 1 FROM platform.account_contact ac'); END \$\$;"
  ["计量v2：试用 30 天"]="DO \$\$ DECLARE d text := pg_get_functiondef('platform.meter_open_subscription(uuid, text)'::regprocedure); BEGIN IF position('interval ''14 days''' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, 'interval ''14 days''', 'interval ''30 days'''); END \$\$;"
  ["计量v2：留存每天 6 次自练"]="DO \$\$ DECLARE d text := pg_get_functiondef('platform.meter_practice(uuid, text)'::regprocedure); BEGIN IF position('IF v_used >= 5 THEN' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, 'IF v_used >= 5 THEN', 'IF v_used >= 6 THEN'); END \$\$;"
  ["计量v2：自练按 UTC 换日"]="DO \$\$ DECLARE d text := pg_get_functiondef('platform.meter_practice(uuid, text)'::regprocedure); BEGIN IF position('v_day_start := (v_now AT TIME ZONE ''Asia/Shanghai'')::date::timestamp AT TIME ZONE ''Asia/Shanghai'';' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, 'v_day_start := (v_now AT TIME ZONE ''Asia/Shanghai'')::date::timestamp AT TIME ZONE ''Asia/Shanghai'';', 'v_day_start := date_trunc(''day'', v_now);'); END \$\$;"
  ["计量v2：上传不按页折算"]="DO \$\$ DECLARE d text := pg_get_functiondef('platform.meter_reserve_upload(uuid, text, text, text, integer)'::regprocedure); BEGIN IF position('ceil(p_pages / 4.0)' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, 'ceil(p_pages / 4.0)', '1'); END \$\$;"
  ["计量v2：上传可走通用预留"]="DO \$\$ DECLARE d text := pg_get_functiondef('platform.meter_reserve_impl(uuid, text, text, text, numeric, text, text, boolean)'::regprocedure); BEGIN IF position('IF (p_resource = ''upload'') <> p_via_upload THEN' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, 'IF (p_resource = ''upload'') <> p_via_upload THEN', 'IF false THEN'); END \$\$;"
  ["计量v2：存储可带插件方法"]="DO \$\$ DECLARE d text := pg_get_functiondef('platform.meter_reserve_impl(uuid, text, text, text, numeric, text, text, boolean)'::regprocedure); BEGIN IF position('    IF p_source_method IS NOT NULL THEN
      RAISE EXCEPTION ''% is not tied' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, '    IF p_source_method IS NOT NULL THEN
      RAISE EXCEPTION ''% is not tied', '    IF false THEN
      RAISE EXCEPTION ''% is not tied'); END \$\$;"
  ["计量v2：beta 学科对所有订阅开放"]="DO \$\$ DECLARE d text := pg_get_functiondef('platform.enroll_subject(uuid, platform.subject)'::regprocedure); BEGIN IF position('IF c.status = ''beta'' AND s.source <> ''pilot_virtual'' THEN' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, 'IF c.status = ''beta'' AND s.source <> ''pilot_virtual'' THEN', 'IF false THEN'); END \$\$;"
  ["计量v2：换科即时生效"]="DO \$\$ DECLARE d text := pg_get_functiondef('platform.enroll_subject(uuid, platform.subject)'::regprocedure); BEGIN IF position('IF v_limit IS NOT NULL AND v_active + v_frozen >= v_limit THEN' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, 'IF v_limit IS NOT NULL AND v_active + v_frozen >= v_limit THEN', 'IF false THEN'); END \$\$;"
  ["计量v2：每周期可换下多科"]="DO \$\$ DECLARE d text := pg_get_functiondef('platform.pause_subject(uuid, platform.subject)'::regprocedure); BEGIN IF position('AND e.paused_at >= s.period_start) THEN' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, 'AND e.paused_at >= s.period_start) THEN', 'AND false) THEN'); END \$\$;"
  ["计量v2：任选 1 科不限"]="DO \$\$ DECLARE d text := pg_get_functiondef('platform.meter_slot_limit(text, platform.plan_tier, boolean)'::regprocedure); BEGIN IF position('WHEN f.value_code = ''pick_one'' THEN 1' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, 'WHEN f.value_code = ''pick_one'' THEN 1', 'WHEN f.value_code = ''pick_one'' THEN NULL'); END \$\$;"
  ["计量v2：重复升级再发放"]="DO \$\$ DECLARE d text := pg_get_functiondef('platform.meter_upgrade(uuid, platform.plan_tier)'::regprocedure); BEGIN IF position('IF p_tier = s.tier THEN' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, 'IF p_tier = s.tier THEN', 'IF false THEN'); END \$\$;"
  ["计量v2：升级按新档全额发放"]="DO \$\$ DECLARE d text := pg_get_functiondef('platform.meter_upgrade(uuid, platform.plan_tier)'::regprocedure); BEGIN IF position('v_topup := greatest(a.quantity - v_old, 0);' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, 'v_topup := greatest(a.quantity - v_old, 0);', 'v_topup := a.quantity;'); END \$\$;"
  ["计量v2：付费到期不进宽限期"]="DO \$\$ DECLARE d text := pg_get_functiondef('platform.meter_renew_due(integer)'::regprocedure); BEGIN IF position('THEN ''grace'' ELSE ''ended'' END' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, 'THEN ''grace'' ELSE ''ended'' END', 'THEN ''ended'' ELSE ''ended'' END'); END \$\$;"
  ["计量v2：宽限期只有 1 天"]="DO \$\$ DECLARE d text := pg_get_functiondef('platform.meter_renew_due(integer)'::regprocedure); BEGIN IF position('period_end + interval ''7 days'' <= v_now' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, 'period_end + interval ''7 days'' <= v_now', 'period_end + interval ''1 days'' <= v_now'); END \$\$;"
  ["计量v2：试用显示为付费"]="DO \$\$ DECLARE d text := pg_get_functiondef('platform.meter_state_of(uuid)'::regprocedure); BEGIN IF position('WHEN s.source = ''trial'' THEN ''trialing''' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, 'WHEN s.source = ''trial'' THEN ''trialing''', 'WHEN false THEN ''trialing'''); END \$\$;"
  ["计量v2：发布不查选科规则"]="DO \$\$ DECLARE d text := pg_get_functiondef('platform.guard_plan_config()'::regprocedure); BEGIN IF position('AND f.feature_code IN (''core_subjects'', ''elective_slots'')) = 2))' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, 'AND f.feature_code IN (''core_subjects'', ''elective_slots'')) = 2))', 'AND f.feature_code IN (''core_subjects'', ''elective_slots'')) >= 1))'); END \$\$;"
  ["计量v2：四档资源可以不一致"]="DO \$\$ DECLARE d text := pg_get_functiondef('platform.guard_plan_config()'::regprocedure); BEGIN IF position('<> 4 * v_resources' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, '<> 4 * v_resources', '< 0'); END \$\$;"
  ["计量v2：深度报告受宵禁"]="UPDATE platform.resource_type SET curfew_applies = true WHERE code = 'deep_report';"
  ["计量v2：BFF 可直接写时间设置"]="GRANT INSERT ON platform.student_time_guard TO starbuddy_bff;"
  ["计量v2：BFF 可读试用领取记录"]="GRANT SELECT ON platform.trial_claim TO starbuddy_bff;"
  ["审核台：签字不查 staff"]="DO \$\$ DECLARE d text := pg_get_functiondef('kge.current_staff()'::regprocedure); BEGIN IF position('AND a.kind = ''staff'' AND a.status = ''active''' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, 'AND a.kind = ''staff'' AND a.status = ''active''', ''); END \$\$;"
  ["审核台：发布不查批准记录"]="DO \$\$ DECLARE d text := pg_get_functiondef('kge.publish_map_version(uuid)'::regprocedure); BEGIN IF position('IF NOT FOUND OR r.decision <> ''approved'' THEN' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, 'IF NOT FOUND OR r.decision <> ''approved'' THEN', 'IF false THEN'); END \$\$;"
  ["审核台：发布不比摘要"]="DO \$\$ DECLARE d text := pg_get_functiondef('kge.publish_map_version(uuid)'::regprocedure); BEGIN IF position('IF r.digest IS DISTINCT FROM v_digest THEN' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, 'IF r.digest IS DISTINCT FROM v_digest THEN', 'IF false THEN'); END \$\$;"
  ["审核台：发布不查变更单"]="DO \$\$ DECLARE d text := pg_get_functiondef('kge.publish_map_version(uuid)'::regprocedure); BEGIN IF position('IF EXISTS (SELECT 1 FROM kge.map_change m WHERE m.target_version = p_version AND m.status NOT IN (''approved'', ''rejected'')) THEN' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, 'IF EXISTS (SELECT 1 FROM kge.map_change m WHERE m.target_version = p_version AND m.status NOT IN (''approved'', ''rejected'')) THEN', 'IF false THEN'); END \$\$;"
  ["审核台：版本号可不递增"]="DO \$\$ DECLARE d text := pg_get_functiondef('kge.publish_map_version(uuid)'::regprocedure); BEGIN IF position('IF v_new <= v_max THEN' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, 'IF v_new <= v_max THEN', 'IF false THEN'); END \$\$;"
  ["审核台：结构性变更只升修订号"]="DO \$\$ DECLARE d text := pg_get_functiondef('kge.publish_map_version(uuid)'::regprocedure); BEGIN IF position('IF v_structural AND v_new[1:2] = v_max[1:2] THEN' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, 'IF v_structural AND v_new[1:2] = v_max[1:2] THEN', 'IF false THEN'); END \$\$;"
  ["审核台：变更单一人即批准"]="DO \$\$ DECLARE d text := pg_get_functiondef('kge.sign_map_change(uuid, text, text)'::regprocedure); BEGIN IF position('>= kge.map_change_required_signers(c.change_type) THEN' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, '>= kge.map_change_required_signers(c.change_type) THEN', '>= 1 THEN'); END \$\$;"
  ["审核台：变更单签字不看摘要"]="DO \$\$ DECLARE d text := pg_get_functiondef('kge.sign_map_change(uuid, text, text)'::regprocedure); BEGIN IF position('AND s.decision = ''approved'' AND s.digest = v_digest)' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, 'AND s.decision = ''approved'' AND s.digest = v_digest)', 'AND s.decision = ''approved'')'); END \$\$;"
  ["审核台：撤下不走函数"]="DO \$\$ DECLARE d text := pg_get_functiondef('kge.guard_map_version()'::regprocedure); BEGIN IF position('IF NEW.status = ''retired'' AND current_user <> ''starbuddy_authz'' THEN' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, 'IF NEW.status = ''retired'' AND current_user <> ''starbuddy_authz'' THEN', 'IF false THEN'); END \$\$;"
  ["审核台：变更单状态可直接改"]="DO \$\$ DECLARE d text := pg_get_functiondef('kge.guard_map_change()'::regprocedure); BEGIN IF position('RAISE EXCEPTION ''变更单的状态只能经签字与发布函数修改'' USING ERRCODE = ''insufficient_privilege'';' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, 'RAISE EXCEPTION ''变更单的状态只能经签字与发布函数修改'' USING ERRCODE = ''insufficient_privilege'';', 'NULL;'); END \$\$;"
  ["审核台：批准后变更单可改"]="DO \$\$ DECLARE d text := pg_get_functiondef('kge.guard_map_change()'::regprocedure); BEGIN IF position('IF OLD.status <> ''proposed'' THEN' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, 'IF OLD.status <> ''proposed'' THEN', 'IF false THEN'); END \$\$;"
  ["审核台：已发布原子批准不比摘要"]="DO \$\$ DECLARE d text := pg_get_functiondef('kge.guard_atom_approval()'::regprocedure); BEGIN IF position('OR r.digest IS DISTINCT FROM v_digest THEN' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, 'OR r.digest IS DISTINCT FROM v_digest THEN', ' THEN'); END \$\$;"
  ["审核台：审核记录可改"]="DROP TRIGGER reject_content_review_change ON kge.content_review;"
  ["审核台：审核记录可清空"]="DROP TRIGGER reject_content_review_truncate ON kge.content_review; DROP TRIGGER reject_signoff_truncate ON platform.content_signoff;"
  ["审核台：知识服务可写结论"]="DROP POLICY content_review_kge_submit ON kge.content_review; CREATE POLICY content_review_kge_submit ON kge.content_review FOR INSERT TO starbuddy_kge WITH CHECK (true);"
  ["审核台：知识服务可发布"]="GRANT EXECUTE ON FUNCTION kge.publish_map_version(uuid) TO starbuddy_kge;"
  ["审核台：已发布原子不查批准"]="DROP TRIGGER guard_atom_approval ON kge.item_tag;"
  ["审核台：签字人可改"]="DROP TRIGGER reject_signoff_change ON platform.content_signoff;"
  ["审核台：变更单签字人可删"]="DROP TRIGGER reject_signoff_change ON platform.map_change_signoff;"
  ["审核台：时政原子不查批准"]="DROP TRIGGER guard_atom_approval ON kge.current_affair;"
  ["审核台：变更单签字可清空"]="DROP TRIGGER reject_signoff_truncate ON platform.map_change_signoff;"
  ["审核台：摘要不含教材投影"]="DO \$\$ DECLARE d text := pg_get_functiondef('kge.map_version_digest(uuid)'::regprocedure); BEGIN IF position('FROM kge.textbook_projection x WHERE x.map_version_id = p_version)' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, 'FROM kge.textbook_projection x WHERE x.map_version_id = p_version)', 'FROM kge.textbook_projection x WHERE false)'); END \$\$;"
  ["审核台：结构差异不看节点"]="DO \$\$ DECLARE d text := pg_get_functiondef('kge.structural_diff(uuid)'::regprocedure); BEGIN IF position('OR EXISTS ((SELECT * FROM n_new' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, 'OR EXISTS ((SELECT * FROM n_new', 'OR false AND EXISTS ((SELECT * FROM n_new'); END \$\$;"
  ["审核台：已批准原子可原地改"]="DO \$\$ DECLARE d text := pg_get_functiondef('kge.guard_atom_approval()'::regprocedure); BEGIN IF position('IF v_was_ok AND v_now_ok AND v_digest IS DISTINCT FROM v_old_digest THEN' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, 'IF v_was_ok AND v_now_ok AND v_digest IS DISTINCT FROM v_old_digest THEN', 'IF false THEN'); END \$\$;"
  ["审核台：版本审核可按低风险签"]="ALTER TABLE kge.content_review DROP CONSTRAINT content_review_version_risk;"
  ["审核台：撤下可不写依据"]="DO \$\$ DECLARE d text := pg_get_functiondef('kge.retire_map_version(uuid, text)'::regprocedure); BEGIN IF position('IF p_basis_code IS NULL THEN' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, 'IF p_basis_code IS NULL THEN', 'IF false THEN'); END \$\$;"
  ["审核台：结构性发布一人签字即可"]="DO \$\$ DECLARE d text := pg_get_functiondef('kge.publish_map_version(uuid)'::regprocedure); BEGIN IF position('IF v_signers < 2 THEN' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, 'IF v_signers < 2 THEN', 'IF v_signers < 1 THEN'); END \$\$;"
  ["审核台：驳回前的批准仍算数"]="DO \$\$ DECLARE d text := pg_get_functiondef('kge.publish_map_version(uuid)'::regprocedure); BEGIN IF position('AND x.decided_at > coalesce(v_last_reject, ''-infinity'')' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, 'AND x.decided_at > coalesce(v_last_reject, ''-infinity'')', ''); END \$\$;"
  ["审核台：结构性变更单只需一人"]="DO \$\$ DECLARE d text := pg_get_functiondef('kge.map_change_required_signers(text)'::regprocedure); BEGIN IF position('THEN 2 ELSE 1' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, 'THEN 2 ELSE 1', 'THEN 1 ELSE 1'); END \$\$;"
  ["审核台：变更单签字不比看到的摘要"]="DO \$\$ DECLARE d text := pg_get_functiondef('kge.sign_map_change(uuid, text, text)'::regprocedure); BEGIN IF position('IF p_expected_digest IS DISTINCT FROM v_digest THEN' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, 'IF p_expected_digest IS DISTINCT FROM v_digest THEN', 'IF false THEN'); END \$\$;"
  ["staff 登录：不锁定"]="DO \$\$ DECLARE d text := pg_get_functiondef('platform.staff_login(text, text, text)'::regprocedure); BEGIN IF position('IF c.locked_until IS NOT NULL AND c.locked_until > v_now THEN' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, 'IF c.locked_until IS NOT NULL AND c.locked_until > v_now THEN', 'IF false THEN'); END \$\$;"
  ["staff 登录：6 位码可重放"]="DO \$\$ DECLARE d text := pg_get_functiondef('platform.staff_login(text, text, text)'::regprocedure); BEGIN IF position('WHERE s > coalesce(c.totp_last_step, -1) AND' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, 'WHERE s > coalesce(c.totp_last_step, -1) AND', 'WHERE'); END \$\$;"
  ["staff 登录：不查口令"]="DO \$\$ DECLARE d text := pg_get_functiondef('platform.staff_login(text, text, text)'::regprocedure); BEGIN IF position('v_ok := platform.staff_crypt(p_password, c.password_hash) = c.password_hash' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, 'v_ok := platform.staff_crypt(p_password, c.password_hash) = c.password_hash', 'v_ok := true'); END \$\$;"
  ["staff 登录：不查 6 位码"]="DO \$\$ DECLARE d text := pg_get_functiondef('platform.staff_login(text, text, text)'::regprocedure); BEGIN IF position('AND v_hit IS NOT NULL' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, 'AND v_hit IS NOT NULL', ''); END \$\$;"
  ["staff 登录：不查账号状态"]="DO \$\$ DECLARE d text := pg_get_functiondef('platform.staff_login(text, text, text)'::regprocedure); BEGIN IF position('a.id = c.account_id AND a.kind = ''staff'' AND a.status = ''active''' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, 'a.id = c.account_id AND a.kind = ''staff'' AND a.status = ''active''', 'a.id = c.account_id'); END \$\$;"
  ["staff 登录：失败不计次"]="DO \$\$ DECLARE d text := pg_get_functiondef('platform.staff_login(text, text, text)'::regprocedure); BEGIN IF position('ELSE 0 END + 1;' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, 'ELSE 0 END + 1;', 'ELSE 0 END;'); END \$\$;"
  ["staff 登录：格式错也计次"]="DO \$\$ DECLARE d text := pg_get_functiondef('platform.staff_login(text, text, text)'::regprocedure); BEGIN IF position('OR p_code IS NULL OR p_code !~ ''^[0-9]{6}$'' THEN
    RETURN NULL;' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, 'OR p_code IS NULL OR p_code !~ ''^[0-9]{6}$'' THEN
    RETURN NULL;', 'OR p_code IS NULL THEN
    RETURN NULL;'); END \$\$;"
  ["staff 会话：不查空闲"]="DO \$\$ DECLARE d text := pg_get_functiondef('platform.staff_session_resolve(text)'::regprocedure); BEGIN IF position('AND s.last_seen_at > v_now - interval ''30 minutes''' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, 'AND s.last_seen_at > v_now - interval ''30 minutes''', ''); END \$\$;"
  ["staff 会话：不查绝对时长"]="DO \$\$ DECLARE d text := pg_get_functiondef('platform.staff_session_resolve(text)'::regprocedure); BEGIN IF position('AND s.expires_at > v_now' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, 'AND s.expires_at > v_now', ''); END \$\$;"
  ["staff 会话：不查撤销"]="DO \$\$ DECLARE d text := pg_get_functiondef('platform.staff_session_resolve(text)'::regprocedure); BEGIN IF position('s.revoked_at IS NULL AND' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, 's.revoked_at IS NULL AND', ''); END \$\$;"
  ["staff 会话：不查账号状态"]="DO \$\$ DECLARE d text := pg_get_functiondef('platform.staff_session_resolve(text)'::regprocedure); BEGIN IF position('a.id = s.account_id AND a.kind = ''staff'' AND a.status = ''active''' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, 'a.id = s.account_id AND a.kind = ''staff'' AND a.status = ''active''', 'a.id = s.account_id'); END \$\$;"
  ["staff 会话：12 小时改 24 小时"]="DO \$\$ DECLARE d text := pg_get_functiondef('platform.staff_login(text, text, text)'::regprocedure); BEGIN IF position('v_now + interval ''12 hours''' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, 'v_now + interval ''12 hours''', 'v_now + interval ''24 hours'''); END \$\$;"
  ["staff 会话：退出无效"]="DO \$\$ DECLARE d text := pg_get_functiondef('platform.staff_logout(text)'::regprocedure); BEGIN IF position('AND revoked_at IS NULL;' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, 'AND revoked_at IS NULL;', 'AND false;'); END \$\$;"
  ["staff 凭据：重置不撤销会话"]="DO \$\$ DECLARE d text := pg_get_functiondef('platform.staff_reset(uuid, text, bytea)'::regprocedure); BEGIN IF position('UPDATE platform.staff_session SET revoked_at = platform.staff_now() WHERE account_id = p_account AND revoked_at IS NULL;' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, 'UPDATE platform.staff_session SET revoked_at = platform.staff_now() WHERE account_id = p_account AND revoked_at IS NULL;', ''); END \$\$;"
  ["staff 凭据：口令可以很短"]="DO \$\$ DECLARE d text := pg_get_functiondef('platform.staff_enroll(uuid, text, text, bytea)'::regprocedure); BEGIN IF position('char_length(p_password) < 12' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, 'char_length(p_password) < 12', 'char_length(p_password) < 1'); END \$\$;"
  ["staff 凭据：可挂非 staff"]="DROP TRIGGER guard_staff_credential ON platform.staff_credential;"
  ["staff 凭据：签字身份可读"]="GRANT SELECT ON platform.staff_credential TO starbuddy_review;"
  ["staff 会话表：签字身份可读"]="GRANT SELECT ON platform.staff_session TO starbuddy_review;"
  ["staff 登录：BFF 可调用"]="GRANT EXECUTE ON FUNCTION platform.staff_login(text, text, text) TO starbuddy_bff;"
  ["staff 凭据：签字身份可建"]="GRANT EXECUTE ON FUNCTION platform.staff_enroll(uuid, text, text, bytea) TO starbuddy_review;"
  ["staff 6 位码：截断算错"]="DO \$\$ DECLARE d text := pg_get_functiondef('platform.staff_totp(bytea, bigint)'::regprocedure); BEGIN IF position('% 1000000' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, '% 1000000', '% 999999'); END \$\$;"
  ["staff 登录：失败计次不衰减"]="DO \$\$ DECLARE d text := pg_get_functiondef('platform.staff_login(text, text, text)'::regprocedure); BEGIN IF position('c.last_failed_at > v_now - interval ''15 minutes'' THEN c.failed_count ELSE 0 END' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, 'c.last_failed_at > v_now - interval ''15 minutes'' THEN c.failed_count ELSE 0 END', 'true THEN c.failed_count ELSE 0 END'); END \$\$;"
  ["staff 登录：一账号多会话"]="DO \$\$ DECLARE d text := pg_get_functiondef('platform.staff_login(text, text, text)'::regprocedure); BEGIN IF position('UPDATE platform.staff_session SET revoked_at = v_now WHERE account_id = c.account_id AND revoked_at IS NULL;' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, 'UPDATE platform.staff_session SET revoked_at = v_now WHERE account_id = c.account_id AND revoked_at IS NULL;', ''); END \$\$;"
  ["staff 登录：口令不限 72 字节"]="DO \$\$ DECLARE d text := pg_get_functiondef('platform.staff_login(text, text, text)'::regprocedure); BEGIN IF position('octet_length(p_password) NOT BETWEEN 1 AND 72' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, 'octet_length(p_password) NOT BETWEEN 1 AND 72', 'octet_length(p_password) NOT BETWEEN 1 AND 1000'); END \$\$;"
  ["建档：家长能读学生自述"]="DO \$\$ DECLARE d text := pg_get_functiondef('insight.onboarding_view(uuid)'::regprocedure); BEGIN IF position('WHEN ''self'' THEN (SELECT coalesce(jsonb_object_agg(r.field_id, r.value), ''{}'') FROM insight.self_report r
                         WHERE r.student_id = p_student AND r.superseded_at IS NULL)
      ELSE (SELECT coalesce(jsonb_object_agg(r.field_id, r.value), ''{}'') FROM insight.guardian_report r' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, 'WHEN ''self'' THEN (SELECT coalesce(jsonb_object_agg(r.field_id, r.value), ''{}'') FROM insight.self_report r
                         WHERE r.student_id = p_student AND r.superseded_at IS NULL)
      ELSE (SELECT coalesce(jsonb_object_agg(r.field_id, r.value), ''{}'') FROM insight.guardian_report r', 'WHEN ''self'' THEN (SELECT coalesce(jsonb_object_agg(r.field_id, r.value), ''{}'') FROM insight.self_report r
                         WHERE r.student_id = p_student AND r.superseded_at IS NULL)
      ELSE (SELECT coalesce(jsonb_object_agg(r.field_id, r.value), ''{}'') FROM insight.self_report r'); END \$\$;"
  ["建档：学生能看到家长录的成绩"]="DO \$\$ DECLARE d text := pg_get_functiondef('insight.onboarding_view(uuid)'::regprocedure); BEGIN IF position('(v_role = ''guardian'' OR e.entered_by_account_id = v_me)' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, '(v_role = ''guardian'' OR e.entered_by_account_id = v_me)', 'true'); END \$\$;"
  ["建档：家长能看到别的家长写的观察"]="DO \$\$ DECLARE d text := pg_get_functiondef('insight.onboarding_view(uuid)'::regprocedure); BEGIN IF position('AND r.guardian_account_id = v_me AND r.superseded_at IS NULL' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, 'AND r.guardian_account_id = v_me AND r.superseded_at IS NULL', 'AND r.superseded_at IS NULL'); END \$\$;"
  ["建档：自述不校验学生本人"]="DO \$\$ DECLARE d text := pg_get_functiondef('insight.submit_self_report(uuid, text, jsonb)'::regprocedure); BEGIN IF position('OR NOT platform.is_self_student(p_student) THEN' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, 'OR NOT platform.is_self_student(p_student) THEN', 'THEN'); END \$\$;"
  ["建档：家长观察不校验绑定"]="DO \$\$ DECLARE d text := pg_get_functiondef('insight.submit_guardian_report(uuid, text, jsonb)'::regprocedure); BEGIN IF position('OR NOT platform.is_linked_guardian(p_student) THEN' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, 'OR NOT platform.is_linked_guardian(p_student) THEN', 'THEN'); END \$\$;"
  ["建档：字段不查白名单"]="DO \$\$ DECLARE d text := pg_get_functiondef('insight.submit_self_report(uuid, text, jsonb)'::regprocedure); BEGIN IF position('IF NOT EXISTS (SELECT 1 FROM insight.report_field WHERE field_id = p_field AND respondent = ''self'')
     OR' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, 'IF NOT EXISTS (SELECT 1 FROM insight.report_field WHERE field_id = p_field AND respondent = ''self'')
     OR', 'IF'); END \$\$;"
  ["建档：改答案覆盖旧行"]="DROP TRIGGER guard_fact_update ON insight.self_report;"
  ["建档：自由文本不限长度"]="DO \$\$ DECLARE d text := pg_get_functiondef('insight.valid_text(jsonb)'::regprocedure); BEGIN IF position('BETWEEN 1 AND 200' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, 'BETWEEN 1 AND 200', 'BETWEEN 1 AND 100000'); END \$\$;"
  ["建档：BFF 可直接读自述"]="GRANT SELECT ON insight.self_report TO starbuddy_bff; CREATE POLICY m_sr ON insight.self_report FOR SELECT TO starbuddy_bff USING (true);"
  ["建档：报表身份可读进度明细"]="GRANT SELECT ON insight.onboarding_progress TO starbuddy_report; CREATE POLICY m_op ON insight.onboarding_progress FOR SELECT TO starbuddy_report USING (true);"
  ["建档：别家家长能改教材版本"]="DO \$\$ DECLARE d text := pg_get_functiondef('platform.set_subject_textbook(uuid, platform.subject, text)'::regprocedure); BEGIN IF position('ELSIF platform.is_linked_guardian(p_student) THEN' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, 'ELSIF platform.is_linked_guardian(p_student) THEN', 'ELSIF true THEN'); END \$\$;"
  ["建档：学生可录排名区间"]="ALTER TABLE insight.exam_record DROP CONSTRAINT exam_record_rank_guardian;"
  ["建档：解除绑定的家长仍算已绑定"]="DO \$\$ DECLARE d text := pg_get_functiondef('platform.is_linked_guardian(uuid)'::regprocedure); BEGIN IF position('l.status = ''active''' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, 'l.status = ''active''', 'true'); END \$\$;"
  ["建档：学生能看到家长填的教材版本"]="DO \$\$ DECLARE d text := pg_get_functiondef('insight.onboarding_view(uuid)'::regprocedure); BEGIN IF position('AND t.updated_by = v_me' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, 'AND t.updated_by = v_me', ''); END \$\$;"
  ["建档：教材版本两边共用一行"]="ALTER TABLE platform.student_subject_textbook DROP CONSTRAINT student_subject_textbook_pkey; ALTER TABLE platform.student_subject_textbook ADD PRIMARY KEY (student_id, subject); DO \$\$ DECLARE d text := pg_get_functiondef('platform.set_subject_textbook(uuid, platform.subject, text)'::regprocedure); BEGIN IF position('ON CONFLICT (student_id, subject, updated_by) DO UPDATE
     SET textbook_version = EXCLUDED.textbook_version, source_type = EXCLUDED.source_type, updated_at = EXCLUDED.updated_at;' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, 'ON CONFLICT (student_id, subject, updated_by) DO UPDATE
     SET textbook_version = EXCLUDED.textbook_version, source_type = EXCLUDED.source_type, updated_at = EXCLUDED.updated_at;', 'ON CONFLICT (student_id, subject) DO UPDATE
     SET textbook_version = EXCLUDED.textbook_version, source_type = EXCLUDED.source_type, updated_by = EXCLUDED.updated_by, updated_at = EXCLUDED.updated_at;'); END \$\$;"
  ["建档：统计不设人数门槛"]="DO \$\$ DECLARE d text := pg_get_functiondef('insight.onboarding_stats(timestamp with time zone, timestamp with time zone)'::regprocedure); BEGIN IF position('CASE WHEN g.n0 >= 5 THEN g.n1 END' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, 'CASE WHEN g.n0 >= 5 THEN g.n1 END', 'g.n1'); END \$\$;"
  ["建档：统计窗口可任意收窄"]="DO \$\$ DECLARE d text := pg_get_functiondef('insight.onboarding_stats(timestamp with time zone, timestamp with time zone)'::regprocedure); BEGIN IF position('p_to - p_from < interval ''1 day''' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, 'p_to - p_from < interval ''1 day''', 'false'); END \$\$;"
  ["建档：考试记录不限量"]="DO \$\$ DECLARE d text := pg_get_functiondef('insight.submit_exam_record(uuid, platform.subject, text, date, numeric, numeric, text, text, text)'::regprocedure); BEGIN IF position('>= 50 THEN' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, '>= 50 THEN', '>= 5000 THEN'); END \$\$;"
  ["建档：学生可录难度说明"]="ALTER TABLE insight.exam_record DROP CONSTRAINT exam_record_difficulty_guardian;"
  ["建档：步骤名不查白名单"]="ALTER TABLE insight.onboarding_progress DROP CONSTRAINT onboarding_progress_step_check;"
  ["建档：考试来源写死为客观记录"]="ALTER TABLE insight.exam_record DROP CONSTRAINT exam_record_source_is_entrant; ALTER TABLE insight.exam_record ALTER COLUMN source_type SET DEFAULT 'objective'; DO \$\$ DECLARE d text := pg_get_functiondef('insight.submit_exam_record(uuid, platform.subject, text, date, numeric, numeric, text, text, text)'::regprocedure); BEGIN IF position('v_role::insight.source_type)' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, 'v_role::insight.source_type)', '''objective'')'); END \$\$;"
  ["建档：进度可改"]="DROP TRIGGER reject_progress_change ON insight.onboarding_progress;"
  ["审核台：签字身份可读审核记录"]="GRANT SELECT ON kge.content_review TO starbuddy_review; CREATE POLICY cr_rev ON kge.content_review FOR SELECT TO starbuddy_review USING (true);"
  ["证据补列：来源类型不核对"]="ALTER TABLE platform.evidence_event DROP CONSTRAINT evidence_event_source_type;"
  ["证据补列：交互信号可带作答情境"]="ALTER TABLE platform.evidence_event DROP CONSTRAINT evidence_event_kind_applies;"
  ["证据补列：提示后可记独立"]="ALTER TABLE platform.attempt DROP CONSTRAINT attempt_hinted_kind;"
  ["证据补列：地图版本不限学科"]="ALTER TABLE platform.evidence_event DROP CONSTRAINT evidence_event_map_version_fk;"
  ["证据补列：作答可记学生自述"]="ALTER TABLE platform.attempt DROP CONSTRAINT attempt_source_type_check;"
  ["证据补列：证据等级可取 D"]="ALTER TABLE platform.evidence_event DROP CONSTRAINT evidence_event_evidence_level_check;"
  ["证据补列：拍照可记 A"]="ALTER TABLE platform.evidence_event DROP CONSTRAINT evidence_event_photo_level; ALTER TABLE platform.attempt DROP CONSTRAINT attempt_photo_level;"
  ["证据补列：键盘可带识别转写"]="ALTER TABLE platform.evidence_event DROP CONSTRAINT evidence_event_keypad_kind;"
  ["证据补列：作答与证据可不一致"]="DROP TRIGGER check_attempt_attributes ON platform.attempt;"
  ["重放封存：可以改"]="DROP TRIGGER reject_replay_seal_change ON platform.replay_seal; GRANT UPDATE ON platform.replay_seal TO starbuddy_learning;"
  ["重放封存：学习服务可以解封"]="GRANT DELETE ON platform.replay_seal TO starbuddy_learning; CREATE POLICY rs_del ON platform.replay_seal FOR DELETE TO starbuddy_learning USING (true);"
  ["重放封存：可以清空"]="DROP TRIGGER reject_replay_seal_truncate ON platform.replay_seal;"
  ["重放封存：BFF 可读"]="GRANT SELECT ON platform.replay_seal TO starbuddy_bff; CREATE POLICY rs_bff ON platform.replay_seal FOR SELECT TO starbuddy_bff USING (true);"
  ["重放封存：BFF 可直接封存"]="GRANT INSERT ON platform.replay_seal TO starbuddy_bff; CREATE POLICY rs_bff_ins ON platform.replay_seal FOR INSERT TO starbuddy_bff WITH CHECK (true);"
  ["重放封存：不限方法"]="ALTER TABLE platform.replay_seal DROP CONSTRAINT replay_seal_method_check;"
  ["学习服务可以自己结案"]="GRANT UPDATE (status, resolved_at, resolved_by_account, resolution_code) ON platform.review_task TO starbuddy_learning; CREATE POLICY rt_upd ON platform.review_task FOR UPDATE TO starbuddy_learning USING (true) WITH CHECK (true);"
  ["学习服务可以插入已结案任务"]="DROP POLICY review_service_open ON platform.review_task; CREATE POLICY review_service_open ON platform.review_task FOR INSERT TO starbuddy_learning WITH CHECK (true);"
  ["结案不校验教研身份"]="DO \$\$ DECLARE d text := pg_get_functiondef('platform.review_current_staff()'::regprocedure); BEGIN IF position('AND a.kind = ''staff''' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, 'AND a.kind = ''staff''', ''); END \$\$;"
  ["更正放行不看结案人身份"]="DO \$\$ DECLARE d text := pg_get_functiondef('platform.check_supersede_target()'::regprocedure); BEGIN IF position('AND a.kind = ''staff''' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, 'AND a.kind = ''staff''', ''); END \$\$;"
  ["插入时可以是已被取代"]="DROP TRIGGER guard_inference_insert ON platform.grade_result; DROP TRIGGER guard_inference_insert ON platform.mastery_estimate; DROP TRIGGER guard_inference_insert ON platform.error_attribution;"
  ["更正可以成环"]="DO \$\$ DECLARE d text := pg_get_functiondef('platform.check_supersede_target()'::regprocedure); BEGIN IF position('WHERE n.id = NEW.superseded_by AND n.superseded_by IS NULL
                     AND n.student_id = OLD.student_id AND n.subject = OLD.subject
                     AND n.attempt_id' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, 'WHERE n.id = NEW.superseded_by AND n.superseded_by IS NULL
                     AND n.student_id = OLD.student_id AND n.subject = OLD.subject
                     AND n.attempt_id', 'WHERE n.id = NEW.superseded_by
                     AND n.student_id = OLD.student_id AND n.subject = OLD.subject
                     AND n.attempt_id'); END \$\$;"
  ["判断模板审核只靠触发器之外的机制"]="DO \$\$ DECLARE d text := pg_get_functiondef('insight.claim_guard()'::regprocedure); BEGIN IF position('IF NOT FOUND OR NOT (v_tpl.status = ''approved'' OR (v_moving_down AND v_tpl.status = ''retired'')) THEN' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, 'IF NOT FOUND OR NOT (v_tpl.status = ''approved'' OR (v_moving_down AND v_tpl.status = ''retired'')) THEN', 'IF NOT FOUND THEN'); END \$\$;"
  ["前端能看到退役模板的判断"]="DROP POLICY template_read ON insight.claim_template; CREATE POLICY template_read ON insight.claim_template FOR SELECT TO starbuddy_learning, starbuddy_bff USING (status IN ('approved', 'retired'));"
  ["判断可见范围不受模板上限"]="DO \$\$ DECLARE d text := pg_get_functiondef('insight.claim_guard()'::regprocedure); BEGIN IF position('IF NOT (NEW.visibility <@ v_tpl.max_visibility) THEN' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, 'IF NOT (NEW.visibility <@ v_tpl.max_visibility) THEN', 'IF false THEN'); END \$\$;"
  ["终态判断可以修改"]="DO \$\$ DECLARE d text := pg_get_functiondef('insight.claim_guard()'::regprocedure); BEGIN IF position('IF TG_OP = ''UPDATE'' AND OLD.status IN (''refuted'', ''withdrawn'') THEN' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, 'IF TG_OP = ''UPDATE'' AND OLD.status IN (''refuted'', ''withdrawn'') THEN', 'IF false THEN'); END \$\$;"
  ["判断状态流转不校验"]="CREATE OR REPLACE FUNCTION insight.change_allowed(p_from insight.claim_status, p_to insight.claim_status, p_reason text) RETURNS boolean LANGUAGE sql IMMUTABLE AS \$\$ SELECT true \$\$;"
  ["改可见范围不需要专门原因"]="DO \$\$ DECLARE d text := pg_get_functiondef('insight.claim_guard()'::regprocedure); BEGIN IF position('AND NEW.change_reason <> ''visibility_changed'' THEN' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, 'AND NEW.change_reason <> ''visibility_changed'' THEN', 'AND false THEN'); END \$\$;"
  ["续期不受模板最长有效期"]="DO \$\$ DECLARE d text := pg_get_functiondef('insight.claim_guard()'::regprocedure); BEGIN IF position('AND NEW.expires_at > now() + v_tpl.max_ttl THEN' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, 'AND NEW.expires_at > now() + v_tpl.max_ttl THEN', 'AND false THEN'); END \$\$;"
  ["创建时间可以伪造"]="DO \$\$ DECLARE d text := pg_get_functiondef('insight.claim_guard()'::regprocedure); BEGIN IF position('NEW.created_at := now();' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, 'NEW.created_at := now();', 'NULL;'); END \$\$;"
  ["判断依据不校验学生"]="CREATE OR REPLACE FUNCTION insight.basis_belongs(p_kind insight.basis_kind, p_id uuid, p_student uuid) RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, insight, platform, pg_temp AS \$\$ SELECT true \$\$;"
  ["节点推断可以不引用依据"]="ALTER TABLE insight.claim DROP CONSTRAINT claim_inferred_has_basis;"
  ["判断 value 不限大小"]="ALTER TABLE insight.claim DROP CONSTRAINT claim_value_small;"
  ["推断判断可以不带证据"]="ALTER TABLE insight.claim DROP CONSTRAINT claim_inferred_has_evidence;"
  ["判断可以不设到期"]="ALTER TABLE insight.claim ALTER COLUMN expires_at DROP NOT NULL; ALTER TABLE insight.claim_history ALTER COLUMN expires_at DROP NOT NULL;"
  ["洞察服务可以自己写历史"]="GRANT INSERT ON insight.claim_history TO starbuddy_insight; CREATE POLICY h ON insight.claim_history FOR INSERT TO starbuddy_insight WITH CHECK (true);"
  ["判断历史可以修改"]="DROP TRIGGER claim_history_guard ON insight.claim_history;"
  ["历史原因不受白名单约束"]="ALTER TABLE insight.claim_history DROP CONSTRAINT history_reason_allowed;"
  ["新建判断不写历史"]="DROP TRIGGER claim_log ON insight.claim; CREATE TRIGGER claim_log AFTER UPDATE ON insight.claim FOR EACH ROW EXECUTE FUNCTION insight.claim_log();"
  ["不改状态的修改不留痕"]="DROP TRIGGER claim_log ON insight.claim; CREATE TRIGGER claim_log_i AFTER INSERT ON insight.claim FOR EACH ROW EXECUTE FUNCTION insight.claim_log(); CREATE TRIGGER claim_log_u AFTER UPDATE ON insight.claim FOR EACH ROW WHEN (OLD.status IS DISTINCT FROM NEW.status) EXECUTE FUNCTION insight.claim_log();"
  ["学科服务可以写判断"]="GRANT INSERT ON insight.claim TO starbuddy_learning; CREATE POLICY l ON insight.claim FOR INSERT TO starbuddy_learning WITH CHECK (true);"
  ["前端能看到推翻与撤回的判断"]="DROP POLICY claim_front_read ON insight.claim; CREATE POLICY claim_front_read ON insight.claim FOR SELECT TO starbuddy_bff USING (expires_at > now() AND EXISTS (SELECT 1 FROM insight.claim_template t WHERE t.template_key = claim.template_key AND t.version = claim.template_version) AND ((platform.is_self_student(student_id) AND 'student' = ANY (visibility)) OR (platform.is_linked_guardian(student_id) AND 'guardian' = ANY (visibility))));"
  ["前端能看到已过期的判断"]="DROP POLICY claim_front_read ON insight.claim; CREATE POLICY claim_front_read ON insight.claim FOR SELECT TO starbuddy_bff USING (status IN ('hypothesis', 'established', 'to_verify') AND EXISTS (SELECT 1 FROM insight.claim_template t WHERE t.template_key = claim.template_key AND t.version = claim.template_version) AND ((platform.is_self_student(student_id) AND 'student' = ANY (visibility)) OR (platform.is_linked_guardian(student_id) AND 'guardian' = ANY (visibility))));"
  ["家长能看到仅学生可见的判断"]="DROP POLICY claim_front_read ON insight.claim; CREATE POLICY claim_front_read ON insight.claim FOR SELECT TO starbuddy_bff USING (status IN ('hypothesis', 'established', 'to_verify') AND expires_at > now() AND platform.can_read_student(student_id));"
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
  ["预留不校验学生本人"]="DO \$\$ DECLARE d text := pg_get_functiondef('platform.meter_reserve_impl(uuid, text, text, text, numeric, text, text, boolean)'::regprocedure); BEGIN IF position('IF p_student IS NULL OR NOT platform.is_self_student(p_student) THEN' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, 'IF p_student IS NULL OR NOT platform.is_self_student(p_student) THEN', 'IF false THEN'); END \$\$;"
  ["复看不看时间窗口"]="DO \$\$ DECLARE d text := pg_get_functiondef('platform.meter_reserve_impl(uuid, text, text, text, numeric, text, text, boolean)'::regprocedure); BEGIN IF position('AND x.finished_at > v_now - rt.dedup_window' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, 'AND x.finished_at > v_now - rt.dedup_window', ''); END \$\$;"
  ["去重键不含学生"]="DO \$\$ DECLARE d text := pg_get_functiondef('platform.meter_reserve_impl(uuid, text, text, text, numeric, text, text, boolean)'::regprocedure); BEGIN IF position('p_student::text || ''|'' || ' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, 'p_student::text || ''|'' || ', ''); END \$\$;"
  ["重复结算不幂等"]="DO \$\$ DECLARE d text := pg_get_functiondef('platform.meter_settle(uuid, numeric)'::regprocedure); BEGIN IF position('IF r.state = ''settled'' THEN' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, 'IF r.state = ''settled'' THEN', 'IF false THEN'); END \$\$;"
  ["额度不足也能预留"]="DO \$\$ DECLARE d text := pg_get_functiondef('platform.meter_reserve_impl(uuid, text, text, text, numeric, text, text, boolean)'::regprocedure); BEGIN IF position('e.granted + e.fair_use_margin - e.reserved - e.settled >= p_quantity THEN' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, 'e.granted + e.fair_use_margin - e.reserved - e.settled >= p_quantity THEN', 'true THEN'); END \$\$;"
  ["key 不校验格式"]="DO \$\$ DECLARE d text := pg_get_functiondef('platform.meter_reserve_impl(uuid, text, text, text, numeric, text, text, boolean)'::regprocedure); BEGIN IF position('IF p_key IS NULL OR p_key !~' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, 'IF p_key IS NULL OR p_key !~', 'IF p_key IS NULL AND p_key !~'); END \$\$;"
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
  ["复看不看处理中的首次"]="DO \$\$ DECLARE d text := pg_get_functiondef('platform.meter_reserve_impl(uuid, text, text, text, numeric, text, text, boolean)'::regprocedure); BEGIN IF position('AND x.expires_at > v_now) THEN' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, 'AND x.expires_at > v_now) THEN', 'AND false) THEN'); END \$\$;"
  ["资源与方法不校验对应"]="DO \$\$ DECLARE d text := pg_get_functiondef('platform.meter_reserve_impl(uuid, text, text, text, numeric, text, text, boolean)'::regprocedure); BEGIN IF position('IF p_source_method IS NULL OR NOT platform.meter_method_allowed(p_resource, p_source_method) THEN' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, 'IF p_source_method IS NULL OR NOT platform.meter_method_allowed(p_resource, p_source_method) THEN', 'IF false THEN'); END \$\$;"
  ["过期后仍可结算"]="DO \$\$ DECLARE d text := pg_get_functiondef('platform.meter_settle(uuid, numeric)'::regprocedure); BEGIN IF position('IF r.expires_at <= v_now THEN' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, 'IF r.expires_at <= v_now THEN', 'IF false THEN'); END \$\$;"
  ["重复释放不幂等"]="DO \$\$ DECLARE d text := pg_get_functiondef('platform.meter_release(uuid, text)'::regprocedure); BEGIN IF position('IF r.state = ''released'' THEN' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, 'IF r.state = ''released'' THEN', 'IF false THEN'); END \$\$;"
  ["续期降级不暂停超额科目"]="DO \$\$ DECLARE d text := pg_get_functiondef('platform.meter_apply_subject_limits(uuid, text, platform.plan_tier)'::regprocedure); BEGIN IF position('UPDATE platform.subject_enrollment SET status = ''paused''' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, 'UPDATE platform.subject_enrollment SET status = ''paused''', 'UPDATE platform.subject_enrollment SET status = status'); END \$\$;"
  ["选科不校验学科已上线"]="DO \$\$ DECLARE d text := pg_get_functiondef('platform.enroll_subject(uuid, platform.subject)'::regprocedure); BEGIN IF position('OR NOT EXISTS (SELECT 1 FROM platform.curriculum_version WHERE subject = p_subject AND status = ''published'') THEN' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, 'OR NOT EXISTS (SELECT 1 FROM platform.curriculum_version WHERE subject = p_subject AND status = ''published'') THEN', ' THEN'); END \$\$;"
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
  ["知识服务可以删除边"]="GRANT DELETE ON kge.knowledge_edge TO starbuddy_kge;"
  ["学科服务可以写节点"]="GRANT INSERT ON kge.knowledge_node TO starbuddy_learning; CREATE POLICY l ON kge.knowledge_node FOR INSERT TO starbuddy_learning WITH CHECK (true);"
  ["洞察服务能读题目标签"]="GRANT SELECT ON kge.item_tag TO starbuddy_insight; CREATE POLICY i ON kge.item_tag FOR SELECT TO starbuddy_insight USING (true);"
  ["发布函数对学科服务开放"]="GRANT EXECUTE ON FUNCTION kge.publish_map_version(uuid) TO starbuddy_learning;"
  ["BFF 能看到草稿版本"]="DROP POLICY map_version_front_read ON kge.map_version; CREATE POLICY map_version_front_read ON kge.map_version FOR SELECT TO starbuddy_bff USING (true);"
  ["BFF 能看到草稿版本的节点"]="DROP POLICY node_front_read ON kge.knowledge_node; CREATE POLICY node_front_read ON kge.knowledge_node FOR SELECT TO starbuddy_bff USING (status = 'active');"
  ["BFF 能看到已停用节点"]="DROP POLICY node_front_read ON kge.knowledge_node; CREATE POLICY node_front_read ON kge.knowledge_node FOR SELECT TO starbuddy_bff USING (EXISTS (SELECT 1 FROM kge.map_version v WHERE v.id = map_version_id AND v.status = 'published'));"
  ["BFF 能看到指向草稿版本的边"]="DROP POLICY edge_front_read ON kge.knowledge_edge; CREATE POLICY edge_front_read ON kge.knowledge_edge FOR SELECT TO starbuddy_bff USING (status = 'active');"
  ["BFF 能看到草稿版本的投影"]="DROP POLICY textbook_front_read ON kge.textbook_projection; CREATE POLICY textbook_front_read ON kge.textbook_projection FOR SELECT TO starbuddy_bff USING (true);"
  ["BFF 能读采分点"]="GRANT SELECT ON kge.scoring_point TO starbuddy_bff; CREATE POLICY b ON kge.scoring_point FOR SELECT TO starbuddy_bff USING (true);"
  ["BFF 能读投影核对人"]="GRANT SELECT (verified_by) ON kge.textbook_projection TO starbuddy_bff;"
  ["发布不跑校验"]="DO \$\$ DECLARE d text := pg_get_functiondef('kge.publish_map_version(uuid)'::regprocedure); BEGIN IF position('IF v_errors > 0 THEN' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, 'IF v_errors > 0 THEN', 'IF false THEN'); END \$\$;"
  ["发布不撤下原已发布版本"]="DO \$\$ DECLARE d text := pg_get_functiondef('kge.publish_map_version(uuid)'::regprocedure); BEGIN IF position('UPDATE kge.map_version SET status = ''retired'' WHERE id = v_old;' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, 'UPDATE kge.map_version SET status = ''retired'' WHERE id = v_old;', 'NULL;'); END \$\$;"
  ["可以不经发布函数直接发布"]="DO \$\$ DECLARE d text := pg_get_functiondef('kge.guard_map_version()'::regprocedure); BEGIN IF position('IF NEW.status = ''published'' AND current_user <> ''starbuddy_authz'' THEN' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, 'IF NEW.status = ''published'' AND current_user <> ''starbuddy_authz'' THEN', 'IF false THEN'); END \$\$;"
  ["地图版本可以直接以已发布新建"]="DO \$\$ DECLARE d text := pg_get_functiondef('kge.guard_map_version()'::regprocedure); BEGIN IF position('IF NEW.status <> ''draft'' OR NEW.published_at IS NOT NULL THEN' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, 'IF NEW.status <> ''draft'' OR NEW.published_at IS NOT NULL THEN', 'IF false THEN'); END \$\$;"
  ["已撤下版本可以复活"]="DO \$\$ DECLARE d text := pg_get_functiondef('kge.guard_map_version()'::regprocedure); BEGIN IF position('OR (OLD.status = ''published'' AND NEW.status = ''retired'')) THEN' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, 'OR (OLD.status = ''published'' AND NEW.status = ''retired'')) THEN', 'OR true) THEN'); END \$\$;"
  ["已发布版本可以改版本号"]="DO \$\$ DECLARE d text := pg_get_functiondef('kge.guard_map_version()'::regprocedure); BEGIN IF position('IF OLD.status <> ''draft'' AND (NEW.version' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, 'IF OLD.status <> ''draft'' AND (NEW.version', 'IF false AND (NEW.version'); END \$\$;"
  ["同一学科可以有多个已发布版本"]="DROP INDEX kge.map_version_one_published;"
  ["已发布版本可以改节点"]="DROP TRIGGER guard_frozen ON kge.knowledge_node;"
  ["已发布版本可以改边"]="DROP TRIGGER guard_frozen ON kge.knowledge_edge;"
  ["已发布版本可以改投影"]="DROP TRIGGER guard_frozen ON kge.textbook_projection;"
  ["已发布版本可以改能力视图挂接"]="DROP TRIGGER guard_frozen ON kge.skill_link;"
  ["已发布版本可以加旧编码迁移"]="DROP TRIGGER guard_frozen ON kge.id_migration;"
  ["冻结只看新版本不看原版本"]="DO \$\$ DECLARE d text := pg_get_functiondef('kge.guard_frozen()'::regprocedure); BEGIN IF position('CASE WHEN TG_OP = ''UPDATE'' THEN ARRAY[OLD.map_version_id] ELSE ''{}''::uuid[] END' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, 'CASE WHEN TG_OP = ''UPDATE'' THEN ARRAY[OLD.map_version_id] ELSE ''{}''::uuid[] END', '''{}''::uuid[]'); END \$\$;"
  ["节点学科前缀不校验"]="DROP TRIGGER guard_node ON kge.knowledge_node;"
  ["节点 ID 不查正则"]="ALTER DOMAIN kge.node_id DROP CONSTRAINT node_id_check;"
  ["层级与 ID 段数可以不一致"]="ALTER TABLE kge.knowledge_node DROP CONSTRAINT node_level_depth;"
  ["上一级可以不是 ID 前缀"]="ALTER TABLE kge.knowledge_node DROP CONSTRAINT node_parent_is_prefix;"
  ["领域可以带类型"]="ALTER TABLE kge.knowledge_node DROP CONSTRAINT node_kind_by_level;"
  ["细目可以是任意类型"]="ALTER TABLE kge.knowledge_node DROP CONSTRAINT node_detail_kind;"
  ["易错点可以不写错因"]="ALTER TABLE kge.knowledge_node DROP CONSTRAINT node_pitfall_error_type;"
  ["认知层次可以给任意类型"]="ALTER TABLE kge.knowledge_node DROP CONSTRAINT node_cognitive_levels_kind;"
  ["边可以自环"]="ALTER TABLE kge.knowledge_edge DROP CONSTRAINT edge_no_self_loop;"
  ["前置边可以跨版本"]="ALTER TABLE kge.knowledge_edge DROP CONSTRAINT edge_same_version;"
  ["投影核对可以不写核对人"]="ALTER TABLE kge.textbook_projection DROP CONSTRAINT projection_verified_by;"
  ["原句哈希可以由写入方填写"]="ALTER TABLE kge.textbook_projection ALTER COLUMN quote_hash DROP EXPRESSION;"
  ["采分点可以不带 Rubric 版本"]="ALTER TABLE kge.scoring_point ALTER COLUMN rubric_version DROP NOT NULL;"
  ["时政有效期不校验"]="ALTER TABLE kge.current_affair DROP CONSTRAINT current_affair_validity;"
  ["变更单一人即可批准"]="DO \$\$ DECLARE d text := pg_get_functiondef('kge.guard_map_change()'::regprocedure); BEGIN IF position('< kge.map_change_required_signers(NEW.change_type) THEN' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, '< kge.map_change_required_signers(NEW.change_type) THEN', '< 1 THEN'); END \$\$;"
  ["撤销的旧编码可以有新节点"]="ALTER TABLE kge.id_migration DROP CONSTRAINT id_migration_retire;"
  ["审核结论可以不写摘要"]="ALTER TABLE kge.content_review DROP CONSTRAINT content_review_decided;"
  ["采分点节点不校验"]="DROP TRIGGER guard_node_refs ON kge.scoring_point;"
  ["题目标签节点不校验"]="DROP TRIGGER guard_node_refs ON kge.item_tag;"
  ["题目标签学科不校验"]="DROP TRIGGER guard_subject ON kge.item_tag;"
  ["能力视图挂接学科不校验"]="DROP TRIGGER guard_subject ON kge.skill_link;"
  ["题目标签不关联 platform.item"]="ALTER TABLE kge.item_tag DROP CONSTRAINT item_tag_subject_item_id_fkey;"
  ["校验漏报环"]="DO \$\$ DECLARE d text := pg_get_functiondef('kge.validate_map_version(uuid)'::regprocedure); BEGIN IF position('FROM r WHERE r.src = r.dst' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, 'FROM r WHERE r.src = r.dst', 'FROM r WHERE false'); END \$\$;"
  ["校验漏报传递边"]="DO \$\$ DECLARE d text := pg_get_functiondef('kge.validate_map_version(uuid)'::regprocedure); BEGIN IF position('WHERE EXISTS (SELECT 1 FROM e ab JOIN r' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, 'WHERE EXISTS (SELECT 1 FROM e ab JOIN r', 'WHERE false AND EXISTS (SELECT 1 FROM e ab JOIN r'); END \$\$;"
  ["孤点不阻断发布"]="DO \$\$ DECLARE d text := pg_get_functiondef('kge.validate_map_version(uuid)'::regprocedure); BEGIN IF position('SELECT ''orphan'', ''error''' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, 'SELECT ''orphan'', ''error''', 'SELECT ''orphan'', ''warning'''); END \$\$;"
  ["孤点不看教材投影"]="DO \$\$ DECLARE d text := pg_get_functiondef('kge.validate_map_version(uuid)'::regprocedure); BEGIN IF position('AND NOT EXISTS (SELECT 1 FROM kge.textbook_projection p WHERE p.map_version_id = p_version AND p.node_id = n.id)' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, 'AND NOT EXISTS (SELECT 1 FROM kge.textbook_projection p WHERE p.map_version_id = p_version AND p.node_id = n.id)', ''); END \$\$;"
  ["缺边界不阻断发布"]="DO \$\$ DECLARE d text := pg_get_functiondef('kge.validate_map_version(uuid)'::regprocedure); BEGIN IF position('SELECT ''missing_boundary'', ''error''' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, 'SELECT ''missing_boundary'', ''error''', 'SELECT ''missing_boundary'', ''warning'''); END \$\$;"
  ["悬空引用不看停用节点"]="DO \$\$ DECLARE d text := pg_get_functiondef('kge.validate_map_version(uuid)'::regprocedure); BEGIN IF position('AND n.id = refs.node AND n.status = ''active''' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, 'AND n.id = refs.node AND n.status = ''active''', 'AND n.id = refs.node'); END \$\$;"
  ["题量不足阻断发布"]="DO \$\$ DECLARE d text := pg_get_functiondef('kge.validate_map_version(uuid)'::regprocedure); BEGIN IF position('SELECT ''few_items'', ''warning''' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, 'SELECT ''few_items'', ''warning''', 'SELECT ''few_items'', ''error'''); END \$\$;"
  ["内容版本可以引用别科地图"]="ALTER TABLE platform.curriculum_version DROP CONSTRAINT curriculum_version_map_version_fk; ALTER TABLE platform.curriculum_version ADD FOREIGN KEY (map_version_id) REFERENCES kge.map_version (id);"
  ["攻击面：公共知识层加学生键"]="ALTER TABLE kge.item_tag ADD COLUMN student_id uuid;"
  ["攻击面：公共知识层外键指向学生档案"]="ALTER TABLE kge.content_review ADD COLUMN subject_ref uuid REFERENCES platform.student_profile (id);"
  ["冻结检查不加锁（发布竞态）"]="DO \$\$ DECLARE d text := pg_get_functiondef('kge.guard_frozen()'::regprocedure); BEGIN IF position('ORDER BY v.id FOR SHARE LOOP' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, 'ORDER BY v.id FOR SHARE LOOP', 'ORDER BY v.id LOOP'); END \$\$;"
  ["冻结检查看不到版本时放行"]="DO \$\$ DECLARE d text := pg_get_functiondef('kge.guard_frozen()'::regprocedure); BEGIN IF position('IF v_seen = 0 THEN' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, 'IF v_seen = 0 THEN', 'IF false THEN'); END \$\$;"
  ["发布后原子可以挂停用节点"]="DO \$\$ DECLARE d text := pg_get_functiondef('kge.guard_node_refs()'::regprocedure); BEGIN IF position('AND n.status = ''active'')) THEN' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, 'AND n.status = ''active'')) THEN', ')) THEN'); END \$\$;"
  ["采分点可以原地修改"]="GRANT UPDATE ON kge.scoring_point TO starbuddy_kge;"
  ["采分点编号可以重复"]="ALTER TABLE kge.scoring_point DROP CONSTRAINT scoring_point_subject_item_id_rubric_version_point_id_key;"
  ["同一 Rubric 版本可跨地图版本"]="DROP TRIGGER guard_scoring_point ON kge.scoring_point;"
  ["题目族可以跨版本引用"]="ALTER TABLE kge.item_tag DROP CONSTRAINT item_tag_map_version_id_family_id_fkey; ALTER TABLE kge.item_tag ADD FOREIGN KEY (family_id) REFERENCES kge.item_family (id);"
  ["公共层 jsonb 可以带个人层的键"]="CREATE OR REPLACE FUNCTION kge.has_personal_key(p jsonb) RETURNS boolean LANGUAGE sql IMMUTABLE AS \$\$ SELECT false \$\$;"
  ["个人键检查只看顶层"]="CREATE OR REPLACE FUNCTION kge.has_personal_key(p jsonb) RETURNS boolean LANGUAGE sql IMMUTABLE AS \$\$ SELECT EXISTS (SELECT 1 FROM jsonb_object_keys(CASE WHEN jsonb_typeof(p) = 'object' THEN p ELSE '{}' END) k WHERE k ~* '(student|attempt|evidence_event|account|guardian|household|learner)') \$\$;"
  ["BFF 能看到未核对的投影"]="DROP POLICY textbook_front_read ON kge.textbook_projection; CREATE POLICY textbook_front_read ON kge.textbook_projection FOR SELECT TO starbuddy_bff USING (EXISTS (SELECT 1 FROM kge.map_version v WHERE v.id = map_version_id AND v.status = 'published'));"
  ["BFF 能看到指向草稿的跨版本边"]="DROP POLICY edge_front_read ON kge.knowledge_edge; CREATE POLICY edge_front_read ON kge.knowledge_edge FOR SELECT TO starbuddy_bff USING (status = 'active' AND EXISTS (SELECT 1 FROM kge.map_version v WHERE v.id = map_version_id AND v.status = 'published'));"
  ["校验不查边类型"]="DO \$\$ DECLARE d text := pg_get_functiondef('kge.validate_map_version(uuid)'::regprocedure); BEGIN IF position('SELECT DISTINCT ''edge_kind_mismatch'', ''error''' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, 'SELECT DISTINCT ''edge_kind_mismatch'', ''error''', 'SELECT DISTINCT ''edge_kind_mismatch'', ''warning'''); END \$\$;"
  ["素材可以进前置依赖图"]="DO \$\$ DECLARE d text := pg_get_functiondef('kge.validate_map_version(uuid)'::regprocedure); BEGIN IF position('(k.type = ''prerequisite'' AND ''material'' IN (a.kind, b.kind))' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, '(k.type = ''prerequisite'' AND ''material'' IN (a.kind, b.kind))', 'false'); END \$\$;"
  ["校验不查标签层次"]="DO \$\$ DECLARE d text := pg_get_functiondef('kge.validate_map_version(uuid)'::regprocedure); BEGIN IF position('AND NOT (i.cognitive_level = ANY (n.cognitive_levels))' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, 'AND NOT (i.cognitive_level = ANY (n.cognitive_levels))', 'AND false'); END \$\$;"
  ["知识节点可以不带认知层次"]="DO \$\$ DECLARE d text := pg_get_functiondef('kge.validate_map_version(uuid)'::regprocedure); BEGIN IF position('OR cardinality(n.cognitive_levels) = 0))' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, 'OR cardinality(n.cognitive_levels) = 0))', '))'); END \$\$;"
  ["易错点可以不写识别特征"]="DO \$\$ DECLARE d text := pg_get_functiondef('kge.validate_map_version(uuid)'::regprocedure); BEGIN IF position('OR (n.kind = ''pitfall'' AND cardinality(n.includes) = 0))' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, 'OR (n.kind = ''pitfall'' AND cardinality(n.includes) = 0))', 'OR false)'); END \$\$;"
  ["过期时政不报警告"]="DO \$\$ DECLARE d text := pg_get_functiondef('kge.validate_map_version(uuid)'::regprocedure); BEGIN IF position('c.valid_until < current_date' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, 'c.valid_until < current_date', 'false'); END \$\$;"
  ["跨版本边过期不报"]="DO \$\$ DECLARE d text := pg_get_functiondef('kge.validate_map_version(uuid)'::regprocedure); BEGIN IF position('AND v.status <> ''published''' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, 'AND v.status <> ''published''', 'AND false'); END \$\$;"
  ["攻击面：公共层加无外键的 uuid 列"]="ALTER TABLE kge.map_change ADD COLUMN sample_ref uuid;"
  ["攻击面：公共层文本里写学生 ID"]="INSERT INTO kge.map_change (subject, change_type, payload, evidence_pack) VALUES ('aya', 'other', '{}', '{\"note\": \"c1000000-0000-4000-8000-0000000000c1\"}');"
  ["考情：来源主机名不校验"]="DO \$\$ DECLARE d text := pg_get_functiondef('policy.guard_source_document()'::regprocedure); BEGIN IF position('AND NOT coalesce(policy.lock_active_source_host(policy.url_host(NEW.url)), false) THEN' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, 'AND NOT coalesce(policy.lock_active_source_host(policy.url_host(NEW.url)), false) THEN', 'AND false THEN'); END \$\$;"
  ["考情：已发布来源文件可改内容"]="DO \$\$ DECLARE d text := pg_get_functiondef('policy.guard_source_document()'::regprocedure); BEGIN IF position('IF OLD.status = ''published'' AND (NEW.city,' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, 'IF OLD.status = ''published'' AND (NEW.city,', 'IF false AND (NEW.city,'); END \$\$;"
  ["考情：来源文件可不经签字发布"]="DROP TRIGGER guard_signoff_publish ON policy.source_document;"
  ["考情：适用届别起止不校验"]="ALTER TABLE policy.source_document DROP CONSTRAINT source_document_cohort_range;"
  ["考情：应用角色可登记来源主机名"]="GRANT INSERT ON policy.source_domain TO starbuddy_kge; CREATE POLICY k ON policy.source_domain FOR INSERT TO starbuddy_kge WITH CHECK (true);"
  ["考情：方案可直接以已发布新建"]="DO \$\$ DECLARE d text := pg_get_functiondef('policy.guard_scheme()'::regprocedure); BEGIN IF position('IF NEW.status <> ''draft'' OR NEW.published_at IS NOT NULL THEN' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, 'IF NEW.status <> ''draft'' OR NEW.published_at IS NOT NULL THEN', 'IF false THEN'); END \$\$; DROP TRIGGER guard_signoff_publish ON policy.exam_scheme;"
  ["考情：方案可不经发布函数发布"]="DO \$\$ DECLARE d text := pg_get_functiondef('policy.guard_scheme()'::regprocedure); BEGIN IF position('IF NEW.status = ''published'' AND current_user <> ''starbuddy_authz'' THEN' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, 'IF NEW.status = ''published'' AND current_user <> ''starbuddy_authz'' THEN', 'IF false THEN'); END \$\$;"
  ["考情：已发布方案可改版本号"]="DO \$\$ DECLARE d text := pg_get_functiondef('policy.guard_scheme()'::regprocedure); BEGIN IF position('IF OLD.status <> ''draft'' AND (NEW.version,' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, 'IF OLD.status <> ''draft'' AND (NEW.version,', 'IF false AND (NEW.version,'); END \$\$;"
  ["考情：已撤下方案可复活"]="DO \$\$ DECLARE d text := pg_get_functiondef('policy.guard_scheme()'::regprocedure); BEGIN IF position('OR (OLD.status = ''published'' AND NEW.status = ''retired'')) THEN' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, 'OR (OLD.status = ''published'' AND NEW.status = ''retired'')) THEN', 'OR true) THEN'); END \$\$;"
  ["考情：同城市同届别可有多个已发布方案"]="DROP INDEX policy.exam_scheme_one_published;"
  ["考情：已发布方案的科目配置可改"]="DROP TRIGGER guard_scheme_frozen ON policy.subject_config;"
  ["考情：已发布方案可加招录规则"]="DROP TRIGGER guard_scheme_frozen ON policy.admission_rule;"
  ["考情：已撤下方案的过渡规则可改"]="DROP TRIGGER guard_scheme_frozen ON policy.transition_rule;"
  ["考情：计分科目可不写满分"]="ALTER TABLE policy.subject_config DROP CONSTRAINT subject_config_scored_full_score;"
  ["考情：统招可写控制线（规则）"]="ALTER TABLE policy.admission_rule DROP CONSTRAINT admission_rule_control_line;"
  ["考情：发布不跑校验"]="DO \$\$ DECLARE d text := pg_get_functiondef('policy.publish_scheme(uuid)'::regprocedure); BEGIN IF position('IF v_errors > 0 THEN' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, 'IF v_errors > 0 THEN', 'IF false THEN'); END \$\$;"
  ["考情：发布不撤下旧方案"]="DO \$\$ DECLARE d text := pg_get_functiondef('policy.publish_scheme(uuid)'::regprocedure); BEGIN IF position('UPDATE policy.exam_scheme SET status = ''retired'' WHERE city = s.city AND cohort = s.cohort AND status = ''published'';' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, 'UPDATE policy.exam_scheme SET status = ''retired'' WHERE city = s.city AND cohort = s.cohort AND status = ''published'';', 'NULL;'); END \$\$;"
  ["考情：校验不查复核人"]="DO \$\$ DECLARE d text := pg_get_functiondef('policy.validate_scheme(uuid)'::regprocedure); BEGIN IF position('WHERE policy.approval_count(''exam_scheme'', p_scheme) < policy.signers_required();' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, 'WHERE policy.approval_count(''exam_scheme'', p_scheme) < policy.signers_required();', 'WHERE false;'); END \$\$;"
  ["考情：校验不查合场分组"]="DO \$\$ DECLARE d text := pg_get_functiondef('policy.validate_scheme(uuid)'::regprocedure); BEGIN IF position('HAVING count(*) < 2;' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, 'HAVING count(*) < 2;', 'HAVING false;'); END \$\$;"
  ["考情：校验不查过渡规则"]="DO \$\$ DECLARE d text := pg_get_functiondef('policy.validate_scheme(uuid)'::regprocedure); BEGIN IF position('AND NOT EXISTS (SELECT 1 FROM policy.transition_rule t WHERE t.scheme_id = p_scheme);' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, 'AND NOT EXISTS (SELECT 1 FROM policy.transition_rule t WHERE t.scheme_id = p_scheme);', 'AND false;'); END \$\$;"
  ["招录：分数可超过总分"]="DO \$\$ DECLARE d text := pg_get_functiondef('policy.guard_admission_line()'::regprocedure); BEGIN IF position('IF NEW.line_score > v_total THEN' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, 'IF NEW.line_score > v_total THEN', 'IF false THEN'); END \$\$;"
  ["招录：来源文件不必覆盖该年"]="DO \$\$ DECLARE d text := pg_get_functiondef('policy.guard_admission_line()'::regprocedure); BEGIN IF position('AND NEW.year BETWEEN d.applies_from_cohort AND coalesce(d.applies_to_cohort, NEW.year)) THEN' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, 'AND NEW.year BETWEEN d.applies_from_cohort AND coalesce(d.applies_to_cohort, NEW.year)) THEN', ') THEN'); END \$\$;"
  ["招录：没有方案也能录"]="DO \$\$ DECLARE d text := pg_get_functiondef('policy.guard_admission_line()'::regprocedure); BEGIN IF position('IF v_total IS NULL THEN' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, 'IF v_total IS NULL THEN', 'IF false THEN'); END \$\$;"
  ["招录：已发布行可改数值"]="DO \$\$ DECLARE d text := pg_get_functiondef('policy.guard_admission_line()'::regprocedure); BEGIN IF position('IF OLD.status = ''published'' AND (NEW.status <> ''retired'' OR' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, 'IF OLD.status = ''published'' AND (NEW.status <> ''retired'' OR', 'IF false AND (NEW.status <> ''retired'' OR'); END \$\$;"
  ["招录：招录行可不经签字发布"]="DROP TRIGGER guard_signoff_publish ON policy.admission_line;"
  ["招录：同一行可有两条已发布"]="DROP INDEX policy.admission_line_one_published;"
  ["考情：写入方可以删除"]="GRANT DELETE ON policy.admission_line TO starbuddy_kge;"
  ["考情：BFF 能看草稿方案"]="DROP POLICY exam_scheme_front_read ON policy.exam_scheme; CREATE POLICY exam_scheme_front_read ON policy.exam_scheme FOR SELECT TO starbuddy_bff USING (true);"
  ["考情：BFF 能看草稿方案的科目配置"]="DROP POLICY subject_config_front_read ON policy.subject_config; CREATE POLICY subject_config_front_read ON policy.subject_config FOR SELECT TO starbuddy_bff USING (true);"
  ["考情：BFF 能看复核人"]="GRANT SELECT (reviewers) ON policy.exam_scheme TO starbuddy_bff;"
  ["考情：BFF 能看草稿招录行"]="DROP POLICY admission_line_front_read ON policy.admission_line; CREATE POLICY admission_line_front_read ON policy.admission_line FOR SELECT TO starbuddy_bff USING (true);"
  ["考情：发布函数对学科服务开放"]="GRANT EXECUTE ON FUNCTION policy.publish_scheme(uuid) TO starbuddy_learning;"
  ["学生届别：与年级不符也能写"]="DO \$\$ DECLARE d text := pg_get_functiondef('platform.guard_student_cohort()'::regprocedure); BEGIN IF position('ELSIF abs(NEW.exam_cohort - v_expected) > 1' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, 'ELSIF abs(NEW.exam_cohort - v_expected) > 1', 'ELSIF false'); END \$\$;"
  ["学生届别：推算不按 9 月切换"]="CREATE OR REPLACE FUNCTION platform.expected_cohort(p_grade smallint, p_at timestamptz) RETURNS smallint LANGUAGE sql STABLE SET search_path = pg_catalog, platform, pg_temp AS \$\$ SELECT (extract(year FROM p_at)::int + 1 + (9 - p_grade))::smallint \$\$;"
  ["攻击面：考情库加学生键"]="ALTER TABLE policy.school ADD COLUMN student_id uuid;"
  ["考情：主机名下线后来源文件改不动"]="DO \$\$ DECLARE d text := pg_get_functiondef('policy.guard_source_document()'::regprocedure); BEGIN IF position('IF (TG_OP = ''INSERT'' OR NEW.url IS DISTINCT FROM OLD.url)' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, 'IF (TG_OP = ''INSERT'' OR NEW.url IS DISTINCT FROM OLD.url)', 'IF true'); END \$\$;"
  ["招录：批次类别不对照招录规则"]="DO \$\$ DECLARE d text := pg_get_functiondef('policy.guard_admission_line()'::regprocedure); BEGIN IF position('AND r.batch_code = NEW.batch_code AND r.track = NEW.track) THEN' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, 'AND r.batch_code = NEW.batch_code AND r.track = NEW.track) THEN', 'AND false) THEN'); END \$\$;"
  ["招录：学校未复核也能录"]="DO \$\$ DECLARE d text := pg_get_functiondef('policy.guard_admission_line()'::regprocedure); BEGIN IF position('SELECT s.city INTO v_city FROM policy.school s WHERE s.id = NEW.school_id AND s.review_status = ''published'';' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, 'SELECT s.city INTO v_city FROM policy.school s WHERE s.id = NEW.school_id AND s.review_status = ''published'';', 'SELECT s.city INTO v_city FROM policy.school s WHERE s.id = NEW.school_id;'); END \$\$;"
  ["招录：学校可不经签字发布"]="DROP TRIGGER guard_signoff_publish ON policy.school;"
  ["招录：已发布学校可改名"]="DO \$\$ DECLARE d text := pg_get_functiondef('policy.guard_school()'::regprocedure); BEGIN IF position('IF TG_OP = ''UPDATE'' AND OLD.review_status = ''published''' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, 'IF TG_OP = ''UPDATE'' AND OLD.review_status = ''published''', 'IF false'); END \$\$;"
  ["招录：BFF 能看未复核学校"]="DROP POLICY school_front_read ON policy.school; CREATE POLICY school_front_read ON policy.school FOR SELECT TO starbuddy_bff USING (true);"
  ["复核队列：结案不核对领取人"]="DO \$\$ DECLARE d text := pg_get_functiondef('platform.resolve_review_task(uuid, text)'::regprocedure); BEGIN IF position('OR r.assigned_to IS DISTINCT FROM v_staff THEN' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, 'OR r.assigned_to IS DISTINCT FROM v_staff THEN', 'THEN'); END \$\$;"
  ["复核队列：结案码不查白名单"]="DO \$\$ DECLARE d text := pg_get_functiondef('platform.resolve_review_task(uuid, text)'::regprocedure); BEGIN IF position('ELSIF NOT EXISTS (SELECT 1 FROM platform.review_resolution_code c' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, 'ELSIF NOT EXISTS (SELECT 1 FROM platform.review_resolution_code c', 'ELSIF false AND EXISTS (SELECT 1 FROM platform.review_resolution_code c'); END \$\$;"
  ["复核队列：结案码不看适用范围"]="DO \$\$ DECLARE d text := pg_get_functiondef('platform.resolve_review_task(uuid, text)'::regprocedure); BEGIN IF position('AND r.source_kind = ANY (c.applies_to)' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, 'AND r.source_kind = ANY (c.applies_to)', ''); END \$\$;"
  ["复核队列：别人领取的也能领"]="DO \$\$ DECLARE d text := pg_get_functiondef('platform.take_review_task(uuid)'::regprocedure); BEGIN IF position('ELSIF r.status = ''in_progress'' THEN' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, 'ELSIF r.status = ''in_progress'' THEN', 'ELSIF false THEN'); END \$\$;"
  ["复核队列：领取不加行锁"]="DO \$\$ DECLARE d text := pg_get_functiondef('platform.take_review_task(uuid)'::regprocedure); BEGIN IF position('WHERE id = p_task FOR UPDATE;' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, 'WHERE id = p_task FOR UPDATE;', 'WHERE id = p_task;'); END \$\$;"
  ["复核队列：放回不核对领取人"]="DO \$\$ DECLARE d text := pg_get_functiondef('platform.release_review_task(uuid)'::regprocedure); BEGIN IF position('AND assigned_to = v_staff;' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, 'AND assigned_to = v_staff;', ';'); END \$\$;"
  ["复核队列：停用的 staff 可操作"]="DO \$\$ DECLARE d text := pg_get_functiondef('platform.review_current_staff()'::regprocedure); BEGIN IF position('AND a.status = ''active''' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, 'AND a.status = ''active''', ''); END \$\$;"
  ["复核队列：学习服务进队不核对判分"]="DROP POLICY review_service_open ON platform.review_task; CREATE POLICY review_service_open ON platform.review_task FOR INSERT TO starbuddy_learning WITH CHECK (status = 'open' AND resolved_at IS NULL AND resolved_by_account IS NULL AND assigned_to IS NULL AND assigned_at IS NULL AND NOT ('profile_dispute' = ANY (reasons::text[])) AND source_kind = 'grade_result');"
  ["复核队列：进队原因不核对判分声明"]="DROP POLICY review_service_open ON platform.review_task; CREATE POLICY review_service_open ON platform.review_task FOR INSERT TO starbuddy_learning WITH CHECK (status = 'open' AND resolved_at IS NULL AND resolved_by_account IS NULL AND assigned_to IS NULL AND assigned_at IS NULL AND NOT ('profile_dispute' = ANY (reasons::text[])) AND (source_kind = 'grade_result' AND EXISTS (SELECT 1 FROM platform.grade_result g WHERE g.id = source_id AND g.student_id = review_task.student_id AND g.subject = review_task.subject)));"
  ["复核队列：学习服务可为观测进队"]="DROP POLICY review_service_open ON platform.review_task; CREATE POLICY review_service_open ON platform.review_task FOR INSERT TO starbuddy_learning WITH CHECK (status = 'open' AND resolved_at IS NULL AND resolved_by_account IS NULL AND assigned_to IS NULL AND assigned_at IS NULL AND NOT ('profile_dispute' = ANY (reasons::text[])) AND ((source_kind = 'grade_result' AND EXISTS (SELECT 1 FROM platform.grade_result g WHERE g.id = source_id AND g.student_id = review_task.student_id AND g.subject = review_task.subject AND review_task.reasons::text[] <@ g.review_reasons::text[])) OR source_kind IN ('skill_observation', 'error_attribution')));"
  ["复核队列：学习服务可指定创建时间"]="GRANT INSERT (created_at) ON platform.review_task TO starbuddy_learning;"
  ["复核队列：已结案的也能领"]="DO \$\$ DECLARE d text := pg_get_functiondef('platform.take_review_task(uuid)'::regprocedure); BEGIN IF position('ELSIF r.status = ''resolved'' THEN' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, 'ELSIF r.status = ''resolved'' THEN', 'ELSIF false THEN'); END \$\$;"
  ["复核队列：读函数授给 BFF"]="GRANT EXECUTE ON FUNCTION platform.review_queue(platform.subject, text, text, integer, timestamptz, uuid) TO starbuddy_bff;"
  ["复核队列：详情带回 student_id"]="DO \$\$ DECLARE d text := pg_get_functiondef('platform.review_task_detail(uuid)'::regprocedure); BEGIN IF position('''student_ref'', platform.review_student_ref(r.student_id)' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, '''student_ref'', platform.review_student_ref(r.student_id)', '''student_ref'', r.student_id::text'); END \$\$;"
  ["复核队列：翻页游标不含 id"]="DO \$\$ DECLARE d text := pg_get_functiondef('platform.review_queue(platform.subject, text, text, integer, timestamptz, uuid)'::regprocedure); BEGIN IF position('(t.created_at, t.id) < (p_before, coalesce(p_before_id, ''ffffffff-ffff-ffff-ffff-ffffffffffff''::uuid))' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, '(t.created_at, t.id) < (p_before, coalesce(p_before_id, ''ffffffff-ffff-ffff-ffff-ffffffffffff''::uuid))', 't.created_at < p_before'); END \$\$;"
  ["打标：知识服务可改候选结论"]="GRANT UPDATE (status, decided_at) ON kge.tag_candidate TO starbuddy_kge; CREATE POLICY m ON kge.tag_candidate FOR UPDATE TO starbuddy_kge USING (true) WITH CHECK (true);"
  ["打标：知识服务可写已处理的候选"]="DROP POLICY tag_candidate_kge_submit ON kge.tag_candidate; CREATE POLICY tag_candidate_kge_submit ON kge.tag_candidate FOR INSERT TO starbuddy_kge WITH CHECK (true);"
  ["打标：处理候选不核对 staff"]="DO \$\$ DECLARE d text := pg_get_functiondef('kge.decide_tag_candidate(uuid, text)'::regprocedure); BEGIN IF position('v_staff uuid := platform.review_current_staff();' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, 'v_staff uuid := platform.review_current_staff();', 'v_staff uuid := platform.current_account_id();'); END \$\$;"
  ["打标：已处理的候选可再处理"]="DO \$\$ DECLARE d text := pg_get_functiondef('kge.decide_tag_candidate(uuid, text)'::regprocedure); BEGIN IF position('ELSIF c.status <> ''open'' THEN' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, 'ELSIF c.status <> ''open'' THEN', 'ELSIF false THEN'); END \$\$;"
  ["打标：模型层候选可不带模型版本"]="ALTER TABLE kge.tag_candidate DROP CONSTRAINT tag_candidate_model_version;"
  ["打标：来源与版本可不成对"]="ALTER TABLE kge.item_tag DROP CONSTRAINT item_tag_source_complete;"
  ["打标：学科服务可读候选"]="GRANT SELECT ON kge.tag_candidate TO starbuddy_learning; CREATE POLICY m ON kge.tag_candidate FOR SELECT TO starbuddy_learning USING (true);"
  ["行为事件：不核对学生本人"]="DO \$\$ DECLARE d text := pg_get_functiondef('insight.record_behavior_event(uuid, text, jsonb, timestamptz, uuid)'::regprocedure); BEGIN IF position('IF p_student IS NULL OR NOT platform.is_self_student(p_student) THEN' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, 'IF p_student IS NULL OR NOT platform.is_self_student(p_student) THEN', 'IF p_student IS NULL OR NOT platform.can_read_student(p_student) THEN'); END \$\$;"
  ["行为事件：学生端可写派生事件"]="DO \$\$ DECLARE d text := pg_get_functiondef('insight.record_behavior_event(uuid, text, jsonb, timestamptz, uuid)'::regprocedure); BEGIN IF position('p_kind NOT IN (''session_start'', ''session_end'', ''plan_done'', ''plan_skip'')' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, 'p_kind NOT IN (''session_start'', ''session_end'', ''plan_done'', ''plan_skip'')', 'p_kind NOT IN (''session_start'', ''session_end'', ''plan_done'', ''plan_skip'', ''exit_after_errors'', ''appeal'')'); END \$\$;"
  ["行为事件：payload 不限键"]="ALTER TABLE insight.behavior_event DROP CONSTRAINT behavior_event_payload; DO \$\$ DECLARE d text := pg_get_functiondef('insight.record_behavior_event(uuid, text, jsonb, timestamptz, uuid)'::regprocedure); BEGIN IF position('OR p_payload IS NULL OR NOT coalesce(insight.valid_behavior_payload(p_kind, p_payload), false)' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, 'OR p_payload IS NULL OR NOT coalesce(insight.valid_behavior_payload(p_kind, p_payload), false)', 'OR p_payload IS NULL'); END \$\$;"
  ["行为事件：会话时长不设上限"]="DO \$\$ DECLARE d text := pg_get_functiondef('insight.valid_behavior_payload(text, jsonb)'::regprocedure); BEGIN IF position('AND (p->>''duration_sec'') ~ ''^[0-9]{1,5}$'' AND (p->>''duration_sec'')::integer BETWEEN 0 AND 86400' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, 'AND (p->>''duration_sec'') ~ ''^[0-9]{1,5}$'' AND (p->>''duration_sec'')::integer BETWEEN 0 AND 86400', 'AND (p->>''duration_sec'')::numeric >= 0'); END \$\$;"
  ["行为事件：时间不设窗口"]="DO \$\$ DECLARE d text := pg_get_functiondef('insight.record_behavior_event(uuid, text, jsonb, timestamptz, uuid)'::regprocedure); BEGIN IF position('OR p_occurred_at IS NULL OR p_occurred_at < now() - interval ''1 day'' OR p_occurred_at > now() + interval ''5 minutes''' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, 'OR p_occurred_at IS NULL OR p_occurred_at < now() - interval ''1 day'' OR p_occurred_at > now() + interval ''5 minutes''', 'OR p_occurred_at IS NULL'); END \$\$;"
  ["行为事件：来源可不是客观记录"]="ALTER TABLE insight.behavior_event DROP CONSTRAINT behavior_event_source_by_kind;"
  ["探针：不核对学生本人"]="DO \$\$ DECLARE d text := pg_get_functiondef('insight.record_confidence_probe(uuid, platform.subject, text, text, timestamptz, uuid)'::regprocedure); BEGIN IF position('IF p_student IS NULL OR NOT platform.is_self_student(p_student) THEN' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, 'IF p_student IS NULL OR NOT platform.is_self_student(p_student) THEN', 'IF p_student IS NULL OR NOT platform.can_read_student(p_student) THEN'); END \$\$;"
  ["探针：关联别人的作答"]="DO \$\$ DECLARE d text := pg_get_functiondef('insight.link_probe_attempt(uuid, uuid, uuid)'::regprocedure); BEGIN IF position('WHERE a.attempt_id = p_attempt AND a.student_id = p_student' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, 'WHERE a.attempt_id = p_attempt AND a.student_id = p_student', 'WHERE a.attempt_id = p_attempt'); END \$\$;"
  ["探针：关联不同题的作答"]="DO \$\$ DECLARE d text := pg_get_functiondef('insight.link_probe_attempt(uuid, uuid, uuid)'::regprocedure); BEGIN IF position('AND a.subject = p.subject AND a.item_id = p.item_id' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, 'AND a.subject = p.subject AND a.item_id = p.item_id', ''); END \$\$;"
  ["探针：已关联可改"]="DO \$\$ DECLARE d text := pg_get_functiondef('insight.link_probe_attempt(uuid, uuid, uuid)'::regprocedure); BEGIN IF position('IF p.attempt_id = p_attempt THEN RETURN; END IF;
    RAISE EXCEPTION ''探针已关联别的作答'' USING ERRCODE = ''unique_violation'';' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, 'IF p.attempt_id = p_attempt THEN RETURN; END IF;
    RAISE EXCEPTION ''探针已关联别的作答'' USING ERRCODE = ''unique_violation'';', 'NULL;'); END \$\$;"
  ["行为事件：家长可读"]="GRANT SELECT ON insight.behavior_event TO starbuddy_bff; CREATE POLICY m ON insight.behavior_event FOR SELECT TO starbuddy_bff USING (platform.can_read_student(student_id));"
  ["行为事件：不随学生档案删除"]="ALTER TABLE insight.behavior_event DROP CONSTRAINT behavior_event_student_id_fkey; ALTER TABLE insight.behavior_event ADD CONSTRAINT behavior_event_student_id_fkey FOREIGN KEY (student_id) REFERENCES platform.student_profile (id);"
  ["打标：已发布版本也写回"]="DO \$\$ DECLARE d text := pg_get_functiondef('kge.decide_tag_candidate(uuid, text)'::regprocedure); BEGIN IF position('OR v_status <> ''draft'' THEN' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, 'OR v_status <> ''draft'' THEN', 'OR false THEN'); END \$\$;"
  ["打标：已有打标时主考点被覆盖"]="DO \$\$ DECLARE d text := pg_get_functiondef('kge.decide_tag_candidate(uuid, text)'::regprocedure); BEGIN IF position('    IF v_found THEN
      RAISE EXCEPTION ''该题已有主考点' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, '    IF v_found THEN
      RAISE EXCEPTION ''该题已有主考点', '    IF false THEN
      RAISE EXCEPTION ''该题已有主考点'); END \$\$;"
  ["打标：次要节点不设上限"]="DO \$\$ DECLARE d text := pg_get_functiondef('kge.decide_tag_candidate(uuid, text)'::regprocedure); BEGIN IF position(' OR cardinality(t.node_ids) >= 3 THEN' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, ' OR cardinality(t.node_ids) >= 3 THEN', ' THEN'); END \$\$;"
  ["打标：不记处理人"]="DO \$\$ DECLARE d text := pg_get_functiondef('kge.decide_tag_candidate(uuid, text)'::regprocedure); BEGIN IF position('INSERT INTO platform.tag_candidate_signoff (tag_candidate_id, account_id, decision) VALUES (p_candidate, v_staff, ''accepted'');' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, 'INSERT INTO platform.tag_candidate_signoff (tag_candidate_id, account_id, decision) VALUES (p_candidate, v_staff, ''accepted'');', ''); END \$\$;"
  ["打标：知识服务可写人工来源"]="DROP TRIGGER guard_human_tag ON kge.item_tag;"
  ["打标：接受主考点不驳回同题其他候选"]="DO \$\$ DECLARE d text := pg_get_functiondef('kge.decide_tag_candidate(uuid, text)'::regprocedure); BEGIN IF position('AND status = ''open'' AND id <> p_candidate' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, 'AND status = ''open'' AND id <> p_candidate', 'AND false'); END \$\$;"
  ["打标：主题可作主考点"]="DO \$\$ DECLARE d text := pg_get_functiondef('kge.decide_tag_candidate(uuid, text)'::regprocedure); BEGIN IF position('IF v_level IS DISTINCT FROM ''node'' THEN' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, 'IF v_level IS DISTINCT FROM ''node'' THEN', 'IF false THEN'); END \$\$;"
  ["打标：签字表可删"]="DROP TRIGGER reject_signoff_change ON platform.tag_candidate_signoff;"
  ["打标：签字表可清空"]="DROP TRIGGER reject_signoff_truncate ON platform.tag_candidate_signoff;"
  ["行为事件：幂等键内容不同不报冲突"]="DO \$\$ DECLARE d text := pg_get_functiondef('insight.record_behavior_event(uuid, text, jsonb, timestamptz, uuid)'::regprocedure); BEGIN IF position('IF e.kind IS DISTINCT FROM p_kind OR e.payload IS DISTINCT FROM p_payload OR e.occurred_at IS DISTINCT FROM p_occurred_at THEN' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, 'IF e.kind IS DISTINCT FROM p_kind OR e.payload IS DISTINCT FROM p_payload OR e.occurred_at IS DISTINCT FROM p_occurred_at THEN', 'IF false THEN'); END \$\$;"
  ["行为事件：不设写入上限"]="DO \$\$ DECLARE d text := pg_get_functiondef('insight.record_behavior_event(uuid, text, jsonb, timestamptz, uuid)'::regprocedure); BEGIN IF position('>= 2000 THEN' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, '>= 2000 THEN', '>= 2000000 THEN'); END \$\$;"
  ["探针：不设写入上限"]="DO \$\$ DECLARE d text := pg_get_functiondef('insight.record_confidence_probe(uuid, platform.subject, text, text, timestamptz, uuid)'::regprocedure); BEGIN IF position('>= 500 THEN' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, '>= 500 THEN', '>= 500000 THEN'); END \$\$;"
  ["诊断：家长可读会话"]="DROP POLICY diagnosis_session_self ON platform.diagnosis_session; CREATE POLICY diagnosis_session_self ON platform.diagnosis_session FOR SELECT TO starbuddy_bff USING (platform.can_read_student(student_id));"
  ["诊断：家长可读步骤"]="DROP POLICY diagnosis_step_self ON platform.diagnosis_step; CREATE POLICY diagnosis_step_self ON platform.diagnosis_step FOR SELECT TO starbuddy_bff USING (EXISTS (SELECT 1 FROM platform.diagnosis_session s WHERE s.id = session_id AND platform.can_read_student(s.student_id)));"
  ["诊断：学生可读选题理由"]="GRANT SELECT (reason_code) ON platform.diagnosis_step TO starbuddy_bff;"
  ["诊断：BFF 可写会话"]="GRANT INSERT ON platform.diagnosis_session TO starbuddy_bff; CREATE POLICY m ON platform.diagnosis_session FOR INSERT TO starbuddy_bff WITH CHECK (true);"
  ["诊断：可同时开多个会话"]="DROP INDEX platform.diagnosis_session_open_uq;"
  ["诊断：用时可倒退"]="DO \$\$ DECLARE d text := pg_get_functiondef('platform.diagnosis_session_guard()'::regprocedure); BEGIN IF position('IF NEW.elapsed_sec < OLD.elapsed_sec OR NEW.item_count < OLD.item_count OR' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, 'IF NEW.elapsed_sec < OLD.elapsed_sec OR NEW.item_count < OLD.item_count OR', 'IF'); END \$\$;"
  ["诊断：结束的会话可重开"]="DO \$\$ DECLARE d text := pg_get_functiondef('platform.diagnosis_session_guard()'::regprocedure); BEGIN IF position('IF OLD.status IN (''finished'', ''abandoned'') AND NEW IS DISTINCT FROM OLD THEN' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, 'IF OLD.status IN (''finished'', ''abandoned'') AND NEW IS DISTINCT FROM OLD THEN', 'IF false THEN'); END \$\$;"
  ["诊断：步骤可挂别人的判分"]="DO \$\$ DECLARE d text := pg_get_functiondef('platform.diagnosis_step_guard()'::regprocedure); BEGIN IF position('WHERE g.id = NEW.grade_result_id AND g.student_id = s.student_id AND g.subject = s.subject' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, 'WHERE g.id = NEW.grade_result_id AND g.student_id = s.student_id AND g.subject = s.subject', 'WHERE g.id = NEW.grade_result_id'); END \$\$;"
  ["诊断：结束的步骤可再改"]="DO \$\$ DECLARE d text := pg_get_functiondef('platform.diagnosis_step_guard()'::regprocedure); BEGIN IF position('IF TG_OP = ''UPDATE'' AND OLD.status IN' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, 'IF TG_OP = ''UPDATE'' AND OLD.status IN', 'IF false AND OLD.status IN'); END \$\$;"
  ["诊断：结束的会话可加步骤"]="DO \$\$ DECLARE d text := pg_get_functiondef('platform.diagnosis_step_guard()'::regprocedure); BEGIN IF position('IF TG_OP = ''INSERT'' AND s.status <> ''active'' THEN' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, 'IF TG_OP = ''INSERT'' AND s.status <> ''active'' THEN', 'IF false THEN'); END \$\$;"
  ["诊断：未作答的过程题可进队"]="DROP POLICY review_service_open ON platform.review_task; CREATE POLICY review_service_open ON platform.review_task FOR INSERT TO starbuddy_learning WITH CHECK (status = 'open' AND ((source_kind = 'grade_result' AND EXISTS (SELECT 1 FROM platform.grade_result g WHERE g.id = source_id AND g.student_id = review_task.student_id AND g.subject = review_task.subject AND review_task.reasons::text[] <@ g.review_reasons::text[])) OR (source_kind = 'diagnosis_step' AND EXISTS (SELECT 1 FROM platform.diagnosis_step st JOIN platform.diagnosis_session s ON s.id = st.session_id WHERE st.id = source_id AND s.student_id = review_task.student_id))));"
  ["诊断：过程观察可挂判分"]="ALTER TABLE platform.review_task DROP CONSTRAINT review_task_diagnosis_step;"
  ["诊断：审核台详情给选题理由"]="DO \$\$ DECLARE d text := pg_get_functiondef('platform.review_task_detail(uuid)'::regprocedure); BEGIN IF position('''kind'', st.kind, ''node_id''' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, '''kind'', st.kind, ''node_id''', '''kind'', st.kind, ''reason_code'', st.reason_code, ''node_id'''); END \$\$;"
  ["诊断：判分可以不是本题的"]="DO \$\$ DECLARE d text := pg_get_functiondef('platform.diagnosis_step_guard()'::regprocedure); BEGIN IF position(' AND a.item_id = NEW.item_id) THEN' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, ' AND a.item_id = NEW.item_id) THEN', ') THEN'); END \$\$;"
  ["诊断：结束会话的步骤可改"]="DO \$\$ DECLARE d text := pg_get_functiondef('platform.diagnosis_step_guard()'::regprocedure); BEGIN IF position('IF TG_OP = ''UPDATE'' AND s.status NOT IN (''active'', ''paused'') THEN' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, 'IF TG_OP = ''UPDATE'' AND s.status NOT IN (''active'', ''paused'') THEN', 'IF false THEN'); END \$\$;"
  ["诊断：过程原文可挂别人的"]="DO \$\$ DECLARE d text := pg_get_functiondef('platform.diagnosis_step_guard()'::regprocedure); BEGIN IF position('WHERE e.evidence_id = NEW.work_evidence_id AND e.student_id = s.student_id' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, 'WHERE e.evidence_id = NEW.work_evidence_id AND e.student_id = s.student_id', 'WHERE e.evidence_id = NEW.work_evidence_id'); END \$\$;"
  ["诊断：自适应题可挂过程原文"]="ALTER TABLE platform.diagnosis_step DROP CONSTRAINT diagnosis_step_work;"
  ["诊断：续做时间可倒退"]="DO \$\$ DECLARE d text := pg_get_functiondef('platform.diagnosis_session_guard()'::regprocedure); BEGIN IF position(' OR NEW.resumed_at < OLD.resumed_at THEN' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, ' OR NEW.resumed_at < OLD.resumed_at THEN', ' THEN'); END \$\$;"
  ["诊断：审核台详情不给过程原文"]="DO \$\$ DECLARE d text := pg_get_functiondef('platform.review_task_detail(uuid)'::regprocedure); BEGIN IF position('''work_evidence_id'', st.work_evidence_id,' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, '''work_evidence_id'', st.work_evidence_id,', ''); END \$\$;"
  ["洞察读权限：可读证据原文"]="GRANT SELECT (payload) ON platform.evidence_event TO starbuddy_insight;"
  ["洞察读权限：可读判分维度"]="GRANT SELECT ON platform.grade_dimension TO starbuddy_insight; CREATE POLICY m ON platform.grade_dimension FOR SELECT TO starbuddy_insight USING (true);"
  ["洞察读权限：可改掌握估计"]="GRANT UPDATE (confidence) ON platform.mastery_estimate TO starbuddy_insight; CREATE POLICY m ON platform.mastery_estimate FOR UPDATE TO starbuddy_insight USING (true) WITH CHECK (true);"
  ["洞察读权限：可读家庭绑定"]="GRANT SELECT ON platform.guardian_student_link TO starbuddy_insight; CREATE POLICY m ON platform.guardian_student_link FOR SELECT TO starbuddy_insight USING (true);"
  ["只读角色：可读草稿与退役版本"]="DROP POLICY map_version_reader ON kge.map_version; CREATE POLICY map_version_reader ON kge.map_version FOR SELECT TO starbuddy_kge_read USING (true);"
  ["只读角色：可读停用节点"]="DROP POLICY node_reader ON kge.knowledge_node; CREATE POLICY node_reader ON kge.knowledge_node FOR SELECT TO starbuddy_kge_read USING (kge.reader_version_published(map_version_id));"
  ["只读角色：可读未核对投影"]="DROP POLICY textbook_reader ON kge.textbook_projection; CREATE POLICY textbook_reader ON kge.textbook_projection FOR SELECT TO starbuddy_kge_read USING (kge.reader_version_published(map_version_id));"
  ["只读角色：可读待审标签"]="DROP POLICY item_tag_reader ON kge.item_tag; CREATE POLICY item_tag_reader ON kge.item_tag FOR SELECT TO starbuddy_kge_read USING (kge.reader_version_published(map_version_id));"
  ["只读角色：可读过期时政"]="DROP POLICY current_affair_reader ON kge.current_affair; CREATE POLICY current_affair_reader ON kge.current_affair FOR SELECT TO starbuddy_kge_read USING (review_status = 'published' AND kge.reader_version_published(map_version_id));"
  ["只读角色：跨版本边不核对对端"]="DROP POLICY edge_reader ON kge.knowledge_edge; CREATE POLICY edge_reader ON kge.knowledge_edge FOR SELECT TO starbuddy_kge_read USING (status = 'active' AND kge.reader_version_published(map_version_id));"
  ["只读角色：版本判定改为定义者权限"]="ALTER FUNCTION kge.reader_version_published(uuid) SECURITY DEFINER;"
  ["只读角色：可读审核记录"]="GRANT SELECT ON kge.content_review TO starbuddy_kge_read; CREATE POLICY m ON kge.content_review FOR SELECT TO starbuddy_kge_read USING (true);"
  ["只读角色：可读核对人"]="GRANT SELECT (verified_by) ON kge.textbook_projection TO starbuddy_kge_read;"
  ["只读角色：可写标签"]="GRANT UPDATE (tag_review_status) ON kge.item_tag TO starbuddy_kge_read; CREATE POLICY m ON kge.item_tag FOR UPDATE TO starbuddy_kge_read USING (true) WITH CHECK (true);"
  ["只读角色：可读题目登记"]="GRANT SELECT ON platform.item TO starbuddy_kge_read; CREATE POLICY m ON platform.item FOR SELECT TO starbuddy_kge_read USING (true);"
  ["只读角色：可调发布函数"]="GRANT EXECUTE ON FUNCTION kge.publish_map_version(uuid) TO starbuddy_kge_read;"
  ["探针：关联不设时间上限"]="DO \$\$ DECLARE d text := pg_get_functiondef('insight.link_probe_attempt(uuid, uuid, uuid)'::regprocedure); BEGIN IF position(' AND a.occurred_at <= p.created_at + interval ''2 hours''' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, ' AND a.occurred_at <= p.created_at + interval ''2 hours''', ''); END \$\$;"
  ["探针：关联用客户端提问时间"]="DO \$\$ DECLARE d text := pg_get_functiondef('insight.link_probe_attempt(uuid, uuid, uuid)'::regprocedure); BEGIN IF position('a.occurred_at >= p.created_at - interval ''5 minutes''' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, 'a.occurred_at >= p.created_at - interval ''5 minutes''', 'a.occurred_at >= p.asked_at - interval ''5 minutes'''); END \$\$;"
  ["探针：关联不核对学生本人"]="DO \$\$ DECLARE d text := pg_get_functiondef('insight.link_probe_attempt(uuid, uuid, uuid)'::regprocedure); BEGIN IF position('IF p_student IS NULL OR NOT platform.is_self_student(p_student) THEN' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, 'IF p_student IS NULL OR NOT platform.is_self_student(p_student) THEN', 'IF p_student IS NULL OR NOT platform.can_read_student(p_student) THEN'); END \$\$;"
  ["行为事件：写入方不设约束"]="ALTER TABLE insight.behavior_event DROP CONSTRAINT behavior_event_writer;"
  ["行为事件：重放不标记"]="DO \$\$ DECLARE d text := pg_get_functiondef('insight.record_behavior_event(uuid, text, jsonb, timestamptz, uuid)'::regprocedure); BEGIN IF position('RETURN QUERY SELECT e.id, true;' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, 'RETURN QUERY SELECT e.id, true;', 'RETURN QUERY SELECT e.id, false;'); END \$\$;"
  ["复核队列：审核台可直接读队列表"]="GRANT SELECT ON platform.review_task TO starbuddy_review; CREATE POLICY rq ON platform.review_task FOR SELECT TO starbuddy_review USING (true);"
  ["复核队列：学生指针就是 student_id"]="CREATE OR REPLACE FUNCTION platform.review_student_ref(p_student uuid) RETURNS text LANGUAGE sql IMMUTABLE STRICT SET search_path = pg_catalog AS \$f\$ SELECT p_student::text \$f\$;"
  ["复核队列：审核台可抽检"]="GRANT EXECUTE ON FUNCTION platform.review_sample_spot_checks(platform.subject, timestamptz, numeric) TO starbuddy_review;"
  ["复核队列：抽检比例不设上限"]="DO \$\$ DECLARE d text := pg_get_functiondef('platform.review_sample_spot_checks(platform.subject, timestamptz, numeric)'::regprocedure); BEGIN IF position('OR p_ratio > 0.5' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, 'OR p_ratio > 0.5', ''); END \$\$;"
  ["复核队列：抽检不限往前天数"]="DO \$\$ DECLARE d text := pg_get_functiondef('platform.review_sample_spot_checks(platform.subject, timestamptz, numeric)'::regprocedure); BEGIN IF position('p_since < now() - interval ''31 days'' OR ' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, 'p_since < now() - interval ''31 days'' OR ', ''); END \$\$;"
  ["复核队列：抽检不按哈希"]="DO \$\$ DECLARE d text := pg_get_functiondef('platform.review_sample_spot_checks(platform.subject, timestamptz, numeric)'::regprocedure); BEGIN IF position('AND (''x'' || substr(md5(g.id::text), 1, 8))::bit(32)::bigint < floor(p_ratio * 4294967296)' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, 'AND (''x'' || substr(md5(g.id::text), 1, 8))::bit(32)::bigint < floor(p_ratio * 4294967296)', ''); END \$\$;"
  ["复核队列：画像异议不查终态"]="DO \$\$ DECLARE d text := pg_get_functiondef('platform.enqueue_claim_dispute(uuid)'::regprocedure); BEGIN IF position('OR c.status::text IN (''refuted'', ''withdrawn'')' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, 'OR c.status::text IN (''refuted'', ''withdrawn'')', ''); END \$\$;"
  ["复核队列：已复核的判断可再入队"]="DO \$\$ DECLARE d text := pg_get_functiondef('platform.enqueue_claim_dispute(uuid)'::regprocedure); BEGIN IF position('IF v_status = ''resolved'' THEN' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, 'IF v_status = ''resolved'' THEN', 'IF false THEN'); END \$\$;"
  ["复核队列：学习服务可调用异议入队"]="GRANT EXECUTE ON FUNCTION platform.enqueue_claim_dispute(uuid) TO starbuddy_learning;"
  ["复核队列：指向判断的任务不限原因"]="ALTER TABLE platform.review_task DROP CONSTRAINT review_task_claim_is_dispute;"
  ["复核队列：处理中可无领取人"]="ALTER TABLE platform.review_task DROP CONSTRAINT review_task_assignment;"
  ["复核队列：详情带判断 value"]="DO \$\$ DECLARE d text := pg_get_functiondef('platform.review_task_detail(uuid)'::regprocedure); BEGIN IF position('''evidence_ids'', c.evidence_ids, ''created_at'', c.created_at)' IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, '''evidence_ids'', c.evidence_ids, ''created_at'', c.created_at)', '''evidence_ids'', c.evidence_ids, ''created_at'', c.created_at, ''value'', c.value)'); END \$\$;"
  ["复核队列：BFF 可读结案码"]="GRANT SELECT ON platform.review_resolution_code TO starbuddy_bff; CREATE POLICY rc ON platform.review_resolution_code FOR SELECT TO starbuddy_bff USING (true);"
)

# 家庭登录与会话（0029，specs/unified-shell-sprint4 H1）：由 79k_auth_session 发现
afr() {  # 函数定义里的文本替换（与 services/bff/tests/mutations.sh 的 fr 相同）；找不到模式时让变异本身失败
  printf "DO \$\$ DECLARE d text := pg_get_functiondef('%s'::regprocedure); BEGIN IF position(%s IN d) = 0 THEN RAISE EXCEPTION 'pattern not found'; END IF; EXECUTE replace(d, %s, %s); END \$\$;" "$1" "$2" "$2" "$3"
}
AI='platform.auth_otp_issue(text, bytea, integer, integer)'
AL='platform.auth_otp_login(text, bytea, integer, integer, text)'
AR='platform.auth_session_resolve(text)'
AS='platform.auth_issue_student_session(uuid, text, integer, integer)'
M+=(
  ["认证：验证码不检查过期"]="$(afr "$AL" "'IF NOT FOUND OR c.expires_at <= v_now THEN'" "'IF NOT FOUND THEN'")"
  ["认证：错码不计次"]="$(afr "$AL" "'SET failed_attempts = c.failed_attempts + 1,'" "'SET failed_attempts = c.failed_attempts,'")"
  ["认证：错满不作废"]="$(afr "$AL" "'>= c.max_attempts'" "'>= c.max_attempts + 100'")"
  ["认证：验证码可重用"]="$(afr "$AL" "'UPDATE platform.auth_otp_challenge SET closed_at = v_now, closed_reason = ''used'' WHERE id = c.id;'" "''")"
  ["认证：同号多家长账号不拒绝"]="$(afr "$AL" "'IF v_n > 1 THEN'" "'IF false THEN'")"
  ["认证：手机号也匹配学生账号"]="$(afr "$AL" "'WHERE ct.phone_e164 = p_phone AND a.kind = ''guardian'';'" "'WHERE ct.phone_e164 = p_phone;'")"
  ["认证：停用家长可登录"]="$(afr "$AL" "'ELSIF v_status <> ''active'' THEN'" "'ELSIF false THEN'")"
  ["认证：登录不废本设备旧令牌"]="$(afr "$AL" "'WHERE token_hash = platform.auth_token_hash(p_prev_token) AND revoked_at IS NULL;'" "'WHERE false;'")"
  ["认证：新申请不作废旧码"]="$(afr "$AI" "'SET closed_at = v_now, closed_reason = ''superseded'''" "'SET closed_at = closed_at, closed_reason = closed_reason'")"
  ["认证：同号可有两个未关闭挑战"]="DROP INDEX platform.auth_otp_open_uq;"
  ["认证：会话不查空闲"]="$(afr "$AR" "' AND s.last_seen_at + s.idle_timeout > v_now'" "''")"
  ["认证：会话不查绝对期限"]="$(afr "$AR" "' AND s.expires_at > v_now AND s.last_seen_at'" "' AND s.last_seen_at'")"
  ["认证：会话不查账号状态"]="$(afr "$AR" "'WHERE a.id = s.account_id AND a.status = ''active'''" "'WHERE a.id = s.account_id'")"
  ["认证：会话不核对来源与账号类型"]="$(afr "$AR" "'AND ((s.origin = ''otp'' AND a.kind = ''guardian'') OR (s.origin = ''guardian_handoff'' AND a.kind = ''student'')))'" "')'")"
  ["认证：登出不废会话"]="$(afr 'platform.auth_session_revoke(text)' "'WHERE token_hash = platform.auth_token_hash(p_token) AND revoked_at IS NULL;'" "'WHERE false;'")"
  ["认证：退出所有设备只废本会话"]="$(afr 'platform.auth_session_revoke_all(text)' "'WHERE account_id = v_acct AND revoked_at IS NULL;'" "'WHERE token_hash = platform.auth_token_hash(p_token) AND revoked_at IS NULL;'")"
  ["认证：学生模式不核对是哪位家长的绑定"]="$(afr "$AS" "'AND l.guardian_account_id = s.account_id AND l.status'" "'AND l.status'")"
  ["认证：学生模式认已解除的绑定"]="$(afr "$AS" "'AND l.status = ''active'') THEN'" "') THEN'")"
  ["认证：学生模式不核对当前账号"]="$(afr "$AS" "'OR platform.current_account_id() IS DISTINCT FROM s.account_id'" "''")"
  ["认证：学生模式不查隐私政策同意"]="$(afr "$AS" "'IF coalesce((SELECT cr.decision'" "'IF false AND coalesce((SELECT cr.decision'")"
  ["认证：不满 14 不要求监护人同意"]="$(afr "$AS" "'(v_birth IS NULL OR (v_birth + interval ''1 month'' - interval ''1 day'' + interval ''14 years'')::date > v_today)'" "'false'")"
  ["认证：出生年月为空按已满 14"]="$(afr "$AS" "'v_birth IS NULL OR '" "''")"
  ["认证：按出生月第一天算年龄"]="$(afr "$AS" "'v_birth + interval ''1 month'' - interval ''1 day'' + interval ''14 years'''" "'v_birth + interval ''14 years'''")"
  ["认证：学生模式不废家长会话"]="$(afr "$AS" "'UPDATE platform.auth_session SET revoked_at = v_now, revoked_reason = ''student_mode'' WHERE id = s.id;'" "''")"
  ["认证：限流不设上限"]="$(afr 'platform.auth_rate_hit(text, bytea, integer, integer)' "'RETURN v_hits <= p_limit;'" "'RETURN true;'")"
  ["认证：限流不清旧窗口"]="$(afr 'platform.auth_rate_hit(text, bytea, integer, integer)' "'DELETE FROM platform.auth_rate_counter WHERE bucket = p_bucket AND key_digest = p_key AND window_start < v_start;'" "''")"
  ["认证：BFF 可直接读会话表"]="GRANT SELECT ON platform.auth_session TO starbuddy_bff; CREATE POLICY m ON platform.auth_session FOR SELECT TO starbuddy_bff USING (true);"
  ["认证：学习服务可解析会话"]="GRANT EXECUTE ON FUNCTION platform.auth_session_resolve(text) TO starbuddy_learning;"
  ["认证：BFF 可直接建会话"]="GRANT EXECUTE ON FUNCTION platform.auth_new_session(uuid, text, uuid, timestamptz, integer, integer) TO starbuddy_bff;"
)

# 计量只读函数（0030，specs/unified-shell-sprint4 H3）：由 79l_meter_guard 发现（去掉授权检查也被 80 的函数越权矩阵发现）
MG='platform.meter_guard_status(uuid)'
M+=(
  ["计量只读：去掉授权检查"]="$(afr "$MG" "'IF p_student IS NULL OR NOT platform.can_read_student(p_student) THEN'" "'IF p_student IS NULL THEN'")"
  ["计量只读：只认学生本人"]="$(afr "$MG" "'NOT platform.can_read_student(p_student)'" "'NOT platform.is_self_student(p_student)'")"
  ["计量只读：只认家长"]="$(afr "$MG" "'NOT platform.can_read_student(p_student)'" "'NOT platform.is_linked_guardian(p_student)'")"
  ["计量只读：用档位上限代替生效上限"]="$(afr "$MG" "'v_cap := platform.meter_daily_cap(p_student);'" "'v_cap := platform.meter_plan_daily_cap(p_student);'")"
  ["计量只读：剩余分钟可为负"]="$(afr "$MG" "'greatest(v_cap - v_used, 0)'" "'v_cap - v_used'")"
  ["计量只读：时钟用系统时间"]="$(afr "$MG" "'v_now timestamptz := platform.meter_now();'" "'v_now timestamptz := now();'")"
  ["计量只读：宵禁起止不取家长设置"]="$(afr "$MG" "'coalesce(g.curfew_start, time ''22:00''), coalesce(g.curfew_end, time ''06:00'')'" "'time ''22:00'', time ''06:00'''")"
  ["计量只读：自练按 UTC 换日"]="$(afr "$MG" "'v_day_start := (v_now AT TIME ZONE ''Asia/Shanghai'')::date::timestamp AT TIME ZONE ''Asia/Shanghai'';'" "'v_day_start := date_trunc(''day'', v_now);'")"
  ["计量只读：留存每天 6 次自练"]="$(afr "$MG" "'greatest(5 - v_practice, 0)'" "'greatest(6 - v_practice, 0)'")"
  ["计量只读：非留存态也给自练次数"]="$(afr "$MG" "'CASE WHEN v_state = ''retained'' THEN greatest(5 - v_practice, 0) END'" "'greatest(5 - coalesce(v_practice, 0), 0)'")"
  ["计量只读：试用结束时间取开始时间"]="$(afr "$MG" "'SELECT x.period_end INTO v_trial_end'" "'SELECT x.period_start INTO v_trial_end'")"
  ["计量只读：返回设置人"]="$(afr "$MG" "'''as_of'', v_now);'" "'''as_of'', v_now, ''updated_by'', (SELECT g.updated_by FROM platform.student_time_guard g WHERE g.student_id = p_student));'")"
  ["计量只读：学习服务可调用"]="GRANT EXECUTE ON FUNCTION $MG TO starbuddy_learning;"
  ["计量只读：对 PUBLIC 开放"]="GRANT EXECUTE ON FUNCTION $MG TO PUBLIC;"
)

# 收回 0002 旧登录入口对 BFF 的授权（0031，specs/unified-shell-sprint4 H2）：由 20_rls_household 与授权快照发现
M+=(
  ["旧入口：BFF 可调旧注册"]="GRANT EXECUTE ON FUNCTION platform.signup_guardian(text) TO starbuddy_bff;"
  ["旧入口：BFF 可按号码查账号"]="GRANT EXECUTE ON FUNCTION platform.lookup_accounts_by_phone(text) TO starbuddy_bff;"
)

# SIE-03（0032–0035，specs/sie-03-dispute-reconcile PR①）：由 79m–79p 与 80 发现。DBMUT_FILTER=SIE-03 只跑这一组
RD='insight.raise_dispute(uuid, text, uuid, uuid, text, uuid)'
DG='insight.dispute_guard()'
IG='insight.intervention_guard()'
IC='insight.intervention_claims_ok(uuid, uuid[])'
PG='insight.exam_prediction_guard()'
RG='insight.exam_reconciliation_guard()'
FD='insight.freeze_due_predictions(timestamptz)'
EP='insight.submit_exam_plan(uuid, platform.subject, text, date, numeric, text[])'
ES='insight.submit_exam_score(uuid, uuid, numeric)'
EX='platform.execute_due_erasures(timestamptz)'
EC='platform.confirm_erasure(uuid)'
EK='platform.cancel_erasure(uuid)'
ER='platform.request_erasure(uuid)'
EE='platform.export_student_data(uuid)'
EI='platform.erasure_in_progress(uuid)'
M+=(
  ["SIE-03 异议：认已解除的绑定"]="$(afr "$RD" "'WHERE l.student_id = c.student_id AND l.status = ''active'' AND a.id = p_raised_by'" "'WHERE l.student_id = c.student_id AND a.id = p_raised_by'")"
  ["SIE-03 异议：不核对可见范围"]="$(afr "$RD" "'OR NOT (CASE v_role WHEN ''student'' THEN ''student'' ELSE ''guardian'' END = ANY (c.visibility))'" "''")"
  ["SIE-03 异议：终态判断可提异议"]="$(afr "$RD" "'IF c.status NOT IN (''hypothesis'', ''established'', ''to_verify'') OR c.expires_at <= now()'" "'IF c.expires_at <= now()'")"
  ["SIE-03 异议：我其实会提交时不核对判断已转待验证"]="DROP TRIGGER dispute_i_know_moved ON insight.dispute;"
  ["SIE-03 异议：家长可提那次只是失误"]="$(afr "$RD" "'IF p_kind = ''one_off_slip'' AND v_role <> ''student'' THEN'" "'IF false THEN'")"
  ["SIE-03 异议：失误不核对作答属于该学生"]="$(afr "$RD" "'OR NOT EXISTS (SELECT 1 FROM platform.attempt a WHERE a.attempt_id = p_attempt AND a.student_id = c.student_id)'" "''")"
  ["SIE-03 异议：数据有误不核对事实属于该学生"]="$(afr "$RD" "'WHEN ''attempt'' THEN EXISTS (SELECT 1 FROM platform.attempt a WHERE a.attempt_id = p_fact_id AND a.student_id = c.student_id)'" "'WHEN ''attempt'' THEN true'")"
  ["SIE-03 异议：数据有误不进复核队列"]="$(afr "$RD" "'v_task := platform.enqueue_claim_dispute(p_claim);'" "'v_task := NULL;'")"
  ["SIE-03 异议：学生与家长合并限额"]="$(afr "$RD" "' AND d.raised_by_role = v_role'" "''")"
  ["SIE-03 异议：结案不核对判断状态"]="$(afr "$DG" "'IF (NEW.kind = ''i_know'' AND NEW.status = ''resolved_supported'' AND v_claim IS DISTINCT FROM ''refuted'')'" "'IF (false)'")"
  ["SIE-03 异议：事实更正不查更正行"]="$(afr "$DG" "'IF NEW.status = ''fact_corrected'' AND NOT (CASE NEW.fact_kind'" "'IF false AND NOT (CASE NEW.fact_kind'")"
  ["SIE-03 异议：验证题不限数量"]="ALTER TABLE insight.dispute DROP CONSTRAINT dispute_refs_count;"
  ["SIE-03 异议：可单独删除"]="$(afr "$DG" "'RAISE EXCEPTION ''异议只随删除请求删除'' USING ERRCODE = ''insufficient_privilege'';'" "'RETURN OLD;'")"
  ["SIE-03 异议：BFF 可读别人提的"]="DROP POLICY dispute_raiser_read ON insight.dispute; CREATE POLICY dispute_raiser_read ON insight.dispute FOR SELECT TO starbuddy_bff USING (platform.can_read_student(student_id));"
  ["SIE-03 异议：BFF 可读验证任务引用"]="GRANT SELECT (verification_task_refs) ON insight.dispute TO starbuddy_bff;"
  ["SIE-03 动作：可引用待验证或过期的判断"]="$(afr "$IC" "' AND c.status IN (''hypothesis'', ''established'') AND c.expires_at > now())'" "')'")"
  ["SIE-03 动作：下发不再核判断"]="$(afr "$IG" "'RAISE EXCEPTION ''引用的判断已终结、过期或转为待验证，不能下发'' USING ERRCODE = ''check_violation'';'" "'NULL;'")"
  ["SIE-03 动作：未签字可下发"]="$(afr "$IG" "'IF NOT EXISTS (SELECT 1 FROM insight.intervention_rule_signoff s WHERE s.rule_key = NEW.rule_key AND s.params_version = NEW.params_version) THEN'" "'IF false THEN'")"
  ["SIE-03 动作：签字不核参数版本"]="$(afr "$IG" "' AND s.params_version = NEW.params_version'" "''")"
  ["SIE-03 动作：新建可直接下发"]="$(afr "$IG" "'IF NEW.status <> ''suggested'' OR NEW.dispatched_at IS NOT NULL THEN'" "'IF false THEN'")"
  ["SIE-03 动作：学生家长可读"]="GRANT SELECT ON insight.intervention_log TO starbuddy_bff; CREATE POLICY m ON insight.intervention_log FOR SELECT TO starbuddy_bff USING (platform.can_read_student(student_id));"
  ["SIE-03 预测：冻结后可改"]="$(afr "$PG" "'IF TG_OP = ''UPDATE'' AND OLD.frozen_at IS NOT NULL THEN'" "'IF false THEN'")"
  ["SIE-03 预测：零点后可改"]="$(afr "$PG" "'RAISE EXCEPTION ''考试当天零点起预测不能再修改'' USING ERRCODE = ''check_violation'';'" "'NULL;'")"
  ["SIE-03 预测：冻结后可删"]="$(afr "$PG" "'RAISE EXCEPTION ''预测只随删除请求删除'' USING ERRCODE = ''insufficient_privilege'';'" "'RETURN OLD;'")"
  ["SIE-03 预测：不核对对应判断"]="$(afr "$PG" "'IF NOT EXISTS (SELECT 1 FROM insight.claim c WHERE c.id = NEW.claim_id AND c.student_id = NEW.student_id AND c.target_type = ''exam'') THEN'" "'IF false THEN'")"
  ["SIE-03 预测：区间级别可改"]="ALTER TABLE insight.exam_prediction DROP CONSTRAINT exam_prediction_level_check;"
  ["SIE-03 预测：按 UTC 零点冻结"]="$(afr 'insight.exam_freeze_at(date)' "'AT TIME ZONE ''Asia/Shanghai'''" "'AT TIME ZONE ''UTC'''")"
  ["SIE-03 预测：可提前冻结"]="$(afr "$FD" "'p_now > now() OR '" "''")"
  ["SIE-03 预测：冻结不看考试日期"]="$(afr "$FD" "' AND insight.exam_freeze_at(e.exam_date) <= p_now'" "''")"
  ["SIE-03 考试安排：不核对录入人"]="$(afr "$EP" "'IF v_role IS NULL THEN'" "'IF false THEN'")"
  ["SIE-03 考试安排：满分不限"]="$(afr "$EP" "'OR p_full_score IS NULL OR p_full_score NOT BETWEEN 1 AND 300 OR p_full_score % 1 <> 0'" "'OR p_full_score IS NULL'")"
  ["SIE-03 考试安排：同一场考试不复用"]="$(afr "$EP" "'AND e.raw_score IS NULL AND e.superseded_at IS NULL;'" "'AND false;'")"
  ["SIE-03 成绩：不核对安排属于该学生"]="$(afr "$ES" "'WHERE e.id = p_plan AND e.student_id = p_student AND e.raw_score IS NULL'" "'WHERE e.id = p_plan AND e.raw_score IS NULL'")"
  ["SIE-03 对账：偏差方向不限四值"]="ALTER TABLE insight.exam_reconciliation DROP CONSTRAINT exam_reconciliation_deviation_directions_check;"
  ["SIE-03 对账：不按安排匹配成绩"]="$(afr "$RG" "' OR e.plan_record_id IS DISTINCT FROM p.exam_record_id THEN'" "' THEN'")"
  ["SIE-03 对账：句子不核对读者可见"]="$(afr "$RG" "' AND NEW.audience <@ c.visibility'" "''")"
  ["SIE-03 对账：句子不核对模板审核"]="$(afr "$RG" "' AND t.status = ''approved'''" "''")"
  ["SIE-03 对账：句子不查过期"]="$(afr "$RG" "' AND c.expires_at > now()'" "''")"
  ["SIE-03 对账：区间内外信调用方"]="$(afr "$RG" "'NEW.in_interval := e.raw_score BETWEEN p.interval_low AND p.interval_high;'" "'NULL;'")"
  ["SIE-03 删除：清单漏登记一张表"]="DELETE FROM platform.personal_table_registry WHERE nspname = 'insight' AND relname = 'behavior_event';"
  ["SIE-03 删除：某表谓词失效"]="UPDATE platform.personal_table_registry SET student_predicate = 'student_id = \$1 AND false' WHERE nspname = 'insight' AND relname = 'behavior_event';"
  # W13 技术债 T20：原先只由 79q 的前置断言发现，79p 补了探针数据与直接核对后由 79p 第三节发现
  ["SIE-03 删除：探针谓词失效"]="UPDATE platform.personal_table_registry SET student_predicate = 'student_id = \$1 AND false' WHERE nspname = 'insight' AND relname = 'confidence_probe';"
  ["SIE-03 删除：探针谓词指向别的学生"]="UPDATE platform.personal_table_registry SET student_predicate = 'student_id <> \$1' WHERE nspname = 'insight' AND relname = 'confidence_probe';"
  ["SIE-03 删除：冷静期可提前"]="$(afr "$EX" "' AND x.cooling_off_until <= p_now'" "''")"
  ["SIE-03 删除：可传将来时间"]="$(afr "$EX" "'p_now > now() OR '" "''")"
  ["SIE-03 删除：不核对发起人绑定"]="$(afr "$EX" "'IF NOT EXISTS (SELECT 1 FROM platform.guardian_student_link l JOIN platform.account a ON a.id = l.guardian_account_id'" "'IF false AND NOT EXISTS (SELECT 1 FROM platform.guardian_student_link l JOIN platform.account a ON a.id = l.guardian_account_id'")"
  ["SIE-03 删除：不停用学生账号"]="$(afr "$EX" "'UPDATE platform.account SET status = ''disabled'' WHERE id = v_account;'" "''")"
  ["SIE-03 删除：函数属主 authz 可删个人层表"]="GRANT DELETE ON insight.claim TO starbuddy_authz;"
  ["SIE-03 删除：守卫不核对角色"]="$(afr "$EI" "'current_user = ''starbuddy_eraser'' AND '" "''")"
  ["SIE-03 删除：守卫认函数属主 authz"]="$(afr "$EI" "'current_user = ''starbuddy_eraser'''" "'current_user IN (''starbuddy_eraser'', ''starbuddy_authz'')'")"
  ["SIE-03 删除：守卫不核对学生"]="$(afr "$EI" "' AND coalesce(current_setting(''starbuddy.erasure_student'', true), '''') = p_student::text'" "''")"
  ["SIE-03 删除：建档事实守卫不放行"]="$(afr 'insight.guard_fact_update()' "'IF platform.erasure_in_progress(OLD.student_id) THEN'" "'IF false THEN'")"
  ["SIE-03 删除：确认不限发起人"]="$(afr "$EC" "' OR r.requested_by IS DISTINCT FROM platform.current_account_id()'" "''")"
  ["SIE-03 删除：确认不限时"]="$(afr "$EC" "'IF r.requested_at < now() - interval ''24 hours'' THEN'" "'IF false THEN'")"
  ["SIE-03 删除：冷静期 1 天"]="$(afr "$EC" "'cooling_off_until = now() + interval ''7 days'''" "'cooling_off_until = now() + interval ''1 days'''")"
  ["SIE-03 删除：已执行可撤回"]="$(afr "$EK" "'IF r.status = ''executed'' THEN'" "'IF false THEN'")"
  ["SIE-03 删除：撤回不限绑定"]="$(afr "$EK" "'IF NOT FOUND OR NOT platform.is_linked_guardian(r.student_id) THEN'" "'IF NOT FOUND THEN'")"
  ["SIE-03 删除：学生本人可发起"]="$(afr "$ER" "'NOT platform.is_linked_guardian(p_student)'" "'NOT platform.can_read_student(p_student)'")"
  ["SIE-03 删除：已删除的档案可再发起"]="$(afr "$ER" "'IF EXISTS (SELECT 1 FROM platform.student_profile s WHERE s.id = p_student AND s.erased_at IS NOT NULL) THEN'" "'IF false THEN'")"
  ["SIE-03 删除：学生可读删除请求"]="DROP POLICY erasure_request_guardian_read ON platform.erasure_request; CREATE POLICY erasure_request_guardian_read ON platform.erasure_request FOR SELECT TO starbuddy_bff USING (platform.can_read_student(student_id));"
  ["SIE-03 导出：学生自述可导出"]="UPDATE platform.personal_table_registry SET export_topic = 'self_reports', export_columns = ARRAY['field_id', 'value'] WHERE nspname = 'insight' AND relname = 'self_report';"
  ["SIE-03 导出：异议不按提出人过滤"]="UPDATE platform.personal_table_registry SET export_filter = NULL WHERE nspname = 'insight' AND relname = 'dispute';"
  ["SIE-03 导出：导出字典外的推断参数"]="UPDATE platform.personal_table_registry SET export_columns = export_columns || ARRAY['p_mastery'] WHERE nspname = 'platform' AND relname = 'mastery_estimate';"
  ["SIE-03 导出：导出预测区间"]="UPDATE platform.personal_table_registry SET export_topic = 'predictions', export_columns = ARRAY['interval_low', 'interval_high'] WHERE nspname = 'insight' AND relname = 'exam_prediction';"
  ["SIE-03 导出：学生本人可导出"]="$(afr "$EE" "'NOT platform.is_linked_guardian(p_student)'" "'NOT platform.can_read_student(p_student)'")"
  ["SIE-03 导出：下载凭据原文入库"]="$(afr "$EE" "'sha256(convert_to(v_ref, ''UTF8''))'" "'sha256(convert_to(''fixed'', ''UTF8''))'")"
  ["攻击面：新函数删除个人层表"]="CREATE FUNCTION platform.t_evil_purge(p uuid) RETURNS void LANGUAGE sql SET search_path = pg_catalog AS \$f\$ DELETE FROM insight.claim WHERE student_id = p \$f\$; REVOKE EXECUTE ON FUNCTION platform.t_evil_purge(uuid) FROM PUBLIC;"
  ["攻击面：删除执行身份有成员"]="CREATE ROLE t_eraser_member IN ROLE starbuddy_eraser;"
)

# 冲刺 5-H 跨科周报 v0（0036，specs/trust-ui-sprint5）：由 79r 与 79p / 79e 发现。DBMUT_FILTER=周报 只跑这一组
WG='insight.weekly_report_guard()'
WS='insight.valid_weekly_sentences(jsonb, text)'
WO='insight.valid_weekly_objective(jsonb)'
WR='platform.review_sample_weekly_reports(date, numeric, integer)'
M+=(
  ["周报：学生可读家长版"]="DROP POLICY weekly_report_front_read ON insight.weekly_report; CREATE POLICY weekly_report_front_read ON insight.weekly_report FOR SELECT TO starbuddy_bff USING (status = 'generated' AND platform.can_read_student(student_id));"
  ["周报：不出的周报可见"]="DROP POLICY weekly_report_front_read ON insight.weekly_report; CREATE POLICY weekly_report_front_read ON insight.weekly_report FOR SELECT TO starbuddy_bff USING ((audience = 'student' AND platform.is_self_student(student_id)) OR (audience = 'guardian' AND platform.is_linked_guardian(student_id)));"
  ["周报：BFF 可读阻断编码"]="GRANT SELECT (withheld_reasons) ON insight.weekly_report TO starbuddy_bff;"
  ["周报：句子不核对读者可见"]="$(afr "$WG" "' AND NEW.audience = ANY (c.visibility)'" "''")"
  ["周报：句子不核对判断状态"]="$(afr "$WG" "' AND c.status IN (''hypothesis'', ''established'')'" "''")"
  ["周报：句子不查过期"]="$(afr "$WG" "' AND c.expires_at > now()'" "''")"
  ["周报：句子不核对模板审核"]="$(afr "$WG" "' AND t.status = ''approved'''" "''")"
  ["周报：句子不核对学生"]="$(afr "$WG" "' AND c.student_id = NEW.student_id'" "''")"
  ["周报：学生版收家长观察"]="$(afr "$WG" "'AND (NEW.audience <> ''student'' OR c.source_type <> ''guardian'')'" "'AND true'")"
  ["周报：内容写入后可改"]="$(afr "$WG" "'OR (to_jsonb(NEW) - ''spot_check_task_id'') IS DISTINCT FROM (to_jsonb(OLD) - ''spot_check_task_id'')'" "''")"
  ["周报：可单独删除"]="$(afr "$WG" "'RAISE EXCEPTION ''周报只随删除请求删除'' USING ERRCODE = ''insufficient_privilege'';'" "'RETURN OLD;'")"
  ["周报：学生版可带置信度"]="$(afr "$WS" "'OR (p_audience = ''student'' AND s ? ''confidence_band'')'" "''")"
  ["周报：句子可带别的键"]="$(afr "$WS" "'WHERE k NOT IN (''claim_id'''" "'WHERE false AND k NOT IN (''claim_id'''")"
  ["周报：客观栏来源不限"]="$(afr "$WO" "'AND p -> ''source_type'' = ''\"objective\"''::jsonb'" "''")"
  ["周报：客观栏含英语"]="$(afr "$WO" "'NOT IN (''aya'', ''zhixing'', ''wenli'')'" "'NOT IN (''aya'', ''zhixing'', ''wenli'', ''echo'')'")"
  ["周报：清单漏登记"]="DELETE FROM platform.personal_table_registry WHERE nspname = 'insight' AND relname = 'weekly_report';"
  ["周报：抽检不设最少份数"]="$(afr "$WR" "'greatest(ceil(v_total * p_ratio)::integer, p_min, 5)'" "'ceil(v_total * p_ratio)::integer'")"
  ["周报：最少份数不在库里兜底 5 份"]="$(afr "$WR" "'p_min, 5)) - v_done'" "'p_min)) - v_done'")"
  ["周报：取不到学科时静默跳过"]="$(afr "$WR" "'IF w.subject IS NULL THEN'" "'IF w.subject IS NULL AND false THEN'")"
  ["周报：审核台详情给客观栏"]="$(afr 'platform.review_task_detail(uuid)' "'''sentences'', w.sentences, ''algorithm_version'''" "'''sentences'', w.sentences, ''objective'', w.objective, ''algorithm_version'''")"
  ["周报：标注不核对判断状态"]="$(afr "$WG" "'AND (s ? ''label'') = (c.status = ''hypothesis'')'" "'AND true'")"
  ["周报：修订标记不核对"]="$(afr "$WG" "'AND (NOT s ? ''claim_revision'' OR c.updated_at = (s ->> ''claim_revision'')::timestamptz)'" "'AND true'")"
  ["周报：不盖修订标记"]="$(afr "$WG" "'x.s || jsonb_build_object(''claim_revision'', c.updated_at)'" "'x.s'")"
  ["周报：收没结束的周"]="$(afr "$WG" "'IF NEW.week_start + 7 > '" "'IF false AND NEW.week_start + 7 > '")"
  ["周报：已抽的不计入份数"]="$(afr "$WR" "' - v_done;'" "';'")"
  ["周报：抽样不限比例"]="$(afr "$WR" "'OR p_ratio > 0.5 '" "''")"
  ["周报：BFF 可调抽样"]="GRANT EXECUTE ON FUNCTION platform.review_sample_weekly_reports(date, numeric, integer) TO starbuddy_bff;"
  ["周报：可用任意原因进队"]="ALTER TABLE platform.review_task DROP CONSTRAINT review_task_weekly_report;"
  ["周报：抽检可用判分结案码"]="UPDATE platform.review_resolution_code SET applies_to = applies_to || ARRAY['weekly_report'] WHERE code = 'upheld';"
)

# KGE-04 第二步 PR 1：掌握节点键、复测任务、学情档案页与阻断记录（0037，specs/kge-04-mastery-step2）：由 79s 与 79p / 79q / 80 发现。
# DBMUT_FILTER=掌握第二步 只跑这一组
KS='platform.check_supersede_target()'
KT='platform.retest_task_guard()'
KP='platform.profile_page_guard()'
KV='platform.valid_profile_sections(jsonb, text)'
KC='platform.valid_profile_violations(jsonb)'
kview() {  # 改写档案页陈述视图：$1 展开哪些项，$2 判断状态条件，$3 可见范围条件，$4 模板状态条件（缺省与 0037 相同：已审核）
  printf "CREATE OR REPLACE VIEW platform.profile_page_statement_v WITH (security_barrier = true) AS SELECT p.id AS profile_page_id, p.student_id, p.subject, s.sec ->> 'key' AS section, s.ord AS section_ord, i.ord AS item_ord, c.id AS claim_id, c.template_key, c.template_version, c.status::text AS claim_status FROM platform.profile_page p CROSS JOIN LATERAL jsonb_array_elements(p.sections) WITH ORDINALITY s(sec, ord) CROSS JOIN LATERAL %s WITH ORDINALITY i(item, ord) JOIN insight.claim c ON c.id = (i.item ->> 'claim_id')::uuid AND c.student_id = p.student_id AND c.template_key = i.item ->> 'template_key' AND c.template_version = (i.item ->> 'template_version')::integer WHERE platform.can_read_student(p.student_id) AND %s AND c.expires_at > now() AND EXISTS (SELECT 1 FROM insight.claim_template t WHERE t.template_key = c.template_key AND t.version = c.template_version AND %s) AND %s;" "$1" "$2" "${4:-t.status = 'approved'}" "$3"
}
M+=(
  ["掌握第二步：复测任务不强制 RLS"]="ALTER TABLE platform.retest_task NO FORCE ROW LEVEL SECURITY;"
  ["掌握第二步：档案页不强制 RLS"]="ALTER TABLE platform.profile_page NO FORCE ROW LEVEL SECURITY;"
  ["掌握第二步：阻断记录不强制 RLS"]="ALTER TABLE platform.profile_page_check NO FORCE ROW LEVEL SECURITY;"
  ["掌握第二步：两种键可同时给"]="ALTER TABLE platform.mastery_estimate DROP CONSTRAINT mastery_estimate_one_key;"
  ["掌握第二步：节点键三列可不齐"]="ALTER TABLE platform.mastery_estimate DROP CONSTRAINT mastery_estimate_node_key_complete;"
  ["掌握第二步：节点键不限格式"]="ALTER TABLE platform.mastery_estimate DROP CONSTRAINT mastery_estimate_node_id_format;"
  ["掌握第二步：同一单元可有两条当前记录"]="DROP INDEX platform.mastery_estimate_node_current_uq;"
  ["掌握第二步：更正链不比节点"]="$(afr "$KS" "'AND n.node_id IS NOT DISTINCT FROM OLD.node_id'" "''")"
  ["掌握第二步：更正链不比认知层次"]="$(afr "$KS" "'AND n.cognitive_level IS NOT DISTINCT FROM OLD.cognitive_level'" "''")"
  ["掌握第二步：更正链不比能力点键"]="$(afr "$KS" "'AND n.skill_local_id IS NOT DISTINCT FROM OLD.skill_local_id
                     AND n.node_id'" "'AND n.node_id'")"
  ["掌握第二步：同一单元同一原因可有两条开着的任务"]="DROP INDEX platform.retest_task_open_uq;"
  ["掌握第二步：复测轮可不带轮次"]="ALTER TABLE platform.retest_task DROP CONSTRAINT retest_task_rung_reason;"
  ["掌握第二步：通过可不记作答"]="ALTER TABLE platform.retest_task DROP CONSTRAINT retest_task_closing_attempt;"
  ["掌握第二步：复测任务可引用别人的掌握记录"]="ALTER TABLE platform.retest_task DROP CONSTRAINT retest_task_estimate_fk; ALTER TABLE platform.retest_task ADD CONSTRAINT retest_task_estimate_fk FOREIGN KEY (scheduled_by_estimate_id) REFERENCES platform.mastery_estimate (id);"
  ["掌握第二步：新任务可直接关闭"]="$(afr "$KT" "'IF NEW.status <> ''open'' OR NEW.closed_at IS NOT NULL OR NEW.closing_attempt_id IS NOT NULL THEN'" "'IF false THEN'")"
  ["掌握第二步：关闭的任务可再改"]="$(afr "$KT" "'IF OLD.status <> ''open'' THEN'" "'IF false THEN'")"
  ["掌握第二步：档案页陈述可引用待验证判断"]="$(afr "$KP" "'THEN c.status IN (''hypothesis'', ''established'')'" "'THEN true'")"
  ["掌握第二步：档案页不核对判断属于该学生"]="$(afr "$KP" "' AND c.student_id = NEW.student_id'" "''")"
  ["掌握第二步：档案页项可带文字"]="$(afr "$KV" "'k NOT IN (''claim_id'', ''template_key'', ''template_version'')'" "'k NOT IN (''claim_id'', ''template_key'', ''template_version'', ''text'')'")"
  ["掌握第二步：档案页可有任意节"]="$(afr "$KV" "'OR (s ->> ''key'') NOT IN (''current_stage'''" "'OR false AND (s ->> ''key'') NOT IN (''current_stage'''")"
  ["掌握第二步：衔接语可含数字"]="$(afr "$KV" "'OR (s ->> ''connective'') ~ ''[0-9０-９]'''" "''")"
  ["掌握第二步：降级页可有衔接语"]="$(afr "$KV" "'OR (p_check_status = ''blocked_fallback'' AND (s ->> ''connective'') <> '''')'" "''")"
  ["掌握第二步：禁用推断命中可存片段"]="$(afr "$KC" "'OR ((v ->> ''code'') = ''forbidden_inference'' AND (v ->> ''fragment'') <> '''')'" "''")"
  ["掌握第二步：阻断记录可挂别人的页"]="ALTER TABLE platform.profile_page_check DROP CONSTRAINT profile_page_check_page_fk; ALTER TABLE platform.profile_page_check ADD CONSTRAINT profile_page_check_page_fk FOREIGN KEY (profile_page_id) REFERENCES platform.profile_page (id) ON DELETE CASCADE;"
  ["掌握第二步：档案页不随档案级联删除"]="ALTER TABLE platform.profile_page DROP CONSTRAINT profile_page_student_id_fkey; ALTER TABLE platform.profile_page ADD CONSTRAINT profile_page_student_id_fkey FOREIGN KEY (student_id) REFERENCES platform.student_profile (id);"
  ["掌握第二步：复测任务不随档案级联删除"]="ALTER TABLE platform.retest_task DROP CONSTRAINT retest_task_student_id_fkey; ALTER TABLE platform.retest_task ADD CONSTRAINT retest_task_student_id_fkey FOREIGN KEY (student_id) REFERENCES platform.student_profile (id);"
  ["掌握第二步：BFF 可写复测任务"]="GRANT INSERT, UPDATE ON platform.retest_task TO starbuddy_bff; CREATE POLICY t_bff_write ON platform.retest_task FOR ALL TO starbuddy_bff USING (platform.can_read_student(student_id)) WITH CHECK (platform.can_read_student(student_id));"
  ["掌握第二步：BFF 可删档案页"]="GRANT DELETE ON platform.profile_page TO starbuddy_bff; CREATE POLICY t_bff_delete ON platform.profile_page FOR DELETE TO starbuddy_bff USING (platform.can_read_student(student_id));"
  ["掌握第二步：BFF 读全部复测任务"]="DROP POLICY retest_task_read ON platform.retest_task; CREATE POLICY retest_task_read ON platform.retest_task FOR SELECT TO starbuddy_bff USING (true);"
  ["掌握第二步：撤销绑定的家长仍能读档案页"]="DROP POLICY profile_page_read ON platform.profile_page; CREATE POLICY profile_page_read ON platform.profile_page FOR SELECT TO starbuddy_bff USING (platform.is_self_student(student_id) OR EXISTS (SELECT 1 FROM platform.guardian_student_link l WHERE l.student_id = profile_page.student_id AND l.guardian_account_id = platform.current_account_id()));"
  ["掌握第二步：BFF 可读阻断记录"]="GRANT SELECT ON platform.profile_page_check TO starbuddy_bff; CREATE POLICY profile_page_check_read ON platform.profile_page_check FOR SELECT TO starbuddy_bff USING (platform.can_read_student(student_id));"
  ["掌握第二步：BFF 可读档案页 sections"]="GRANT SELECT (sections) ON platform.profile_page TO starbuddy_bff;"
  ["掌握第二步：陈述视图含待办与待验证"]="$(kview "jsonb_array_elements((s.sec -> 'statements') || (s.sec -> 'todo'))" "c.status IN ('hypothesis', 'established', 'to_verify')" "((platform.is_self_student(c.student_id) AND 'student' = ANY (c.visibility)) OR (platform.is_linked_guardian(c.student_id) AND 'guardian' = ANY (c.visibility)))")"
  ["掌握第二步：陈述视图不看判断可见范围"]="$(kview "jsonb_array_elements(s.sec -> 'statements')" "c.status IN ('hypothesis', 'established')" "true")"
  ["掌握第二步：陈述视图不看模板是否仍已审核（退役模板的陈述仍返回）"]="$(kview "jsonb_array_elements(s.sec -> 'statements')" "c.status IN ('hypothesis', 'established')" "((platform.is_self_student(c.student_id) AND 'student' = ANY (c.visibility)) OR (platform.is_linked_guardian(c.student_id) AND 'guardian' = ANY (c.visibility)))" "true")"
  ["掌握第二步：复测轮到期时间不核对"]="$(afr "$KT" "'IF m.next_retest_at IS DISTINCT FROM NEW.due_at THEN'" "'IF false THEN'")"
  ["掌握第二步：复测轮轮次不核对门槛"]="$(afr "$KT" "'IF NEW.rung IS NOT NULL AND NEW.rung IS DISTINCT FROM'" "'IF false AND NEW.rung IS DISTINCT FROM'")"
  ["掌握第二步：档案页不核对学科"]="$(afr "$KP" "'AND (c.subject = NEW.subject OR c.subject IS NULL)'" "'AND true'")"
  ["掌握第二步：阻断记录不核对判断属于该学生"]="$(afr 'platform.profile_page_check_guard()' "'ELSE NOT EXISTS ('" "'ELSE false AND NOT EXISTS ('")"
  ["掌握第二步：阻断记录读不到的判断可冒充"]="$(afr 'platform.profile_page_check_guard()' "'THEN EXISTS (SELECT 1 FROM insight.claim c WHERE c.id = x.claim_id::uuid AND c.student_id <> NEW.student_id)'" "'THEN false'")"
  ["掌握第二步：阻断片段可含数字"]="$(afr "$KC" "'OR (v ->> ''fragment'') ~ ''[0-9０-９]'''" "''")"
  ["掌握第二步：学习服务可删档案页"]="GRANT DELETE ON platform.profile_page TO starbuddy_learning;"
  ["掌握第二步：学习服务可改到期时间"]="GRANT UPDATE (due_at) ON platform.retest_task TO starbuddy_learning;"
  ["掌握第二步：洞察服务可读档案页"]="GRANT SELECT ON platform.profile_page TO starbuddy_insight; CREATE POLICY t_insight_read ON platform.profile_page FOR SELECT TO starbuddy_insight USING (true);"
  ["掌握第二步：清单漏登记档案页"]="DELETE FROM platform.personal_table_registry WHERE nspname = 'platform' AND relname = 'profile_page';"
  ["掌握第二步：删除执行身份删不了复测任务"]="REVOKE DELETE ON platform.retest_task FROM starbuddy_eraser;"
  ["掌握第二步：导出档案页引用"]="GRANT SELECT (sections) ON platform.profile_page TO starbuddy_authz; UPDATE platform.personal_table_registry SET export_columns = export_columns || ARRAY['sections'] WHERE nspname = 'platform' AND relname = 'profile_page';"
)

# G 流第二部分 PR A：编译页面审核记录（0038，specs/release-gate-sprint4 冲刺 3 段）：由 79t 发现。DBMUT_FILTER=页面审核 只跑这一组
PS='kge.sign_page_review(text, text, text, text, text)'
PR='kge.page_review_record(platform.subject)'
M+=(
  ["页面审核：提交号不限格式"]="ALTER TABLE kge.content_review DROP CONSTRAINT content_review_page_commit; ALTER TABLE kge.content_review ADD CONSTRAINT content_review_page_commit CHECK (CASE WHEN object_type = 'page' THEN page_commit IS NOT NULL AND page_signer_no IS NOT NULL AND page_signer_no BETWEEN 1 AND 16 AND decision <> 'pending' AND action = 'review' AND object_id ~ '^[A-Z][A-Z0-9]{1,9}(\.[A-Z0-9][A-Z0-9_]{0,31}){1,4}\$' ELSE page_commit IS NULL AND page_signer_no IS NULL END);"
  ["页面审核：页面记录可缺提交号"]="ALTER TABLE kge.content_review DROP CONSTRAINT content_review_page_commit; ALTER TABLE kge.content_review ADD CONSTRAINT content_review_page_commit CHECK (CASE WHEN object_type = 'page' THEN (page_commit IS NULL OR page_commit ~ '^[0-9a-f]{40}\$') AND page_signer_no IS NOT NULL AND page_signer_no BETWEEN 1 AND 16 AND decision <> 'pending' AND action = 'review' AND object_id ~ '^[A-Z][A-Z0-9]{1,9}(\.[A-Z0-9][A-Z0-9_]{0,31}){1,4}\$' ELSE page_commit IS NULL AND page_signer_no IS NULL END);"
  ["页面审核：页面记录可待审"]="ALTER TABLE kge.content_review DROP CONSTRAINT content_review_page_commit; ALTER TABLE kge.content_review ADD CONSTRAINT content_review_page_commit CHECK (CASE WHEN object_type = 'page' THEN page_commit IS NOT NULL AND page_commit ~ '^[0-9a-f]{40}\$' AND page_signer_no IS NOT NULL AND page_signer_no BETWEEN 1 AND 16 AND action = 'review' AND object_id ~ '^[A-Z][A-Z0-9]{1,9}(\.[A-Z0-9][A-Z0-9_]{0,31}){1,4}\$' ELSE page_commit IS NULL AND page_signer_no IS NULL END);"
  ["页面审核：非页面记录可带提交号"]="ALTER TABLE kge.content_review DROP CONSTRAINT content_review_page_commit; ALTER TABLE kge.content_review ADD CONSTRAINT content_review_page_commit CHECK (CASE WHEN object_type = 'page' THEN page_commit IS NOT NULL AND page_commit ~ '^[0-9a-f]{40}\$' AND page_signer_no IS NOT NULL AND page_signer_no BETWEEN 1 AND 16 AND decision <> 'pending' AND action = 'review' AND object_id ~ '^[A-Z][A-Z0-9]{1,9}(\.[A-Z0-9][A-Z0-9_]{0,31}){1,4}\$' ELSE true END);"
  ["页面审核：同一提交同一签字位可记两次"]="DROP INDEX kge.content_review_page_uq;"
  ["页面审核：同一账号可签两次"]="$(afr "$PS" "' AND s.account_id = v_staff) THEN'" "' AND false) THEN'")"
  ["页面审核：签字不核对 staff"]="$(afr "$PS" "'v_staff uuid := kge.current_staff();'" "'v_staff uuid := platform.current_account_id();'")"
  ["页面审核：签字函数不核对提交号"]="$(afr "$PS" "'IF p_page_commit IS NULL OR p_page_commit !~ ''^[0-9a-f]{40}\$'' THEN'" "'IF false THEN'")"
  ["页面审核：签字函数不核对页面 ID"]="$(afr "$PS" "'IF p_page_id IS NULL OR p_page_id !~'" "'IF false AND p_page_id !~'")"
  ["页面审核：签字序号不递增"]="$(afr "$PS" "'SELECT coalesce(max(r.page_signer_no), 0) + 1 INTO v_no'" "'SELECT 1 INTO v_no'")"
  ["页面审核：知识服务可签页面"]="GRANT EXECUTE ON FUNCTION $PS TO starbuddy_kge;"
  ["页面审核：学习服务可读页面记录"]="GRANT EXECUTE ON FUNCTION $PR TO starbuddy_learning;"
  ["页面审核：导出混入非页面记录"]="$(afr "$PR" "'WHERE r.object_type = ''page'''" "'WHERE true'")"
  ["页面审核：导出不按学科过滤"]="$(afr "$PR" "'AND (p_subject IS NULL OR '" "'AND (true OR '")"
  ["页面审核：后续签字风险可与第一条不同"]="$(afr "$PS" "'IF v_first_risk IS NOT NULL AND v_first_risk <> p_risk_level THEN'" "'IF false THEN'")"
  ["页面审核：签字不加咨询锁"]="$(afr "$PS" "'PERFORM pg_advisory_xact_lock(hashtext(''kge.sign_page_review''), hashtext(p_page_id || ''@'' || p_page_commit));'" "''")"
)

# G 流第二部分 PR B：内容版本与题目准入（0039，specs/release-gate-sprint4 第 7 节、冲刺 4 段）：由 79u 发现（授权类的同时由 80 的快照发现）。
# DBMUT_FILTER=内容发布 与 DBMUT_FILTER=准入 只跑这两组
GCV='platform.guard_curriculum_version()'
GCR='platform.guard_content_registration()'
PCV='platform.publish_content_version(uuid)'
SCV='platform.sign_content_version(uuid, text, text, text)'
RCV='platform.retire_content_version(uuid, text)'
DCV='platform.content_version_digest(uuid)'
ATO='platform.admission_transition_ok(platform.admission, platform.admission)'
CAT='platform.check_admission_target(platform.subject, platform.admission, text, text, text, text)'
SAD='platform.sign_admission(platform.subject, text, platform.admission, text, text, text, text, text, text, text)'
CAD='platform.change_admission(uuid[])'
M+=(
  ["内容发布：可直接以已发布登记"]="$(afr "$GCV" "'IF NEW.status <> ''draft'' OR NEW.published_at IS NOT NULL OR NEW.retired_at IS NOT NULL THEN'" "'IF false THEN'")"
  ["内容发布：状态可不经函数改"]="$(afr "$GCV" "'IF current_user <> ''starbuddy_authz'' THEN'" "'IF false THEN'")"
  ["内容发布：已发布可改其他字段"]="$(afr "$GCV" "'IF OLD.status = ''published'' AND (to_jsonb(NEW)'" "'IF false AND (to_jsonb(NEW)'")"
  ["内容发布：已撤下可改"]="$(afr "$GCV" "'IF OLD.status = ''retired'' THEN'" "'IF false THEN'")"
  ["内容发布：已发布可删"]="$(afr "$GCV" "'IF OLD.status <> ''draft'' THEN'" "'IF false THEN'")"
  ["内容发布：已发布版本可追加登记"]="$(afr "$GCR" "'ELSIF v_status IS DISTINCT FROM ''draft'' THEN'" "'ELSIF false THEN'")"
  ["内容发布：登记可挪版本"]="$(afr "$GCR" "'IF TG_OP = ''UPDATE'' AND NEW.curriculum_version_id IS DISTINCT FROM OLD.curriculum_version_id THEN'" "'IF false THEN'")"
  ["内容发布：题目可以 admitted 登记"]="$(afr "$GCR" "'IF TG_OP = ''INSERT'' AND NEW.admission = ''admitted'' THEN'" "'IF false THEN'")"
  ["内容发布：准入可不经函数改"]="$(afr "$GCR" "'IF current_user <> ''starbuddy_authz'' THEN'" "'IF false THEN'")"
  ["内容发布：骨架学科可不填地图"]="$(afr "$PCV" "'IF platform.content_requires_map(c.subject) THEN'" "'IF false THEN'")"
  ["内容发布：地图版本可不是已发布"]="$(afr "$PCV" "'IF v_map_status IS DISTINCT FROM ''published'' THEN'" "'IF false THEN'")"
  ["内容发布：版本号可不递增"]="$(afr "$PCV" "'IF v_max IS NOT NULL AND string_to_array'" "'IF false AND string_to_array'")"
  ["内容发布：版本号只比已发布中的"]="$(afr "$PCV" "'AND o.subject = c.subject AND o.id <> c.id AND o.published_at IS NOT NULL;'" "'AND o.subject = c.subject AND o.id <> c.id AND o.status = ''published'';'")"
  ["内容发布：发布不查批准记录"]="$(afr "$PCV" "'IF NOT FOUND OR r.decision <> ''approved'' THEN'" "'IF NOT FOUND THEN'")"
  ["内容发布：发布不比摘要"]="$(afr "$PCV" "'IF r.digest IS DISTINCT FROM v_digest THEN'" "'IF false THEN'")"
  ["内容发布：摘要不含题目登记"]="$(afr "$DCV" "'FROM platform.item i WHERE i.curriculum_version_id = v.id)'" "'FROM platform.item i WHERE false)'")"
  ["内容发布：摘要不含地图版本"]="$(afr "$DCV" "'''map_version_id'', v.map_version_id'" "'''map_version_id'', NULL'")"
  ["内容发布：签字不核对 staff"]="$(afr "$SCV" "'v_staff uuid := platform.review_current_staff();'" "'v_staff uuid := platform.current_account_id();'")"
  ["内容发布：签字不比看到的摘要"]="$(afr "$SCV" "'IF p_expected_digest IS DISTINCT FROM v_digest THEN'" "'IF false THEN'")"
  ["内容发布：撤下可不写依据"]="$(afr "$RCV" "'IF p_basis_code IS NULL THEN'" "'IF false THEN'")"
  ["内容发布：BFF 可看草稿能力视图"]="DROP POLICY skill_node_read ON platform.skill_node; CREATE POLICY skill_node_read ON platform.skill_node FOR SELECT TO starbuddy_bff USING (true);"
  ["内容发布：BFF 可看草稿版本下已准入题"]="DROP POLICY item_read ON platform.item; CREATE POLICY item_read ON platform.item FOR SELECT TO starbuddy_bff USING (admission = 'admitted');"
  ["内容发布：BFF 可读审核记录"]="GRANT SELECT ON platform.release_record TO starbuddy_bff; CREATE POLICY t_rr ON platform.release_record FOR SELECT TO starbuddy_bff USING (true);"
  ["内容发布：学习服务可改状态"]="GRANT UPDATE (status, published_at) ON platform.curriculum_version TO starbuddy_learning;"
  ["内容发布：学习服务可改准入"]="GRANT UPDATE (admission, admitted_at) ON platform.item TO starbuddy_learning;"
  ["内容发布：学习服务可发布"]="GRANT EXECUTE ON FUNCTION $PCV TO starbuddy_learning;"
  ["内容发布：记录可改"]="DROP TRIGGER reject_release_record_change ON platform.release_record;"
  ["内容发布：记录可清空"]="DROP TRIGGER reject_release_record_truncate ON platform.release_record;"
  ["准入：方向不限"]="$(afr "$ATO" "'SELECT (p_from = ''source_unverified'' AND p_to = ''pending_review'')'" "'SELECT true OR (p_from = ''source_unverified'' AND p_to = ''pending_review'')'")"
  ["准入：rejected 可再准入"]="$(afr "$ATO" "'OR (p_from = ''pending_review'' AND p_to = ''admitted'')'" "'OR (p_from IN (''pending_review'', ''rejected'') AND p_to = ''admitted'')'")"
  ["准入：admitted 可回退"]="$(afr "$ATO" "'SELECT (p_from = ''source_unverified'' AND p_to = ''pending_review'')'" "'SELECT (p_to IN (''pending_review'', ''source_unverified''))'")"
  ["准入：可跳级到 admitted"]="$(afr "$ATO" "'OR (p_from = ''pending_review'' AND p_to = ''admitted'')'" "'OR (p_from IN (''pending_review'', ''source_unverified'') AND p_to = ''admitted'')'")"
  ["准入：admitted 不查审核等级"]="$(afr "$CAT" "'AND (p_review_level IS NULL OR'" "'AND false AND (p_review_level IS NULL OR'")"
  ["准入：审核等级门槛 Q1"]="$(afr "platform.admission_min_review_rank(platform.subject)" "' SELECT 2 '" "' SELECT 1 '")"
  ["准入：升级不查来源"]="$(afr "$CAT" "'AND NOT (p_source_ref IS NOT NULL'" "'AND false AND NOT (p_source_ref IS NOT NULL'")"
  ["准入：登记可自报授权"]="$(afr "$GCR" "'NEW.license_status := ''unverified'';'" "'NULL;'")"
  ["准入：升 admitted 沿用题目上的授权"]="$(afr "$SAD" "'IF p_decision = ''approved'' AND p_to = ''admitted'' AND p_license_status IS NULL THEN'" "'IF false THEN'")"
  ["准入：已撤下版本不能降为 rejected"]="$(afr "$GCR" "'AND NEW.admission IS DISTINCT FROM ''rejected'' THEN'" "' THEN'")"
  ["准入：变化记录带回签字人"]="DROP FUNCTION platform.admission_change_log(timestamptz); CREATE FUNCTION platform.admission_change_log(p_since timestamptz) RETURNS TABLE (applied_at timestamptz, subject platform.subject, item_id text, from_admission platform.admission, to_admission platform.admission, affected_rubric_versions text[], signer uuid) LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, platform, pg_temp AS \$f\$ SELECT x.decided_at, x.subject, x.item_id::text, x.from_admission, x.to_admission, x.affected_rubric_versions, x.signer FROM platform.release_record x WHERE x.line = 'admission' AND x.action = 'apply' \$f\$; ALTER FUNCTION platform.admission_change_log(timestamptz) OWNER TO starbuddy_authz; REVOKE EXECUTE ON FUNCTION platform.admission_change_log(timestamptz) FROM PUBLIC; GRANT EXECUTE ON FUNCTION platform.admission_change_log(timestamptz) TO starbuddy_learning;"
  ["内容发布：记录学科可与版本不一致"]="ALTER TABLE platform.release_record DROP CONSTRAINT release_record_cv_subject;"
  ["内容发布：发布不加咨询锁"]="$(afr "$PCV" "'PERFORM pg_advisory_xact_lock(hashtext(''platform.publish_content_version''),'" "'PERFORM pg_advisory_xact_lock_shared(hashtext(''platform.publish_content_version''),'")"  ["准入：签字不比看到的摘要"]="$(afr "$SAD" "'IF p_expected_digest IS DISTINCT FROM v_digest THEN'" "'IF false THEN'")"
  ["准入：已撤下版本题目可变"]="$(afr "$SAD" "'IF v_cv_status = ''retired'' AND p_to <> ''rejected'' THEN'" "'IF false THEN'")"
  ["准入：执行不查结论"]="$(afr "$CAD" "'IF r.decision <> ''approved'' THEN'" "'IF false THEN'")"
  ["准入：执行不认最近一条"]="$(afr "$CAD" "'AND (x.decided_at, x.id) > (r.decided_at, r.id)) THEN'" "'AND false) THEN'")"
  ["准入：同一审核可执行两次"]="$(afr "$CAD" "'WHERE x.applies_review_id = r.id) THEN'" "'WHERE false) THEN'")"
  ["准入：执行不比摘要"]="$(afr "$CAD" "'IF platform.item_admission_digest(r.subject, r.item_id) IS DISTINCT FROM r.digest THEN'" "'IF false THEN'")"
  ["准入：降级不留清单"]="$(afr "$CAD" "'IF i.admission = ''admitted'' THEN'" "'IF false THEN'")"
  ["准入：来源不按审核记录写"]="$(afr "$CAD" "'source_ref = r.source_ref, source_type = r.source_type, license_status = r.license_status'" "'source_ref = source_ref'")"
  ["准入：已准入可缺来源"]="ALTER TABLE platform.item DROP CONSTRAINT item_admitted_source;"
)

# G 流第二部分 PR C：考情政策线签字收口（0040–0041，specs/release-gate-sprint4 第 8 节、冲刺 4“考情”）：由 77、79v 与 run.sh 的并发测试发现
# （授权类的同时由 80 的快照发现）。DBMUT_FILTER=考情签字 只跑这一组；相关的旧组用 DBMUT_FILTER=考情 / DBMUT_FILTER=招录
QSS='policy.sign_scheme(uuid, text, text)'
QSR='policy.sign_record(text, uuid, text, text)'
QPS='policy.publish_scheme(uuid)'
QAC='policy.approval_count(text, uuid)'
QGS='policy.guard_signoff_publish()'
QOD='policy.object_digest(text, uuid)'
M+=(
  ["考情签字：方案签字不查 staff"]="$(afr "$QSS" "'v_staff uuid := platform.review_current_staff();'" "'v_staff uuid := platform.current_account_id();'")"
  ["考情签字：记录签字不查 staff"]="$(afr "$QSR" "'v_staff uuid := platform.review_current_staff();'" "'v_staff uuid := platform.current_account_id();'")"
  ["考情签字：发布不查 staff"]="$(afr "$QPS" "'v_staff uuid := platform.review_current_staff();'" "'v_staff uuid := platform.current_account_id();'")"
  ["考情签字：方案签字不比看到的摘要"]="$(afr "$QSS" "'IF p_expected_digest IS DISTINCT FROM v_digest THEN'" "'IF false THEN'")"
  ["考情签字：记录签字不比看到的摘要"]="$(afr "$QSR" "'IF p_expected_digest IS DISTINCT FROM v_digest THEN'" "'IF false THEN'")"
  ["考情签字：一人即可发布"]="$(afr "policy.signers_required()" "' SELECT 2 '" "' SELECT 1 '")"
  ["考情签字：驳回不清零"]="$(afr "$QAC" "'AND s.decision = ''rejected'' AND s.digest = v_digest) THEN'" "'AND false) THEN'")"
  ["考情签字：旧内容的签字也计数"]="$(afr "$QAC" "'AND s.decision = ''approved'' AND s.digest = v_digest);'" "'AND s.decision = ''approved'');'")"
  ["考情签字：方案摘要不含科目配置"]="$(afr "$QOD" "'FROM policy.subject_config c WHERE c.scheme_id = s.id)'" "'FROM policy.subject_config c WHERE false)'")"
  ["考情签字：同一账号可签两次"]="ALTER TABLE platform.policy_signoff DROP CONSTRAINT policy_signoff_once;"
  ["考情签字：计数不按账号去重"]="ALTER TABLE platform.policy_signoff DROP CONSTRAINT policy_signoff_once; $(afr "$QAC" "'count(DISTINCT s.account_id)'" "'count(s.account_id)'")"
  ["考情签字：可直接以已发布新建"]="$(afr "$QGS" "'IF v_new <> ''draft'' OR NEW.reviewed_at IS NOT NULL THEN'" "'IF false THEN'")"
  ["考情签字：新建可自带复核时间"]="$(afr "$QGS" "' OR NEW.reviewed_at IS NOT NULL THEN'" "' THEN'")"
  ["考情签字：写入方可直接改为已发布"]="$(afr "$QGS" "'(to_jsonb(OLD) ->> v_col) IS DISTINCT FROM ''published'' AND current_user <> ''starbuddy_authz'' THEN'" "'false THEN'")"
  ["考情签字：复核时间可不经函数写"]="$(afr "$QGS" "'IF NEW.reviewed_at IS DISTINCT FROM OLD.reviewed_at AND current_user <> ''starbuddy_authz'' THEN'" "'IF false THEN'")"
  ["考情签字：方案可自带复核时间新建"]="DROP TRIGGER guard_signoff_publish ON policy.exam_scheme;"
  ["考情签字：写入方可写复核时间"]="GRANT UPDATE (reviewed_at) ON policy.exam_scheme TO starbuddy_kge;"
  ["考情签字：写入方可改自报复核人"]="GRANT UPDATE (reviewers) ON policy.exam_scheme TO starbuddy_kge;"
  ["考情签字：写入方可调发布"]="GRANT EXECUTE ON FUNCTION $QPS TO starbuddy_kge;"
  ["考情签字：写入方可签字"]="GRANT EXECUTE ON FUNCTION $QSR TO starbuddy_kge;"
  ["考情签字：已发布的记录还能签"]="$(afr "$QSR" "'IF v_status <> ''draft'' THEN'" "'IF false THEN'")"
  ["考情签字：已发布的方案还能签"]="$(afr "$QSS" "'IF v_status <> ''draft'' THEN'" "'IF false THEN'")"
  ["考情签字：签字不锁对象"]="$(afr "$QSR" "'FROM policy.school h WHERE h.id = p_id FOR UPDATE;'" "'FROM policy.school h WHERE h.id = p_id;'")"
  ["考情签字：签字记录可改"]="DROP TRIGGER reject_policy_signoff_change ON platform.policy_signoff;"
  ["考情签字：签字记录可清空"]="DROP TRIGGER reject_policy_signoff_truncate ON platform.policy_signoff;"
  ["考情签字：BFF 可读签字人"]="GRANT SELECT ON platform.policy_signoff TO starbuddy_bff; CREATE POLICY t_ps ON platform.policy_signoff FOR SELECT TO starbuddy_bff USING (true);"
  ["考情签字：学习服务可读签字导出"]="GRANT EXECUTE ON FUNCTION platform.policy_signoff_record() TO starbuddy_learning;"
  ["考情签字：导出函数对 PUBLIC 开放"]="GRANT EXECUTE ON FUNCTION platform.policy_signoff_record() TO PUBLIC;"
  ["考情签字：写入方可签字（kge 签方案）"]="GRANT EXECUTE ON FUNCTION $QSS TO starbuddy_kge;"
)
# 考情方案发布路径的并发锁（specs/policy-schema-v0.9 lessons 后续项）：由 run.sh 的并发场景 6–10 发现；地图校验第 11 项的草稿路径由 77 发现
M+=(
  ["考情并发：发布不加同城同届咨询锁"]="$(afr "$QPS" "'PERFORM pg_advisory_xact_lock(hashtext(''policy.publish_scheme:'' || s.city || '':'' || s.cohort));'" "''")"
  ["考情并发：发布不锁方案行"]="$(afr "$QPS" "'SELECT * INTO s FROM policy.exam_scheme WHERE id = p_scheme FOR UPDATE;'" "'SELECT * INTO s FROM policy.exam_scheme WHERE id = p_scheme;'")"
  ["考情并发：发布不锁来源文件"]="$(afr "$QPS" "'PERFORM 1 FROM policy.source_document WHERE id = s.source_document_id FOR SHARE;'" "''")"
  ["考情并发：子表写入不锁方案行"]="$(afr 'policy.guard_scheme_frozen()' "'ORDER BY s.id FOR SHARE LOOP'" "'ORDER BY s.id LOOP'")"
  ["考情并发：方案签字不锁方案行"]="$(afr "$QSS" "'WHERE s.id = p_scheme FOR UPDATE;'" "'WHERE s.id = p_scheme;'")"
  ["考情：地图校验把草稿方案也算作已有方案"]="$(afr 'kge.validate_map_version(uuid)' "'AND e.status = ''''published'''')'" "')'")"
)

# 考情待定事项落地 PR1（0045–0048，specs/policy-decisions-v0.9.1）：由 77、79q、79y 与 run.sh 的并发场景 11–13 发现（授权类的同时由 80 的快照发现）。
# DBMUT_FILTER=届别 / 往年 / 控制线 / 来源清单 / staff 权限 分组跑
CE='platform.correct_exam_cohort(uuid, smallint)'
CG='platform.current_grade(smallint, timestamp with time zone)'
GCL='policy.guard_control_line()'
ASD='policy.add_source_domain(text, text)'
RSD='policy.retire_source_domain(text)'
NSH='policy.normalize_source_host(text)'
M+=(
  ["届别：年级推算不按 9 月切换"]="$(afr "$CG" "' - interval ''8 months'''" "''")"
  ["届别：年级越界不返回 NULL"]="$(afr "$CG" "'WHERE g BETWEEN 7 AND 9'" "'WHERE true'")"
  ["届别：建档标 derived 可与推算值不同"]="$(afr 'platform.guard_student_cohort()' "'IF NEW.exam_cohort_source = ''derived'' AND NEW.exam_cohort <> v_expected THEN'" "'IF false THEN'")"
  ["届别：之后改届别仍拿建档年级比对"]="DROP TRIGGER guard_student_cohort ON platform.student_profile; CREATE TRIGGER guard_student_cohort BEFORE INSERT OR UPDATE OF grade, exam_cohort ON platform.student_profile FOR EACH ROW EXECUTE FUNCTION platform.guard_student_cohort();"
  ["届别：更正按账号类型分渠道"]="$(afr "$CE" "'IF v_via_bff AND NOT v_via_review THEN'" "'IF platform.is_active_guardian() THEN'") $(afr "$CE" "'ELSIF v_via_review AND NOT v_via_bff THEN'" "'ELSIF true THEN'")"
  ["届别：超级用户也能更正"]="$(afr "$CE" "'ELSIF v_via_review AND NOT v_via_bff THEN'" "'ELSIF v_via_review THEN'")"
  ["届别：更正不核对绑定"]="$(afr "$CE" "'IF p_student IS NULL OR NOT platform.is_linked_guardian(p_student) THEN'" "'IF p_student IS NULL OR NOT platform.is_active_guardian() THEN'")"
  ["届别：学生本人可更正"]="$(afr "$CE" "'NOT platform.is_linked_guardian(p_student) THEN'" "'NOT platform.can_read_student(p_student) THEN'")"
  ["届别：客服更正不查 staff"]="$(afr "$CE" "'a.kind = ''staff'' AND a.status = ''active'' AND '" "''")"
  ["届别：更正不限 ±1"]="$(afr "$CE" "'IF abs(p_new_cohort - v_old) <> 1 THEN'" "'IF p_new_cohort = v_old THEN'")"
  ["届别：更正后年级可越界"]="$(afr "$CE" "'IF platform.current_grade(p_new_cohort, v_at) IS NULL THEN'" "'IF false THEN'")"
  ["届别：更正不锁档案行"]="$(afr "$CE" "'WHERE s.id = p_student FOR UPDATE;'" "'WHERE s.id = p_student;'")"
  ["届别：更正不写来源"]="$(afr "$CE" "'SET exam_cohort = p_new_cohort, exam_cohort_source = v_source WHERE'" "'SET exam_cohort = p_new_cohort WHERE'")"
  ["届别：更正不留记录"]="$(afr "$CE" "'VALUES (p_student, v_old, p_new_cohort, v_source, v_actor, v_at);'" "'SELECT p_student, v_old, p_new_cohort, v_source, v_actor, v_at WHERE false;'")"
  ["届别：已删除档案可更正"]="$(afr "$CE" "'IF v_old IS NULL OR v_erased IS NOT NULL THEN'" "'IF v_old IS NULL THEN'")"
  ["届别：更正记录对 BFF 可读"]="GRANT SELECT ON platform.exam_cohort_change TO starbuddy_bff; CREATE POLICY t_ecc ON platform.exam_cohort_change FOR SELECT TO starbuddy_bff USING (platform.can_read_student(student_id));"
  ["届别：更正记录不随删除请求删除"]="UPDATE platform.personal_table_registry SET strategy = 'exempt', exempt_reason = 'x', delete_order = NULL, erase_scope = NULL WHERE nspname = 'platform' AND relname = 'exam_cohort_change';"
  ["往年：BFF 看不到已撤下方案"]="DROP POLICY exam_scheme_front_read ON policy.exam_scheme; CREATE POLICY exam_scheme_front_read ON policy.exam_scheme FOR SELECT TO starbuddy_bff USING (status = 'published');"
  ["往年：BFF 看不到已撤下来源文件"]="DROP POLICY source_document_front_read ON policy.source_document; CREATE POLICY source_document_front_read ON policy.source_document FOR SELECT TO starbuddy_bff USING (status = 'published');"
  ["往年：BFF 能看草稿来源文件"]="DROP POLICY source_document_front_read ON policy.source_document; CREATE POLICY source_document_front_read ON policy.source_document FOR SELECT TO starbuddy_bff USING (true);"
  ["往年：BFF 能看草稿方案的招录规则"]="DROP POLICY admission_rule_front_read ON policy.admission_rule; CREATE POLICY admission_rule_front_read ON policy.admission_rule FOR SELECT TO starbuddy_bff USING (true);"
  ["往年：BFF 能看草稿方案的过渡规则"]="DROP POLICY transition_rule_front_read ON policy.transition_rule; CREATE POLICY transition_rule_front_read ON policy.transition_rule FOR SELECT TO starbuddy_bff USING (true);"
  ["往年：BFF 看不到已撤下方案的科目配置"]="DROP POLICY subject_config_front_read ON policy.subject_config; CREATE POLICY subject_config_front_read ON policy.subject_config FOR SELECT TO starbuddy_bff USING (EXISTS (SELECT 1 FROM policy.exam_scheme s WHERE s.id = scheme_id AND s.status = 'published'));"
  ["控制线：同一键可有多个已发布"]="DROP INDEX policy.control_line_one_published;"
  ["控制线：不核对控制线依据"]="$(afr "$GCL" "' AND r.control_line_basis <> ''none'') THEN'" "') THEN'")"
  ["控制线：没有方案也能录"]="$(afr "$GCL" "'IF v_total IS NULL THEN'" "'IF false THEN'")"
  ["控制线：分数可超过总分"]="$(afr "$GCL" "'IF NEW.score > v_total THEN'" "'IF false THEN'")"
  ["控制线：来源文件不核对"]="$(afr "$GCL" "'WHERE d.id = NEW.source_document_id AND d.status = ''published'' AND d.city = NEW.city'" "'WHERE d.id = NEW.source_document_id'")"
  ["控制线：来源文件不必覆盖该年"]="$(afr "$GCL" "'AND NEW.year BETWEEN d.applies_from_cohort AND coalesce(d.applies_to_cohort, NEW.year)) THEN'" "') THEN'")"
  ["控制线：已发布可改分数"]="$(afr "$GCL" "'IF OLD.status = ''published'' AND (NEW.status <> ''retired'' OR'" "'IF false AND (NEW.status <> ''retired'' OR'")"
  ["控制线：已撤下可复活"]="$(afr "$GCL" "'IF OLD.status = ''retired'' THEN'" "'IF false THEN'")"
  ["控制线：可不经签字发布"]="DROP TRIGGER guard_signoff_publish ON policy.control_line;"
  ["控制线：摘要不含分数"]="$(afr "$QOD" "'SELECT to_jsonb(k) - ''status'''" "'SELECT to_jsonb(k) - ''score'' - ''status'''")"
  ["控制线：导出不带城市"]="$(afr 'platform.policy_signoff_record()' "'coalesce(d.city, e.city, lh.city, h.city, k.city)'" "'coalesce(d.city, e.city, lh.city, h.city)'")"
  ["控制线：BFF 能看草稿"]="DROP POLICY control_line_front_read ON policy.control_line; CREATE POLICY control_line_front_read ON policy.control_line FOR SELECT TO starbuddy_bff USING (true);"
  ["控制线：BFF 能看复核时间"]="GRANT SELECT (reviewed_at) ON policy.control_line TO starbuddy_bff;"
  ["控制线：草稿可直接撤下"]="$(afr "$GCL" "'IF OLD.status = ''draft'' AND NEW.status = ''retired'' THEN'" "'IF false THEN'")"
  ["控制线：复核时间 CHECK 删掉"]="ALTER TABLE policy.control_line DROP CONSTRAINT control_line_reviewed;"
  ["往年：草稿方案可直接撤下"]="$(afr 'policy.guard_scheme()' "'IF OLD.status = ''draft'' AND NEW.status = ''retired'' THEN'" "'IF false THEN'") $(afr 'policy.guard_scheme()' "'(OLD.status = ''draft'' AND NEW.status = ''published'')'" "'(OLD.status = ''draft'' AND NEW.status IN (''published'', ''retired''))'")"
  ["往年：草稿来源文件可直接撤下"]="$(afr 'policy.guard_source_document()' "'IF OLD.status = ''draft'' AND NEW.status = ''retired'' THEN'" "'IF false THEN'")"
  ["往年：方案复核时间 CHECK 删掉"]="ALTER TABLE policy.exam_scheme DROP CONSTRAINT exam_scheme_reviewed;"
  ["往年：来源文件复核时间 CHECK 删掉"]="ALTER TABLE policy.source_document DROP CONSTRAINT source_document_reviewed;"
  ["届别：更正记录不限 ±1（表约束）"]="ALTER TABLE platform.exam_cohort_change DROP CONSTRAINT exam_cohort_change_step;"
  ["届别：调用角色取常量而不是登录角色"]="$(afr "$CE" "'session_user::text'" "'''postgres'''")"
  ["届别：建档来源可是客服更正"]="$(afr 'platform.guard_student_cohort()' "'IF NEW.exam_cohort_source NOT IN (''derived'', ''guardian_report'') THEN'" "'IF false THEN'")"
  ["届别：改届别可不改来源"]="DROP TRIGGER guard_student_cohort_update ON platform.student_profile;"
  ["届别：更正函数内也要求改来源"]="$(afr 'platform.guard_student_cohort_update()' "' AND current_user <> ''starbuddy_authz'''" "''")"
  ["来源清单：收公共后缀"]="$(afr "$NSH" "'''gov.cn'', ''com.cn'', ''edu.cn'', '" "''")"
  ["攻击面：控制线归档表对 BFF 可读"]="GRANT SELECT ON policy.admission_line_control_line_archive TO starbuddy_bff;"
  ["控制线：写入方可以删除"]="GRANT DELETE ON policy.control_line TO starbuddy_kge;"
  ["来源清单：新增不查权限"]="$(afr "$ASD" "'IF NOT EXISTS (SELECT 1 FROM platform.staff_permission p WHERE p.staff_id = v_staff AND p.permission = ''policy_source_admin'') THEN'" "'IF false THEN'")"
  ["来源清单：下线不查权限"]="$(afr "$RSD" "'IF NOT EXISTS (SELECT 1 FROM platform.staff_permission p WHERE p.staff_id = v_staff AND p.permission = ''policy_source_admin'') THEN'" "'IF false THEN'")"
  ["来源清单：权限不看是谁"]="$(afr "$ASD" "'p.staff_id = v_staff AND '" "''")"
  ["来源清单：新增不查 staff"]="$(afr "$ASD" "'v_staff uuid := platform.review_current_staff();'" "'v_staff uuid := platform.current_account_id();'")"
  ["来源清单：主机名不转小写"]="$(afr "$NSH" "'lower(btrim(p_host))'" "'btrim(p_host)'")"
  ["来源清单：不去结尾的点"]="$(afr "$NSH" "' AS h) x'" "' AS h0) y, LATERAL (SELECT lower(btrim(p_host)) AS h) x'")"
  ["来源清单：收 IP"]="$(afr "$NSH" "')+[a-z]('" "')+[a-z0-9]('")"
  ["来源清单：下线删行"]="GRANT DELETE ON policy.source_domain TO starbuddy_authz; $(afr "$RSD" "'UPDATE policy.source_domain SET status = ''retired'' WHERE host = v_host;'" "'DELETE FROM policy.source_domain WHERE host = v_host;'")"
  ["来源清单：新增不记操作"]="$(afr "$ASD" "'VALUES (v_host, v_action, v_note, v_staff, clock_timestamp());'" "'SELECT v_host, v_action, v_note, v_staff, clock_timestamp() WHERE false;'")"
  ["来源清单：下线不记操作"]="$(afr "$RSD" "'VALUES (v_host, ''retire'', NULL, v_staff, clock_timestamp());'" "'SELECT v_host, ''retire'', NULL, v_staff, clock_timestamp() WHERE false;'")"
  ["来源清单：已在清单也能新增"]="$(afr "$ASD" "'IF v_status = ''active'' THEN'" "'IF false THEN'")"
  ["来源清单：登记人写账号"]="$(afr "$ASD" "'''review-console'''" "'v_staff::text'")"
  ["来源清单：操作记录可改"]="DROP TRIGGER reject_source_domain_change_change ON platform.policy_source_domain_change;"
  ["来源清单：操作记录可清空"]="DROP TRIGGER reject_source_domain_change_truncate ON platform.policy_source_domain_change;"
  ["staff 权限：学生账号也能有"]="DROP TRIGGER guard_staff_permission ON platform.staff_permission;"
  ["staff 权限：签字身份读得到别人的"]="DROP POLICY staff_permission_self_read ON platform.staff_permission; CREATE POLICY staff_permission_self_read ON platform.staff_permission FOR SELECT TO starbuddy_review USING (true);"
  ["staff 权限：签字身份可授予"]="GRANT INSERT ON platform.staff_permission TO starbuddy_review; CREATE POLICY t_sp ON platform.staff_permission FOR INSERT TO starbuddy_review WITH CHECK (true);"
  ["攻击面：届别更正记录对 BFF 可读"]="GRANT SELECT ON platform.exam_cohort_change TO starbuddy_bff;"
  ["攻击面：来源清单函数授给知识服务"]="GRANT EXECUTE ON FUNCTION $ASD TO starbuddy_kge;"
  ["攻击面：届别更正授给学习服务"]="GRANT EXECUTE ON FUNCTION $CE TO starbuddy_learning;"
)

# 考情待人确认事项按默认处理 PR3（0049 platform、0050 policy，specs/policy-decisions-v0.9.1“PR3”）：由 79y、79z、80 与 run.sh 的并发场景 14 发现
# （授权类的同时由 80 的快照发现）。DBMUT_FILTER=届别 / 导出届别 / 来源清单 / 来源说明 / 考情 / 诊断会话 / 退回额度 分组跑
ILO='policy.idn_label_ok(text)'
RGC='platform.meter_refund_grade_changed(uuid)'
LSH='policy.lock_active_source_host(text)'
M+=(
  ["届别：客服更正认别的权限"]="$(afr "$CE" "'p.permission = ''cohort_correction'')'" "'true)'")"
  ["届别：客服更正不查权限表"]="$(afr "$CE" "'JOIN platform.staff_permission p ON p.staff_id = a.id'" "'LEFT JOIN platform.staff_permission p ON true'") $(afr "$CE" "'p.permission = ''cohort_correction'')'" "'true)'")"
  ["届别：客服更正不查学生是否存在"]="$(afr "$CE" "'OR NOT EXISTS (SELECT 1 FROM platform.student_profile s WHERE s.id = p_student AND s.erased_at IS NULL) THEN'" "'THEN'")"
  ["届别：客服更正对已删除档案报不同的错"]="$(afr "$CE" "' AND s.erased_at IS NULL) THEN'" "') THEN'")"
  ["导出届别：profile 不带届别与来源"]="UPDATE platform.personal_table_registry SET export_columns = ARRAY['grade', 'region', 'birth_year_month', 'created_at'] WHERE nspname = 'platform' AND relname = 'student_profile';"
  ["导出届别：更正记录不导出"]="UPDATE platform.personal_table_registry SET export_topic = NULL, export_columns = NULL WHERE nspname = 'platform' AND relname = 'exam_cohort_change';"
  ["导出届别：更正记录带操作者账号"]="UPDATE platform.personal_table_registry SET export_columns = ARRAY['old_cohort', 'new_cohort', 'source', 'changed_at', 'changed_by'] WHERE nspname = 'platform' AND relname = 'exam_cohort_change';"
  ["导出届别：更正记录只导本人做的"]="UPDATE platform.personal_table_registry SET export_filter = 'changed_by = platform.current_account_id()' WHERE nspname = 'platform' AND relname = 'exam_cohort_change';"
  ["来源清单：不核对 xn-- 标签"]="$(afr "$NSH" "' AND NOT EXISTS (SELECT 1 FROM unnest(string_to_array(h, ''.'')) l WHERE NOT policy.idn_label_ok(l))'" "''")"
  ["来源清单：xn-- 不做往返核对"]="$(afr "$ILO" "'RETURN v_enc = v_in;'" "'RETURN true;'")"
  ["来源清单：xn-- 解码溢出不拒"]="$(afr "$ILO" "'IF v_i > 2147483647 THEN RETURN false; END IF;'" "''") $(afr "$ILO" "'IF v_w > 2147483647 THEN RETURN false; END IF;'" "''") $(afr "$ILO" "'IF v_n > 0x10FFFF OR v_n BETWEEN 0xD800 AND 0xDFFF THEN RETURN false; END IF;'" "''")"
  ["来源清单：xn-- 解码出零宽字符不拒"]="$(afr "$ILO" "'WHERE policy.is_hidden_codepoint(c)'" "'WHERE false'")"
  ["来源清单：xn-- 解码出句号变体不拒"]="$(afr "$ILO" "'OR c IN (0x3002, 0xFE52, 0xFF0E, 0xFF61)'" "''")"
  ["来源清单：xn-- 标签不限 63 个字符"]="$(afr "$ILO" "'IF char_length(p_label) > 63 THEN'" "'IF false THEN'")"
  ["来源清单：xn-- 前缀区分大小写"]="$(afr "$ILO" "'IF lower(left(p_label, 4)) <> ''xn--'' THEN'" "'IF left(p_label, 4) <> ''xn--'' THEN'")"
  ["来源说明：函数不拒格式字符"]="$(afr "$ASD" "'IF policy.has_hidden_chars(v_note) THEN'" "'IF false THEN'")"
  ["来源说明：码位表漏了零宽字符"]="$(afr 'policy.is_hidden_codepoint(integer)' "'OR p BETWEEN 0x200B AND 0x200F'" "'OR p BETWEEN 0x200C AND 0x200F'")"
  ["来源说明：码位表漏了行段分隔符"]="$(afr 'policy.is_hidden_codepoint(integer)' "'OR p BETWEEN 0x2028 AND 0x2029'" "''")"
  ["来源说明：码位表多收了普通空格"]="$(afr 'policy.is_hidden_codepoint(integer)' "'SELECT p BETWEEN 0x1 AND 0x1F'" "'SELECT p BETWEEN 0x1 AND 0x20'")"
  ["来源说明：码位表漏了补充平面的格式字符"]="$(afr 'policy.is_hidden_codepoint(integer)' "'OR p BETWEEN 0x13430 AND 0x1343F'" "'OR p BETWEEN 0x13430 AND 0x13438'")"  ["来源说明：清单行 CHECK 删掉"]="ALTER TABLE policy.source_domain DROP CONSTRAINT source_domain_issuer_visible;"
  ["来源说明：操作记录 CHECK 删掉"]="ALTER TABLE platform.policy_source_domain_change DROP CONSTRAINT policy_source_domain_change_note_visible;"
  ["考情：url_host 收显式端口"]="CREATE OR REPLACE FUNCTION policy.url_host(p text) RETURNS text LANGUAGE sql IMMUTABLE PARALLEL SAFE AS \$\$ SELECT lower(substring(p FROM '^https://([^/:?#]+)')) \$\$;"
  ["考情：来源主机名核对不加共享锁"]="$(afr "$LSH" "' FOR SHARE;'" "';'")"
  ["考情：共享锁函数不看状态"]="$(afr "$LSH" "' AND d.status = ''active'' FOR SHARE;'" "' FOR SHARE;'")"
  ["考情：共享锁函数授给 BFF"]="GRANT EXECUTE ON FUNCTION $LSH TO starbuddy_bff;"
  ["诊断会话：学习服务可改建立年级"]="GRANT UPDATE (start_grade) ON platform.diagnosis_session TO starbuddy_learning;"
  ["诊断会话：建立年级不限 7–9"]="ALTER TABLE platform.diagnosis_session DROP CONSTRAINT diagnosis_session_start_grade_check;"
  ["诊断会话：进行中也能带放弃原因"]="ALTER TABLE platform.diagnosis_session DROP CONSTRAINT diagnosis_session_abandon_reason_status;"
  ["诊断会话：BFF 可写放弃原因"]="GRANT UPDATE (abandon_reason) ON platform.diagnosis_session TO starbuddy_bff;"
  ["退回额度：不看放弃原因"]="$(afr "$RGC" "' AND s.abandon_reason = ''grade_changed'''" "''")"
  ["退回额度：不看额度桶"]="$(afr "$RGC" "'AND e.resource_type = ''diagnosis_full'')'" "')'")"
  ["退回额度：不核对幂等键"]="$(afr "$RGC" "' AND s.idempotency_key::text = r.idempotency_key'" "''")"
  ["退回额度：不核对会话属于同一学生"]="$(afr "$RGC" "' AND s.student_id = r.student_id AND s.idempotency_key'" "' AND s.idempotency_key'")"
  ["退回额度：退回时不再核届别"]="$(afr "$RGC" "'AND platform.diagnosis_grade_changed(s.student_id, s.created_at, s.start_grade)) THEN'" "'AND true) THEN'")"
  ["退回额度：余量为 0 也退"]="$(afr "$RGC" "'IF v_left IS NULL OR v_left <= 0 THEN'" "'IF v_left IS NULL THEN'")"
  ["退回额度：余量不扣已有更正"]="$(afr "$RGC" "'WHERE c.corrects_entry_id = v_settle.entry_id AND c.corrects_occurred_at = v_settle.occurred_at), 0)'" "'WHERE false), 0)'")"
  ["退回额度：不限次数"]="$(afr "$RGC" "'AND l.student_id = r.student_id AND l.kind = ''adjust'''" "'AND false AND l.kind = ''adjust'''")"
  ["诊断会话：可直接以已放弃新建"]="$(afr 'platform.diagnosis_session_write_guard()' "'IF TG_OP = ''INSERT'' AND (NEW.status <> ''active'' OR'" "'IF false AND (NEW.status <> ''active'' OR'")"
  ["诊断会话：库里不核实届别"]="$(afr 'platform.diagnosis_session_write_guard()' "'AND NOT platform.diagnosis_grade_changed(NEW.student_id, NEW.created_at, NEW.start_grade) THEN'" "'AND false THEN'")"
  ["诊断会话：旧会话也能标届别变化"]="$(afr 'platform.diagnosis_grade_changed(uuid, timestamp with time zone, smallint)' "'SELECT p_start_grade IS NOT NULL'" "'SELECT true'")"
  ["退回额度：不核对本人"]="$(afr "$RGC" "'PERFORM platform.meter_authorize_usage(p_usage);'" "''")"
  ["退回额度：可重复退"]="$(afr "$RGC" "'AND l.source_method = r.source_method AND l.source_idempotency_key = r.idempotency_key)'" "'AND false)'")"
  ["退回额度：不加回 granted"]="$(afr "$RGC" "'UPDATE platform.entitlement SET granted = granted + v_left WHERE id = r.entitlement_id;'" "''")"
  ["退回额度：未结算的不释放"]="$(afr "$RGC" "'RETURN platform.meter_release(p_usage, ''grade_changed'');'" "'RETURN jsonb_build_object(''outcome'', ''conflict'', ''state'', r.state);'")"
  ["退回额度：授给学习服务"]="GRANT EXECUTE ON FUNCTION $RGC TO starbuddy_learning;"
  ["攻击面：客服更正不查 cohort_correction"]="$(afr "$CE" "'JOIN platform.staff_permission p ON p.staff_id = a.id'" "'LEFT JOIN platform.staff_permission p ON true'") $(afr "$CE" "'p.permission = ''cohort_correction'')'" "'true)'")"
)

# 考情 PR4（0051 platform、0052 policy，specs/policy-decisions-v0.9.1“PR4”）：由 79za、80 与 run.sh 的并发场景 16、16b、17 发现
# （攻击面类只由 80 发现）。DBMUT_FILTER=选科年级 / 锁等待 分组跑
ENR='platform.enroll_subject(uuid, platform.subject)'
PAU='platform.pause_subject(uuid, platform.subject)'
M+=(
  ["选科年级：库里不核对年级"]="$(afr "$ENR" "'IF platform.current_grade(v_cohort, platform.meter_now()) IS NULL THEN'" "'IF false THEN'")"
  ["选科年级：按墙钟推算"]="$(afr "$ENR" "'platform.current_grade(v_cohort, platform.meter_now())'" "'platform.current_grade(v_cohort, now())'")"
  ["选科年级：读建档年级"]="$(afr "$ENR" "'IF platform.current_grade(v_cohort, platform.meter_now()) IS NULL THEN'" "'IF (SELECT sp.grade FROM platform.student_profile sp WHERE sp.id = p_student) NOT BETWEEN 7 AND 9 THEN'")"
  ["选科年级：拒绝报成别的错误码"]="$(afr "$ENR" "'RAISE EXCEPTION ''GRADE_OUT_OF_RANGE'' USING ERRCODE = ''object_not_in_prerequisite_state'';'" "'RAISE EXCEPTION ''GRADE_OUT_OF_RANGE'' USING ERRCODE = ''check_violation'';'")"
  ["选科年级：年级核对先于授权"]="$(afr "$ENR" "'IF p_student IS NULL OR p_subject IS NULL OR NOT platform.can_read_student(p_student) THEN'" "'IF p_student IS NOT NULL AND platform.current_grade((SELECT sp.exam_cohort FROM platform.student_profile sp WHERE sp.id = p_student), platform.meter_now()) IS NULL THEN RAISE EXCEPTION ''GRADE_OUT_OF_RANGE'' USING ERRCODE = ''object_not_in_prerequisite_state''; END IF; IF p_student IS NULL OR p_subject IS NULL OR NOT platform.can_read_student(p_student) THEN'")"
  ["选科年级：不看档案删除"]="$(afr "$ENR" "'IF v_erased IS NOT NULL THEN'" "'IF false THEN'")"
  ["选科年级：删除状态报成年级错误"]="$(afr "$ENR" "'IF v_erased IS NOT NULL THEN
    RAISE EXCEPTION ''not permitted'' USING ERRCODE = ''insufficient_privilege'';'" "'IF v_erased IS NOT NULL THEN
    RAISE EXCEPTION ''GRADE_OUT_OF_RANGE'' USING ERRCODE = ''object_not_in_prerequisite_state'';'")"
  ["选科年级：不锁档案行"]="$(afr "$ENR" "' WHERE sp.id = p_student FOR SHARE;'" "' WHERE sp.id = p_student;'")"
  ["选科年级：暂停也核对年级"]="$(afr "$PAU" "'s := platform.meter_main_subscription(p_student);'" "'IF platform.current_grade((SELECT sp.exam_cohort FROM platform.student_profile sp WHERE sp.id = p_student), platform.meter_now()) IS NULL THEN RAISE EXCEPTION ''GRADE_OUT_OF_RANGE'' USING ERRCODE = ''object_not_in_prerequisite_state''; END IF; s := platform.meter_main_subscription(p_student);'")"
  ["锁等待：下线不设上限"]="ALTER FUNCTION $RSD RESET lock_timeout;"
  ["锁等待：新增不设上限"]="ALTER FUNCTION $ASD RESET lock_timeout;"
  ["锁等待：下线上限放宽到 30 秒"]="ALTER FUNCTION $RSD SET lock_timeout = '30s';"
  ["锁等待：下线超时报成规则错误"]="$(afr "$RSD" "'USING ERRCODE = ''lock_not_available'';'" "'USING ERRCODE = ''check_violation'';'")"
  ["锁等待：新增超时不给说明"]="$(afr "$ASD" "'官方来源清单的这一行正被占用，请稍后重试'" "'锁等待超时'")"
  ["锁等待：取锁不设空闲上限"]="$(afr "$LSH" "'PERFORM set_config(''idle_in_transaction_session_timeout'', ''60s'', true);'" "'NULL;'")"
  ["锁等待：取锁放宽调用方更短的空闲上限"]="$(afr "$LSH" "'IF v_idle = 0 OR v_idle > 60000 THEN'" "'IF true THEN'")"
  ["锁等待：空闲上限留到会话结束"]="$(afr "$LSH" "'''60s'', true)'" "'''60s'', false)'")"
  ["攻击面：来源清单下线不设锁等待上限"]="ALTER FUNCTION $RSD RESET lock_timeout;"
  ["攻击面：清单取锁函数授给学习服务"]="GRANT EXECUTE ON FUNCTION $LSH TO starbuddy_learning;"
)

# PLT-14 第 1 步 家长诉求（0053，specs/family-goals-monthly-plan）：由 79zb 发现（授权类的同时由 80 的快照、清单类由 79p 发现）。
# DBMUT_FILTER=家长诉求 只跑这一组
GGS='insight.submit_guardian_goal(uuid, platform.subject[], jsonb, uuid, text)'
GGV='insight.guardian_goal_view(uuid)'
M+=(
  ["家长诉求：写入认学生本人"]="$(afr "$GGS" "'IF p_student IS NULL OR NOT platform.is_linked_guardian(p_student) THEN'" "'IF p_student IS NULL OR NOT platform.can_read_student(p_student) THEN'")"
  ["家长诉求：学生读得到"]="$(afr "$GGV" "'IF p_student IS NULL OR NOT platform.is_linked_guardian(p_student) THEN'" "'IF p_student IS NULL OR NOT platform.can_read_student(p_student) THEN'")"
  ["家长诉求：取值核对先于授权"]="$(afr "$GGS" "'-- 授权先于一切取值核对'" "'IF NOT insight.valid_goal_note(p_note) THEN RAISE EXCEPTION ''x'' USING ERRCODE = ''invalid_parameter_value''; END IF; -- 授权先于一切取值核对'")"
  ["家长诉求：更正覆盖另一位家长"]="$(afr "$GGS" "'WHERE student_id = p_student AND guardian_account_id = v_me AND superseded_at IS NULL;'" "'WHERE student_id = p_student AND superseded_at IS NULL;'")"
  ["家长诉求：历史可改写"]="DROP TRIGGER guard_fact_update ON insight.guardian_goal;"
  ["家长诉求：暂停的学科也算在读"]="$(afr "$GGS" "'n.subject::text = x.s AND n.status = ''active'')) THEN'" "'n.subject::text = x.s)) THEN'")"
  ["家长诉求：初二可设学校"]="$(afr "$GGS" "'IF v_grade <> 9 THEN'" "'IF false THEN'")"
  ["家长诉求：学校不看发布与在办"]="$(afr "$GGS" "'WHERE h.id = p_school AND h.review_status = ''published'' AND h.status = ''active'''" "'WHERE h.id = p_school'")"
  ["家长诉求：学校不看地区"]="$(afr "$GGS" "'AND d.status = ''published'' AND h.city::text = v_region) THEN'" "'AND d.status = ''published'') THEN'")"
  ["家长诉求：可选学校不分年级"]="$(afr "$GGV" "'CASE WHEN v_grade = 9 THEN'" "'CASE WHEN true THEN'")"
  ["家长诉求：说明不拒隐形字符"]="CREATE OR REPLACE FUNCTION insight.valid_goal_note(p text) RETURNS boolean LANGUAGE plpgsql IMMUTABLE SET search_path = pg_catalog, pg_temp AS \$f\$ BEGIN RETURN p IS NULL OR char_length(p) BETWEEN 1 AND 200; END \$f\$;"
  ["家长诉求：不限写入量"]="$(afr "$GGS" "'>= 20 THEN'" "'>= 2000 THEN'")"
  ["家长诉求：已毕业照样写"]="$(afr "$GGS" "'IF v_grade IS NULL THEN'" "'IF false THEN'")"
  ["家长诉求：来源不限家长"]="ALTER TABLE insight.guardian_goal DROP CONSTRAINT guardian_goal_source_type_check;"
  ["家长诉求：学习服务可读表"]="GRANT SELECT ON insight.guardian_goal TO starbuddy_learning;"
  ["家长诉求：洞察服务可调读函数"]="GRANT EXECUTE ON FUNCTION $GGV TO starbuddy_insight;"
  ["家长诉求：导出不按作者过滤"]="UPDATE platform.personal_table_registry SET export_filter = NULL WHERE nspname = 'insight' AND relname = 'guardian_goal';"
  ["家长诉求：目标分校验依赖短路"]="CREATE OR REPLACE FUNCTION insight.valid_goal_targets(p jsonb) RETURNS boolean LANGUAGE sql IMMUTABLE SET search_path = pg_catalog AS \$f\$ SELECT jsonb_typeof(p) = 'array' AND NOT EXISTS (SELECT 1 FROM jsonb_array_elements(p) e WHERE NOT ((e ->> 'full_score')::numeric > 0 AND (e ->> 'full_score')::numeric <= 300 AND (e ->> 'target_score')::numeric BETWEEN 0 AND (e ->> 'full_score')::numeric) OR jsonb_typeof(e) <> 'object' OR NOT (e ->> 'subject') = ANY (enum_range(NULL::platform.subject)::text[])) \$f\$;"
  ["家长诉求：学校名称不看在办"]="$(afr "$GGV" "'WHERE h.id = g.school_id AND h.review_status = ''published'' AND h.status = ''active'''" "'WHERE h.id = g.school_id AND h.review_status = ''published'''")"
  ["家长诉求：不登记个人层表清单"]="DELETE FROM platform.personal_table_registry WHERE nspname = 'insight' AND relname = 'guardian_goal';"
)

# PLT-14 第 2 步 月度计划快照（0054，specs/family-goals-monthly-plan/step2-spec.md）：由 79zc 发现（授权类的同时由 80 的快照、清单类由 79p 发现；
# 攻击面类只由 80 发现）。DBMUT_FILTER=月度计划 只跑这一组
MPS='insight.save_monthly_plan(uuid, date, jsonb)'
MPF='insight.monthly_plan_facts(uuid, date)'
MPG='insight.monthly_plan_guardian_view(uuid, date)'
MPV='insight.monthly_plan_student_view(uuid, date)'
MPD='insight.monthly_plan_guard()'
MPL='insight.monthly_live_claim(uuid, text, text, integer, text)'
MPE='insight.monthly_plan_entitled(uuid)'
MPR='insight.monthly_plan_replay_inputs(uuid, date)'
M+=(
  ["月度计划：写入不查权益"]="$(afr "$MPS" "'IF NOT insight.monthly_plan_entitled(p_student) THEN'" "'IF false THEN'")"
  ["月度计划：写入先查权益再返回已有快照"]="$(afr "$MPS" "'PERFORM pg_advisory_xact_lock('" "'IF NOT insight.monthly_plan_entitled(p_student) THEN RAISE EXCEPTION ''PLAN_NOT_ENTITLED'' USING ERRCODE = ''object_not_in_prerequisite_state''; END IF; PERFORM pg_advisory_xact_lock('")"
  ["月度计划：已有快照仍写新行"]="$(afr "$MPS" "'IF FOUND THEN'" "'IF false THEN'")"
  ["月度计划：写入不查档案"]="$(afr "$MPS" "'IF NOT EXISTS (SELECT 1 FROM platform.student_profile s WHERE s.id = p_student AND s.erased_at IS NULL) THEN'" "'IF false THEN'")"
  ["月度计划：权益不看周报取值"]="$(afr "$MPE" "'position(''monthly'' IN f.value_code) > 0'" "'true'")"
  ["月度计划：权益看错权益代码"]="$(afr "$MPE" "'WHERE f.feature_code = ''weekly_report'''" "'WHERE f.feature_code = ''lesson_mode'''")"
  ["月度计划：输入读已解绑家长的诉求"]="$(afr "$MPF" "' AND l.status = ''active'''" "''")"
  ["月度计划：输入读旧版诉求"]="$(afr "$MPF" "'WHERE g.student_id = p_student AND g.superseded_at IS NULL'" "'WHERE g.student_id = p_student'")"
  ["月度计划：输入带补充说明"]="$(afr "$MPF" "'''target_scores'', g.target_scores)'" "'''target_scores'', g.target_scores, ''note'', g.note)'")"
  ["月度计划：输入不分月份复测"]="$(afr "$MPF" "' AND r.due_at < v_end'" "''")"
  ["月度计划：守卫收家长观察来源"]="$(afr "$MPD" "' AND c.source_type <> ''guardian'''" "''")"
  ["月度计划：学习计划不要求家长可见"]="$(afr "$MPD" "' AND ''guardian'' = ANY (c.visibility) AND c.source_type'" "' AND c.source_type'")"
  ["月度计划：学习计划不要求学生可见"]="$(afr "$MPD" "'''student'' = ANY (c.visibility) AND '" "''")"
  ["月度计划：守卫不查所引判断在输入引用里"]="$(afr "$MPD" "'WHERE NOT EXISTS (SELECT 1 FROM jsonb_array_elements(NEW.input_refs -> ''claims'') r WHERE r ->> 0 = x.cid)) THEN'" "'WHERE false) THEN'")"
  ["月度计划：守卫收已点亮的判断"]="$(afr "$MPD" "'IN (''unseen'', ''understand'', ''execute'', ''stable'')'" "'IS NOT NULL'")"
  ["月度计划：守卫不查层次"]="$(afr "$MPD" "'AND c.value ->> ''level'' = e ->> ''cognitive_level'''" "'AND true'")"
  ["月度计划：守卫不查判断属于该学生"]="$(afr "$MPD" "'c.student_id = NEW.student_id AND c.target_type = ''node'''" "'c.student_id IS NOT NULL AND c.target_type = ''node'''")"
  ["月度计划：守卫收待验证的判断"]="$(afr "$MPD" "'c.status IN (''hypothesis'', ''established'') AND '" "''")"
  ["月度计划：守卫收已过期的判断"]="$(afr "$MPD" "' AND c.expires_at > now()'" "''")"
  ["月度计划：守卫不查月份"]="$(afr "$MPD" "'IF NEW.month IS DISTINCT FROM date_trunc'" "'IF false AND NEW.month IS DISTINCT FROM date_trunc'")"
  ["月度计划：守卫不查日期在本月"]="$(afr "$MPD" "'WHERE insight.monthly_date(d) < NEW.month OR insight.monthly_date(d) >= v_end)'" "'WHERE false)'")"
  ["月度计划：分差不查诉求作者仍绑定"]="$(afr "$MPD" "' AND l.status = ''active'''" "''")"
  ["月度计划：分差不查诉求当前版"]="$(afr "$MPD" "' AND g.superseded_at IS NULL'" "''")"
  ["月度计划：分差不查成绩当前版"]="$(afr "$MPD" "' AND r.superseded_at IS NULL'" "''")"
  ["月度计划：分差不查考试判断家长可见"]="$(afr "$MPD" "'''guardian'' = ANY (c.visibility))'" "'true)'")"
  ["月度计划：上月点亮不查迁移门槛"]="$(afr "$MPD" "'AND c.value ->> ''gate'' IN (''transfer'', ''retain'')'" "''") $(afr "$MPD" "'AND h.value ->> ''gate'' IN (''transfer'', ''retain''))'" "')'")"
  ["月度计划：上月点亮不限上个月进入迁移"]="$(afr "$MPD" "') < v_start)) THEN'" "') IS NOT NULL)) THEN'")"
  ["月度计划：上月点亮不查在上月计划里"]="$(afr "$MPD" "'n ->> ''subject'' = e ->> ''subject'' AND n ->> ''node_id'' = e ->> ''node_id'''" "'true'")"
  ["月度计划：上月回顾不核对上月快照"]="$(afr "$MPD" "'IF v_prev_nodes IS NULL OR '" "'IF false AND '")"
  ["月度计划：快照可修改"]="$(afr "$MPD" "'RAISE EXCEPTION ''月度计划只追加，不能修改'''" "'RETURN NEW; RAISE EXCEPTION ''x'''")"
  ["月度计划：删除不限删除请求"]="DROP TRIGGER monthly_plan_guard ON insight.monthly_plan;"
  ["月度计划：快照可清空"]="DROP TRIGGER monthly_plan_truncate ON insight.monthly_plan;"
  ["月度计划：分差可存值"]="$(afr 'insight.valid_monthly_review(jsonb)' "'''goal_id'', ''subject'', ''template_key'', ''template_version'']) THEN'" "'''goal_id'', ''subject'', ''template_key'', ''template_version'']) AND false THEN'")"
  ["月度计划：上月回顾收不引用判断的计数"]="$(afr 'insight.valid_monthly_review(jsonb)' "'ARRAY[''copy_key'', ''lit'', ''month'', ''planned_nodes'']) THEN'" "'ARRAY[''copy_key'', ''lit'', ''month'', ''planned_nodes'']) AND false THEN'")"
  ["月度计划：注意事项变量收文字"]="$(afr 'insight.valid_monthly_notes(jsonb)' "'jsonb_typeof(v.value) = ''boolean'''" "'jsonb_typeof(v.value) IN (''boolean'', ''string'')'")"
  ["月度计划：学生段收多余字段"]="$(afr 'insight.valid_monthly_student_plan(jsonb)' "'ARRAY[''claim_id'', ''cognitive_level'', ''copy_key'', ''node_id'', ''position'', ''subject'', ''template_key'', ''template_version'']) THEN'" "'ARRAY[''claim_id'', ''cognitive_level'', ''copy_key'', ''node_id'', ''position'', ''subject'', ''template_key'', ''template_version'']) AND false THEN'")"
  ["月度计划：家长视图认学生本人"]="$(afr "$MPG" "'NOT platform.is_linked_guardian(p_student)'" "'NOT platform.can_read_student(p_student)'")"
  ["月度计划：学生视图认家长"]="$(afr "$MPV" "'NOT platform.is_self_student(p_student)'" "'NOT platform.can_read_student(p_student)'")"
  ["月度计划：家长视图分差不按作者"]="$(afr "$MPG" "' AND g.guardian_account_id = v_me'" "''")"
  ["月度计划：家长视图给诉求 ID"]="$(afr "$MPG" "'''subject'', e ->> ''subject'', ''exam_type'''" "'''goal_id'', e ->> ''goal_id'', ''subject'', e ->> ''subject'', ''exam_type'''")"
  ["月度计划：家长视图给精确分差"]="$(afr "$MPG" "'insight.monthly_gap_band(ts.target - r.raw_score)'" "'to_jsonb(ts.target - r.raw_score)'")"
  ["月度计划：家长视图泄露排序依据"]="$(afr "$MPG" "'''guardian_notes'', m.guardian_notes,'" "'''guardian_notes'', m.guardian_notes, ''plan_basis'', m.plan_basis,'")"
  ["月度计划：家长视图分差不核成绩当前版"]="$(afr "$MPG" "' AND r.superseded_at IS NULL'" "''")"
  ["月度计划：家长视图分差不核诉求当前版"]="$(afr "$MPG" "' AND g.superseded_at IS NULL'" "''")"
  ["月度计划：学生视图带回顾"]="$(afr "$MPV" "'''student_plan'', insight.monthly_live_student_plan(m.student_plan, p_student, ''student''))'" "'''student_plan'', insight.monthly_live_student_plan(m.student_plan, p_student, ''student''), ''review'', m.review)'")"
  ["月度计划：视图不再核判断"]="$(afr "$MPL" "'AND c.status IN (''hypothesis'', ''established'') AND c.expires_at > now() AND p_audience = ANY (c.visibility)'" "'AND true'")"
  ["月度计划：学生视图不滤家长观察来源"]="$(afr "$MPL" "' AND (p_audience <> ''student'' OR c.source_type <> ''guardian'')'" "''")"
  ["月度计划：重放取回未登记的诉求"]="$(afr "$MPR" "'AND g.id::text IN (SELECT jsonb_array_elements_text(m.input_refs -> ''goals''))'" "'AND true'")"
  ["月度计划：重放取回未登记的复测"]="$(afr "$MPR" "'AND r.id::text IN (SELECT jsonb_array_elements_text(m.input_refs -> ''retest_tasks''))'" "'AND true'")"
  ["月度计划：学习服务可调重放函数"]="GRANT EXECUTE ON FUNCTION $MPR TO starbuddy_learning;"
  ["月度计划：BFF 可调写入函数"]="GRANT EXECUTE ON FUNCTION $MPS TO starbuddy_bff;"
  ["月度计划：学习服务可调输入函数"]="GRANT EXECUTE ON FUNCTION $MPF TO starbuddy_learning;"
  ["月度计划：洞察服务可直读快照表"]="GRANT SELECT ON insight.monthly_plan TO starbuddy_insight; CREATE POLICY t_mp ON insight.monthly_plan FOR SELECT TO starbuddy_insight USING (true);"
  ["月度计划：洞察服务可调权益判断"]="GRANT EXECUTE ON FUNCTION $MPE TO starbuddy_insight;"
  ["月度计划：不登记个人层表清单"]="DELETE FROM platform.personal_table_registry WHERE nspname = 'insight' AND relname = 'monthly_plan';"
  ["攻击面：月度计划视图对 PUBLIC 开放"]="GRANT EXECUTE ON FUNCTION $MPG TO PUBLIC;"
  ["攻击面：月度计划写入函数不固定 pg_temp"]="ALTER FUNCTION $MPS SET search_path = pg_catalog, insight, platform;"
)

# PLT-15 PR1 今日计划快照（0055）与计划事件核对任务 ID（0056），specs/today-plan-snapshot-plt15：由 79zd 发现（授权类的同时由 80 的快照、
# 清单类由 79p、删除覆盖由 79q 发现；攻击面类只由 80 发现）。DBMUT_FILTER=今日计划 只跑这一组
TPE='platform.execute_due_erasures(timestamp with time zone)'
TPB='insight.record_behavior_event(uuid, text, jsonb, timestamp with time zone, uuid)'
M+=(
  ["今日计划快照：BFF 读不限当天"]="DROP POLICY today_plan_item_read ON platform.today_plan_item; CREATE POLICY today_plan_item_read ON platform.today_plan_item FOR SELECT TO starbuddy_bff USING (platform.can_read_student(student_id));"
  ["今日计划快照：BFF 读不限本人与绑定家长"]="DROP POLICY today_plan_item_read ON platform.today_plan_item; CREATE POLICY today_plan_item_read ON platform.today_plan_item FOR SELECT TO starbuddy_bff USING (plan_date = (now() AT TIME ZONE 'Asia/Shanghai')::date);"
  ["今日计划快照：家长读不到（只给学生本人）"]="DROP POLICY today_plan_item_read ON platform.today_plan_item; CREATE POLICY today_plan_item_read ON platform.today_plan_item FOR SELECT TO starbuddy_bff USING (platform.is_self_student(student_id) AND plan_date = (now() AT TIME ZONE 'Asia/Shanghai')::date);"
  ["今日计划快照：可补写别的日子"]="DROP TRIGGER today_plan_item_guard ON platform.today_plan_item;"
  ["今日计划快照：学习服务可改"]="GRANT UPDATE ON platform.today_plan_item TO starbuddy_learning;"
  ["今日计划快照：学习服务可删"]="GRANT DELETE ON platform.today_plan_item TO starbuddy_learning; CREATE POLICY t_tp_del ON platform.today_plan_item FOR DELETE TO starbuddy_learning USING (true);"
  ["今日计划快照：BFF 可写"]="GRANT INSERT ON platform.today_plan_item TO starbuddy_bff; CREATE POLICY t_tp_bff ON platform.today_plan_item FOR INSERT TO starbuddy_bff WITH CHECK (true);"
  ["今日计划快照：洞察服务可读"]="GRANT SELECT ON platform.today_plan_item TO starbuddy_insight; CREATE POLICY t_tp_ins ON platform.today_plan_item FOR SELECT TO starbuddy_insight USING (true);"
  ["今日计划快照：authz 可读节点列"]="GRANT SELECT (node_id) ON platform.today_plan_item TO starbuddy_authz;"
  ["今日计划快照：同一条目可重复"]="DROP INDEX platform.today_plan_item_entry_uq;"
  ["今日计划快照：序号可重复"]="ALTER TABLE platform.today_plan_item DROP CONSTRAINT today_plan_item_seq;"
  ["今日计划快照：来源与类别不核对"]="ALTER TABLE platform.today_plan_item DROP CONSTRAINT today_plan_item_kind_source;"
  ["今日计划快照：计划任务可带条目引用"]="ALTER TABLE platform.today_plan_item DROP CONSTRAINT today_plan_item_entry_ref;"
  ["今日计划快照：复测条目可不带节点"]="ALTER TABLE platform.today_plan_item DROP CONSTRAINT today_plan_item_entry_node;"
  ["今日计划快照：复测条目可带推荐理由"]="ALTER TABLE platform.today_plan_item DROP CONSTRAINT today_plan_item_rationale;"
  ["今日计划快照：推荐理由收任意编码与文本"]="ALTER TABLE platform.today_plan_item DROP CONSTRAINT today_plan_item_rationale_code;"
  ["今日计划快照：推荐理由收未登记的编码"]="ALTER TABLE platform.today_plan_item DROP CONSTRAINT today_plan_item_rationale_code; ALTER TABLE platform.today_plan_item ADD CONSTRAINT today_plan_item_rationale_code CHECK (rationale_code ~ '^[a-z0-9_]{1,48}\$');"
  ["今日计划快照：守卫不查已删除"]="$(afr 'platform.today_plan_item_guard()' "'IF NOT FOUND OR v_erased IS NOT NULL THEN'" "'IF false THEN'")"
  ["今日计划快照：守卫不查档案存在"]="$(afr 'platform.today_plan_item_guard()' "'IF NOT FOUND OR v_erased IS NOT NULL THEN'" "'IF v_erased IS NOT NULL THEN'")"
  ["今日计划快照：守卫不锁档案行"]="$(afr 'platform.today_plan_item_guard()' "' FOR KEY SHARE;'" "';'")"
  ["今日计划快照：BFF 可读节点与理由"]="GRANT SELECT (node_id, rationale_code) ON platform.today_plan_item TO starbuddy_bff;"
  ["今日计划快照：保留期清理不隔离"]="$(afr "$TPE" "'  EXCEPTION WHEN OTHERS THEN
    RAISE WARNING ''today_plan_item 保留期清理失败（%），本次跳过'', SQLSTATE;
'" "''")"
  ["攻击面：今日计划守卫不固定 pg_temp"]="ALTER FUNCTION platform.today_plan_item_guard() SET search_path = pg_catalog, platform;"
  ["今日计划快照：删档案不级联"]="ALTER TABLE platform.today_plan_item DROP CONSTRAINT today_plan_item_student_id_fkey; ALTER TABLE platform.today_plan_item ADD FOREIGN KEY (student_id) REFERENCES platform.student_profile (id);"
  ["今日计划快照：不登记个人层表清单"]="DELETE FROM platform.personal_table_registry WHERE nspname = 'platform' AND relname = 'today_plan_item';"
  ["今日计划快照：清单登记为豁免"]="UPDATE platform.personal_table_registry SET strategy = 'exempt', exempt_reason = 'x', delete_order = NULL, erase_scope = NULL WHERE nspname = 'platform' AND relname = 'today_plan_item';"
  ["今日计划快照：删除作业不清保留期"]="$(afr "$TPE" "'    DELETE FROM platform.today_plan_item
     WHERE plan_date <= (p_now AT TIME ZONE ''Asia/Shanghai'')::date - platform.today_plan_retention_days();'" "''")"
  ["今日计划快照：保留期边界差一天"]="$(afr "$TPE" "'WHERE plan_date <= (p_now'" "'WHERE plan_date < (p_now'")"
  ["今日计划快照：保留期清理不分日期"]="$(afr "$TPE" "'WHERE plan_date <= (p_now AT TIME ZONE ''Asia/Shanghai'')::date - platform.today_plan_retention_days();'" "'WHERE plan_date <= (p_now AT TIME ZONE ''Asia/Shanghai'')::date;'")"
  ["今日计划快照：保留期改成 30 天"]="CREATE OR REPLACE FUNCTION platform.today_plan_retention_days() RETURNS integer LANGUAGE sql IMMUTABLE SET search_path = pg_catalog AS \$f\$ SELECT 30 \$f\$;"
  ["今日计划快照：保留期配置授给学习服务"]="GRANT EXECUTE ON FUNCTION platform.today_plan_retention_days() TO starbuddy_learning;"
  ["计划事件：不核对任务 ID"]="$(afr "$TPB" "'IF p_kind IN (''plan_done'', ''plan_skip'') THEN'" "'IF false THEN'")"
  ["计划事件：不核对任务属于该生"]="$(afr "$TPB" "' AND i.student_id = p_student'" "''")"
  ["计划事件：不核对计划日"]="$(afr "$TPB" "'
                      AND i.plan_date BETWEEN v_day - 1 AND v_day'" "''")"
  ["计划事件：计划日放宽到 ±2 天"]="$(afr "$TPB" "'BETWEEN v_day - 1 AND v_day)'" "'BETWEEN v_day - 2 AND v_day + 2)'")"
  ["计划事件：计划日又收后一天"]="$(afr "$TPB" "'BETWEEN v_day - 1 AND v_day)'" "'BETWEEN v_day - 1 AND v_day + 1)'")"
  ["计划事件：计划日只认当天"]="$(afr "$TPB" "'BETWEEN v_day - 1 AND v_day)'" "'BETWEEN v_day AND v_day)'")"
  ["计划事件：事件日按 UTC 算"]="$(afr "$TPB" "'v_day := (p_occurred_at AT TIME ZONE ''Asia/Shanghai'')::date;'" "'v_day := (p_occurred_at AT TIME ZONE ''UTC'')::date;'")"
  ["计划事件：任务核对先于重放"]="$(afr "$TPB" "'  SELECT * INTO e FROM insight.behavior_event b WHERE b.student_id = p_student AND b.idempotency_key = p_key;'" "'  IF p_kind IN (''plan_done'', ''plan_skip'') AND NOT EXISTS (SELECT 1 FROM platform.today_plan_item i WHERE i.id = (p_payload ->> ''plan_task_id'')::uuid) THEN RAISE EXCEPTION ''PLAN_TASK_NOT_FOUND'' USING ERRCODE = ''invalid_parameter_value''; END IF;
  SELECT * INTO e FROM insight.behavior_event b WHERE b.student_id = p_student AND b.idempotency_key = p_key;'")"
  ["计划事件：别人的任务报不同的错（泄露存在性）"]="$(afr "$TPB" "'    IF NOT EXISTS (SELECT 1 FROM platform.today_plan_item i'" "'    IF EXISTS (SELECT 1 FROM platform.today_plan_item i WHERE i.id = (p_payload ->> ''plan_task_id'')::uuid AND i.student_id <> p_student) THEN RAISE EXCEPTION ''not allowed'' USING ERRCODE = ''insufficient_privilege''; END IF;
    IF NOT EXISTS (SELECT 1 FROM platform.today_plan_item i'")"
  ["攻击面：今日计划快照对 BFF 全开"]="DROP POLICY today_plan_item_read ON platform.today_plan_item; CREATE POLICY today_plan_item_read ON platform.today_plan_item FOR SELECT TO starbuddy_bff USING (true);"
)

# PLT-15 PR3 计划完成 / 跳过事件的来源改为学生自述（0057）：由 79ze 发现（写入函数类的同时由 79zd 的计划事件写入发现）。
# DBMUT_FILTER=计划来源 只跑这一组（约束整条删掉的情形是上面的“行为事件：来源可不是客观记录”，由 79g 发现）
M+=(
  ["计划来源：计划事件可标客观记录"]="ALTER TABLE insight.behavior_event DROP CONSTRAINT behavior_event_source_by_kind; ALTER TABLE insight.behavior_event ADD CONSTRAINT behavior_event_source_by_kind CHECK (CASE WHEN kind IN ('plan_done', 'plan_skip') THEN source_type IN ('self', 'objective') ELSE source_type = 'objective' END);"
  ["计划来源：会话事件可标学生自述"]="ALTER TABLE insight.behavior_event DROP CONSTRAINT behavior_event_source_by_kind; ALTER TABLE insight.behavior_event ADD CONSTRAINT behavior_event_source_by_kind CHECK (CASE WHEN kind IN ('plan_done', 'plan_skip') THEN source_type = 'self' ELSE source_type IN ('objective', 'self') END);"
  ["计划来源：写入函数仍写客观记录"]="$(afr "$TPB" "'THEN ''self''::insight.source_type ELSE'" "'THEN ''objective''::insight.source_type ELSE'")"
  ["计划来源：自己标记栏来源不限"]="$(afr "$WO" "'AND p -> ''self_marked'' -> ''source_type'' = ''\"self\"''::jsonb'" "''")"
  ["计划来源：自己标记栏可带别的键"]="$(afr "$WO" "'= ARRAY[''plan_done'', ''plan_skipped'', ''source_type'']'" "'IS NOT NULL'")"
  ["计划来源：新形状顶层可再放计划计数"]="$(afr "$WO" "'= ARRAY[''self_marked'', ''source_type'', ''subjects'', ''usage_minutes'']'" "'@> ARRAY[''self_marked'', ''source_type'', ''subjects'', ''usage_minutes'']'")"
  ["计划来源：自己标记计数不设上限"]="$(afr "$WO" "'(p -> ''self_marked'' ->> ''plan_done'') ~ ''^[0-9]{1,4}\$'''" "'(p -> ''self_marked'' ->> ''plan_done'') ~ ''^[0-9]+\$'''")"
  ["计划来源：新版本可写旧形状"]="ALTER TABLE insight.weekly_report DROP CONSTRAINT weekly_report_objective_plan_source;"
  ["计划来源：客观栏核对可返回 NULL"]="$(afr "$WO" "'''subjects'')), false)'" "'''subjects'')), NULL)'")"
)

# G 流第二部分 PR D：审核台读内容版本与发布记录（0042）、时政过期下架（0043）：由 79w 发现（授权类的同时由 80 的快照发现）。
# DBMUT_FILTER=审核台读 与 DBMUT_FILTER=时政下架 只跑这两组
RCL='platform.content_version_list(platform.subject)'
RCS='platform.content_version_snapshot(uuid)'
RRE='platform.release_record_export(platform.subject)'
CEX='kge.expire_current_affairs(date)'
M+=(
  ["审核台读：学习服务可读发布记录导出"]="GRANT EXECUTE ON FUNCTION $RRE TO starbuddy_learning;"
  ["审核台读：BFF 可读内容版本快照"]="GRANT EXECUTE ON FUNCTION $RCS TO starbuddy_bff;"
  ["审核台读：BFF 可列内容版本"]="GRANT EXECUTE ON FUNCTION $RCL TO starbuddy_bff;"
  ["审核台读：导出函数对 PUBLIC 开放"]="GRANT EXECUTE ON FUNCTION $RRE TO PUBLIC;"
  ["审核台读：签字身份可读发布记录导出"]="GRANT EXECUTE ON FUNCTION $RRE TO starbuddy_review;"
  ["审核台读：签字身份可列内容版本"]="GRANT EXECUTE ON FUNCTION $RCL TO starbuddy_review;"
  ["审核台读：签字身份可读内容版本快照"]="GRANT EXECUTE ON FUNCTION $RCS TO starbuddy_review;"
  ["审核台读：列表不按学科过滤"]="$(afr "$RCL" "' WHERE v.subject = p_subject'" "' WHERE true'")"
  ["审核台读：导出不按学科过滤"]="$(afr "$RRE" "' WHERE r.subject = p_subject'" "' WHERE true'")"
  ["审核台读：快照摘要不是签字摘要"]="$(afr "$RCS" "'''digest'', platform.content_version_digest(v.id)'" "'''digest'', md5(v.id::text)'")"
  ["审核台读：知识服务可直接读内容版本表"]="GRANT SELECT ON platform.curriculum_version TO starbuddy_kge; CREATE POLICY t_cv_kge ON platform.curriculum_version FOR SELECT TO starbuddy_kge USING (true);"
  ["时政下架：签字身份可调下架"]="GRANT EXECUTE ON FUNCTION $CEX TO starbuddy_review;"
  ["时政下架：学习服务可调下架"]="GRANT EXECUTE ON FUNCTION $CEX TO starbuddy_learning;"
  ["时政下架：可提前下架"]="$(afr "$CEX" "'IF p_today > (now() AT TIME ZONE ''Asia/Shanghai'')::date THEN'" "'IF false THEN'")"
  ["时政下架：当天到期也下架"]="$(afr "$CEX" "'AND x.valid_until < p_today'" "'AND x.valid_until <= p_today'")"
  ["时政下架：待审的也下架"]="$(afr "$CEX" "'WHERE x.review_status = ''published'' AND'" "'WHERE x.review_status <> ''retired'' AND'")"
  ["时政下架：不写审核记录"]="$(afr "$CEX" "'    INSERT INTO kge.content_review (object_type, object_id, risk_level, decision, decided_at, digest, basis_code, action)
    VALUES (''current_affair'', c.id::text, ''low'', ''approved'', clock_timestamp(), kge.row_digest(to_jsonb(c) - ''review_status''),
            ''expired_auto'', ''retire'');'" "''")"
  ["时政下架：依据不是 expired_auto"]="$(afr "$CEX" "'''expired_auto'', ''retire'');'" "'''content_error'', ''retire'');'")"
  ["时政下架：已下架可改回上线"]="DROP TRIGGER guard_affair_retired ON kge.current_affair;"
  ["时政下架：已下架可退回待审"]="$(afr 'kge.guard_affair_retired()' "'AND NEW.review_status IS DISTINCT FROM ''retired'' THEN'" "'AND NEW.review_status = ''published'' THEN'")"
  ["时政下架：下架守卫也放过已发布版本"]="$(afr 'kge.guard_affair_retired()' "'IF v_status IS DISTINCT FROM ''draft'' THEN'" "'IF v_status NOT IN (''draft'', ''published'') THEN'")"
  ["时政下架：知识服务可直接写下架记录"]="DROP POLICY content_review_kge_submit ON kge.content_review; CREATE POLICY content_review_kge_submit ON kge.content_review FOR INSERT TO starbuddy_kge WITH CHECK (decision = 'pending' OR basis_code = 'expired_auto');"
)

# SIE-02b 第二步 PR 1：洞察服务的授权再核对（0044 insight.verify_actor，specs/sie-02b-claims-first-report“第二步补充”第 1 条）：
# 由 79x 发现（授权类的同时由 80 的快照发现）。DBMUT_FILTER=授权再核对 只跑这一组
VA='insight.verify_actor(uuid, text, uuid)'
M+=(
  ["授权再核对：认已撤销的绑定"]="$(afr "$VA" "'l.status = ''active'' AND l.revoked_at IS NULL AND '" "''")"
  ["授权再核对：家长不核对学生"]="$(afr "$VA" "'WHERE l.student_id = p_student AND '" "'WHERE '")"
  ["授权再核对：学生不核对档案"]="$(afr "$VA" "'WHERE s.id = p_student AND '" "'WHERE '")"
  ["授权再核对：停用的学生账号仍通过"]="$(afr "$VA" "'a.kind = ''student'' AND a.status = ''active'''" "'a.kind = ''student'''")"
  ["授权再核对：停用的家长账号仍通过"]="$(afr "$VA" "'a.kind = ''guardian'' AND a.status = ''active'''" "'a.kind = ''guardian'''")"
  ["授权再核对：家长分支不查账号类型"]="$(afr "$VA" "'a.kind = ''guardian'' AND '" "''")"
  ["授权再核对：学生分支不查账号类型"]="$(afr "$VA" "'a.kind = ''student'' AND '" "''")"
  ["授权再核对：不查档案已删除"]="$(afr "$VA" "'s.erased_at IS NULL'" "'true'")"
  ["授权再核对：家长不查档案已删除"]="$(afr "$VA" "'g.erased_at IS NULL'" "'true'")"
  ["授权再核对：家长分支要求学生账号有效（与 BFF 不一致）"]="$(afr "$VA" "'g.erased_at IS NULL'" "'g.erased_at IS NULL AND EXISTS (SELECT 1 FROM platform.account sa WHERE sa.id = g.account_id AND sa.status = ''active'')'")"
  ["授权再核对：不分受众"]="$(afr "$VA" "'WHEN p_audience = ''guardian'' THEN'" "'WHEN true THEN'")"
  ["授权再核对：未知受众返回 NULL"]="$(afr "$VA" "'ELSE false END'" "'END'")"
  ["授权再核对：空参数返回 NULL"]="$(afr "$VA" "'WHEN p_actor IS NULL OR p_student IS NULL THEN false'" "'WHEN p_actor IS NULL OR p_student IS NULL THEN NULL'")"
  ["授权再核对：改为调用者权限"]="ALTER FUNCTION $VA SECURITY INVOKER;"
  ["授权再核对：BFF 可调用"]="GRANT EXECUTE ON FUNCTION $VA TO starbuddy_bff;"
  ["授权再核对：学习服务可调用"]="GRANT EXECUTE ON FUNCTION $VA TO starbuddy_learning;"
  ["授权再核对：调度器可调用"]="GRANT EXECUTE ON FUNCTION $VA TO starbuddy_scheduler;"
  ["授权再核对：对 PUBLIC 开放"]="GRANT EXECUTE ON FUNCTION $VA TO PUBLIC;"
)

# 攻击面矩阵补测（specs/household-pentest-sprint3 待人确认 #13 里不需要人决定的几项）：按行的可见性、写矩阵、
# 复核角色与定时任务角色的函数矩阵。都由 80 本身发现（预期断言登记在下面的 EXPECT）。DBMUT_FILTER=攻击面 一起跑
M+=(
  ["攻击面：周报不分受众"]="DROP POLICY weekly_report_front_read ON insight.weekly_report; CREATE POLICY weekly_report_front_read ON insight.weekly_report FOR SELECT TO starbuddy_bff USING (status = 'generated' AND platform.can_read_student(student_id));"
  ["攻击面：家长看得到同一学生其他家长的绑定"]="DROP POLICY link_read ON platform.guardian_student_link; CREATE POLICY link_read ON platform.guardian_student_link FOR SELECT TO starbuddy_bff USING ((guardian_account_id = platform.current_account_id() AND platform.is_active_guardian()) OR platform.is_self_student(student_id) OR platform.is_linked_guardian(student_id));"
  ["攻击面：家长自述删除不看作者"]="DROP POLICY greport_delete ON platform.guardian_report; CREATE POLICY greport_delete ON platform.guardian_report FOR DELETE TO starbuddy_bff USING (true);"
  ["攻击面：解除绑定不限本人"]="DROP POLICY link_revoke ON platform.guardian_student_link; CREATE POLICY link_revoke ON platform.guardian_student_link FOR UPDATE TO starbuddy_bff USING (status = 'active') WITH CHECK (status = 'revoked');"
  ["攻击面：复核队列认任何有效账号"]="$(afr 'platform.review_current_staff()' "'a.kind = ''staff'' AND '" "''")"
  ["攻击面：签字发布认任何有效账号"]="$(afr 'kge.current_staff()' "'a.kind = ''staff'' AND '" "''")"
  ["攻击面：删除执行接受未来时钟"]="$(afr 'platform.execute_due_erasures(timestamp with time zone)' "'p_now > now() OR '" "''")"
  ["攻击面：抽检接受未来起点"]="$(afr 'platform.review_sample_spot_checks(platform.subject, timestamp with time zone, numeric)' "' OR p_since > now()'" "''")"
)

# 并行执行（每个变异各起一个一次性 Postgres，互不影响）；DBMUT_JOBS 控制并发数，默认等于 CPU 核数
JOBS="${DBMUT_JOBS:-$(nproc 2>/dev/null || echo 2)}"
RES="$(mktemp -d "${TMPDIR:-/tmp}/starbuddy-dbmut.XXXXXX")"
trap 'rm -rf "$RES"' EXIT
# 攻击面变异：只跑 80（在渗透测试夹具上），而且第一条失败的必须是这里登记的断言（安全审查阻断 2）。
# 这样才能证明是 80 本身发现了变异，而不是 20–70 的逐表断言或快照先拦下。
# 除 KEEP_SNAPSHOT 里的以外，快照都按变异后的状态重新生成，不让快照先拦截。
declare -A EXPECT=(
  ["攻击面：考情库加学生键"]="攻击面：公共知识层与考情库（kge、policy）没有学生"
  ["攻击面：公共层加无外键的 uuid 列"]="攻击面：公共知识层与考情库的 uuid 列只能是"
  ["攻击面：公共层文本里写学生 ID"]="攻击面：公共知识层与考情库的文本与 jsonb 列里没有"
  ["攻击面：公共知识层加学生键"]="攻击面：公共知识层与考情库（kge、policy）没有学生"
  ["攻击面：公共知识层外键指向学生档案"]="攻击面：公共知识层与考情库的外键只指向"
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
  ["攻击面：新函数删除个人层表"]="攻击面：除删除执行函数外，没有函数删除个人层清单里的表"
  ["攻击面：删除执行身份有成员"]="攻击面：starbuddy_eraser 没有任何成员"
  ["攻击面：周报不分受众"]="攻击面：可见性矩阵（按行）——"
  ["攻击面：家长看得到同一学生其他家长的绑定"]="攻击面：可见性矩阵（按行）——"
  ["攻击面：家长自述删除不看作者"]="攻击面：写矩阵——"
  ["攻击面：解除绑定不限本人"]="攻击面：写矩阵——"
  ["攻击面：复核队列认任何有效账号"]="攻击面：复核角色函数矩阵——"
  ["攻击面：签字发布认任何有效账号"]="攻击面：复核角色函数矩阵——"
  ["攻击面：删除执行接受未来时钟"]="攻击面：定时任务函数矩阵——"
  ["攻击面：抽检接受未来起点"]="攻击面：定时任务函数矩阵——"
  ["攻击面：届别更正记录对 BFF 可读"]="攻击面：届别更正记录、staff 权限表、来源清单操作记录只有函数属主能写"
  ["攻击面：来源清单函数授给知识服务"]="攻击面：届别更正只授 BFF 与签字身份"
  ["攻击面：届别更正授给学习服务"]="攻击面：届别更正只授 BFF 与签字身份"
  ["攻击面：控制线归档表对 BFF 可读"]="攻击面：控制线旧值归档表只读"
  ["攻击面：客服更正不查 cohort_correction"]="攻击面：客服届别更正——"
  ["攻击面：来源清单下线不设锁等待上限"]="攻击面：来源清单的新增与下线有 5 秒锁等待上限"
  ["攻击面：清单取锁函数授给学习服务"]="攻击面：来源清单的新增与下线有 5 秒锁等待上限"
  ["攻击面：月度计划视图对 PUBLIC 开放"]="攻击面：每个 SECURITY DEFINER 函数"
  ["攻击面：月度计划写入函数不固定 pg_temp"]="攻击面：每个 SECURITY DEFINER 函数"
  ["攻击面：今日计划快照对 BFF 全开"]="攻击面：可见性矩阵——"
  ["攻击面：今日计划守卫不固定 pg_temp"]="攻击面：每个 SECURITY DEFINER 函数"
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
