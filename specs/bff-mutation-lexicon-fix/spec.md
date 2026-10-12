# 修两条“碰巧被发现”的 BFF 变异

2026-10-12 用户要求处理（PLT-14 第 1 步 lessons 第 15 条）。只改测试与变异脚本，不改业务代码、迁移、契约。

## 背景
`services/bff/tests/mutations.sh` 的变异副本（`star_copy`）默认不带 `docs/`，禁用推断词表（`src/trust/lexicon.ts` 读 `docs/design/sie/`）读不到时带文字的路由 fail closed（503）。
“信任：周报收已撤回的判断”（`src/http/trust-routes.ts`）与“报告：不核对受众”（`src/trust/report.ts`）两条变异因此被“发现”，并不是对应断言抓到；副本带上词表后两条漏检。

## 范围
1. 所有变异副本与对照组一律链接 `docs/`（去掉只给“诉求：”“月度计划：”组链接的特例），使检测反映真实断言；更新相关注释。
2. 在 `tests/trust-units.test.ts` / `tests/trust.integration.test.ts` 补行为断言：周报只收 `hypothesis`、`established` 状态的判断，已撤回（及其他状态）的判断不出现在周报里。
3. 在 `tests/report-units.test.ts` / `tests/report.integration.test.ts` 补行为断言：报告的 `audience` 与请求受众不一致时拒绝（按现有错误形态）。
4. 全量 `make bff-mutation` 全部被发现，且无“第一条失败不是登记的断言”。

## 验收
- 两条变异在带词表的副本里被对应断言发现；全量 bff-mutation 全部发现；`make bff-test` 通过；严格 `make check` 通过。
