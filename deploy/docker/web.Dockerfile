# 前端镜像（apps/web，Next.js；LT-01 范围第 1 条）。部署形态待人确认（spec 待人确认 #2：静态导出 + CDN 还是 Node 运行时）；
# 本草案按 Node 运行时（next start）打包，没有改 next.config.ts（standalone 输出另议，见 lessons）。
#   - 多阶段：build 阶段按 package-lock.json 全量安装（构建要 TypeScript 等开发依赖；npm ci --ignore-scripts），生产构建
#     （不设 STARBUDDY_DEV_PAGES，开发专用页面不进构建产物，tests/build-output.test.ts 的口径）；运行阶段只拷构建产物与依赖。
#   - 基础镜像以摘要给出（ARG NODE_IMAGE，没有缺省值；决定记录第 11 条）。Next.js 遥测关闭。
#   - 只读根文件系统可运行：.next/cache 由编排挂 tmpfs；监听地址由 WEB_HOST 给（staging 为容器网络内网地址），
#     BFF 地址 STARBUDDY_BFF_URL 在 staging 只收内网地址（src/lib/bff.ts）。
#   - 非 root（uid 10001）、HTTP 健康检查（GET /readyz，LT-03）；不带任何密钥（STARBUDDY_PRESENCE_HMAC_KEY 等由运行环境注入）。
# 构建：docker build -f deploy/docker/web.Dockerfile --build-arg NODE_IMAGE=<仓库>/node:22-bookworm-slim@sha256:<摘要> -t starbuddy-web:<版本> .
# 已知：运行阶段保留全部依赖（含开发依赖），保证 next start 能读 next.config.ts；瘦身随部署形态决定（待人确认）。
ARG NODE_IMAGE

FROM ${NODE_IMAGE} AS build
ENV NEXT_TELEMETRY_DISABLED=1
WORKDIR /srv/starbuddy/apps/web
COPY apps/web/package.json apps/web/package-lock.json ./
COPY apps/web/vendor ./vendor
RUN npm ci --ignore-scripts --no-audit --no-fund
# 舞台样张页（开发专用）在源码里引用 packages/stage：生产构建不含该页，但类型检查要能解析，保持仓库相对结构
COPY packages/stage/package.json /srv/starbuddy/packages/stage/package.json
COPY packages/stage/src /srv/starbuddy/packages/stage/src
COPY packages/stage/schema /srv/starbuddy/packages/stage/schema
COPY apps/web/next.config.ts apps/web/tsconfig.json ./
COPY apps/web/src ./src
RUN npx --no-install next build

FROM ${NODE_IMAGE}
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    PORT=3000 \
    WEB_HOST=127.0.0.1
WORKDIR /srv/starbuddy/apps/web
COPY --from=build /srv/starbuddy/apps/web/package.json ./package.json
COPY --from=build /srv/starbuddy/apps/web/next.config.ts ./next.config.ts
COPY --from=build /srv/starbuddy/apps/web/node_modules ./node_modules
COPY --from=build /srv/starbuddy/apps/web/.next ./.next
# 启动包装（LT-01 安全审查 A1）：先核对运行环境、监听地址、镜像标记与 staging 的同意版本，再起 next start
COPY apps/web/scripts/serve.ts ./scripts/serve.ts
COPY apps/web/src/lib/serve-env.ts apps/web/src/lib/consent-version.ts ./src/lib/
# 镜像固化的环境标记（LT-01 安全审查 S10）：staging 镜像拒绝以 dev / test 运行
ARG STARBUDDY_IMAGE_ENV
RUN case "${STARBUDDY_IMAGE_ENV}" in staging|test) ;; *) echo "STARBUDDY_IMAGE_ENV 必须是 staging 或 test" >&2; exit 1;; esac
ENV STARBUDDY_IMAGE_ENV=${STARBUDDY_IMAGE_ENV}
RUN useradd --system --uid 10001 --no-create-home --shell /usr/sbin/nologin starbuddy
USER 10001
EXPOSE 3000
# 健康检查（LT-03）：HTTP GET /readyz（启动包装同一套环境核对）
HEALTHCHECK --interval=15s --timeout=3s --start-period=30s --retries=3 \
  CMD ["node", "-e", "require('node:http').get({host:process.env.WEB_HOST||'127.0.0.1',port:Number(process.env.PORT||3000),path:'/readyz',timeout:2000},(r)=>process.exit(r.statusCode===200?0:1)).on('error',()=>process.exit(1)).on('timeout',()=>process.exit(1))"]
CMD ["node", "--no-warnings", "scripts/serve.ts"]
