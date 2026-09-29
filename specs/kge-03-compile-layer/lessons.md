# KGE-03 编译层 · lessons

## PR ①：页面格式 + 编译器 + 程序体检（含 SymPy）+ 页面仓库模板（子代理）
范围：spec 待人确认 #1 的 PR ①。PR ②（知识服务读工具、MCP、迁移 0026、快照发布、`page.get`）与 PR ③（兜底检索、对照评测、M1 报表）不在这里。

### 交付
- `services/knowledge/`（Python 包 `knowledge`，工程约定照 `services/learning`；`.venv` 已被根 `.gitignore` 忽略）：
  - `atoms.py`：公共层原子与地图快照（白名单字段，未知字段一律拒收）、原子键与哈希（规范 JSON 的 sha256 截 16 位）、快照差异；数据源接口 `AtomSource`，本 PR 为文件快照，PR ② 接库。
  - `nodeid.py`：节点 ID 正则与学科前缀；测试直接用 `docs/design/kge/check_node_id.py` 的解析函数核对与文档、迁移 0011、知识服务契约逐字一致（页面仓库 CI 没有本仓的 docs，所以包里固化一份）。
  - `pages/`：Markdown + YAML 头部；三种段落用 `<!-- kge:section id=… kind=locked|render|narrative -->` / `<!-- kge:end id=… -->` 标记；规范写法往返逐字节稳定。
  - `compiler/`：编入（`atom_refs` 索引 + 挂在节点上的新增原子；节点改名时连带相邻页）、渲染重建、全量重编（经模型路由）、合并窗口、本地输出（每个合并请求一个目录：新版页面 + `MERGE_REQUEST.md` + `merge_request.json`，含差异、依据原子、体检结果）。命令行 `python -m knowledge.compiler`。
  - `lint/`：程序体检 20 类检查，命令行 `python -m knowledge.lint <页面目录> [--map-snapshot] [--baseline] [--lexicon] [--json]`；人设词表只读抽取，`--export-lexicon` 导出给页面仓库。
- `docs/design/kge/codification-math-v1.md`：结构骨架与机器可检规则；第 10 节教学内容要求全部留空“待教研填写并签发”。
- `tools/kge-pages-template/`：页面仓库模板（`repo/` 目录结构、`ci/lint.sh`、GitHub 与 GitLab 两份 CI 样例、合并请求模板）、分支保护设置清单、教研操作说明骨架。
- 合成夹具：`tests/fixtures/map-v1.yaml`（合成节点 `MATH.SYN.*`、合成课标、合成教材与地区投影）、`tests/fixtures/pages/kp/` 4 页（由 `tests/fixtures/make_pages.py` 生成，测试核对逐字节一致）。
- 测试 195 条（`make knowledge-test`，约 15 秒）；变异 30 条（`make knowledge-mutation`，约 80 秒），均已加进 `make check`。

### 实现要点
- 页面 ID = 节点 ID（KGE 第二章），文件 `pages/kp/<节点 ID>.md`。
- 引用写法：原子 `[@类型:ID]`，页面 `[[页面 ID]]`，教材原句占位 `{{quote:<投影 ID>}}`。边的 ID 用 `<from>><类型>><to>`，课标用 `<年份>/<编码>`，避免把数据库 UUID 写进页面（个人信息检查会拦 UUID）。
- 锁定段与渲染段只能由编译器从原子生成：体检同时做“与模板逐字一致”（`template`）和“与上一审核版一致，除非所引原子哈希变了”（`locked_baseline`）。后者不依赖快照，页面仓库没给快照时也能拦住改写。
- 风险按 KGE 第四章分级发布闸门判：锁定段变更、前置关系表述变更、教材投影（页码、原句）变更、全量重编 = 高；其余渲染段变化与只有哈希更新 = 低。全量重编一律高风险，即使模型写回的叙述段逐字相同。
- 合并窗口按北京时间换日；同一页同一天的多次低风险编译合并成一次发布（版本号只升一次），当天已发布过的顺延到次日窗口。高风险与中风险单独成请求，审核人数 2。
- 全量重编：任务 T8（后台批量生产）× L0，是路由表里最贴近“离线公共知识构建”的任务，且路由表 T8 只接 L0——**不需要改路由配置，没有新增供应商**。接入方式同 `evals/persona/diandian/compare.py`（把 `services/learning` 加进 `sys.path`）。请求体由白名单逐字段挑出公共原子，发送前再递归扫键名，出现个人层字段即拒绝；测试断言请求里不含任何个人层字段、不含教材原句。
- 模型标识名不进产物：页面与合并请求说明只记 `source_version`（`prompt:kge-compile-kp@1`），`model_version` 只在模型路由的调用记录里（有测试扫输出目录）。
- LaTeX：没有用 `sympy.parsing.latex`（要 antlr4 或 lark，属新增依赖），自写子集转换器；`parse_expr` 内部会 eval，所以转换结果先过字符与标识符白名单、数字位数与指数上限（有测试）。
- 人设词表全部只读抽取：A–E 类取 `forbidden.yaml`，F 类取阿鸦（数学页）与妙笔（语文页）规格书 §5.2 引号里的表述，G 类取考官型基线 §5.2，角色名取各规格书标题与“称呼”行，口头禅取写明“主句式 / 固定句式”的行；锚点找不到就报错（fail closed）。
- 坑：一页解析失败会让别的页面的 `[[链接]]` 与模板连带报错，所以解析失败的页按文件名计入页面集合，只由 `format` 阻断。

### 与 spec 的偏差
1. **教材投影变更判高风险**：任务说明写“渲染段其他变化 = 低”，但 KGE 第四章分级发布闸门把“教材投影中的原句与页码”列为高，按 KGE（知识内容以 KGE 为准）处理；原句只在投影表里、页面文字不变时也判高。
2. **节点—课标承接**：`kge.*` 没有节点与课标条目的承接表；快照格式补了 `curriculum_links`（并入节点原子的哈希）。PR ② 接库时需要确定来源（见待人确认 1）。
3. **定义段的内容**：`kge.knowledge_node` 没有“定义”文本列，定义段用节点的名称、包括、不包括渲染。
4. **全量重编只有 Python 接口**：命令行只做渲染重建，不在命令行里调模型。
5. **时政原子不渲染进知识点页**：只在体检里查挂在节点上的时政是否过期（数学首期没有时政）。
6. **新建页面**不在本 PR：编译器只重建已有页面；新增页面会改变相邻页的 `[[链接]]`，相邻页需随之渲染重建（否则 `template` 阻断）。

### 待人确认
1. 节点—课标承接关系放在哪里（`kge.*` 新表、节点列，还是由 `aligned_to` 边表达）——涉及迁移，属必停项；PR ② 前定。
2. 句长上限默认 60 字、群体统计门槛默认 30（spec #6）、截断哈希 16 位：由教研与人确认后写进编纂规范签发版。
3. G 类（考官型）禁语对所有页面生效，其中“不错”“一般来说”“我觉得”等在数学叙述里也会被拦；F 类按学科生效。是否收窄由人设负责人定。
4. 角色名“春秋”“方圆”对数学页面生效没有问题，但历史、道法页面上线前要处理与正文词语的冲突（例如“春秋时期”）。
5. 中风险在阶段 0 按高风险处理（两人），与 release-gate-sprint4 #7 一致；语义体检上线后是否改为抽检，由人定。
6. 页面仓库托管平台（ADR 12 #1）、审核类别（ADR 12 #2）、CI 取本仓工具的方式与凭据（只读、固定版本），由执行人按模板 README 配置。
7. 全量重编的 `model_version` 只记在路由调用记录、不写进页面仓库：审计时按合并请求里的编译任务 ID 与调用记录关联，是否足够由人确认。

## PR ②：知识服务读工具（HTTP + MCP）+ 页面快照导出与 page.get + 数据库数据源（子代理）
范围：spec 设计要点 5，待人确认 #3、#4、#7。迁移 0026（只读角色）已在前一提交合入，本 PR 不改迁移、不改契约、不改人设与金标数据。

### 交付
- `services/knowledge/knowledge/service/`：
  - `contract.py`：加载 `knowledge-service.v0.9.yaml`（跨文件引用 plugin-api），工具清单由契约 paths 生成；严格校验；MCP inputSchema 由同一 schema 打包（`$defs`）。
  - `tools.py`：唯一实现层，HTTP 与 MCP 共用。七个公共层工具；`evidence.write`、`mastery.get`、`mastery.explain` 回 501 `METHOD_NOT_IMPLEMENTED`。
  - `reader.py`：`starbuddy_kge_read` 连接池（SET ROLE + 会话只读事务）、启动时角色断言、带过滤条件的 SQL、`DbAtomSource`。
  - `pages.py`：读快照目录、`page.get` 渲染；`resolve.py`：`node.resolve` 规则解析；`app.py`（FastAPI，`/mcp` 挂 MCP）、`mcp_server.py`、`config.py`、`__main__.py`。
- `knowledge/compiler/snapshot.py`：导出已发布页面快照（`python -m knowledge.compiler.snapshot`）。
- 测试（`make knowledge-test`，共 257 条，约 25 秒；库上部分用 `tests/pgkge.py` 起一次性 Postgres）：
  `test_service_unit.py`（契约、配置、鉴权比较、快照反例）、`test_service_db.py`（可见性、角色、不能写、第二道防线、数据源）、
  `test_service_contract.py`（契约一致性）、`test_service_mcp.py`（进程内 MCP 客户端 + streamable HTTP）、`test_service_e2e.py`（端到端）。
- 变异：`tests/mutations.sh` 新增 23 条（共 53 条，约 3.5 分钟），全部被发现；没有 Postgres 时知识服务的 19 条跳过并以退出码 3 报告（严格模式判失败）。

### 实现要点
- **鉴权照学习服务内部接口**：Bearer 令牌 sha256 后常量时间比较、全部比完再取结果（`services/learning/learning/app.py` 的做法）；
  三类调用方各一个令牌（`KNOWLEDGE_TOKEN_BFF/_ORCHESTRATOR/_LEARNING`），令牌决定 caller；请求体 `context.caller` 不一致 → 403；
  `node.resolve` 只给 orchestrator，个人层只给 orchestrator / learning（与契约的 ResolveContext、StudentContext 枚举一致，有测试比对）。
- **数据库两道防线**：RLS（0026）之外，本服务 SQL 自带“已发布、有效、已核对、审核通过、强前置、两端同版本”的条件；
  测试在不受 RLS 约束的超级用户连接上核对这些 SQL（变异“放过草稿版本 / 未核对投影 / 待审标签”靠它发现——只靠 RLS 时服务代码的过滤写错也看不出来）。
  库上夹具把草稿、退役版本先于已发布版本插入，漏写发布条件时会先读到它们。
- **启动断言**：登录角色不是超级用户、没有 BYPASSRLS、继承 `starbuddy_kge_read`、不继承 `starbuddy_kge` 与其他应用角色，SET ROLE 后当前角色是 `starbuddy_kge_read`；注入的连接池同样断言。
- **响应校验**：每个 200 响应组好信封后按契约校验，不合格回 500 且只在日志记实例路径与关键字（库里有超长 includes 的合成节点证明它在起作用）。
- **page.get**：只读快照目录（启动时逐页核对 sha256、头部与 manifest 一致，只有 manifest 列出的页面算已发布），节点必须在当前已发布版本里且有效；
  锁定段、前置与后续、叙述段逐字取页面；教材位置段与地区权重段在读取时按库里当前**已核对**的投影、用编译器同一模板重新生成，
  给了 edition 才按版次填原句，给了 region 只列该地区。`page_version` 取页面头部；提交号按 ADR 12 映射进 `source_version`（`graph:kge-pages@<提交号前 12 位>`），契约字段不变。
- **快照导出**：只读 Git 对象（rev-parse / ls-tree / show），导出前对该提交全部页面跑程序体检（带该提交的 `snapshots/current.yaml`），有阻断拒绝导出；
  先写临时目录再改名，文件 0444、目录 0555，目标已存在即拒绝。
- **DbAtomSource**：投影、地区投影、时政在页面里的键是 `tp-/rp-/ca-` + UUID 的 sha256 前 16 位（不把 UUID 写进页面，体检会拦 UUID）；只收同版本两端都可见的边。
- **MCP**：官方 SDK 2.2.0 的低层 Server；工具名 = operationId，inputSchema 由契约打包；成功时 `structured_content` 是契约信封，失败时 `is_error` + Problem。
  经 HTTP 时挂在知识服务的 `/mcp`（streamable HTTP、无状态、JSON 响应），同一令牌鉴权，DNS 重绑定防护开启；进程内客户端测试。
- 坑：mcp 2.x 的 streamable HTTP 需要 session manager 在应用 lifespan 里运行（挂载子应用时它自己的 lifespan 不会被调用）；
  asyncpg 的 `transaction(readonly=False)` 不会显式发 `READ WRITE`，测试“关掉只读事务也写不了”要手写 `BEGIN TRANSACTION READ WRITE`。

### 与 spec / 任务说明的偏差
1. **服务契约一致性没有现成机制**：`packages/contracts/conformance` 只针对学科插件 API（运行中的适配器），`scripts/check_services.py` 只检查契约文件本身；
   所以一致性测试放在 `services/knowledge/tests/test_service_contract.py`（正例、反例、错误体全部按 OpenAPI 严格校验，严格模式下不跳过），没有改 packages/contracts。
2. **page.get 的教材位置段与地区权重段不是逐字取自页面**：按库里当前已核对的投影重新生成（同一模板）。好处是投影在页面编译后被撤销核对时不会再被返回；代价是这两段可能与该 `page_version` 的仓库原文不同。
3. **快照在启动时整体载入**：换快照要重启（或改配置后滚动重启）；运行中删掉快照目录不影响已载入内容。
4. **MCP 与 HTTP 同一端口**（`/mcp`），没有另开端口；监听地址同一配置。
5. **node.resolve 的低置信复核**只在响应里标 `human_review_required` + `low_confidence`，没有写复核队列（知识服务不持有任何写权限）；由调用方（编排层）按契约只带候选节点入队。
6. **projections / region_weights 超过契约上限（16）时截断**，按版次、年份排序后取前 16。

### 待人确认
1. **契约缺口（不改契约，登记到 v1.0 评审）**：
   a. `PageGetResponse.atom_refs` 的类型只有 node、item、scoring_point、current_affair、textbook_projection；页面引用的边、课标条目、地区投影没有对应类型，本 PR 不返回这三类引用（边的键里有 `>`，也不合 pattern）。
   b. Problem 的 `code` 枚举没有鉴权专用值：401 / 403 用 `INVALID_REQUEST`（与参考假适配器相同），靠 HTTP 状态码区分。
   c. `PageId` 是小写编码，页面仓库的页面 ID = 节点 ID（大写）：本 PR 用“转小写、下划线换连字符”映射（可逆，节点 ID 里没有连字符）；是否写进契约描述由人定。
   d. `ScoringPointView.keyword_family`（字符串数组）与 `partial_credit`（枚举）对应的库列是 jsonb：本 PR 约定 keyword_family 为数组原样、对象按键排序展开各数组值；
      partial_credit_rule 为空对象 = `all_or_nothing`、`{"mode": <枚举值>}` 取该值，其他写法回 500。需要内容侧确认写法。
   e. 提交号映射进 `source_version` 用了 `graph:kge-pages@<前 12 位>`（SourceVersion 的类别只有 rubric / verifier / prompt / items / algo / graph，版本段 ≤ 32 字符放不下 40 位提交号）；是否新增类别（例如 `pages`）属契约变更。
2. **item.search / item.similar 不核对题目准入**：`starbuddy_kge_read` 没有 `platform.item` 的权限（0026 按最小权限），只按打标审核通过过滤；调用方用题前仍须按 `platform.item.admission` 核对。是否给只读角色加 `platform.item(admission)` 的列权限属迁移，由人定。
3. **编译页面里的“前置”包含弱前置边**：PR ① 的模板按 prerequisite 类型取边、不看 strength，而 `node.path` 只走强前置边；页面是否只列强前置由教研定（改模板会改变全部页面，属高风险渲染重建）。
4. **节点—课标承接**（PR ① 待人确认 1）仍未定：`DbAtomSource` 没有承接关系可读，页面的“课标要求”段在库数据下为“（暂无）”。
5. **部署**：知识服务的登录角色（只继承 `starbuddy_kge_read`）由人在演练库创建；快照目录的存放位置、换快照的流程（导出新目录 → 改 `KNOWLEDGE_PAGE_SNAPSHOT` → 重启）写进运维手册；MCP 的内网地址由平台侧网关规划。
6. **MCP SDK 版本**：固定 2.2.0（MIT），它带进来的传递依赖较多（cryptography、PyJWT、opentelemetry-api、httpx2 等，许可证已核，见 README）；本服务不用其 OAuth 与客户端网络功能。是否接受由人定；另一个选择是 MCP 随编排层（冲刺 4）再加。
7. **没有声明 MCP outputSchema**：错误结果的 `structured_content` 是 Problem，与成功信封形状不同，声明后客户端会按 outputSchema 校验失败；需要时可在 v1.0 把两者合成一个 oneOf。
