# LT-04 learning-internal v1 签字与启用（[NEEDS-HUMAN]：契约签字）

2026-10-20 用户决定“现在就签”learning-internal v1（LT-01 契约审查：诊断、再练、复测在 staging 依赖它，否则教研配置签字后学习服务仍拒绝启动）。本文件是签字包，并给出启用前必须先完成的工程项。

## 一、签字范围
- 契约文件：`packages/contracts/openapi/learning-internal.v1.yaml`（当前 `info.version: 1.1.0`）。
- 签字即确认：接口集合、请求 / 响应 schema、问题码、`evidence_kind` 推导口径、`purpose` 枚举（practice / retest / transfer_check）与现行实现一致，冻结为 1.1.0 正式版；之后任何改动走契约变更（必停）。
- 签字**不等于**立即放开 `LEARNING_INTERNAL_V1`：见第二节。

## 二、启用前必须先完成（来自 specs/contract-v1.0-signoff/lessons.md 安全审查阻断）
1. **调用方声明字段不可信**：`ingest.purpose`、`evidence_level` 由调用方声明，裸 `/internal/v1/grade` 可写 A 级迁移证据。须改为由学习服务按会话 / 票据上下文推导或校验（不接受调用方任意声明），或限定只有特定服务身份可写特定用途。
2. **绕过计量**：裸 `/internal/v1/grade` 不经 BFF 预留即可写证据。须要求有效的预留 / 计次凭据（usage_id + idempotency_key 与计量账本核对），或只允许 BFF 服务身份调用且 BFF 侧已计量。
3. 契约文字修订：约第 161 行“候选版开关关闭时 plan unavailable”改为签字后口径。
4. 签字后：删除 `LEARNING_INTERNAL_V1` 开关，BFF 写入切到 `/internal/v1`（决定记录 6.3 第 5、7 条）；测试覆盖开关删除前后行为与上述两项防护。

## 三、流程
1. 本签字包由用户（代表技术负责人）确认第一节 → 契约 1.1.0 状态标为已签字（签字人、日期写入契约 `info` 的签字字段与 `specs/launch-lt04-learning-internal-v1/lessons.md`）。
2. 工程实现第二节 1–4 → 安全与契约审查 → 严格 `make check` → `[NEEDS-HUMAN]` 提交。
3. 之后 staging 才可启用诊断、再练、复测（仍需教研签字配置）。

## 待人确认
- 第一节签字范围是否确认；第二节 1、2 的防护方式（推导校验 vs 服务身份限定）。

## 签字与决定记录（2026-10-20）
- **签字**：用户（代表技术负责人）确认第一节签字范围，learning-internal v1 冻结为 1.1.0 正式版。契约文件中的签字字段在 LT-04 实现提交中写入（与第二节第 3 条文字修订同批，属契约变更，`[NEEDS-HUMAN]`）。
- **防护方式**：服务身份限定 + 计量核对——只有 BFF 服务身份可调用 `/internal/v1` 的 grade / diagnose 写入，且必须携带与计量账本核对一致的 `usage_id` + 幂等键；`purpose` 与证据等级由学习服务按票据 / 会话上下文推导，不接受调用方自报。
- 启用（删除 `LEARNING_INTERNAL_V1`、BFF 切 `/internal/v1`）在第二节 1–4 实现并过审查、严格检查后进行；LT-01 提交后开工。

## 决定记录（续，2026-10-20，用户）
实施中发现学习服务角色读不到任何计量表（0008、0015 只授权 BFF）。子代理写了迁移草稿 0058 并停下。用户决定：
1. **采用迁移草稿 0058**：只读函数 `platform.learning_meter_check(p_usage, p_key, p_student, p_method)`（SECURITY DEFINER、属主 `starbuddy_authz`、只授权 `starbuddy_learning`），只返回状态编码 `reserved / lapsed / settled / released / expired / counted / none`，不返回额度与时间、不改扣次规则。须更新权限快照并补越权与判据测试；草稿仍待人在副本演练后执行。
2. **核对不通过返回 409 `IDEMPOTENCY_KEY_SEALED`**（不改已签字契约的枚举），在契约描述中注明“该幂等键无有效计量，不可写入”。各路径放行状态：`/internal/v1` 的 grade / diagnose 放行 `reserved`；再练、复测放行 `counted`；诊断会话按会话自身 usage_id 与预留键、方法 diagnose，放行 `reserved` 与 `settled`。
3. **证据等级与 purpose 由学习服务推导**：直接经 HTTP 的写入 purpose 只能是 practice（自报其他值 400 `INVALID_REQUEST`）；键盘输入记 A，其他通道须有平台侧确认记录，若核实需新读权限则另写迁移草稿并停下。
4. 服务身份：沿用 `LEARNING_SERVICE_TOKEN`（只有 BFF 持有），补其他令牌调用写入接口 401 的测试。BFF 写入切 v1 时请求体改为 `{request, ingest}` 信封、`contract_version` 1.0.0；计量指纹算法不变（如须变更，停下）。
