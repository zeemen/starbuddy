# 阶段 0 占位：代码落地后逐个替换为真实检查。未实现的目标明确打印“跳过”，不得伪装为通过。
.PHONY: check-fast check contract-test eval-persona eval-grade

check-fast:
	@echo "check-fast: 暂无代码，跳过"

check: check-fast contract-test
	@echo "check: 暂无代码，跳过"

contract-test:
	@echo "contract-test: packages/contracts 尚未建立，跳过"

eval-persona:
	@echo "eval-persona: evals/persona 尚未建立，跳过"

eval-grade:
	@echo "eval-grade: SUBJECT=$(SUBJECT)，evals 尚未建立，跳过"
