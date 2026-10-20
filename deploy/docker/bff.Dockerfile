# BFF 镜像（LT-01，specs/launch-lt01-deploy-packaging 范围第 1 条）。
#   - 多阶段：deps 阶段只按 package-lock.json 装运行依赖（npm ci --omit=dev --ignore-scripts：完整性哈希锁定、不跑安装脚本）；
#     运行阶段只拷依赖与源码（Node ≥ 22.18 直接运行 .ts，见 package.json engines）。
#   - 基础镜像以摘要给出（ARG NODE_IMAGE，没有缺省值；deploy/scripts/build-images.sh 拒绝不带 @sha256 的值；决定记录第 11 条）。
#   - 不带任何密钥、白名单 OTP 条目或审计文件：BFF_LOG_HMAC_KEY 等由运行环境注入，BFF_STAGING_OTP_FILE 只读挂载，
#     BFF_STAGING_OTP_AUDIT_FILE 写到可写卷（不在根文件系统上）。
#   - 非 root（uid 10001）、只读根文件系统可运行、TCP 健康检查。上下文是仓库根，忽略规则见同名 .dockerignore（白名单）。
# BFF 按仓库相对位置读 docs/design/sie/（禁用推断词表）与 services/insight/templates/（月度计划模板库，开关关时不读），镜像里保持同样结构。
# 构建：docker build -f deploy/docker/bff.Dockerfile --build-arg NODE_IMAGE=<仓库>/node:22-bookworm-slim@sha256:<摘要> -t starbuddy-bff:<版本> .
ARG NODE_IMAGE

FROM ${NODE_IMAGE} AS deps
WORKDIR /srv/starbuddy/services/bff
COPY services/bff/package.json services/bff/package-lock.json ./
RUN npm ci --omit=dev --ignore-scripts --no-audit --no-fund

FROM ${NODE_IMAGE}
ENV NODE_ENV=production \
    PORT=8787
WORKDIR /srv/starbuddy
COPY docs/design/sie/sie_lexicon.py docs/design/sie/forbidden-inference.v0.yaml docs/design/sie/no-promise.v0.draft.yaml docs/design/sie/
COPY services/insight/templates services/insight/templates
COPY --from=deps /srv/starbuddy/services/bff/node_modules services/bff/node_modules
COPY services/bff/package.json services/bff/package.json
COPY services/bff/config services/bff/config
COPY services/bff/src services/bff/src
# OTP 审计文件所在目录：编排在这里挂可写卷（只读根文件系统之外）；先建好并交给运行用户，命名卷首次挂载时继承属主
# 镜像固化的环境标记（LT-01 安全审查 S10）：staging 镜像拒绝以 dev / test 运行（src/http/config.ts checkImageEnv）
ARG STARBUDDY_IMAGE_ENV
RUN case "${STARBUDDY_IMAGE_ENV}" in staging|test) ;; *) echo "STARBUDDY_IMAGE_ENV 必须是 staging 或 test" >&2; exit 1;; esac
ENV STARBUDDY_IMAGE_ENV=${STARBUDDY_IMAGE_ENV}
RUN useradd --system --uid 10001 --no-create-home --shell /usr/sbin/nologin starbuddy \
 && mkdir -p /var/lib/starbuddy/audit && chown 10001:10001 /var/lib/starbuddy/audit && chmod 700 /var/lib/starbuddy/audit
USER 10001
WORKDIR /srv/starbuddy/services/bff
EXPOSE 8787
HEALTHCHECK --interval=15s --timeout=3s --start-period=20s --retries=3 \
  CMD ["node", "-e", "const s=require('node:net').connect(Number(process.env.PORT||8787),process.env.BFF_HOST||'127.0.0.1');s.setTimeout(2000);s.on('connect',()=>{s.end();process.exit(0)});s.on('error',()=>process.exit(1));s.on('timeout',()=>process.exit(1))"]
CMD ["node", "--no-warnings", "src/main.ts"]
