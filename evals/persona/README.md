# evals/persona · 人设一致性评测

## 点点一致性用例 v0（冲刺 1-I，`specs/diandian-cases-v0-sprint1/`）

| 位置 | 内容 | 谁写 |
|---|---|---|
| `diandian/cases/*.yaml` | 120 条用例：学生侧合成输入 + 可判定的检查点，九类一类一个文件 | 工程起草，人设负责人 / 教研抽查 |
| `diandian/schema/case.schema.json` | 用例格式（合并文件级 defaults 之后） | 工程 |
| `diandian/lexicon/forbidden.yaml` | 禁语 A–E 与规则模式的机器可读转写，逐条注明出处；`reviewed: false` 直到人设负责人核对 | 工程转写，**人核对**（必停项） |
| `diandian/holdout.txt` | 留出集冻结名单（首次按类别分层、固定种子抽 30%）；修订 Prompt 时不看；正式评测前由教研负责人重新封存（规则 14，lessons 待人确认 #6） | `python -m scorer holdout --write` 首次生成 |
| `diandian/fixtures/selftest.yaml` | 评分器自检用的合成回复：中性“【测试】”口吻，带 `not_persona_corpus: true`，**不是人设语料、不是金标准**，只有评分器读 | 工程 |
| `diandian/gold/` | 期望答案与判定要点（本任务不建；hook 拦截写入） | **只能教研写** |
| `diandian/run.py` | 离线全流程：用例 → 模型路由 test 环境（假供应商）→ 评分；只验证流程，分数无意义 | 工程 |
| `scorer/` | 离线评分器：规则、结构字段判定；review 类只记待评审；报告不含回复原文 | 工程 |

命令（在 `evals/persona` 下，用 `services/learning/.venv/bin/python`）：
- `python -m scorer validate`：格式、条数、正负样例、编号、核心标记、留出集、只含合成数据、核心可自动判定比例 ≥ 80%；
- `python -m scorer selftest`：每类“应通过 / 应失败”的合成回复判得对，红线样例进红线清单；
- `python -m scorer score --responses X.jsonl --out DIR`：对一批回复评分（回复格式见 spec 设计要点 1）；
- `python diandian/run.py [--runs N] [--out DIR]`：离线全流程；回复只能写到仓库外（`--out` 指向仓库内会被拒绝），用完请删除。

`make eval-persona` 跑上面的校验、自检、测试与变异（不联网、不需要密钥），已并入 `make check`。
真实模型评测（2-I、3-I）等 ADR 04 / 08 签字后另做，不进 CI；真实回复不入库，只把不含原文的报告入库。
