# CI 的 Actions 升级到 Node 24 原生的大版本 · 规格草稿

- 状态：**草稿（2026-11-19），等人确认第五节**。
- 来源：`specs/launch-test-2026/todo-by-owner.md` 第一节第 7 条；`specs/ci-supply-chain-pins/` 只做了固定、没有升级（spec 第三节“不做”）。
- 不涉及必停项：只改 `.github/workflows/check.yml` 与它的结构测试 `deploy/tests/test_check_workflow.py`，不改依赖、契约、迁移、权限、计量。

## 一、现状（2026-11-19 核对）
`check.yml` 12 处 `uses:` 已固定到提交哈希，但都是 Node 20 的旧大版本。CI 日志里有 Node 20 弃用提示，GitHub 现在把它们强制跑在 Node 24 上。
等 Node 20 运行时从托管 runner 撤掉，这些旧版本就会直接报错。

各 Action 的 `action.yml` 里写的运行时（2026-11-19 用 `git fetch --depth 1` 取标签、读 `runs.using`）：

| Action | 现在 | 最早用 Node 24 的大版本 | 最新大版本 | 跨过的大版本里会影响本仓的变化 |
|---|---|---|---|---|
| `actions/checkout` | v4.4.0（node20） | v5 | v7（v7.0.1） | v6：`persist-credentials: true` 时凭据改存到 `$RUNNER_TEMP`（本仓一律 `false`，无影响）；v7：`pull_request_target`、`workflow_run` 触发时默认拒绝检出 fork 代码（本仓只用 `pull_request`、`push`、`workflow_dispatch`，无影响） |
| `actions/setup-python` | v5.6.0（node20） | v6 | v7（v7.1.0） | v6 只换运行时；v7 内部改 ESM，输入输出不变 |
| `actions/setup-node` | v4.4.0（node20） | v5 | v7（v7.1.0） | v5 起**没给 `cache` 输入时会按包管理器自动开缓存**；v6 去掉 `always-auth`；v7 去掉 `NODE_AUTH_TOKEN` 占位（本仓不设 `registry-url`，无影响） |
| `actions/upload-artifact` | v4.6.2（node20） | v6 | v7（v7.0.2） | v5 仍是 node20；v6 换 node24；v7 加 `archive` 输入（不用） |

`check.yml` 文件头已写明“不加缓存”（依赖树小，缓存恢复和装一遍差不多，还多一套缓存键）。setup-node 的自动缓存会悄悄违背这条，
而且缓存是 CI 里常见的投毒入口，所以要显式关掉。

## 二、默认做法
1. **四个 Action 都升到最新大版本**：checkout v7、setup-python v7、setup-node v7、upload-artifact v7。
   - 理由：四个最新版都是 node24；跨过的变化上表逐条核过，对本仓都没有影响（或靠第 2 条关掉）。只升到“最早 node24”的版本省不了核对的工夫，
     反而更早再落到“旧大版本”。
   - 仍固定到 40 位提交哈希，注释写具体版本（例如 `# v7.0.1`）。哈希按文件头的查法取：`git ls-remote --tags`，取版本标签剥离后的提交，
     并核对与大版本标签 `v7` 指向同一提交。
2. **setup-node 三处都加 `package-manager-cache: false`**，注释指向文件头“不加缓存”那段。
3. **结构测试**（`deploy/tests/test_check_workflow.py`）加两条：
   - 每个 `actions/*` 的版本注释大版本号不低于一张最低表（checkout 5、setup-python 6、setup-node 5、upload-artifact 6），防止回退到 node20 的版本；
   - 每处 `actions/setup-node` 都带 `package-manager-cache: false`。
4. 文件头“查法”那段不变；在 `specs/ci-actions-node24/lessons.md` 记下这次取到的四个哈希与核对方法。

## 三、不做
- 加缓存、Dependabot / Renovate 之类的自动升级（后者是新增第三方服务，必停）。
- 换 Python、Node 版本（仍是 3.11 与 22）。
- 其他工作流：仓库里只有 `check.yml`。

## 四、验收
- `check.yml` 里没有 node20 的 Action；结构测试（新两条在内）与 `make deploy-check`、`make check-fast` 通过。
- 推上去后 CI 一轮全绿，日志里不再有 Node 20 弃用提示；`persona-report` 制品照常上传。
- 手动跑一次 `scope=mutation` 不是必需（不改迁移、RLS、鉴权、计量），默认不跑。

## 五、待人确认（括号里是默认）
1. **升到哪个大版本**：四个都升到最新的 v7（默认）；还是只升到最早用 Node 24 的版本（checkout v5、setup-python v6、setup-node v5、upload-artifact v6）？
2. **setup-node 自动缓存**：显式 `package-manager-cache: false`，维持文件头“不加缓存”的决定（默认）；还是借这次开缓存？
3. **最低版本表放进测试**：加（默认），以后谁降级测试就拦；还是只靠代码评审？
