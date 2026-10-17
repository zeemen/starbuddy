# PLT-18 模考限时整卷 · 第一步（组卷清单、限时状态机、整卷汇总的纯函数）

2026-10-17 起由 Claude 按用户授权自主推进（开发计划 v2.0 阶段 1 · 4 月“模考限时整卷（数学、道法）与模考诊断”，提前做底座）。
编号 PLT-18 为仓库排队号。零迁移、不改契约、不改掌握计算与计量逻辑、不接路由、零新依赖。

## 现状（只读摸底）
- 库里没有卷结构（`kge.paper` 推迟到 KGE-08 落库任务）；`platform.item` 只存准入状态；分值只在 `kge.scoring_point.score`；满分与时长在 `policy.subject_config.full_score / duration_minutes`。
- 作答一次一题：再练、复测都经 `ingest("grade")`，`evidence_kind` 只有 independent / hinted / transfer / delayed_retest，`purpose` 只有 practice / retest / transfer_check，没有考试情境。
- 计量已有资源 `mock_exam`（按次、预留 150 分钟；各档 0 / 1 / 4 / 10），无调用方。
- 契约：diagnose 有 `mode: paper` 但阿鸦、知行均回 422；grade 是单题。`insight.exam_record` 只收手工录入（含 mock1 / mock2）。
- 前端计时只在诊断组件里。

## 范围（第一步只做纯函数与格式，放 `services/learning/learning/mock_exam/`，不注册路由）
1. **卷清单格式** `paper manifest`（JSON + JSON Schema 草案，放 `services/learning/config/mock_exam/`）：卷 ID、学科、版本、满分、限时分钟、分节（节名、题目 ID 列表、每题分值、题型码）、来源（合成 / 教研组卷）、`synthetic` 标志。
   - 校验：题目 ID 唯一；分值和 = 满分；满分与限时与考情配置一致（输入为考情配置的只读快照，合成样例）；题目须在“准入”清单内（输入为准入清单快照）；题型码属于该科 `question_type_codes`。
   - 合成样例：数学（九年级函数主题）与道法（法律领域）各一份，题目 ID 取现有合成夹具，不含真实题目原文。
2. **限时状态机**（纯函数，时间由调用方注入）：`created → in_progress → submitted | expired`；
   - 截止时间只由服务端在开始时计算（开始时间 + 限时 + 配置宽限），客户端时间不参与；
   - 作答：截止后一律拒绝；同题重复作答取最后一次（截止前）；交卷后拒绝；到截止未交卷 → 自动 `expired`，已作答部分照常计分；
   - 会话快照可序列化为带 HMAC 签名的不透明票据（沿用 BFF `sbp1` 与 presence 的写法思路，Python 侧用 `hmac` 标准库；密钥由调用方注入，测试用合成密钥），篡改、过期、换卷、换学生均拒绝；
   - 第一步不落库：多实例与重放防护（同一票据重复开始）写入“待人确认”，第二步建会话表时解决。
3. **整卷汇总**（纯函数）：输入为逐题判分结果（只接受带 `evidence_ids`、`confidence`、`source_version`、`model_version`、`human_review_required` 的验证器 / Rubric 结果，规则 3；缺任一字段拒绝），输出：
   - 得分、各节得分、未作答题数、超时状态；`human_review_required` 或低置信的题单列为“待复核”，总分标“暂定”；
   - 按知识节点的得失分聚合（节点映射输入为题目 → 节点快照）。只是证据汇总，**不生成任何对学生的结论性陈述**（规则 11）；报告文案、判断与对账另起。
4. **计量与证据口径只写方案**（RUNBOOK 段落，不实现）：开始时 `mock_exam` 预留、交卷或过期时结算、开始失败回滚；整卷内逐题判分不再另计再练次数；逐题证据第一步按 `independent` 口径设计，是否新增考试情境的 `evidence_kind / purpose` 值待人确认。
5. **测试与变异**：清单校验正反例；状态机全部转移、截止边界（截止前 1ms / 截止时刻 / 之后）、时钟回拨、重复作答、交卷后作答；票据篡改与换绑；汇总与手算一致、缺溯源字段拒绝、待复核与暂定标注；make 目标 `mock-exam-test`、`mock-exam-mutation` 接进 check。

## 不做
- 迁移（会话表、`kge.paper`）、契约变更（learning-internal 的 purpose、学科插件 paper 模式）、BFF 与学习服务路由、前端页面、计量调用、写证据、模考进对账或预测、任何学生可见文案。

## 验收
- 两份合成卷清单校验通过、错误清单被拒；状态机与票据测试通过；整卷汇总与手算一致；变异全部发现；严格 `make check` 通过。

## 待人确认（实施中补充）
1. 宽限时长；截止后是否允许“已开始的最后一题”提交。
2. 是否新增考试情境的 `evidence_kind / purpose`（改 0017 CHECK、learning-internal 契约、KGE-04 权重键，必停），还是按 independent 计入。
3. 模考成绩是否进入预测与对账（需放开 `exam_record` 的来源约束，迁移）。
4. 组卷来源与审核：教研组卷还是按考情配置自动组卷；卷清单放本仓配置还是内容包。
5. 第二步的会话表与重放防护（必停）；`mock_exam` 计量接入（必停）。
