# 知识服务（services/knowledge）

KGE-03 编译层（`specs/kge-03-compile-layer/spec.md`）。本目录目前是 **PR ①**：页面格式、编译器、程序体检（含 SymPy）；
页面仓库模板在 `tools/kge-pages-template/`，编纂规范骨架在 `docs/design/kge/codification-math-v1.md`。
知识服务读工具、MCP、迁移 0026（PR ②）与兜底检索、对照评测、M1 报表（PR ③）不在本 PR。实现记录与待人确认见同 spec 目录的 `lessons.md`。

本包只读公共层（kge.* 的节点、边、课标、投影；本 PR 用合成快照喂入），不读写任何个人层表（规则 1、2、13）。

| 模块 | 说明 |
|---|---|
| `knowledge/atoms.py` | 公共层原子与地图快照：白名单字段、原子键与哈希、快照差异；数据源接口 `AtomSource`（本 PR 为文件快照，PR ② 接库） |
| `knowledge/nodeid.py` | 节点 ID 正则与学科前缀（测试核对与 `docs/design/kge/check_node_id.py`、迁移 0011、知识服务契约一致） |
| `knowledge/pages/` | 页面格式：YAML 头部 + 三种段落的固定标记；解析与序列化（规范写法往返逐字节稳定） |
| `knowledge/compiler/` | 编入（受影响页、优先级 = 风险 × 影响）→ 渲染重建（叙述段逐字保留）/ 全量重编（经模型路由，T8 × L0）→ 合并窗口 → 本地输出与合并请求说明 |
| `knowledge/lint/` | 程序体检：结构、引用、过期、公式（SymPy）、个人信息与群体统计、人设中立与适龄；结果字段对应 KGE 第七章 `lint_issue` |

## 常用命令
```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt     # 在本目录执行（.venv 已被 .gitignore 忽略）
make knowledge-test knowledge-mutation                                       # 在仓库根目录：单测 + 端到端 + 变异

# 程序体检（页面仓库 CI 用同一条命令，见 tools/kge-pages-template/repo/ci/lint.sh）
.venv/bin/python -m knowledge.lint tests/fixtures/pages --map-snapshot tests/fixtures/map-v1.yaml --baseline tests/fixtures/pages --json
.venv/bin/python -m knowledge.lint --export-lexicon /path/to/lexicon.json    # 导出人设词表给页面仓库

# 编译（渲染重建 + 合并窗口），只写本地目录，不推送任何远程仓库
.venv/bin/python -m knowledge.compiler --pages tests/fixtures/pages --old-snapshot tests/fixtures/map-v1.yaml \
  --new-snapshot /path/to/map-v2.yaml --out /tmp/kge-out --now 2026-09-29T10:00:00+08:00
```
退出码：`knowledge.lint` 0 无阻断、1 有阻断、2 参数或输入错误（快照、词表读不了一律失败，不当作通过）；
`knowledge.compiler` 0 完成、1 有合并请求体检阻断、2 参数或输入错误。

全量重编只有 Python 接口（`knowledge.compiler.full.full_recompile`），经 `services/learning/learning/model_router`（接入方式同
`evals/persona/diandian/compare.py`：把 `services/learning` 加进 `sys.path`）；测试只用路由表 test 环境的进程内假供应商，不联网、不读凭据。

## 依赖与许可证（规则 10）
| 包 | 版本 | 许可证 | 用途 |
|---|---|---|---|
| sympy | 1.14.0 | BSD-3-Clause | 体检的等式验算 |
| mpmath | 1.3.0 | BSD-3-Clause | SymPy 的依赖 |
| pyyaml | 6.0.3 | MIT | 页面头部与快照 |
| httpx | 0.28.1 | BSD-3-Clause | 模型路由的供应商适配器（学习服务已登记同版本）；传递依赖 anyio（MIT）、httpcore（BSD-3）、h11（MIT）、idna（BSD-3）、certifi（MPL-2.0，只作 CA 证书数据，不修改）、typing_extensions（PSF-2.0） |
| pytest（开发） | 9.1.1 | MIT | 单测；传递依赖 pluggy（MIT）、iniconfig（MIT）、packaging（Apache-2.0 或 BSD-2）、Pygments（BSD-2） |

LaTeX 解析没有引入 `sympy.parsing.latex`（需要 antlr4 或 lark 运行时，属新增依赖）：体检自带一个 LaTeX 子集转换器
（`knowledge/lint/mathcheck.py`，分式、根式、乘方、下标变量、四则、括号、π），转换结果先过字符与标识符白名单再交给 SymPy。
