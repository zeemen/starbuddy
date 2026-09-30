# 冲刺 4-K 道法切片 · lessons

spec：`specs/kge-daofa-slice-sprint4/spec.md`（已确认，待人确认 #1–#7 按默认）。PR 首行 `[NEEDS-HUMAN]`（判分依据入库、教研金标、真实内容交接）。
本任务**不改** `db/migrations`、`packages/contracts`、`docs/personas`、`evals/**/gold/**`，不改适配器运行时与审核台；不写任何真实道法内容：
观点、页码、Rubric、时政一律是标明“合成、非教材内容 / 非真实时政”的样例，人物地名虚构，来源主机是 `.example` 保留域名，不含任何教材原句。

## 交付

| spec 设计要点 | 位置 | 验证 |
|---|---|---|
| 1 观点地图导入 | `tools/kge-import/src/daofa/{yaml-doc,common,domain,seed,snapshot,main}.ts`、`templates/daofa-domain.yaml`、`samples/daofa/{law,pack-nodes}.yaml` | `tests/daofa/domain.test.ts`、`cli.test.ts`、`daofa-db.sh` 第 1 段 |
| 2 投影与页码核验 | `src/daofa/page-check.ts`（抽样、核验单、回填校验、逐版次判定、回填 SQL） | `tests/daofa/page-check.test.ts`、`page-backfill-demo.sh` |
| 3 采分点入原子层 | `src/daofa/rubric.ts`、`sql/export-scoring-points.sql`、`tests/daofa/rubric-check.sh` | `rubric.test.ts`（逐字段变异）、`daofa-db.sh` 第 2 段、`make zhixing-rubric-check` |
| 4 时政 | `src/daofa/affairs.ts`、`templates/daofa-current-affairs.yaml`、`samples/daofa/{current-affairs.yaml,source-domains.txt}` | `affairs.test.ts`、`daofa-db.sh` 第 3、5 段 |
| 5 《学生读本》 | 覆盖报表单列“读本内容：已排除”；导入报告 `student_reader: excluded` | `tools/kge-coverage/tests/daofa.test.ts`、`daofa-db.sh` 第 4 段 |
| 6 覆盖与评测 | `tools/kge-coverage/src/daofa.ts`、`sql/daofa-snapshot.sql`、`--subject zhixing`；`evals/kge-tagging` 的 `--subject`；`evals/daofa-grading/` | `kge-coverage-test`、`kge-tagging-eval-test`（`test_daofa.py`）、`daofa-grading-eval-test` |

Makefile：新增 `kge-daofa-import-test`（类型检查 + 单测 + `daofa-db.sh` + `page-backfill-demo.sh`，并作为 `kge-import-test` 的前置）、
`zhixing-rubric-check`（默认一次性库上“内容包 → kge.scoring_point → 导出 → 比对”；`ZHIXING_RUBRIC_EXPORT=` 给演练库导出时直接比对）、
`daofa-grading-eval-test`；三者进 `check`。已有的 `kge-coverage-test`、`kge-tagging-eval-test`、`kge-items-test` 自动带上道法单测
（`tests/*.test.ts`、`tests/test_*.py`、kge-import 的 tsc 覆盖 `src/daofa`）。缺依赖或 Postgres 时非严格跳过、严格失败。

## 实现要点

1. **YAML 子集**：kge-import 原有的 `yaml.ts` 只认映射（且有测试断言列表报错），道法模板需要列表，另写 `src/daofa/yaml-doc.ts`
   （块状列表、列表项映射、单行流式列表），不引入 YAML 依赖（规则 10）。原解析器与其测试不动。
2. **多文件一个版本**：`map` 可给多个领域文件，合成一个草稿地图版本（ID = `stableUuid("kge:zhixing:<版本>")`，与 KGE-01 同一派生法）。
   合成内容包的采分点挂在 `MORAL.SYN.*` 节点上，所以样例分两个文件：`law.yaml`（验收要求的法律领域 2 个大概念、6 个观点、关联边）与
   `pack-nodes.yaml`（采分点挂接节点）。不改 `adapters/zhixing/content/pack.json`（避免动适配器测试与契约行为）。
3. **原句拦截按键名**：任何层级出现 `quote / original / excerpt / verbatim / phrases / full_text / reprint / 原句 / 原文 / 摘抄 / 转载 / 全文` 的键都拒绝，
   再加每层的键白名单；投影只收版次、年份、印次、册、章、页码。种子里 `quote_text` 固定写 NULL。
4. **前置边**：默认只收 `related`（无方向，反向重复拒绝）；`prerequisite` 必须 `explicit_prerequisite: true` + `basis`（写进 `evidence_ref = explicit:<依据>`），
   并复用 `tools/kge-inventory/src/graph.ts` 拦成环与传递边（与库里 `validate_map_version` 口径一致）。其余边类型一律拒绝。
5. **合成挡板**：合成样例生成的地图、采分点、时政 SQL 都带 `DO $synthetic$` 挡板，只有 `SET starbuddy.allow_synthetic = on` 的会话能执行
   （库上测试断言不 SET 时执行失败且不写入）。合成与真实内容不能进同一版本、内容包与地图的 `synthetic` 不一致也拒绝。
6. **采分点只新增**：`INSERT … ON CONFLICT DO NOTHING` 之后，用同一批 VALUES 在 `DO $check$` 里逐字段核对库里的行；已有同键不同内容 → 整个事务失败
   （0011 本来就没给 UPDATE；这一步把“静默跳过”变成“报错”）。采分点校验复用适配器的 `indexPack`（同一套规则），只收 `discipline = daofa`。
7. **一致性检查**比对 8 个字段（`score、node_ids、subject_dimension、keyword_family、required_logic、material_linkage、partial_credit_rule、common_errors`）
   与采分点集合；jsonb 会重排键，比对按规范化 JSON（键排序）进行；只报 ID 与字段名，不回显内容。单测对每个字段与 6 个子字段做变异，
   并断言变异清单覆盖全部比对字段。
8. **时政**：离线按 `--source-domains`（人从 `policy.source_domain` 导出的主机清单）核对，种子里再用 `policy.url_host` 同一规则在库里核一次
   （`starbuddy_kge` 对 `policy.source_domain` 只有 SELECT）。12 个月上限用“加 12 个月、月末截断”判定（1 月 31 日 → 次年 1 月 31 日可以，多一天不行）。
   到期当天即不可见（0026：`current_date < valid_until`），即将到期报表同一口径：`valid_until − 今天 ≤ 0` 为已过期，`≤ 30` 为即将到期。
9. **页码抽样**：每版次 `max(ceil(20% × N), 20)` 条且不超过 N，按 `sha256(种子 \0 投影 ID)` 排序取前 k 条（与输入顺序无关）；投影 ID 由
   `stableUuid(版本, 节点, 版次, 册, 页码)` 派生，核验单可以跨次生成对得上。通过率用整数比较（`passed × 10000 ≥ round(阈值 × 10000) × sampled`），恰好 95% 算过。
10. **快照**：覆盖报表的输入由导入器离线组装，也可由 `tools/kge-coverage/sql/daofa-snapshot.sql` 在库里只读导出；库上测试断言两边逐项相同、
    覆盖报表两边逐字节相同（与 KGE-02 `coverage.sql` 的做法一致）。

## 与 spec / 任务说明的偏差

1. **“verified” 在现有表上写不进去**：0011 的 `projection_verified_by` 要求 `mapping_state = 'verified'` 时 `quote_text` 非空，而 decisions #5 / spec 待人确认 #2
   要求原句不入库。所以“核验通过 → 标 verified”在现有 schema 上必然失败（`page-backfill-demo.sh` 第 3 段断言失败原因就是这条约束，且整体回滚）；
   也就是说，**在法务确认原句口径之前，任何道法投影都不会对只读角色可见**（0026 只放行 verified）。见待人确认 A。
2. **回填没有专用函数**：0011 / 0019 没有“投影核对 / 签字”函数；现有路径是知识服务（`starbuddy_kge`，审核台后端）在草稿版本上直接写
   `mapping_state / verified_by / verified_at`，再由 active staff 对整个地图版本签字（`kge.sign_review('map_version', …)`，摘要含投影，签字后再改要重签）。
   演示脚本只用这条路径，不改审核台、不新增函数；“以 staff 身份逐条写核对人”需要新函数（待人确认 B）。
3. **时政摘要不入库**：`kge.current_affair`（0011）没有摘要列；模板要求自写摘要并校验（≤ 200 字、拒绝转载字样与原文字段），但种子只写来源、
   挂接观点、有效期与 `pending`，摘要留在内容仓。见待人确认 C。
4. **采分点只能挂在观点上**：导入器要求 `node_ids` 都是目标版本的有效观点（0011 只要求节点存在）。这是本切片的约定，不是库约束。
5. **题目数口径**：覆盖报表的“题目数”= 挂到该观点的采分点所属的不同题目（道法题还没有 `kge.item_tag`）；道法打标接入后再并入 item_tag。
6. **`skill_node` 不比对**：内容包采分点的 `skill_node` 是能力视图挂接（`platform.skill_node`），不在 `kge.scoring_point`，一致性检查不含它。
7. **判分一致率的输入**是逐点 CSV（`response_id,item_id,point_id,score,attribution`）；适配器 `grade` 返回 → CSV 的转写脚本随真实判分评测另起。
8. **kge-coverage 的道法报表只含 ID**（不含观点名称），比数学报表更严，以满足“报告只含汇总与 ID”。

## 待人确认

A. **教材原句与 verified 的关系（spec 待人确认 #2、#5）**：现有约束让“页码已核对”依赖“原句已入库”。两个选择：
   (a) 法务确认原句口径后按版次逐条核对入库（原句在投影表锁定哈希），届时 verified 自然可写；
   (b) 另起迁移草稿，把“页码已核对”与“原句已核对”分开（例如放宽 `projection_verified_by` 为只要求核对人与时间，或加 `page_verified_*` 列 / 新状态），
   让只读角色在没有原句时也能看到已核对页码。默认不改，等人决定。
B. **页码回填函数（spec 待人确认 #5）**：若要求 staff 逐条签核对结果（而不是知识服务写、staff 签整个版本），需要迁移草稿新增例如
   `kge.sign_projection_check(版次, 回填摘要, 通过率)`：以 `kge.current_staff()` 取签字人、在库里复算通过率 ≥ 阈值才允许把该版次标 verified、
   写 `content_review` 与签字人表。需求已按本任务的回填文件格式与判定函数定义好，SQL 由人另起任务。
C. **时政摘要是否入库**：现为只在内容仓；如需在知识服务里返回摘要，要给 `kge.current_affair` 加摘要列（迁移）并定审核口径。
D. **《学生读本》（spec 待人确认 #1）**：按默认封测范围明确排除；覆盖报表已单列“读本内容：已排除”。**学生端与家长端道法入口的范围说明文案属于产品话术，由人提供**
   （本任务不写任何对学生、家长可见的文案）。读本入库另起任务，前提是法务确认原文口径。
E. **时政上限与提醒（spec 待人确认 #6）**：默认最长 12 个月、提前 30 天，可用 `--max-months`、`--days` 调；由教研确认。
F. **白名单清单的来源**：离线校验用的 `--source-domains` 由人从 `policy.source_domain` 导出；`policy.source_domain` 目前只登记考情政策线的官方来源主机，
   时政白名单是否与之共用一张表（还是按用途加一列）待教研与政策线确认（decisions #6）。

## 真实内容交接清单（教研与法务工作，本任务不做）

1. **观点下钻**（spec 待人确认 #3）：法律领域先行，按 `templates/daofa-domain.yaml` 填写 `content/daofa/<领域>.yaml`（内容仓）：大概念 → 观点的自写概括、
   认知层次、包含 / 不包含、典型题例（合成题或题目 ID）、关联边、确需时的显式前置；知行 30 个大概念的短编码写进 `legacy_code`。
   过 `node src/daofa/main.ts map` 校验，在演练库执行种子、跑 `kge.validate_map_version`，两名 staff 签字发布。
2. **页码核对**：`page-sample` 生成核验单 → 对照纸质教材逐条核对并回填 → `page-backfill` 判定；低于 95% 的版次先更正投影再重抽。
   verified 的写入依赖待人确认 A。
3. **Rubric 审定**：教研审定真实题的采分点（point_id、分值、学科维度、关键词三档、必要逻辑、材料关联、给分规则、常见错误），写进内容包格式，
   `rubric` 导入 `kge.scoring_point`（新 `rubric_version`），`make zhixing-rubric-check ZHIXING_RUBRIC_EXPORT=<演练库导出>` 核对；合成 4 题届时替换。
4. **时政白名单与选题**：教研确定白名单主机（由人登记进 `policy.source_domain`）与选题，按模板录入自写摘要与有效期；导入为 pending，审核台签字后发布；
   每周跑 `affairs-expiring` 下架到期条目。不自动抓取、不由模型生成核心政治判断内容。
5. **≥ 100 题试标注**：两名教师按 `evals/kge-tagging/README.md` 的格式独立标注主考点（道法节点 `MORAL.*`），提交到 `evals/kge-tagging/gold/`；
   `python3 kappa.py --subject zhixing --annotations gold/<文件>.csv --enforce`，kappa ≥ 0.7 为验收。
6. **≥ 50 题教师判分标注**（spec 待人确认 #7）：按 `evals/daofa-grading/README.md` 的逐点格式提交到 `evals/daofa-grading/gold/`（只含回应编号、题目、采分点、分数、归因，
   不含作答原文）；适配器判分转写后 `python3 grading.py --gold gold/<文件>.csv --adapter <判分>.csv --pack … --enforce`，逐点一致率 ≥ 90% 为验收。

## 经验

- `node --test` 的 strip-only 模式不支持 TypeScript 的构造函数参数属性（`constructor(private x)`），要写成普通字段。
- `psql -c "SET ROLE …; SELECT …"` 会先打印 `SET` 命令标签；取查询结果要只看最后一行。
- 其他子代理同时在改 Makefile（`check` 里新出现了 `mastery-mutation`）；本任务对 Makefile 只做插入式修改。
