# 架构决策记录（ADR）索引

结构：每份 ADR 按“背景 / 选项 / 决定 / 后果 / 待人确认”写，“决定”一栏在签字前写“待签字”。
命名：`NN-slug.md`。
签字：计划在开发计划 W2 周五评审，建议按下表“评审顺序”签。签字后另开 PR，把状态改成“已采纳”，写上签字人和日期。

| 编号 | 标题 | 状态 | 建议决策人 | 依赖 | 必停项 | 评审顺序 |
|---|---|---|---|---|---|---|
| [01](01-bff-and-learning-service.md) | BFF（TypeScript）与学科服务（FastAPI）分工 | 草稿，待签字 | 技术负责人 | — | — | 3 |
| [02](02-postgres-shared-db-schemas.md) | Postgres 共库分 schema | 草稿，待签字 | 技术负责人 + 资深工程师 | 09、10 | 是（migrations、RLS） | 4 |
| [03](03-explicit-state-machine-orchestration.md) | 编排用显式状态机，LangGraph 仅限复杂多步 | 草稿，待签字 | 技术负责人 | 01 | — | 5 |
| [04](04-model-routing-by-data-class.md) | 模型路由接口与供应商优先级 | 草稿，待签字 | 技术负责人 + 产品负责人 | 09 | 是（供应商、出境） | 2 |
| [05](05-event-table-partitioning.md) | 事件表分区策略 | 草稿，待签字 | Python 后端 | 02 | 是（migrations） | 6 |
| [06](06-metering-ledger-consistency.md) | 计量账本的一致性模型 | 草稿，待签字 | 资深工程师 | 01、02、05 | 是（计量） | 7 |
| [07](07-openmaic-dsl-scope-and-license.md) | 讲解 DSL 采用 OpenMAIC 子包的范围与许可证 | 草稿，待签字 | 技术负责人 + 合规顾问 | — | 是（依赖许可证） | 8 |
| [08](08-english-model-and-voice-chain.md) | 英语模型与语音链路的接入方式（原“英语保留 Gemini 链路”） | 草稿，待签字 | 产品负责人 | 04、09 | 是（供应商、出境） | 9 |
| [09](09-data-residency.md) | 数据驻留 | 草稿，待签字 | 产品负责人 + 技术负责人，合规顾问会签 | — | 是（出境） | 1 |
| [10](10-domestic-hosting-selection.md) | 境内托管选型 | 草稿，待签字 | 技术负责人 + 产品负责人 | 09 | 是（第三方服务） | 2 |

## 依赖关系

```
09 数据驻留
├── 04 模型路由 ── 08 英语链路
├── 10 境内托管 ── 02 共库分 schema ── 05 事件分区 ── 06 计量账本
└── 08 英语链路
01 服务分工 ── 03 编排状态机
07 OpenMAIC（独立）
```

## 起草依据
- 完整方案 v1.0（`docs/plan/完整方案-v1.0.html`）第五、六、七、十一章
- 开发计划 阶段 0–1（`docs/plan/开发计划-阶段0-1.html`）冲刺 0 W2
- 四科盘点与决定（`specs/inventory-{aya,wenli,zhixing,echo}/`，负责人已于 2026-09-25 确认）
- 起草计划：`specs/adr-01-10/spec.md`
