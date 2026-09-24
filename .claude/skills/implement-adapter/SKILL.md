---
name: implement-adapter
description: 按已确认的 spec 实现一科适配器的一个方法并完成自检与审查。用法：/implement-adapter <subject> <method>
---
# 实现适配器方法

前置条件：specs/adapter-<subject>-<method>/spec.md 已存在且标注"已确认"。否则停下，先起草 spec 请人确认。

1. 在独立 git worktree 与分支 feat/adapter-<subject>-<method> 中工作。
2. 委派 adapter-implementer 子代理实现，并写契约测试与合成数据用例。
3. 运行 make check；失败则修复，直到通过。
4. 依次委派 contract-guardian 与 security-reviewer 审查；若触及人设话术，再委派 persona-evaluator。
5. 汇总为 PR 描述：做了什么、验收标准逐条如何满足、三份审查结论、测试摘要。任何审查为"需人确认"或改动命中 CLAUDE.md 的必停清单，PR 首行写 [NEEDS-HUMAN]。
6. 只开 PR，不合并。合并由人完成。
