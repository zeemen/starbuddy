# evals/daofa-grading · 道法采分点判分一致率

冲刺 4-K（`specs/kge-daofa-slice-sprint4/spec.md` 设计要点 6，待人确认 #7 默认口径）。评测即代码：只用 Python 3 标准库，不联网、不调用任何模型。
验收门槛“采分点判分与教师一致率 ≥ 90%”（逐点判定一致率）**只在教研金标到位后作为后续验收**，不是 `make check` 的通过条件：
脚本只在加 `--enforce` 时按门槛返回失败。

| 位置 | 内容 | 谁写 |
|---|---|---|
| `grading.py` | 逐点判定一致率（主指标）、逐题总分一致率、失分归因一致率与混淆表 | 工程 |
| `fixtures/` | **合成、非金标**的格式样例（回应 `SYN-R-*`，题目取合成内容包），报告标“流程样例” | 工程 |
| `transcribe.py` | 把适配器 `grade` 返回转写成上面的 CSV（W13 T34） | 工程 |
| `tests/` | 与手算一致的单测（手算写在注释里）；转写脚本的单测 | 工程 |
| `gold/` | 教师逐点标注（至少 50 题，道法教研提供）。**只由教研提交，只读**；本任务不创建 | **只能教研写** |

## 输入格式（教师标注与适配器判分同一格式）

UTF-8 CSV，开头以 `#` 起始的行是注释；表头固定：

```
response_id,item_id,point_id,score,attribution
```

- `response_id`：不透明的回应编号（**不是学生 ID**，不含姓名、学号等个人信息）；
- `item_id`、`point_id`：同内容包（`adapters/zhixing/content/pack.json`）与 `kge.scoring_point`；
- `score`：该采分点得分（0.5 的倍数）；`attribution`：失分归因（`shenti_cuo` 审题错 / `dingwei_cuo` 定位错 / `zhuanhua_cuo` 转化错 / `guifan_cuo` 规范错），没有失分留空；
- **只含 ID 与分数**：作答原文、评语一类的列一律拒绝（表头多一列即报错，规则 13）。
- 给 `--pack` 时核对题目与采分点存在、得分在允许分值里、满分的点不标失分归因。

## 口径

- 逐点判定一致率 = 两边得分相同的采分点 / 教师标注的采分点；适配器缺点算不一致，多出的点不计分（只计数）；
- 逐题总分一致率 = 总分相同的（回应, 题目）/ 教师标注的（回应, 题目）；适配器缺任何一个点算不一致；
- 失分归因混淆表：行 = 教师归因，列 = 适配器归因；没有失分记 `none`，适配器缺点记 `missing`；失分归因一致率 = 教师标了归因的点里归因相同的比例；
- 全部用分数精确计算：恰好 90% 算达标。
- 金标不在 `evals/daofa-grading/gold/` 下时，报告标“流程样例”（合成数据只证明计算流程，数字不代表判分质量）。报告只含汇总与 ID。

## 命令

```bash
cd evals/daofa-grading
python3 -m unittest discover -s tests            # make daofa-grading-eval-test（并入 make check）
python3 grading.py --gold fixtures/teacher.csv --adapter fixtures/adapter.csv --pack ../../adapters/zhixing/content/pack.json [--json] [--summary-only]
# 教研金标到位后的验收（不进 make check）：
python3 grading.py --gold gold/<文件>.csv --adapter <适配器判分>.csv --pack ../../adapters/zhixing/content/pack.json --enforce
```

退出码：0 算完（未加 `--enforce` 时未达标也是 0）；`--enforce` 且逐点一致率未达标为 1；输入错误为 2。
适配器判分 CSV 由适配器 `grade` 返回的逐维度结果转写（`dimension_id` = 采分点 `point_id`）：`python3 transcribe.py --in <grades.jsonl> --out <adapter.csv>`（每行 `{"response_id", "item_id", "grade"}`；错因按证据编号对到维度；未判定的点不写，评测按缺点计；只写 ID 与分数。W13 T34）。
