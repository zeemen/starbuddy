"""学科插件 API 一致性测试（specs/contract-conformance-sprint1/spec.md 范围第 2 条）。

同一套代码测任何一科的适配器：用例从契约生成，期望来自共享的平台侧核对库与夹具里的编码。
这里没有任何学科专用的判断。
"""
from __future__ import annotations

import copy

import pytest

from starbuddy_contract.checks import ContractViolation, verify
from starbuddy_contract.contract import CONTRACT_VERSION, plugin_contract
from support import METERED, SUBJECTS, attempts_of, build_request, leak_problems, student_inputs

ECHO_CANARY = "学生作答-CONFORMANCE-CANARY-51f7"
RESPONSE_SCHEMA = {"grade": "GradeResponse", "diagnose": "DiagnoseResponse"}


def _tag(request, method: str) -> None:
    request.node.user_properties.append(("method", method))


def _problem_ok(body) -> list[str]:
    return plugin_contract().validate("Problem", body)


# ---------- 能力声明 ----------
def test_capabilities_match_contract(capabilities, fx):
    assert plugin_contract().validate("Capabilities", capabilities) == []
    assert capabilities["subject"] == fx.subject
    assert capabilities["contract_version"] == CONTRACT_VERSION


def test_every_implemented_method_has_fixtures(capabilities, fx):
    """覆盖率只能由真实跑过的用例证明：声明已实现的方法，夹具里必须有用例。"""
    missing = [m for m, c in capabilities["methods"].items() if c["implemented"] and not fx.cases_for(m)]
    assert missing == [], f"这些方法声明已实现，但夹具里没有用例：{missing}"


def test_requires_service_credential(client):
    r = client.call("GET", "capabilities", auth=False)
    assert r.status in (401, 403)


# ---------- 未实现的方法 ----------
def test_unimplemented_methods_return_501(request, client, capabilities, method):
    if capabilities["methods"][method]["implemented"]:
        pytest.skip("已实现")
    _tag(request, method)
    r = client.call("POST", method, {})
    assert r.status == 501, f"{method} 未实现却返回 {r.status}"
    assert _problem_ok(r.body) == []
    assert r.body["code"] == "METHOD_NOT_IMPLEMENTED"


# ---------- 请求校验：由契约变出的坏请求 ----------
def _negatives(method: str, body: dict, subject: str):
    other = next(s for s in SUBJECTS if s != subject)
    def drop(path):
        def f(b):
            del b["context"][path]
        return f
    def setc(k, v):
        def f(b):
            b["context"][k] = v
        return f
    yield "no_idempotency_key", drop("idempotency_key")
    yield "short_idempotency_key", setc("idempotency_key", "short")
    if method in METERED:
        yield "no_usage_id", drop("usage_id")
    yield "wrong_contract_version", setc("contract_version", "0.8.0")
    yield "data_class_below_l2", setc("data_class", "L1")
    yield "student_ref_not_uuid", setc("student_ref", "not-a-uuid")
    def wrong_subject(b):
        b["context"]["subject"] = other
        if "content_version" in b["context"]:
            b["context"]["content_version"]["subject"] = other
    yield "wrong_subject", wrong_subject
    def extra_field(b):
        b["commentary"] = "free text"
    yield "extra_top_level_field", extra_field


def test_bad_requests_are_rejected(request, client, capabilities, fx, method_case):
    method = method_case["method"]
    if not capabilities["methods"][method]["implemented"]:
        pytest.skip("未实现")
    _tag(request, method)
    base = build_request(fx, method_case)
    # 填空作答换成一个醒目的合成标记，用来检查错误体有没有回显学生作答（短作答会和 UUID 偶然重合）
    for a in attempts_of(method, base):
        if "text" in a.get("response", {}):
            a["response"]["text"] = ECHO_CANARY
    inputs = [ECHO_CANARY] + [s for s in student_inputs(method, base) if len(s) >= 8]
    failures = []
    for name, mutate in _negatives(method, base, fx.subject):
        b = copy.deepcopy(base)
        mutate(b)
        r = client.call("POST", method, b)
        if not (400 <= r.status < 500):
            failures.append(f"{name}: 返回 {r.status}")
            continue
        if _problem_ok(r.body):
            failures.append(f"{name}: 错误体不是契约 Problem")
        if any(s in r.raw for s in inputs):
            failures.append(f"{name}: 错误体回显了学生作答")
    assert failures == []


# ---------- 响应：契约 + 平台侧核对 + 夹具期望 ----------
def test_case(request, client, capabilities, fx, case):
    method = case["method"]
    _tag(request, method)
    if not capabilities["methods"][method]["implemented"]:
        pytest.fail(f"夹具里有 {method} 的用例，但适配器声明未实现")
    body = build_request(fx, case)
    r = client.call("POST", method, body)
    assert r.status == 200, f"返回 {r.status}"
    resp = r.body
    assert plugin_contract().validate(RESPONSE_SCHEMA[method], resp) == []
    try:
        v = verify(method, body, resp, registered_items=fx.items, registered_nodes=fx.skill_nodes)
    except ContractViolation as e:
        pytest.fail(f"平台侧核对不通过：{e}")
    # 适配器自己就该是平台判定的样子：不能比平台更松（否则落库时会被改写）
    assert v.admission_rewritten == [], "证据的准入状态比平台登记更松"
    assert resp["semantics"] == v.semantics, "semantics 比平台判定更松"
    assert set(v.review_reasons) <= set(resp.get("review_reasons", [])), "缺少平台要求的复核原因"

    exp = case.get("expect", {})
    if "verdict" in exp:
        assert resp["result"]["verdict"] == exp["verdict"]
    for reason in exp.get("review_reasons_include", []):
        assert reason in resp.get("review_reasons", []), f"缺少复核原因 {reason}"
    if "error_categories" in exp:
        got = {a["cross_subject_category"] for a in resp["result"].get("error_attributions", [])}
        assert got == set(exp["error_categories"])
    if method == "diagnose":
        obs = resp["result"]["observations"]
        for i, ev_id in enumerate(v.response_evidence_ids):
            mine = [o for o in obs if ev_id in o["evidence_ids"]]
            assert mine, f"第 {i + 1} 道作答没有任何能力点观测"
            if "outcomes" in exp:
                assert {o["outcome"] for o in mine} == {exp["outcomes"][i]}, f"第 {i + 1} 道作答的观测结论不符"
            bounds = exp.get("max_confidence") or []
            if i < len(bounds) and bounds[i] is not None:
                assert max(o["confidence"] for o in mine) <= bounds[i] + 1e-9, f"第 {i + 1} 道作答的置信度超过上限"

    times = [a["occurred_at"] for a in attempts_of(method, body)]
    problems = leak_problems(resp, inputs=student_inputs(method, body), times=times,
                             secrets=fx.secrets + case.get("secrets", []))
    assert problems == []


# ---------- 幂等 ----------
def test_idempotency(request, client, capabilities, fx, method_case):
    method = method_case["method"]
    if not capabilities["methods"][method]["implemented"]:
        pytest.skip("未实现")
    _tag(request, method)
    body = build_request(fx, method_case)
    first = client.call("POST", method, body)
    assert first.status == 200
    again = client.call("POST", method, body)
    assert again.status == 200 and again.body["evidence_ids"] == first.body["evidence_ids"], "同 key 同请求体重试，证据 ID 变了"
    retry = copy.deepcopy(body)
    retry["context"]["request_id"] = "00000000-0000-4000-8000-00000000abcd"
    r = client.call("POST", method, retry)
    assert r.status == 200 and r.body["evidence_ids"] == first.body["evidence_ids"], "换了 request_id，证据 ID 变了"
    changed = copy.deepcopy(body)
    for a in attempts_of(method, changed):
        a["elapsed_sec"] = int(a.get("elapsed_sec", 0)) + 7
    c = client.call("POST", method, changed)
    assert c.status == 409, f"同 key 换了请求体，返回 {c.status}"
    assert _problem_ok(c.body) == [] and c.body["code"] == "IDEMPOTENCY_CONFLICT"
