# 调度器镜像（LT-01，specs/launch-lt01-deploy-packaging 范围第 1 条；调度器设计见 services/scheduler/README.md）。
# 调度器按时间表起子进程：services/<服务>/.venv/bin/python -m <模块>（scheduler/runner.py），所以镜像里按同样路径建两个虚拟环境：
#   services/insight/.venv   洞察服务的锁（insight.scheduled、insight.jobs；调度器本身也用这一份，锁文件逐字相同）
#   services/learning/.venv  学习服务的锁 + 契约共享库（learning.jobs mastery-sweep、review_console expire-affairs）
# 做法同 services/learning/Dockerfile：基础镜像以摘要给出（ARG PYTHON_IMAGE，没有缺省值；决定记录第 11 条）；只从带哈希的锁文件装；
# 非 root（uid 10001）、只读根文件系统可运行；不带密钥与签字时间表（staging 只收签字时间表，运行时挂载，--schedule 指定）。
# 调度器不监听端口：健康检查只核对进程与时间表可读（--dry-run 不连库、不起子进程）。
# 构建：docker build -f deploy/docker/scheduler.Dockerfile --build-arg PYTHON_IMAGE=<仓库>/python:3.11-slim-bookworm@sha256:<摘要> -t starbuddy-scheduler:<版本> .
ARG PYTHON_IMAGE
FROM ${PYTHON_IMAGE}

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /srv/starbuddy

COPY services/insight/requirements.lock services/insight/requirements.lock
COPY services/learning/requirements.lock services/learning/requirements.lock
RUN python -m venv services/insight/.venv \
 && services/insight/.venv/bin/python -m pip install --require-hashes -r services/insight/requirements.lock \
 && python -m venv services/learning/.venv \
 && services/learning/.venv/bin/python -m pip install --require-hashes -r services/learning/requirements.lock

COPY packages/contracts/python/pyproject.toml packages/contracts/python/pyproject.toml
COPY packages/contracts/python/starbuddy_contract packages/contracts/python/starbuddy_contract
COPY packages/contracts/openapi packages/contracts/openapi
COPY services/learning/scripts/place_contract_yaml.py services/learning/scripts/place_contract_yaml.py
RUN services/learning/.venv/bin/python -m pip install --no-deps ./packages/contracts/python \
 && services/learning/.venv/bin/python -I services/learning/scripts/place_contract_yaml.py --source packages/contracts/openapi

COPY docs/design/sie/sie_lexicon.py docs/design/sie/forbidden-inference.v0.yaml docs/design/sie/field-dictionary.v0.9.yaml docs/design/sie/no-promise.v0.draft.yaml docs/design/sie/
COPY docs/mapping/error-taxonomy.v0.9.yaml docs/mapping/
COPY services/learning/config services/learning/config
COPY services/learning/learning services/learning/learning
COPY services/learning/review_console services/learning/review_console
COPY services/insight/config services/insight/config
COPY services/insight/templates services/insight/templates
COPY services/insight/insight services/insight/insight
COPY services/scheduler/config services/scheduler/config
COPY services/scheduler/scheduler services/scheduler/scheduler

# 镜像固化的环境标记（LT-01 安全审查 S10）：staging 镜像拒绝以 dev / test 运行（子进程环境白名单不转交这个变量，由调度器自己核对）
ARG STARBUDDY_IMAGE_ENV
RUN case "${STARBUDDY_IMAGE_ENV}" in staging|test) ;; *) echo "STARBUDDY_IMAGE_ENV 必须是 staging 或 test" >&2; exit 1;; esac
ENV STARBUDDY_IMAGE_ENV=${STARBUDDY_IMAGE_ENV}
RUN useradd --system --uid 10001 --no-create-home --shell /usr/sbin/nologin starbuddy
USER 10001
WORKDIR /srv/starbuddy/services/scheduler
HEALTHCHECK --interval=60s --timeout=10s --start-period=20s --retries=3 \
  CMD ["/srv/starbuddy/services/insight/.venv/bin/python", "-I", "-c", "import os; os.kill(1, 0)"]
ENTRYPOINT ["/srv/starbuddy/services/insight/.venv/bin/python", "-m", "scheduler"]
