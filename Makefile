# 未实现的目标明确打印“跳过”，不得伪装为通过。
.PHONY: check-fast check contract-test contract-mutation taxonomy-check db-test db-mutation adapter-aya-test eval-persona eval-grade

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
check-fast: contract-test taxonomy-check

check: contract-test contract-mutation taxonomy-check db-test db-mutation adapter-aya-test
	@echo "check: lint 尚未配置，跳过；其余检查已通过"

# 契约合法性 + 示例 + 反例集
contract-test:
	$(call run_contract,packages/contracts/scripts/check_contract.py)

# 变异测试：证明契约检查没有盲区（约 15 秒）
contract-mutation:
	$(call run_contract,packages/contracts/tests/test_mutations.py)

# 错因跨科映射表：七大类与契约、数据库一致，编码合法，文档表格与 YAML 同步
taxonomy-check:
	$(call run_contract,docs/mapping/check_error_taxonomy.py)

# 迁移与 RLS：在一次性的本地 Postgres 上跑 up → down → up 与 RLS / 约束测试（约 3 秒）
# 只连本机临时实例，不连接任何共享或生产库（CLAUDE.md 规则 9）
db-test:
	@bash db/tests/run.sh; rc=$$?; \
	  if [ $$rc -eq 3 ] && [ "$(CONTRACT_STRICT)" != "1" ]; then exit 0; else exit $$rc; fi

# 数据库变异测试：逐条放宽 RLS 或约束，断言测试能发现（约 40 秒）
db-mutation:
	@bash db/tests/mutations.sh; rc=$$?; \
	  if [ $$rc -eq 3 ] && [ "$(CONTRACT_STRICT)" != "1" ]; then exit 0; else exit $$rc; fi

# 阿鸦适配器：类型检查 + 单测（契约、验证器、映射、泄露、幂等、HTTP）+ 端到端（本地临时 Postgres，约 5 秒）
# 依赖需先在 adapters/aya 下 npm ci；没装时非严格模式打印“跳过”
adapter-aya-test:
	@if [ ! -d adapters/aya/node_modules/ajv ]; then \
	  echo "adapter-aya-test: 跳过（没有安装依赖；cd adapters/aya && npm ci）"; \
	  if [ "$(CONTRACT_STRICT)" = "1" ]; then exit 1; else exit 0; fi; fi
	@cd adapters/aya && npx --no-install tsc -p tsconfig.json && echo "adapter-aya-test: 类型检查通过"
	@cd adapters/aya && node --no-warnings --test --test-reporter=dot tests/*.test.ts
	@bash adapters/aya/tests/e2e/run.sh; rc=$$?; \
	  if [ $$rc -eq 3 ] && [ "$(CONTRACT_STRICT)" != "1" ]; then exit 0; else exit $$rc; fi

eval-persona:
	@echo "eval-persona: evals/persona 尚未建立，跳过"

eval-grade:
	@echo "eval-grade: SUBJECT=$(SUBJECT)，evals 尚未建立，跳过"
