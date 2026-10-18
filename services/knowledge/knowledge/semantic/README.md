# 规则式语义体检（KGE-11 第一步）

依据：specs/kge11-semantic-lint/spec.md；KGE V1.1“体检两层”“事件驱动自动编入”“风险分级自动分流”。

- 确定性、不调模型、不连库、零新依赖。只看叙述段：锁定段和渲染段由程序体检的模板检查逐字核对。
- 顺序：先跑程序体检（`python -m knowledge.lint`，KGE-03），再跑语义体检。
- 这是规则式草稿，分级待教研签发。
- 消息只写定位和结论。可以回显的只有节点 ID、原子键、快照里的值和词表内容，页面原文不回显（规则 13）。

## 报告格式 kge-lint-report/1.1（两层共用）

- 顶层字段：
  - `layer`：`program` 或 `semantic`。
  - `map_version`：所依据的地图版本。
  - `scope`：检查的页面，每页有 `page_id`、`page_version` 和原文 `sha256`；语义层另有 `affected`、`missing`、`pairs`。
  - `inputs`：快照、人设词表、上一审核版、超纲 / 文风 / 术语词表的 SHA-256 摘要。
  - `config`：被跳过的检查 `skipped_checks`；语义层另有 `levels_default`、`levels_overrides`，以及各词表的 `synthetic_inputs`。
  - `summary`、`issues` 与 /1 相同。
- 条目的 `layer` 必须与报告一致，条目 `id` 与 /1 相同。
- 检查类型并入 `knowledge/lint/issues.py` 的枚举（代码内的枚举，不是契约）。
- 读取方：
  - M1 接受 /1 和 /1.1，只统计程序体检报告。语义报告、或带语义条目的报告拒收。
  - 页面仓库 CI（`ci/lint.sh`）只读 `summary`，不受影响（有测试）。

## 检查项与缺省分级（全部待教研确认，见 config.py）

| 检查类型 | 规则键 | 缺省 | 判定 |
|---|---|---|---|
| `atom_conflict` | `.formula` | 阻断 | 叙述段的等式与所引节点原子（includes、examples 里的 `$…$`）对照：左边经 SymPy 等价、右边不等价即报。两个方向都比，复用 `lint/mathcheck.verify_chain` |
| | `.region_number` | 阻断 | 引用 `[@region:…]` 的自然段：权重、占比（%）、考试年份必须与原子一致 |
| | `.curriculum_level` | 阻断 | 引用 `[@curriculum:…]` 的自然段：写的要求层次必须等于条目的 verb_level |
| | `.page_range` | 阻断 | 引用 `[@projection:…]` 的自然段：页码必须落在投影的页码范围内 |
| | `.excluded_scope` | 阻断 | 同一句里出现节点的“不包括”项、肯定说法（属于、包括……），又提到本节点，并且没有否定词 |
| `prerequisite_direction` | `.reversed` / `.pages_disagree` / `.no_edge` | 阻断 / 阻断 / 提示 | 前置表述（四种句式；节点按名称、别名、`[[ID]]`、“本节”识别）：与骨架边相反；骨架无边而两张相邻页说法相反；骨架无边 |
| `term_consistency` | `.variant` | 提示 | 相邻页对同一节点用了不同写法（节点名称或别名，按最长匹配；重名的写法不参与比对） |
| `edition_scope` | `.unmarked` / `.mismatch` | 阻断 | 版次专属表述或教材页码出现在没引该版次投影的段落；或本段引了投影、表述却属于另一版次 |
| `out_of_scope` | `.term` | 阻断 | 超纲词表里学段不在本学科适用学段的术语，节点可在 node_allow 里放行 |
| | `.no_vocab` / `.synthetic_vocab` | 提示 | 没给词表时跳过并提示；词表是合成样例时也提示 |
| `age_style` | `.<规则 ID>` / `.default`、`.no_lexicon` | 提示 | 文风词表的逐条规则（行内公式先替换掉）；没给词表时跳过并提示 |
| `persona_mix` | `.normalized` | 阻断 | 自然段 NFKC 归一化、去掉空白标点后比人设词表（角色名、称呼、口头禅、设定元素）；只报程序层逐行比对没比上的 |
| `affected_scope` | `.missing` | 阻断 | 受影响页不在页面目录里（编入清单与页面目录对不上，不静默忽略） |

分级可以用 `--levels`（`kge-semantic-levels/1`）覆盖。出现未登记的键或非法级别时拒收；覆盖项写进报告 `config`。

### 已知限制

- 版次专属表述：快照里少于两个版次时，不从投影推断，只用术语表的 `edition_terms`。与节点名称、别名重合（相等或互为子串）的词一律剔除。段落标版次的唯一方式是“本段引用了该版次的教材投影”。
- 课标层次只认四个词：知道、理解、运用、综合（与 `atoms.VERB_LEVELS` 相同）。“了解”“掌握”等近义说法不识别（漏报，不误报），正式词表待教研给。
- 地图快照里没有采分点原子，“与采分点对照”这次没做。
- 适龄文风只有词表规则；句长仍由程序体检检查。
- 角色语言混入不对禁语正则做归一化比对：禁语正则含标点，归一化后会误报。

## 比对范围

- 受影响页来自编入清单（`--affected`，清单的 `to_map_version` 必须等于 `--map-snapshot` 的版本）或 `--affected-pages`。两者都不给时是每日全量，每页都算受影响。
- 骨架相邻页包括：前置（双向）、易混（双向）、同主题。同主题指节点 ID 前三段相同（学科.领域.单元），不看 parent_id。
- 逐页检查只查受影响页及其相邻页。
- 跨页比对只比“受影响页 × 它的相邻页”，不做全量两两。

## 输入文件（仓库里只有合成样例：services/knowledge/tests/fixtures/semantic/）

- 超纲词表 `kge-semantic-scope/1`：`subjects.<学科>.{stages, terms[{term, stage}], node_allow}`
- 文风词表 `kge-semantic-style/1`：`rules[{id, kind: literal|regex, pattern, message}]`
- 术语表 `kge-semantic-terms/1`：`aliases{节点 ID: [别名]}`、`edition_terms{版次: [表述]}`

三种文件都必须用 `synthetic` 标明是不是合成样例。字段不对、重复或越界时一律拒收。正式词表由教研提供。

## 命令行

```
python -m knowledge.semantic <页面目录> --map-snapshot 快照 [--affected 编入清单 | --affected-pages ID,...] \
  [--scope-vocab 超纲词表] [--style-lexicon 文风词表] [--terms 术语表] [--levels 分级配置] [--json]
python -m knowledge.compiler plan-events --pages 页面目录 \
  [--changes 原子变更清单 --old-snapshot 旧快照 --new-snapshot 新快照 [--allow-partial]] \
  [--signals 兜底检索信号.jsonl [--signal-min-group 20]] [--out 编入清单]
```

语义体检的退出码：0 表示没有阻断，1 表示有阻断，2 表示参数或输入错误。

编入清单（`knowledge/compiler/events.py`）：

- 原子变更清单是 `kge-atom-changes/1`。缺省必须列全两个快照的差异，漏列即退出码 2；加 `--allow-partial` 后 `summary.unlisted` 报漏列数。
- 每条与快照差异、地图版本核对。来源类型与原子类型交叉校验：考试说明 → region / curriculum；真题、审核通过的上传题 → region；确认的易错模式 → node / edge；课标 → curriculum / node；教材 → projection；时政 → affair；地图编辑 → node / edge。
- 快照里还没有题目、采分点、易错模式原子，所以真题、上传题、易错模式目前只是来源标签。
- 编入复用 `plan_jobs`：列全时结果与 `plan_jobs(页面, 旧快照, 新快照)` 逐条相同。
- 兜底检索信号：命中键只收 `page:<ID>@<版本>#<段落>` 或 `atom:<原子键>`。按（节点，原因）聚合后，次数与不同问法数都 ≥ 门槛（缺省 20，待人确认）才列出，低于门槛只计入 `summary.signals_below_threshold`。
- `--out` 用临时文件加 `os.replace` 原子写，拒绝指向输入文件或页面目录内。

## 审核台分流（services/learning/review_console/risk.py，尚未生效）

`suggest_review_level(level, program_report, semantic_report, *, page_id, page_version, map_version, structural=False, enabled=False)`：

- 开关关（缺省）时，结果等于 `review_level(level)`。
- 开关开时：
  - 结构性变更一律 high。
  - 中风险需要同时满足以下条件才返回 `medium_spot_check`：
    - 两份 /1.1 报告的顶层 layer 分别为 program、semantic；
    - 都覆盖被审页面的这一版本，地图版本一致，快照摘要相同；
    - 没有跳过的检查、没有词表缺失提示；
    - 分级全是缺省，超纲与文风词表都不是合成样例；
    - 都零阻断。
  - 中风险其余情形一律 high；低、高风险不变。
- `risk.SUGGESTED_REVIEWERS` 写了建议级别与 `compiler/window.py` 的 `REVIEWERS`、`review_console/pages.py` 的 `REQUIRED` 的对应关系。两处常量都还没有 medium_spot_check，中风险仍两人（测试核对）。

## 测试与变异

- 测试：`tests/test_semantic.py`、`tests/test_semantic_events.py`，以及审核台的 `services/learning/tests/test_review_triage.py`。
- 变异：`tests/semantic_mutations.py`。

## 待人确认

1. 规则式语义体检能否作为中风险降为抽检的依据（必停：改审核级别）。开关保持关。
2. 超纲词表、文风词表、术语别名、课标层次近义词，以及阻断 / 提示分级，由教研提供。
3. 叙述段改写判中还是判高：编译器 `rebuild.classify` 判高，审核台 `classify_page` 判中，两边测试各自固定了现状。
4. `compile_job`、`lint_issue` 入库与调度接入另起（迁移，必停）。
5. 采分点、题目、易错模式是否进地图快照，以便语义体检对照、编入按真实原子计。
6. 兜底信号的最小门槛取值（缺省 20），以及按问法摘要计数能否代表人数。
7. kge-lint-report/1.1 的绑定字段是否就是 lint_issue 入库时的版本字段（迁移另起）。
