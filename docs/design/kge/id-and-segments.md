# KGE · 节点 ID 与六段标识规范 v0.9

状态：草稿，随迁移 `0011_kge__knowledge_map` 一起评审（`specs/kge-schema-v0.9/spec.md` 设计要点 5）。
依据：KGE V1.1 第三章（层级）、第五章（版本号、旧编码迁移）、第七章（统一词汇表、ID 命名）；完整方案 v1.1 5.5。

本文件里的节点 ID 正则、学科前缀对照表与迁移里的定义由 `docs/design/kge/check_node_id.py` 逐字比对，
正例与反例也由它逐条校验（`make kge-id-check`，已接入 `check-fast` 与 `check`）。改任何一边都要同步另一边。

## 1. 节点 ID

格式：`学科.领域[.主题[.节点[.细目]]]`。全大写；第一段是学科前缀，其余每段以字母或数字开头，段内只用大写字母、数字、下划线。

正则（与迁移中的 `kge.node_id` 域一致）：

<!-- node-id-regex:start -->
`^[A-Z][A-Z0-9]{1,9}(\.[A-Z0-9][A-Z0-9_]{0,31}){1,4}$`
<!-- node-id-regex:end -->

段数决定层级，上一级就是去掉最后一段的 ID（`kge.knowledge_node` 的约束 `node_level_depth`、`node_parent_is_prefix`）：

| 段数 | 层级 `level` | 例子 | 类型 `kind` |
|---|---|---|---|
| 2 | 领域 domain | `MATH.FUNC` | 不带 |
| 3 | 主题 / 单元 topic | `MATH.FUNC.QUAD` | 不带 |
| 4 | 知识节点 node | `MATH.FUNC.QUAD.VERTEX_FORM` | 七种之一 |
| 5 | 细目 detail | `MATH.FUNC.QUAD.VERTEX_FORM.SIGN` | 只能是易错点或考点（spec 待人确认 #7） |

<!-- node-id-examples:start -->
| ID | 是否合法 | 说明 |
|---|---|---|
| `MATH.FUNC.QUAD.VERTEX_FORM` | 合法 | KGE 第七章的例子 |
| `CHN.READ.NARR.CHARACTER` | 合法 | 语文 |
| `MORAL.LAW.CONST.SUPREME` | 合法 | 道法 |
| `MATH.FUNC` | 合法 | 领域 |
| `MATH.FUNC.QUAD.VERTEX_FORM.SIGN` | 合法 | 细目 |
| `MATH.GEOM.TRI.CONG_2D` | 合法 | 段内可以有数字与下划线 |
| `math.func.quad.vertex_form` | 不合法 | 必须大写 |
| `MATH` | 不合法 | 至少到领域 |
| `MATH.FUNC.QUAD.VERTEX_FORM.SIGN.EXTRA` | 不合法 | 最多五段 |
| `MATH.FUNC.QUAD.VERTEX-FORM` | 不合法 | 不能用连字符 |
| `MATH..QUAD` | 不合法 | 不能有空段 |
| `MATH.FUNC._PRIVATE` | 不合法 | 段不能以下划线开头 |
| `K-FUN-0901` | 不合法 | 阿鸦旧编码，走迁移表 |
| `m1` | 不合法 | 道法旧短编码，走迁移表 |
<!-- node-id-examples:end -->

ID 跨地图版本保持不变；表的主键是（地图版本, ID）。节点改名、拆分、合并时，旧 ID 登记进 `kge.id_migration`（见第 5 节）。

## 2. 学科前缀

与 `platform.subject` 的对应（迁移中的 `kge.subject_prefix`）。节点 ID 的第一段必须等于所在地图版本学科的前缀（触发器 `guard_node`）。

<!-- subject-prefix:start -->
| platform.subject | 前缀 | 状态 |
|---|---|---|
| aya | MATH | 数学 |
| wenli | CHN | 语文 |
| zhixing | MORAL | 道德与法治 |
| echo | ENG | 英语，搁置（对照文档 #1） |
<!-- subject-prefix:end -->

预留前缀：`HIST`（历史）、`PHY`（物理）、`CHEM`（化学）。这三科在 `platform.subject` 增加取值时，一起补进上表与 `kge.subject_prefix`（属于契约与迁移，必停项）。

**错因学科码**另起一套，不是节点 ID。同一个“定位”在两科写法不同（KGE 第七章）：

- 语文：`CHN.LOCATE_TEXT`（在文中定位）；
- 道法：`MORAL.LOCATE_BOOK`（在教材中定位）。

错因大类只能取 `platform.cross_subject_error` 的七大类；学科码 v0.9 不进库，由各科 Rubric 与错因映射提供。

## 3. 六段标识

六段是：地区 `region`、考试年份 `exam_year`、年级 `grade`、教材版本 `textbook_version`、学科 `subject`、内容版本 `content_version`。

- 六段**只存在** `platform.curriculum_version`（内容登记 0003），唯一键就是这六段；
- 题目（`platform.item` / `kge.item_tag`）、采分点、页面都经内容版本继承，不在自己身上重复存；
- 内容版本 `content_version` 与地图版本 `kge.map_version` 是两回事：
  - 内容版本是内容包发布的版本；
  - 地图版本是骨架版本；
  - 一个内容版本引用一个地图版本：`platform.curriculum_version.map_version_id`（0011 新增，可空，按（学科, 地图版本）引用，保证同学科）。
- 教材投影的版次 `kge.textbook_projection.edition` 与 `textbook_version` 用同一套编码（如 `bubian-2024`）。

## 4. 版本号

地图版本号是“主.次.修订”（`kge.map_version.version`，同一学科不重复）：

| 位 | 节奏 | 典型变化 |
|---|---|---|
| 修订 | 按周 | 投影更正、边界补充、弱边增删 |
| 次版本 | 按月 | 节点增删、拆分、合并、前置边调整（需两人确认的变更单） |
| 主版本 | 按学期 | 课标或教材换版、领域重组 |

- 尽量避开学期中发主版本，避免学生的星图在复习期大面积变化。
- 同一学科同一时刻只有一个已发布版本。发布只能经 `kge.publish_map_version`：
  - 先跑 `kge.validate_map_version` 的七项检查，有阻断问题就拒绝；
  - 原已发布版本自动转为撤下。
- 已发布或已撤下版本的骨架不能再改（节点、边、投影、能力视图挂接、旧编码迁移），改地图就发新版本。
- 原子（题目标签、题目族、采分点、时政）不随骨架冻结。

## 5. 旧编码迁移规则

`kge.id_migration(map_version_id, subject, old_id, new_id, rule, change_id)`。`rule` 取以下值：

- `rename`：改名；
- `split`：拆分，一对多；
- `merge`：合并，多对一；
- `retire`：撤销，没有新 ID；
- `manual`：人工映射。

| 来源 | 例子 | 规则 |
|---|---|---|
| 阿鸦知识图谱节点 | `K-FUN-0901`、`K-QF-0301` | KGE-01 导入时逐个登记：结构能对上的用 `rename`；拆分、合并按变更单登记 |
| 阿鸦的自然语言前置 | KGE-00 报告：156 条 | **不自动映射**，列入 KGE-01 的人工映射清单，映射后登记为 `manual` |
| 道法大概念的短编码 | `m1`、`xr1` | 进迁移表，规则同上 |
| 语文 | —— | KGE-00 报告：没有可打标的能力点，从零建节点，没有旧编码 |

- 无法可靠迁移的旧编码登记为 `retire`。对应学生的掌握度由学习服务标为“待复测”，不直接搬过去（KGE 第五章）。这一步属于个人层，在 KGE-01 / 冲刺 1-C 实现，不在 0011。
- 迁移表属于骨架，随地图版本冻结。

## 6. 取舍记录

- KGE 第八章的节点表写的是 `domain_id / topic_id`。本仓库用 `parent_id` 加“段数 = 层级”，领域与主题由 ID 前缀直接得到，不另存两列。好处是少一处可能不一致的地方；代价是改领域或主题的 ID 会连带改下级 ID，这类变化按次版本走迁移表。
- 课标条目 `kge.curriculum_item` 已建。节点与课标条目的对齐关系，以及 `aligned_to` 边是否指向课标条目，留给 KGE-01 决定；0011 的 `aligned_to` 只连节点。
