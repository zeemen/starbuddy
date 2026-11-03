# 知识服务镜像（LT-01，specs/launch-lt01-deploy-packaging 范围第 1 条）。做法同 services/learning/Dockerfile：
#   - 基础镜像以摘要给出（ARG PYTHON_IMAGE，没有缺省值；决定记录第 11 条）；第三方包只从带哈希的锁文件装；
#   - 不带任何密钥或页面快照：快照目录（KNOWLEDGE_PAGE_SNAPSHOT 或指针）在运行时只读挂载；
#   - 非 root（uid 10001）；只读根文件系统可运行：分词器缓存写到 KNOWLEDGE_CACHE_DIR（缺省 /tmp/knowledge-cache，编排里挂 tmpfs）；
#   - HTTP 健康检查（GET /readyz，LT-03）；上下文是仓库根，忽略规则见同名 .dockerignore（白名单）。
# 构建：docker build -f deploy/docker/knowledge.Dockerfile --build-arg PYTHON_IMAGE=<仓库>/python:3.11-slim-bookworm@sha256:<摘要> -t starbuddy-knowledge:<版本> .
# 运行：python -m knowledge.service（监听 KNOWLEDGE_HOST:KNOWLEDGE_PORT，缺省 127.0.0.1:8790；staging 显式配置内网 IPv4）。
# 已知：jieba 只有源码包，安装时本地构建（纯 Python，见 services/knowledge/requirements.txt）。
ARG PYTHON_IMAGE
FROM ${PYTHON_IMAGE}

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    KNOWLEDGE_CACHE_DIR=/tmp/knowledge-cache

WORKDIR /srv/starbuddy

COPY services/knowledge/requirements.lock services/knowledge/requirements.lock
RUN python -m pip install --require-hashes -r services/knowledge/requirements.lock

COPY packages/contracts/openapi packages/contracts/openapi
COPY services/knowledge/knowledge services/knowledge/knowledge

# 镜像固化的环境标记（LT-01 安全审查 S10）：staging 镜像拒绝以 dev / test 运行
ARG STARBUDDY_IMAGE_ENV
RUN case "${STARBUDDY_IMAGE_ENV}" in staging|test) ;; *) echo "STARBUDDY_IMAGE_ENV 必须是 staging 或 test" >&2; exit 1;; esac
ENV STARBUDDY_IMAGE_ENV=${STARBUDDY_IMAGE_ENV}
RUN useradd --system --uid 10001 --no-create-home --shell /usr/sbin/nologin starbuddy
USER 10001
WORKDIR /srv/starbuddy/services/knowledge
EXPOSE 8790
# 健康检查（LT-03）：HTTP GET /readyz（Host 头即监听地址，过 HostGuard）
HEALTHCHECK --interval=15s --timeout=3s --start-period=20s --retries=3 \
  CMD ["python", "-I", "-c", "import os,http.client as h; c=h.HTTPConnection(os.environ.get('KNOWLEDGE_HOST') or '127.0.0.1', int(os.environ.get('KNOWLEDGE_PORT') or 8790), timeout=2); c.request('GET', '/readyz'); raise SystemExit(0 if c.getresponse().status == 200 else 1)"]
CMD ["python", "-m", "knowledge.service"]
