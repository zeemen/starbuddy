# apps/web · 统一壳前端（原型）

冲刺 3-H（`specs/star-map-sprint3/spec.md`）只有两页，外加一个开发登录页：

- `/star-map/[studentId]`：星图原型。每个在读学科一片星空，数据经 BFF 读平台 `mastery_estimate`，受 RLS 约束。
- `/dev/star-glyphs`：星形符号样张。三态加保持，16 / 24 / 32 / 48px，浅色与深色，还有点亮动效。
- `/dev/login`：开发身份桩，只列合成账号，只在 `STARBUDDY_DEV_AUTH=1` 时存在。4-H 删除。
- `/dev/probe/[studentId]`：“能做对吗”探针演示页（冲刺 2-L）。文件名 `page.dev.tsx`：只有构建时 `STARBUDDY_DEV_AUTH=1` 且
  `STARBUDDY_ENV` 为 dev / test 才是页面（`next.config.ts` 的 `pageExtensions`），生产构建里没有这个路由（`tests/build-output.test.ts`）。
  组件 `src/probe/ConfidenceProbe.tsx`，文案在 `src/probe/copy.ts`（过点点禁语表，待人设评审）。`scripts/e2e.sh` 会把 `.next` 重建成测试构建。
- `/review`：审核台 v0（staff 专用，specs/release-gate-sprint4 冲刺 1 · PR 3）。地图版本列表、差异审核、节点边界编辑、签字、发布、撤下、版本记录。
  服务端组件与 server action 经审核台后端（`STARBUDDY_REVIEW_URL`，只允许 loopback；`services/learning/review_console`）读写；
  staff 用 `/review/login` 登录（登录名 + 口令 + 验证器 App 的 6 位码，PR 4）；会话令牌放在 httpOnly、SameSite=Strict 的 cookie 里
  （正式环境 `__Host-sb_staff`，本地 dev / test 为 `sb_staff`），前端只转交，身份由库函数解析。凭据由人用 `platform.staff_enroll` 建立。

## 边界
- 浏览器只和本前端通信；服务端组件经 BFF 取数据（`STARBUDDY_BFF_URL`，只允许 loopback）。浏览器不直连 BFF、数据库或学习服务。
- 前端不推算掌握状态，只认 BFF 给的 `display`、`retained`、`needs_verification`（规则 2）。
- 以下都不做：不缓存星图页（`no-store`），不用 localStorage、IndexedDB，不注册 Service Worker，不加载境外字体或 CDN（ADR 09）。
- 不装 `sharp`：用 `vendor/no-sharp` 占位包顶替，并关闭图片优化（ADR 07 待人确认 #2）。许可证清单见 `LICENSES.md`。
- Next.js 遥测一律关闭（`NEXT_TELEMETRY_DISABLED=1`）。

## 本地运行
```bash
cd apps/web && npm ci && npx playwright install chromium   # 浏览器只处理合成数据
make web-test      # 许可证清单、next build（含类型检查）、单测与静态检查、端到端（一次性 Postgres + BFF）
make web-visual    # 视觉回归（与 e2e/__screenshots__ 的基线比较）；UPDATE=1 更新基线
```

开发库手动试用：
1. 跑阿鸦内容登记和 `db/seeds/dev/star-map-synthetic.sql`；
2. 执行种子时设 `PGOPTIONS='-c starbuddy.allow_synthetic_seed=on'`；起 BFF：`STARBUDDY_ENV=dev STARBUDDY_DEV_AUTH=1 BFF_DATABASE_URL=… node services/bff/src/star-map/main.ts`（4-H H1 起必须设 `STARBUDDY_ENV`；家庭登录页 `/login` 的验证码走短信捕获桩，可设 `BFF_SMS_CAPTURE_FILE` 查看）；
3. 起前端：`STARBUDDY_DEV_AUTH=1 STARBUDDY_ENV=dev npm run build && STARBUDDY_DEV_AUTH=1 STARBUDDY_ENV=dev npm start`
   （开发身份桩要两个条件都满足；用 127.0.0.1 或 localhost 访问，其他 Host 一律当未登录）；
4. 打开 `/dev/login`。
