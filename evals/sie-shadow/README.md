# evals/sie-shadow · 影子评审比对：首份画像报告 vs 教师判断

SIE-02b（`specs/sie-02b-claims-first-report/spec.md` 设计要点 6、待人确认 #7）。评测即代码：不联网、不连库、不调模型。
**脚本只出数字，验收结论由人定**；真实比对在 KGE-04 第二步、模板审核与教师标注到位后进行（spec 验收的“后续验收”）。

| 位置 | 内容 | 谁写 |
|---|---|---|
| `compare_shadow.py` | 逐“学生 × 节点 × 认知层次”比门槛状态，输出 Markdown + JSON（一致率、覆盖率、分歧清单） | 工程 |
| `fixtures/` | **合成、非金标**样例：`report-synthetic.json`（报告判断）、`gold-synthetic.yaml`（教师判断） | 工程 |
| `reports/sample-synthetic/` | 合成样例上的报告，报告头标“流程样例，不是评测结论”（测试核对它是最新的） | 工程 |
| `tests/` | 与手算一致、确定性、分歧三类、输入校验、不写 `gold/`、样例是最新的 | 工程 |
| `gold/` | 真实教师判断。**只由教研提交，只读**（本任务不创建；脚本只读它，输出目录不能在 `gold/` 下） | **只能教研写** |

## 口径
- 可比项 = 报告与教师都判断了的“学生 × 节点 × 层次”；**一致率 = 门槛状态相同的可比项 ÷ 可比项**；
- 覆盖率 = 可比项 ÷ 报告判断项（每位测试学生由一名数学教师独立判断首份报告覆盖的节点，#7）；
- 分歧：`state_mismatch`（两边状态不同）、`not_judged`（报告有、教师没判）、`not_in_report`（教师判了、报告没有）。
- 比的是门槛状态（`unseen … retain`，“知识掌握”判断的 `value.gate`），不是判断的“假设 / 成立”——教师判断不了后者（lessons 待人确认）。
- 报告只含编码（学生 ID、节点 ID、层次、状态）与评审教师人数，不含姓名、原文、时间与本机路径；同样输入逐字节相同。

## 输入格式
```json
{"format": "sie-shadow-report/0.1", "synthetic": false,
 "students": [{"student_ref": "<平台学生 UUID>", "judgments": [{"node_id": "MATH....", "level": "apply", "state": "stable"}]}]}
```
报告一侧由洞察服务导出：`insight.report.shadow_judgments(首份报告, 报告用的判断)`。教师一侧（YAML）：
```yaml
format: sie-shadow-gold/0.1
synthetic: false
students:
  - student_ref: <平台学生 UUID>
    reviewer: <评审人编码，不写姓名>
    judgments:
      - {node_id: MATH...., level: apply, state: stable}
```

```bash
../../services/insight/.venv/bin/python compare_shadow.py --report fixtures/report-synthetic.json \
  --gold fixtures/gold-synthetic.yaml --out reports/sample-synthetic
# 真实比对：--gold 缺省是 gold/shadow-review.yaml（只读）；退出码 0 生成成功，2 参数或输入错误（金标不存在也是 2，不当作通过）
```
测试随 `make insight-test` 运行（用洞察服务的 venv）。
