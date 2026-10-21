# LT-02 真实模型供应商接入路由（W3，specs/launch-test-2026）

2026-10-21 起由 Claude 推进。目标：staging 内部成员测试能经模型路由调用境内已备案的真实模型（知行判分 T2、文砺批改 T4、点点人设评测 T1；知识全量重编 T8 可选），**仓库内不含任何真实地址值、密钥、账号**；供应商开通、密钥、预算上限、应用登记由人完成。新增第三方属必停，提交首行 `[NEEDS-HUMAN]`。

## 依据
- 《底层大模型选型建议 V1.0》：按任务分层、双云；主平台阿里云百炼（Qwen3.8 系列），第二平台火山方舟（豆包 2.1、托管 DeepSeek）；生产只调带日期的快照名；Claude / Gemini / GPT 不进生产。
- 现状（调研）：`services/learning/learning/model_router/` 骨架、校验、熔断、调用记录已齐；staging / pilot / prod 路由表为空；`docs/compliance/vendors.md` 为空模板；业务代码无绕过路由的直连。

## 范围（默认，待人确认）
1. **staging 路由表**：`services/learning/config/model_routes.yaml` 新增 staging 节，登记两家供应商（百炼、方舟），地址与 Key 只写变量名，`whitelist: pending`（人批准后改 approved，staging 校验要求 approved 才可启动用于 L2）。路由：
   - T1 教学对话：主 Qwen3.8-Flash（非思考），备 豆包 2.1 Turbo；
   - T2 判分推理：主 Qwen3.8-Max（思考），备 DeepSeek V4.1-Flash（方舟托管，思考）；
   - T4 作文批改：主 Qwen3.8-Max，备 豆包 2.1 Pro；
   - T8 后台批量：主 DeepSeek V4.1-Flash（方舟），备 Qwen3.8-Flash；
   - T9 守卫：本期不接云内容安全（另起任务），保持现有自建规则。
   - `api_name` 写快照名占位（形如 `<由人填写的带日期快照名>`），启动时若仍为占位则拒绝——快照名由人在开通后按控制台实际名称填写。
2. **staging 校验加严到 pilot 口径**：必须登记 `allowed_hosts`、必须显式带日期 / 版本的 `api_name`、拒绝占位值。
3. **供应商登记联动**：`docs/compliance/vendors.md` 增两家条目（状态“待开通 / 待登记”），新增检查：路由表里 staging 及以上环境引用的供应商必须在登记表中且状态一致。
4. **变量与出网**：`deploy/config/env-inventory.yaml` 登记供应商地址 / Key 变量（标密钥）、两科路由客户端令牌；`deploy/k8s/staging.yaml` 出网白名单写供应商主机名占位与说明；`deploy/flags/staging.yaml` 的 `model_provider` pending 说明改为“待人开通与填写快照名”。
5. **成本护栏（路由层，进程内）**：按环境配置每任务每日调用次数与 token 上限（staging 缺省小额度，由人改），超限返回确定性错误（适配器回 503 / 可读提示），不改计量与扣次规则；调用记录补成本估算字段（单价表由人填，缺省为空则不估算）。
6. **真实模型冒烟与人设回归手册**：不进 CI；补 `make model-smoke ENV=staging`（人执行，限次）与 `compare.py` 在 staging 的执行步骤；CI 仍只跑假供应商。
7. 测试：配置解析与校验的正反例、占位拒绝、登记联动、成本护栏、出网过滤、变异。

## 不做
- 真实密钥、真实地址、真实调用；语音（T5 / T6）、向量检索（T7）、云内容安全（T9 云服务）；调用记录落库（`platform.model_call_log`，随计量 v2）；熔断跨实例共享；流式输出；四科原产品迁移。

## 验收
- staging 配置在“占位未填”时拒绝启动、填入合成快照名与合成主机后通过；境外 / 未批准 / 未登记供应商在 staging 被拒；成本护栏超限行为有测试；严格 `make check` 通过。

## 待人确认
1. 主平台百炼、第二平台方舟（选型建议默认）；是否本期就接 DeepSeek（方舟托管）。
2. 成本护栏放在路由层进程内（多实例时上限按实例计）是否可接受；staging 缺省额度。
3. staging 校验加严到 pilot 口径。
4. 调用记录落库是否本期做（需迁移草稿）。

## 决定记录（2026-10-21，用户）
1. 供应商按选型建议：阿里云百炼为主、火山方舟为备，本期接 DeepSeek（方舟托管）作 T2 备与 T8 主；仓库只登记变量名与“待开通”状态，开通、密钥、快照名、应用登记由人完成。
2. 成本护栏放在路由层进程内（多实例时上限按实例计），staging 缺省小额度，由人调整。
3. staging 路由校验加严到 pilot 口径（必须 allowed_hosts、带日期快照名、拒绝占位值）。
4. 调用记录本期只记日志并补成本估算字段；落库随计量 v2。

## 决定记录（续，2026-10-21，用户）
5. 适配器接线：staging 下学习服务另在 127.0.0.1 起一个只服务模型路由接口（`/internal/v1/model/route`）的监听；适配器不改代码（沿用 LT-01 决定：同 Pod 回环互访）；补测试与部署文档。
