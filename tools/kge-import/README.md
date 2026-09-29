# tools/kge-import · 阿鸦题库导入与打标三层（KGE-02）

对应 `specs/kge-02-atoms-tagging/spec.md` 设计要点 1、2、6。把 legacy 阿鸦题目按 KGE-01 已导入的地图版本打标，
生成 `db/seeds/kge/aya-items-<地图版本号>/` 三件产物。**只生成 SQL，不连任何数据库、不联网、不调用任何真实模型。**

> KGE-01 的骨架导入器在 `tools/kge-inventory/src/import/`（见 KGE-01 lessons 差异 2），本工具是 KGE-02 新建的包：
> 复用 `tools/kge-inventory` 的 legacy 只读加载器（解析钩子）与 `stableUuid`，也借用它的开发依赖（`typescript`、`@types/node`），自己不装依赖。

## 用法

```bash
cd tools/kge-import
# 类型检查（用 tools/kge-inventory 已装的 tsc；先在那边 npm ci）
../kge-inventory/node_modules/.bin/tsc -p tsconfig.json
# 单测（node --test；不需要 legacy，需要 legacy 的那条没有副本时打印“跳过”）
node --no-warnings --test --test-reporter=dot tests/*.test.ts
# 生成种子（默认只跑规则层）
node --no-warnings src/main.ts --aya ../../legacy/aya --map ../../db/seeds/kge/aya-fun-0.1.0
```

参数：

| 参数 | 默认 | 说明 |
|---|---|---|
| `--aya` | 必填 | 阿鸦 legacy 只读副本根目录 |
| `--map` | 必填，可多个 | KGE-01 生成的地图版本种子目录（读其中的 `import.sql`）；多个目录必须属于同一地图版本 |
| `--out` | `db/seeds/kge/aya-items-<地图版本号>` | 输出目录；开了 `--vector` / `--model` 时**必须显式给出，且不能在仓库 `db/seeds/` 之下**（含借符号链接绕进去），否则报错退出 2 |
| `--config` | `config/aya-chapter-segments.yaml` | 章节 → 六段映射表（待教研核对） |
| `--pack` / `--no-pack` | `adapters/aya/content/pack.json` | 核对题目已在内容包登记（`platform.item` 有这道题）；未登记的不写库、进工作单 |
| `--vector` | 关 | 开启向量层，**只接假嵌入**（`FakeNgramEmbedder`）；产物标注“含假实现，不得执行” |
| `--model` | 关 | 开启模型层，**只接假模型**（`FakeTagModel`，`model:fake-a/fake-tagger@0.0.0`）；产物标注“含假实现，不得执行” |
| `--top-k` | 5 | 向量层 Top-K |
| `--rules-only-a` | 关 | 只跑规则层时，规则直接命中（0.9）也算 A 级；供教研试用时放宽，见下 |

`make` 目标由仓库维护者另加（本任务不改 Makefile），建议：类型检查 + 单测并入 `make check`；生成命令做成 `make kge-items`。

## 产物

- `items.sql`：一个事务，`SET LOCAL ROLE starbuddy_kge` 写入
  - `kge.item_family`：legacy 只有压轴专项有题组（母题切片 + 变式 + 挑战），按组生成，`code = aya-sprint-<组>`，ID 由“地图版本号 + code”确定性派生；
    主考点取组内“规则直接命中”的主考点里出现最多的；组内没有落在已导入节点上的，不生成、进工作单。章节题的“单元”（U01…）不是题组，不生成；
  - `kge.item_tag`：只写 **A 级、六段齐全、已登记** 的题；`source_kind = legacy`（迁移 0024 新增的取值，旧产品题库来源待核实）、`license_state = unverified`、
    `tag_review_status = pending`（待审草稿），加迁移 0024 的 `tag_confidence`、`tag_source`、`tagger_version`、`tag_model_version`（只有模型层非空）
    与 `tag_basis`（A 级结论依据的每一层及其版本：规则层写打标器版本、一致的向量层写嵌入器版本、一致的模型层写模型版本；只有一层也写一个元素）；
    `node_ids` 含主考点、1–3 个；
    认知层次只在主考点节点恰好有一个认知层次时填（KGE-01 导入时都为空，所以现在都是 NULL）；
  - `kge.tag_candidate`：其余在已导入主题里、有候选的题，去重后的全部候选，`item_source_kind = legacy`（教研接受主考点候选写回 item_tag 时沿用；
    `id`、`status`、`created_at` 用默认值）；
  - 不写 `platform.curriculum_version`（六段由内容登记脚本负责）；不含题干、答案、解析。
  - 执行前提：迁移 0011、0019、0024；地图版本种子已执行；题目已由 `adapters/aya/scripts/register-content.ts` 登记。
- `worklist.md`：教研工作单——推不出六段的题、落在未导入主题的题（计数与 ID）、自然语言引用对不上节点的题（附 legacy 同名 / 相近节点建议）、
  无处可标的题、A 级但不能写 item_tag 的题、节点数超上限的题、未登记的题、未生成的题组等。
- `tagging-report.json`：格式 `kge-tagging-report/1`，类型定义在 `src/items/report-types.ts`（`TaggingReport`，覆盖矩阵报表按它读）。
  含各层候选数、A/B 级数、每题最终结果（题目 ID、主考点、节点、置信度、层、B 级原因、A 级依据 `tag_basis`）与每题推出的六段；items 每题一行。
- **含假实现的产物**（开了 `--vector` / `--model`）：SQL 头部、报告顶层 `warning`、工作单开头都标“含假实现，不得执行”，报告 `run.fake_layers` 列出假层；
  SQL 在 `BEGIN` 之后第一句就 `RAISE EXCEPTION`，误执行时整个事务回滚。命令行拒绝把这类产物写进 `db/seeds/`。

同一输入两次运行逐字节相同（没有时间戳，全部排序；题组 ID 确定性派生）；`tests/legacy.test.ts` 在有 legacy 副本时核对仓库种子没有过期。

## 打标三层（`src/tagging/`）

1. **规则层**（`rules.ts`，完整实现，版本 `aya-tagger-rules@0.1.0`）
   - 章节归属：专题注册表 → 题目 ID 前缀（`Q-FUN-U01-…` → `fun`）→ 压轴专项按主测节点所在章节投票；章节未导入地图版本的题不打标；
   - 显式引用：`mainNodes` 里的旧编码经 `kge.id_migration` 落到节点 → 主考点候选，**直接命中 0.9**（`rule_explicit_ref`）；
     `auxNodes` → 次要候选 0.9（`rule_explicit_aux`）；
   - 章节 → 主题：旧编码在已导入章节、但节点没导入（能力子节点 C、缺错因的易错点 P）→ 它所在知识簇的主题节点，**推断 0.6**（`rule_chapter_topic`）；
     同一题已有该主题下节点被直接命中时丢掉（父子节点不同时进节点清单）；
   - 自然语言引用：名称规范化（NFKC、去空白、去结尾括号说明）后与地图节点同名且唯一 → 推断 0.6（`rule_text_name`，只对主测 / 辅助节点）；
     legacy 的 97 处全部在 `preNodes`（前置、错误追溯方向），不是题目考查的节点，**只解析、进工作单，不产生候选**；部分包含只作建议。
2. **向量层**（`vector.ts`）：`Embedder` 接口（异步，请求带模型路由必需字段 `task = 'T7'`、`data_class = 'L0'`）+ `FakeNgramEmbedder`（字符一元 / 二元组哈希 + 余弦，**不是生产模型**）；
   按节点“名称 / 包含 / 题例”文本与题目文本（单元名 + 题干 + 选项，只在内存里）排 Top-K，第 1 名作主考点候选（`vector_similarity`）。
   真实嵌入模型待 spec 待人确认 #4。`RoutedEmbedder` 是经路由 T7 的桩：构造时要求路由地址、令牌与嵌入器版本，**调用一律抛“真实路由未接入（spec 待人确认 #4、#5）”，
   不回落到假实现**；`main.ts` 不接它。
3. **模型层**（`model.ts`）：`TagModel` 接口（异步，入参带 `task = 'T8'`、`data_class = 'L0'`）+ `FakeTagModel`；只能从规则与向量层的候选里选主考点与 1–3 个节点，输出理由码（不收自由文本）与模型版本；
   `modelLayer` 把关，越界一律抛错。真实实现经学习服务模型路由（T8、L0）、只用境内已备案模型，待 spec 待人确认 #4、#5，本工具不接；
   `RoutedTagModel` 是定下接口形状的桩（构造要求路由地址、令牌、打标器版本与模型版本，调用一律抛错，令牌只在私有字段里）。
   两层接口异步，流水线（`pipeline.ts`）与 `buildItems` 相应返回 Promise；题目逐道顺序处理，输出仍确定。

**合并与分级**（`merge.ts`，spec 待人确认 #6 默认）：去重；主考点取置信度最高的主考点候选（平手按 legacy 引用顺序）；
节点清单 = 主考点 + 规则层、模型层的其他节点（向量层只作召回），上限 3，超出截断并降 B；
**A 级 = 主考点上规则层主考点候选 ≥ 0.85，且向量或模型层也把它作主考点**（参与一致的层与版本记进 `tag_basis`）；其余 B 级进候选队列；没有任何候选的为“无处可标”。

> **注意：默认只跑规则层，按上面的口径一律 B 级——默认输出里 `item_tag` 为空，全部进候选。**
> 这是严格执行 spec 的结果，不是缺陷。教研试用时可加 `--rules-only-a`：只跑规则层时，规则直接命中 0.9 也算 A 级（开了别的层就不放宽）。
> 当前二次函数主题：默认 A 0 / B 34 / 无处可标 1；`--rules-only-a` 下 A 27（写 item_tag 19 题，另 8 题是六段未定的压轴专项）。

## 安全与数据

- legacy 在隔离子进程里加载（`src/extract-items.ts`）：`--permission` 只读 legacy、本工具与 kge-inventory 源码目录，不能写文件与起子进程，环境变量清空，
  导入前拿掉全局网络接口；输出按不可信数据逐项核对（`checkBank`）。沿用 KGE-01 的做法，Node 权限模型不管网络（KGE-01 待人确认 #7）。
- 题目文本只在内存里给向量层 / 模型层用，不写进任何产物；产物里只有 ID、节点、置信度、理由码与计数。
- 测试夹具全部合成；不含任何学生数据。
