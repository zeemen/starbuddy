# LT-04 learning-internal v1 签字与启用 · 实现记录

2026-10-20/21 由 Claude 推进（specs/launch-test-2026）。提交首行 `[NEEDS-HUMAN]`：迁移草稿 0058、契约文字与签字字段（v1）及 v0 下线说明、计量放行规则（学习服务侧核对、诊断会话放行有效期内过期预留）、v0 写入下线。

## 签字记录
- learning-internal v1：用户（代表技术负责人）2026-10-20 签字，冻结为 1.1.0 正式版；契约 `info.x-signoff` 已写入（签字人、日期、状态 signed、版本 1.1.0、记录指向 spec）。接口、schema、枚举、路径未改（去掉 description / summary / x-signoff / examples 后与签字前结构比对一致）。

## 交付
- **迁移草稿 0058**：`platform.learning_meter_check(p_usage, p_key, p_student, p_method)`，SECURITY DEFINER、属主 `starbuddy_authz`、search_path 以 pg_temp 结尾、只授 `starbuddy_learning`、STABLE。只返回状态编码 reserved / lapsed / settled / released / expired / counted / started_in_time / none，不返回额度与时间、不改扣次。`started_in_time`：方法 diagnose、预留已 lapsed 或 expired、且存在 usage_id + 幂等键 + 学生一致的诊断会话，其创建时间不晚于预留过期时间（时间只在库内比较）。权限快照、越权与判据测试（db/tests/79zf）、db 变异已补。**草稿，待人在副本演练后执行，且须先于本版学习服务。**
- **学习服务**：`learning/meter.py` 放行规则——v1 直接写入只放行 reserved；再练、复测只放行 counted；诊断会话放行 reserved / settled / started_in_time。核对在幂等认领之后、调适配器之前，已结算后的重放不受影响；不通过返回 409 `IDEMPOTENCY_KEY_SEALED`（不可重试，不封存）。启动时核对 0058 存在且有执行权，否则拒绝启动。
- **证据属性推导**：直接写入 purpose 只能 practice（其他 400）；自报证据等级 400；非键盘通道 400；键盘记 A；复测只由进程内调用方按票据写。
- **v0 写入下线**：`/internal/v0/grade`、`/internal/v0/diagnose` 一律 410（不读请求体、不落库）；0.9.0 写下的结果仍可经 v1 重放接口查回；v0 契约文件加 deprecated 与 410 说明。
- **开关删除**：`LEARNING_INTERNAL_V1` 从学习服务、部署矩阵、环境变量清单、e2e 与压测启动中移除；开关矩阵新增 pending `migration_0058`。
- **BFF**：改发 `/internal/v1/{method}`，信封 `{request, ingest:{channel:'keypad'}}`；`contract_version` 非 1.0.0 时在预留前抛 TypeError，缺省补 1.0.0；计量指纹算法与输入不变。
- **测试夹具防护**：`learningtest.provision_meter` 不授学习服务角色、库名限定 `^starbuddy_(dev|test)`、由超级用户池代建；迁移后的库不存在 learningtest schema 有断言。
- **压测种子**：`writepath.SeedMeter` 以 BFF 角色走计量函数预留 / 结算后再写。

## 测试
子代理逐目标：db-test 3137 断言、learning-test 3600、diagnosis / practice / retest / staging / internal-v1 变异全发现、bff-test、deploy-check、loadtest-test 175、contract-test 与 contract-mutation（284）通过；db-mutation 与 bff-mutation 只跑了相关组（25、17 全发现），全量由严格 `make check` 覆盖（见提交说明）。

## 审查意见的处理
- 安全阻断：v0 绕过计量 → 用户决定下线；测试夹具后门 → 按默认收权、库名防护、反向断言；诊断会话卡死 → 用户决定放行有效期内开始的会话。建议全部采纳（启动核对执行权、BFF 版本校验、压测种子、文档统一）；核对与落库间时间窗（只会少扣、不会多扣）与“pending 幂等行先于核对”（需猜中 UUIDv4）记为已知、可接受。
- 契约阻断：签字契约中与实现矛盾的旧文字 → 用户决定扩大文字修订；lessons 缺失 → 本文件；0058 硬依赖未在矩阵体现 → pending 项。

## 教训
- 签字契约的“与现行实现一致”要逐段核对说明文字与示例，schema 校验发现不了过时的说明与示例取值。
- 新增服务侧核对时，旧版本路径（v0）要同时处理，否则防护可被整体绕过。
- 测试夹具若是 SECURITY DEFINER，必须自带环境防护，并不得授予被测服务角色。
- 计量放行规则要和真实用户节奏（暂停、跨天续做）一起推演，未成年用户卡死比少扣更不可接受。
- 包装函数里的参数校验放在 try 之外，否则会被“结果未知”分支吞掉。

## 待人确认
1. **0058 执行**：先副本演练，并先于本版学习服务执行；缺 0058 学习服务拒绝启动。
2. **诊断会话续做无上限**：有效期内开始的会话暂停后可无限期续做、过期预留一直放行，是否加上限。
3. **0058 返回码由七种变为八种**（新增 `started_in_time`）。
4. **v0 契约文件**加 deprecated 与 410（超出“v1 只改文字”，属契约变更）。
5. **示例名** `DiagnoseIngestCanvasC`、`DiagnoseSummaryLevelC` 沿用旧名（内容已改为键盘 A 级），是否改名。
6. **0.9.0 写入核心**生产已无调用方，仅测试进程内使用，是否删除。
7. **压测种子扣额度**：每名写路径学生多两笔已结算批改预留；真实压测基线未实跑。
8. **v0 410 不验令牌**的口径。
9. **非键盘通道一律 400**：输入通道服务（ADR 15）上线前是否对部分情况放行。
10. 其余契约（插件契约等）中若仍有“待签字”字样，另起修订。

## 决定记录（2026-10-21，用户“按建议确认”）
对上节待人确认 1–10 按默认确认：
1. 0058 按“副本演练 → 先于本版学习服务执行”的顺序，由人执行（不代为执行）。
2. 诊断会话续做暂不加上限，内部成员测试期间观察；外部家庭测试前复议。
3. 接受 v0 契约文件加 deprecated 与 410（契约变更已随 ea5ea2b 以 `[NEEDS-HUMAN]` 提交）。
4. 示例名沿用旧名，下次契约修订时一并改名。
5. 0.9.0 写入核心暂保留（仅测试进程内使用），外部测试前另起任务删除。
6. 接受压测种子占批改额度；真实压测基线随 staging 部署后再跑。
7. 接受 v0 写入 410 不验令牌（不读请求体、不落库）。
8. 非键盘通道在输入通道服务（ADR 15）上线前一律 400。
9. 其余契约中残留的“待签字”字样另起文字修订任务。
