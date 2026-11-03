"""错题再练闭环（specs/practice-loop-stage1/spec.md）。

纯函数（PR 1，不连库、不发 HTTP）：config（配置位与合成占位）、queue（错题条目读时推导、闭环、轮次）、
select（候选过滤、同族兜底、核对适配器选题）、ticket（再练票据签发与验签）。
读取层与内部接口（PR 2）：store、presenter、service、answer、api。
本模块不改掌握计算、不写判断账本、不新增表：错题与再练状态都由作答、判分、证据与题目打标读时算出（规则 2、11）。
"""
