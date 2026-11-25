# Python 依赖的已知漏洞 · 实现记录

- 2026-11-25 用户“按建议，默认值都同意，开始实现”：两个包一个 PR；核对脚本进仓库、每月手动跑；版本须发布满两周。

## 做了什么
- 六个锁文件（学习、洞察、知识服务的 `requirements.lock` 与 `requirements-dev.lock`）：`certifi` 2026.7.22 → 2026.10.23；
  知识服务两个锁文件另把 `opentelemetry-api` 1.45.0 → 1.45.2（依赖声明与 1.45.0 相同，只有 `typing-extensions>=4.5.0`）。每个文件只动这两个包的版本行与哈希行。
- `services/knowledge/README.md` 依赖表的 opentelemetry-api 版本同步。
- 新增 `tools/dep-audit/pypi_vulns.py`（只用标准库）与 `tools/dep-audit/README.md`；`Makefile` 新增 `make dep-audit`（Node 七个包的 `npm audit` + Python 锁文件核对，不进 check）。
- 技术债清单 T53 扩到 Python，并记下这次的结果。

## 与 spec 的差异
- **没有用 `pip-compile --upgrade-package` 重生锁文件**：试了，学习、洞察服务的锁文件会被整份改写——文件头的手写说明没了，
  `-e ../../packages/contracts/python` 行回来了（`--require-hashes` 不接受，原先是手工删掉的），还多出全部 `# via` 注解；知识服务也会丢一行文件头。
  改为只替换这两个包的版本行与哈希行，哈希取 PyPI 发布文件的 sha256（与 `pip-compile` 算出的一致）。做法写进了 `tools/dep-audit/README.md`。
- 本机 `.venv` 是 Python 3.13，CI 是 3.11；两个包都是纯 Python 的通用 wheel，没有版本差异。

## 验证
- 三个服务 `.venv` 按 `--require-hashes --only-binary=:all:` 装开发锁文件，`pip check` 无冲突。
- `make dep-audit`：Node 七个包与 Python 55 个包与版本全部为 0。
- 本机：`make learning-test`（4034 过、2 预期失败）、`make insight-test`、`make knowledge-test`、`make check-fast`、`deploy/tests`（326 过）全部通过；以 CI 为准。

## 待人确认
- 无新增。
