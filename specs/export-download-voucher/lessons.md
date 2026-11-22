# 导出下载凭据响应写完才作废：经验与待人确认

## 做了什么
- `src/trust/exports.ts`：`take()` 换成 `claim()`，返回 `{ body, attempt, commit, release }`。
  - 占用排他（`claimUntil`）；`commit` 作废；`release` 解除占用，次数用完（`MAX_CLAIMS = 3`）时等于作废；
  - 占用超时 `CLAIM_HOLD_MS = 2 分钟`，在下一次兑换时惰性判断；被接手后旧占用的 `release` 返回 `stale`、不解除新占用；旧占用晚到的 `commit` 照样作废；
  - 有效期、绑定、上限、`purgeStudent` 都没变，占用中的条目照常被清扫、清掉。
- `src/http/routes.ts` / `server.ts`：`Ctx.onDelivered(cb)`，每个请求恰好结算一次；回调抛错只另记一行（`on_delivered_error`，异常类名）。
- `src/http/trust-routes.ts` 下载路由：鉴权与再核顺序不变，最后一步占用；访问日志加 `export_store: claimed | miss` 与 `attempts`；
  写出结束另记 `export_download_settled` 一行（`export_job`、账号摘要、`outcome`、`attempts`），都是 `LOG_FIELDS` 里已有的键，白名单没改。

## 经验
1. **只看 `finish` 判断不了“写完”**。本机（Node 22.23）复现：服务器 `res.end(32 MB)`，客户端收到响应头就断开，
   socket 先报 `ECONNRESET`，随后 `ServerResponse` 仍然发 `finish`（`writableFinished === true`，`bytesWritten` 是尝试写的字节数），
   之后才发 `close`。所以按 spec 字面“`finish` 才作废”实现，断开的那次照样作废，第一版集成测试就是这样失败的。
   现在的判据是：`finish` 触发时 socket 还在、没有销毁、`errored` 为空才算送达；`close` 先到算没送达。
   变异“finish 即送达（不看连接是否已断）”能被测试发现。
2. **小包断开仍算送达**。包能随响应头一起进内核缓冲时（例如 1 KB），客户端读完响应头再断，服务器一侧看到的是正常的 `finish`，
   凭据作废。这就是 spec 待人确认 4 接受的口径（“交给操作系统”不等于对端收到）。只有包大到写不完，断开才看得见；
   测试因此用 32 MB 合成包（集成测试里暂存的单包上限相应放大，只影响测试）。
3. **`close` 早于 `finish` 在 Node 22 里走不到**。调用过 `res.end()` 之后，对端重置（ECONNRESET）、服务器自己
   `socket.destroy()` 或 `res.destroy()`（ECANCELED）都是先发 `finish`（socket 已销毁、带错误）再发 `close`（本机逐个复现）。
   变异“close 早于 finish 当成送达”第一轮漏检，原因就在这里：它是等价变异，已从 `mutations.sh` 去掉并注明。
   分支留作防御（以后的 Node 版本若不再补发 `finish`，没有它占用会一直挂到 2 分钟超时）。
5. **“作废”和“一直占着”在 2 分钟内看起来一样**。变异“写完不结算”（`commit` 变空操作）第一轮漏检：占用没解除，
   2 分钟内再兑换同样是 404。两处测试补了“拨过占用超时（注入时钟）仍 404”的断言，现在能发现。
4. 第一次用 Python 按切片替换源码时，切片的终点锚点在接口声明里先出现，切成了空串、把文件弄坏了；已从 git 恢复后重做。
   以后按锚点切片前先断言锚点只出现一次、起点在终点之前。

## 测试
- `tests/trust-units.test.ts`：旧用例改为 `claim` + `commit`；新增占用排他、释放后再兑换、上限、超时接手、占用中被清扫或清掉。
- `tests/export-delivery.test.ts`（新，不连库）：桩连接池 + 真实 `createShellServer` 与下载路由；写完作废、断开释放后重试拿到完整包、
  事务提交失败（500）释放、结算行只有元数据。
- `tests/trust.integration.test.ts`：真 Postgres，真实会话与授权再核；断开可重试、占用中第二个请求与“凭据不对”逐字节相同的 404、
  3 次上限、占用中解除绑定后再兑换 404 且暂存已清、日志键都在白名单里。

## 变异（`tests/mutations.sh`“信任：”一组，跑 trust-units、export-delivery、trust.integration）
新增 7 条：占用不排他、释放即作废、不设重试上限、超时接手后旧释放解除新占用、finish 即送达（不看连接是否已断）、
事务提交失败不释放、写完不结算；原有“导出凭据可重复兑换”改写到新的 `commit` 位置。
第一轮（名字含“导出”的 17 条）漏检 2 条，处理见经验 3、5；补测试后“写完不结算”单独重跑能被发现。整组结果见 PR 描述。

## 待人确认
1. **安全负责人过目**（spec 待人确认 3，按默认需要）：一次性凭据的语义从“兑换即作废”变成“送达即作废”，且同一凭据最多可被占用 3 次。
   请重点看：并发的第二个请求是否确实与“凭据不对”同一个 404；超时接手的残余风险（下一条）；断开判据依赖 Node 的 socket 状态（经验 1）。
2. **残余风险（按默认接受）**：一次写出卡住超过 2 分钟、同时同一位家长再点下载，可能先后收到两份同样的包（对象仍是这位家长本人）。
3. **前端注释**：`apps/web/src/lib/trust-api.ts` 里 `EXPORT_DOWNLOAD_TIMEOUT_MS` 的注释和下载路由的注释仍写“兑换一次即作废”。
   按 spec 本任务不改前端，行为上也没矛盾（前端超时中止后 BFF 会释放）；文字是否跟着改，请定。
4. **浏览器那一跳**仍不覆盖（spec 待人确认 6）：前端路由拿到整包之后浏览器再断，凭据已作废，建议另起。
