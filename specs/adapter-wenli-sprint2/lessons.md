# ADP-语文-01 文砺适配器 · lessons

## 实现要点
- `adapters/wenli/`：骨架照知行，包括 ajv 契约自检（fail closed）、幂等 409、令牌 SHA-256 定长比较、v0.9 / v1.0 候选版开关，
  以及两条启动检查（开了代理就拒绝启动；路由令牌与服务令牌相同就拒绝启动）。teach / generate_variation / report 回契约规定的未实现。
- **合成内容包**，全部 pending_review，标“合成、非真题”：
  - 作文题 2 道；
  - 五维量表 `wenli-essay-5d@0.1.0` 加字数规范维，共 50 分；
  - 客观题 10 道，能力格 10 个（`CHN.SYN.D<n>_L<m>`）；
  - 登记脚本只输出 SQL。
- **grade**：两段式。
  - 第一段：模型逐维定档，经学习服务路由接口调用，任务类型 T4，数据级别 L2，Prompt `wenli-essay-grade@0.1.0`。
  - 第二段：确定性后处理，档位换分值，不确定档位换成 uncertainty 分值区间；证据位置必须落在作文原文里；重算总分；一类卷强制复核。
  - 卷面书写维不可判：`score: null`，原因 `dimension_unavailable`。
  - 字数规范维是确定性的 `rubric_rule`，规则 `wenli-convention-rules@0.1.0`。
- **diagnose**：客观题确定性判分。观测置信度按作答通道加权：原生测评 1.0，拍照已确认 0.7。
  C 级或需要确认的拍照作答列入 `ocr_confirmation_required`，不判分。缺口选择 `pickGapCells` 已移植，但契约里没有字段放它，所以不输出。
- **plan**：确定性。移植 `recommendTargets`，按时间预算截断，不含提分。
- **测试**：
  - 适配器单测 97 条；conformance v0.9 / v1.0 各 25 过、3 跳过；`make adapter-wenli-test` 与文砺 conformance 并入 `make check`。
  - 学习服务端到端 `tests/test_e2e_wenli.py`：
    - 一篇合成作文经学习服务 → 适配器进程 → 路由接口（假供应商）→ 平台库；
    - 断言 evidence_ids 一致；四个主观维是 rubric_model 且 confidence ≤ 0.5；字数维是 rubric_rule；卷面维不可判；
      真实快照名 `model:fake-a/fake-chat@2026-09-01`（T4 路由）；需要复核；
    - 再走一遍统一复核队列闭环（冲刺 2-G 三科验收之一）。

## 与 spec 的偏差
- 缺口建议不输出（待人确认 #7 默认）；plan 的 `item_refs` 只取已准入的题，内容包全是 pending，所以实际输出里没有 item_refs。
- `exam_date` 不参与 plan 计算：算倒计时要用“今天”，会破坏确定性。
- 作文全部可判时 verdict 是 `partial`（与契约示例一致）；有维度不可判时是 `needs_review`，按契约要求带 `verifier_boundary_case`。
- 一致性夹具由实现会话起草，需要独立复核、教研抽查。

## 踩过的坑
- 子代理在主工作区实现，途中我同时在改 `db/tests/mutations.sh`。它跑 `make check` 时撞上改到一半的文件，报语法错误；
  事后复查语法已通过。并行子代理共用一个工作区时，不能靠它跑全量检查，全量检查由主会话合并后做。
- 测试路由表里 T4 路由到 `fake-chat`，T2 路由到 `fake-reasoner`；端到端断言按实际路由写，不能照抄知行。

## 待人确认（实现中新增）
1. **拍照通道怎么标记**：契约 v1.0 的 GradeRequest 已有 `evidence_level` 与 `answer_evidence_ref`，但 DiagnoseRequest 的作答（AttemptInput）没有
   （审查后更正：原先写成“契约没有证据等级字段”）。适配器暂从 `prior_evidence` 读 `interaction_signal` 的 `evidence_level_a/b/c` 或 `ocr_transcription`；
   这个约定在真实链路上走不通，见下方“审查意见处理”第 1 条。
2. **plan 的掌握映射**：只有 transfer / retain 算已掌握；各状态对应的数值、任务分钟下限（diagnose 5、practice 10）都未经教研确认。
3. **字数规则**：“不足 50 字也扣 1 分”，向上取整，属于判分逻辑。
4. **置信度常数**（主观维 0.4、规则维 0.9、客观题 1.0、拍照 0.7）都未校准。
5. spec 待人确认 #1–#8 都按默认执行。判分 Prompt 与判分逻辑属于必停项，PR 首行写 `[NEEDS-HUMAN]`。

## 审查意见处理（按默认建议，2026-09-29）
1. **拍照通道约定走不通（契约建议 1、安全 A1）**：学习服务不把 ingest 的证据等级转给适配器；当次拍照的 OCR 证据还没入库，不能作为 prior_evidence 引用；
   C 级证据被学习服务拒收；也没有任何一方写 evidence_level_* 信号。所以经平台时，拍照作答在适配器看来都是 A 级：观测置信度 1.0 而不是 0.7，
   `ocr_confirmation_required` 不会输出。
   - **适配器的通道门禁不是安全边界**。真正的边界是学习服务的 ingest：v1 路径 `ingest.evidence_level` 必填，C 级不写能力观察。
   - 验收第 3 条（按通道加权、C 级列入 ocr_confirmation_required）改为**部分满足**：单测成立，端到端不可达。
   - 另起任务（待人确认）二选一：学习服务核对 prior_evidence 的 kind 与 payload，并把 ingest 等级按约定转发；或者契约给 AttemptInput 加
     `evidence_level`，由学习服务透传（必停项），随后删掉私有约定、澄清 `ocr_confirmation_required` 的描述（契约备注 4）。
     同一任务里，学习服务在 v0.9 路径上拒收拍照通道（v0.9 没有等级，适配器默认放行）。
2. **注入防护（安全 A2）**：作文命中指令特征时，四个模型维一律不采纳（score 为 null，原因 `injection_suspected`），
   整篇 needs_review，带 verifier_boundary_case 与 low_confidence；不采纳的档位不算一类卷。补了“模型照注入给出全 L4”的单测，conformance 夹具同步。
   这属于判分逻辑，待人确认。
3. **复核原因分不清失败类型（契约建议 3）**：有模型维不可判时追加 `low_confidence`，与卷面维按设计不可判区分；有单测。
4. **grade 不读 v1.0 的 evidence_level（契约建议 2、安全 B1）**：维持现状。C 级照常批改（ADR 15），不进学习者模型由平台保证。
   要不要对 B/C 级拍照作文追加 `ocr_low_confidence`，待人确认。
5. **路由接口没有按学科限定任务与数据级别（安全 A3，本任务之前就有）**：另起任务，在路由接口上按学科设 (task, data_class) 白名单，
   例如文砺只允许 {T4: L2}。涉及出境路径，待人确认。
6. 维持现状、只记录：证据片段只核对范围（安全 B2，加最短长度属于判分逻辑）；注入词表可能误伤正常作文（B3）；
   Makefile“跳过”分支的已知问题（B4）；detail 带 instancePath（B5）；rule_ref 没有登记处（契约备注 5，平台已有缺口）；
   uncertainty 不落库（备注 6）；缺口建议不输出（备注 7）；卷面维标 rubric_model（备注 9）。
