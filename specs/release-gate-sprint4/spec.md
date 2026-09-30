# G 流：发布流水线与审核台（冲刺 1 审核台 v0 · 冲刺 3 页面审核线 · 冲刺 4 分级发布；内容六段标识）

状态：**冲刺 1 段已确认（2026-09-28，用户“按默认建议，开始实现”；待人确认 #1、#3、#4、#5、#10、#12 按默认）**；冲刺 3、4 段仍为草稿。2026-09-30 按冲刺 3–4 合并后的现状复核（见“2026-09-30 复核”）；**冲刺 3、4 段已确认（2026-09-30，用户“按默认建议，开始实现”），待人确认全部按默认**。实现顺序：先做不碰迁移的部分（阿鸦内容包格式 3、准入决定文件、提示原文进 C9 检查），迁移与审核台部分在统一壳 H3 / H2 合入后接着编号。实现记录见 lessons.md

> **2026-09-28 按开发计划 v2.0 改写**（方案 `specs/sprint4-v2-rewrite/spec.md`，按其待人确认 #2 的默认做法：仍是一份 spec，按冲刺分节）。v1.0 口径的原稿见本文件的 git 历史；两稿的差别见“相对 v1.0 版本的变化”。
> 目录名保留 `release-gate-sprint4`，以免断开已有引用（`specs/plan-v2-alignment/spec.md`、四份冲刺 4 spec 的协调节）。

任务包：开发计划 v2.0 的 **G 流“发布流水线与审核台”**。v2.0 没有给 G 流任务包编号（待人确认 #1）。本 spec 覆盖 G 流的三段：

| 冲刺 | 周 | 任务 | v2.0 验收 |
|---|---|---|---|
| 冲刺 1 | W3–W4 | 审核台 v0：能改、能审、能发布、差异对比 | 教研可独立完成一次节点审核发布 |
| 冲刺 3 | W7–W8 | 编译页面审核线（随 KGE-03 编译层落地；v2.0 把它写在 K 流，本 spec 负责审核这一侧） | 见第三部分验收（v2.0 在 KGE M1 里要求“知识点页发布率 100%、体检零阻断”） |
| 冲刺 4 | W9–W10 | 分级发布：低风险合并窗口、中风险抽检与小流量试用、高风险两人确认；内容六段标识；考情政策线 | 四科各发布一个经闸门的内容版本（口径见待人确认 #2） |

冲刺 2 的 G 任务“统一复核队列（低置信、申诉、抽检、画像异议四类）”**不在本 spec**，另起 spec；本 spec 只约定两者共用 staff 登录与签字函数的做法（设计要点 3）。

## 关联（现行依据）
- **开发计划 v2.0**（`docs/plan/开发计划-v2.0.html`）：
  - 第四章冲刺 1 / 2 / 4 的 G 行（任务与验收见上表），冲刺 3 的 K 行 KGE-03（编纂规范、编译器、每日合并窗口、程序体检、Git 主分支保护与 CI）；
  - 甘特图 G 行：审核台 v0 → 统一复核队列 → 分级发布；阶段 0 终点交付物：“知识地图与页面审核台（差异对比）；统一复核队列（含画像异议）；分级发布与合并窗口；六段内容标识”，人审负责“工程监督 Py + 教研”；
  - 第六章 Gate 0 软条件“发布闸门四科各有一个版本”，证据是“版本记录”；第八章工程规范“pilot 只有经闸门发布的内容”。
- **KGE V1.1**（`docs/plan/KGE-知识与成长引擎-V1.1.html`；知识结构与内容成长以它为准）：
  - 第二章原则“成长必经闸门：候选 → 证据包 → 审核 → 版本发布”；
  - 第四章：页面三种段落（锁定段、渲染段、叙述段）、两种重建、五个操作、**分级发布闸门**表、“审核界面的核心是差异对比”；
  - 第五章：变更流程（结构性变更两人确认；发布前影响分析；按旧 → 新 ID 映射迁移证据），版本号“主.次.修订”（修订按周、次版本按月、主版本按学期）；
  - 第八章：编译层试点期用 Git；第九章 W3–4 “审核台 v0（能改、能审、能发布、差异对比）”。
- **完整方案 v1.1**（`docs/plan/完整方案-v1.1.html`）：第六章“内容发布闸门”（v1.1 新增知识地图与编译页面一条审核线；时政与当年考情有生效和失效日期）；第十一章“一键停用评分版本并重算”；第十二章“可信度（发布阻断）”“知识底座（发布阻断）”；第十三章“审核中心”六线。
- **平台规划 v1.3 增补篇**（`docs/plan/平台规划-v1.3增补篇-升学导航与家庭支持.html`）第六章：考情政策线，双人复核。
- **表结构（迁移草稿，均待人执行）**：
  - `db/migrations/0011_kge__knowledge_map.up.sql`：`kge.map_version`（第 78–94 行）、`kge.map_change`（第 210–226 行，两人确认的 CHECK）、`kge.scoring_point`（第 278–299 行，`rubric_version`）、`kge.current_affair`（第 301–311 行）、`kge.content_review`（第 313–327 行）、`kge.validate_map_version`（第 462–614 行）、`kge.publish_map_version`（第 619–645 行，SECURITY DEFINER，属主 `starbuddy_authz`，发布前校验有阻断问题就拒绝）；
  - `db/migrations/0012_platform__curriculum_map_version.up.sql`：`platform.curriculum_version.map_version_id`（可空，发布新内容版本时由本流程填写）；
  - `db/migrations/0013_policy__exam_policy.up.sql`：`policy.exam_scheme`（第 80–96 行，两人复核 CHECK）、`policy.validate_scheme`（第 352–403 行）、`policy.publish_scheme`（第 405–433 行，SECURITY DEFINER，属主 `starbuddy_authz`）；
  - 已合并：`0003`（内容登记与六段标识）、`0006`（`review_task`、`resolve_review_task`）、`0015`（`enroll_subject` 的上线门控，第 571–573 行）。
- **设计文档**：`docs/design/kge/id-and-segments.md`（第 3 节六段标识、第 4 节版本号）；`docs/design/policy/data-entry-rules.md`（双人复核、更正与撤下）。
- **已确认的 schema spec**：`specs/kge-schema-v0.9/spec.md` 与 `lessons.md`（待人确认 #1–#13 中留给审核台的项）；`specs/policy-schema-v0.9/spec.md` 与 `lessons.md`。
- **契约**：`packages/contracts/openapi/knowledge-service.v0.9.yaml`（`page.get` 的 `page_version`；`source_version` 带地图版本段）；`packages/contracts/openapi/plugin-api.v1.0.yaml`（候选版：`ContentVersion` 六段；`generate_variation` “产出只能进入平台 content_review”）。
- **ADR**：12 编译层试点期用 Git（`docs/adr/12-compiled-pages-git-pilot.md`，草稿）；11 星图统一定义（星 = 已发布地图版本里的知识节点）；14 任务包格式与人审点；09 数据驻留；02 共库分 schema（每个迁移只改一个 schema）。
- **CLAUDE.md**：规则 1、2、3、8、9、13；必停项“db/migrations、RLS 与权限”“新增第三方服务”。

**只作追溯（已被取代）**：`docs/archive/完整方案-v1.0（已被v1.1取代）.html` 第六章“内容发布闸门”、第十三章审核中心五线；`docs/archive/开发计划-阶段0-1-v1.0（已被v2.0取代）.html` 冲刺 4-G“发布闸门 v0”。本 spec 不再以它们为依据。

## 相对 v1.0 版本的变化

| 改写点 | v1.0 版本 | 本版 | 依据 |
|---|---|---|---|
| 任务范围 | 冲刺 4-G 一次交付“草稿 → 检查 → 审核 → 发布” | G 流分冲刺 1 / 3 / 4 三段交付；冲刺 2 复核队列另起 spec | 开发计划 v2.0 第四章 G 行；`specs/sprint4-v2-rewrite/spec.md` 待人确认 #2 |
| 审核对象 | 课程版本、题目准入 | 加知识地图（节点、边、投影）、原子（采分点、题目标签、时政）、编译页面、考情政策四条线 | KGE 第四、五章；完整方案 v1.1 第六章；v1.3 增补篇第六章 |
| 流程形态 | 一条流程，一名 staff 签字 | 按风险分级：低风险合并窗口 + 事后抽检；中风险抽检 + 新版试用；高风险必审，结构性变更与考情两人确认 | KGE 第四章“分级发布闸门”、第五章变更流程；0011 `map_change_two_reviewers`；0013 `exam_scheme_two_reviewers` |
| 审核入口 | 命令行 `python -m learning.release`，不做网页 | 网页审核台，看差异不看全文；页面走 Git 合并请求网页界面 | 开发计划 v2.0 冲刺 1 验收；KGE 第四章；ADR 12 |
| 发布路径 | 新建 `publish_content_release` 等五个函数 | 地图走已有的 `kge.publish_map_version`，考情走已有的 `policy.publish_scheme`，页面按 ADR 12；只为内容版本与题目准入新增发布函数 | 迁移草稿 0011、0013；ADR 12 |
| 审核记录 | 新建 `platform.content_review_record`、`content_release` | 统一用 `kge.content_review`（不另建第二张），补齐只追加、staff 身份、摘要、页面提交号；考情的复核人留在 policy 表本身 | 规则 2；0011 第 313–327 行；`specs/kge-schema-v0.9/lessons.md` 待人确认 #1、#2 |
| 六段标识 | 只在 `curriculum_version` 上 | 仍只存 `platform.curriculum_version`，发布时绑定已发布的 `kge.map_version`（0012） | `docs/design/kge/id-and-segments.md` 第 3 节 |
| 迁移编号 | “0011_platform__release_gate” | 作废：0010–0015 已被其他草稿占用；新迁移按合并顺序在 0015 之后排定 | `db/migrations/` 现状 |
| 选科门控 | 待人确认 #6 | 已由计量 v2 定：`enroll_subject` 需学科目录非 planned 且有已发布课程版本 | `specs/metering-v2/spec.md`（已确认）；0015 第 571–573 行 |
| 时政 | 不做（待人确认 #13） | `kge.current_affair` 已有生效与失效日期；冲刺 4 做过期自动下架 | 0011 第 301–311 行；KGE 第七章 |
| Rubric 版本 | 验证器 / Rubric 注册表不做 | 采分点带 `rubric_version`，改内容只能发新版本；审核台负责它的审核与发布 | 0011 第 278–299 行；开发计划 v2.0 冲刺 4 K 行 |
| 英语 | 开口成章已搁置，Gate 0 口径交人 | 英语搁置不计，不为它准备版本记录 | `specs/plan-v2-alignment/spec.md` 第三节决定 #1 |
| 道法 | 知行无真实内容，只做合成版本 | 标准团队模式：道法切片冲刺 4 过门，道法应有真实内容版本 | 同上，决定 #10；开发计划 v2.0 冲刺 4 K 行 |

## 目标
教研能在一个网页上完成“改 → 看差异与体检 → 签字 → 发布”，并且：
1. 知识地图、原子、编译页面、内容版本、考情政策五类对象，都只能经这条流水线进入已发布状态；
2. 按风险分级：低风险不堵教研，高风险必须有人签字，结构性变更与考情必须两个不同的人签字；
3. 签字人是真实的 staff 账号，不是自报的工号；签字时看到的内容就是发布的内容；
4. 每一次发布都能在“版本记录”里查到：六段标识、地图版本、Rubric 版本、页面提交号、签字人、时间、体检摘要（Gate 0 证据）。

## 输入契约与禁止修改的范围（ADR 14 草稿的格式）
- **输入契约**：迁移草稿 0011、0012、0013（按人执行后的版本）；`knowledge-service.v0.9.yaml`；`plugin-api.v1.0.yaml` 候选版；ADR 12（签字后的版本）。
- **禁止修改**：`packages/contracts/`（需要改时停下来登记，见必停项）；`evals/`；`docs/personas/`；`docs/plan/`；各适配器的判分逻辑；页面仓库里的页面正文（只能经合并请求改）。

## 现状（2026-09-28 核实）

### 各审核线在库里的样子
下文“kge lessons #N”指 `specs/kge-schema-v0.9/lessons.md` 的“待人确认”第 N 条。

| 线 | 对象与位置 | 已有的发布规则 | 缺口（本 spec 要补） |
|---|---|---|---|
| 知识地图 | `kge.map_version`、`knowledge_node`、`knowledge_edge`、`textbook_projection`、`region_projection`、`skill_link`、`id_migration`（0011） | 版本只能以草稿新建；发布只能经 `kge.publish_map_version`；非草稿版本骨架冻结；同学科只有一个已发布版本 | 发布函数只查结构，不查有没有人批准（kge lessons 待人确认 #1）；版本号不检查是否递增（`UNIQUE (subject, version)` 只防重复）；撤下可以不经函数（kge lessons #4）；只有 `starbuddy_kge` 能调用发布，审核人与提交方是同一个角色 |
| 地图变更单 | `kge.map_change`（0011） | 批准或应用时至少两个不同的 `reviewers` | `reviewers` 是自报工号，库层只能保证“两个不同字符串”（kge lessons #2） |
| 原子 | `kge.item_tag`、`item_family`、`scoring_point`、`current_affair`（0011）；题目准入 `platform.item.admission`（0003） | 采分点只能新增、不能修改；时政生效与失效日期必填；过期时政由校验报警告 | 原子不随骨架冻结，已发布地图版本上可以直接新增原子，不经审核（`docs/design/kge/id-and-segments.md` 第 4 节）；`starbuddy_learning` 对课程版本、题目有不受限的 UPDATE（0003 第 44、49、54、59 行），可以自己把题改成 `admitted` |
| 编译页面 | 单独的 Git 仓库（ADR 12，草稿，托管平台未定） | ADR 12：主分支保护、CI 体检、教研经网页审核、高风险两人 | 仓库、CI、编译器都还没有（KGE-03 冲刺 3）；`kge.content_review` 的 `object_type` 里没有页面，也没有记提交号的列 |
| 内容版本（六段） | `platform.curriculum_version`（0003；0012 加 `map_version_id`） | 六段组合唯一；BFF 只读已发布 | 没有发布函数与保护触发器；`retired_at` 缺失；`map_version_id` 可以指向草稿或已撤下的地图版本（kge lessons #4） |
| 考情政策 | `policy.source_document`、`exam_scheme`、`admission_line`、`school`（0013） | 两位不同复核人 + 复核时间；方案只能经 `policy.publish_scheme` 发布，校验阻断；版本号必须递增 | 复核人同样是自报工号（`specs/policy-schema-v0.9/lessons.md`“待人确认” #4）；来源文件、招录行、学校可以由 kge 角色直接改为已发布（同文件“审查提出、需要人决定的” #4） |
| 审核记录 | `kge.content_review`（0011 第 313–327 行） | `decision` 为 pending 时审核人与时间为空 | `starbuddy_kge` 可以 UPDATE，不是只追加；审核人是自报工号；没有签字时的内容摘要 |

### 身份与入口
- `platform.account_kind` 有 `staff`（0001 第 45 行）；`starbuddy_review` 角色只读复核队列、调用结案函数（`db/bootstrap/roles.sql` 第 6 行）；`resolve_review_task` 要求当前账号是 active staff（0006 第 42–60 行）。这是“提交方不能自己签字”的现成模式。
- **没有任何 staff 登录**：BFF 只有计量与星图（`services/bff/src/`），统一壳的家庭登录在 4-H；staff 账号只能由人用 SQL 建。
- 知识服务、编译器、审核台都还没有代码；KGE 代码放在哪里待 ADR（CLAUDE.md 仓库结构）。

### 发布状态已经在影响选科与星图
- `enroll_subject`：学科目录为 planned，或该学科没有已发布课程版本时返回 `not_launched`（0015 第 571–573 行）。
- 星 = 已发布地图版本里的知识节点（ADR 11 草稿）。地图版本不发布，星图就没有星。
- 测试夹具直接插入已发布的课程版本（`db/tests/10_fixtures.sql` 第 29、33 行）；星图种子直接插入草稿版本（`db/seeds/dev/star-map-synthetic.sql` 第 71 行）。

### 各科内容
- **数学（阿鸦）**：内容包 `adapters/aya/content/pack.json`，六段 `nanjing / 2027 / 9 / suke-2024 / aya / 0.1.0`，年级、教材、年份是登记时猜的（`specs/adapter-aya-sprint1/lessons.md` 待人确认 #1）；721 道题全部 `source_unverified`，准入升降都只能走发布闸门（同文件 #9）；准入规则 `admissionOf` 在 `adapters/aya/src/content.ts` 第 45 行，共享核对库“只能往更严的方向改”（`packages/contracts/python/starbuddy_contract/checks.py` 第 193–201 行）；来源待核（`specs/inventory-aya/decisions.md` #3）。KGE-01（冲刺 1）导入阿鸦图谱，KGE-02（冲刺 2）题库补六段标识。
- **道法（知行）**：没有适配器，ADP-道法-01 在冲刺 2；道法切片在冲刺 4（K 流），36 个大概念页码未绑定版次（`specs/kge-00-asset-inventory/lessons.md`）。时政只取白名单源（`specs/inventory-zhixing/decisions.md` #6）。
- **语文（文砺）**：没有适配器，ADP-语文-01 在冲刺 2；语文切片移到阶段 1 的 1–2 月，阶段 0 没有语文地图版本。
- **英语**：搁置（对照文档决定 #1）。

## 2026-09-30 复核（冲刺 3、4 段；冲刺 1 段已实现）
- **审核台与 staff 登录已有**（冲刺 1 段 + 迁移 0020）：待人确认 #3 已按默认实现（审核台后端单独 staff 登录）；#10 代码位置按默认（`services/learning/review_console`、`apps/web` staff 路由组）。
- **冲刺 3 · 编译页面审核线**：KGE-03 已按 ADR 12 交付页面仓库模板（主分支保护、CODEOWNERS、CI 只从 main / 固定版本取体检工具）、程序体检与合并窗口、快照导出只接受受保护分支上的提交；页面审核在页面仓库的合并请求里完成。本 spec 冲刺 3 段剩下的是审核台一侧：页面合并请求与 `kge.content_review` 的对账（提交号 ↔ 审核记录）与版本记录导出里的页面行（见第 6 节），不再重复建页面编辑器。
- **冲刺 4 · 已有的部分**：4-K 道法切片已交付采分点入原子层（只新增）、时政录入（白名单、有效期、pending 导入）与即将到期报表；过期由只读角色 RLS 自动不可见、KGE-03 体检阻断引用过期时政的页面。本 spec 第 9 节“每日体检后把过期时政置 retired 并写系统审核记录”仍需实现。
- **阿鸦内容包**：4-D 已把内容包升到格式 2（题目用途、提示阶梯等）；待人确认 #8 / #9 的合并顺序实际变为“4-D 先、4-G 后”，本流程在格式 2 之上加逐题来源与准入决定（格式 3），并把新进包的提示原文纳入 C9 词表检查（阿鸦 lessons #5）。
- **判分一致率**（#13）：`evals/daofa-grading/`（4-K）与各科 `make eval-grade` 口径已有，默认仍是阶段 0 只在版本记录里记结果、不阻断发布。
- **迁移编号**：0029 起被 4-H H1 占用、H3 随后；本 spec 的新迁移按合并顺序接着编号。

## 设计要点

### 1. 审核线与发布路径
| 线 | 审核对象 | 发布路径 | 默认风险 | 交付冲刺 |
|---|---|---|---|---|
| 知识地图 | 节点（名称、包含 / 不包含、典型题例、认知层次、范围、状态）、边、教材投影、地区投影、能力视图挂接、旧编码迁移 | 草稿地图版本 → `kge.publish_map_version` | 结构性变更高（两人）；投影原句与页码高；边界补充中；错别字低 | 冲刺 1 |
| 原子 | 采分点（`rubric_version`）、题目标签、题目族、时政 | 草稿地图版本随版本发布；已发布版本上新增原子须先有批准的审核记录（第 5 节） | 采分点高；题目族引用低；时政中 | 冲刺 1 建表规则，冲刺 4 分级 |
| 编译页面 | 知识点页、观点页等（锁定段、渲染段、叙述段） | 页面仓库合并请求 → 每日合并窗口 → 知识服务 `page.get`（ADR 12） | 按 KGE 第四章表 | 冲刺 3 |
| 内容版本 | 六段标识、能力视图登记、题目登记与准入 | 新增 `platform.publish_content_version`、`platform.change_admission`（第 7 节） | 高 | 冲刺 4 |
| 考情政策 | 来源文件、方案、招录行、学校 | `policy.publish_scheme`；来源文件、招录行、学校收口到函数（第 8 节） | 高（两人） | 冲刺 4 |

**不在本 spec 的审核线**：人设语料（F 流人设治理）；生成题（未启用，第 10 节）；批改抽检、申诉、画像异议（冲刺 2 统一复核队列）；通识内容（阶段 1）。

### 2. 风险分级（KGE 第四章为准）
| 风险 | 改动类型 | 发布方式 | 签字 |
|---|---|---|---|
| 低 | 新增题目族引用、考情数值更新（页面里的统计，不是政策库）、渲染段变化、错别字 | 程序体检通过后进每日合并窗口，每个页面每天最多发布一次；事后按比例抽检 | 无事前签字；抽检记审核记录 |
| 中 | 讲解要点、方法说明、易错纠正话术改写；节点边界补充（包含 / 不包含 / 典型题例，KGE 表未列，本 spec 默认） | 语义体检通过后进抽检队列；页面先以“新版试用”小流量发布 | 一名教研 |
| 高 | 定义与课标要求、评分点与判分依据、前置关系表述、教材投影中的原句与页码、编纂规范变更、全量重编 | 必须教研审核；旧版本在新版发布前继续服务 | 一名教研；**结构性变更两人** |

- **结构性变更**：`kge.map_change.change_type` 为 `node_add`、`node_split`、`node_merge`、`node_retire`、`edge_add`、`edge_remove`（前置边与从属边）的变更单，需两名不同的 staff 签字。与 `docs/design/kge/id-and-segments.md` 第 4 节一致：带结构性变更的版本至少升次版本号。
- **分级由规则判定，不由提交方自报**：分类器是纯函数，输入是差异（第 4 节），输出风险级别与理由；判不出的一律按高风险。
- 抽检比例、合并窗口的时间与紧急修复通道由教研负责人定（ADR 12 待人确认 #3），本 spec 只留配置位。
- 语义体检（与原子冲突、跨页矛盾、超纲、适龄文风、角色语言混入）属于 KGE-03；阶段 0 没有语义体检时，中风险按高风险处理（待人确认 #7）。

### 3. 审核身份与签字：提交方不能自己签字
沿用 `resolve_review_task` 的模式（0006 第 42–60 行）：
- **两个连接身份**：审核台后端编辑草稿、提交变更单用 `starbuddy_kge`；签字与发布用 `starbuddy_review`，事务里设当前 staff 账号。两个身份用不同的登录角色（0011 开头已断言一个登录角色不能同时继承 kge 与 review）。
- **签字函数**（新增，SECURITY DEFINER，属主 `starbuddy_authz`，`search_path` 固定，只授予 `starbuddy_review`）：
  - `kge.sign_review(object_type, object_id, decision, basis_code, note)`：要求当前账号是 active staff；审核人记账号 ID，由函数取，调用方不能传；写入签字时对象的内容摘要（第 4 节）；
  - `kge.sign_map_change(change_id, decision)`：追加签字人；同一账号对同一变更单只能签一次；达到两人时变更单才能转为 approved；
  - `policy.sign_*`：考情线同样做法（第 8 节）。
- **`kge.content_review` 改为只追加**：收回 `starbuddy_kge` 对它的 UPDATE；`reviewer` 由自报工号改为 staff 账号引用（新列，旧列保留为空或删除，见待人确认 #4）；新增 `digest`、`basis_code`、`page_commit` 等列（第 4、6 节）。触发器拒绝 UPDATE、DELETE、TRUNCATE。
- **发布也由签字身份执行**：`kge.publish_map_version`、`policy.publish_scheme` 的 EXECUTE 从 `starbuddy_kge` 改授 `starbuddy_review`，函数内要求 active staff。编译器、导入脚本只能提交，不能发布。
- **staff 登录**：审核台 v0 需要真实的 staff 登录才能满足“教研独立完成”。默认做法见待人确认 #3。登录前的开发与测试用合成 staff 账号，事务里 `SET LOCAL app.account_id`。
- 冲刺 2 的统一复核队列用同一套 staff 登录与签字模式，不另建身份体系。

### 4. 差异对比与“看到的就是发布的”
- **差异基准**：同学科当前已发布的地图版本；没有已发布版本时，全部按新增列出。
- **差异内容**（纯函数，同一输入逐字节相同，便于测试）：
  - 节点：新增、停用、字段变化（逐字段新旧值）；
  - 边：新增、停用、强弱变化；
  - 投影：页码、原句（给出新旧 `quote_hash`）、核对状态变化；
  - 旧编码迁移：新增的映射与 `retire`（提示“对应掌握度标为待复测”，KGE 第五章）；
  - 原子：新增的采分点（按 `rubric_version` 分组）、题目标签、时政；
  - 每条差异附：风险级别与理由、所依据的变更单或证据包、`kge.validate_map_version` 中与该对象相关的问题。
- **影响分析**（KGE 第五章“发布前自动影响分析”）：v0 只给公共层的计数——多少题目标签要重算、多少页面引用了变化的节点；“哪些学生画像受影响”要读个人层，属于 KGE-04 / SIE，不在审核台 v0 里算（待人确认 #8）。
- **摘要**：`kge.map_version_digest(uuid)` 对草稿版本的骨架与原子按固定顺序序列化后取 SHA-256。签字时写进审核记录；`kge.publish_map_version` 发布前重算，与最近一条批准记录的摘要不同就拒绝（签字后草稿又被改过，要重新签）。内容版本、考情方案同样做法。

### 5. 地图发布规则（冲刺 1 补进 `kge.publish_map_version`）
在现有“只能发布草稿、校验无阻断、同学科串行”之外增加：
1. 存在一条对象为该地图版本、`decision = approved` 的审核记录，且其摘要与当前摘要相同（kge lessons 待人确认 #1，默认改为库层强制，见待人确认 #5）；
2. `target_version` 指向该版本的结构性变更单全部为 approved，且签字人为两个不同的 active staff 账号；
3. 版本号按“主.次.修订”三段数字严格大于同学科已发布与已撤下的最大版本；含结构性变更时次版本号或主版本号必须增加；
4. 撤下只能经 `kge.retire_map_version(uuid, basis_code)`（新增，同样要求 staff），不能直接 UPDATE（kge lessons #4）；
5. 已发布、已撤下地图版本上新增原子：`item_tag.tag_review_status`、`current_affair.review_status` 改为 approved / published 前须有对应的批准审核记录；采分点新 `rubric_version` 须有批准记录才能被判分引用（由知识服务 `rubric.get` 只返回已批准版本保证，库层做法见待人确认 #6）。

### 6. 编译页面线（冲刺 3，按 ADR 12）
- **仓库与分支保护**：页面仓库在境内托管，主分支不允许直接推送、强推与改写历史；合并前 CI 通过、至少一名教研批准，高风险页面两人。工程侧的批量改动（编译器重建）同样走合并请求。托管平台与账号开通由人定（ADR 12 待人确认 #1）。
- **合并请求就是审核台的页面视图**：合并请求模板写明改了哪几句（锁定段、渲染段、叙述段分开列）、依据的原子（`atom_refs`）、体检结果、风险级别。教研在托管平台的网页界面审核，不在本地提交。
- **CI 体检**（ADR 12 第 3 节）：页面头部字段、引用节点在已发布地图版本中存在、公式可解析与 SymPy 验算、人设中立与适龄文风、疑似个人信息与学生标识拦截、群体统计带人数且不低于门槛。体检不过不能合并。
- **合并窗口**：编译器（KGE-03）每天在窗口内把主分支编进知识服务，每个页面每天最多发布一次；页面带来源提交号，知识服务把提交号映射到 `page_version`（ADR 12，不新增契约字段）。
- **审核记录对账**：每个合并进主分支的页面改动，在 `kge.content_review` 里有一条 `object_type = 'page'` 的记录，写页面 ID、`page_commit`（40 位十六进制）与合并请求上的批准人（映射到 staff 账号）。对账脚本比对托管平台导出的合并记录与审核记录：
  - 一方有、一方没有，或批准人数不符（高风险少于两人）→ 对账失败；
  - 对账失败时，合并窗口不发布受影响的页面，直到人处理。
  - 托管平台用户名与 staff 账号的对应表由人维护在审核台配置里，不含学生数据。

### 7. 内容版本与六段标识（冲刺 4）
- **六段只存在 `platform.curriculum_version`**（`docs/design/kge/id-and-segments.md` 第 3 节）；题目、采分点、页面经内容版本继承，不重复存。
- **内容版本与地图版本的对应**：
  - 发布内容版本时，有 KGE 骨架的学科（数学、道法）`map_version_id` 必填，且所指地图版本同学科、在发布时刻为已发布（kge lessons #4 的默认，本 spec 采纳）；
  - 阶段 0 没有语文地图版本，语文内容版本 `map_version_id` 可空（待人确认 #2 连带）；
  - 地图版本升级不自动改已发布的内容版本；内容版本的下一个版本改指新地图版本，中间按 `kge.id_migration` 映射（投影随骨架冻结带来的问题见 kge lessons #7，待 KGE 定）；
  - `region`、`exam_year` 对应政策库的（城市, 届别）：发布时核对有已发布方案，没有就在报告里记警告，不阻断（`specs/policy-schema-v0.9/spec.md` 设计要点 5）。
- **状态机与保护**（沿用 0008 `guard_plan_config` 的做法）：
  - 草稿可追加能力视图与题目登记；已发布只能改为已撤下；已撤下只读；新增 `retired_at` 与状态一致性 CHECK；
  - 同前五段下，新发布的 `content_version` 必须大于已发布的最大值；
  - 发布只经 `platform.publish_content_version(cv_id)`，停用只经 `platform.retire_content_version(cv_id, basis_code)`：SECURITY DEFINER，属主 `starbuddy_authz`，只授 `starbuddy_review`，要求 active staff 与摘要一致的批准记录；
  - 题目准入升降只经 `platform.change_admission(review_ids[])`：方向只允许 `source_unverified → pending_review → admitted`，以及任意状态 → `rejected`；升为 `admitted` 须有来源记录编号与学科审核等级（阿鸦 ≥ Q2）；
  - 收回 `starbuddy_learning` 对 `status`、`published_at`、`admission`、`admitted_at` 的 UPDATE；登记草稿的 INSERT 保留。
- **准入的唯一真相源仍是 `platform.item.admission`**（`specs/kge-schema-v0.9/spec.md` 设计要点 1，已确认）。适配器内容包里的准入是副本，共享核对库保证副本落后时只会更严。阿鸦内容包要能逐题标来源，才能让准入升级真正生效（待人确认 #9）。
- **BFF 可见性**：`skill_node_read` 改为所属版本已发布；`item_read` 改为已准入且所属版本已发布；审核记录对 BFF 不可见。
- **准入升级不追溯、降级追溯**：现有行为（`services/learning/learning/store.py` 第 116–130 行取更严），不改。按降级或停用评分版本重算掌握度属于 KGE-04（`specs/mastery-estimate-sprint4/spec.md`），本流程只在发布记录上留“受影响的题目与 `rubric_version` 清单”。

### 8. 考情政策线（冲刺 4）
- 审核台给考情方案一个差异视图：与同城市同届别的上一版本、与上一届已发布方案比较（科目、满分、计分、合场、批次）；`policy.validate_scheme` 的结果逐条列出。
- 两位不同的 staff 经 `policy.sign_scheme(scheme_id, decision)` 签字，函数写 `reviewers`（改为账号引用，与第 3 节同一决定）与 `reviewed_at`；发布由签字身份调 `policy.publish_scheme`。
- 来源文件、招录行、学校的发布也收口到签字函数，kge 角色不再能直接改为已发布（`specs/policy-schema-v0.9/lessons.md`“审查提出、需要人决定的” #4）。
- **考情的复核人留在 policy 表本身，不在 `kge.content_review` 里再记一份**（规则 2）。版本记录导出时从两处合并。
- 录入工具不在本 spec：南京近 5 届回填是人的工作，工程侧的录入工具按 `specs/policy-schema-v0.9/spec.md` 待人确认 #7 放阶段 1。测试只用合成城市 `synthcity`（`docs/design/policy/data-entry-rules.md` 第 6 节）。

### 9. 时政过期下架与停用
- 每日体检把 `valid_until < 当天` 且仍为 published 的 `kge.current_affair` 改为 retired，并追加一条审核记录（`basis_code = expired_auto`，审核人为空、标明系统动作）。这是 KGE 第七章“过期由体检自动下架”，属于低风险自动动作。
- 停用内容版本：BFF 立即看不到；该学科没有其他已发布版本时，新选科返回 `not_launched`（0015），已在读的学生不受影响。
- “一键停用评分版本并重算受影响报告”（完整方案 v1.1 第十一章）：本流程只负责停用 `rubric_version` 的签字与登记；重算属于 KGE-04 与 SIE，另行对接。

### 10. 模型草稿
- 编译页面的叙述段由模型编译（KGE-03），进入本流水线时一律标来源 `model_draft`，只能走中、高风险路径，不能进低风险合并窗口。
- 题目、采分点、时政不接受模型草稿：契约 `generate_variation` 是“选题，不生成”，生成器另立项。
- 接入任何模型、用哪家模型编译公共层内容（KGE 第八章允许离线公共知识构建用 Claude；`docs/plan/底层大模型选型建议-V1.0.html` 要求生产只用境内已备案模型）属于 KGE-03 与模型路由的决定，是必停项，不在本 spec。

### 11. 版本记录（Gate 0 证据）
审核台后端提供只读导出（命令行与页面各一），每行一次发布：线、对象、版本号；内容版本的六段标识与所指地图版本；本次涉及的 `rubric_version`；页面提交号；签字人（staff 账号）、签字时间、依据；体检或校验报告摘要。`.claude/skills/gate-check/SKILL.md` 收集的“版本记录”即此导出。

## 范围

### 做

**冲刺 1（W3–W4）· 审核台 v0**
- PR 1（`[NEEDS-HUMAN]`：迁移、RLS 与权限）：kge 一个新迁移（编号按合并顺序，0015 之后）：`content_review` 只追加与新列、签字函数、`map_version_digest`、`retire_map_version`、`publish_map_version` 的新增检查与改授权；DB 测试、变异、攻击面登记（`db/tests/80_attack_surface.sql` 的 SECURITY DEFINER 函数表与越权矩阵）。
- PR 2：审核台后端（差异、风险分类、签字与发布调用、版本记录导出）与单测；位置默认 `services/learning` 下独立入口，待 KGE 代码位置 ADR 后迁移（待人确认 #10）。
- PR 3：审核台前端（`apps/web` 下独立的 staff 路由组，不与学生、家长页面共用导航）：地图版本列表、节点与边编辑、差异视图、校验结果、签字、发布；端到端测试。
- PR 4（`[NEEDS-HUMAN]`：权限）：staff 登录（按待人确认 #3 的决定）。
- 走查：一名教研在 staging 用函数主题草稿（KGE-01 导入的公共内容）独立完成一次节点审核发布，记录在本目录的 lessons.md（实现时新建）。

**冲刺 3（W7–W8）· 编译页面审核线**
- 页面仓库的分支保护与 CI 配置（仓库本身与托管平台由人开通，`[NEEDS-HUMAN]`：新增第三方服务）；合并请求模板。
- kge 迁移：`content_review.object_type` 加 `page`、`page_commit` 列（`[NEEDS-HUMAN]`）。
- 对账脚本与 Makefile 目标，纳入 `make check`（用合成导出文件测试，不连托管平台）。
- 审核台加“页面”入口：列出待审合并请求与对账状态，链到托管平台网页界面。

**冲刺 4（W9–W10）· 分级发布、内容版本、考情政策线**
- 风险分类器补全（页面、原子、考情）；低风险合并窗口的签字规则与事后抽检（固定种子抽样）；中风险抽检队列与“新版试用”标记（实现方式见待人确认 #11）；高风险两人。
- platform 迁移（`[NEEDS-HUMAN]`：迁移、RLS 与权限）：内容版本与准入的保护触发器、发布 / 停用 / 准入函数、`retired_at`、收回学习服务 UPDATE、BFF 可见性调整；同一 PR 改 `db/tests/10_fixtures.sql` 与星图种子的写法（先插草稿再经函数发布）。
- policy 迁移（`[NEEDS-HUMAN]`）：签字函数、复核人改账号引用、发布改授权、来源文件与招录行收口。
- 时政过期下架任务。
- 数学（与道法，若切片过门）走完一次“内容版本 + 地图版本”发布，导出版本记录。

### 不做
- 冲刺 2 统一复核队列（另起 spec）；人设语料线；通识内容线。
- 知识服务、编译器、编纂规范、语义体检本身（KGE-03）；按降级或停用评分版本重算掌握度与判断（KGE-04、SIE）。
- 题目、采分点的模型生成；接入任何模型供应商。
- 考情录入工具（阶段 1）；页面迁入 `kge.wiki_page`（阶段 1 审核台 v1）。
- 验证器注册表（`specs/learning-ingest-sprint1/lessons.md` #9 另立任务）。
- `item_exposure`；能力视图与题目主键加版本（跟随 ADR 11 与 kge lessons #6）。
- 修改 `evals/`、`docs/personas/`、`packages/contracts/`、`docs/plan/`。
- 在任何共享环境执行迁移；pilot 的发布由人执行（CLAUDE.md 第 9 条）。

## 验收标准（可测试）

### 冲刺 1 · 审核台 v0
**库（一次性 Postgres，合成数据）**
- [ ] `kge.sign_review`、`kge.sign_map_change`、`kge.publish_map_version`、`kge.retire_map_version`：以非 staff、停用的 staff、未设账号调用全部被拒；`starbuddy_kge` 调用全部被拒（42501）。
- [ ] `kge.content_review`：任何角色的 UPDATE、DELETE、TRUNCATE 被拒；直接 INSERT 一条 approved 记录被拒（只能经签字函数）。
- [ ] 没有批准记录的地图版本发布被拒；签字后草稿再改一个节点，发布因摘要不符被拒；重新签字后发布成功。
- [ ] 目标为该版本的结构性变更单只有一名签字人，或两次签字是同一账号，发布被拒；两名不同 staff 签字后放行。
- [ ] 版本号不大于同学科已有最大版本被拒；含结构性变更而只升修订号被拒。
- [ ] 直接 UPDATE 地图版本状态为 retired 被拒，经 `retire_map_version` 成功。
- [ ] 已发布地图版本上新增题目标签，未经批准不能改为 approved。
- [ ] BFF 读不到 `content_review`、`map_change`；攻击面测试登记全部新函数；变异测试覆盖每条新检查。

**差异与分类（单测）**
- [ ] 合成的两个地图版本：节点字段变化、节点停用、边增删、强弱变化、投影页码与原句变化、旧编码 `retire` 各有预期输出；同一输入两次输出逐字节相同。
- [ ] 每种 `change_type` 与每类字段变化的风险级别与 KGE 第四章表一致；未知改动判为高风险。

**端到端与走查**
- [x] 端到端测试：合成 staff 登录 → 打开草稿 → 改一个节点的“不包含” → 看到差异与校验结果 → 签字 → 发布 → 知识服务或 BFF 读到新版本，旧版本变为 retired。
- [ ] **v2.0 验收“教研可独立完成一次节点审核发布”**：一名教研在 staging 上完成上述流程，工程人员不代操作；版本记录导出里有这一行（签字人、时间、摘要）；耗时与问题写进 lessons。

### 冲刺 3 · 编译页面审核线
- [ ] 页面仓库：直接推送主分支被拒；CI 失败的合并请求不能合并；标为高风险的页面只有一名批准时不能合并（以托管平台配置截图与一次演示记录为证）。
- [ ] CI 对合成页面：引用不存在的节点、公式验算失败、含合成的学生标识，各被拦截一次。
- [ ] 对账：合成导出与审核记录一致时通过；删掉一条审核记录、改一个提交号、高风险只剩一名批准人，各报失败；对账失败时合并窗口跳过受影响页面（编译器侧测试，随 KGE-03）。
- [ ] `content_review` 的页面记录 `page_commit` 必须是 40 位十六进制；同一页面同一提交不能记两次。
- [ ] 一个知识点页从修改到发布走完：合并请求 → 批准 → 合并窗口 → `page.get` 返回的 `page_version` 能对回提交号。

### 冲刺 4 · 分级发布、内容版本、考情
**分级**
- [ ] 低风险改动不经事前签字进合并窗口，同一页面一天内第二次发布被推迟到下一窗口；事后抽检按固定种子抽样，结果记审核记录。
- [ ] 中风险改动进抽检队列；“新版试用”期间，未进入试用的请求仍拿到旧版本（实现按待人确认 #11）。
- [ ] 高风险改动没有签字不能发布；结构性与考情没有两名不同 staff 不能发布。

**内容版本与准入（DB 测试）**
- [ ] `starbuddy_learning` 直接 UPDATE `status`、`published_at`、`admission`、`admitted_at` 得到 42501；题目 INSERT 时 `admission = 'admitted'` 被拒。
- [ ] 已发布版本只能经函数改为 retired，改其他字段、改回草稿、删除全部被拒；已撤下版本任何改动被拒。
- [ ] 有骨架的学科发布时 `map_version_id` 为空、指向草稿或已撤下地图版本、指向别的学科，全部被拒。
- [ ] 同前五段下不大于已发布最大值的 `content_version` 被拒；签字后追加一条题目登记，发布因摘要不符被拒。
- [ ] 准入方向：`rejected → admitted`、`admitted → source_unverified` 被拒；目标 `admitted` 缺来源编号或审核等级不够被拒。
- [ ] BFF 看不到草稿、已撤下版本下的能力视图与题目；发布后 `enroll_subject` 对该学科不再返回 `not_launched`（学科目录非 planned 时），停用后新选科回到 `not_launched`。

**考情（DB 测试，合成城市）**
- [ ] 方案、来源文件、招录行、学校：非 staff 签字被拒；同一账号签两次只算一人；kge 角色直接改为已发布被拒；`policy.publish_scheme` 只能由签字身份调用。

**时政**
- [ ] 合成的过期时政在每日体检后变为 retired，并有一条系统动作的审核记录；未过期的不受影响。

**版本记录与 v2.0 验收**
- [ ] 版本记录导出含数学的一个内容版本（六段标识、所指地图版本、`rubric_version` 清单、签字人、时间、摘要）与其地图版本。
- [ ] 道法：若 K 流道法切片在冲刺 4 过门且 ADP-道法-01 已接入，同样有一行；否则 lessons 如实记录。
- [ ] **“四科各发布一个经闸门的内容版本”**：按待人确认 #2 的口径在 lessons 里逐科记录；英语按对照文档决定 #1 标“搁置，不计”。

**通用**
- [ ] 严格模式 `make check` 通过，包含新的 DB 测试、变异、攻击面检查、差异与分类单测、对账测试。

## 涉及的必停项（见 CLAUDE.md）
| 必停项 | 是否涉及 | 在哪里 |
|---|---|---|
| `db/migrations` 下的任何文件 | **是** | 冲刺 1 kge 迁移；冲刺 3 kge 迁移（页面记录）；冲刺 4 platform 迁移与 policy 迁移。每个迁移只改一个 schema（ADR 02），先在副本演练 |
| RLS 与权限策略 | **是** | `content_review` 收回 UPDATE；发布函数改授 `starbuddy_review`；收回学习服务对状态与准入的 UPDATE；BFF 可见性调整；staff 登录与会话。全部附测试 |
| 计量与权益逻辑 | **否，但有行为影响** | 不改计量代码。发布与停用决定 `enroll_subject` 是否返回 `not_launched`（0015 第 571–573 行），这是计量 v2 已确认的门控 |
| `packages/contracts` | **否（默认）** | 页面提交号按 ADR 12 映射到已有的 `page_version`；“新版试用”若要 `page.get` 带版本选择就变成“是”（待人确认 #11） |
| 判分与掌握计算逻辑 | **否** | 本流程只登记 `rubric_version` 的批准与停用；重算属于 KGE-04 |
| 判断模板、禁用推断词表、画像可见范围 | **否** | 公共层内容；CI 的个人信息拦截不使用 SIE 词表 |
| `evals/` 金标准 | **否** | 不读不写 |
| 人设 Prompt、人设语料、禁语表 | **否** | 页面体检的人设中立规则来自编纂规范 v1（KGE-03），不改 `docs/personas` |
| 新增第三方服务、模型供应商或数据出境 | **是（冲刺 3）** | 页面仓库托管平台（ADR 12 待人确认 #1，须境内）。模型编译属于 KGE-03 |

含迁移、权限或第三方的 PR 首行写 `[NEEDS-HUMAN]`。

## 依赖与输入
- **人执行的迁移**：0011、0012、0013 在冲刺 1 开始前由人在开发库执行（均为草稿）。
- **K 流**：KGE-01 骨架与阿鸦图谱导入（冲刺 1，同期；审核台 v0 需要一个函数主题草稿版本）；KGE-02 题库补六段标识（冲刺 2）；KGE-03 编译器、编纂规范、合并窗口（冲刺 3）；道法切片（冲刺 4）。
- **ADR 签字**：ADR 12（页面仓库）；ADR 11（星 = 已发布地图版本的知识节点）；KGE 代码位置的 ADR（未起草）。
- **教研**：审核台 v0 的走查人；阿鸦六段标识确认（`specs/adapter-aya-sprint1/lessons.md` 待人确认 #1）；高风险页面类别（ADR 12 待人确认 #2）；抽检比例与合并窗口时间（ADR 12 待人确认 #3）。
- **法务**：题目来源与授权核实口径（`specs/inventory-aya/decisions.md` #3）。没有它，阿鸦题不能升为 `admitted`，只有 A 级、已准入内容的证据能推动星亮（4-C），数学星点不亮。
- **4-H 统一壳**（`specs/unified-shell-sprint4/spec.md`）：家庭登录；staff 登录是否复用同一套会话机制见待人确认 #3。
- **计量 v2**：`enroll_subject` 门控已确认（`specs/metering-v2/spec.md`）。

## 与同期 spec 的协调
四份冲刺 4 spec 同时按 v2.0 改写（`specs/sprint4-v2-rewrite/spec.md`），以改写后的复核为准。与本 spec 有关的约定：
1. **迁移编号**：0010–0015 已被 insight、kge、platform、policy、计量 v2 的草稿占用；各 spec 的新迁移按合并顺序在 0015 之后排定。原稿里“0010 给 4-H、0011 给 4-G、0012 给 4-C”的约定作废。
2. **已发布课程版本的测试数据**：内容版本保护上线后，已发布版本与已准入题目只能经发布函数产生。本流程的冲刺 4 PR 同时改 `db/tests/10_fixtures.sql`、`db/seeds/dev/star-map-synthetic.sql` 与 4-H 已有的测试种子；4-H 的验收用合成已发布版本，不以本流程为门禁。
3. **4-C（KGE-04）**：读取本流程发布记录上的“受影响的题目与 `rubric_version` 清单”做重算；“只有已准入、A 级证据推动星亮”依赖本流程的准入发布。
4. **4-D（阿鸦）**：`generate_variation` 从已发布的题目族选题；阿鸦内容包格式若升级（逐题来源），由本流程先定格式（待人确认 #9），4-D 在其上加字段。
5. **Gate 0 口径**：本流程“四科各一个版本”与 4-D 的契约覆盖率、4-H 的“进入四科”同属 Gate 0 评审决定的事项（待人确认 #2）。

## 已决事项（引用决定记录，不再列为待人确认）
- 英语搁置、不为其准备证据：`specs/plan-v2-alignment/spec.md` 第三节决定 #1。
- 标准团队模式，数学与道法切片都在 Gate 0 过门：同上，决定 #10。
- 审核台后端写公共层用 `starbuddy_kge`：`specs/kge-schema-v0.9/spec.md` 待人确认 #1（已确认）。
- 题目准入只在 `platform.item.admission`，kge 不存第二份：同上，设计要点 1（已确认）。
- 考情政策库写入方复用 `starbuddy_kge`、两人复核：`specs/policy-schema-v0.9/spec.md` 待人确认 #1（已确认）。
- 时政生效与失效日期、Rubric 版本：迁移 0011 已建（`kge.current_affair`、`kge.scoring_point.rubric_version`）。
- 发布即上线，选科门控按已发布课程版本：`specs/metering-v2/spec.md`（已确认），0015。
- 签字人数：低风险无事前签字、中高风险一人、结构性与考情两人：KGE V1.1 第四、五章；0011、0013 的两人 CHECK。
- 审核入口是网页审核台，页面走 Git 合并请求：开发计划 v2.0 冲刺 1 验收；ADR 12（草稿，签字后生效）。
- ADR 编号顺延（v2.0 的 ADR 10 即本仓 ADR 12）：`specs/plan-v2-alignment/spec.md` 第三节 #2（已确认）。

## 待人确认
1. **任务包编号**：v2.0 没有给 G 流编号。默认记为 `PLT-G1`（审核台 v0）、`PLT-G3`（页面审核线）、`PLT-G4`（分级发布）；也可以按 ADR 14 的格式另定。
2. **“四科各发布一个经闸门的内容版本”的口径**：
   - 数学能发布；道法取决于冲刺 4 切片过门与 ADP-道法-01；语文阶段 0 没有地图版本，文砺适配器在冲刺 2；英语搁置。
   - 默认：数学、道法各一个真实版本（道法未过门则如实记录）；语文若文砺已接入，发布一个 `map_version_id` 为空的内容版本并注明；英语不计。Gate 0 这一条按实际记录，由 Gate 0 评审决定是否带条件进入阶段 1。
3. **staff 登录**：v2.0 验收要求教研独立操作，现在没有任何 staff 登录。
   - 默认：审核台后端单独实现 staff 登录（账号由人建，口令 + 第二因素，会话只在审核台后端），只部署在 dev / staging；pilot 前随 PLT-01 修订的“后台人员按岗位 RBAC 与审计”（对照文档第八节）统一。
   - 另一个选择：复用 4-H 的家庭登录会话机制，按 `account_kind` 分流。
4. **审核人身份改为账号引用**（`specs/kge-schema-v0.9/lessons.md` 待人确认 #2、`specs/policy-schema-v0.9/lessons.md` 待人确认 #4）：默认 `content_review`、`map_change`、`policy.*` 的审核人改为 staff 账号 ID，由签字函数写入；现有自报工号列保留为可空的展示字段，还是删除？默认保留为可空，不再作为身份依据。
5. **发布前必须有批准记录，在库层强制**（kge lessons 待人确认 #1）：默认库层强制（第 5 节）。另一个选择：只由审核台流程保证。
6. **已发布地图版本上新增原子的审核**：默认题目标签与时政在库层要求批准记录；采分点新 `rubric_version` 由知识服务只返回已批准版本保证。是否也在库层给 `scoring_point` 加“已批准”状态列（迁移）？
7. **阶段 0 没有语义体检时中风险怎么办**：默认按高风险处理（必审，不做小流量）。另一个选择：只做程序体检后按中风险抽检发布。
8. **发布前影响分析的范围**：默认审核台 v0 只给公共层计数；“哪些学生画像受影响”由 KGE-04 / SIE 在发布后按版本计算，审核台只显示结果链接。
9. **阿鸦内容包与准入副本**：要让准入升级在适配器侧生效，内容包要能逐题标来源并按平台导出的准入决定重建。默认本流程冲刺 4 定格式、改构建脚本；另一个选择是改共享核对库让平台登记直接覆盖适配器声明（改 `packages/contracts`，必停，并改变“适配器只能更严”的语义）。
10. **审核台代码位置**：KGE 与洞察服务的代码位置待 ADR。默认后端先放 `services/learning` 下独立入口与独立连接角色，前端放 `apps/web` 的 staff 路由组；ADR 定了再迁。
11. **“新版试用”小流量的实现**：公共层请求不带学生标识（规则 13），分流只能在编排层做；而 `page.get` 的请求里没有版本参数，编排层无法指定试用版，要加就是契约变更（必停）。
    - 默认：阶段 0 不做小流量，中风险页面全量发布前先抽检（与 #7 一起定）；小流量随契约 v1.x 另议。
    - 另一个选择：本 spec 冲刺 4 就在 `page.get` 加可选的版本参数，编排层按学生分桶请求试用版（改 `packages/contracts`）。
12. **测试夹具怎么产生已发布版本**：保护触发器也拦超级用户。默认夹具改为“先插草稿，再以合成 staff 账号经函数发布”；另一个选择是触发器对 `session_replication_role = replica` 放行，更省事但留了绕过口。
13. **判分一致率是否阻断发布**（完整方案 v1.1 第十二章“可信度（发布阻断）”）：`evals/` 目录还不存在。默认阶段 0 只在版本记录里记“未评测”，不阻断；Gate 1 前按 v2.0“发布阻断指标无超阈值”补上。
14. **学习服务是否拒绝未发布内容版本的请求**：现在不检查。默认不改学习服务，pilot 靠“只登记、只发布经闸门的内容”保证；另一个选择是学习服务写入前核对内容版本已发布。
15. **来源与授权材料放在哪里**：默认不进仓库、不进平台库，只记编号，由法务或教研保管；另一个选择是放受控对象存储（新增存储路径，按 ADR 09 登记）。
