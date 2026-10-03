# 合成样例（Gate 0 证据包格式）

**合成联调，不是内测结论。** 由 `make gate0-evidence GATE0_OUT=tools/e2e-cohort/sample GATE0_STARMAP=1` 生成，只含固定种子生成的合成学生 ID 与计数。
`services/learning/tests/test_e2e_cohort.py` 把每次联调渲染出的证据包与这里逐字节比对（`run_metadata.json` 忽略提交号与工作区状态），
代码或内容包变更导致不一致时重新生成并在 PR 里说明差异。真实执行的证据包放执行人目录，不进仓库（spec 待人确认 #5）。

`starmap/`：星图截图（**星图 v0（3-H），掌握未写库，无点亮星；H5 合入后重截**）。学生视角 3 张（S01 全对、S02 系统性缺口、S03 刷同题）、
家长视角 1 张（S01 的家长），JPEG 质量 60、视口 1024×768 整页，每张约 110 KB。`starmap.json` 记录页面路径、选取规则、视口、截取提交号、
星数统计、对账结果（每条规则在 v0 下的效力也写在里面）和截图的文件 / 像素 SHA-256（`volatile`，不要求逐字节一致，不参与比对）；
`provenance.md` 写截图对应的页面版本与截取提交号，与本证据包提交号不同时标“截图沿用”。

**v0 截图证明什么**：只证明真实会话 + RLS 能打开该学生页（同一会话打开另一户学生的星图页与 BFF 接口都是 404）、星空等于库里在读科目。
v0 页面画的是各科全部已登记能力点、全部是中性底星，页面上没有只属于某名学生的星，三名学生的学生视角截图逐字节相同；
“证据节点 ⊆ 页面节点”“带状态的星 ⊆ 证据节点”在 v0 下与学生无关或空真。Gate 0 硬1“同一学生模型”主要由 `events.csv`、`nodes.csv`、
`checks.json` 证明，截图只作辅证。

`starmap-v1/`：H5 星图 v1 的截图（**未启用**，spec `specs/star-map-v1-h5` 待人确认 #7）。不由 `make gate0-evidence` 生成，也不参与样例比对；
由 `make gate0-starmap-v1` 在 test 环境、开关打开、合成数据（`db/seeds/dev/star-map-v1-synthetic.sql`）下截学生端与家长端各 1 张（PNG），
`starmap-v1.json` 记文件与像素 SHA-256，`provenance.md` 写来历。它不替换上面的 v0 截图。

**何时重截**：只在星图页面改版时（如 H5 星图 v1、掌握写库后）用 `make gate0-evidence GATE0_OUT=tools/e2e-cohort/sample GATE0_STARMAP=1` 重截；
其余变更用不带 `GATE0_STARMAP=1` 的命令重生样例，`starmap/` 保留不删，`provenance.md` 会标“截图沿用”。
