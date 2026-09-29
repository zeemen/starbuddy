# ADP-道法-01 知行适配器 · lessons

## 实现要点
- 两个 worktree 并行：学习服务内部路由接口（本会话）与 `adapters/zhixing/`（子代理），各自测试通过后合并；端到端在合并后补。
- **学习服务内部路由接口** `POST /internal/v1/model/route`：令牌按学科发放并绑定学科；严格校验；路由拒绝 400、无可用供应商 503；
  日志不记 messages 与 output；`learning-internal.v1.yaml` 新增路径、schema、错误码（契约变更）。测试 24 条。
- **适配器**（34 个文件）：骨架照阿鸦（ajv 契约自检、fail closed、幂等 409、令牌 SHA-256 定长比较）；
  - grade 两段式：模型逐点判定（经路由接口，T2、L2，Prompt `zhixing-m3-grade@0.1.0`，从 legacy 移植、去人设语气与总评）+ 确定性后处理
    （clamp、0.5 倍数、重算总分、证据位置必须落在作答原文内、满分与矛盾强制复核）；每维 `rubric_model`、主观、confidence 0.4；一律复核；无自由文本；
  - diagnose（process）只汇总观测；generate_variation 只从已准入题选，内容包全是 pending，所以线上返回空并给 empty_reason；
  - 合成内容 4 道（`zhixing:synthetic-m3-0N`，标“合成、非真题”），登记脚本只输出 SQL。
  - 单测 77 条；conformance v0.9 与 v1.0 各 26 过、3 跳过；`make adapter-zhixing-test` 与 conformance 并入 `make check`。
- **端到端**（`services/learning/tests/test_e2e_zhixing.py`）：学习服务 → 适配器进程 → 本机回环上的路由接口（uvicorn 线程、lifespan 关闭）→ 假供应商 →
  平台库；断言 evidence_ids 一致、每维 rubric_model / 主观 / confidence ≤ 0.5、真实快照名 `model:fake-a/fake-reasoner@2026-09-01`、复核标记，
  以及判分 Prompt 确实经路由接口发出、适配器进程环境里没有任何供应商配置。

## 与 spec 的偏差
- `rubric_version` 写作 `zhixing-synthetic@0.1.0`：契约 GradeRequest 与 `kge.scoring_point` 都要求 VersionedName，不带 `rubric:` 前缀；前缀只出现在 source_version。
- 只做同步返回，没有实现 202 异步（契约允许；grade 预算 10 秒内完成）。
- 一致性夹具由实现会话起草（spec 没要求独立会话），需要独立复核。

## 踩过的坑
- 子代理拿到的 worktree 基于旧提交（没有 spec 与 v1.0 契约），先快进到当前分支再实现；以后派 worktree 子代理前先确认基线。
- 契约加载器从主工作区读契约文件：在 worktree 里改 OpenAPI，校验会读到主工作区的旧文件，只能合并后再验。
- 学习服务的 v1 路径要 `STARBUDDY_ALLOW_CANDIDATE_CONTRACT=1`（候选版），端到端测试与 test_v1_replay 一样只在测试里放开。
- Makefile 的“跳过”分支里 `exit 0` 只结束那一行，make 会继续跑下一行（已有问题，严格模式下不受影响）；另起任务修。

## 待人确认（实现中新增）
1. **diagnose 的输入**：契约 `DiagnoseRequest` 没有携带既往逐点得分与归因的字段，diagnose 只能按有无命中片段给 partial / inconclusive，
   移植的 `m3ToDimensionRates` 暂时用不上；要按它算，需要契约加字段（必停项）。
2. **复核原因码**：契约规定 `verdict = needs_review` 必须带 `verifier_boundary_case`；模型输出不合格时照此填写，语义上不是“验证器边界”。
   是否给模型判分加专门原因码，由契约评审决定。
3. **错因映射**是草稿（error_attributions 置信度 0.4），“转化错”“定位错”在映射表里仍标 disputed。
4. **置信度常数**（主观维 0.4、diagnose 0.3 / 0.1）未经校准。
5. 路由返回 `model_mismatch` 时，复核原因加 `low_confidence`。
6. 判分 Prompt 与后处理规则属于判分逻辑（必停项，spec 待人确认 #2）；学习服务内部路由接口的 OpenAPI 属于契约变更。PR 首行 `[NEEDS-HUMAN]`。
7. 路由接口不按幂等键去重：适配器同 key 重试会再调一次模型（适配器自己有幂等缓存，重启后才会重复调用）；计量由学习服务上游的预留覆盖。
