# tools/e2e-cohort · 跨科联调与 Gate 0 证据包

冲刺 5-C+D（`specs/e2e-integration-sprint5/spec.md`）。20 名**合成**学生在同一个学习服务上走三科（数学 aya、语文 wenli、道法 zhixing）
诊断、批改、复测，断言“写入同一学生模型”，并生成 Gate 0 证据包。**合成联调，不是内测结论。**

## 用法

```bash
make e2e-cohort                                   # 联调 + 断言 + 反例 + 证据包与样例一致（约 1 分钟；同一测试也随 make learning-test 运行）
make gate0-evidence GATE0_OUT=<目录>              # 在新的一次性 Postgres 上跑一轮并把证据包写到 <目录>（约 30 秒）
make gate0-evidence GATE0_OUT=<目录> GATE0_STARMAP=1   # 同上并尝试星图截图（4-H 壳页面合入前会跳过并记录）
make gate0-evidence GATE0_OUT=tools/e2e-cohort/sample  # 代码或内容变更后重生仓库里的合成样例
```

依赖：`services/learning/.venv`（同 `learning-test`）、本机 Postgres 16 服务端、`node`，以及三个适配器的 `npm ci`。
缺依赖时 `e2e-cohort` 跳过（`CONTRACT_STRICT=1` 时失败）。

## 组成（`e2e_cohort/`）

| 文件 | 作用 |
|---|---|
| `cohort.py` | 合成队列生成器：固定种子 `20260930`，20 名；年级七八九；选科三科（部分另选英语，适配器搁置，断言英语零事件）；作答模式全对 / 系统性缺口 / 刷同题 / 提示依赖 / 中断各 4 名；4 户两个孩子 |
| `scripted.py` | test 环境模型路由的假供应商：认出已登记的合成作答，给出结构化判定（道法按采分点关键词族，语文按锚点片段）；判分仍由适配器后处理与 Rubric 决定 |
| `stack.py` | 一次性 Postgres 上的学习服务（进程内）+ 三科适配器（真实 HTTP 子进程，三科都按契约 1.0.0 启动）+ 内部模型路由接口；适配器进程拿不到数据库或供应商配置；学习服务到适配器的 HTTP 经 `RecordingTransport` 原样转发，grade 响应只留在内存里供断言；内容登记 SQL 以 `SET ROLE starbuddy_learning` 执行；开启临时实例的连接日志，核对连库角色 |
| `replay.py` | 建档（平台账号侧准备）→ `/internal/v1/diagnose` → `/internal/v1/grade` → 复测。复测调度是接口桩 `StubRetestScheduler`（KGE-04 第二步前），执行经同一 grade 接口（`ingest.purpose = retest`），每条标 `stub`；整轮重跑用同一幂等键 |
| `checks.py` | 断言（只读事务）：三科事件并存不覆盖；私有副本扫描（个人层 = `platform.*` 与 `insight.*` 白名单；个人层以外无学生键列、列值里无本批学生相关 ID（学生、账号、家庭、家长、证据、作答）；个人层无学科名开头的表；带 `student_id` 的表都在个人表清单里，清单未建时标“待 0035”）；家长绑定有来历；grade 五个溯源字段（契约边界 + 落库）；重跑零新增；RLS 隔离（按个人表清单逐表比对，学生、家长、未绑定家长、两种未登录）；节点查询与事件计数 |
| `verify.py` | 真实内测只读核对：库名比对、拒绝超级用户与非只读会话、拒绝写进仓库、名单不全返回 1、只输出汇总 |
| `runner.py` | 串起一整轮，另加：全部调用成功、适配器无数据库驱动与连接信息、联调期间连库的只有学习服务登录角色与管理员；返回只含 ID 与计数的 Report |
| `evidence.py` | 证据包渲染（纯函数、确定性）与星图截图接口 `capture_starmap` |
| `__main__.py` | 命令行：`cohort` / `evidence` / `verify`（真实内测只读核对，见 `RUNBOOK.md`） |

## 证据包

`summary.md`、`students.csv`、`events.csv`（事件计数 × 科目 × 事件类型 = 来源方法/作答情境；复测带“（桩）”后缀）、`nodes.csv`（节点 ID、证据数、观察计数、状态）、
`retests.csv`（全部标“桩”）、`checks.json`、`run_metadata.json`（提交号、契约版本、适配器与内容包版本、队列种子与摘要、星图截图状态）。
除 `run_metadata.json` 的提交号与工作区状态外，同一代码逐字节相同；`test_e2e_cohort.py` 与 `sample/` 逐字节比对。
节点状态在 KGE-04 第二步（掌握写库）前一律是 `evidence_only`，不是掌握结论。

## 边界

- 只用合成数据（规则 8），只连本机临时实例（规则 9）；学习数据只经契约接口写入（规则 1），不改契约、迁移、判分与掌握逻辑；
- 真实 20 名内部测试学生的联调不在本仓执行，见 `RUNBOOK.md`；
- 已知缺口与后续见 `specs/e2e-integration-sprint5/lessons.md`。
