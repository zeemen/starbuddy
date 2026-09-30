# 合成样例（Gate 0 证据包格式）

**合成联调，不是内测结论。** 由 `make gate0-evidence GATE0_OUT=tools/e2e-cohort/sample` 生成，只含固定种子生成的合成学生 ID 与计数。
`services/learning/tests/test_e2e_cohort.py` 把每次联调渲染出的证据包与这里逐字节比对（`run_metadata.json` 忽略提交号与工作区状态），
代码或内容包变更导致不一致时重新生成并在 PR 里说明差异。真实执行的证据包放执行人目录，不进仓库（spec 待人确认 #5）。
