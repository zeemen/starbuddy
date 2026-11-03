# 执行手册：在试点环境导出伪名化掌握状态、作答与错因，并运行地图校准（只读，本仓不执行）

对应 `specs/kge09-map-calibration/spec.md` 范围 1 的“只读 SQL 说明”与待人确认 3，以及 `spec-step2.md` 范围 1 的导出说明（第 6 节）。
**本仓 CI 不执行本手册，也没有连接任何数据库的代码**；本仓只有合成管线（`make kge-calibrate-test`，样例见 `sample/`，标“合成，不是试点结论”）。

> **在下面两件事都完成之前，不得做任何真实导出：**
> 1. 只读导出函数（草案 [`sql/export-function.draft.sql`](sql/export-function.draft.sql)）已由另起的 [NEEDS-HUMAN] PR 落为迁移、附 RLS 测试、合并并经人执行；
> 2. 工具的 synthetic 守卫已由另起任务、经人确认放开（当前版本只接受 `synthetic=true` 且伪名为 `syn-` 开头的掌握状态表）。
>
> 在那之前，流程演练**只用合成数据**：`python -m kge_calibrate synth --out <空目录>` 生成输入，再 `run`。

## 0. 前置条件（缺一不可，任何一项不满足就停止）

| # | 条件 | 负责人 | 记录 |
|---|---|---|---|
| 1 | 试点学生的监护人同意已签署，用途包含“学习效果评估与内容改进”（`platform.consent_record` 的 `pilot_participation`） | 合规 / 产品 | 授权记录编号 |
| 2 | 试点环境在境内、已完成部署评审；只连试点库，**不连生产**（规则 9） | 平台负责人 | 环境名 |
| 3 | `config.json` 的口径与阈值已由教研确认（现在每一项都是“待教研确认”的占位值），确认记录写进 `signoff` | 教研负责人 | 配置指纹 |
| 4 | 导出函数已按上文第 1 件事合并执行；执行 EXECUTE 只授给专用只读导出角色 | DBA + 平台负责人 | PR 编号 |
| 5 | 工具的 synthetic 守卫已按上文第 2 件事放开 | 平台负责人 | PR 编号 |
| 6 | 执行机是试点环境内的受控设备；导出文件与输出目录不在任何同步盘或仓库里 | 执行人 | 目录 |
| 7 | 两人在场：一人执行，一人核对输出只含聚合数（第 4 节） | 教研负责人指定 | 姓名不写进任何产物 |

## 1. 读取方式与口径

个人层表（`platform.mastery_estimate` 等）都是 `FORCE ROW LEVEL SECURITY`。导出只调用导出函数（`SECURITY DEFINER`、只读），
它在库内按授权名单过滤、选星亮层次、生成伪名，只返回 伪名 × 节点 × 门槛状态 × 掌握概率。`BYPASSRLS` 角色不用于本导出。

- **掌握口径**：与学习服务 `services/learning/learning/mastery/gates.py` 的 `star_basis_level` / `star_lit` 同口径、不另立。
  调用时传该学科掌握配置 `services/learning/config/mastery/<学科>.json` 里 `star_level_rule` 的原文：
  `star_level` 模式取配置给节点的层次（未配置取最高层次），`all_levels` 模式取门槛最低的层次；门槛状态与掌握概率取自同一行。
  工具只看门槛：`transfer` / `retain` 算掌握（`star_lit`），其余门槛算未掌握，`unseen`（含星亮层次没有估计）算未观测；掌握概率不参与判定。
- **地图版本**：只取在当前已发布地图版本上计算的当前估计；更早版本上的估计不经 `kge.id_migration` 映射，直接不取（待人确认）。
- **名单**：试点参与同意的最近一次决定为 `granted`（同一时刻有撤回按撤回），排除已执行删除的档案与删除请求进行中（含冷静期）的学生。
- **伪名**：salt 由函数在库内每次调用现场生成（`gen_random_bytes`），**不经命令行、不进 SQL 文本、不落盘**；
  伪名 `pil-` 加 24 个字母，不同次导出不可对齐。伪名只在执行机内存与临时文件里用来对齐同一学生的不同节点，任何输出都不含伪名。

## 2. 导出（只在第 0 节全部满足后；连接串只从环境变量读，不写命令行、不进日志）

```bash
umask 077
WORK=$(mktemp -d)                                   # 受控设备上的临时目录，不在仓库与同步盘里
psql -X -q -v ON_ERROR_STOP=1 -v subject=aya -v star_rule="$(jq -c .star_level_rule services/learning/config/mastery/aya.json)" \
     -f tools/kge-calibrate/sql/export-mastery.readonly.sql -o "$WORK/body.csv"
```

再导出同一已发布版本的地图快照（知识服务的快照格式，JSON 写法；与 `knowledge.m1` / `knowledge.m2` 用的是同一份文件），记下 `map_version`。

## 3. 拼成工具的输入并运行

掌握状态表首行是元数据行，其后是导出的 CSV（元数据行的 `synthetic=false` 在守卫放开之前会被拒绝，退出码 2）：

```bash
{ printf '# format=kge-calibrate-mastery/1 synthetic=false subject=aya map_version=%s\n' "$MAP_VERSION"; cat "$WORK/body.csv"; } > "$WORK/mastery.csv"
cd tools/kge-calibrate && python -m kge_calibrate run --map "$WORK/map-snapshot.json" --mastery "$WORK/mastery.csv" --out "$WORK/out"
```

## 4. 输出核对与销毁

- 输出只有 `calibration-report.json` 与 `worklist.md`（输出目录必须不存在或为空；文件以 0600 新建，不覆盖、不跟随符号链接）。
- 输出不含任何精确计数：人数向下取整到 10，比率、区间、差值保留 2 位，p 值只给分档；任何四格表有一格人数 < `min_cell`（默认 20）的，
  结论只写“样本不足”；学生总数不足 `min_cell` 也只写“样本不足”。
- 核对人逐项确认：没有伪名、没有学生 ID、没有精确计数；然后才把这两个文件交给教研（审核台接入另起任务）。
- `$WORK/body.csv`、`$WORK/mastery.csv` 是个人层数据：核对完立即删除整个 `$WORK`，不复制、不上传、不进仓库（规则 13：个人层永不进入公共层）。

## 5. 停止条件

任一前置条件缺失、连接的不是试点库、输出里出现任何伪名、学生级字段或精确计数、需要写入才能继续——立即停止，
记录在 `specs/kge09-map-calibration/lessons.md` 的“待人确认”。

## 6. 第二步：作答聚合行与错因行（易错候选、难归类、节点内部分化）

前置条件与第 0 节相同（导出函数草案里的第二个函数 `kge.calibration_export_responses` 同样须经另起的 [NEEDS-HUMAN] PR 落为迁移、
合并并经人执行；synthetic 守卫同样须另起任务放开），另加两项：

| # | 条件 | 负责人 | 记录 |
|---|---|---|---|
| 8 | 错因分类表（`docs/mapping/error-taxonomy.md`）的映射已签字，或明确按草稿运行；工具只读它，不改它 | 教研负责人 | 分类表版本 |
| 9 | 易错点编码对照（`pitfall-codes.json`：易错点 → 它覆盖的学科错因编码，公共层）由教研给出，与地图快照同一版本。这份对照只是教研的**临时口径**：易错点的错因大类以 `kge.knowledge_node.error_type` 为准，同一易错点的编码必须映射到同一个跨科大类（工具拒绝不一致的对照） | 教研负责人 | 对照版本 |

口径（详见 `sql/export-function.draft.sql` 第二个函数的注释；均待人确认）：
- 作答：独立作答、证据等级 A、证据与题目已准入、正式判分、未用提示、判分不需人工复核且判分置信度 ≥ `min_grade_confidence`；
  只取 correct / incorrect；每名学生每道题只取第一次；自报“猜的”且判对的不导出；题目经已发布地图版本上审核通过的题目标签挂到主节点，
  并带出题目的认知层次。
- 错因：`platform.error_attribution` 的当前行（`superseded_by IS NULL`），与作答同口径（只取挂在上述作答上的错因）；
  该表没有节点列，经证据找到作答的题目再挂到节点，挂不上的不导出；库内按 学生 × 节点 × 编码 去重。
- 只导出错因分类表里状态“明确”的编码：待定与敏感编码（如 `xinli_xiguan`）不出库（工具读入时也会再丢一次）。
- 一次调用同时导出两类行（同一伪名对齐同一学生；两表伪名完全不相交时工具拒绝）；与第一步的掌握状态导出是不同的调用，伪名不可对齐。

导出（连接串只从环境变量读）并按 `record_kind` 拆成工具的两个输入（元数据行的 `synthetic=false` 在守卫放开之前会被拒绝）：

```bash
CODES=$(cd tools/kge-calibrate && python -m kge_calibrate codes --subject aya)      # 只含“明确”的编码，公共层
psql -X -q -v ON_ERROR_STOP=1 -v subject=aya -v usable_codes="$CODES" -v min_grade_confidence=0.8 \
     -f tools/kge-calibrate/sql/export-responses.readonly.sql -o "$WORK/responses.csv"
{ printf '# format=kge-calibrate-attempts/1 synthetic=false subject=aya map_version=%s\n' "$MAP_VERSION"
  echo 'pseudonym,item_id,node_id,correct,cognitive_level'
  awk -F, '$1=="attempt"{print $2","$3","$4","$5","$6}' "$WORK/responses.csv"; } > "$WORK/attempts.csv"
{ printf '# format=kge-calibrate-errors/1 synthetic=false subject=aya map_version=%s\n' "$MAP_VERSION"
  echo 'pseudonym,node_id,subject_category,cross_subject_category,confidence'
  awk -F, '$1=="error"{print $2","$4","$7","$8","$9}' "$WORK/responses.csv"; } > "$WORK/errors.csv"
cd tools/kge-calibrate && python -m kge_calibrate run --map "$WORK/map-snapshot.json" --mastery "$WORK/mastery.csv" \
  --attempts "$WORK/attempts.csv" --errors "$WORK/errors.csv" --pitfall-codes "$WORK/pitfall-codes.json" \
  --taxonomy ../../docs/mapping/error-taxonomy.md --out "$WORK/out"
```

输出核对在第 4 节之外另加：
- 易错候选只列四格（扰动后，“无该编码”两格多留一份余量）都够门槛的“节点 × 编码”，其余只给“样本不足”的条数；
  同一节点的各编码表共用行人数扰动；难归类每个节点两格都 ≥ `min_cell` 才给数；
- 题目只在作答人数达标时列出：答对、答错两格都扰动，扰动后作答人数 ≥ `split.min_item_attempts` + 4（保证真实 ≥ 20）；
  两格（扰动后）都 ≥ `min_cell` + 2 才给 2 位小数正确率，否则只给分档（≥0.90 / ≤0.10 / 0.10–0.90），防“全对 / 全错”；
  题目 ID 与认知层次属公共层，可以出现；
- 工作单只提出候选：不生成错因编码、易错点文本，不改错因分类表与地图。
- `$WORK/responses.csv`、`attempts.csv`、`errors.csv` 与掌握状态表一样是个人层数据：核对完与整个 `$WORK` 一起删除。
