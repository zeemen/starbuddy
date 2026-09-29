#!/usr/bin/env bash
# 知识服务（KGE-03 PR ①）的变异测试：逐条把编译器与程序体检的关键检查改坏，断言单测能发现。
# 仿照 services/bff/tests/mutations.sh 的源码变异：把 services/knowledge 复制到临时目录（保持仓库结构，docs、evals、
# services/learning 等软链接回仓库），改一处源码，跑指定的测试文件。
# 先跑一次不变异的对照，必须通过；变异后只有“pytest 退出码 1（有断言失败）”才算被发现——
# 收集错误、语法错误（退出码 2 及以上）算变异本身写错；原文找不到也算写错。每条限时 120 秒，超时算漏检。
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
SVC="$(cd "$HERE/.." && pwd)"
ROOT="$(cd "$SVC/../.." && pwd)"
PY="$SVC/.venv/bin/python"
if [ ! -x "$PY" ] || ! "$PY" -c "import pytest, sympy" 2>/dev/null; then
  echo "knowledge-mutation: 跳过（没有安装依赖；见 services/knowledge/README.md）"; exit 3
fi

# 每条：名字<TAB>文件（相对 services/knowledge）<TAB>原文<TAB>替换为<TAB>测试文件（空格分隔）
M=(
  $'锁定段与上一审核版比对失效\tknowledge/lint/checks.py\tif not any(old_hash.get(k) != new_hash.get(k) for k in keys):\tif False:\ttests/test_lint.py'
  $'锁定段与渲染段不核对模板\tknowledge/lint/checks.py\tif cur is not None and cur.content != sec.content:\tif False:\ttests/test_lint.py'
  $'缺引用不查\tknowledge/lint/checks.py\t            if not CITATION.search(para):\t            if False:\ttests/test_lint.py'
  $'SymPy 恒等不验\tknowledge/lint/mathcheck.py\t        if not _equal(a, b):\t        if False:\ttests/test_lint.py tests/test_mathcheck_lexicon.py'
  $'SymPy 代入取值不生效\tknowledge/lint/mathcheck.py\t        if subs:\n\t        if False:\n\ttests/test_lint.py tests/test_mathcheck_lexicon.py'
  $'标了 check 却无法解析只报警告\tknowledge/lint/checks.py\tout.append(_issue(pf, "sympy", "block", f"标了 % check 的式子无法解析\tout.append(_issue(pf, "sympy", "warn", f"标了 % check 的式子无法解析\ttests/test_lint.py'
  $'手机号模式失效\tknowledge/lint/checks.py\t(?<!\\d)1[3-9]\\d[- ]?\t(?<!\\d)0[3-9]\\d[- ]?\ttests/test_lint.py'
  $'姓名加学校组合不查\tknowledge/lint/checks.py\t        for label, pat in PII_PATTERNS:\t        for label, pat in PII_PATTERNS[:-1]:\ttests/test_lint.py'
  $'群体统计人数门槛失效\tknowledge/lint/checks.py\telif min(ns) < ctx.min_group:\telif min(ns) < 1:\ttests/test_lint.py'
  $'人设词表：角色名称呼口头禅不查\tknowledge/lint/checks.py\tliteral = [("角色名", w) for w in lex.role_names] + [("称呼", w) for w in lex.address_terms] + [("口头禅或固定句式", w) for w in lex.catchphrases]\tliteral = []\ttests/test_lint.py'
  $'禁语不查\tknowledge/lint/checks.py\t        for rule in lex.forbidden:\t        for rule in lex.forbidden[:0]:\ttests/test_lint.py'
  $'词表不抽 G 类\tknowledge/lint/lexicon.py\tforbidden += _phrase_rules(_block(examiner, \t_ = (_block(examiner, \ttests/test_lint.py tests/test_mathcheck_lexicon.py'
  $'句长上限不查\tknowledge/lint/checks.py\t                    if n > lex.max_sentence_chars:\t                    if False:\ttests/test_lint.py'
  $'原子哈希过期不查\tknowledge/lint/checks.py\t        elif atom.hash != r.hash:\t        elif False:\ttests/test_lint.py tests/test_e2e.py'
  $'失效链接不查\tknowledge/lint/checks.py\t            if m.group(1) not in ctx.page_ids:\t            if False:\ttests/test_lint.py'
  $'撤下节点的孤立页不查\tknowledge/lint/checks.py\t        if node.get("status", "active") != "active":\t        if False:\ttests/test_lint.py'
  $'带有效期原子过期不查\tknowledge/lint/checks.py\t        if until < ctx.today or\t        if False and until < ctx.today or\ttests/test_lint.py'
  $'教材原句写进页面不查\tknowledge/lint/checks.py\t            if len(q) >= 6 and any(p in flat for p in pieces):\t            if False:\ttests/test_lint.py'
  $'编入不按节点找新增原子\tknowledge/compiler/jobs.py\t            if nid in by_node:\t            if False:\ttests/test_compiler.py'
  $'前置关系表述变更判为低风险\tknowledge/compiler/rebuild.py\telif spec.id == "relations" and\telif False and\ttests/test_compiler.py'
  $'叙述段不逐字保留\tknowledge/compiler/rebuild.py\t            sections.append(narrative[spec.id])\t            sections.append(Section(spec.id, "narrative", narrative[spec.id].content.rstrip() + " "))\ttests/test_compiler.py tests/test_e2e.py'
  $'全量重编不一律高风险\tknowledge/compiler/rebuild.py\t    if kind == "full":\n        risk = "high"\t    if False:\n        risk = "high"\ttests/test_full.py'
  $'全量重编请求放过个人层字段\tknowledge/compiler/full.py\t            if str(k).lower() in PERSONAL_FIELDS:\t            if False:\ttests/test_full.py'
  $'全量重编整条转发节点记录\tknowledge/compiler/full.py\t"node": {"ref": atom.key, "node_id": node_id, "name": str(n["name"]),\t"node": {**dict(n), "ref": atom.key, "node_id": node_id, "name": str(n["name"]),\ttests/test_full.py'
  $'全量重编数据级别不是 L0\tknowledge/compiler/full.py\tdata_class=mr.DataClass.L0\tdata_class=mr.DataClass.L1\ttests/test_full.py'
  $'合并窗口每页每天可发布多次\tknowledge/compiler/window.py\t        slot[r.page_id] = combine(slot[r.page_id], r) if r.page_id in slot else r\t        slot[f"{r.page_id}#{len(slot)}"] = r\ttests/test_compiler.py'
  $'当天已发布的页面不顺延\tknowledge/compiler/window.py\t        while day in done.get(r.page_id, set()):\t        while False:\ttests/test_compiler.py tests/test_e2e.py'
  $'高风险只要 1 人审核\tknowledge/compiler/window.py\tREVIEWERS = {"low": 0, "medium": 2, "high": 2}\tREVIEWERS = {"low": 0, "medium": 2, "high": 1}\ttests/test_compiler.py tests/test_e2e.py'
  $'快照放过未知字段\tknowledge/atoms.py\t    if unknown:\n        raise SnapshotError(f"{where}: 未知字段\t    if False:\n        raise SnapshotError(f"{where}: 未知字段\ttests/test_format_atoms.py'
  $'头部缺字段不查\tknowledge/lint/checks.py\t    if missing:\n        bad(f"头部缺字段\t    if False:\n        bad(f"头部缺字段\ttests/test_lint.py'
)

WORK="$(mktemp -d "${TMPDIR:-/tmp}/kge-mut-XXXXXX")"
trap 'rm -rf "$WORK"' EXIT

copy_tree() {  # $1 目标根目录：services/knowledge 复制一份，其余软链接回仓库
  local dst="$1"
  mkdir -p "$dst/services" "$dst/tools"
  (cd "$SVC" && tar --exclude=.venv --exclude=__pycache__ -cf - .) | (mkdir -p "$dst/services/knowledge" && cd "$dst/services/knowledge" && tar -xf -)
  ln -s "$ROOT/services/learning" "$dst/services/learning"
  ln -s "$ROOT/tools/kge-pages-template" "$dst/tools/kge-pages-template"
  for d in docs evals db packages; do ln -s "$ROOT/$d" "$dst/$d"; done
}

run_tests() {  # $1 副本根目录，$2 测试文件；输出 pytest 退出码
  ( cd "$1/services/knowledge" && PYTHONDONTWRITEBYTECODE=1 timeout 120 "$PY" -m pytest -q -x -p no:cacheprovider $2 >/dev/null 2>&1 ); echo $?
}

control="$WORK/control"; copy_tree "$control"
rc=$(run_tests "$control" "tests")
if [ "$rc" != "0" ]; then echo "knowledge-mutation: 对照（未变异）没有通过（退出码 $rc），变异结果不可信"; exit 1; fi

missed=0; total=0; i=0
for entry in "${M[@]}"; do
  IFS=$'\t' read -r -d '' name file orig repl tests < <(printf '%s\0' "$entry")
  # M 里用 $'...' 写的 \n 是真换行；以 NUL 为记录分隔读入，字段里的换行原样保留
  total=$((total + 1)); i=$((i + 1))
  dst="$WORK/m$i"; copy_tree "$dst"
  if ! MUT_FILE="$dst/services/knowledge/$file" MUT_ORIG="$orig" MUT_REPL="$repl" "$PY" - <<'PY'
import os, sys
p = os.environ["MUT_FILE"]; o = os.environ["MUT_ORIG"]; r = os.environ["MUT_REPL"]
s = open(p, encoding="utf-8").read()
if s.count(o) != 1:
    sys.exit(1)
open(p, "w", encoding="utf-8").write(s.replace(o, r))
PY
  then echo "  变异本身写错（原文找不到或不唯一）：$name"; missed=$((missed + 1)); rm -rf "$dst"; continue; fi
  rc=$(run_tests "$dst" "$tests")
  case "$rc" in
    1) : ;;
    0) echo "  漏检：$name"; missed=$((missed + 1)) ;;
    124) echo "  超时（算漏检）：$name"; missed=$((missed + 1)) ;;
    *) echo "  变异本身写错（pytest 退出码 $rc）：$name"; missed=$((missed + 1)) ;;
  esac
  rm -rf "$dst"
done

if [ $missed -ne 0 ]; then echo "knowledge-mutation: $missed / $total 条没有被测试发现"; exit 1; fi
echo "knowledge-mutation: $total 条变异全部被测试发现"
