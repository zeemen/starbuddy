# 依赖已知漏洞核对（W13 技术债 T53）

每月或升级依赖前手动跑一次，结果记进 `docs/gates/w13-debt-and-decisions.md` 的 T53 行。要联网，通告库随时间变化，所以不进 `make check` 与 CI 卡口。

```bash
make dep-audit
```

- Node：对 `Makefile` 里 `NPM_AUDIT_DIRS` 列出的每个带锁文件的包跑 `npm audit`（新增 Node 包时把目录加进去）。
- Python：`tools/dep-audit/pypi_vulns.py` 读 `packages/*`、`services/*` 下的 `requirements*.lock` 与 `build-requirements.lock`，
  逐个查 PyPI JSON 接口的 `vulnerabilities` 字段（数据来自 OSV / GitHub 通告库），列出未撤回的通告。只用标准库，不装 `pip-audit`。
  走代理的环境按代理的 CA 包设 `SSL_CERT_FILE`。`--self-test` 只验解析，不联网。
- 退出码：全部为 0 才是 0；有通告或查询失败为非零（查询失败不当作“没有漏洞”）。

修复时只选发布满两周的版本（`specs/npm-audit-fixes`、`specs/py-audit-fixes` 第五节第 3 条）。Python 锁文件是带哈希的，
学习、洞察服务的锁文件头还有手写说明、去掉了注解，直接用 `pip-compile` 重生会整份改写；只升个别包时，只替换那个包的版本行与哈希行
（哈希取 PyPI 的发布文件），再用 `pip install --require-hashes` 装一遍核对。
