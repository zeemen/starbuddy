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

## C 知识服务的 WeKnora 后端（2026-11-07，开关缺省关）
- 代码：`services/knowledge/knowledge/weknora/`（manifest.py 交付清单、filters.py 过滤策略、client.py 导入 tools/weknora-probe 的客户端、
  backend.py 相似题与 standard.search）；`service/tools.py` 的 item.similar 加 WeKnora 分支，`service/reader.py` 加 `items_by_ids`，
  `service/config.py` 加 `KNOWLEDGE_WEKNORA_BACKEND`（缺省 0）、`_MANIFEST`、`_TIMEOUT_MS`。开关关时不导入 WeKnora 客户端，原有 644 个测试不变地通过。
- ID 映射：WeKnora 文档 ID →（交付清单）中台内容 ID →（交付清单的 item_ref）题目引用 →（kge.item_tag）已发布、审核通过再核对。
  WeKnora 只给候选与排序，题目视图仍由 SQL 生成，与 SQL 实现同一份。
- 过滤：只实现“允许清单”（spec 决定记录第 4 条）；命中回来再核对是否在允许集合里，模拟 WeKnora 打开 ignore_filters 缺陷时也只出允许的文档。
  允许集合为空时不调 WeKnora（不退化成全库检索），超过 2000 个文档时报错不检索。
- 查询文本不进日志与异常（测试用 caplog 核对）；访问日志只多 `backend` 与 `weknora_dropped` 两个字段，且只在开关开时有。
- 测试：`tests/test_weknora_backend.py`（25 个，含库上 4 个）；变异 16 条进 `tests/mutations.sh`（12 条不需库、4 条库上）。

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
   - **已定（2026-11-07）**：全部按草案，见 spec.md 决定记录第 8 条。
4. **题目引用从哪里来**：ADR 16 第 3.4 节说 platform.item 的 source_ref 记 WeKnora 与中台内容 ID，但知识服务的只读角色读不到 platform.item
   （迁移 0026 只授 kge.*）。现在的做法：同步步骤（未做，随中台交付清单接口 ZT-08 一起）按 source_ref 对好题目引用，写进交付清单文件，
   知识服务只读这份文件（同页面快照）；命中的题仍经 kge.item_tag 核对。另一做法是新迁移给只读角色授权读 platform.item 的
   (subject, item_id, source_ref)——迁移与权限变更，必停。**建议先用清单文件**，等 ZT-08 定稿再看要不要改。
5. **条目上缺的维度怎么算**：试卷题、教辅往往没有出版社、版本、学期。现在把条目上缺的这三项当“不适用”（不排除），请求给了考试年份、地区、
   类型或节点时条目必须有且相等。是否合适要教研看（例如“苏科版八上”的请求是否该带出没有版本信息的南京中考题）。
6. **WeKnora 出错时 item.similar 怎么办**：现在退回 SQL 实现（结果与开关关时相同，访问日志记 `sql_fallback`）。另一做法是回 503
   UPSTREAM_UNAVAILABLE。退回 SQL 对学生无感，但会掩盖 WeKnora 故障，要靠日志与指标发现。**建议退回 SQL，并在开开关前加一个计数指标**。
7. **镜像**：deploy/docker/knowledge.Dockerfile 的白名单只有 services/knowledge/knowledge，没有 tools/weknora-probe。开关关时不需要；
   集群恢复、要在 staging 打开开关之前，随部署改动一起加（或把客户端挪进 packages/ 做成共享包）。
8. **item.search 没有改**：v0.9 的 item.search 只有结构化过滤、没有查询文本，WeKnora 帮不上忙，开关开时仍走 SQL。要“按文字搜题”，
   需要在 v0.10 给 item.search 加可选的 query 字段（契约变更），或直接用 standard.search（content_types 选 item）。**建议用 standard.search**，
   item.search 保持结构化过滤。
9. **相似题的查询文本**：用题目本身在 WeKnora 里的正文（`GET /knowledge/:id` 的 content）。真实 WeKnora 的这个接口是否返回正文未核对
   （技术验证 V11）；取不到正文时走 SQL（访问日志记 `sql_no_content`）。
10. **standard.search 何时对外**：已实现并按 v0.10 的 schema 测过，但服务端仍按 v0.9 建路由。v0.10 签字后，把知识服务的契约切到 v0.10、
    在 ToolCore 里接上 standard.search（调用方 bff、orchestrator），另开 PR。
11. **允许清单装不下相似题**：相似题的允许集合是“同学科的全部已交付题”，题库一大就会超过 2000 个文档 ID 的上限；现在超了就走 SQL
    （访问日志记 `sql_too_broad`），不回 500。真正解决要等技术验证 V07 定过滤策略（组合标签或按学科分库就不需要传 ID 列表）。
