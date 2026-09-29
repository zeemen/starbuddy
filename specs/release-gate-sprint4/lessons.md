# G 流（审核台 v0 起）· 经验与待人确认

## 冲刺 1 · PR 1：签字与发布规则（迁移 0018、0019）
### 交付
- **0018 platform**：签字人表 `platform.content_signoff`（审核记录的签字人）、`platform.map_change_signoff`（变更单签字人，带签字时的内容摘要）；只追加，只有签字函数属主能写。
- **0019 kge**：
  - `kge.current_staff()`：当前账号必须是 active staff；
  - `kge.map_version_digest(uuid)`：草稿骨架与原子的 SHA-256；`kge.row_digest`、`kge.map_change_digest`；
  - `kge.sign_review`、`kge.sign_map_change`、`kge.retire_map_version`（授 `starbuddy_review`）；
  - `kge.publish_map_version` 改授 `starbuddy_review`，新增：最近一条审核结论是批准且摘要一致、目标变更单全部两人批准、版本号严格递增、含结构性变更须升次版本号；发布后变更单转为已应用；
  - `content_review` 只追加（UPDATE、DELETE、TRUNCATE 连超级用户也拦）；知识服务只能提交“待审”记录；
  - 变更单状态只能经函数推进，批准后内容不可改；已发布版本上的题目标签、时政要有摘要一致的批准记录才能生效；
  - 撤下只能经函数（发布新版本时的自动撤下除外）。
- 测试：`db/tests/79b_kge_review_signoff.sql`（48 项断言）、76 号改为经 staff 签字发布；变异 20 条；攻击面登记 5 个新函数（共 54 个）；KGE-01 库上测试改走签字发布。

### 与 spec 的差异（按默认处理，记在这里供评审）
1. **签字人不放在 kge，放在 platform**：spec 写“`reviewer` 改为 staff 账号引用（新列）”。但公共知识层不能存任何账号引用（规则 13，`80_attack_surface` 的列名、外键、uuid 列与金丝雀四条断言都会拦），
   所以签字人记在 platform 的两张签字人表，按审核记录 / 变更单 ID 引用 kge；按 ADR 02“每个迁移只改一个 schema”拆成 0018（platform）与 0019（kge）。
2. **变更单签字带内容摘要**：待审期间知识服务仍能改变更单内容，只看“两个不同账号签过”会出现“两人签的不是同一份”。每个签字记下签时的内容摘要，批准只数与当前摘要相同的签字。
3. 审核对象 v0 只支持 `map_version`、`item_tag`、`current_affair`（节点、边的审核随整个版本签字）；风险级别由调用方（审核台后端的分类器）传入，库只存。
4. 测试里地图版本 ID 从 `b1…b4` 改为 `ca01…ca04`：`b1…b1` 与夹具里一个学生账号 ID 相同，签字把它写进 `content_review.object_id` 后触发了金丝雀断言。

### 待人确认
1. 迁移 0018、0019 依赖 0011（草稿，由人执行）；按顺序在副本演练：0011 → 0012 → … → 0018 → 0019。
2. `kge.sign_review` 的风险级别由调用方传：是否要在库里按对象类型给下限（例如地图版本至少“高”）。
3. 发布人与签字人可以是同一个 staff（v0 走查只有一名教研时需要）；两人规则只约束结构性变更单。是否要求“发布人不能是唯一签字人”。
4. 摘要只覆盖公共层内容；编译页面（冲刺 3）另有 Git 提交号对账。

## 冲刺 1 · 其余 PR（未开始）
- PR 2 审核台后端（差异、风险分类、签字与发布调用、版本记录导出）；PR 3 前端 staff 路由组；PR 4 staff 登录；走查（需要真实教研在 staging 操作）。
