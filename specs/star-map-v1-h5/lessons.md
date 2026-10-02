# H5 星图 v1 · lessons

## PR 1 实现记录：学习服务星态接口（2026-10-02）

### 产物
- **契约**：`packages/contracts/openapi/learning-internal.v1.yaml` 在未签字的 1.1.0 草稿内追加 `POST /internal/v1/star-map/nodes`（spec #3 默认，没有升版）。
  - 只认 BFF 服务凭据（沿用现有 BFF 令牌方案）；编排层、洞察服务令牌一律 401；请求带 `caller`，不是 `bff` 回 403。
  - 请求 `StarMapNodesRequest`：`request_id`、`caller`、`student_ref`、`subject`（不含英语）。
  - 响应 `StarMapNodes`：`subject`、`status`（`ok` / `no_map`）、`map_version`、`nodes[]`；每项只有 `node_id`、`state`、`star_lit`、`basis_id`。各层对象都是 `additionalProperties: false`。
  - schema 约束：`basis_id` 为 null 时 `state` 必为 `unseen`；`star_lit` 当且仅当 `state` 是 `transfer` 或 `retain`；`no_map` 时不带版本号也不带星。
  - 检查脚本 `check_contract.py` 新增递归检查：响应里不得出现 `p_mastery`、`confidence`、`evidence_ids`、`cognitive_level`、`levels` 等字段，每个对象必须 `additionalProperties: false`，请求必须要求 `caller`；反例 123 个，契约变异增 21 个。
- **学习服务**：
  - `learning/mastery/gates.py`：抽出 `star_basis_level`（星亮看哪一层），`star_lit` 与星图共用这一份口径。
  - `learning/mastery/api.py`：`star_map_nodes` 用独立查询，只读 `id`、`node_id`、`cognitive_level`、`state`，按学生与学科过滤；不读 `p_mastery`、`confidence`、`evidence_ids`。
  - `learning/mastery_routes.py`、`learning/app.py`：路由与接线（BFF 凭据用现有 `settings.service_credential`，没有改 `config.py`）。
  - 测试：`tests/test_mastery_star_map.py`（9 条，一次性库、合成地图与掌握行）；`tests/test_mastery_gates.py` 新增 `test_star_basis_level`。

### 口径
- **星的集合**：当前已发布地图里全部 `status = active`、`level = 'node'` 的节点（统一壳 #9 默认）。不参与掌握计算的节点（kind 不可计算，或没有认知层次）一律 `state = unseen`、`star_lit = false`、`basis_id = null`，在前端会是中性底星。
- **可计算节点**：`state` 与 `basis_id` 取 `star_basis_level` 那一层的当前掌握记录；无记录为 `unseen`。
- **all_levels 模式**：取门槛最低的层次，同门槛取较低层次；与“全部层次都亮才算亮”等价（`LIT` 正好是最高两档）。现有配置都是 `star_level` 模式。
- **顺序**：先查地图——没有已发布地图返回 200 `no_map`；地图已发布但没有掌握配置返回 409 `MASTERY_UNAVAILABLE`。签字前生产环境每个学科都会是这两者之一。

### 偏差
1. `star_basis_level` 是“星亮门槛”必停区里的行为不变重构：契约审查做了新旧 `star_lit` 穷举对拍（0–4 个层次的全部组合 × 每层 6 个合法门槛加 None 与非法值 × 6 种规则，共 39366 组），结果全部一致；原有星亮测试与变异仍成立。
2. 初版只返回参与掌握计算的节点，偏离 #9 默认；审查后按默认改为全部 node 层有效节点（见“审查意见处理”）。
3. 有两个变异写不出来：去掉 kind 过滤、`mastery/get` 去掉 `level = 'node'` 过滤——0011 规定不可计算类型不能带认知层次、可计算类型只能在 node 层，现有条件已经把它们排除。`_nodes` 已加注释说明。
4. 响应 `nodes` 上限 5000，超出时响应自检会抛错回 500；真实阿鸦能力坐标约 959 个，知识节点数待地图发布后核对（spec #8）。

### 给 PR 2（BFF）的约束（来自两项审查）
1. **先授权再调用**：学习服务完全相信 BFF 传来的 `student_ref`，自己不再校验。BFF 必须先在 `withAccount` 事务里确认可读（`platform.can_read_student`，与 guardian_student_link 同一条件），不可见回 404，**不发调用**；`student_ref` 只取自已校验的路径参数。测试覆盖兄妹、另一户、撤销绑定，并断言学习服务没有被调用。
2. 只为在读学科（`subject_enrollment.status = 'active'`）调用。
3. **中性底星不下发星态**：没有匹配判断的星，响应里不得出现 `state`、`star_lit`、`basis_id`；`basis_id` 只在 BFF 内部比对，不下发。路由测试断言中性星的字段集合。
4. 不记录学习服务响应体；如需缓存，键含 (account, student, subject)；不在持有 `withAccount` 事务时等待这次 HTTP 调用，并设短超时。
5. **判断匹配的边界**：同一条已亮掌握记录会同时挂“知识掌握”与“知识迁移”两条判断——画星只用“知识掌握”，“知识迁移”只用于摘要位；all_levels 下最低层次会随学习变化，旧判断的 `basis_id` 对不上时显示中性底星，直到判断重新生成；最低层次不在判断策略的门槛集合里时该层没有判断，星为中性。PR 2 夹具覆盖这三种边界。
6. 409 与 `no_map` 的呈现：默认 `no_map` 显示“地图建设中”，409 按中性底星或“地图建设中”处理，由 PR 2 定并写入 lessons。

### 测试结果（审查修订后，均为 `CONTRACT_STRICT=1`）
`make contract-test` 通过（learning-internal v1 反例 123 个全部被拒）；`make contract-mutation` 215 个全部被发现（learning-internal v1 57 个）；`make learning-test` 2633 passed；`make mastery-mutation` 纯函数组 77 个、库上组 54 个全部被发现。过程中没有命令被 guard hook 或权限拦截，改文件只用 Read / Edit / Write。

### 审查意见处理
安全审查无阻断；契约审查 1 条阻断（星的口径偏离 #9 默认且未记录）。按用户“按默认建议处理审查意见”：星的集合改为全部 node 层有效节点，不可计算节点为 unseen；契约 description 改准确；`star_basis_level` 补纯函数单测与同级取低的变异；星图查询只读必要列；补 caplog 测试（日志不含学生 ID、记录 ID、节点 ID）；`_nodes` 加注释。给 BFF 的安全建议列在上面，由 PR 2 落实。

### 待人确认
- **A**：契约 1.1.0 签字范围扩大到 `star-map/nodes`；BFF 令牌第一次能读到掌握派生的星态（只有状态，没有数值）。
- **B**：不参与掌握计算的节点（方法、素材、易错点、考点等）在星图上恒为中性底星，教研与产品确认是否符合 #9 的预期。
- **C**：all_levels 模式下 BFF 匹配的是门槛最低层次上的判断（偏差与边界见上）。
- **D**：BFF 如何呈现 409 与 `no_map`（PR 2 定）。
- **E**：纵深防御（请求另带账号 ID，学习服务再查一次授权）要改契约，现在不做，留给 ADR。
