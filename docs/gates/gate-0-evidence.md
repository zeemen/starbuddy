# Gate 0 证据包

- 依据：`docs/plan/开发计划-v2.0.html` 第六章“阶段 0 验收清单”（第 414–427 行）；执行方法 `.claude/skills/gate-check/SKILL.md`（参数 0）。
- **证据只对应 `d0ba0f7`；文中提交号为该文件最后修改提交**（`git log -1 -- <路径>`）。主工作树另有未提交的 H6 改动（apps/web、services/bff 等），不在证据范围；`make check` 与单独命令都在独立工作树 `/home/user/wt-check`（`git checkout -f --detach d0ba0f7`，运行前后 `git status` 干净，忽略 node_modules / .venv）上运行。取证日期：2026-10-01。
- 判定只取三种：**满足 / 不满足 / 无法自动判断（需人）**。本文件**不下总体结论**，Gate 0 是否通过由人决定。
- 规则 14：留出集只引用教研负责人交回的报告；本次取证没有打开任何留出集文件（`evals/persona/diandian/holdout.txt`、`evals/kge-tagging/run_holdout.sh`）。但 `make check` 里的 `eval-persona` 会**程序读取**点点留出集的冻结名单（`evals/persona/diandian/compare.py:71` 的 `HOLDOUT_PATH`、`evals/persona/diandian/run.py:79` 的 `read_holdout(HERE / "holdout.txt")`），本次运行同样如此。
- 文中“合成”指固定种子生成的合成数据与 test 环境假供应商，不代表真实学生或真实模型表现（规则 8）。
- 债务与决策编号（B = 阻断级待决、T = 技术债）引自 `docs/gates/w13-debt-and-decisions.md`（与本文件同目录，同样未提交）。

## 判定一览

| # | 类型 | 条件 | 判定 | w13 编号 |
|---|---|---|---|---|
| H1 | 硬 | 三科能写入同一学生模型（原“四科”，2026-10-01 改） | 不满足 | B01、B02、B03、T10 |
| H2 | 硬 | 家长隔离与扣次幂等通过 | 不满足 | B05、B06、B17、B24、T17 |
| H3 | 硬 | 点点跨模型一致性有明确结论 | 不满足 | B07、T50 |
| H4 | 硬 | 数学、道法知识切片过门 | 不满足 | B08、B09、B10、B11、B12、T33 |
| H5 | 硬 | 画像底线 | 不满足 | B13、B14、B15、B16、B17、B18、T21、T24 |
| S1 | 软 | 契约测试覆盖率 | 无法自动判断（需人） | B01、B02、T02、T03 |
| S2 | 软 | 页面优先对照评测 | 不满足 | B08、T30 |
| S3a | 软 | 人设家族级回归在 CI 中运行 | 无法自动判断（需人） | T05、T08 |
| S3b | 软 | 发布闸门三科各有一个版本（原“四科”） | 不满足 | T01 |
| S4 | 软 | 监管分类意见；境内试点环境 | 无法自动判断（需人） | B20、B23 |
| S5 | 软 | 单位经济首版 | 无法自动判断（需人） | T39 |

分项判定同样只用上述三个判定词；括号里可以补充说明（范围、依据、需谁决定），但不引入第四种判定。条件级判定以上表为准。

---

## H1 · 三科能写入同一学生模型（硬；原“四科”）　判定：**不满足**

计划要求的证据：20 名测试学生三科事件与节点查询记录；星图截图（计划原文为“四科”，按下述决定改为三科）。不满足的依据：星图截图未生成、复测为桩、掌握未写库（w13 B03）。与 H2“报告不存在即不满足”同一口径。

另注：**口径已定（2026-10-01 用户决定：Gate 0 “四科”改为“三科”，英语搁置不计）**（英语搁置见 `specs/adapter-echo-sprint3/lessons.md`，`025901b`；w13 B02 已关闭）。合成证据能否作 Gate 证据仍待人确认（`specs/e2e-integration-sprint5/lessons.md` 待人确认 #3）。

| 分项 | 证据 | 分项判定 |
|---|---|---|
| 20 名学生三科事件写入同一学生模型 | `tools/e2e-cohort/sample/summary.md`、`checks.json`（最后修改 `d0ba0f7`）；`events.csv`、`students.csv`、`nodes.csv`、`retests.csv`（最后修改 `981c934`，**`d0ba0f7` 未重生**）。合成学生 20 名，三科（数学 aya、语文 wenli、道法 zhixing）都有事件的 20 名；证据事件 aya 264、wenli 140、zhixing 110；十二条断言全部通过。报告头标“**合成联调，不是内测结论**”。**样例来源**：`run_metadata.json` 记 `commit = dadc5b7…`、`worktree_dirty: true`（在含其他任务未合入改动的工作区上生成；w13 T10 要求干净工作区重生）。证据在未签字候选契约 1.0.0 下取得（`STARBUDDY_ALLOW_CANDIDATE_CONTRACT=1`，w13 B01）。 | 无法自动判断（需人：合成证据口径，待人确认 #3） |
| 节点查询记录 | `nodes.csv`（207 行）。KGE-04 第二步（掌握写库）前节点状态一律 `evidence_only`（lessons 偏离 #4）；没有按学生查节点的读接口，节点查询是只读 SQL 聚合（契约缺口 #2）。 | 不满足（掌握未写库） |
| 复测 | `retests.csv` 32 次，全部是桩（`run_metadata.json` 的 `retest_scheduler: stub`）。 | 不满足（复测为桩） |
| 星图截图 | `run_metadata.json`：`starmap_screenshot.status = skipped`，原因“依赖 4-H 壳页面合入”；lessons 偏离 #3。 | 不满足（未生成） |
| “四科”改“三科” | 已决定（2026-10-01 用户决定：Gate 0 “四科”改为“三科”，英语搁置不计）；lessons 待人确认 #4 关闭。 | 满足（口径已定） |

其他偏离（`specs/e2e-integration-sprint5/lessons.md`）：
- 道法没有诊断，只走批改与复测（偏离 #1）；三科内容包只有九年级（偏离 #6）；计量零新增是“没有经过 BFF 的零”（偏离 #5，w13 B06 ④）。
- **grade 摘要缺三字段**：learning-internal v1 的摘要不带 `confidence / source_version / model_version`，溯源断言只在适配器响应体与 `platform.grade_result` 落库行上做（契约缺口 #1，lessons 第 54 行）。
- **验证器任意 `rule_ref`**：平台尚未对照注册表核实 `rule_ref`、`rubric_version`，`judged_by = verifier` 配任意 `rule_ref` 都按非主观、高置信落库（`specs/plugin-api-v1.0-step2/lessons.md:42`；w13 第 160 行）。`grade_*_provenance` 断言只核对字段存在。
- **`no_private_copies` 依赖草稿**：私有副本扫描读迁移草稿 0035 的 `platform.personal_table_registry`（跨任务依赖 #1，lessons 第 30 行），insight 白名单随个人层归属 ADR 未定（契约缺口 #7，lessons 第 67 行）。

自动化守护：`services/learning/tests/test_e2e_cohort.py` 把每次联调渲染出的证据包与 `sample/` 逐字节比对（`run_metadata.json` 忽略提交号与工作区状态），随 `learning-test` 进入 `make check`。

**记录（2026-10-02，KGE-04 第二步 PR 3，`specs/kge-04-mastery-step2/lessons.md`“PR 3 实现记录”；不改上面的判定与证据锁定）**：
联调复测改由 `platform.retest_task` 驱动（去掉“桩”），合成样例已重生（在 scratchpad 的工作树副本里跑 `make gate0-evidence`，与仓库内样例逐字节比对，
只拷了变化的 `events.csv`、`nodes.csv`、`retests.csv`、`checks.json`、`summary.md`、`run_metadata.json`；`students.csv` 与 `starmap/` 未变）。变化：
- 复测断言 `retest_via_stub` → `retest_via_retest_task`，摘要显示“**通过（0 次，未走到）**”；**复测 32 次（桩）→ 0 次**。联调用合成、未签字的数学掌握配置，
  数学证据在重放里全部不计入（`checks.json` 的 `excluded_reasons`，共 116 条）：
  - `no_grade` 80：插件诊断（`diagnose`）的作答证据没有判分结果（`grade_result`），按第一步 #23“只计有判分且有作答引用的证据”不计入；
  - `pending_review` 36：批改（`grade`）的证据——阿鸦合成内容包题目全部未准入（`source_unverified`），判分一律带 `content_not_admitted`、待复核，
    待复核不推动门槛（第一步 #11）。
  计入 0 条 → 没有节点键掌握记录 → 没有复测轮任务 → 不执行复测；知行、文砺没有掌握配置，不写掌握、不建任务。没有编造复测。
- 证据事件 aya 264 → 232、zhixing 110 → 54（少了桩复测的作答）；节点查询行 207 → 201；`run_metadata.json` 的 `learning-internal` 1.0.0 → 1.1.0、`retest_scheduler` 改为 retest_task。
- **样例生成于未提交的工作区**（`commit = 8c91e30…`、`worktree_dirty: true`）；本节锁定的证据提交号（`d0ba0f7`）与 H1 各行的“最后修改”提交号**待 PR 3 合入后由人更新**。
- H1“复测”分项仍是**不满足**，理由由“复测为桩”变为“真实调度在合成内容下到不了复测（内容未准入）”；是否在联调里登记已准入的合成题以走通复测，见上述 lessons 待人确认。

**记录（2026-10-02，H5 星图 v1 PR 3，`specs/star-map-v1-h5/spec.md` 待人确认 #7；不改上面的判定与证据锁定）**：
另附一份星图 v1 截图 `tools/e2e-cohort/sample/starmap-v1/`（`V1-student.png`、`V1-guardian.png`、`starmap-v1.json`、`provenance.md`），标“**未启用**”：
开关 `starmap_v1` / `starmap_dashed` 默认关，只在 test 环境、合成数据（`db/seeds/dev/star-map-v1-synthetic.sql`：合成数学地图 0.9.0、合成掌握配置与合成判断，均未经教研签字）下截取；
由 `make gate0-starmap-v1` 生成（视口 1024×768、DPR 1、浅色、整页 PNG），`starmap-v1.json` 记文件与像素 SHA-256，同一环境重跑两次像素哈希一致。
- **v0 截图不替换**：`tools/e2e-cohort/sample/starmap/` 与它的 `starmap.json`、`provenance.md` 未动；“星图截图”分项的判定与依据不变（仍以 v0 为准）。
- v1 截图只说明 v1 页面在开关打开时的样子（三态、“保持”外圈、中性底星、虚线、地图建设中、能力视图占位），**不是**同一学生模型的证据；样例生成于未提交的工作区（`worktree_dirty: true`）。

---

## H2 · 家长隔离与扣次幂等通过（硬）　判定：**不满足**

计划要求的证据：渗透测试报告；计量并发、断线、重试、回滚测试。

| 分项 | 证据 | 分项判定 |
|---|---|---|
| 渗透测试报告（第一轮） | `docs/security/pentest/` 只有交接包模板：`coverage.md` 为空白模板、`findings.csv` 只有表头、`report-template.md`、`scope.md`、`rules-of-engagement.md`（`d0ba0f7`）。`specs/household-pentest-sprint3/lessons.md`：“**渗透测试本身没有执行**”。仓库内没有 `docs/security/pentest/<日期>-round1/`（w13 B05）。 | 不满足（报告不存在） |
| 渗透测试报告（第二轮：用户入口、会话、真实部署拓扑） | `specs/household-pentest-round2/spec.md`（`3ec4157`）状态“**草稿，等人确认后才开始实现**”；该 spec 写明“两轮都完成、严重与高级别发现全部关闭或书面接受之后，Gate 0 硬条件‘家长隔离’才算有证据”（w13 B05）。 | 不满足（报告不存在） |
| 自动化隔离基线 | `db/tests/20_rls_household.sql`（`de1f610`）；`db/tests/80_attack_surface.sql`（`d0ba0f7`：每表 ENABLE + FORCE RLS、SECURITY DEFINER 函数核对、授权与策略快照、可见性矩阵、函数越权矩阵）；渗透夹具 `db/pentest/`（`make pentest-env-test`）；攻击面清单 `docs/security/attack-surface.md` 与 `docs/security/check_attack_surface.py`；联调断言 `isolation_rls`、`guardian_links_backed`（H1）。**前提**：结论**仅在一次性库按序应用 0001–0036 时成立**。0010–0036 都是草稿、未在任何共享环境执行（w13 B24）；特别是 0031 执行前，0002 的 `signup_guardian`（`db/migrations/0002_platform__household.up.sql:142`，免验证码注册）与 `lookup_accounts_by_phone`（同文件 `:153`，攻击面 C3 号码枚举）仍授予 BFF，0031 才收回（`db/migrations/0031_platform__revoke_legacy_signup.up.sql:8`）。另：已绑定家长在库层可读学生全部证据，含诊断作答与过程原文（w13 B17）。 | 满足（仅限一次性库按序应用 0001–0036 的范围；共享环境需人确认迁移执行，B24） |
| 计量并发 / 断线 / 重试 / 回滚 | 库层：`db/tests/70_metering_functions.sql`（`fffd4fd`；同 key 重试、失败回滚、预留后断线由过期任务退回、过期后同 key 重试）、`db/tests/78_metering_v2.sql`（`6545962`）、`db/tests/79l_meter_guard.sql`。BFF 层：`services/bff/tests/gate0.integration.test.ts`（`e206752`）场景 1–6（20 连接并发扣同一额度恰好 1 个成功、预留提交后断线同 key 重试只扣一次、提交前连接被杀不留痕、结算前失败回滚、过期与结算并发不死锁、24 小时复看、选科 / 单日上限 / 试用并发）；**单独运行结果见文末“单独命令”：22 tests / 22 pass / 0 fail / 0 skipped**。压测：`tools/loadtest/sample/baseline.md`（`4625476`）“计量一致性（压测后）”预留 44 笔、结算 42、释放 2、仍在预留 0，结论“零泄漏”；但**批改没有 BFF HTTP 入口，压测经计量驱动桩直接调用 BFF 的 `meteredCall`**（`tools/loadtest/sample/baseline.md:87`），且为本机 small 档。扣次相关的已接受风险与规则待签字（w13 B06：模型路由不核对预留也不去重；转人工复核按成功扣 1 次；诊断预留 10 分钟与诊断最长 35 分钟；联调回放不经 BFF）。 | 无法自动判断（需人：B06 签字；自动化部分只在库层与 BFF `meteredCall` 范围内满足） |

---

## H3 · 点点跨模型一致性有明确结论（硬）　判定：**不满足**

计划要求的证据：三家模型对照报告与决策记录（w13 B07）。

| 分项 | 证据 | 分项判定 |
|---|---|---|
| 对照工具 | `evals/persona/diandian/`（`run.py`、`compare.py`、`r2report.py`、`r2_criteria.yaml`、`postproc/`、`revisions/`），`85ab7a8`。 | 满足（仅工具具备，不是结论） |
| 对照报告 | `evals/persona/reports/` 只有 `r1-sample-fake/`、`r2-sample-fake/`（三家**假候选**的流程样例；`85ab7a8`）。`specs/diandian-consistency-r2-sprint3/lessons.md` 第 4 行：“**真实首轮与二轮没有跑**”。 | 不满足（真实三家对照未跑） |
| 决策记录 | `specs/diandian-consistency-r2-sprint3/adr-08-decision-record.md`（`85ab7a8`）状态“草稿，待真实二轮与签字”，结论未勾、依据栏与签字栏空白；ADR 08 草稿待签。留出集汇总未交回。 | 不满足 |
| 留出集封存 | **构建方已接触留出集原文，需重新封存**：`referral.yaml` 开发集与留出集混在一起，实现时读过（`specs/diandian-consistency-r2-sprint3/lessons.md:55-56`；w13 B07）。另见文首：`eval-persona` 程序读取冻结名单。 | 无法自动判断（需人：教研负责人重新封存） |

---

## H4 · 数学、道法知识切片过门（硬）　判定：**不满足**

计划门槛：课标承接 100%；留出集无处可标 < 2%；kappa ≥ 0.7；打标 Top-1 ≥ 90%；核心节点页面发布率 100%；道法采分点一致率 ≥ 90%。人工产出（编纂规范 v1、页面仓库、各 `gold/` 目录）均未到位（w13 B08）。

| 指标 | 工具 / 证据 | 分项判定 |
|---|---|---|
| 课标承接 100% | `services/knowledge/knowledge/m1.py`（`3bd3451`，`curriculum_coverage`）。节点—课标承接关系没有表，库上数据时恒为“不可计算：承接关系未入库”（w13 B09）。仓库内没有真实 M1 报告。 | 不满足（未产出） |
| 留出集无处可标 < 2% | `evals/kge-tagging/run_holdout.sh`（教研负责人在封存环境执行，规则 14）；没有教研交回的报告。 | 无法自动判断（需人：待教研报告） |
| kappa ≥ 0.7 | `evals/kge-tagging/kappa.py`（`evals/kge-tagging` 最后修改 `359d9ef`）；`evals/*/gold/` 不存在，`fixtures/` 为合成格式样例。 | 不满足（未产出） |
| 打标 Top-1 ≥ 90% | `evals/kge-tagging/top1.py`；`fixtures/tagging-report.json` 为合成（`map_version 9.9.9-syn`）。 | 不满足（未产出） |
| 核心节点页面发布率 100% | `services/knowledge/knowledge/m2.py`（`f9b90e2`）；道法 viewpoint 节点挂不了页面（`specs/kge-m2-gate-sprint5/lessons.md` 偏离 #3 / A1；w13 B11）；道法教材投影在现 schema 上写不成 verified，只读角色看不到任何道法投影（w13 B10）；核心节点清单待教研签字（A2、A5）；审核台同一 staff 可自改自签自发（w13 B12）。没有真实 M2 报表。 | 不满足 |
| 道法采分点一致率 ≥ 90% | `evals/daofa-grading/grading.py`（`359d9ef`），`fixtures/` 为“合成、非金标”，报告标“流程样例”。 | 不满足（未产出） |

`make kge-m2` 需执行人给真实输入，不进 `make check`；`services/knowledge/tests/test_m2.py` 只证明合成夹具上的计算流程。M1 报告无快照摘要（w13 T33）。

---

## H5 · 画像底线（硬）　判定：**不满足**

计划要求：对学生家长可见的陈述 100% 引用判断；禁用推断 0；C 级识别内容不生成结论；删除覆盖全表。SIE-03 各 PR：`fffd4fd`（PR① 迁移草稿 0032–0035）、`547843c`（PR②）、`95a8400`（PR③）、`e4abec0`（信任界面前端）、`dadc5b7`（PR④）；周报 v0：`d0ba0f7`。

| 分项 | 证据 | 分项判定 |
|---|---|---|
| 可见陈述 100% 引用判断 | 首份报告 `services/insight/insight/report.py`（`f60d9f4`）每句带判断 ID，检查不过不出报告；周报迁移草稿 `db/migrations/0036_insight__weekly_report.up.sql` 与 `db/tests/79r_weekly_report.sql`（`d0ba0f7`）逐句核对判断；信任界面 `data-claim-id`（`specs/trust-ui-sprint5/lessons.md`）。**合成模板，没有真实陈述**：洞察服务只有 `syn.*` 占位模板，`external_claim_templates` 为空，壳内与周报都不显示任何判断陈述（w13 B14）；`ActorVerifier` 缺省一律不通过，真实环境里 `insight.explain` / `insight.dispute` 都是 404（w13 B15）。星图 v0 星上没有 `claim_id`，按规则 11 过渡条款运行（w13 T24）；运行时 DOM 爬取未做（w13 T21）。 | 无法自动判断（需人：B14 模板登记、B15 授权接线、过渡期展示口径） |
| 禁用推断 0 | `services/insight/tests/test_forbidden_inference_sie03.py`（`dadc5b7`，43 项，五类来源零命中、各带注入反例），随 `insight-test` 进入 `make check`。词表 `status: draft` 未签字，且漏拦“粗心 / 马虎 / 粗心大意”（`specs/sie-03-dispute-reconcile/lessons.md:328-329`；w13 B13）。 | 无法自动判断（需人：B13 词表签字与口径） |
| C 级识别内容不生成结论 | 学习服务侧：`services/learning/tests/test_evidence_attributes.py`（`52e6ae9`）。洞察服务侧约束测试未补；`skill_observation` 无等级标记、`insight.claim.evidence_ids` 不按等级过滤（`specs/kge-m2-gate-sprint5/lessons.md` A3；w13 B18）。 | 不满足（洞察服务侧缺约束与测试） |
| 删除覆盖全表 | `db/tests/79q_erasure_coverage.sql`（`d0ba0f7`）：按值全库扫描，执行后内容标记零处、ID 标记只剩 11 张豁免表。SIE-03 lessons PR④：“验收第 3 条未全部完成”——档案页尚无表；`trial_claim` 豁免理由与实际不符；认证两表留存手机号摘要不在清单；库外存储不覆盖；staff 四表只靠函数守卫（w13 B16）。另：家长在库层可读诊断过程原文（w13 B17）。迁移 0032–0036 为草稿（B24）。 | 不满足（档案页未覆盖；B16 缺口待人确认） |

---

## 软条件

### S1 · 契约测试覆盖率　判定：**无法自动判断（需人）**

| 科目 | 证据 | 分项判定 |
|---|---|---|
| 阿鸦 | `specs/adapter-aya-methods-sprint4/lessons.md`（`34c8e30`）；本次 `conformance-aya.json`：contract 1.0.0，6 / 6。 | 满足（**仅在未签字候选版 1.0.0 下**；0.9.0 下 2/6，四个新方法声明未实现、返回 501；签字前需人，w13 B01） |
| 知行 | `adapters/zhixing/tests/contract.test.ts`（`b5c445f`）；本次 `conformance-zhixing.json`：contract 0.9.0，5 / 6（teach 未实现）。 | 满足（0.9.0 下） |
| 文砺 | `adapters/wenli/tests/contract.test.ts`（`b5c445f`）；本次 `conformance-wenli.json`：contract 0.9.0，5 / 6（teach 未实现）。 | 满足（0.9.0 下） |
| 开口成章 | 已搁置，按三科口径不计入本条（2026-10-01 用户决定：Gate 0 “四科”改为“三科”，英语搁置不计）。 | 满足（不计入，口径已定） |

**三科版本口径不一致**：阿鸦按 1.0.0（候选）计，知行、文砺按 0.9.0 计。一致性夹具由实现会话起草、需独立复核（w13 T02）；平台侧核对与泄露检查只覆盖 grade、diagnose（w13 T03）。

### S2 · 页面优先对照评测　判定：**不满足**

`evals/kge-compile/compare_retrieval.py`；仅有 `evals/kge-compile/reports/sample-synthetic/compare-retrieval.md`（`1cf75ef`），报告头“**流程样例，不是评测结论**”，12 题合成金标，少于 50 题下限。`evals/kge-compile/gold` 不存在（w13 B08）。没有真实结论报告，也没有“未达标以切块检索为主路径”的书面决定。

### S3a · 人设家族级回归在 CI 中运行　判定：**无法自动判断（需人：附 CI run 与 persona-report 产物）**

`.github/workflows/check.yml`（`efa45aa`）第 77 行配置了 `make eval-persona CONTRACT_STRICT=1 PERSONA_REPORT_DIR=…` 并上传 `persona-report`。仓库内看不到 CI 实际运行记录与产物（CI 首次真实运行未核验，w13 T05；CI 中跑两遍，w13 T08）。本地 `make check` 中 eval-persona 通过，但输出为“**录音 有效 0 / 过期 0 / 缺失 0**”——当前没有任何录音参与回归，回归只覆盖评分器自检与治理检查，不含真实或录制的模型回复。

### S3b · 发布闸门三科各有一个版本（原“四科”）　判定：**不满足**

`specs/release-gate-sprint4/`：冲刺 4 只完成第一部分（阿鸦内容包格式 3 与提示原文 C9 检查），`platform.publish_content_version` / `change_admission`（第二部分）未实现，迁移中无该函数；没有任何一科的经闸门内容版本记录（w13 T01）。英语搁置不计。

### S4 · 监管分类意见；境内试点环境　判定：**无法自动判断（需人）**

均在仓库外。仓库内只有 `docs/adr/10-domestic-hosting-selection.md`（草稿待签，`121fbae`）、`docs/adr/09-data-residency.md`、`docs/compliance/vendors.md`（没有任何供应商）；没有意见书、排期函或环境验收记录（w13 B20；同意与监护 B23）。

### S5 · 单位经济首版　判定：**无法自动判断（需人）**

`tools/loadtest/sample/cost-report.md`（`4625476`）：数学、语文、道法 × 基本 / Plus / Pro / Max 的 P50 / P90 与敏感性分析；英语行“搁置，无数据”。报告头：“**合成估算：示例价、非真实会话数据**”，额度取未发布草稿配置 `v12-pilot@1`。多科分摊口径、使用分布、价格待定（w13 T39）。

---

## 运行检查

### make check

- 命令（`/home/user/wt-check`，`d0ba0f7`，干净工作区）：`make check CONTRACT_STRICT=1 PYTHON=<scratchpad venv> CONFORMANCE_REPORT_DIR=<scratchpad>`。2026-10-01 02:13 开始，约 1 小时。
- **结果：退出码 0**，末行 `check: lint 尚未配置，跳过；其余检查已通过`。`make check` 已包含 `contract-test` 与 `eval-persona`（Makefile 第 19 行），`eval-persona` 未另跑。

| 目标 | 输出（原文摘录） | 关联条件 |
|---|---|---|
| contract-test | `contract-test: 通过（OpenAPI 3.1 合法；17 个示例符合 schema 且证据引用闭合；55 个反例全部被拒绝（v1.0 另加 15 个）；…v1.0 候选版合法、24 个示例、21 条破坏性变更 v0.9 接受 / v1.0 拒绝）` | S1 |
| contract-mutation | `mutation-test: 通过（158 个变异全部被发现，其中服务契约 86 个、v1.0 候选版 26 个）` | S1 |
| taxonomy-check | `taxonomy-check: 警告：契约示例里的 zhixing.locate 不在映射表中`；`taxonomy-check: 通过（七大类与契约、数据库一致；4 科 32 个编码，其中 12 个待教研拍板）` | — |
| attack-surface-check | `attack-surface-check: 通过（50 条攻击面、236 条引用全部有效；scope.md 的 94 个 SECURITY DEFINER 函数与授权快照一致）`——计数对应 `d0ba0f7` 的 `docs/security/attack-surface.md` 与 `scope.md`（主工作树的未提交改动不在内） | H2 |
| sie-dictionary-check | `sie-dictionary-check: 通过（47 个字段、9 个退役旧字段、词表 10 类；20 个反例与人读版篡改全部被拦下；字段白名单 12 项与迁移一致）` | H5 |
| db-test | `db-test: 迁移 up → down → up 通过（36 个迁移）`；`db-test: 最新迁移单独 down → up 通过（0036_insight__weekly_report.up.sql）`；`db-test: 通过（2010 项断言）` | H2、H5 |
| db-mutation | `db-mutation: 通过（670 个变异全部被发现）` | H2、H5 |
| learning-test | `2521 passed in 126.63s`（含 `test_e2e_cohort.py`、`test_evidence_attributes.py`） | H1、H5 |
| bff-test | `bff-test: 通过（单测 + Gate 0 六类场景集成测试）`（dot reporter，无逐项计数；计数见下“单独命令”） | H2 |
| bff-mutation | `bff-mutation: 通过（110 个变异全部被发现）` | H2 |
| conformance | `conformance-aya.json`：contract 1.0.0，implemented 6 / passed 6 / of 6；`conformance-zhixing.json`、`conformance-wenli.json`：contract 0.9.0，implemented 5 / passed 5 / of 6 | S1 |
| conformance-mutation | `conformance-mutation: 通过（28 类违约全部被发现）` | S1 |
| pentest-env-test | `pentest-env-test: 通过`；`ok - 80_attack_surface 在渗透测试夹具上全部通过（可见性矩阵覆盖全部 16 个账号）` | H2 |
| knowledge-test / mutation | `knowledge-test: 通过`；`knowledge-mutation: 122 条变异全部被测试发现` | H4 |
| kge-tagging / daofa-grading / kge-compile | `kge-tagging-eval-test: 通过`；`daofa-grading-eval-test: 通过`；`kge-compile-eval-test: 通过`（工具测试，合成夹具） | H4、S2 |
| eval-persona | `eval-persona: 用例校验通过（120 条，九类，留出集 34 条，核心可自动判定 88%）`；`eval-persona: 评分器自检通过（120 条用例 × 应通过 / 应失败两组合成回复，红线 6 条）`；`eval-persona: 人设治理回归通过（检查器自检 74/74，录音 有效 0 / 过期 0 / 缺失 0；…）`；`eval-persona-mutation: 通过（109 个变异全部被发现：点点评分器 44 个，人设治理组件 41 个，一致性二轮 24 个）` | H3、S3a |
| persona-registry-check | `persona-registry-check: 通过（登记 Prompt 3 份，豁免 0 处）` | S3a |
| insight-test / mutation | `insight-test: 通过`（含 `test_forbidden_inference_sie03.py`）；`insight-mutation: 118 条变异全部被测试发现` | H5 |
| license-check | `license-check: 通过（36 个包；…）` | — |
| 跳过项 | `kge-import-db: 第二段跳过（没有 legacy/aya 只读副本…）`；`kge-items-db: 第二段跳过（没有 legacy/aya 副本）`；lint 尚未配置 | — |

自动化全绿不替代 H2 所需的渗透测试报告，也不是 H3 的一致性结论。

### 单独命令（同一工作树、同一提交）

| 命令 | 结果 |
|---|---|
| `cd services/bff && bash ../../db/tests/pg-ephemeral.sh -- node --no-warnings --test --test-concurrency=1 --test-reporter=spec tests/gate0.integration.test.ts`（沿用 bff-test 的一次性库方式） | 退出码 0；`ℹ tests 22`、`ℹ suites 1`、`ℹ pass 22`、`ℹ fail 0`、`ℹ cancelled 0`、`ℹ skipped 0`、`ℹ todo 0` |
| `make contract-test CONTRACT_STRICT=1 PYTHON=<scratchpad venv>`（带依赖 venv） | 退出码 0；`contract-test: 通过（OpenAPI 3.1 合法；17 个示例符合 schema 且证据引用闭合；55 个反例全部被拒绝（v1.0 另加 15 个）；…）` |

### 输出文件摘要（日志不入仓，存于执行人 scratchpad）

| 文件 | sha256 |
|---|---|
| `make-check.log` | `06d44311d5e416139f9ea7a8db87fde874c93cd41d410262922315d0bd27bcc5` |
| `conformance-aya.json` | `731811a1eae76bb38e2d12a0bba1d56b793c827a3b364219851a8a6a3358da7c` |
| `conformance-zhixing.json` | `b216d039662d18d4f270c3d42a64f69639d02288e403ba0209f5e4724b952703` |
| `conformance-wenli.json` | `723070a8f90adde76343eb6f2cc3ae154ebe13ab1c3f074a73c40a9e892691a3` |
| `conformance-reference.json` | `1277f0b22a6452f1eb8ea8c1b023069e74e8dc8a0253c6c6a2064c65bf65d9f0` |
| `gate0-spec.log`（gate0 集成测试单独运行） | `b5f62a86917a638d318a153501fe0f8b3af30ba520b54753a215b26a74d32594` |
| `contract-test-only.log` | `86c10c7fcc87becb69c91ee9336adea01724997f63c39b1708dad8e910f107df` |
