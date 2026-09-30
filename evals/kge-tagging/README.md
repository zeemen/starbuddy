# evals/kge-tagging · 打标一致性与准确率评测

KGE-02（`specs/kge-02-atoms-tagging/spec.md` 设计要点 5）。评测即代码：只用 Python 3 标准库，不联网、不需要密钥、不调用任何模型。
门槛来自 KGE V1.1 :745–753：两位教师主考点标注 kappa ≥ 0.7；打标主考点 Top-1 ≥ 90%（金标集，每次发版都测）；留出集无处可标率 < 2%。
**教研标注到位、真实模型接入之前，这些门槛不作为 `make check` 的通过条件**：脚本只在加 `--enforce` 时才按门槛返回失败。

| 位置 | 内容 | 谁写 |
|---|---|---|
| `kappa.py` | 两名标注者主考点 Cohen's kappa、节点集合平均 Jaccard、主考点分歧清单 | 工程 |
| `top1.py` | 打标结果对金标：Top-1 准确率、Top-3 召回、无处可标率、门槛判定（可顺带算 kappa） | 工程 |
| `run_holdout.sh` | 留出集盲测入口，只打印汇总 | 工程写；**教研负责人执行** |
| `common.py` | 读标注 CSV、打标报告、候选；门槛常量 | 工程 |
| `fixtures/` | 合成小样本（合成节点 ID `MATH.SYN.*`、合成题目 ID `SYN-G-*`），不是金标 | 工程 |
| `tests/` | 与手算结果核对的单测（手算过程写在注释里） | 工程 |
| `gold/` | 金标集（约 100 题）。**只由教研提交，只读**；构建方只用它算指标，不用来调规则或阈值（调参用单独的开发集） | **只能教研写** |
| 留出集 | **不在本仓库**。由教研负责人封存盲测（CLAUDE.md 规则 14），构建方不读取、不索要、不用它调参 | 教研负责人 |

`gold/` 目录本任务不创建（仓库 hook 拦截对 `evals/*/gold/` 的任何写入，工程也不应在里面放文件）；由教研首次提交时建立。

## 标注文件格式（金标、两人试标注、开发集通用）

UTF-8 CSV，第一行表头固定为：

```
item_id,annotator,main_node_id,node_ids
```

| 列 | 说明 |
|---|---|
| `item_id` | 题目 ID（同 `platform.item.item_id`：字母数字开头，字母数字与 `_ . -`，≤ 64 字符） |
| `annotator` | 标注者代号（如 `T1`、`T2`；定稿用 `final`）。不写真实姓名 |
| `main_node_id` | 主考点节点 ID（同 `kge.node_id`，如 `MATH.FUNC.FUN_VTX.K_FUN_0930`）；**留空 = 无处可标** |
| `node_ids` | 其他关联节点，分号 `;` 分隔，可留空；可以包含主考点（集合比较时自动并入） |

- 同一文件里同一标注者对同一题只能有一行；两人独立标注放同一文件（每题两行），定稿放 `annotator = final` 的行或单独文件。
- 建议文件名带地图版本，如 `gold/aya-fun-0.1.0.csv`；金标只对同一地图版本的打标结果有意义。
- 只含题目 ID 与节点 ID：题干、答案、解析不写进来（迁移 0011 的约定），也不含任何学生数据。

## 打标结果与候选格式

- 打标报告：`tagging-report.json`，格式以打标器的类型为准（`tools/kge-import/src/items/report-types.ts`，`format = kge-tagging-report/1`，
  `map_version` 为 `{id, version, …}`）；也接受没有 `format` 的简化格式（`map_version` 为字符串，夹具用它）。
  用到的字段：每题 `item_id`、`main_node_id|null`、`node_ids`，可选 `candidates: [{node_id, confidence, role?, layer?}]`（只取 `role` 为 `main` 或没有 `role` 的）。
  未导入主题的题（`status = out_of_scope`）主考点为空，在金标里出现时按“无处可标”计。
- 候选文件（`--candidates`）：`kge.tag_candidate`（迁移 0024）导出的 CSV 或 JSON 数组，至少有 `item_id,node_id,confidence`，可带 `role,layer,grade,status` 等；只取 `role = main`。
- Top-3 候选来源优先级：`--candidates` ＞ 报告里每题的 `candidates` ＞ 退化为 `[主考点] + node_ids`（输出里会注明）。
  排序：同一节点取最高置信度，置信度降序，相同按节点 ID 码点升序。

## 口径

- kappa：类别是主考点节点，“无处可标”也算一个类别；p_e = 1（两人全部标同一类别，必然完全一致）时记 1；只算两人都标了的题。
- Jaccard：每题 |A ∩ B| / |A ∪ B|，集合 = {主考点} ∪ node_ids，两人都为空记 1，取平均（不设门槛，供参考）。
- Top-1 / Top-3 的分母是金标有主考点的题；金标里有、打标报告里没有的题算错；报告多出的题不计分。
- 无处可标率 = 预测没有主考点（含缺预测）的题 / 金标全部题数。
- 全部用分数精确计算：kappa 恰好 0.7、Top-1 恰好 90% 算达标；无处可标率恰好 2% 不达标（严格小于）。

## 命令

在 `evals/kge-tagging` 下：

```bash
python3 -m unittest discover -s tests            # 单测（合成夹具，约 1 秒）
python3 kappa.py --annotations fixtures/annotations.csv [--annotators T1,T2] [--disagreements 分歧.csv] [--json] [--enforce]
python3 top1.py  --gold fixtures/gold.csv --tagging fixtures/tagging-report.json \
                 [--candidates fixtures/candidates.csv] [--annotations fixtures/annotations.csv] [--json] [--summary-only] [--enforce]
```

退出码：0 算完（未加 `--enforce` 时未达标也是 0）；加 `--enforce` 且有门槛未通过为 1；输入错误为 2。
分歧清单写到 `--disagreements` 指定的位置（不要写进 `gold/`）。

给 Makefile 的入口（不加 `--enforce`，并入 `make check` 只证明计算正确）：

```make
kge-tagging-eval-test:
	@cd evals/kge-tagging && PYTHONDONTWRITEBYTECODE=1 $(PYTHON) -m unittest discover -s tests
```

教研标注到位后的验收（不进 `make check`）：`python3 top1.py --gold gold/<文件>.csv --tagging <打标报告> --annotations gold/<两人标注>.csv --enforce`。

## 学科参数化（冲刺 4-K：道法）

`kappa.py`、`top1.py`、`run_holdout.sh` 都接受 `--subject aya|zhixing|wenli`：给了学科，标注、金标、打标报告、候选里的每个节点 ID 都必须是
该学科的前缀（`MATH.` / `MORAL.` / `CHN.`，与 `kge.subject_prefix` 一致），否则按输入错误退出 2；JSON 汇总多一个 `subject` 字段。
不给时不核对（KGE-02 的数学用法与输出不变，`tests/test_daofa.py` 核对数学夹具加不加 `--subject aya` 结果相同）。
道法合成夹具：`fixtures/daofa-annotations.csv`、`daofa-gold.csv`、`daofa-tagging-report.json`（合成，不是金标；手算见 `tests/test_daofa.py`）。
道法试标注（约 100 题，kappa ≥ 0.7）由教研完成后：`python3 kappa.py --subject zhixing --annotations gold/<文件>.csv --enforce`。

## 留出集（规则 14）

教研负责人在封存环境里执行，构建方不接触：

```bash
bash evals/kge-tagging/run_holdout.sh --gold <留出集金标.csv> --tagging <对留出集的打标报告.json> [--candidates <候选>] [--enforce]
```

- 脚本不含任何留出集路径，输入全部由执行人传入；输入在仓库目录内时拒绝执行；
- 只打印汇总指标（Top-1、Top-3 召回、无处可标率、门槛判定），不打印逐题结果、不写任何文件；输入格式错误时也只报行号或字段名；
- 对留出集的打标报告由教研负责人用同一版本的打标器在封存环境里生成。
