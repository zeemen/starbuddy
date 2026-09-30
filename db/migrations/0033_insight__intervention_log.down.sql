-- 0033 回滚：删除触发规则动作记录与签字登记
DROP TABLE insight.intervention_log;
DROP TABLE insight.intervention_rule_signoff;
DROP FUNCTION insight.intervention_guard();
DROP FUNCTION insight.intervention_claims_ok(uuid, uuid[]);
