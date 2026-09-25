# 开口成章（英语听说 · echo / EchoQuest）阶段 0 盘点

状态：草稿，等人确认
关联：完整方案 v1.0 第 5.4、5.5、5.8、5.9 节 · 开发计划阶段 0 冲刺 0、冲刺 3 · 模块清单依据 docs/subjects/echo-开口成章PRD-V1.2.md（F01–F24）
盘点日期：2026-09-24 · 方式：inventory-auditor 子代理只读盘点（没有启动工程、没有做延迟实测。只做了一次纯本地 node import，用来统计 Prompt 字符数）

**路径缩写**：E = `legacy/echo/Ai-english-claude/echo-quest`

`.env*` 的读取被 `.claude/hooks/guard.py` 拦截，`.env.example` 的内容没有核对。

---

## 0. 主线认定与重大出入

| 对象 | 性质 | 结论 |
|---|---|---|
| `Ai-english-claude/echo-quest/` | 按 PRD V1.2 实现的可运行 MVP，有前端和 Node 代理层（README:5） | **主线** |
| `legacy/echo/app/` | Kimi Agent 生成的纯前端原型，页面标题"偶境Quest IdolRealm Quest"（`app/index.html:6`）。没有网络调用、没有 LLM，状态只在内存里 | 参考 / 废弃 |
| `Kimi_Agent_游戏化英语学习.zip` | app 的早期快照，90 个文件，约 11 MB | 只作参考 |
| `偶境Quest（grok）.docx` | Grok 写的方案文档。内嵌的两张图与根目录的 `rth3m.jpg`、`5J3YV.jpg` 字节数完全一致 | 两张 jpg 就是从这份 docx 里取出来的 |
| 五份 `AI英语沉浸式学习APP-*.md`、PRD V1.0/V1.1 | 前期方案 | 只作参考 |
| 场景映射表、NPC 人设卡模板、话题简述 Rubric 细则（V1.0） | 主线的设计输入 | 主线代码按这三份实现 |
| `开口成章-MVP交互原型-U1迎新报到日.html` | 在浏览器里直连 Anthropic（:280-283），请求不带 key | 参考；**应标为禁止部署的原型** |
| `Ai-english-claude/.playwright-mcp/` | 2026-07-11 的快照和 console 日志 | echo-quest 以 Demo 模式实际跑过的**唯一证据** |

**重大出入**：完整方案（:199,239,308）、开发计划（:266）和角色规范（:452）都说英语是"Gemini 开发、Gemini 实时语音链（流式 ASR→LLM→流式 TTS）、第三方音素级评测"。但 legacy/echo 里**没有任何 Gemini、流式 ASR、第三方 TTS 或发音评测的代码**，grep 零命中。主线实际走的是 **Anthropic 加浏览器 Web Speech**。

---

## 1. 代码与部署

### 1.1 echo-quest（主线）
- **技术栈**：React 18.3、Vite 5.4、TypeScript strict、Tailwind 3.4、zustand 5（persist）、framer-motion、react-router 6。后端是零依赖的 Node `http` 服务（`server/index.mjs`）。
- **目录**：
  - `shared/data/`：units、scenes、npcs、topics、progression
  - `server/`：anthropic、handlers、promptAssembler、rubric、scriptedEngine、index
  - `src/`：5 个页面、20 个组件、2 个 hook、3 个 lib
- **规模**：自有代码约 4085 行。
- **API**：`POST /api/npc`、`POST /api/rate`、`GET /api/health`（`server/handlers.mjs:108-122`）。
- **脚本**：dev、build、preview、serve、typecheck。**没有 test 和 lint**。
- **部署配置**：**没有**（没有 vercel、railway、Docker 或 CI）。
- **构建产物**：dist 存在（372 KB），bundle 里**没有密钥**。
- **外部依赖**：从 Google Fonts 加载字体，在国内可能失败。
- **许可证**：直接依赖都是常见 MIT 包。**没有逐包核对**，建议后续用 license-checker 确认。

### 1.2 app（Kimi 原型）
- React 19、Vite 7、shadcn/ui、Radix、recharts。
- lockfile 许可证：MIT 475、ISC 29、Apache-2.0 16，其余是 BSD/0BSD/CC-BY/Python-2.0。**没有 GPL/LGPL**。
- **供应链风险**：开发依赖 `kimi-plugin-inspect-react` 来自私有镜像 `npm.mirrors.msh.team`（`app/package-lock.json:6594-6596`），在 `vite.config.ts:4,9` 注入。

### 1.3 git 与测试
- `legacy/` 被 `.gitignore` 忽略，echo 里也没有 `.git`，**没有任何 git 历史**。zip 内时间戳是 2026-07-10。
- 单测和 e2e：**0**。
- 已知运行期缺陷：Playwright 日志里有 React Hooks 顺序错误（DialogueRunner，`console-2026-07-11T03-38-41-866Z.log:5`）。

---

## 2. 真实可用状态（F01–F24）

没有部署证据，所以没有"已上线"项。"可演示"是指本地 `pnpm dev` 无密钥时，脚本引擎能跑通这条学生路径（Playwright 快照显示 U1 三连问 3/3，界面提示"Demo 未配置密钥"）。LLM 路径没有运行证据，按半成品计。

| # | 模块 | 状态 | 证据与差距 |
|---|---|---|---|
| F01 | 账号与监护人同意 | 半成品 | 只有一个自声明勾选框，存到 localStorage（`GuardianGate.tsx:26-31`、`store.ts:69`）。没有账号，没有核验 |
| F02 | 冷启动分班测试 | 仅方案 | — |
| F03 | 主线场景×3 | **可演示** | 3 场景 × 5 节 = 15 节，线性解锁（`scenes.js:338`）。五步闭环缺第 ④ 步"变式练习" |
| F04 | 实时语音对话链路 | 半成品 | Web Speech 非流式 ASR（`useSpeech.ts:94-122`）；HTTP 整包返回的 LLM（`anthropic.mjs:8-22`）；浏览器 TTS。没有 WebSocket、按住说话、延迟埋点、异步降级 |
| F05 | NPC 对话引擎 | 半成品（脚本部分可演示） | 七段式 Prompt（`promptAssembler.mjs:42-83`），白名单只注入在 Prompt 里，会话记忆 8 条。没有图谱 RAG，没有输出后超纲校验 |
| F06 | 虚拟学伴 | 半成品 | Skye 只有静态三级提示（`SkyeHint.tsx:15-19`），纠错和复盘没有。按规范要并入点点 |
| F07 | 口语三档脚手架 | **可演示** | 跟读、半开放、自由三档，手动切（`ScaffoldDock.tsx:6-10`）。没有动态推荐，跟读没有音素对比 |
| F08 | 听力双模式 | 半成品 | 统一做成三选一，可重听 2 次，语速固定 0.84。任务听力和地图选路没有 |
| F09 | 发音评测集成 | **仅方案** | 没有引擎。D 维写死 72 或 55（`rubric.mjs:81`）；朗读用 ASR 词覆盖率近似（`ReadAloud.tsx:15-22`） |
| F10 | 词汇背包 | 半成品 | 用到的词会点亮，共 36 词。遗忘曲线复现没有 |
| F11 | 单元 Boss 关×3 | **可演示** | U1、U2 是话题简述，U3 是听力 Boss（`scenes.js:98-99,210-211,313-317`） |
| F12 | 学生端任务报告 | **可演示** | `CompletionOverlay.tsx`，模板拼接 |
| F13 | 家长端周报 | 半成品 | 是学生端的一个 Tab，同一台设备，没有监护人身份；"本周"其实是累计值；建议文案写死；缺进度同步度 |
| F14 | 防沉迷基线 | **仅方案** | 只有同意页上的一句宣称（`GuardianGate.tsx:16`），**宣称与实现不符** |
| F15 | 内容安全过滤 | **仅方案** | 只有 Prompt 约束；`flag_review` 会产出，但没有消费方；同意页"全链路安全过滤"的宣称（`GuardianGate.tsx:18`）**与实现不符** |
| F16 | 试炼之塔模考 | 半成品 | 只有话题简述一个题型，5 道题。试音、指令、四题型都没有；考官卡定义了但没用上（`npcs.js:128-143`） |
| F17 | 错题本 + LLM 变形 | 仅方案 | — |
| F18 | 好感度与羁绊 | 仅方案（主线） | 只有 app 原型里的静态数值 |
| F19 | 费曼教学伴 | 仅方案 | — |
| F20 | 支线补弱推送 | 仅方案 | `weakPoints` **没有任何 action 更新它**（`store.ts:64,164`），却被注入 Prompt |
| F21 | 技能树可视化 | 半成品 | 五维雷达，数值是固定增量（`useNpcDialogue.ts:67-73`），不是掌握度估计 |
| F22 | 场景扩至全学期 | 仅方案 | U4–U8 只有文档 |
| F23 | 专项玩法 | 仅方案 | — |
| F24 | NPC 长期记忆 | 仅方案 | 只把英文名拼成一句 memory |

**统计**：已上线 0／可演示 4（F03、F07、F11、F12；F05 的脚本部分也可演示，但按半成品计入）／半成品 10／仅方案 10。

---

## 3. 数据库

- **没有数据库**。两套工程都没有 schema、迁移、RLS 或种子数据。
- echo-quest 的全部学生状态存在浏览器 localStorage，key 是 `echo-quest`（`src/lib/store.ts:52-168`）。字段有：profile（onboarded、guardianConsent、名字、英文名、xp）、completedNodes、vocab、skills、achievements、selfCorrections、listenStreak、towerBest、stats、weakPoints。
- app 原型只在内存里；示例名"李华"是考试常用的虚构人名。
- **学生与家长关系**：没有建模，"家长"就是同一设备上的一次勾选。不存在按手机号关联的问题，也没有 `guardian_student_link` 的对应物。
- **真实学生数据**：没发现（Playwright 快照里的是合成句）。
- 内容全部是静态 ESM 常量，没有审核字段。

---

## 4. 模型调用

| 项 | echo-quest |
|---|---|
| 供应商与端点 | Anthropic Messages API，`${ANTHROPIC_BASE_URL \|\| https://api.anthropic.com}/v1/messages`（`server/anthropic.mjs:7`）；BaseURL 可以指向中转（需要确认） |
| 模型 | `ECHO_MODEL`，缺省 `claude-sonnet-5`（`anthropic.mjs:4`） |
| 调用点 | `handleNpc`（max_tokens 700，temperature 0.6）；`handleRate`（max_tokens 700，temperature 0） |
| Prompt | `server/promptAssembler.mjs:4-91`（七段，[3][5][7] 锁定）；`server/rubric.mjs:115-137`；`handlers.mjs:68` |
| 流式 | **否**，整包返回后 `parseJsonLoose` |
| token 粗估（推断） | NPC：system 约 3.8–4.1k 字符（约 1.0–1.3k token），输入约 1.3–2k，输出约 150–350。评分：输入约 0.5–0.8k，输出约 300–500 |
| 计量、幂等 | **没有** |
| 重试与超时 | **没有重试**（模板文档要求失败重试一次，代码直接降级）；fetch **没有超时** |
| 降级 | 没配 key、解析失败或异常时，走确定性脚本引擎，返回同一套 JSON 契约（`scriptedEngine.mjs`） |
| 密钥 | 只在服务端，dist 里没有；仓库里没有 `.env` 文件 |
| 前端直连 LLM | 主线没有；**原型 html 有**（:280-283，没带 key） |

**服务端安全缺口**：
1. `/api/*` 没有鉴权、没有限流，等于一个用服务器 key 的**开放代理**。
2. 客户端传来的 `history` 原样拼进 messages，可以伪造 assistant 轮次（`handlers.mjs:34`）。
3. 客户端传来的 `weakPoints/memory` 直接注入 system 的 seg6，这是一个 **Prompt 注入面**（`promptAssembler.mjs:74-78`）。
4. 请求体没有 schema 校验。
5. 错误日志会带出上游响应片段。

---

## 5. 英语专项：实时语音链

| 环节 | 实现 | 证据 |
|---|---|---|
| ASR | 浏览器 Web Speech API，`en-US`，`continuous=false`，interim 开启；启动看门狗 2.5 s，整体看门狗 12 s；失败就引导打字 | `useSpeech.ts:15-17,84-160`；`ScaffoldDock.tsx:13-32` |
| TTS | 浏览器 `speechSynthesis`，按 NPC 卡的 voiceProfile 调 rate 和 pitch | `useSpeech.ts:34-49` |
| 发音评测 | **没有** | — |
| 第三方 SDK | **没有** | — |

**数据流向**：
- App **不采集、不上传、不存储原始音频**（没有 MediaRecorder 或 getUserMedia）。
- Chrome 的 Web Speech 会把麦克风音频送到 **Google 云端**，**数据出境**，保留期不受 App 控制。国内网络下大概率报 network 错误。Edge、Safari 走各自厂商的服务。
- 识别出的文本、英文名、weakPoints 经 `/api/npc` 转发给 **Anthropic（美国）**，**文本出境**。服务端不落盘。
- 同意页只写了"录音仅用于当次反馈"（`GuardianGate.tsx:15`），**没有告知这两次出境**。

**延迟（未实测，全部是推断值）**：代码里没有任何延迟埋点，文档里也没有测量记录。

| 段 | 推断 |
|---|---|
| 说完 → ASR final | 0.5–1.5 s |
| /api/npc → Anthropic（非流式） | 2–6 s |
| 解析 → TTS 出声 | 0.1–0.3 s |
| **端到端（LLM 模式）** | **约 3–8 s**，超过 PRD 13.3 的目标 ≤2.0 s 和上限 3.0 s |
| 端到端（脚本模式） | 约 0.6–2 s |

"语音延迟 p95 达标"这条验收（完整方案 5.9）目前**无法测量**。

---

## 6. 评测资产

- 判分金标准、人设回归、单测、e2e：**都没有**。
- 只有 `.playwright-mcp/` 下 14 份快照和 3 份日志，是一次人工探索，不是回归用例。
- 可以转成评测种子的材料：
  - `scenes.js` 里 23 条 completionConditions（带正则或关键词），可以当 grade 的规则基线
  - `scriptedEngine.mjs:30-49` 里 4 类语法错探测
  - 5 道话题的 wordPack 和提纲
  - Rubric 细则的档位描述

---

## 7. 内容资产

| 项 | 数量与状态 |
|---|---|
| 单元 | 映射表覆盖七上 8 单元，代码实现 U1–U3（37.5%） |
| 场景与节 | 3 场景、15 节、8 条开场白、23 条完成条件 |
| 对话脚本 | 没有写好的多轮脚本；靠 LLM 生成或脚本模板（6 个 NPC 的起句和成功句） |
| 听力 | 4 个听力节，约 6 段以上 clip |
| 朗读短文 | 1 篇 |
| 词汇 | 36 词（IPA、搭配、句型、易混词） |
| 语法点、句型族 | 7 个、9 个 |
| NPC 人设卡 | 8 张（Skye、6 个场景 NPC、Examiner） |
| 话题简述题 | 5 道 |
| 审核状态 | **全部未审核**；映射表自己声明"须逐单元对照教材实物校核"（:9） |

**Rubric 细则 V1.0 与代码对照**：

| 细则 | 代码 | 差距 |
|---|---|---|
| 权重 A50、B20、C15、D15，5 分制 | 一致（`rubric.mjs:32-40,82`） | — |
| A：有效句 = 主谓结构 + 新信息，逐字重复不算，检查总起、展开、收尾 | 脚本版：≥3 词算有效句，加 wordPack 覆盖率 | 没有主谓判定，没有去重，没有结构检查 |
| B：错误计数；ASR 疑似噪声标 uncertain 并排除 | 脚本版 4 条正则；**LLM prompt 漏掉了 uncertain 规则** | 没有 B1 档 |
| C：用时间戳算停顿，加连接词 | 只看连接词 | Web Speech 不给时间戳 |
| D：只能来自发音引擎 | 写死 72 或 55 | **没有引擎** |
| LLM 双评加人工抽样校准 | 单评，temperature 0 | 没有 |

**版权与形象风险**：
- 没发现整段照搬教材的情况，但也**没有和教材实物比对**。
- `5J3YV.jpg`、`rth3m.jpg`：AI 生成的日系偶像风概念图（"Luna Starlight"，带好感度 UI），著作权归属不明。与 PRD 3.4 情感边界、3.6"偶像全渠道禁用"冲突。
- `app/public/characters/*.png`：带"AI生成"水印的偶像风角色，被 app 原型引用。
- 主线 echo-quest 用 emoji 做头像，**没有用上面这些图**。

---

## 8. 人设实现与规格差距

**规格**：
- 英语老师是**雅典娜（考官型）**（考官型基线 V1.1 附录 C，:268-286）。
- 学伴职能并入**点点**（点点 V1.2:142）。
- 剧情 NPC 保留为"场景角色"，**不讲解、不判分、不引用引擎**（七科角色规范 :348,430）。

| legacy | 与规格的冲突 |
|---|---|
| Skye（苏晴学姐） | 规范要求删除学长/学姐角色，职能并入点点 |
| Examiner（无名，没用上） | 应改为雅典娜：首次披露、固定句式、逐项"达标/部分达标/未达标。依据：…"。**全部缺失** |
| 塔内结果页展示 strengths 和 improvement（`TopicTalk.tsx:190-256`） | 塔内只报评分项、依据、合计；暖反馈归点点在塔外说 |
| 场景 NPC 在同一次调用里输出 `student_turn_analysis` 并纠错（`promptAssembler.mjs:21-36`；`scriptedEngine.mjs:94`） | 违反"场景角色不判分"和 CLAUDE.md 第 3 条 |
| Prompt 要求"you are NOT an AI assistant"（`promptAssembler.mjs:50-51`） | 与 AI 身份披露合同（角色规范 :175）**方向相反** |
| 禁语 | 只覆盖 D、C 的一部分、外貌、"wrong"；A、B、E、G 类没有；没有分类器和回归 |
| 转介 | NPC 用一句话引导"talk to a parent or teacher"，并置 `flag_review`，但**没有消费方**；平台规定由点点执行转介 |
| U1 完成条件要求用外貌词描述自己（slim、pretty，`scenes.js:66`） | 与外貌评价红线存在张力 |
| app 标题"偶境Quest IdolRealm" | 违反"偶像全渠道禁用"（只在非主线） |

---

## 9. 六方法对应

插件 API 映射和差距见 [adapter-design.md](./adapter-design.md) 第 1 节。

结论：
- **grade** 是最适合先包装成异步版本的方法（冲刺 3 目标），但缺 D 维引擎。
- **teach**（NPC）必须把判定职能剥离出来。
- **diagnose** 和 **generate_variation** 基本没有。
- **report** 要改成字段提供者。

---

## 需要人确认的疑点

1. **Gemini 链路在哪？** 平台文档多处说英语"由 Gemini 开发、有 Gemini 实时语音链和第三方音素评测"，但 legacy 里只有 Anthropic 加 Web Speech。是另有仓库没放进来，还是文档描述不准？这影响 ADR 08 和点点跨模型一致性验证的前提。
2. **主线认定**：确认 echo-quest 为主线、`app/` 和 zip 为废弃参考？
3. **部署**：有没有部署过？有没有真实学生用过？
4. **`ANTHROPIC_BASE_URL`**：是否指向过第三方中转？
5. **`.env.example`** 需要人工确认没有真实 key；`.env` 是否曾经随包分发过？
6. **同意页宣称**了防沉迷和"全链路安全过滤"，但代码没有实现。如果有过真实用户，是否构成误导性告知？需要法务评估。
7. **Web Speech 出境**（Google 等浏览器厂商），以及 PRD 13.3 的"3 家 ASR 引擎真实学生语音盲测"是否做过？有没有记录？
8. **NPC 判定**：保留为"证据"再由 grade 复核，还是必须拆成两次调用？后者会进一步拉高延迟（推断已经 3–8 s）。
9. **场景 NPC 能否豁免 AI 身份披露？**
10. **U1 外貌词完成条件**是否符合红线？需要教研确认。
11. **偶像风图片**（`5J3YV.jpg`、`rth3m.jpg`、`app/public/characters/*.png`）是否隔离，并标为禁用资产？
12. **成就和 XP** 留 echo.*，还是并入平台星图？
13. **Rubric 权重**只是 V1.0 初始值，试点前是否有"产品分对真实分"的校准计划？
14. **教材一致性**：是否和 2024 新版译林七上实物一致？
15. **`kimi-plugin-inspect-react`**（私有镜像）：如果将来复用 app 的代码，需要先做供应链审查。
