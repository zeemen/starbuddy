# evals/kge-compile · 对照评测：页面优先 vs 切块检索

KGE-03（`specs/kge-03-compile-layer/spec.md` 设计要点 7，待人确认 #8）。评测即代码：不联网、不需要密钥、不调用任何模型。
KGE V1.1 第九章：“页面优先 vs 切块检索对照：金标问答正确率、同题讲法一致性、单次上下文成本”，口径是“正确率不低于切块路径，
一致性与成本明显更优”，M1 前一次、此后每季度。**脚本只出数字，M1 结论由人定**；未达标时以切块检索为主路径进入封测，编译层继续迭代。

| 位置 | 内容 | 谁写 |
|---|---|---|
| `compare_retrieval.py` | 两条路径各跑一遍，输出 Markdown + JSON 报告 | 工程 |
| `fixtures/` | **合成**夹具：`gold-synthetic.yaml`（12 题合成金标）、`map-synthetic.yaml`（合成地图）、`page-snapshot/`（合成页面快照，由 `make_fixture.py` 生成） | 工程 |
| `reports/sample-synthetic/` | 合成夹具上的样例报告，报告头标“流程样例，不是评测结论” | 工程（测试核对它是最新的） |
| `tests/` | 指标与手算一致、确定性、报告不含原文、样例与夹具是最新的 | 工程 |
| `gold/` | 真实金标问答。**只由教研提交，只读**（仓库 hook 拦截对 `evals/*/gold/` 的写入，本任务不创建） | **只能教研写** |

## 两条路径
- **页面优先**（`knowledge.retrieval.pipeline.PageFirstRetriever`）：定位节点（规则 + 全文）→ 读已发布页面（定义、课标要求、讲解要点、易错、高频设问）→
  节点没有页面时查原子（节点、课标条目）→ 没定位到、低置信、没有页面或页面对查询词覆盖率低于 50% 时兜底（BM25 取 3 块）。
- **切块检索**（`ChunkRetriever`）：同一套公共层切块（已发布页面的段落 + 已发布原子）上的 BM25，取前 5 块。

两条路径用同一份语料、同一个分词器，结果确定。

## 第三条路径：WeKnora（技术验证计划 V12，可选）
`--weknora` 时多跑一条路径（`weknora_path.py`）：把同一份公共层切块逐块导入 **WeKnora 测试知识库**（标题 `sbeval-<run_id>-<切块键>`，
打本次运行专用的标签），只按这个标签检索，取前 `--weknora-top-k`（缺省 5）条，再按标题里的切块键换回原来源，所以“同题讲法一致性”
与另两条路径同口径。结束时删除导入的文档与标签（`--weknora-keep` 保留）。
- 地址与密钥从环境变量读：`WEKNORA_TARGET=test`、`WEKNORA_BASE_URL`、`WEKNORA_API_KEY`、`WEKNORA_KB_ID`（同 `tools/weknora-probe/README.md`），
  不是测试目标就拒绝运行；报告不含地址、密钥与原文。
- 报告多一列 WeKnora，多一行“WeKnora − 切块”，method 里记 top_k、导入切块数与认不出来源的结果条数；不加 `--weknora` 时报告与原来逐字节相同。
- 数字只对那次的 WeKnora 配置（嵌入、重排模型）成立；第一阶段用自部署开源模型（验证计划已定事项第 2 条），百炼获批后重跑。结论由人定。
- 测试（`tests/test_weknora_path.py`）对模拟 WeKnora 只核对流程与报告，数字没有意义。

## 指标
| 指标 | 算法 |
|---|---|
| 正确率 | 主问题第一次取回的上下文是否覆盖答案要点。判定规则可插拔：`all_points`（缺省，全部要点出现）、`majority`（过半）；`register_judge()` 可接别的规则（例如教研的同义词表）。要点与上下文先 NFKC、小写、去空白与标点，再按子串匹配 |
| 要点覆盖率 | Σ 覆盖要点数 / Σ 要点数 |
| 同题讲法一致性 | 主问题取回 `--runs` 次（缺省 2）+ 每条同义改写各 1 次，两两算来源集合的 Jaccard 取平均（来源 = 页面 ID + 版本 + 段落，或原子键）；各题再平均 |
| 单次上下文成本 | 主问题上下文的字符数与 token 估算（每个汉字 1、每串连续字母数字 1、其余每个非空白符号 1）；报平均与最大 |
| 定位命中率、兜底次数 | 辅助诊断：上下文包的节点是否等于金标节点；页面优先路径按原因计兜底次数 |

报告只含汇总与题目 ID：问题、改写、答案要点、上下文原文都不进报告；不含时间与本机路径；同样输入逐字节相同。

## 金标格式（`format: kge-gold-qa/1`）
```yaml
format: kge-gold-qa/1
synthetic: false            # 真实金标写 false；合成写 true（报告头会标“流程样例”）
map_version: 0.9.0          # 可选，便于核对
questions:
  - id: MATH-QA-001         # 题目 ID：字母数字开头，字母数字与 _ . -，≤ 64 字符，唯一
    node_id: MATH.FUNC....  # 金标节点（可省；给了才算定位命中率）
    question: ……            # 问题原文（不含任何学生信息）
    paraphrases: [……]       # 同义改写（可省；一致性用）
    answer_points: [……, ……] # 答案要点（短语，按子串匹配）
```
默认 ≥ 50 题、按节点分层（spec 待人确认 #8）；少于 50 题时报告会写明“不足以支撑 M1 结论”。

## 执行手册（真实 M1）
1. **金标**：教研把金标问答提交到 `evals/kge-compile/gold/<学科>-<地图版本>.yaml`（只由教研写；或放仓库外，用 `--gold` 传路径）。
   金标不得含学生作答、姓名、学号等任何个人信息（规则 8、13）。它不是留出集：留出集由教研负责人封存，与这里无关（规则 14）。
2. **语料**：
   - 地图快照：已发布地图版本的公共层快照（`knowledge.atoms` 格式；库上可用 `DbAtomSource` 导出）；
   - 页面快照：`python -m knowledge.compiler.snapshot --repo <页面仓库> --commit <主分支提交> --out <目录>` 导出的只读快照。
3. **运行**（仓库根目录，用知识服务的 venv）：
   ```bash
   services/knowledge/.venv/bin/python evals/kge-compile/compare_retrieval.py \
     --gold evals/kge-compile/gold/<金标>.yaml --map-snapshot <地图快照> --page-snapshot <页面快照目录> \
     --out <报告目录> [--judge all_points] [--runs 2]
   ```
   退出码 0 生成成功；2 参数或输入错误（消息只含字段名与题号）。
4. **结论由人定**：评测负责人对照 KGE 第九章口径判断是否达标，写进 M1 验收记录；未达标则以切块检索为主路径进入封测
   （编排层按 `ChunkRetriever` 取上下文），编译层继续迭代，下个季度重测。
5. 分词器、停用词、检索参数的任何改动都会改变结果：改动后升 `TOKENIZER_VERSION` / `RETRIEVAL_VERSION`，重跑并更新样例报告。

更新合成样例报告（夹具或检索改动后，测试会提示过期）：
```bash
cd evals/kge-compile/fixtures && ../../../services/knowledge/.venv/bin/python make_fixture.py && cd ../../..
services/knowledge/.venv/bin/python evals/kge-compile/compare_retrieval.py --gold evals/kge-compile/fixtures/gold-synthetic.yaml \
  --map-snapshot evals/kge-compile/fixtures/map-synthetic.yaml --page-snapshot evals/kge-compile/fixtures/page-snapshot \
  --out evals/kge-compile/reports/sample-synthetic
```

## 测试
```bash
make kge-compile-eval-test            # 缺依赖时非严格跳过、严格（CONTRACT_STRICT=1）失败
make knowledge-mutation               # 变异里含本脚本的判定、一致性、token 估算、报告不带原文等 6 条
```
