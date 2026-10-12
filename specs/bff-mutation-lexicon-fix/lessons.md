# 修两条“碰巧被发现”的 BFF 变异 · 实现记录

## 交付
- `services/bff/tests/mutations.sh`：所有变异副本（含对照组）一律链接 `docs/`，检测只反映对应断言；月度计划组仍另链 `services/insight`。
- 周报：BFF 连接受 RLS `claim_front_read` 约束，撤回与推翻的判断本来就读不到，唯一能观察到的差别是“待验证”判断。集成测试新增一条待验证判断，断言周报只收成立或假设的判断。
- 报告受众：单测补三种 audience 不一致（含 null）均返回 null；集成测试补“只有 audience 字段对不上”，响应 200 + `report: null`，日志 `invalid_shape`。
- 结果：信任组 49、报告组 28、全量 319 个变异全部被发现；`make bff-test` 通过。

## 经验
- 变异副本与真实运行环境不一致（缺词表）时，fail closed 的路径会让任意变异都“被发现”，掩盖断言缺失。副本应与仓库内跑测试的环境一致。
- 有 RLS 兜底的过滤条件，测试要造 RLS 放行、而业务过滤应拦下的状态（这里是 to_verify），否则变异不可观察。
