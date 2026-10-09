# 学习服务镜像：只用于 dev / staging（W13 技术债 T07，specs/tech-debt-batch1）。不部署生产、不带任何密钥或真实数据（CLAUDE.md 规则 8、9）；
# 学习服务本身只接受 LEARNING_ENV=dev|test（learning/config.py）。
#
# 构建（在仓库根目录，上下文是整个仓库；只取下面 COPY 的路径，其余由同目录的 Dockerfile.dockerignore 排除，.env、.venv、legacy/ 不进上下文）：
#   docker build -f services/learning/Dockerfile -t starbuddy-learning:dev .
# 运行：环境变量同 services/learning/README.md“常用命令”（LEARNING_ENV、LEARNING_DATABASE_URL、LEARNING_SERVICE_TOKEN、LEARNING_ADAPTER_*），
#   由运行环境注入，不写进镜像。
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
# 监听地址：python -m learning 只监听 127.0.0.1（learning/__main__.py，由平台侧网关转发），容器里也只有同一网络命名空间
# （同一 Pod 的网关边车）能访问；要不要改监听地址随 ADR 10 部署拓扑定，本镜像不改。
#
# 基础镜像用的是版本标签，没有固定到摘要（本会话无法核实摘要）；纳入 staging 前由仓库管理员固定到 @sha256 摘要。
FROM python:3.11-slim-bookworm

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

RUN useradd --system --uid 10001 --no-create-home starbuddy
USER 10001
WORKDIR /srv/starbuddy/services/learning
CMD ["python", "-m", "learning"]
