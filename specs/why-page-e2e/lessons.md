# “为什么”页与异议的端到端覆盖 · 实现记录

2026-11-11 用户确认（“按建议，默认值都同意，开始实现”）：spec 第五节待人确认 1–3 全部按默认——只补“为什么”页与异议提交；
桩的异议受理按契约形状最小实现、不模拟二次授权；保留“读取不可用”用例（桩对指定 ID 回 503）。不涉及必停项。

## 交付
- **假洞察服务**（`apps/web/scripts/fake-insight.ts`）：另认周报合成学生 c1 的六条判断 f1–f6（f1 成立、f2 假设、f3 已推翻、f4 已撤回、
  f5 待验证、f6 成立但读取不可用）。`insight.explain` 按契约信封返回；f3、f4 回与“不存在”相同的 404；f6 回 503 `UPSTREAM_UNAVAILABLE`。
  新增最小的 `insight.dispute`：异议类型不合法或缺幂等键 400，判断不在成立 / 假设状态 409，否则返回合成的 `dispute_id` 与 `next_step`
  （i_know → verification_scheduled 带 `verify_by`，one_off_slip → observe_followups，data_error → open_fact_for_correction 带合成的
  `fact_ref`）。c1 的 `insight.report` 回 404（周报不走它）；其余工具仍 501。只听 127.0.0.1、令牌每次随机。
- **接线**（`apps/web/scripts/e2e.sh`）：信任界面全开那一套 BFF 另起一个桩实例（与首份报告那一个分开），`BFF_INSIGHT_URL` 指过去，
  信任开关加 `why`；退出清理与端口等待一并登记。
- **种子**（`db/seeds/dev/weekly-synthetic.sql`）：新增 f6（不进周报，只给“读取不可用”用例）。
- **端到端**（`apps/web/e2e/trust-on.spec.ts`）：新增学生、家长两条“为什么”页用例——
  f1、f2 结论带 `data-claim-id`（实心），f5 画虚线且不出现结论文字；f3、f4、f6 一律“暂时无法获取”、没有 `data-claim-id`、没有异议面板、
  文字不在 HTML 里；学生端不出现置信度分档、有三条异议路，家长端带置信度分档、只有两条（没有“一时失手”）；提交异议后结果区显示下一步。
  每页都过 DOM 编码核对与无障碍扫描。原第一条用例（不存在的判断：暂时无法获取、不显示异议按钮）保留。
  T21 全站爬取另走 f1、f2、f5 的“为什么”页（学生与家长各一遍），进 `claim-binding.ts` 的三条规则。
- **变异**（`apps/web/scripts/claims-mutation.sh`）：新增 4 条——结论不带 `data-claim-id`、读取不可用时仍显示异议入口、待验证画成实心、
  家长多出只给学生的异议路；另 2 条守住下面两处修复——表单 `data-testid` 带异议类型编码、结果区 `data-code` 不经 `domCodes` 改写。

## 新覆盖发现的产品缺陷（已修）
异议面板以前在端到端里从没真正渲染过（没接洞察服务，读取一律不可用），接上之后 DOM 编码核对（`e2e/dom-leak.ts`）立刻抓到两处内部编码进了页面：
1. 三张异议表单的 `data-testid="dispute-i_know"` 等带着异议类型编码。改为短横线写法（`dispute-i-know`、`dispute-one-off-slip`、
   `dispute-data-error`），编码原样不再进 DOM（`apps/web/src/trust/WhyPage.tsx`）。
2. 提交后结果区的 `data-code="verification_scheduled"` 等带着下一步编码（`dom-leak.ts` 本来就写明“异议的下一步编码不放行”）。
   `ResultForm` 新增可选的 `domCodes`（结果码写进 `data-code` 前的改写），异议用 `DISPUTE_NEXT_DOM` 改成序号 `next-1`…`next-3`
   （与占位键 `dispute.next.N` 对应，与偏差方向“只给序号”同一做法）；显示的文案不变，仍按原结果码取。`dom-leak.ts` 的 `code` 白名单登记这三个序号。

## 与 spec 的偏差
1. **多一条种子判断 f6**：spec 写“桩对指定的合成 ID 回 503”。不存在的 ID 在 BFF 先查库就 404，到不了桩，所以“读取不可用”要一条库里存在、
   状态可展示的判断——新增 f6（只在开发种子里，不进周报）。
2. **“渲染已推翻的判断”变异放不到前端或 BFF**：试过让 BFF 的解释放行已推翻、已撤回的判断，端到端发现不了——库的 RLS 本来就不把这两种状态
   给 BFF 看（`EXPLAINABLE` 之外的状态读不到），变异与原代码等价，已删掉。推翻、撤回不出现由 RLS 与 BFF 状态核对双重保证，库上测试另有覆盖。
3. **变异条数**：spec 写“至少三条”，实际 6 条（另两条守住上面修掉的两处泄露）。

## 测试结果（本机，`PW_CHROMIUM_EXECUTABLE` 指本机 Chromium 1243）
- 前端单测 236 项、tsc 通过。
- `trust-on` 端到端 11 项全部通过（含新增 2 条与 T21 爬取）。
- `make web-claims-mutation`：先跑原有 4 条 + 新增 4 条，8 条全部被发现；后补的 2 条用只含这两条的临时副本另跑（含对照），2 条全部被发现。
  合计 10 条。本机没有重跑全部 10 条，CI 手动触发变异组时会一起跑。

## 经验
- 新开一段端到端覆盖时，先让被覆盖的组件真正渲染一次再写断言：这次两处泄露都是“组件从没在端到端里出现过”留下的。
- 想断言“某状态不出现”时先看 RLS 是否已经挡住：挡住了，上层的变异就是等价变异。

## 待人确认
- 无新增。`data-code` 里的序号、`data-testid` 的短横线写法沿用 `dom-leak.ts` 现有规则；判断状态、置信度分档等编码留在 DOM 里与否，
  仍是 `specs/sie-03-dispute-reconcile/lessons.md` 第 10 条的待人确认项，本任务不动。
- 考试预测与对账卡的端到端仍要先补契约（必停），见 spec 第三节。
