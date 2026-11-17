# 信任界面表格在手机上改卡片排列 · 实现记录

- 2026-11-17 用户“按建议，默认值都同意，开始实现”：只改“为什么”的证据表与“我提出的异议”表；前后对比写进本文件，随 PR 过目。

## 做了什么
- `src/trust/WhyPage.tsx`（`why-evidence`）、`src/trust/DisputesPage.tsx`（`disputes`）：加 `card-table`，每格加 `data-label`（取表头同一条文案），
  表格各层显式 `role`。样式复用 `globals.css` 的 `.card-table`（`specs/practice-retest-list-polish`），只在 767px 以下生效，桌面不变。
- **格子里的内容一个字没改**：每格仍是改前同一个表达式（`tests/trust.test.ts` 逐格钉住）。
- `e2e/dom-leak.ts`：`data-label` 的取值清单加上两张表的表头文案。

## 规则 11 的前后对比
- 截图：`compare-390.png`（390 宽，家长端；左两张“为什么”页改前 / 改后，右两张“我提出的异议”改前 / 改后）。合成数据：GT 的判断 f1 的证据行，
  以及直接插库的两条家长异议（一条进行中、一条已处理；端到端的洞察服务是桩，提交异议不写库）。
- 逐格核对：改前、改后在 390 宽下按行取每格 `innerText`（列名由 `::before` 显示，不在 innerText 里），四页（学生 / 家长的证据表、家长异议、
  学生异议）**逐格完全相同**。学生看不到家长提出的异议（学生异议页为空），改前改后一致。
- 关于学生的句子仍只在 `data-claim-id` 里（`onlyCopyOrClaims` 照常通过）；表里没有关于学生的句子，只有时间、证据种类与等级、知识点编号、
  异议类型与状态、日期和“查看”链接。

## 测试
- 前端静态检查（`tests/trust.test.ts`）：两张表每格带 `data-label`、与表头文案一致、显式 role；格子表达式与改前相同。
- 端到端（`e2e/trust-on.spec.ts` 末尾一条）：家长看证据表与异议表、学生看证据表；1280 宽仍是表格行；360 / 390 / 430 宽下无横向滚动、按卡片排列、
  列名与表头一致，**每格文字与桌面宽度下逐格相同**；`onlyCopyOrClaims`、`expectNoDomLeak`、axe 通过。插入的两条异议用固定 ID、
  放在文件最后一条（前面的用例断言过异议列表为空；playwright 配置 `workers: 1`、按文件顺序执行）。
- trust-on 一套 14 项通过；前端单测 268 项通过。

## 与 spec 的差异
- 无。

## 补记：漏跑了跨模块测试（2026-11-17）
- 洞察服务的禁用推断测试（`services/insight/tests/test_forbidden_inference_sie03.py` 的 `test_render_scan_counterexample`）
  往 `DisputesPage.tsx` 里按 `<td>{DISPUTE_KIND[d.kind]}</td>` 精确匹配注入反例；格子加了 role 与 data-label 后注入点不存在，CI 的 insight 组 13 条失败。
  本机只跑了前端与 trust-on 端到端，没跑 `make insight-test`。已把注入点改成新的格子写法，13 个反例仍全部被扫描报出。
- 教训：改 `apps/web/src` 下的页面前，先 `grep -rn 文件名 services packages tools` 查其他模块的测试有没有读这个文件的源码。

## 待人确认
- 无新增。数据导出回执、周报学科栏仍是表格（列少）；证据表里的知识点仍显示编号（换名称要动洞察服务契约，见
  `specs/practice-retest-list-polish/lessons.md` 待人确认第 3 条）。
