# 架构决策记录（ADR）索引

结构：每份 ADR 按“背景 / 选项 / 决定 / 后果 / 待人确认”写，“决定”一栏在签字前写“待签字”。
命名：`NN-slug.md`。
签字：计划在开发计划 W2 周五评审，建议按下表“评审顺序”签。签字后另开 PR，把状态改成“已采纳”，写上签字人和日期。

| 编号 | 标题 | 状态 | 建议决策人 | 依赖 | 必停项 | 评审顺序 |
|---|---|---|---|---|---|---|
| [01](01-bff-and-learning-service.md) | BFF（TypeScript）与学科服务（FastAPI）分工 | 草稿，待签字 | 技术负责人 | — | — | 3 |
| [02](02-postgres-shared-db-schemas.md) | Postgres 共库分 schema | 草稿，待签字 | 技术负责人 + 资深工程师 | 09、10 | 是（migrations、RLS） | 4 |
| [03](03-explicit-state-machine-orchestration.md) | 编排用显式状态机，LangGraph 仅限复杂多步 | 草稿，待签字 | 技术负责人 | 01 | — | 5 |
| [04](04-model-routing-by-data-class.md) | 模型路由接口与供应商优先级（2026-09-28 增补：按任务类型路由） | 草稿，待签字 | 技术负责人 + 产品负责人 | 09 | 是（供应商、出境） | 2 |
| [05](05-event-table-partitioning.md) | 事件表分区策略 | 草稿，待签字 | Python 后端 | 02 | 是（migrations） | 6 |
| [06](06-metering-ledger-consistency.md) | 计量账本的一致性模型 | 草稿，待签字 | 资深工程师 | 01、02、05 | 是（计量） | 7 |
| [07](07-openmaic-dsl-scope-and-license.md) | 讲解 DSL 采用 OpenMAIC 子包的范围与许可证 | 草稿，待签字 | 技术负责人 + 合规顾问 | — | 是（依赖许可证） | 8 |
| [08](08-english-model-and-voice-chain.md) | 英语模型与语音链路的接入方式（原“英语保留 Gemini 链路”） | 草稿，待签字 | 产品负责人 | 04、09 | 是（供应商、出境） | 9 |
| [09](09-data-residency.md) | 数据驻留 | 草稿，待签字 | 产品负责人 + 技术负责人，合规顾问会签 | — | 是（出境） | 1 |
| [10](10-domestic-hosting-selection.md) | 境内托管选型 | 草稿，待签字 | 技术负责人 + 产品负责人 | 09 | 是（第三方服务） | 2 |

### 开发计划 v2.0 新增的 ADR（2026-09-27 登记，2026-09-28 起草，`specs/adr-11-15/`）

开发计划 v2.0 冲刺 0 W2 写的是“ADR 01–08 沿用 v1.0；新增 09 星图统一定义、10 编译层试点期用 Git、11 判断账本唯一写入方为洞察服务、12 Claude Code 任务包格式与人审点”。本仓库的 09、10 已经被“数据驻留”“境内托管”占用，并被多份 spec 引用。**已定（2026-09-27）：已有编号不动，新增四份顺延为 11–14**（`specs/plan-v2-alignment/spec.md` #2）。

| 编号 | v2.0 编号 | 标题 | 状态 | 必停项 |
|---|---|---|---|---|
| [11](11-star-map-unified-definition.md) | 09 | 星图统一定义（星 = 知识节点，能力坐标为能力视图） | 草稿，待签字 | 是（影响已合并的 3-H 星图与 `platform.skill_node` 的用法） |
| [12](12-compiled-pages-git-pilot.md) | 10 | 编译层试点期用 Git：主分支保护，教研只经网页审核 | 草稿，待签字 | 是（新增仓库与 CI） |
| [13](13-claim-ledger-single-writer.md) | 11 | 判断账本唯一写入方为洞察服务 | 草稿，待签字（随 SIE-00 与迁移 0010） | 是（schema、权限） |
| [14](14-task-packages-and-human-review.md) | 12 | Claude Code 任务包格式与人审点 | 草稿，待签字 | — |
| [15](15-input-channels-and-answer-evidence.md) | — | 输入通道与作答证据分级（技术蓝图 v2.1） | 草稿，待签字 | 是（契约、掌握口径、第三方服务） |

另外，v2.0 要求的 schema 从一个（platform.*）变成三个（platform.* / kge.* / insight.*），ADR 02“共库分 schema”需要补一节说明个人层表放哪一个 schema（#3：默认留在 platform.*，W2 Schema v0.9 评审时定）。kge 一节已作为草稿增补在 ADR 02 末尾（specs/kge-schema-v0.9），随迁移 0011 / 0012 一起评审。

## 依赖关系

```
09 数据驻留
├── 04 模型路由 ── 08 英语链路
├── 10 境内托管 ── 02 共库分 schema ── 05 事件分区 ── 06 计量账本
└── 08 英语链路
01 服务分工 ── 03 编排状态机
07 OpenMAIC（独立）
13 判断账本 ── 11 星图统一定义
04、09、10、11、13 ── 15 输入通道与作答证据
09、10 ── 12 编译层 Git 试点
14 任务包与人审点（独立）
```

## 起草依据
- 完整方案 v1.0（`docs/archive/完整方案-v1.0（已被v1.1取代）.html`）第五、六、七、十一章
- 开发计划 阶段 0–1（`docs/archive/开发计划-阶段0-1-v1.0（已被v2.0取代）.html`）冲刺 0 W2
- 四科盘点与决定（`specs/inventory-{aya,wenli,zhixing,echo}/`，负责人已于 2026-09-25 确认）
- 起草计划：`specs/adr-01-10/spec.md`
- 2026-09-27 起的上游依据：完整方案 v1.1、开发计划 v2.0、KGE V1.1、SIE V1.1（`docs/plan/`）。01–10 签字前需按它们复核，差异见 `specs/plan-v2-alignment/spec.md`。
