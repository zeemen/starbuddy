# 执行手册：真实 20 名内部测试学生的跨科联调核对（只读）

对应 `specs/e2e-integration-sprint5/spec.md` 设计要点 4、待人确认 #1。**本仓 CI 不执行本手册**；合成联调见 `README.md`。
Gate 0 以哪一份（合成证据包或本手册的汇总）作为“四科写入同一学生模型”的证据，由人判定。

## 0. 前置条件（缺一不可，任何一项不满足就停止）

| # | 条件 | 负责人 | 记录 |
|---|---|---|---|
| 1 | 20 名内部测试学生的监护人同意已签署，用途包含“联调核对”（J 流同意记录，`platform.consent_record` 有对应条目） | 合规 / 产品 | 授权记录编号（下文 `AUTH_REF`） |
| 2 | 试点环境在境内、已完成部署评审；本手册只连试点库，**不连生产**（规则 9） | 平台负责人 | 环境名 |
| 3 | 学生在试点环境里正常使用三科（经 BFF → 学习服务 → 适配器），不做任何脚本化写入 | 教研 | — |
| 4 | 已开通一个**只读核对登录**（见第 1 节），由 DBA 发放、用完即停 | DBA | 登录名、有效期 |
| 5 | 执行机是受控设备；输出目录不在任何同步盘或仓库里 | 执行人 | 目录 |

## 1. 只读核对登录（必停项：RLS 与权限策略变更，需人确认后由 DBA 执行）

断言需要：
- **不受 RLS 限制地读个人层**：并存、判分溯源、隔离的“应见集合”都要按学生读 `platform.*` 与 `insight.*` 个人表。
  个人层表都是 `FORCE ROW LEVEL SECURITY`，普通只读角色读到的是 0 行，断言会“全部通过”却什么也没核对。
  因此核对角色**必须**二选一：`BYPASSRLS`，或只经一组 `SECURITY DEFINER` 只读视图 / 函数读取（按名单过滤、只返回计数）。
  两种都是 RLS 与权限策略变更，**列为必停项**，由人决定用哪一种并另起 PR（带测试），本任务不写迁移；
- 读 `kge.*` / `policy.*` 公共层与任何其他 schema（私有副本扫描）、读 `platform.personal_table_registry`（现只授予 authz、eraser）；
- 能 `SET ROLE starbuddy_bff` 以 BFF 口径复现 RLS 隔离（切换后 RLS 按 `starbuddy_bff` 生效，与核对角色是否 BYPASSRLS 无关）。

建议形态（合成库上的同形角色见 `test_e2e_cohort.py` 的 `audit_dsn` 夹具）：

- 登录角色 `e2e_audit_<日期>`：`LOGIN NOINHERIT`（`BYPASSRLS` 或改用定义者视图），**不是超级用户**，
  `default_transaction_read_only = on`，`CONNECTION LIMIT 1`，`VALID UNTIL` 当天；
- 成员：`starbuddy_bff`（仅为 `SET ROLE` 复现隔离）；另授只含 `SELECT` 的读权限；
- 不授予任何 `INSERT / UPDATE / DELETE`、不授予写函数的 `EXECUTE`；核对结束立即 `DROP ROLE`。

`verify` 自带护栏（任一不满足即拒绝、返回 2、不写输出）：连上的库名必须等于 `--expect-db`；登录角色是超级用户则拒绝；
会话不是只读（`transaction_read_only <> on`）则拒绝；`--out` 解析后落在仓库目录之内则拒绝。

## 2. 准备学生名单（只有 ID）

- 由试点运营从后台导出 20 个 `student_id`（UUID），每行一个，存为 `students.txt`；**不含姓名、手机号、学校**；
- 名单文件放执行机本地受控目录，核对完成后删除。

## 3. 执行（只读连接跑同一套断言）

```bash
cd services/learning
export PILOT_AUDIT_DSN='postgresql://…'      # 连接串只放环境变量，不写命令行、不进仓库、不进日志
PYTHONPATH=../../tools/e2e-cohort .venv/bin/python -m e2e_cohort verify \
  --dsn-env PILOT_AUDIT_DSN --students /受控目录/students.txt \
  --authorization-ref AUTH_REF --expect-db <试点库名> --out /受控目录/gate0-pilot
unset PILOT_AUDIT_DSN
```

`verify` 以 `default_transaction_read_only = on` 连接，每条断言在 `READ ONLY` 事务里执行，跑与合成联调同一套函数（`e2e_cohort/checks.py`）。
名单里有学生在库里找不到时返回 1（汇总照写），任一断言失败也返回 1。

| 断言 | 含义 |
|---|---|
| `coexistence` | 名单里每名学生在同一 `student_id` 下都有数学、语文、道法三科证据；没有英语或其他科证据 |
| `no_private_copies` | 个人层（`platform.*`，与 `insight.*` 白名单里的判断账本与分表事实）以外：没有学生键列（学生、作答、证据、判分、家庭、账号等）；列值里没有这批学生、其账号、家庭、家长账号、证据 ID、作答 ID；个人层里没有以学科名开头的表；个人层带 `student_id` 的表都在个人表清单里（清单未建时标“待 0035”跳过） |
| `grade_rows_provenance` | 这批学生的每条判分都有 `evidence_ids / confidence / source_version / model_version / human_review_required`，证据都是同一学生、同一学科的 |
| `isolation_rls` | 以 BFF 口径逐表比对个人表清单里的每张表（清单未建时回退 6 张并标注）：学生本人只看到自己（按家庭关联的表放宽到同一家庭）；家长只看到**其全部生效绑定**的孩子（名单外的孩子也算应见，再与名单取交集比较）；未绑定家长、未登录（空账号与从不设账号两种）什么也看不到 |
| `guardian_links_backed` | 名单学生的生效家长绑定里，既不是“学生所在家庭的创建人”、也没有对应“已使用邀请”的条数（只输出条数） |

**局限（家长绑定）**：`isolation_rls` 只能证明“RLS 按绑定放行”，不能证明“绑定本身是对的”——库里绑了谁，RLS 就放行谁。
`guardian_links_backed` 只核对绑定有没有来历（家庭创建人或已使用的邀请）；平台没有“家庭成员”表，邀请是否发给了真正的监护人、
家庭创建人是否就是监护人，库里查不到，需由试点运营对照线下同意书人工核对（同意书只核对，不带出试点环境）。
条数不为 0 时停止并交平台负责人在试点环境内排查。

真实环境**不跑**合成联调里的“整轮重跑零新增”（会产生写入），幂等由合成联调与 `db-test`、`bff-test` 覆盖。
私有副本的取值扫描会遍历个人层以外的所有表，建议在试点低峰执行。

## 4. 结果只入汇总

- 输出只有 `pilot_summary.json`：授权编号、名单人数与找到人数、各科证据事件总数、五条断言通过与否、三科齐全的人数、
  隔离比对的表来源与表数、隔离违规条数、个人表清单状态、无来历的家长绑定条数；
  **不输出任何学生级明细**（没有学生 ID、节点、作答）；
- 汇总交 Gate 0 评审；名单文件、输出目录在评审确认后删除，删除记录写进评审纪要；
- 断言失败时只记失败的断言名与计数，由平台负责人在试点环境内排查，不把明细带出试点环境。

## 5. 星图截图（可选）

4-H 壳页面合入、H5 星图 v1 上线后，由教研以**合成家庭**在试点壳内截图（不截真实学生页面）；真实学生的星图不截图、不出试点环境。

## 6. 停止条件

任一前置条件缺失、发现连接的不是试点库、断言输出里出现任何学生级字段、或需要写入才能继续 —— 立即停止并记录在
`specs/e2e-integration-sprint5/lessons.md` 的“待人确认”。
