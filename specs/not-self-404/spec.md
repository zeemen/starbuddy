# BFF 对“不是本人 / 不存在”统一回 404（诊断、行为事件与探针从 403 改过来）· 规格草稿

- 状态：**已确认（2026-11-22，用户“按建议，默认值都同意，开始实现”）**：第五节三条都按默认。实现记录见 lessons.md。
- 来源：W13 技术债 T26“BFF 接口口径：无权一律 403 与建档‘一律 404’不一致”（`docs/gates/w13-debt-and-decisions.md`；
  `specs/behavior-events-sprint2/lessons.md` 待人确认第 3 条）。用户 2026-11-22 定口径：**统一回 404**。
- **涉及必停项**：改的是授权失败时的响应码（CLAUDE.md“RLS 与权限策略”一类）。不改 RLS 策略、库函数与授权判断本身，只改“判断为不放行之后回什么”。
  提交首行标 `[NEEDS-HUMAN]`，请安全负责人过目。不改 `packages/contracts`（这些是 BFF 自己的接口定义 `services/bff/openapi/*.v0.yaml`）。

## 一、现状（2026-11-22 核对）
- BFF 大部分按学生取数的路由，对“兄妹、另一户学生、不存在的学生、路径参数不合法”**一律同一个 404**（`src/http/problems.ts` 头注释：
  “未授权与不存在同一个 404”；接口定义里写作 `x-sb-not-self: 404`，例如再练、复测、月度计划、建档）。
- 例外是两组较早的接口，仍是**一律同一个 403 `FORBIDDEN`**（接口定义写作 `x-sb-not-self: 403`）：
  - 诊断 6 条（`openapi/diagnosis.v0.yaml`，实现 `src/diagnosis/service.ts`：`isSelf`、`ownsSession` 不通过、库端 42501、学习服务 404 都回 `FORBIDDEN`）；
  - 行为事件与探针 3 条（`openapi/events.v0.yaml`，实现 `src/events/query.ts` 的 `forbidden` 与 `src/http/feature-routes.ts` 的 `writeReply`）。
- H6 授权矩阵（`tests/route-matrix.integration.test.ts`）按 `x-sb-not-self` 核对每条路由的拒绝码；BFF 集成测试 `diagnosis.integration.test.ts`（11 处）、
  `events.integration.test.ts`（10 处），前端端到端 `e2e/behavior-events.spec.ts`（3 处）断言 403。
- 另有几类 403 **不是“看不看得到这个学生”**，与存在性无关：
  - 账号类型不对（学生会话调家长路由、家长会话调学生路由；在任何查询之前判断，`src/http/server.ts`）；
  - CSRF（403 `CSRF`）、二次确认（403 `CONFIRMATION_REQUIRED`）、重新登录（403 `REAUTH_REQUIRED`）；
  - 信任界面“那次只是失误”只给学生本人（`src/http/trust-routes.ts:82`，按账号类型）。

## 二、默认做法
1. **把“不是本人 / 不存在 / 参数不合法”统一成 404 `NOT_FOUND`**，与其他路由逐字节相同（复用 `P.NOT_FOUND`）：
   - `src/diagnosis/service.ts`：`FORBIDDEN` 的所有用处改为 `NOT_FOUND`（包括学习服务 404 映射、库端 42501）；
     宵禁检查里的 `'forbidden'`（第 168 行一带）同样按“看不到这个学生”处理；
   - `src/events/query.ts` 的 `forbidden` 改名 `not_found`，`writeReply` 回 `P.NOT_FOUND`；
   - 两份接口定义的 `x-sb-not-self: 403` 改为 `404`，响应表里的 `'403'` 换成 `'404'`，头部说明同步。
2. **前端**：`src/lib/diagnosis.ts`、`src/lib/events.ts` 本地拦下的非法 ID 也改回 404（与 BFF 一致）；`events.ts` 的计数分档里 `403` 换成 `404`。
   页面行为不变（诊断失败一律显示“暂时无法开始”，行为事件写入失败不打扰学生）。
3. **其余 403 不动**：账号类型不对、CSRF、二次确认、重新登录、“那次只是失误”的账号类型限制（见待人确认第 2 条）。
4. **测试**：
   - H6 授权矩阵按新的 `x-sb-not-self: 404` 自动覆盖这 9 条路由；另加一条：**所有**按学生取数的路由，`x-sb-not-self` 只能是 404（以后不会再出现 403 的例外）；
   - 诊断、事件集成测试：403 断言改为 404，并加“兄妹 / 另一户 / 不存在 / 非法 ID 四种的响应体逐字节相同、且与其他路由的 404 相同”；
   - 端到端 `behavior-events.spec.ts` 的 3 处 403 改为 404；
   - BFF 变异：诊断某一处退回 `FORBIDDEN`、事件 `writeReply` 退回 403，各一条。
5. `docs/gates/w13-debt-and-decisions.md` 的 T26 标为已做（403 / 404 口径部分）。

## 三、不做
- 账号类型不对等与存在性无关的 403（第一节最后一条）。
- 探针关联窗口（0025，必停）、学习服务内部接口的错误码（BFF 已把它们映射掉）。

## 四、验收
- 9 条路由对“不是本人 / 不存在 / 参数不合法”都回与其他路由逐字节相同的 404；H6 矩阵、新加的“只能是 404”检查、集成测试、端到端通过；
  `make bff-test`、`make web-test`、`make check-fast` 通过。

## 五、待人确认（括号里是默认）
1. **范围**：只改“不是本人 / 不存在 / 参数不合法”的 9 条路由（默认）；还是连同下一条一起改？
2. **账号类型不对**：维持 403（默认：在任何查询之前判断，只说明“这个账号类型不能用这个接口”，不泄露学生是否存在）；还是也改 404
   （家长诉求、月度计划已经对学生会话回 404，`guardianOnly404`）？
3. **前端本地拦截**：`lib/diagnosis.ts`、`lib/events.ts` 本地拦下非法 ID 时也改回 404（默认，与 BFF 一致）；还是保持 403（只在前端内部，不出页面）？
