# 前端与适配器依赖的已知漏洞：Next.js 升到 16.4.6，source-map-js、fast-uri 升补丁版 · 规格草稿

- 状态：**草稿，待人确认第五节**（2026-11-24）。
- 来源：2026-11-24 对仓库内所有带锁文件的 Node 包跑 `npm audit`（只读）。BFF、`tools/kge-coverage`、`tools/kge-inventory` 为 0；
  `apps/web` 2 项（1 critical、1 high），三个适配器各 1 项（moderate）。
- 不涉及必停项：只升已有依赖的版本，不新增依赖、不新增第三方服务；不改契约、迁移、计量、判分、人设。许可证不变（都是 MIT），
  `apps/web/LICENSES.md` 由 `node scripts/license-check.mjs --write` 重生。

## 一、现状（2026-11-24 核对）
### `apps/web`：`next` 16.3.6（直接依赖，精确版本）
`npm audit` 列出 16 条通告，修复版本 16.4.6（同一大版本，`isSemVerMajor: false`）。与本仓库用法对照：

| 通告 | 级别 | 本仓库是否受影响 |
|---|---|---|
| GHSA-p3m5-59mp-jv5r App Router 里 Server Actions 的拒绝服务（≤16.4.0） | high | **受影响**：作答、诊断、信任界面都走 server action |
| GHSA-2464-fhr4-w9xw 图片优化接口处理 SVG 时未认证远程代码执行（≤16.4.0） | critical | 大概率不受影响：`next.config.ts` 设了 `images.unoptimized: true`，但 `/_next/image` 路由是否仍注册未逐项核实 |
| GHSA-cjq9-62q9-8jv4 图片优化 SSRF；GHSA-7qx7-33qx-rqx2 SVG 拒收绕过 | high / moderate | 同上 |
| GHSA-673q-mjm6-r5xr 等 7 条 `use cache` / `unstable_cache` 相关（RCE、跨用户缓存泄露与投毒） | critical / moderate | 不受影响：源码里没有 `use cache`、`unstable_cache`，页面一律 no-store |
| GHSA-4jqv-mc3x-m676、GHSA-mcj8-r9mp-w47p SSG / ISR 缓存投毒 | moderate | 基本不受影响：页面动态渲染、不缓存 |
| GHSA-pc58-hwjq-p385 自托管时转发 fetch 响应的跨客户端注入与泄露 | moderate | **要核**：自托管（`next start`），server action 里 fetch BFF 后取字段返回，未原样转发响应体 |
| GHSA-f3mh-rxg3-f5g2 Edge 运行时路由经内部响应头暴露 HttpOnly cookie | moderate | 不受影响：没有 Edge 运行时路由 |
| GHSA-f87g-xv8r-7p7x 元数据图片路由；GHSA-39w2-rjm5-chcv 开发服务器 MCP 端点 | moderate / low | 不受影响或只影响本机开发 |

即使多数条目碰不到，Server Actions 那条直接命中；而且逐条证明不受影响，比升补丁版本贵。

### `apps/web`：`source-map-js` 1.2.1（经 `next → postcss@8.5.23`，间接依赖）
- GHSA-68fv-2mgg-jv7q 构造的 source map 让事件循环卡死（high），修复版本 1.2.2。只在构建时处理 CSS source map 时用到，运行时不加载；
  风险在构建机，不在线上。`postcss` 被 `next` 钉死在 8.5.23（它声明 `source-map-js: ^1.2.1`），只能用 `overrides` 指定版本。

### 三个适配器（`adapters/aya`、`wenli`、`zhixing`）：`fast-uri` 3.1.8（经 `ajv@8.20.0`，间接依赖）
- 4 条（2 moderate、2 low）：百分号编码的协议欺骗、主机名规范化混淆、相对引用归一化等，修复版本 3.1.9。
  适配器用 `ajv` 校验契约 JSON，`format: uri` 的字段会走到它；校验结果不直接用来发请求。只改锁文件即可（`ajv` 声明 `fast-uri: ^3.0.1`）。

### 试升结果（2026-11-24，只在本机工作区，未提交）
- `next` 改为 16.4.6、`overrides` 加 `source-map-js: 1.2.2`，`npm install` 改了 5 个包（`next`、`@next/env`、各平台 `@next/swc-*`、`source-map-js`），
  之后 `npm audit` 为 0；`license-check --write` 重生 `LICENSES.md`（11 行版本号变化，许可证不变）。
- 本机 `make web-test`：单测 280 项通过，生产构建通过；端到端 181 过、2 失败，失败的是 `e2e/glyphs.spec.ts` DPR 2 两条，
  与升级前在同一台机器上的结果完全相同（本机 Chromium 153 与 Playwright 自带版本不同的抗锯齿差异，CI 上这两条通过，见 PR zeemen/starbuddy#35）。
  即升级没有引入新的失败。试升改动已还原，实现时重做并以 CI 为准。

## 二、默认做法
1. **版本选择**：只选发布满两周的版本（与 Actions 升级同一口径）：`next` 16.4.6（2026-11-09 发布）、`source-map-js` 1.2.2（2026-09-30）、
   `fast-uri` 3.1.9（2026-10-19）。不用更新的 `source-map-js` 1.2.3 / 1.2.4（11-18、11-19 发布，不满两周）。
2. **`apps/web`**：`package.json` 的 `next` 改为 `16.4.6`（精确版本，同现在）；`overrides` 加 `"source-map-js": "1.2.2"`；重生锁文件与 `LICENSES.md`。
   `eslint-config-next` 之类的配套包本仓库没有，不用跟着升。
3. **三个适配器**：只更新锁文件里的 `fast-uri` 到 3.1.9（`npm update fast-uri` 后核对锁文件只动这一个包），`package.json` 不变。
4. **验证**：`make web-test`（单测、构建、端到端）、三个适配器的单测与契约测试（`make contract-test`）、`make check-fast`；
   核对生产构建里仍没有开发专用页面（`tests/build-output.test.ts`）、安全头与 CSP 不变（已有单测）。
5. **记录**：`specs/star-map-sprint3/lessons.md` 里写的 Next.js 16.3.6 不改（那是当时的记录）；新版本写在本任务 lessons。

## 三、不做
- 把 `npm audit` 加进 CI 卡口：要联网查通告库，结果随时间变化、会让旧提交突然变红；是否加、加在哪一级，放第五节第 2 条。
- Python 依赖的漏洞核对（`pip-audit` 要新装工具、联网查库）：另起任务。
- React 等其他依赖的升级：`npm audit` 没有报，不动。

## 四、验收
- `apps/web` 与三个适配器 `npm audit` 都为 0；`LICENSES.md` 与锁文件一致；上面第二节第 4 条的检查全部通过；CI 全绿。

## 五、待人确认（括号里是默认）
1. **范围**：Next.js 与两个间接依赖一次做完、一个 PR（默认）；还是先单独做 Next.js（命中 Server Actions 拒绝服务那条），适配器另起？
2. **以后怎么发现**：不加 CI 卡口，每月或升级前手动跑一次 `npm audit`，结果写进技术债清单（默认）；
   还是在 CI 里加一个只报告、不失败的 `npm audit --audit-level=high` 步骤（要联网，结果随时间变化）？
3. **版本要发布满两周**：沿用（默认）；遇到 critical 时是否允许用更新的修复版本（这次用不着，16.4.6 已满两周）？
