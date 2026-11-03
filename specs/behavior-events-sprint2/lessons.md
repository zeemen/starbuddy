# 冲刺 2-L 行为事件埋点与“能做对吗”探针 · lessons

## 实现要点
- **迁移 0025**（主会话）：`insight.behavior_event`（来源 objective）、`insight.confidence_probe`（来源 self，不存实际对错，按关联作答取判分）；
  三个写入函数只认学生本人、payload 白名单、时间窗口、幂等键；只给 Agent 读；随学生档案级联删除。DB 测试 79g、变异 13 条；
  80 攻击面：三个函数有第二个 uuid 参数，登记进函数越权矩阵豁免表并写明由 79g 覆盖；字段字典两项改为 live。
- **BFF**（子代理）：`/bff/v0/students/{student_id}/events`、`/probes`、`/probes/{probe_id}/attempt`；请求体键集合严格一致、payload 白名单同 0025、
  时间 RFC3339 带时区、幂等键 UUIDv4；错误映射 42501→403、22023 等→422、23505→409、54000→429；日志只记路由、状态码、耗时与学生 ID 哈希。
  集成测试 8 项，变异新增 3 条。
- **前端**（子代理）：`ConfidenceProbe` 组件（文案常量在 `src/probe/copy.ts`）、开发环境演示页 `/dev/probe/<学生>`（构建时排除：生产构建里不存在该路由）；
  e2e 3 条：学生选“不确定”→ 库里一条 self 探针；session_start、session_end 各一条 objective 事件；axe 无违规。

## 与 spec 的偏差
1. 路径沿用现有 BFF 约定（学生 ID 在路径里），不是 spec 里写的 `/v0/events`；请求体带 student_id 回 422。
2. 同一幂等键重放也回 201 与原 ID（库函数分不出首次与重放，BFF 没有表权限去查）。
3. 无权、学生不存在、路径不是 UUID 一律 403；建档接口是“一律 404”，两者不一致。
4. 没有新加限流：BFF 里没有现成的限流可沿用，0025 函数也没有写入上限；只做了 54000→429 的映射；请求体上限沿用 16 KB。
5. 演示页开关改为**构建时排除**（`page.dev.tsx` 只在 `STARBUDDY_DEV_AUTH=1` 且环境为 dev / test 的构建里生效，运行时再核对开关与本机 Host）；
   代价是 `scripts/e2e.sh` 每次先做一份测试构建并留标记文件，构建产物检查见到标记才跳过。
6. 离开页面记 session_end 是尽力而为（pagehide 触发 server action，页面关闭时请求可能被取消；点“结束”是可靠的）。
7. 探针 `asked_at` 取提交时的服务端时间，避开客户端时钟偏差。
8. 顺带修了建档 e2e“家长版全跳过”的偶发失败：连续点“跳过”不等跳转会重复提交同一步；改为先等进入步骤页、点完再等步骤变化。

## 待人确认
1. **探针文案**（人设必停项，spec #7）：“这道题你觉得能做对吗？”、能 / 不能 / 不确定、跳过，以及演示页提示（`src/probe/copy.ts`）。
   单测用只读方式扫了 `evals/persona/diandian/lexicon/forbidden.yaml` 的 A–E 类与打分、评价类用词；该词表是点点的、`reviewed: false`，仍需人设评审。
2. **演示页构建时排除**的做法与 e2e 覆盖 `.next` 是否接受。
3. **写入上限**：0025 函数没有写入上限；是加库里的限额（改迁移）还是在 BFF 加限流。
4. **403 还是 404**：与建档接口统一哪一种。
5. spec 待人确认 #4 授权未单独答复，按默认：不单独征得用途授权、不自动删除。

## 审查处理（安全审查、契约审查，用户“按默认建议处理审查意见”）
- **写入上限**（原偏差 4、待人确认 3）：0025 两个写入函数加库内限额：同一学生 24 小时内事件最多 2000 条、探针最多 500 条，超出报 54000，BFF 映射 429；OpenAPI 与测试补 429。
- **幂等重放**（原偏差 2）：函数改为返回 `(id, replayed)`；先查幂等键再校验时间窗口（过窗重放仍回原行），同键不同内容报 23505；BFF 首次 201、重放 200 并带 `idempotent-replayed` 响应头。
- **写入方**：`behavior_event.recorded_by`（student / backend / orchestrator）与约束：appeal 只能 backend、exit_after_errors 只能 orchestrator，其余只能 student。
- **探针关联窗口**：以探针 `created_at` 为准，作答时间须在 [-5 分钟, +2 小时] 内，且学生、题目一致。
- **409 区分**：幂等键复用 `IDEMPOTENCY_KEY_REUSED`、探针已关联其他作答 `PROBE_ALREADY_LINKED`，文案都不提示重试；建档接口仍用 CONFLICT。OpenAPI 的 `prediction` 枚举加引号（YAML 1.1 会把 yes/no 读成布尔）。
- **e2e 构建**：改用单独的 distDir，不再覆盖生产 `.next`（原待人确认 2 的后半句随之消解）。
- 事件类型与 payload 键名过禁用推断词表检查（`services/bff/scripts/check_event_lexicon.py`，bff-test 自动带上，含反例自检）；字段字典写明不做作息分析、删除随档案级联。
- DB 测试 79g 补重放、过窗重放、写入方、上限（保存点批量插入）、关联窗口；变异：行为事件 12 条、探针 8 条，全部杀死。

## 待人确认（审查后更新）
1. 探针文案（人设评审，同上 1）。
2. 保留期与用途分项授权 `purpose_grant`（spec #4，按默认未做）。
3. 403 还是 404 与建档接口统一哪一种（同上 4）。
4. 上限数值 2000 / 500 是否合适。
5. 洞察服务契约 `BehaviorEvent` 加 payload、加探针事件（契约变更，另起）。
6. 作答提交后由学习服务自动回填探针关联（目前由 BFF 接口显式关联），待学习服务的作答写入路径定下后另起。
7. 词表检查脚本是否挪进 `sie-dictionary-check`（目前在 BFF 内）；`make web-visual` 前那次生产构建已多余，是否删掉。
