# 分学科闸门的日志字段补进白名单，并加定时汇总 · 规格草稿

- 状态：**草稿（2026-11-20），等人确认第五节**。
- 来源：`specs/subject-concurrency-plt16/lessons.md` 待人确认第 4 条的后续项“`stats()` 的生产出口（目前只能从访问日志汇总）”
  （2026-10-17 用户确认排入后续任务）。核对时发现“从访问日志汇总”在生产上其实做不到，见第一节。
- 涉及安全审查 B（日志字段白名单）：只加四个取值是固定枚举的字段，不加任何标识、内容或自由文本；请安全负责人知情（待人确认第 2 条）。
  不涉及 CLAUDE.md 的必停项（不改迁移、契约、权限、计量、人设）。

## 一、现状（2026-11-20 核对）
1. **闸门的访问日志字段在生产上被丢掉**：闸门在每个经过它的请求的访问日志里记四个字段：`gate`（admitted / queued / rejected / bypassed）、
   `gate_subject`（学科码）、`gate_queue_ms`（排队分档 0 / lt100 / lt500 / lt2000 / ge2000）、`gate_reason`（被拒原因）。
   但生产的访问日志走 `src/observability/log.ts` 的 `emit`，顶层字段只留 `LOG_FIELDS` 白名单（LT-05 安全审查 B），这四个都不在里面，**整项丢掉**。
   本机验证：`format('info', { event: 'x', gate_subject: 'aya', gate_reason: 'full', status: 503 })` 输出里只剩 `event` 和 `status`。
   集成测试没发现，是因为测试把日志函数换成了直接收原始记录的数组（`tests/subject-gate.integration.test.ts` 的 `log: (l) => log.push(l)`），
   不经过白名单。
2. **当前占用看不到**：`stats()` 有各科当前在处理数、排队数和累计计数，但只在进程内，没有任何出口。访问日志只能看到“被拒了几次”，看不到“平时占了多少”，
   定闸门数值（PLT-16 待人确认第 1 条）缺数据。
3. 闸门在 staging 还是关的（`deploy/flags/staging.yaml` 的 `subject_gate`，“压测项，是否在 staging 打开由人定”）。

## 二、默认做法
1. **四个字段补进白名单**：`gate`、`gate_subject`、`gate_queue_ms`、`gate_reason` 加进 BFF 的 `LOG_FIELDS`，并同步到 Python 三份 `obs.py`
   （`deploy/tests/test_obs_shared.py` 要求四份逐项相同）。取值都是代码里的固定枚举，不含学生、账号与请求内容。
2. **防止再犯**：BFF 加一条静态检查（`tests/observability.test.ts`）：源码里所有 `ctx.note('键', …)` 与闸门 `observe?.('键', …)` 的键都必须在
   `LOG_FIELDS` 里，否则测试失败。另加一条用例：闸门打开时，真实的 `format` 输出里有这四个字段。
3. **定时汇总**：闸门打开时，BFF 每 60 秒写一行 `event: subject_gate_stats`，把 `stats()` 放进已登记的 `counts` 字段
   （按学科：当前在处理数、排队数与各项累计计数）。只有数字，不含学生与请求信息。闸门关时不写；定时器 `unref()`，不挡进程退出。
4. **不加新出口**：不新开 HTTP 接口、不接新的监控服务（新增第三方服务属必停）。SLS 已经采 BFF 的标准输出，按 `event` 过滤就能汇总。
   IaC 里的日志索引字段（`deploy/iac/check_iac.py` 的 `LOG_INDEX_FIELDS`）这次不改，改了要重新 plan / apply（见待人确认第 3 条）。

## 三、测试
- BFF 单测：上面两条静态检查与 `format` 用例；定时汇总用注入的定时器测“闸门开时按间隔写一行、内容只有计数、闸门关时不写”。
- BFF 变异：去掉白名单里的 `gate_subject`、定时汇总不写 `counts`，各一条。
- `deploy-check`（含 `test_obs_shared.py`）、`make bff-test`、`make learning-test`、`make insight-test`、`make knowledge-test`（三份 `obs.py` 改了）、`make check-fast`。

## 四、不做
- 前端、学习服务、洞察服务的业务逻辑；闸门数值；多实例共享状态（PLT-16 待人确认第 3 条）。
- 改 IaC 日志索引、在 staging 打开闸门（都由人定）。

## 五、待人确认（括号里是默认）
1. **定时汇总间隔**：60 秒（默认）；还是另定？
2. **白名单加四个字段**：加（默认，取值全是固定枚举）；请安全负责人知情。还是要先过一轮安全审查？
3. **日志索引**：这次不改 IaC（默认）；还是把 `gate`、`gate_subject` 也加进 SLS 的字段索引（要 plan / apply，按“每次 apply 前给 plan 摘要”的约定另做）？
