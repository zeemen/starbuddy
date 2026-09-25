# 未实现的目标明确打印“跳过”，不得伪装为通过。
.PHONY: check-fast check contract-test contract-mutation db-test db-mutation eval-persona eval-grade

# 可指向装了 packages/contracts/requirements-dev.txt 的解释器，例如 make check PYTHON=.venv/bin/python
PYTHON ?= python3
export PYTHONDONTWRITEBYTECODE = 1
# CI 里设 CONTRACT_STRICT=1：缺依赖（Python 依赖或本机 Postgres）直接失败，不允许“跳过”变绿
CONTRACT_STRICT ?= 0

# 退出码 3 = 缺依赖：非严格模式下打印“跳过”但不算失败
define run_contract
	@$(PYTHON) $(1); rc=$$?; \
	  if [ $$rc -eq 3 ] && [ "$(CONTRACT_STRICT)" != "1" ]; then exit 0; else exit $$rc; fi
endef

# 契约检查约 1 秒，改动后也跑一遍
check-fast: contract-test

check: contract-test contract-mutation db-test db-mutation
	@echo "check: 类型检查、lint、单测尚无代码，跳过"

# 契约合法性 + 示例 + 反例集
contract-test:
	$(call run_contract,packages/contracts/scripts/check_contract.py)

# 变异测试：证明契约检查没有盲区（约 15 秒）
contract-mutation:
	$(call run_contract,packages/contracts/tests/test_mutations.py)

# 迁移与 RLS：在一次性的本地 Postgres 上跑 up → down → up 与 RLS / 约束测试（约 3 秒）
# 只连本机临时实例，不连接任何共享或生产库（CLAUDE.md 规则 9）
db-test:
	@bash db/tests/run.sh; rc=$$?; \
	  if [ $$rc -eq 3 ] && [ "$(CONTRACT_STRICT)" != "1" ]; then exit 0; else exit $$rc; fi

# 数据库变异测试：逐条放宽 RLS 或约束，断言测试能发现（约 40 秒）
db-mutation:
	@bash db/tests/mutations.sh; rc=$$?; \
	  if [ $$rc -eq 3 ] && [ "$(CONTRACT_STRICT)" != "1" ]; then exit 0; else exit $$rc; fi

eval-persona:
	@echo "eval-persona: evals/persona 尚未建立，跳过"

eval-grade:
	@echo "eval-grade: SUBJECT=$(SUBJECT)，evals 尚未建立，跳过"
