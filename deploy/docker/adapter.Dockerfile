# 三科适配器镜像（LT-01，范围第 1 条；决定记录第 5 条：适配器不改代码，staging 下与学习服务共用网络命名空间经回环互访）。
# 一份 Dockerfile 三科共用：--build-arg ADAPTER=aya|zhixing|wenli。
#   - 多阶段：deps 阶段只按 package-lock.json 装运行依赖（npm ci --omit=dev --ignore-scripts）；运行阶段只拷依赖、源码与内容包。
#   - 基础镜像以摘要给出（ARG NODE_IMAGE，没有缺省值；决定记录第 11 条）。
#   - 适配器按仓库相对位置读 packages/contracts/openapi（插件契约）与 docs/mapping/error-taxonomy.v0.9.yaml（错因映射），镜像里保持同样结构。
#   - 只监听 127.0.0.1:$PORT（src/main.ts，不改）；不带令牌（<学科>_ADAPTER_SERVICE_TOKEN 由运行环境注入）；不读任何模型供应商地址或 Key。
#   - 非 root（uid 10001）、只读根文件系统可运行、TCP 健康检查（回环）。
# 构建：docker build -f deploy/docker/adapter.Dockerfile --build-arg NODE_IMAGE=<仓库>/node:22-bookworm-slim@sha256:<摘要> --build-arg ADAPTER=aya -t starbuddy-adapter-aya:<版本> .
ARG NODE_IMAGE

FROM ${NODE_IMAGE} AS deps
ARG ADAPTER
RUN case "$ADAPTER" in aya|zhixing|wenli) ;; *) echo "ADAPTER 必须是 aya、zhixing 或 wenli" >&2; exit 1;; esac
WORKDIR /srv/starbuddy/adapters/${ADAPTER}
COPY adapters/${ADAPTER}/package.json adapters/${ADAPTER}/package-lock.json ./
RUN npm ci --omit=dev --ignore-scripts --no-audit --no-fund

FROM ${NODE_IMAGE}
ARG ADAPTER
ENV NODE_ENV=production
WORKDIR /srv/starbuddy
COPY packages/contracts/openapi packages/contracts/openapi
COPY docs/mapping/error-taxonomy.v0.9.yaml docs/mapping/
COPY --from=deps /srv/starbuddy/adapters/${ADAPTER}/node_modules adapters/${ADAPTER}/node_modules
COPY adapters/${ADAPTER}/package.json adapters/${ADAPTER}/package.json
COPY adapters/${ADAPTER}/content adapters/${ADAPTER}/content
COPY adapters/${ADAPTER}/src adapters/${ADAPTER}/src
# 镜像固化的环境标记（LT-01 安全审查 S10）：适配器不改代码、不读这个标记，只为与其他镜像同一构建口径（便于核对镜像来源）
ARG STARBUDDY_IMAGE_ENV
RUN case "${STARBUDDY_IMAGE_ENV}" in staging|test) ;; *) echo "STARBUDDY_IMAGE_ENV 必须是 staging 或 test" >&2; exit 1;; esac
ENV STARBUDDY_IMAGE_ENV=${STARBUDDY_IMAGE_ENV}
RUN useradd --system --uid 10001 --no-create-home --shell /usr/sbin/nologin starbuddy
USER 10001
WORKDIR /srv/starbuddy/adapters/${ADAPTER}
HEALTHCHECK --interval=15s --timeout=3s --start-period=20s --retries=3 \
  CMD ["node", "-e", "const s=require('node:net').connect(Number(process.env.PORT),'127.0.0.1');s.setTimeout(2000);s.on('connect',()=>{s.end();process.exit(0)});s.on('error',()=>process.exit(1));s.on('timeout',()=>process.exit(1))"]
CMD ["node", "--no-warnings", "src/main.ts"]
