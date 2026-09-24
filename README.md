# StarBuddy Claude Code 启动包

放入平台单仓根目录。包含：
- CLAUDE.md：仓库总则（< 200 行，常驻上下文）
- .claude/settings.json + .claude/hooks/：确定性守卫（阻止生产操作、密钥读写、改教研金标准、推主干）与改动后快速检查
- .claude/agents/：inventory-auditor、adapter-implementer、contract-guardian、security-reviewer、persona-evaluator
- .claude/skills/：/subject-inventory、/implement-adapter、/gate-check
- specs/_template/spec.md：任务规格模板

使用前请修改 guard.py 中的 PROD_*_PLACEHOLDER 为真实的生产环境标识，并在仓库中提供 Makefile 的 check-fast / check / contract-test / eval-persona / eval-grade 目标。
