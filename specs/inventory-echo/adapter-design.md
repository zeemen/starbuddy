# 开口成章（echo）适配器设计

状态：草稿 v0.2，已按 [decisions.md](./decisions.md)（2026-09-25）修订；标"待输入"的条目等教研或负责人的材料。**不开始实现。**
依据：本目录 inventory.md、decisions.md；完整方案 v1.0 第 5.4、5.5、5.8 节；开发计划精简方案（:146）与冲刺 3；考官型基线 V1.1 附录 C；点点 V1.2；七科角色规范 v1.1。
E = `legacy/echo/Ai-english-claude/echo-quest`

> 契约字段、`echo.*` schema、计量、雅典娜/点点/NPC 的人设 Prompt、新增 ASR/TTS/评测供应商与数据出境路径，都属于必停项，落地时 PR 首行必须写 [NEEDS-HUMAN]。

### v0.2 修订摘要（对应 decisions.md 编号）

| 决定 | 本文改动 |
|---|---|
| #1 没有 Gemini 仓库 | 第 0 节前置条件解除；以 echo-quest 为准 |
| #2 echo-quest 为主线 | 全文以 E 为准；`app/` 不迁移 |
| A1 阶段 0 只接异步 grade、diagnose、report | 第 0、1 节 |
| #8/A2 NPC 判定降级为证据 | 第 1 节 teach、diagnose；第 2 节 |
| A3 D 维返回 null，不计入总分 | 第 1 节 grade、第 2 节 |
| A5 合成转写稿作金标准 | 第 2 节 |
| #12/A6 成就和 XP 留 echo.* | 第 3 节 |
| #11 偶像风图片禁用 | 第 3.1 节 |
| #13、#14 未校准、未准入 | 第 2 节、第 3.1 节 |
| #7 Web Speech 不作生产方案 | 第 4 节 |
| #9 NPC 不豁免 AI 披露 | 第 5 节 |
| #4、#6、#7、#9 | 第 7 节阻断项 |
| #3 无真实用户 | 第 3 节不做数据迁移 |

---

## 0. 阶段 0 接入范围（已定，A1）

- **实时对话留在原产品里运行，适配器不接实时链。**
- 适配器接三项：
  - 异步 **grade**：转写稿（或录音 → ASR 转写）→ Rubric A/B/C；D 维见第 2 节
  - **diagnose**：只读取证据
  - **report**：作为字段提供者
- 前提已经解除（#1）：英语没有 Gemini 仓库，以 echo-quest 为准。总纲等文档对英语技术栈的描述另行修订。

---

## 1. 六方法映射

所有方法都要返回 `result / evidence_ids / confidence / source_version / model_version / human_review_required`。

| 插件方法 | 阶段 | 复用的现有代码 | 做法 | 主要差距 |
|---|---|---|---|---|
| **grade**（冲刺 3 首接） | 0 | `server/rubric.mjs`（buildRubricPrompt、scriptedRate）、`handlers.mjs` 里的 `handleRate` | 包装成异步 grade：A/B/C 由 LLM Rubric 判，`scriptedRate` 作为低置信兜底和对照 oracle。**D 维返回 null，不计入总分**；总分只按 A、B、C 计算，标注"不含发音维"，置 `human_review_required = true`。权重维持 V1.0 初始值，结果标注"未校准" | LLM prompt 缺 uncertain 规则；没有 ASR 时间戳所以 C 维不完整；缺溯源字段、双评、复核队列。雅典娜只负责宣判模板，不参与判分 |
| **diagnose** | 0 | 每轮的 `student_turn_analysis`（types.ts:150-161）、RubricResult、completionConditions 命中情况 | 这些只作为 **evidence_event** 输入，由平台掌握估计聚合。NPC 回合的判定字段只是证据，不是判分（#8） | 没有入口筛查（F02）；weakPoints 是写死的；五维 skills 是启发式增量，**不迁移**，由平台重算 |
| **report** | 0 | `ParentReport.tsx`、`CompletionOverlay` 的字段 | 字段提供者，由平台报告引擎合成双语报告 | 现在是纯前端读本地 store，而且家长报告放在学生设备上 |
| **teach** | 1 | `handleNpc`、`promptAssembler.mjs`（七段）、`scriptedEngine.mjs`、`SkyeHint` | 阶段 0 不接入平台。阶段 1 包装时：**保持单次调用**，NPC 只负责叙事对话，`task_complete/errors` 降级为 evidence_event；任务完成由 completionConditions **规则验证器**判定；正式判分走异步 grade | 鉴权、计量、流式都缺；Skye 改为点点；删掉否认 AI 身份的指令（#9） |
| **plan** | 1 | `SCENE_ORDER` 线性解锁（scenes.js:338、store.ts:171-183） | 由平台编排；echo 提供五步单元闭环模板和前置关系 | 不自适应，没有档位推荐，没有支线推送 |
| **generate_variation** | 1 | `units.js` 的 `patterns.family`；场景重玩 | 对齐其他三科：契约 v1 **只从已审题库选题**；阶段 1 新建题库（F17） | 基本没有 |

---

## 2. 判分溯源字段差距

| 契约字段 | NpcTurn | RubricResult | 适配器做法 |
|---|---|---|---|
| result | — | 各维档位与总分 | A/B/C 维档位与总分；**D 维为 null**，并附"不含发音维"标记 |
| evidence_ids | 无 | 无（只有 `A.evidence_zh` 文本和 `valid_sentences`） | transcript 句子、ASR 片段、NPC 回合信号都落为 evidence_event |
| confidence | 无 | 无 | 按维度给出；LLM 判分为中低置信；`scriptedRate` 兜底时为低置信；ASR 质量信号（如果有）要参与 |
| source_version | 无（Prompt、数据都没有版本号） | 无 | Rubric、prompt、units、topics 加版本号和六段标识；权重版本标"V1.0-未校准" |
| model_version | 只有 `source: llm \| scripted` | 同左 | 由平台模型路由回传；脚本判分记算法版本 |
| human_review_required | 只有 `flag_review`（语义是安全审核） | 无 | 以下任一情况置真：D 维缺失（阶段 0 必然成立）、低置信、双评分歧、内容未准入。**安全审核另开一个 flag，两者语义分开** |

**金标准（A5）**：阶段 0 用教研标注的**合成文字转写稿**，作为 A、B、C 维金标准（标注人待定）。真实学生录音要等监护人同意流程上线后、经授权采集。

---

## 3. 数据归属

**迁到 platform.***：

| legacy 字段 | 平台对象 |
|---|---|
| studentName、englishName | 学生档案（英文名可以在 echo 里冗余一份，用于 NPC 称呼） |
| guardianConsent | `guardian_student_link` + `consent_record`（现在的自声明勾选作废） |
| skills 五维 | `skill_node(subject=echo)` + `mastery_estimate`（平台重算，legacy 数值不迁移） |
| vocab | `skill_node(echo, local_id=word)` + evidence_event |
| completedNodes、stats、listenStreak、selfCorrections | attempt、evidence_event（自我修正单独作为高价值信号） |
| towerBest、RubricResult | attempt + grade 结果（带溯源） |
| weakPoints、每轮 errors | `error_attribution`（英语错因映射到跨科大类，待教研签字） |

**留在 echo.***：
- 内容表：场景、节点、完成条件、NPC 卡、词表与阈值、话题，补六段标识和准入状态（见 3.1）
- 对话会话、回合原文、NPC 叙事记忆、好感度。NPC 记忆只存叙事事实，**不得成为画像的第二真相源**
- **成就和 XP**：作为游戏化资产留在 echo.*，**不得作为掌握度的依据**（#12）

**原始音频**：不进学生模型；存储、保留期和删除由平台语音层统一管理。

**数据迁移**：不做（#3：没有真实用户）。

### 3.1 内容与资产准入

| 内容 | 状态 | 能否入平台 |
|---|---|---|
| U1–U3 词表、句型、话题、场景 | **未准入**（没有对照 2024 新版译林七上实物校核，#14） | 教研校核后准入；在此之前只能用于练习与演示 |
| U1 外貌词完成条件（slim、pretty） | 待删改（#10） | 建议改为兴趣、性格、爱好类描述，教研确认后生效 |
| Rubric 权重 A50/B20/C15/D15 | **未校准**（#13） | 可以使用，结果标注"未校准" |
| NPC 人设卡 8 张 | 需要按 #9 和角色规范改写 | 改写并跑人设回归后准入（[NEEDS-HUMAN]） |
| `5J3YV.jpg`、`rth3m.jpg`、`app/public/characters/*.png` | **禁用资产**（#11） | 永不迁移 |
| `app/`、Kimi zip、MVP 原型 html | 参考（#2） | 不迁移 |

---

## 4. 语音链放在哪一层

| 层 | 负责 |
|---|---|
| **平台层** | ASR、TTS、发音评测的供应商适配（PRD 13.2 的"评测适配层"）；按分钟计量和独立资源池（完整方案 5.8）；音频保留与删除；出境管控；延迟埋点，包括 p95 |
| **echo 适配器** | NPC 回合状态机、Prompt 组装、白名单超纲校验（从 Prompt 约束改为**输出后校验**）、completionConditions 规则验证器、Rubric A/B/C |

**选型（#7，上线前必须解决）**：
- 浏览器 Web Speech **不作为生产方案**：音频出境，国内网络也基本不可用。
- 境内 ASR、TTS、发音评测供应商选型，并做三家盲测，纳入 ADR 09"数据驻留"。
- 盲测用的学生语音必须在监护人同意流程上线后、经授权采集。
- 评测供应商到位后，D 维从 null 改为引擎输出，同时撤销"不含发音维"的标注。

---

## 5. 包装 vs 重写与角色边界

**可以包装**（加校验、版本号、溯源后复用）：
- `shared/data/*` 内容（按 3.1 准入）
- 七段式 Prompt 结构（改动属于人设变更，[NEEDS-HUMAN]）
- `rubric.mjs`（补 uncertain 规则；D 维改为返回 null）
- `scriptedEngine.mjs`（作为降级引擎和评测 oracle）
- completionConditions（23 条，作为任务完成的规则验证器）
- NpcTurn / RubricResult 作为内部结构，外面包统一返回
- `useSpeech.ts` 的看门狗和打字兜底，作为前端组件经验

**需要重写或丢弃**：
- localStorage 持久化 → 平台 API
- `GuardianGate` → 平台家庭账号和 consent_record；同意页文案只能描述已实现的能力（#6）
- `ParentReport` → 平台报告引擎
- `handlers.mjs`：补鉴权、限流、schema 校验、计量与幂等、超时和重试；**history 和 profile 由服务端读取**，不信任客户端（ECHO-KD-02、03）
- `anthropic.mjs` → 平台模型路由

**角色边界**：

| legacy | 去向 |
|---|---|
| Skye（学姐学伴） | 并入点点 |
| Examiner（未使用） | 改为雅典娜：首次披露、固定句式、逐项"达标/部分达标/未达标。依据：…" |
| 塔内结果页的 strengths、improvement | 塔内只报评分项、依据、合计；暖反馈归点点在塔外说 |
| 场景 NPC | 只负责叙事对话，不判分、不纠错（#8）；**不豁免 AI 身份披露**：会话开始由平台统一披露，被问及时必须承认是 AI 扮演（#9） |
| 转介 | 由点点执行平台确定性转介；NPC 不承接 |

---

## 6. 与其他文档的关系

英语没有 Gemini 链路（#1）。总纲（:199,239,308）、开发计划（:266）、角色规范（:452）以及 ADR 08"英语保留 Gemini 链路"的描述需要更正，另起文档修订 PR。点点跨模型一致性验证的前提也要随之重新评估。

---

## 7. 上线前阻断项

与 decisions.md 的"上线前必须解决"对齐。这些是上线门禁，不是阶段 0 测试环境接入的门禁。

1. **数据驻留**（#4、#7）：模型路由、ASR/TTS/评测供应商、音频与文本的存储和传输区域按 ADR 09 执行；Web Speech 不进生产。
2. **同意页如实描述**（#6）：文案只能描述已实现的能力；配套 consent_record 和监护人核验。
3. **AI 身份披露**（#9）：删掉 NPC 否认身份的指令；平台统一披露（[NEEDS-HUMAN]）。
4. **开放代理**（ECHO-KD-01~03）：所有 API 鉴权并计量；history 和 profile 由服务端读取。
5. **防沉迷与内容安全**（F14、F15）：由平台层提供，同意页上线前必须已经实现。
6. **学生侧危机转介**：平台层确定性转介，由点点执行（对齐其他三科）。

legacy 里的对应问题登记为已知缺陷 ECHO-KD-01~12（见 decisions.md），不在 legacy 里修。

---

## 待输入

| 编号 | 事项 | 影响 |
|---|---|---|
| #5 | `.env.example` 核查 | 无（只作记录） |
| #10 | U1 外貌词条件删改（教研确认） | U1 内容准入 |
| #13 | Rubric 权重校准计划 | 能否撤销"未校准"标注 |
| #14 | 教材实物校核 | U1–U3 内容准入 |
| A4 | 错因映射到跨科大类（教研签字） | error_attribution 的写入 |
| A5 | 合成转写稿金标准由谁标注 | grade 能否从全量复核降为抽样复核 |
