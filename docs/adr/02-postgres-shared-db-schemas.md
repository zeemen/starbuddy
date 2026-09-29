# ADR 02 · Postgres 共库分 schema

- 状态：**草稿，待签字**
- 日期：2026-09-25
- 决策人：待定（建议：技术负责人 + 资深工程师）
- 关联：完整方案 v1.0 第 5.5 节末行、第七章；开发计划 W2（原标题“Supabase 共库分 schema”）；ADR 01、05、06、09、10
- 来源：四份盘点 §3（数据库）；ADR 09 §4“需要一起决定的事项”

> [NEEDS-HUMAN] 本 ADR 决定 `db/migrations` 的组织方式和 RLS 的归属，属于 CLAUDE.md 规定的必停项。

---

## 1. 背景

### 1.1 总纲的设定
完整方案 5.5：“先共库分 schema（`platform.*` 与 `wenli.* / aya.* / zhixing.* / echo.*`），家庭关系与 RLS 由平台 schema 统一；后期按瓶颈拆分。”首期写的是“仍在 Supabase”。

### 1.2 ADR 09 带来的变化
ADR 09 建议 pilot 和生产全链路境内。Supabase 托管版没有中国大陆区域（待核实），所以“Supabase”不能再作为 pilot 和生产的前提。本 ADR 的标题因此从“Supabase 共库分 schema”改为“Postgres 共库分 schema”。托管方式由 ADR 10 决定。

### 1.3 盘点发现

| 学科 | 数据库现状 |
|---|---|
| 文砺 | Supabase，35 个迁移，48 张表，全部开 RLS；**和知行共用同一个 Supabase 项目**；有 `guardians` 邀请码绑定，结构接近 `guardian_student_link`，但 `guardians_guardian_rw` 的 with check 没约束 student_id，可能绕过绑定 |
| 知行 | 寄宿在文砺库里（迁移 0033–0035）；判分和能力行学生可写（违反 CLAUDE.md 第 3 条）；学生可以把自己出的题改成“已通过” |
| 阿鸦 | Supabase，2 个手工 SQL，**没有迁移工具**；代码只用 `profiles` 一张表，存整份 JSON；没有家长实体 |
| 开口成章 | **没有数据库**，全部在浏览器 localStorage |

- 四科都用 `public` schema，没有一个用了学科命名空间。
- 四科都**没有真实用户**（四份 decisions），所以没有需要迁移的生产数据。

---

## 2. 选项

### 选项 A：单个 Postgres，平台和四科各一个 schema
- `platform.*`：家庭关系、学习者模型、内容版本、权益与计量。
- `wenli.* / aya.* / zhixing.* / echo.*`：各科私有表（游戏化数值、学科专用缓存等），**不能存学生画像的第二份真相**。
- RLS 由平台统一定义；各科 schema 的表如果含学生数据，也必须引用 `platform` 的授权函数。

**优点**：跨科查询和事务简单；一套备份和权限体系；与总纲一致。
**缺点**：一个库的故障影响所有学科；各科迁移要在同一条迁移序列里协调。

### 选项 B：平台一个库，每科一个库
**优点**：隔离好。
**缺点**：跨科报表和星图需要跨库；家庭授权要在多个库里重复实现，RLS 容易不一致。阶段 0 的规模（试点约 100 户，内测约 1,000 户）完全不需要这种隔离。**不推荐。**

### 选项 C：沿用四科现有的 Supabase 项目，平台通过 API 汇总
违反“学生画像唯一真相源”（CLAUDE.md 第 2 条），并且阿鸦、开口成章根本没有可用的库。**不推荐。**

---

## 3. 决定

**待签字。**

**建议采纳选项 A**，并加上以下约束：

1. **环境与托管按 ADR 09 分开**：dev 和 staging 可以用 Supabase 托管版，只放合成数据；pilot 和生产用境内 Postgres（具体选型见 ADR 10）。迁移脚本必须**与托管无关**：只用标准 Postgres 能力，不依赖 Supabase 专有的 schema（如 `auth.*`、`storage.*`）。如果要用，先在 ADR 10 里确认境内托管能提供对应能力。
2. **迁移只有一条序列**：全部放在 `db/migrations/`，按编号顺序执行，平台和各科共用。每个迁移只能改一个 schema，文件名带 schema 前缀。
3. **RLS 归平台**：家长访问只能通过 `platform.guardian_student_link`（CLAUDE.md 第 5 条）；各科 schema 里含学生数据的表，读权限都必须调用平台的授权函数。任何 RLS 变更都要附测试。
4. **写权限收紧**：判分、能力、掌握度类的表，学生角色只读，由服务端角色写入（修正知行现有的 owner `for all`）。
5. **四科既有的表不整体迁移**：没有真实用户，所以不做数据迁移。各科私有 schema 按适配器设计文档**重新建**，只保留确实需要的表。
6. **事件表的分区策略**见 ADR 05；**计量账本表**见 ADR 06。

---

## 4. 后果

### 正面
- 家庭授权和 RLS 只有一处实现，渗透测试（开发计划冲刺 3）只需要覆盖一套。
- 迁移与托管解耦，ADR 10 的选型结果不会返工 schema。

### 代价
- 不能直接用 Supabase Auth。认证方案要单独决定：自建、境内托管的 Auth，或 BFF 自己管。这件事并入 ADR 10。
- 文砺、知行现有的 48 张表不能照搬，需要按平台 schema 重新设计。
- 各科的迁移都要过资深工程师审查（必停项）。

---

## 5. 待人确认

1. 认证方案在 ADR 10 里决定，还是另起一份 ADR？
2. 各科私有 schema 的表，由各科负责人提、技术负责人批，还是统一由平台组设计？
3. 文砺 `guardians` 表的 RLS 漏洞（with check 没约束 student_id）只存在于 legacy 里，平台不会沿用。是否还需要在 legacy 里修？（legacy 只读，修复要去原仓库）

---

## 增补：kge schema（草稿，2026-09-27，specs/kge-schema-v0.9）

- **新增 `kge` schema**：放知识与成长引擎的公共知识层（地图版本、骨架、投影、原子、成长治理）。它与 `platform`、`insight` 同库，理由与本 ADR 选项 A 相同：发布流程要在同一事务里核对题目登记（`platform.item`）与能力视图（`platform.skill_node`）。
- **不含学生数据**：公共层没有学生键，外键只指向 `kge` 自身与 platform 的内容登记，`db/tests/80_attack_surface.sql` 断言（CLAUDE.md 规则 13）。个人层表（证据、作答、掌握）仍在 `platform.*`（对照文档 #3）。
- **一个迁移只改一个 schema（第 2 条）**：`0011_kge__knowledge_map` 只建 kge；`platform.curriculum_version.map_version_id` 单独放在 `0012_platform__curriculum_map_version`。
- **写入方**：新增数据库角色 `starbuddy_kge`，是公共层的唯一写入方，与其他应用角色互斥（0011 断言）；没有任何角色能删除公共层的行。
- 待签字：随 0011 / 0012 的 PR 一起评审（必停项）。
- **0018 / 0019（审核台签字，specs/release-gate-sprint4）**：签字人是账号，属于平台层，放 `platform.content_signoff` / `platform.map_change_signoff`（0018 只改 platform），kge 不出现账号列（攻击面测试）；签字、发布、撤下函数在 0019（只改 kge），经新角色 `starbuddy_review` 调用，知识服务 `starbuddy_kge` 只能提交待审。

## 增补：policy schema（草稿，2026-09-28，specs/policy-schema-v0.9）

- **新增 `policy` schema**：放考情政策库与招录库。它们是基础数据，不是学科内容（平台规划 v1.3 §6），与 kge 分开；版本节奏也不同：kge 按地图版本冻结，policy 按城市届别版本化。
- **写入方复用 `starbuddy_kge`**：公共层只有一个写入方，审核台后端统一；发布经 SECURITY DEFINER 函数。
- **一个迁移只改一个 schema**：0013 只建 policy；学生档案的中考届别在 0014（platform）。kge 地图校验对政策库的读取写在 0011 的校验函数里，用动态查询，政策库不存在时跳过。
- **不含学生数据**：攻击面测试的公共层断言扩到 policy。

