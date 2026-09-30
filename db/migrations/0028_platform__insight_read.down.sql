-- 0028 回滚：撤掉洞察服务对平台推断与事实的只读授权与策略
DROP POLICY evidence_insight_read ON platform.evidence_event;
DROP POLICY attempt_insight_read ON platform.attempt;
DROP POLICY grade_insight_read ON platform.grade_result;
DROP POLICY attribution_insight_read ON platform.error_attribution;
DROP POLICY observation_insight_read ON platform.skill_observation;
DROP POLICY mastery_insight_read ON platform.mastery_estimate;
REVOKE SELECT ON platform.evidence_event, platform.attempt, platform.grade_result, platform.error_attribution,
                 platform.skill_observation, platform.mastery_estimate FROM starbuddy_insight;
