# 未实现的目标明确打印“跳过”，不得伪装为通过。
.PHONY: check-fast check contract-test contract-mutation taxonomy-check db-test db-mutation adapter-aya-test learning-test bff-test bff-mutation conformance conformance-mutation eval-persona eval-grade

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

check: contract-test contract-mutation taxonomy-check db-test db-mutation adapter-aya-test learning-test bff-test bff-mutation conformance conformance-mutation
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

# 数据库变异测试：逐条放宽 RLS 或约束，断言测试能发现（并行执行，约 1–2 分钟）
db-mutation:
	@bash db/tests/mutations.sh; rc=$$?; \
	  if [ $$rc -eq 3 ] && [ "$(CONTRACT_STRICT)" != "1" ]; then exit 0; else exit $$rc; fi

# 阿鸦适配器：类型检查 + 单测（契约、验证器、映射、泄露、幂等、HTTP）。端到端在 learning-test 里
# 依赖需先在 adapters/aya 下 npm ci；没装时非严格模式打印“跳过”
adapter-aya-test:
	@if [ ! -d adapters/aya/node_modules/ajv ]; then \
	  echo "adapter-aya-test: 跳过（没有安装依赖；cd adapters/aya && npm ci）"; \
	  if [ "$(CONTRACT_STRICT)" = "1" ]; then exit 1; else exit 0; fi; fi
	@cd adapters/aya && npx --no-install tsc -p tsconfig.json && echo "adapter-aya-test: 类型检查通过"
	@cd adapters/aya && node --no-warnings --test --test-reporter=dot tests/*.test.ts

# 学习服务：写入接口的单测、幂等与核对测试、端到端（学习服务 → 阿鸦适配器 → 本地临时 Postgres，约 10 秒）
# 依赖需先安装：cd services/learning && python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
learning-test:
	@if [ ! -x services/learning/.venv/bin/pytest ]; then \
	  echo "learning-test: 跳过（没有安装依赖；见 services/learning/README.md）"; \
	  if [ "$(CONTRACT_STRICT)" = "1" ]; then exit 1; else exit 0; fi; fi
	@cd services/learning && LEARNING_TEST_STRICT=$(if $(filter 1,$(CONTRACT_STRICT)),1,0) .venv/bin/python -m pytest -q

# BFF 计量模块（冲刺 2-E）：类型检查 + 单测（共享规范化向量、指纹、学习服务响应分类）+ Gate 0 六类场景集成测试
# 集成测试在 db/tests/pg-ephemeral.sh 起的一次性 Postgres 上跑（多连接并发、断线），约 5 秒
# 依赖需先在 services/bff 下 npm ci；没装时非严格模式打印“跳过”
bff-test:
	@if [ ! -d services/bff/node_modules/pg ]; then \
	  echo "bff-test: 跳过（没有安装依赖；cd services/bff && npm ci）"; \
	  if [ "$(CONTRACT_STRICT)" = "1" ]; then exit 1; else exit 0; fi; fi
	@cd services/bff && npx --no-install tsc -p tsconfig.json && echo "bff-test: 类型检查通过"
	@cd services/bff && bash ../../db/tests/pg-ephemeral.sh -- node --no-warnings --test --test-concurrency=1 --test-reporter=dot tests/*.test.ts; rc=$$?; \
	  if [ $$rc -eq 0 ]; then echo "bff-test: 通过（单测 + Gate 0 六类场景集成测试）"; fi; \
	  if [ $$rc -eq 3 ] && [ "$(CONTRACT_STRICT)" != "1" ]; then exit 0; else exit $$rc; fi

# BFF 集成测试的变异测试：去掉计量函数里的加锁或并发判断，断言 Gate 0 集成测试能发现（约 10 秒）
bff-mutation:
	@if [ ! -d services/bff/node_modules/pg ]; then \
	  echo "bff-mutation: 跳过（没有安装依赖；cd services/bff && npm ci）"; \
	  if [ "$(CONTRACT_STRICT)" = "1" ]; then exit 1; else exit 0; fi; fi
	@bash services/bff/tests/mutations.sh; rc=$$?; \
	  if [ $$rc -eq 3 ] && [ "$(CONTRACT_STRICT)" != "1" ]; then exit 0; else exit $$rc; fi

# 契约一致性测试（冲刺 1-A）：共享核对库单测；同一套测试跑参考假适配器（同步、异步）与阿鸦适配器（约 10 秒）
# 覆盖率报告写到 $(CONFORMANCE_REPORT_DIR)/conformance-<学科>.json，供 Gate 0 统计
# 需要 packages/contracts/requirements-dev.txt；阿鸦部分还需要 adapters/aya 已 npm ci
CONFORMANCE_REPORT_DIR ?= $(or $(TMPDIR),/tmp)
conformance:
	$(call run_contract,-m pytest -q -p no:cacheprovider packages/contracts/python/tests)
	$(call run_contract,packages/contracts/conformance/cli.py --reference --quiet --report $(CONFORMANCE_REPORT_DIR)/conformance-reference.json)
	$(call run_contract,packages/contracts/conformance/cli.py --reference --reference-async --quiet)
	@if [ ! -d adapters/aya/node_modules/ajv ]; then \
	  echo "conformance: 阿鸦部分跳过（没有安装依赖；cd adapters/aya && npm ci）"; \
	  if [ "$(CONTRACT_STRICT)" = "1" ]; then exit 1; else exit 0; fi; fi
	$(call run_contract,packages/contracts/conformance/cli.py --quiet --adapter-cmd "node --no-warnings src/main.ts" --adapter-cwd adapters/aya --token-var AYA_ADAPTER_SERVICE_TOKEN --fixtures adapters/aya/conformance/fixtures.yaml --report $(CONFORMANCE_REPORT_DIR)/conformance-aya.json)

# 一致性测试的变异测试：参考假适配器逐一注入 28 类违约，断言每次都由指定的测试发现（约 45 秒）
conformance-mutation:
	$(call run_contract,packages/contracts/conformance/mutation.py)

eval-persona:
	@echo "eval-persona: evals/persona 尚未建立，跳过"

eval-grade:
	@echo "eval-grade: SUBJECT=$(SUBJECT)，evals 尚未建立，跳过"
