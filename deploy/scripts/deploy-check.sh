#!/usr/bin/env bash
# make deploy-check 的实现（LT-01 范围第 6 条）：
#   1. deploy/tests：Dockerfile / .dockerignore / 编排 / 清单的静态校验、开关矩阵（含 BFF 与前端真实解析函数、Python 服务真实启动校验）、
#      环境变量清单、迁移执行器单测；
#   1b. IaC 草案的静态规则检查（LT-03，deploy/iac/check_iac.py，只用标准库；外部工具的 validate 另在 make iac-validate）；
#   1c. 告警规则与仪表盘的离线校验（LT-05，deploy/observability/check_observability.py；promtool 在时另跑）；
#   2. 迁移执行器在一次性本机库上的副本演练（deploy/migrate/rehearse-ephemeral.sh，db/tests/pg-ephemeral.sh 同款实例）；
#   3. 两份 compose 文件的结构校验（docker compose config；只要 docker 命令行，不要守护进程；取值全部是合成占位）；
#   4. DEPLOY_BUILD=1 时另加镜像构建（deploy/scripts/build-images.sh，需要能拉到基础镜像）与本机合成全栈冒烟（compose-smoke.sh）。
# 缺依赖（学习服务 venv、本机 Postgres、docker 命令行）：非严格模式打印“跳过”，CONTRACT_STRICT=1 时失败。退出码 0 / 1。
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"
STRICT="${CONTRACT_STRICT:-0}"
skip() { echo "deploy-check: 跳过 $1"; if [ "$STRICT" = "1" ]; then exit 1; fi; }
PY="$ROOT/services/learning/.venv/bin/python"

if [ -x "$PY" ]; then
  PYTHONDONTWRITEBYTECODE=1 "$PY" -m pytest -q -p no:cacheprovider deploy/tests || { echo "deploy-check: 失败（deploy/tests）"; exit 1; }
else
  skip "deploy/tests（没有 services/learning/.venv）"
fi

# LT-03：IaC 草案的静态规则检查（只用标准库；terraform / tflint / checkov / tfsec 另在 make iac-validate，工具在时才跑）
PYTHONDONTWRITEBYTECODE=1 python3 -I deploy/iac/check_iac.py deploy/iac/alicloud || { echo "deploy-check: 失败（IaC 静态规则检查）"; exit 1; }

# LT-05：告警规则与仪表盘只引用登记的指标（deploy/observability/check_observability.py，需要 PyYAML，用学习服务的 venv）；
# promtool 在就另跑 promtool check rules，缺失只打印“跳过”（Python 校验已覆盖结构与引用；有工具的 CI 用 make obs-promtool 严格要求）
if [ -x "$PY" ]; then
  PYTHONDONTWRITEBYTECODE=1 "$PY" -I deploy/observability/check_observability.py deploy/observability --promtool \
    || { echo "deploy-check: 失败（告警规则与仪表盘校验）"; exit 1; }
else
  skip "告警规则与仪表盘校验（没有 services/learning/.venv）"
fi

PYTHONDONTWRITEBYTECODE=1 PYTHON="${PY:-python3}" bash deploy/migrate/rehearse-ephemeral.sh; rc=$?
if [ $rc -eq 3 ]; then skip "迁移副本演练（本机没有 Postgres）"; elif [ $rc -ne 0 ]; then echo "deploy-check: 失败（迁移副本演练）"; exit 1; fi

if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
  syn() { case "$1" in *_DIR) printf '/nonexistent/syn-placeholder-%s' "$1";; *) printf 'syn-placeholder-%s' "$1";; esac; }
  vars=(STARBUDDY_REGISTRY STARBUDDY_IMAGE_TAG STARBUDDY_SIGNED_CONFIG_DIR STARBUDDY_PAGE_SNAPSHOT_DIR STARBUDDY_OTP_DIR POSTGRES_IMAGE
        LEARNING_DATABASE_URL LEARNING_SERVICE_TOKEN AYA_ADAPTER_SERVICE_TOKEN ZHIXING_ADAPTER_SERVICE_TOKEN WENLI_ADAPTER_SERVICE_TOKEN
        KNOWLEDGE_TOKEN_LEARNING LEARNING_INSIGHT_CLIENT_TOKEN LEARNING_PRACTICE_TICKET_SECRET KNOWLEDGE_DATABASE_URL INSIGHT_DATABASE_URL
        INSIGHT_TOKEN_BFF REVIEW_CONSOLE_KGE_DATABASE_URL REVIEW_CONSOLE_REVIEW_DATABASE_URL BFF_DATABASE_URL BFF_SHELL_ORIGIN BFF_LOG_HMAC_KEY
        BFF_AUTH_SECRET BFF_TRUST_SECRET BFF_CONSENT_VERSIONS STARBUDDY_PRESENCE_HMAC_KEY STARBUDDY_CONSENT_VERSION_PRIVACY
        STARBUDDY_CONSENT_VERSION_GUARDIAN SCHEDULER_DATABASE_URL)
  envs=(); for v in "${vars[@]}"; do envs+=("$v=$(syn "$v")"); done
  for f in deploy/compose/staging.yaml deploy/compose/local-test.yaml; do
    env "${envs[@]}" docker compose -f "$f" config -q || { echo "deploy-check: 失败（$f 结构校验）"; exit 1; }
  done
  echo "deploy-check: compose 结构校验通过（2 份）"
else
  skip "compose 结构校验（没有 docker compose 命令行）"
fi

if [ "${DEPLOY_BUILD:-0}" = "1" ]; then
  # 冒烟用 test 标记的镜像（staging 标记的镜像按设计拒绝以 test 运行）
  STARBUDDY_IMAGE_ENV=test bash deploy/scripts/build-images.sh || { echo "deploy-check: 失败（镜像构建）"; exit 1; }
  bash deploy/scripts/compose-smoke.sh; rc=$?
  if [ $rc -eq 3 ]; then skip "compose 冒烟（没有 docker 守护进程）"; elif [ $rc -ne 0 ]; then echo "deploy-check: 失败（compose 冒烟）"; exit 1; fi
else
  echo "deploy-check: 镜像构建与 compose 冒烟未执行（DEPLOY_BUILD=1 时执行；需要可拉取的基础镜像与 docker 守护进程）"
fi
echo "deploy-check: 通过"
