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
1. **拍照通道怎么标记**：契约里没有输入通道或证据等级字段，适配器从 `prior_evidence` 读 `interaction_signal` 的 `evidence_level_a/b/c`，
   或者读 `ocr_transcription`。只有拍照识别、没有等级标记的按 C 级处理；多个标记取最严。
   平台要么按这个约定传，要么在契约 `AttemptInput` 上加 `evidence_level`（契约变更，必停项）。
2. **plan 的掌握映射**：只有 transfer / retain 算已掌握；各状态对应的数值、任务分钟下限（diagnose 5、practice 10）都未经教研确认。
3. **字数规则**：“不足 50 字也扣 1 分”，向上取整，属于判分逻辑。
4. **置信度常数**（主观维 0.4、规则维 0.9、客观题 1.0、拍照 0.7）都未校准。
5. spec 待人确认 #1–#8 都按默认执行。判分 Prompt 与判分逻辑属于必停项，PR 首行写 `[NEEDS-HUMAN]`。
