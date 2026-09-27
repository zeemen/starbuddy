# KGE-00：三科知识资产盘点脚本与报告

状态：**草稿，等人确认后才开始实现**。确认人、日期：＿＿＿
任务包：KGE-00（开发计划 v2.0 冲刺 0 · W1，盘点项 6“知识资产”与盘点项 7“画像相关数据”）

关联：
- **开发计划 v2.0** 第三章 W1 盘点表：
  - 项 6 知识资产（KGE-00）：“节点 / 题目 / Rubric 计数；字段缺失；孤点与环；旧编码并存（文砺 20 格与 28 点）；道法页码是否绑定版次；《学生读本》是否入库” → 产出“知识资产盘点报告（回填 KGE 第七章）”；
  - 项 7 画像相关数据：“各科现有学生报告里有哪些结论性字段、如何算出、有无证据引用” → 产出“结论字段清单（SIE 迁移输入：旧结论不迁移，只存档）”；
  - 盘点纪律：“状态只按‘学生今天能不能真的用’打标”；
  - 第十章“本周就做”第 4 项：“用 Claude Code 编写 KGE-00 知识资产盘点脚本，获取四科库的只读权限”。
- **KGE V1.1**：第三章六条粒度标准（“可独立诊断：能出至少 3 道以它为主考点的题”等）；第七章三科资产检查（21 项问题，其中 4 项阻断，本任务要用实物数据核验）；第八章 kge schema 草案（字段名作为对照口径）；第九章 S0 冲刺“KGE-00 资产盘点脚本：三科 schema 表清单、节点 / 题目 / Rubric 计数、字段缺失、孤点、环、旧编码并存检查”，验收“M0：盘点报告回填第七章”。
- **完整方案 v1.1** 5.10：四项阻断问题中“数学：知识图谱节点清单的实际完成度未知”“道法：页码索引未绑定版次与印次”“道法：《学生读本》尚未入库”都要本任务给出实数。
- **对照文档** `specs/plan-v2-alignment/spec.md` #11：盘点脚本写成“给人运行”的只读方式，不连接任何非本机数据库。
- **已有盘点**：`specs/inventory-{aya,wenli,zhixing}/`（2026-09-25 负责人确认），本任务不重做模块状态，只补知识资产与结论字段两项。
- **已有工具**：`adapters/aya/scripts/legacy-loader.ts` 与 `legacy-hooks.mjs`（让 Node 直接 import legacy 的 TS 源码，只读）；`adapters/aya/content/pack.json`（阿鸦内容包，已从 legacy 导出）。
- **CLAUDE.md**：规则 8（不出现真实学生数据）、9（不连接非本机数据库）、13（个人层不进公共层）、14（不接触留出集）。

## 目标
用一个可复跑的脚本，从两个只读 legacy 仓库里把三科知识资产数清楚、查出结构问题，生成《知识资产盘点报告》与《结论字段清单》，供教研与技术负责人在 W2 决策会上确认切片范围、回填 KGE 第七章。

## 现状（起草时核实，2026-09-27）

三科知识资产**都在 legacy 仓库的 TS 常量或 JSON 里**；各科 Supabase 库里只有 schema，另有少量由脚本或 AI 生成后写入的内容行。路径缩写：A = `legacy/aya`（`/home/user/ayaheshui`），W = `legacy/wenli/初中语文_AI_class/wenli`，S = `W/packages/shared/lib`。

| 学科 | 资产 | 位置与格式 | 起草时看到的情况 |
|---|---|---|---|
| 数学 | 知识节点 | `A/content/chapters/<32 章>/nodes.ts`，由 `A/content/topics.ts` 汇总并导出 `allNodeById` | 959 个（K 322 / S 312 / M 159 / P 152 / C 14）。字段只有 9 个：id、name、kind、cluster、clusterName、core、prerequisites、abilities、evidence。没有“后续”“基础 / 中考要求”“常见错误”。`prerequisites` 混用节点 id 和自然语言（如“有理数运算”），不分强弱。`A/db/schema.sql` 的 `knowledge_nodes` 表未被使用，约束装不下 C 类 |
| 数学 | 100 能力点 | `A/lib/types.ts` 的 `BOARDS` × `ABILITY_DIMS`；节点与题目挂 11 个 `ABILITY_CODES`（`A/content/abilities.ts`），靠 `dim` 映射 | 没有独立的 100 点数据，是两轴常量加映射 |
| 数学 | 题库 | `chapters/*/questions*.ts`（693）与 `A/content/sprint/*.ts`（28），字段见 `A/lib/types.ts:120-152` | 721 题；`reviewStatus` 全是 Q1；没有六段标识与版权字段；难度是八维向量 D1–D8 |
| 数学 | 错因 | `ROOT_CAUSES`（12 类）；`ERROR_TRAPS`（429 个，code / desc / rootCause） | 陷阱码与根因有映射；掌握五门槛 `MASTERY_GATES`；没有名为“能力五层级”的定义 |
| 语文 | 28 点 | `S/domain/ability.ts`；库里枚举 `domain_t`、`level_t` | 旧 5 × 4 = 20 格在代码、迁移、data 里都没找到；仓库只有 1 个提交，查不到历史 |
| 语文 | 篇目与素材 | `W/data/catalog.json`（237 篇）、`corpus_candidates.json`（109 篇）、`S/domain/classical-catalog.ts`（55 条）、`S/domain/writing-syllabus.ts`（35 个写作单元） | “85 篇必背”没有静态清单，只在库表 `textbook_catalog.must_recite`；“120 实词、18 虚词”“34 个单元导语阅读方法”都没找到；篇目与写作单元都没有页码 |
| 语文 | 判分与错因 | 作文五维在 `S/prompts/essay-rubric.ts`（prompt 字符串，无版本）与 `S/domain/schemas.ts` 的 `GradeResultSchema`；阅读错因标签 `W/content/reading/错因标签.json`（15 个标签）与 `S/domain/reading-error-tags.ts`；中文六类 `S/domain/error-types.ts` 与枚举 `error_t` | 阅读采分点没有独立资产，由模型生成 `grade_focus`；错因两套命名并存，经 `systemErrorType` 映射 |
| 道法 | 大概念与页码 | `S/domain/daofa-knowledge-map.ts` 的 `DAOFA_KNOWLEDGE_MAP` | 4 个领域共 30 个节点，另有 6 个读本节点（xr*）。`pages` 是字符串、注明近似，读本节点标“待核”；没有版次与印次；没有高频设问、答题话术、真题回链 |
| 道法 | 采分点 Rubric | `S/domain/daofa-question-bank.ts` 的 `DaofaRubricPoint` | 2 道题共 6 个采分点，字段齐全，没有 `rubric_version`；AI 生成题的 Rubric 在库表 `daofa_item.rubric` |
| 道法 | 时政、《学生读本》 | `W/apps/daofa/app/news/page.tsx` 的 `SEED_NEWS`；读本只作节点来源 | 时政 2 条，没有生效 / 失效字段；读本没有原文入库 |

结论性字段（盘点项 7）分布在：
- 阿鸦 `A/lib/evaluation.ts`、`exam.ts`、`calibration.ts`、`weekly.ts`、`teacher.ts`、`consultation.ts`、`trends.ts`；
- 文砺 `S/domain/schemas.ts` 的 `DiagnoseResultSchema`、`PlanResultSchema`、`GradeResultSchema`、`ParentReportSchema`，以及表 `ability_state`、`point_mastery`、`mastery_snapshot`、`parent_report`、`stage_report`；
- 知行 `S/domain/daofa-diagnosis.ts`、表 `daofa_ability`、`daofa_submission`。

上表是起草时的人工调查结果。报告里的数字一律以脚本输出为准；两者不一致时，把差异写进报告。

## 设计要点

### 1. 两种模式
- **静态模式**（本会话与任何有 legacy 只读副本的人都能跑）：只读 `legacy/` 下的源码与 JSON，不联网、不连库。覆盖上表绝大部分资产。
- **库模式**（给人运行）：一份只读 SQL，只查内容类表的计数与结构，由有权限的人在各科 Supabase 上运行，把输出文件交回。本会话不运行它，也不索要凭据。

### 2. 代码位置与运行方式
- 新目录 `tools/kge-inventory/`：Node ≥ 22.18，直接运行 TS（与 `adapters/aya` 相同），**零运行时依赖**，devDependencies 只有 `typescript` 与 `@types/node`（与 `adapters/aya` 同版本，许可证已核）。
- legacy 加载：把 `adapters/aya/scripts/legacy-hooks.mjs` 的做法泛化为两个根目录（阿鸦的 `@/` 别名、文砺 monorepo 的包内相对路径）。只 import 纯数据模块；数据模块引用的第三方包（如 `zod`）由加载钩子替换成空桩，不在 legacy 里安装依赖，不写 legacy 任何文件。
- 入口：`node --import ./src/loader.ts src/main.ts --aya <路径> --wenli <路径> --out <目录>`；`make kge-inventory` 用默认路径 `legacy/aya`、`legacy/wenli`，找不到就明确报错退出。

### 3. 检查项（静态模式）
按学科输出，每项给计数、比例与样例 ID（样例最多 10 个，只含资产 ID 与名称，不含题干全文）。

**通用（三科都跑）**
1. 节点：总数、按类型计数、字段缺失率（按 KGE `knowledge_node` 的 includes / excludes / examples / scope / cognitive_levels 口径逐项标“有 / 无 / 可推导”）。
2. 边：可解析为节点 ID 的前置边数；自然语言前置数与样例；指向不存在 ID 的悬空边；**环**（按可解析边做强连通分量）；**传递边**（A→C 且存在 A→…→C 的其他路径）；强弱是否区分。
3. 孤点：没有任何边、也没有题目或 Rubric 引用的节点。
4. 旧编码并存：同一概念多套编码（阿鸦 939 / 959 去重、知识节点 kind 与 `A/db/schema.sql` 约束不符；文砺 20 格与 28 点；错因中文六类与英文标签两套）逐项核对，写明“发现并存 / 只有一套 / 无法判断（需库模式或历史）”。
5. 六段标识：题目与内容上 region / exam_year / grade / textbook_version / subject / content_version 各字段的覆盖率；审核状态字段的取值分布。

**数学**
6. 题目：总数、按 usage 与 format 计数；mainNodes / auxNodes / preNodes 可解析率；**以某节点为主考点的题目数分布**，列出少于 3 题的节点数（粒度标准“可独立诊断”的初筛，只作信号，不下结论）；九年级“函数”主题的节点与题目覆盖单列（封测切片起点）。
7. 陷阱码到根因的映射完整率；陷阱码被题目引用的比例。
8. 100 能力点：由 BOARDS × ABILITY_DIMS 与 ABILITY_CODES 映射推出的格子数、每格挂的节点数与题目数、空格数。

**语文**
9. 28 点定义与库枚举是否一致；20 格是否存在（静态模式只能查当前快照）。
10. 篇目：catalog / corpus_candidates / classical-catalog 三份清单的条数、交集与差集（按标题 + 作者）；有无册 / 课 / 页；“85 篇必背”“120 实词”“18 虚词”“34 个单元导语”逐项写“找到（位置）/ 未找到”。
11. 写作单元：条数、有无页码。
12. 判分依据：作文五维是否有结构化版本号；阅读采分点是否有静态资产；错因两套命名的映射完整率。

**道法**
13. 大概念节点：领域分布；`pages` 的覆盖率与格式（能否解析为页码区间）；标注“近似”“待核”的节点数；**是否绑定版次 / 印次**（逐节点）。
14. 采分点 Rubric：题数、采分点数、字段完整率、有无 `rubric_version`。
15. 时政：条数、有无生效 / 失效字段。
16. 《学生读本》：相关节点数、有无原文、有无页码；结论写“未入库 / 部分入库 / 已入库”。

### 4. 库模式的只读 SQL
- 文件 `tools/kge-inventory/db/readonly-inventory.sql`，开头 `BEGIN TRANSACTION READ ONLY; SET LOCAL statement_timeout = '30s';`，结尾 `ROLLBACK;`。
- 只查**内容类表**：阿鸦库 `A/db/schema.sql` 里代码不用的内容表（如 `knowledge_nodes`）；文砺库 `textbook_catalog`（总数、`must_recite` 为真的篇数、有无页码列）、`recitation_corpus`、`daofa_item`（总数、`rubric` 非空数、按审核状态计数）以及其他盘点中确认的内容表；另查 `information_schema` 的表与列清单。
- **不查任何学生个人表**（作答、画像、报告、错题、账号），也不输出任何行内容，只输出计数与列名。文件里用一条静态检查保证：禁止出现学生表名、`SELECT *`、写语句（见验收）。
- 由人运行，输出保存为 `db-output-<学科>.txt` 交回；脚本提供 `--db-output` 参数把它并入报告。没有交回时，报告里对应项写“待库模式”。

### 5. 结论字段清单（盘点项 7）
- 静态模式对上面列出的结论性代码做结构提取：每个结论字段写来源文件、字段名、类型、算法（规则 / 模型 / 混合，按代码判断）、**有无证据引用**（是否带题目 / 作答 ID）、是否带置信度。
- 输出单独一份 `conclusion-fields.md`，每条标“SIE 迁移处理：不迁移，只存档”（完整方案 v1.1 5.5）。
- 只读代码与类型，不读取任何 localStorage 导出或库里的学生数据。

### 6. 输出
- `--out` 目录下生成：`asset-inventory.md`（给人看的报告，章节与 KGE 第七章三科对应，末尾汇总“阻断 / 重要 / 一般”问题的实物核验结果）、`asset-inventory.json`（全部计数，便于 W2 以后复跑对比）、`conclusion-fields.md`。
- 报告首部记录 legacy 两个仓库的 commit、脚本版本、运行时间。输出排序固定，同一输入两次运行结果逐字节相同（运行时间单列一行，比较时忽略）。
- 生成的报告提交到 `specs/kge-00-asset-inventory/report/`。报告只含资产 ID、名称、计数与文件路径，不含题干、范文、原文全文（版权），不含任何学生数据。
- **不改 KGE V1.1 的 HTML**：第七章的回填由教研与技术负责人依据报告完成。

### 7. 测试
- `tools/kge-inventory/tests/` 用**合成夹具**（小型假 legacy 目录，两科各一份）覆盖：环、传递边、悬空边、自然语言前置、孤点、少于 3 题的节点、六段标识缺失、页码无版次、Rubric 缺版本、两套编码并存、第三方 import 被替换成空桩。
- SQL 静态检查：只含 `SELECT`、`BEGIN`、`SET LOCAL`、`ROLLBACK`；不含 `SELECT *`；不含学生表名黑名单；有 `READ ONLY`。
- 确定性：同一夹具运行两次输出相同。
- 真实 legacy 不在 CI 里（CI 没有 legacy 副本）；`make check` 只跑夹具测试（新目标 `kge-inventory-test`），真实报告在本地生成后提交。

## 范围
### 做
- `tools/kge-inventory/`：加载器、静态检查、报告生成、只读 SQL、夹具测试。
- Makefile：`kge-inventory`（本地生成报告）、`kge-inventory-test`（加入 `make check` 与 CI）。
- 在本会话用静态模式跑一次真实 legacy，提交报告与结论字段清单。
- `specs/kge-00-asset-inventory/lessons.md`：记录与起草调查不一致的数字、无法判断的项。

### 不做
- 不连接任何数据库；不索要凭据；库模式只交付 SQL。
- 不修改 legacy；不在 legacy 里安装依赖。
- 不导入任何资产到平台库（导入是 KGE-01 / KGE-02）。
- 不判断节点粒度是否合格、前置是否成立（那是教研的试标注与必要性检验）；脚本只给信号。
- 不改 KGE / SIE 的 HTML 文档、`docs/plan/`。
- 不涉及英语（已搁置）。
- 不读取留出集（CLAUDE.md 规则 14）。

## 验收标准（可测试）
- [ ] `make kge-inventory-test` 通过，并已加入 `make check` 与 CI；夹具覆盖“测试”一节列出的全部检查项，每项至少一个正例与一个反例。
- [ ] 静态模式对真实 legacy 运行成功，生成 `asset-inventory.md`、`asset-inventory.json`、`conclusion-fields.md`，报告首部有两个 legacy commit。
- [ ] 报告逐项回答开发计划 v2.0 盘点项 6 的六个问题：节点 / 题目 / Rubric 计数；字段缺失；孤点与环；旧编码并存；道法页码是否绑定版次；《学生读本》是否入库。每个回答给数字或“未找到（查过的位置）”或“待库模式”。
- [ ] 报告对 KGE 第七章四项阻断问题各给出实物核验结论与数据。
- [ ] 同一输入两次运行，`asset-inventory.json` 逐字节相同。
- [ ] 报告与清单中不出现题干、范文、原文全文，不出现任何学生数据（静态检查：输出里没有超过 40 字的连续中文原文片段；字段名白名单）。
- [ ] 只读 SQL 通过静态检查，且在本机临时 Postgres 上对一个按 legacy 迁移建出的**空库**能执行完毕（证明语法与只读事务正确；不连任何远程库）。
- [ ] legacy 两个目录在运行前后 `git status` 均无变化。

## 涉及的必停项（见 CLAUDE.md）
- 迁移 / RLS / 计量 / 契约 / 人设 / 判分与掌握逻辑 / 判断模板：**否**。
- 第三方服务、模型供应商、数据出境：**否**（脚本不联网，不调用模型）。
- 新增依赖：只有 devDependencies `typescript`、`@types/node`，版本与 `adapters/aya` 相同。
- 库模式的 SQL 由人拿去连各科库运行：这是人的操作，不在本仓库自动执行。

## 依赖与输入
- `legacy/aya`、`legacy/wenli` 只读副本（本会话已有）。
- 已有盘点 `specs/inventory-{aya,wenli,zhixing}/`。
- 库模式的输出：需要有各科 Supabase 只读权限的人运行（见待人确认 #3）。

## 待人确认
1. **报告放哪、谁回填第七章**：默认提交到 `specs/kge-00-asset-inventory/report/`；KGE V1.1 第七章由教研与技术负责人依据报告更新，本任务不改 HTML。
2. **“少于 3 题”阈值**：按 KGE 粒度标准“能出至少 3 道以它为主考点的题”做初筛，只作信号。教研是否要别的阈值或按认知层次分别统计？
3. **库模式谁来跑、跑哪几个库**：文砺 / 知行共用的 Supabase 一个库；阿鸦库除存学生 JSON 的 `profiles` 外，还有 `A/db/schema.sql` 建的 6 张代码不用的表（含 `knowledge_nodes`），里面有没有数据未知，默认也跑一次（只查这几张内容表的计数，不碰 `profiles`）。需要一位有只读权限的人在 W1 周五前运行并交回输出。
4. **文砺 20 格**：当前快照里找不到；仓库只有 1 个提交。是否能提供完整 git 历史，或由文砺负责人确认 20 格是否已完全退役？
5. **语文素材清单**：“85 篇必背、120 实词、18 虚词、34 个单元导语阅读方法”在仓库里都找不到静态清单。它们在哪里（库、文档、教研手里的表格）？找不到的话，报告里如实写“未找到”，由教研决定语文切片（阶段 1 · 1–2 月）的素材来源。
6. **自然语言前置**：阿鸦 `prerequisites` 里的自然语言项，脚本只能列出、不能自动映射到节点。映射交给 KGE-01 导入时由教研做，还是本任务先出一份“候选映射”（按名称精确匹配，只作建议）？默认只列出，不做候选映射。
7. **报告里的节点名称与篇目标题**：属于内容元数据，不含原文。默认可以写进报告；如果教研认为篇目清单本身也涉及版权或不宜入库，改为只写 ID 与计数。
