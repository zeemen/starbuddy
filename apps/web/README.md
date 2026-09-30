# apps/web · 统一壳前端（原型）

冲刺 3-H（`specs/star-map-sprint3/spec.md`）起的页面；4-H（`specs/unified-shell-sprint4/spec.md`）H1 加了家庭登录，H2 删除了开发身份桩：

- `/login`、`/family`、`/student`：家庭登录（手机号 + 验证码，开发与测试只有短信捕获桩）、家长首页、学生模式落地页（H1）。
- `/s/[studentId]/star-map`（学生）与 `/family/[studentId]/star-map`（家长）：星图原型（H2 挂到真实会话；原 `/star-map/[studentId]` 已删除）。
  每个在读学科一片星空，数据经 BFF 读平台 `mastery_estimate`，受 RLS 约束。
- `/onboarding/[studentId]`：建档（SIE-01），学生版与家长版由会话身份决定。
- 开发专用页面（文件名 `page.dev.tsx`）：`/dev/star-glyphs` 符号样张、`/dev/probe/[studentId]` 探针演示（2-L）、`/dev/diagnosis/[studentId]`
  数学诊断演示（SIE-02a）。只有构建时 `STARBUDDY_DEV_PAGES=1` 且 `STARBUDDY_ENV` 为 dev / test 才是页面（构建期常量，`next.config.ts`
  的 `pageExtensions`），staging、pilot、生产构建里没有这些路由（`tests/build-output.test.ts`）；运行时再核对环境与本机 Host。
  演示页与正式页面一样只认真实会话。
- `/review`：审核台 v0（staff 专用，specs/release-gate-sprint4 冲刺 1 · PR 3）。地图版本列表、差异审核、节点边界编辑、签字、发布、撤下、版本记录。
  服务端组件与 server action 经审核台后端（`STARBUDDY_REVIEW_URL`，只允许 loopback；`services/learning/review_console`）读写；
  staff 用 `/review/login` 登录（登录名 + 口令 + 验证器 App 的 6 位码，PR 4）；会话令牌放在 httpOnly、SameSite=Strict 的 cookie 里
  （正式环境 `__Host-sb_staff`，本地 dev / test 为 `sb_staff`），前端只转交，身份由库函数解析。凭据由人用 `platform.staff_enroll` 建立。

## 边界
- 浏览器只和本前端通信；服务端组件与 server action 经 BFF 取数据（`STARBUDDY_BFF_URL`：开发与测试只允许本机 loopback 的 http，
  staging、pilot、生产只允许内网地址，`src/lib/bff.ts`）。浏览器不直连 BFF、数据库或学习服务。
- 身份只来自会话（`src/lib/session.ts`）：HttpOnly cookie 里的会话令牌放进 `x-sb-session` 转交；改变状态的请求另带浏览器的 Origin 与
  CSRF 令牌（表单或组件带回的令牌必须等于 CSRF cookie）。前端不判断授权。
- 安全头（spec 第 16 节）：`src/proxy.ts` 为每个请求生成 nonce，CSP 不含 `unsafe-inline`（生产构建也不含 `unsafe-eval`）、
  `frame-ancestors 'none'`；staging、pilot、生产另发 HSTS。主题变量是静态文件 `src/app/theme.css`，组件里不写 style 属性。
- 前端不推算掌握状态，只认 BFF 给的 `display`、`retained`、`needs_verification`（规则 2）。
- 以下都不做：页面一律 `no-store`，不用 localStorage、IndexedDB，不注册 Service Worker，不加载境外字体或 CDN（ADR 09）。
- 不装 `sharp`：用 `vendor/no-sharp` 占位包顶替，并关闭图片优化（ADR 07 待人确认 #2）。许可证清单见 `LICENSES.md`。
- Next.js 遥测一律关闭（`NEXT_TELEMETRY_DISABLED=1`）。

## 本地运行
```bash
cd apps/web && npm ci && npx playwright install chromium   # 浏览器只处理合成数据
make web-test      # 许可证清单、next build（含类型检查）、单测与静态检查、端到端（一次性 Postgres + BFF，全程真实会话）
make web-visual    # 视觉回归（与 e2e/__screenshots__ 的基线比较）；UPDATE=1 更新基线
```

开发库手动试用：
1. 跑阿鸦内容登记和 `db/seeds/dev/star-map-synthetic.sql`、`db/seeds/dev/auth-synthetic.sql`（执行种子时设
   `PGOPTIONS='-c starbuddy.allow_synthetic_seed=on'`）；
2. 起 BFF：`STARBUDDY_ENV=dev BFF_SMS_CAPTURE_FILE=/tmp/sb-sms BFF_DATABASE_URL=… node services/bff/src/main.ts`
   （验证码走短信捕获桩，只收 +999 合成号码，写进 `BFF_SMS_CAPTURE_FILE`）；
3. 起前端：`STARBUDDY_DEV_PAGES=1 STARBUDDY_ENV=dev npm run build && STARBUDDY_ENV=dev npm start`（用 127.0.0.1 或 localhost 访问）；
4. 打开 `/login`，用合成家长号码 `+99900000101` 登录，从捕获文件读验证码；在家长首页为孩子打开学生模式或查看星图。
