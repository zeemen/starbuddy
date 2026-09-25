# 知行（道法 · zhixing）阶段 0 盘点

状态：草稿，等人确认
关联：完整方案 v1.0 第 5.4、5.5 节 · 开发计划阶段 0 冲刺 0 · 模块清单依据 docs/subjects/zhixing-道法PRD-V1.1.md（F1–F11）
盘点日期：2026-09-24 · 方式：inventory-auditor 子代理只读盘点（没有执行脚本、没有装依赖、没有连库、没有访问线上域名）

## 路径约定与关键前提

- **Z** = `legacy/zhixing`
- **S** = `legacy/wenli/初中语文_AI_class/wenli`（文砺 monorepo 副本）
- **D** = `docs`

`legacy/zhixing` 只是原 wenli monorepo 里 `apps/daofa` 的外壳，共 36 个文件：页面、API 路由、4 个组件、配置和文档。判分 Rubric、Prompt、人设、知识图谱、Schema、迁移、单测全在 `S/packages/shared` 和 `S/supabase/migrations`（37 个 `daofa-*` 文件，另有 5 个 `build-*` Prompt 文件）。只看 Z 会漏掉大约 70% 的实质内容。本盘点把两处合起来看。

读取 `.env*` 被 `.claude/hooks/guard.py` 拦截，所以没有看过任何 env 内容。

---

## 1. 代码与部署

### 1.1 技术栈

| 项 | 事实 | 证据 |
|---|---|---|
| 包 | `@wenli/daofa`，唯一依赖 `@wenli/shared` | Z/package.json:2,12-14 |
| 框架 | Next.js 16.2 + React 18 + TS strict + Tailwind | S/package.json；Z/tsconfig.json:7 |
| 构建 | `next build --webpack`，dev 端口 3002 | Z/package.json:7-9 |
| 共享层 | `@shared/*` 指向 `../../packages/shared/*` | Z/tsconfig.json:19 |
| 运行时 | API 全部 `runtime="nodejs"`，`maxDuration` 30–60 秒 | Z/app/api/m3/grade/route.ts:20-21 |
| 规模 | 14 个页面路由、13 个 API route、2 个 auth route、4 个组件、3 个脚本、1 份文档 | — |

**在当前仓库里构建不了**：
- `@shared` 会解析到 `starbuddy/packages/shared`，这个目录不存在。
- `vercel.json` 假定在 wenli monorepo 根目录执行（Z/vercel.json:4-5）。
- `next.config.mjs` 会从 `../../` 读 `.env` 注入环境变量（Z/next.config.mjs:8-24）。放在 starbuddy 下时，它会读 starbuddy 根目录的 env，这是一个意外的配置耦合。

### 1.2 依赖与许可证
- 实际依赖来自 S/package.json：next、react、@supabase/ssr、@supabase/supabase-js、@anthropic-ai/sdk、zod、framer-motion、@napi-rs/canvas（均为 MIT），pdfjs-dist（Apache-2.0），dev 依赖 tailwind、vitest、playwright。
- 没有 LGPL/GPL 依赖，没有引入 OpenMAIC 或 mathml2omml。
- 许可证按包的公开许可证判断，**没有从 lockfile 核验**。

### 1.3 部署
- vercel.json 依赖 monorepo 根目录。
- DEPLOY.md 是操作说明，不是部署证据：
  - 源仓库 `github.com/zeemen/wenli`，Root Directory `apps/daofa`（Z/DEPLOY.md:15-16）
  - 域名 `daofa.huabo.ai`（:45-53）
  - **与语文产品共用同一个 Supabase**（:75）
  - 环境变量键名见 :25-37
- 没有 `.vercel/`、部署日志或 CI，**线上是否真的部署过无法核实**。
- **middleware 鉴权**（Z/middleware.ts）：
  - 页面要求登录（:16-21）
  - `/api/` 整体白名单放行（:6）
  - Supabase 没配时整站不拦（:12）
  - 结果：**所有 API 都可以匿名调用，包括会调模型的接口**

### 1.4 git 历史
- `legacy/` 被 `.gitignore:1` 忽略，本仓库没有它的历史，Z 和 S 下都没有 `.git`。
- progress.md:32-43 列出的 12 个 commit 无法核实。原仓库应为 `github.com/zeemen/wenli`。

### 1.5 测试

| 类型 | 实际 | 备注 |
|---|---|---|
| Z 目录内测试 | 0 | — |
| daofa 单测（在 shared 里） | 19 个文件，约 76 个用例：`domain/daofa-*.test.ts` 14 个，`prompts/build-*.test.ts` 5 个 | 自述"20 文件"（progress.md:48）和"81 passed"（README.md:95）都和实际不一致 |
| e2e | 0 个涉及 daofa | S/e2e 的 5 个 spec 都是文砺的 |
| smoke.sh | 默认 19 项，`--full` 另加 2 项调模型的 | Z/scripts/smoke.sh:27-51 |

**smoke 自述和 middleware 对不上**：smoke 期望 `/chat`、`/admin/items` 等页面未登录时返回 200（smoke.sh:42-44）。但只要 Supabase 配好了，middleware 会返回 307。所以"19/19 通过"只可能在 Supabase 未配置、或加登录门禁之前出现。

### 1.6 部署层安全问题（只记位置）
1. **匿名调用模型、无限流、无计量**：`/api/chat`、`/api/m1/split`、`/api/m3/grade`、`/api/m4/anchor`、`/api/items/generate` 不要求登录，middleware 也放行 `/api/`。
2. **命题接口没有管理员门禁**：Z/app/api/items/generate/route.ts:20-28 任何人都能触发模型，登录用户还会写入 `daofa_item`（:55-73）。只有审核接口有 `getAdminUser`。
3. **错误信息原样透传**：S/packages/shared/lib/api/respond.ts:35-36 把 `e.message` 直接返回前端；S/packages/shared/lib/llm/openai.ts:72 会把上游响应体拼进错误信息。
4. **超时不一致**：LLM 超时 280 秒（openai.ts:20），路由 `maxDuration` 只有 60 秒，平台会先杀掉请求。

---

## 2. 真实可用状态（F1–F11）

判定口径：只看学生今天能不能真的用。线上部署无法核实，所以**没有模块能打"已上线"**。"可演示"的意思是配好 Supabase 和模型 Key 后，本地能跑通完整链路。

代码编号和 PRD 编号对应：M1=F1，M2=F2，M3=F3，M4=F5，M5=F7（五维）加 F9（家长摘要），M6=F10。

| PRD 模块 | 状态 | 已实现 | 缺失 / 与验收的差距 |
|---|---|---|---|
| F1 逆向拆题（P0） | **可演示**（4 个功能点实现 2 个） | 三要素、坑点、拆法，学生自拆逐项评判（Z/app/api/m1/split/route.ts:20-51；S/…/prompts/build-m1-split.ts:19-55）；题型案例 24 个，注明"非真题，needs_review"（S/…/domain/daofa-m1-cases.ts:5-6）；自拆回流审题力 | **选择题干扰项生成和标注仅方案**（全库没有"干扰"相关代码）；"与教研一致率 ≥90%"没有评测集 |
| F2 开卷定位 + 限时（P0） | **可演示**（内容是雏形） | 统编 30 个节点 + 读本 6 个脚手架节点（S/…/daofa-knowledge-map.ts:31-106）；定位题 10 道（daofa-locate-bank.ts:14-25）；确定性判定（daofa-locate.ts:14-45） | 页码是近似值（knowledge-map.ts:4）；节点没有高频设问、答题话术、真题回链；是节点选择题，不是说出页码；**材料解构器仅方案**；**作答不落库**（m2/locate/route.ts:25-31） |
| F3 采分点批改（P0） | **可演示** | 逐点给出判定档、证据句、标准、四类归因、升格建议（daofa-m3-schema.ts:34-58）；服务端 clamp 并重算总分（m3/grade/route.ts:84-90）；满分强制二评（:92-93）；落库和复核入队（:105-131）；申诉入队（m3/appeal/route.ts:23-31） | 内置题只有 **2 道**，共 6 个采分点；拍照 OCR：API 支持但页面不发送；**申诉没有消费端**；一致率从未测过；页面上的多 Agent 日志是**定时器播放的假日志**（Z/app/grading/page.tsx:12-19,61-62） |
| F4 学科语言润色（P0） | **仅方案** | 只有 F3 输出里的 `polish_suggestion` 字段 | 诊断文案引导学生去用 F4（daofa-diagnosis.ts:43），但这个工具不存在 |
| F5 时政挂接流（P0/P1） | **半成品** | 挂接考点、变式题、坑点，`needs_review` 强制为 true（m4/anchor/route.ts:21-53） | 页面硬编码 2 条种子新闻（Z/app/news/page.tsx:7-29）；没有抓取、白名单、落库、审核、发布 |
| F6 苏格拉底答疑（P1） | **仅方案** | `/chat` 只是通用人设对话 | 没有追问控制，也没有"3 轮后切讲解"的逻辑 |
| F7 错题变式 + 个人图谱（P1） | **半成品** | 五维 running average（S/…/server/daofa-ability-reflow.ts）；雷达图（Z/components/FiveDimRadar.tsx）；管理员按节点命题 | 没有错题本，没有按错因生成变式；学生看不到已审核通过的题（Z/README.md:114） |
| F8 语音抽背 / 闪卡（P1） | **仅方案** | — | 没有任何代码 |
| F9 家长门户与周报（P0） | **半成品** | 学生诊断页上有一张"家长视角"卡片（Z/app/diagnosis/page.tsx:102-108） | 没有家长端；daofa 表没有家长读取的 RLS；没有推送 |
| F10 全真合场模拟（P1/P2） | **半成品** | 两段计时器和配速判定（S/…/daofa-mock.ts:7-66） | 没有试卷：历史段让学生在纸上作答（mock/page.tsx:82），道法段只展示 1 道内置题 |
| F11 分支决策情境卡（P1） | **仅方案** | — | 没有任何代码 |

**统计**：已上线 0（无法核实）／可演示 3／半成品 4／仅方案 4。仅方案占 36%，接近开发计划风险表里 40% 的收缩阈值。

**不属于 F 编号、但代码里有的**：
- 命题引擎和命题审核台：可演示，仅管理员可用
- 大模型配置台：可演示，属于平台能力
- 伴学室 `/chat`：可演示，但已划归点点，不算知行模块

**自述与代码不符的地方**：

| 自述 | 实际 | 证据 |
|---|---|---|
| turbopack.root 消警告（progress.md:35,53） | 配置里没有 turbopack.root，构建强制 `--webpack` | next.config.mjs:28-36 |
| progress 说统一 pnpm | DEPLOY 说统一 npm（DEPLOY.md:4），两份文档矛盾 | — |
| "免登录跑确定性模块 M1/M3"（DEPLOY.md:69） | M1、M3 都调模型 | m1:27；m3:62 |
| 知识图谱"20→30 节点"（README.md:104） | 实际 30 + 6 共 36 个 | knowledge-map.ts |
| 五维"信号源已全齐"（README.md:23） | 诊断只统计 M3 的四维，时政敏感度不参与 | daofa-diagnosis.ts:75-85 |
| 人设"去年道法 58 分"（README.md:5） | 代码里是"去年道法满分" | daofa-persona.ts:22 |
| 题库"含南京卷真题（教研已审核）"（question-bank.ts:4,59,205） | README 诚信边界说没有生成真题（README.md:102），两处互相矛盾 | — |
| 多 Agent 编排（PRD 7.0） | 每个功能一次 LLM 调用，前端 Agent 日志是装饰 | grading/page.tsx:12-19 |

---

## 3. 数据库

### 3.1 迁移位置
- Z 里没有迁移文件。
- 知行相关迁移在 S：`S/supabase/migrations/0033_daofa_m3.sql`、`0034_daofa_ability.sql`、`0035_daofa_item.sql`。
- **这几份迁移是否已应用到线上、线上 RLS 是否和文件一致、有多少行数据，都无法核实。**

### 3.2 表结构（按文件推断）

| 表 | 主要字段 | 来源 |
|---|---|---|
| `daofa_submission` | student_id→profiles、question_id(text)、persona、answer_text、image_path（未使用）、graded_json、total_score、needs_human_review、model | 0033:10-22；写入时 `model:"routed"` 写死（m3/grade:117） |
| `daofa_review_queue` | submission_id、student_id、reason、status、resolved_by、resolved_note | 0033:28-37 |
| `daofa_ability` | student_id、dimension（五维中文名）、avg_rate、sample_count | 0034:7-15；reflow 会吞掉异常（daofa-ability-reflow.ts:41-46） |
| `daofa_item` | created_by、node_id、题面、rubric jsonb、difficulty、status、reviewed_by | 0035:7-23 |
| 共用（文砺） | `profiles`（guardian_ack、lang_model_id）、`admin_user`、`model_option`（Key 以 AES-GCM 密文存）、`app_settings`、`review_status_t` | 0001、0002、0006、0007 |

题库、Rubric、知识图谱、定位题、M1 案例**都不在库里**，是代码资产（0033:4-5）。

### 3.3 RLS 与关系建模
迁移文件里的策略：
- submission 和 ability：owner `for all`
- review_queue：学生只能插入和读取自己的
- item：作者 `for all`，已通过的题所有登录用户可读

**文件层面的 RLS 漏洞**（线上是否一样待核实）：
1. **学生可以把自己生成的题改成已通过**：`daofa_item_owner` 的 `with check` 只检查 `created_by`，不限制 `status`（0035:30-33）。改完之后所有人可读，M3 批改也会使用。**审核门禁可以被绕过。**
2. **判分和能力行学生可写**：submission 和 ability 都是 owner `for all`（0033:49-52；0034:20-23），违反 CLAUDE.md 第 3 条。
3. **申诉可以挂到别人的作答上**：插入时不校验 `submission_id` 是不是本人的（0033:58-60）。

**学生与家长关系**：知行没有建模。
- daofa 表上没有家长读取策略。
- 文砺的 `guardians` 表是按邀请码绑定的（0019_guardians.sql），**不按手机号**，但它没有覆盖 daofa 表。
- 知行登录只用邮箱加密码。
- 结论：**没有违反"禁止按手机号关联"，因为家长访问根本没有实现。**

**未成年人同意**：注册勾选"已满 14 岁或已获监护人同意"，属于学生自己声明，写进 user metadata（Z/app/login/page.tsx:31,47-48,85）。它和 `profiles.guardian_ack` 是否同步，看不到代码证据。

### 3.4 admin 权限
- `admin_user` 白名单，用户只能读自己那一行（0006:4-13）。
- 跨用户写操作走 service role（admin/items/route.ts:52）。`adminClient` 只在服务端 route 里用。
- `model_option` 只有 service role 能访问。

### 3.5 种子数据
- `Z/scripts/seed-admin.sql` 用的是占位邮箱，没有真实账号或密码。
- bootstrap.sh 默认 dry-run，没有执行。

---

## 4. 模型调用

| 项 | 事实 | 证据 |
|---|---|---|
| 入口 | 所有调用都走 `callText("grade", …)`，**对话也走这个**；有图片时走 `callVisionText` | chat:33；m1:27；m3:51-62；m4:28；items/generate:28 |
| 路由优先级 | 用户 `lang_model_id` → `model_option` active → `app_settings.model_routes` → env 默认值 | S/packages/shared/lib/llm/index.ts:93-182 |
| 供应商 | anthropic（官方 SDK）；openai 兼容（DashScope、DeepSeek、智谱等） | index.ts:21-24；openai.ts:1-6 |
| env 默认 | grade、author、plan 用 `claude-sonnet-4-6`，diagnose 用 `claude-opus-4-8`，**都是境外模型** | index.ts:27-52 |
| 线上实际模型 | 由库里配置决定，**无法核实**（DEPLOY 列了 DashScope、DeepSeek 的 key 名；progress 自述用"真实 DashScope"跑过 smoke） | — |
| 可切换 | 管理员运行时可增删、启停、测连通；按用户选模型的后端逻辑存在，但知行没有做界面 | admin/models/route.ts:41-192 |
| 强制 JSON | OpenAI 兼容通道一律 `json_object`，对话也用 `{"reply":…}` 承载 | openai.ts:39,86 |
| 流式 | **没有** | openai.ts:35-77；anthropic.ts |
| max_tokens | chat 1500（最坏情况两次）；M1 1800；M3 3200；M4 2000；命题 2600（一次 3 个难度并行） | 各 route |
| 重试 | 只对网络瞬时错误重试 1 次；**没有幂等键** | openai.ts:25,44-69 |
| 计量 | **完全没有**：没有 usage_id、idempotency_key、token 记账或限流 | grep 无结果 |
| model_version | 写死 `"routed"`，实际模型名没有返回给调用方 | m3/grade:117 |
| Key 位置 | 只在服务端（env 或 AES-GCM 加密存库）；源码扫描没有发现硬编码 Key | admin/models:117 |
| Prompt 位置 | S/packages/shared/lib/prompts/build-{chat,m1-split,m3-grade,m4-anchor,item-generate}.ts、domain/daofa-m3-rubric.ts、domain/daofa-persona.ts；**没有版本号** | — |

**平均 token 粗估**（按字符数估，不是实测）：

| 请求 | 输入 | 输出 |
|---|---|---|
| chat | 约 0.8–3k | 约 0.1–0.3k |
| M1 | 约 0.7–0.9k | 约 0.2–0.3k |
| M3 | 约 2.0–2.8k | 约 0.8–1.5k |
| M4 | 约 2.0–2.5k | 约 0.4–0.7k |
| 命题 | 约 0.6–0.8k ×3 | 约 0.6–1.0k ×3 |

**跨境风险**：未成年人作答原文和对话内容会发给当前路由到的模型。如果落到默认的 Anthropic 路由就是出境，与总纲"不默认调用境外 API"冲突。

---

## 5. 评测资产

| 资产 | 现状 |
|---|---|
| 判分评测集 / 金标准 | **没有**。题库里有 3 份带期望判分的样卷（question-bank.ts:122-200,263-300），但没有测试拿模型去跑它们 |
| 与教师一致率 | 从未测过（PRD 要求 ≥90%） |
| 人设评测 | 只有 `PERSONA_PROBES` 5 条探针（daofa-persona-guard.ts:51-57），没有执行器 |
| 单测 | 19 个文件、约 76 个用例，都是纯逻辑，不调模型 |
| smoke | 只检查状态码和字段是否存在 |
| e2e | 0 个 |
| 本仓库 `evals/` | 没有知行的东西 |

---

## 6. 内容资产

| 资产 | 数量 | 审核状态 | 证据 |
|---|---|---|---|
| 内置主观题（带完整 Rubric） | 2 道，共 6 个采分点 | 自称"教研已审核"，没有记录 | S/…/daofa-question-bank.ts:54-305 |
| AI 命题（`daofa_item`） | 线上数量未知 | pending_review / approved / rejected | 0035 |
| 知识图谱节点 | 统编 30 个 + 读本 6 个（`phrases` 为空，页码"待核"） | 教研雏形，需要特级教师审校 | daofa-knowledge-map.ts:4,31-106 |
| 定位题 | 10 道 | 雏形 | daofa-locate-bank.ts |
| M1 题型案例 | 24 个（8 种题型） | needs_review，非真题 | daofa-m1-cases.ts |
| 时政 | 硬编码 2 条 | 没有来源，没有白名单 | Z/app/news/page.tsx:7-29 |
| Rubric 覆盖 | 只覆盖 2 道内置题和审核通过的 AI 题；AI 题的 `common_errors` 是空的 | daofa-item-map.ts:27 |

**版权与合规风险点**：
- 知识图谱 `phrases` 逐句摘录统编教材（knowledge-map.ts:36-83）。读本填充指南要求"逐字抄原文"，源 PDF 在仓库外（Z/docs/学生读本节点填充指南.md:33,48）。
- 两道内置题标注为"南京某区二模真题"和"2025 年南京中考真题改编"（question-bank.ts:59,205），涉及试题版权。
- 时政和变式题由模型自由生成，违反 PRD 第十章"受限 RAG 白名单"的要求；M4 结果不落库，`needs_review` 标记之后没有真正的审核动作。
- 没有六段内容标识（region、exam_year、grade、textbook_version、subject、content_version）。

---

## 7. 人设实现与规格差距

**应该是谁**：
- 道法老师是**方圆（考官型）**，只在 F3 批改、F10 结算、F11 收尾、申诉时出场（考官型基线 V1.1 附录 A，:230-247）。
- 课堂由**点点**主持（点点-规格书-V1.2.md:140）。
- 备考搭子 V1.0 已被点点 V1.2 替代。

| 规格要求 | 代码现状 | 差距 |
|---|---|---|
| 方圆作为判分的"脸" | 全库没有方圆 | 缺失 |
| 点点主持、跨科身份 | 仍是单科"学长/学姐"（daofa-persona.ts:18-55） | 需要迁移 |
| 年级刻意模糊 | 写成"去年中考道法满分……现在读高一"（daofa-persona.ts:22,34,46；layout.tsx:9） | **违反** |
| 判分中性、不鼓励（G 类） | M3 同一次调用里由学长风格写 `overall_comment`（daofa-m3-rubric.ts:41-44；build-m3-grade.ts:60-62） | 需要拆开 |
| AI 身份披露 | 对话页横幅、首页徽章、系统 Prompt 都有 | 基本达到；M1–M6 各页没有单独披露 |
| 禁语 A | 只拦开场句式（persona-guard.ts:15） | 部分覆盖 |
| 禁语 B | 有（:19-20,32） | — |
| 禁语 C | 正则（:14） | 部分覆盖 |
| **禁语 D（恋爱暧昧）** | **没有检测** | 缺失（未成年人红线） |
| 禁语 E | 单科产品，不适用 | 迁移后由平台负责 |
| 禁语 G | 没有；M2 文案反而有"快！……我当年的用时"（daofa-locate.ts:26-30） | 违反 |
| 守护覆盖面 | 只检查 /chat 的输出；M1、M3、M4 的人设文本不检查；**学生输入端不检测危机信号** | 缺口大 |
| 转介话术 | 靠 LLM 自由发挥（daofa-persona.ts:80），**没有确定性转介路径** | 高风险 |
| 时长节律 | 只有单会话发言数到 20 时提醒（chat/page.tsx:9,21） | 部分 |
| 家长端和学生端双轨 | 家长卡片放在学生页上 | 位置不对 |
| 三风格 | steady/roast/companion 与 V1.0 一致 | 可以作为点点语料的候选（需要教研审核） |
| 回归测试、版本化 | 都没有 | 缺失 |

---

## 8. 六方法对应

插件 API 映射和差距见 [adapter-design.md](./adapter-design.md) 第 1 节。

结论：
- **可以包装**：grade（M3 加上 M2 这个确定性验证器）、diagnose（纯函数）、report（字段提供者）、generate_variation（命题引擎）。
- **需要重写**：plan（只有静态文案）；teach 的 F4、F6、F11 仅方案，F1、F2、F5 的引擎可以包装，但要剥离人设文本。

---

## 需要人确认的疑点

1. **线上状态**：`daofa.huabo.ai` 是否部署过？有没有真实学生用过？共用 Supabase 里 daofa_* 表有多少行，是否含真实未成年人数据？需要负责人在合规前提下提供快照。
2. **线上模型**：`model_option` 当前 active 的是哪家？如果是 Anthropic 默认路由，未成年人作答已经出境。
3. **2 道内置题的来源和版权**：谁审的，有没有记录？这和 README 诚信边界的说法矛盾。
4. **3 份样卷**是合成数据还是来自真实学生？
5. **教材和读本原文**的使用授权与版本是什么？页码"近似值"对应哪一版？
6. **时政种子**的来源；白名单源由谁确定？
7. **能力坐标**：代码用"五维"（含时政敏感度），总纲写"过程四步"，以哪个为准？
8. **错因映射**：知行四类映射到七大类时，"转化错"归哪一类？需要教研签字（冲刺 0 验收项）。
9. **RLS 漏洞**（可以自批 approved、判分行学生可写、申诉不校验归属）：线上是否一样？如果有学生在用，是否需要立即处置？修复属于 [NEEDS-HUMAN] 必停项。
10. **匿名 API 和成本**：线上有没有被滥用的账单记录？
11. **smoke 的"19/19"**是否在 Supabase 未配置的环境里跑的？
12. **git 历史**：是否提供 `github.com/zeemen/wenli` 的只读访问来核实 progress.md 里的 commit？
13. **历史学科**：春秋、知行·史的 Rubric 分库什么时候建？M6 历史段现在是纸面作答。
14. **三风格语料**能否作为点点的种子语料？需要教研审核，并跑人设回归。
15. **未成年人同意**：是否迁到平台 `consent_record` 并做监护人核验？它和 `profiles.guardian_ack` 是什么关系？
16. **自伤和危机信号**目前没有确定性转介，是否作为知行接入平台前的阻断项？
17. **env 键名核对**：`.env.example` 被 guard hook 拦截。是否允许只看键名来核对环境变量清单？本盘点的键名来自 Z/DEPLOY.md:25-37。
