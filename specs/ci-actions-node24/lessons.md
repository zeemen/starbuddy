# CI 的 Actions 升级到 Node 24 原生的大版本 · 实现记录

- 2026-11-20 用户“按建议，默认值都同意，开始实现”：四个都升到 v7；setup-node 显式关自动缓存；最低版本表写进结构测试。

## 做了什么
- `.github/workflows/check.yml` 12 处 `uses:` 换成 v7，仍固定到 40 位提交哈希。哈希来自 2026-11-20 `git ls-remote --tags`，
  四个都是轻量标签（没有 `^{}` 行），具体版本标签与大版本标签 `v7` 指向同一提交：
  | Action | 旧 | 新 | 提交 |
  |---|---|---|---|
  | `actions/checkout` | v4.4.0 | v7.0.1 | `3d3c42e5aac5ba805825da76410c181273ba90b1` |
  | `actions/setup-python` | v5.6.0 | v7.1.0 | `6d4fd22b20465ccc0f7354476588e8df7c9ca0e0` |
  | `actions/setup-node` | v4.4.0 | v7.1.0 | `949feb2413d6458794dcd2491c4babbbce0c15c1` |
  | `actions/upload-artifact` | v4.6.2 | v7.0.2 | `cf430e030ddbb5b0abf93d22962f4752f3646cd9` |
- setup-node 三处加 `package-manager-cache: false`（v5 起不给 `cache` 就按包管理器自动开缓存）。
- `deploy/tests/test_check_workflow.py` 加两条：`test_actions_not_below_node24_majors`（最低大版本表：checkout 5、setup-python 6、setup-node 5、
  upload-artifact 6；新出现的 `actions/*` 没登记就失败）、`test_setup_node_disables_auto_cache`。
  反例自检：把一处 checkout 退回 v4.4.0、删掉一处 `package-manager-cache` 后两条都失败，恢复后通过。

## 与 spec 的差异
- 无。

## 验证
- `make deploy-check`、`make check-fast` 通过；工作流结构测试 12 条通过。CI 一轮的结果见 PR #28。

## 待人确认
- 无新增。
