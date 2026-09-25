# 文砺（wenli）盘点结论与决定记录

关联：本目录 inventory.md 文末"需要人确认的疑点"（编号与之一致）；adapter-design.md 的疑点记为 A1–A7
记录日期：2026-09-25
状态：负责人已于 2026-09-25 确认，全部照起草建议执行。

## 【已关闭】

| # | 疑点 | 结论 |
|---|---|---|
| 1 | 线上状态与真实用户 | **没有真实用户，处于测试阶段**（四科同样如此）。模块打标维持"可演示"为最高；不做数据迁移，也不需要合规事件处置 |
| 2 | daofa 归属 | 文砺 HEAD 里的 `apps/daofa`、迁移 0033–0035 和 shared 里的 daofa 代码**归知行**，文砺的盘点与适配器不再计入。工作区删除 `apps/daofa` 只作记录，legacy 不改 |
| 4 / A1 | 掌握度真相源 | 平台 `mastery_estimate` 是**唯一真相源**，skill_node 粒度由平台定。`ability_state`（28 格）和 `point_mastery`（知识点级 Beta）都只作为算法参考 |
| 5 / A4 | 位次和提分预测 | **妙笔的话语里不出现位次和提分**。如果保留，只放在报告引擎里，标明"预测、非承诺"，并且只基于已准入内容 |
| 6 / A5 | 即时生成题 | 契约 v1 的 generate_variation **只从已审题库选题**（对齐阿鸦 #9）。即时生成题不进正式测评和掌握门槛 |
| 8 | 迁移应用范围 | 没有真实用户，不影响迁移，**不追查** |
| 9 | LGPL 依赖 | sharp/libvips **登记进许可证清单**（对齐阿鸦 #12） |
| 10 | Next 16 + React 18 | 平台前端统一壳另定版本，legacy **不追查** |
| 11 | 测试基线 | legacy 只读，**不追查**。包装进适配器的纯函数，在平台仓库里重新建立测试基线 |
| A2 | 主观判分 confidence 口径 | 作文、阅读等主观维度，confidence 一律为低值，并置 `human_review_required = true`；规则判分（quiz）给出高置信 |
| A7 | study_plan 归属 | 文砺只输出学科处方，交给平台今日计划编排 |

## 【上线前必须解决】

| # | 疑点 | 结论与要求 |
|---|---|---|
| 3 | 版权处置 | Z-Library 来源的教辅笔记（249 段）**不进入平台内容底座**。教材全文切块（1097 段）一律标为"来源待核"，法务确认使用口径之前不得入库。legacy 的 git 历史不重写，记为已知缺陷 WENLI-KD-06 |
| 7 | 家长绑定越权 | legacy 记为已知缺陷 WENLI-KD-03。平台 `guardian_student_link` 的写入**只允许走服务端兑换流程**，RLS 附越权测试（[NEEDS-HUMAN]） |

## 【已采纳，待提供输入】

建议已采纳，但执行需要负责人或教研提供材料：

| # | 事项 | 待提供 |
|---|---|---|
| 12 | 仓库外文档 | 负责人提供 `文砺-M1诊断引擎修订.html` 和南京满分设定（初一初二 100、初三 120），补进 `docs/subjects/`；满分设定写进内容元数据 |
| 13 | 手机号夹具 | 负责人确认 `phone.test.ts` 里的号码是虚构的；包装时一律换成明显虚构的号码 |
| 14 | 守卫拦截的位置 | 人工确认 `W/.env.example` 只有占位符；`starbuddy-secrets-backup/` 加进 `.gitignore`（已列入 PR #1 跟进项） |
| A3 | 6 类错因映射到跨科大类 | 教研签字（冲刺 0 验收项） |
| A6 | grade 金标准 | 确定由谁标注作文和阅读的**合成**样本（CLAUDE.md 第 8 条） |

## 已知缺陷（legacy/wenli，只读，不修）

| 编号 | 缺陷 | 位置 | 平台侧对策 |
|---|---|---|---|
| WENLI-KD-01 | 任何登录用户都能用 service role 把题改成 approved | `apps/student/app/api/review/item/route.ts:19-48` | 审核动作只允许平台审核角色在服务端执行 |
| WENLI-KD-02 | 即时生成题不经审核直接推给学生 | `apps/student/app/api/quiz/generate/route.ts` 等 | 见 #6 |
| WENLI-KD-03 | 家长可能绕过邀请码自绑任意学生（待测试确认） | `supabase/migrations/0019_guardians.sql:41-43` | 见 #7 |
| WENLI-KD-04 | 判分记录 `model: "routed"`，没有真实模型版本 | `apps/student/app/api/essays/grade/route.ts:90` | 由平台模型路由回传 model_version |
| WENLI-KD-05 | 学生侧自由对话没有危机筛查与转介 | intake、苏格拉底、辩论等 prompt | 平台层确定性转介，由点点执行 |
| WENLI-KD-06 | Z-Library 教辅和教材全文已进入 git 历史 | `data/notes_chunks.json`、`data/chunks.json`、`scripts/ocr-notes.py:16-17` | 见 #3 |

## 对适配器设计的影响（已于 2026-09-25 同步到 adapter-design.md v0.2）

- 第 1 节：generate_variation 定为选题语义；grade 的主观维度一律低置信并进复核；report 的位次和提分只放在报告引擎。
- 第 2 节：confidence 口径已定。
- 第 3 节：掌握度真相源已定；新增内容准入规则；教辅和教材全文不入库；不做数据迁移；daofa 移出。
- 第 5 节：阻断项更新。
