# KGE-00 知识资产盘点报告

> 由 `tools/kge-inventory` 生成，请勿手改；重新生成：`make kge-inventory`。
> 依据：开发计划 v2.0 冲刺 0 · W1 盘点项 6；KGE V1.1 第七章。口径与限制见 `specs/kge-00-asset-inventory/spec.md`。
> 本报告只含资产 ID、名称、计数与文件路径，不含题干、范文、原文，不含任何学生数据。

## 生成信息

| 项 | 值 |
|---|---|
| 工具 | tools/kge-inventory 0.1.0 |
| legacy aya | `legacy/aya` · 提交 0ed2fe45b13248737df47f1614b205e727ce29c6 · 提交数 1 |
| legacy wenli | `legacy/wenli` · 提交 67d5e8f1aa5d1f015ed306f37d1d3652e8aece06 · 提交数 1 |
| 库模式输出 | 未提供（相关项写“待库模式”） |

## 一、盘点项 6 的六个问题

### 节点 / 题目 / Rubric 计数

| 学科 | 回答 |
|---|---|
| 数学 · 阿鸦 | 节点 959（C 14 / K 322 / M 159 / P 152 / S 312）；题目 721；Rubric：无独立 Rubric，判分靠答案与 429 个陷阱码 |
| 语文 · 文砺 | 能力格 28（7 域 × 4 级）；可打标能力点 0；篇目清单 237 / 109 / 55 条；写作单元 35；题目：仓库里没有静态题库，见库模式；Rubric：作文五维量表 1 份（prompt 字符串），阅读采分点无静态资产 |
| 道法 · 知行 | 节点 36（统编 30，读本 6，5 个领域）；静态题目 2；采分点 6；AI 生成题与其 Rubric 在库表 daofa_item，见库模式 |

### 字段缺失

| 学科 | 回答 |
|---|---|
| 数学 · 阿鸦 | KGE 节点字段 includes / excludes / scope / cognitive_levels / map_version 全部缺失；题目六段标识 region 0/721、exam_year 0/721、grade 0/721、textbook_version 0/721、subject 0/721、content_version 0/721；前置不分强弱 |
| 语文 · 文砺 | 篇目与写作单元都没有册-课-页；静态清单没有“必背”标记；作文量表没有版本号；120 实词、18 虚词、34 个单元导语都没有找到清单 |
| 道法 · 知行 | 没有版次 / 印次（0/36）；没有高频设问、答题话术、真题回链挂载；Rubric 没有 rubric_version（0/2）；时政没有生效 / 失效日期；6 个节点的规范表述为空 |

### 孤点与环

| 学科 | 回答 |
|---|---|
| 数学 · 阿鸦 | 环 1 个；孤点 4 个；传递边 74 条；另有自然语言前置 156 条、悬空边 1 条无法参与计算；507 个节点没有任何以它为主考点的题目 |
| 语文 · 文砺 | 不适用：语文现有资产是能力坐标与素材清单，没有节点间关系，也就没有环与孤点 |
| 道法 · 知行 | 节点间没有任何边，不存在环；没有静态题目引用的节点 32/36 |

### 旧编码并存

| 学科 | 回答 |
|---|---|
| 数学 · 阿鸦 | 没有节点使用 KGE 命名（0/959），全部要进迁移表；其中 956 个形如 K-FUN-0901，其余 3 个另有格式；题目里有 97 处用自然语言而不是节点 ID 引用节点；节点类型 C 不在 NodeKind 定义中、C 不在未使用的 knowledge_nodes 表约束中；没有“能力五层级”数据 |
| 语文 · 文砺 | 旧 20 格：当前快照里没有提到（仓库只有 1 个提交，查不到历史；库里见库模式）；错因两套命名并存（中文六类 + 15 个英文阅读标签），映射完整率 100.0% |
| 道法 · 知行 | 节点 ID 是短编码（如 l1、l2、l3），0/36 个使用 KGE 的 MORAL.* 命名，需要迁移表；失分归因四类（审题 / 定位 / 转化 / 规范）与平台七大类的映射见 docs/mapping/error-taxonomy.md |

### 道法页码是否绑定版次

| 学科 | 回答 |
|---|---|
| 道法 · 知行 | 否：36 个节点里绑定版次 / 印次的 0 个；统编节点有页码 30/30，源文件注明为近似值；读本节点页码“待核” 6 个 |

### 《学生读本》是否入库

| 学科 | 回答 |
|---|---|
| 道法 · 知行 | 未入库（只有脚手架节点，没有原文、页码与规范表述）；读本节点 6 个 |

## 二、KGE 第七章阻断问题的实物核验

| 学科 | 问题 | 核验结论 | 依据（本报告的键） |
|---|---|---|---|
| 数学 · 阿鸦 | 知识图谱节点清单的实际完成度未知，不能以 100 能力点代替诊断单元 | 节点清单是实物：32 个专题、959 个节点、1041 条可解析前置边，覆盖七至九年级苏科版；但前置边有 156 条自然语言、74 条传递边，且节点缺少包含 / 不包含 / 范围 / 认知层次字段。全部节点中 507 个没有以它为主考点的题目；九年级“函数”主题 45 个节点中 39 个主考点题目少于 3 道。可作为 KGE-01 导入的起点，拆解、必要性检验与试标注仍需教研完成 | `math.nodes` `math.prereq.text` `math.graph.transitive` `math.nodes.main_zero` `math.g9fn.main_lt3` |
| 语文 · 文砺 | 28 点尚未下钻为可打标的能力点 | 确认：仓库里只有 28 格能力坐标、阅读题型与错因标签，没有“能力 × 文本类型 × 题型”的能力点数据；“积累与运用”素材网的 120 实词、18 虚词、34 个单元导语也没有找到清单，85 篇必背只能在库里查 | `chn.grid.cells` `chn.ability_kp` `chn.shici` `chn.xuci` `chn.unit_intro` `chn.must_recite.db` |
| 道法 · 知行 | 页码索引未绑定教材版次与印次 | 确认：0/36 个节点绑定版次；页码是写在节点上的字符串，源文件注明为近似值，能解析为区间的 30/30。迁移时须进入 textbook_projection 并由教研抽样核对 | `moral.pages.edition_bound` `moral.pages.parsable` `moral.pages.approx` |
| 道法 · 知行 | 《学生读本》属于考查范围但尚未入库 | 确认：未入库（只有脚手架节点，没有原文、页码与规范表述）。仓库里文件名含“读本”的文件 1 个（见 moral.reader.files 样例，需人工确认其中有无原文） | `moral.reader.nodes` `moral.reader.pages` `moral.reader.files` |

## 三、数学 · 阿鸦

读过的源文件：`content/topics.ts`、`content/sprint/index.ts`、`content/abilities.ts`、`lib/types.ts`、`db/schema.sql`

### 节点

| 键 | 项 | 值 | 样例 / 说明 |
|---|---|---|---|
| `math.topics` | 专题（章）数 | 32 |  |
| `math.nodes.raw` | 节点定义数（未去重） | 959 |  |
| `math.nodes` | 节点数（按 ID 去重） | 959 |  |
| `math.nodes.duplicate_ids` | 重复定义的节点 ID | 0 |  |
| `math.nodes.by_kind` | 按类型计数 | C 14，K 322，M 159，P 152，S 312 |  |

### 节点字段（对照 KGE knowledge_node）

| 键 | 项 | 值 | 样例 / 说明 |
|---|---|---|---|
| `math.kge_field.includes` | KGE 字段 includes（包含什么） | 无，可推导（0/959） | core（核心内容）可作参考，不是边界说明 |
| `math.kge_field.excludes` | KGE 字段 excludes（不包含什么） | 无（0/959） |  |
| `math.kge_field.examples` | KGE 字段 examples（典型题例） | 无，可推导（0/959） | 可由“以该节点为主考点的题目”推出 |
| `math.kge_field.scope` | KGE 字段 scope（范围属性） | 无（0/959） |  |
| `math.kge_field.cognitive_levels` | KGE 字段 cognitive_levels（认知层次） | 无（0/959） |  |
| `math.kge_field.error_type` | KGE 字段 error_type（易错节点的错因类型） | 无，可推导（0/959） | 易错信息只在题目选项的陷阱码里，可反查 |
| `math.kge_field.map_version` | KGE 字段 map_version（地图版本） | 无（0/959） |  |
| `math.field_empty.name` | legacy 字段 name 为空的节点 | 0（0.0%） |  |
| `math.field_empty.cluster` | legacy 字段 cluster 为空的节点 | 0（0.0%） |  |
| `math.field_empty.core` | legacy 字段 core 为空的节点 | 0（0.0%） |  |
| `math.field_empty.prerequisites` | legacy 字段 prerequisites 为空的节点 | 4（0.4%） | 样例：K-FUN-0910；P-FUN-0912；P-FUN-0914；P-FUN-0915 |
| `math.field_empty.abilities` | legacy 字段 abilities 为空的节点 | 0（0.0%） |  |
| `math.field_empty.evidence` | legacy 字段 evidence 为空的节点 | 0（0.0%） |  |

### 前置边与图结构

| 键 | 项 | 值 | 样例 / 说明 |
|---|---|---|---|
| `math.prereq.total` | 前置条目总数 | 1198 |  |
| `math.prereq.id_edges` | 可解析为节点 ID 的前置边 | 1041 |  |
| `math.prereq.text` | 自然语言前置（不能自动连边） | 156 | 样例：一元一次方程；一元一次方程中等式的基本性质（七年级上册）；一元一次方程应用题中设未知数的方法（七年级上册）；一元一次方程的定义；一元一次方程的概念（七上）；一元一次方程的概念（七年级上册）；一元一次方程的解法（七上）；一次方程建模；七年级平移与轴对称经验；七年级数据的收集与整理　按默认只列出，不做候选映射（spec 待人确认 #6） |
| `math.prereq.dangling` | 形似 ID 但不存在的前置（悬空边） | 1 | 样例：S-SIM-0925 ← P-SIM-0920 |
| `math.prereq.strength` | 前置是否区分强弱 | 否（字段只有 ID 或文字列表） |  |
| `math.graph.cycles` | 环（强连通分量，样例列出环内节点） | 1 | 样例：K-PRF-1207、K-PRF-1211 |
| `math.graph.transitive` | 传递边（可由其他路径推出的直连前置） | 74 | 样例：K-APP-0804 → S-APP-0808；K-APP-0811 → M-APP-0815；K-APP-0819 → S-APP-0823；K-APP-0831 → M-APP-0837；K-EQU-0913 → S-EQU-0917；K-EQU-0913 → S-FUN-0941；K-FRAC-1005 → S-FRAC-1007；K-FRAC-1013 → S-FRAC-1015；K-PAR-0901 → S-PAR-0903；K-PAR-0911 → P-PAR-0915 |
| `math.graph.orphans` | 孤点（无可解析边、也没有题目引用） | 4 | 样例：P-SIM-0912；S-EQU-0907；S-EQU-0955；S-FUN-0911 |

### 题库

| 键 | 项 | 值 | 样例 / 说明 |
|---|---|---|---|
| `math.items` | 题目数（按 ID 去重） | 721 |  |
| `math.items.topic` | 其中属于专题的题目 | 693 |  |
| `math.items.sprint` | 其中压轴专项题（不属于任何专题） | 28 |  |
| `math.items.duplicate_ids` | 重复定义的题目 ID | 0 |  |
| `math.items.by_usage` | 按用途计数（D 诊断 / P 探针 / L 训练 / T 迁移 / R 复测） | D 419，L 28，P 29，R 126，T 119 |  |
| `math.items.by_format` | 按作答形式计数 | fill 338，multi 14，single 369 |  |
| `math.items.node_refs` | 题目引用节点次数（主 / 辅 / 前置） | 2421 |  |
| `math.items.node_refs_unresolved` | 引用了不存在节点的次数 | 97 | 样例：Q-APP-U01-D-001 → 数据的收集与整理；Q-APP-U01-D-003 → 频率与频数；Q-APP-U02-D-001 → 数据的集中趋势（平均数、中位数、众数）；Q-APP-U02-D-002 → 加权平均数；Q-APP-U04-D-001 → 等可能条件下的概率；Q-APP-U05-D-001 → 用频率估计概率；Q-APP-U06-D-001 → 等可能条件下的概率；Q-APP-U06-D-002 → 对照实验思想；Q-CIR-U02-D-001 → 勾股定理；Q-CIR-U02-R-001 → 勾股定理 |
| `math.items.difficulty` | 带八维难度向量的题目 | 721/721 | KGE 建议改为各因子分级后加权求和作为先验难度 |
| `math.nodes.main_zero` | 没有任何题目以它为主考点的节点 | 507（52.9%） |  |
| `math.nodes.main_lt3` | 以它为主考点的题目少于 3 道的节点（粒度“可独立诊断”初筛信号） | 887（92.5%） | 只作信号，不判断粒度是否合格（spec 待人确认 #2） |

### 六段标识、版权与审核状态

| 键 | 项 | 值 | 样例 / 说明 |
|---|---|---|---|
| `math.item.six.region` | 题目字段 region | 0/721 |  |
| `math.item.six.exam_year` | 题目字段 exam_year | 0/721 |  |
| `math.item.six.grade` | 题目字段 grade | 0/721 |  |
| `math.item.six.textbook_version` | 题目字段 textbook_version | 0/721 |  |
| `math.item.six.subject` | 题目字段 subject | 0/721 |  |
| `math.item.six.content_version` | 题目字段 content_version | 0/721 |  |
| `math.item.six.derivable` | 可从所属专题的章节标签推出年级与教材版本的题目 | 693/721 | 章节标签形如“苏科版九上 · 第1章”；压轴专项题不属于任何专题 |
| `math.item.field.license` | 题目字段 license（版权 / 来源） | 0/721 |  |
| `math.item.field.copyright` | 题目字段 copyright（版权 / 来源） | 0/721 |  |
| `math.item.field.source` | 题目字段 source（版权 / 来源） | 0/721 |  |
| `math.item.field.sourceKind` | 题目字段 sourceKind（版权 / 来源） | 0/721 |  |
| `math.item.review_status` | 审核状态分布 | Q1 721 |  |

### 九年级“函数”主题（封测切片起点）

| 键 | 项 | 值 | 样例 / 说明 |
|---|---|---|---|
| `math.g9fn.topics` | 九年级且属于“函数”板块的专题 | fun 二次函数（苏科版九下 · 第5章） |  |
| `math.g9fn.nodes` | 节点数 | 45 |  |
| `math.g9fn.items` | 题目数 | 24 |  |
| `math.g9fn.main_lt3` | 主考点题目少于 3 道的节点 | 39/45 | 样例：C-FUN-0909；C-FUN-0948；C-FUN-0956；C-FUN-GEO；C-FUN-OPT；C-FUN-RNG；K-FUN-0901；K-FUN-0902；K-FUN-0903；K-FUN-0904 |
| `math.fn_all.topics` | “函数”板块全部专题（七至九年级） | fun 二次函数（苏科版九下 · 第5章）；lin 一次函数（苏科版八上 · 第6章）；inv 反比例函数（苏科版八下 · 第11章） |  |

### 错因：十二类根因与陷阱码

| 键 | 项 | 值 | 样例 / 说明 |
|---|---|---|---|
| `math.root_causes` | 根因类数 | 12 |  |
| `math.traps` | 陷阱码数 | 429 |  |
| `math.traps.bad_root` | 根因不在十二类里的陷阱码 | 0 |  |
| `math.traps.used` | 被题目引用的陷阱码 | 398/429（92.8%） |  |
| `math.traps.unknown_refs` | 题目引用了不存在的陷阱码 | 0 |  |
| `math.pitfall_nodes` | 易错节点（KGE pitfall 类型） | 0 | legacy 节点类型里没有易错节点；易错信息只在陷阱码上 |

### 100 能力点（能力视图 skill_node 的来源）

| 键 | 项 | 值 | 样例 / 说明 |
|---|---|---|---|
| `math.grid.boards` | 板块数 × 能力维度数 | 10 × 10 = 100 |  |
| `math.grid.ability_codes` | 章节能力代码数（映射到能力维度） | 11 |  |
| `math.grid.standalone_data` | 是否有独立的 100 点数据 | 否（由两个常量相乘，节点与题目经能力代码映射） |  |
| `math.grid.cells_with_nodes` | 有节点挂靠的格子 | 68/100 |  |
| `math.grid.cells_with_items` | 有题目挂靠的格子 | 66/100 |  |
| `math.grid.empty` | 既无节点也无题目的格子 | 32 | 样例：三角形×几何直观；三角形×数据分析；三角形×空间观念；函数×几何直观；函数×数据分析；函数×空间观念；四边形×几何直观；四边形×探究创新；四边形×数学建模；四边形×数据分析 |
| `math.grid.unknown_codes` | 节点上不存在的能力代码 | 0 |  |

### 旧编码并存

| 键 | 项 | 值 | 样例 / 说明 |
|---|---|---|---|
| `math.codes.node_id_style` | 节点 ID 形如“类型-簇-编号”（如 K-FUN-0901） | 956/959 |  |
| `math.codes.kge_style` | 已是 KGE 命名“学科.领域.主题.节点”的节点 | 0/959 | 全部要进 id_migration 迁移表 |
| `math.codes.kind_vs_type` | 实际节点类型不在 lib/types.ts NodeKind 里 | C | NodeKind = K \| M \| P \| S |
| `math.codes.kind_vs_db` | 实际节点类型不在 db/schema.sql 的 kind 约束里 | C | 约束 = K, M, P, S（该表代码未使用） |
| `math.codes.node_vs_ability` | 与能力代码同名的节点 ID | 0 |  |
| `math.codes.levels` | “能力五层级”定义 | 未找到；最接近的是掌握五门槛 MASTERY_GATES | KGE：题目层用课标四级，五道门槛保留为掌握状态 |

## 四、语文 · 文砺

读过的源文件：`packages/shared/lib/domain/ability.ts`、`packages/shared/lib/domain/error-types.ts`、`packages/shared/lib/domain/reading-error-tags.ts`、`packages/shared/lib/domain/classical-catalog.ts`、`packages/shared/lib/domain/writing-syllabus.ts`、`packages/shared/lib/domain/reading-methods.ts`、`packages/shared/lib/prompts/essay-rubric.ts`、`data/catalog.json`、`data/corpus_candidates.json`、`content/reading/错因标签.json`、`supabase/migrations/*.sql`

### 能力坐标：28 点与旧 20 格

| 键 | 项 | 值 | 样例 / 说明 |
|---|---|---|---|
| `chn.grid.domains` | 能力域数 | 7 |  |
| `chn.grid.levels` | 等级数 | 4 |  |
| `chn.grid.cells` | 能力格数（域 × 级） | 28 |  |
| `chn.grid.db_enum_match` | 与迁移里的 domain_t / level_t 枚举一致 | 是 | domain_t = D1,D2,D3,D4,D5,D6,D7；level_t = L1,L2,L3,L4 |
| `chn.grid.legacy20` | 提到旧 20 格（5 × 4）的文件 | 0 | 只能查当前快照；库里是否有 20 格编码的数据见库模式 |
| `chn.ability_kp` | “能力 × 文本类型 × 题型”能力点数据 | 未找到 |  |

### 篇目与素材

| 键 | 项 | 值 | 样例 / 说明 |
|---|---|---|---|
| `chn.catalog.rows` | data/catalog.json 篇目数 | 237 | 字段：author、genre、grade、parent、section、seq、title、volume |
| `chn.corpus.rows` | data/corpus_candidates.json 篇目数 | 109 | 字段：author、genre、grade、raw、title、volume |
| `chn.classical.rows` | classical-catalog.ts 文言与诗词条目数 | 55 |  |
| `chn.catalog.overlap` | 三份清单按标题的交集（catalog∩corpus / catalog∩classical / corpus∩classical） | 106 / 40 / 33 |  |
| `chn.catalog.pages` | 篇目清单里的页码字段 | 无（三份清单都没有册-课-页） |  |
| `chn.must_recite.static` | 静态清单里标“必背”的字段 | 无 |  |
| `chn.must_recite.db` | 迁移里定义 must_recite 列的文件 | 1 | 样例：supabase/migrations/0003_knowledge_base.sql　85 篇必背只能在库里查，见库模式 |
| `chn.shici` | 120 实词清单 | 未找到清单（只有关键字命中，需人工核实） | 样例：data/chunks.json；data/notes_chunks.json；packages/shared/lib/domain/ability.ts；packages/shared/lib/domain/badges.ts；packages/shared/lib/domain/classical-reflow.test.ts；packages/shared/lib/domain/classical-reflow.ts；packages/shared/lib/domain/classical.test.ts；packages/shared/lib/domain/classical.ts；packages/shared/lib/domain/error-types.ts；packages/shared/lib/domain/reading-types.ts　查过：data、content、packages/shared/lib/domain、supabase |
| `chn.xuci` | 18 虚词清单 | 未找到清单（只有关键字命中，需人工核实） | 样例：data/chunks.json；data/corpus_candidates.json；data/notes_chunks.json；packages/shared/lib/domain/ability.ts；packages/shared/lib/domain/badges.ts；packages/shared/lib/domain/error-types.ts；packages/shared/lib/domain/reading-types.ts　查过：data、content、packages/shared/lib/domain、supabase |
| `chn.unit_intro` | 34 个单元导语阅读方法 | 未找到 | 查过：data、content、packages/shared/lib/domain、supabase |

### 写作单元与阅读方法

| 键 | 项 | 值 | 样例 / 说明 |
|---|---|---|---|
| `chn.writing.units` | 写作单元数 | 35 | 字段：code、engine、grade、line、method、order、semester、title |
| `chn.writing.pages` | 写作单元的页码字段 | 无 |  |
| `chn.reading_methods` | 阅读方法卡数（reading-methods.ts） | 6 |  |

### 判分依据与错因

| 键 | 项 | 值 | 样例 / 说明 |
|---|---|---|---|
| `chn.essay_rubric` | 作文五维量表 | 有（以 prompt 字符串形式存在） | 没有版本号，判分结果无法绑定量表版本 |
| `chn.reading_scoring_points` | 阅读采分点的静态资产 | 无，由模型生成 grade_focus | 样例：packages/shared/lib/domain/schemas.ts；packages/shared/lib/prompts/reading.ts |
| `chn.error_types` | 中文六类错因 | 知识性、审题性、定位性、思维性、表达规范性、策略时间性 |  |
| `chn.error_types.db_match` | 与迁移枚举 error_t 一致 | 是 |  |
| `chn.reading_tags` | 阅读错因标签数 / 大类数 | 15 / 6 |  |
| `chn.reading_tags.mapping` | 标签能映射回中文六类的比例 | 15/15（100.0%） |  |
| `chn.reading_tags.ts_vs_json` | TS 标签与 错因标签.json 的差异（仅 TS / 仅 JSON） | 0 / 0 | JSON 版本 1.0 |

## 五、道法 · 知行

读过的源文件：`packages/shared/lib/domain/daofa-knowledge-map.ts`、`packages/shared/lib/domain/daofa-question-bank.ts`、`apps/daofa/app/news/page.tsx`

### 大概念节点与页码

| 键 | 项 | 值 | 样例 / 说明 |
|---|---|---|---|
| `moral.domains` | 领域数（含读本） | 5 | 心理与道德 7；法律（宪法为核心） 8；国情与国策 8；责任与担当 7；习近平新时代中国特色社会主义思想·学生读本 6 |
| `moral.nodes` | 节点数 | 36 |  |
| `moral.nodes.textbook` | 其中统编教材节点 | 30 |  |
| `moral.nodes.reader` | 其中《学生读本》节点 | 6 |  |
| `moral.pages.present` | 统编节点有页码 | 30/30 |  |
| `moral.pages.parsable` | 页码能解析为页码区间 | 30/30 |  |
| `moral.pages.approx` | 源文件注明页码是近似值 | 是 |  |
| `moral.pages.pending` | 页码为“待核”的节点 | 6 | 样例：xr1；xr2；xr3；xr4；xr5；xr6 |
| `moral.pages.book` | 统编节点标了册别（七上…九下） | 30/30 |  |
| `moral.pages.edition_bound` | 绑定教材版次 / 印次的节点 | 0/36 | KGE：页码与原句一律进教材投影表 textbook_projection，按版次核对 |
| `moral.needs_review` | 标记 needs_review（待特级教师审校）的节点 | 6 | 样例：xr1；xr2；xr3；xr4；xr5；xr6 |
| `moral.phrases.empty` | 教材规范表述为空的节点 | 6 | 样例：xr1；xr2；xr3；xr4；xr5；xr6 |
| `moral.mount.questions` | 节点挂载：高频设问 | 无 |  |
| `moral.mount.answer_phrases` | 节点挂载：答题话术 | 无 |  |
| `moral.mount.exam_refs` | 节点挂载：真题回链 | 无 |  |
| `moral.edges` | 节点间关系（前置 / 关联） | 无 |  |

### 采分点 Rubric

| 键 | 项 | 值 | 样例 / 说明 |
|---|---|---|---|
| `moral.items` | 带 Rubric 的静态题目数 | 2 |  |
| `moral.points` | 采分点数 | 6 |  |
| `moral.points.point_id` | 采分点字段 point_id | 6/6 |  |
| `moral.points.score_value` | 采分点字段 score_value | 6/6 |  |
| `moral.points.subject_dimension` | 采分点字段 subject_dimension | 6/6 |  |
| `moral.points.core_keywords` | 采分点字段 core_keywords | 6/6 |  |
| `moral.points.required_logic` | 采分点字段 required_logic | 6/6 |  |
| `moral.points.material_linkage` | 采分点字段 material_linkage | 4/6 |  |
| `moral.points.partial_credit_rule` | 采分点字段 partial_credit_rule | 6/6 |  |
| `moral.points.common_errors` | 采分点字段 common_errors | 6/6 |  |
| `moral.rubric_version` | 带 rubric_version 的题目 | 0/2 |  |
| `moral.items.node_refs_unresolved` | 题目引用了不存在的节点 | 0 |  |
| `moral.nodes.unreferenced` | 没有任何静态题目引用的节点 | 32/36（88.9%） | 样例：l1；l2；l3；l4；l5；l7；l8；m1；m2；m3　节点间没有边，这里的“孤点”只看题目引用；AI 生成题在库表 daofa_item，见库模式 |

### 时政条目

| 键 | 项 | 值 | 样例 / 说明 |
|---|---|---|---|
| `moral.news` | 时政种子条目数（news 页常量 SEED_NEWS） | 2 |  |
| `moral.news.validity` | 时政条目有生效 / 失效字段 | 无 |  |
| `moral.news.stored` | 时政条目是否入库 | 否（写在页面常量里） |  |

### 《学生读本》

| 键 | 项 | 值 | 样例 / 说明 |
|---|---|---|---|
| `moral.reader.nodes` | 读本节点数 | 6 | 样例：xr1 以人民为中心的发展思想；xr2 全过程人民民主；xr3 全面依法治国·宪法法律至上；xr4 铸牢中华民族共同体意识；xr5 生态文明·建设美丽中国；xr6 推动构建人类命运共同体 |
| `moral.reader.pages` | 读本节点有可用页码 | 0/6 |  |
| `moral.reader.phrases` | 读本节点有规范表述 | 0/6 |  |
| `moral.reader.files` | 文件名含“读本”的文件（任意类型） | 1 | 样例：apps/daofa/docs/学生读本节点填充指南.md |
| `moral.reader.verdict` | 结论 | 未入库（只有脚手架节点，没有原文、页码与规范表述） |  |

## 六、库模式

没有提供库模式输出。以下各项在报告里写“待库模式”：语文 85 篇必背（`textbook_catalog.must_recite`）、库表 `knowledge_point` 与 `item_bank`、道法 AI 生成题（`daofa_item.rubric`）、阿鸦库 `knowledge_nodes` / `questions` 是否有数据、库里是否残留 20 格编码。

运行方式见 `tools/kge-inventory/db/readonly-inventory.sql` 文件头；输出用 `--db-output <库名>=<文件>` 并入。
