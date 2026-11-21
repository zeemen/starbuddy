# 知识服务的 jieba 源码构建也按哈希装构建依赖 · 规格草稿

- 状态：**已确认（2026-11-21，用户“按建议，默认值都同意，开始实现”）**：第五节三条都按默认。实现记录见 lessons.md。
- 来源：PR zeemen/starbuddy#28 合并前增量安全审查 N6 的剩余口子（`specs/launch-test-2026/pr28-review-notes.md` 末节）：按锁文件安装 Python 依赖时
  已一律 `--only-binary=:all:`，只有知识服务的 `jieba==0.42.1` 只发布源码包，用 `--no-binary=jieba` 放开。
- 不涉及必停项：只改知识服务的依赖文件、安装脚本、知识服务与调度器以外的 Dockerfile 不动；不新增第三方服务。

## 一、现状（2026-11-21 核对）
- `jieba-0.42.1.tar.gz` 只有 `setup.py`（`from distutils.core import setup`），没有 `pyproject.toml`。
- pip 构建它时走构建隔离：临时环境里装 `setuptools>=40.8.0`（PEP 517 的缺省构建依赖）。**这一步不受 `--require-hashes` 约束**，
  装哪个版本、从哪来都没有核对。用到的地方：`scripts/bootstrap-deps.sh`（本机与 CI 的知识服务 `.venv`）、`deploy/docker/knowledge.Dockerfile`。

## 二、默认做法
1. **新增构建依赖锁** `services/knowledge/build-requirements.lock`：只锁 `setuptools`（版本取知识服务现有锁文件里同名包的版本；没有就取当前稳定版），
   `pip-compile --generate-hashes` 在 Python 3.11 上生成；源头 `build-requirements.in`。
2. **两步安装**（只在装知识服务依赖时）：
   - 先 `pip install --require-hashes --only-binary=:all: -r build-requirements.lock`；
   - 再 `pip install --require-hashes --only-binary=:all: --no-binary=jieba --no-build-isolation -r requirements(-dev).lock`。
     关掉构建隔离后，jieba 用的就是上一步按哈希装好的 setuptools。
   - 改 `scripts/bootstrap-deps.sh` 的知识服务那一段（`PIP_LOCKED` 不变，知识服务另加一步）与 `deploy/docker/knowledge.Dockerfile`。
3. **测试**（`deploy/tests/test_check_workflow.py` 与 `deploy/tests/test_static.py`）：
   - 凡是带 `--no-binary=jieba` 的安装，同一处前面必须先装 `build-requirements.lock`，且本条带 `--no-build-isolation`；
   - `build-requirements.lock` 每个包都带哈希；
   - 在 Python 3.11 与 3.13 的全新虚拟环境里按两步装一遍，`import jieba` 成功（本机验证，写进 lessons）。
4. 许可证：setuptools 是 MIT。

## 三、不做
- 换掉 jieba、自己打 wheel 放进仓库或私有源（新增分发渠道，要人定）。
- 其他服务（都已只装预编译包）。

## 四、验收
- 知识服务依赖在本机、CI、镜像构建里都不再有不校验哈希的下载；`make knowledge-test`、`make deploy-check`、`make check-fast` 通过；CI 一轮全绿。

## 五、待人确认（括号里是默认）
1. **做法**：两步安装 + 关构建隔离（默认）；还是改为团队自己构建 jieba 的 wheel、放进制品库按哈希装（要新增制品存放）？
2. **setuptools 版本**：与知识服务锁文件里已有的同名包一致（默认，没有就取当前稳定版）；还是另定？
3. **范围**：本机安装脚本、CI、知识服务镜像一起改（默认）；还是只改镜像（生产路径）？
