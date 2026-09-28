# KGE-01 骨架导入：阿鸦“二次函数”主题节点初稿入库

状态：**草稿 v0.1，待确认**（2026-09-28）
任务包：开发计划 v2.0 冲刺 1（W3–W4）K 流“KGE-01 骨架：版本、节点、边、投影、迁移表；发布前校验（环、传递边、孤点、包含 / 不包含必填）；
导入阿鸦知识图谱”，验收“函数主题节点初稿入库，校验全绿”。
关联：
- `specs/kge-schema-v0.9/`（迁移 0011：`kge.*` 表、`kge.validate_map_version`、`kge.publish_map_version`，已交付 schema 部分，“导入与服务是 KGE-01”）；
- `specs/kge-00-asset-inventory/`（阿鸦 959 节点、自然语言前置 156 条、传递边 74 条、环 1 个、孤点 4 个；九年级“函数”45 个节点）；
- `docs/design/kge/id-and-segments.md`（节点 ID 规则、旧编码走 `kge.id_migration`）；ADR 11（星 = 知识节点）；
- `tools/kge-inventory/`（已有的 legacy 只读加载器与图算法，复用）；CLAUDE.md 规则 1、2、13。

> 不改迁移、不连任何数据库：导入器只生成 SQL 文件，由人在演练库执行（CLAUDE.md 规则 9、必停项“db/migrations”不涉及）。
> 节点类型映射、边界字段、易错点的错因类型属于知识结构与教研口径，写进“待人确认”。

## 目标
把阿鸦九下第 5 章“二次函数”（`legacy/aya/content/chapters/fun/nodes.ts`，45 个节点）转成一个**草稿地图版本**：节点、前置边、旧编码迁移、能力视图挂接齐全，
在库里跑 `kge.validate_map_version` 时**结构类检查（环、传递边、孤点、悬空跨版本边）全绿**；教研要补的内容（包含 / 不包含 / 题例 / 认知层次、易错点错因）
以工作单形式列出，补齐之后边界检查也能全绿、可以经审核台发布。

## 范围
### 做
1. **导入器 `tools/kge-import`**（TypeScript，复用 `tools/kge-inventory` 的 legacy 加载器；只读 `legacy/aya`）：
   - 输入：主题代码（默认 `fun`）、地图版本号（默认 `0.1.0`）、可选的**教研覆盖文件**（YAML：节点改名、类型、包含 / 不包含 / 题例 / 认知层次、易错点错因，缺省为空）；
   - 输出三件，写到 `db/seeds/kge/aya-<主题>-<版本>/`：
     - `import.sql`：一个事务里建草稿 `kge.map_version`（`aya`，`standard_year = 2022`）、领域 / 主题 / 知识节点、前置边、`kge.id_migration`（`rename`）、`kge.skill_link`（权重 1）；
     - `worklist.md`：教研工作单（见第 3 条），每行带旧编码、名称、`core` 原文、缺什么；
     - `import-report.json`：计数与被丢弃 / 未连接的条目（供核对，不含任何学生数据）；
   - 同一输入两次运行产出逐字节相同（确定性），便于评审看差异。
2. **映射规则（默认，待人确认）**：
   - ID：领域 `MATH.FUNC`；主题按知识簇 `FUN-CON` → `MATH.FUNC.CON`（7 个簇）；节点按旧编码机械生成 `MATH.FUNC.CON.K_FUN_0901`，
     旧编码登记进 `kge.id_migration`；语义化命名（如 `VERTEX_FORM`）由教研在覆盖文件里给，给了就用；
   - 类型：`K` → `kp`，`S` → `ability_kp`，`M` → `method`，`P` → `pitfall`（**必须**有覆盖文件给的错因大类，否则不导入、进工作单）；
     `C`（能力子节点，二次函数里 7 个）不作知识节点，只进工作单作为能力视图候选（ADR 11：能力坐标是能力视图）；
   - 前置边：只连能解析为本主题内节点 ID 的前置；指向本主题外节点的前置、自然语言前置、悬空前置不连，全部进工作单（KGE-00 待人确认 #6：不自动猜映射）；
     **传递边丢弃**（KGE 规定只连直接前置），丢弃清单进报告；环不自动拆，进报告并让导入失败（需教研决定断哪条边）；
   - `name` 取 legacy 名称；`includes` / `excludes` / `examples` / `cognitive_levels` 只取覆盖文件，**不从 `core` 自动推**（`core` 不是边界说明，KGE-00 结论）；
   - 能力视图挂接：每个导入的节点挂到同编码的 `platform.skill_node`（阿鸦已登记的 959 个 local_id），权重 1。
3. **教研工作单**：按节点列出缺失的包含、不包含、典型题例、认知层次；易错点缺错因大类；主题外前置与自然语言前置的候选（只列原文，不给候选节点）；
   能力子节点；被丢弃的传递边；孤点（导入后没有任何边的节点）。
4. **测试**：
   - 导入器单测：映射规则每一条的正反例、确定性、覆盖文件生效、环时失败；
   - 数据库集成测试：在一次性库上执行生成的 `import.sql`，跑 `kge.validate_map_version`，断言**只剩** `missing_boundary`（与工作单条数一致），
     没有 `cycle`、`transitive_edge`、`orphan`；再用一份合成的“补齐覆盖文件”生成、执行，断言校验全绿、`kge.publish_map_version` 能发布；
   - `make kge-import-test` 接入 `make check`。

### 不做
- 其余 31 个专题与九年级以外（KGE-02 起逐步扩）；题目导入与打标、`kge.item_tag`（KGE-02）；教材投影（需要教材页码核对，人的工作）；
- 审核台 v0（G 流，另起 spec）；星图改用知识节点（ADR 11 签字后另开）；
- 不从 `core` 或名称自动生成包含 / 不包含 / 认知层次；不自动映射自然语言前置；不在任何共享库执行 SQL。

## 验收标准（可测试）
- [ ] `tools/kge-import` 对 `fun` 生成 `import.sql`、`worklist.md`、`import-report.json`；连续两次运行产出一致。
- [ ] 一次性库执行 `import.sql` 成功；`validate_map_version` 无 `cycle` / `transitive_edge` / `orphan` / `stale_cross_edge`；剩余 `missing_boundary` 与工作单一一对应。
- [ ] 合成的补齐覆盖文件生成的版本校验全绿，能经 `kge.publish_map_version` 发布。
- [ ] 旧编码 → 新 ID 全部登记在 `kge.id_migration`；每个导入节点都有 `kge.skill_link`。
- [ ] 映射规则单测（类型、边、ID、覆盖文件、环失败、确定性）全绿；严格 `make check` 通过。

## 涉及的必停项
- migrations / RLS / 计量 / contracts / 人设 / 第三方：否（只生成种子 SQL，不改迁移，不执行到共享库）；
- 知识结构：是（节点类型映射、只连主题内前置、丢弃传递边的口径），PR 首行写 [NEEDS-HUMAN] 请教研确认。

## 依赖与输入
- 迁移 0011（草稿、由人执行）；`platform.skill_node` 已登记阿鸦节点（`adapters/aya/scripts/register-content.ts`）。
- `legacy/aya` 只读。

## 待人确认
1. 首个主题选“二次函数”（`fun`，45 节点，默认），还是把一次函数、反比例函数一并导入成“函数”领域？
2. 类型映射 `K→kp`、`S→ability_kp`、`M→method`、`P→pitfall`（默认）是否成立？`C` 能力子节点不作知识节点（默认）？
3. 节点 ID 先用旧编码机械生成（默认，语义化命名由教研在覆盖文件里给），还是导入前由教研先给出全部语义化 ID？
4. 主题外前置（例如指向八年级一次函数的节点）不连边、只进工作单（默认），还是导入时连成跨主题边（要求对方节点也在同一地图版本）？
5. 易错点没有错因大类就不导入（默认）；是否允许先用题目陷阱码的“根因”自动推一个候选，交教研确认？
