---
name: gate-check
description: 汇总阶段 Gate 的证据包（Gate 0 / Gate 1），逐条核对通过条件。用法：/gate-check <0|1>
---
# 阶段门证据汇总

读取 docs/plan/开发计划-阶段0-1.html 中对应 Gate 的硬条件与软条件，逐条收集证据：
- Gate 0 硬条件：四科写入同一学生模型（查询 20 名合成测试学生的四科事件）；家长隔离与扣次幂等测试结果；点点跨模型一致性报告。
- 软条件：契约测试覆盖率（阿鸦 6/6、知行与文砺 ≥5/6、开口成章 ≥4/6）；人设家族级回归最近一次结果；发布闸门版本记录；单位经济首版。
运行 make check、make contract-test、make eval-persona，引用输出。
写入 docs/gates/gate-<n>-evidence.md。对每一条只写"满足 / 不满足 / 无法自动判断（需人）"，不下总体结论——Gate 的通过与否由人决定。
