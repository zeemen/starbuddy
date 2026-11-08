# 技术债第二批 · 实现记录

2026-11-08 用户确认（“按建议，默认值都同意，开始实现”），待人确认 1–3 全部按默认。不涉及必停项。

## 交付

### A. T21：陈述—判断一致性的全站爬取
- `apps/web/e2e/claim-binding.ts`：真相来自一次性测试库的判断账本（`insight.claim`，经 psql 只读），不信页面自己说的状态。断言：
  1. 每个 `data-claim-id` 都是账本里本人的判断，状态成立、假设或待验证且没过期；待验证的只画虚线（标了画法就必须是 dashed，有结论句必须在 dashed 里）；
  2. 已推翻、已撤回的判断与别的学生的判断，ID 不出现在页面 HTML 的任何位置（含链接、属性与 `<script>` 里的服务端组件载荷）；
  3. 合成判断模板的渲染句（库里的模板一律以“【占位模板”开头，文案文件里没有这个前缀）只出现在带 `data-claim-id` 的元素里，
     不出现在标题、说明类属性里；给了“句子 → 判断”对照（库里的周报句子）时，句子必须挂在对应的判断上；
  4. 每套另有阳性对照：确实爬到了带判断的页面，核对不是空转。
- 接入四套：`trust-on`（GT 的学生与家长全部页面，含周报）、`report-on`（R1 首份报告，R2 的页面上没有 R1 的判断）、`starmap-on`（V1、V2 星图 v1）、
  `family-on`（M1 月度计划家长页与学生“本月计划”、M2）。
- 种子 `db/seeds/dev/weekly-synthetic.sql`：GT 另加两条判断 f3、f4，周报生成时成立，之后 f3 经待验证被推翻、f4 事实更正后撤回；周报快照里仍留着这两句。
  另加 f5：生成时成立、之后到期转为待验证。BFF 读周报时逐句在库里再核判断（`services/bff/src/http/trust-routes.ts` 的 `weeklyClaims`，
  只收成立、假设），这三句都不出现在任何页面——爬取通过，现有实现是对的。
  **两道防线**：推翻、撤回的判断首先被库的 RLS 挡住（0010 的读取策略只放行成立、假设、待验证），BFF 的状态过滤是第二道；
  只去掉 BFF 过滤时 f3、f4 仍被 RLS 挡住（第一版变异因此漏检），所以 f5（RLS 放行的待验证）专门用来证明 BFF 这一道在起作用。
- 变异 `apps/web/scripts/claims-mutation.sh`（`make web-claims-mutation`，进 bff-web 变异组，CI 手动触发时跑）：每类断言至少一条——
  BFF 周报不再核判断状态（推翻、撤回的句子露出）、周报句子不带 `data-claim-id`、周报句子抄进说明属性、星图把待验证的判断画成实心星。
  每条变异都要重新构建端到端前端，单条几分钟。

### B. T46：安全加固
1. CSP 去掉 `img-src data:`，改为 `img-src 'self'`（`apps/web/src/lib/csp.ts`）。单测、逐页安全头核对（`e2e/headers-check.ts`）同时断言；
   `make web-test` 在构建后扫描产物，有 `data:` 图片或字体即失败。源码与构建产物里本来就没有 `data:` 图片。
   连带：`e2e/glyphs.spec.ts` 原来在应用页面里把截图当 `data:` 图片解码，CSP 收紧后被挡；改为在同一上下文另开的空白页里解码（测试本身的做法，应用不变）。
2. 前端转交客户端地址（`apps/web/src/lib/bff.ts` 的 `clientIpFor`）：只在 `WEB_TRUST_CLIENT_IP=1` 时转交，且只取网关覆盖写入的 `X-Real-IP`，
   不再取 `X-Forwarded-For` 的第一段；缺省不转交。staging 的 k8s 与 compose 编排显式设 `0`，部署测试核对；env-inventory 与 `deploy/config/README.md` 登记（发布检查项 S5）。
3. BFF 地址白名单只认 RFC 1918 IPv4 字面量：保持不变。理由：staging 的前端与审核台按固定内网地址连 BFF（k8s Service clusterIP、compose 固定地址），
   放开主机名会引入 DNS 解析这一层（解析结果可能被改写成公网地址），收益只是配置方便。

## 环境与经验
- 本机（云端会话）预装的 Chromium 与仓库锁定的 `@playwright/test` 1.56.1 不配套（锁定版要 1194，本机是 1243）：`playwright.config.ts` 加了可选的
  `PW_CHROMIUM_EXECUTABLE`（不设时照旧，CI 不受影响；Firefox / WebKit 几套不用它）。本机跑：`PW_CHROMIUM_EXECUTABLE=/opt/pw-browsers/chromium make web-test`。
- 本机这版 Chromium 上 `e2e/glyphs.spec.ts` 的 DPR 2 两例（16px 未亮星填充比例 0.146 / 0.104，上限 0.10）失败；把 CSP 与测试换回改动前的版本同样失败、数值相同，
  是浏览器版本的渲染差异，不是本批改动引起的。CI 用锁定版本的浏览器，不受影响；见待人确认第 1 条。
- 爬取最初把 `<script>` 里 Next.js 服务端组件载荷当成“没挂判断的句子”：文字只看渲染出来的节点（跳过 script、style、template、noscript），
  ID 是否出现仍查整页 HTML（含 script）。
- 星图虚线星旁“下一次验证”的日期行也带 `data-claim-id`，但没有结论句、没标画法：断言按“标了画法必须是 dashed、有结论句必须在 dashed 里”写，不误报它。

## 测试结果（本机）
- `make web-test` 的构建、产物扫描、单测通过；端到端在本机除 glyphs 的 DPR 2 两例（见上）外全部通过；T21 四个用例通过。
- `make deploy-check` 通过。
- `apps/web/scripts/claims-mutation.sh`：4 条全部被发现（先跑未变异的对照，trust-on、starmap-on 两套都通过）。

## 待人确认
1. **glyphs 的 DPR 2 两例**：只在与锁定版本不配套的浏览器上失败。建议不改阈值；若以后升级 `@playwright/test`（浏览器随之升级）时同样失败，
   由视觉负责人重定 16px 未亮星的填充比例上限。
2. **T21 没有覆盖的**：“为什么”页（端到端环境没有洞察服务的 explain，只显示“暂时无法获取”）、考试与对账卡（同上，503）。
   等端到端接上假洞察服务的 explain 与预测后补进爬取。
3. **跨学生**这一类断言（别的学生的判断 ID 不出现）现在只有阳性数据（兄妹、另一户的页面），没有专门的变异；需要的话另加一条
   （例如周报查询不按学生过滤）。
