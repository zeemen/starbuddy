# KGE-12 · lessons

## A 节点表导出（2026-11-06）
- 实现：`services/knowledge/knowledge/export/node_table.py`（`python -m knowledge.export.node_table`），测试 `tests/test_export_node_table.py`，
  变异 5 条加进 `services/knowledge/tests/mutations.sh`（未核对投影、撤下节点、教材原句、出版社缺编码、草稿版本）。
- 与需求单附录 A.2 的差异已改进需求单：多了 `level`、`kind`、`taggable`、`includes`、`excludes`、`cognitive_levels`（中台打标时用来区分相近节点）；
  `node_type` 改为 `level` + `kind`（地图本来的字段）；`aliases` 为空、`publisher` 靠对照表（决定记录第 6 条）。
- `parent_node_id` 指向已撤下的节点时输出 null，不输出悬空引用。

## B 契约 v0.10 候选版（2026-11-07，必停：契约变更，待签字）
- 文件：`packages/contracts/openapi/knowledge-service.v0.10.yaml`。v0.9 不动；v0.10 = v0.9 逐字复制 + 版本号换成 0.10.0 + `standard.search`
  一条路径、10 个 schema、3 个示例。“只做增量”由 `packages/contracts/scripts/check_knowledge_v010.py` 逐项断言（v0.9 的每个 schema、路径、
  示例、components.responses / securitySchemes、security、servers 在把 contract_version 换成 0.10.0 之后逐字相等）。
- 反例 41 个（`tests/counterexamples_v010.py`）：六个必填过滤维度各缺一个、上下文带学生标识、数据级别 L1、学习服务调用、命中夹带答案 / 解析 /
  学生标识、命中缺中台内容 ID / WeKnora ID / 来源、公共层结果引用学生证据、信封缺溯源字段等；变异 38 个（`V010_MUTATIONS`）。
- `source_version` 的类别沿用平台的封闭清单（rubric / verifier / prompt / items / algo / graph），知识库版本写成
  `items:<知识库名>@<版本>`，不新开类别（新开要改 plugin-api 的 SourceVersion，破坏性更大）。
- `item.search`、`item.similar` 在 v0.10 里一字不改（决定记录第 3 条：仍只返回引用与标签）；“相似题的查询方式”等可选字段等 C 做完、
  知道 WeKnora 实际需要什么再加，现在不预先加。
- 服务端仍按 v0.9 运行（`services/knowledge/knowledge/service/contract.py` 读 v0.9）；v0.10 签字前不接进服务，C 的 WeKnora 后端开关默认关。

## 待人确认
1. **ID 迁移表怎么导出**：迁移记录在 `kge.id_migration`，不在公共层快照里；知识服务的只读角色 `starbuddy_kge_read` 没有这张表的
   SELECT 权限，而且只能看到已发布的那一个版本（看不到旧版本）。可选：
   - (a) 新迁移给只读角色授权读 `kge.id_migration`（限已发布目标版本）——迁移与权限变更，必停；
   - (b) 在审核台的发布流程里，把 ID 迁移记录随地图快照一起导出（快照格式加一段），导出工具只读快照；
   - (c) **（建议）先不做**：每科目前只有一个已发布版本，ID 迁移表只在地图升版时才需要；到第一次升版时按 (b) 做。
   - **已定（2026-11-07）**：按建议 (c)，见 spec.md 决定记录第 7 条。
2. 出版社对照表（版本编码 → 出版社全称）由教研提供，放哪里、谁维护（建议跟地图快照一起放在页面仓库或审核台导出目录）。
3. **v0.10 草案里我先定了、需要签字时确认的取值**（都写在契约里，改起来只动 v0.10）：
   - `standard.search` 只给 BFF 与编排层调（`caller` ∈ {bff, orchestrator}），学习服务不调——学习服务的再练选题仍走 `item.search` / `item.similar`；
   - 年级 7–12（初一到高三），学期 `first` / `second`；省份、出版社、地区用中台交付元数据里的小写编码（`StandardCode`），版本沿用 v0.9 的 `Edition`；
   - 考试类型枚举 `zhongkao`、`gaokao`、`midterm`、`final`、`other`；
   - 内容类型六种：教材小节、知识点、题目、试卷、参考资料（`reference`）、编译页（需求单附录 A.1 的 content_type 加上编译页）；
   - `top_k` 1–20，缺省 5；查询文本至多 500 字；命中正文至多 4000 字。
