# 插件 API v1.0 签字后切换 · 实现记录

2026-10-09 起由 Claude 按用户授权自主推进；改 packages/contracts，提交首行 `[NEEDS-HUMAN]`。

## 交付
- 共享库 `DEFAULT_CONTRACT_VERSION = "1.0.0"`，`CANDIDATE_VERSIONS = {"1.1.0"}`；三科适配器缺省 1.0.0，候选集合阿鸦、知行 `["1.1.0"]`、文砺 `[]`；阿鸦能力声明随缺省改报 1.0.0。
- 0.9.0 保留为迁移期旧版，须显式指定：Makefile 原靠缺省 0.9.0 的一致性行改为显式 0.9.0；三科适配器旧用例 helpers 钉 0.9.0。
- 一致性测试中 1.0.0 各行不再开候选开关；Gate 0 覆盖率报告改取 1.0.0 那一遍（参考 2/6、阿鸦 6/6、知行 5/6、文砺 5/6），0.9.0 各行只作回归。
- 变异测试显式在 0.9.0 与 1.0.0 两版下跑（35 类违约、63 次注入全部发现），不再依赖缺省值。
- 知行、文砺新增启动测试：不设任何版本变量起正式入口，capabilities 报 1.0.0；1.1.0 无开关、未知版本拒绝启动。

## 审查意见的处理（按默认）
- **安全审查阻断**：learning-internal v1 未签字，原先靠插件 1.0.0 候选开关顺带挡住；签字后不能随之放开（`ingest.purpose`、`evidence_level` 由调用方声明，裸 `/internal/v1/grade` 可写 A 级迁移证据且不经 BFF 预留）。→ 学习服务新增独立开关 `LEARNING_INTERNAL_V1`（缺省关），挡住 `/internal/v1` 的 grade、diagnose、plan（各科 unavailable）、诊断与再练（含复测）的启动检查及再练变式，行为与签字前相同；测试覆盖开关两侧。
- 契约审查阻断：lessons 缺失（本文件）；Gate 0 样例在未提交的工作区生成 → 已撤回，代码提交后在干净工作区重生并单独提交。
- 建议：变异测试补 0.9.0、报告取 1.0.0、补缺省版本启动测试、改过时文字（检查脚本输出、README“到 W6”等）；BFF 诊断集成测试、web e2e、压测、Gate 0 联调改设新开关，去掉对 1.0.0 已不需要的候选开关。

## 与 spec 的偏差
1. 新增学习服务开关 `LEARNING_INTERNAL_V1`（spec 未列，为不放开未签字的 learning-internal v1）。
2. `tools/e2e-cohort/e2e_cohort/stack.py` 联调改为关候选开关、开新开关跑，使 Gate 0 本身验证“插件缺省按 1.0.0 工作”。
3. 重放接口 `/internal/v1/replay/...` 不受新开关约束：签字前它就不受候选开关约束，BFF 计量重试（N17）也用它，挡住会改计量回滚路径。

## 61 项核对
`plugin-api.v1.0.yaml` 与 61 项默认结论（及 learning-internal 的 N17、N18，insight-service 的 pol-#2）一致。过时说明未改（spec 不改 YAML），另开 `[NEEDS-HUMAN]` 契约文字修订：
1. `/diagnose` 说明（约 124 行）仍写“只能出现在 `report` 的预测字段中”、约 284 行“预测类字段……”，与 sie-#2 移除 `forecast` 矛盾，会误导适配器作者 —— 优先修。
2. `info.description` 仍写“候选版，待评审签字”；破坏性变更段写“v0.9 保留到 W6”，与“迁移期终点另定”不一致。
3. `Subject` 说明“v0.9 只列四科”措辞。
4. `learning-internal.v1.yaml` 约 161 行仍写“候选版开关关闭时 plan unavailable”，现应为 `LEARNING_INTERNAL_V1`。
5. YAML 示例中有具体模型标识（原有内容），请契约负责人看是否替换为占位。

## 待人确认
1. `LEARNING_INTERNAL_V1` 何时打开：随 learning-internal 1.1.0 签字（决定记录 6.3 第 5、7 条）；签字后删除该开关并让 BFF 写入切 `/internal/v1`。
2. 重放接口是否也受新开关约束（会影响计量重试，必停）。
3. 新开关暂无变异用例（“去掉这道闸”未被变异测试覆盖），可在诊断或再练变异里补。
4. 仍保留 0.9.0 的迁移期终点未定；删除 v0.9 前两份服务契约须先改指 v1.0。
5. `packages/contracts/python/build/lib/` 下有被 .gitignore 忽略的旧副本（缺省 0.9.0），学习服务加载源码目录不受影响，建议人清理。
6. `docs/gates/gate-0-evidence.md`、`docs/gates/w13-debt-and-decisions.md`（B01）中关于候选版的描述待收口。
