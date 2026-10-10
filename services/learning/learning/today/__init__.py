"""今日计划跨科编排 · 第一步（PLT-11，specs/today-plan-orchestration-plt11/spec.md）：读时汇总，不落库。

把三个已有来源汇成一张跨科有序列表：各科今日计划（learning/plan.py，调适配器 /plan）、错题再练列表（learning/practice）的 due 条目、
复测与验证题列表（learning/retest）的 due 条目。全部在进程内复用各模块的读取函数，不走 HTTP。
- config：配置位（缺省时间预算、来源优先级、再练与复测条目的预计分钟；全部待人签字，合成占位只允许 test / dev）；
- merge：纯函数（去重、排序、按预算截断），不连库；
- service、api：取来源（任一来源未启用或失败只跳过它，标 source_unavailable）与服务目录内部接口
  （openapi/today-internal.v0.yaml，只供 BFF；不改 packages/contracts）。
只读：不写任何表、不计量、不调模型；对 BFF 只回学科、类别、节点、预计分钟、来源、可去做的条目引用与计划原有的 rationale_code，
不回掌握、置信度、证据 ID（规则 3、11）。
"""
