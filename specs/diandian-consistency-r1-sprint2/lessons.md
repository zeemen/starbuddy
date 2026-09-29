# 冲刺 2-I 点点一致性首轮 · lessons

## 实现要点（子代理实现，主会话合并）
- `evals/persona/diandian/compare.py`：对照运行器。模型路由不支持指定供应商，所以每个候选临时拼一张只含它自己的路由表（T1、L0），再用路由自己的
  `config.parse` 校验；供应商必须在当前环境登记、模型必须在该供应商已登记的路由目标里；境外供应商一律拒绝；staging / pilot / prod 还要求白名单批准。
  test 环境只用进程内假供应商；其他环境走 `ModelRouter.from_config`，凭据来自执行环境变量，运行器不持有 Key。
- 人设 Prompt 从 `docs/personas/prompts/diandian-v0.md` 读，不存在时 test 以外的环境一律拒绝；`--out` 不能在仓库内；报告目录只能是 `evals/persona/reports/` 的子目录；
  默认不跑留出集（86 条非留出、34 条留出）。
- `evals/persona/scorer/compare_report.py`：每候选的总 / 核心 / 按九类通过率、红线单列、N 次间不稳定比例；两两差值与 95% 区间（按用例配对自助法 1000 次、固定种子）；
  按类差值表；只在某一家失败的用例（只列 ID 与检查点）；3-I 口径只报告核心通过率最大差，结论字段固定为空。Markdown 与 JSON，确定性输出。
- 测试 22 条、变异新增 10 条（共 34 条全部被发现）；流程样例报告 `evals/persona/reports/r1-sample-fake/`，头部标注“流程样例，不是模型评测结果”。

## 待人确认（实现中新增）
1. **回复格式**：真实模型的纯文本回复里没有转介、交接等结构字段，默认 `--reply-format text` 下这些检查点会全部失败（它们在核心用例里）。
   另有 `--reply-format envelope`（按 `{"text","fields"}` 解析，test 环境默认用它）。真实输出格式要和人设 Prompt v0 一起定（3-I 后处理）。
2. **用例上下文的注入方式**：用例的 context（合成）以 JSON 放进第二条 system 消息，需要人设负责人确认。
3. **人设 Prompt 版本号**：占位 `prompt:diandian@0`，报告另记文件 sha256；Prompt 文件格式与版本号怎么取待定。
4. **留出集**：没有提供跑留出集的开关，由教研负责人在封存环境自行运行（规则 14）。
5. **dev 环境**按“非 test”处理：需要人设 Prompt 文件，不接受假供应商配置。
6. spec 待人确认 #3（开发环境境外对照）未单独答复，按默认不允许。
7. 真实首轮的前置：ADR 04 / 08 候选签字、供应商登记进 `docs/compliance/vendors.md` 与路由配置、人设 Prompt v0 审定、执行人在有凭据的环境运行，只提交不含原文的报告。
