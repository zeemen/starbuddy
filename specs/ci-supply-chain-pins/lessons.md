# CI 与契约检查依赖的供应链固定 · 实现记录

- 2026-11-19 用户“按建议，默认值都同意，开始实现”：用 `add_repo` 查 4 个 `actions/*` 公开仓库的标签；只固定、不升级大版本；锁文件放
  `packages/contracts/requirements-dev.lock`。提交首行 `[NEEDS-HUMAN]`（`packages/contracts` 下的变更）。

## 做了什么
- **Actions 固定到提交哈希**（`.github/workflows/check.yml` 12 处）。哈希来自 2026-11-19 `git ls-remote --tags`，取大版本标签当时指向的提交
  （注释标签已剥离到提交），注释写同一提交上的具体版本：
  | Action | 版本 | 提交 |
  |---|---|---|
  | `actions/checkout` | v4.4.0 | `11d5960a326750d5838078e36cf38b85af677262` |
  | `actions/setup-python` | v5.6.0 | `a26af69be951a213d495a4c3e4e4022e16d87065` |
  | `actions/setup-node` | v4.4.0 | `49933ea5288caeca8642d1e84afbd3f7d6820020` |
  | `actions/upload-artifact` | v4.6.2 | `ea165f8d65b6e75b540449e92b4886f43607fa02` |
  `add_repo` 的结果是“公开仓库、本会话的 git 代理已可匿名只读”，没有把这 4 个仓库挂到会话上，也没有克隆，只读了标签。
- **契约检查依赖的哈希锁** `packages/contracts/requirements-dev.lock`（25 个包）：在 Python 3.11（与 CI 相同）上用 pip-compile 生成；
  CI 装 `.venv-contracts` 的步骤与 `scripts/bootstrap-deps.sh` 改为 `--require-hashes -r requirements-dev.lock`；`requirements-dev.txt` 保留作源头。
  许可证逐个核过：MIT / BSD-2 / BSD-3 / BSD / Apache-2.0 / PSF-2.0，没有 GPL 类。
- **测试**（`deploy/tests/test_check_workflow.py`，随 `deploy-check`）：每个 `uses:` 必须是 40 位哈希且同一行注释版本；锁覆盖全部直接依赖、
  每个包都带哈希；CI 与安装脚本都用 `--require-hashes` 装这份锁。

## 与 spec 的差异
1. **版本口径**：spec 写“版本与当前 `.venv-contracts` 已装版本逐个相同”且“共有包与其他服务锁文件版本、哈希一致”。两条在 5 个包上冲突
   （本机 `.venv-contracts` 以前按不锁传递依赖的文件装，装到了更新的 iniconfig 2.3.1、pluggy 1.7.0、pydantic 2.14.1、pydantic-core 2.50.1、rpds-py 2026.9.1）。
   取后者：共有的 16–18 个包与学习、洞察、知识服务锁文件版本和哈希逐个相同（已比对）；其余包取本机已装版本。
2. **在 3.11 上解析**：本机默认 Python 是 3.13；有的依赖只在低版本 Python 上需要（如 typing-extensions 之于 referencing），所以锁在 3.11 上生成，
   并在 3.11 与 3.13 的全新环境里各按哈希装过一遍。

## 验证
- 本机按新锁重建 `.venv-contracts`（安装脚本识别锁文件变化、整目录重建）；`make contract-test`、`make conformance`、`make check-fast`、
  `make deploy-check`、`make db-test`（3328 项断言）、`make make-skip-check`（35 组）通过。
- 教训：`make make-skip-check` 不能带 `CONTRACT_STRICT=1`（会经 MAKEFLAGS 传进子 make，把要验证的“非严格应跳过”也变成严格，报 30 处假失败）。

## 待人确认
- 无新增。升级到 Node 24 原生的新大版本（CI 日志里的 Node 20 弃用提示）另起一项；升级时按文件头的查法同时改哈希与注释。
