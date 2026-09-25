# 文砺（语文 · wenli）阶段 0 盘点

状态：草稿，等人确认
关联：完整方案 v1.0 第 5.1、5.4 节 · 开发计划阶段 0 冲刺 0 · 模块清单依据 docs/subjects/wenli-文砺v2-产品说明书.html 第 05 章（M1–M6）
盘点日期：2026-09-24 · 方式：inventory-auditor 子代理只读盘点（没有装依赖、没有跑测试、没有连库、没有访问线上域名）

## 路径约定

- **W** = `legacy/wenli/初中语文_AI_class/wenli/`
- git 根是 `legacy/wenli/`，远端 `github.com/zeemen/wenli`

守卫 `.claude/hooks/guard.py` 拦下了对 `W/.env.example`、密钥类路径、仓库根 `starbuddy-secrets-backup` 的读取，这些位置标为"未核查"。

**状态口径**："已上线"要有能运行的线上证据。本次拿不到线上证据，所以最高只标"可演示"：代码链路完整、页面接好了、PROGRESS 里有真实 LLM 联调的记录。

---

## 1. 代码与部署

### 1.1 仓库与 git 历史

| 项 | 事实 |
|---|---|
| 提交数 | 520 |
| 时间跨度 | 2026-06-29 至 2026-07-12，14 天 |
| 作者 | 1 人 |
| 最近提交 | 2026-07-12 `docs(deploy): 修正 student DEPLOY 两处上线实战坑` |
| 提交作用域前五 | writing 53、ui 36、reading 30、spec 28、plan 26、daofa 19 |
| 工作区状态 | 有 47 个未提交的删除，全部在 `apps/daofa/` |

**文砺仓库同时是知行（daofa）的宿主**：HEAD 里带着 `apps/daofa`、迁移 0033–0035，以及 shared 里约 30 个 `daofa-*` 文件。知行部分另见 `specs/inventory-zhixing/`。

### 1.2 技术栈

| 项 | 实际 | 与文档不符 |
|---|---|---|
| 框架 | Next.js **16.2.10**（App Router），强制 `--webpack` | PROGRESS.md:7 和总纲 5.2 都写 Next.js 14 |
| React | 18.3.1 | Next 16 + React 18 的组合需要确认 |
| UI | Tailwind 3，自研 `packages/shared/ui`，没有 shadcn/radix | 总纲写 shadcn |
| 后端 | Next Route Handlers，没有独立服务 | — |
| 长任务 | 没有 Railway。`jobs` 表是空壳（0002:16），长请求靠 `maxDuration=300` 硬撑 | 总纲写 Railway 长任务 |
| 数据 | Supabase（Postgres + RLS + Auth + Storage + pgvector） | — |
| 结构 | npm workspaces：`apps/student`、`apps/parent`、`packages/shared`（HEAD 里另有 `apps/daofa`） | — |

### 1.3 依赖与许可证
- 直接依赖：@anthropic-ai/sdk、@napi-rs/canvas、@supabase/ssr、@supabase/supabase-js、framer-motion、next、pdfjs-dist、react、zod。
- lockfile 共 296 个包：MIT 239、Apache-2.0 22、ISC 9，另有 **LGPL-3.0-or-later 14 个**。LGPL 的全部是 sharp/libvips 的平台二进制（Next 图片优化间接引入，可选依赖，动态链接）。按 CLAUDE.md 第 10 条，需要人判断是否可以接受。
- 没有 OpenMAIC 的 mathml2omml。

### 1.4 脚本
- `typecheck` 串联了 `apps/daofa`，daofa 在工作区被删后，这条命令在当前拷贝里必然失败。
- 以下脚本会写库，**本次没有执行**：`set-routes.mjs`、`kb:ingest`、`kb:ocr-notes`。

### 1.5 部署
- Student 端：`W/apps/student/vercel.json`；`DEPLOY.md:60-100` 写了域名 `yuwen.huabo.ai`、备用 `yuwen-seven.vercel.app`，**与 daofa 共用同一个 Supabase**，push main 自动部署。
- Parent 端：没有部署配置。
- `next.config.mjs:1-27` 会在构建时读仓库根的 `.env`。
- 没有 CI。
- 已部署的旁证：最后几个提交都是部署修复，PROGRESS.md:188 写"线上就绪"。无法验证。

### 1.6 测试（静态统计，没有运行）

| 类型 | 数量 |
|---|---|
| Vitest 单测 | 100 个文件、638 个用例（其中文砺 86 个文件、579 个用例），基本都是纯函数和 prompt 字符串断言 |
| RLS 集成测试 | 9 个用例，要设 env 才跑，默认 skip（`packages/shared/guardians/rls.integration.test.ts:14-16`） |
| E2E | 4 个 spec、9 个用例；有登录态的用例依赖真实测试账号，会连**共用的**真实 Supabase（`e2e/global.setup.ts:10-13`） |
| 覆盖率 | 没有配置 |

PROGRESS 自述的测试基线前后不一：338+9、394+9、506。

---

## 2. 真实可用状态（M1–M6）

| 模块 | 状态 | 代码证据 | 方案有、代码没有的部分 |
|---|---|---|---|
| **M1 学情诊断引擎** | **可演示** | 上传或粘贴 → OCR（视觉模型）→ `callTextJSON("diagnose")` → 写 `error_record` 和 28 格 `ability_state`（`apps/student/app/api/diagnose/route.ts:135-148,268-285`）；PDF 栅格化；试卷与答题卡配对；缺口补测 `api/gap-test`；面谈画像 `api/diagnose/intake`；自评与信号冲突（0020/0022）；雷达和热力图；位次区间（`prompts/diagnose.ts:46,52`） | 十法中的 ⑥⑦（CAT/DINA）没有做；⑨一致率基座只有 `calibration_sample` 表，**代码没有读写**；"30–45 分钟诊断卷"实际做成了缺口补测 |
| **M2 处方与学习计划** | **可演示**（缺动态升降阶） | `api/plan/route.ts` 生成 ROI 三层处方，写 `study_plan`（:138-141）；`prompts/plan.ts:47-65`；`domain/stage-cycle.ts`；`api/daily/today` | "连续三错降阶、三对升阶"：`domain/adaptive.ts:61 nextLevel` **没有调用点**，实际用的是 Beta 掌握度（`domain/mastery.ts`）；年度长期路线没有 |
| **M3 内容与学霸方法库** | **半成品** | 知识点由 AI 生成、管理员审（`api/admin/knowledge/generate`，maxTokens 32000，写 `knowledge_point` draft/approved）；一日一课；静态策展：名著 12 部、南京文脉、阅读方法 | 3–6 分钟微课没有；学霸方法库没有数据来源；三类课程体系没有；自述"只有七上 U1 27 条草稿待审"（PROGRESS.md:196） |
| **M4 命题与测评** | **半成品**（审核门禁形同虚设） | 定向命题写 `item_bank(pending_review)`（`api/items/generate/route.ts:50-76`）；模考 `api/exam/*`；每日小测 `api/quiz/*` | ① `/api/review/item` **只校验登录、不校验管理员**，用 service role 改审核状态（`review/item/route.ts:19-48`），也没有 UI 调用；② 已审题**没有消费方**，学生侧 quiz/exam/reading 都是**即时生成、不经审核**直接推给学生（`api/quiz/generate/route.ts:1-4`），违反方案；③ BKT/DKT 没有，11 维标签只是 jsonb，周测月测没有 |
| **M5 作文批改与升格** | **可演示** | `api/essays/grade/route.ts`：五维（主判/参考）、逐句 marks、升格路径、`uncertainty_note`，一类卷或模型自报时置 `needs_human_review`（:41-90）；Schema `domain/schemas.ts:225-253`；量表 `prompts/essay-rubric.ts`；视觉模型看卷面；写作支架（审题四问、苏格拉底、应用文、口语、辩论、限时） | 人工二评**只有标志位，没有队列**（`app/grading/page.tsx:334`）；修改前后对比、二次成文留痕没有；小作文量表只部分覆盖 |
| **M6 AI 陪练与错题复盘** | **可演示** | 三级提示加解析，前三级不给答案（`api/coach/hint`、`prompts/hint.ts:18,32-33`）；复盘节点"当天/3天/14天"（`review_log`）；复发预警（`app/mistakes`、`domain/review.ts:52`）；苏格拉底写作 | "多次无法推进才给完整解析"由前端按级数控制，不是引擎判定 |

**统计**：已上线 0（无法核实）／可演示 4（M1、M2、M5、M6）／半成品 2（M3、M4）／仅方案 0。按功能点看，M3、M4 的主要承诺还没有兑现。

**PROGRESS.md 与代码不符的地方**：
1. 写 Next.js 14，实际 16.2.10。
2. "⑨一致率基座 ✅"：`calibration_sample` 表没有任何读写。
3. "items 不直推学生"：审核接口没有管理员鉴权、没有 UI，其他生成路径都直推学生。
4. "全部迁移已上线"只覆盖到 0018。0019–0031 分节声称已 apply，0032–0035 没有 apply 记录。
5. 测试基线数字前后不一。
6. 工作目录路径与当前拷贝不一致；引用了仓库外的 `../文砺-M1诊断引擎修订.html` 和"记忆 nanjing-exam-fullmarks"。

---

## 3. 数据库

### 3.1 表清单
35 个迁移，1485 行，48 张表，**全部开启 RLS**。

- **学习者与证据**：profiles、ability_state（28 格 + data_confidence）、submission / submission_file、error_record（+error_tag）、study_plan、essay_review、review_log、semester_assessment、quiz_attempt、parent_report、point_mastery（Beta alpha/beta + 复习节点）、daily_checkin、mock_exam、mastery_snapshot、perception_card、socratic_session、self_assessment、signal_conflict、pattern_flag、diagnostic_session、calibration_sample、intake_profile、semester_score、ability_self_rating、assessment_event、stage_report、debate_session
- **内容**：item_bank / item_review、textbook_catalog / recitation_corpus / doc_chunks（pgvector）、knowledge_point、classical_text
- **系统**：jobs、app_settings、admin_user、model_option（Key 以 AES-GCM 密文存）、wechat_identity
- **家长**：bind_code、guardians、parent_consult_session、parent_action_plan
- **知行**：daofa_submission、daofa_review_queue、daofa_ability、daofa_item

### 3.2 RLS
- 学生私有表都是 owner 策略 `student_id = auth.uid()`（0001:211 循环统一加）。
- 已绑家长只读：`is_bound_guardian(sid)`，security definer，固定了 search_path（0019:53-66）。放行 SELECT 的有 19 张表。
- 内容表是"已审公开读、管理员写"。
- **风险**：`guardians_guardian_rw` 是 `for all`，条件只有 `guardian_id = auth.uid()`，with check 没有约束 student_id（0019:41-43）。家长可能凭 anon key 直接 insert 一行，**绕过邀请码绑定任意学生**。需要用 RLS 测试验证。

### 3.3 学生与家长关系
- **没有按手机号关联**。家长是独立账号，靠学生签发的一次性邀请码 `bind_code` 兑换进 `guardians(guardian_id, student_id, status)`，多对多，软解绑（0019:6-27；`apps/parent/app/api/bind/redeem/route.ts:57-65`）。手机号只用于 OTP 登录。结构上接近 `guardian_student_link`，可以直接映射。
- **合规缺口**：`profiles.guardian_ack` 在代码里零引用；一键删除、夜间限制、时长提醒都没有实现。

### 3.4 种子与行数（只能推断）
- 迁移里只有 `app_settings.model_routes` 一条种子。
- 知识库源文件：`data/catalog.json` 237 篇，`chunks.json` 1097 段（约 76.5 万字），`notes_chunks.json` 249 段，`corpus_candidates.json` 109 篇。推断线上 `doc_chunks` 约 1346 行，待确认。
- `knowledge_point` 自述 27 条草稿。

---

## 4. 模型调用

| 项 | 事实 | 证据 |
|---|---|---|
| 抽象层 | callText / callTextJSON / callVision*；路由：用户所选 → 全局 active → app_settings → env | `packages/shared/lib/llm/index.ts:95-278` |
| 供应商 | Anthropic SDK；OpenAI 兼容（DeepSeek、DashScope、智谱、OpenRouter） | `llm/anthropic.ts`、`llm/openai.ts` |
| env 默认 | diagnose、ocr 用 `claude-opus-4-8`；author、grade、plan 用 `claude-sonnet-4-6`；embed 用 `text-embedding-v3`，**默认是境外模型** | `llm/index.ts:28-50` |
| 线上实际 | 自述：文本 DeepSeek（`deepseek-v4-pro`），视觉 Qwen-VL | PROGRESS.md:8,187（无法核实） |
| bug | ocr 的 env 兜底读的是 DIAGNOSE 变量 | `llm/index.ts:33-34` |
| 学生自选模型 | 可以，`profiles.lang_model_id` | `llm/index.ts:104-111` |
| 流式 | **没有** | — |
| max_tokens | 默认 1500/2000/3000；提示 400–600、作文 2200、阅读 2600、诊断 4000、知识点 32000 | 各 route |
| 超时与重试 | 只有 OpenAI 协议有：280s，重试 2 次；Anthropic 路径没有 | `openai.ts:20,25` |
| 计量、幂等、配额 | **全部没有**；`subscription`、`min_tier` 也没有使用 | — |
| model_version | 写死 `"routed"` | `essays/grade/route.ts:90` |
| 鉴权 | 39 处调用点都要求登录 | — |
| RAG | pgvector `match_chunks`，k=4–10 | `rag/retrieve.ts` |
| Prompt | 集中在 `packages/shared/lib/prompts/*.ts`，TS 模板，**没有版本号**。最大的是 reading.ts（10.5k 字） | — |

**平均 token 粗估**（不是实测）：

| 请求 | 输入 | 输出 |
|---|---|---|
| 作文批改 | 约 3.5–5k | ≤2.2k |
| 诊断 | 约 6–10k | ≤4k |
| 提示 | 约 1–1.5k | ≤0.6k |
| 知识点生成（单次成本最高） | 约 6–8k | ≤32k |

---

## 5. 评测资产

| 资产 | 有无 |
|---|---|
| 判分金标准 | **没有**；`calibration_sample` 是空壳 |
| 人设评测 | **没有**（文砺没有人设守护） |
| Prompt 单测 | 有，只断言字符串和 schema |
| 领域纯函数单测 | 丰富，86 个文件、579 个用例 |
| E2E | 9 个用例（mock 数据是合成的） |
| RLS 测试 | 9 个用例，默认 skip |
| 教研规范 | `W/content/reading/`：错因标签 15 个（6 大类）、9 类题型 + 5 个难度类、R1–R9 达标标准。没有和判分做对照评测 |

---

## 6. 内容资产

| 资产 | 计数 | 来源与审核 | 版权风险 |
|---|---|---|---|
| 教材目录 | 237 篇（七 79、八 87、九 71） | 从教材 PDF 抽取 | 低 |
| 教材全文切块 `data/chunks.json` | 1097 段，约 76.5 万字，**已提交进 git** | 部编教材原文 | **高** |
| 古诗文原文 | 109 篇，候选，未审 | — | 底本和注释的版权需要确认 |
| 教辅笔记 `data/notes_chunks.json` | 249 段 | OCR 商业教辅；`scripts/ocr-notes.py:16-17` 的源文件名带 "(Z-Library)"，已进 RAG 底座和 git 历史 | **极高** |
| 名著地图 | 12 部 | 自写梗概 | 低 |
| 南京文脉 | 7 个母题 | — | 片段出处待确认 |
| 知识点 | 27 条草稿，0 条已审 | AI 生成 → 管理员审 | — |
| 题库 | 没有种子 | 审核链路不通 | 命题 prompt 要求原创 |
| Rubric | 作文五维 + 五档锚点 + 升格 + 扣分 + 反套路；阅读采分点；古诗文；非连续；应用文四维；口语三维 | 都内嵌在 prompt 里，**没有教研签字、没有版本** | "南京评分标准"的出处待确认 |
| 能力坐标 | 7 域 × 4 级 = 28 点 | `domain/ability.ts` | — |
| 错因 | 6 类 | 0001:16 | — |

---

## 7. 人设实现与规格差距

**代码里没有"妙笔"**，只出现在一个徽章文案里（`domain/badges.ts:75-76`）。老师身份散落在各 prompt 里，互不一致：
- "南京中考语文特级教师、苏派阅卷专家"
- "阅卷教师"
- "亲切的初中语文老师"
- "命题专家、阅读教练、审题教练、辩论对手/评委"

家长端是"AI 家长顾问"，不出现妙笔，这一点符合规格书 4.10。

| 规格条款（妙笔 V1.0） | 现状 | 差距 |
|---|---|---|
| 2.1 首次声明"AI 扮演" | 文砺 prompt 都没有 AI 身份披露 | 缺 |
| 2.1、5.2 比喻体系、笔帽、五色 | 没有 | 缺 |
| 2.3-1 判分引量表、引擎先判人设后说 | 分数来自 schema，但同一次 LLM 调用既判分又写评语 | 部分 |
| 2.3-2 主观维带不确定度 | 有 `by` + `uncertainty_note` | 基本有 |
| 2.3-3 提示不越级 | 三级加解析；"无法推进"由前端判定 | 基本有 |
| 2.3-4 伪归因禁用 | 没有禁用清单 | 缺 |
| 2.3-5 不代写 | 写作 prompt 有红线，**没有服务端拦截器和评测** | 部分 |
| 2.3-6 引用来自审核语料库 | 没有校验 | 缺 |
| 5.2 禁语 A–F | 没有禁语表和过滤器。**F 类（分数与位次承诺）与现有输出冲突**：诊断输出"全市位次区间"，处方输出"预计 +X 分"（`diagnose.ts:45-55`、`plan.ts:58,65`） | 缺，并有冲突 |
| 5.3 年级语气三档 | 没有 | 缺 |
| 4.9 转介话术 | **学生侧（面谈、苏格拉底、辩论等自由对话）没有任何危机筛查和转介**。家长侧有 `screenCrisisSignals`，但热线是占位（`apps/parent/app/consult/page.tsx:13-19`） | 学生侧缺（高风险） |
| 回归测试与漂移检测 | 没有 | 缺 |

---

## 8. 六方法对应

插件 API 映射和差距见 [adapter-design.md](./adapter-design.md) 第 1 节。

结论：diagnose、plan、grade、report 的引擎都可以包装。teach 的人设层要重写。generate_variation 的审核门禁要重写。

---

## 安全发现（只记位置与类型）

1. **授权缺陷（高）**：`apps/student/app/api/review/item/route.ts:19-48`，任何登录用户都能用 service role 把题改成 approved。
2. **RLS 疑似越权（高，需测试确认）**：`supabase/migrations/0019_guardians.sql:41-43`，家长可能自绑任意学生。
3. **密钥**：源码扫描没有发现 `sk-…` 或 JWT。`W/.env.example` 和 `starbuddy-secrets-backup/` 被守卫拦截，未核查。
4. **真实学生数据**：没有发现。fixture 和 mock 都是合成数据。`packages/shared/lib/auth/phone.test.ts` 里有 2 个手机号形态的夹具，需要确认是虚构的。E2E 会用真实测试账号连共用的 Supabase。
5. **版权**：Z-Library 来源的教辅 OCR 和教材全文都已进入 git 历史。

---

## 需要人确认的疑点

1. **线上状态**：`yuwen.huabo.ai` 是否在线？有没有真实学生在用？共用 Supabase 里是否已有真实未成年人数据？这决定模块能否改标"已上线"，也决定是否需要数据迁移和合规处置。
2. **daofa 归属**：HEAD 里的 `apps/daofa`、迁移 0033–0035、shared 里的 daofa 代码，是不是知行的主代码？工作区删除 `apps/daofa` 是有意的吗？（知行盘点已按"两处合并"处理。）
3. **版权处置**：Z-Library 教辅 OCR（249 段）和教材全文（1097 段）是否从平台内容底座剔除？是否需要重写 git 历史？
4. **两套掌握度**：`ability_state`（28 格）和 `point_mastery`（知识点级 Beta）迁到平台后，谁是唯一真相源？
5. **位次和提分预测**：说明书允许，但妙笔 F 类禁语禁止。以哪个为准？学生端是否保留？
6. **即时生成题**不经教研审核直接给学生，这是否违规？还是可以认定"不入库就不受约束"？
7. **`guardians_guardian_rw` 越权**：需要用 RLS 测试验证；现有 9 个用例是否覆盖这个场景？
8. **迁移应用范围**：0032–0035 是否已在共用 Supabase 上 apply？
9. **LGPL 依赖**：sharp/libvips 是否可以接受？
10. **Next 16 + React 18** 能否正常构建运行？
11. **测试基线**：以 338、394、506 中的哪个为准？需要在隔离环境里跑一次确认。
12. **仓库外文档**：`文砺-M1诊断引擎修订.html` 和南京满分设定（初一初二 100、初三 120）是否补进 docs？
13. **手机号夹具**：`phone.test.ts` 里的号码是否确为虚构？
14. **守卫拦截的位置**：`W/.env.example` 和 `starbuddy-secrets-backup/` 需要人工确认不含真实凭据，或已被正确 ignore。
