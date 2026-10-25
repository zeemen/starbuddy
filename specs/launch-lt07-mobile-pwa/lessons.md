# LT-07 前端移动端与 PWA · 实现记录

2026-10-24 由 Claude 推进（specs/launch-test-2026）。提交首行 `[NEEDS-HUMAN]`：新增家长端可见文案（全部为 `{{TODO_COPY:pwa.*}}` 占位，须人写人审）、首次提交 16 张移动视觉基线（须视觉负责人签字）、Service Worker 上线（原 3-H / 4-H spec “不注册 Service Worker”的条款被本任务决定记录取代，已在两份旧 spec 末尾注明）。不改契约、迁移、适配器、BFF、人设 Prompt 与语料。

## 交付
- **移动端布局**：`e2e/mobile.spec.ts` 按页面登记表逐页检查（无横向滚动、触控 ≥ 44×44、输入框字号 ≥ 16px、固定元素不遮挡可点元素、移动视口 axe 无违规）；修复集中在 `globals.css`（安全区 `max(16px, env(safe-area-inset-*))`、≤767px 长串折行与控件限宽、点点入口改为页面末尾、触屏补足触控尺寸），桌面取值与基线不变；`layout.tsx` 加 `viewport-fit=cover`、`interactive-widget=resizes-content`、theme-color，不禁止缩放。Playwright 新增 `mobile-iphone13`、`mobile-pixel7`、`mobile-360`、`mobile-430` 四个项目跑 mobile / shell / visual-mobile。
- **PWA**（`src/pwa/`）：Manifest（standalone、start_url `/login`、占位主题色）；代码生成的占位图标；手写 Service Worker：只预缓存离线页、离线样式、manifest 与图标，`/_next/static` 下只缓存带 immutable、未重定向的同源构建产物；导航只走网络，断网回固定离线页；非 GET、跨源、带查询串、RSC、`/api`、`/bff` 一律不接管；激活时清旧版本；无推送 / 通知 / 后台同步。CSP 加 `worker-src 'self'`、`manifest-src 'self'`。
- **清缓存**：家长退出、退出学生模式、审核台退出、内测退出、数据删除确认成功后都请 SW 删除全部缓存（前端无“撤回同意”入口）。
- **停用开关** `STARBUDDY_PWA_KILL_SWITCH=1`：`/sw.js` 返回自毁版（删全部缓存、注销注册、让已打开页面重新导航），页面不再注册；`pwa-off` 前端与 `e2e/pwa-off.spec.ts` 验证。
- **微信内置浏览器**：家长首页“添加到主屏幕”说明按 UA 区分 iOS / 安卓 / 其他，微信与企业微信内提示“在浏览器打开”；OTP `autocomplete="one-time-code"`、cookie SameSite=Lax / HttpOnly / staging Secure 核对无需改动；微信 UA 下登录与学生模式端到端通过。
- **包体积预算** `tests/bundle-budget.test.ts` 接入 web-test（实测 gzip 后首屏 JS 174–187 KB、CSS 3.1 KB、静态 JS 总量 222 KB；阈值约 1.3 倍，占位待签字）。
- **静态约束**：只允许 `src/pwa/client.ts` 使用 Service Worker 接口（不区分大小写检查），`caches.` 只允许出现在 client.ts 与 SW 源码；禁推送断言；PWA 文案接入人设守卫（platform）与禁用推断词表扫描。
- **部署**：web 镜像只拷 `public/offline.html`、`offline.css`（dockerignore 同口径，测试断言 public/ 下只有这两个文件）；env-inventory 登记停用开关。

## 测试
子代理逐目标（严格模式）：web-test（端到端 173）、web-visual（31，桌面基线不变）、staging-mutation（89 + 前端 9）、deploy-check、persona-guard-test（131）、eval-persona（含 109 个变异）、contract-test 通过。严格 `make check` 见提交说明。

## 审查意见的处理
- 安全审查：无阻断。建议全部采纳：可靠停用（自毁版 + 开关）、重定向响应不缓存、dockerignore 收紧、删多余响应头、过时注释、更多结束会话路径清缓存。
- 契约审查：流程阻断“缺 lessons.md” → 本文件。建议采纳：PWA 文案扫描与壳文案同口径、静态检查收紧、补 430 宽；对账卡与星图卡片式 / 缩放改动不做（对账卡本身已是卡片；改信任界面表格结构须单独 PR 并附前后对比，规则 11）；e2e 实验开关全局设置经全量 e2e 验证稳定，保留。

## 教训
- 移动端问题要靠逐页自动检查发现（先写检查再修），人工走查漏得多：占位长串、固定定位遮挡、触控尺寸都是检查一跑就暴露。
- Service Worker 的“停用”不能依赖 `/sw.js` 返回 404（按规范只是更新失败），必须有自毁版本。
- 白名单式缓存（只列静态壳与带 immutable 的构建产物）比黑名单安全；登出清缓存只是兜底。
- Playwright 断网模拟默认管不到 SW 发出的请求，要设实验开关并先唤醒 SW 再断网。

## 待人确认
1. **家长端新增文案**（`src/pwa/copy.ts`：添加到主屏幕引导四段、离线页标题 / 正文 / “重新打开”、应用名“魁星点斗”）须人写人审。
2. **16 张移动视觉基线**首次提交，须视觉负责人签字；截图按 DPR 1。
3. 图标与主题色为占位，待设计提供。
4. 包体积阈值与 3G 首屏可交互目标待签字。
5. **WebKit 真机与真实微信**内的添加到主屏幕、离线回退、清缓存须人工核对（本机只有 Chromium，iPhone 机型为模拟）。
6. 自毁版的“旧注册经更新检查换成自毁版”这一步没有端到端覆盖（同源不能运行中翻开关），只由 vm 单测覆盖事件顺序。
7. 对账卡以外的信任界面表格改卡片式、星图小屏缩放与平移，如需要另起 PR。
8. `docs/security/` 中关于 PWA 的单独评估（household-pentest-round2 第 89 行）建议安排一次 SW 缓存范围复核。
