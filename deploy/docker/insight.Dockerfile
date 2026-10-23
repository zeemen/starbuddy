# 洞察服务镜像（LT-01，specs/launch-lt01-deploy-packaging 范围第 1 条）。做法同 services/learning/Dockerfile：
#   - 基础镜像以摘要给出（ARG PYTHON_IMAGE，没有缺省值；deploy/scripts/build-images.sh 拒绝不带 @sha256 的值；决定记录第 11 条）；
#   - 第三方包只从带哈希的锁文件装；不带任何密钥、真实数据或运行时配置（判断策略等签字配置在运行时挂载，INSIGHT_POLICY 等指向它）；
#   - 非 root（uid 10001）、只读根文件系统可运行、HTTP 健康检查（GET /readyz，LT-03）；上下文是仓库根，忽略规则见同名 .dockerignore（白名单）。
# 构建：docker build -f deploy/docker/insight.Dockerfile --build-arg PYTHON_IMAGE=<仓库>/python:3.11-slim-bookworm@sha256:<摘要> -t starbuddy-insight:<版本> .
# 目录结构按仓库保持（/srv/starbuddy 下）：洞察服务按相对位置读 packages/contracts/openapi、docs/design/sie/，并把 services/learning
# 加进 sys.path 复用学习服务的纯配置模块与审核台命令行的连接核对（insight/shared.py）。
# 运行：python -m insight.service（监听 INSIGHT_HOST:INSIGHT_PORT，缺省 127.0.0.1:8791；staging 显式配置内网 IPv4）。
# 合成占位配置（config/*.synthetic.json）随代码进镜像，但只在 INSIGHT_ENV=test 能加载；staging 必须挂载签字配置，否则拒绝启动。
ARG PYTHON_IMAGE
FROM ${PYTHON_IMAGE}

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /srv/starbuddy

COPY services/insight/requirements.lock services/insight/requirements.lock
RUN python -m pip install --require-hashes -r services/insight/requirements.lock

COPY packages/contracts/openapi packages/contracts/openapi
COPY docs/design/sie/sie_lexicon.py docs/design/sie/forbidden-inference.v0.yaml docs/design/sie/field-dictionary.v0.9.yaml docs/design/sie/no-promise.v0.draft.yaml docs/design/sie/
COPY services/learning/learning services/learning/learning
COPY services/learning/review_console services/learning/review_console
COPY services/learning/config services/learning/config
COPY services/insight/config services/insight/config
COPY services/insight/templates services/insight/templates
COPY services/insight/insight services/insight/insight

# 镜像固化的环境标记（LT-01 安全审查 S10）：staging 镜像拒绝以 dev / test 运行
ARG STARBUDDY_IMAGE_ENV
RUN case "${STARBUDDY_IMAGE_ENV}" in staging|test) ;; *) echo "STARBUDDY_IMAGE_ENV 必须是 staging 或 test" >&2; exit 1;; esac
ENV STARBUDDY_IMAGE_ENV=${STARBUDDY_IMAGE_ENV}
RUN useradd --system --uid 10001 --no-create-home --shell /usr/sbin/nologin starbuddy
USER 10001
WORKDIR /srv/starbuddy/services/insight
EXPOSE 8791
# 健康检查（LT-03）：HTTP GET /readyz（Host 头即监听地址，过 HostGuard）
HEALTHCHECK --interval=15s --timeout=3s --start-period=20s --retries=3 \
  CMD ["python", "-I", "-c", "import os,http.client as h; c=h.HTTPConnection(os.environ.get('INSIGHT_HOST') or '127.0.0.1', int(os.environ.get('INSIGHT_PORT') or 8791), timeout=2); c.request('GET', '/readyz'); raise SystemExit(0 if c.getresponse().status == 200 else 1)"]
CMD ["python", "-m", "insight.service"]
