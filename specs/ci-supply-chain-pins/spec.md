# CI 与契约检查依赖的供应链固定 · 规格草稿

- 状态：**已确认（2026-11-19，用户“按建议，默认值都同意，开始实现”）**：第五节三条都按默认。实现记录与差异见 lessons.md。
- 来源：`specs/launch-test-2026/todo-by-owner.md` 第一节（技术负责人）第 4 条“GitHub Actions 固定到提交哈希（防供应链）”
  （出处 `specs/contract-conformance-sprint1/lessons.md` 第 3 条）与第 5 条“契约测试依赖是否补带哈希的锁文件”（`blockers.md` 第一节）。
- **涉及必停项**：在 `packages/contracts/` 下新增锁文件（“packages/contracts 的任何变更”）。提交首行标 `[NEEDS-HUMAN]`。
  不改契约本身（OpenAPI、共享库代码都不动），只加依赖锁。

## 一、现状（2026-11-18 核对）
1. **Actions 用的是可移动的标签**：`.github/workflows/check.yml` 里 12 处 `uses:` 全是大版本标签
   （`actions/checkout@v4` ×4、`actions/setup-python@v5` ×4、`actions/setup-node@v4` ×3、`actions/upload-artifact@v4` ×1）。
   标签可以被重新指向别的提交，上游一旦被篡改，CI 会直接执行被改过的代码（CI 里虽然不配密钥，但有仓库读令牌、能改构建产物）。
2. **契约检查依赖没有哈希锁**：`.venv-contracts` 按 `packages/contracts/requirements-dev.txt` 装（只锁了 4 个直接依赖的版本，
   传递依赖不锁、全部不带哈希）。其余五个 Python 环境（学习、洞察、知识、调度器、审核台）都已用带哈希的锁文件（W13 T06）。
   这个环境跑契约检查、一致性测试、数据库测试与变异的 Python 部分，是 CI 每一组都会装的。

## 二、默认做法
1. **Actions 固定到提交哈希**：每处 `uses:` 改成 `owner/repo@<40 位提交哈希> # vX.Y.Z`（注释写对应的版本标签，便于人读和日后升级）。
   取当前大版本标签此刻指向的提交（只固定、不升级大版本）。
   - 查哈希需要读这 4 个公开仓库的标签（`actions/checkout`、`actions/setup-python`、`actions/setup-node`、`actions/upload-artifact`）。
     它们不在本会话的仓库范围里，默认用 `add_repo` 以只读方式加入后查标签（见待人确认第 1 条）。
   - `deploy/tests/test_check_workflow.py` 加一条：每个 `uses:` 都必须是 40 位十六进制哈希并带版本注释，不许出现标签或分支。
2. **契约检查依赖加哈希锁**：新增 `packages/contracts/requirements-dev.lock`。
   - 生成方式与其他锁文件相同：`pip-compile --generate-hashes`，版本与当前 `.venv-contracts` 已装版本逐个相同（只锁现有版本，不新增依赖）；
     与其他服务锁文件共有的包，版本与哈希保持一致。
   - CI（`check.yml` 里装 `.venv-contracts` 的步骤）与 `scripts/bootstrap-deps.sh` 改用 `--require-hashes -r requirements-dev.lock`；
     `requirements-dev.txt` 保留作源头（改它后重新生成锁）。
   - 许可证：锁里每个包按 README“依赖与许可证”的口径复核一遍（现有声明说全部是 MIT / BSD / Apache-2.0 / PSF-2.0，再核一遍传递依赖）。
   - 加一条一致性测试：锁文件里的版本覆盖 `requirements-dev.txt` 的全部直接依赖，且每个包都带哈希。

## 三、不做
- 升级 Actions 大版本（日志里有 Node 20 弃用提示：`actions/checkout@v4` 等被强制跑在 Node 24 上）。升级另起一项，避免和固定混在一起。
- Dependabot 或 Renovate 之类的自动升级（新增第三方服务，必停）。

## 四、验收
- `check.yml` 里没有任何标签或分支形式的 `uses:`；工作流结构测试通过；CI 一轮全绿（各组都用新锁装 `.venv-contracts`）。
- `make check-fast`、`make make-skip-check`（bootstrap 相关）与 `deploy-check` 通过；本机用新锁重建 `.venv-contracts` 后 `make contract-test` 通过。

## 五、待人确认（括号里是默认）
1. **查 Actions 哈希的方式**：用 `add_repo` 把 4 个 `actions/*` 公开仓库以只读方式加入本会话，只读标签（默认）；还是由人提供哈希？
2. **固定到哪个版本**：当前大版本标签此刻指向的提交（默认，只固定不升级）；还是顺带升级到 Node 24 原生的新大版本？
3. **锁文件放哪**：`packages/contracts/requirements-dev.lock`（默认，与 `requirements-dev.txt` 同目录，属必停目录、需人审）；
   还是放到仓库根（例如 `requirements-contracts.lock`）避开必停目录？
