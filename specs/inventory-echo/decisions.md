# 开口成章（echo）盘点结论与决定记录

关联：本目录 inventory.md 文末"需要人确认的疑点"（编号与之一致）；adapter-design.md 的疑点记为 A1–A6
记录日期：2026-09-25
状态：负责人已于 2026-09-25 确认：第 1 项选 (b)，第 2 项确认，其余全部照起草建议执行。

## 【已关闭】

| # | 疑点 | 结论 |
|---|---|---|
| 1 | Gemini 链路在哪 | **没有 Gemini 仓库**。英语产品以 `legacy/echo` 为准：Anthropic + 浏览器 Web Speech，没有第三方评测。总纲、开发计划、角色规范和 ADR 08 中关于英语"Gemini 开发、Gemini 实时语音链、第三方音素评测"的描述，另起文档修订 PR 更正（不在本记录范围） |
| 2 | 主线认定 | **`Ai-english-claude/echo-quest` 为主线**。`app/`、Kimi zip、MVP 原型 html 只作参考，不迁移 |
| 3 | 部署与真实用户 | **没有真实用户，处于测试阶段**（四科同样如此）。模块打标维持"可演示"为最高；不做数据迁移，也不需要合规事件处置 |
| 8 / A2 | NPC 判定 | **单次调用，信号只作证据**：NPC 回合的 `task_complete/errors` 降级为 evidence_event，不作为判分；任务完成由 completionConditions 规则验证器判定；正式判分走异步 grade。不增加调用次数 |
| 11 | 偶像风图片 | `5J3YV.jpg`、`rth3m.jpg`、`app/public/characters/*.png` 标为**禁用资产，不迁移**；legacy 只读，不删除 |
| 12 / A6 | 成就和 XP 归属 | 成就、XP 作为游戏化资产留在 echo.*；掌握度和星图口径归平台，**XP 不得作为掌握度的依据** |
| 15 | `kimi-plugin-inspect-react` | 只在 `app/` 里，`app/` 不迁移，关闭 |
| A1 | 阶段 0 接入范围 | 实时对话留在原产品里；适配器只接**异步 grade、diagnose、report** |
| A3 | D 维（发音）在评测供应商到位前 | D 维返回 null，**不计入总分**；总分只按 A、B、C 计算，并标注"不含发音维"；置 `human_review_required = true` |

## 【上线前必须解决】

| # | 疑点 | 结论与要求 |
|---|---|---|
| 4 | `ANTHROPIC_BASE_URL` 与模型出境 | 并入 **ADR 09"数据驻留"**；没有真实用户，不追查历史 |
| 6 | 同意页宣称与实现不符 | 不构成已发生的误导，不需要法务处置。**同意页文案只能描述已实现的能力**；legacy 记为已知缺陷 ECHO-KD-05 |
| 7 | 语音链出境与选型 | 浏览器 Web Speech **不作为生产方案**。境内 ASR、TTS、发音评测供应商选型并做三家盲测，一并纳入 ADR 09。盲测用的学生语音必须在监护人同意流程上线后、经授权采集 |
| 9 | AI 身份披露 | **场景 NPC 不豁免**：删掉否认身份的指令；会话开始时由平台统一披露，NPC 被问及时必须承认是 AI 扮演，其余时间可以保持角色。人设变更，[NEEDS-HUMAN] |

## 【已采纳，待提供输入】

| # | 事项 | 已定部分 | 待提供 |
|---|---|---|---|
| 5 | `.env.example` | — | 人工确认只有占位符 |
| 10 | U1 外貌词完成条件 | 建议移除外貌词条件，改为兴趣、性格、爱好类描述 | 教研确认 |
| 13 | Rubric 权重 | 维持 A50/B20/C15/D15 初始值，结果标注"未校准" | 教研提供校准计划 |
| 14 | 教材一致性 | 内容一律按"未准入"处理 | 教研对照 2024 新版译林七上实物校核 |
| A4 | 错因映射 | — | 教研签字：英语错因映射到跨科大类（冲刺 0 验收项） |
| A5 | grade 金标准 | 阶段 0 用教研标注的**合成文字转写稿**作为 A、B、C 维金标准；真实录音等监护人同意流程上线后经授权采集 | 确定由谁标注 |

## 已知缺陷（legacy/echo，只读，不修）

E = `legacy/echo/Ai-english-claude/echo-quest`。

| 编号 | 缺陷 | 位置 | 平台侧对策 |
|---|---|---|---|
| ECHO-KD-01 | `/api/*` 没有鉴权、没有限流，等于用服务器 key 的开放代理 | E/server/handlers.mjs:108-122 | 所有 API 鉴权并计量 |
| ECHO-KD-02 | 客户端传来的 history 原样拼进 messages，可以伪造 assistant 轮次 | E/server/handlers.mjs:34 | 会话历史由服务端保存和读取 |
| ECHO-KD-03 | 客户端传来的 weakPoints/memory 直接注入 system prompt | E/server/promptAssembler.mjs:74-78 | 画像只从平台学习者模型读取 |
| ECHO-KD-04 | LLM 请求没有超时，没有重试 | E/server/anthropic.mjs | 平台模型路由统一处理 |
| ECHO-KD-05 | 同意页宣称的防沉迷、安全过滤没有实现 | E/src/components/GuardianGate.tsx:16,18 | 见 #6 |
| ECHO-KD-06 | 音频经 Web Speech 送到 Google，文本送到 Anthropic，都没有告知 | E/src/hooks/useSpeech.ts；E/server/anthropic.mjs | 见 #7 |
| ECHO-KD-07 | NPC Prompt 要求否认是 AI | E/server/promptAssembler.mjs:50-51 | 见 #9 |
| ECHO-KD-08 | NPC 在同一次调用里判定并纠错 | E/server/promptAssembler.mjs:21-36；scriptedEngine.mjs:94 | 见 #8 |
| ECHO-KD-09 | Rubric D 维写死为 72 或 55 | E/server/rubric.mjs:81 | 见 A3 |
| ECHO-KD-10 | `flag_review` 会产出但没有消费方 | E/src/lib/types.ts 等 | 安全审核接入平台复核队列 |
| ECHO-KD-11 | `weakPoints` 没有任何 action 更新，却被注入 Prompt | E/src/lib/store.ts:64,164 | 由平台 error_attribution 提供 |
| ECHO-KD-12 | MVP 原型 html 在浏览器里直连 Anthropic | `legacy/echo/开口成章-MVP交互原型-U1迎新报到日.html:280-283` | 禁止部署，不迁移 |

## 对其他文档的影响

- **adapter-design.md**：已于 2026-09-25 同步到 v0.2。
- **总纲、开发计划、角色规范、ADR 08**：英语技术栈描述需要更正（#1），另起文档修订 PR。
