# 前端与适配器依赖的已知漏洞 · 实现记录

- 2026-11-24 用户“按建议，默认值都同意，开始实现”：一个 PR 做完；不进 CI 卡口，每月或升级前手动跑 `npm audit`、记进技术债清单；版本须发布满两周。

## 做了什么
- `apps/web/package.json`：`next` 16.3.6 → 16.4.6（精确版本）；`overrides` 加 `"source-map-js": "1.2.2"`。`npm install` 改了 5 个包
  （`next`、`@next/env`、各平台 `@next/swc-*`、`source-map-js`）；`LICENSES.md` 由 `license-check --write` 重生（只有版本号变化）。
- `adapters/{aya,wenli,zhixing}/package-lock.json`：`npm update fast-uri`，锁文件只动 `fast-uri` 一个包（3.1.8 → 3.1.9），`package.json` 不变。
- 技术债清单 `docs/gates/w13-debt-and-decisions.md` 新增 T53（定期核对），并记下这次首次核对的结果。
- 之后 `apps/web`、三个适配器、BFF、`tools/kge-coverage`、`tools/kge-inventory` 的 `npm audit` 全部为 0。

## 与 spec 的差异
- spec 初稿写“许可证都是 MIT”有误：`source-map-js`、`fast-uri` 是 BSD-3-Clause（与升级前相同，三个适配器 README 早有登记），已更正。

## 验证
- 三个适配器单测（`make adapter-aya-test`、`adapter-zhixing-test`、`adapter-wenli-test`）、`make conformance`、`make check-fast` 通过（`CONTRACT_STRICT=1`）。
- `make web-test`：见下一节（本机）；以 CI 为准。

## 待人确认
- 无新增。
