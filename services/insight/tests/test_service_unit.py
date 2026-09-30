"""服务骨架（不连库）：配置、监听地址、令牌与 caller、JSON 解析、Host 校验、契约加载、工具准入与 501、insight.record。"""
from __future__ import annotations

import uuid

import pytest

from insight.policy import synthetic_policy
from insight.service.app import allowed_hosts, caller_for, host_allowed, strict_json
from insight.service.config import DEFAULT_HOST, DEFAULT_PORT, Settings, check_host
from insight.service.contract import IMPLEMENTED, NOT_IMPLEMENTED, contract
from insight.service.tools import ALLOWED_CALLERS, ToolCore, UnavailableVerifier
from insight.shared import forbidden_matcher
from support import CREDS, agent_ctx, front_ctx

ENV = {"INSIGHT_ENV": "test", "INSIGHT_DATABASE_URL": "postgresql://unused", "INSIGHT_TOKEN_BFF": CREDS["bff"],
       "INSIGHT_TOKEN_ORCHESTRATOR": CREDS["orchestrator"]}


def test_settings_defaults_and_guards():
    s = Settings.from_env(dict(ENV))
    assert (s.host, s.port) == ("127.0.0.1", 8791) == (DEFAULT_HOST, DEFAULT_PORT) and set(s.tokens) == {"bff", "orchestrator"}
    for bad in ({"INSIGHT_ENV": "prod"}, {"INSIGHT_DATABASE_URL": ""}, {"INSIGHT_TOKEN_BFF": "short"},
                {"INSIGHT_TOKEN_BFF": CREDS["orchestrator"]}, {"INSIGHT_PORT": "0"}, {"INSIGHT_HOST": "0.0.0.0"}):
        with pytest.raises(RuntimeError):
            Settings.from_env({**ENV, **bad})
    with pytest.raises(RuntimeError):
        Settings.from_env({k: v for k, v in ENV.items() if not k.startswith("INSIGHT_TOKEN")})


@pytest.mark.parametrize("host", ["0.0.0.0", "::", "[::]", "*", "", "0", "0x0", "0.0"])
def test_wildcard_hosts_rejected(host):
    with pytest.raises(RuntimeError):
        check_host(host)


def test_specific_hosts_allowed():
    assert check_host("127.0.0.1") == "127.0.0.1" and check_host("10.1.2.3") == "10.1.2.3" and check_host("::1") == "::1"


def test_caller_from_token():
    tokens = dict(CREDS)
    assert caller_for(f"Bearer {CREDS['bff']}", tokens) == "bff"
    assert caller_for(f"Bearer {CREDS['orchestrator']}", tokens) == "orchestrator"
    assert caller_for("Bearer nope", tokens) is None and caller_for(None, tokens) is None
    assert caller_for(CREDS["bff"], tokens) is None


def test_strict_json():
    with pytest.raises(ValueError):
        strict_json(b'{"a": 1, "a": 2}')
    with pytest.raises(ValueError):
        strict_json(b'{"a": NaN}')
    assert strict_json(b'{"a": 1}') == {"a": 1}


def test_host_allow_list():
    allowed = allowed_hosts("127.0.0.1")
    assert host_allowed("127.0.0.1:8791", allowed) and host_allowed("localhost", allowed)
    assert not host_allowed("evil.example:8791", allowed) and not host_allowed("127.0.0.1:80x", allowed)
    assert not host_allowed(None, allowed)
    assert allowed_hosts("10.0.0.5") == ["10.0.0.5", "10.0.0.5:*"]


def test_contract_tools_and_examples_are_valid():
    c = contract()
    assert set(c.tools) == set(IMPLEMENTED) | set(NOT_IMPLEMENTED) == set(ALLOWED_CALLERS)
    examples = c.doc["components"]["examples"]
    for t in c.tools.values():
        name = t.request_schema.removesuffix("Request")
        assert c.validate(t.request_schema, examples[f"{name}Request"]["value"]) == []
        assert c.validate(t.response_schema, examples[f"{name}Response"]["value"]) == []


class NoStore:
    async def visible_claims(self, *a, **k):  # pragma: no cover - 不应被调用
        raise AssertionError("store must not be reached")

    async def evidence(self, *a, **k):  # pragma: no cover
        raise AssertionError("store must not be reached")


def core() -> ToolCore:
    return ToolCore(NoStore(), contract(), synthetic_policy(), forbidden_matcher(), UnavailableVerifier())


def body_for(tool: str, caller: str = "orchestrator") -> dict:
    c = contract()
    name = c.tools[tool].request_schema.removesuffix("Request")
    import copy
    b = copy.deepcopy(c.doc["components"]["examples"][f"{name}Request"]["value"])
    b["context"]["request_id"] = str(uuid.uuid4())
    return b


def test_all_contract_tools_are_implemented():
    assert NOT_IMPLEMENTED == () and set(IMPLEMENTED) == set(contract().tools)


@pytest.mark.anyio
@pytest.mark.parametrize("tool", ["insight.dispute", "insight.predict", "insight.reconcile"])
async def test_sie03_tools_without_store_return_501(tool):
    """没有配置 SIE-03 的库层（ops / sie03）时回 501，不碰数据库"""
    caller = sorted(ALLOWED_CALLERS[tool])[0]
    status, out = await core().call(tool, body_for(tool), caller)
    assert status == 501 and out["code"] == "METHOD_NOT_IMPLEMENTED" and contract().problem_errors(out) == []


@pytest.mark.anyio
@pytest.mark.parametrize("tool", ["insight.export", "insight.erase"])
async def test_export_erase_unwired_return_501(tool):
    """0035 的导出与删除库函数只授 BFF 会话：缺省实现不接线，一律 501（fail closed）；缺省授权核对不放行时先 404"""
    status, out = await core().call(tool, body_for(tool), "bff")
    assert status == 404 and "detail" not in out
    from support import StaticVerifier
    c = ToolCore(NoStore(), contract(), synthetic_policy(), forbidden_matcher(),
                 StaticVerifier({("00000000-0000-4000-8000-00000000a201", "guardian", "00000000-0000-4000-8000-00000000a001")}))
    status, out = await c.call(tool, body_for(tool), "bff")
    assert status == 501 and out["code"] == "METHOD_NOT_IMPLEMENTED" and contract().problem_errors(out) == []


def test_default_scheduler_is_stub_only_in_test():
    """接口桩只给 test 环境；其他环境缺省不可用（安全审查 F2）"""
    from insight.dispute import StubVerificationScheduler, UnavailableVerificationScheduler, default_scheduler
    assert isinstance(default_scheduler("test"), StubVerificationScheduler)
    assert isinstance(default_scheduler("dev"), UnavailableVerificationScheduler) and not default_scheduler("dev").available
    assert isinstance(core().scheduler, UnavailableVerificationScheduler)  # ToolCore 自己不带接口桩


def test_non_test_env_refuses_synthetic_sie03_config():
    """非 test 环境不设 INSIGHT_SIE03（缺省是合成占位）→ 加载即拒绝，服务不启动（安全审查 F10）"""
    from insight.policy import CONFIG_DIR, PolicyError
    from insight.sie03_policy import load_sie03
    with pytest.raises(PolicyError):
        load_sie03(CONFIG_DIR / "sie03.synthetic.json", env="dev")


@pytest.mark.anyio
async def test_caller_admission_and_binding():
    c = core()
    status, out = await c.call("insight.claims", body_for("insight.claims"), "bff")
    assert status == 403
    status, _ = await c.call("insight.context", body_for("insight.context"), "bff")
    assert status == 403
    status, _ = await c.call("insight.export", body_for("insight.export"), "orchestrator")
    assert status == 403
    status, _ = await c.call("insight.dispute", {"context": agent_ctx()}, "orchestrator")
    assert status == 403  # 异议只能代表学生本人或家长提出（v0.9 只给 BFF）
    b = body_for("insight.explain")  # 例子是 bff 的请求体；以编排层令牌调用 → 请求体 caller 与令牌不一致
    status, out = await c.call("insight.explain", b, "orchestrator")
    assert status == 403 and "detail" not in out
    # BFF 即便带着自洽的 BFF 上下文也不能调 claims / context（只给编排层）：在准入这一步就 403
    front = {"context": front_ctx("student", "a1000000-0000-4000-8000-0000000000a1")}
    status, _ = await c.call("insight.claims", front, "bff")
    assert status == 403
    status, _ = await c.call("insight.context", {**front, "task": "plan"}, "bff")
    assert status == 403
    status, _ = await c.call("insight.nope", {}, "orchestrator")
    assert status == 404


@pytest.mark.anyio
async def test_request_validation_and_version():
    c = core()
    b = body_for("insight.claims")
    b["surprise"] = 1
    status, out = await c.call("insight.claims", b, "orchestrator")
    assert status == 400 and out["code"] == "INVALID_REQUEST" and "student_ref" not in out.get("detail", "")
    b = body_for("insight.claims")
    b["context"]["contract_version"] = "1.0.0"
    status, out = await c.call("insight.claims", b, "orchestrator")
    assert status == 400 and out["code"] == "CONTRACT_VERSION_UNSUPPORTED"
    status, _ = await c.call("insight.claims", {"context": "x"}, "orchestrator")
    assert status == 400


@pytest.mark.anyio
async def test_record_validates_then_501():
    c = core()
    status, out = await c.call("insight.record", body_for("insight.record"), "bff")
    assert status == 501 and out["code"] == "METHOD_NOT_IMPLEMENTED"
    bad = body_for("insight.record")
    bad["event"]["event_type"] = "self_report"  # 学生自述只能由学生本人提交：受众是家长 → 契约校验不过
    status, out = await c.call("insight.record", bad, "bff")
    assert status == 400


@pytest.mark.anyio
async def test_bff_explain_without_verifier_is_404():
    """缺省授权核对一律不通过：BFF 代表学生 / 家长的解释请求按“不存在”处理，且不碰数据库"""
    b = {"context": front_ctx("student", "a1000000-0000-4000-8000-0000000000a1"), "claim_id": str(uuid.uuid4())}
    status, out = await core().call("insight.explain", b, "bff")
    assert status == 404 and "detail" not in out and out["title"] == out["code"]


def test_agent_ctx_shape_is_contract_valid():
    c = contract()
    assert c.validate("InsightClaimsRequest", {"context": agent_ctx()}) == []


@pytest.mark.anyio
async def test_context_entries_follow_field_dictionary():
    """上下文包的条目：来源必须是字段字典为该字段登记的来源、受众必须在字段可见范围里，否则不返回"""
    from datetime import datetime, timedelta, timezone

    from insight.claims import ClaimRow
    from insight.store import VisibleClaim
    now = datetime(2026, 10, 1, tzinfo=timezone.utc)
    row = ClaimRow("c1a00000-0000-4000-8000-000000000001", "c1000000-0000-4000-8000-0000000000c1", "aya", "node",
                   "MATH.SYN.EQ.LINEAR", "syn.math.node_mastery", 1, {"level": "apply", "gate": "execute"}, 0.5,
                   ("e1000000-0000-4000-8000-000000000001",), "mastery_estimate", "6d000000-0000-4000-8000-000000000001",
                   "inferred", "algo:kge-mastery@0.1.0.syn", "hypothesis", ("agent",), now, now + timedelta(days=1), now)

    class OneClaim:
        async def visible_claims(self, *a, **k):
            return [VisibleClaim(row, None)]

    body = {"context": agent_ctx(), "task": "plan"}
    for fields, n in (({"inner.g3.node_mastery": {"source_type": ("inferred",), "visibility": ("agent",)}}, 1),
                      ({"inner.g3.node_mastery": {"source_type": ("objective",), "visibility": ("agent",)}}, 0),
                      ({"inner.g3.node_mastery": {"source_type": ("inferred",), "visibility": ("student",)}}, 0), ({}, 0)):
        c = ToolCore(OneClaim(), contract(), synthetic_policy(), forbidden_matcher(), UnavailableVerifier(), fields)
        status, out = await c.call("insight.context", body, "orchestrator")
        assert status == 200 and len(out["result"]["entries"]) == n
