# tools/kge-import · 阿鸦题库导入与打标三层（KGE-02）

对应 `specs/kge-02-atoms-tagging/spec.md` 设计要点 1、2、6。把 legacy 阿鸦题目按 KGE-01 已导入的地图版本打标，
生成 `db/seeds/kge/aya-items-<地图版本号>/` 三件产物。**只生成 SQL，不连任何数据库、不联网、不调用任何真实模型。**

> KGE-01 的骨架导入器在 `tools/kge-inventory/src/import/`（见 KGE-01 lessons 差异 2），本工具是 KGE-02 新建的包：
> 复用 `tools/kge-inventory` 的 legacy 只读加载器（解析钩子）与 `stableUuid`，也借用它的开发依赖（`typescript`、`@types/node`），自己不装依赖。

## 用法

```bash
cd tools/kge-import
# 类型检查（用 tools/kge-inventory 已装的 tsc；先在那边 npm ci）
../kge-inventory/node_modules/.bin/tsc -p tsconfig.json
# 单测（node --test；不需要 legacy，需要 legacy 的那条没有副本时打印“跳过”）
node --no-warnings --test --test-reporter=dot tests/*.test.ts
# 生成种子（默认只跑规则层）
node --no-warnings src/main.ts --aya ../../legacy/aya --map ../../db/seeds/kge/aya-fun-0.1.0
```

参数：

| 参数 | 默认 | 说明 |
|---|---|---|
| `--aya` | 必填 | 阿鸦 legacy 只读副本根目录 |
| `--map` | 必填，可多个 | KGE-01 生成的地图版本种子目录（读其中的 `import.sql`）；多个目录必须属于同一地图版本 |
| `--out` | `db/seeds/kge/aya-items-<地图版本号>` | 输出目录；开了 `--vector` / `--model` 时**必须显式给出，且不能在仓库 `db/seeds/` 之下**（含借符号链接绕进去），否则报错退出 2 |
| `--config` | `config/aya-chapter-segments.yaml` | 章节 → 六段映射表（待教研核对） |
| `--pack` / `--no-pack` | `adapters/aya/content/pack.json` | 核对题目已在内容包登记（`platform.item` 有这道题）；未登记的不写库、进工作单 |
| `--vector` | 关 | 开启向量层，**只接假嵌入**（`FakeNgramEmbedder`）；产物标注“含假实现，不得执行” |
| `--model` | 关 | 开启模型层，**只接假模型**（`FakeTagModel`，`model:fake-a/fake-tagger@0.0.0`）；产物标注“含假实现，不得执行” |
| `--top-k` | 5 | 向量层 Top-K |
| `--rules-only-a` | 关 | 只跑规则层时，规则直接命中（0.9）也算 A 级；供教研试用时放宽，见下 |

`make` 目标由仓库维护者另加（本任务不改 Makefile），建议：类型检查 + 单测并入 `make check`；生成命令做成 `make kge-items`。

## 产物

- `items.sql`：一个事务，`SET LOCAL ROLE starbuddy_kge` 写入
  - `kge.item_family`：legacy 只有压轴专项有题组（母题切片 + 变式 + 挑战），按组生成，`code = aya-sprint-<组>`，ID 由“地图版本号 + code”确定性派生；
    主考点取组内“规则直接命中”的主考点里出现最多的；组内没有落在已导入节点上的，不生成、进工作单。章节题的“单元”（U01…）不是题组，不生成；
  - `kge.item_tag`：只写 **A 级、六段齐全、已登记** 的题；`source_kind = legacy`（迁移 0024 新增的取值，旧产品题库来源待核实）、`license_state = unverified`、
    `tag_review_status = pending`（待审草稿），加迁移 0024 的 `tag_confidence`、`tag_source`、`tagger_version`、`tag_model_version`（只有模型层非空）
    与 `tag_basis`（A 级结论依据的每一层及其版本：规则层写打标器版本、一致的向量层写嵌入器版本、一致的模型层写模型版本；只有一层也写一个元素）；
    `node_ids` 含主考点、1–3 个；
    认知层次只在主考点节点恰好有一个认知层次时填（KGE-01 导入时都为空，所以现在都是 NULL）；
  - `kge.tag_candidate`：其余在已导入主题里、有候选的题，去重后的全部候选，`item_source_kind = legacy`（教研接受主考点候选写回 item_tag 时沿用；
    `id`、`status`、`created_at` 用默认值）；
  - 不写 `platform.curriculum_version`（六段由内容登记脚本负责）；不含题干、答案、解析。
  - 执行前提：迁移 0011、0019、0024；地图版本种子已执行；题目已由 `adapters/aya/scripts/register-content.ts` 登记。
- `worklist.md`：教研工作单——推不出六段的题、落在未导入主题的题（计数与 ID）、自然语言引用对不上节点的题（附 legacy 同名 / 相近节点建议）、
  无处可标的题、A 级但不能写 item_tag 的题、节点数超上限的题、未登记的题、未生成的题组等。
- `tagging-report.json`：格式 `kge-tagging-report/1`，类型定义在 `src/items/report-types.ts`（`TaggingReport`，覆盖矩阵报表按它读）。
  含各层候选数、A/B 级数、每题最终结果（题目 ID、主考点、节点、置信度、层、B 级原因、A 级依据 `tag_basis`）与每题推出的六段；items 每题一行。
- **含假实现的产物**（开了 `--vector` / `--model`）：SQL 头部、报告顶层 `warning`、工作单开头都标“含假实现，不得执行”，报告 `run.fake_layers` 列出假层；
  SQL 在 `BEGIN` 之后第一句就 `RAISE EXCEPTION`，误执行时整个事务回滚。命令行拒绝把这类产物写进 `db/seeds/`。

同一输入两次运行逐字节相同（没有时间戳，全部排序；题组 ID 确定性派生）；`tests/legacy.test.ts` 在有 legacy 副本时核对仓库种子没有过期。

## 打标三层（`src/tagging/`）

1. **规则层**（`rules.ts`，完整实现，版本 `aya-tagger-rules@0.1.0`）
   - 章节归属：专题注册表 → 题目 ID 前缀（`Q-FUN-U01-…` → `fun`）→ 压轴专项按主测节点所在章节投票；章节未导入地图版本的题不打标；
   - 显式引用：`mainNodes` 里的旧编码经 `kge.id_migration` 落到节点 → 主考点候选，**直接命中 0.9**（`rule_explicit_ref`）；
     `auxNodes` → 次要候选 0.9（`rule_explicit_aux`）；
   - 章节 → 主题：旧编码在已导入章节、但节点没导入（能力子节点 C、缺错因的易错点 P）→ 它所在知识簇的主题节点，**推断 0.6**（`rule_chapter_topic`）；
     同一题已有该主题下节点被直接命中时丢掉（父子节点不同时进节点清单）；
   - 自然语言引用：名称规范化（NFKC、去空白、去结尾括号说明）后与地图节点同名且唯一 → 推断 0.6（`rule_text_name`，只对主测 / 辅助节点）；
     legacy 的 97 处全部在 `preNodes`（前置、错误追溯方向），不是题目考查的节点，**只解析、进工作单，不产生候选**；部分包含只作建议。
2. **向量层**（`vector.ts`）：`Embedder` 接口（异步，请求带模型路由必需字段 `task = 'T7'`、`data_class = 'L0'`）+ `FakeNgramEmbedder`（字符一元 / 二元组哈希 + 余弦，**不是生产模型**）；
   按节点“名称 / 包含 / 题例”文本与题目文本（单元名 + 题干 + 选项，只在内存里）排 Top-K，第 1 名作主考点候选（`vector_similarity`）。
   真实嵌入模型待 spec 待人确认 #4。`RoutedEmbedder` 是经路由 T7 的桩：构造时要求路由地址、令牌与嵌入器版本，**调用一律抛“真实路由未接入（spec 待人确认 #4、#5）”，
   不回落到假实现**；`main.ts` 不接它。
3. **模型层**（`model.ts`）：`TagModel` 接口（异步，入参带 `task = 'T8'`、`data_class = 'L0'`）+ `FakeTagModel`；只能从规则与向量层的候选里选主考点与 1–3 个节点，输出理由码（不收自由文本）与模型版本；
   `modelLayer` 把关，越界一律抛错。真实实现经学习服务模型路由（T8、L0）、只用境内已备案模型，待 spec 待人确认 #4、#5，本工具不接；
   `RoutedTagModel` 是定下接口形状的桩（构造要求路由地址、令牌、打标器版本与模型版本，调用一律抛错，令牌只在私有字段里）。
   两层接口异步，流水线（`pipeline.ts`）与 `buildItems` 相应返回 Promise；题目逐道顺序处理，输出仍确定。

**合并与分级**（`merge.ts`，spec 待人确认 #6 默认）：去重；主考点取置信度最高的主考点候选（平手按 legacy 引用顺序）；
节点清单 = 主考点 + 规则层、模型层的其他节点（向量层只作召回），上限 3，超出截断并降 B；
**A 级 = 主考点上规则层主考点候选 ≥ 0.85，且向量或模型层也把它作主考点**（参与一致的层与版本记进 `tag_basis`）；其余 B 级进候选队列；没有任何候选的为“无处可标”。

> **注意：默认只跑规则层，按上面的口径一律 B 级——默认输出里 `item_tag` 为空，全部进候选。**
> 这是严格执行 spec 的结果，不是缺陷。教研试用时可加 `--rules-only-a`：只跑规则层时，规则直接命中 0.9 也算 A 级（开了别的层就不放宽）。
> 当前二次函数主题：默认 A 0 / B 34 / 无处可标 1；`--rules-only-a` 下 A 27（写 item_tag 19 题，另 8 题是六段未定的压轴专项）。

## 安全与数据

- legacy 在隔离子进程里加载（`src/extract-items.ts`）：`--permission` 只读 legacy、本工具与 kge-inventory 源码目录，不能写文件与起子进程，环境变量清空，
  导入前拿掉全局网络接口；输出按不可信数据逐项核对（`checkBank`）。沿用 KGE-01 的做法，Node 权限模型不管网络（KGE-01 待人确认 #7）。
- 题目文本只在内存里给向量层 / 模型层用，不写进任何产物；产物里只有 ID、节点、置信度、理由码与计数。
- 测试夹具全部合成；不含任何学生数据。

## 道法切片（冲刺 4-K，`src/daofa/`）

对应 `specs/kge-daofa-slice-sprint4/spec.md` 设计要点 1–5。只读输入、只生成文件，不连库、不联网、不调用任何模型；全部命令见 `src/daofa/main.ts` 开头。
**本仓只放模板、校验器与合成样例**；真实观点、页码、Rubric、时政由教研在内容仓填写（真实内容的交接清单见 lessons）。

| 位置 | 内容 |
|---|---|
| `templates/daofa-domain.yaml` | 观点地图模板：领域 → 大概念（主题层）→ 观点（`kind = viewpoint`），关联边、教材投影（只有版次、册、章、页码） |
| `templates/daofa-current-affairs.yaml` | 时政录入模板 |
| `samples/daofa/` | 合成样例（合成、非教材内容）：`law.yaml`（法律领域 2 个大概念、6 个观点、关联边、1 条显式前置边）、`pack-nodes.yaml`（合成内容包采分点挂接的 `MORAL.SYN.*` 节点）、`current-affairs.yaml`、`source-domains.txt`（虚构的 `.example` 主机） |
| `sql/export-scoring-points.sql` | 原子层导出（只读），`make zhixing-rubric-check` 的比对输入 |
| `tests/daofa/` | 单测与一次性库上的测试（`daofa-db.sh`、`page-backfill-demo.sh`、`rubric-check.sh`） |

```bash
cd tools/kge-import
node src/daofa/main.ts map --domain samples/daofa/law.yaml --domain samples/daofa/pack-nodes.yaml --version 0.0.1 --out /tmp/daofa-map
node src/daofa/main.ts rubric --pack ../../adapters/zhixing/content/pack.json --map /tmp/daofa-map --out /tmp/daofa-map
node src/daofa/main.ts affairs --entries samples/daofa/current-affairs.yaml --source-domains samples/daofa/source-domains.txt --map /tmp/daofa-map --out /tmp/daofa-map
node src/daofa/main.ts affairs-expiring ... --today 2026-09-30            # 即将到期（默认提前 30 天）与已过期，只列 ID
node src/daofa/main.ts page-sample --map /tmp/daofa-map --out checklist.csv  # 固定种子抽样，核验单不含原文
node src/daofa/main.ts page-backfill --map /tmp/daofa-map --checklist checklist.csv --backfill filled.csv \
  --approver <工号> --approved-on 2026-09-30 --out /tmp/daofa-backfill        # 低于 95% 的版次不生成标 verified 的语句，退出 1
node src/daofa/main.ts snapshot --map /tmp/daofa-map --pack ... --entries ... --source-domains ... --out snapshot.json  # 覆盖报表输入
```

- **观点地图**（`map`）：校验 ID（`MORAL.` 前缀，领域 2 段、大概念 3 段、观点 4 段）、观点必须挂在大概念下、任何层级不得出现原句类字段
  （`quote`、`original`、`phrases`、原句、原文……，decisions #5）、观点间只用 `related`（前置边须 `explicit_prerequisite: true` + `basis`，
  不能成环、不能是传递边）、合成样例须标明“合成、非教材内容”且不能与真实内容进同一版本。产物 `import.sql`（草稿地图版本，以 `starbuddy_kge` 写入，
  投影 `quote_text` 为空、`unverified`；大概念旧短编码进 `kge.id_migration`）、`import-report.json`、`snapshot.json`，同一输入逐字节相同。
  发布走已有的签字与 `kge.publish_map_version`（0019），本工具不发布。
- **合成挡板**：合成样例生成的 SQL 开头有 `DO $synthetic$` 挡板，只有会话里先 `SET starbuddy.allow_synthetic = on`（一次性测试库）才能执行。
- **采分点**（`rubric`、`rubric-check`）：内容包（`zhixing-content-pack/1`，用适配器同一套校验 `indexPack`）→ `kge.scoring_point`，带 `rubric_version`；
  只新增（`ON CONFLICT DO NOTHING` + 逐字段核对已有行，内容不同则整个事务失败）；一致性检查逐字段比对内容包与原子层导出，只报 ID 与字段名。
  `skill_node` 是适配器侧的能力视图挂接，不在原子层、不比对。
- **时政**（`affairs`）：来源须是白名单主机（离线按 `--source-domains`，执行时库里按 `policy.source_domain` 再核一次）、有效期必填且 ≤ 12 个月、
  只收自写摘要（摘要不入库，`kge.current_affair` 没有摘要列）；一律 `pending`。
- **页码核验**（`page-sample`、`page-backfill`）：每版次 20%、至少 20 条（不足全抽），`sha256(种子, 投影 ID)` 排序取前 k 条；回填文件在核验单上填
  `result,checker,checked_on,note`，核验单的列不许改；通过率 ≥ 95%（等于算过）的版次才能整体标 verified。
  已知限制：0011 的 `projection_verified_by` 要求 verified 必须有 `quote_text`，原句不入库时写不进 verified（`page-backfill-demo.sh` 演示；lessons 待人确认）。

`make kge-daofa-import-test`（并入 `kge-import-test` 与 `check`）、`make zhixing-rubric-check`。

## 语文切片（KGE-07 第一步，`src/chinese/`）

对应 `specs/kge07-chinese-slice/spec.md` 范围 1。只读输入、只生成文件，不连库；全程合成数据，不含篇目原文、教材注释与真实实词虚词清单。

```bash
node src/chinese/main.ts map --domain samples/chinese/reading.yaml --domain samples/chinese/accumulation.yaml --version 0.0.1 --out /tmp/chinese-map
```

- **模板** `templates/chinese-domain.yaml`：领域 → 主题 → 知识节点层；节点只收能力点 `ability_kp`（能力 × 文本类型 × 题型，标明在能力阶梯的哪一级）、
  素材 `material`、方法 `method`；能力阶梯（`ladders`）、文本类型、题型的编码表可在多个领域文件里重复写，定义必须一致。
- **只收合成样例**：`synthetic: false` 在法务与语文教研确认前一律拒绝；合成样例的名称、领域 ID 与每个自由文本字段
  （includes、excludes、examples、basis、册、课、印次）都须带“合成”标记。
- **边只用迁移 0011 已有的类型**：`progression`（层次递进，只连同一能力阶梯的相邻两级，且文本类型、题型相同）→ `prerequisite` + `strength = weak`；
  `prerequisite`（教研显式强前置，须 `explicit_prerequisite: true` + `basis`）→ `prerequisite` + `strong`；`related`、`confusable`（无方向）、`uses_method`（能力点 → 方法）同名。
  诊断与 `node.path` 只走强前置边，弱边只表达递进。
- **校验**：素材与方法不进前置图；前置图（强弱合在一起，与 `kge.validate_map_version` 同口径）无环、无传递冗余边；知识节点层无孤点；
  ID 符合节点 ID 规范且以 `CHN.` 开头；拒绝原文、注释类字段（`quote`、`original`、`原文`、`annotation`、`注释`……任何层级）；
  素材与方法不带认知层次；合成样例须 `synthetic: true`、notice 写明“合成、非教材内容”、名称带“合成”、领域 ID 第二段以 `SYN` 开头。
- **产物**：`import.sql`（草稿地图版本，`subject = wenli`，以 `starbuddy_kge` 写入，投影 `quote_text` 为空、`unverified`，合成样例带挡板）、
  `import-report.json`（计数、`expected_missing_boundary`、不计入必修的节点、能力阶梯格 `ability_grid`）、
  `snapshot.json`（`kge-chinese-snapshot/1`，覆盖报表 `tools/kge-coverage --subject wenli` 的输入）。同一输入逐字节相同。发布走已有路径，本工具不发布。

`make kge-chinese-import`（单测 + 一次性库上的 `tests/chinese/chinese-db.sh`）、`make kge-chinese-mutation`，两者都并入 `check`。
