#!/usr/bin/env bash
# 洞察服务（SIE-02b 第一步、SIE-03 PR②）的变异测试：逐条把判断生命周期、首份报告两道检查、渲染、中圈指标、读取过滤、契约校验、鉴权、
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
  # 渲染规则只有一份（learning/mastery/render.py，规则本身的变异在 services/learning/scripts/mastery_mutations.py）；这里改调用点
  $'渲染调用点不传禁用推断匹配\tinsight/render.py\t    text = render_text(c.text_template, c.target_id, c.value, forbidden)\t    text = render_text(c.text_template, c.target_id, c.value, lambda t: [])\ttests/test_report.py'
  $'待验证不画虚线\tinsight/render.py\t"display": "dashed" if c.status == "to_verify" else "solid"\t"display": "solid"\ttests/test_report.py tests/test_service_db.py'
  # 中圈指标（设计要点 5）
  $'独立解题率样本不足也写\tinsight/metrics.py\t        if len(xs) >= k:\n            correct = sum(1 for a in xs if a.verdict == "correct")\t        if len(xs) >= 1:\n            correct = sum(1 for a in xs if a.verdict == "correct")\ttests/test_metrics.py'
  $'独立解题率计入非首次作答\tinsight/metrics.py\t        first.setdefault((a.subject, a.item_id), a)\t        first[(a.subject, a.item_id, a.fact_id)] = a\ttests/test_metrics.py'
  $'提示依赖度不按阶梯长度归一\tinsight/metrics.py\tmin(a.hint_level_used or 0, ladder) / ladder\tmin(a.hint_level_used or 0, ladder)\ttests/test_metrics.py'
  $'迁移差母题不限同节点\tinsight/metrics.py\t and a.node_id in nodes]\t]\ttests/test_metrics.py'
  $'自评校准偏差方向写反\tinsight/metrics.py\t    direction = "over" if bias > eps else "under"\t    direction = "under" if bias > eps else "over"\ttests/test_metrics.py'
  $'审题占比不要求两科\tinsight/metrics.py\t    if sum(1 for n in counts.values() if n >= per) < need:\t    if False:\ttests/test_metrics.py'
  # 计划完成 / 跳过的口径（PLT-15 PR2，insight/plan_tasks.py）：指标与周报共用；同一任务以最后一次为准、按发生时间排序、只认已实现的来源口径
  $'计划完成率不看最后一次记录\tinsight/plan_tasks.py\t        last[r.plan_task_id] = r.kind\t        last.setdefault(r.plan_task_id, r.kind)\ttests/test_metrics.py tests/test_plan_tasks.py tests/test_weekly_db.py'
  $'计划口径不按发生时间排序\tinsight/plan_tasks.py\tkey=lambda r: (r.occurred_at, r.record_id))\tkey=lambda r: r.record_id)\ttests/test_plan_tasks.py'
  $'计划口径配置放行只计 plan\tinsight/plan_tasks.py\tSUPPORTED_SOURCES = ("all",)\tSUPPORTED_SOURCES = ("all", "plan")\ttests/test_plan_tasks.py'
  $'计划口径不核对来源配置\tinsight/plan_tasks.py\t    if (counting or default_counting()).count_sources not in SUPPORTED_SOURCES:\t    if False:\ttests/test_plan_tasks.py'
  $'指标不用共用口径\tinsight/metrics.py\t    last = last_per_task(PlanRecord(p.fact_id, p.kind, p.plan_task_id, p.occurred_at) for p in xs)\t    last = {p.plan_task_id: p.kind for p in sorted(xs, key=lambda p: p.kind)}\ttests/test_metrics.py tests/test_plan_tasks.py'
  $'周报客观栏回到只要有完成就算完成\tinsight/store_weekly.py\t                                        if r["kind"] in PLAN_KINDS and r["task"])\t                                        if r["kind"] in PLAN_KINDS and r["task"] and not (r["kind"] == "plan_skip" and any(x["kind"] == "plan_done" and x["task"] == r["task"] for x in beh)))\ttests/test_weekly_db.py'
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
  $'SIE-03 未配置不回 501\tinsight/service/tools.py\t        if tool in SIE03_TOOLS and (self.ops is None or self.sie03 is None):\t        if False:\ttests/test_service_unit.py'
  $'insight.record 不回 501\tinsight/service/tools.py\t        if tool == "insight.record":\t        if False:\ttests/test_service_unit.py'
  $'claims 放给 BFF\tinsight/service/tools.py\t    "insight.context": frozenset({"orchestrator"}), "insight.claims": frozenset({"orchestrator"}),\t    "insight.context": frozenset({"orchestrator"}), "insight.claims": frozenset({"orchestrator", "bff"}),\ttests/test_service_unit.py'
  $'缺省授权核对放行\tinsight/service/tools.py\t        return False\n\n\nclass DbActorVerifier\t        return True\n\n\nclass DbActorVerifier\ttests/test_service_unit.py tests/test_service_db.py'
  $'上下文不按字段字典核对来源与可见范围\tinsight/service/tools.py\t        return f is not None and source_type in f["source_type"] and audience in f["visibility"]\t        return True\ttests/test_service_db.py tests/test_service_unit.py'
  # SIE-03 PR②：异议、预测与对账、触发规则、禁用推断、调度器、幂等、导出与删除（specs/sie-03-dispute-reconcile）
  $'提出人身份不核对\tinsight/store_sie03.py\t                if d["raised_by_role"] != actor_role or str(d["raised_by"]) != actor_ref or d["kind"] != kind:\t                if False:\ttests/test_sie03_db.py'
  $'我其实会不转待验证\tinsight/store_sie03.py\t            if claim.status in ("hypothesis", "established"):\n                n = await conn.execute("UPDATE insight.claim SET status = \'to_verify\'\t            if False:\n                n = await conn.execute("UPDATE insight.claim SET status = \'to_verify\'\ttests/test_sie03_db.py'
  $'验证任务数不按配置\tinsight/store_sie03.py\t                                                 params.dispute.verification_items,\t                                                 1,\ttests/test_sie03_db.py'
  $'验证期限不是 24 小时\tinsight/dispute.py\t    return created_at + timedelta(hours=hours)\t    return created_at + timedelta(hours=hours * 2)\ttests/test_sie03_units.py tests/test_sie03_db.py'
  $'验证结论方向写反\tinsight/dispute.py\t    if got > said:\n        return "supported"\t    if got < said:\n        return "supported"\ttests/test_sie03_units.py'
  $'未签字也能回到成立\tinsight/store.py\t                if not policy.signed or tpl is None or cat not in NODE_CATEGORIES:\t                if tpl is None or cat not in NODE_CATEGORIES:\ttests/test_sie03_db.py'
  $'验证不按异议时的快照比\tinsight/store.py\t            said_ev = tuple(str(e) for e in snap["evidence_ids"]) if snap else claim.evidence_ids\t            said_ev = claim.evidence_ids\ttests/test_sie03_db.py'
  $'事实未更正也撤回\tinsight/store_sie03.py\t                    if not await self._fact_corrected(conn, d["fact_kind"], d["fact_id"]):\n                        continue\t                    if False:\n                        continue\ttests/test_sie03_db.py'
  $'相关判断只撤回被异议的那条\tinsight/store_sie03.py\tAND (id = $2::uuid OR (basis_id IS NOT NULL AND basis_id = $3::uuid)\tAND (id = $2::uuid OR (false AND basis_id = $3::uuid)\ttests/test_sie03_db.py'
  $'事实更正后不重算\tinsight/store_sie03.py\t                if settled and rows is not None:\t                if False:\ttests/test_sie03_db.py'
  $'失误异议不等观察期\tinsight/store_sie03.py\t                        elif r["created_at"] <= r["now"] - timedelta(days=params.dispute.one_off_slip_observe_days):\t                        elif True:\ttests/test_sie03_db.py'
  $'异议放给编排层\tinsight/service/tools.py\t    "insight.dispute": frozenset({"bff"}),\t    "insight.dispute": frozenset({"bff", "orchestrator"}),\ttests/test_service_unit.py'
  $'预测不随证据收缩\tinsight/predict.py\t        var = p_hat * (1 - p_hat) / (cnt + k + 1)\t        var = p_hat * (1 - p_hat) / (k + 1)\ttests/test_sie03_units.py tests/test_sie03_db.py'
  $'预测上界固定为满分\tinsight/predict.py\t    high = min(float(full_score), math.ceil(hi_f * full_score * 10) / 10)\t    high = float(full_score)\ttests/test_sie03_units.py'
  $'满分超出契约响应上限不拒绝\tinsight/store_sie03.py\t        if full > params.exam.response_max_score:\t        if False:\ttests/test_sie03_db.py'
  $'考试安排与请求不核对\tinsight/store_sie03.py\t                if float(plan["full_score"]) != float(full) or set(plan["scope_node_ids"]) != set(exam["scope_node_ids"]):\t                if False:\ttests/test_sie03_db.py'
  $'考试当天零点后仍重算\tinsight/store_sie03.py\t                if now >= freeze_at or (existing is not None and existing["frozen_at"] is not None):\t                if existing is not None and existing["frozen_at"] is not None and False:\ttests/test_sie03_db.py'
  $'高于区间也给默认方向\tinsight/predict.py\t    return ReconcilePlan(False, position, tuple(dev), (), blocked="no_direction_for_above_interval")\t    return ReconcilePlan(False, position, tuple(dev), ("diagnosis_optimistic",))\ttests/test_sie03_units.py tests/test_sie03_db.py'
  $'偏差方向收四值以外\tinsight/predict.py\t    if any(d not in DIRECTIONS for d in given):\t    if False:\ttests/test_sie03_units.py'
  $'区间内也给偏差方向\tinsight/predict.py\t    if position == "inside":\n        return ReconcilePlan(True, position, tuple(dev), ())\t    if position == "inside":\n        return ReconcilePlan(True, position, tuple(dev), given)\ttests/test_sie03_units.py'
  $'区间外不把偏差节点的判断转待验证\tinsight/store_sie03.py\t                if not plan.in_interval and plan.deviation_nodes:\t                if False:\ttests/test_sie03_db.py'
  $'对账判断渲染命中词表仍出卡\tinsight/store_sie03.py\t                if render_text(tpl.text_template, str(e["id"]), value, forbidden) is None:\t                if False:\ttests/test_sie03_db.py'
  $'对账句子不查读者可见范围\tinsight/predict.py\tc.expires_at > now and set(audience) <= set(c.visibility))\tc.expires_at > now)\ttests/test_sie03_units.py'
  $'对账读取不按学生\tinsight/store_sie03.py\tWHERE r.student_id = $1::uuid AND (r.exam_record_id\tWHERE $1::uuid IS NOT NULL AND (r.exam_record_id\ttests/test_sie03_db.py'
  $'触发引用待验证或终结的判断\tinsight/triggers.py\t    return c.status in LIVE_OK and c.expires_at > now\t    return c.expires_at > now\ttests/test_sie03_units.py'
  $'触发去重失效\tinsight/triggers.py\t        if key not in seen:\t        if True:\ttests/test_sie03_units.py tests/test_sie03_db.py'
  $'降负不要求多于上一窗口\tinsight/triggers.py\t        if recent >= params.exit_after_errors_min and recent > before:\t        if recent >= params.exit_after_errors_min:\ttests/test_sie03_units.py'
  $'未签字也下发\tinsight/store_sie03.py\t        if not params.signed:\t        if False:\ttests/test_sie03_db.py'
  $'SIE-03 占位文案不过词表\tinsight/sie03_policy.py\t    hits = check_copy(copy, forbidden or forbidden_matcher())\t    hits = []\ttests/test_sie03_units.py'
  $'调度器角色不断言\tinsight/scheduled.py\t    if row["predefined"] or row["others"] or not row["is_scheduler"] or row["cur"] != ROLE:\t    if False:\ttests/test_sie03_db.py'
  $'幂等同 key 异体不报 409\tinsight/service/tools.py\t        if hit[0] != digest:\t        if False:\ttests/test_sie03_units.py tests/test_sie03_db.py'
  $'幂等摘要含 request_id\tinsight/service/tools.py\t        b["context"] = {k: v for k, v in dict(body["context"]).items() if k != "request_id"}\t        b["context"] = dict(body["context"])\ttests/test_sie03_units.py'
  $'导出缺省不接线却放行\tinsight/guardian_requests.py\t        raise NotWired("export library function is granted to the BFF session only")\t        return ExportTicket("00000000-0000-4000-8000-000000000000", "queued")\ttests/test_service_unit.py'
  # SIE-03 PR② 审查修改（安全审查 F1–F6、契约审查 M2、M3、6–8）
  $'预测可见范围为空时回退到模板上限\tinsight/store_sie03.py\t                if not vis:\n                    raise Unavailable("prediction visibility")\t                if not vis:\n                    vis = tpl.max_visibility[:1]\ttests/test_sie03_db.py'
  $'异议不先做可见性预检\tinsight/store_sie03.py\t                   AND c.expires_at > now() AND $3 = ANY (c.visibility) AND t.status = \'approved\'\t                   AND $3 IS NOT NULL\ttests/test_sie03_db.py'
  $'写入工具不先核对 actor\tinsight/service/tools.py\t        if caller == "bff" and tool in WRITE_TOOLS and not await self.verifier.verify(\t        if False and caller == "bff" and tool in WRITE_TOOLS and not await self.verifier.verify(\ttests/test_service_unit.py tests/test_sie03_db.py'
  $'没有验证任务接口时 i_know 不先回 501\tinsight/service/tools.py\t        if body["kind"] == "i_know" and not getattr(self.scheduler, "available", True):\t        if False:\ttests/test_sie03_db.py'
  $'接口桩在非 test 环境也缺省启用\tinsight/dispute.py\t    if env == "test":\n        return StubVerificationScheduler()\t    if True:\n        return StubVerificationScheduler()\ttests/test_service_unit.py tests/test_kge04_db.py'
  $'预测判断已终结仍更新或另建\tinsight/store_sie03.py\t            if r is None or r["status"] in TERMINAL:\n                raise Conflict("prediction claim is final")\t            if False:\n                raise Conflict("prediction claim is final")\ttests/test_sie03_db.py'
  $'预测不按当前掌握行过滤\tinsight/store_sie03.py\t                rows = [r for r in rows if r.student_id == student and r.basis_id in current]\t                rows = list(rows)\ttests/test_sie03_db.py'
  $'接口桩期间比率仍算有效\tinsight/store_sie03.py\t        valid = total > 0 and stub == 0\t        valid = total > 0\ttests/test_sie03_db.py'
  $'异议响应不带判断证据\tinsight/service/tools.py\t        meta = {"evidence_ids": list(out.evidence_ids), "confidence": 1.0,\t        meta = {"evidence_ids": [], "confidence": 1.0,\ttests/test_sie03_db.py'
  $'卡片方向不核对判断账本\tinsight/predict.py\t and s["direction"] in c.directions))\t))\ttests/test_sie03_units.py'
  $'对账判断不记方向\tinsight/store_sie03.py\t"directions": list(plan.directions), "deviation_nodes"\t"directions": [], "deviation_nodes"\ttests/test_sie03_db.py'
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
  # 冲刺 5-H 跨科周报 v0（specs/trust-ui-sprint5 设计要点 4；迁移 0036）
  $'周报收待验证的判断\tinsight/weekly.py\tSTATUSES = ("hypothesis", "established")\tSTATUSES = ("hypothesis", "established", "to_verify")\ttests/test_weekly.py'
  $'周报不按受众可见范围\tinsight/weekly.py\t and audience in wc.visibility\t and True\ttests/test_weekly.py'
  $'学生版收家长观察来源的判断\tinsight/weekly.py\t            and not (audience == "student" and wc.source_type == "guardian"))\t            and True)\ttests/test_weekly.py'
  $'周报不跑一致性检查\tinsight/weekly.py\t    result = consistency.check_page(check_sections, views, check_cfg, forbidden)\t    result = consistency.CheckResult()\ttests/test_weekly.py'
  $'周报不查排除词（同伴比较、排名、家长评价）\tinsight/weekly.py\t    if any(w in full for w in weekly.excluded_terms[audience]):\t    if False:\ttests/test_weekly.py'
  $'周报标题不查禁用推断\tinsight/weekly.py\t    if forbidden(title):\t    if False:\ttests/test_weekly.py'
  $'点亮不看是否本周\tinsight/weekly.py\t    if cat == "node_transfer" and week[0] <= wc.created_at < week[1]:\t    if cat == "node_transfer":\ttests/test_weekly.py'
  $'攻克收已到迁移的判断\tinsight/weekly.py\t and gate not in STAR_GATES\t\ttests/test_weekly.py'
  $'家长版不带置信度分档\tinsight/weekly.py\t            if audience == "guardian":\n                s["confidence_band"]\t            if False:\n                s["confidence_band"]\ttests/test_weekly.py tests/test_weekly_db.py'
  $'周报每段不设上限\tinsight/weekly.py\t[:weekly.max_per_section]\t\ttests/test_weekly.py'
  $'客观栏收英语\tinsight/weekly.py\t for s in sorted(practice) if s in OBJECTIVE_SUBJECTS]\t for s in sorted(practice)]\ttests/test_weekly.py'
  $'客观栏不标来源 objective\tinsight/weekly.py\t    return {"source_type": "objective", "usage_minutes"\t    return {"source_type": "inferred", "usage_minutes"\ttests/test_weekly.py'
  # PLT-15 PR3（迁移 0057）：计划计数单独一栏标来源 self；改了客观栏形状必须升周报算法版本
  $'自己标记栏标成客观记录\tinsight/weekly.py\t"self_marked": {"source_type": "self",\t"self_marked": {"source_type": "objective",\ttests/test_weekly.py tests/test_weekly_db.py'
  $'改了客观栏形状不升周报版本\tinsight/weekly.py\tALGORITHM_VERSION = "sie-weekly@0.2.0"\tALGORITHM_VERSION = "sie-weekly@0.1.1"\ttests/test_weekly.py tests/test_plan_tasks.py'
  $'合成周报配置在 dev 也能加载\tinsight/weekly.py\t    if doc["synthetic"] and env != TEST_ENV:\t    if False:\ttests/test_weekly.py'
  $'周报抽检比例不校验\tinsight/weekly.py\t and 0 < ratio <= 0.5,\t,\ttests/test_weekly.py'
  # PLT-15 PR2：原“先跳过后完成算跳过”（改的是已删掉的集合差写法）由上面“计划完成率不看最后一次记录”（共用口径，同时跑周报库上测试）
  # 与“周报客观栏回到只要有完成就算完成”替代
  $'周报客观栏跳过数多算\tinsight/store_weekly.py\t    return ObjectiveFacts(seconds, done, skipped,\t    return ObjectiveFacts(seconds, done, skipped + done,\ttests/test_weekly_db.py'
  $'客观栏不限本周\tinsight/store_weekly.py\t\'plan_skip\') AND occurred_at >= $2 AND occurred_at < $3\t\'plan_skip\') AND occurred_at < $3\ttests/test_weekly_db.py'
  $'周报重复运行重写\tinsight/store_weekly.py\t           ON CONFLICT (student_id, week_start, audience) DO NOTHING RETURNING id\t           RETURNING id\ttests/test_weekly_db.py'
  $'周报生成后不抽检\tinsight/store_weekly.py\t    sampled = await sample_week(store, week_start, weekly)\t    sampled = 0\ttests/test_weekly_db.py'
  $'周报不限周的范围\tinsight/store_weekly.py\t    if week_start + timedelta(days=7) > today or (today - week_start).days > LOOKBACK_DAYS:\t    if False:\ttests/test_weekly_db.py'
  $'周报前不触发档案页编译（KGE-04 第二步 PR 4）\tinsight/store_weekly.py\t        o, f = await ensure_profile_pages(profile_pages, s, policy)\t        o, f = 0, 0\ttests/test_weekly_db.py'
  $'档案页触发失败阻塞周报\tinsight/store_weekly.py\t        except Exception:  # noqa: BLE001 — 触发失败不影响周报\n            done = False\t        except ZeroDivisionError:\n            done = False\ttests/test_weekly_db.py'
  $'档案页触发不按周报可见学科（全科都调）\tinsight/store_weekly.py\tfor subject in sorted(s for s in policy.visible_subjects if s in PROFILE_SUBJECTS):\tfor subject in PROFILE_SUBJECTS:\ttests/test_weekly_db.py'
  $'档案页触发客户端失败往外抛\tinsight/profile_pages.py\t        except Exception:  # noqa: BLE001 — 失败不阻塞周报，只计数\t        except ZeroDivisionError:\ttests/test_weekly_db.py'
  $'档案页触发用会话开始的触发点\tinsight/profile_pages.py\tTRIGGER = "weekly"\tTRIGGER = "session_start"\ttests/test_weekly_db.py tests/test_kge04_db.py'
  $'周报收没结束的周\tinsight/store_weekly.py\t    if week_start + timedelta(days=7) > today or\t    if week_start > today or\ttests/test_weekly_db.py'
  $'周报句子不记修订标记\tinsight/weekly.py\t            if wc.updated_at is not None:\t            if False:\ttests/test_weekly.py'
  $'周报配置未签字也生成\tinsight/store_weekly.py\t    if env != TEST_ENV and not weekly.signed:\t    if False:\ttests/test_weekly_db.py'
  $'守卫拒收时整批中断\tinsight/store_weekly.py\t        except asyncpg.CheckViolationError:\t        except asyncpg.UniqueViolationError:\ttests/test_weekly_db.py'
  $'周报对没有判断的学生写不出记录\tinsight/store_weekly.py\t        if res.empty:\t        if False:\ttests/test_weekly_db.py'
  # KGE-04 第二步 PR 3：真实验证任务调度与真实掌握行来源
  $'配置了学习服务也不用真实调度器\tinsight/dispute.py\t    if learning is not None:\n        return LearningVerificationScheduler(*learning)\t    if False:\n        return LearningVerificationScheduler(*learning)\ttests/test_kge04_db.py'
  $'掌握行来源读到已被取代的行\tinsight/predict.py\t AND m.node_id IS NOT NULL AND m.is_current\t AND m.node_id IS NOT NULL\ttests/test_kge04_db.py'
  $'掌握行来源不核对节点的认知层次\tinsight/predict.py\t                           AND m.cognitive_level = ANY (n.cognitive_levels)\t                           AND true\ttests/test_kge04_db.py'
  $'缺省掌握行来源仍是“没有”\tinsight/service/app.py\t                                       mastery=mastery or DbMasterySource(p),\t                                       mastery=mastery,\ttests/test_kge04_db.py'
  $'学习服务 409 不按不可用处理\tinsight/store_sie03.py\t            except VerificationUnavailable:\t            except ZeroDivisionError:\ttests/test_kge04_db.py'
  $'验证任务不带判断的层次\tinsight/store_sie03.py\t                                                 cognitive_level=level if isinstance(level, str) else None)\t                                                 cognitive_level=None)\ttests/test_kge04_db.py'
  $'事务回滚后不撤销已建任务\tinsight/store_sie03.py\t                failed = await scheduler.cancel(stu, ids)\t                failed = 0\ttests/test_kge04_db.py'
  $'调学习服务没有总时长上限\tinsight/dispute.py\t            return await asyncio.wait_for(go(), self.total_timeout)\t            return await go()\ttests/test_kge04_db.py'
  $'结案不读真实掌握行\tinsight/jobs.py\t    rows = await DbMasterySource(store.pool).all_rows(student)\t    rows = ()\ttests/test_kge04_db.py'
  $'学习服务回的单元不核对\tinsight/dispute.py\tt.get("node_id") != node_id or t.get("cognitive_level") != cognitive_level\tFalse\ttests/test_kge04_db.py'
  # SIE-02b 第二步 PR 1：授权再核对（0044 insight.verify_actor）、契约工具 insight.report、命令行 python -m insight.jobs
  $'授权再核对不看函数返回值\tinsight/service/tools.py\t        return ok is True\t        return True\ttests/test_verify_actor_db.py'
  $'授权再核对不核对身份与受众一致\tinsight/service/tools.py\t or actor.get("role") != audience\t\ttests/test_verify_actor_db.py'
  $'授权函数不可用时放行\tinsight/service/tools.py\t"error_class": type(e).__name__[:48]}, ensure_ascii=False))\n            return False\t"error_class": type(e).__name__[:48]}, ensure_ascii=False))\n            return True\ttests/test_verify_actor_db.py'
  $'缺省授权核对不调库函数\tinsight/service/app.py\tverifier or DbActorVerifier(p),\tverifier,\ttests/test_verify_actor_db.py'
  $'首份报告不先核对 actor\tinsight/service/tools.py\t        if not await self.verifier.verify(ctx.get("actor") or {}, audience, student):\n            raise not_found()  # 无权与没有报告同一个 404\t        if False:\n            raise not_found()  # 无权与没有报告同一个 404\ttests/test_verify_actor_db.py'
  $'首份报告不跑禁用推断\tinsight/service/tools.py\t        out = build_first_report(claims, audience, self.policy, self.check_cfg, self.forbidden, now)\t        out = build_first_report(claims, audience, self.policy, self.check_cfg, lambda t: [], now)\ttests/test_verify_actor_db.py'
  $'首份报告对编排层开放\tinsight/service/tools.py\t    "insight.report": frozenset({"bff"}),\t    "insight.report": frozenset({"bff", "orchestrator"}),\ttests/test_verify_actor_db.py'
  $'首份报告信封不带判断证据\tinsight/service/tools.py\t        meta = {"evidence_ids": self._union(rc.evidence_ids for rc in used),\t        meta = {"evidence_ids": [],\ttests/test_verify_actor_db.py'
  $'命令行不限运行环境\tinsight/jobs_cli.py\t    if env not in SERVICE_ENVS:\t    if False:\ttests/test_jobs_cli.py'
  $'命令行连接前不核对本机\tinsight/jobs_cli.py\t    return env, check_local_url(url, env)\t    return env, url\ttests/test_jobs_cli.py'
  $'命令行不核对服务端地址\tinsight/jobs_cli.py\t        await check_server_local(conn, env)\t        pass\ttests/test_jobs_cli.py'
  # LT-01（决定记录第 1、5、9 条）：staging 运行环境、监听地址与学习服务地址的内网口径、月度计划开关
  $'staging 监听地址不限内网\tinsight/service/config.py\t    if host != DEFAULT_HOST and not private_ipv4(host):\t    if False:\ttests/test_staging_env.py'
  $'staging 学习服务地址收任意 http\tinsight/service/config.py\t            staging_private = env == STAGING and m is not None and private_ipv4(m.group(1))\t            staging_private = env == STAGING\ttests/test_staging_env.py'
  $'内网判断放过公网\tinsight/service/config.py\t    return str(ip) == host and any(ip in n for n in _PRIVATE_V4)\t    return True\ttests/test_staging_env.py'
  $'月度计划在 staging 缺省开\tinsight/service/config.py\t                        monthly_enabled=env in ALLOWED_ENVS or bool(e.get("INSIGHT_MONTHLY_PLAN")),\t                        monthly_enabled=True,\ttests/test_staging_env.py'
  $'月度计划关也加载配置\tinsight/service/app.py\t    monthly_on = settings.monthly_enabled or monthly is not None\t    monthly_on = True\ttests/test_staging_env.py'
  $'命令行 staging 连接串收公网\tinsight/jobs_cli.py\t        return local_only(url, env)\t        return url\ttests/test_staging_env.py'
  $'staging 收契约目录覆盖\tinsight/service/config.py\t        if env == STAGING and e.get("INSIGHT_CONTRACT_DIR"):\t        if False:\ttests/test_staging_env.py'
  $'镜像环境标记不查\tinsight/service/config.py\t        if marked is not None and marked != env:\t        if False:\ttests/test_staging_env.py'
  # SIE-05（specs/sie05-silent-metrics）：metrics --skip（定时作业暂不重算学习持续性）
  $'指标 skip 不生效\tinsight/metrics.py\t        if calc.__name__ in skipped:\t        if False:\ttests/test_metrics.py'
  $'指标 skip 收未知名字\tinsight/metrics.py\t    if not skipped <= set(METRIC_NAMES):\t    if False:\ttests/test_metrics.py'
  $'命令行 --skip 不核对名字\tinsight/jobs_cli.py\t    if any(s not in METRIC_NAMES for s in out):\t    if False:\ttests/test_jobs_cli.py'
  $'命令行不把 --skip 传给重算\tinsight/jobs_cli.py\tjobs.recompute_metrics(store, s, policy, skip=skip)\tjobs.recompute_metrics(store, s, policy)\ttests/test_jobs_cli.py'
  $'库上重算不把 skip 传给计算\tinsight/store.py\t                results = compute_metrics(inputs, policy, now, forbidden, skip)\t                results = compute_metrics(inputs, policy, now, forbidden)\ttests/test_db_metrics.py'
  # PLT-13 第一步（specs/scheduler-plt13）：调度器身份的命令行 insight.scheduled 与 insight.jobs weekly
  $'调度器命令行不限运行环境\tinsight/scheduled.py\t    if environ.get(ENV_VAR, "") not in ALLOWED_ENVS:\t    if False:\ttests/test_scheduled_cli.py'
  $'调度器命令行连接前不核对本机\tinsight/scheduled.py\t        return local_only(url, environ.get(ENV_VAR))\t        return url\ttests/test_scheduled_cli.py'
  $'调度器命令行的服务端核对失效\tinsight/scheduled.py\t        await check_server_local(conn, env)\t        pass\ttests/test_scheduled_cli.py'
  $'调度器命令行连上后不核对服务端\tinsight/scheduled.py\t        await check_server(conn, env)\n        if a.cmd == "freeze":\t        if a.cmd == "freeze":\ttests/test_scheduled_cli.py'  $'计量循环一批不满也不停\tinsight/scheduled.py\t        if n < batch:\t        if n < 0:\ttests/test_scheduled_cli.py'
  $'计量循环到了上限不报仍有到期\tinsight/scheduled.py\t    return total, max_batches, True\t    return total, max_batches, False\ttests/test_scheduled_cli.py'
  $'仍有到期时退出码不是 1\tinsight/scheduled.py\t    return 1 if out.get("more_due") else 0\t    return 0\ttests/test_scheduled_cli.py'
  $'计量循环不核对调度器角色\tinsight/scheduled.py\t    await assert_scheduler_role(conn)\n    total = 0\t    total = 0\ttests/test_scheduled_cli.py'
  $'抽检不用调用方给的比例\tinsight/scheduled.py\tlookback_hours, ratio))\tlookback_hours, 0.5))\ttests/test_scheduled_cli.py'
  $'抽检回看小时数不限范围\tinsight/scheduled.py\t            if a.lookback_hours is not None and not 1 <= a.lookback_hours <= MAX_LOOKBACK_HOURS:\t            if False:\ttests/test_scheduled_cli.py'
  $'周报命令缺省不是上一周\tinsight/jobs_cli.py\t        week = week_of(today)[0] - timedelta(days=7)  # 上一个已结束的周\t        week = week_of(today)[0]\ttests/test_scheduled_cli.py'
  $'周报命令有拒收也退出 0\tinsight/jobs_cli.py\t    return 1 if out["job"] == "weekly" and out["outcomes"]["failed"] else 0\t    return 0\ttests/test_scheduled_cli.py'
  $'周报命令不限周一\tinsight/jobs_cli.py\t    if d.isoweekday() != 1:\t    if False:\ttests/test_scheduled_cli.py'
  $'抽检只处理第一个学科\tinsight/scheduled.py\t        for s in dict.fromkeys(a.subject):\t        for s in list(dict.fromkeys(a.subject))[:1]:\ttests/test_scheduled_cli.py'
  $'周报生成中的 ValueError 也当周参数错\tinsight/jobs_cli.py\t        raise JobError("周报配置未签字：test 以外不生成") from None\t        raise JobError("周报配置未签字：test 以外不生成") from None\n    except ValueError:\n        raise JobError("周只接受已结束、35 天内的周一") from None\ttests/test_scheduled_cli.py'
  $'周报命令不先核对周参数\tinsight/jobs_cli.py\t        await check_week(store, week)\t        pass\ttests/test_scheduled_cli.py'
  $'周报配置不按运行环境加载\tinsight/jobs_cli.py\t/ "weekly.synthetic.json"), env=env)\t/ "weekly.synthetic.json"), env="test")\ttests/test_scheduled_cli.py'
  $'周报检查词表不按运行环境加载\tinsight/jobs_cli.py\t/ "weekly-check.synthetic.json"), env=env)\t/ "weekly-check.synthetic.json"), env="test")\ttests/test_scheduled_cli.py'
  # 审查意见：报告单独上限（不沿用 200 条静默截断，超出整份阻断）与固定文字按策略版本可核对
  $'报告读取沿用 200 条截断\tinsight/store.py\t                                           limit=MAX_REPORT_CLAIMS + 1):\t                                           limit=200):\ttests/test_report.py'
  $'报告读取超上限不阻断\tinsight/report.py\t    if len(claims) > MAX_REPORT_CLAIMS:\t    if False:\ttests/test_report.py tests/test_verify_actor_db.py'
  $'报告一节超 200 句不阻断\tinsight/report.py\t    if any(len(g) > MAX_SECTION_SENTENCES for g in groups.values()):\t    if False:\ttests/test_report.py'
  $'报告固定文字超长不阻断\tinsight/report.py\t        blocked.append("over_length")\t        pass\ttests/test_report.py'
  $'报告不带策略版本\tinsight/service/tools.py\t("format", "audience", "policy_version", "notice",\t("format", "audience", "notice",\ttests/test_verify_actor_db.py'
  # PLT-14 第 2 步月度计划（insight/monthly.py、store_monthly.py、service/monthly_route.py）
  $'月度计划：关注学科系数不生效\tinsight/monthly.py\t        ff = cfg.focus_factor if r.subject in focus else 1.0\t        ff = 1.0\ttests/test_monthly.py'
  $'月度计划：优先级不乘考情权重\tinsight/monthly.py\t        pr = round(gap * weight * freq * ff, 6)\t        pr = round(gap * freq * ff, 6)\ttests/test_monthly.py'
  $'月度计划：优先知识点不要求学生可见\tinsight/monthly.py\t                and "student" in c.visibility and "guardian" in c.visibility and c.source_type != "guardian"\t                and "guardian" in c.visibility and c.source_type != "guardian"\ttests/test_monthly.py'
  $'月度计划：优先知识点收家长观察来源\tinsight/monthly.py\t and c.source_type != "guardian"\n\t\n\ttests/test_monthly.py'
  $'月度计划：判断门槛与掌握不一致也收\tinsight/monthly.py\t        if c is None or c.value.get("gate") != r.gate:\t        if c is None:\ttests/test_monthly.py'
  $'月度计划：收待验证的判断\tinsight/monthly.py\tLIVE = ("hypothesis", "established")\tLIVE = ("hypothesis", "established", "to_verify")\ttests/test_monthly.py'
  $'月度计划：不查判断到期\tinsight/monthly.py\t    return c.status in LIVE and c.expires_at > as_of\t    return c.status in LIVE\ttests/test_monthly.py'
  $'月度计划：不查判断能否渲染\tinsight/monthly.py\t                and isinstance(level, str) and gate in GAP_GATES and _renders(c, forbidden)):\t                and isinstance(level, str) and gate in GAP_GATES):\ttests/test_monthly.py'
  $'月度计划：优先知识点不截断\tinsight/monthly.py\tranked[:cfg.max_priority_nodes]\tranked\ttests/test_monthly.py'
  $'月度计划：学生段带关注学科标记\tinsight/monthly.py\t"copy_key": COPY["priority_node"]})\t"copy_key": COPY["priority_node"], "focus": r.subject in focus})\ttests/test_monthly.py'
  $'月度计划：注意事项不限条数\tinsight/monthly.py\t        if len(out) >= cfg.max_guardian_notes:\t        if False:\ttests/test_monthly.py'
  $'月度计划：注意事项不看条件\tinsight/monthly.py\t            ok = ok and (fact is v if cond == "exam_in_month" else fact >= v)\t            ok = True\ttests/test_monthly.py'
  $'月度计划：上月点亮不限上个月进入迁移\tinsight/monthly.py\t                and c.entered_star_at is not None and start <= c.entered_star_at < end and _renders(c, forbidden)):\t                and _renders(c, forbidden)):\ttests/test_monthly.py'
  $'月度计划：上月回顾带不引用判断的计数\tinsight/monthly.py\t"planned_nodes": min(len(planned), 20), "lit"\t"planned_nodes": min(len(planned), 20), "retests_done": 0, "lit"\ttests/test_monthly.py'
  $'月度计划：分差存值\tinsight/monthly.py\t            out.append({"subject": t.subject, "goal_id": g.goal_id,\t            out.append({"gap": t.target_score - s.raw_score, "subject": t.subject, "goal_id": g.goal_id,\ttests/test_monthly.py'
  $'月度计划：输入引用不记判断历史版本\tinsight/monthly.py\t"claims": sorted([c.claim_id, c.history_id] for c in inp.claims),\t"claims": sorted([c.claim_id, c.claim_id] for c in inp.claims),\ttests/test_monthly.py'
  $'月度计划：指纹不含今日计划配置\tinsight/monthly.py\t"fingerprints": {k: fps[k] for k in FINGERPRINT_KEYS}}\t"fingerprints": {k: fps[k] for k in FINGERPRINT_KEYS if k != "today"}}\ttests/test_monthly.py'
  $'月度计划：上月点亮不限上月计划节点\tinsight/monthly.py\t(c.subject, c.target_id) in wanted\tTrue\ttests/test_monthly.py'
  $'月度计划：分差不比满分口径\tinsight/monthly.py\t or round(s.full_score, 1) != round(t.full_score, 1):\t:\ttests/test_monthly.py'
  $'月度计划：分差的考试判断不要求家长可见\tinsight/monthly.py\t        if c.target_type == "exam" and _claim_live(c, inp.as_of) and "guardian" in c.visibility\t        if c.target_type == "exam" and _claim_live(c, inp.as_of)\ttests/test_monthly.py'
  $'月度计划：输入摘要不含生成时刻\tinsight/monthly.py\t"month": inp.month.isoformat(), "as_of": inp.as_of.isoformat(),\t"month": inp.month.isoformat(), "as_of": "fixed",\ttests/test_monthly.py'
  $'月度计划：模板库不查承诺词表\tinsight/monthly.py\t        _need(not npm(text), "模板库文案命中“名次 / 概率 / 承诺”词表")\t        pass\ttests/test_monthly.py'
  $'月度计划：模板库不查禁用推断\tinsight/monthly.py\t        _need(not fm(text), "模板库文案命中禁用推断词表")\t        pass\ttests/test_monthly.py'
  $'月度计划：未签字模板收人写文案\tinsight/monthly.py\t        _need(signed and 1 <= len(text.strip())\t        _need(1 <= len(text.strip())\ttests/test_monthly.py'
  $'月度计划：合成配置可在 dev 加载\tinsight/monthly.py\t    if doc["synthetic"] and env != TEST_ENV:\n        raise PolicyError("合成占位的月度计划配置\t    if False:\n        raise PolicyError("合成占位的月度计划配置\ttests/test_monthly.py'
  # PLT-14 第 3 步审查（模板一致性）：钉死文件核对、签字文案的 {变量} 集合（指纹算法一改，仓库里的模板库就与钉死文件对不上、
  # 模块导入即失败，变异脚本会把它当成“变异本身写错”，所以不单列；由 test_template_fingerprint_is_sha256_of_raw_bytes_and_lock_is_checked 覆盖）
  $'月度计划：模板库不核对钉死文件\tinsight/monthly.py\t    check_lock(lib, lp.read_text(encoding="utf-8"))\n    return lib\t    return lib\ttests/test_monthly.py'
  $'月度计划：签字文案变量不核对\tinsight/monthly.py\t        _need(set(SLOT.findall(text)) == set(variables) and\t        _need(True or\ttests/test_monthly.py'
  $'月度计划：生成不读诉求\tinsight/store_monthly.py\tgoals=_goals(facts.get("goals")),\tgoals=(),\ttests/test_monthly_db.py'
  $'月度计划：生成先查权益再返回已有快照\tinsight/store_monthly.py\t        existing = await conn.fetchval("SELECT insight.monthly_plan_read($1::uuid, $2::date)", student, month)\t        f0 = await conn.fetchval("SELECT insight.monthly_plan_facts($1::uuid, $2::date)", student, month)\n        if not f0 or f0.get("entitled") is not True:\n            raise MonthlyError("unavailable")\n        existing = await conn.fetchval("SELECT insight.monthly_plan_read($1::uuid, $2::date)", student, month)\ttests/test_monthly_db.py'
  $'月度计划：重放不按历史版本取判断\tinsight/store_monthly.py\t WHERE c.student_id = $1::uuid AND h.id = ANY ($2::uuid[])\t WHERE c.student_id = $1::uuid AND h.claim_id IN (SELECT x.claim_id FROM insight.claim_history x WHERE x.id = ANY ($2::uuid[]))\ttests/test_monthly_db.py'
  $'月度计划：生成不核对本月\tinsight/store_monthly.py\t        if month != await current_month(conn):\t        if False:\ttests/test_monthly_db.py'
  $'月度计划：接口不鉴权\tinsight/service/monthly_route.py\t            if caller not in CALLERS:\t            if False:\ttests/test_monthly_db.py'
  $'月度计划：接口收未知键\tinsight/service/monthly_route.py\tset(body) - want or\tFalse or\ttests/test_monthly_db.py'
  $'月度计划：编排层可带 actor\tinsight/service/monthly_route.py\t    want = {"student_id", "month", "actor"} if caller == "bff" else {"student_id", "month"}\t    want = {"student_id", "month", "actor"}\ttests/test_monthly_db.py'
  $'月度计划：BFF 不带 actor 也放行\tinsight/service/monthly_route.py\tif caller == "bff" and (actor is None or actor.get("role") != "guardian"\tif caller == "bff" and actor is not None and (actor.get("role") != "guardian"\ttests/test_monthly_db.py'
  $'月度计划：BFF 的 actor 不在库内再核\tinsight/service/monthly_route.py\t or not await state["verifier"].verify(actor, "guardian", student)):\t or False):\ttests/test_monthly_db.py'
  $'月度计划：接口回多余内容\tinsight/service/monthly_route.py\t"inputs_digest": out.inputs_digest})\t"inputs_digest": out.inputs_digest, "student_id": student})\ttests/test_monthly_db.py'
  $'月度计划：没权益另给一种回答（泄露档位）\tinsight/service/monthly_route.py\t    "unavailable": not_found,\t    "unavailable": lambda: ServiceError(403, "PLAN_NOT_ENTITLED", "x"),\ttests/test_monthly_db.py'
  # LT-03 健康端点（specs/launch-lt03-cloud-staging 范围第 4 条）：存活不查库、就绪等启动前置并查库、限时、不回显异常
  $'健康：就绪不等启动前置\tinsight/service/app.py\thealth.pool_alive(state.get("ready_pool")))\thealth.pool_alive(state.get("ready_pool") or pool))\ttests/test_health.py'
  $'健康：就绪不查库\tinsight/service/health.py\t    return pool is not None and await pool.fetchval("SELECT 1") == 1\t    return pool is not None\ttests/test_health.py'
  $'健康：就绪不限时\tinsight/service/health.py\t        return await asyncio.wait_for(probe(), timeout) is True\t        return await probe() is True\ttests/test_health.py'
  $'健康：就绪回显异常\tinsight/service/health.py\t    except Exception:  # noqa: BLE001 —— 连接断开、超时、池已关闭：都只是“没就绪”\n        return False\t    except Exception as e:  # noqa: BLE001\n        raise RuntimeError(str(e))\ttests/test_health.py'
  $'健康：存活也查库\tinsight/service/health.py\t    async def healthz() -> JSONResponse:\n        return alive()\t    async def healthz() -> JSONResponse:\n        return await ready(probe)\ttests/test_health.py'
  # LT-03 决定记录（续）第 6 条：staging 连库 TLS；契约审查阻断 2：额外允许 Host（Service clusterIP）
  $'连库：staging 不查 TLS\tinsight/service/config.py\t    if env == STAGING and not db_tls_ok(url):\t    if False:\ttests/test_staging_env.py'
  $'连库：CA 不限固定路径\tinsight/service/config.py\t            and q.get("sslrootcert") == [RDS_CA_PATH] and os.path.isfile(RDS_CA_PATH))\t            and os.path.isfile(RDS_CA_PATH))\ttests/test_staging_env.py'
  $'连库：CA 文件可以不在\tinsight/service/config.py\t            and q.get("sslrootcert") == [RDS_CA_PATH] and os.path.isfile(RDS_CA_PATH))\t            and q.get("sslrootcert") == [RDS_CA_PATH])\ttests/test_staging_env.py'
  $'连库：启动不查 TLS\tinsight/service/app.py\t        check_db_tls(settings.database_url, settings.env)  # LT-03\t        pass  # LT-03\ttests/test_staging_env.py'
  $'额外 Host：收任意写法\tinsight/service/config.py\t    if not private_ipv4(raw):\n        raise RuntimeError("INSIGHT_EXTRA_HOST\t    if False:\n        raise RuntimeError("INSIGHT_EXTRA_HOST\ttests/test_staging_env.py'
  $'额外 Host：dev 也能设\tinsight/service/config.py\t    if env != STAGING:\n        raise RuntimeError("INSIGHT_EXTRA_HOST\t    if False:\n        raise RuntimeError("INSIGHT_EXTRA_HOST\ttests/test_staging_env.py'
  $'额外 Host：不进允许列表\tinsight/service/app.py\t    if extra:\n        names.append(extra)\t    pass\ttests/test_staging_env.py'
  $'健康：响应可以缓存\tinsight/service/health.py\tHEADERS = {"cache-control": "no-store", "x-content-type-options": "nosniff"}\tHEADERS = {"x-content-type-options": "nosniff"}\ttests/test_health.py'
)

WORK="$(mktemp -d "${TMPDIR:-/tmp}/starbuddy-insight-mut.XXXXXX")"
trap 'rm -rf "$WORK"' EXIT

copy_tree() {  # $1 目标根目录：services/insight 复制一份，其余软链接回仓库
  local dst="$1"
  mkdir -p "$dst/services"
  (cd "$SVC" && tar --exclude=.venv --exclude=__pycache__ -cf - .) | (mkdir -p "$dst/services/insight" && cd "$dst/services/insight" && tar -xf -)
  ln -s "$ROOT/services/learning" "$dst/services/learning"
  # 禁用推断扫描（tests/test_forbidden_inference_sie03.py）还要读 BFF 与前端的源码：只读引用
  ln -s "$ROOT/services/bff" "$dst/services/bff"
  for d in docs db packages apps; do ln -s "$ROOT/$d" "$dst/$d"; done
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
