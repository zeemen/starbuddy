# apps/web 依赖许可证清单

由 `node scripts/license-check.mjs --write` 从 `package-lock.json` 生成，不要手改。`make web-test` 会检查它与锁文件一致。

- 规则：不得有 GPL、AGPL、LGPL（CLAUDE.md 规则 10）；例外须人批准。
- `sharp` 由仓库内的占位包 `vendor/no-sharp` 顶替，锁文件里没有 `@img/sharp-libvips-*`（LGPL-3.0-or-later，ADR 07 待人确认 #2）。
- MPL-2.0 的 `axe-core` 只用于测试，不进构建产物（spec 待人确认 #13）。

## 汇总

| 许可证 | 包数 |
|---|---|
| MIT | 24 |
| Apache-2.0 | 6 |
| MPL-2.0 | 2 |
| 0BSD | 1 |
| BSD-3-Clause | 1 |
| CC-BY-4.0 | 1 |
| ISC | 1 |

## 明细

| 包 | 版本 | 许可证 | 用途 | 可选 |
|---|---|---|---|---|
| @axe-core/playwright | 4.13.0 | MPL-2.0 | 开发 |  |
| @next/env | 16.3.6 | MIT | 运行 |  |
| @next/swc-darwin-arm64 | 16.3.6 | MIT | 运行 | 可选 |
| @next/swc-darwin-x64 | 16.3.6 | MIT | 运行 | 可选 |
| @next/swc-linux-arm64-gnu | 16.3.6 | MIT | 运行 | 可选 |
| @next/swc-linux-arm64-musl | 16.3.6 | MIT | 运行 | 可选 |
| @next/swc-linux-x64-gnu | 16.3.6 | MIT | 运行 | 可选 |
| @next/swc-linux-x64-musl | 16.3.6 | MIT | 运行 | 可选 |
| @next/swc-win32-arm64-msvc | 16.3.6 | MIT | 运行 | 可选 |
| @next/swc-win32-x64-msvc | 16.3.6 | MIT | 运行 | 可选 |
| @playwright/test | 1.56.1 | Apache-2.0 | 开发（next 的可选同伴依赖） |  |
| @swc/helpers | 0.5.23 | Apache-2.0 | 运行 |  |
| @types/node | 22.20.4 | MIT | 开发 |  |
| @types/react-dom | 19.3.0 | MIT | 开发 |  |
| @types/react | 19.3.0 | MIT | 开发 |  |
| axe-core | 4.13.0 | MPL-2.0 | 开发 |  |
| baseline-browser-mapping | 2.11.26 | Apache-2.0 | 运行 |  |
| caniuse-lite | 1.0.30001812 | CC-BY-4.0 | 运行 |  |
| client-only | 0.0.1 | MIT | 运行 |  |
| csstype | 3.2.3 | MIT | 开发 |  |
| fsevents | 2.3.2 | MIT | 开发 | 可选 |
| nanoid | 3.3.19 | MIT | 运行 |  |
| next | 16.3.6 | MIT | 运行 |  |
| picocolors | 1.1.1 | ISC | 运行 |  |
| playwright-core | 1.56.1 | Apache-2.0 | 开发（next 的可选同伴依赖） |  |
| playwright | 1.56.1 | Apache-2.0 | 开发（next 的可选同伴依赖） |  |
| postcss | 8.5.23 | MIT | 运行 |  |
| react-dom | 19.3.0 | MIT | 运行 |  |
| react | 19.3.0 | MIT | 运行 |  |
| scheduler | 0.28.0 | MIT | 运行 |  |
| sharp | 0.0.0-starbuddy-stub | MIT | 运行 |  |
| source-map-js | 1.2.1 | BSD-3-Clause | 运行 |  |
| styled-jsx | 5.1.6 | MIT | 运行 |  |
| tslib | 2.8.1 | 0BSD | 运行 |  |
| typescript | 5.9.3 | Apache-2.0 | 开发 |  |
| undici-types | 6.21.0 | MIT | 开发 |  |
