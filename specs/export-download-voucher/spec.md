# 导出下载凭据：响应写完才作废（PR 28 合并前增量审查 N8）

状态：已确认（用户 2026-11-22：“按默认建议全部确认”，待人确认 1–6 均按默认）
关联：SIE-03 PR③ 数据权利（`specs/sie-03-dispute-reconcile/`）· 冲刺 5-H 信任界面 · `specs/launch-test-2026/pr28-review-notes.md` 增量审查 N4、N8
基线：PR zeemen/starbuddy#28 的头分支 `claude/busy-edison-5365e8`（`a42bed7`）。本任务在其上开分支，见待人确认 5。

## 目标
家长下载导出包时，如果 BFF 还没把包完整写出连接就断了（包大、网络慢、前端 60 秒超时），凭据不作废，
家长在原有效期内能用同一张凭据再下载，不必重新发起导出（重新导出会占用库里“24 小时 10 次”的名额并多留一条导出任务）。

## 现状
- `services/bff/src/trust/exports.ts` `MemoryExportStore.take()`：凭据、任务、学生、账号对上就**立即删除**并返回包；
- `services/bff/src/http/trust-routes.ts` `exportDownload`（约 598 行）调用 `take()` 后才返回 `Reply`，由 `server.ts` 的 `send()` 在会话事务提交之后写出；
- 写出失败、写到一半连接断开、事务提交失败（回 500）时，凭据都已经没了。
- 下载是两跳：浏览器 → 前端路由 `apps/web/src/app/family/[studentId]/data/download/route.ts` → `downloadExport()`（`bffFetch`，60 秒）→ BFF。
  前端读 BFF 响应体超时或中断时，`bffFetch` 中止请求，BFF 一侧看到的是“`close` 早于 `finish`”。

## 范围
### 做
1. **存储改为“占用—确认 / 释放”**（`ExportStore` 接口与 `MemoryExportStore`）：
   - `claim(ref, match, now)`：凭据、任务、学生、账号对上、未过期、**当前没有有效占用**、占用次数未到上限 → 返回 `{ body, commit, release }`，
     记占用（占用时刻 + 占用超时、占用序号、累计占用次数 +1）；否则返回 `null`（路由回同一个 404）。
   - `commit()`：作废凭据（删暂存）。只要这次响应写完就作废，即使它的占用已经超时、被后来的兑换接手（包已经送达过一次）。
   - `release()`：解除占用，凭据在原有效期内可再用；只对“当前占用”生效（占用已超时并被接手的旧兑换再释放，不影响新的占用）。
     累计占用次数已到上限时，释放等于作废。
   - 占用超时：到点没有 `commit` / `release` 的占用，下一次兑换时视为已释放（惰性判断，不起定时器）。
   - `commit` / `release` 幂等、只生效一次；条目已被过期清扫、`purgeStudent` 清掉时两者都是空操作。
   - 有效期（`min(1 小时, 库里 expires_at)`）、绑定（任务、学生、账号）、条数 / 单包 / 总量上限、`purgeStudent` 语义**都不变**；
     占用中的条目照常按有效期清扫、照常被 `purgeStudent` 清掉（删除确认、解除绑定优先于正在进行的下载能否重试）。
   - 删掉 `take()`，不留两套兑换入口（`purgeStudent`、`put` 不动）。
2. **HTTP 层给 handler 一个“响应结果”回调**（`routes.ts` 的 `Ctx`、`server.ts`）：
   - `Ctx.onDelivered?(cb: (delivered: boolean) => void)`：handler 登记，服务器在本次请求结束时恰好调用一次：
     - handler 的回复由 `send()` 写出且 `res` 触发 `finish`，并且那一刻 socket 没有销毁、没有出错 → `true`
       （实现中发现：对端重置连接时 Node 仍会发 `finish`，见 lessons）；
     - `close` 早于 `finish`、`send()` 因连接已断或响应头已发而没写出、handler 之后的事务提交失败或任何异常转成 500 / 其他错误回复 → `false`。
   - 三种上下文（自管事务、会话事务、公开）都提供；回调抛错不影响请求（吞掉并记 `error_class`）。
   - 只是进程内回调，不改任何响应头、状态码、响应体。
3. **下载路由**：鉴权与再核（有效绑定、删除状态，不满足时清暂存回 404）顺序不变；之后 `claim`，拿不到回原来同一个 404；
   拿到则 `ctx.onDelivered(ok => ok ? commit() : release())`。拿不到 `onDelivered` 的上下文（单测里直接调 handler）退回“立即作废”，失败即关闭。
4. **日志只记元数据**（规则 13，只用白名单里已有的键，不改 `LOG_FIELDS`）：
   - 访问日志行加 `export_store`：`claimed` / `miss`（凭据不对、已用、过期、不是本人、正被占用、次数用完，不细分，避免日志成为凭据状态的旁路）；
   - 写出结束另起一行：`event: export_download_settled`、`export_job`、`account`（`logHash`）、`outcome`（`delivered` / `released` / `released_final` / `stale`：这次占用已不是当前占用，例如被清掉或超时被接手）、
     `attempts`（第几次占用）。不记凭据、凭据摘要、包大小以外的任何内容；包大小也不记。
5. **测试与变异**（见验收标准）、`lessons.md`。

### 不做
- 不改迁移与库：`platform.export_job` 对 BFF 仍不可读，兑换留痕仍只在访问日志（库内留痕函数另见 `specs/sie-03-dispute-reconcile/lessons.md` M2，必停项）。
- 不改凭据有效期、绑定、404 口径、限流桶（`export_download.account.hour` 照旧对每次兑换计数，重试也计数）、响应头与响应体。
- 不改 `packages/contracts`（洞察服务契约里“一次性下载凭据”的语义不变：成功送达至多一次）。
- 不改前端：`EXPORT_DOWNLOAD_TIMEOUT_MS` 维持 60 秒；页面上“凭据失效请重新生成”的文案维持（重试是家长重新点下载，不做自动重试）。
- **不覆盖浏览器那一跳**：BFF 写完给前端路由之后，浏览器 ↔ 前端之间再断，凭据已作废（见待人确认 6）。
- 不处理多实例：暂存仍在进程内，pilot 前换对象存储的事项不变。

## 输入契约
- 迁移 0035（`platform.export_student_data`、`platform.is_linked_guardian`）及之后的删除请求表，不改；
- BFF 壳路由表（`trust-routes.ts` `data_rights` 开关）、`services/bff/src/observability/log.ts` 的 `LOG_FIELDS`（只用不改）；
- 前端 `apps/web/src/lib/trust-api.ts` `downloadExport`（只读，不改）。

## 禁止修改的范围
`db/migrations/`、`packages/contracts/`、`evals/` 金标、人设、计量与权益逻辑、RLS 与权限策略、`LOG_FIELDS` 白名单、前端。

## 验收标准（可测试）
存储单测（`tests/trust-units.test.ts`，假时钟）：
- [ ] 占用后 `commit` → 再兑换 404；占用后 `release` → 同一凭据可再占用并拿到同一份包；
- [ ] 占用期间第二次兑换返回 `null`；占用超时后可再占用；超时被接手后，旧占用的 `release` 不解除新占用、旧占用的 `commit` 作废凭据；
- [ ] 累计占用到上限后 `release` 等于作废；`commit` / `release` 重复调用只生效一次；
- [ ] 占用中被过期清扫或 `purgeStudent` 清掉后，`commit` / `release` 不报错、不复活条目，总字节计数正确；
- [ ] 原有用例（绑定三项、有效期 1 小时上限、条数 / 单包 / 总量上限）照旧通过。

HTTP 层单测（`onDelivered` 的语义，起真实 `http.Server`）：
- [ ] 写完触发 `true`；客户端在 `finish` 之前断开触发 `false`；handler 之后抛错转 500 触发 `false`；每个请求恰好一次。

集成测试（`tests/trust.integration.test.ts`，真 Postgres 与 BFF 进程内服务器；大包用测试直接 `put` 进注入的 `MemoryExportStore` 的合成内容）：
- [ ] **写出成功才作废**：正常下载 200，再兑换 404（原有“只能兑换一次”用例保留）；
- [ ] **中途断开可重试**：客户端收到响应头后不读、断开连接（包足够大，`finish` 不会先到）→ 同一凭据再下载 200，内容完整一致，之后 404；
- [ ] **重试有上限**：连续断开到上限后，再兑换 404；
- [ ] **并发只放行一个**：第一个请求占用中（不读响应体），第二个同凭据请求 404，且与“凭据不对”的 404 逐字节相同；第一个读完后，第三个 404；
- [ ] **授权与删除口径不变**：占用中家长解除绑定或删除请求被确认 → 断开后再兑换 404（暂存已清）；别的家长、别的孩子路径带这张凭据仍 404；
- [ ] **日志**：访问日志与 `export_download_settled` 行只含白名单键；不含凭据原文、凭据摘要与包内容；`account` 是 16 位摘要。

变异（`tests/mutations.sh`“信任：”一组，跑 trust-units 与 trust.integration）至少：
- [ ] 占用不排他（并发两个都放行）；释放即作废（断开后不能重试）；写完不作废（`commit` 变空操作）；
- [ ] `close` 早于 `finish` 当成送达；不设重试上限；占用超时被接手后旧释放解除了新占用；
- [ ] 现有“信任：导出凭据可重复兑换”改写到新的 `commit` 位置，仍被发现。

完成定义：`make bff-test`、`make check-fast` 通过；`BFFMUT_FILTER=信任 make bff-mutation` 全部被发现；写 `lessons.md`；PR 描述逐条写验收如何满足，不合并。

## 涉及的必停项（见 CLAUDE.md）
- 迁移 / RLS / 计量 / contracts / 人设 / 第三方 / 判分与掌握：**否**。
- 涉及家长数据权利与一次性凭据的安全语义：不在必停清单，但属于安全审查范围，是否请安全负责人过目见待人确认 3。

## 依赖与输入
- PR zeemen/starbuddy#28 未合并；`pr28-review-notes.md`、前端 60 秒时限、`mutations.sh` 现行版本都只在它的头分支上。

## 待人确认
1. **重试次数上限**：默认**每张凭据最多占用 3 次**（首次 + 2 次重试），第 3 次也没写完就作废、家长重新导出。可改。
2. **占用超时**：默认 **2 分钟**（前端时限 60 秒，正常情况下前端中止后 BFF 立刻看到 `close` 并释放；2 分钟只兜底卡死的连接）。
   超时只做惰性判断：到点后**正在写的那次响应不会被 BFF 主动断开**，所以极端情况下（一次写出卡住超过 2 分钟、同时家长又点了下载）
   同一位家长可能先后收到两份同样的包。若要严格“同时只一份在途”，需要在占用超时时主动断开那次响应（多一个 `ctx` 钩子），请定。
3. **是否需要安全负责人过目**：默认**需要**——改的是数据权利的一次性凭据语义（从“兑换即作废”变成“送达即作废”），PR 描述首行写 `[NEEDS-HUMAN]` 并点名安全负责人。
4. **“送达”的口径**：以 BFF 的 `res` `finish`（数据交给操作系统）为准，不等于对端确实收到；`finish` 之后才断的极少数情况凭据照样作废。默认接受。
5. **分支与 PR 基线**：默认本分支从 PR 28 头分支开出，PR 的 base 设为 `claude/busy-edison-5365e8`（diff 只含本任务；PR 28 合并并删分支后 GitHub 会把 base 改成 main）。
   若希望等 PR 28 合并后再基于 main 提，请说明。
6. **浏览器那一跳**不在本任务：前端路由已拿到完整包后浏览器断开，凭据已作废。要覆盖需要前端流式转发并把“送达”回传给 BFF，改动跨两端，建议另起。
