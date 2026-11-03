"""复测与验证题交付（KGE-05，specs/retest-delivery-kge05）的变异测试：把任务 → 条目、用途、到期、自述验证道数、8 周复测点开关、
票据前缀与核对、作答确认、只出新族、鉴权等关键检查逐条改坏，断言复测测试能发现。

做法与错题再练的变异脚本相同（复用 scripts/practice_mutations.py 的临时目录复制与运行）：每个变异在临时目录里改一处源码再跑测试；
只有“测试失败”（退出码 1）算发现。先跑一次不变异的对照组，必须通过。两组：
- 纯函数：tests/test_retest_pure.py，不连库；
- 库上：tests/test_retest_service.py，一次性 Postgres（每个变异起一个库）；没有本机 Postgres 时退出码 3（Makefile 决定是否算失败）。
"""
from __future__ import annotations

import os
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import practice_mutations as PM  # noqa: E402

HERE = PM.HERE
PURE_TESTS = ["tests/test_retest_pure.py"]
DB_TESTS = ["tests/test_retest_service.py"]
K, C, SV, ST, API, APP, PSV = ("learning/retest/tasks.py", "learning/retest/config.py", "learning/retest/service.py",
                               "learning/retest/store.py", "learning/retest/api.py", "learning/app.py", "learning/practice/service.py")
TK = "learning/practice/ticket.py"

PURE: dict[str, list[tuple[str, str, str]]] = {
    "hint_followup 也列": [(K, 'RETEST_REASONS = ("scheduled", "early", "decay", "verify_in_platform", "verify_self_report")',
                          'RETEST_REASONS = ("scheduled", "early", "decay", "verify_in_platform", "verify_self_report", "hint_followup")')],
    # 作答用途（审查阻断 1：签字前保守口径，衰减 retest、其余 practice；恢复 transfer_check 必须被发现）
    "验证任务用途恢复 transfer_check": [(K, '"verify_in_platform": "practice", "verify_self_report": "practice"}',
                                     '"verify_in_platform": "transfer_check", "verify_self_report": "transfer_check"}')],
    "平台内验证用途写成复测": [(K, '"verify_in_platform": "practice", "verify_self_report": "practice"}',
                             '"verify_in_platform": "retest", "verify_self_report": "practice"}')],
    "复测轮用途恢复 retest": [(K, 'PURPOSE = {"scheduled": "practice", "early": "practice",', 'PURPOSE = {"scheduled": "retest", "early": "retest",')],
    "衰减用途改成练习": [(K, '"decay": "retest", FOLLOWUP: "practice",', '"decay": "practice", FOLLOWUP: "practice",')],
    "8 周复测点用途写成复测": [(K, '"decay": "retest", FOLLOWUP: "practice",', '"decay": "retest", FOLLOWUP: "retest",')],
    "没到期也列": [(K, "if t.reason not in RETEST_REASONS or t.due_at > now:", "if t.reason not in RETEST_REASONS:")],
    "自述验证少做一道也算做完": [(K, ">= cfg.verify_items_per_task:", ">= cfg.verify_items_per_task - 1:")],
    "自述验证不认票据锚点（同单元任何作答都算）": [(K, "if a.unit == unit and a.has_grade and a.attempt_id in mine)", "if a.unit == unit and a.has_grade)")],
    "自述验证算上没判分的作答": [(K, "if a.unit == unit and a.has_grade and a.attempt_id in mine)", "if a.unit == unit and a.attempt_id in mine)")],
    "作答 ID 不含锚点": [(K, 'f"{key}|{anchor}|attempt"', 'f"{key}|attempt"')],
    "做完的自述验证仍在列表": [(K, 'return [e for e in entries if e.status == "due"][:cfg.queue_cap]', "return entries[:cfg.queue_cap]")],
    "列表不截断": [(K, 'return [e for e in entries if e.status == "due"][:cfg.queue_cap]', 'return [e for e in entries if e.status == "due"]')],
    "参照题取最早一次作答": [(K, "return xs[-1].item_id if xs else None", "return xs[0].item_id if xs else None")],
    "条目 ID 不含学生": [(K, 'f"{student}|{subject}|{anchor}"', 'f"{subject}|{anchor}"')],
    "锚点用途不校验": [(K, 'ANCHOR_RE = re.compile(r"^(practice|retest):', 'ANCHOR_RE = re.compile(r"^([a-z_]+):')],
    "8 周复测点不看开关": [(K, "if not cfg.followup_enabled or cfg.followup_after_days is None:", "if cfg.followup_after_days is None:")],
    "8 周复测点不排除有任务的单元": [(K, "busy = set(taken)", "busy = set()")],
    "8 周复测点不看天数": [(K, "if not xs or xs[-1].recorded_at > now - gap:", "if not xs:")],
    "8 周复测点不要求掌握记录": [(K, "for u in sorted(set(estimated) - busy,", "for u in sorted({a.unit for a in attempts if a.unit} - busy,")],
    "8 周复测点默认打开": [(C, '"followup_enabled": False, "followup_after_days": None,', '"followup_enabled": True, "followup_after_days": 56,')],
    "开关打开不要求天数": [(C, '_need(not doc["followup_enabled"] or days is not None,', "_need(True,")],
    "合成配置在 pilot 也能用": [(C, "if env not in ALLOWED_ENVS and not cfg.signed:", "if False:")],
    "票据前缀不进签名": [(TK, 'head = f"{parts[0]}.{parts[1]}"', 'head = f"pt1.{parts[1]}"')],
}

DB: dict[str, list[tuple[str, str, str]]] = {
    "用途一律写成复测": [(SV, 'body["ingest"] = {"channel": INGEST_CHANNEL, "purpose": purpose}',
                    'body["ingest"] = {"channel": INGEST_CHANNEL, "purpose": "retest"}')],
    "用途一律写成 transfer_check": [(SV, 'body["ingest"] = {"channel": INGEST_CHANNEL, "purpose": purpose}',
                                 'body["ingest"] = {"channel": INGEST_CHANNEL, "purpose": "transfer_check"}')],
    "用途一律写成练习（衰减也是）": [(SV, 'body["ingest"] = {"channel": INGEST_CHANNEL, "purpose": purpose}',
                               'body["ingest"] = {"channel": INGEST_CHANNEL, "purpose": "practice"}')],
    "作答 ID 不按锚点派生": [(SV, 'body["request"]["attempt"]["attempt_id"] = K.attempt_id_for(key, t.anchor)', "pass")],
    # 审查意见 3：本模块只经 grade 写；作答之后直接改任务必须被对拍发现
    "作答后直接 UPDATE retest_task": [(SV, 'summary, replayed = await svc.ingest.ingest("grade", body, PA.GRADE_VERSION, meter=METER.COUNTED)',
                                     'summary, replayed = await svc.ingest.ingest("grade", body, PA.GRADE_VERSION, meter=METER.COUNTED)\n'
                                     '            async with P.tx(svc.pool) as _c:\n'
                                     '                await _c.execute("UPDATE platform.retest_task SET status = \'cancelled\' '
                                     'WHERE id = $1::uuid AND status = \'open\'", t.anchor.split(":", 1)[1])')],
    # 审查意见 2：计次前预检与只找回原结果
    "预检不确认条目仍到期": [(SV, "        await self._check_open(t, student, subject, Q.Unit(t.node_id, t.cognitive_level), now)\n", "")],
    "预检不核对票据的学生": [(SV, "        if t.student != student or t.subject != subject:\n            self.stats[\"ticket_invalid\"] += 1\n"
                                "            raise err(403, \"TICKET_INVALID\", \"retest ticket is not valid\")\n"
                                "        await self._check_open(",
                          "        await self._check_open(")],
    "只找回原结果不核对锚点": [(SV, " or K.attempt_id_for(key, t.anchor) not in row.attempt_ids:", ":")],
    "只找回原结果不核对 usage_id": [(SV, "row.usage_id not in (None, usage_id) or ", "")],
    "只找回原结果改用会封存的重放": [(SV, "            row = await RS.replay_row(conn, student, key)",
                                   "            row = await RS.replay_row(conn, student, key)\n"
                                   "            await conn.execute(\"INSERT INTO platform.replay_seal (method, idempotency_key, student_id, usage_id) "
                                   "VALUES ('grade', $1, $2::uuid, $3::uuid) ON CONFLICT DO NOTHING\", key, student, usage_id) "
                                   "if row is None else None")],
    "请求可以给 purpose、channel": [(API, '_keys(b, {"student_ref", "subject", "ticket", "answer", "idempotency_key", "usage_id"})',
                                  '_keys(b, {"student_ref", "subject", "ticket", "answer", "idempotency_key", "usage_id"}, '
                                  '{"purpose", "channel", "hint_level_used", "evidence_level"})')],
    # LT-04 计量核对：复测作答只认题库自练计次（counted）
    "复测作答按预留核对（不认自练计次）": [(SV, 'PA.GRADE_VERSION, meter=METER.COUNTED)', 'PA.GRADE_VERSION, meter=METER.DIRECT)')],
    "复测作答不核对计量": [(SV, 'PA.GRADE_VERSION, meter=METER.COUNTED)',
                     'PA.GRADE_VERSION, meter=METER.MeterBasis(frozenset(METER.STATES), trusted_purpose=True))')],
    "不核对票据的学生": [(SV, "if t.student != student or t.subject != subject:", "if t.subject != subject:")],
    "不核对票据的学科": [(SV, "if t.student != student or t.subject != subject:", "if t.student != student:")],
    "复测票据与再练票据同前缀": [(SV, 'PREFIX = "rt1"', 'PREFIX = "pt1"')],
    "锚点用途不认识也收": [(SV, "if K.purpose_of_anchor(t.anchor) is None:\n            self.stats", "if False:\n            self.stats")],
    "作答不确认条目仍到期": [(SV, 'if cur is None or cur.status != "due" or cur.unit != unit:', "if cur is None or cur.unit != unit:")],
    "同票据换键再答不拦": [(SV, "if any(a.item_id == t.item_id and a.recorded_at >= since for a in rs.practice.attempts):", "if False:")],
    "键已提交过也按过期拒": [(SV, "t = self.ticket_of(ticket, now, allow_expired=settled)", "t = self.ticket_of(ticket, now, allow_expired=False)")],
    "拿锁后不再读键": [(SV, "if not PA._settled(await PA._key_state(svc, key), student):", "if not settled:")],
    "作答时间取当前（重试指纹变了）": [(SV, "occurred_at=datetime.fromtimestamp(t.issued_at, tz=timezone.utc), key=key,",
                                  "occurred_at=self.clock(), key=key,")],
    "作答返回带出证据 ID": [(SV, 'out: dict = {"verdict": verdict, "entry": entry}',
                         'out: dict = {"verdict": verdict, "entry": entry, "evidence_ids": summary.get("evidence_ids")}')],
    "待复核不标明": [(SV, 'if summary.get("human_review_required") is True:\n            out["pending_review"] = True',
                   'if False:\n            out["pending_review"] = True')],
    "不用再练的单元锁": [(SV, 'async with PA.unit_lock(svc, f"{student}|{subject}|{unit.node_id}|{unit.cognitive_level}"):',
                     "if True:")],
    "出题不要求没做过的族": [(SV, "rs.practice, qe, presenter, target, require_new_family=True)",
                          "rs.practice, qe, presenter, target, require_new_family=False)")],
    "选题忽略只出新族的要求": [(PSV, "if require_new_family:\n            tier = S.tier(f.kept, used, prefer_new_family=True)",
                            "if False:\n            tier = S.tier(f.kept, used, prefer_new_family=True)")],
    "列表不标只剩做过的族": [(SV, "elif S.tier(f.kept, rs.practice.used_families(e.unit), prefer_new_family=True).same_family_only:",
                          "elif False:")],
    "列表无参照题不标不可用": [(SV, "if e.source_item_id is None:\n                status, reason", "if False:\n                status, reason")],
    "列表不读掌握记录（8 周复测点失效）": [(SV, "rs = await RS.load(conn, student, subject, self.practice.cfg, now, with_estimates=self.cfg.followup_enabled)",
                                     "rs = await RS.load(conn, student, subject, self.practice.cfg, now, with_estimates=False)")],
    "列表不按学生过滤任务": [(ST, "WHERE student_id = $1::uuid AND subject = $2::platform.subject AND status = 'open' AND reason",
                          "WHERE $1::uuid IS NOT NULL AND subject = $2::platform.subject AND status = 'open' AND reason")],
    "鉴权不查": [(API, "if not authorized(request):", "if False:")],
    "也认编排层令牌": [(APP, 'retest_api.register(app, lambda r: _bearer_ok(r.headers.get("authorization"), settings.service_credential), state)',
                    'retest_api.register(app, lambda r: _bearer_ok(r.headers.get("authorization"), settings.service_credential) or '
                    '_bearer_ok(r.headers.get("authorization"), settings.orchestrator_client_credential or "-"), state)')],
    "未配置也启动复测": [(APP, "            if retest_cfg is not None:\n", "            if True:\n")],
}


def group(label: str, mutations: dict[str, list[tuple[str, str, str]]], tests: list[str], workers: int) -> int:
    import tempfile
    with tempfile.TemporaryDirectory(prefix="retest-mut.") as t:
        rc, out = PM._run(PM._copy(Path(t)), tests)
    if rc != 0:
        print(out)
        print(f"retest-mutation（{label}）：失败（对照组没有通过，rc={rc}）")
        return 1
    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(lambda kv: PM.one(kv[0], kv[1], tests), mutations.items()))
    missed = [r for r in results if r]
    for m in missed:
        print(f"  {m}")
    if missed:
        print(f"retest-mutation（{label}）：失败，{len(missed)}/{len(mutations)} 个变异没有被发现")
        return 1
    print(f"retest-mutation（{label}）：通过（{len(mutations)} 个变异全部被发现）")
    return 0


def main() -> int:
    if not (HERE / ".venv/bin/python").exists():
        print("retest-mutation: 跳过（没有安装学习服务依赖）")
        return 3
    rc = group("纯函数", PURE, PURE_TESTS, 6)
    if rc:
        return rc
    from glob import glob
    if not (os.environ.get("PGBIN") or glob("/usr/lib/postgresql/*/bin/initdb")):
        print("retest-mutation（库上）：跳过（本机没有 Postgres 服务端）")
        return 3
    return group("库上", DB, DB_TESTS, 4)


if __name__ == "__main__":
    sys.exit(main())
