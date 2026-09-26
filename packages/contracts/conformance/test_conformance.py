"""学科插件 API 一致性测试（specs/contract-conformance-sprint1/spec.md 范围第 2 条）。

同一套代码测任何一科的适配器：用例从契约生成，期望来自共享的平台侧核对库与夹具里的编码。
这里没有任何学科专用的判断。

范围说明（README“检查边界”）：
- 六个方法都做契约 schema 校验、坏请求、幂等；
- 平台侧核对与泄露检查目前只覆盖 grade、diagnose（共享库只实现了这两个方法的规则）；
- 异步：适配器返回 202 时按契约轮询 /jobs/{job_id}，用任务结果继续校验。
"""
from __future__ import annotations

import copy

import pytest

from starbuddy_contract.checks import SUPPORTED_METHODS, ContractViolation, verify
from starbuddy_contract.contract import CONTRACT_VERSION, plugin_contract
from support import (
    METERED,
    RESPONSE_SCHEMA,
    SUBJECTS,
    attempts_of,
    build_request,
    conflict_variant,
    leak_problems,
    string_leaves,
    student_inputs,
)

# 合成标记由中文和纯 ASCII 两段组成：错误体即使把作答转义成 \\uXXXX（整体转义或二次编码），
# 纯 ASCII 那段也原样可见；另外对解析后的字符串再比对一次（契约守护 B1）
ECHO_CANARY_ASCII = "CONFORMANCE-CANARY-51f7"
ECHO_CANARY = f"学生作答-{ECHO_CANARY_ASCII}"


def _tag(request, method: str) -> None:
    request.node.user_properties.append(("method", method))


def _problem_errors(reply) -> list[str]:
    errs = plugin_contract().validate("Problem", reply.body)
    if not reply.content_type.startswith("application/problem+json"):
        errs.append(f"content-type 应为 application/problem+json，实际 {reply.content_type or '无'}")
    return errs


def _echoes(reply, inputs: list[str]) -> bool:
    decoded = string_leaves(reply.body) if reply.body is not None else []
    return any(s in reply.raw or any(s in d for d in decoded) for s in inputs)


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
def test_unimplemented_methods_are_refused(request, client, capabilities, method):
    """未实现的方法返回 METHOD_NOT_IMPLEMENTED（状态码 501 是本测试的约定，已列入 v1.0 评审清单 N13）。"""
    if capabilities["methods"][method]["implemented"]:
        pytest.skip("已实现")
    _tag(request, method)
    r = client.call("POST", method, {})
    assert r.status == 501, f"{method} 未实现却返回 {r.status}"
    assert _problem_errors(r) == []
    assert r.body["code"] == "METHOD_NOT_IMPLEMENTED"


# ---------- 请求校验：由契约变出的坏请求 ----------
def _negatives(method: str, subject: str):
    other = next(s for s in SUBJECTS if s != subject)

    def drop(k):
        return lambda b: b["context"].pop(k)

    def setc(k, v):
        return lambda b: b["context"].__setitem__(k, v)

    yield "no_idempotency_key", drop("idempotency_key")
    yield "short_idempotency_key", setc("idempotency_key", "short")
    if method in METERED:
        yield "no_usage_id", drop("usage_id")
    yield "wrong_contract_version", setc("contract_version", "0.8.0")
    yield "data_class_L0", setc("data_class", "L0")
    yield "data_class_L1", setc("data_class", "L1")
    yield "student_ref_not_uuid", setc("student_ref", "not-a-uuid")

    def wrong_subject(b):
        b["context"]["subject"] = other
        if "content_version" in b["context"]:
            b["context"]["content_version"]["subject"] = other
    yield "wrong_subject", wrong_subject
    yield "extra_top_level_field", lambda b: b.__setitem__("commentary", "free text")


def test_bad_requests_are_rejected(request, client, capabilities, fx, method_case):
    method = method_case["method"]
    if not capabilities["methods"][method]["implemented"]:
        pytest.skip("未实现")
    _tag(request, method)
    base = build_request(fx, method_case)
    for a in attempts_of(method, base):
        if "text" in a.get("response", {}):
            a["response"]["text"] = ECHO_CANARY
    inputs = [ECHO_CANARY, ECHO_CANARY_ASCII] + [s for s in student_inputs(method, base) if len(s) >= 8]
    failures = []
    for name, mutate in _negatives(method, fx.subject):
        b = copy.deepcopy(base)
        mutate(b)
        r = client.call("POST", method, b)
        if not (400 <= r.status < 500):
            failures.append(f"{name}: 返回 {r.status}")
            continue
        if _problem_errors(r):
            failures.append(f"{name}: 错误体不是契约 Problem（{_problem_errors(r)[:2]}）")
        if _echoes(r, inputs):
            failures.append(f"{name}: 错误体回显了学生作答")
    assert failures == []


# ---------- 响应：契约 + 平台侧核对 + 夹具期望 ----------
def test_case(request, client, capabilities, fx, case):
    method = case["method"]
    _tag(request, method)
    if not capabilities["methods"][method]["implemented"]:
        pytest.fail(f"夹具里有 {method} 的用例，但适配器声明未实现")
    body = build_request(fx, case)
    first, resp, _job = client.invoke(method, body)
    assert resp is not None, f"返回 {first.status}"
    assert plugin_contract().validate(RESPONSE_SCHEMA[method], resp) == []
    if method not in SUPPORTED_METHODS:
        return  # 平台侧核对与泄露检查尚未覆盖这个方法（README“检查边界”）
    try:
        v = verify(method, body, resp, registered_items=fx.items, registered_nodes=fx.skill_nodes)
    except ContractViolation as e:
        pytest.fail(f"平台侧核对不通过：{e}")
    # 适配器自己就该是平台判定的样子：准入状态、效力、复核原因都不能比平台更松
    # （平台落库时会改写；这里要求适配器不依赖平台兜底，因为登记数据本来就来自该科自己）
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

    problems = leak_problems(resp, inputs=student_inputs(method, body), secrets=fx.secrets + case.get("secrets", []))
    assert problems == []


# ---------- 幂等 ----------
def test_idempotency(request, client, capabilities, fx, method_case):
    method = method_case["method"]
    if not capabilities["methods"][method]["implemented"]:
        pytest.skip("未实现")
    _tag(request, method)
    body = build_request(fx, method_case)
    f1, r1, job1 = client.invoke(method, body)
    assert r1 is not None, f"返回 {f1.status}"
    f2, r2, job2 = client.invoke(method, body)
    assert r2 is not None and r2.get("evidence_ids") == r1.get("evidence_ids"), "同 key 同请求体重试，证据 ID 变了"
    if job1:
        assert job2 == job1, "同 key 重试，异步任务的 job_id 变了（契约 JobAccepted）"
    # 换了 request_id 视为同一请求（本测试的约定，已列入 v1.0 评审清单 N13）
    retry = copy.deepcopy(body)
    retry["context"]["request_id"] = "00000000-0000-4000-8000-00000000abcd"
    _, r3, _ = client.invoke(method, retry)
    assert r3 is not None and r3.get("evidence_ids") == r1.get("evidence_ids"), "换了 request_id，证据 ID 变了"
    changed = conflict_variant(method_case, body)
    assert changed is not None, f"夹具 {method_case['id']} 需要提供 conflict_body（同 key 的另一个请求体）"
    c = client.call("POST", method, changed)
    assert c.status == 409, f"同 key 换了请求体，返回 {c.status}"
    assert _problem_errors(c) == [] and c.body["code"] == "IDEMPOTENCY_CONFLICT"
    assert not _echoes(c, [s for s in student_inputs(method, changed) if len(s) >= 8]), "409 错误体回显了学生作答"
