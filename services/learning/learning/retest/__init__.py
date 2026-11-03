"""复测与验证题交付（KGE-05，specs/retest-delivery-kge05/spec.md）。

让 KGE-04 已排出的复测与验证任务真正被做到：列出到期任务（只读 platform.retest_task）、按任务的单元选一道没做过的题目族的题、
作答经现有 grade 写入路径落证据（purpose 由服务端按任务原因写入，映射只在 tasks.PURPOSE；签字前保守口径：衰减 retest，其余 practice），之后由 KGE-04 的同步重算按既有规则
关闭或改排任务。本模块不写任何表、不改掌握计算与复测调度的数值、不新增 retest_task.reason。
复用错题再练（learning/practice）的选题、票据、题面与作答组件；票据前缀 rt1 与再练的 pt1 分开（签名覆盖前缀，不能互换）。
- config：配置位（票据有效期、列表上限、验证题道数、Gate 1 的“8 周”复测点开关与天数，默认关，待教研签字）；
- tasks：任务 → 条目的纯函数（到期、用途、验证题是否做满、8 周复测点）；
- store、service、api：读取层、列表 / 出题 / 作答、服务目录内部接口（openapi/retest-internal.v0.yaml，只供 BFF）。
"""
