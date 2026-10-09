#!/usr/bin/env bash
# Makefile“跳过”分支的可复现验证（W13 技术债 T04，specs/tech-debt-batch1）。不进 make check；用法：make make-skip-check
# 用仓库的 Makefile（make -f）在临时目录里跑，目录里只有这里摆出的伪依赖：
#   1. 空目录（什么依赖都没有）：每个带跳过分支的目标，非严格打印“跳过”并返回 0、后面的行不执行（不出现 cd 失败等）；
#      严格（CONTRACT_STRICT=1）返回非零。release-walkthrough 另在命令行传入 PGBIN 时再验一遍。
#   2. 伪依赖目录（假的 node_modules 目录、打印 FAKE-RAN 的假 .venv/bin/python 与 pytest、假 PGBIN/initdb）：
#      依赖齐全的目标不跳过，后续各行的 guard 放行、命令真的执行（看到 FAKE-RAN），严格与非严格都一样；
#      命令行传入 PGBIN 时 release-walkthrough 的跳过判断与后续 guard 在运行时看到同一个 PGBIN（不跳过，进入下一步）。
# 只在临时目录里建文件，结束即删除；不连库、不联网。退出码：0 通过；1 失败。
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
MK="$ROOT/Makefile"
T="$(mktemp -d "${TMPDIR:-/tmp}/make-skip-check.XXXXXX")"
trap 'rm -rf "$T"' EXIT
fails=0; n=0
bad() { echo "  失败：$*"; fails=$((fails + 1)); }

run() {  # $1 目录；其余是 make 参数。输出在 $OUT，退出码在 $RC
  OUT="$(cd "$1" && make -s -f "$MK" "${@:2}" 2>&1)"; RC=$?
}

TARGETS=(adapter-aya-test adapter-aya-mutation adapter-zhixing-test adapter-wenli-test learning-test page-reconcile-test
  release-walkthrough model-router-mutation diagnosis-mutation practice-mutation adapter-client-mutation mastery-mutation
  bff-test bff-mutation web-test web-visual kge-inventory-test kge-items-test kge-coverage-test kge-daofa-import-test
  kge-import-test knowledge-test kge-compile-eval-test eval-persona insight-test e2e-cohort loadtest-baseline
  degradation-test cost-report)

# ---------- 1. 空目录 ----------
EMPTY="$T/empty"; mkdir -p "$EMPTY"
for t in "${TARGETS[@]}"; do
  n=$((n + 1))
  run "$EMPTY" "$t"
  if [ $RC -ne 0 ] || [[ "$OUT" != *跳过* ]] || echo "$OUT" | grep -qE "can't cd|No such file|not found|Error"; then
    bad "$t 非严格：应打印“跳过”并返回 0、后面的行不执行（rc=$RC：$(echo "$OUT" | tail -2 | tr '\n' ' ')）"
  fi
  run "$EMPTY" "$t" CONTRACT_STRICT=1
  [ $RC -ne 0 ] || bad "$t 严格：缺依赖应返回非零"
done
for strict in 0 1; do
  n=$((n + 1))
  run "$EMPTY" release-walkthrough CONTRACT_STRICT=$strict PGBIN=/nonexistent-pgbin
  if [ $strict = 0 ]; then
    { [ $RC -eq 0 ] && [[ "$OUT" == *跳过* ]]; } || bad "release-walkthrough 命令行 PGBIN、非严格：应跳过并返回 0（rc=$RC）"
  else
    [ $RC -ne 0 ] || bad "release-walkthrough 命令行 PGBIN、严格：应返回非零"
  fi
done

# ---------- 2. 伪依赖目录 ----------
FAKE="$T/fake"
mkdir -p "$FAKE/adapters/aya/node_modules/ajv" "$FAKE/services/learning/.venv/bin" "$FAKE/fakepg"
for exe in "$FAKE/services/learning/.venv/bin/python" "$FAKE/services/learning/.venv/bin/pytest" "$FAKE/fakepg/initdb"; do
  printf '#!/bin/sh\necho "FAKE-RAN $0 $*"\nexit 0\n' > "$exe"; chmod +x "$exe"
done
for strict in 0 1; do
  n=$((n + 1))
  # 学习服务与阿鸦依赖都在：不跳过，guard 放行，变异脚本那一行真的执行（假 python 打印 FAKE-RAN）
  run "$FAKE" diagnosis-mutation CONTRACT_STRICT=$strict
  { [ $RC -eq 0 ] && [[ "$OUT" == *FAKE-RAN*diagnosis_mutations.py* ]] && [[ "$OUT" != *跳过* ]]; } \
    || bad "diagnosis-mutation 依赖齐全（严格=$strict）：应执行命令而不是跳过（rc=$RC：$(echo "$OUT" | tail -2 | tr '\n' ' ')）"
  # practice-mutation 只要学习服务依赖：同样照跑
  run "$FAKE" practice-mutation CONTRACT_STRICT=$strict
  { [ $RC -eq 0 ] && [[ "$OUT" == *FAKE-RAN*practice_mutations.py* ]]; } || bad "practice-mutation 依赖齐全（严格=$strict）：应执行命令（rc=$RC）"
  # 命令行传入 PGBIN（指向只有假 initdb 的目录）：跳过判断不成立，进入下一步（tools/release-walkthrough 在伪目录里不存在，make -C 失败），
  # 说明首行与后续 guard 都在运行时按同一个 PGBIN 判断，没有“跳过”
  run "$FAKE" release-walkthrough CONTRACT_STRICT=$strict PGBIN="$FAKE/fakepg" RELEASE_WALK_OUT="$T/walk-out-$strict"
  { [[ "$OUT" != *跳过* ]] && [[ "$OUT" == *产物目录* ]]; } \
    || bad "release-walkthrough 命令行 PGBIN、依赖齐全（严格=$strict）：不应跳过（rc=$RC：$(echo "$OUT" | tail -2 | tr '\n' ' ')）"
done
n=$((n + 1))
run "$FAKE" adapter-zhixing-test
{ [ $RC -eq 0 ] && [[ "$OUT" == *跳过* ]]; } || bad "伪依赖目录里没有知行依赖：adapter-zhixing-test 应跳过（rc=$RC）"

# ---------- 3. 不在展开时求值 ----------
n=$((n + 1))
if grep -qE '^[a-z_]+ *= *\$\(if \$\(shell' "$MK"; then bad "Makefile 里还有展开时用 \$(shell) 求值的跳过判断"; fi

if [ $fails -gt 0 ]; then echo "make-skip-check: 失败（$fails 处）"; exit 1; fi
echo "make-skip-check: 通过（${#TARGETS[@]} 个目标的空目录严格 / 非严格、命令行 PGBIN、伪依赖放行，共 $n 组）"
