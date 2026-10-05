"""错题再练（specs/practice-loop-stage1）的变异测试：把错题判定、闭环、轮次、选题过滤、票据、作答核对与鉴权的关键检查逐条改坏，
断言再练测试能发现。

做法同 scripts/diagnosis_mutations.py：每个变异在临时目录里改一处（或几处）源码再跑测试；只有“测试失败”（退出码 1）算发现，
收集错误、导入错误不算。临时目录保持仓库结构（services/learning 复制，db、adapters、packages、docs 软链接回仓库）。
先跑一次不变异的对照组，必须通过。两组：
- 纯函数（PR 1）：tests/test_practice_pure.py、tests/test_practice_properties.py，不连库；
- 库上（PR 2）：tests/test_practice_service.py，一次性 Postgres（每个变异起一个库）。PR 2 的测试文件还不存在时这一组打印“跳过”；
  没有本机 Postgres 时退出码 3（Makefile 决定是否算失败）。
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]          # services/learning
REPO = HERE.parents[1]
PURE_TESTS = ["tests/test_practice_pure.py", "tests/test_practice_properties.py"]
DB_TESTS = ["tests/test_practice_service.py"]
Q, S, T, C = ("learning/practice/queue.py", "learning/practice/select.py", "learning/practice/ticket.py", "learning/practice/config.py")
SV, AN, ST, API, APP, PR, SR = ("learning/practice/service.py", "learning/practice/answer.py", "learning/practice/store.py",
                                "learning/practice/api.py", "learning/app.py", "learning/practice/presenter.py",
                                "learning/practice/sources.py")

# 名字 → [(文件, 原文, 替换为), …]
PURE: dict[str, list[tuple[str, str, str]]] = {
    "partial 不算错题": [(Q, 'WRONG_VERDICTS = frozenset({"incorrect", "partial"})', 'WRONG_VERDICTS = frozenset({"incorrect"})')],
    "待复核也算错题": [(Q, "    if a.human_review_required:\n        return PENDING_REVIEW\n", "")],
    "申诉中也算错题": [(Q, "if APPEAL in a.review_reasons:\n        return IN_APPEAL", "if False:\n        return IN_APPEAL")],
    "C 级也算": [(Q, 'LEVELS = frozenset({"A", "B"})', 'LEVELS = frozenset({"A", "B", "C", None})')],
    "拍照、语音也进": [(Q, 'CHANNELS = frozenset({"keypad", "canvas"})', 'CHANNELS = frozenset({"keypad", "canvas", "photo", "voice"})')],
    "不看回看期": [(Q, "if a.recorded_at < now - timedelta(days=cfg.lookback_days):", "if False:")],
    "诊断开关不生效": [(Q, "if a.in_diagnosis and not cfg.include_diagnosis_attempts:", "if False:")],
    "原题做对也闭环": [(Q, "and a.item_id not in set(wrong_items)", "")],
    "提示后做对也闭环": [(Q, "and a.hint_level == 0", "and (a.hint_level or 0) <= 3")],
    "自报猜的也闭环": [(Q, 'and a.self_report != "guessed"', "")],
    "待复核的做对也闭环": [(Q, 'a.verdict == "correct" and not a.human_review_required', 'a.verdict == "correct"')],
    "再练做错另开条目": [(Q, "if ep is None or ep.closed_at is not None:", "if True:")],
    "闭环后的错题并入旧条目": [(Q, "if ep is None or ep.closed_at is not None:", "if ep is None:")],
    "轮次间隔不生效": [(Q, "opens = ep.last_wrong_at + timedelta(hours=cfg.round_delay_hours[done])", "opens = ep.last_wrong_at")],
    "做满不算 exhausted": [(Q, "if done >= cfg.max_rounds:", "if done > cfg.max_rounds:")],
    "条目数不截断": [(Q, "return entries[:cfg.queue_cap] if capped else entries", "return entries")],
    "条目 ID 不含学生": [(Q, 'f"{student}|{subject}|{unit.node_id}', 'f"{subject}|{unit.node_id}')],
    "hint 条目同一道题做对也闭环": [(Q, 'if closes(a, {src} if src else set(), task.due_at):', "if closes(a, set(), task.due_at):")],
    "第 1 轮可以延后": [(C, "_need(delays[0] == 0,", "_need(True,")],
    "轮次间隔可以递减": [(C, "_need(all(a <= b for a, b in zip(delays, delays[1:])),", "_need(True,")],
    "合成配置在 pilot 也能用": [(C, "if env not in ALLOWED_ENVS and not cfg.signed:", "if False:")],
    "不排除原题": [(S, "and c.item_id != source_item_id]", "]")],
    "版权受限也出": [(S, 'c.license_state != "restricted" and admission', "admission")],
    "未准入也出": [(S, 'and admission.get(c.item_id) == "admitted"]', "]")],
    "做过的题不排除": [(S, "kept = [c for c in ok if c.item_id not in excluded]", "kept = ok")],
    "不同族不优先": [(S, "if prefer_new_family and fresh:", "if False:")],
    "只有同族时不标 same_family_only": [(S, "return Tier(tuple(stale[:FAMILY_LIMIT]), bool(stale))",
                                       "return Tier(tuple(stale[:FAMILY_LIMIT]), False)")],
    "适配器越界也收": [(S, "c = by_ref.get(ref) if isinstance(ref, str) else None",
                    "c = by_ref.get(ref) if isinstance(ref, str) and ref in by_ref else (offered[0] if offered else None)")],
    "平台未准入也收": [(S, ' or admission.get(c.item_id) != "admitted"', "")],
    "敏感错因也传": [(S, "return code if sensitive is False else None", "return code if sensitive is not None else None")],
    "不按判分方式过滤（主观题也出）": [(S, "if objective is not None:\n        ok = [c for c in ok if objective(c.item_id)]",
                                    "if False:\n        ok = [c for c in ok if objective(c.item_id)]")],
    "原题不固定在排除列表第一位": [(S, 'refs = src + sorted({f"{subject}:{i}" for i in exclude_item_ids if ITEM_ID_RE.fullmatch(i)} - set(src))',
                                'refs = sorted({f"{subject}:{i}" for i in exclude_item_ids if ITEM_ID_RE.fullmatch(i)} | set(src))')],
    "不验签": [(T, "if not hmac.compare_digest(_sign(secret, head).encode(), parts[2].encode()):", "if False:")],
    "不看过期": [(T, "if now > at + timedelta(minutes=ttl_minutes):", "if False:")],
    "未来签发的票据也收": [(T, "if at > now + CLOCK_SKEW:", "if False:")],
}

DB: dict[str, list[tuple[str, str, str]]] = {
    "作答不确认条目仍开着这一轮": [(AN, "if cur is None or cur.status != \"due\" or cur.round != t.round or cur.anchor != t.anchor:",
                              "if cur is None:")],
    "不核对票据的学生": [(AN, "if t.student != student or t.subject != subject:", "if t.subject != subject:")],
    "不核对票据的学科": [(AN, "if t.student != student or t.subject != subject:", "if t.student != student:")],
    "用途写成复测": [(AN, 'INGEST = {"channel": "keypad", "purpose": "practice"}', 'INGEST = {"channel": "keypad", "purpose": "retest"}')],
    "不报提示等级": [(AN, '"response": answer, "hint_level_used": 0},', '"response": answer},')],
    "作答返回带出证据 ID": [(AN, 'out: dict = {"verdict": verdict, "entry": entry}',
                         'out: dict = {"verdict": verdict, "entry": entry, "evidence_ids": summary.get("evidence_ids")}')],
    "作答时间取当前（重试指纹变了）": [(AN, "occurred_at=datetime.fromtimestamp(t.issued_at, tz=timezone.utc)", "occurred_at=svc.clock()")],
    "键已提交过也按过期拒": [(AN, "t = svc.ticket_of(ticket, now, allow_expired=settled)", "t = svc.ticket_of(ticket, now, allow_expired=False)")],
    "同票据换键再答不拦（B1）": [(AN, "if any(a.item_id == t.item_id and a.recorded_at >= since for a in snap.attempts):", "if False:")],
    "去掉作答串行锁（B2）": [(AN, "if not await _lock(conn, name):", "if False:")],
    "失败或租约过期的键也放过过期票据（H1）": [(ST, 'return self.student == student and (self.status == "succeeded" or '
                                                '(self.status == "pending" and self.leased))', "return self.student == student")],
    "拿锁后不再读键（S1）": [(AN, "if not _settled(await _key_state(svc, key), student):", "if not settled:")],
    "错因表读不到就失败（S4）": [(SV, "except Exception as e:  # noqa: BLE001 分类表读不到就不传错因",
                              "except ZeroDivisionError as e:  # noqa: BLE001 分类表读不到就不传错因")],
    "出题时无原题也调适配器（S5）": [(SV, "if source_item_id is None:\n            # hint_followup", "if False:\n            # hint_followup")],
    "列表上无原题不标不可用（S5）": [(SV, 'if e.status == "due" and e.source_item_id is None:', "if False:")],
    "待复核不标明（S6）": [(AN, 'if summary.get("human_review_required") is True:', "if False:")],
    "出题不按判分方式过滤（S7）": [(SV, "return lambda item_id: p.grading(subject, item_id) in S.OBJECTIVE_GRADING",
                               "return lambda item_id: True")],
    "知识服务请求不自检（S8）": [(SR, "if knowledge_contract().validate(REQUESTS[tool], req):\n            raise RequestMismatch(tool)",
                              "if False:\n            raise RequestMismatch(tool)")],
    "票据计数键带重复前缀（S11）": [(SV, "self.stats[e.code.lower()] += 1", 'self.stats[f"ticket_{e.code.lower()}"] += 1')],
    "列表不算可用性": [(SV, 'if f.empty_reason is not None:\n                    e = replace(e, status="unavailable"',
                    'if False:\n                    e = replace(e, status="unavailable"')],
    "出题不核对适配器结果": [(SV, 'picks = S.check_picks(result["candidates"], tier.offered, admission)',
                       'picks = [c for c in (S.from_view(subject, {"item_ref": x["item_ref"], "main_node_id": entry.unit.node_id, '
                       '"cognitive_level": entry.unit.cognitive_level, "license_state": "original"}) for x in result["candidates"]) if c]')],
    "出题时不同族不优先": [(SV, "tier = S.tier(f.kept, used, prefer_new_family=self.cfg.prefer_new_family)",
                       "tier = S.tier(f.kept, used, prefer_new_family=False)")],
    "出题不查平台准入": [(SV, "return await store.admissions(conn, subject, (c.item_id for c in cs))",
                     'return {c.item_id: "admitted" for c in cs}')],
    "可重试的适配器故障当成不可用": [(SV, "if e.retryable:\n                raise err(503", "if False:\n                raise err(503")],
    "做过的题不读": [(ST, "recent = frozenset(r.item_id for r in rows if r.recorded_at >= since)", "recent = frozenset()")],
    "hint_followup 任务不读": [(ST, 'for t in tasks if t["reason"] == "hint_followup"\n', 'for t in tasks if False\n')],
    "复测标记不读": [(ST, 'and t["due_at"] <= now)', "and False)")],
    "作答通道不读（拍照也算键盘）": [(ST, "channel=channels.get(r.attempt_id)", 'channel="keypad"')],
    "鉴权不查": [(API, "if not authorized(request):", "if False:")],
    "也认编排层令牌": [(APP, 'practice_api.register(app, lambda r: _bearer_ok(r.headers.get("authorization"), settings.service_credential), state)',
                    'practice_api.register(app, lambda r: _bearer_ok(r.headers.get("authorization"), settings.service_credential) or '
                    '_bearer_ok(r.headers.get("authorization"), settings.orchestrator_client_credential or "-"), state)')],
    "请求可以给 purpose、channel": [(API, '_keys(b, {"student_ref", "subject", "ticket", "answer", "idempotency_key", "usage_id"})',
                                  '_keys(b, {"student_ref", "subject", "ticket", "answer", "idempotency_key", "usage_id"}, '
                                  '{"purpose", "channel", "hint_level_used", "evidence_level"})')],
    "合成题面不核对库名": [(APP, 'if not SYNTHETIC_DB.fullmatch(db or ""):\n            raise RuntimeError("再练合成题面',
                       'if False:\n            raise RuntimeError("再练合成题面')],
    "题面带出夹具字段": [(PR, "return _clean(view)", "return dict(it)")],
}


def _copy(tmp: Path) -> Path:
    root = tmp / "repo"
    dst = root / "services" / "learning"
    shutil.copytree(HERE, dst, ignore=shutil.ignore_patterns("__pycache__", ".venv", ".cache", "*.pyc"))
    for name in ("db", "adapters", "packages", "evals", "docs"):
        (root / name).symlink_to(REPO / name)
    return dst


def _run(dst: Path, tests: list[str]) -> tuple[int, str]:
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "PYTHONDONTWRITEBYTECODE": "1", "LEARNING_TEST_STRICT": "1",
           "HOME": os.environ.get("HOME", "/tmp")}
    r = subprocess.run([str(HERE / ".venv/bin/python"), "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider", *tests],
                       cwd=dst, capture_output=True, text=True, env=env, timeout=900)
    return r.returncode, r.stdout[-2000:]


def one(name: str, edits: list[tuple[str, str, str]], tests: list[str]) -> str | None:
    with tempfile.TemporaryDirectory(prefix="practice-mut.") as t:
        dst = _copy(Path(t))
        for fname, pattern, replacement in edits:
            f = dst / fname
            text = f.read_text(encoding="utf-8")
            if pattern not in text:
                return f"变异本身写错（找不到原文）：{name}"
            f.write_text(text.replace(pattern, replacement, 1), encoding="utf-8")
        rc, _ = _run(dst, tests)
        if rc != 1:
            return f"{'漏检' if rc == 0 else f'不是被断言发现（退出码 {rc}）'}：{name}"
    return None


def group(label: str, mutations: dict[str, list[tuple[str, str, str]]], tests: list[str], workers: int) -> int:
    with tempfile.TemporaryDirectory(prefix="practice-mut.") as t:
        rc, out = _run(_copy(Path(t)), tests)
    if rc != 0:
        print(out)
        print(f"practice-mutation（{label}）：失败（对照组没有通过，rc={rc}）")
        return 1
    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(lambda kv: one(kv[0], kv[1], tests), mutations.items()))
    missed = [r for r in results if r]
    for m in missed:
        print(f"  {m}")
    if missed:
        print(f"practice-mutation（{label}）：失败，{len(missed)}/{len(mutations)} 个变异没有被发现")
        return 1
    print(f"practice-mutation（{label}）：通过（{len(mutations)} 个变异全部被发现）")
    return 0


def main() -> int:
    if not (HERE / ".venv/bin/python").exists():
        print("practice-mutation: 跳过（没有安装学习服务依赖）")
        return 3
    rc = group("纯函数", PURE, PURE_TESTS, 6)
    if rc:
        return rc
    if not (HERE / DB_TESTS[0]).exists():
        print("practice-mutation（库上）：跳过（PR 2 的测试还没有合入）")
        return 0
    from glob import glob
    if not (os.environ.get("PGBIN") or glob("/usr/lib/postgresql/*/bin/initdb")):
        print("practice-mutation（库上）：跳过（本机没有 Postgres 服务端）")
        return 3
    return group("库上", DB, DB_TESTS, 4)


if __name__ == "__main__":
    sys.exit(main())
