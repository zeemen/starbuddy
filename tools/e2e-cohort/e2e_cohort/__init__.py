"""冲刺 5-C+D 端到端联调（specs/e2e-integration-sprint5/spec.md）：合成队列、跨科回放、断言、Gate 0 证据包。

全部是合成数据（CLAUDE.md 规则 8）：学生、家庭、作答、作文都由固定种子生成，不来自任何真实学生。
"""

GENERATOR_VERSION = "e2e-cohort@0.1.0"
SYNTHETIC_NOTICE = "合成联调，不是内测结论"
