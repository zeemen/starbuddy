# 讲评课教研验收清单（`specs/lesson-acceptance-checklist`）

借鉴 OpenMAIC `eval/zone-of-proximal-development` 的人工验收清单：由测试者构造合成输入、按清单逐条操作，每条写**实际观察到的结果**，
不按关键词计分，不用模型判。本目录只管“清单写得全不全、记录填得全不全”，不判断讲评课好不好。

| 文件 | 说明 |
|---|---|
| `checklist.yaml` | 清单：A 证据情境、B 支架、C 提交前不露答案、D 讲解内容、E 路线与记录、F 边界，共 27 条（必过 13 条）。**草稿，待教研负责人签字**；签字后改条目归教研 |
| `check.py` | 完整性检查：清单格式、记录覆盖每一条、`fail` / `blocked` 有复现、证据情境与期望不同不能记 `pass`、合成数据声明、疑似真实数据（手机号、身份证号、邮箱、学校全名）、附件只写相对路径；**结论由脚本按规则算出** |
| `examples/sample-record.yaml` | 合成示例记录，只用来测检查器；清单一改要更新 `checklist_sha256` |
| `tests/`、`mutations.py` | 检查器单测与变异测试 |

## 结论怎么算
- 清单未签字 → **试跑**，不能作验收结论；
- 已签字、必过项全部 `pass` → **通过**；任何必过项 `fail`、`blocked` 或 `n_a` → **不通过**。观察项不影响结论。

## 怎么用
```bash
make lesson-acceptance-check PYTHON=.venv-contracts/bin/python          # 校验清单与合成示例（进 make check）
cd evals/lesson-acceptance && python check.py <记录.yaml>                # 校验一份验收记录并打印结论
```
- 真实验收记录放教研台账，**不进本仓库**；测试输入只用测试者构造的合成数据（规则 8），不用留出集（规则 14），只在 staging 或本地跑（规则 9）。
- 什么时候跑：讲评课首次上线前全清单一遍；之后每次改人设 Prompt、提示阶梯或讲解生成，跑 A–D 节。
- 能自动检查的（DSL 与台词稿校验、引语逐字校验、禁用推断、人设回归）清单只写“跑过、通过”并引用结果，不重复做。
