#!/usr/bin/env bash
# 洞察服务（SIE-02b 第一步）的变异测试：逐条把判断生命周期、首份报告两道检查、渲染、中圈指标、读取过滤、契约校验、鉴权、
# 角色断言、监听地址与影子评审脚本（evals/sie-shadow）的关键检查改坏，断言测试能发现。
# 做法同 services/knowledge/tests/mutations.sh：把 services/insight 复制到临时目录（保持仓库结构，services/learning、docs、db、
# packages 与其他 evals 软链接回仓库，evals/sie-shadow 复制一份），改一处源码，跑指定的测试文件。
# 先跑一次不变异的对照，必须通过；变异后只有“pytest 退出码 1（有断言失败）”才算被发现——收集错误、语法错误（退出码 2 及以上）
# 算变异本身写错；原文找不到或不唯一也算写错。每条限时 180 秒，超时算漏检。
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
SVC="$(cd "$HERE/.." && pwd)"
ROOT="$(cd "$SVC/../.." && pwd)"
PY="$SVC/.venv/bin/python"
if [ ! -x "$PY" ] || ! "$PY" -c "import pytest, fastapi, asyncpg, jsonschema, yaml, httpx" 2>/dev/null; then
  echo "insight-mutation: 跳过（没有安装依赖；见 services/insight/README.md）"; exit 3
fi
# 库上测试要一次性 Postgres；没有时库上变异会以“跳过”通过而漏检，所以直接以退出码 3 报“跳过”（严格模式下 Makefile 判失败）
HAVE_PG=0
if [ -n "${PGBIN:-}" ] && [ -x "$PGBIN/initdb" ]; then HAVE_PG=1; fi
if ls /usr/lib/postgresql/*/bin/initdb >/dev/null 2>&1; then HAVE_PG=1; fi
if [ "$HAVE_PG" != "1" ]; then echo "insight-mutation: 跳过（本机没有 Postgres）"; exit 3; fi

# 每条：名字<TAB>文件（相对 services/insight；以 evals/ 开头的相对仓库根）<TAB>原文<TAB>替换为<TAB>测试文件（空格分隔；
# 以 evals/sie-shadow/ 开头的在副本的 evals/sie-shadow 目录下跑）
M=(
  # 判断生命周期（spec 设计要点 2）
  $'未签字也能成立\tinsight/claims.py\t    return (policy.signed and GATE_RANK\t    return (True and GATE_RANK\ttests/test_claims.py tests/test_properties.py'
  $'未签字策略被当作已签字\tinsight/policy.py\t    signed = bool(doc["signed_by"].strip() and doc["signed_at"].strip()) and not doc["synthetic"]\t    signed = True\ttests/test_policy.py tests/test_claims.py'
  $'到期不转待验证\tinsight/claims.py\t    if c is not None and status in ("hypothesis", "established") and c.expires_at <= now:\t    if False:\ttests/test_claims.py'
  $'证据被更正不撤回\tinsight/claims.py\t        corrected = row is None or bool(set(c.evidence_ids) - set(row.evidence_ids))\t        corrected = row is None\ttests/test_claims.py'
  $'验证不支持不推翻（一律撤回）\tinsight/claims.py\t            if verified_window:\n                ops.append(Op(c.id, u, status, "refuted"\t            if False:\n                ops.append(Op(c.id, u, status, "refuted"\ttests/test_claims.py'
  $'复测时间不对齐到期时间\tinsight/claims.py\t    if retest is not None and now < retest < exp:\t    if False:\ttests/test_claims.py'
  $'修改时续期（绕开待验证）\tinsight/claims.py\t    if changed or new_status != status:\n        ops.append(Op(c.id, u, status, new_status, reason, changed))\t    changed["expires_at"] = _expiry(now, policy.ttl_days[cat], tpl, None)\n    if changed or new_status != status:\n        ops.append(Op(c.id, u, status, new_status, reason, changed))\ttests/test_claims.py'
  $'可见范围不按首发学科限制\tinsight/claims.py\t    want = {"agent"} | ({"student", "guardian"} if subject in policy.visible_subjects else set())\t    want = {"agent", "student", "guardian"}\ttests/test_claims.py'
  $'可见范围超出模板上限\tinsight/claims.py\t    got = tuple(a for a in AUDIENCE_ORDER if a in want and a in tpl.max_visibility)\t    got = tuple(a for a in AUDIENCE_ORDER if a in want)\ttests/test_claims.py'
  $'白名单漏掉验证推翻\tinsight/claims.py\t    ("to_verify", "established", "verification_supported"), ("to_verify", "refuted", "verification_refuted"),\t    ("to_verify", "established", "verification_supported"),\ttests/test_claims.py'
  # 首份报告与渲染（设计要点 3、4）
  $'报告收待验证的判断\tinsight/report.py\tREPORT_STATUSES = ("hypothesis", "established")\tREPORT_STATUSES = ("hypothesis", "established", "to_verify")\ttests/test_report.py'
  $'报告不按受众可见范围\tinsight/report.py\t and audience in rc.visibility\t and True\ttests/test_report.py'
  $'报告收中圈指标\tinsight/report.py\tand rc.target_type == "node" and\tand rc.target_type in ("node", "metric") and\ttests/test_report.py'
  $'报告不跑一致性检查\tinsight/report.py\t    result = consistency.check_page(check_sections, views, check_cfg, forbidden)\t    result = consistency.CheckResult()\ttests/test_report.py'
  $'报告提示语与标题不查禁用词\tinsight/report.py\t    if forbidden(fixed):\t    if False:\ttests/test_report.py'
  $'家长版不附置信度说明\tinsight/report.py\t            if audience == "guardian":\t            if False:\ttests/test_report.py tests/test_service_db.py'
  $'渲染不查禁用推断词表\tinsight/render.py\t    if not text or len(text) > MAX_TEXT or forbidden(text):\t    if not text or len(text) > MAX_TEXT:\ttests/test_report.py'
  $'待验证不画虚线\tinsight/render.py\t"display": "dashed" if c.status == "to_verify" else "solid"\t"display": "solid"\ttests/test_report.py tests/test_service_db.py'
  # 中圈指标（设计要点 5）
  $'独立解题率样本不足也写\tinsight/metrics.py\t        if len(xs) >= k:\n            correct = sum(1 for a in xs if a.verdict == "correct")\t        if len(xs) >= 1:\n            correct = sum(1 for a in xs if a.verdict == "correct")\ttests/test_metrics.py'
  $'独立解题率计入非首次作答\tinsight/metrics.py\t        first.setdefault((a.subject, a.item_id), a)\t        first[(a.subject, a.item_id, a.fact_id)] = a\ttests/test_metrics.py'
  $'提示依赖度不按阶梯长度归一\tinsight/metrics.py\tmin(a.hint_level_used or 0, ladder) / ladder\tmin(a.hint_level_used or 0, ladder)\ttests/test_metrics.py'
  $'迁移差母题不限同节点\tinsight/metrics.py\t and a.node_id in nodes]\t]\ttests/test_metrics.py'
  $'自评校准偏差方向写反\tinsight/metrics.py\t    direction = "over" if bias > eps else "under"\t    direction = "under" if bias > eps else "over"\ttests/test_metrics.py'
  $'审题占比不要求两科\tinsight/metrics.py\t    if sum(1 for n in counts.values() if n >= per) < need:\t    if False:\ttests/test_metrics.py'
  $'计划完成率不看最后一次记录\tinsight/metrics.py\t        last[p.plan_task_id] = p.kind\t        last.setdefault(p.plan_task_id, p.kind)\ttests/test_metrics.py'
  $'持续性不要求满 4 周\tinsight/metrics.py\t    if not facts or min(a.occurred_at for a in facts) > now - timedelta(days=7 * weeks):\t    if not facts:\ttests/test_metrics.py'
  $'指标判断对学生家长可见\tinsight/metrics.py\t"visibility": tuple(a for a in AUDIENCE_ORDER if a == "agent"),\t"visibility": AUDIENCE_ORDER,\ttests/test_metrics.py'
  $'指标名与说明不过禁用词表\tinsight/metrics.py\t        if forbidden(f"{key}\\n{name}\\n{desc}"):\t        if False:\ttests/test_metrics.py'
  $'指标只认当前判分失效\tinsight/store.py\tWHERE g.attempt_id = a.attempt_id AND g.student_id = a.student_id AND g.superseded_by IS NULL\tWHERE g.attempt_id = a.attempt_id AND g.student_id = a.student_id\ttests/test_db_metrics.py'
  # 读取、契约与鉴权（设计要点 1、3）
  $'读取不按受众可见范围过滤\tinsight/store.py\tc.expires_at > now() AND $2 = ANY (c.visibility) \tc.expires_at > now() AND $2 = $2 \ttests/test_db.py tests/test_service_db.py'
  $'读取放过已到期的判断\tinsight/store.py\t"c.status IN (\'hypothesis\', \'established\', \'to_verify\') AND c.expires_at > now() AND\t"c.status IN (\'hypothesis\', \'established\', \'to_verify\') AND\ttests/test_db.py'
  $'前端读取不排除中圈指标\tinsight/store.py\t        cond += " AND c.target_type <> \'metric\'"\t        cond += ""\ttests/test_db.py'
  $'不核对请求体 caller 与令牌\tinsight/service/tools.py\t        if ctx.get("caller") != caller:\t        if False:\ttests/test_service_unit.py'
  $'跳过请求校验\tinsight/service/tools.py\t        errors = self.contract.validate(spec.request_schema, body)\t        errors = []\ttests/test_service_unit.py'
  $'跳过响应校验\tinsight/service/tools.py\t        bad = self.contract.validate(spec.response_schema, envelope)\t        bad = []\ttests/test_service_db.py'
  $'五个工具不回 501\tinsight/service/tools.py\t        if tool in NOT_IMPLEMENTED:\t        if False:\ttests/test_service_unit.py'
  $'insight.record 不回 501\tinsight/service/tools.py\t        if tool == "insight.record":\t        if False:\ttests/test_service_unit.py'
  $'claims 放给 BFF\tinsight/service/tools.py\t    "insight.context": frozenset({"orchestrator"}), "insight.claims": frozenset({"orchestrator"}),\t    "insight.context": frozenset({"orchestrator"}), "insight.claims": frozenset({"orchestrator", "bff"}),\ttests/test_service_unit.py'
  $'缺省授权核对放行\tinsight/service/tools.py\t        return False\n\n\ndef load_field_dictionary\t        return True\n\n\ndef load_field_dictionary\ttests/test_service_unit.py tests/test_service_db.py'
  $'上下文不按字段字典核对来源与可见范围\tinsight/service/tools.py\t        return f is not None and source_type in f["source_type"] and audience in f["visibility"]\t        return True\ttests/test_service_db.py tests/test_service_unit.py'
  # 服务骨架（设计要点 1）
  $'监听地址缺省为 0.0.0.0\tinsight/service/config.py\tDEFAULT_HOST = "127.0.0.1"\tDEFAULT_HOST = "0.0.0.0"\ttests/test_service_unit.py'
  $'通配地址解析后不拒绝\tinsight/service/config.py\t    if not ips or any(ip.is_unspecified for ip in ips):\t    if not ips:\ttests/test_service_unit.py'
  $'BYPASSRLS 角色不报错\tinsight/store.py\t    if row["rolbypassrls"]:\t    if False:\ttests/test_db.py'
  $'与其他应用角色共存不报错\tinsight/store.py\t    if row["others"]:\t    if False:\ttests/test_db.py'
  $'pg_ 预置角色成员不报错\tinsight/store.py\t    if row["predefined"]:\t    if False:\ttests/test_db.py'
  $'注入的连接池不做角色断言\tinsight/service/app.py\t                    await assert_insight_role(c)\t                    pass\ttests/test_db.py'
  $'合成策略在 dev 也能加载\tinsight/policy.py\t    if doc["synthetic"] and env != TEST_ENV:\t    if False:\ttests/test_policy.py'
  # 影子评审比对（设计要点 6）
  $'影子评审一致率分母用并集\tevals/sie-shadow/compare_shadow.py\t    rate = Fraction(len(agree), len(both)) if both else None\t    rate = Fraction(len(agree), len(set(report.judgments) | set(gold.judgments))) if both else None\tevals/sie-shadow/tests/test_compare.py'
  $'影子评审允许写进 gold/\tevals/sie-shadow/compare_shadow.py\t    if any(p.name == "gold" for p in [out.resolve(), *out.resolve().parents]):\t    if False:\tevals/sie-shadow/tests/test_compare.py'
  $'影子评审样例不标注流程样例\tevals/sie-shadow/compare_shadow.py\t    if res["sample"]:\n        lines += [SAMPLE_BANNER, ""]\t    if False:\n        lines += [SAMPLE_BANNER, ""]\tevals/sie-shadow/tests/test_compare.py'
)

WORK="$(mktemp -d "${TMPDIR:-/tmp}/starbuddy-insight-mut.XXXXXX")"
trap 'rm -rf "$WORK"' EXIT

copy_tree() {  # $1 目标根目录：services/insight 复制一份，其余软链接回仓库
  local dst="$1"
  mkdir -p "$dst/services"
  (cd "$SVC" && tar --exclude=.venv --exclude=__pycache__ -cf - .) | (mkdir -p "$dst/services/insight" && cd "$dst/services/insight" && tar -xf -)
  ln -s "$ROOT/services/learning" "$dst/services/learning"
  for d in docs db packages; do ln -s "$ROOT/$d" "$dst/$d"; done
  mkdir -p "$dst/evals"
  for e in "$ROOT"/evals/*; do
    [ "$(basename "$e")" = "sie-shadow" ] || ln -s "$e" "$dst/evals/$(basename "$e")"
  done
  (cd "$ROOT/evals/sie-shadow" && tar --exclude=__pycache__ -cf - .) | (mkdir -p "$dst/evals/sie-shadow" && cd "$dst/evals/sie-shadow" && tar -xf -)
}

run_tests() {  # $1 副本根目录 $2 测试文件（空格分隔）
  local dir="$1/services/insight" t="$2"
  case "$t" in evals/sie-shadow/*) dir="$1/evals/sie-shadow"; t="${t//evals\/sie-shadow\//}" ;; esac
  ( cd "$dir" && INSIGHT_TEST_STRICT=1 PYTHONDONTWRITEBYTECODE=1 timeout 180 "$PY" -m pytest -q -x -p no:cacheprovider $t >/dev/null 2>&1 ); echo $?
}

control="$WORK/control"; copy_tree "$control"
rc=$(run_tests "$control" "tests")
if [ "$rc" != "0" ]; then echo "insight-mutation: 对照（未变异）没有通过（退出码 $rc），变异结果不可信"; exit 1; fi
rc=$(run_tests "$control" "evals/sie-shadow/tests")
if [ "$rc" != "0" ]; then echo "insight-mutation: 影子评审脚本的对照（未变异）没有通过（退出码 $rc），变异结果不可信"; exit 1; fi
rm -rf "$control"

missed=0; total=0; i=0
for entry in "${M[@]}"; do
  IFS=$'\t' read -r -d '' name file orig repl tests < <(printf '%s\0' "$entry")
  total=$((total + 1)); i=$((i + 1))
  dst="$WORK/m$i"; copy_tree "$dst"
  case "$file" in evals/*) mut_file="$dst/$file" ;; *) mut_file="$dst/services/insight/$file" ;; esac
  if ! MUT_FILE="$mut_file" MUT_ORIG="$orig" MUT_REPL="$repl" "$PY" - <<'PY'
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

if [ $missed -ne 0 ]; then echo "insight-mutation: $missed / $total 条没有被测试发现"; exit 1; fi
echo "insight-mutation: $total 条变异全部被测试发现"
