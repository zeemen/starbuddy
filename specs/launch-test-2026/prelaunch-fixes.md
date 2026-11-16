# 内测前整改三项（2026-11-16）

来源：用户 2026-11-16 提出的 3 项整改（1 项 P1 建议内测前优先修复，2 项 P2）。三项都不在 CLAUDE.md 必停范围内（不改迁移、契约、权限、计量、人设）。

## P1 畸形请求让 BFF 进程退出（已修，提交 89339a8）
- **问题**：`services/bff/src/http/server.ts` 路由匹配里 `new URL(req.url)` 在 try 之外；外层 Promise 链也没有 catch。`GET //[/` 会被当成主机名解析而抛错，
  未处理的拒绝让进程以状态 1 退出。
- **修复**：解析不了的请求目标按表外处理（与“不存在”同一个 404）；外层处理链加兜底：未预期的异常回统一的 500（`application/problem+json`，`code: INTERNAL`），
  日志只记 `event=unhandled_request_error` 与错误类别（`exc_type`），不记路径、请求头、请求体。响应头已发出时直接断开连接。
- **验证**：`tests/malformed-request.test.ts` 在子进程里起服务、用原始 TCP 发 `//[/`、`//[`、`//a:b@[/`、`/%`、`//[::1/`，修复前复现“进程以状态 1 退出”，
  修复后都回 404、进程存活、`/healthz` 照常 200；另一条让访问日志抛错，验证外层兜底。BFF 变异组加两条“畸形请求：”（去掉解析兜底、去掉处理链兜底），均被发现。
  `make bff-test` 全量通过。

## P2-1 中文目录下测试 ENOENT（已修）
- **问题**：测试与工具把 `new URL(..., import.meta.url).pathname` 直接交给文件系统。pathname 是百分号编码的，目录名含中文时变成 `%E5%8D%87…`，找不到文件。
- **范围**（不止报告里的 `apps/web/tests/auth.test.ts`）：全仓 51 处 `.pathname`（前端 30 个测试文件、BFF 7 个测试文件、阿鸦适配器的测试与脚本、KGE 盘点工具、内容登记工具），
  3 处目录遍历里的 `[u.pathname]`（BFF 的 `route-matrix`、`shell-openapi`、`shell-pages` 测试），两个 tsx 加载器的 `fileName`，
  以及 7 个 KGE 命令行工具的“是不是主模块”判断（`import.meta.url === \`file://${process.argv[1]}\``：中文目录下永远不相等，**命令行静默不运行**，比测试失败更隐蔽）。
- **修复**：一律改成 `fileURLToPath(new URL(...))`；主模块判断改成 `pathToFileURL(process.argv[1]).href`；动态 `import()` 改用 `pathToFileURL(...).href`。
- **防回归**：`tools/path-url-check/check.py`（接进 `make check-fast` 与 CHECK_GROUP_CONTRACTS），拦 `new URL(…, import.meta.url).pathname`、
  `new URL(…, *ROOT/HERE/DIR/REPO*).pathname`、目录遍历里的 `.pathname`、手拼 `file://${…}`；带正反例自检。
- **验证**：把仓库检出到 `…/升学教育产品/starbuddy`（与报告里的目录同样含中文）：修复前前端单测有 100 处 ENOENT；修复后前端单测 255 项、`make bff-test` 全量、
  阿鸦适配器、KGE 盘点 / 导入 / 覆盖 / 去重、内容登记、舞台组件的测试全部通过。

## P2-2 基础页面调 BFF 没有明确超时与网络错误处理（已修）
- **问题**：登录（`src/lib/shell-auth.ts`）、今日计划与选科等（`src/lib/shell-api.ts`）直接调 `fetch`：BFF 断连时抛异常，慢响应等底层超时，进不了页面已有的失败分支。
  同样问题还在星图 v0 / v1（无超时）、信任界面（有 catch、无超时）、建档（有超时、无 catch，读响应体也可能抛）。
- **修复**：`src/lib/bff.ts` 加共用底层 `bffFetch`：每次请求带 `AbortSignal.timeout(10_000)`（与审核台客户端一致，连同读响应体）；
  断连、拒绝连接、超时、读体中断一律返回 null，由各客户端映射成已有的失败结果：
  | 客户端 | 断连 / 超时时的结果 | 页面上 |
  |---|---|---|
  | shell-auth（登录、/me、学生模式、退出） | `503 INTERNAL` | 登录页“暂时无法完成，请稍后再试。”；家长首页、学生页按会话无效回登录页（原有分支） |
  | shell-api（今日计划、选科、额度、时长、建档等） | `503 INTERNAL` | 选科等表单“暂时无法完成，请稍后再试。”；今日计划页走原有的非 401 分支（见待人确认 1） |
  | trust-api | `503 UNAVAILABLE`（原有） | “暂时无法获取” |
  | 星图 v0 / v1 | 与 5xx 同一个分支：抛错，进错误页 | 原有错误页 |
  | onboarding | `{ ok: false, status: 503 }`；响应体不是 JSON 时 502 | 原有分支 |
  BFF 地址配置错误（`bffBaseUrl` 抛出）不吞掉。`shell-auth.ts` 的 `next/headers` 改成按需加载（与 `bff.ts` 的 `requestMeta` 相同），单测才能直接引用。
- **验证**：`apps/web/tests/bff-fetch.test.ts`（8 项）：每个客户端在三种故障（拒绝连接、一直不应答、读体中断）下都落到上表的结果，每次请求都带 signal、默认 10 秒；
  静态检查：`src` 下服务端直接调 `fetch` 的文件必须带 `AbortSignal.timeout` 并 try/catch（`bff.ts`、Service Worker、`events.ts` 除外，理由写在测试里）。
  手工变异 7 条（去掉 catch、去掉超时、改回直接 fetch、断连当 404、建档断连抛出、信任界面码变了、自练客户端去掉超时）都被发现。

## 待人确认
1. **今日计划页 BFF 不可达时显示“没有找到”**：`today/page.tsx` 对非 401 的失败一律 `notFound()`（原有行为，断连现在也稳定走这里）。是否改成“暂时无法获取”的提示？
   改的是学生可见页面的文案与分支，需产品定；本次未改。
2. **中文目录的端到端防线**：静态检查是按行的正则，只拦常见写法。要彻底防住，可在 CI 加一个任务：把仓库检出到含中文的目录跑一遍单测（约多 10 分钟）。是否加？
3. **`events.ts` 的 `post` 仍把网络异常抛给调用方**：真实页面只经 `postEventQuietly`（接住并计为 `network`），只有开发探针页（`/dev/probe`，生产构建里没有）没接住。维持现状。
