# 插件 API v1.0 签字后切换（关候选开关、缺省版本切到 1.0.0、重生 Gate 0 样例）

2026-10-09 起由 Claude 按用户授权自主推进。改 packages/contracts，属必停项，提交首行 `[NEEDS-HUMAN]`。

## 背景
用户 2026-10-09 确认插件 API v1.0 的 61 项全部按默认结论签字（`docs/reviews/decisions-2026-10-09.md` 5.1）。
签字后的动作见同文件 6.3 第 1–4 条；本任务只做这四条。

## 默认口径（写进 lessons 待人确认，可推翻）
- 【表决表】写“随本表一起确认”的 5 处 61 项之外的候选版变更（`idempotency_key` 限定小写 UUIDv4 等，决定记录 5.2 末条）已由用户 2026-10-09 在对话中确认随 61 项一并签字。
- 1.1.0（PLT-12 present_item）未签字，仍是候选，仍要 `STARBUDDY_ALLOW_CANDIDATE_CONTRACT=1`。
- 0.9.0 保留为迁移期旧版，不删（迁移期终点另定，6.3 第 6 条）。
- `learning-internal`、`insight-service` 两份契约未签字，本任务不动它们的版本号与 BFF 写入路径（6.3 第 5、7 条另起）。

## 范围
1. 核对 `packages/contracts/openapi/plugin-api.v1.0.yaml` 与 61 项默认结论一致（【清单】`specs/contract-conformance-sprint1/v1.0-review.md`）；发现不一致只记进 lessons，不在本任务改契约内容。
2. 共享库 `packages/contracts/python/starbuddy_contract/contract.py`：`CANDIDATE_VERSIONS` 去掉 1.0.0；缺省版本 0.9.0 → 1.0.0；相关注释与 README“版本策略”同步。
3. 三科适配器 `adapters/{aya,zhixing,wenli}/src/contract.ts`（及 adapter.ts / main.ts 中相应处）：`CANDIDATE_CONTRACT_VERSIONS` 去掉 1.0.0，`DEFAULT_CONTRACT_VERSION` 改 1.0.0；阿鸦能力声明改报 1.0.0。
4. 学习服务调用适配器使用的缺省插件契约版本随共享库切到 1.0.0（`services/learning/learning/config.py` 等）；内部 API 路径不变。
5. Makefile 契约测试中 1.0.0 那几行去掉 `STARBUDDY_ALLOW_CANDIDATE_CONTRACT=1` 与 `--allow-candidate-contract`；1.1.0 那几行保留。0.9.0 仍保留一组回归（若现有没有，补一行参考适配器回归）。
6. 测试：候选开关的单测改为“1.0.0 不需开关、1.1.0 仍需开关、未知版本拒绝”；适配器同样。
7. 重生 Gate 0 样例：干净工作区跑 `make gate0-evidence GATE0_OUT=tools/e2e-cohort/sample`，lessons 写清差异。

## 不做
- 改契约 YAML 内容、反例、变异语义；删除 0.9.0；签另外两份契约；BFF 切 `/internal/v1`。

## 验收
- 不设 `STARBUDDY_ALLOW_CANDIDATE_CONTRACT` 时，共享库、学习服务、三科适配器缺省按 1.0.0 工作；1.1.0 不设开关仍被拒绝。
- 0.9.0 显式指定时仍可用。
- Gate 0 样例已重生，差异有说明；严格 `make check` 通过。
