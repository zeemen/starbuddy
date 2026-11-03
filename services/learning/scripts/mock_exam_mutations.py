"""模考限时整卷第一步（PLT-18，specs/mock-exam-plt18）的变异测试：把截止边界（< / <=）、宽限、交卷后作答、重复作答取最后一次、
时钟回拨、票据签名比较、kid 选钥、绑定（换卷、换学生）、时序一致性与过期、清单分值和与考情 / 准入 / 题型校验、汇总的折算与舍入、
采分点归节点、待复核 / 暂定 / 未校准标注、缺溯源字段、节点映射类型等关键检查逐条改坏，断言模考测试能发现。

做法与错题再练、复测的变异脚本相同（复用 scripts/practice_mutations.py 的临时目录复制与运行）：每个变异在临时目录里改一处源码再跑测试；
只有“测试失败”（退出码 1）算发现。先跑一次不变异的对照组，必须通过。只有纯函数一组（本模块不连库）。
"""
from __future__ import annotations

import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import practice_mutations as PM  # noqa: E402

HERE = PM.HERE
TESTS = ["tests/test_mock_exam_manifest.py", "tests/test_mock_exam_session.py", "tests/test_mock_exam_summary.py"]
CF, MF, SE, SU = ("learning/mock_exam/config.py", "learning/mock_exam/manifest.py", "learning/mock_exam/session.py",
                  "learning/mock_exam/summary.py")

ADVANCE_EXPIRE = ('if s.state == "in_progress" and s.deadline_ms is not None and t >= s.deadline_ms:\n'
                  '        return replace(s, state="expired", ended_ms=s.deadline_ms, seen_ms=t)\n    return replace(s, seen_ms=t)')
SUBMIT_EXPIRE = ('if s.state == "in_progress" and s.deadline_ms is not None and t >= s.deadline_ms:\n'
                 '        return replace(s, state="expired", ended_ms=s.deadline_ms, seen_ms=t)\n    _require_open(s, t)')
VERIFY = "if not hmac.compare_digest(_sign(key, head).encode(), parts[3].encode()):"
SEAL = 'head = f"{PREFIX}.{keys.current_kid}.{_b64(payload.encode())}"\n    return f"{head}.{_sign(bytes(keys.current), head)}"'
PROV = 'PROVENANCE = ("evidence_ids", "confidence", "source_version", "model_version", "human_review_required")'
GRADING_CHECK = 'if g["grading"] not in GRADINGS or not isinstance(g["answer_ref"], str) or not isinstance(g["calibrated"], bool):'

MUTATIONS: dict[str, list[tuple[str, str, str]]] = {
    # ---------- 截止边界与宽限 ----------
    "作答截止 < 改 <=（截止时刻仍收）": [(SE, "if s.deadline_ms is None or t >= s.deadline_ms:", "if s.deadline_ms is None or t > s.deadline_ms:")],
    "作答截止提前 1ms": [(SE, "if s.deadline_ms is None or t >= s.deadline_ms:", "if s.deadline_ms is None or t >= s.deadline_ms - 1:")],
    "作答不查截止": [(SE, "if s.deadline_ms is None or t >= s.deadline_ms:", "if s.deadline_ms is None:")],
    "过期判定 < 改 <=（截止时刻不过期）": [(SE, ADVANCE_EXPIRE, ADVANCE_EXPIRE.replace("t >= s.deadline_ms", "t > s.deadline_ms"))],
    "截止时刻交卷算按时交": [(SE, SUBMIT_EXPIRE, SUBMIT_EXPIRE.replace("t >= s.deadline_ms", "t > s.deadline_ms"))],
    "到截止不自动过期": [(SE, ADVANCE_EXPIRE, ADVANCE_EXPIRE.replace('state="expired"', 'state="in_progress"'))],
    "过期结束时刻记发现时刻": [(SE, ADVANCE_EXPIRE, ADVANCE_EXPIRE.replace("ended_ms=s.deadline_ms", "ended_ms=t"))],
    "宽限不计入截止": [(SE, "paper.duration_minutes * 60_000 + cfg.grace_seconds * 1000", "paper.duration_minutes * 60_000")],
    "宽限按分钟计": [(SE, "cfg.grace_seconds * 1000", "cfg.grace_seconds * 60_000")],
    "限时写死 120 分钟": [(SE, "paper.duration_minutes * 60_000", "120 * 60_000")],
    "作答时重算截止（客户端时刻参与）": [(SE, "return replace(s, answers=tuple(sorted(kept, key=lambda a: a.item_id)), seen_ms=t)",
                                    "return replace(s, answers=tuple(sorted(kept, key=lambda a: a.item_id)), seen_ms=t, "
                                    "deadline_ms=t + paper.duration_minutes * 60_000)")],
    # ---------- 状态转移 ----------
    "交卷后仍可作答": [(SE, 'if s.state == "submitted":\n        raise SessionError("ALREADY_SUBMITTED")',
                    'if False:\n        raise SessionError("ALREADY_SUBMITTED")')],
    "过期后不报过期": [(SE, 'if s.state == "expired":\n        raise SessionError("EXPIRED")', 'if False:\n        raise SessionError("EXPIRED")')],
    "未开始也可作答": [(SE, 'if s.state == "created":\n        raise SessionError("NOT_STARTED")', 'if False:\n        raise SessionError("NOT_STARTED")')],
    "可以重复开始": [(SE, 'if s.state != "created":\n        raise SessionError("ALREADY_STARTED")',
                   'if False:\n        raise SessionError("ALREADY_STARTED")')],
    "交卷记的不是交卷时刻": [(SE, 'return replace(s, state="submitted", ended_ms=t, seen_ms=t)',
                         'return replace(s, state="submitted", ended_ms=s.deadline_ms, seen_ms=t)')],
    "重复作答取第一次": [(SE, "kept = [a for a in s.answers if a.item_id != item_id]\n    kept.append(Answer(item_id, answer_ref, t))",
                      "kept = list(s.answers)\n    if all(a.item_id != item_id for a in s.answers):\n"
                      "        kept.append(Answer(item_id, answer_ref, t))")],
    "重复作答不去重": [(SE, "kept = [a for a in s.answers if a.item_id != item_id]", "kept = list(s.answers)")],
    "时钟回拨不拦": [(SE, "if t < s.seen_ms:", "if False:")],
    "作答不核对题在卷内": [(SE, 'if paper.item(item_id) is None:\n        raise SessionError("ITEM_NOT_IN_PAPER")',
                        'if False:\n        raise SessionError("ITEM_NOT_IN_PAPER")')],
    "操作不核对卷": [(SE, 'if (s.paper_id, s.paper_version, s.subject) != (paper.paper_id, paper.version, paper.subject):\n'
                       '        raise SessionError("PAPER_MISMATCH")', 'if False:\n        raise SessionError("PAPER_MISMATCH")')],
    # ---------- 票据：签名、kid、密钥 ----------
    "签名只比前 8 位": [(SE, "hmac.compare_digest(_sign(key, head).encode(), parts[3].encode())",
                     "hmac.compare_digest(_sign(key, head).encode()[:8], parts[3].encode()[:8])")],
    "签名比较写反（不等才收）": [(SE, VERIFY, VERIFY.replace("if not hmac", "if hmac"))],
    "不验签": [(SE, VERIFY, "if False:")],
    "签名不覆盖载荷": [(SE, 'head = f"{parts[0]}.{parts[1]}.{parts[2]}"', 'head = f"{parts[0]}.{parts[1]}."'),
                    (SE, SEAL, SEAL.replace("_sign(bytes(keys.current), head)",
                                            "_sign(bytes(keys.current), PREFIX + chr(46) + keys.current_kid + chr(46))"))],
    "不按 kid 选钥（一律当前）": [(SE, "key = keys.lookup(parts[1])", "key = bytes(keys.current)")],
    "上一把不核 kid": [(SE, "if self.previous_kid is not None and kid == self.previous_kid:", "if self.previous is not None:")],
    "用上一把签发": [(SE, SEAL, SEAL.replace("f\"{PREFIX}.{keys.current_kid}.", "f\"{PREFIX}.{keys.previous_kid or keys.current_kid}.")
                     .replace("_sign(bytes(keys.current), head)", "_sign(bytes(keys.previous or keys.current), head)"))],
    "密钥长度不查": [(SE, "if not isinstance(key, (bytes, bytearray)) or len(key) < MIN_KEY:", "if not isinstance(key, (bytes, bytearray)):")],
    "kid 不校验": [(SE, "if not isinstance(kid, str) or KID_RE.fullmatch(kid) is None:", "if not isinstance(kid, str):")],
    "两把密钥可相同": [(SE, "if len(pairs) == 2 and (self.previous_kid == self.current_kid or bytes(self.previous) == bytes(self.current)):",
                     "if False:")],
    "裸密钥也收": [(SE, 'if not isinstance(keys, SigningKeys):\n        raise SigningKeyError("签名密钥必须是 SigningKeys")\n    payload',
                  'if False:\n        raise SigningKeyError("签名密钥必须是 SigningKeys")\n    payload')],
    # ---------- 票据：绑定、过期、形状与时序 ----------
    "不核对学生（换学生）": [(SE, "paper.version, paper.subject) or s.student != student \\", "paper.version, paper.subject) \\")],
    "不核对卷版本（换卷）": [(SE, "if (s.paper_id, s.paper_version, s.subject) != (paper.paper_id, paper.version, paper.subject) or s.student",
                          "if (s.paper_id, s.subject) != (paper.paper_id, paper.subject) or s.student")],
    "不核对卷 ID（换卷）": [(SE, "if (s.paper_id, s.paper_version, s.subject) != (paper.paper_id, paper.version, paper.subject) or s.student",
                         "if (s.paper_version, s.subject) != (paper.version, paper.subject) or s.student")],
    "票据作答不核对卷内题": [(SE, " \\\n            or any(paper.item(a.item_id) is None for a in s.answers):", ":")],
    "票据不查过期": [(SE, "if t > issued + cfg.ticket_ttl_minutes * 60_000:", "if False:")],
    "票据过期 > 改 >=": [(SE, "if t > issued + cfg.ticket_ttl_minutes * 60_000:", "if t >= issued + cfg.ticket_ttl_minutes * 60_000:")],
    "票据签发在未来不拦": [(SE, "if issued > t + CLOCK_SKEW_MS:", "if False:")],
    "载荷不核对状态与时刻一致": [(SE, 'if started != (d["started_ms"] is not None) or started != (d["deadline_ms"] is not None):\n        return None',
                             'if False:\n        return None')],
    "载荷同题两条也收": [(SE, "if len({a.item_id for a in answers}) != len(answers) or (answers and not started):", "if False:")],
    "不查时序一致性": [(SE, "if not _timeline_ok(s):\n        return None", "if False:\n        return None")],
    "时序不查建立 ≤ 开始 ≤ 截止": [(SE, "if not s.created_ms <= s.started_ms <= s.deadline_ms:", "if False:")],
    "时序作答可在截止时刻": [(SE, "if not s.started_ms <= a.at_ms < s.deadline_ms:", "if not s.started_ms <= a.at_ms <= s.deadline_ms:")],
    "时序交卷可在截止时刻": [(SE, "s.started_ms <= s.ended_ms < s.deadline_ms):", "s.started_ms <= s.ended_ms <= s.deadline_ms):")],
    "时序过期结束不查": [(SE, 'if s.state == "expired" and s.ended_ms != s.deadline_ms:', "if False:")],
    "时序不查 seen": [(SE, "return all(s.seen_ms >= m for m in marks)", "return True")],
    # ---------- 清单 ----------
    "分值和不校验": [(MF, "_need(sum(it.paper_cents for it in items) == full,", "_need(True,")],
    "分值和允许差 1 分": [(MF, "_need(sum(it.paper_cents for it in items) == full,", "_need(abs(sum(it.paper_cents for it in items) - full) <= 100,")],
    "题目可以重复": [(MF, "_need(iid not in seen,", "_need(True,")],
    "满分不对考情": [(MF, "_need(full == exam.full_cents,", "_need(True,")],
    "限时不对考情": [(MF, "_need(dur == exam.duration_minutes,", "_need(True,")],
    "不查准入": [(MF, "_need((subject, iid) in admitted,", "_need(True,")],
    "准入不分学科": [(MF, "_need((subject, iid) in admitted,", "_need(iid in {i for _, i in admitted},")],
    "准入快照收非 admitted": [(MF, 'if r.get("admission") == "admitted":', "if True:")],
    "准入快照不要求题型": [(MF, 'and isinstance(r.get("question_type_code"), str) and CODE_RE.fullmatch(r["question_type_code"]) is not None,',
                         ",")],
    "题型码不查词表": [(MF, "_need(isinstance(code, str) and code in exam.question_type_codes,", "_need(isinstance(code, str),")],
    "题型码不对准入快照": [(MF, "_need(admitted[(subject, iid)] == code,", "_need(True,")],
    "合成卷在 pilot 也收": [(MF, "_need(not doc[\"synthetic\"] or env in ALLOWED_SYNTHETIC_ENVS,", "_need(True,")],
    "合成来源可不标 synthetic": [(MF, "_need(doc[\"source\"] != \"synthetic\" or doc[\"synthetic\"] is True,", "_need(True,")],
    "考情快照不核学科": [(MF, "_need(POLICY_SUBJECT[subject] == exam.policy_subject,", "_need(True,")],
    "三位小数分值截断后收": [(MF, "if t != t.to_integral_value():\n        return None", "if False:\n        return None")],
    "分值精度退回一位小数": [(MF, "    t = d * 100\n", "    t = d * 10\n")],
    "清单可夹带多余键": [(MF, "_need(isinstance(r, Mapping) and set(r) == ITEM_KEYS,", "_need(isinstance(r, Mapping) and ITEM_KEYS <= set(r),")],
    "学科映射写死在代码": [(MF, "POLICY_SUBJECT = _load_subjects()", 'POLICY_SUBJECT = {"aya": "math", "zhixing": "politics"}')],
    # ---------- 汇总：溯源与判分结果 ----------
    "缺溯源字段不按缺溯源拒": [(SU, 'if k not in g or g[k] is None:\n            raise SummaryError("MISSING_PROVENANCE", iid)',
                            'if False:\n            raise SummaryError("MISSING_PROVENANCE", iid)')],
    "溯源字段为空当作有": [(SU, "if k not in g or g[k] is None:", "if k not in g:")],
    "不要求 model_version": [(SU, PROV, PROV.replace(', "model_version"', "")),
                            (SU, 'if not (isinstance(g["model_version"], str) and MODEL_VERSION_RE.fullmatch(g["model_version"])):',
                             'if "model_version" in g and not (isinstance(g["model_version"], str) '
                             'and MODEL_VERSION_RE.fullmatch(g["model_version"])):')],
    "不要求 human_review_required": [(SU, PROV, PROV.replace(', "human_review_required"', "")),
                                    (SU, 'if not isinstance(g["human_review_required"], bool):',
                                     'if not isinstance(g.setdefault("human_review_required", False), bool):')],
    "模型版本不校验格式": [(SU, 'if not (isinstance(g["model_version"], str) and MODEL_VERSION_RE.fullmatch(g["model_version"])):',
                         'if not isinstance(g["model_version"], str):')],
    "模型版本正则与契约不同": [(SU, "|algo:[A-Za-z0-9._\\-]+@[A-Za-z0-9._\\-]+)$\")", "|algo:[A-Za-z0-9._\\-]+@[A-Za-z0-9._\\-]*)$\")")],
    "证据为空也收": [(SU, "if not (isinstance(ev, list) and ev and all(", "if not (isinstance(ev, list) and all(")],
    "置信度越界也收": [(SU, "or not 0 <= c <= 1:", "or c < 0:")],
    "非验证器 / Rubric 来源也收": [(SU, GRADING_CHECK, GRADING_CHECK.replace('g["grading"] not in GRADINGS or ', ""))],
    "不要求校准标记": [(SU, GRADING_CHECK, GRADING_CHECK.replace(' or not isinstance(g["calibrated"], bool)', ""))],
    "判分可夹带多余字段": [(SU, " or not set(g) <= set(REQUIRED) | OPTIONAL", "")],
    "采分点可夹带文本": [(SU, '"point_id" in p and set(p) <= POINT_KEYS and', '"point_id" in p and')],
    "采分点可重复": [(SU, "if len({pid for pid, _ in out}) != len(out):", "if False:")],
    "得分可超满分": [(SU, "(sc is None or sc < 0 or sc > mx)", "(sc is None or sc < 0)")],
    "判分满分 0 也收": [(SU, "if mx is None or mx <= 0:", "if mx is None or mx < 0:")],
    "得分 null 整卷拒绝": [(SU, 'if g["score"] is not None and (sc is None or', 'if (sc is None or')],
    # ---------- 汇总：折算、标注、节点 ----------
    "不折算（直接用判分得分）": [(SU, "earned = scale(g.score, g.max_score, item.paper_cents)", "earned = int(g.score * 100)")],
    "舍入改银行家舍入": [(SU, "rounding=ROUND_HALF_UP", 'rounding="ROUND_HALF_EVEN"')],
    "舍入改截断": [(SU, "rounding=ROUND_HALF_UP", 'rounding="ROUND_DOWN"')],
    "待复核不标人工复核": [(SU, "([REVIEW_HUMAN] if g.human_review_required else [])", "([])")],
    "低置信不标": [(SU, "([REVIEW_LOW_CONFIDENCE] if g.confidence < cfg.review_confidence_below else [])", "([])")],
    "低置信阈值 < 改 <=": [(SU, "g.confidence < cfg.review_confidence_below", "g.confidence <= cfg.review_confidence_below")],
    "得分 null 不标待复核": [(SU, "([REVIEW_SCORE_UNAVAILABLE] if g.score is None else [])", "([])")],
    "总分不标暂定": [(SU, '"provisional": bool(pending)', '"provisional": False')],
    "未校准不标": [(SU, "uncalibrated.append(iid)", "pass")],
    "超时不标": [(SU, '"timed_out": session.state == "expired"', '"timed_out": False')],
    "旧作答的判分也收（不取最后一次）": [(SU, "if g.answer_ref != a.answer_ref:", "if False:")],
    "作答了没判分不拒": [(SU, 'if a.item_id not in by_item:\n            raise SummaryError("GRADE_MISSING"',
                       'if False:\n            raise SummaryError("GRADE_MISSING"')],
    "未作答的判分也收": [(SU, 'if a is None:\n            raise SummaryError("GRADE_FOR_UNANSWERED", g.item_id)\n'
                            '        if g.answer_ref != a.answer_ref:',
                       'if a is None:\n            pass\n        elif g.answer_ref != a.answer_ref:')],
    "同题两条判分不拒": [(SU, 'if g.item_id in by_item:\n            raise SummaryError("GRADE_DUPLICATE"',
                       'if False:\n            raise SummaryError("GRADE_DUPLICATE"')],
    "判分版本不核对": [(SU, 'raise SummaryError("RUBRIC_MISMATCH", g.item_id)', "pass")],
    "判分版本认任何段": [(SU, 'seg.split(":", 1)[0] in ("rubric", "verifier") and ', "")],
    "未结束也汇总": [(SU, "if session.state not in FINISHED:", "if False:")],
    "汇总不核对卷": [(SU, "if (session.paper_id, session.paper_version, session.subject) != (paper.paper_id, paper.version, paper.subject):\n"
                       '        raise SummaryError("PAPER_MISMATCH")', 'if False:\n        raise SummaryError("PAPER_MISMATCH")')],
    "未作答题不计未作答数": [(SU, "unanswered += 1", "pass")],
    "未作答题不计节点可得分": [(SU, "credit(n, iid, earned, item.paper_cents, BASIS_WHOLE)",
                           "credit(n, iid, earned, item.paper_cents if g else 0, BASIS_WHOLE)")],
    "多节点题只计首节点": [(SU, "for n in mapped:", "for n in mapped[:1]:")],
    "节点映射值不查类型": [(SU, "if not isinstance(v, (list, tuple)) or not all(", "if not all(")],
    "不按采分点归节点": [(SU, "if not covered:\n                    snap = None", "if True:\n                    snap = None")],
    "采分点覆盖不查完整": [(SU, "covered = ({pid for pid, _ in g.points} == set(snap) and", "covered = (True and")],
    "采分点不核对判分满分": [(SU, "\n                           and sum(sc for sc, _ in snap.values()) == g.max_score)", ")")],
    "整题计入不标注": [(SU, "whole.append(iid)", "pass")],
    # ---------- 配置 ----------
    "合成配置在 pilot 可用": [(CF, "if env not in ALLOWED_ENVS and not cfg.signed:", "if False:")],
}


def main() -> int:
    if not (HERE / ".venv/bin/python").exists():
        print("mock-exam-mutation: 跳过（没有安装学习服务依赖）")
        return 3
    with tempfile.TemporaryDirectory(prefix="mock-exam-mut.") as t:
        rc, out = PM._run(PM._copy(Path(t)), TESTS)
    if rc != 0:
        print(out)
        print(f"mock-exam-mutation：失败（对照组没有通过，rc={rc}）")
        return 1
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(lambda kv: PM.one(kv[0], kv[1], TESTS), MUTATIONS.items()))
    missed = [r for r in results if r]
    for m in missed:
        print(f"  {m}")
    if missed:
        print(f"mock-exam-mutation：失败，{len(missed)}/{len(MUTATIONS)} 个变异没有被发现")
        return 1
    print(f"mock-exam-mutation：通过（{len(MUTATIONS)} 个变异全部被发现）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
