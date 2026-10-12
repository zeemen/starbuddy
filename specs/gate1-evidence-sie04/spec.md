# SIE-04 Gate 1 证据管线 · 第一步（合成纵向队列与四项报表）

2026-10-12 起由 Claude 按用户授权自主推进。编号 SIE-04 为本任务首次使用（SIE 分册编号只到 SIE-03；最接近的是 S6–S8、S9–S13，`docs/plan/SIE-学生洞察引擎-V1.1.html:655-656`），待人登记。
依据：开发计划 v2.0 Gate 1 条件 `:447-454`、时间倒排 `:129`、月度节点 `:440-444`；完整方案 v1.1 门 1 `:706`、发布阻断六项 `:643`；SIE 分册中圈指标与效度检验 `:401-412`、预测校准 `:595-597`、续用 `:601`。

## 范围（只用合成数据，不连生产、不碰留出集、不新增迁移）
1. `tools/gate1-evidence/`（参照 `tools/e2e-cohort` 的 cohort、evidence 写法）：
   - 合成纵向队列：固定种子约 40 名学生、12 周的作答、复测（含首轮与 8 周后）、会话事件、两到三轮考试预测与成绩；纯函数生成，不连库。
   - 四个纯函数报表，均带最小样本门槛（不足时输出 `insufficient_sample`，不出结论）：
     - G1-2 8 周延迟复测对比（首轮 vs 8 周后正确率）与 8 周续用率；
     - G1-3 每轮考试 80% 区间覆盖率、中值 MAE、轮间对比（区间内外判断沿用 `services/insight/insight/predict.py` 的 `reconcile_plan` 口径，直接调用该纯函数）；
     - G1-4 中圈指标效度检验（指标值与结局的相关与方向，复用 `services/insight/insight/metrics.py` 纯函数计算指标值）；
     - 附加：分组误差差异（公平性；分组只用合成的非敏感分组键，不用地区、学校、家庭推断，规则 12）。
   - 方法与阈值放未签字配置 `tools/gate1-evidence/config.unsigned.json`（“改善”的定义、覆盖率区间 70%–90%、效度相关阈值、最小样本），每项标注待签字；报表输出带配置指纹。
2. `make gate1-evidence GATE1_OUT=<目录>`：确定性证据包（CSV、checks.json、summary.md，数据文件附 SHA-256，标“合成，不是试点结论”）；仓库收一份合成样例 `tools/gate1-evidence/sample`，测试比对样例逐字节一致。
   G1-1（批改一致率）、G1-5（四科分科验收）、G1-6（合规）只引用既有评测或文档位置，判定写“无法自动判断（需人）”。
3. `docs/gates/gate-1-evidence.md` 模板；`.claude/skills/gate-check/SKILL.md` 补 Gate 1 六条与附加项清单，口径同 Gate 0（满足 / 不满足 / 无法自动判断，不下总体结论）。
4. 真实试点核对只写 `tools/gate1-evidence/RUNBOOK.md`（照搬 `tools/e2e-cohort/verify.py` 的只读护栏思路），本仓不执行、不连库。

## 不做
- 迁移、指标快照表、可见范围变更、指标 `validated: true`、判断模板、打开 8 周复测点或改作答用途映射、读或用留出集、改金标。

## 验收
- 证据包确定性（同种子两次逐字节一致）；样本不足时不出结论；四项报表各有纯函数测试与反例（覆盖率边界、MAE、轮间、相关方向、样本门槛）；变异测试接进 `make check`；严格 `make check` 通过。
