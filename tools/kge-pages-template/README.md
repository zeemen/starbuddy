# 页面仓库模板（KGE-03 编译层，试点期）

编译层页面（知识点页等）试点期存在**单独的 Git 仓库**里，不在本仓（CLAUDE.md“知识页面（编译层）试点期放单独的 Git 仓库”；ADR 12 草稿）。
本目录是那个仓库的模板：**由人创建页面仓库时把 `repo/` 下的内容拷过去**。本仓与编译器都不创建、不推送任何远程仓库。

- 托管平台（GitHub / GitLab / 自建）**待 ADR 12 待人确认 #1**；所以 CI 只给两份样例（`repo/ci/github-actions.yml.example`、
  `repo/ci/gitlab-ci.yml.example`），启用哪一份、怎么放到平台要求的位置，由人按选定的平台决定。
- 检查命令复用本仓（ADR 12 待人确认 #4 默认“复用本仓的检查命令”）：CI 取本仓 `services/knowledge` 的**固定版本预构建只读制品**
  （`repo/ci/install-tools.sh` 按 `repo/ci/kge-tools.pin` 下载、核 sha256，依赖按制品里的 `requirements.lock` 以 `pip --require-hashes` 安装），
  跑 `python -m knowledge.lint`（`repo/ci/lint.sh` 包了一层）。体检有阻断（退出码 1）即不能合并；参数或输入错误（退出码 2，含缺地图快照）也按失败处理。
- **信任边界**：`lint.sh`、地图快照、人设词表与上一审核版一律取自 `main` 的检出，合并请求头部只提供 `pages/`；
  合并请求任务不带任何凭据（GitHub 只用 `pull_request`，不得用 `pull_request_target` 检出头部代码；action 固定到提交 SHA、镜像固定到 digest）；
  `ci/`、`.github/`、`snapshots/`、`lexicon/`、`.gitlab-ci.yml` 由平台负责人审核（`repo/.github/CODEOWNERS`）。见 `BRANCH_PROTECTION.md` 第 13–18 条。
- 分支保护与审核规则见 `BRANCH_PROTECTION.md`；教研怎么改页面见 `OPERATIONS.md`（骨架，待教研补充）。

## 拷贝步骤（执行人）
1. 在选定的托管平台建空仓库（私有），默认分支 `main`。
2. 拷贝 `repo/` 下全部文件（含隐藏目录 `.github/`）到仓库根目录；按平台启用一份 CI 样例（去掉 `.example` 后缀放到平台要求的位置）。
3. 在本仓导出人设词表：`cd services/knowledge && .venv/bin/python -m knowledge.lint --export-lexicon ../../<页面仓库>/lexicon/lexicon.json`
   （词表来自人设治理组件 guard 的导出：`packages/personas/registry.yaml` 与 `packages/personas/forbidden/`；人设负责人更新后重新导出）。
4. 放入已发布地图版本的快照 `snapshots/current.yaml`（格式见 `services/knowledge/knowledge/atoms.py`；PR ② 起由知识服务从 `kge.*` 导出）。
   快照是体检与快照导出的必需输入：缺失时 CI 与 `knowledge.compiler.snapshot` 都以退出码 2 失败。
5. 由平台在本仓的发布流程里产出 `services/knowledge` 的只读制品（tar.gz，含 `requirements.lock`），放到内网匿名只读可取的 https 地址，
   把地址与 sha256 写进 `ci/kge-tools.pin`（占位必须替换）。合并请求任务里不配置任何访问本仓的凭据。
6. 替换 CI 样例里的占位：action 的提交 SHA、镜像的 digest；按 `.github/CODEOWNERS` 填平台负责人团队。
7. 按 `BRANCH_PROTECTION.md` 逐条设置并截图留档。

## 目录（repo/）
| 路径 | 说明 |
|---|---|
| `pages/kp/<节点 ID>.md` | 知识点页（页面 ID = 节点 ID）；其余七类页面首期不做 |
| `snapshots/current.yaml` | 页面所依据的已发布地图快照（只读，由编译器流程更新） |
| `lexicon/lexicon.json` | 人设中立词表（从本仓导出，只读） |
| `ci/lint.sh` | CI 与本地共用的体检入口（CI 里从 `main` 的检出运行，头部只给 `pages/`） |
| `ci/install-tools.sh`、`ci/kge-tools.pin` | 在无凭据任务里安装固定版本的检查工具（核 sha256、`--require-hashes`） |
| `ci/*.example` | 两种托管平台的 CI 样例 |
| `.github/CODEOWNERS` | 路径审核规则：CI、工作流、快照、词表由平台负责人审核 |
| `.github/PULL_REQUEST_TEMPLATE.md` | 合并请求模板（GitLab 放到 `.gitlab/merge_request_templates/Default.md`） |
