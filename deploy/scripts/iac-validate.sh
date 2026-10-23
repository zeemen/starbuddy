#!/usr/bin/env bash
# make iac-validate 的实现（LT-03 范围第 1 条）：IaC 草案的外部工具检查，只在工具已经装好时运行，不联网下载任何工具或 provider。
#   1. terraform（或 OpenTofu 的 tofu）：init -backend=false（不连远端状态）+ validate。provider 必须已在本地镜像目录
#      IAC_PLUGIN_DIR（由人准备的 aliyun/alicloud 离线包，见 deploy/iac/alicloud/README.md 第 2 节）；没有就跳过，不去下载；
#   2. tflint（MPL-2.0）、checkov（Apache-2.0）、tfsec（MIT）：装了哪个跑哪个；
#   3. 不执行任何会读写云上资源的命令（只做 validate 与静态检查，决定记录第 3 条）。
# 不依赖外部工具的静态规则检查（deploy/iac/check_iac.py）不在这里：它总是随 make deploy-check 运行。
# 退出码：0 全部通过；1 有工具报错；3 有工具缺失而跳过（Makefile：非严格模式算通过，CONTRACT_STRICT=1 时失败，与仓库其他目标一致）。
set -uo pipefail
# 不连上游：关闭 terraform / tofu 的版本与安全公告检查（checkpoint）；checkov 用 --skip-download（见下）
export CHECKPOINT_DISABLE=1
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
DIR="$ROOT/deploy/iac/alicloud"
skipped=0; failed=0
note_skip() { echo "iac-validate: 跳过 $1"; skipped=1; }

TF=""
if command -v terraform >/dev/null 2>&1; then TF=terraform; elif command -v tofu >/dev/null 2>&1; then TF=tofu; fi
if [ -z "$TF" ]; then
  note_skip "terraform validate（本机没有 terraform 或 tofu；不联网下载）"
elif [ -z "${IAC_PLUGIN_DIR:-}" ] || [ ! -d "${IAC_PLUGIN_DIR}" ]; then
  note_skip "terraform validate（没有本地 provider 镜像目录 IAC_PLUGIN_DIR；不联网下载 provider）"
else
  work="$(mktemp -d "${TMPDIR:-/tmp}/starbuddy-iac.XXXXXX")"
  cp "$DIR"/*.tf "$work/"
  # 在副本里跑：.terraform/ 与锁文件只落在临时目录，不进仓库
  if TF_IN_AUTOMATION=1 "$TF" -chdir="$work" init -backend=false -input=false -no-color -plugin-dir="$IAC_PLUGIN_DIR" >/dev/null \
     && TF_IN_AUTOMATION=1 "$TF" -chdir="$work" validate -no-color; then
    echo "iac-validate: $TF validate 通过"
  else
    echo "iac-validate: $TF validate 失败"; failed=1
  fi
  rm -rf "$work"
fi

if command -v tflint >/dev/null 2>&1; then
  tflint --chdir="$DIR" --no-color || { echo "iac-validate: tflint 失败"; failed=1; }
else
  note_skip "tflint（本机没有）"
fi
if command -v checkov >/dev/null 2>&1; then
  checkov -d "$DIR" --framework terraform --quiet --compact --skip-download || { echo "iac-validate: checkov 失败"; failed=1; }
else
  note_skip "checkov（本机没有）"
fi
if command -v tfsec >/dev/null 2>&1; then
  tfsec "$DIR" --no-color --soft-fail=false || { echo "iac-validate: tfsec 失败"; failed=1; }
else
  note_skip "tfsec（本机没有）"
fi

if [ $failed -ne 0 ]; then echo "iac-validate: 失败"; exit 1; fi
if [ $skipped -ne 0 ]; then echo "iac-validate: 部分跳过（工具缺失；不伪装为通过）"; exit 3; fi
echo "iac-validate: 通过"
