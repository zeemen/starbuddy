# 冲刺 2-L：行为事件埋点与“能做对吗”探针

状态：**草稿，等人确认后才开始实现**（涉及迁移、RLS、画像可见范围、禁用推断的边界、保留期与授权，均为必停项）。确认人、日期：＿＿＿
任务包：开发计划 v2.0 冲刺 2（W5–W6）L 流“行为事件埋点；做题前‘能做对吗’探针组件”，验收“事件带来源类型入库”。
关联：
- SIE V1.1 :263（四类来源）、:366（诊断和部分练习前问“能做对吗”）、:408（自评校准：Brier 分数 + 偏差方向，至少 20 次）、:468（自述与客观冲突两边都存）、
  :548（`behavior_event(kind: session_start|session_end|plan_done|plan_skip|exit_after_errors|appeal, payload)`）、:552（`confidence_probe(item_id, predicted_correct, actual_correct)`）、
  :606（不推断清单）、:610（授权与保留期）；完整方案 v1.1 :474（evidence_event + behavior_event 事件化写入、标来源类型）；
- 迁移 0010（`insight.source_type`：inferred / self / guardian / objective）、0021/0022（SIE-01：`insight.self_report` 等建档事实与写入函数的做法）、
  0004（`platform.attempt.self_report` 是做题**时**的把握度，与探针时机不同，字段字典 :308 写明不合并）；
- 字段字典 `middle.g5.behavior_events`（objective，planned）、`middle.g7.confidence_probe`（self，planned）、`self_assessment_calibration`（silent）；
- 洞察服务契约 `insight-service.v0.9.yaml` 的 `BehaviorEvent`（:633，没有 payload，只许编排层调用）；specs/plan-v2-alignment #3（新对象放 insight.*）、#7；
- CLAUDE.md 规则 2、6、11、12、13。

## 现状（调研结论）
- 没有行为事件表，也没有探针表；证据表的 `interaction_signal` 定为客观记录，只有 `signal_code` 与 `value`，学习服务只用到 `hint_opened`。
- BFF 学生端只有建档写接口；`apps/web` 还没有做题页面（做题页随冲刺 4 的统一外壳），探针组件目前没有地方放。
- 没有任何埋点代码或第三方 SDK；`docs/compliance/vendors.md` 为空。行为事件的保留期、用途分项授权（`purpose_grant`）都还没定、没建。
- 洞察服务未实现；它的 `insight.record` 契约里的 BehaviorEvent 没有 payload，也没有探针事件。

## 设计要点
### 1. 两张表（迁移 0025，草稿，由人执行；放 insight.*，对照 #3）
- `insight.behavior_event`：学生、事件类型（SIE 六种）、发生时间、payload（按类型的**白名单**键，小而结构化：计划任务 ID、会话时长秒数等，
  不收自由文本）、`source_type` 固定 `objective`、写入方、幂等键。
- `insight.confidence_probe`：学生、学科、题目引用、预测（`yes` / `no` / `unsure`）、提问时间、`source_type` 固定 `self`、关联的作答 ID（作答提交后回填）。
  **实际对错不在这里存**：按作答 ID 关联 `platform.grade_result` 取（规则 2，同一事实不存两份）。
- 都开启并强制 RLS；随学生档案删除级联；不按月分区（试点量小，量大再议）。

### 2. 写入（沿用 SIE-01 的做法：写入函数 + BFF 学生端接口）
- 库函数 `insight.record_behavior_event`、`insight.record_confidence_probe`、`insight.link_probe_attempt`（SECURITY DEFINER，只授 BFF；
  只认学生本人，家长、其他学生一律拒绝；按幂等键去重）。
- BFF 学生端接口 `POST /v0/events`（只收 `session_start` / `session_end` / `plan_done` / `plan_skip`）与 `POST /v0/probes`；计量：不消耗额度，不走预留。
- `appeal` 在申诉入口（4-H）落地时由后端写；`exit_after_errors` 是由作答序列算出的派生事件，只能由编排层按规则生成（本任务不实现，只保留类型）。

### 3. “能做对吗”探针组件（apps/web）
- 组件：题目展示前问“这道题你觉得能做对吗？”，三个按钮（能 / 不能 / 不确定），可跳过（跳过不记录）；不打分、不评价、不显示历史，文案按人设禁语表过一遍。
- 放置：`apps/web` 还没有做题页。默认先做组件与一个只在开发环境可见的演示页（生产构建里不存在），做题页落地时接入（待人确认 #6）。
- 端到端：演示页上点选 → BFF → 库里一条 `self` 来源的探针；会话开始、结束各一条 `objective` 来源的行为事件。

### 4. 不推断与可见范围
- 行为事件、探针**只给 Agent 用**（字典已登记），学生、家长界面都不显示；本任务不做任何聚合或校准计算（Brier 分数随 SIE 后续任务，且只能以判断账本的形式对外）。
- 不从行为事件推断情绪、性格、专注力等（规则 12）：payload 白名单里没有此类键；事件类型与 payload 键名过禁用推断词表检查（沿用 SIE-00 的检查脚本）；有测试。
- 不接入任何第三方埋点 SDK，全部自建；不采集设备指纹、IP、地理位置。

## 范围
### 做
迁移 0025（两张表、三个写入函数、RLS）与 DB 测试、变异、快照、攻击面；BFF 两个学生端接口与测试；探针组件、开发环境演示页与端到端；字段字典状态更新；lessons。
### 不做
- 洞察服务与 `insight.record` 契约（BehaviorEvent 加 payload、加探针事件属于契约变更，另起）；
- 校准计算与任何对外展示；`exit_after_errors` 的派生规则；申诉入口；
- `platform.attempt.self_report` 迁出；给 0005、0006 旧表补来源列（规则 6 的遗留，另起）；
- 保留期自动清理（期限待定）。

## 验收标准（可测试）
- [ ] 行为事件与探针入库，各带来源类型（objective / self），实际对错不重复存储。
- [ ] 只有学生本人能写自己的事件；家长、其他学生、未登录一律拒绝；幂等键重放不重复；BFF 之外的角色不能调用写入函数。
- [ ] payload 只收白名单键，拒绝自由文本与禁用推断类键；删除学生档案时两张表的数据一起删除。
- [ ] 学生、家长可见的任何接口都不返回事件或探针；严格 `make check`、安全审查、契约审查通过。
- [ ] 探针组件 axe 无违规，文案过禁语检查；演示页不出现在生产构建里。

## 涉及的必停项
- **db/migrations 与 RLS**：迁移 0025。**画像可见范围**：只给 Agent。**禁用推断**：payload 白名单与词表检查。
- 人设话术：探针文案（待人确认 #7）。契约：否（BFF 接口在 `services/bff/openapi`，洞察服务契约另起）。第三方：否。PR 首行 `[NEEDS-HUMAN]`。

## 待人确认
1. **表放哪**：默认 insight.*（对照 #3）；实际对错按作答 ID 关联判分表取，不在探针表另存。
2. **payload 白名单**：默认 session_start {入口}、session_end {时长秒数}、plan_done / plan_skip {计划任务 ID}；其他键一律拒绝。
3. **写入路径**：默认 BFF → 库函数（同 SIE-01），不经尚未实现的洞察服务；洞察服务契约的 BehaviorEvent 加 payload、加探针事件另起契约变更。
4. **保留期与授权**：默认先不自动删除、随学生档案删除级联；是否需要用途分项授权（purpose_grant）单独同意，请定。
5. **来源类型**：默认行为事件 = objective，探针 = self。
6. **探针放置**：默认先做组件与开发环境演示页，做题页（冲刺 4 统一外壳）落地时接入；另一个选择是本任务先在建档页后加一次性“试一题”。
7. **探针文案**：默认“这道题你觉得能做对吗？” + 能 / 不能 / 不确定 + 跳过；属于面向学生的话术，需要人设评审。
