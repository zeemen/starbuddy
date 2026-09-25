# 知行（zhixing）盘点结论与决定记录

关联：本目录 inventory.md 文末"需要人确认的疑点"（编号与之一致）；adapter-design.md 的疑点记为 A1–A6
记录日期：2026-09-25
状态：负责人已于 2026-09-25 确认，全部照起草建议执行。

前提：按文砺决定 #2，知行的主体代码在 `legacy/wenli/…/wenli/` 里（`apps/daofa`、迁移 0033–0035、shared 里的 `daofa-*` 和 `build-*` Prompt），`legacy/zhixing` 只是页面外壳。本记录覆盖这两处。

## 【已关闭】

| # | 疑点 | 结论 |
|---|---|---|
| 1 | 线上状态与真实用户 | **没有真实用户，处于测试阶段**（四科同样如此）。模块打标维持"可演示"为最高；不做数据迁移，也不需要合规事件处置 |
| 9 | RLS 漏洞 | 没有真实用户，**不做线上处置**；legacy 记为已知缺陷 ZHIXING-KD-03~05。平台侧要求列入 adapter-design.md 第 6 节阻断项 |
| 10 | 匿名 API 与成本 | legacy 记为已知缺陷 ZHIXING-KD-01、02，**不追查账单**。平台侧要求所有 API 鉴权并计量（阻断项） |
| 11 | smoke 的"19/19" | legacy **不追查**；在平台仓库里重新建立测试基线 |
| 12 | git 历史 | 知行历史以 `legacy/wenli/` 为准（520 个提交，其中 daofa 作用域 19 个），**不另行追查** |
| 13 / A5 | 历史学科 | 历史与道法同属 zhixing 适配器，**内容分库**（道法、历史各自的 Rubric 和题库）；历史 Rubric 另立项，**契约 v1 不含历史判分** |
| 14 | 三风格语料 | steady/roast/companion 只作为点点风格语料的**候选**，需要教研审核并跑 `evals/persona` 回归才能采用；"去年道法满分""我当年……"这类表述不得沿用。采用时属于人设变更，[NEEDS-HUMAN] |
| A2 | confidence 口径 | M3 主观题判分一律低置信，并置 `human_review_required = true`；M2 定位是确定性验证器，给出高置信 |
| A6 | 阻断项与冲刺 2 | 冲刺 2 可以在测试环境里接入 grade、diagnose、generate_variation；阻断项作为**上线门禁**，不作为冲刺 2 门禁 |

## 【上线前必须解决】

| # | 疑点 | 结论与要求 |
|---|---|---|
| 2 | 模型路由与出境 | 并入 **ADR 09"数据驻留"**。没有真实用户，不存在已发生的出境，不追查历史 |
| 3 | 2 道内置题的来源 | 一律标为**"来源待核"**，核实前不得作为已准入内容使用 |
| 5 | 教材和读本原文 | 一律**"来源待核"**，法务确认使用口径之前不入库；知识图谱节点只保留自写的要点概括和页码索引（对齐文砺 #3） |
| 6 | 时政来源 | 时政只能取自**教研确定的白名单源**，并且走 content_review 审核发布；M4 挂接和命题**不得自由生成核心政治判断内容**（PRD 第十章） |
| 15 | 未成年人同意 | 迁到平台 **`consent_record`**，由监护人核验；legacy 的学生自声明勾选作废 |
| 16 | 自伤和危机信号 | **平台层确定性转介，由点点执行**；知行不自行处理（对齐 WENLI-KD-05） |

## 【已采纳，待提供输入】

| # | 事项 | 已定部分 | 待提供 |
|---|---|---|---|
| 4 | 3 份样卷的来源 | 按"来源待核"处理，**不作为金标准** | 负责人确认是否来自真实学生；如果是，从平台仓库和评测集中排除（CLAUDE.md 第 8 条） |
| 7 / A1 | 能力坐标 | 平台 `mastery_estimate` 是唯一真相源；五维只作为算法参考 | 教研拍板 skill_node 坐标：五维，还是总纲的"过程四步" |
| 8 / A3 | 错因映射 | — | 教研签字：知行四类映射到跨科七大类，"转化错"归哪一类（冲刺 0 验收项） |
| 17 | env 键名核对 | — | 人工确认 `.env.example` 只有占位符 |
| A4 | grade 金标准 | 2 道内置题和 3 份样卷不作为金标准 | 教研提供合成标注集 |

## 已知缺陷（legacy，只读，不修）

位置缩写：Z = `legacy/zhixing`，S = `legacy/wenli/初中语文_AI_class/wenli`。

| 编号 | 缺陷 | 位置 | 平台侧对策 |
|---|---|---|---|
| ZHIXING-KD-01 | `/api/*` 全部匿名可调，包括调用模型的接口；没有限流和计量 | Z/middleware.ts:6 | 所有 API 鉴权并计量 |
| ZHIXING-KD-02 | 命题接口没有管理员门禁，任何人都能触发模型并写入 `daofa_item` | Z/app/api/items/generate/route.ts:20-28,55-73 | 生成只进 content_review，只允许审核角色在服务端操作 |
| ZHIXING-KD-03 | 学生可以把自己生成的题改成 approved，绕过审核门禁 | S/supabase/migrations/0035_daofa_item.sql:30-33 | 题目状态只允许审核角色改，RLS 附测试 |
| ZHIXING-KD-04 | 判分和能力行学生可写 | S/…/0033_daofa_m3.sql:49-52；0034_daofa_ability.sql:20-23 | 判分和推断对学生只读，RLS 附测试 |
| ZHIXING-KD-05 | 申诉不校验作答归属 | S/…/0033_daofa_m3.sql:58-60 | 申诉写入校验 attempt 归属，RLS 附测试 |
| ZHIXING-KD-06 | 错误信息原样透传给前端，可能带出上游响应 | S/packages/shared/lib/api/respond.ts:35-36；lib/llm/openai.ts:72 | 平台统一错误封装 |
| ZHIXING-KD-07 | 判分记录 `model: "routed"`，没有真实模型版本 | Z/app/api/m3/grade/route.ts:117 | 由平台模型路由回传 model_version |
| ZHIXING-KD-08 | 批改页的多 Agent 日志是定时器播放的假日志 | Z/app/grading/page.tsx:12-19,61-62 | 不迁移 |
| ZHIXING-KD-09 | 没有确定性危机转介；禁语 D 类没有检测 | S/…/domain/daofa-persona.ts:80；daofa-persona-guard.ts | 见 #16；禁语过滤由平台人设守护承担 |
| ZHIXING-KD-10 | 人设写死年级；M2 文案有 G 类快慢评价 | S/…/daofa-persona.ts:22,34,46；daofa-locate.ts:26-30 | 见 #14 |

## 对适配器设计的影响（已于 2026-09-25 同步到 adapter-design.md v0.2）

- 第 1 节：grade 置信度口径已定；generate_variation 定为只从已审题库选题；历史判分不在契约 v1 范围内。
- 第 2 节：confidence 和金标准口径。
- 第 3 节：唯一真相源；新增内容准入表；不做数据迁移。
- 第 6 节：阻断项与本记录对齐。
