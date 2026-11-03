# 编纂规范 · 数学 v1（结构骨架与机器可检规则）

状态：**骨架草稿**（KGE-03 PR ①，`specs/kge-03-compile-layer/spec.md` 设计要点 2、待人确认 #2）。
本文只写页面的**结构骨架**与**机器可检规则**（由 `services/knowledge` 的程序体检执行；第 9 节所列语义层检查是 KGE-11 的
规则式草稿，由 `knowledge.semantic` 执行，阻断 / 提示分级待教研签发）；**教学内容要求由教研填写并签发**（第 10 节），
签发前本规范不作为页面发布依据。依据：KGE V1.1 第四章（页面体系、三种段落、两种重建、分级发布闸门）、第五章 V1.1 修订、第七章、第九章。

每条机器规则后面的 `check_type` 是体检结果里的检查类型（字段对应 KGE 第七章 `lint_issue`）；“阻断”即不能合并。

## 1. 适用范围
- 学科：数学（`subject: aya`，节点 ID 前缀 `MATH`）。首期只做**知识点页**（`page_type: kp`，挂在 `kp` / `ability_kp` 类型的节点上）。
- 其余七类页面（观点页、素材卡、方法页、易错页、单元综述、考情页、版本差异页）只预留类型名，不实现；出现即阻断（`header`）。
- 页面仓库试点期是单独的 Git 仓库（ADR 12 草稿）；模板见 `tools/kge-pages-template/`。

## 2. 文件与头部字段
- 文件位置：`pages/kp/<节点 ID>.md`；**页面 ID = 节点 ID**（KGE 第二章）。只用 LF 换行。（`header`、`format`）
- 头部是 YAML，键顺序固定如下；用编译器序列化出的写法为规范写法，非规范写法报警告（`format`）。

| 字段 | 取值 | 机器规则（`header` / `node_id`） |
|---|---|---|
| `page_id` | 节点 ID | 等于 `node_id` |
| `page_type` | `kp` | 首期只收 `kp` |
| `subject` | 平台学科编码 | 与节点 ID 前缀一致（`aya` ↔ `MATH`） |
| `node_id` | 节点 ID | 合 `docs/design/kge/id-and-segments.md` 的正则；存在于指定的已发布地图快照（`node_id`） |
| `map_version` | 主.次.修订 | 等于体检所用地图快照的版本（`node_id`） |
| `schema_version` | `1` | 本规范对应的页面格式版本 |
| `page_version` | semver | 低风险升修订号，高风险升次版本号，全量重编升主版本号（编译器维护） |
| `risk_level` | `low` / `medium` / `high` | 全量重编必须是 `high` |
| `rebuild_kind` | `render` / `full` | 最近一次重建的种类 |
| `locked_sections` | `[definition, curriculum]` | 与第 3 节的锁定段一致 |
| `atom_refs` | `{type, id, hash}` 列表 | 非空；类型见第 4 节；`hash` 为 `sha256:` + 16 位十六进制；不重复 |

## 3. 段落
正文只有一个一级标题（`# 节点名称`，与地图一致），其余内容全部在带固定标记的段落里，顺序固定（`sections`）：

```
<!-- kge:section id=<段落 ID> kind=<locked|render|narrative> -->
## <段落标题>
...
<!-- kge:end id=<段落 ID> -->
```

| 顺序 | 段落 ID | 种类 | 标题 | 由谁写 | 机器规则 |
|---|---|---|---|---|---|
| 1 | `definition` | 锁定段 | 定义 | 编译器从节点原子生成 | 与地图快照渲染的模板逐字一致（`template`）；与上一审核版一致，除非所引原子哈希变了（`locked_baseline`） |
| 2 | `curriculum` | 锁定段 | 课标要求 | 编译器从课标条目生成 | 同上 |
| 3 | `relations` | 渲染段 | 前置与后续 | 编译器从前置边生成 | 与模板逐字一致（`template`） |
| 4 | `textbook` | 渲染段 | 教材位置 | 编译器从教材投影生成 | 同上；**不写教材原句**，只放占位 `{{quote:<投影 ID>}}`（`textbook_quote`） |
| 5 | `region` | 渲染段 | 地区权重 | 编译器从地区投影生成 | 同上 |
| 6 | `key_points` | 叙述段 | 讲解要点 | 模型起草、教研审核 | 第 4–8 节 |
| 7 | `pitfalls` | 叙述段 | 易错 | 同上 | 同上 |
| 8 | `frequent_questions` | 叙述段 | 高频设问 | 同上 | 同上 |

段落标记之外不得有内容（`sections`）。渲染重建逐字保留已审核的叙述段；全量重编重写叙述段，一律高风险。

## 4. 引用格式
- 原子引用：`[@<类型>:<ID>]`，写在自然段末尾。类型与 ID：

| 类型 | ID 写法 | 来源 |
|---|---|---|
| `node` | 节点 ID | `kge.knowledge_node` |
| `edge` | `<from_id>><类型>><to_id>` | `kge.knowledge_edge`（`prerequisite`：from 是 to 的前置） |
| `curriculum` | `<课标年份>/<条目编码>` | `kge.curriculum_item` |
| `projection` | 投影 ID | `kge.textbook_projection` |
| `region` | 投影 ID | `kge.region_projection` |
| `affair` | 条目 ID | `kge.current_affair`（带有效期；数学首期没有） |

- 页面链接：`[[<页面 ID>]]`；不用相对路径链接，外部链接报警告（`link`）。
- 引用完整率 100%：每个自然段（空行分隔；标题行与独立公式块除外）至少一个原子引用（`citation`）。
- 引用必须登记在头部 `atom_refs`，且原子存在于地图快照（`link`）；登记的哈希必须等于快照里的哈希，否则需要重编（`atom_hash`）。
- 带有效期的原子过期或下架即阻断（`atom_expired`）。
- 页面必须挂在现行节点上：节点撤下、节点类型挂不了知识点页、同一节点挂了两个页面都算孤立页（`orphan`）。

## 5. 公式写法
- 行内公式 `$...$`；独立公式块用单独一行的 `$$` 开合，块内一行一个式子。
- 需要验算的等式在行末加 `% check`：按 `=` 切成等式链，相邻两项必须恒等（SymPy）；
  `% check x=2, y=-1` 表示代入取值后链上各项相等（验解）。标了 `% check` 却验算不通过、无法解析或含不等号，一律阻断（`sympy`）。
- 未标记的式子解析失败只报警告（`latex`）。验算支持的 LaTeX 子集：`\frac`、`\sqrt`（含根指数）、`^`、下标变量（1–3 位）、
  `\cdot`、`\times`、`\div`、括号与 `\left` / `\right`、`\pi`；数字不超过 6 位，指数不超过 64。
- 待教研填写并签发：哪些式子必须标 `% check`（第 10 节）。

## 6. 适龄文风的可测项
- 句长上限：叙述段每句不超过 **60 字**（公式记 1 字，引用不计；数值待教研签发时确认）（`sentence_length`）。
- 禁用表述：禁语 A–E 类引用 `evals/persona/diandian/lexicon/forbidden.yaml`；数学 F 类引用 `docs/personas/阿鸦-规格书-V1.1.md` §5.2；
  G 类引用 `docs/personas/考官型基线与角色卡-方圆春秋雅典娜-V1.1.md` §5.2。只读抽取，不在本规范另写一份（`forbidden_phrase`）。
- 人设中立（KGE V1.1 修订“页面人设中立”）：页面一律中性教研口吻，不写任何角色的名字、称呼、口头禅与固定句式、设定元素；
  词表从 `docs/personas/` 只读抽取（`persona`）。页面仓库里用导出的 `lexicon/lexicon.json`。

## 7. 个人信息与群体统计（规则 13）
- 页面只放公共层内容。疑似个人信息与学生标识一律阻断：手机号、身份证号、学号式编号、UUID、电子邮箱、姓名 + 学校组合（`pii`）。
- 引用群体统计（正确率、得分率、平均分、带“学生 / 考生 / 作答”的百分比等）必须写明人数，且不低于门槛 **30**
  （spec 待人确认 #6；试点期数学页面暂不引用群体统计）（`group_stats`）。

## 8. 风险分级与发布（KGE 第四章分级发布闸门）
| 风险 | 改动类型 | 发布 |
|---|---|---|
| 低 | 地区权重等考情数值、后续节点、只有原子哈希更新 | 程序体检通过后进每日合并窗口，每页每天最多发布一次，事后抽检 |
| 中 | 叙述段改写（阶段 0 没有语义体检，按高风险处理；KGE-11 的规则式语义体检是草稿、分级待教研签发，分流建议未生效，仍按高风险） | 单独合并请求，两人审核 |
| 高 | 定义与课标要求（锁定段）、前置关系表述、教材投影（页码、版次、原句）、全量重编 | 单独合并请求，必须教研审核，两人确认（审核类别待 ADR 12 #2） |

## 9. 体检结果
- 结构化 JSON（`python -m knowledge.lint <页面目录> --json`，格式 `kge-lint-report/1.1`），每条字段：`id`、`page_id`、`page_version`、
  `layer`（`program`）、`check_type`、`severity`（`block` / `warn`）、`detail`（说明与定位：文件、行、段落）、`status`。
  报告顶层另有 `layer`、`map_version`、`scope`（页面 ID、版本、原文摘要）、`inputs`（输入摘要）、`config`（被跳过的检查等），`summary` 不变。
- 检查类型：`format`、`header`、`node_id`、`sections`、`template`、`locked_baseline`、`link`、`orphan`、`citation`、`atom_hash`、
  `atom_expired`、`latex`、`sympy`、`pii`、`group_stats`、`persona`、`forbidden_phrase`、`sentence_length`、`textbook_quote`、`snapshot`、
  `atom_conflict`、`prerequisite_direction`、`term_consistency`、`edition_scope`、`out_of_scope`、`age_style`、`persona_mix`、`affected_scope`。
- 后八类属语义层（`layer` 为 `semantic`，KGE-11 规则式语义体检，`python -m knowledge.semantic`，见
  services/knowledge/knowledge/semantic/README.md）：程序体检先跑，语义体检随后；报告格式相同。阻断 / 提示分级待教研确认。

## 10. 教学内容要求（待教研填写并签发）
### 10.1 讲解要点怎么写
待教研填写并签发。

### 10.2 易错怎么写（错因表述、纠正思路）
待教研填写并签发。

### 10.3 高频设问怎么选、怎么写
待教研填写并签发。

### 10.4 例子与类比的选取
待教研填写并签发。

### 10.5 必须标 `% check` 的式子范围
待教研填写并签发。

### 10.6 用语表（术语统一、各版本教材用词差异）
待教研填写并签发。

### 10.7 超纲与范围属性的写法
待教研填写并签发。

## 11. 签发记录
| 版本 | 日期 | 签发人 | 说明 |
|---|---|---|---|
| — | — | — | 未签发 |
