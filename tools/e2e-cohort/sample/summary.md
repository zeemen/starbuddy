# Gate 0 证据包（合成）

> **合成联调，不是内测结论。** 全部学生、家庭、作答均为固定种子生成的合成数据（仓库总则规则 8）；判分经 test 环境假供应商与各科 Rubric / 验证器得出，不代表任何真实学生或真实模型表现。
> 复测调度为接口桩（KGE-04 第二步前），证据包里所有复测都标“桩”。英语未纳入（适配器搁置）。

- 合成学生：20 名；三科（数学 aya、语文 wenli、道法 zhixing）都有事件的：20 名
- 证据事件：aya 264，wenli 140，zhixing 110
- 节点查询行：207；复测（桩）：32 次
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
| retest_via_stub | 通过 |
| isolation_rls | 通过 |
| rerun_zero_new_rows | 通过 |
| rerun_same_result | 通过 |
| db_clients_only_learning_and_admin | 通过 |

## 数据文件 SHA-256

- `students.csv` 9ab342410efe6767014f9144c1605de8f96f76a9b411a3f5c6a4f7ee327047ac
- `events.csv` d2824dbcb621b9ded006e3fc006a1080575797fd0ec3d6ffef077665e26df298
- `nodes.csv` a2239a10d626b22a40e0cea33b0d33903cd5eca89100ef1c8193d00793b6745c
- `retests.csv` 786912772649f744e7464412b72ebdeac74b39e43c9b7595e522e3171bd0975e
- `checks.json` f9d6017f6a1239b2bb491facc28c264fcc35b8961aa619aa33bf65abb65a1686

## 星图截图

- **星图 v0（3-H），掌握未写库，无点亮星；H5 合入后重截。** 页面状态只有 evidence_only / 中性底星（KGE-04 第二步前不写掌握），截图不是掌握结论。
- v0 截图只证明真实会话 + RLS 能打开该学生页（打不开未绑定学生的页）、星空等于在读科目；硬1 的同一学生模型主要由 events.csv、nodes.csv、checks.json 证明，截图只作辅证。
- `make gate0-evidence GATE0_STARMAP=1` 时生成 `starmap/`：合成学生经真实会话打开壳内星图页的截图（学生视角若干张、家长视角 1 张），`starmap/starmap.json` 记录 status（captured / failed）、页面路径、学生选取规则、视口、截取提交号、截图哈希与对账（每条规则在 v0 下的效力也写在里面）；对账不一致即 failed。
- 截图对应的页面版本与截取提交号见 `starmap/provenance.md`；与本证据包提交号不同时标“截图沿用”（只在星图页面改版时重截）。

运行元数据（提交号、契约与适配器版本、星图截图记录位置）见 `run_metadata.json`。
