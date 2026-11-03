# 审核台后端镜像（LT-01，决定记录第 5 条；代码在 services/learning/review_console，与学习服务不共用进程、连接角色与路由）。
# 做法同 services/learning/Dockerfile：基础镜像以摘要给出（ARG PYTHON_IMAGE，没有缺省值）；只从带哈希的锁文件装（与学习服务同一份锁）；
# 非 root（uid 10001）、只读根文件系统可运行、HTTP 健康检查（GET /readyz，LT-03）；不带密钥与 staff 凭据（staff 由人用 platform.staff_enroll 建立）。
# 运行：python -m review_console serve --port <端口>（监听 REVIEW_CONSOLE_HOST，缺省 127.0.0.1；staging 显式配置内网 IPv4）。
# 构建：docker build -f deploy/docker/review-console.Dockerfile --build-arg PYTHON_IMAGE=<仓库>/python:3.11-slim-bookworm@sha256:<摘要> -t starbuddy-review-console:<版本> .
ARG PYTHON_IMAGE
FROM ${PYTHON_IMAGE}

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    REVIEW_CONSOLE_PORT=8792

WORKDIR /srv/starbuddy

COPY services/learning/requirements.lock services/learning/requirements.lock
RUN python -m pip install --require-hashes -r services/learning/requirements.lock

COPY packages/contracts/python/pyproject.toml packages/contracts/python/pyproject.toml
COPY packages/contracts/python/starbuddy_contract packages/contracts/python/starbuddy_contract
COPY packages/contracts/openapi packages/contracts/openapi
COPY services/learning/scripts/place_contract_yaml.py services/learning/scripts/place_contract_yaml.py
RUN python -m pip install --no-deps ./packages/contracts/python \
 && python -I services/learning/scripts/place_contract_yaml.py --source packages/contracts/openapi

COPY docs/design/sie/sie_lexicon.py docs/design/sie/forbidden-inference.v0.yaml docs/design/sie/
COPY docs/mapping/error-taxonomy.v0.9.yaml docs/mapping/
COPY services/learning/config services/learning/config
COPY services/learning/learning services/learning/learning
COPY services/learning/review_console services/learning/review_console

# 镜像固化的环境标记（LT-01 安全审查 S10）：staging 镜像拒绝以 dev / test 运行
ARG STARBUDDY_IMAGE_ENV
RUN case "${STARBUDDY_IMAGE_ENV}" in staging|test) ;; *) echo "STARBUDDY_IMAGE_ENV 必须是 staging 或 test" >&2; exit 1;; esac
ENV STARBUDDY_IMAGE_ENV=${STARBUDDY_IMAGE_ENV}
RUN useradd --system --uid 10001 --no-create-home --shell /usr/sbin/nologin starbuddy
USER 10001
WORKDIR /srv/starbuddy/services/learning
EXPOSE 8792
# 健康检查（LT-03）：HTTP GET /readyz（Host 头即监听地址：回环，或 staging 显式配置的内网监听地址）
HEALTHCHECK --interval=15s --timeout=3s --start-period=20s --retries=3 \
  CMD ["python", "-I", "-c", "import os,http.client as h; c=h.HTTPConnection(os.environ.get('REVIEW_CONSOLE_HOST') or '127.0.0.1', int(os.environ.get('REVIEW_CONSOLE_PORT') or 8792), timeout=2); c.request('GET', '/readyz'); raise SystemExit(0 if c.getresponse().status == 200 else 1)"]
CMD ["sh", "-c", "exec python -m review_console serve --port \"$REVIEW_CONSOLE_PORT\""]
