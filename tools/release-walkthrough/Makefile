# 数学“内容版本 + 地图版本”合成发布走查（specs/release-gate-sprint4 冲刺 4 段；第二部分 PR E）。
# 在仓库根目录：make -C tools/release-walkthrough walkthrough [OUT=<产物目录>]（或根目标 make release-walkthrough，已纳入 make check）
#   一次性本地 Postgres（Unix socket、不监听 TCP，结束即删除）上，两名合成教研经审核台后端走完：地图版本签字发布 → 内容版本登记 →
#   签字发布 → 准入变更 → 版本记录导出 → 阿鸦准入决定文件导出 → 离线重建内容包 → 逐题核对不比平台宽（verify）；规范化产物与 sample/ 逐字节比对。
#   只连本机一次性实例（规则 9），只用合成 staff、合成地图与合成来源编号（规则 8）。约 1 分钟。
#   OUT 须是新目录或空目录（已存在且非空即退出码 2）；不给时新建临时目录，跑完保留（路径打印在输出里）。
# 发布门禁：真实重建阿鸦内容包之后必须跑 admission_export verify 且通过，未跑 verify 不得发布（见 adapters/aya/README.md）。
# 重生样例：make -C tools/release-walkthrough sample [OUT=<产物目录>]（走查流程或导出格式有意改变时；diff 里核对变化）
# 单测（规范化、导出规则、命令行）：make -C tools/release-walkthrough test
# 依赖：services/learning/.venv（见 services/learning/README.md）、本机 postgresql-16、node 与 adapters/aya 下 npm ci。
.PHONY: walkthrough sample test
REPO := $(abspath ../..)
PY := $(REPO)/services/learning/.venv/bin/python
OUT ?=
OUT_BAD = $(or $(filter-out 0 1,$(words $(OUT))),$(findstring ',$(OUT)))
export PYTHONDONTWRITEBYTECODE = 1
export PYTHONPATH = $(REPO)/tools/release-walkthrough:$(REPO)/services/learning:$(REPO)/services/learning/tests

walkthrough:
	@$(if $(OUT_BAD),echo "用法：make -C tools/release-walkthrough walkthrough [OUT=<目录>]（路径不含空格与单引号）"; exit 2)
	@if [ ! -x $(PY) ]; then echo "release-walkthrough: 没有安装学习服务依赖（见 services/learning/README.md）"; exit 2; fi
	@cd $(REPO)/services/learning && $(PY) -m release_walkthrough run $(if $(OUT),--out '$(abspath $(OUT))',) --check-sample $(REPO)/tools/release-walkthrough/sample

sample:
	@$(if $(OUT_BAD),echo "用法：make -C tools/release-walkthrough sample [OUT=<目录>]（路径不含空格与单引号）"; exit 2)
	@if [ ! -x $(PY) ]; then echo "release-walkthrough: 没有安装学习服务依赖（见 services/learning/README.md）"; exit 2; fi
	@cd $(REPO)/services/learning && $(PY) -m release_walkthrough run $(if $(OUT),--out '$(abspath $(OUT))',) --write-sample $(REPO)/tools/release-walkthrough/sample

test:
	@if [ ! -x $(PY) ]; then echo "release-walkthrough: 没有安装学习服务依赖（见 services/learning/README.md）"; exit 2; fi
	@cd $(REPO)/services/learning && $(PY) -m pytest -q -p no:cacheprovider tests/test_admission_export.py
	@cd $(REPO)/services/learning && $(PY) -m pytest -q -p no:cacheprovider --rootdir $(REPO)/tools/release-walkthrough \
	  $(REPO)/tools/release-walkthrough/tests
