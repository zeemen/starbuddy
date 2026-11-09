# 未实现的目标明确打印“跳过”，不得伪装为通过。
.PHONY: check-mutation check-group-persona-mutation check-group-contracts-mutation check-group-aya-mutation check-group-adapters-mutation check-group-learning-a-mutation check-group-learning-b-mutation check-group-bff-web-mutation check-group-kge-tools-mutation check-group-kge-model-mutation check-group-knowledge-mutation check-group-insight-mutation check-fast check check-core check-group-persona-light check-group-contracts-light check-group-aya-light check-group-adapters-light check-group-learning-a-light check-group-learning-b-light check-group-bff-web-light check-group-kge-tools-light check-group-kge-model-light check-group-knowledge-light check-group-insight-light check-light weknora-probe-test check-group-persona check-group-contracts check-group-aya check-group-adapters check-group-learning-a check-group-learning-b check-group-bff-web check-group-kge-tools check-group-kge-model check-group-knowledge check-group-insight contract-test contract-mutation taxonomy-check db-test db-mutation adapter-aya-test adapter-aya-mutation adapter-zhixing-test adapter-wenli-test learning-test page-reconcile-test current-affair-expire model-router-mutation diagnosis-mutation adapter-client-mutation bff-test bff-mutation conformance conformance-mutation pentest-env-test attack-surface-check web-test web-claims-mutation web-visual kge-inventory kge-inventory-test kge-import kge-import-test kge-items-test kge-coverage-test kge-tagging-eval-test kge-daofa-import-test zhixing-rubric-check daofa-grading-eval-test sie-dictionary-check kge-id-check knowledge-test knowledge-mutation kge-compile-eval-test eval-persona eval-grade persona-registry-check persona-guard-test persona-lock persona-record insight-test insight-mutation release-walkthrough kge-dedupe-test kge-dedupe-mutation kge-calibrate-test kge-calibrate-mutation cost-dashboard-test cost-dashboard-mutation kge-irt-test kge-irt-mutation

# 可指向装了 packages/contracts/requirements-dev.txt 的解释器，例如 make check PYTHON=.venv/bin/python
PYTHON ?= python3
export PYTHONDONTWRITEBYTECODE = 1
# CI 里设 CONTRACT_STRICT=1：缺依赖（Python 依赖或本机 Postgres）直接失败，不允许“跳过”变绿
CONTRACT_STRICT ?= 0

# 退出码 3 = 缺依赖：非严格模式下打印“跳过”但不算失败。可选的第二个参数放在命令前，用于 guard（见下）
define run_contract
	@$(2)$(PYTHON) $(1); rc=$$?; \
	  if [ $$rc -eq 3 ] && [ "$(CONTRACT_STRICT)" != "1" ]; then exit 0; else exit $$rc; fi
endef

# “跳过”分支要结束整个目标（W13 技术债 T04，specs/tech-debt-batch1）：配方每一行是单独的 shell，跳过分支里的 exit 0
# 只结束它所在的那一行，make 会接着执行后面的行。guard 是放在跳过分支后面各行开头的一段 shell：每行运行时自己再查一次
# 缺依赖条件（不在 make 展开时求值，所以命令行传入的 PGBIN 等变量与配方看到的一致），缺依赖时严格模式以 1 退出、
# 非严格静默以 0 退出（“跳过”只由首行的跳过分支打印一次）；不缺依赖时什么都不做，命令原样执行。
# 写法：缺依赖条件写成一个变量，跳过分支（if $(XXX_MISSING); then …）与后面各行（@$(call guard,$(XXX_MISSING))…）共用。
# 验证：make make-skip-check（tools/make-skip-check/check.sh，不进 make check）
guard = if $(1); then [ "$(CONTRACT_STRICT)" = "1" ] && exit 1; exit 0; fi;
skip_rest = $(call guard,$(1))
AYA_MISSING = [ ! -d adapters/aya/node_modules/ajv ]
ZHIXING_MISSING = [ ! -d adapters/zhixing/node_modules/ajv ]
WENLI_MISSING = [ ! -d adapters/wenli/node_modules/ajv ]
LEARNING_MISSING = [ ! -x services/learning/.venv/bin/pytest ]
BFF_MISSING = [ ! -d services/bff/node_modules/pg ]
KGE_INVENTORY_MISSING = [ ! -d tools/kge-inventory/node_modules/typescript ]
KGE_COVERAGE_MISSING = [ ! -d tools/kge-coverage/node_modules/typescript ]

# 跳过分支的可复现验证（W13 技术债 T04）：空目录里严格 / 非严格、命令行 PGBIN、伪依赖目录放行（约 1 分钟，不进 make check）
.PHONY: make-skip-check
make-skip-check:
	@bash tools/make-skip-check/check.sh

# 契约检查约 1 秒，改动后也跑一遍
check-fast: contract-test taxonomy-check attack-surface-check sie-dictionary-check kge-id-check

# check-core：除 db-mutation 以外的全部检查，按模块分成 11 组；CI 每组一个任务并行跑（.github/workflows/check.yml），
# db-mutation 另按分片并行。check-core 就是这些组的并集：新检查必须放进某一组，否则进不了 check。
# 本地 make check 仍然一次跑全部。
CHECK_GROUP_PERSONA := eval-persona persona-registry-check persona-guard-test voice-blind-eval-test
CHECK_GROUP_CONTRACTS := contract-test contract-mutation taxonomy-check attack-surface-check sie-dictionary-check kge-id-check conformance conformance-mutation db-test pentest-env-test
CHECK_GROUP_AYA := adapter-aya-test adapter-aya-mutation
CHECK_GROUP_ADAPTERS := adapter-zhixing-test adapter-wenli-test adapter-client-mutation zhixing-rubric-check daofa-grading-eval-test
CHECK_GROUP_LEARNING_A := learning-test page-reconcile-test model-router-mutation diagnosis-mutation mastery-mutation practice-mutation
CHECK_GROUP_LEARNING_B := internal-v1-mutation retest-mutation today-mutation review-console-mutation release-walkthrough loadtest-test cost-dashboard-test cost-dashboard-mutation mock-exam-test mock-exam-mutation deploy-check staging-mutation
CHECK_GROUP_BFF_WEB := bff-test bff-mutation internal-test-tool-test web-test web-claims-mutation stage-test stage-mutation
CHECK_GROUP_KGE_TOOLS := kge-inventory-test kge-import-test kge-items-test kge-coverage-test kge-tagging-eval-test kge-daofa-import-test kge-chinese-import kge-chinese-mutation kge-dedupe-test kge-dedupe-mutation
CHECK_GROUP_KGE_MODEL := kge-calibrate-test kge-calibrate-mutation kge-irt-test kge-irt-mutation
CHECK_GROUP_KNOWLEDGE := knowledge-test knowledge-mutation kge-compile-eval-test knowledge-material-mutation knowledge-cache-mutation knowledge-semantic-test knowledge-semantic-mutation weknora-probe-test
CHECK_GROUP_INSIGHT := insight-test insight-mutation scheduler-test scheduler-mutation gate1-test gate1-mutation
check-core: $(CHECK_GROUP_PERSONA) $(CHECK_GROUP_CONTRACTS) $(CHECK_GROUP_AYA) $(CHECK_GROUP_ADAPTERS) $(CHECK_GROUP_LEARNING_A) $(CHECK_GROUP_LEARNING_B) $(CHECK_GROUP_BFF_WEB) $(CHECK_GROUP_KGE_TOOLS) $(CHECK_GROUP_KGE_MODEL) $(CHECK_GROUP_KNOWLEDGE) $(CHECK_GROUP_INSIGHT)

check-group-persona: $(CHECK_GROUP_PERSONA)
check-group-contracts: $(CHECK_GROUP_CONTRACTS)
check-group-aya: $(CHECK_GROUP_AYA)
check-group-adapters: $(CHECK_GROUP_ADAPTERS)
check-group-learning-a: $(CHECK_GROUP_LEARNING_A)
check-group-learning-b: $(CHECK_GROUP_LEARNING_B)
check-group-bff-web: $(CHECK_GROUP_BFF_WEB)
check-group-kge-tools: $(CHECK_GROUP_KGE_TOOLS)
check-group-kge-model: $(CHECK_GROUP_KGE_MODEL)
check-group-knowledge: $(CHECK_GROUP_KNOWLEDGE)
check-group-insight: $(CHECK_GROUP_INSIGHT)

# 轻量检查（PR 上跑，.github/workflows/check.yml）：每组去掉名字以 -mutation 结尾的变异测试；db-mutation 也不在其中。
# 变异测试在合并到 main（push）或手动触发时跑全量。本地 make check 仍跑全部；make check-light 跑轻量那一份。
check-group-persona-light: $(filter-out %-mutation,$(CHECK_GROUP_PERSONA))
check-group-contracts-light: $(filter-out %-mutation,$(CHECK_GROUP_CONTRACTS))
check-group-aya-light: $(filter-out %-mutation,$(CHECK_GROUP_AYA))
check-group-adapters-light: $(filter-out %-mutation,$(CHECK_GROUP_ADAPTERS))
check-group-learning-a-light: $(filter-out %-mutation,$(CHECK_GROUP_LEARNING_A))
check-group-learning-b-light: $(filter-out %-mutation,$(CHECK_GROUP_LEARNING_B))
check-group-bff-web-light: $(filter-out %-mutation,$(CHECK_GROUP_BFF_WEB))
check-group-kge-tools-light: $(filter-out %-mutation,$(CHECK_GROUP_KGE_TOOLS))
check-group-kge-model-light: $(filter-out %-mutation,$(CHECK_GROUP_KGE_MODEL))
check-group-knowledge-light: $(filter-out %-mutation,$(CHECK_GROUP_KNOWLEDGE))
check-group-insight-light: $(filter-out %-mutation,$(CHECK_GROUP_INSIGHT))
check-light: $(filter-out %-mutation,$(CHECK_GROUP_PERSONA)) $(filter-out %-mutation,$(CHECK_GROUP_CONTRACTS)) $(filter-out %-mutation,$(CHECK_GROUP_AYA)) $(filter-out %-mutation,$(CHECK_GROUP_ADAPTERS)) $(filter-out %-mutation,$(CHECK_GROUP_LEARNING_A)) $(filter-out %-mutation,$(CHECK_GROUP_LEARNING_B)) $(filter-out %-mutation,$(CHECK_GROUP_BFF_WEB)) $(filter-out %-mutation,$(CHECK_GROUP_KGE_TOOLS)) $(filter-out %-mutation,$(CHECK_GROUP_KGE_MODEL)) $(filter-out %-mutation,$(CHECK_GROUP_KNOWLEDGE)) $(filter-out %-mutation,$(CHECK_GROUP_INSIGHT))

# 变异测试那一份（CI 上只在手动触发 workflow_dispatch 时跑，见 .github/workflows/check.yml）：每组只留名字以 -mutation
# 结尾的目标。与 -light 严格互补——filter-out 与 filter 用的是同一个模式，所以对每一组都有
#   check-group-X-light 的目标 ∪ check-group-X-mutation 的目标 = check-group-X 的目标，
# 一个检查都不会漏掉。persona 组现在没有变异测试，目标为空（make 打印“无需做任何事”并以 0 退出）；
# 以后往 CHECK_GROUP_PERSONA 里加 *-mutation，这里会自动带上，不用改工作流。
# 本地 make check 不变，仍跑全部；make check-mutation 跑变异那一份。
check-group-persona-mutation: $(filter %-mutation,$(CHECK_GROUP_PERSONA))
check-group-contracts-mutation: $(filter %-mutation,$(CHECK_GROUP_CONTRACTS))
check-group-aya-mutation: $(filter %-mutation,$(CHECK_GROUP_AYA))
check-group-adapters-mutation: $(filter %-mutation,$(CHECK_GROUP_ADAPTERS))
check-group-learning-a-mutation: $(filter %-mutation,$(CHECK_GROUP_LEARNING_A))
check-group-learning-b-mutation: $(filter %-mutation,$(CHECK_GROUP_LEARNING_B))
check-group-bff-web-mutation: $(filter %-mutation,$(CHECK_GROUP_BFF_WEB))
check-group-kge-tools-mutation: $(filter %-mutation,$(CHECK_GROUP_KGE_TOOLS))
check-group-kge-model-mutation: $(filter %-mutation,$(CHECK_GROUP_KGE_MODEL))
check-group-knowledge-mutation: $(filter %-mutation,$(CHECK_GROUP_KNOWLEDGE))
check-group-insight-mutation: $(filter %-mutation,$(CHECK_GROUP_INSIGHT))
check-mutation: $(filter %-mutation,$(CHECK_GROUP_PERSONA)) $(filter %-mutation,$(CHECK_GROUP_CONTRACTS)) $(filter %-mutation,$(CHECK_GROUP_AYA)) $(filter %-mutation,$(CHECK_GROUP_ADAPTERS)) $(filter %-mutation,$(CHECK_GROUP_LEARNING_A)) $(filter %-mutation,$(CHECK_GROUP_LEARNING_B)) $(filter %-mutation,$(CHECK_GROUP_BFF_WEB)) $(filter %-mutation,$(CHECK_GROUP_KGE_TOOLS)) $(filter %-mutation,$(CHECK_GROUP_KGE_MODEL)) $(filter %-mutation,$(CHECK_GROUP_KNOWLEDGE)) $(filter %-mutation,$(CHECK_GROUP_INSIGHT))

check: check-core db-mutation
	@echo "check: lint 尚未配置，跳过；其余检查已通过"

# LT-01 部署打包（specs/launch-lt01-deploy-packaging）：镜像与编排的静态校验、开关矩阵、环境变量清单、迁移执行器单测与一次性库上的
# 副本演练、compose 结构校验（约 10 秒，接进 make check）。DEPLOY_BUILD=1 时另加镜像构建与本机合成全栈冒烟（要能拉基础镜像与 docker
# 守护进程，耗时长，只单独跑：make deploy-check DEPLOY_BUILD=1 PYTHON_IMAGE=…@sha256:… NODE_IMAGE=…@sha256:… POSTGRES_IMAGE=…）。
.PHONY: deploy-check staging-mutation iac-validate
deploy-check:
	@CONTRACT_STRICT=$(CONTRACT_STRICT) bash deploy/scripts/deploy-check.sh

# LT-03 IaC 草案（deploy/iac/alicloud）的外部工具检查：terraform（或 tofu）init -backend=false + validate、tflint、checkov、tfsec，
# 只在工具已装好时运行（不联网下载工具与 provider；provider 须在本地镜像目录 IAC_PLUGIN_DIR），缺失时打印“跳过”（退出码 3：
# 非严格模式算通过，CONTRACT_STRICT=1 时失败）。不执行 plan / apply。不依赖工具的静态规则检查 deploy/iac/check_iac.py 随 deploy-check 运行。
# 本机没有这些工具，所以不接进 check（接进 deploy-check 会让严格模式在没有工具的环境里失败）；有工具的 CI 里单独跑：make iac-validate
iac-validate:
	@bash deploy/scripts/iac-validate.sh; rc=$$?; \
	  if [ $$rc -eq 3 ] && [ "$(CONTRACT_STRICT)" != "1" ]; then exit 0; else exit $$rc; fi

# LT-05 告警规则（deploy/observability/alerts.yaml）的 promtool 校验：只在 promtool 已装好时运行（不联网下载），缺失时打印“跳过”
# （退出码 3：非严格算通过，CONTRACT_STRICT=1 时失败）。结构与指标引用的 Python 校验（O01–O06）随 deploy-check 运行；本机没有 promtool，
# 所以不接进 check（同 iac-validate 的做法）；有工具的 CI 里单独跑：make obs-promtool
.PHONY: obs-promtool
obs-promtool:
	@if [ ! -x services/learning/.venv/bin/python ]; then echo "obs-promtool: 跳过（没有 services/learning/.venv）"; \
	  if [ "$(CONTRACT_STRICT)" = "1" ]; then exit 1; else exit 0; fi; fi
	@services/learning/.venv/bin/python -I deploy/observability/check_observability.py deploy/observability --require-promtool; rc=$$?; \
	  if [ $$rc -eq 3 ] && [ "$(CONTRACT_STRICT)" != "1" ]; then exit 0; else exit $$rc; fi

# LT-01 staging 运行环境：学习服务与审核台后端的 staging 启动校验逐条改坏，断言 tests/test_staging_env.py 能发现（纯函数，约 30 秒）
staging-mutation:
	@if $(LEARNING_MISSING); then \
	  echo "staging-mutation: 跳过（没有安装依赖；见 services/learning/README.md）"; \
	  if [ "$(CONTRACT_STRICT)" = "1" ]; then exit 1; else exit 0; fi; fi
	@$(call guard,$(LEARNING_MISSING))cd services/learning && .venv/bin/python scripts/staging_mutations.py

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
	@# 行为事件与探针的编码过禁用推断词表（W13 T26：与字段词典同一处检查；脚本解析 BFF 的 events.v0.yaml，仍放在 BFF 目录）
	$(call run_contract,services/bff/scripts/check_event_lexicon.py)
	$(call run_contract,services/bff/scripts/check_event_lexicon.py --self-test)

# 节点 ID 规范：文档正则、例子、学科前缀与迁移 0011 一致（只用标准库）
kge-id-check:
	$(PYTHON) docs/design/kge/check_node_id.py

# 迁移与 RLS：在一次性的本地 Postgres 上跑 up → down → up 与 RLS / 约束测试（约 3 秒）
# 只连本机临时实例，不连接任何共享或生产库（CLAUDE.md 规则 9）
db-test:
	@PYTHON="$(PYTHON)" bash db/tests/run.sh; rc=$$?; \
	  if [ $$rc -eq 3 ] && [ "$(CONTRACT_STRICT)" != "1" ]; then exit 0; else exit $$rc; fi

# 数据库变异测试：逐条放宽 RLS 或约束，断言测试能发现（并行执行，约 1–2 分钟）
db-mutation:
	@PYTHON="$(PYTHON)" bash db/tests/mutations.sh; rc=$$?; \
	  if [ $$rc -eq 3 ] && [ "$(CONTRACT_STRICT)" != "1" ]; then exit 0; else exit $$rc; fi

# 阿鸦适配器：类型检查 + 单测（契约、验证器、映射、泄露、幂等、HTTP）。端到端在 learning-test 里
# 依赖需先在 adapters/aya 下 npm ci；没装时非严格模式打印“跳过”
adapter-aya-test:
	@if $(AYA_MISSING); then \
	  echo "adapter-aya-test: 跳过（没有安装依赖；cd adapters/aya && npm ci）"; \
	  if [ "$(CONTRACT_STRICT)" = "1" ]; then exit 1; else exit 0; fi; fi
	@$(call guard,$(AYA_MISSING))cd adapters/aya && npx --no-install tsc -p tsconfig.json && echo "adapter-aya-test: 类型检查通过"
	@$(call guard,$(AYA_MISSING))cd adapters/aya && node --no-warnings --test --test-reporter=dot tests/*.test.ts

# 阿鸦适配器冲刺 4-D 四个方法（plan、teach、generate_variation、report）的变异测试：逐条改坏关键规则，断言单测能发现（约 3–4 分钟）
adapter-aya-mutation:
	@if $(AYA_MISSING); then \
	  echo "adapter-aya-mutation: 跳过（没有安装依赖；cd adapters/aya && npm ci）"; \
	  if [ "$(CONTRACT_STRICT)" = "1" ]; then exit 1; else exit 0; fi; fi
	@$(call guard,$(AYA_MISSING))cd adapters/aya && node --no-warnings scripts/mutations.ts; rc=$$?; \
	  if [ $$rc -eq 3 ] && [ "$(CONTRACT_STRICT)" != "1" ]; then exit 0; else exit $$rc; fi

# 知行（道法）适配器（ADP-道法-01，specs/adapter-zhixing-sprint2）：类型检查 + 单测（判分后处理、路由客户端、幂等、禁语与供应商信息扫描）
# 依赖需先在 adapters/zhixing 下 npm ci；没装时非严格模式打印“跳过”
adapter-zhixing-test:
	@if $(ZHIXING_MISSING); then \
	  echo "adapter-zhixing-test: 跳过（没有安装依赖；cd adapters/zhixing && npm ci）"; \
	  if [ "$(CONTRACT_STRICT)" = "1" ]; then exit 1; else exit 0; fi; fi
	@$(call guard,$(ZHIXING_MISSING))cd adapters/zhixing && npx --no-install tsc -p tsconfig.json && echo "adapter-zhixing-test: 类型检查通过"
	@$(call guard,$(ZHIXING_MISSING))cd adapters/zhixing && node --no-warnings --test --test-reporter=dot tests/*.test.ts && echo "adapter-zhixing-test: 单测通过"

# 文砺（语文）适配器（ADP-语文-01，specs/adapter-wenli-sprint2）：类型检查 + 单测（作文后处理、客观题确定性判分、通道加权、plan、路由客户端、幂等、禁语与供应商信息扫描）
# 依赖需先在 adapters/wenli 下 npm ci；没装时非严格模式打印“跳过”
adapter-wenli-test:
	@if $(WENLI_MISSING); then \
	  echo "adapter-wenli-test: 跳过（没有安装依赖；cd adapters/wenli && npm ci）"; \
	  if [ "$(CONTRACT_STRICT)" = "1" ]; then exit 1; else exit 0; fi; fi
	@$(call guard,$(WENLI_MISSING))cd adapters/wenli && npx --no-install tsc -p tsconfig.json && echo "adapter-wenli-test: 类型检查通过"
	@$(call guard,$(WENLI_MISSING))cd adapters/wenli && node --no-warnings --test --test-reporter=dot tests/*.test.ts && echo "adapter-wenli-test: 单测通过"

# 学习服务：写入接口的单测、幂等与核对测试、端到端（学习服务 → 阿鸦适配器 → 本地临时 Postgres，约 10 秒）
# 依赖需先安装（带哈希的锁文件，W13 技术债 T06）：cd services/learning && python3 -m venv .venv &&
#   .venv/bin/pip install --require-hashes -r requirements-dev.lock && .venv/bin/pip install --no-deps -e ../../packages/contracts/python
learning-test:
	@if $(LEARNING_MISSING); then \
	  echo "learning-test: 跳过（没有安装依赖；见 services/learning/README.md）"; \
	  if [ "$(CONTRACT_STRICT)" = "1" ]; then exit 1; else exit 0; fi; fi
	@$(call guard,$(LEARNING_MISSING))cd services/learning && LEARNING_TEST_STRICT=$(if $(filter 1,$(CONTRACT_STRICT)),1,0) .venv/bin/python -m pytest -q

# 编译页面对账（specs/release-gate-sprint4 冲刺 3 段，第 6 节）：托管平台合并记录导出 × kge.content_review 页面审核记录 × staff 对应表。
# 只用合成文件（services/learning/tests/fixtures/pages-reconcile），不连托管平台、不连库：一致通过；删一条审核记录、改一个提交号、
# 高风险只剩一名批准人各报失败；命令行退出码与“受影响页面清单”（held_pages，合并窗口据此跳过）。约 3 秒；也随 learning-test 运行
page-reconcile-test:
	@if $(LEARNING_MISSING); then \
	  echo "page-reconcile-test: 跳过（没有安装依赖；见 services/learning/README.md）"; \
	  if [ "$(CONTRACT_STRICT)" = "1" ]; then exit 1; else exit 0; fi; fi
	@$(call guard,$(LEARNING_MISSING))cd services/learning && .venv/bin/python -m pytest -q -p no:cacheprovider tests/test_review_pages.py && echo "page-reconcile-test: 通过"

# 时政过期下架（specs/release-gate-sprint4 第 9 节，迁移 0043）：每日任务的命令行入口，手动或本机调度执行；不接生产调度（规则 9）。
# 需要 REVIEW_CONSOLE_ENV=dev|test 与 REVIEW_CONSOLE_KGE_DATABASE_URL（只继承 starbuddy_kge 的登录角色，主机只能是本机或 Unix socket，命令里核对）。
# 可选 TODAY=YYYY-MM-DD（缺省北京时间今天）。测试随 learning-test（tests/test_review_console.py）
current-affair-expire:
	@if [ ! -x services/learning/.venv/bin/python ]; then echo "current-affair-expire: 没有安装学习服务依赖（见 services/learning/README.md）"; exit 2; fi
	@cd services/learning && .venv/bin/python -m review_console expire-affairs $(if $(TODAY),--today $(TODAY),)

# 数学“内容版本 + 地图版本”合成发布走查（specs/release-gate-sprint4 冲刺 4 段；第二部分 PR E），约 1 分钟：一次性本地 Postgres 上两名合成教研
# 走完地图版本与内容版本签字发布、准入变更、版本记录导出、阿鸦准入决定导出（review_console.admission_export）、离线重建内容包与 verify，
# 规范化产物与 tools/release-walkthrough/sample 逐字节比对。调用 tools/release-walkthrough 的 walkthrough 目标。
# 产物目录：RELEASE_WALK_OUT=<新目录或空目录>；不给时用 mktemp -d 在 $TMPDIR（缺省 /tmp）下新建 release-walkthrough.XXXXXX。
# 产物目录跑完保留、不自动删除（路径打印在输出里；约 1 MB，主要是重建后的内容包），由人或系统的临时目录清理处理。
# 缺学习服务依赖、node、adapters/aya 的 npm ci 或本机 Postgres 服务端（PGBIN 或 /usr/lib/postgresql/*/bin/initdb）时非严格跳过、严格失败。
RELEASE_WALK_BAD = $(or $(filter-out 0 1,$(words $(RELEASE_WALK_OUT))),$(findstring ',$(RELEASE_WALK_OUT)))
RELEASE_WALK_MISSING = [ ! -x services/learning/.venv/bin/python ] || ! command -v node >/dev/null 2>&1 || [ ! -d adapters/aya/node_modules/ajv ] || \
	  { [ ! -x "$${PGBIN:-/nonexistent}/initdb" ] && ! ls /usr/lib/postgresql/*/bin/initdb >/dev/null 2>&1; }
.PHONY: release-walkthrough
release-walkthrough:
	@$(if $(RELEASE_WALK_BAD),echo "用法：make release-walkthrough [RELEASE_WALK_OUT=<新目录或空目录>]（路径不含空格与单引号）"; exit 2)
	@if $(RELEASE_WALK_MISSING); then \
	  echo "release-walkthrough: 跳过（缺学习服务依赖、node、adapters/aya 的 npm ci 或本机 Postgres 服务端）"; \
	  if [ "$(CONTRACT_STRICT)" = "1" ]; then exit 1; else exit 0; fi; fi
	@$(call guard,$(RELEASE_WALK_MISSING))out='$(RELEASE_WALK_OUT)'; \
	  if [ -z "$$out" ]; then out=$$(mktemp -d "$${TMPDIR:-/tmp}/release-walkthrough.XXXXXX") || exit 1; fi; \
	  echo "release-walkthrough: 产物目录 $$out（跑完保留，不自动删除）"; \
	  $(MAKE) --no-print-directory -C tools/release-walkthrough walkthrough OUT="$$out" && echo "release-walkthrough: 通过"

# 模型路由（冲刺 1-F，specs/model-router-sprint1）：单测与性质测试在 learning-test 里；这里改坏出境过滤、启动校验、熔断等关键检查，断言测试能发现（约 15 秒）
model-router-mutation:
	@if $(LEARNING_MISSING); then \
	  echo "model-router-mutation: 跳过（没有安装依赖；见 services/learning/README.md）"; \
	  if [ "$(CONTRACT_STRICT)" = "1" ]; then exit 1; else exit 0; fi; fi
	@$(call guard,$(LEARNING_MISSING))cd services/learning && .venv/bin/python scripts/model_router_mutations.py

# 真实模型冒烟（LT-02，specs/launch-lt02-model-provider 范围第 6 条）：由人在有凭据的 staging 执行环境里运行，不进 make check、不进 CI。
# 每个不同的路由目标各发一次合成 L0 请求（不重试），只打印元数据；必须显式给 MAX_CALLS（硬上限 12）；pilot、prod、test 一律拒绝。
#   make model-smoke SMOKE_ENV=staging MAX_CALLS=5      步骤见 deploy/config/README.md 第 7 节
.PHONY: model-smoke
model-smoke:
	@if $(LEARNING_MISSING); then echo "model-smoke: 没有安装学习服务依赖（见 services/learning/README.md）"; exit 1; fi
	@if [ -z "$(SMOKE_ENV)" ]; then echo "model-smoke: 必须给 SMOKE_ENV（只收 staging、dev）"; exit 2; fi
	@cd services/learning && .venv/bin/python scripts/model_smoke.py --env "$(SMOKE_ENV)" $(if $(MAX_CALLS),--max-calls "$(MAX_CALLS)")

# 数学自适应诊断（SIE-02a，specs/sie-02a-math-diagnosis）：单测、合成学生回放与性质测试在 learning-test 里；
# 这里把选题规则、状态机、幂等与可见范围的关键检查逐条改坏，断言诊断测试能发现（并行 4 路，约 1 分钟；需要本机 Postgres 与阿鸦依赖）
diagnosis-mutation:
	@if $(LEARNING_MISSING) || $(AYA_MISSING); then \
	  echo "diagnosis-mutation: 跳过（没有安装学习服务或阿鸦适配器依赖）"; \
	  if [ "$(CONTRACT_STRICT)" = "1" ]; then exit 1; else exit 0; fi; fi
	@$(call guard,$(LEARNING_MISSING) || $(AYA_MISSING))services/learning/.venv/bin/python services/learning/scripts/diagnosis_mutations.py; rc=$$?; \
	  if [ $$rc -eq 3 ] && [ "$(CONTRACT_STRICT)" != "1" ]; then exit 0; else exit $$rc; fi

# learning-internal v1 签字后的写入防护（LT-04，specs/launch-lt04-learning-internal-v1）：测试在 learning-test 里；这里把服务身份限定、
# 计量核对（放行状态、方法、重放先于核对、409 码）与用途、证据等级的服务端推导逐条改坏，断言写入路径的测试能发现（并行 4 路，约 2 分钟；
# 需要本机 Postgres）。数据库函数 0058 本身的变异在 db-mutation（DBMUT_FILTER=计量核对）
.PHONY: internal-v1-mutation
internal-v1-mutation:
	@if $(LEARNING_MISSING); then \
	  echo "internal-v1-mutation: 跳过（没有安装学习服务依赖；见 services/learning/README.md）"; \
	  if [ "$(CONTRACT_STRICT)" = "1" ]; then exit 1; else exit 0; fi; fi
	@$(call guard,$(LEARNING_MISSING))services/learning/.venv/bin/python services/learning/scripts/internal_v1_mutations.py; rc=$$?; \
	  if [ $$rc -eq 3 ] && [ "$(CONTRACT_STRICT)" != "1" ]; then exit 0; else exit $$rc; fi

# 错题再练（specs/practice-loop-stage1）：单测、性质测试与库上测试在 learning-test 里；这里把错题判定、闭环、轮次、选题过滤、
# 票据、作答核对与鉴权的关键检查逐条改坏，断言再练测试能发现（纯函数一组不连库；库上一组每个变异起一个一次性 Postgres，并行 4 路）
.PHONY: practice-mutation
practice-mutation:
	@if $(LEARNING_MISSING); then \
	  echo "practice-mutation: 跳过（没有安装学习服务依赖；见 services/learning/README.md）"; \
	  if [ "$(CONTRACT_STRICT)" = "1" ]; then exit 1; else exit 0; fi; fi
	@$(call guard,$(LEARNING_MISSING))services/learning/.venv/bin/python services/learning/scripts/practice_mutations.py; rc=$$?; \
	  if [ $$rc -eq 3 ] && [ "$(CONTRACT_STRICT)" != "1" ]; then exit 0; else exit $$rc; fi

# 复测与验证题交付（KGE-05，specs/retest-delivery-kge05）：把任务 → 条目、用途、到期、自述验证道数、8 周复测点开关、票据前缀与核对、
# 作答确认、只出新族、鉴权逐条改坏，断言复测测试能发现（纯函数组不连库；库上组每个变异一个一次性 Postgres）
.PHONY: retest-mutation
retest-mutation:
	@if $(LEARNING_MISSING); then \
	  echo "retest-mutation: 跳过（没有安装学习服务依赖；见 services/learning/README.md）"; \
	  if [ "$(CONTRACT_STRICT)" = "1" ]; then exit 1; else exit 0; fi; fi
	@$(call skip_rest,$(LEARNING_MISSING))services/learning/.venv/bin/python services/learning/scripts/retest_mutations.py; rc=$$?; \
	  if [ $$rc -eq 3 ] && [ "$(CONTRACT_STRICT)" != "1" ]; then exit 0; else exit $$rc; fi

# 今日计划跨科编排（PLT-11，specs/today-plan-orchestration-plt11）：把去重、排序、预算截断、来源失败降级、只取 due、只读、开关、鉴权与
# 响应字段逐条改坏，断言今日编排测试能发现（纯函数组不连库；库上组每个变异一个一次性 Postgres）
.PHONY: today-mutation
today-mutation:
	@if $(LEARNING_MISSING); then \
	  echo "today-mutation: 跳过（没有安装学习服务依赖；见 services/learning/README.md）"; \
	  if [ "$(CONTRACT_STRICT)" = "1" ]; then exit 1; else exit 0; fi; fi
	@$(call skip_rest,$(LEARNING_MISSING))services/learning/.venv/bin/python services/learning/scripts/today_mutations.py; rc=$$?; \
	  if [ $$rc -eq 3 ] && [ "$(CONTRACT_STRICT)" != "1" ]; then exit 0; else exit $$rc; fi

# 模考限时整卷第一步（PLT-18，specs/mock-exam-plt18）：卷清单校验、限时状态机与会话票据、整卷汇总的纯函数（learning/mock_exam/，
# 不注册路由、不连库）。mock-exam-test 只跑这三份测试（也随 learning-test 运行）；mock-exam-mutation 把截止边界、宽限、交卷后作答、
# 重复作答取最后一次、票据签名与换卷换学生、分值和、待复核与暂定、缺溯源字段等逐条改坏，断言测试能发现（不连库，约 1 分钟）
.PHONY: mock-exam-test mock-exam-mutation
mock-exam-test:
	@if $(LEARNING_MISSING); then \
	  echo "mock-exam-test: 跳过（没有安装学习服务依赖；见 services/learning/README.md）"; \
	  if [ "$(CONTRACT_STRICT)" = "1" ]; then exit 1; else exit 0; fi; fi
	@$(call skip_rest,$(LEARNING_MISSING))cd services/learning && .venv/bin/python -m pytest -q -p no:cacheprovider \
	  tests/test_mock_exam_manifest.py tests/test_mock_exam_session.py tests/test_mock_exam_summary.py && echo "mock-exam-test: 通过"

mock-exam-mutation:
	@if $(LEARNING_MISSING); then \
	  echo "mock-exam-mutation: 跳过（没有安装学习服务依赖；见 services/learning/README.md）"; \
	  if [ "$(CONTRACT_STRICT)" = "1" ]; then exit 1; else exit 0; fi; fi
	@$(call skip_rest,$(LEARNING_MISSING))services/learning/.venv/bin/python services/learning/scripts/mock_exam_mutations.py; rc=$$?; \
	  if [ $$rc -eq 3 ] && [ "$(CONTRACT_STRICT)" != "1" ]; then exit 0; else exit $$rc; fi

# 调度器第一步（PLT-13，specs/scheduler-plt13）：services/scheduler 按时间表以子进程调用已有入口（insight.scheduled、insight.jobs、
# learning.jobs、review_console），环境变量按身份白名单传递，pg_try_advisory_lock 互斥，结构化日志与 alert 级别；不建表、不新增授权。
# 测试：时间表纯函数（时区、月末、周一、与夏令时无关）、配置校验、运行器（白名单、超时、退出码映射、清洗）、命令行启动核对，
# 以及一次性 Postgres 上每个任务 --once 的端到端效果、重复运行幂等与两个实例互斥（子进程用洞察服务与学习服务的 venv）。
# 依赖需先安装（锁文件与洞察服务相同，不新增包）：cd services/scheduler && python3 -m venv .venv && .venv/bin/pip install --require-hashes -r requirements-dev.lock
SCHEDULER_MISSING = [ ! -x services/scheduler/.venv/bin/python ] || ! services/scheduler/.venv/bin/python -c "import asyncpg, yaml, pytest, fastapi" 2>/dev/null
.PHONY: scheduler-test scheduler-mutation
scheduler-test:
	@if $(SCHEDULER_MISSING); then \
	  echo "scheduler-test: 跳过（没有安装调度器依赖；见 services/scheduler/README.md）"; \
	  if [ "$(CONTRACT_STRICT)" = "1" ]; then exit 1; else exit 0; fi; fi
	@$(call guard,$(SCHEDULER_MISSING))cd services/scheduler && SCHEDULER_TEST_STRICT=$(if $(filter 1,$(CONTRACT_STRICT)),1,0) PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -rs && echo "scheduler-test: 通过"

# 变异测试：把时间表计算、配置校验、环境白名单、启动核对、超时、退出码映射、告警、计数清洗、咨询锁与常驻循环逐条改坏，
# 断言调度器的测试能发现（纯函数组不连库；库上组每个变异一个一次性 Postgres）。没有依赖或 Postgres 时退出码 3（非严格跳过、严格失败）
scheduler-mutation:
	@if $(SCHEDULER_MISSING); then \
	  echo "scheduler-mutation: 跳过（没有安装调度器依赖；见 services/scheduler/README.md）"; \
	  if [ "$(CONTRACT_STRICT)" = "1" ]; then exit 1; else exit 0; fi; fi
	@$(call skip_rest,$(SCHEDULER_MISSING))services/scheduler/.venv/bin/python services/scheduler/scripts/mutations.py; rc=$$?; \
	  if [ $$rc -eq 3 ] && [ "$(CONTRACT_STRICT)" != "1" ]; then exit 0; else exit $$rc; fi

# 适配器客户端（learning/adapter_client.py）：把重试、错误归类、编码转发、严格 JSON、202、超时、总预算与 5 MB 上限逐条改坏，断言客户端测试能发现（不连库，并行 8 路，约 25 秒）
adapter-client-mutation:
	@if $(LEARNING_MISSING); then \
	  echo "adapter-client-mutation: 跳过（没有安装学习服务依赖；见 services/learning/README.md）"; \
	  if [ "$(CONTRACT_STRICT)" = "1" ]; then exit 1; else exit 0; fi; fi
	@$(call guard,$(LEARNING_MISSING))services/learning/.venv/bin/python services/learning/scripts/adapter_client_mutations.py; rc=$$?; \
	  if [ $$rc -eq 3 ] && [ "$(CONTRACT_STRICT)" != "1" ]; then exit 0; else exit $$rc; fi

# BFF 计量模块（冲刺 2-E）：类型检查 + 单测（共享规范化向量、指纹、学习服务响应分类）+ Gate 0 六类场景集成测试
# 集成测试在 db/tests/pg-ephemeral.sh 起的一次性 Postgres 上跑（多连接并发、断线），约 5 秒
# 依赖需先在 services/bff 下 npm ci；没装时非严格模式打印“跳过”
bff-test:
	@if $(BFF_MISSING); then \
	  echo "bff-test: 跳过（没有安装依赖；cd services/bff && npm ci）"; \
	  if [ "$(CONTRACT_STRICT)" = "1" ]; then exit 1; else exit 0; fi; fi
	@$(call guard,$(BFF_MISSING))cd services/bff && npx --no-install tsc -p tsconfig.json && echo "bff-test: 类型检查通过"
	@$(call guard,$(BFF_MISSING))cd services/bff && bash ../../db/tests/pg-ephemeral.sh -- node --no-warnings --test --test-concurrency=1 --test-reporter=dot tests/*.test.ts; rc=$$?; \
	  if [ $$rc -eq 0 ]; then echo "bff-test: 通过（单测 + Gate 0 六类场景集成测试）"; fi; \
	  if [ $$rc -eq 3 ] && [ "$(CONTRACT_STRICT)" != "1" ]; then exit 0; else exit $$rc; fi

# LT-06 内部成员测试的白名单运营工具（tools/internal-test，specs/launch-lt06-internal-test-ops 范围第 1 条）：类型检查 + 单测与端到端
# （生成、按批次作废、审计摘要；与 BFF StagingOtp 互验）。借用 services/bff 的开发依赖（不另装依赖），约 3 秒；
# 变异测试在 bff-mutation 的“内测工具：”一组（BFFMUT_FILTER=内测工具 只跑这一组）
.PHONY: internal-test-tool-test
internal-test-tool-test:
	@if $(BFF_MISSING); then \
	  echo "internal-test-tool-test: 跳过（没有安装依赖；cd services/bff && npm ci）"; \
	  if [ "$(CONTRACT_STRICT)" = "1" ]; then exit 1; else exit 0; fi; fi
	@$(call guard,$(BFF_MISSING))cd tools/internal-test && ../../services/bff/node_modules/.bin/tsc -p tsconfig.json && echo "internal-test-tool-test: 类型检查通过"
	@$(call guard,$(BFF_MISSING))cd tools/internal-test && node --no-warnings --test --test-reporter=dot tests/*.test.ts && echo "internal-test-tool-test: 通过"

# BFF 集成测试的变异测试：去掉计量函数里的加锁或并发判断，断言 Gate 0 集成测试能发现（约 10 秒）
bff-mutation:
	@if $(BFF_MISSING); then \
	  echo "bff-mutation: 跳过（没有安装依赖；cd services/bff && npm ci）"; \
	  if [ "$(CONTRACT_STRICT)" = "1" ]; then exit 1; else exit 0; fi; fi
	@$(call guard,$(BFF_MISSING))bash services/bff/tests/mutations.sh; rc=$$?; \
	  if [ $$rc -eq 3 ] && [ "$(CONTRACT_STRICT)" != "1" ]; then exit 0; else exit $$rc; fi

# 契约一致性测试（冲刺 1-A）：共享核对库单测；同一套测试跑参考假适配器（同步、异步）与阿鸦适配器（约 10 秒）
# 覆盖率报告写到 $(CONFORMANCE_REPORT_DIR)/conformance-<学科>.json，供 Gate 0 统计
# 需要 packages/contracts/requirements-dev.txt；阿鸦部分还需要 adapters/aya 已 npm ci
CONFORMANCE_REPORT_DIR ?= $(or $(TMPDIR),/tmp)
conformance:
	$(call run_contract,-m pytest -q -p no:cacheprovider packages/contracts/python/tests)
	@# v1.0 已于 2026-10-09 签字（specs/contract-v1.0-signoff）：共享库与三科缺省版本都是 1.0.0，不再需要候选版开关。
	@# 迁移期 0.9.0 仍保留（迁移期终点另定）：下面凡是按 0.9.0 跑的行都显式写 STARBUDDY_CONTRACT_VERSION=0.9.0（适配器另加
	@# --contract-version 0.9.0），不再依赖缺省值；0.9.0 各行只作回归、不出覆盖率报告。Gate 0 覆盖率报告（--report）一律取已签字的
	@# 1.0.0 那一遍（参考适配器、阿鸦、知行、文砺），文件名与签字前相同
	@STARBUDDY_CONTRACT_VERSION=0.9.0 $(PYTHON) packages/contracts/conformance/cli.py --reference --quiet; rc=$$?; \
	  if [ $$rc -eq 3 ] && [ "$(CONTRACT_STRICT)" != "1" ]; then exit 0; else exit $$rc; fi
	@STARBUDDY_CONTRACT_VERSION=0.9.0 $(PYTHON) packages/contracts/conformance/cli.py --reference --reference-async --quiet; rc=$$?; \
	  if [ $$rc -eq 3 ] && [ "$(CONTRACT_STRICT)" != "1" ]; then exit 0; else exit $$rc; fi
	@# v1.0：参考适配器按 1.0.0 再跑一遍（迁移期两版并存，specs/plugin-api-v1.0/spec.md），覆盖率报告取这一遍；三科也是两版都跑（见下）
	@STARBUDDY_CONTRACT_VERSION=1.0.0 $(PYTHON) packages/contracts/conformance/cli.py --reference --quiet --report $(CONFORMANCE_REPORT_DIR)/conformance-reference.json; rc=$$?; \
	  if [ $$rc -eq 3 ] && [ "$(CONTRACT_STRICT)" != "1" ]; then exit 0; else exit $$rc; fi
	@STARBUDDY_CONTRACT_VERSION=1.0.0 $(PYTHON) packages/contracts/conformance/cli.py --reference --reference-async --quiet; rc=$$?; \
	  if [ $$rc -eq 3 ] && [ "$(CONTRACT_STRICT)" != "1" ]; then exit 0; else exit $$rc; fi
	@# 下面三科各有一个跳过分支，只结束该科的几行（guard），其余部分照跑
	@if $(AYA_MISSING); then \
	  echo "conformance: 阿鸦部分跳过（没有安装依赖；cd adapters/aya && npm ci）"; \
	  if [ "$(CONTRACT_STRICT)" = "1" ]; then exit 1; else exit 0; fi; fi
	@# 阿鸦按 0.9.0：plan、teach、generate_variation、report 只按 v1.0 实现（specs/adapter-aya-methods-sprint4），0.9.0 下声明未实现，
	@# 所以这一遍用去掉这四个方法用例的夹具副本 fixtures.v0.9.yaml（与 fixtures.yaml 的漂移由 adapters/aya/tests/methods-v1.test.ts 断言）
	@$(call guard,$(AYA_MISSING))STARBUDDY_CONTRACT_VERSION=0.9.0 $(PYTHON) packages/contracts/conformance/cli.py --quiet --adapter-cmd "node --no-warnings src/main.ts --contract-version 0.9.0" --adapter-cwd adapters/aya --token-var AYA_ADAPTER_SERVICE_TOKEN --fixtures adapters/aya/conformance/fixtures.v0.9.yaml; rc=$$?; \
	  if [ $$rc -eq 3 ] && [ "$(CONTRACT_STRICT)" != "1" ]; then exit 0; else exit $$rc; fi
	@# 阿鸦按 1.0.0（specs/plugin-api-v1.0-step2/spec.md 第 3 条）：请求按 context.contract_version 应答；能力声明缺省已报 1.0.0，
	@# 这里仍显式传启动参数（cli.py 不向适配器进程透传环境变量）。冲刺 4-D 起覆盖率报告取这一遍（阿鸦 6/6，spec 验收标准）
	@$(call guard,$(AYA_MISSING))STARBUDDY_CONTRACT_VERSION=1.0.0 $(PYTHON) packages/contracts/conformance/cli.py --quiet --adapter-cmd "node --no-warnings src/main.ts --contract-version 1.0.0" --adapter-cwd adapters/aya --token-var AYA_ADAPTER_SERVICE_TOKEN --fixtures adapters/aya/conformance/fixtures.yaml --report $(CONFORMANCE_REPORT_DIR)/conformance-aya.json; rc=$$?; \
	  if [ $$rc -eq 3 ] && [ "$(CONTRACT_STRICT)" != "1" ]; then exit 0; else exit $$rc; fi
	@# 知行（ADP-道法-01）：conformance/harness.ts 先起模型路由接口的本机桩，再加载 src/main.ts；v0.9 与 v1.0 都跑
	@if $(ZHIXING_MISSING); then \
	  echo "conformance: 知行部分跳过（没有安装依赖；cd adapters/zhixing && npm ci）"; \
	  if [ "$(CONTRACT_STRICT)" = "1" ]; then exit 1; else exit 0; fi; fi
	@$(call guard,$(ZHIXING_MISSING))STARBUDDY_CONTRACT_VERSION=0.9.0 $(PYTHON) packages/contracts/conformance/cli.py --quiet --adapter-cmd "node --no-warnings conformance/harness.ts --contract-version 0.9.0" --adapter-cwd adapters/zhixing --token-var ZHIXING_ADAPTER_SERVICE_TOKEN --fixtures adapters/zhixing/conformance/fixtures.yaml; rc=$$?; \
	  if [ $$rc -eq 3 ] && [ "$(CONTRACT_STRICT)" != "1" ]; then exit 0; else exit $$rc; fi
	@$(call guard,$(ZHIXING_MISSING))STARBUDDY_CONTRACT_VERSION=1.0.0 $(PYTHON) packages/contracts/conformance/cli.py --quiet --adapter-cmd "node --no-warnings conformance/harness.ts --contract-version 1.0.0" --adapter-cwd adapters/zhixing --token-var ZHIXING_ADAPTER_SERVICE_TOKEN --fixtures adapters/zhixing/conformance/fixtures.yaml --report $(CONFORMANCE_REPORT_DIR)/conformance-zhixing.json; rc=$$?; \
	  if [ $$rc -eq 3 ] && [ "$(CONTRACT_STRICT)" != "1" ]; then exit 0; else exit $$rc; fi
	@# 文砺（ADP-语文-01）：同知行，harness.ts 先起路由桩；v0.9 与 v1.0 都跑
	@if $(WENLI_MISSING); then \
	  echo "conformance: 文砺部分跳过（没有安装依赖；cd adapters/wenli && npm ci）"; \
	  if [ "$(CONTRACT_STRICT)" = "1" ]; then exit 1; else exit 0; fi; fi
	@$(call guard,$(WENLI_MISSING))STARBUDDY_CONTRACT_VERSION=0.9.0 $(PYTHON) packages/contracts/conformance/cli.py --quiet --adapter-cmd "node --no-warnings conformance/harness.ts --contract-version 0.9.0" --adapter-cwd adapters/wenli --token-var WENLI_ADAPTER_SERVICE_TOKEN --fixtures adapters/wenli/conformance/fixtures.yaml; rc=$$?; \
	  if [ $$rc -eq 3 ] && [ "$(CONTRACT_STRICT)" != "1" ]; then exit 0; else exit $$rc; fi
	@$(call guard,$(WENLI_MISSING))STARBUDDY_CONTRACT_VERSION=1.0.0 $(PYTHON) packages/contracts/conformance/cli.py --quiet --adapter-cmd "node --no-warnings conformance/harness.ts --contract-version 1.0.0" --adapter-cwd adapters/wenli --token-var WENLI_ADAPTER_SERVICE_TOKEN --fixtures adapters/wenli/conformance/fixtures.yaml --report $(CONFORMANCE_REPORT_DIR)/conformance-wenli.json; rc=$$?; \
	  if [ $$rc -eq 3 ] && [ "$(CONTRACT_STRICT)" != "1" ]; then exit 0; else exit $$rc; fi
	@# v1.1 候选版（PLT-12 present_item，specs/present-item-contract）：参考适配器（同步、异步）、阿鸦、知行按 1.1.0 再跑一遍；夹具里
	@# since: 1.1.0 的题面用例只在这一遍跑。覆盖率报告另写 conformance-<学科>-v1.1.json（Gate 0 仍取上面 1.0.0 那一遍）。文砺不在 PLT-12
	@STARBUDDY_CONTRACT_VERSION=1.1.0 STARBUDDY_ALLOW_CANDIDATE_CONTRACT=1 $(PYTHON) packages/contracts/conformance/cli.py --reference --quiet --report $(CONFORMANCE_REPORT_DIR)/conformance-reference-v1.1.json; rc=$$?; \
	  if [ $$rc -eq 3 ] && [ "$(CONTRACT_STRICT)" != "1" ]; then exit 0; else exit $$rc; fi
	@STARBUDDY_CONTRACT_VERSION=1.1.0 STARBUDDY_ALLOW_CANDIDATE_CONTRACT=1 $(PYTHON) packages/contracts/conformance/cli.py --reference --reference-async --quiet; rc=$$?; \
	  if [ $$rc -eq 3 ] && [ "$(CONTRACT_STRICT)" != "1" ]; then exit 0; else exit $$rc; fi
	@# 阿鸦这一遍带题面叠加文件（合成题面，--stem-overlay；只在候选版开关打开时读），夹具里的 complete 题面用例靠它。
	@# 缺依赖时：严格模式（CONTRACT_STRICT=1）报错，否则打印“跳过”（审查建议 14：不静默跳过）
	@if ! $(AYA_MISSING); then \
	  STARBUDDY_CONTRACT_VERSION=1.1.0 STARBUDDY_ALLOW_CANDIDATE_CONTRACT=1 $(PYTHON) packages/contracts/conformance/cli.py --quiet --adapter-cmd "node --no-warnings src/main.ts --contract-version 1.1.0 --allow-candidate-contract --stem-overlay conformance/stems.synthetic.json" --adapter-cwd adapters/aya --token-var AYA_ADAPTER_SERVICE_TOKEN --fixtures adapters/aya/conformance/fixtures.yaml --report $(CONFORMANCE_REPORT_DIR)/conformance-aya-v1.1.json; rc=$$?; \
	  if [ $$rc -eq 3 ] && [ "$(CONTRACT_STRICT)" != "1" ]; then exit 0; else exit $$rc; fi; \
	else echo "conformance: 阿鸦 v1.1 部分跳过（没有安装依赖；cd adapters/aya && npm ci）"; \
	  if [ "$(CONTRACT_STRICT)" = "1" ]; then exit 1; fi; fi
	@if ! $(ZHIXING_MISSING); then \
	  STARBUDDY_CONTRACT_VERSION=1.1.0 STARBUDDY_ALLOW_CANDIDATE_CONTRACT=1 $(PYTHON) packages/contracts/conformance/cli.py --quiet --adapter-cmd "node --no-warnings conformance/harness.ts --contract-version 1.1.0 --allow-candidate-contract" --adapter-cwd adapters/zhixing --token-var ZHIXING_ADAPTER_SERVICE_TOKEN --fixtures adapters/zhixing/conformance/fixtures.yaml --report $(CONFORMANCE_REPORT_DIR)/conformance-zhixing-v1.1.json; rc=$$?; \
	  if [ $$rc -eq 3 ] && [ "$(CONTRACT_STRICT)" != "1" ]; then exit 0; else exit $$rc; fi; \
	else echo "conformance: 知行 v1.1 部分跳过（没有安装依赖；cd adapters/zhixing && npm ci）"; \
	  if [ "$(CONTRACT_STRICT)" = "1" ]; then exit 1; fi; fi

# 一致性测试的变异测试：参考假适配器逐一注入各类违约，断言每次都由指定的测试发现。契约版本一律显式指定：0.9.0 与 1.0.0
# 两版都跑基线、每类违约两版都要被发现；只在某版有意义的违约（reference_adapter.FAULT_CONTRACT，目前是 1.1.0 的题面违约）只跑那一版（约 2 分钟）
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
# 用花括号成组：原先 `! [ a ] && [ b ]` 里的 ! 只取反前一半，两个都没装时不跳过（T04 一并修）
WEB_DEPS_OK = { [ -d apps/web/node_modules/next ] && [ -d services/bff/node_modules/pg ]; }
WEB_MISSING = ! $(WEB_DEPS_OK)
# 前端单测里的禁语扫描与禁用推断词表检查直接调用 python3（要 PyYAML）：把 make 指定的 PYTHON 所在目录放到 PATH 最前，
# 让 python3 指向它（CI 是 .venv-contracts），不依赖系统 python3 恰好装了 PyYAML
PY_ON_PATH = $(if $(findstring /,$(PYTHON)),PATH="$(abspath $(dir $(PYTHON))):$$PATH" ,)
web-test:
	@if $(WEB_MISSING); then \
	  echo "web-test: 跳过（没有安装依赖；cd apps/web && npm ci，cd services/bff && npm ci）"; \
	  if [ "$(CONTRACT_STRICT)" = "1" ]; then exit 1; else exit 0; fi; fi
	@$(call guard,$(WEB_MISSING))cd apps/web && node scripts/license-check.mjs
	@$(call guard,$(WEB_MISSING))cd apps/web && NEXT_TELEMETRY_DISABLED=1 npx --no-install next build > .next-build.log 2>&1 || { tail -30 .next-build.log; rm -f .next-build.log; exit 1; }; \
	  rm -f .next-build.log; echo "web-test: next build 通过（含类型检查）"
	@$(call guard,$(WEB_MISSING))if grep -rlE 'x-dev-staff-id|sb_dev_staff|SYNTHETIC_STAFF|5f0e0000-0000-4000-8000-0000000000e[12]' apps/web/.next --exclude-dir=cache >/dev/null 2>&1; then \
	  echo "web-test: 构建产物里还有开发 staff 身份桩"; exit 1; fi; echo "web-test: 构建产物里没有开发 staff 身份桩"
	@$(call guard,$(WEB_MISSING))if grep -rlE 'x-dev-account-id|sb_dev_account|STARBUDDY_DEV_AUTH|/dev/login|/dev/session|/dev/star-glyphs|SYNTHETIC_ACCOUNTS' apps/web/.next --exclude-dir=cache >/dev/null 2>&1; then \
	  echo "web-test: 生产构建产物里还有开发身份桩或开发专用页面（4-H H2）"; exit 1; fi; echo "web-test: 生产构建产物里没有开发身份桩与开发专用页面"
	@$(call guard,$(WEB_MISSING))if grep -rlE 'data:(image|font)/' apps/web/.next/static apps/web/.next/server --include='*.js' --include='*.css' --include='*.html' >/dev/null 2>&1; then \
	  echo "web-test: 构建产物里有 data: 图片或字体（CSP 的 img-src / font-src 不许 data:，T46）"; exit 1; fi; echo "web-test: 构建产物里没有 data: 图片与字体"
	@$(call guard,$(WEB_MISSING))cd apps/web && $(PY_ON_PATH)node --no-warnings --test --test-reporter=dot tests/*.test.ts && echo "web-test: 单测与静态检查通过"
	@$(call guard,$(WEB_MISSING))bash apps/web/scripts/e2e.sh --grep-invert @visual --reporter=dot; rc=$$?; \
	  if [ $$rc -eq 0 ]; then echo "web-test: 端到端通过"; fi; \
	  if [ $$rc -eq 3 ] && [ "$(CONTRACT_STRICT)" != "1" ]; then exit 0; else exit $$rc; fi

# 陈述—判断一致性爬取（T21，specs/tech-debt-batch2）的变异测试：每条变异都重新构建端到端前端，单条几分钟；只在变异组里（CI 手动触发）。
web-claims-mutation:
	@if $(WEB_MISSING); then echo "web-claims-mutation: 跳过（没有安装依赖）"; \
	  if [ "$(CONTRACT_STRICT)" = "1" ]; then exit 1; else exit 0; fi; fi
	@$(call guard,$(WEB_MISSING))bash apps/web/scripts/claims-mutation.sh; rc=$$?; \
	  if [ $$rc -eq 3 ] && [ "$(CONTRACT_STRICT)" != "1" ]; then exit 0; else exit $$rc; fi

# 视觉回归（@visual）：与已提交的基线比较，差异像素 ≤ 0.1%。基线要在固定版本的容器与浏览器里生成，
# 首次提交需视觉负责人签字（lessons）。暂不进 make check 与 CI（spec 待人确认 #11）；更新基线：加 UPDATE=1
web-visual:
	@if $(WEB_MISSING); then echo "web-visual: 跳过（没有安装依赖）"; \
	  if [ "$(CONTRACT_STRICT)" = "1" ]; then exit 1; else exit 0; fi; fi
	@# e2e.sh 自己做测试构建（.next-e2e），这里不再先做一次生产构建（W13 T26，specs/behavior-events-sprint2/lessons.md 第 49 行）
	@$(call guard,$(WEB_MISSING))bash apps/web/scripts/e2e.sh --grep @visual $(if $(filter 1,$(UPDATE)),--update-snapshots,)

# KGE-00 知识资产盘点（specs/kge-00-asset-inventory/spec.md）。
# 测试只用合成夹具（CI 没有 legacy 副本）：类型检查、图检查正反例、夹具盘点、只读 SQL 的静态检查与本机临时库运行
kge-inventory-test:
	@if $(KGE_INVENTORY_MISSING); then \
	  echo "kge-inventory-test: 跳过（没有安装依赖；cd tools/kge-inventory && npm ci）"; \
	  if [ "$(CONTRACT_STRICT)" = "1" ]; then exit 1; else exit 0; fi; fi
	@$(call guard,$(KGE_INVENTORY_MISSING))cd tools/kge-inventory && npx --no-install tsc -p tsconfig.json && echo "kge-inventory-test: 类型检查通过"
	@$(call guard,$(KGE_INVENTORY_MISSING))cd tools/kge-inventory && node --no-warnings --test --test-reporter=dot tests/*.test.ts
	@$(call guard,$(KGE_INVENTORY_MISSING))bash tools/kge-inventory/tests/sql-run.sh; rc=$$?; \
	  if [ $$rc -eq 3 ] && [ "$(CONTRACT_STRICT)" != "1" ]; then exit 0; else exit $$rc; fi

# KGE-01 骨架导入（specs/kge-01-skeleton-import）：生成阿鸦 fun 专题的草稿地图版本种子；不连库，SQL 由人在演练库执行
# 教研补齐工作单后：make kge-import KGE_OVERRIDES=path/to/overrides.json
kge-import:
	@cd tools/kge-inventory && node --no-warnings src/import/main.ts --aya ../../legacy/aya --topic fun --version 0.1.0 \
	  $(if $(KGE_OVERRIDES),--overrides $(abspath $(KGE_OVERRIDES))) --out ../../db/seeds/kge/aya-fun-0.1.0

# KGE-02 题库导入与打标（specs/kge-02-atoms-tagging）：类型检查（借用 kge-inventory 的 tsc）+ 单测 + 种子的库上验证（一次性 Postgres）
kge-items-test:
	@if $(KGE_INVENTORY_MISSING); then \
	  echo "kge-items-test: 跳过（没有安装依赖；cd tools/kge-inventory && npm ci）"; \
	  if [ "$(CONTRACT_STRICT)" = "1" ]; then exit 1; else exit 0; fi; fi
	@$(call guard,$(KGE_INVENTORY_MISSING))cd tools/kge-import && ../kge-inventory/node_modules/.bin/tsc -p tsconfig.json && echo "kge-items-test: 类型检查通过"
	@$(call guard,$(KGE_INVENTORY_MISSING))cd tools/kge-import && node --no-warnings --test --test-reporter=dot tests/*.test.ts && echo "kge-items-test: 单测通过"
	@$(call guard,$(KGE_INVENTORY_MISSING))bash tools/kge-import/tests/items-db.sh; rc=$$?; \
	  if [ $$rc -eq 3 ] && [ "$(CONTRACT_STRICT)" != "1" ]; then exit 0; else exit $$rc; fi

# KGE-02 覆盖矩阵报表：单测 + 只读 SQL 与报表在一次性库上逐字节核对
kge-coverage-test:
	@if $(KGE_COVERAGE_MISSING); then \
	  echo "kge-coverage-test: 跳过（没有安装依赖；cd tools/kge-coverage && npm ci）"; \
	  if [ "$(CONTRACT_STRICT)" = "1" ]; then exit 1; else exit 0; fi; fi
	@$(call guard,$(KGE_COVERAGE_MISSING))cd tools/kge-coverage && npx --no-install tsc -p tsconfig.json && node --no-warnings --test --test-reporter=dot tests/*.test.ts && echo "kge-coverage-test: 单测通过"
	@$(call guard,$(KGE_COVERAGE_MISSING))bash tools/kge-coverage/tests/coverage-db.sh; rc=$$?; \
	  if [ $$rc -eq 3 ] && [ "$(CONTRACT_STRICT)" != "1" ]; then exit 0; else exit $$rc; fi

# KGE-02 打标评测脚本（kappa、Top-1）：只在合成夹具上证明计算正确；教研金标到位后另用 --enforce 验收
kge-tagging-eval-test:
	@cd evals/kge-tagging && PYTHONDONTWRITEBYTECODE=1 $(if $(findstring /,$(PYTHON)),$(abspath $(PYTHON)),$(PYTHON)) -m unittest discover -s tests -q && echo "kge-tagging-eval-test: 通过"

# 单测（tests/import.test.ts）随 kge-inventory-test 的 tests/*.test.ts 一起跑；这里只跑库上验证。
# 道法部分（冲刺 4-K）作为前置目标一起跑（同一次 make 里不重复执行）
kge-import-test: kge-daofa-import-test
	@if $(KGE_INVENTORY_MISSING); then \
	  echo "kge-import-test: 跳过（没有安装依赖；cd tools/kge-inventory && npm ci）"; \
	  if [ "$(CONTRACT_STRICT)" = "1" ]; then exit 1; else exit 0; fi; fi
	@$(call guard,$(KGE_INVENTORY_MISSING))bash tools/kge-inventory/tests/import-db.sh; rc=$$?; \
	  if [ $$rc -eq 3 ] && [ "$(CONTRACT_STRICT)" != "1" ]; then exit 0; else exit $$rc; fi

# 冲刺 4-K 道法切片（specs/kge-daofa-slice-sprint4）：观点地图导入器、页码抽样核验、采分点入原子层、时政（tools/kge-import/src/daofa）。
# 类型检查（借用 kge-inventory 的 tsc）+ 单测（tests/daofa/*.test.ts）+ 一次性 Postgres 上的端到端（daofa-db.sh：种子两次逐字节相同、
# 合成挡板、草稿版本、采分点只新增、时政白名单与 pending、库上快照 = 离线快照、发布后只读角色看不到过期时政）+ 页码回填演示
# （page-backfill-demo.sh：低于阈值不能标 verified；达标时现有表因 projection_verified_by 写不进 verified，见 lessons 待人确认）。
# 缺 tsc 或 Postgres 时非严格跳过、严格失败
kge-daofa-import-test:
	@if $(KGE_INVENTORY_MISSING); then \
	  echo "kge-daofa-import-test: 跳过（没有安装依赖；cd tools/kge-inventory && npm ci）"; \
	  if [ "$(CONTRACT_STRICT)" = "1" ]; then exit 1; else exit 0; fi; fi
	@$(call guard,$(KGE_INVENTORY_MISSING))cd tools/kge-import && ../kge-inventory/node_modules/.bin/tsc -p tsconfig.json && echo "kge-daofa-import-test: 类型检查通过"
	@$(call guard,$(KGE_INVENTORY_MISSING))cd tools/kge-import && node --no-warnings --test --test-reporter=dot tests/daofa/*.test.ts && echo "kge-daofa-import-test: 单测通过"
	@$(call guard,$(KGE_INVENTORY_MISSING))bash tools/kge-import/tests/daofa/daofa-db.sh; rc=$$?; \
	  if [ $$rc -eq 3 ] && [ "$(CONTRACT_STRICT)" != "1" ]; then exit 0; else exit $$rc; fi
	@$(call guard,$(KGE_INVENTORY_MISSING))bash tools/kge-import/tests/daofa/page-backfill-demo.sh; rc=$$?; \
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
	@cd evals/daofa-grading && PYTHONDONTWRITEBYTECODE=1 $(if $(findstring /,$(PYTHON)),$(abspath $(PYTHON)),$(PYTHON)) -m unittest discover -s tests -q && echo "daofa-grading-eval-test: 通过"

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
KNOWLEDGE_MISSING = [ ! -x services/knowledge/.venv/bin/pytest ] || ! services/knowledge/.venv/bin/python -c "import fastapi, asyncpg, mcp, jsonschema, jieba" 2>/dev/null
knowledge-test:
	@if $(KNOWLEDGE_MISSING); then \
	  echo "knowledge-test: 跳过（没有安装依赖；见 services/knowledge/README.md）"; \
	  if [ "$(CONTRACT_STRICT)" = "1" ]; then exit 1; else exit 0; fi; fi
	@$(call guard,$(KNOWLEDGE_MISSING))cd services/knowledge && KNOWLEDGE_TEST_STRICT=$(if $(filter 1,$(CONTRACT_STRICT)),1,0) .venv/bin/python -m pytest -q -rs \
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
KGE_COMPILE_EVAL_MISSING = [ ! -x services/knowledge/.venv/bin/pytest ] || ! services/knowledge/.venv/bin/python -c "import jieba, yaml" 2>/dev/null
# WeKnora 技术验证探针（specs/kb-weknora-integration/weknora-validation-plan.md）：对进程内的模拟 WeKnora 跑全部探针，
# 并证明打开缺陷开关时对应探针判失败。只用标准库 + pytest，不联网（模拟服务只监听 127.0.0.1）。真实运行见 tools/weknora-probe/README.md
WEKNORA_PROBE_MISSING = ! $(PYTHON) -c "import pytest" 2>/dev/null
weknora-probe-test:
	@if $(WEKNORA_PROBE_MISSING); then \
	  echo "weknora-probe-test: 跳过（$(PYTHON) 没有 pytest；用 PYTHON=.venv-contracts/bin/python）"; \
	  if [ "$(CONTRACT_STRICT)" = "1" ]; then exit 1; else exit 0; fi; fi
	@$(call guard,$(WEKNORA_PROBE_MISSING))cd tools/weknora-probe && PYTHONDONTWRITEBYTECODE=1 $(if $(findstring /,$(PYTHON)),$(abspath $(PYTHON)),$(PYTHON)) -m pytest -q && echo "weknora-probe-test: 通过"

kge-compile-eval-test:
	@if $(KGE_COMPILE_EVAL_MISSING); then \
	  echo "kge-compile-eval-test: 跳过（没有安装依赖；见 services/knowledge/README.md）"; \
	  if [ "$(CONTRACT_STRICT)" = "1" ]; then exit 1; else exit 0; fi; fi
	@$(call guard,$(KGE_COMPILE_EVAL_MISSING))cd evals/kge-compile && PYTHONDONTWRITEBYTECODE=1 ../../services/knowledge/.venv/bin/python -m pytest -q && echo "kge-compile-eval-test: 通过"

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
EVAL_PY_MISSING = [ ! -x $(EVAL_PY) ]
PERSONA_REPORT_DIR ?= $(or $(TMPDIR),/tmp)/persona-report
eval-persona:
	@if $(EVAL_PY_MISSING); then \
	  echo "eval-persona: 跳过（没有安装依赖；见 services/learning/README.md）"; \
	  if [ "$(CONTRACT_STRICT)" = "1" ]; then exit 1; else exit 0; fi; fi
	@$(call guard,$(EVAL_PY_MISSING))cd evals/persona && ../../$(EVAL_PY) -m scorer validate
	@$(call guard,$(EVAL_PY_MISSING))cd evals/persona && ../../$(EVAL_PY) -m scorer selftest
	@$(call guard,$(EVAL_PY_MISSING))cd evals/persona && ../../$(EVAL_PY) -m pytest -q -p no:cacheprovider tests && echo "eval-persona: 评分器与人设治理测试通过（含离线全流程）"
	@$(call guard,$(EVAL_PY_MISSING))cd evals/persona && ../../$(EVAL_PY) -m governance run $(if $(ROLE),--role $(ROLE)) $(if $(BASE),--base $(BASE)) \
	  $(if $(filter 1,$(CONTRACT_STRICT)),--strict) --out $(abspath $(PERSONA_REPORT_DIR)); rc=$$?; \
	  if [ $$rc -eq 3 ] && [ "$(CONTRACT_STRICT)" != "1" ]; then exit 0; else exit $$rc; fi
	@$(call guard,$(EVAL_PY_MISSING))if [ -n "$(ROLE)" ]; then echo "eval-persona: 指定了 ROLE，跳过变异测试"; \
	  else cd evals/persona && ../../$(EVAL_PY) scripts/mutations.py; fi

eval-grade:
	@echo "eval-grade: SUBJECT=$(SUBJECT)，evals 尚未建立，跳过"

# 掌握 v0 纯函数部分（KGE-04 第一步，specs/mastery-estimate-sprint4）：单测与性质测试在 learning-test 里；
# 这里把配置校验、证据分类、门槛状态机、衰减、写入判定、一致性检查的关键判定逐条改坏，断言掌握测试能发现（不连库，约 20 秒）；
# 第二组（KGE-04 第二步 PR 2）在一次性 Postgres 上把读取层、写入层、复测任务、grade 接入逐条改坏（每个变异起一个库，约 1–2 分钟）。
# 没有本机 Postgres 时第二组退出码 3：非严格跳过、严格失败
.PHONY: mastery-mutation
mastery-mutation:
	@if $(LEARNING_MISSING); then \
	  echo "mastery-mutation: 跳过（没有安装学习服务依赖；见 services/learning/README.md）"; \
	  if [ "$(CONTRACT_STRICT)" = "1" ]; then exit 1; else exit 0; fi; fi
	@$(call guard,$(LEARNING_MISSING))services/learning/.venv/bin/python services/learning/scripts/mastery_mutations.py; rc=$$?; \
	  if [ $$rc -eq 3 ] && [ "$(CONTRACT_STRICT)" != "1" ]; then exit 0; else exit $$rc; fi

# SIE-02b 第一步（specs/sie-02b-claims-first-report）：洞察服务 services/insight——判断生成与生命周期（合成掌握行驱动）、
# insight.claims / explain / context（契约严格校验）、首份报告结构化生成与两道检查、中圈指标静默计算，以及影子评审比对脚本
# （evals/sie-shadow，用洞察服务的 venv）。库上测试在 tests/pginsight.py 起的一次性 Postgres 上跑（执行 bootstrap 角色与全部迁移）。
# 没有 Postgres 时库上测试非严格跳过、严格失败。依赖需先安装（带哈希的锁文件）：
#   cd services/insight && python3 -m venv .venv && .venv/bin/pip install --require-hashes -r requirements-dev.lock
INSIGHT_MISSING = [ ! -x services/insight/.venv/bin/python ] || ! services/insight/.venv/bin/python -c "import fastapi, asyncpg, jsonschema, yaml, httpx, pytest" 2>/dev/null
insight-test:
	@if $(INSIGHT_MISSING); then \
	  echo "insight-test: 跳过（没有安装依赖；见 services/insight/README.md）"; \
	  if [ "$(CONTRACT_STRICT)" = "1" ]; then exit 1; else exit 0; fi; fi
	@$(call guard,$(INSIGHT_MISSING))cd services/insight && INSIGHT_TEST_STRICT=$(if $(filter 1,$(CONTRACT_STRICT)),1,0) PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -rs
	@$(call guard,$(INSIGHT_MISSING))cd evals/sie-shadow && PYTHONDONTWRITEBYTECODE=1 ../../services/insight/.venv/bin/python -m pytest -q && echo "insight-test: 通过"

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
# 先跑星图截图的对账与护栏单测 tools/e2e-cohort/tests（不需要数据库与浏览器，几秒；specs/e2e-integration-sprint5/lessons.md 的后续项）。
.PHONY: e2e-cohort gate0-evidence
e2e-cohort:
	@if $(LEARNING_MISSING); then \
	  echo "e2e-cohort: 跳过（没有安装依赖；见 services/learning/README.md）"; \
	  if [ "$(CONTRACT_STRICT)" = "1" ]; then exit 1; else exit 0; fi; fi
	@$(call guard,$(LEARNING_MISSING))cd services/learning && PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider --rootdir '$(abspath tools/e2e-cohort)' '$(abspath tools/e2e-cohort/tests)'
	@$(call guard,$(LEARNING_MISSING))cd services/learning && LEARNING_TEST_STRICT=$(if $(filter 1,$(CONTRACT_STRICT)),1,0) .venv/bin/python -m pytest -q tests/test_e2e_cohort.py

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

# Gate 1 证据管线第一步（SIE-04，specs/gate1-evidence-sie04）：固定种子 40 名合成学生 12 周的纵向队列（作答、首轮与 8 周后复测、
# 会话、三轮考试预测与成绩），四项报表（G1-2 8 周复测与续用、G1-3 考试区间覆盖率与 MAE、G1-4 中圈指标效度、附加分组误差差异）
# 直接调用洞察服务的纯函数（insight/predict.py 的 predict_interval / reconcile_plan、insight/metrics.py），方法与阈值放配置
# tools/gate1-evidence/config.unsigned.json（文件名沿用历史；2026-10-14 已由项目负责人代表三方签字）。不连库、不联网、不读留出集；用洞察服务的 venv（不新增依赖）。
#   make gate1-evidence GATE1_OUT=<输出目录>   证据包（CSV、checks.json、summary.md、run_metadata.json；标“合成，不是试点结论”）
#   make gate1-test       纯函数测试与反例、证据包确定性、与仓库样例 tools/gate1-evidence/sample 逐字节一致（几秒）
#   make gate1-mutation   把报表的门槛、边界、方向、口径逐条改坏，断言测试能发现（约 1 分钟）
# 样例过期时：make gate1-evidence GATE1_OUT=tools/gate1-evidence/sample（路径不含空格与单引号）
GATE1_PY := services/insight/.venv/bin/python
GATE1_MISSING = [ ! -x $(GATE1_PY) ] || ! $(GATE1_PY) -c "import pytest" 2>/dev/null
GATE1_BAD = $(or $(filter-out 1,$(words $(GATE1_OUT))),$(findstring ',$(GATE1_OUT)))
.PHONY: gate1-evidence gate1-test gate1-mutation
gate1-evidence:
	@$(if $(GATE1_BAD),echo "用法：make gate1-evidence GATE1_OUT=<输出目录>（路径不含空格与单引号）"; exit 2)
	@if [ ! -x $(GATE1_PY) ]; then echo "gate1-evidence: 没有安装洞察服务依赖（见 services/insight/README.md）"; exit 2; fi
	@cd services/insight && PYTHONDONTWRITEBYTECODE=1 PYTHONPATH='$(abspath tools/gate1-evidence)' .venv/bin/python -m gate1_evidence evidence --out '$(abspath $(GATE1_OUT))'

gate1-test:
	@if $(GATE1_MISSING); then \
	  echo "gate1-test: 跳过（没有安装洞察服务依赖；见 services/insight/README.md）"; \
	  if [ "$(CONTRACT_STRICT)" = "1" ]; then exit 1; else exit 0; fi; fi
	@$(call guard,$(GATE1_MISSING))cd services/insight && PYTHONDONTWRITEBYTECODE=1 PYTHONPATH='$(abspath tools/gate1-evidence)' .venv/bin/python -m pytest -q -p no:cacheprovider --rootdir '$(abspath tools/gate1-evidence)' '$(abspath tools/gate1-evidence/tests)' && echo "gate1-test: 通过"

gate1-mutation:
	@if $(GATE1_MISSING); then \
	  echo "gate1-mutation: 跳过（没有安装洞察服务依赖；见 services/insight/README.md）"; \
	  if [ "$(CONTRACT_STRICT)" = "1" ]; then exit 1; else exit 0; fi; fi
	@$(call skip_rest,$(GATE1_MISSING))$(GATE1_PY) tools/gate1-evidence/mutations.py; rc=$$?; \
	  if [ $$rc -eq 3 ] && [ "$(CONTRACT_STRICT)" != "1" ]; then exit 0; else exit $$rc; fi

# 冲刺 5 压测基线、单模型失效降级、成本测算（specs/load-cost-baseline-sprint5；工具与手册见 tools/loadtest/README.md、RUNBOOK.md）。
# 只在本机一次性栈上跑（规则 9），只用合成数据（规则 8）；依赖同 learning-test、三科适配器与 BFF 的 npm ci。
#   make loadtest-baseline [LOADTEST_STAGES=small|full|10:120,50:120] [LOADTEST_OUT=<目录>] [LOADTEST_ARGS='--fault fake-a=unavailable']
#     默认 small（5 → 10 并发 × 10 s）；完整分阶段 10 → 50 → 100 → 200 用 LOADTEST_STAGES=full LOADTEST_ARGS=--i-know（约 8 分钟，占满本机）
#     PLT-16：LOADTEST_ARGS='--subject-gate --with-practice --fault fake-a=slow:800' 打开分学科并发闸门与再练、复测场景并观察排队
#   make degradation-test   故障注入与降级断言（路由层 + 端到端，约 40 秒）
#   make cost-report [COST_RECORDS=<调用记录 jsonl>] [COST_OUT=<目录>]   成本测试 + 科目 × 会员档 P50 / P90 报表（合成估算、示例价）
# 不给输出目录时写到新建的临时目录（mkdtemp）；给了则不能是符号链接、须属于当前用户。
# 护栏（同 GATE0_BAD）：路径不含空格与单引号；LOADTEST_STAGES 只收 small、full 或“数字:数字,…”；LOADTEST_ARGS 只收白名单选项。
# 变量一律经环境变量传给命令（不在命令行里展开），shell 不会把它们当代码执行。
.PHONY: loadtest-baseline degradation-test cost-report
LOADTEST_PY := services/learning/.venv/bin/python
LOADTEST_PY_MISSING = [ ! -x $(LOADTEST_PY) ]
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
	  for a in $$LT_ARGS; do case "$$a" in --i-know|--fault|--retry-scale|--median-ms|--p90-ms|--seed|--subject-gate|--with-practice|fake-[abx]=unavailable|fake-[abx]=timeout|fake-[abx]=rate_limited|fake-[abx]=error_ratio:[0-9.]*|fake-[abx]=slow:[0-9]*|[0-9]*) ;; *) echo "loadtest-baseline: LOADTEST_ARGS 里的 $$a 不在白名单"; exit 2;; esac; done
	@if $(LOADTEST_PY_MISSING); then echo "loadtest-baseline: 跳过（没有 services/learning/.venv）"; \
	  if [ "$(CONTRACT_STRICT)" = "1" ]; then exit 1; else exit 0; fi; fi
	@$(call guard,$(LOADTEST_PY_MISSING))cd tools/loadtest && PYTHONDONTWRITEBYTECODE=1 ../../$(LOADTEST_PY) -m pytest -q tests/test_arrivals.py tests/test_guards.py
	@$(call guard,$(LOADTEST_PY_MISSING))cd tools/loadtest && ../../$(LOADTEST_PY) -m loadtest baseline --stages "$$LT_STAGES" $${LT_OUT:+--out "$$LT_OUT"} $$LT_ARGS \
	  && echo "loadtest-baseline: 报告见上面 out 目录的 baseline.md（本机基线，不代表生产容量）"
degradation-test:
	@if $(LEARNING_MISSING); then echo "degradation-test: 跳过（没有 services/learning/.venv）"; \
	  if [ "$(CONTRACT_STRICT)" = "1" ]; then exit 1; else exit 0; fi; fi
	@$(call guard,$(LEARNING_MISSING))cd tools/loadtest && PYTHONDONTWRITEBYTECODE=1 ../../$(LOADTEST_PY) -m pytest -q tests/test_metrics_faults.py
	@$(call guard,$(LEARNING_MISSING))cd services/learning && LEARNING_TEST_STRICT=$(if $(filter 1,$(CONTRACT_STRICT)),1,0) .venv/bin/python -m pytest -q -rs tests/test_degradation.py \
	  && echo "degradation-test: 通过"
cost-report:
	@$(if $(LOADTEST_PATH_BAD),echo "cost-report: 路径不能含空格或单引号"; exit 2)
	@if $(LOADTEST_PY_MISSING); then echo "cost-report: 跳过（没有 services/learning/.venv）"; \
	  if [ "$(CONTRACT_STRICT)" = "1" ]; then exit 1; else exit 0; fi; fi
	@$(call guard,$(LOADTEST_PY_MISSING))cd tools/loadtest && PYTHONDONTWRITEBYTECODE=1 ../../$(LOADTEST_PY) -m pytest -q tests/test_cost.py
	@$(call guard,$(LOADTEST_PY_MISSING))cd tools/loadtest && ../../$(LOADTEST_PY) -m loadtest cost --records "$$LT_COST_RECORDS" $${LT_COST_OUT:+--out "$$LT_COST_OUT"} \
	  && echo "cost-report: 报告见上面 out 目录的 cost-report.md（合成估算、示例价）"

# PLT-17 成本仪表盘第一步（specs/cost-dashboard-plt17，全程合成数据，离线、零新依赖；复用 tools/loadtest 成本模块的额度、价格、分位数与报表）：
#   make cost-dashboard [DASH_USAGE=<消耗聚合行 csv>] [DASH_RECORDS=<调用记录 jsonl>] [DASH_OUT=<目录>]
#     消耗聚合行 + 调用记录 → cost-dashboard.json / .md（只出聚合；每人一个点、人数加 ±2 扰动后 ≥ 22 才公开、取整到 10；分位数插值后粗化；
#     标注由数据决定：合成 / 试点、示例价 / 签字价）。不给 DASH_USAGE / DASH_RECORDS 时先用固定种子生成合成队列（临时目录，退出时清理）；
#     给了输入就必须给 DASH_OUT（须不存在或为空）。本目标产出数据，不进 check。
#   make cost-dashboard-test       仪表盘单测：手算、扰动门槛与取整、P90 尾部、未归因份额、分摊两口径、标注、输入守卫、差分（删一人重跑）、逐字节一致（几秒）
#   make cost-dashboard-mutation   关键检查逐条改坏，断言测试能发现（约 10 秒）
# 样例过期时：python -m loadtest dashboard-synth --out <新的空临时目录>，再 dashboard 输出到另一个空临时目录，
#   把 cost-dashboard.md 替换进 tools/loadtest/sample，并把 cost-dashboard.json 的 sha256 更新到 tests/test_dashboard.py。
# 护栏同 cost-report：路径不含空格与单引号；变量经环境变量传给命令。缺学习服务 venv 时非严格跳过、严格失败。
.PHONY: cost-dashboard
DASH_USAGE ?=
DASH_RECORDS ?=
DASH_OUT ?=
DASH_PATH_BAD = $(or $(filter-out 0 1,$(words $(DASH_USAGE))),$(findstring ',$(DASH_USAGE)),$(filter-out 0 1,$(words $(DASH_RECORDS))),$(findstring ',$(DASH_RECORDS)),$(filter-out 0 1,$(words $(DASH_OUT))),$(findstring ',$(DASH_OUT)))
COST_DASH_MISSING = [ ! -x $(LOADTEST_PY) ] || ! $(LOADTEST_PY) -c "import pytest, yaml" 2>/dev/null
cost-dashboard: export LT_DASH_USAGE = $(if $(DASH_USAGE),$(abspath $(DASH_USAGE)),)
cost-dashboard: export LT_DASH_RECORDS = $(if $(DASH_RECORDS),$(abspath $(DASH_RECORDS)),)
cost-dashboard: export LT_DASH_OUT = $(if $(DASH_OUT),$(abspath $(DASH_OUT)),)
cost-dashboard:
	@$(if $(DASH_PATH_BAD),echo "cost-dashboard: 路径不能含空格或单引号"; exit 2)
	@case "$${LT_DASH_USAGE:+u}$${LT_DASH_RECORDS:+r}" in u|r) echo "cost-dashboard: DASH_USAGE 与 DASH_RECORDS 要么都给、要么都不给"; exit 2;; esac
	@if [ -n "$$LT_DASH_USAGE" ] && [ -z "$$LT_DASH_OUT" ]; then echo "cost-dashboard: 给了输入就必须给 DASH_OUT（受控目录，须不存在或为空）"; exit 2; fi
	@if $(COST_DASH_MISSING); then echo "cost-dashboard: 跳过（没有 services/learning/.venv 或其中没有 pytest、PyYAML）"; \
	  if [ "$(CONTRACT_STRICT)" = "1" ]; then exit 1; else exit 0; fi; fi
	@$(call guard,$(COST_DASH_MISSING))cd tools/loadtest && export PYTHONDONTWRITEBYTECODE=1; \
	  if [ -z "$$LT_DASH_USAGE" ]; then syn=$$(mktemp -d) || exit 1; trap 'rm -rf "$$syn"' EXIT; \
	    ../../$(LOADTEST_PY) -m loadtest dashboard-synth --out "$$syn" \
	    && LT_DASH_USAGE="$$syn/usage.csv" && LT_DASH_RECORDS="$$syn/call-records.jsonl" || exit 1; fi; \
	  ../../$(LOADTEST_PY) -m loadtest dashboard --usage "$$LT_DASH_USAGE" --records "$$LT_DASH_RECORDS" $${LT_DASH_OUT:+--out "$$LT_DASH_OUT"} \
	  && echo "cost-dashboard: 报表见上面 out 目录的 cost-dashboard.md（合成，不是试点结论；价格为示例价）"

cost-dashboard-test:
	@if $(COST_DASH_MISSING); then echo "cost-dashboard-test: 跳过（没有 services/learning/.venv 或其中没有 pytest、PyYAML）"; \
	  if [ "$(CONTRACT_STRICT)" = "1" ]; then exit 1; else exit 0; fi; fi
	@$(call guard,$(COST_DASH_MISSING))cd tools/loadtest && PYTHONDONTWRITEBYTECODE=1 ../../$(LOADTEST_PY) -m pytest -q -p no:cacheprovider tests/test_dashboard.py \
	  && echo "cost-dashboard-test: 通过"

cost-dashboard-mutation:
	@if $(COST_DASH_MISSING); then echo "cost-dashboard-mutation: 跳过（没有 services/learning/.venv 或其中没有 pytest、PyYAML）"; \
	  if [ "$(CONTRACT_STRICT)" = "1" ]; then exit 1; else exit 0; fi; fi
	@$(call skip_rest,$(COST_DASH_MISSING))PYTHONDONTWRITEBYTECODE=1 $(LOADTEST_PY) tools/loadtest/dashboard_mutations.py; rc=$$?; \
	  if [ $$rc -eq 3 ] && [ "$(CONTRACT_STRICT)" != "1" ]; then exit 0; else exit $$rc; fi

# PLT-20 第一步（specs/loadtest-2x-peak-plt20）：压测工具的纯函数单测——可选场景与请求体、SLO 判定、peak2x 阶段、报告格式与
# digest 钉值、样例 sample/peak2x.md 标注。不起栈、不跑真实压测（实际压测见 tools/loadtest/README.md，不进 check）
# PLT-21（specs/loadtest-write-path-plt21）另加 tests/test_write_path.py：写路径种子、一致性核对与环节耗时的纯函数，
# 以及一次性 Postgres 上的种子（经准入与发布闸门）与真知识服务（只读）启动；没有 Postgres、node 或知识服务 .venv 时这两条跳过（严格模式失败）
# PLT-22（specs/loadtest-mastery-path-plt22）另加 tests/test_mastery_path.py：合成掌握配置的加载与环境限制、种子不写任务表、
# 复测任务来源核对、作答 = 计次 = 作答证据 = 单元重算的一致性、重算耗时与状态变化的纯函数（不起栈）
.PHONY: loadtest-test
loadtest-test:
	@if $(COST_DASH_MISSING); then echo "loadtest-test: 跳过（没有 services/learning/.venv 或其中没有 pytest、PyYAML）"; \
	  if [ "$(CONTRACT_STRICT)" = "1" ]; then exit 1; else exit 0; fi; fi
	@$(call guard,$(COST_DASH_MISSING))cd tools/loadtest && PYTHONDONTWRITEBYTECODE=1 ../../$(LOADTEST_PY) -m pytest -q -p no:cacheprovider \
	  tests/test_peak2x.py tests/test_arrivals.py tests/test_metrics_faults.py tests/test_write_path.py tests/test_mastery_path.py \
	  && echo "loadtest-test: 通过"

# KGE-06 封测周修订支持（specs/kge06-weekly-revision）审核台部分：低置信打标候选工作单与修订发布预检的测试在 learning-test 里
# （tests/test_review_revision.py）；这里把开关、排序与游标、只列待处理、只经签字身份决定、改标核对、版本号段与两人签、
# 结构性以库为准等检查逐条改坏，断言测试能发现（纯函数组不连库；库上组每个变异一个一次性 Postgres）。
# 没有依赖或 Postgres 时退出码 3（非严格跳过、严格失败）
.PHONY: review-console-mutation
review-console-mutation:
	@if $(LEARNING_MISSING); then \
	  echo "review-console-mutation: 跳过（没有安装依赖；见 services/learning/README.md）"; \
	  if [ "$(CONTRACT_STRICT)" = "1" ]; then exit 1; else exit 0; fi; fi
	@$(call skip_rest,$(LEARNING_MISSING))services/learning/.venv/bin/python services/learning/scripts/review_console_mutations.py; rc=$$?; \
	  if [ $$rc -eq 3 ] && [ "$(CONTRACT_STRICT)" != "1" ]; then exit 0; else exit $$rc; fi

# KGE-07 语文切片工程底座第一步（specs/kge07-chinese-slice，全程合成数据）：
# kge-chinese-import：导入器语文分支（tools/kge-import/src/chinese）的类型检查（借用 kge-inventory 的 tsc）+ 单测（tests/chinese/*.test.ts：
#   确定性、素材不进前置图、层次递进 = prerequisite + weak、无环、无传递冗余边、无孤点、CHN. 规则与反例）+ 一次性 Postgres 上的端到端
#   （chinese-db.sh：两次逐字节相同、合成挡板、以 starbuddy_kge 写入草稿、发布前校验零阻断、库上校验与离线同口径、
#   库上快照 = 离线快照、语文覆盖报表两边逐字节相同）。语文覆盖报表的单测与数学、道法回归随 kge-coverage-test 运行
# kge-chinese-mutation：导入器、语文覆盖报表与库上快照导出的关键检查逐条改坏（约 4 分钟；没有 Postgres 时库上一组跳过，退出码 3）
# knowledge-material-mutation：编译层素材卡（页面类型、模板、体检、编译器风险分级与编入）逐条改坏，断言 tests/test_material.py 能发现（约 30 秒）
# 缺依赖或 Postgres 时非严格跳过、严格失败
.PHONY: kge-chinese-import kge-chinese-mutation knowledge-material-mutation
kge-chinese-import:
	@if $(KGE_INVENTORY_MISSING); then \
	  echo "kge-chinese-import: 跳过（没有安装依赖；cd tools/kge-inventory && npm ci）"; \
	  if [ "$(CONTRACT_STRICT)" = "1" ]; then exit 1; else exit 0; fi; fi
	@$(call guard,$(KGE_INVENTORY_MISSING))cd tools/kge-import && ../kge-inventory/node_modules/.bin/tsc -p tsconfig.json && echo "kge-chinese-import: 类型检查通过"
	@$(call guard,$(KGE_INVENTORY_MISSING))cd tools/kge-import && node --no-warnings --test --test-reporter=dot tests/chinese/*.test.ts && echo "kge-chinese-import: 单测通过"
	@$(call guard,$(KGE_INVENTORY_MISSING))bash tools/kge-import/tests/chinese/chinese-db.sh; rc=$$?; \
	  if [ $$rc -eq 3 ] && [ "$(CONTRACT_STRICT)" != "1" ]; then exit 0; else exit $$rc; fi

kge-chinese-mutation:
	@if $(KGE_INVENTORY_MISSING) || $(KGE_COVERAGE_MISSING); then \
	  echo "kge-chinese-mutation: 跳过（没有安装依赖；cd tools/kge-inventory && npm ci；cd tools/kge-coverage && npm ci）"; \
	  if [ "$(CONTRACT_STRICT)" = "1" ]; then exit 1; else exit 0; fi; fi
	@$(call skip_rest,$(KGE_INVENTORY_MISSING) || $(KGE_COVERAGE_MISSING))node --no-warnings tools/kge-import/tests/chinese/mutations.ts; rc=$$?; \
	  if [ $$rc -eq 3 ] && [ "$(CONTRACT_STRICT)" != "1" ]; then exit 0; else exit $$rc; fi

knowledge-material-mutation:
	@if $(KNOWLEDGE_MISSING); then \
	  echo "knowledge-material-mutation: 跳过（没有安装依赖；见 services/knowledge/README.md）"; \
	  if [ "$(CONTRACT_STRICT)" = "1" ]; then exit 1; else exit 0; fi; fi
	@$(call skip_rest,$(KNOWLEDGE_MISSING))services/knowledge/.venv/bin/python services/knowledge/tests/material_mutations.py; rc=$$?; \
	  if [ $$rc -eq 3 ] && [ "$(CONTRACT_STRICT)" != "1" ]; then exit 0; else exit $$rc; fi

# PLT-19 第一步（specs/knowledge-read-cache-plt19）：知识服务公共层只读缓存（缺省关）的变异测试——缓存键、已发布版本核对、
# 错误不入缓存、副本、热切换清空、学生 ID 类字段拦截、LRU 与计数、开关缺省值、应用接线逐条改坏，断言 tests/test_service_cache.py
# 与 tests/test_service_cache_db.py 能发现（约 2 分钟）。测试本身随 knowledge-test 运行。没有 Postgres 时库上的变异跳过
# （退出码 3：非严格算跳过、严格算失败）
.PHONY: knowledge-cache-mutation
knowledge-cache-mutation:
	@if $(KNOWLEDGE_MISSING); then \
	  echo "knowledge-cache-mutation: 跳过（没有安装依赖；见 services/knowledge/README.md）"; \
	  if [ "$(CONTRACT_STRICT)" = "1" ]; then exit 1; else exit 0; fi; fi
	@$(call skip_rest,$(KNOWLEDGE_MISSING))PYTHONDONTWRITEBYTECODE=1 services/knowledge/.venv/bin/python services/knowledge/tests/cache_mutations.py; rc=$$?; \
	  if [ $$rc -eq 3 ] && [ "$(CONTRACT_STRICT)" != "1" ]; then exit 0; else exit $$rc; fi

# KGE-11 第一步（specs/kge11-semantic-lint）：规则式语义体检（knowledge.semantic，报告 kge-lint-report/1.1）、文件式编入事件
# （knowledge.compiler plan-events）、审核台分流建议（review_console.risk.suggest_review_level，开关缺省关、尚未生效）。不连库、不调模型。
# knowledge-semantic-test：services/knowledge/tests/test_semantic.py、test_semantic_events.py 与 services/learning/tests/test_review_triage.py
# knowledge-semantic-mutation：检查规则、比对范围、报告绑定、M1 兼容、分级、输入校验、编入清单与信号、分流逐条改坏（约 10 分钟；
#   没有审核台依赖时审核台一侧跳过，退出码 3：非严格算跳过、严格算失败）
.PHONY: knowledge-semantic-test knowledge-semantic-mutation
knowledge-semantic-test:
	@if $(KNOWLEDGE_MISSING) || $(LEARNING_MISSING); then \
	  echo "knowledge-semantic-test: 跳过（没有安装依赖；见 services/knowledge/README.md、services/learning/README.md）"; \
	  if [ "$(CONTRACT_STRICT)" = "1" ]; then exit 1; else exit 0; fi; fi
	@$(call guard,$(KNOWLEDGE_MISSING) || $(LEARNING_MISSING))cd services/knowledge && PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/test_semantic.py tests/test_semantic_events.py
	@$(call guard,$(KNOWLEDGE_MISSING) || $(LEARNING_MISSING))cd services/learning && PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/test_review_triage.py && echo "knowledge-semantic-test: 通过"

knowledge-semantic-mutation:
	@if $(KNOWLEDGE_MISSING); then \
	  echo "knowledge-semantic-mutation: 跳过（没有安装依赖；见 services/knowledge/README.md）"; \
	  if [ "$(CONTRACT_STRICT)" = "1" ]; then exit 1; else exit 0; fi; fi
	@$(call skip_rest,$(KNOWLEDGE_MISSING))PYTHONDONTWRITEBYTECODE=1 services/knowledge/.venv/bin/python services/knowledge/tests/semantic_mutations.py; rc=$$?; \
	  if [ $$rc -eq 3 ] && [ "$(CONTRACT_STRICT)" != "1" ]; then exit 0; else exit $$rc; fi

# 舞台第一步（specs/stage-whiteboard-step1，全程合成数据）：packages/stage 的讲解 DSL v0.1.0（类型、JSON Schema、确定性校验器）、
# 播放状态机与最小 SVG 白板（零新依赖：本包不装依赖，借用 apps/web 已安装的 typescript、react 与 @types）。
# stage-test：类型检查 + 单测（校验器正反例、状态机、白板折叠、三套布局令牌、schema 与常量一致、SSR 快照、渲染无外链、讲解权 1.25 线、
#   只渲染已校验的 DSL、源码不碰存储与网络）。样张页 /dev/stage 的接线与端到端随 web-test 运行（tests/stage.test.ts、e2e/stage.spec.ts）
# stage-mutation：校验器、文字安全、公式排版、状态机、白板折叠、组件、布局令牌与 schema 逐条改坏，断言测试能发现（约 2 分钟）
# 缺 apps/web 依赖时非严格跳过、严格失败
STAGE_MISSING = [ ! -d apps/web/node_modules/react ] || [ ! -x apps/web/node_modules/.bin/tsc ]
.PHONY: stage-test stage-mutation
stage-test:
	@if $(STAGE_MISSING); then \
	  echo "stage-test: 跳过（没有安装 apps/web 依赖；cd apps/web && npm ci）"; \
	  if [ "$(CONTRACT_STRICT)" = "1" ]; then exit 1; else exit 0; fi; fi
	@$(call guard,$(STAGE_MISSING))apps/web/node_modules/.bin/tsc -p packages/stage/tsconfig.json && echo "stage-test: 类型检查通过"
	@$(call guard,$(STAGE_MISSING))cd packages/stage && node --no-warnings --test --test-reporter=dot tests/*.test.ts && echo "stage-test: 单测通过"

stage-mutation:
	@if $(STAGE_MISSING); then \
	  echo "stage-mutation: 跳过（没有安装 apps/web 依赖；cd apps/web && npm ci）"; \
	  if [ "$(CONTRACT_STRICT)" = "1" ]; then exit 1; else exit 0; fi; fi
	@$(call skip_rest,$(STAGE_MISSING))node --no-warnings packages/stage/scripts/mutations.ts; rc=$$?; \
	  if [ $$rc -eq 3 ] && [ "$(CONTRACT_STRICT)" != "1" ]; then exit 0; else exit $$rc; fi

# KGE-08 第一步（specs/kge08-paper-dedupe，全程合成数据，离线、零新依赖：借用 tools/kge-inventory 的 tsc 与 @types）：
# tools/kge-dedupe 的规范化、SHA-256 + 64 位 SimHash、近似两道门、题目族候选、同卷识别（宁严勿松）、输入守卫与报告。
# kge-dedupe-test：类型检查 + 单测（含“报告与清单不含题干”与逐字节一致）
# kge-dedupe-mutation：关键检查逐条改坏（106 条），断言测试能发现（约 11 分钟）
# 缺 tools/kge-inventory 依赖时非严格跳过、严格失败
kge-dedupe-test:
	@if $(KGE_INVENTORY_MISSING); then \
	  echo "kge-dedupe-test: 跳过（没有安装依赖；cd tools/kge-inventory && npm ci）"; \
	  if [ "$(CONTRACT_STRICT)" = "1" ]; then exit 1; else exit 0; fi; fi
	@$(call guard,$(KGE_INVENTORY_MISSING))cd tools/kge-dedupe && ../kge-inventory/node_modules/.bin/tsc -p tsconfig.json && echo "kge-dedupe-test: 类型检查通过"
	@$(call guard,$(KGE_INVENTORY_MISSING))cd tools/kge-dedupe && node --no-warnings --test --test-reporter=dot tests/*.test.ts && echo "kge-dedupe-test: 单测通过"

kge-dedupe-mutation:
	@if $(KGE_INVENTORY_MISSING); then \
	  echo "kge-dedupe-mutation: 跳过（没有安装依赖；cd tools/kge-inventory && npm ci）"; \
	  if [ "$(CONTRACT_STRICT)" = "1" ]; then exit 1; else exit 0; fi; fi
	@$(call skip_rest,$(KGE_INVENTORY_MISSING))node --no-warnings tools/kge-dedupe/tests/mutations.ts; rc=$$?; \
	  if [ $$rc -eq 3 ] && [ "$(CONTRACT_STRICT)" != "1" ]; then exit 0; else exit $$rc; fi

# KGE-09 第一步（specs/kge09-map-calibration，全程合成数据，离线、零新依赖：纯标准库，借洞察服务的 venv 跑 pytest，同 gate1-test）：
# tools/kge-calibrate 的前置边检验（Wilson / Newcombe、BH）、隐藏依赖候选（phi、对数优势比）、最小格守卫、伪名不外泄、输入守卫、合成队列召回与误报。
# kge-calibrate-test：统计函数正反例 + 守卫 + 召回与误报上限 + 逐字节一致（含与 tools/kge-calibrate/sample 一致，几秒）
# kge-calibrate-mutation：关键检查逐条改坏，断言测试能发现（约半分钟）
# 样例过期时：python -m kge_calibrate synth 与 run 都输出到新的空临时目录（工具不覆盖已有文件），再把结果替换进 tools/kge-calibrate/sample
# 缺洞察服务 venv 或其中没有 pytest 时非严格跳过、严格失败
KGE_CALIBRATE_PY := services/insight/.venv/bin/python
KGE_CALIBRATE_MISSING = [ ! -x $(KGE_CALIBRATE_PY) ] || ! $(KGE_CALIBRATE_PY) -c "import pytest" 2>/dev/null
.PHONY: kge-calibrate-test kge-calibrate-mutation
kge-calibrate-test:
	@if $(KGE_CALIBRATE_MISSING); then \
	  echo "kge-calibrate-test: 跳过（没有安装洞察服务依赖；见 services/insight/README.md）"; \
	  if [ "$(CONTRACT_STRICT)" = "1" ]; then exit 1; else exit 0; fi; fi
	@$(call guard,$(KGE_CALIBRATE_MISSING))cd tools/kge-calibrate && PYTHONDONTWRITEBYTECODE=1 '$(abspath $(KGE_CALIBRATE_PY))' -m pytest -q -p no:cacheprovider --rootdir '$(abspath tools/kge-calibrate)' '$(abspath tools/kge-calibrate/tests)' && echo "kge-calibrate-test: 通过"

kge-calibrate-mutation:
	@if $(KGE_CALIBRATE_MISSING); then \
	  echo "kge-calibrate-mutation: 跳过（没有安装洞察服务依赖；见 services/insight/README.md）"; \
	  if [ "$(CONTRACT_STRICT)" = "1" ]; then exit 1; else exit 0; fi; fi
	@$(call skip_rest,$(KGE_CALIBRATE_MISSING))$(KGE_CALIBRATE_PY) tools/kge-calibrate/mutations.py; rc=$$?; \
	  if [ $$rc -eq 3 ] && [ "$(CONTRACT_STRICT)" != "1" ]; then exit 0; else exit $$rc; fi

# KGE-10 第一步（specs/kge10-irt-pilot，全程合成数据，离线、零新依赖：纯标准库，同 kge-calibrate 借洞察服务的 venv 跑 pytest）：
# tools/kge-irt 的 MML-EM（Gauss–Hermite）1PL / 2PL、时间切分评估（对数损失、Brier、AUC，对照 BKT 基线与只用先验难度）、自助法区间、
# 秩相关与可识别性告警；守卫、表键扰动、取整与输出写法复用 tools/kge-calibrate。
# kge-irt-test：求积与似然、梯度数值核对、EM 单调、参数恢复、指标手算、门槛与扰动、删一人差分、守卫、逐字节一致（含与样例一致，约一分钟）
# kge-irt-mutation：关键检查逐条改坏，断言测试能发现（几分钟）
# 样例过期时：python -m kge_irt synth 与 run 都输出到新的空临时目录（工具不覆盖已有文件），把 irt-report.md 替换进 tools/kge-irt/sample，
# 并把 irt-report.json 的 sha256 更新到 tests/test_irt.py 的 SAMPLE_JSON_SHA256
# 缺洞察服务 venv 或其中没有 pytest 时非严格跳过、严格失败
.PHONY: kge-irt-test kge-irt-mutation
kge-irt-test:
	@if $(KGE_CALIBRATE_MISSING); then \
	  echo "kge-irt-test: 跳过（没有安装洞察服务依赖；见 services/insight/README.md）"; \
	  if [ "$(CONTRACT_STRICT)" = "1" ]; then exit 1; else exit 0; fi; fi
	@$(call guard,$(KGE_CALIBRATE_MISSING))cd tools/kge-irt && PYTHONDONTWRITEBYTECODE=1 '$(abspath $(KGE_CALIBRATE_PY))' -m pytest -q -p no:cacheprovider --rootdir '$(abspath tools/kge-irt)' '$(abspath tools/kge-irt/tests)' && echo "kge-irt-test: 通过"

kge-irt-mutation:
	@if $(KGE_CALIBRATE_MISSING); then \
	  echo "kge-irt-mutation: 跳过（没有安装洞察服务依赖；见 services/insight/README.md）"; \
	  if [ "$(CONTRACT_STRICT)" = "1" ]; then exit 1; else exit 0; fi; fi
	@$(call skip_rest,$(KGE_CALIBRATE_MISSING))PYTHONDONTWRITEBYTECODE=1 $(KGE_CALIBRATE_PY) tools/kge-irt/mutations.py; rc=$$?; \
	  if [ $$rc -eq 3 ] && [ "$(CONTRACT_STRICT)" != "1" ]; then exit 0; else exit $$rc; fi
