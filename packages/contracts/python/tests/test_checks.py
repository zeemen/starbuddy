"""共享平台侧核对库的单测（冲刺 1-A 验收：共享库本身有单测）。合成数据（规则 8）。

每条规则一个正例一个反例；学习服务的集成测试与一致性测试另外覆盖端到端行为。
"""
from __future__ import annotations

import copy
import sys
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from starbuddy_contract.checks import ContractViolation, parse_time, verify  # noqa: E402

T = "2026-10-08T11:00:00+00:00"
E1, E2 = str(uuid.uuid4()), str(uuid.uuid4())
ITEMS = {"SYN-1": "source_unverified", "SYN-A": "admitted"}
NODES = {"K-1"}


def request(item="SYN-1", text="42", appeal=None, data_class="L2"):
    r = {
        "context": {"request_id": str(uuid.uuid4()), "contract_version": "0.9.0", "subject": "aya", "student_ref": str(uuid.uuid4()),
                    "data_class": data_class, "usage_id": str(uuid.uuid4()), "idempotency_key": "synthetic-key-0000001"},
        "attempt": {"item_ref": f"aya:{item}", "occurred_at": T, "response": {"text": text}},
        "rubric_version": "syn-verifier@1.0.0",
    }
    if appeal is not None:
        r["appeal"] = appeal
    return r


def response(item="SYN-1", text="42", admission="source_unverified", score=1):
    common = {"occurred_at": T, "item_ref": f"aya:{item}", "skill_node": "aya:K-1", "admission": admission}
    reasons = [] if admission == "admitted" else ["content_not_admitted"]
    out = {
        "contract_version": "0.9.0",
        "evidence": [
            {"evidence_id": E1, "kind": "response_text", **common, "payload": {"text": text}},
            {"evidence_id": E2, "kind": "verifier_trace", **common, "payload": {"sampled_points": 8, "agreeing_points": 8}},
        ],
        "evidence_ids": [E1, E2],
        "confidence": 1,
        "source_version": "verifier:syn-verifier@1.0.0",
        "model_version": "algo:syn-verifier@1.0.0",
        "human_review_required": bool(reasons),
        "semantics": "formal" if admission == "admitted" else "practice",
        "result": {
            "verdict": "correct", "rubric_version": "syn-verifier@1.0.0",
            "score": {"total": score, "max_total": 1, "calibrated": False},
            "dimensions": [{"dimension_id": "answer", "score": score, "max_score": 1, "judged_by": "verifier", "subjective": False,
                            "rule_ref": "syn-verifier@1.0.0", "confidence": 1, "evidence_ids": [E1, E2]}],
            "verifier": {"verifier_id": "syn-verifier", "version": "1.0.0", "reason_codes": []},
        },
    }
    if reasons:
        out["review_reasons"] = reasons
    return out


def run(req, resp, **kw):
    return verify("grade", req, resp, registered_items=kw.get("items", ITEMS), registered_nodes=kw.get("nodes", NODES),
                  prior_admission=kw.get("prior"))


def test_valid_result_passes_and_maps_attempt_to_response_evidence():
    v = run(request(), response())
    assert v.response_evidence_ids == [E1]
    assert v.semantics == "practice" and v.human_review_required and "content_not_admitted" in v.review_reasons


def _mut(fn):
    r = response()
    fn(r)
    return r


@pytest.mark.parametrize("name,mutate", [
    ("schema", lambda r: r["result"].__setitem__("comment", "free text")),
    ("dangling", lambda r: r["result"]["dimensions"][0].__setitem__("evidence_ids", [str(uuid.uuid4())])),
    ("subject", lambda r: r["evidence"][1].__setitem__("skill_node", "wenli:K-1")),
    ("unregistered_node", lambda r: r["evidence"][1].__setitem__("skill_node", "aya:K-404")),
    ("media_ref", lambda r: r["evidence"][1].__setitem__("media_ref", "objstore://other/p.jpg")),
    ("rubric", lambda r: r["result"].__setitem__("rubric_version", "syn-verifier@0.9.0")),
    ("fabricated_text", lambda r: r["evidence"][0]["payload"].__setitem__("text", "正确答案")),
    ("bad_time", lambda r: r["evidence"][1].__setitem__("occurred_at", "epoch")),
    ("nan", lambda r: r.__setitem__("confidence", float("nan"))),
    ("over_max_total", lambda r: r["result"]["score"].__setitem__("total", 5)),
    ("over_max_dimension", lambda r: r["result"]["dimensions"][0].__setitem__("score", 5)),
])
def test_violations_are_rejected(name, mutate):
    with pytest.raises(ContractViolation):
        run(request(), _mut(mutate))


def test_admission_only_gets_stricter():
    v = run(request(item="SYN-1"), response(item="SYN-1", admission="admitted"))
    assert v.admission_rewritten == [E1, E2]
    assert v.semantics == "practice" and "content_not_admitted" in v.review_reasons
    ok = run(request(item="SYN-A"), response(item="SYN-A", admission="admitted"))
    assert ok.semantics == "formal" and not ok.human_review_required


def test_appeal_always_requires_review():
    v = run(request(item="SYN-A", appeal=True), response(item="SYN-A", admission="admitted"))
    assert v.human_review_required and "student_appeal" in v.review_reasons


def test_prior_evidence_admission_comes_from_platform():
    prior_id = str(uuid.uuid4())
    req = request(item="SYN-A")
    req["prior_evidence"] = [{"evidence_id": prior_id, "kind": "verifier_trace", "occurred_at": T, "item_ref": "aya:SYN-1",
                              "admission": "admitted", "payload": {"sampled_points": 0, "agreeing_points": 0}}]
    resp = response(item="SYN-A", admission="admitted")
    resp["result"]["dimensions"][0]["evidence_ids"].append(prior_id)
    with pytest.raises(ContractViolation):  # 平台库里查不到：不算闭合
        run(req, copy.deepcopy(resp))
    v = run(req, resp, prior={prior_id: "source_unverified"})
    assert v.semantics == "practice"


def test_l3_evidence_needs_l3_request():
    resp = response()
    resp["evidence"][1]["kind"] = "asr_transcription"
    resp["evidence"][1]["payload"] = {"text": "synthetic"}
    with pytest.raises(ContractViolation):
        run(request(data_class="L2"), resp)


def test_other_methods_are_explicitly_unsupported():
    with pytest.raises(ValueError):
        verify("plan", {}, {}, registered_items={}, registered_nodes=set())


@pytest.mark.parametrize("bad", ["epoch", "2026-10-08T11:00:00", "infinity", "2026-10-08 11:00:00+00:00"])
def test_parse_time_is_strict(bad):
    with pytest.raises(ValueError):
        parse_time(bad)


# ---------- 双版本（specs/plugin-api-v1.0-step2；契约审查“共享库补单测”） ----------
from starbuddy_contract.contract import UnsupportedContractVersion, plugin_contract  # noqa: E402

A1 = str(uuid.uuid4())


def v1(req, resp):
    req["context"].update(contract_version="1.0.0", idempotency_key=str(uuid.uuid4()))
    req["attempt"]["attempt_id"] = A1
    resp["contract_version"] = "1.0.0"
    for e in resp["evidence"]:
        e["attempt_id"] = A1
    return req, resp


def test_plugin_contract_version_gate(monkeypatch):
    assert plugin_contract("0.9.0").version == "0.9.0"
    with pytest.raises(UnsupportedContractVersion):
        plugin_contract("2.0.0")
    assert plugin_contract(None).version == "0.9.0", "不指定版本时用环境默认版本"
    monkeypatch.delenv("STARBUDDY_ALLOW_CANDIDATE_CONTRACT", raising=False)
    with pytest.raises(UnsupportedContractVersion):
        plugin_contract("1.0.0")
    monkeypatch.setenv("STARBUDDY_ALLOW_CANDIDATE_CONTRACT", "1")
    assert plugin_contract("1.0.0").version == "1.0.0"


def test_v1_result_passes(monkeypatch):
    monkeypatch.setenv("STARBUDDY_ALLOW_CANDIDATE_CONTRACT", "1")
    assert run(*v1(request(), response())).response_evidence_ids == [E1]


@pytest.mark.parametrize("name,mutate", [
    ("response_version_differs", lambda r: r.__setitem__("contract_version", "0.9.0")),
    ("missing_attempt_id", lambda r: r["evidence"][0].pop("attempt_id")),
    ("foreign_attempt_id", lambda r: r["evidence"][0].__setitem__("attempt_id", str(uuid.uuid4()))),
    ("trace_foreign_attempt_id", lambda r: r["evidence"][1].__setitem__("attempt_id", str(uuid.uuid4()))),
])
def test_v1_violations_are_rejected(monkeypatch, name, mutate):
    monkeypatch.setenv("STARBUDDY_ALLOW_CANDIDATE_CONTRACT", "1")
    req, resp = v1(request(), response())
    mutate(resp)
    with pytest.raises(ContractViolation):
        run(req, resp)


def test_v0_response_to_v0_request_must_say_v0():
    resp = response()
    resp["contract_version"] = "1.0.0"
    with pytest.raises(ContractViolation):
        run(request(), resp)
