---
name: gate-check
description: 汇总阶段 Gate 的证据包（Gate 0 / Gate 1），逐条核对通过条件。用法：/gate-check <0|1>
---
# 阶段门证据汇总

读取 docs/plan/开发计划-v2.0.html 第六章（Gate 0）或第七章（Gate 1 条件）中对应 Gate 的硬条件与软条件，逐条收集证据：
- Gate 0 硬条件（五条）：
  1. 三科写入同一学生模型（查询 20 名合成测试学生的三科事件与节点查询记录；星图截图）；2026-10-01 用户决定 Gate 0 “四科”改为“三科”，英语搁置不计；
  2. 家长隔离与扣次幂等（渗透测试报告；计量并发、断线、重试、回滚测试）；
  3. 点点跨模型一致性有明确结论（三家模型对照报告与决策记录）；
  4. 数学、道法知识切片过门（课标承接 100%；留出集无处可标 < 2%；kappa ≥ 0.7；打标 Top-1 ≥ 90%；核心节点页面发布率 100%；道法采分点一致率 ≥ 90%）；
  5. 画像底线（可见陈述 100% 引用判断；禁用推断 0；C 级识别内容不生成结论；删除覆盖全表）。
- 软条件：契约测试覆盖率（阿鸦 6/6、知行与文砺 ≥5/6、开口成章已搁置，按三科口径不计入）；页面优先对照评测结论；人设家族级回归在 CI 中运行、发布闸门三科各有一个版本；监管分类意见与境内试点环境；单位经济首版。
- Gate 1 条件（六条，开发计划 v2.0 第 447–454 行）与附加项；模板 docs/gates/gate-1-evidence.md，证据管线 `make gate1-evidence GATE1_OUT=<目录>`：
  1. G1-1 各科批改与教师一致率达标，发布阻断指标无超阈值（只引用：完整方案 :643、evals/daofa-grading、make eval-grade、make eval-persona、教研报告）；
  2. G1-2 试点用户 8 周延迟复测改善，且续用（证据包 retests.csv、checks.json 的 G1-2）；
  3. G1-3 至少两轮学校考试对账，80% 区间覆盖率落在 70%–90%，误差较首轮下降（exams.csv、checks.json 的 G1-3；区间内外按洞察服务 reconcile_plan）；
  4. G1-4 至少两个中圈指标通过效度检验并上线（metrics.csv、checks.json 的 G1-4；“上线”另附记录）；
  5. G1-5 四科通过分科验收（完整方案 5.9，含知识切片与画像两项；只引用）；
  6. G1-6 合规路径明确（只引用：完整方案 :623、docs/compliance/vendors.md、docs/gates/human-checklist.md）；
  附加：画像误差的群体公平性（fairness.csv、checks.json 的 G1-附加；分组只用非敏感键，不用地区、学校、家庭，规则 12）。
  口径：报表结果是 insufficient_sample 时不得写“满足”；方法与阈值来自 tools/gate1-evidence/config.unsigned.json，未签字时只是“对照未签字阈值”；
  tools/gate1-evidence/sample 是合成样例（“合成，不是试点结论”），G1-2、G1-3、G1-4 与附加项据此一律写“无法自动判断（需人）”；
  真实试点核对见 tools/gate1-evidence/RUNBOOK.md（只读，本仓不执行）。
- 留出集的盲测结果只引用教研给出的报告，不读取留出集本身（CLAUDE.md 规则 14）。
运行 make check、make contract-test、make eval-persona（Gate 1 另跑 make gate1-test 与 make gate1-evidence），引用输出。
写入 docs/gates/gate-<n>-evidence.md。对每一条只写"满足 / 不满足 / 无法自动判断（需人）"，不下总体结论——Gate 的通过与否由人决定。
