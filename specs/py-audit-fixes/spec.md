# Python 依赖的已知漏洞：certifi、opentelemetry-api 升补丁版 · 规格草稿

- 状态：**已确认（2026-11-25，用户“按建议，默认值都同意，开始实现”）**：第五节三条都按默认。实现记录与差异见 lessons.md。
- 来源：`specs/npm-audit-fixes/spec.md` 第三节“Python 依赖的漏洞核对另起任务”。2026-11-25 对仓库内全部 Python 锁文件
  （`packages/contracts/requirements-dev.lock`、`services/{learning,insight,knowledge,scheduler}/requirements*.lock`、`services/knowledge/build-requirements.lock`，
  共 55 个包与版本）逐个查 PyPI 的漏洞字段（数据来自 OSV / GitHub 通告库），只读，不装新工具。
- 不涉及必停项：只升已有传递依赖的补丁版本，不新增依赖、不新增第三方服务；不改契约、迁移、计量、判分、人设。许可证不变
  （certifi MPL-2.0、opentelemetry-api Apache-2.0，与登记相同）。

## 一、现状（2026-11-25 核对）
| 包 | 锁定版本 | 通告 | 修复版本 | 出现在 | 本仓库怎么用到 |
|---|---|---|---|---|---|
| `certifi` | 2026.7.22 | GHSA-6q48-6c7w-9gw2 / PYSEC-2026-4320：CA 包里的 `emSign ECC Root CA - G3` 下有 8 张中间 CA 证书被攻破，链到它的伪造证书会被信任 | 2026.10.18 | 学习、洞察、知识服务的运行与开发锁文件 | 随 `httpx` 引入，是这三个服务出站 HTTPS 的默认信任库：学习服务、知识服务调模型供应商，洞察服务调学习服务。伪造证书可以冒充对端、中间人读到请求内容 |
| `opentelemetry-api` | 1.45.0 | GHSA-xcq7-p3pf-p88h（`tracestate` 头的正则没有长度上限）、GHSA-mf5v-w3w3-f6cg（`traceparent` 头的正则 ReDoS） | 1.45.1 / 1.45.2 | 知识服务的运行与开发锁文件 | 随 `mcp` 2.2.0 引入（知识服务用 MCP 低层 Server 与 streamable HTTP）；入站请求若带构造的 `traceparent` / `tracestate` 头并被解析，单个请求就能占满 CPU。是否真被解析未逐行核实，按可达处理 |

其余 53 个包没有未撤回的通告；`services/scheduler`、`packages/contracts` 的锁文件不受影响。

## 二、默认做法
1. **版本选择**（只选发布满两周的版本，同 Node 依赖口径）：
   - `certifi` → **2026.10.23**（10-23 发布）。不用更新的 2026.11.20（不满两周）。
   - `opentelemetry-api` → **1.45.2**（10-29 发布），两条都修。
2. **怎么改**：三个服务都是 `requirements.txt` 用 pip-tools 7.6.1 生成带哈希的锁文件（文件头写着命令）。在各服务 `.venv` 里装 pip-tools 7.6.1
   （只是开发工具，文件头已登记），用 `pip-compile --generate-hashes --allow-unsafe --strip-extras --no-header --upgrade-package certifi==2026.10.23 …`
   只升这两个包，其他包保持原版本；运行锁与开发锁都重生。改完核对：锁文件的差异只有这两个包的版本与哈希。
3. **镜像与安装脚本不用改**：Dockerfile、`scripts/bootstrap-deps.sh`、CI 都按锁文件装（`--require-hashes --only-binary=:all:`），换版本后自动生效。
4. **登记**：`services/knowledge/README.md` 依赖表里的 `opentelemetry-api 1.45.0` 改为 1.45.2；certifi 在 README 里没有写版本，不改。
   技术债清单 T53 扩为“Node 与 Python 依赖”：每月或升级依赖前，两边都核对一次，结果记在那一行。核对用的脚本放 `tools/dep-audit/pypi_vulns.py`
   （只用标准库，读锁文件、查 PyPI，不装 `pip-audit`），README 写怎么跑。
5. **验证**：`make learning-test`、`make insight-test`、`make knowledge-test`、`make check-fast`；重新跑核对脚本，结果为 0；CI 全绿。

## 三、不做
- 把核对脚本加进 CI 卡口（同 Node：要联网，结果随时间变化），见第五节第 2 条。
- 换用系统信任库（`truststore`）或给 `httpx` 指定自带 CA：属于部署与网络口径，另议。
- 升级不在通告里的其他 Python 依赖。

## 四、验收
- 全部 Python 锁文件核对为 0；锁文件差异只有这两个包；第二节第 5 条的检查全部通过。

## 五、待人确认（括号里是默认）
1. **范围**：两个包一起、一个 PR（默认）；还是 certifi 先单独做（它影响三个服务出站 HTTPS 的信任）？
2. **核对脚本**：放进仓库 `tools/dep-audit/`，每月手动跑、不进 CI（默认）；还是只在 lessons 里记命令、不进仓库？
3. **版本要发布满两周**：沿用（默认）。
