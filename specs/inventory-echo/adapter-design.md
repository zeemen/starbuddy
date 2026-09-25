# 开口成章（echo）适配器设计初稿

状态：草稿，等人确认。**不开始实现。**
依据：本目录 inventory.md；完整方案 v1.0 第 5.4、5.5、5.8 节；开发计划精简方案（:146）与冲刺 3；考官型基线 V1.1 附录 C；点点 V1.2；七科角色规范 v1.1。
E = `legacy/echo/Ai-english-claude/echo-quest`

> 契约字段、`echo.*` schema、计量、雅典娜/点点/NPC 的人设 Prompt、新增 ASR/TTS/评测供应商与数据出境路径，都属于必停项，落地时 PR 首行必须写 [NEEDS-HUMAN]。

---

## 0. 阶段 0 的现实路径（建议）

按开发计划的精简方案：
- **实时对话留在原产品里运行，适配器不接实时链。**
- 适配器先接三项：
  - 异步 **grade**：录音 → 评测 → Rubric
  - **diagnose**：只读取证据
  - **report**：作为字段提供者
- 前提：疑点 1（Gemini 链路在哪）必须先弄清楚。如果另有 Gemini 仓库，本设计要按那个仓库重做盘点。

---

## 1. 六方法映射

所有方法都要返回 `result / evidence_ids / confidence / source_version / model_version / human_review_required`。

| 插件方法 | 复用的现有代码 | 做法 | 主要差距 |
|---|---|---|---|
| **grade**（冲刺 3 首接） | `server/rubric.mjs`（buildRubricPrompt、scriptedRate）、`handlers.mjs` 里的 `handleRate` | 包装成异步 grade：A/B/C 由 LLM Rubric 判，`scriptedRate` 作为低置信兜底和对照 oracle，**D 维只能来自发音评测引擎**，没有引擎时 D 维输出 null，并强制 `human_review_required` 或低置信 | 没有发音引擎；没有 ASR 时间戳所以 C 维不完整；LLM prompt 缺 uncertain 规则；缺溯源字段、双评、复核队列。雅典娜只负责宣判模板，不参与判分 |
| **diagnose** | 每轮的 `student_turn_analysis`（types.ts:150-161）、RubricResult、completionConditions 的命中情况 | 这些只作为 evidence_event 输入，由平台掌握估计负责聚合 | 没有入口筛查（F02）；weakPoints 是写死的；五维 skills 是启发式增量，**不能迁移**，要重算 |
| **teach** | `handleNpc`、`promptAssembler.mjs`（七段）、`scriptedEngine.mjs`、`SkyeHint` | 阶段 0 不接入平台；阶段 1 包装时**拆开**：NPC 只负责叙事对话，`task_complete/errors` 另走验证器（completionConditions 规则）或 grade | NPC 判定职能与"场景角色不判分"冲突；鉴权、计量、流式都缺；Skye 改为点点 |
| **plan** | `SCENE_ORDER` 线性解锁（scenes.js:338、store.ts:171-183） | 由平台编排；echo 提供五步单元闭环模板和前置关系 | 不自适应，没有档位推荐，没有支线推送 |
| **generate_variation** | `units.js` 的 `patterns.family`；场景重玩 | 阶段 1 新建（F17） | 基本没有 |
| **report** | `ParentReport.tsx`、`CompletionOverlay` 的字段 | 字段提供者，由平台报告引擎合成双语报告 | 现在是纯前端读本地 store，而且家长报告放在学生设备上 |

---

## 2. 判分溯源字段差距

| 契约字段 | NpcTurn | RubricResult | 适配器做法 |
|---|---|---|---|
| evidence_ids | 无 | 无（只有 `A.evidence_zh` 文本和 `valid_sentences`） | transcript 句子、ASR 片段、评测结果都落为 evidence_event |
| confidence | 无 | 无 | 按维度给出；D 维没有引擎时置 null 或低值；ASR 质量信号（如果有）要参与 |
| source_version | 无 | 无 | Rubric、prompt、units、topics 加版本号和六段标识 |
| model_version | 只有 `source: llm \| scripted` | 同左 | 由平台模型路由回传；脚本判分记算法版本 |
| human_review_required | 只有 `flag_review`（语义是安全审核） | 无 | 低置信、D 维缺失、双评分歧时置真；安全审核另开一个 flag，两者语义分开 |

---

## 3. 数据归属

**迁到 platform.***：

| legacy 字段 | 平台对象 |
|---|---|
| studentName、englishName | 学生档案（英文名可以在 echo 里冗余一份，用于 NPC 称呼） |
| guardianConsent | `guardian_student_link` + 同意记录（现在的自声明勾选作废） |
| skills 五维 | `skill_node(subject=echo)` + `mastery_estimate`（需要重算） |
| vocab | `skill_node(echo, local_id=word)` + evidence_event |
| completedNodes、stats、listenStreak、selfCorrections | attempt、evidence_event（自我修正单独作为高价值信号） |
| towerBest、RubricResult | attempt + grade 结果（带溯源） |
| weakPoints、每轮 errors | `error_attribution`（英语三维映射到跨科大类，待教研签字） |

**留在 echo.***：
- 内容表：场景、节点、完成条件、NPC 卡、词表与阈值、话题，补六段标识和审核状态
- 对话会话、回合原文、NPC 叙事记忆、好感度。NPC 记忆只存叙事事实，**不得成为画像的第二真相源**

**原始音频**：不进学生模型。存储、保留期和删除由平台语音层统一管理。

**待定**：成就和 XP 是留在 echo 还是并入星图（疑点 12）。

---

## 4. 语音链放在哪一层（建议）

| 层 | 负责 |
|---|---|
| **平台层** | ASR、TTS、发音评测的供应商适配（PRD 13.2 的"评测适配层"）；按分钟计量和独立资源池（完整方案 5.8）；音频保留与删除；出境管控；延迟埋点，包括 p95 |
| **echo 适配器** | NPC 回合状态机、Prompt 组装、白名单超纲校验（从 Prompt 约束改成**输出后校验**）、完成条件验证器、Rubric A/B/C |

**选型前置**：确定境内 ASR、TTS、评测供应商，并做 PRD 13.3 的三家盲测。浏览器 Web Speech 不能作为生产方案：音频出境，国内网络也基本不可用。

---

## 5. 包装 vs 重写

**可以包装**（加校验、版本号、溯源后复用）：
- `shared/data/*` 内容（需要教研审核）
- 七段式 Prompt 结构（改动属于人设变更，[NEEDS-HUMAN]）
- `rubric.mjs`（补 uncertain 规则）
- `scriptedEngine.mjs`（作为降级引擎和评测 oracle）
- NpcTurn / RubricResult 作为内部结构，外面包统一返回
- `useSpeech.ts` 的看门狗和打字兜底，作为前端组件经验

**需要重写或丢弃**：
- localStorage 持久化 → 平台 API
- `GuardianGate` → 平台家庭账号
- `ParentReport` → 平台报告引擎
- `handlers.mjs`：补鉴权、限流、schema 校验、计量与幂等、超时和重试。**history 和 profile 要由服务端取，不能信任客户端传来的内容**
- `anthropic.mjs` → 平台模型路由
- 角色调整：Skye 改为点点；Examiner 改为雅典娜；NPC 的判定职能剥离出来
- `legacy/echo/app` 整体不迁（只参考它的四部分模考结构）；偶像风图片不迁

---

## 6. 接入前阻断项（建议）

1. 弄清 Gemini 链路的真实情况（疑点 1）。
2. 境内语音供应商选型与出境评估；在此之前不采集真实学生语音。
3. 开放代理问题：`/api/*` 鉴权与限流；关闭 history 和 profile 注入面。
4. 同意页文案与实际能力对齐（防沉迷、安全过滤）。
5. AI 身份披露：场景 NPC 的 "NOT an AI" 指令必须处理。
6. D 维没有引擎时，grade 不得输出确定的 D 维分数。

---

## 需要人确认的疑点（适配器设计相关）

1. 阶段 0 是否确认"实时对话留在原产品，适配器只接异步 grade、diagnose、report"？
2. NPC 判定：作为证据再由 grade 复核（单次调用），还是拆成两次调用？
3. 发音评测供应商没到位之前，grade 的 D 维怎么返回：null 加强制复核，还是这个维度不计分？
4. 英语错因三维映射到跨科大类，需要教研签字。
5. grade 金标准：由谁采集、标注真实学生话题简述录音？需要监护人同意和数据存储方案。
6. 成就和 XP 的归属。
