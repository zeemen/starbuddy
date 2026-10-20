# 学习服务镜像：用于 dev / staging（W13 技术债 T07，specs/tech-debt-batch1；LT-01 复核，specs/launch-lt01-deploy-packaging）。
# 不部署生产、不带任何密钥或真实数据（CLAUDE.md 规则 8、9）；学习服务只接受 LEARNING_ENV=dev|test|staging（learning/config.py，
# staging 拒绝合成配置与开发开关）。
#
# 构建（在仓库根目录，上下文是整个仓库；只取下面 COPY 的路径，其余由同目录的 Dockerfile.dockerignore 排除，.env、.venv、legacy/ 不进上下文）。
# 基础镜像必须以摘要给出（LT-01 决定记录第 11 条；正式来源是选定的境内云厂商镜像仓库，随 LT-03），没有缺省值，不给就构建失败：
#   docker build -f services/learning/Dockerfile --build-arg PYTHON_IMAGE=<仓库>/python:3.11-slim-bookworm@sha256:<摘要> -t starbuddy-learning:<版本> .
#   统一入口：deploy/scripts/build-images.sh（拒绝不带 @sha256 的基础镜像）。
# 运行：环境变量见 deploy/config/env/learning.yaml（LEARNING_ENV、LEARNING_DATABASE_URL、LEARNING_SERVICE_TOKEN、LEARNING_ADAPTER_* 等），
#   由运行环境注入，不写进镜像。只读根文件系统可运行（不写任何文件；PYTHONDONTWRITEBYTECODE）。
#
# 依赖：第三方包只从带哈希的锁文件装（requirements.lock，--require-hashes）；契约共享库非可编辑安装（--no-deps，依赖已在锁里）。
# 共享库按自己文件的位置找契约 YAML（site-packages 往上两级的 openapi/），所以装完用 scripts/place_contract_yaml.py 把
# packages/contracts/openapi 原样放过去并逐份核对（python -I：不读 PYTHONPATH 与当前目录，只用已安装的共享库；拒绝符号链接；
# 同一步骤的启动测试：tests/test_noneditable_install.py）。
# 已知未锁：构建共享库时 pip 在构建隔离环境里现拉 setuptools（pyproject 的 build-system 要求，不在锁文件里；见 README“镜像”一节）。
#
# 学习服务按仓库目录结构读几份文件，镜像里保持同样的相对位置（/srv/starbuddy 下）：
#   services/learning/config/（模型路由、掌握配置）、docs/design/sie/（禁用推断词表，掌握一致性检查用）、
#   docs/mapping/error-taxonomy.v0.9.yaml（再练选题的错因编码）。
#
# 监听地址：缺省只监听 127.0.0.1；staging 由 LEARNING_HOST 显式配置为容器网络的内网 IPv4（LT-01 决定记录第 1 条）。
# 三科适配器与本服务共用网络命名空间，经回环互访（决定记录第 5 条）。
# 健康检查：TCP 探活（连上监听端口即健康；HTTP 健康端点另起，决定记录第 11 条）。
ARG PYTHON_IMAGE
FROM ${PYTHON_IMAGE}

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /srv/starbuddy

# 先装锁定的第三方依赖（这一层只在锁文件变化时重建）
COPY services/learning/requirements.lock services/learning/requirements.lock
RUN python -m pip install --require-hashes -r services/learning/requirements.lock

# 契约共享库：非可编辑安装，再放置并核对契约 YAML
COPY packages/contracts/python/pyproject.toml packages/contracts/python/pyproject.toml
COPY packages/contracts/python/starbuddy_contract packages/contracts/python/starbuddy_contract
COPY packages/contracts/openapi packages/contracts/openapi
COPY services/learning/scripts/place_contract_yaml.py services/learning/scripts/place_contract_yaml.py
RUN python -m pip install --no-deps ./packages/contracts/python \
 && python -I services/learning/scripts/place_contract_yaml.py --source packages/contracts/openapi

# 服务代码与它按相对路径读取的文件
COPY docs/design/sie/sie_lexicon.py docs/design/sie/forbidden-inference.v0.yaml docs/design/sie/
COPY docs/mapping/error-taxonomy.v0.9.yaml docs/mapping/
COPY services/learning/config services/learning/config
COPY services/learning/learning services/learning/learning

# 镜像固化的环境标记（LT-01 安全审查 S10）：staging 镜像拒绝以 dev / test 运行（本机冒烟另构建 test 标记的镜像）
ARG STARBUDDY_IMAGE_ENV
RUN case "${STARBUDDY_IMAGE_ENV}" in staging|test) ;; *) echo "STARBUDDY_IMAGE_ENV 必须是 staging 或 test" >&2; exit 1;; esac
ENV STARBUDDY_IMAGE_ENV=${STARBUDDY_IMAGE_ENV}
RUN useradd --system --uid 10001 --no-create-home --shell /usr/sbin/nologin starbuddy
USER 10001
WORKDIR /srv/starbuddy/services/learning
EXPOSE 8788
HEALTHCHECK --interval=15s --timeout=3s --start-period=20s --retries=3 \
  CMD ["python", "-I", "-c", "import os,socket; socket.create_connection((os.environ.get('LEARNING_HOST') or '127.0.0.1', int(os.environ.get('PORT') or 8788)), 2).close()"]
CMD ["python", "-m", "learning"]
