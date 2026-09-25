# 阿鸦（数学 · aya）阶段 0 盘点

状态：草稿，等人确认
关联：完整方案 v1.0 第 5.4、5.5 节 · 开发计划阶段 0 冲刺 0、冲刺 1-D · 模块清单依据 docs/subjects/aya-数学产品设计整体方案.pdf 第 30 页 11.1 表（12 Agent）
盘点日期：2026-09-24 · 方式：inventory-auditor 子代理只读盘点（没有执行脚本、没有装依赖、没有连库、没有访问线上域名）

路径默认相对 `legacy/aya/`。`.env.example` 存在（1684 字节），但被守卫拦截，没有读。

**一句话结论**：这是一个单人 6 天写出来的 Next.js 单体前端。学习状态以浏览器 localStorage 为准，登录后把整份 JSON 同步到 Supabase 的一张表。真正调用模型的只有两个 API：AI 教练对话、拍照过程判分。"12 Agent"是一张写死的登记表，加一个管理员才能看到的只读文字流水，**没有编排**。内容量大（32 章、939 个节点、721 道题），但全部是 Q1 待审。判分返回没有溯源字段，没有计量、家长授权关系和 AI 身份披露。

---

## 1. 代码与部署

### 1.1 技术栈与目录

| 项 | 事实 | 证据 |
|---|---|---|
| 包名 | `jiuzhang`（九章 · AI数学教练）；IP 名后来改成阿鸦，工程内核没改名 | package.json:2,5；README.md:6 |
| 框架 | Next.js 16.2.10 + React 18.3.1 + Tailwind 3 + TS 5，App Router，强制 `--webpack` | package.json:7-8,19-20 |
| 数据 | @supabase/ssr、@supabase/supabase-js | package.json:16-17 |
| 公式渲染 | **KaTeX（MIT）**；没有 MathJax，没有 mathml2omml | package.json:18；components/MathText.tsx |
| 规模 | 235 个受 git 管理的文件；24 个页面、5 个 API 路由、lib 39 个模块、components 17 个 | — |
| 目录 | app/、components/、content/（TS 静态数据）、lib/、db/（2 个 SQL）、scripts/（2 个自测脚本）、docs/superpowers/（账号和设置相关的 spec 与 plan） | — |

### 1.2 依赖与许可证（package-lock，共 153 个包）
- MIT 111、Apache-2.0 17、ISC 8、BSD-3 1、0BSD 1、CC-BY-4.0 1。
- **LGPL-3.0-or-later 14 个**，全部是 sharp/libvips 的平台二进制（Next 的可选依赖，动态链接）。按 CLAUDE.md 第 10 条需要登记和确认。
- 数学公式相关的依赖**没有 LGPL**。
- `verify:*` 脚本用 `npx tsx` 执行，但 tsx 不在 devDependencies 里，运行时会临时从 npm 拉取，是个轻度供应链问题。

### 1.3 构建与部署
- 脚本：dev、build、start、typecheck、verify:symbolic、verify:figures。**没有 lint 和 test**。
- 没有 vercel.json、CI 或 Dockerfile。
- 自述已部署到 `https://ayaheshui.vercel.app`（progress.md:58,176），**无法验证**。
- 环境变量键名（从代码反推）：DEEPSEEK_API_KEY、DASHSCOPE_API_KEY、COACH_PROVIDER、COACH_MODEL、OPENAI_BASE_URL、VISION_*、NEXT_PUBLIC_SUPABASE_URL/ANON_KEY、SUPABASE_SERVICE_ROLE_KEY、NEXT_PUBLIC_ADMIN_EMAILS。
- git 历史里只提交过 `.env.example`。

### 1.4 git 历史

| 项 | 值 |
|---|---|
| 提交数 | 109 |
| 时间跨度 | 2026-07-15 至 07-20，共 6 天 |
| 作者 | 1 人（2 个邮箱身份） |
| 分支 | 只有 master |
| 最近提交 | 2026-07-20 "docs: 更新 progress.md 记录 V3.22" |
| 工作区 | 干净 |

### 1.5 测试
- **单测 0、e2e 0**。
- 2 个自测脚本，没有执行：
  - `scripts/verify-symbolic.mjs`：36 个断言
  - `scripts/verify-figures.mjs`：全量图形核验 + 2 个负向用例

---

## 2. 真实可用状态

### 2.1 "多 Agent"是装饰，不是编排
- `lib/agents.ts:30-127` 的 `AGENT_ROLES` 是写死的数组，12 个角色的 status 全部是 `"active"`。
- `runLearningCycle`（:143-271）是一个同步纯函数，依次调用已有的规则函数，拼出 10 段中文文字，注释写明"只读"。
- 全仓库只有管理员可见的 `app/agents/page.tsx` 调用它。
- 真正的 LLM 调用只有 `lib/llm.ts`（教练）和 `lib/vision.ts`（OCR）。
- 第 ⑧ 步"验证 Agent"输出的是一句写死的宣传文案（:244）。

### 2.2 12 Agent 逐项打标
仓库里没有任何能证明线上可用的证据，所以**没有模块标"已上线"**。

| # | Agent | 状态 | 依据 | 差距 |
|---|---|---|---|---|
| 1 | 总控 | **半成品**（接近仅方案） | 只读文字流水，管理员页可见 | 没有调度；学生端没有入口 |
| 2 | 学生模型 | **可演示** | lib/scoring.ts（简化 BKT，全量重放）、lib/evaluation.ts:176（六维）；数据在 localStorage | BKT 参数固定；证据和推断混在一起存；**猜对也加掌握度**（scoring.ts:39-41） |
| 3 | 诊断评估 | **可演示** | lib/adaptive.ts:14-41（确定性决策树）、lib/calibration.ts:53（θ 估计）、lib/grading.ts:46-73（陷阱识别）、12 根因计数 | 过程分析只有 6 道 OCR 题，而且不写回画像 |
| 4 | 知识教师 | **可演示**（多模态部分仅方案） | lib/lesson.ts:49（九步微课，静态拼装）；/api/coach（LLM 苏格拉底对话） | 没有视频、白板或讲解 DSL |
| 5 | 思维教练 | **半成品** | lib/methods.ts（按方法挑题）；content/sprint 28 道静态变式 | **没有生成能力**，没有思维导图 |
| 6 | 训练 | **可演示**（只有选题） | mistakes.pickVariant、adaptive、review（2/7/14/21 天间隔复测） | 没有生成题，没有 11.3 十项检查 |
| 7 | 错误分析 | **可演示** | 按 12 根因分组，429 个陷阱码 | 归因来自预标注的错误选项匹配，不分析过程 |
| 8 | 规划 | **半成品** | lib/agenda.ts（只有今日）、scoring 里的优先级 top6、学期同步 | 没有周计划、月计划和里程碑 |
| 9 | 中考教练 | **可演示** | lib/exam.ts（组卷、五维归因、分数区间） | 组卷**不按年级过滤**（会把七年级内容混进中考卷，exam.ts:31-49）；**用 Q1 题给出预测区间**，违反方案 11.3 和 README:124 |
| 10 | 陪伴激励 | **半成品** | growth（徽章、连续天数）、十二尾羽、唤醒 | 没有状态监测和情感支持；这部分职责归点点 |
| 11 | 验证 | **半成品** | ① 填空题等价判分接入了 lib/symbolic.ts（确定性数值采样）；② 教练输出只核验纯数值等式，结果只显示为徽章（lib/verify-claims.ts）；③ 几何一致性只在离线脚本里跑 | 没有 SymPy；不能验证证明，不能检查生成题，不能拦截 LLM 输出；不支持三角函数 |
| 12 | 报告 | **可演示** | narrative、weekly、teacher、consultation、trends，全部规则式 | "教师版"只能看当前账号自己的画像 |

**统计**：已上线 0／可演示 7／半成品 5／仅方案 0。

### 2.3 页面模块覆盖（对照方案 12.1–12.4）
- **可演示**：今日学习、AI 教练、能力诊断、同步学习、思维训练、错题诊所、知识地图、中考模式、成长中心、过程得分（只有 6 题）、运维后台。
- **半成品**：个性题库（没有独立页面）；家长端（与学生**共用一个账号**）。
- **仅方案**：教师与教研端（班级热力图、作业布置、题目审核、图谱维护、对话抽检）；教研内容中台。

### 2.4 自述与代码不符的地方
1. README:72 写"10 就绪/2 部分"；README:27 和 progress:59 写"12/12 全就绪"。实际 5 个是半成品，总控只是展示用。
2. "符号验证 ✅"（README:21,69）和"[ ] 符号计算验证引擎"（README:118）自相矛盾；实际是 TS 数值采样，**不是 SymPy**。
3. progress:151 写 Next.js 15，实际是 16.2.10。
4. 各章节点数和题数与 progress 表对不上（fun、sim、trg 三章）；README 说"20 道种子题"，实际 721 道。
5. **题目来源矛盾**：README:123 说"重新编写的样例，非教材原题"；progress:105-107 说来自"05 题库（**真题**）"和苏科版**教科书 PDF** 抽取，章节文件头也写"真题库"。
6. "全站强制登录"（progress:55）：只要缺少 Supabase env，middleware 就放行（middleware.ts:12）。
7. "schema 含 RLS"（README:88）：代码**只用了 `profiles` 一张表**，其余 6 张表没有任何读写。
8. "安全红线 ✅"（README:81）：没有 AI 身份披露、转介话术和内容安全过滤。

---

## 3. 数据库

- `db/schema.sql`（7 张表，public schema）+ `db/schema-remaining.sql`（补跑脚本）。**没有迁移工具，也没有版本号**，要手工粘贴到 SQL Editor 执行。
- **代码只用了 `profiles`**：id、data jsonb（整份 StudentProfile）、updated_at，按"最后写入者胜出"同步。
- 以下表**代码都没有使用**：knowledge_nodes、questions（payload 含答案）、students、attempts、node_mastery、coach_sessions（教练对话完全不落库）。

### RLS
- 每张表都是"本人读写"。
- `knowledge_nodes` 和 `questions` 对所有人可读（注释自己承认答案字段应当拆开）。
- `/api/admin/users` 用 service_role 读取所有用户的完整画像。管理员身份靠 `NEXT_PUBLIC_ADMIN_EMAILS` 白名单判断，这个白名单会**进入客户端 bundle**。
- 没有 RLS 测试。

### 学生、家长、教师关系
- **没有家长实体，也没有 guardian_student_link**。家长和学生共用一个账号。
- 家长端靠一个"二次密码"：SHA-256 加固定盐，哈希存在学生画像里（lib/parent-gate.ts）。学生可以删改这个哈希，**未设置密码时直接放行**，所以这道门可以被学生绕过。
- 没有教师角色。
- **没有按手机号关联**（全仓库没有 phone 字段）。这一条不违规，原因是根本没有建家长关系模型。
- 没有监护人同意、隐私或未成年人条款。

### 种子与行数
- 没有 seed。生产环境 `profiles` 行数等于注册用户数，数量不可知（progress 表明有注册用户）。

### 与 wenli 的关系
- **代码不共享**。
- 两边的 `profiles` 同名、同 schema、结构不兼容，所以大概率是**不同的 Supabase 项目**，但无法验证。

---

## 4. 模型调用

| 项 | AI 教练 `/api/coach` | 拍照过程判分 `/api/process-grade` |
|---|---|---|
| 供应商与模型 | DeepSeek `deepseek-chat`（默认），可切到 DashScope `qwen-plus`；`OPENAI_BASE_URL` 可覆盖（lib/llm.ts:10-28） | DashScope `qwen-vl-max`（lib/vision.ts:15-33） |
| Prompt | lib/prompts.ts:8-34，没有版本号 | lib/process-grade-prompt.ts:7-38，没有版本号 |
| 流式 | **是**（SSE） | 否 |
| 参数 | temperature 0.5，max_tokens 1200 | temperature 0，max_tokens 1500 |
| 超时与重试 | maxDuration 60，不重试 | maxDuration 60，断连后重试 1 次 |
| 输入校验 | zod：不超过 40 条消息、每条不超过 4000 字 | zod：只接受 data:image |
| 鉴权 | 只有配置了 Supabase 时才要求登录 | 同左 |
| 计量、幂等、限流 | **都没有** | **都没有** |
| 落库 | **不落库** | 不落库，也不回写画像 |

- **提示等级由客户端决定**：`allowedHintLevel` 来自请求体（route.ts:13），学生构造请求就能直接拿到 6 级提示。
- **token 粗估**（不是实测）：
  - system prompt 约 1–1.5k tokens，回复约 100–250。
  - 历史每轮全量回传，10 轮会话累计输入约 15–20k。
  - OCR 单次：图片加约 0.5k 文本，输出不超过 1.5k。
- **Key 只在服务端**，没有前端直连模型。
- **跨境**：模型供应商在境内，但应用托管在 Vercel，Supabase 区域未知。未成年人画像和**手写作答照片**可能先经过境外。

---

## 5. 评测资产

| 资产 | 现状 |
|---|---|
| 判分金标准、与教师一致率 | 没有 |
| 人设评测 | 没有（规格书第八章的 13 类回归用例一条都没有） |
| 单测、e2e | 0 / 0 |
| **确定性数学验证器** | **有，轻量版**：`lib/symbolic.ts` 自写解析器，在 8 个固定无理数点采样求值，容差 1e-6，至少 4 个点一致才判等价，用于填空题判分。选择题是答案集合比对，也是确定性的 |
| 数值核验 | `lib/verify-claims.ts`，只显示结果，不拦截 |
| 几何核验 | `lib/geometry.ts`，用于离线内容质检 |
| OCR 判分评测 | 没有 |

---

## 6. 内容资产

| 资产 | 数量 |
|---|---|
| 章节 | 32 章（苏科版七上到九下） |
| 知识节点 | **939 个**（K/S/P/M） |
| 章节题 | **693 道**（诊断约 430、复测约 125、迁移约 115、探针 29、学习 5）；题型只有单选、多选、填空，**解答题被改成了选择或填空** |
| 压轴专项 | 5 类，28 道 |
| 错误陷阱码 | 429 个，归入 12 根因 |
| 能力码 | 11 个（10 维 × 10 板块） |
| 几何图 | 章节题里 31 处 + 压轴母题图 |
| **Rubric** | **只有 6 道**，覆盖 32 章中的 4 章（equ、fun、sim、trg），分建模和几何两套框架 |
| 方法库 | 10 个（方案写的是十二类，少 2 类） |
| 南京卷面结构 | 120 分，6+10+11 题，难度系数 0.7，易中难 7:2:1（content/exam-structure.ts）；板块权重自认"Q1 待校准，部分是产品推导值" |
| 元数据 | 只支持苏科版；没有 region、exam_year、textbook_version 字段 |

- **审核状态**：**全部是 Q1**（728 处），没有 Q2 及以上。但模考预测已经在使用这些题。
- **版权风险高**：progress 明确说来源是"真题"和"教科书 PDF 抽取"，与"重新编写"的说法矛盾，而且没有逐题标注年份和地区。
- **题库和答案全部进入浏览器 bundle**，判分也在客户端执行。作答结果可以被伪造，作为平台证据的可信度低。

---

## 7. 人设实现与规格差距（阿鸦-规格书-V1.1）

| 规格 | 代码现状 | 差距 |
|---|---|---|
| 2.1 导师/教练，不是伴学 | prompt 写"陪伴南京初中生的伴学精灵"（prompts.ts:10） | 偏向陪伴型，和点点重叠 |
| 2.1 形象：圆规耳朵、函数尾巴、星图眼 | 彩虹巨喙、尾羽扇、琥珀眼 | 形象不一致 |
| 4.1 AI 身份披露 | 没有（CoachChat.tsx:122 写的是"我是你的数学教练"） | **缺失** |
| 2.3-1 验证器优先 | LLM 拿到完整解析，只靠 prompt 约束 | 缺少强约束 |
| 2.3-2 提示等级由引擎决定 | 由客户端决定 | 可被绕过 |
| 2.3-3 讲法由引擎选择 | 没有 | 仅方案 |
| 2.2 设定元素反滥用 | 每答对一题都显示"投进一颗石子" | 需要重新对齐 |
| 4.3 索要答案的三段结构和典故变体 | 没有 | 缺失 |
| 4.6 猜对不庆祝、记为未掌握 | UI 照样庆祝，掌握度照样增加 | **违背** |
| 4.9 未验证题不讲 | Q1 题照常教和测 | 违背 |
| 4.10 情感信号与转介 | 没有 | **缺失** |
| 4.11 阿鸦不进家长端 | 家长端标题是"阿鸦来报喜"，还放了阿鸦形象（app/parent/page.tsx:153-155） | **违背** |
| 4.12 考前四周"稳住"模式 | 没有 | 缺失 |
| 5.1 鼓励要具体 | 有"你能行！" | 轻度偏离 |
| 5.2 禁语 A–F | prompt 只有 4 条红线，没有输出过滤 | 大部分缺失 |
| 5.3 年级语气三档 | 没有 | 缺失 |
| 八章 回归集 | 没有 | 缺失 |

注：阿鸦规格书定义的禁语是 **A–F**；G 类属于考官型基线，不适用于阿鸦。

**与点点重叠、需要剥离的能力**：今日中枢（agenda）、连续天数和成就（growth）、家长周报口吻、升学路径会诊、学期同步、账号/设置/云同步/家长门禁/管理后台。

**阿鸦应保留**：唤醒词、石子比喻、规律碎片和定理图鉴、学科内提示阶梯。

---

## 8. 六方法对应
插件 API 映射和溯源字段对照见 [adapter-design.md](./adapter-design.md) 第 1、2 节。

结论：diagnose 和 report 都是纯函数，可以高度包装；grade 的客观题是确定性的（但要移到服务端）；teach 的人设要重写；generate_variation 没有生成能力。

---

## 需要人确认的疑点

1. **线上状态**：`ayaheshui.vercel.app` 是否在线？生产 `profiles` 里有多少真实用户，其中多少是未成年人？目前没有监护人同意，也没有隐私条款。
2. **Supabase 项目**：用的是哪个项目，是否与文砺、知行共用？区域在哪？Vercel 函数区域在哪？未成年人画像和手写照片是否出境？
3. **题目版权**：693 道章节题来自"真题"题库和教科书 PDF 抽取，与"重新编写"的说法矛盾。授权情况如何？逐题来源是什么？
4. **生产环境是否执行过 `db/schema-remaining.sql`**？如果执行过，含答案的 `questions` 表对所有人可读。
5. `docs/superpowers/plans/2026-07-19-user-management-redesign.md:434` 里有 1 个邮箱地址（内容没有复制）；`NEXT_PUBLIC_ADMIN_EMAILS` 会进入客户端 bundle。这些是否需要处理？
6. `.env.example` 需要人工确认只有占位符。
7. 阿鸦的禁语按 **A–F** 对齐（G 类属于考官型），请确认。
8. **"12 Agent"的对外口径**：按"7 个可演示、5 个半成品、0 个已上线"上报，需要阿鸦负责人确认。
9. **generate_variation**：契约 v1 是否接受"从已审题库选变式"？
10. **grade 验证器路线**：把现有 TS 数值采样引擎登记为验证器，还是新建 Python SymPy 服务（开发计划冲刺 1-D）？
11. **Q1 内容**能否进入平台正式测评和星亮门槛？教研过审（升 Q2）的排期是什么？
12. **LGPL**：sharp/libvips 是否登记？统一前端壳里是否禁用图片优化？
13. **人设形象**：代码里的立绘和规格书 V1.1 冲突，以哪一版为母版？
14. **十二尾羽和"每答对一题投一颗石子"**与规格书的反滥用条款如何取舍？
