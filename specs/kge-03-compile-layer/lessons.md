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

## PR ③：兜底检索 + 对照评测 + M1 报表（子代理）
范围：spec 设计要点 6、7、8，待人确认 #5、#8。不改迁移、契约、人设与金标数据；不接触留出集；全部是合成数据。

### 交付
- `services/knowledge/knowledge/retrieval/`：`tokenize`（jieba 0.42.1，MIT，独立 `Tokenizer` 实例、只用词典 HMM=False、搜索引擎模式、NFKC + 小写、去页面标记与停用词）、
  `bm25`（进程内 BM25）、`chunks`（只切公共层）、`pipeline`（`PageFirstRetriever` 查阅路径与上下文包、`ChunkRetriever` 对照路径、`Corpus`）、
  `resolver`（`FullTextResolver`、`CombinedResolver`）、`signals`（补页面信号与 sink）、`vector`（向量检索只留接口）。
- `knowledge/service/tools.py`：`ToolCore` 缺省解析器由 `RuleResolver` 改为 `CombinedResolver`（规则 + 全文），经 PR ② 的 `Resolver` 接口；
  `model_version` 取解析器的版本（`algo:kge-node-resolve@0.2.0`）。契约不变，低置信仍只标复核。
- `knowledge/m1.py`（`python -m knowledge.m1`）：M1 报表，Markdown + JSON。
- `evals/kge-compile/`：`compare_retrieval.py`、合成夹具（金标 12 题、地图、页面快照及其生成脚本）、合成样例报告 `reports/sample-synthetic/`、测试、README（执行手册）。
- 测试：`tests/test_retrieval.py`（18）、`tests/test_m1.py`（9，其中 1 条库上）；`evals/kge-compile/tests/test_compare.py`（16）。
  `make knowledge-test` 共 284 条；新目标 `make kge-compile-eval-test` 已加进 `make check`。
- 变异：`tests/mutations.sh` 新增 28 条（检索 17、M1 5、对照评测 6；共 81 条，约 4.5 分钟），全部被发现。对照评测的变异在副本里复制
  `evals/kge-compile`（其余 evals 仍软链接），在该目录下跑它自己的测试。

### 实现要点
- **BM25**：k1 = 1.2、b = 0.75，idf 用 Lucene 的非负写法 ln(1 + (N − df + 0.5)/(df + 0.5))；查询词去重；分数四舍五入到 6 位后按“分数降序、键升序”排，
  与输入顺序无关（测试有手算）。另有“按 idf 加权的查询词覆盖率”，供解析置信度与页面覆盖判断用。
- **只切公共层**：`build_chunks` 只收 `PageSnapshot`（manifest 列出、哈希核对过）与 `status = published` 的 `Snapshot`（白名单字段），类型写死，
  传别的一律 TypeError；页面按段落切，跳过“教材位置”段（只有原句占位）；节点已撤下的页面整页跳过；原子只切节点（名称、包括、不包括）与课标条目。
  教材原句（投影 quote_text）不切（KGE V1.1 修订 3），地区投影与边不切。
- **查阅路径**：定位（调用方给节点就用，否则规则 + 全文解析）→ 页面五段（定义、课标要求、讲解要点、易错、高频设问；教材位置与地区权重要按版次、
  地区渲染，走 `page.get`，不进上下文包）→ 没有页面时查原子 → 兜底条件：没定位到、置信度 < 0.5、没有页面、页面对查询词覆盖率 < 0.5 →
  BM25 取 3 块（已在包里的来源不重复）→ 预算 4000 字，超出整段丢弃。
- **补页面信号**：`kge-gap-signal/1`，字段白名单（节点、原因、查询摘要、查询词个数、命中来源、最高分）。查询只记 HMAC-SHA256 摘要（截 16 位），
  密钥不落盘、缺省每个实例随机——同一进程内同一问法可聚合，但摘要不能用字典反推（短查询的无密钥哈希可以被穷举）；没有任何学生参数。
- **全文解析置信度**：0.6 × idf 加权覆盖率，覆盖率 < 0.34 不算命中（与规则解析的二元组下限一致）；节点文档 = 名称 + 包括 + 例子，不含“不包括”。
  合并时每个节点取较高置信度，所以规则解析的分数（名称整体出现 0.6–0.9、ID 直引 1.0）原样保留。
- **M1 报表放在 services/knowledge**：三项输入（地图快照的 `curriculum_links`、页面快照 manifest、体检 JSON）的解析与校验都在本包；
  `tools/kge-coverage` 读的是 KGE-01 导入产物与打标报告，拿不到承接关系、页面与体检。百分比用精确分数四舍五入到 0.1%（不经浮点）。
- **对照评测**：金标 YAML `kge-gold-qa/1`（必须写明 `synthetic`）；两条路径同一份语料、同一分词器；正确率判定可插拔（`all_points` 缺省、`majority`、
  `register_judge`）；一致性 = 主问题重复 `--runs` 次 + 同义改写各 1 次的来源集合两两 Jaccard 平均；成本 = 字符数与 token 估算。
  报告只含汇总与题目 ID（测试逐条核对问题、改写、答案要点、切块原文都不在报告里），`synthetic: true` 时头部标“流程样例，不是评测结论”，
  题量 < 50 时写明不足以支撑结论。测试核对样例报告与夹具是最新的（改检索后要重跑样例，命令见 `evals/kge-compile/README.md`）。
- 坑：jieba 0.42.1 导入时用 `pkg_resources`（弃用警告），导入处局部屏蔽；它首次分词会写词典缓存 `jieba.cache`（只有公共词典；审查处理后改写在服务私有目录，见“审查处理”12）。
  `CombinedResolver` 调子解析器时候选上限要取 max(节点数, 调用方上限)，否则没有节点列表的测试桩被截成空。

### 与 spec / 任务说明的偏差
1. **兜底检索只做进程内 BM25**，没有做 Postgres tsvector 版本（任务说明：可选，不必做）；spec #5 的默认写的是 tsvector，按任务说明改为 BM25 首选。
2. **补页面信号没有写进体检报告**：spec 设计要点 6 写“兜底命中记录进体检报告”；体检报告是页面级 CI 产物（`lint_issue` 字段、按页面定位），
   信号是运行期、跨页面的，所以单独成 `kge-gap-signal/1`（JSON Lines），用 `summarize` 聚合后交教研。是否要合进体检报告由人定。
3. **信号还没有接进 HTTP 服务**：知识服务只有 `node.resolve`（契约里没有“取上下文包”的工具），查阅路径目前是 Python 接口
   （`PageFirstRetriever`），给编排层（冲刺 4）与对照评测用；编排层接入时再配 `JsonlSink` 与密钥。
4. **上下文包不含教材位置与地区权重段**：它们要按学生版次、地区渲染（`page.get`），查阅路径没有学生参数。
5. **M1 报表的“课标承接率”在库上数据时恒为“不可计算：承接关系未入库”**（承接关系没有表，见 PR ① 待人确认 1）；文件快照的 `curriculum_links` 可算。
6. **`node.resolve` 的 `model_version` 由 0.1.0 升为 0.2.0**（契约里的示例仍写 0.1.0，只是示例，不改契约）。

### 待人确认
1. **jieba 依赖**（spec #5）：固定 0.42.1（MIT，已登记 README）；它只有源码包、最后发布于 2020 年，词典随包分发。是否接受由人定；替代方案是自写基于词表的最大匹配。
2. **检索参数**：停用词表（`STOPWORDS`）、页面覆盖率下限 0.5、兜底取 3 块、切块路径取 5 块、上下文预算 4000 字、全文置信度系数 0.6 与覆盖率下限 0.34——
   都是工程默认值，**没有用任何金标调过**；真实金标到位后只能用开发集调，不能用金标或留出集调（规则 14）。
3. **补页面信号的存放与保留期**：摘要密钥的管理（每实例随机 vs 部署级固定密钥以便跨实例聚合）、JSON Lines 的存放位置与保留期，由平台与数据负责人定。
4. **一致性指标的口径**：现在按“来源集合”（页面段落或原子）算 Jaccard，页面优先路径天然偏高（同一节点总是给同一组段）；是否改按文字重合度（如字符 n-gram）由评测负责人定。
5. **正确率判定**：缺省“全部要点按子串出现”偏严、不认同义词；教研给同义词表或改用人工判定时经 `register_judge` 接入，名字进报告。
6. **向量检索**（pgvector + BGE-M3 + bge-reranker）：只留接口，引入属新增扩展与本地模型，需人确认；融合方式与权重进对照评测再定。

### 真实 M1：人的工作交接清单
| 事项 | 执行人 | 产出 / 去处 | 依赖本仓的什么 |
|---|---|---|---|
| 编纂规范 v1 签发 | 教研负责人 | `docs/design/kge/codification-math-v1.md` 第 10 节教学内容要求填写、签发；句长上限、群体统计门槛（PR ① 待人确认 2）定稿 | 结构骨架与机器可检规则已写好 |
| 页面仓库 | 执行人（平台） | 按 ADR 12 选托管平台、建仓、配主分支保护与 CI（`tools/kge-pages-template/`），配置编译器推送凭据（不进仓库） | 模板、`python -m knowledge.lint`、`knowledge.compiler`、`knowledge.compiler.snapshot` |
| 数学知识点页撰写与审核 | 教研 | 叙述段编写；高风险页两人审核（ADR 12 #2 的审核类别）；合并后导出页面快照 | 编译器与体检；审核台（release-gate-sprint4） |
| 高风险页审核 | 教研（两人） | 锁定段、前置关系、教材投影变更与全量重编一律高风险，合并前两人签字 | 合并请求说明（差异、依据原子、体检结果） |
| 承接关系入库 | 人定方案（迁移属必停项） | 节点—课标承接的表或列（PR ① 待人确认 1），迁移由人执行；入库后 M1 报表的课标承接率才可在库上计算 | `knowledge.m1`（快照带 `curriculum_links` 时已可算） |
| 金标问答 | 教研 | `evals/kge-compile/gold/<学科>-<地图版本>.yaml`（≥ 50 题、按节点分层、`synthetic: false`），只由教研提交 | `compare_retrieval.py` 与执行手册 |
| 对照评测结论 | 评测负责人 | 跑 `compare_retrieval.py`，按 KGE 第九章口径判断；未达标则切块检索为主路径进入封测 | 报告只含汇总与题目 ID |
| 留出集盲测（无处可标率 < 2%） | 教研负责人，在封存环境 | `bash evals/kge-tagging/run_holdout.sh ...`，只打印汇总；结果填入 M1 验收记录（规则 14，构建方不接触） | KGE-02 已交付的脚本 |
| M1 报表 | 执行人 | `python -m knowledge.m1 --map-snapshot/--database-url ... --page-snapshot ... --lint-report ...`，与留出集结果、对照评测结论一起进 M1 验收记录 | `knowledge.m1` |
| M1 是否达成 | 人 | 课标承接 100%、无处可标 < 2%、知识点页发布率 100%、体检零阻断、对照评测达标；真实 M1 不在本任务通过条件里（后续验收） | — |

## 审查处理（子代理）
范围：按审查意见修 KGE-03 的 `services/knowledge`、`tools/kge-pages-template`、`evals/kge-compile`（阻断 4 条、建议 12 条）。
不改 `db/`（迁移与 DB 测试由主会话改）、`packages/contracts`、`docs/personas`、evals 金标数据；本任务的库上测试只改了
`services/knowledge/tests`（`pgkge.py` 的合成种子加了三个反例角色与三条反例采分点）。全部是合成数据。

### 阻断
1. **page.get 旧版本页面**：`tools.page_get` 在节点核对之前比较快照里页面的 `map_version` 与当前已发布版本，不一致回 404
   `CONTENT_VERSION_NOT_FOUND`（`not_found()`：title = code，不带 detail，与“不存在”同一个错误体）。端到端测试
   `test_page_with_stale_map_version_not_served`。**取舍**：升版到重编合并之间，落后页暂不服务；若要继续服务旧页，
   需要让 `source_version` 反映页面编译时的地图版本（现在写的是当前已发布版本），属契约变更，登记到 v1.0（待人确认 9）。
   M1 报表的发布率随之改口径（建议 14）。
2. **页面仓库 CI 可被合并请求篡改**：`ci/lint.sh` 改为从 `main` 的检出运行，地图快照、人设词表、上一审核版一律取自它自己所在的
   `main` 检出，合并请求头部只经 `HEAD_PAGES_DIR` 提供 `pages/`（头部页面目录里有符号链接即退出码 2）；CI 必须带
   `--require-map-snapshot`，快照或词表缺失退出码 2。检查工具改为固定版本的预构建只读制品：新增 `ci/install-tools.sh`
   （只读 `ci/kge-tools.pin` 两行值、只接 https、先核 sha256 再解包、`pip install --require-hashes -r requirements.lock`，不带任何凭据）。
   GitHub 样例只用 `pull_request`、`permissions: contents: read`、两次检出 `persist-credentials: false`、不引用任何 secrets，
   注释写明不得用 `pull_request_target` 检出头部代码；action 固定到 40 位提交 SHA（占位 `0000…`，注明必须替换）。
   GitLab 样例用 `git worktree` 检出 `main` 再跑它的 `ci/lint.sh`，镜像按 digest 固定（占位）。`BRANCH_PROTECTION.md` 加第 13–18 条
   （CODEOWNERS 与路径规则：`ci/`、`.github/`、`.gitlab/`、`.gitlab-ci.yml`、`snapshots/`、`lexicon/`、`CODEOWNERS` 由平台负责人审核；
   信任边界；无凭据；触发器；固定版本；地图升版顺序），新增 `repo/.github/CODEOWNERS` 样例（团队名占位）。
   测试 `tests/test_template.py`：样例 YAML 解析后断言触发器、无 secrets、SHA 固定、头部只取 `pages/`、体检入口来自 `main`；
   头部改了 `ci/lint.sh`、快照、词表时坏页面照样阻断；安装脚本拒绝占位与非 https。
3. **个人信息检查可绕过**：`check_pii` 对原文与剥掉引用 / 占位后的文字各扫一遍（引用键、原句占位里的个人信息同样拦）；
   头部所有值与键名（含 `atom_refs[].id`）也扫，消息只给字段名；原子 ID 按类型白名单校验（节点 `is_node_id`，其余
   `[A-Za-z0-9_./>:-]{1,200}`），头部与正文引用、原句占位都查。`knowledge.lint --require-map-snapshot`、快照导出缺
   `snapshots/current.yaml` 均退出码 2。`load_snapshot_dir` 载入时逐页跑 `published_page_checks`（头部含 ID 白名单、引用登记、
   缺引用、个人信息、人设与禁语），有阻断即拒绝加载（服务拒绝启动）。审查者的复现写成测试
   `test_review_repro_pii_in_citation_key_blocks_without_snapshot`（无快照体检：header、pii、link 三类阻断，报告不含原文），
   快照侧 `test_snapshot_reruns_page_checks_at_load`。
4. **快照导出不核对 main**：`git merge-base --is-ancestor <提交> refs/heads/<分支>`（缺省 `main`，`--branch` 可指定，分支名校验），
   不在即退出码 2；manifest 记 `branch`；调 git 一律带 `-c core.hooksPath=/dev/null -c core.fsmonitor=false`，读文件改用
   `git cat-file blob <提交>:<路径>`。测试 `test_export_only_from_protected_branch`、`test_export_requires_map_snapshot`。

### 建议（按默认修复）
5. 全量重编请求：`assert_public` 除键名外对所有字符串值跑 `PII_PATTERNS`（与体检同一份 `pii_hits`），命中即拒绝送出，消息不回显值。
6. SymPy：`.` 只允许在两个数字之间；`parse_expr` 的 global_dict 为 Integer、Float、Rational、Symbol、Add、Mul、Pow、Function
   （后四个是 `evaluate=False` 改写与 `auto_symbol` 必需的；Function 生成的调用与逗号生成的 Tuple 在树检查里拒收），`__builtins__` 置空；
   先 `evaluate=False` 解析，遍历树只收加、乘、乘方、符号、有理数、浮点数与 π，限节点 256、深度 32、常数指数 |e| ≤ 64，
   通过后再 `evaluate=True` 解析；代入取值每段限 6 位。验算放进 fork 出的子进程（RLIMIT_CPU 3 秒、RLIMIT_AS 为 fork 时地址空间
   + 512 MB，墙钟 2 秒），超时、超限、子进程异常一律“无法验证”（带 `% check` 的阻断）；正常结论按（式子，取值）在本进程缓存
   （fork 冷启动使体检测试从约 11 秒涨到 39 秒，缓存后约 13 秒）。`2^{2^{2^{20}}}`、`E.n(99999)`、`N(pi,999999)` 有测试。
7. `reader.assert_reader_role`：登录角色与当前角色不能有 CREATEROLE、CREATEDB，登录角色不能是任何 `pg_%` 预置角色的成员；
   server settings 固定 `TimeZone=Asia/Shanghai` 并在启动时核对。库上测试新增 `knowledge_predef`（pg_read_all_data）、
   `knowledge_createdb`、`knowledge_createrole` 三个反例角色（只在测试的一次性库里建）。
8. `rubric.get`：SQL 与 `kge.item_tag` 联查，只返回标签审核通过的题目的采分点；`partial_credit_rule` 有 `mode` 以外的键、
   `keyword_family` 是对象（或数组里有非字符串）时抛 `LossyMapping` 回 500，日志只记异常类名。score > 999 已由响应校验回 500（登记）。
9. 服务与 MCP：`McpAuth` 对 lifespan 以外的所有 scope 生效（websocket 以 1008 关闭，未知类型不回应不下传）；新增 `HostGuard`
   中间件对整个应用（含 `/v0/*`）校验 Host，允许列表与 MCP 的 DNS 重绑定防护同一份（`mcp_server.allowed_hosts`，精确值或
   `<主机>:*`，端口只收数字）；`check_host` 对主机名与旧式 IPv4 写法（`0`、`0x0`）解析，结果有 unspecified 或解析不了即拒绝；
   修正 `[::1]` 生成 `[[::1]]:*`；MCP `list_tools` 只列 7 个公共工具，个人层三个在 MCP 回 404（与未知工具同），HTTP 仍回 501。
10. 快照载入：目录、`pages/`、`pages/kp/`、manifest 与每页 lstat 拒绝符号链接，属组或其他用户可写即拒绝。manifest 签名（HMAC）未实现（待人确认 2）。
11. 体检消息不回显页面原文：缺引用段、段落外内容、公式（含 `detail.formula`）、SymPy 等式两边、LaTeX 命令名、失效链接目标、
    外部链接、未登记头部键名、非法类型或 ID 都不再回显；只回显已校验的节点 ID 与原子键、快照与词表里的内容、人数。
    测试 `test_messages_never_echo_page_text` 用探针文字扫整份报告。
12. jieba 缓存：`tmp_dir` 指向 `KNOWLEDGE_CACHE_DIR`（缺省 `services/knowledge/.cache`，新增 `services/knowledge/.gitignore`）下的
    `jieba/`，建成 0700，必须属于本进程用户、不是符号链接、属组与其他用户不可写。
13. 供应链：pip-tools 7.6.1（BSD-3-Clause，只装在 `.venv`）生成 `requirements.lock`、`requirements-dev.lock`（逐包 sha256，
    与 `.venv` 现装版本一致，`pip install --require-hashes --dry-run` 通过）；README 与 Makefile 的安装提示改为 `--require-hashes`。jieba 是否接受仍由人定。
14. M1：知识点页发布率主口径的分子排除 `map_version` 落后的页（它们 `page.get` 不服务），并列 `including_stale` 口径（Markdown 多一行，
    注明“仅供对照”）；测试更新。
15. `node.resolve` 的 `source_version` 附 `algo:kge-tokenize@<TOKENIZER_VERSION>`（契约 SourceVersion 允许 `;` 多段与 algo 类别；
    版本段不收 `+`，换成 `.`：`jieba-0.42.1.search-nohmm.sw1`），不改契约。
16. 本节。变异：`tests/mutations.sh` 新增 40 条（审查处理 27 条 + 库上 13 条，共 121 条），模板目录在副本里复制一份以便变异 CI 样例。
    首轮变异漏检 7 条，已补测试：通配地址字面值走快速路径（消息核对）；SymPy 失败消息不回显式子；`check_tree` 直接拒收函数与元组；
    安装脚本按去注释后的代码核对 `--require-hashes`；调 git 的每次调用都带安全参数（spy `subprocess.run`）；`scoring_points` 在不受 RLS
    约束的连接上也不返回待审题目的采分点。原“MCP 关闭 DNS 重绑定防护”变异因 `HostGuard` 在外层兜住而等价，改为变异
    `transport_security` 的开关（单测核对）。

### 与审查意见的偏差
1. **Host 不合法回 400**（`INVALID_REQUEST` Problem），不是 MCP SDK 的 421；`HostGuard` 在最外层，`/mcp` 的坏 Host 也先被它拦下。
2. **`check_host` 解析不了的主机名也拒绝**（比“解析结果为 unspecified 才拒绝”更严）：解析不了本来也监听不上。
3. **SymPy 子进程用 fork**，不是新解释器（新解释器每次导入 SymPy 约 0.5 秒）；没有 fork 的平台一律“无法验证”。
4. **`keyword_family` 数组里有非字符串也 fail closed**（原实现静默丢弃）。
5. **快照载入时的复查不含页面间链接与模板**：这两项要完整页面集与地图快照，已在导出前的体检里查。
6. **地图快照取自 `main`** 带来升版顺序约束：先合并只改 `snapshots/` 的请求，再合并重编请求（`BRANCH_PROTECTION.md` 第 18 条）。
7. **GitLab 的 `CI_JOB_TOKEN` 无法去掉**：样例不显式使用它，清单要求把作业令牌访问范围限定为本项目；`.gitlab-ci.yml` 与 GitHub 工作流文件
   本身来自合并请求，只能靠 CODEOWNERS 或平台的“必需工作流 / 流水线执行策略”兜住（写进清单第 16 条）。
8. **`knowledge.lint` 本身不拒绝符号链接**：由 `ci/lint.sh` 在调用前拒绝头部页面目录里的符号链接；快照载入另有 lstat 检查。

### 待人确认（更新）
1. **历史 rubric_version 查询**：`rubric.get` 只查当前已发布地图版本里的采分点；要查历史地图版本的 rubric，需改 0026 的 RLS
   放开非发布版本的 `scoring_point`（迁移与权限变更，必停）。
2. **manifest 签名与密钥**：快照 manifest 的 HMAC 签名属于密钥管理（密钥存放、轮换、导出机与服务间分发），本次不实现。
3. **全量重编的出境与 T8 供应商**：请求只含公共原子且已扫个人信息，但 T8 的真实供应商、是否境外（数据出境路径）由人定（必停项“新增模型供应商或数据出境路径”）。
4. **jieba 与 mcp 依赖**：仍待人确认是否接受（jieba 0.42.1 只有源码包、2020 年后未发布；mcp 2.2.0 传递依赖较多）；pip-tools 只在开发环境。
5. **契约 v1.0 登记项**（本次不改契约）：SourceVersion 新增 `pages` 类别（页面快照提交号现在借用 `graph:kge-pages@<前 12 位>`）；
   Problem.code 增加 `UNAUTHENTICATED` / `FORBIDDEN`（现在 401 / 403 借用 `INVALID_REQUEST`）；`PageGetResponse.atom_refs` 补
   `edge`、`curriculum_item`、`region_projection` 类型；写明契约 `page_id` 与节点 ID 的映射关系（小写、下划线换连字符）；
   写明 `textbook_projection:tp-<哈希>` 等派生键只用于溯源、不能反查数据库行；`page.get` 服务旧版页面时 `source_version` 如何反映编译版本（阻断 1 的取舍）。
6. **全量重编 SYSTEM_PROMPT 不跑 evals/persona**：它是编译任务的指令（要求页面人设中立），不是任何角色的人设 Prompt，也不面向学生；
   按 CLAUDE.md“人设 Prompt 变更跑人设回归”的范围不适用。改它仍需审查，是否另设编译 Prompt 的回归由人定。
7. **采分点的写法**：`partial_credit_rule` 除 `mode` 外是否有其他条件、`keyword_family` 的分组对象是否要进契约，由内容侧确认；
   score > 999 的采分点现在由响应校验回 500。
8. **页面仓库检查工具制品**：StarBuddy 发布流程打包 `services/knowledge`（含 `requirements.lock`）、放到内网匿名只读的 https 地址、
   把地址与 sha256 写进 `ci/kge-tools.pin`，由平台定；CI 样例里 action 的提交 SHA、镜像 digest、CODEOWNERS 团队名都是占位。
9. **旧版页面是否继续服务**：见阻断 1；默认升版到重编合并之间回 404。
