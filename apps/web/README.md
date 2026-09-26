# apps/web · 统一壳前端（原型）

冲刺 3-H（`specs/star-map-sprint3/spec.md`）只有两页，外加一个开发登录页：

- `/star-map/[studentId]`：星图原型。每个在读学科一片星空，数据经 BFF 读平台 `mastery_estimate`，受 RLS 约束。
- `/dev/star-glyphs`：星形符号样张。三态加保持，16 / 24 / 32 / 48px，浅色与深色，还有点亮动效。
- `/dev/login`：开发身份桩，只列合成账号，只在 `STARBUDDY_DEV_AUTH=1` 时存在。4-H 删除。

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
2. 起 BFF：`STARBUDDY_DEV_AUTH=1 BFF_DATABASE_URL=… node services/bff/src/star-map/main.ts`；
3. 起前端：`STARBUDDY_DEV_AUTH=1 npm run build && npm start`；
4. 打开 `/dev/login`。
