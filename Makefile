# 未实现的目标明确打印“跳过”，不得伪装为通过。
.PHONY: check-fast check contract-test eval-persona eval-grade

# 可指向装了 packages/contracts/requirements-dev.txt 的解释器，例如 make check PYTHON=.venv/bin/python
PYTHON ?= python3

# 契约检查不到 1 秒，改动后也跑一遍
check-fast: contract-test

check: contract-test
	@echo "check: 类型检查、lint、单测尚无代码，跳过"

# 退出码 3 = 缺依赖：打印“跳过”但不算失败；其他非零退出码 = 契约检查失败
contract-test:
	@$(PYTHON) packages/contracts/scripts/check_contract.py; rc=$$?; \
	  if [ $$rc -eq 3 ]; then exit 0; else exit $$rc; fi

eval-persona:
	@echo "eval-persona: evals/persona 尚未建立，跳过"

eval-grade:
	@echo "eval-grade: SUBJECT=$(SUBJECT)，evals 尚未建立，跳过"
