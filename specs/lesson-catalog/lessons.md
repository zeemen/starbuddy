# 讲评课目录与入口 · 实现记录

## PR A：契约 `list_lessons` 与阿鸦实现
### 做了什么
- **插件契约 v1.1 草稿内追加（再续）**（`packages/contracts/openapi/plugin-api.v1.1.yaml`）：
  - `Method` 枚举与能力声明各多一项 `list_lessons`（能力声明里必填，与 `present_item` 一样）；
  - 新路径 `/list_lessons`；
  - 新 schema：`ListLessonsRequest`（`context` 复用 `PresentItemContext`：与学生无关、不计量，另加 `content_version`）、`LessonCatalogEntry`、`LessonCatalog`、`ListLessonsResponse`（不产生证据）；
  - 新示例 `ListLessonsRequestAya`、`ListLessonsResponseAya`；`CapabilitiesAya` 多一项；契约开头说明加“草稿内追加（再续）”一节。
- **目录条目**：只有 `lesson_ref`、`skill_node`、`item_ref`（可选）、`title`、`step_count`。
  - 标题上限 40，与 `packages/stage` 的 `LIMITS.maxTitleChars` 一致；字符口径与台词正文一致（拒收尖括号、控制字符、不可见字符，不收换行）；
  - 步数 1–100，与 `LIMITS.maxSteps` 一致；目录最多 500 条。
- **契约检查**（`scripts/check_contract.py`）：
  - `check_v11_additive` 把只读方法写成 `V11_READ_ONLY_METHODS`（`present_item`、`list_lessons`），逐项放过 Method、能力声明、路径、schema 与示例；
  - 新增 `check_list_lessons`：
    - 目录树里不许出现讲解内容与答案类字段名（`dsl`、`script`、`lines`、`answers` 等）；
    - 对象一律 `additionalProperties: false`，数组一律有 `maxItems`；
    - 条目字段恰好是那五个；请求只收 `context`（`PresentItemContext`）与 `content_version`；响应不带证据；
    - 标题、步数上限与 `packages/stage` 一致（读 `dsl.ts`）；
  - 标题加进受限文字探针表。
- **反例与变异**：
  - 反例 +16（带学生、带计量、缺内容版本、夹带过滤条件、条目夹带 DSL、台词或答案、标题 HTML、超长、换行、步数越界、产生证据、信封外夹带评语或推荐理由）；
  - 契约变异 +13。
- **一致性测试**（`packages/contracts/conformance`）：
  - 只读方法的上下文、坏请求变体、幂等检查改按 `READ_ONLY_METHODS` 判断；
  - `list_lessons` 另加一个坏请求变体（要求讲解内容）；
  - 新增 `lesson_catalog_problems`：条目字段白名单、答案类字段名、不产生证据、秘密与受控值；
  - 参考适配器实现 `list_lessons`，并加 4 类违约（夹带台词稿、产生证据、带学生也应答、两次目录不一样）；参考夹具加一条；
  - `mutation.py` 的分母检查两个只读方法都单列。
- **阿鸦**：
  - `src/methods/list-lessons.ts`：从讲评课投影目录，按编号与版本排序；
  - `src/adapter.ts`：`list_lessons` 路由，只按 1.1.0；内容版本核对同 `present_item`；响应自检；能力声明 1.1.0 声明它；
  - 测试 `tests/list-lessons.test.ts` 5 条；变异 +5；一致性夹具加一条（那一遍不带讲评课包，目录为空）。
- **知行**：1.1.0 能力声明写 `list_lessons: { implemented: false }`，请求回 501。

### 验证
- `check_contract.py` 通过：v1.1 的 107 个反例全部被拒绝，其中新加 16 个；`contract-mutation` 357 个变异全部被发现，其中新加 13 个。
- `make conformance`（严格模式，全部适配器、全部契约版本）通过：
  - 阿鸦 v1.1：`list_lessons` 实现，3 条测试通过；
  - 知行 v1.1：声明未实现，回 501；
  - Gate 0 分母仍是六个学科方法。
- `conformance-mutation`：39 类违约、67 次注入全部被发现，其中新加 4 类。
- 阿鸦单测 179 条、知行单测 126 条全过；`check-group-contracts`（严格模式：契约、变异、一致性、db-test、渗透环境自检）通过。
- 阿鸦全量变异与 `check-group-adapters` 的结果见 PR 评论。

### 决定与差异
1. **请求复用 `PresentItemContext`**：它的口径（只读、与学生无关、不计量、幂等键可选、最低 L1）正合目录，不另起一个上下文 schema。名字带 present 稍显别扭；签字时若要改名，两个方法一起改。
2. **能力声明里必填**：与 `present_item` 一致；不实现的适配器写 `implemented: false`（知行）。文砺不按 1.1.0 应答，不受影响。

### 待人确认
已定（2026-11-28，用户“按建议，默认值都同意”）：C1 接受签字范围再扩大一项；C2 在 `build:lessons` 构建时就按契约口径核对标题与台词，另起小 PR，不放进 PR A。

- **C1**：v1.1 的签字范围又多一项（`specs/stage-point-to-ask-answer` lessons P1 同一个问题）。
- **C2**：契约的标题口径比 `packages/stage` 严，例如 `x < y` 在 stage 里可以，契约里任何尖括号都不收。讲评课标题若含尖括号，阿鸦的响应自检会让整份目录回 500；台词稿现在也是同一口径。是否要在讲评课构建（`build:lessons`）时就按契约口径核对标题与台词，请定。
