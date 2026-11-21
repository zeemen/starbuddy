# 知识服务 jieba 源码构建按哈希装构建依赖 · 实现记录

- 2026-11-21 用户“按建议，默认值都同意，开始实现”：两步安装 + 关构建隔离；setuptools 取当前稳定版；本机安装脚本、CI、知识服务镜像一起改。

## 做了什么
- `services/knowledge/build-requirements.in` / `build-requirements.lock`：只锁 `setuptools==84.2.1`（两个哈希；MIT）。在 Python 3.11 上用
  pip-tools 7.6.1 生成（`--allow-unsafe`，setuptools 默认被 pip-compile 当成“不安全”包不写进锁）。知识服务原有锁文件里没有 setuptools，所以取当前稳定版。
- `scripts/bootstrap-deps.sh` 的 `py_locked`：服务目录里有 `build-requirements.lock` 时，先按哈希装它，主锁再加 `--no-build-isolation`；
  构建依赖锁计入 `.venv` 的摘要（改了就重建）。CI 用的就是这个脚本。
- `deploy/docker/knowledge.Dockerfile`：同样两步；`.dockerignore` 白名单加上构建依赖锁。
- `services/knowledge/README.md`、`Makefile` 的安装说明同步。
- 测试 `deploy/tests/test_check_workflow.py::test_jieba_build_deps_installed_by_hash`：构建依赖锁只有 setuptools 且带哈希；镜像里先装构建依赖、
  主锁那一行带 `--no-build-isolation`；安装脚本的两步写法；只有知识服务有构建依赖锁。

## 验证
- 全新虚拟环境、`--no-cache-dir`：
  - 两步都做：Python 3.11.17、3.13.17 都装成功，`import jieba` 正常分词；
  - 跳过第一步：失败（`Cannot import 'setuptools.build_meta'`）——证明关掉构建隔离后，构建用的就是第一步按哈希装好的 setuptools，不另下载。
- 本机按新脚本重建知识服务 `.venv`：`make knowledge-test` 678 项通过；`make deploy-check`、`make check-fast` 通过；`deploy/tests` 326 项通过。

## 经验
- 验证安装方式时要加 `--no-cache-dir`：pip 会缓存之前构建好的 wheel，第一次不加时“跳过第一步也成功”，是缓存掩盖了问题。

## 待人确认
- 无新增。setuptools 以后升级时按锁文件头部的命令重新生成，并复核许可证。
