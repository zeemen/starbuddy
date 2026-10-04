# Gate 0 证据包（合成）

> **合成联调，不是内测结论。** 全部学生、家庭、作答均为固定种子生成的合成数据（仓库总则规则 8）；判分经 test 环境假供应商与各科 Rubric / 验证器得出，不代表任何真实学生或真实模型表现。
> 复测由 KGE-04 第二步的真实调度驱动（platform.retest_task；联调用合成、未签字的数学掌握配置，知行与文砺不配置）。合成配置下到不了复测时如实为 0 次，原因见 checks.json 的 retest_via_retest_task。英语未纳入（适配器搁置）。

- 合成学生：20 名；三科（数学 aya、语文 wenli、道法 zhixing）都有事件的：20 名
- 证据事件：aya 232，wenli 140，zhixing 54
- 节点查询行：201；复测（retest_task 驱动）：0 次
- 断言：全部通过

| 断言 | 结果 |
|---|---|
| all_calls_succeeded | 通过 |
| adapters_have_no_db_or_vendor_config | 通过 |
| coexistence | 通过 |
| no_private_copies | 通过 |
| guardian_links_backed | 通过 |
| grade_envelope_provenance | 通过 |
| grade_rows_provenance | 通过 |
| retest_via_retest_task | 通过（0 次，未走到） |
| isolation_rls | 通过 |
| rerun_zero_new_rows | 通过 |
| rerun_same_result | 通过 |
| db_clients_only_learning_and_admin | 通过 |

## 数据文件 SHA-256

- `students.csv` 9ab342410efe6767014f9144c1605de8f96f76a9b411a3f5c6a4f7ee327047ac
- `events.csv` 5cf29b901add67fab4eb2d9d891ae96ec595c9e8babd7a4d3d79e1b72c555744
- `nodes.csv` 668290965515c1cb40a1aad4127ae5adce497bc94f01d6a048e197034b46afc6
- `retests.csv` f9283cf27316e9ea2c1e263f0d88eb8d7ff3d3a28b437d17bf67c8c3ce57a7f4
- `checks.json` 52bbbb9ce9757042dc1c494571e7f04ba53f02e1c91d089a5c6aaafaab434965

## 星图截图

- **星图 v0（3-H），掌握未写库，无点亮星；H5 合入后重截。** 页面状态只有 evidence_only / 中性底星（KGE-04 第二步前不写掌握），截图不是掌握结论。
- v0 截图只证明真实会话 + RLS 能打开该学生页（打不开未绑定学生的页）、星空等于在读科目；硬1 的同一学生模型主要由 events.csv、nodes.csv、checks.json 证明，截图只作辅证。
- `make gate0-evidence GATE0_STARMAP=1` 时生成 `starmap/`：合成学生经真实会话打开壳内星图页的截图（学生视角若干张、家长视角 1 张），`starmap/starmap.json` 记录 status（captured / failed）、页面路径、学生选取规则、视口、截取提交号、截图哈希与对账（每条规则在 v0 下的效力也写在里面）；对账不一致即 failed。
- 截图对应的页面版本与截取提交号见 `starmap/provenance.md`；与本证据包提交号不同时标“截图沿用”（只在星图页面改版时重截）。

运行元数据（提交号、契约与适配器版本、星图截图记录位置）见 `run_metadata.json`。
