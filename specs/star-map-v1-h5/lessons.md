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

## PR 2 实现记录：BFF 星图 v1 读模块（2026-10-02）

### 产物（均在 `services/bff/`）
- `src/star-map/features.ts`：开关 `starmap_v1`、`starmap_dashed`（环境变量 `STARBUDDY_STARMAP`），默认都关；只在 dev / test 能开，其他环境设了就拒绝启动；只开虚线不开 v1 也拒绝。
- `src/star-map/nodes-client.ts`：调学习服务 `star-map/nodes`。`caller=bff`、不跟随重定向、超时 3000 ms、不重试、不缓存、不记响应体；响应先限体积（正常 2 MiB、409 为 64 KiB），再按契约逐字段核对（多字段、`star_lit` 与 state 不一致、basis 为空却不是 unseen、学科不对、节点重复，任一项不合格整科按不可用）。
- `src/star-map/v1.ts`：`paintSky`（画星纯函数）与 `getStarMapV1`（读取流程）；`src/http/star-map-v1-routes.ts`；`openapi/star-map.v1.yaml`。
- `config/shell.json` 新增 `star_map`：`timeout_ms`、`mastery_templates`、`transfer_templates`（两张表加载时必须是对外白名单的子集且互不相交；仓库里为空）。
- 测试：`tests/star-map-v1.test.ts`、`tests/star-map-v1.integration.test.ts`（所有 200 响应按 `star-map.v1.yaml` 校验 schema，借用 adapters/aya 已锁版本的 ajv）；route-matrix、openapi-ops、shell-openapi、shell-units、shell-startup 相应更新；`tests/mutations.sh` 新增“星图v1：”类 12 条。

### 读取流程
1. 第一个只读事务：`platform.can_read_student` 确认可读，不可读统一 404、不调任何下游；取在读学科（`active`，不含英语与 planned）、BFF 能读到的已发布地图版本与节点，家长另取有效绑定。
2. 事务外：只为“在读且 BFF 看得到已发布地图”的学科调学习服务；`student_ref` 只来自已校验的路径参数。
3. 一致性核对：学习服务返回的地图版本与节点集合必须与 BFF 读到的完全一致，否则该科全部中性。
4. 第二个只读事务：再确认一次可读；读判断（受众可见、成立或假设，开虚线时另含待验证；未过期、模板已审核、依据是掌握记录）；开虚线时另读该节点最早一条未关闭复测任务的日期。
5. 事务外：画星；星数说明与迁移摘要经 `insight.explain` 取文字（沿用每页 10 条、整页时限与每会话限频），再过禁用推断词表。

### 画星规则（规则 11）
- 三条件都满足才画：模板在白名单且属于“知识掌握”维；受众可见、成立或假设；判断 `basis_id` 等于学习服务给的当前掌握记录。满足时下发 `display`（unlit / half / lit）、`retained`（“保持”外圈）与 `claim_id`。
- 中性星只有 `node_id`、`name`、`display: neutral`；响应里从不出现 `state`、`star_lit`、`basis_id`。
- “知识迁移”判断不画星，只用于该科迁移摘要。
- 星数说明与迁移摘要的文字过禁用推断词表（规则 12，与周报同做法）：命中就不显示该字段，星照常画；词表读不到时一句都不显示。

### 409 与 `no_map` 的呈现（PR 1 待确认 D 的答复）
- `no_map`，或 BFF 读不到已发布地图：显示“地图建设中”，不调学习服务。
- 409 `MASTERY_UNAVAILABLE`、学习服务不可用、地图版本或节点集合不一致：按 BFF 读到的节点画全中性星，不下发星态、`claim_id` 与说明文字。理由：地图确实已发布，只是掌握口径不可用，显示“建设中”会误导。

### 偏差
1. **v1 路径**：独立路径 `GET /bff/v0/students/{id}/star-map/v1`，没用 `?v=1`（路由表与接口定义按“方法 + 路径”一一对应，身份注入测试把查询参数当噪声）。开关关时不注册，与表外路径同一个 404；v0 响应在开关开、关两种部署下逐字节相同。
2. **学生端也带 `confidence_band`**：spec 只要求家长端；为与今日计划、档案页一致，学生端也带。
3. **“下一次验证任务”的来源**：契约没有现成字段，取 `platform.retest_task` 该节点最早一条未关闭任务的上海日期（0037 已授权 BFF 读），不带原因编码。
4. **虚线按库里 `status = to_verify` 画**，没核对 `RenderedStatement.display = dashed`；挂在默认关闭的开关后，ADR 13 #2 签字后改为按后者画。
5. **星数说明取节点级判断**：取该科最近更新的那条匹配“知识掌握”判断的陈述（最多试 3 条候选），是节点句放在学科摘要位；spec 7.2 的例子是学科级的“已点亮 N 颗”。
6. **成立优先于待验证**：同一节点、同一依据下若同时有较旧的“成立”与较新的“待验证”（不同模板），按“成立”画星。

### 测试结果（审查修订后）
`make bff-test` 通过；`make bff-mutation` 148 个变异全部被发现（星图 v1 类 12 条）。契约审查另以 `CONTRACT_STRICT=1` 跑过 `make contract-test`（通过；它不检查 `star-map.v1.yaml`，该文件由集成测试的 schema 校验覆盖）。过程中没有命令被 guard hook 或权限拦截，改文件只用 Read / Edit / Write。

### 审查意见处理
安全审查无阻断；契约审查 1 条阻断（规则 12：星图文字没有过禁用推断词表）。按用户“按默认建议处理审查意见”：星数说明与迁移摘要过词表（夹具与变异）；补节点集合多一个、少一个两种不一致的测试与变异；变异脚本里被拼接的注释拆回单独一行；补 `transfer_templates` 含白名单外模板被拒；集成测试用 schema 校验实际响应；学习服务响应设体积上限。

### 纵深防御与写不出的变异
- 授权有两层：去掉 `can_read_student` 后，RLS 仍让越权方读不到在读学科，学习服务依然不会被调到；该变异靠 404 断言发现。
- 去掉判断查询里的受众过滤写不出可被发现的变异：RLS `claim_front_read` 仍按受众过滤，结果不变。
- 集成测试在库里已有的已发布数学地图上追加合成节点，依赖测试文件按字母序执行（shell-pages 先建图）。

### 待人确认
- **F**：`config/shell.json` 的 `star_map.mastery_templates` / `transfer_templates` 由人登记（画像可见范围，必停）；现在为空，所以星全部中性。
- **G**：“下一次验证任务”的口径（偏差 3）。
- **H**：v1 路由没挂限流桶。每次请求最多为每个在读学科调一次学习服务，外加两个数据库事务，只有陈述文字有每会话限频。开放到预发环境之前，必须在限流配置里加按会话计的桶（`config/auth.json`，必停，阈值由人定）。
- **I**：学生端是否也带 `confidence_band`（偏差 2）。
- **J**：星数说明用节点句还是等学科级模板（偏差 5）。
- **K**：成立优先于待验证是否符合判断账本语义（偏差 6），请 SIE 负责人确认。
- **L**：虚线改为按 `RenderedStatement.display = dashed` 画的时机（偏差 4，随 ADR 13 #2 签字）。

## PR 3 实现记录：前端星图 v1（2026-10-03）

### 产物
- **开关**：`apps/web/src/star/features.ts`，`starmap_v1`、`starmap_dashed`（环境变量 `STARBUDDY_STARMAP`），默认关，只在 dev / test 生效；只开虚线不开 v1 一律算关；只在服务端读，没有 `NEXT_PUBLIC`。开关关时 `StarMapPage.tsx` 提前返回，v0 代码路径一字未动，不发任何 v1 请求。
- **渲染**：`src/lib/star-map-v1.ts`（类型与 BFF 客户端，类型与 `star-map.v1.yaml` 一致）、`src/star/v1-view.ts`（纯函数：只显示数学、语文、道法；虚线开关关时按中性画；没有 `claim_id` 的星一律中性；一页超过 120 颗按领域折叠）、`src/star/StarMapV1.tsx`（领域为星座、主题为子群）；`Star.tsx` 新增 `neutral`（与 v0 未亮同一绘制）与 `dashed`；`tokens.ts` 新增虚线线段与间隔；`copy.ts` 新增 `STAR_MAP_V1`、`STAR_DISPLAY_V1`；`globals.css` 只追加 v1 类名。
- **测试**：单测 `tests/star-v1.test.ts`、组件级渲染测试 `tests/star-v1-render.test.ts`（`tests/tsx-loader.mjs` 只供单测）；E2E `e2e/star-map-v1.spec.ts`（第三套环境 `starmap-on`）、`e2e/star-map-v1-off.spec.ts`、`e2e/visual-v1.spec.ts`（5 张 v1 新基线）；`scripts/e2e.sh` 新增第二个学习服务（合成掌握配置）与第三个 BFF（两个开关都开，测试专用显示规则写进 mktemp 生成的配置）。
- **合成种子**：`db/seeds/dev/star-map-v1-synthetic.sql`（合成数学地图 0.9.0，119 个节点；家庭 HV：V1 在读数学、道法、英语，V2 在读数学、语文；掌握行以 `starbuddy_learning`、判断以 `starbuddy_insight` 身份写入，0010 守卫照常生效）。挡板三道：`starbuddy.allow_synthetic_seed=on`、库名限 `starbuddy_(dev|test)…`、只能执行一次且已有已发布数学地图就拒绝。
- **Gate 0**：`make gate0-starmap-v1`；`tools/e2e-cohort/sample/starmap-v1/`（学生端、家长端 PNG，`starmap-v1.json` 含文件与像素 SHA-256，`provenance.md`），都标“未启用”；`docs/gates/gate-0-evidence.md` 只在 H1 下追加一条记录，判定与 v0 截图不变。两次运行像素哈希一致：学生端 `136221d5ccf32ac0b16f83a5b9f6d3ecb3bff7b7d0fc81ba0fc21eb5a026358c`，家长端 `ccab0f3c7b54692bc5b5cb0bd305ba6951ec2901f2d4855fa7150f1a6860b064`。截图生成于未提交工作区，合入后更新提交号。

### 判断先于展示在前端的落点（规则 11）
- 画了的星的 `<li>`、虚线日期行、陈述 `<span>` 都带 `data-claim-id`；节点、领域、主题名放 `data-node-name`，数字与日期放 `data-value`。
- 状态词（未亮、半亮、亮、亮·保持）不算壳文案，不进全量文案表，只能出现在 `data-claim-id` 元素内；单测、组件渲染测试与 E2E 文本节点核对都在守。
- 中性底星不带 `claim_id`，没有可见文字，读屏读“节点名：暂无可展示的结论”。
- E2E 核对页面上的 `data-claim-id` 集合与同一会话 BFF 响应一致。

### 审查意见处理
两项审查都没有阻断。按用户“按默认建议处理审查意见”：
- 置信度说明前端只在家长端显示（BFF 仍两端下发），与周报、今日计划一致——PR 2 待确认 I 在前端侧关闭；组件级渲染测试断言家长端有、学生端没有。
- 状态词移出全量文案表，单独断言只在 `data-claim-id` 内。
- E2E 的前端与前两套 BFF 显式清空 `STARBUDDY_STARMAP`，不从调用方环境继承。
- 正式 `services/bff/config/shell.json` 永不含 `syn.*` 合成模板，有单测守护；种子模板处注明“合成占位，永不进正式白名单”。
- 补学生会话访问另一户孩子的页面与 BFF v1 均 404。
- 可选项：前端开、BFF 关时显示“没有找到”的单测；截图输出目录限定在仓库内；前端类型收紧；种子 `evidence_ids` 注明不对应任何证据。

### 测试结果
`make web-test`：55 passed、1 skipped（`@gate0`）；`e2e.sh star-map-v1`（含开关关）5 passed；v1 视觉回归 5 passed；`make gate0-starmap-v1` 两次哈希一致；`make bff-test` 通过。过程中没有命令被拦截；第一轮生成基线时子代理用 `rm` 删过一张自己刚生成的 PNG 后重生，修订轮没有删除仓库内文件。

### 已知问题
- **v0 视觉基线早已过期**：`make web-visual` 中 v0 星图页 S1 的 3 张失败。v0 基线在 3-H（51dfea1）提交后没更新，之后统一壳 H4 给学生页套了顶栏（实际截图比基线高 94px）；统一壳 lessons 待确认 M 记录过当时没重跑视觉回归。本 PR 对 v0 输出没有改动（符号样张页基线通过）。`web-visual` 不在 `make check` 里。
- **诊断 E2E 偶发失败**：3 次完整 `make web-test` 中出现 1 次“现在还不能开始”，单独重跑通过，原因未查明，与本 PR 无关，建议另开任务查。

### 待人确认
- **M**：“120 颗”按整页累计（现做法），还是按每片星空分别计。
- **N**：合成种子把数学地图直接写成已发布，用复制模式（`session_replication_role=replica`，只包住地图三条 INSERT）绕过发布守卫；审核台里会出现没有签字记录的已发布地图。只限种子的例外。
- **O**：合成判断模板在种子里是 `approved`（模拟人工登记），测试环境把它们列进对外白名单（必停：判断模板、画像可见范围）。
- **P**：“为什么”入口只挂在星数说明与迁移摘要旁，没有挂在每颗星旁（16px 星格放不下），星的详情位待设计。
- **Q**：v1 五张视觉基线与虚线比例（线段 2px、间隔 1.5px，占位值）待视觉负责人签字。
- **R**：E2E 借用学习服务单测的合成掌握配置 `synthetic-test.json`（只在 test 环境的第二个学习服务实例里）；折叠用例的 121 颗依赖审核台种子发布的语文地图（2 颗）。
- **S**：v0 视觉基线是否重生（见“已知问题”）。
- **T**：E2E 环境没有洞察服务，星数说明的文字与置信度只由组件级渲染测试覆盖。
