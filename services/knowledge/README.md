# 知识服务（services/knowledge）

KGE-03 编译层（`specs/kge-03-compile-layer/spec.md`）。本目录现在包含 **PR ①**（页面格式、编译器、程序体检含 SymPy）与
**PR ②**（知识服务读工具 HTTP + MCP、页面快照导出与 `page.get`、编译器的数据库数据源）与
**PR ③**（页面优先检索 + 全文兜底、`node.resolve` 接全文检索、补页面信号、M1 报表）；
页面仓库模板在 `tools/kge-pages-template/`，编纂规范骨架在 `docs/design/kge/codification-math-v1.md`，
对照评测脚本在 `evals/kge-compile/`。实现记录与待人确认见同 spec 目录的 `lessons.md`。

本包只读公共层（kge.* 的节点、边、课标、投影、题目标签、采分点），不读写任何个人层表（规则 1、2、13）：
数据库只用迁移 0026 的只读角色 `starbuddy_kge_read`，页面只读编译器导出的只读快照目录，不在线访问 Git。

| 模块 | 说明 |
|---|---|
| `knowledge/atoms.py` | 公共层原子与地图快照：白名单字段、原子键与哈希、快照差异；数据源接口 `AtomSource`（文件快照；数据库实现见 `service/reader.py` 的 `DbAtomSource`） |
| `knowledge/nodeid.py` | 节点 ID 正则与学科前缀（测试核对与 `docs/design/kge/check_node_id.py`、迁移 0011、知识服务契约一致） |
| `knowledge/pages/` | 页面格式：YAML 头部 + 三种段落的固定标记；解析与序列化（规范写法往返逐字节稳定） |
| `knowledge/compiler/` | 编入（受影响页、优先级 = 风险 × 影响）→ 渲染重建（叙述段逐字保留）/ 全量重编（经模型路由，T8 × L0）→ 合并窗口 → 本地输出与合并请求说明 |
| `knowledge/lint/` | 程序体检：结构、引用、过期、公式（SymPy）、个人信息与群体统计、人设中立与适龄；结果字段对应 KGE 第七章 `lint_issue` |
| `knowledge/compiler/snapshot.py` | 导出已发布页面快照：页面仓库指定提交 → 只读快照目录（`manifest.json` 带 40 位提交号，每页 `page_version`、`map_version`、`sha256`）；导出前体检，有阻断拒绝导出 |
| `knowledge/service/contract.py` | 知识服务契约（`packages/contracts/openapi/knowledge-service.v0.9.yaml`）的加载与严格校验；工具清单由契约 paths 生成；MCP 的 inputSchema 由同一 schema 打包 |
| `knowledge/service/tools.py` | 工具的唯一实现层（HTTP 与 MCP 共用）：调用方准入 → 个人层 501 → caller 与令牌一致 → 请求校验 → 执行 → 统一信封 → 响应校验（不合契约回 500） |
| `knowledge/service/reader.py` | 只读 kge.*：`starbuddy_kge_read` 连接与启动时的角色断言；SQL 自带发布、有效、已核对、审核通过、强前置的条件（RLS 之外的第二道防线）；`DbAtomSource`（PR ① `AtomSource` 的数据库实现） |
| `knowledge/service/pages.py` | 读页面快照（逐页核对哈希与头部，只有 manifest 列出的算已发布）；`page.get` 按版次、地区渲染 |
| `knowledge/service/resolve.py` | `node.resolve` 的规则解析（节点 ID 直引 + 名称 / includes 关键词）与 `Resolver` 接口；缺省解析器是 PR ③ 的 `CombinedResolver`（规则 + 全文） |
| `knowledge/service/app.py`、`mcp_server.py`、`config.py` | FastAPI 应用（`POST /v0/<工具名>`）、MCP（streamable HTTP 挂在 `/mcp`，工具名 = operationId）、环境变量配置 |
| `knowledge/retrieval/` | PR ③ 页面优先检索 + 全文兜底：`tokenize`（jieba 独立实例、只用词典、搜索引擎模式、停用词）、`bm25`（进程内 BM25）、`chunks`（只切公共层：已发布页面快照的段落 + 已发布原子）、`pipeline`（查阅路径与带引用的上下文包；对照路径 `ChunkRetriever`）、`resolver`（`node.resolve` 的全文检索与合并）、`signals`（补页面信号）、`vector`（向量检索只留接口） |
| `knowledge/m1.py` | PR ③ M1 报表：课标承接率、知识点页发布率、体检阻断数；留出集无处可标率只写来源与执行人 |
| `knowledge/m2.py` | 冲刺 5-K M2 报表：核心节点内容完整率（本模块计算）+ 数学、道法准入汇总（汇总已有报告与执行人填的准入记录，不重算） |

## 常用命令
```bash
python3 -m venv .venv && .venv/bin/pip install --require-hashes -r requirements-dev.lock   # 在本目录执行（.venv 已被 .gitignore 忽略）
# 只装运行依赖（部署、页面仓库 CI 的检查工具制品）：pip install --require-hashes -r requirements.lock
make knowledge-test knowledge-mutation                                       # 在仓库根目录：单测 + 端到端 + 变异
make knowledge-cache-mutation                                                # PLT-19 公共层只读缓存的变异（约 1–2 分钟）

# 程序体检（页面仓库 CI 用同一条命令，见 tools/kge-pages-template/repo/ci/lint.sh；CI 带 --require-map-snapshot）
.venv/bin/python -m knowledge.lint tests/fixtures/pages --map-snapshot tests/fixtures/map-v1.yaml --require-map-snapshot \
  --baseline tests/fixtures/pages --json
.venv/bin/python -m knowledge.lint --export-lexicon /path/to/lexicon.json    # 导出人设词表给页面仓库

# 编译（渲染重建 + 合并窗口），只写本地目录，不推送任何远程仓库
.venv/bin/python -m knowledge.compiler --pages tests/fixtures/pages --old-snapshot tests/fixtures/map-v1.yaml \
  --new-snapshot /path/to/map-v2.yaml --out /tmp/kge-out --now 2026-09-29T10:00:00+08:00 --no-hold-report
#   对账报告必填：--hold-report <审核台 reconcile-pages 的报告> 或（只用于开发与测试）--no-hold-report；上例是开发用法，正式窗口必须给报告
```

**页面对账（G 流冲刺 3，`specs/release-gate-sprint4` 第 6 节）**：编译器与快照导出都必须给对账报告（`kge-pages-reconcile/1`，
审核台 `python -m review_console reconcile-pages` 产出），开发与测试时显式 `--no-hold-report`。
- 报告的 `held_pages`：编译器的合并窗口跳过这些页面；快照导出不发布这些页面在本提交的内容，沿用 `--previous`（上一份已发布快照）
  里的原文与清单条目（条目加 `source_commit`，`page.get` 的 `source_version` 用它，`page_version` 仍能对回原来的提交）；上一份里没有就不导出该页。
  有被扣页面时，体检按该提交在主分支上的完整页面集合做，被扣页面自己的阻断不算；manifest 记下 `held_pages`。
- **有效期**：报告的 `exported_at`（所依据的托管平台合并记录导出时刻）早于“当前时刻减有效期”即拒绝（退出码 2），在将来超过 5 分钟也拒绝。
  有效期缺省 24 小时（一个合并窗口周期），用 `--hold-max-age-hours` 改；编译器按 `--now` 计，快照导出按当前时刻计。
- 报告格式不对、`ok` 与 `held_pages` 矛盾、超过 10 MB：退出码 2，不当作没有失败。
- 已知限制：沿用的旧页带旧的 `map_version`，地图升版后 `page.get` 对它回 404，直到对账通过、重新导出。
退出码：`knowledge.lint` 0 无阻断、1 有阻断、2 参数或输入错误（快照、词表读不了一律失败，不当作通过；带 `--require-map-snapshot` 时没给快照也是 2）；
`knowledge.compiler` 0 完成、1 有合并请求体检阻断、2 参数或输入错误。

```bash
# M1 报表（Markdown + JSON，同样输入逐字节相同）；库上数据用 --database-url 与 --subject，课标承接率会如实报“不可计算：承接关系未入库”
.venv/bin/python -m knowledge.m1 --map-snapshot tests/fixtures/map-v1.yaml --page-snapshot /srv/kge/snapshots/<提交号> \
  --lint-report /path/to/lint.json --out /tmp/kge-m1
# 退出码：0 生成成功；2 参数或输入错误（体检 JSON 的汇总与明细不一致也算输入错误）
```

```bash
# 节点表导出（KGE-12 A，specs/kge-12-weknora-integration）：给知识库供应链中台打 KGE 节点 ID 用（中台需求单附录 A.2）。
# 只出已发布版本的生效节点与已核对投影，不带教材原句；--publishers 是教研给的“版本编码 → 出版社全称”对照表（可选）
.venv/bin/python -m knowledge.export.node_table --map-snapshot tests/fixtures/map-v1.yaml --out /tmp/kge-node-table [--publishers <对照表.yaml>]
# 输出 node-table.jsonl、node-table.csv、node-table.manifest.json（行数与 sha256）；同样输入逐字节相同。退出码：0 成功；2 参数或输入错误
```

```bash
# 导出已发布页面快照（合并窗口时对页面仓库主分支的指定提交执行；只读 Git 对象，不改工作区、不访问远程）
.venv/bin/python -m knowledge.compiler.snapshot --repo /path/to/kge-pages --commit main --out /srv/kge/snapshots/<提交号> [--branch main] \
  --hold-report /path/to/reconcile.json --previous /srv/kge/snapshots/<上一份>   # 开发时可用 --no-hold-report（见上“页面对账”）
# 退出码：0 完成，1 体检阻断（不产生任何输出目录），2 参数、仓库或页面错误——含提交不在受保护分支上
#   （git merge-base --is-ancestor <提交> refs/heads/<分支>）、提交里没有地图快照 snapshots/current.yaml；目标目录已存在即拒绝（快照不可变）。
# 调 git 一律带 -c core.hooksPath=/dev/null -c core.fsmonitor=false，读文件用 git cat-file blob；manifest 记下分支名

# 知识服务（缺省只监听 127.0.0.1:8790）
KNOWLEDGE_ENV=dev KNOWLEDGE_DATABASE_URL=... KNOWLEDGE_TOKEN_ORCHESTRATOR=... KNOWLEDGE_PAGE_SNAPSHOT=/srv/kge/snapshots/<提交号> \
  .venv/bin/python -m knowledge.service
```

## 知识服务：配置与部署要点
| 环境变量 | 说明 |
|---|---|
| `KNOWLEDGE_ENV` | `dev` 或 `test`；本服务不连接 pilot 或生产（规则 9） |
| `KNOWLEDGE_DATABASE_URL` | asyncpg 连接串。登录角色**只继承** `starbuddy_kge_read`（例：`CREATE ROLE knowledge_app LOGIN IN ROLE starbuddy_kge_read`，由人按 `db/bootstrap/roles.sql` 的约定创建）。连接建立后服务执行 `SET ROLE starbuddy_kge_read`，会话默认只读事务、`TimeZone=Asia/Shanghai`（时政有效期按北京时间）；启动时断言登录角色不是超级用户、没有 BYPASSRLS、CREATEROLE、CREATEDB，不继承 `starbuddy_kge` 或其他应用角色，也不是任何 `pg_` 预置角色（`pg_read_all_data` 等）的成员，不满足即拒绝启动 |
| `KNOWLEDGE_TOKEN_BFF` / `_ORCHESTRATOR` / `_LEARNING` | 服务间令牌（≥ 32 字符、各不相同、至少配一个）。令牌决定 caller，请求体 `context.caller` 必须一致（否则 403）；`node.resolve` 只给编排层，个人层工具没有 bff。学生与家长没有令牌（401） |
| `KNOWLEDGE_PAGE_SNAPSHOT` | 页面快照目录。不设：`page.get` 回契约规定的 501；设了但目录不存在或不合格：拒绝启动。换快照 = 导出新目录、改配置、重启（快照在启动时整体核对后载入内存）。载入时：目录、manifest 与页面文件不能是符号链接、不能对属组或其他用户可写；逐页再跑头部（含原子 ID 白名单）、引用登记、缺引用、个人信息、人设与禁语检查，有阻断即拒绝启动。manifest 签名未实现（待人确认） |
| `KNOWLEDGE_PAGE_SNAPSHOT_POINTER` | （KGE-06，与上一项二选一）快照指针文件的绝对路径：文件里一行快照目录的绝对路径，不能含 `..`（指针文件不能是符号链接、不能组可写，它所在的目录也不能对属组或其他用户可写）。启动时按它载入；换快照 = 导出新目录、**先把新路径写进同目录的临时文件、再 `rename` 覆盖指针文件**（原子替换，不会读到半行）、给进程发 `SIGHUP`：新快照按上一行的全部核对（另加签名；manifest.json 以 O_NOFOLLOW 打开、对描述符 fstat 核对后只读一次，验签与解析用同一份字节）通过才原子切换，失败保留旧快照、失败计数加一（`app.state.knowledge["snapshot"].stats`，日志只记原因）。热切换期间的请求整个用开头取到的那一份快照。没配置指针文件时也装 `SIGHUP` 处理器，只记一条日志、快照不变；关停时等进行中的切换做完 |
| `KNOWLEDGE_PAGE_SNAPSHOT_ROOT` | （可选，绝对路径）快照根目录：配置时载入的快照目录（启动与热切换，含指针指向的目录）解析符号链接后必须在根目录里面，否则不载入 |
| `KNOWLEDGE_SNAPSHOT_HMAC_KEY` | （可选，≥ 32 字符）manifest 签名密钥，**只留接口**（`knowledge/service/hotswap.py` 的 `ManifestVerifier`）。不设：按现状不验签，每次载入记一条“未验签”日志并计数。设了：按占位格式验 `manifest.json.hmac-sha256`（HMAC-SHA256(密钥, manifest.json 原始字节) 的十六进制），缺或不符即拒绝载入；导出器还不写签名文件，签名格式、密钥保管与轮换待人确认（T30、T31） |
| `KNOWLEDGE_HOST` / `KNOWLEDGE_PORT` | 缺省 `127.0.0.1:8790`（契约 servers）；不接受 `0.0.0.0`、`::` 等通配地址，主机名与旧式写法（如 `0`）先解析，结果是通配地址或解析不了一律拒绝；需要内网地址时显式配置具体地址。MCP 与 HTTP 同一监听地址；整个应用（`/v0/*` 与 `/mcp`）按同一份 Host 允许列表校验（不在列表回 400），MCP 另有 DNS 重绑定防护 |
| `KNOWLEDGE_READ_CACHE` / `_SIZE` / `_MAX_BYTES` / `_LOG_EVERY` | （PLT-19 第一步，`knowledge/service/cache.py`）公共层只读缓存开关：`1` 开，`0` 或不设关（**缺省关**，关时代码路径、响应与日志与原来相同）。容量两道上限，任一超出按 LRU 淘汰：条目数缺省 1024（1–10000）、总字节数缺省 64 MiB（64 KiB–1 GiB，按条目 JSON 长度估算）；单条超过总字节上限的 1/64（缺省 1 MiB）不入缓存。不设 TTL（键含版本）。数值只收 ASCII 十进制数字，不合格拒绝启动。只缓存 `node.get`、`node.path`、`page.get` 的成功结果；键 = 工具 + 学科 + `map_version_id` + 页面快照 commit 与快照代号（仅 `page.get`）+ 入键参数（edition、region 等；契约有缺省值的先补缺省值，如 `max_depth`=3；context 不入键）；每请求仍先查已发布版本。`page.get` 另有三种结果不入缓存：请求途中快照被换掉、没有任何教材投影、单条过大。参数里有学生 ID 类字段名时不缓存并告警（只记类别）。单进程单事件循环，不加锁（理由见 cache.py） |
| `KNOWLEDGE_CACHE_DIR` | 服务私有的运行时目录（缺省 `services/knowledge/.cache`，已被忽略）；jieba 的词典缓存写在 `<目录>/jieba/`（0700，必须属于服务进程用户、不是符号链接），不用系统共享的临时目录 |

- 鉴权照学习服务内部接口：Bearer 令牌 sha256 后常量时间比较、全部比完再取结果；请求体限 64 KB，JSON 拒绝 NaN / Infinity 与重复键。
  `/mcp` 前置鉴权对 lifespan 以外的所有 scope 生效（websocket 等一律拒绝）；MCP 只列、只接 7 个公共层工具，个人层三个只在 HTTP 回 501。
- `page.get` 只服务 `map_version` 与当前已发布地图版本一致的页面：升版到重编合并之间，落后页回 404 `CONTENT_VERSION_NOT_FOUND`（与“不存在”同一个错误体）。
- `rubric.get` 只返回已发布版本里、题目标签审核通过的题目的采分点（SQL 与 `item_tag` 联查）；库里的写法映射到契约会丢信息时
  （`partial_credit_rule` 有 `mode` 以外的键、`keyword_family` 是对象或含非字符串）回 500，日志只记异常类名。
- `node.resolve` 的 `source_version` 附分词器版本：`graph:<学科>-map@<版本>;algo:kge-tokenize@<TOKENIZER_VERSION，非编码字符换成点>`。
- 日志只记 request_id（UUID 形状才记）、caller、工具名、状态码、错误码、耗时；不记请求体（`node.resolve` 的文本可能来自学生，规则 13）。
  只读缓存开着时（PLT-19）：访问日志对 `node.get`、`node.path`、`page.get` 多一个 `cache` 字段（`hit` / `miss` / `bypass`）；
  logger `starbuddy.knowledge.cache` 每 `KNOWLEDGE_READ_CACHE_LOG_EVERY` 次缓存判断打一条计数日志
  （`cache_stats`：按工具的 hits、misses、evictions、bypass、not_stored、entries、bytes，另有 requests、entries、bytes、generation），只有数字、不含请求参数；
  请求参数里有学生 ID 类字段名时记一条 warning（工具名与命中的类别如 `student`，不记原始键名与值）。同一份计数在 `app.state.knowledge["cache"].snapshot()`。
  没开时日志与原来相同。不改契约文件。
- 热切换与缓存（PLT-19）：同一个 commit 的两份快照内容可能不同（对账扣页 `held_pages` 不同时，被扣页沿用上一份快照的内容），
  所以 `page.get` 的缓存不能只靠 commit 区分快照。前提与做法：热切换（`SnapshotSwapper.install`）必须在同一段同步代码里先换快照、
  再换快照代号并清空 `page.get` 条目（清空是正确性所必需的一层）；键里带快照代号；入缓存前核对当前快照仍是请求开头取到的那一份。
  绕过 `install` 直接改 `ToolCore.pages` 既不换代号也不清空：换成同 commit 的快照时，旧条目仍可能被命中。缓存开着时只能经 `install` 换快照。
- 测试：`tests/pgkge.py` 起一次性 Postgres（做法同 `services/learning/tests/pgharness.py`），执行 bootstrap 角色与全部迁移、灌合成 kge 数据；
  `make knowledge-test CONTRACT_STRICT=1` 时没有 Postgres 直接失败，非严格模式下库上测试跳过。

全量重编只有 Python 接口（`knowledge.compiler.full.full_recompile`），经 `services/learning/learning/model_router`（接入方式同
`evals/persona/diandian/compare.py`：把 `services/learning` 加进 `sys.path`）；测试只用路由表 test 环境的进程内假供应商，不联网、不读凭据。

## 依赖与许可证（规则 10）
| 包 | 版本 | 许可证 | 用途 |
|---|---|---|---|
| sympy | 1.14.0 | BSD-3-Clause | 体检的等式验算 |
| mpmath | 1.3.0 | BSD-3-Clause | SymPy 的依赖 |
| pyyaml | 6.0.3 | MIT | 页面头部与快照 |
| httpx | 0.28.1 | BSD-3-Clause | 模型路由的供应商适配器（学习服务已登记同版本）；传递依赖 anyio（MIT）、httpcore（BSD-3）、h11（MIT）、idna（BSD-3）、certifi（MPL-2.0，只作 CA 证书数据，不修改）、typing_extensions（PSF-2.0） |
| fastapi | 0.141.1 | MIT | 知识服务 HTTP（与学习服务同版本）；传递依赖 starlette 1.7.0（BSD-3）、pydantic 2.13.5 / pydantic_core（MIT）、annotated-types（MIT）、annotated-doc（MIT）、typing-inspection（MIT） |
| uvicorn | 0.54.0 | BSD-3-Clause | 知识服务进程（与学习服务同版本）；传递依赖 click（BSD-3） |
| asyncpg | 0.31.0 | Apache-2.0 | 只读数据库连接（psycopg 3 为 LGPL-3.0，不引入） |
| jsonschema / referencing | 4.26.0 / 0.37.0 | MIT / MIT | 契约严格校验；传递依赖 jsonschema-specifications（MIT）、rpds-py（MIT）、attrs（MIT） |
| mcp / mcp-types | 2.2.0 / 2.2.0 | MIT / MIT | MCP 官方 Python SDK（spec 待人确认 #4）。传递依赖：httpx2 / httpcore2 2.13.1（BSD-3）、sse-starlette 3.5.0（BSD-3）、python-multipart 0.0.32（Apache-2.0）、PyJWT 2.15.1（MIT）、cryptography 50.0.1（Apache-2.0 或 BSD-3）、cffi 2.1.1（MIT-0）、pycparser 3.0（BSD-3）、opentelemetry-api 1.45.0（Apache-2.0）、truststore 0.10.4（MIT）。SDK 的 OAuth、stdio、客户端网络功能本服务不用；只用低层 Server、streamable HTTP 与测试用的进程内客户端 |
| jieba | 0.42.1 | MIT | 兜底检索与 `node.resolve` 全文检索的中文分词（spec 待人确认 #5）。只有源码包（纯 Python，安装时本地构建），自带词典；没有运行时依赖（导入时若有 setuptools 会用其 `pkg_resources` 读词典，否则按文件路径读）。首次分词在服务私有目录（`KNOWLEDGE_CACHE_DIR`，缺省 `.cache/jieba/`）写一份词典缓存 `jieba.cache`（只含公共词典，不含任何查询），不用系统临时目录 |
| prometheus-client | 0.26.0 | Apache-2.0 AND BSD-2-Clause（PyPI 元数据；BSD-2 部分是包内附带的 decorator.py） | LT-05 可观测性：`/metrics`（`knowledge/service/obs.py`）；无运行依赖 |
| pytest（开发） | 9.1.1 | MIT | 单测；传递依赖 pluggy（MIT）、iniconfig（MIT）、packaging（Apache-2.0 或 BSD-2）、Pygments（BSD-2） |
| pip-tools（只在 .venv 里生成锁文件） | 7.6.1 | BSD-3-Clause | 生成带哈希的 `requirements.lock` / `requirements-dev.lock`（`pip-compile --generate-hashes`）；不是运行依赖，不进锁文件 |

供应链：`requirements.lock`（运行）与 `requirements-dev.lock`（运行 + pytest）由 `requirements*.txt` 用 pip-compile 生成，逐包带 sha256；
安装一律 `pip install --require-hashes -r <锁文件>`。改 `requirements*.txt` 后必须重新生成锁文件并复核许可证（命令写在锁文件头部）。

LaTeX 解析没有引入 `sympy.parsing.latex`（需要 antlr4 或 lark 运行时，属新增依赖）：体检自带一个 LaTeX 子集转换器
（`knowledge/lint/mathcheck.py`，分式、根式、乘方、下标变量、四则、括号、π），转换结果先过字符与标识符白名单再交给 SymPy。
SymPy 加固：`.` 只允许在两个数字之间；`parse_expr` 的全局名字只给最小集合、不给 builtins；先 `evaluate=False` 解析并遍历表达式树
（只收加、乘、乘方、符号、有理数、浮点数与 π，限节点数 256、深度 32，常数指数绝对值 ≤ 64）；验算放进 fork 出的子进程
（限 CPU 3 秒、地址空间 +512 MB，墙钟 2 秒），超时或超限按“无法验证”处理，带 `% check` 的即阻断。体检消息不回显页面原文。

## 兜底检索与补页面信号（PR ③）
- 查阅路径：定位节点 → 读已发布页面 → 按需查原子 → 兜底检索（没定位到、置信度 < 0.5、节点没有页面、页面对查询词的 idf 加权覆盖率 < 0.5）→
  上下文包（每段带来源：页面 = 页面 ID + `page_version` + 段落；原子 = 原子键 + 原子哈希；包里带页面快照提交号与地图版本）。
- 全文检索：jieba 分词 + 进程内 BM25（k1 = 1.2、b = 0.75、Lucene 的非负 idf；同分按键排序，结果确定）。没有做 Postgres tsvector 版本。
- `node.resolve`：`ToolCore` 的缺省解析器改为 `CombinedResolver`（规则 + 全文，每个节点取较高置信度），`model_version` 为
  `algo:kge-node-resolve@0.2.0`；全文单独给出的置信度 = 0.6 × idf 加权覆盖率（< 0.34 不算命中），低置信仍只标复核。契约不变。
- 切块只收 `PageSnapshot` 与 `status = published` 的 `Snapshot`，类型写死；教材位置段、教材原句、地区投影不切。
- 补页面信号（`kge-gap-signal/1`）：节点、原因、查询的 HMAC-SHA256 摘要（截 16 位，密钥不落盘、缺省每个实例随机）、查询词个数、
  命中来源、最高分；**不记原始查询文本与分词结果，不记任何学生标识**。去处：`MemorySink` 或 `JsonlSink`（部署方放在服务日志目录）；
  `signals.summarize` 按节点与原因聚合给教研排补页面的优先级。本 PR 没有把信号接进 HTTP 服务（`node.resolve` 只解析节点），
  编排层调用查阅路径时再接。
- 向量检索（pgvector + BGE-M3 + bge-reranker）只留接口（`retrieval/vector.py`），引入需人确认（新增扩展与本地模型）。

## M2 报表（冲刺 5-K，`specs/kge-m2-gate-sprint5`）
每科一页（数学 `aya`、道法 `zhixing`），Markdown + JSON，同样输入逐字节相同；不含时间、本机路径与学生数据。**不下总结论**：
每项给数值、门槛、判定（达到 / 未达到 / 不可计算 / 待填）与来源文件 sha256，末尾列出全部未达到、不可计算与待填项，过门由人定。
缺输入一律“待填”，不当作 0；输入格式错误退出码 2。

```bash
make kge-m2 M2_SUBJECT=aya M2_INPUTS=/path/to/inputs M2_OUT=/path/to/out     # 在仓库根目录；学科只收 aya / zhixing，路径不含空格与单引号
.venv/bin/python -m knowledge.m2 --subject zhixing --inputs <目录> [--kappa-report <文件> ...] --out <目录>   # 单独参数优先于输入目录
```

| 输入目录里的文件 | 来源 | 进报表的项 |
|---|---|---|
| `map-snapshot.yaml` / `.json` | 已发布地图快照，与 M1 用的是同一个文件：`knowledge.atoms` 的快照白名单格式，**不加任何字段**（核心节点另给清单） | 核心节点内容完整率、核心节点页面发布率 |
| `core-nodes.yaml` / `.json` | `kge-m2-core-nodes/1`：`subject`、`map_version`（必须与快照一致）、`node_ids`（必须都在快照里，不重复）；教研签字。没有这个文件报“不可计算：核心节点未标注” | 同上 |
| `items.json` / `.yaml` | `kge-m2-items/1`：`kge.item_tag` 与 `platform.item.admission` 的只读导出，每题只有 `item_id, main_node_id, cognitive_level, admission, tag_review_status`（多一个字段即拒收） | 同上 |
| `page-snapshot/` | 已发布页面快照目录（`knowledge.compiler.snapshot` 导出，按 `page.get` 同一套逐页核对） | 同上 |
| `m1-report.json` | `python -m knowledge.m1` 的 JSON | 课标承接率、知识点页发布率、体检阻断数 |
| `kappa-report.json` | `evals/kge-tagging/kappa.py --subject <学科> --json` | kappa ≥ 0.7 |
| `top1-report.json` | `evals/kge-tagging/top1.py --subject <学科> --json --summary-only`（金标集，不是留出集） | Top-1 ≥ 90%（`map_version` 须合 主.次.修订；与本地图版本不一致报不可计算） |
| `daofa-grading-report.json` | `evals/daofa-grading/grading.py --json --summary-only`（仅道法） | 逐点一致率 ≥ 90%（`label = 流程样例` 报不可计算） |
| `shadow-compare.json` | `evals/sie-shadow/compare_shadow.py` 的 `compare-shadow.json`（只取汇总数字，分歧清单里的学生编码不进报表） | 影子评审比对流程可用（`sample: true` 报不可计算） |
| `attestation.yaml` / `.json` | 执行人填的 `kge-m2-attestation/1`（下例） | 留出集无处可标率、影子评审就位、C 级、监护人同意与删除、安全测试 |

- 完整：核心节点声明的每个认知层次都有 ≥ 1 道已发布题目（主考点是该节点、该层次，`admitted` 且标签 `approved`），且页面快照里有
  map_version 与本地图版本一致的页面；完整率门槛 ≥ 95%（精确分数），核心节点页面发布率门槛 100%。缺口清单按节点 × 层次列出，只含节点 ID。
  未标认知层次、或层次不在节点声明里的已发布题不计入任何层次，只在说明里计数。
- M1 报告：`met` 必须与数值一致（比率 = 分子等于分母，阻断 = 0），否则退出 2；“不可计算：没有给……”改判待填；其他说明只转引 `knowledge.m1`
  的固定文案，不回显输入。M1 的页面快照提交号与本报表读的不一致时，知识点页发布率与核心节点页面发布率报不可计算。
- 快照摘要（KGE-06，T33）：M1 报告记 `map.snapshot_digest`（地图快照的规范化摘要：解析后按键排序、无多余空白、保留中文的 JSON 的 sha256，
  与写法无关）与 `manifest_sha256`（页面快照 manifest.json 原始字节，与本报表 inputs 里 page_snapshot 的 sha256 同一口径）。本报表对自己
  读的地图快照用同一函数计算（`map.map_snapshot_digest`）：不一致或 M1 没有地图摘要（旧版）时 M1 三项报不可计算；manifest 摘要不一致时
  两项页面指标报不可计算（旧版 M1 没有 manifest 摘要时只比提交号）。
- kappa、Top-1 的判定沿用来源脚本（按精确分数算）；报告里的门槛与本报表不一致、或数值与判定矛盾，按输入错误处理。

准入记录（只收汇总数字与状态，不收名册；未知字段一律拒收）：
```yaml
format: kge-m2-attestation/1
subject: aya
shadow_review: {reviewers: 2, weeks_covered: 5, weeks_required: 5}       # 教研负责人填；门槛每科 ≥ 2 人、覆盖封测全周期
holdout: {untaggable: 1, total: 100, executor_role: 教研负责人, signed: true}   # run_holdout.sh 的汇总数字（规则 14）；角色只收“教研负责人”；未签字报待填
c_level: {status: passed, evidence: [evidence/c-level-tests.txt]}          # passed / failed；缺这一节报待填
guardian: {deletion_e2e: passed, consent_flow: live, evidence: [evidence/sie03-deletion-e2e.txt]}   # 同意流程 not_live → 未达到
security: {pentest_round2: passed, evidence: [evidence/pentest-round2.txt]}                          # 未填 → 待填
```
- 填了“通过”但没有证据文件的项报“待填”。证据文件写相对准入记录所在目录的路径：每段 `^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$`、
  任何一段是符号链接即拒、解析后必须仍在该目录内、单个文件 ≤ 10 MB；报表只记路径写法与 sha256。
- 合成样例：`tests/fixtures/m2/`（数学、道法各一个全部达标、一个多项未达标或缺输入的场景；数学地图直接用 `tests/fixtures/map-v1.yaml`）。
