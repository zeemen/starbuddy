# KGE-02 原子与打标 · lessons

## 迁移 0024（主会话）
- `kge.item_tag` 加打标来源、置信度、打标器版本、模型版本四列，约束“三者同有同无、模型层必须带模型版本、其他层不能带”。
- `kge.tag_candidate`：公共层候选表，只有知识服务能以“待处理”写入，不能改结论、不能删；教研只经 `kge.decide_tag_candidate`（只授 review）接受或驳回。
  草稿版本：主考点候选在该题没有打标时写回（来源 human、置信度 1），次要节点追加到已有打标（上限 3 个）；已发布版本只记结论（打标变更走 0019 审核记录）。
- **与 spec 的偏差**：处理人不能记在公共层（80 攻击面断言：kge 的 uuid 列只能是主键或指向本 schema 的外键），改为平台层签字表
  `platform.tag_candidate_signoff`（沿用 0018 签字表的做法：只追加、没有策略、只有属主 authz 读写）。
- 加列会改变 `kge.map_version_digest` 的输入，已有签字记录的摘要在演练库上会对不上（本迁移之前没有已发布的阿鸦版本）。
- 测试：`db/tests/79f_kge_tagging.sql`；变异 11 条；76 号测试的 kge 表数 14 → 15；`run.sh` 的 kge 角色断言库跳过 0024（依赖 0011）。
- 坑：同一条 SQL 里多次调用有副作用的函数，求值顺序不确定，测试要分开写。

## 题库导入与打标三层（子代理，tools/kge-import）
范围：spec 设计要点 1（题库导入）、2（打标三层）、6（多主题支持）的工具部分。迁移 0024、覆盖矩阵、评测脚本、Makefile 由别的任务并行做。

## 交付
- 新包 `tools/kge-import/`：`src/main.ts` 命令行；`src/extract-items.ts` 隔离子进程加载 legacy；`src/items/build.ts` 纯函数生成三件产物；
  `src/tagging/`（规则层、向量层与假嵌入、模型层与假模型、合并分级、流水线）；`config/aya-chapter-segments.yaml` 章节六段映射表（待教研核对）。
- 种子 `db/seeds/kge/aya-items-0.1.0/`：`items.sql`、`worklist.md`、`tagging-report.json`（格式 `kge-tagging-report/1`，TS 类型在 `src/items/report-types.ts`）。
- 单测 43 条（node --test，合成夹具）；有 legacy 副本时核对重新生成与仓库种子逐字节相同，没有时打印“跳过”。

## 结果（默认只跑规则层，地图版本 aya 0.1.0 = 二次函数 24 个节点）
- 721 题：已导入主题（fun）里 35 题（章节题 24 + 压轴专项 DP / QF 11）；未导入主题 686 题（31 个章节，只计数与列 ID）。
- 35 题：A 0、B 34、无处可标 1（`Q-FUN-U07-P-004`，主测节点都在未导入的“一元二次方程”章节）；item_tag 0 题、tag_candidate 69 行、题组 2 个（dp、qf）。
- B 级原因：没有另一层一致 34（其中规则只推断 0.6 的 6 题、节点超 3 个的 1 题 `Q-SPR-DP-C1`）。
- `--rules-only-a`：A 27，写 item_tag 19 题；另 8 道压轴专项 A 级但六段未定，只写候选（grade A）。
- `--vector --model`（假实现，只证明管道通）：A 19 / B 16，item_tag 13，候选 225 行——数字没有任何质量含义。
- 六段：693 题按章节推出（七至九年级 32 章），28 道压轴专项推不出，进工作单。
- 自然语言引用 97 处全部解析（与 KGE-00 一致），全部在 `preNodes`，都对不上地图节点；工作单给出 legacy 同名节点建议 28 处、名称相近建议 31 处、无任何建议 38 处。

## 与 spec / 任务说明的差异
1. **`tools/kge-import` 原先不存在**：KGE-01 的导入器实际在 `tools/kge-inventory/src/import/`（KGE-01 lessons 差异 2）。按“只改 tools/kge-import/”新建了这个包，
   以相对路径复用 kge-inventory 的 legacy 加载器、解析钩子与 `stableUuid`，并借用它的 `node_modules`（`tsc`、`@types/node`，tsconfig 里 `typeRoots` 指过去），不另装依赖。
   Makefile 目标由主会话接入（`kge-items-test`）。
2. **自然语言引用不产生打标候选**：97 处全部在 `preNodes`（前置节点，错误追溯方向），不是题目考查的节点。规则层对它们只解析（地图同名 / legacy 同名 / 相近），
   进工作单；只有出现在 `mainNodes` / `auxNodes` 里的自然语言才会按同名产生推断候选（现有数据里没有）。
3. **“直接命中 0.9 / 推断 0.6”的划分**：直接命中 = 旧编码经迁移表落到已导入节点；推断 = 未导入节点退到知识簇主题、自然语言同名。名称同名不算直接命中（名称不是标识）。
4. **item_tag 字段按任务说明而非 spec 原文**：spec 写的 `source = legacy_aya`、审核状态 `draft` 在 0011 的 CHECK 里不存在，按任务说明用 `source_kind = original`、
   `tag_review_status = pending`。
5. **退到主题节点作候选**：未导入的 P / C 节点退到所在知识簇的**主题**节点（`MATH.FUNC.FUN_GRA` 这类），主题节点在 `kge.knowledge_node` 里、外键过得去，
   但把题目挂到主题而不是知识节点是否可以接受，待教研确认（见待人确认 2）。同一题已有该主题下节点被直接命中时，主题候选丢掉。
6. **题组只来自压轴专项**：legacy 章节题只有“单元”（U01…）和诊断模块（DiagModule），都不是题目族；只有 `SPRINT_CATEGORIES`（母题切片 + 变式 + 挑战）是题组。
   `Q-EQU-U03-R-004A` 这类带字母后缀的 3 道题像变式但没有对应母题，未作题组。
7. **A 级但六段未定、或未登记**：不写 item_tag，只把候选以 grade A 写进候选表并进工作单（验收第 1 条要求“能定年级与教材的才生成 item_tag”）。
   item_tag 本身不存六段（经 `platform.curriculum_version` 继承），这条限制是否要保留待确认（见待人确认 4）。
8. **报告体量**：tagging-report.json 约 330 KB（721 题每题一行，含未导入主题的题与每题六段）；覆盖矩阵需要的字段都在里面。
9. **YAML**：任务要求映射表用 YAML，工具包没有 YAML 依赖，写了一个只认块状映射与标量的最小解析器（`src/yaml.ts`），不引入依赖（规则 10）。

## 经验
- 默认口径下（只跑规则层就一律 B）默认种子里 item_tag 为空，教研看到的全是候选：真实向量 / 模型层接入前，A 级实际上只能靠 `--rules-only-a` 或人工接受候选产生。
- legacy 的节点编码前缀不等于章节（`SCL`、`MEA` 等属于 sim / trg），章节归属必须按节点所在专题查，不能按编码前缀猜；题目 ID 前缀则与专题一一对应（721 题核对无例外）。
- 压轴专项的主测节点常是能力子节点 C 与易错点 P（KGE-01 未导入），二次函数专项 11 题里有 1 题合并后超过 3 个节点。
- 生成物确定性：题组 ID 由“地图版本号 + code”派生；打乱题目与专题顺序输出不变（有单测）。

## 待人确认
1. **A/B 级口径**（spec 待人确认 #6）：只跑规则层时一律 B，默认种子 item_tag 为空。是否在真实向量 / 模型层接入前允许 `--rules-only-a`（规则直接命中 0.9 即 A）试用？
2. **退到主题节点**：未导入的 P / C 节点，是退到知识簇主题节点作 0.6 候选（现做法），还是不产生候选、直接进工作单？P 类映射定下（KGE-01 待人确认 #1）后大部分会变成直接命中。
3. **多个主测节点**：legacy 的 `mainNodes` 常有 2 个，现取置信度最高、平手按 legacy 顺序的第一个作主考点，其余作节点清单里的次要节点。是否需要教研逐题定主考点？
4. **A 级但六段未定的压轴专项**（8 题）：是否仍写 item_tag（item_tag 不存六段），还是等教研定年级与教材再写？
5. **六段映射表**（spec 待人确认 #2）：`config/aya-chapter-segments.yaml` 的教材版本（统一 `suke-2024`）、`exam_year`（整包沿用 2027，对七、八年级内容是否按届别区分）待教研核对。
6. **自然语言前置的映射**：97 处前置的节点映射（工作单列了 legacy 同名 / 相近建议）由教研确认后，是写回 legacy 替代品（内容包）还是进 KGE 的前置边，与 KGE-01 待人确认 #2 一起定。
7. **小题节点上限对压轴大题是否适用**：现对所有题一律上限 3；压轴综合题（C1）是否放宽。
8. ~~Makefile 接入~~：已加 `kge-items-test`（类型检查 + 单测 + 库上验证），并入 `make check`。
9. ~~库上验证~~：已加 `tools/kge-import/tests/items-db.sh`：全部迁移 → 阿鸦内容登记 → KGE-01 地图种子 → items.sql，候选 69 行、打标 0 题；
   有 legacy 时再以 `--rules-only-a` 重新生成并执行，写入 19 题打标草稿，全部带来源、版本、置信度，节点 1–3 个。


## 覆盖矩阵报表与评测脚本（子代理，tools/kge-coverage、evals/kge-tagging）
- `tools/kge-coverage`：节点 × 认知层次矩阵、空节点、节点数超 3 标注、各层与 A/B 占比，确定性输出；只读 `sql/coverage.sql` 在一次性库上与报表逐字节一致（`tests/coverage-db.sh`）。
  `--scope all|item_tag`（默认 all，含 B 级候选的主考点）；`--map` 可给多次。
- `evals/kge-tagging`：`kappa.py`（Cohen's kappa、Jaccard、分歧清单）、`top1.py`（Top-1、Top-3、无处可标率，门槛只在 `--enforce` 时决定退出码）、
  `run_holdout.sh`（输入由执行人传入、在仓库目录内拒绝执行、只打印汇总）；30 条单测，kappa 与手算一致。
- 偏差：`evals/kge-tagging/gold/README.md` 没建（仓库 hook 拦截对 `evals/*/gold/` 的写入，没有绕过），金标格式写在上级 README；
  打标报告的实际格式是 `kge-tagging-report/1`，两边都按实际格式读；报告里没有认知层次，按导入器的同一规则推（目前全落在“未标”）。
- 实跑：二次函数 24 个节点里 15 个有主考点题（62.5%，含 B 级）；6 道题主考点落在主题层，列在“需要核对的题”里。
- 待人确认：
  1. 覆盖门槛口径：“有主考点题的节点 / 有效知识节点” = 100%，是否等价于 KGE :747 的“课标条目承接率 / 教材映射率”。
  2. 报表默认是否含 B 级候选（`--scope all`）。
  3. 打标报告是否直接带 `cognitive_level`。
  4. 无处可标率的分母与计入范围（KGE :748）。

## 出境口径
- spec 待人确认 #5（KGE V1.1 :664“离线构建可用 Claude”与 2026-09-28“一律不出境”冲突）尚未单独答复；本任务模型层只有假实现，不受影响；
  真实模型接入前必须定。
