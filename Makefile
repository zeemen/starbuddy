# 未实现的目标明确打印“跳过”，不得伪装为通过。
.PHONY: check-fast check contract-test contract-mutation taxonomy-check db-test db-mutation adapter-aya-test adapter-aya-mutation adapter-zhixing-test adapter-wenli-test learning-test page-reconcile-test model-router-mutation diagnosis-mutation bff-test bff-mutation conformance conformance-mutation pentest-env-test attack-surface-check web-test web-visual kge-inventory kge-inventory-test kge-import kge-import-test kge-items-test kge-coverage-test kge-tagging-eval-test kge-daofa-import-test zhixing-rubric-check daofa-grading-eval-test sie-dictionary-check kge-id-check knowledge-test knowledge-mutation kge-compile-eval-test eval-persona eval-grade persona-registry-check persona-guard-test persona-lock persona-record insight-test insight-mutation

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
check-fast: contract-test taxonomy-check attack-surface-check sie-dictionary-check kge-id-check

check: contract-test contract-mutation taxonomy-check attack-surface-check sie-dictionary-check kge-id-check db-test db-mutation adapter-aya-test adapter-aya-mutation adapter-zhixing-test adapter-wenli-test learning-test page-reconcile-test model-router-mutation diagnosis-mutation mastery-mutation bff-test bff-mutation conformance conformance-mutation pentest-env-test web-test kge-inventory-test kge-import-test kge-items-test kge-coverage-test kge-tagging-eval-test kge-daofa-import-test zhixing-rubric-check daofa-grading-eval-test knowledge-test knowledge-mutation kge-compile-eval-test persona-registry-check persona-guard-test eval-persona insight-test insight-mutation voice-blind-eval-test
	@echo "check: lint 尚未配置，跳过；其余检查已通过"

# 契约合法性 + 示例 + 反例集
contract-test:
	$(call run_contract,packages/contracts/scripts/check_contract.py)

# 变异测试：证明契约检查没有盲区（约 1 分钟，含两份服务契约）
contract-mutation:
	$(call run_contract,packages/contracts/tests/test_mutations.py)

# 错因跨科映射表：七大类与契约、数据库一致，编码合法，文档表格与 YAML 同步
taxonomy-check:
	$(call run_contract,docs/mapping/check_error_taxonomy.py)

# SIE 画像字段字典与禁用推断词表（specs/sie-00-field-dictionary/spec.md）：8 项检查、17 个反例、词表正反例、人读版一致，约 1 秒
sie-dictionary-check:
	$(call run_contract,docs/design/sie/check_field_dictionary.py)

# 节点 ID 规范：文档正则、例子、学科前缀与迁移 0011 一致（只用标准库）
kge-id-check:
	$(PYTHON) docs/design/kge/check_node_id.py

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

# 阿鸦适配器冲刺 4-D 四个方法（plan、teach、generate_variation、report）的变异测试：逐条改坏关键规则，断言单测能发现（约 3–4 分钟）
adapter-aya-mutation:
	@if [ ! -d adapters/aya/node_modules/ajv ]; then \
	  echo "adapter-aya-mutation: 跳过（没有安装依赖；cd adapters/aya && npm ci）"; \
	  if [ "$(CONTRACT_STRICT)" = "1" ]; then exit 1; else exit 0; fi; fi
	@cd adapters/aya && node --no-warnings scripts/mutations.ts; rc=$$?; \
	  if [ $$rc -eq 3 ] && [ "$(CONTRACT_STRICT)" != "1" ]; then exit 0; else exit $$rc; fi

# 知行（道法）适配器（ADP-道法-01，specs/adapter-zhixing-sprint2）：类型检查 + 单测（判分后处理、路由客户端、幂等、禁语与供应商信息扫描）
# 依赖需先在 adapters/zhixing 下 npm ci；没装时非严格模式打印“跳过”
adapter-zhixing-test:
	@if [ ! -d adapters/zhixing/node_modules/ajv ]; then \
	  echo "adapter-zhixing-test: 跳过（没有安装依赖；cd adapters/zhixing && npm ci）"; \
	  if [ "$(CONTRACT_STRICT)" = "1" ]; then exit 1; else exit 0; fi; fi
	@cd adapters/zhixing && npx --no-install tsc -p tsconfig.json && echo "adapter-zhixing-test: 类型检查通过"
	@cd adapters/zhixing && node --no-warnings --test --test-reporter=dot tests/*.test.ts && echo "adapter-zhixing-test: 单测通过"

# 文砺（语文）适配器（ADP-语文-01，specs/adapter-wenli-sprint2）：类型检查 + 单测（作文后处理、客观题确定性判分、通道加权、plan、路由客户端、幂等、禁语与供应商信息扫描）
# 依赖需先在 adapters/wenli 下 npm ci；没装时非严格模式打印“跳过”
adapter-wenli-test:
	@if [ ! -d adapters/wenli/node_modules/ajv ]; then \
	  echo "adapter-wenli-test: 跳过（没有安装依赖；cd adapters/wenli && npm ci）"; \
	  if [ "$(CONTRACT_STRICT)" = "1" ]; then exit 1; else exit 0; fi; fi
	@cd adapters/wenli && npx --no-install tsc -p tsconfig.json && echo "adapter-wenli-test: 类型检查通过"
	@cd adapters/wenli && node --no-warnings --test --test-reporter=dot tests/*.test.ts && echo "adapter-wenli-test: 单测通过"

# 学习服务：写入接口的单测、幂等与核对测试、端到端（学习服务 → 阿鸦适配器 → 本地临时 Postgres，约 10 秒）
# 依赖需先安装：cd services/learning && python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
learning-test:
	@if [ ! -x services/learning/.venv/bin/pytest ]; then \
	  echo "learning-test: 跳过（没有安装依赖；见 services/learning/README.md）"; \
	  if [ "$(CONTRACT_STRICT)" = "1" ]; then exit 1; else exit 0; fi; fi
	@cd services/learning && LEARNING_TEST_STRICT=$(if $(filter 1,$(CONTRACT_STRICT)),1,0) .venv/bin/python -m pytest -q

# 编译页面对账（specs/release-gate-sprint4 冲刺 3 段，第 6 节）：托管平台合并记录导出 × kge.content_review 页面审核记录 × staff 对应表。
# 只用合成文件（services/learning/tests/fixtures/pages-reconcile），不连托管平台、不连库：一致通过；删一条审核记录、改一个提交号、
# 高风险只剩一名批准人各报失败；命令行退出码与“受影响页面清单”（held_pages，合并窗口据此跳过）。约 3 秒；也随 learning-test 运行
page-reconcile-test:
	@if [ ! -x services/learning/.venv/bin/pytest ]; then \
	  echo "page-reconcile-test: 跳过（没有安装依赖；见 services/learning/README.md）"; \
	  if [ "$(CONTRACT_STRICT)" = "1" ]; then exit 1; else exit 0; fi; fi
	@cd services/learning && .venv/bin/python -m pytest -q -p no:cacheprovider tests/test_review_pages.py && echo "page-reconcile-test: 通过"

# 模型路由（冲刺 1-F，specs/model-router-sprint1）：单测与性质测试在 learning-test 里；这里改坏出境过滤、启动校验、熔断等关键检查，断言测试能发现（约 15 秒）
model-router-mutation:
	@if [ ! -x services/learning/.venv/bin/pytest ]; then \
	  echo "model-router-mutation: 跳过（没有安装依赖；见 services/learning/README.md）"; \
	  if [ "$(CONTRACT_STRICT)" = "1" ]; then exit 1; else exit 0; fi; fi
	@cd services/learning && .venv/bin/python scripts/model_router_mutations.py

# 数学自适应诊断（SIE-02a，specs/sie-02a-math-diagnosis）：单测、合成学生回放与性质测试在 learning-test 里；
# 这里把选题规则、状态机、幂等与可见范围的关键检查逐条改坏，断言诊断测试能发现（并行 4 路，约 1 分钟；需要本机 Postgres 与阿鸦依赖）
diagnosis-mutation:
	@if [ ! -x services/learning/.venv/bin/pytest ] || [ ! -d adapters/aya/node_modules/ajv ]; then \
	  echo "diagnosis-mutation: 跳过（没有安装学习服务或阿鸦适配器依赖）"; \
	  if [ "$(CONTRACT_STRICT)" = "1" ]; then exit 1; else exit 0; fi; fi
	@services/learning/.venv/bin/python services/learning/scripts/diagnosis_mutations.py; rc=$$?; \
	  if [ $$rc -eq 3 ] && [ "$(CONTRACT_STRICT)" != "1" ]; then exit 0; else exit $$rc; fi

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
	@# v1.0 候选版：参考适配器按 1.0.0 再跑一遍（迁移期两版并存，specs/plugin-api-v1.0/spec.md）；阿鸦两版都跑（见下）
	@STARBUDDY_CONTRACT_VERSION=1.0.0 STARBUDDY_ALLOW_CANDIDATE_CONTRACT=1 $(PYTHON) packages/contracts/conformance/cli.py --reference --quiet; rc=$$?; \
	  if [ $$rc -eq 3 ] && [ "$(CONTRACT_STRICT)" != "1" ]; then exit 0; else exit $$rc; fi
	@STARBUDDY_CONTRACT_VERSION=1.0.0 STARBUDDY_ALLOW_CANDIDATE_CONTRACT=1 $(PYTHON) packages/contracts/conformance/cli.py --reference --reference-async --quiet; rc=$$?; \
	  if [ $$rc -eq 3 ] && [ "$(CONTRACT_STRICT)" != "1" ]; then exit 0; else exit $$rc; fi
	@if [ ! -d adapters/aya/node_modules/ajv ]; then \
	  echo "conformance: 阿鸦部分跳过（没有安装依赖；cd adapters/aya && npm ci）"; \
	  if [ "$(CONTRACT_STRICT)" = "1" ]; then exit 1; else exit 0; fi; fi
	@# 阿鸦按 0.9.0：plan、teach、generate_variation、report 只按 v1.0 实现（specs/adapter-aya-methods-sprint4），0.9.0 下声明未实现，
	@# 所以这一遍用去掉这四个方法用例的夹具副本 fixtures.v0.9.yaml（与 fixtures.yaml 的漂移由 adapters/aya/tests/methods-v1.test.ts 断言）
	$(call run_contract,packages/contracts/conformance/cli.py --quiet --adapter-cmd "node --no-warnings src/main.ts" --adapter-cwd adapters/aya --token-var AYA_ADAPTER_SERVICE_TOKEN --fixtures adapters/aya/conformance/fixtures.v0.9.yaml)
	@# 阿鸦按 1.0.0（specs/plugin-api-v1.0-step2/spec.md 第 3 条）：请求按 context.contract_version 应答；能力声明默认报 0.9.0，
	@# 这里用启动参数让它报 1.0.0（cli.py 不向适配器进程透传环境变量）。冲刺 4-D 起覆盖率报告取这一遍（阿鸦 6/6，spec 验收标准）
	@STARBUDDY_CONTRACT_VERSION=1.0.0 STARBUDDY_ALLOW_CANDIDATE_CONTRACT=1 $(PYTHON) packages/contracts/conformance/cli.py --quiet --adapter-cmd "node --no-warnings src/main.ts --contract-version 1.0.0 --allow-candidate-contract" --adapter-cwd adapters/aya --token-var AYA_ADAPTER_SERVICE_TOKEN --fixtures adapters/aya/conformance/fixtures.yaml --report $(CONFORMANCE_REPORT_DIR)/conformance-aya.json; rc=$$?; \
	  if [ $$rc -eq 3 ] && [ "$(CONTRACT_STRICT)" != "1" ]; then exit 0; else exit $$rc; fi
	@# 知行（ADP-道法-01）：conformance/harness.ts 先起模型路由接口的本机桩，再加载 src/main.ts；v0.9 与 v1.0 候选版都跑
	@if [ ! -d adapters/zhixing/node_modules/ajv ]; then \
	  echo "conformance: 知行部分跳过（没有安装依赖；cd adapters/zhixing && npm ci）"; \
	  if [ "$(CONTRACT_STRICT)" = "1" ]; then exit 1; else exit 0; fi; fi
	$(call run_contract,packages/contracts/conformance/cli.py --quiet --adapter-cmd "node --no-warnings conformance/harness.ts" --adapter-cwd adapters/zhixing --token-var ZHIXING_ADAPTER_SERVICE_TOKEN --fixtures adapters/zhixing/conformance/fixtures.yaml --report $(CONFORMANCE_REPORT_DIR)/conformance-zhixing.json)
	@STARBUDDY_CONTRACT_VERSION=1.0.0 STARBUDDY_ALLOW_CANDIDATE_CONTRACT=1 $(PYTHON) packages/contracts/conformance/cli.py --quiet --adapter-cmd "node --no-warnings conformance/harness.ts --contract-version 1.0.0 --allow-candidate-contract" --adapter-cwd adapters/zhixing --token-var ZHIXING_ADAPTER_SERVICE_TOKEN --fixtures adapters/zhixing/conformance/fixtures.yaml; rc=$$?; \
	  if [ $$rc -eq 3 ] && [ "$(CONTRACT_STRICT)" != "1" ]; then exit 0; else exit $$rc; fi
	@# 文砺（ADP-语文-01）：同知行，harness.ts 先起路由桩；v0.9 与 v1.0 候选版都跑
	@if [ ! -d adapters/wenli/node_modules/ajv ]; then \
	  echo "conformance: 文砺部分跳过（没有安装依赖；cd adapters/wenli && npm ci）"; \
	  if [ "$(CONTRACT_STRICT)" = "1" ]; then exit 1; else exit 0; fi; fi
	$(call run_contract,packages/contracts/conformance/cli.py --quiet --adapter-cmd "node --no-warnings conformance/harness.ts" --adapter-cwd adapters/wenli --token-var WENLI_ADAPTER_SERVICE_TOKEN --fixtures adapters/wenli/conformance/fixtures.yaml --report $(CONFORMANCE_REPORT_DIR)/conformance-wenli.json)
	@STARBUDDY_CONTRACT_VERSION=1.0.0 STARBUDDY_ALLOW_CANDIDATE_CONTRACT=1 $(PYTHON) packages/contracts/conformance/cli.py --quiet --adapter-cmd "node --no-warnings conformance/harness.ts --contract-version 1.0.0 --allow-candidate-contract" --adapter-cwd adapters/wenli --token-var WENLI_ADAPTER_SERVICE_TOKEN --fixtures adapters/wenli/conformance/fixtures.yaml; rc=$$?; \
	  if [ $$rc -eq 3 ] && [ "$(CONTRACT_STRICT)" != "1" ]; then exit 0; else exit $$rc; fi

# 一致性测试的变异测试：参考假适配器逐一注入 28 类违约，断言每次都由指定的测试发现（约 45 秒）
conformance-mutation:
	$(call run_contract,packages/contracts/conformance/mutation.py)

# 渗透测试环境自检（冲刺 3-B）：up.sh 一条命令起环境 → 只监听本机、令牌随机且不落仓库、夹具齐全 → down.sh 什么都不留（约 15 秒）
# 服务依赖没装时非严格模式只测数据库部分（--db-only）；严格模式要求完整环境
pentest-env-test:
	@args=""; \
	  if [ ! -d adapters/aya/node_modules/ajv ] || [ ! -x services/learning/.venv/bin/python ]; then \
	    if [ "$(CONTRACT_STRICT)" = "1" ]; then echo "pentest-env-test: 失败（学习服务或阿鸦适配器依赖没装）"; exit 1; fi; \
	    echo "pentest-env-test: 服务依赖没装，只测数据库部分"; args="--db-only"; fi; \
	  bash db/pentest/selftest.sh $$args; rc=$$?; \
	  if [ $$rc -eq 3 ] && [ "$(CONTRACT_STRICT)" != "1" ]; then exit 0; else exit $$rc; fi

# 攻击面清单（docs/security/attack-surface.md）引用的断言、变异名都真实存在；没有引用的条目必须标“人工”或“第二轮”
attack-surface-check:
	$(call run_contract,docs/security/check_attack_surface.py)

# 统一壳前端（冲刺 3-H，specs/star-map-sprint3/spec.md）：许可证清单 → next build → 单测与静态检查（含构建产物不外链）→
# 端到端（一次性 Postgres + 阿鸦登记 + 合成种子 + BFF（真实会话、短信捕获桩）+ Playwright：计数、授权、安全头、16px 像素、axe、不外链、不存储）
# 依赖：apps/web 与 services/bff 已 npm ci，Playwright 的 chromium 已安装；没装时非严格模式打印“跳过”
WEB_DEPS_OK = [ -d apps/web/node_modules/next ] && [ -d services/bff/node_modules/pg ]
web-test:
	@if ! $(WEB_DEPS_OK); then \
	  echo "web-test: 跳过（没有安装依赖；cd apps/web && npm ci，cd services/bff && npm ci）"; \
	  if [ "$(CONTRACT_STRICT)" = "1" ]; then exit 1; else exit 0; fi; fi
	@cd apps/web && node scripts/license-check.mjs
	@cd apps/web && NEXT_TELEMETRY_DISABLED=1 npx --no-install next build > .next-build.log 2>&1 || { tail -30 .next-build.log; rm -f .next-build.log; exit 1; }; \
	  rm -f .next-build.log; echo "web-test: next build 通过（含类型检查）"
	@if grep -rlE 'x-dev-staff-id|sb_dev_staff|SYNTHETIC_STAFF|5f0e0000-0000-4000-8000-0000000000e[12]' apps/web/.next --exclude-dir=cache >/dev/null 2>&1; then \
	  echo "web-test: 构建产物里还有开发 staff 身份桩"; exit 1; fi; echo "web-test: 构建产物里没有开发 staff 身份桩"
	@if grep -rlE 'x-dev-account-id|sb_dev_account|STARBUDDY_DEV_AUTH|/dev/login|/dev/session|/dev/star-glyphs|SYNTHETIC_ACCOUNTS' apps/web/.next --exclude-dir=cache >/dev/null 2>&1; then \
	  echo "web-test: 生产构建产物里还有开发身份桩或开发专用页面（4-H H2）"; exit 1; fi; echo "web-test: 生产构建产物里没有开发身份桩与开发专用页面"
	@cd apps/web && node --no-warnings --test --test-reporter=dot tests/*.test.ts && echo "web-test: 单测与静态检查通过"
	@bash apps/web/scripts/e2e.sh --grep-invert @visual --reporter=dot; rc=$$?; \
	  if [ $$rc -eq 0 ]; then echo "web-test: 端到端通过"; fi; \
	  if [ $$rc -eq 3 ] && [ "$(CONTRACT_STRICT)" != "1" ]; then exit 0; else exit $$rc; fi

# 视觉回归（@visual）：与已提交的基线比较，差异像素 ≤ 0.1%。基线要在固定版本的容器与浏览器里生成，
# 首次提交需视觉负责人签字（lessons）。暂不进 make check 与 CI（spec 待人确认 #11）；更新基线：加 UPDATE=1
web-visual:
	@if ! $(WEB_DEPS_OK); then echo "web-visual: 跳过（没有安装依赖）"; exit 0; fi
	@cd apps/web && NEXT_TELEMETRY_DISABLED=1 npx --no-install next build > /dev/null
	@bash apps/web/scripts/e2e.sh --grep @visual $(if $(filter 1,$(UPDATE)),--update-snapshots,)

# KGE-00 知识资产盘点（specs/kge-00-asset-inventory/spec.md）。
# 测试只用合成夹具（CI 没有 legacy 副本）：类型检查、图检查正反例、夹具盘点、只读 SQL 的静态检查与本机临时库运行
kge-inventory-test:
	@if [ ! -d tools/kge-inventory/node_modules/typescript ]; then \
	  echo "kge-inventory-test: 跳过（没有安装依赖；cd tools/kge-inventory && npm ci）"; \
	  if [ "$(CONTRACT_STRICT)" = "1" ]; then exit 1; else exit 0; fi; fi
	@cd tools/kge-inventory && npx --no-install tsc -p tsconfig.json && echo "kge-inventory-test: 类型检查通过"
	@cd tools/kge-inventory && node --no-warnings --test --test-reporter=dot tests/*.test.ts
	@bash tools/kge-inventory/tests/sql-run.sh; rc=$$?; \
	  if [ $$rc -eq 3 ] && [ "$(CONTRACT_STRICT)" != "1" ]; then exit 0; else exit $$rc; fi

# KGE-01 骨架导入（specs/kge-01-skeleton-import）：生成阿鸦 fun 专题的草稿地图版本种子；不连库，SQL 由人在演练库执行
# 教研补齐工作单后：make kge-import KGE_OVERRIDES=path/to/overrides.json
kge-import:
	@cd tools/kge-inventory && node --no-warnings src/import/main.ts --aya ../../legacy/aya --topic fun --version 0.1.0 \
	  $(if $(KGE_OVERRIDES),--overrides $(abspath $(KGE_OVERRIDES))) --out ../../db/seeds/kge/aya-fun-0.1.0

# KGE-02 题库导入与打标（specs/kge-02-atoms-tagging）：类型检查（借用 kge-inventory 的 tsc）+ 单测 + 种子的库上验证（一次性 Postgres）
kge-items-test:
	@if [ ! -d tools/kge-inventory/node_modules/typescript ]; then \
	  echo "kge-items-test: 跳过（没有安装依赖；cd tools/kge-inventory && npm ci）"; \
	  if [ "$(CONTRACT_STRICT)" = "1" ]; then exit 1; else exit 0; fi; fi
	@cd tools/kge-import && ../kge-inventory/node_modules/.bin/tsc -p tsconfig.json && echo "kge-items-test: 类型检查通过"
	@cd tools/kge-import && node --no-warnings --test --test-reporter=dot tests/*.test.ts && echo "kge-items-test: 单测通过"
	@bash tools/kge-import/tests/items-db.sh; rc=$$?; \
	  if [ $$rc -eq 3 ] && [ "$(CONTRACT_STRICT)" != "1" ]; then exit 0; else exit $$rc; fi

# KGE-02 覆盖矩阵报表：单测 + 只读 SQL 与报表在一次性库上逐字节核对
kge-coverage-test:
	@if [ ! -d tools/kge-coverage/node_modules/typescript ]; then \
	  echo "kge-coverage-test: 跳过（没有安装依赖；cd tools/kge-coverage && npm ci）"; \
	  if [ "$(CONTRACT_STRICT)" = "1" ]; then exit 1; else exit 0; fi; fi
	@cd tools/kge-coverage && npx --no-install tsc -p tsconfig.json && node --no-warnings --test --test-reporter=dot tests/*.test.ts && echo "kge-coverage-test: 单测通过"
	@bash tools/kge-coverage/tests/coverage-db.sh; rc=$$?; \
	  if [ $$rc -eq 3 ] && [ "$(CONTRACT_STRICT)" != "1" ]; then exit 0; else exit $$rc; fi

# KGE-02 打标评测脚本（kappa、Top-1）：只在合成夹具上证明计算正确；教研金标到位后另用 --enforce 验收
kge-tagging-eval-test:
	@cd evals/kge-tagging && PYTHONDONTWRITEBYTECODE=1 $(PYTHON) -m unittest discover -s tests -q && echo "kge-tagging-eval-test: 通过"

# 单测（tests/import.test.ts）随 kge-inventory-test 的 tests/*.test.ts 一起跑；这里只跑库上验证。
# 道法部分（冲刺 4-K）作为前置目标一起跑（同一次 make 里不重复执行）
kge-import-test: kge-daofa-import-test
	@if [ ! -d tools/kge-inventory/node_modules/typescript ]; then \
	  echo "kge-import-test: 跳过（没有安装依赖；cd tools/kge-inventory && npm ci）"; \
	  if [ "$(CONTRACT_STRICT)" = "1" ]; then exit 1; else exit 0; fi; fi
	@bash tools/kge-inventory/tests/import-db.sh; rc=$$?; \
	  if [ $$rc -eq 3 ] && [ "$(CONTRACT_STRICT)" != "1" ]; then exit 0; else exit $$rc; fi

# 冲刺 4-K 道法切片（specs/kge-daofa-slice-sprint4）：观点地图导入器、页码抽样核验、采分点入原子层、时政（tools/kge-import/src/daofa）。
# 类型检查（借用 kge-inventory 的 tsc）+ 单测（tests/daofa/*.test.ts）+ 一次性 Postgres 上的端到端（daofa-db.sh：种子两次逐字节相同、
# 合成挡板、草稿版本、采分点只新增、时政白名单与 pending、库上快照 = 离线快照、发布后只读角色看不到过期时政）+ 页码回填演示
# （page-backfill-demo.sh：低于阈值不能标 verified；达标时现有表因 projection_verified_by 写不进 verified，见 lessons 待人确认）。
# 缺 tsc 或 Postgres 时非严格跳过、严格失败
kge-daofa-import-test:
	@if [ ! -d tools/kge-inventory/node_modules/typescript ]; then \
	  echo "kge-daofa-import-test: 跳过（没有安装依赖；cd tools/kge-inventory && npm ci）"; \
	  if [ "$(CONTRACT_STRICT)" = "1" ]; then exit 1; else exit 0; fi; fi
	@cd tools/kge-import && ../kge-inventory/node_modules/.bin/tsc -p tsconfig.json && echo "kge-daofa-import-test: 类型检查通过"
	@cd tools/kge-import && node --no-warnings --test --test-reporter=dot tests/daofa/*.test.ts && echo "kge-daofa-import-test: 单测通过"
	@bash tools/kge-import/tests/daofa/daofa-db.sh; rc=$$?; \
	  if [ $$rc -eq 3 ] && [ "$(CONTRACT_STRICT)" != "1" ]; then exit 0; else exit $$rc; fi
	@bash tools/kge-import/tests/daofa/page-backfill-demo.sh; rc=$$?; \
	  if [ $$rc -eq 3 ] && [ "$(CONTRACT_STRICT)" != "1" ]; then exit 0; else exit $$rc; fi

# 采分点单一真相源（冲刺 4-K 设计要点 3）：adapters/zhixing/content/pack.json 与原子层 kge.scoring_point 按同一 rubric_version 逐字段一致，
# 不一致即失败。默认在一次性 Postgres 上走“内容包 → kge.scoring_point → export-scoring-points.sql 导出”再比对（合成 4 题）；
# 人在演练库导出后可直接比对：make zhixing-rubric-check ZHIXING_RUBRIC_EXPORT=path/to/export.json（不起库）
zhixing-rubric-check:
	@ZHIXING_RUBRIC_EXPORT="$(if $(ZHIXING_RUBRIC_EXPORT),$(abspath $(ZHIXING_RUBRIC_EXPORT)))" bash tools/kge-import/tests/daofa/rubric-check.sh; rc=$$?; \
	  if [ $$rc -eq 3 ] && [ "$(CONTRACT_STRICT)" != "1" ]; then exit 0; else exit $$rc; fi

# 道法采分点判分一致率（冲刺 4-K 设计要点 6）：逐点一致率、逐题总分一致率、失分归因混淆表在合成夹具上与手算一致（只用标准库）。
# 教研金标到位后另用 --enforce 验收（evals/daofa-grading/README.md），不进 make check
daofa-grading-eval-test:
	@cd evals/daofa-grading && PYTHONDONTWRITEBYTECODE=1 $(PYTHON) -m unittest discover -s tests -q && echo "daofa-grading-eval-test: 通过"

# 对本机 legacy 只读副本生成真实报告，写到 specs/kge-00-asset-inventory/report/（不进 make check；只读 legacy，不连库）。
# 并入库模式输出：make kge-inventory DB_OUTPUT="wenli=path/to/db-output-wenli.txt aya=..."
kge-inventory:
	@cd tools/kge-inventory && node --no-warnings src/main.ts --out ../../specs/kge-00-asset-inventory/report \
	  $(foreach d,$(DB_OUTPUT),--db-output $(d))

# KGE-03 编译层（specs/kge-03-compile-layer）PR ①：页面格式、编译器（编入、渲染重建、全量重编经模型路由假供应商、合并窗口）、
# 程序体检（含 SymPy 验算、个人信息模式、人设词表）、页面仓库模板；合成地图与合成页面上的端到端。
# PR ②：知识服务读工具（HTTP + MCP）、页面快照导出与 page.get；库上测试、契约一致性（knowledge-service.v0.9 严格校验）、
# 端到端在 tests/pgkge.py 起的一次性 Postgres 上跑（约 25 秒）。没有 Postgres 时库上测试非严格跳过、严格失败
# PR ③：兜底检索（jieba + 进程内 BM25，只切公共层）、node.resolve 接全文检索、补页面信号、M1 报表（tests/test_retrieval.py、test_m1.py）
# 依赖需先安装（带哈希的锁文件）：cd services/knowledge && python3 -m venv .venv && .venv/bin/pip install --require-hashes -r requirements-dev.lock
knowledge-test:
	@if [ ! -x services/knowledge/.venv/bin/pytest ] || ! services/knowledge/.venv/bin/python -c "import fastapi, asyncpg, mcp, jsonschema, jieba" 2>/dev/null; then \
	  echo "knowledge-test: 跳过（没有安装依赖；见 services/knowledge/README.md）"; \
	  if [ "$(CONTRACT_STRICT)" = "1" ]; then exit 1; else exit 0; fi; fi
	@cd services/knowledge && KNOWLEDGE_TEST_STRICT=$(if $(filter 1,$(CONTRACT_STRICT)),1,0) .venv/bin/python -m pytest -q -rs \
	  && echo "knowledge-test: 通过"

# 变异测试：改坏编译器与程序体检（锁定段比对、缺引用、SymPy 验算、个人信息模式、人设词表、合并窗口、全量重编）和
# 知识服务读工具（草稿 / 退役版本、未核对投影、待审标签、弱前置边、caller 绑定、请求与响应校验、角色断言、未发布页、
# 快照导出体检、MCP 鉴权与监听地址）的关键检查，以及 PR ③ 的兜底检索（BM25、分词、只切公共层、兜底条件、补页面信号）、
# M1 报表与对照评测脚本（evals/kge-compile）的关键计算，以及审查处理（个人信息与 ID 白名单、快照载入与导出、SymPy 加固、
# 角色断言、Host 校验、MCP 工具清单、页面仓库 CI 样例）的检查，以及页面对账（G 流冲刺 3：合并窗口与快照导出跳过被扣页面、对账报告必填且不过期），断言测试能发现（132 条，约 10 分钟）。没有 Postgres 时知识服务的变异
# 跳过（退出码 3：非严格算跳过、严格算失败）
knowledge-mutation:
	@bash services/knowledge/tests/mutations.sh; rc=$$?; \
	  if [ $$rc -eq 3 ] && [ "$(CONTRACT_STRICT)" != "1" ]; then exit 0; else exit $$rc; fi

# 冲刺 5-K KGE M2 报表（specs/kge-m2-gate-sprint5）：核心节点内容完整率 + 数学、道法准入汇总（Markdown + JSON，同样输入逐字节相同）。
# 由执行人对真实输入运行（不进 make check；测试 tests/test_m2.py 随 knowledge-test 运行）。输入目录的约定文件名见 services/knowledge/README.md；
# 缺的文件报“待填”。用法：make kge-m2 M2_SUBJECT=aya|zhixing M2_INPUTS=<输入目录> M2_OUT=<输出目录>
# 参数在 make 层先过白名单：学科只收 aya / zhixing（各一个词）；两个路径各恰好一个词（不含空格）、不含单引号，传给 shell 时单引号包裹
.PHONY: kge-m2
KGE_M2_BAD = $(or $(filter-out 1,$(words $(M2_SUBJECT))),$(if $(filter aya zhixing,$(M2_SUBJECT)),,bad),$(filter-out 1,$(words $(M2_INPUTS))),$(filter-out 1,$(words $(M2_OUT))),$(findstring ',$(M2_INPUTS)$(M2_OUT)))
kge-m2:
	@$(if $(KGE_M2_BAD),echo "用法：make kge-m2 M2_SUBJECT=aya|zhixing M2_INPUTS=<输入目录> M2_OUT=<输出目录>（路径不含空格与单引号）"; exit 2)
	@if [ ! -x services/knowledge/.venv/bin/python ]; then echo "kge-m2: 没有安装依赖（见 services/knowledge/README.md）"; exit 2; fi
	@cd services/knowledge && .venv/bin/python -m knowledge.m2 --subject '$(M2_SUBJECT)' --inputs '$(abspath $(M2_INPUTS))' --out '$(abspath $(M2_OUT))'

# KGE-03 PR ③ 对照评测（evals/kge-compile）：页面优先 vs 切块检索的指标与手算一致、确定性输出、报告只含汇总与题目 ID、
# 合成样例报告与夹具是最新的。用知识服务的 venv（需要 jieba 与 knowledge 包）；缺依赖时非严格跳过、严格失败
kge-compile-eval-test:
	@if [ ! -x services/knowledge/.venv/bin/pytest ] || ! services/knowledge/.venv/bin/python -c "import jieba, yaml" 2>/dev/null; then \
	  echo "kge-compile-eval-test: 跳过（没有安装依赖；见 services/knowledge/README.md）"; \
	  if [ "$(CONTRACT_STRICT)" = "1" ]; then exit 1; else exit 0; fi; fi
	@cd evals/kge-compile && PYTHONDONTWRITEBYTECODE=1 ../../services/knowledge/.venv/bin/python -m pytest -q && echo "kge-compile-eval-test: 通过"

# 人设治理（冲刺 3-F，specs/persona-governance-sprint3）：Prompt 登记表与指纹锁、禁语分类器 guard、七类家族级检查、长对话漂移、
# N16 泄露探针、录制回放与人设回归报告。全部离线：不联网、不读密钥、不调用任何模型。只用标准库 + PyYAML（+ jsonschema、pytest 跑测试）。
# persona-registry-check：登记的文件与符号存在、版本合法；Prompt 或禁语词表改了没升版本 → 失败；指纹与锁文件不一致 → 失败；
# 未登记的 Prompt（启发式扫描 adapters/ services/ packages/ apps/，可用 `persona-registry: ignore <原因>` 豁免）→ 失败（约 1 秒）
persona-registry-check:
	@PYTHONPATH=packages $(PYTHON) -m personas.registry check; rc=$$?; \
	  if [ $$rc -eq 3 ] && [ "$(CONTRACT_STRICT)" != "1" ]; then exit 0; else exit $$rc; fi

# 重算 packages/personas/prompts.lock（锁文件只能由它改写；内容变了而版本没升时拒绝）。改完跑 make eval-persona 并在 PR 里附前后对比
persona-lock:
	@PYTHONPATH=packages $(PYTHON) -m personas.registry lock

# guard 分类器与登记表的单测（种子正例全中、反例零误报、结构层三种情况、按角色判定、词表迁移镜像、未登记扫描、无网络调用；约 10 秒）
persona-guard-test:
	@if ! $(PYTHON) -c "import yaml, pytest" 2>/dev/null; then \
	  echo "persona-guard-test: 跳过（$(PYTHON) 没有 PyYAML 或 pytest；CI 用 .venv-contracts）"; \
	  if [ "$(CONTRACT_STRICT)" = "1" ]; then exit 1; else exit 0; fi; \
	else PYTHONPATH=packages $(PYTHON) -m pytest -q -p no:cacheprovider packages/personas/tests && echo "persona-guard-test: 通过"; fi

# 录制（spec 设计要点 6 第 2 层）：由持有 dev 密钥的人在本地执行，Claude Code 不持有密钥、不执行录制。
# 录制脚本要等 ADR 04 签字的境内供应商与人设负责人提交的 Prompt 到位后再写；现在只是占位，步骤见 evals/persona/README.md“录制回放”。
persona-record:
	@echo "persona-record: 尚未提供录制脚本（占位）。录制由人在本地用 dev 密钥执行，输出 evals/persona/recordings/$(or $(ROLE),<角色>)/<用例>.json，"
	@echo "  格式见 evals/persona/schema/recording.schema.json；前置条件与步骤见 evals/persona/README.md。"
	@exit 2

# 人设回归（冲刺 1-I 点点一致性 + 冲刺 3-F 人设治理）：用例格式校验、评分器自检、测试（含离线全流程与治理验收）、
# 人设回归报告（$(PERSONA_REPORT_DIR)/persona-report.{json,md}，按 evals/persona/schema/report.schema.json 校验）、变异测试（并行 4 路，约 3 分钟）。
#   make eval-persona [ROLE=aya] [BASE=<提交>]：ROLE 只跑一个角色（此时跳过变异测试）；BASE 是前后对比的基线提交
#   （默认 PR 的目标分支或上一个提交）。录音缺失或过期：严格模式失败“需要重新录制”，非严格打印“跳过（缺录音）”并在报告里记“未运行”（退出码 3）。
# 不联网、不需要密钥、不调用任何真实模型，所以并入 make check；真实模型评测（2-I、3-I）不进 CI。
EVAL_PY := services/learning/.venv/bin/python
PERSONA_REPORT_DIR ?= $(or $(TMPDIR),/tmp)/persona-report
eval-persona:
	@if [ ! -x $(EVAL_PY) ]; then \
	  echo "eval-persona: 跳过（没有安装依赖；见 services/learning/README.md）"; \
	  if [ "$(CONTRACT_STRICT)" = "1" ]; then exit 1; else exit 0; fi; fi
	@cd evals/persona && ../../$(EVAL_PY) -m scorer validate
	@cd evals/persona && ../../$(EVAL_PY) -m scorer selftest
	@cd evals/persona && ../../$(EVAL_PY) -m pytest -q -p no:cacheprovider tests && echo "eval-persona: 评分器与人设治理测试通过（含离线全流程）"
	@cd evals/persona && ../../$(EVAL_PY) -m governance run $(if $(ROLE),--role $(ROLE)) $(if $(BASE),--base $(BASE)) \
	  $(if $(filter 1,$(CONTRACT_STRICT)),--strict) --out $(abspath $(PERSONA_REPORT_DIR)); rc=$$?; \
	  if [ $$rc -eq 3 ] && [ "$(CONTRACT_STRICT)" != "1" ]; then exit 0; else exit $$rc; fi
	@if [ -n "$(ROLE)" ]; then echo "eval-persona: 指定了 ROLE，跳过变异测试"; \
	  else cd evals/persona && ../../$(EVAL_PY) scripts/mutations.py; fi

eval-grade:
	@echo "eval-grade: SUBJECT=$(SUBJECT)，evals 尚未建立，跳过"

# 掌握 v0 纯函数部分（KGE-04 第一步，specs/mastery-estimate-sprint4）：单测与性质测试在 learning-test 里；
# 这里把配置校验、证据分类、门槛状态机、衰减、写入判定、一致性检查的关键判定逐条改坏，断言掌握测试能发现（不连库，约 20 秒）；
# 第二组（KGE-04 第二步 PR 2）在一次性 Postgres 上把读取层、写入层、复测任务、grade 接入逐条改坏（每个变异起一个库，约 1–2 分钟）。
# 没有本机 Postgres 时第二组退出码 3：非严格跳过、严格失败
.PHONY: mastery-mutation
mastery-mutation:
	@if [ ! -x services/learning/.venv/bin/pytest ]; then \
	  echo "mastery-mutation: 跳过（没有安装学习服务依赖；见 services/learning/README.md）"; \
	  if [ "$(CONTRACT_STRICT)" = "1" ]; then exit 1; else exit 0; fi; fi
	@services/learning/.venv/bin/python services/learning/scripts/mastery_mutations.py; rc=$$?; \
	  if [ $$rc -eq 3 ] && [ "$(CONTRACT_STRICT)" != "1" ]; then exit 0; else exit $$rc; fi

# SIE-02b 第一步（specs/sie-02b-claims-first-report）：洞察服务 services/insight——判断生成与生命周期（合成掌握行驱动）、
# insight.claims / explain / context（契约严格校验）、首份报告结构化生成与两道检查、中圈指标静默计算，以及影子评审比对脚本
# （evals/sie-shadow，用洞察服务的 venv）。库上测试在 tests/pginsight.py 起的一次性 Postgres 上跑（执行 bootstrap 角色与全部迁移）。
# 没有 Postgres 时库上测试非严格跳过、严格失败。依赖需先安装（带哈希的锁文件）：
#   cd services/insight && python3 -m venv .venv && .venv/bin/pip install --require-hashes -r requirements-dev.lock
insight-test:
	@if [ ! -x services/insight/.venv/bin/python ] || ! services/insight/.venv/bin/python -c "import fastapi, asyncpg, jsonschema, yaml, httpx, pytest" 2>/dev/null; then \
	  echo "insight-test: 跳过（没有安装依赖；见 services/insight/README.md）"; \
	  if [ "$(CONTRACT_STRICT)" = "1" ]; then exit 1; else exit 0; fi; fi
	@cd services/insight && INSIGHT_TEST_STRICT=$(if $(filter 1,$(CONTRACT_STRICT)),1,0) PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -rs
	@cd evals/sie-shadow && PYTHONDONTWRITEBYTECODE=1 ../../services/insight/.venv/bin/python -m pytest -q && echo "insight-test: 通过"

# 变异测试：改坏判断生命周期、首份报告的一致性与禁用推断检查、渲染、中圈指标、可见范围过滤、契约校验、调用方准入与授权核对、
# 角色断言、监听地址与影子评审脚本的关键检查，断言测试能发现（50 条以上，约 1–2 分钟）。没有依赖或 Postgres 时退出码 3（非严格跳过、严格失败）
insight-mutation:
	@bash services/insight/tests/mutations.sh; rc=$$?; \
	  if [ $$rc -eq 3 ] && [ "$(CONTRACT_STRICT)" != "1" ]; then exit 0; else exit $$rc; fi

# 冲刺 4-I 第一部分（specs/voice-blind-test-sprint4）：语音盲测评测框架 evals/voice-blind——CER / WER、延迟分位数、音素一致性、
# 自助法区间与手算一致；报告确定性、只含汇总与编号、拒绝写 gold/；合成样例报告是最新的；不联网、不持有密钥（测试断言）；
# 再跑变异测试（27 条，约 30 秒）。只用标准库 + PyYAML + pytest；缺依赖时非严格跳过、严格失败。
# 系统 python 缺 pytest 时可传 PYTHON=services/learning/.venv/bin/python
.PHONY: voice-blind-eval-test
voice-blind-eval-test:
	@if ! $(PYTHON) -c "import yaml, pytest" 2>/dev/null; then \
	  echo "voice-blind-eval-test: 跳过（$(PYTHON) 缺 PyYAML 或 pytest；可传 PYTHON=services/learning/.venv/bin/python）"; \
	  if [ "$(CONTRACT_STRICT)" = "1" ]; then exit 1; else exit 0; fi; \
	else \
	  (cd evals/voice-blind && PYTHONDONTWRITEBYTECODE=1 $(if $(findstring /,$(PYTHON)),$(abspath $(PYTHON)),$(PYTHON)) -m pytest -q) && \
	  PYTHONDONTWRITEBYTECODE=1 $(PYTHON) evals/voice-blind/scripts/mutations.py && echo "voice-blind-eval-test: 通过"; \
	fi

# 冲刺 5-C+D 端到端联调（specs/e2e-integration-sprint5）：20 名合成学生在一次性 Postgres + 学习服务 + 三科适配器上走诊断、批改、复测（桩），
# 断言三科事件并存不覆盖、私有表无学生事实副本、grade 溯源五字段、整轮重跑零新增、RLS 隔离，证据包与合成样例一致，另有反例与 verify 护栏测试（约 1 分钟）。
# 同一测试也随 learning-test 运行；这里单独跑。依赖同 learning-test 与三科适配器（adapters/{aya,zhixing,wenli} 下 npm ci）
.PHONY: e2e-cohort gate0-evidence
e2e-cohort:
	@if [ ! -x services/learning/.venv/bin/pytest ]; then \
	  echo "e2e-cohort: 跳过（没有安装依赖；见 services/learning/README.md）"; \
	  if [ "$(CONTRACT_STRICT)" = "1" ]; then exit 1; else exit 0; fi; fi
	@cd services/learning && LEARNING_TEST_STRICT=$(if $(filter 1,$(CONTRACT_STRICT)),1,0) .venv/bin/python -m pytest -q tests/test_e2e_cohort.py

# Gate 0 证据包（合成联调，不是内测结论）：跑一遍完整合成联调，把事件计数、节点查询摘要、断言结果与运行元数据写到执行人指定目录
# （确定性输出，只含合成数据与 ID；仓库只收一份合成样例 tools/e2e-cohort/sample/）。
# 星图截图：GATE0_STARMAP=1（同一一次性库上起 BFF 与前端测试构建，本机 Chromium 经真实会话截壳内星图页，写到 <目录>/starmap/；
# 先跑对账单测 tools/e2e-cohort/tests；失败即 starmap.status = failed、删本轮截图并非零退出；与 make web-test 共用 apps/web/.next-e2e，不能同时跑）。
# 不带 GATE0_STARMAP=1 时不截图、不删已有 starmap/（starmap/provenance.md 标“截图沿用”）；星图页面改版（如 H5）时才重截样例。
# 用法：make gate0-evidence GATE0_OUT=<输出目录> [GATE0_STARMAP=1]（路径不含空格与单引号）
GATE0_BAD = $(or $(filter-out 1,$(words $(GATE0_OUT))),$(findstring ',$(GATE0_OUT)))
gate0-evidence:
	@$(if $(GATE0_BAD),echo "用法：make gate0-evidence GATE0_OUT=<输出目录>（路径不含空格与单引号）"; exit 2)
	@if [ ! -x services/learning/.venv/bin/python ]; then echo "gate0-evidence: 没有安装依赖（见 services/learning/README.md）"; exit 2; fi
	@$(if $(filter 1,$(GATE0_STARMAP)),cd services/learning && .venv/bin/python -m pytest -q -p no:cacheprovider --rootdir '$(abspath tools/e2e-cohort)' '$(abspath tools/e2e-cohort/tests)',true)
	@cd services/learning && PYTHONPATH='$(abspath tools/e2e-cohort)' .venv/bin/python -m e2e_cohort evidence --out '$(abspath $(GATE0_OUT))' $(if $(filter 1,$(GATE0_STARMAP)),--starmap,)

# H5 星图 v1 的 Gate 0 截图（specs/star-map-v1-h5 待人确认 #7）：v0 截图（<证据包>/starmap/）不替换；另在 test 环境、开关打开、
# 合成数据（db/seeds/dev/star-map-v1-synthetic.sql）下截 v1 学生端与家长端各一张，标“未启用”，附文件与像素 SHA-256
# （apps/web/e2e/visual-v1.spec.ts 的 @gate0 用例，走 apps/web/scripts/e2e.sh 的一次性环境；与 make web-test 共用 .next-e2e，不能同时跑）。
# 用法：make gate0-starmap-v1 [GATE0_V1_OUT=<仓库内的输出目录>]（缺省写到合成样例旁的 tools/e2e-cohort/sample/starmap-v1/；仓库外的目录被拒绝）
GATE0_V1_OUT ?= tools/e2e-cohort/sample/starmap-v1
.PHONY: gate0-starmap-v1
gate0-starmap-v1:
	@if ! $(WEB_DEPS_OK); then echo "gate0-starmap-v1: 没有安装依赖（cd apps/web && npm ci，cd services/bff && npm ci）"; exit 2; fi
	@STARBUDDY_GATE0_V1_OUT='$(abspath $(GATE0_V1_OUT))' bash apps/web/scripts/e2e.sh --grep @gate0 --project starmap-on --reporter=line

# 冲刺 5 压测基线、单模型失效降级、成本测算（specs/load-cost-baseline-sprint5；工具与手册见 tools/loadtest/README.md、RUNBOOK.md）。
# 只在本机一次性栈上跑（规则 9），只用合成数据（规则 8）；依赖同 learning-test、三科适配器与 BFF 的 npm ci。
#   make loadtest-baseline [LOADTEST_STAGES=small|full|10:120,50:120] [LOADTEST_OUT=<目录>] [LOADTEST_ARGS='--fault fake-a=unavailable']
#     默认 small（5 → 10 并发 × 10 s）；完整分阶段 10 → 50 → 100 → 200 用 LOADTEST_STAGES=full LOADTEST_ARGS=--i-know（约 8 分钟，占满本机）
#   make degradation-test   故障注入与降级断言（路由层 + 端到端，约 40 秒）
#   make cost-report [COST_RECORDS=<调用记录 jsonl>] [COST_OUT=<目录>]   成本测试 + 科目 × 会员档 P50 / P90 报表（合成估算、示例价）
# 不给输出目录时写到新建的临时目录（mkdtemp）；给了则不能是符号链接、须属于当前用户。
# 护栏（同 GATE0_BAD）：路径不含空格与单引号；LOADTEST_STAGES 只收 small、full 或“数字:数字,…”；LOADTEST_ARGS 只收白名单选项。
# 变量一律经环境变量传给命令（不在命令行里展开），shell 不会把它们当代码执行。
.PHONY: loadtest-baseline degradation-test cost-report
LOADTEST_PY := services/learning/.venv/bin/python
LOADTEST_STAGES ?= small
LOADTEST_OUT ?=
LOADTEST_ARGS ?=
COST_RECORDS ?= tools/loadtest/sample/call-records.jsonl
COST_OUT ?=
LOADTEST_PATH_BAD = $(or $(filter-out 0 1,$(words $(LOADTEST_OUT))),$(findstring ',$(LOADTEST_OUT)),$(filter-out 0 1,$(words $(COST_OUT))),$(findstring ',$(COST_OUT)),$(filter-out 1,$(words $(COST_RECORDS))),$(findstring ',$(COST_RECORDS)))
loadtest-baseline degradation-test cost-report: export LT_STAGES = $(LOADTEST_STAGES)
loadtest-baseline degradation-test cost-report: export LT_ARGS = $(LOADTEST_ARGS)
loadtest-baseline degradation-test cost-report: export LT_OUT = $(if $(LOADTEST_OUT),$(abspath $(LOADTEST_OUT)),)
loadtest-baseline degradation-test cost-report: export LT_COST_OUT = $(if $(COST_OUT),$(abspath $(COST_OUT)),)
loadtest-baseline degradation-test cost-report: export LT_COST_RECORDS = $(abspath $(COST_RECORDS))
loadtest-baseline:
	@$(if $(LOADTEST_PATH_BAD),echo "loadtest-baseline: 路径不能含空格或单引号"; exit 2)
	@case "$$LT_STAGES" in small|full) ;; ""|*[!0-9:,.]*) echo "loadtest-baseline: LOADTEST_STAGES 只收 small、full 或 并发:秒,…"; exit 2;; esac
	@case "$$LT_ARGS" in *[!A-Za-z0-9=:._\ -]*) echo "loadtest-baseline: LOADTEST_ARGS 含不允许的字符"; exit 2;; esac; \
	  for a in $$LT_ARGS; do case "$$a" in --i-know|--fault|--retry-scale|--median-ms|--p90-ms|--seed|fake-[abx]=unavailable|fake-[abx]=timeout|fake-[abx]=rate_limited|fake-[abx]=error_ratio:[0-9.]*|fake-[abx]=slow:[0-9]*|[0-9]*) ;; *) echo "loadtest-baseline: LOADTEST_ARGS 里的 $$a 不在白名单"; exit 2;; esac; done
	@if [ ! -x $(LOADTEST_PY) ]; then echo "loadtest-baseline: 跳过（没有 services/learning/.venv）"; \
	  if [ "$(CONTRACT_STRICT)" = "1" ]; then exit 1; else exit 0; fi; fi
	@cd tools/loadtest && PYTHONDONTWRITEBYTECODE=1 ../../$(LOADTEST_PY) -m pytest -q tests/test_arrivals.py tests/test_guards.py
	@cd tools/loadtest && ../../$(LOADTEST_PY) -m loadtest baseline --stages "$$LT_STAGES" $${LT_OUT:+--out "$$LT_OUT"} $$LT_ARGS \
	  && echo "loadtest-baseline: 报告见上面 out 目录的 baseline.md（本机基线，不代表生产容量）"
degradation-test:
	@if [ ! -x services/learning/.venv/bin/pytest ]; then echo "degradation-test: 跳过（没有 services/learning/.venv）"; \
	  if [ "$(CONTRACT_STRICT)" = "1" ]; then exit 1; else exit 0; fi; fi
	@cd tools/loadtest && PYTHONDONTWRITEBYTECODE=1 ../../$(LOADTEST_PY) -m pytest -q tests/test_metrics_faults.py
	@cd services/learning && LEARNING_TEST_STRICT=$(if $(filter 1,$(CONTRACT_STRICT)),1,0) .venv/bin/python -m pytest -q -rs tests/test_degradation.py \
	  && echo "degradation-test: 通过"
cost-report:
	@$(if $(LOADTEST_PATH_BAD),echo "cost-report: 路径不能含空格或单引号"; exit 2)
	@if [ ! -x $(LOADTEST_PY) ]; then echo "cost-report: 跳过（没有 services/learning/.venv）"; \
	  if [ "$(CONTRACT_STRICT)" = "1" ]; then exit 1; else exit 0; fi; fi
	@cd tools/loadtest && PYTHONDONTWRITEBYTECODE=1 ../../$(LOADTEST_PY) -m pytest -q tests/test_cost.py
	@cd tools/loadtest && ../../$(LOADTEST_PY) -m loadtest cost --records "$$LT_COST_RECORDS" $${LT_COST_OUT:+--out "$$LT_COST_OUT"} \
	  && echo "cost-report: 报告见上面 out 目录的 cost-report.md（合成估算、示例价）"
