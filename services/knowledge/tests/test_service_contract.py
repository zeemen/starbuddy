"""契约一致性（spec 验收：知识服务读工具通过严格契约一致性测试；个人层三个工具回 501）。

packages/contracts/conformance 只覆盖学科插件 API（对运行中的适配器），没有服务契约的一致性机制，所以在这里实现：
每个工具的正例请求与响应、反例请求与错误体，都用 knowledge-service.v0.9.yaml 严格校验（additionalProperties、枚举、长度、
格式）。需要一次性 Postgres；严格模式（CONTRACT_STRICT=1）下没有 Postgres 直接失败，不跳过。
鉴权：只接受契约允许的调用方令牌；学生与家长没有令牌。
"""
from __future__ import annotations

import copy
import json

import pytest

from knowledge.service.contract import PERSONAL_TOOLS, PUBLIC_TOOLS, contract
from knowledge.service.pages import load_snapshot_dir
from knowledge.service.reader import DbAtomSource
from pgkge import APP_ROLE, APPLY, LINEAR, TRANSPOSE
from svc import CREDS, auth, build_pages, call, running_app, write_snapshot_dir

pytestmark = pytest.mark.anyio
C = contract()
EXAMPLES = C.doc["components"]["examples"]
SUBST = {"MATH.FUNC.QUAD.VERTEX_FORM": LINEAR, "MATH.FUNC.QUAD.GRAPH": APPLY, "aya:synthetic-0301-07": "aya:SYN-I1",
         "aya-quad-rubric@0.1.0": "syn-rubric@0.1.0", "bubian-2024": "syn-ed-a", "nanjing": "syn-region",
         "math.func.quad.vertex-form": "math.syn.eq.linear"}


def example_request(tool: str) -> dict:
    """契约示例请求，把示例里的节点、题目、版次换成合成库里的编码"""
    raw = json.dumps(EXAMPLES[C.tools[tool].request_schema]["value"], ensure_ascii=False)
    for a, b in SUBST.items():
        raw = raw.replace(a, b)
    return json.loads(raw)


def check(tool: str, r) -> dict:
    body = r.json()
    if r.status_code == 200:
        assert r.headers["content-type"].startswith("application/json")
        assert C.validate(C.tools[tool].response_schema, body) == [], (tool, body)
        assert body["evidence_ids"] == [] and body["contract_version"] == "0.9.0"
    else:
        assert r.headers["content-type"].startswith("application/problem+json")
        assert C.problem_errors(body) == [], (tool, body)
        assert body["status"] == r.status_code
    return body


@pytest.fixture(scope="module")
def pages_snapshot(kgedb, tmp_path_factory):
    snap = DbAtomSource(kgedb.dsn(APP_ROLE), "aya").load()
    d = write_snapshot_dir(tmp_path_factory.mktemp("snap") / "s", build_pages(snap, [LINEAR, TRANSPOSE]))
    return load_snapshot_dir(d)


async def test_every_public_tool_example_conforms(kgedb, pages_snapshot):
    async with running_app(kgedb, pages=pages_snapshot) as client:
        for tool in sorted(PUBLIC_TOOLS):
            body = example_request(tool)
            r = await call(client, tool, body, body["context"]["caller"])
            assert r.status_code == 200, (tool, r.text)
            check(tool, r)


async def test_personal_tools_return_501(kgedb):
    async with running_app(kgedb) as client:
        for tool in sorted(PERSONAL_TOOLS):
            body = example_request(tool)
            for caller in ("orchestrator", "learning"):
                r = await call(client, tool, {**body, "context": {**body["context"], "caller": caller}}, caller)
                assert r.status_code == 501 and check(tool, r)["code"] == "METHOD_NOT_IMPLEMENTED"
            r = await call(client, tool, body, "bff")  # 个人层没有 bff
            assert r.status_code == 403


def _bad_requests(tool: str) -> list[tuple[str, dict, int, str]]:
    ok = example_request(tool)
    out = []

    def mut(name: str, fn, status: int = 400, code: str = "INVALID_REQUEST") -> None:
        b = copy.deepcopy(ok)
        fn(b)
        out.append((name, b, status, code))

    mut("顶层多余字段", lambda b: b.__setitem__("extra", 1))
    mut("上下文多余字段（学生标识）", lambda b: b["context"].__setitem__("student_ref", "00000000-0000-4000-8000-00000000a001"))
    mut("request_id 不是 UUID", lambda b: b["context"].__setitem__("request_id", "not-a-uuid"))
    mut("数据级别 L3", lambda b: b["context"].__setitem__("data_class", "L3"))
    mut("缺上下文必填字段", lambda b: b["context"].pop("data_class"))
    mut("契约版本不支持", lambda b: b["context"].__setitem__("contract_version", "1.0.0"), 400, "CONTRACT_VERSION_UNSUPPORTED")
    mut("地图版本格式不对", lambda b: b["context"].__setitem__("map_version", "v1"))
    if "node_id" in ok:
        mut("节点 ID 不合规范", lambda b: b.__setitem__("node_id", "math.lower.case"))
    if "edition" in ok:
        mut("版次编码不合规范", lambda b: b.__setitem__("edition", "人教版"))
    if tool == "node.resolve":
        mut("文本超长", lambda b: b.__setitem__("text", "长" * 2001))
        mut("文本为空", lambda b: b.__setitem__("text", ""))
        mut("候选数越界", lambda b: b.__setitem__("max_candidates", 11))
        mut("数据级别必须 L2", lambda b: b["context"].__setitem__("data_class", "L1"))
    if tool == "node.path":
        mut("方向枚举", lambda b: b.__setitem__("direction", "sideways"))
        mut("深度越界", lambda b: b.__setitem__("max_depth", 11))
    if tool == "item.search":
        mut("学科枚举", lambda b: b.__setitem__("subject", "math"))
        mut("数量越界", lambda b: b.__setitem__("limit", 51))
        mut("难度越界", lambda b: b.__setitem__("difficulty_max", 1.5))
    if tool in ("item.similar", "rubric.get"):
        mut("题目引用格式", lambda b: b.__setitem__("item_ref", "SYN-I1"))
    if tool == "rubric.get":
        mut("Rubric 版本写成自然语言", lambda b: b.__setitem__("rubric_version", "第一版"))
    if tool == "page.get":
        mut("页面 ID 大写", lambda b: b.__setitem__("page_id", "MATH.SYN.EQ.LINEAR"))
    return out


@pytest.mark.parametrize("tool", sorted(PUBLIC_TOOLS))
async def test_invalid_requests_rejected_with_problem(kgedb, pages_snapshot, tool):
    async with running_app(kgedb, pages=pages_snapshot) as client:
        for name, body, status, code in _bad_requests(tool):
            r = await call(client, tool, body, body["context"].get("caller", "orchestrator"))
            b = check(tool, r)
            assert (r.status_code, b["code"]) == (status, code), (tool, name, r.text)
            assert "长长" not in r.text  # 错误体不回显请求原文


async def test_auth_and_caller_binding(kgedb):
    async with running_app(kgedb) as client:
        body = example_request("node.get")  # caller = bff
        for headers in ({}, {"authorization": "Bearer student-app-session"}, {"authorization": f"Basic {CREDS['bff']}"},
                        {"authorization": f"Bearer {CREDS['bff']}x"}):
            r = await client.post("/v0/node.get", json=body, headers=headers)
            assert r.status_code == 401 and check("node.get", r)["code"] == "INVALID_REQUEST"
        r = await call(client, "node.get", body, "orchestrator")  # 令牌是编排层，请求体写 bff
        assert r.status_code == 403
        r = await call(client, "node.get", {**body, "context": {**body["context"], "caller": "student"}}, "bff")
        assert r.status_code == 403
        rb = example_request("node.resolve")
        r = await call(client, "node.resolve", {**rb, "context": {**rb["context"], "caller": "bff"}}, "bff")
        assert r.status_code == 403  # node.resolve 只给编排层
        r = await call(client, "node.resolve", {**rb, "context": {**rb["context"], "caller": "learning"}}, "learning")
        assert r.status_code == 403
        for caller in ("bff", "orchestrator", "learning"):
            r = await call(client, "node.get", {**body, "context": {**body["context"], "caller": caller}}, caller)
            assert r.status_code == 200


async def test_body_parsing_is_strict(kgedb):
    async with running_app(kgedb) as client:
        h = {**auth("orchestrator"), "content-type": "application/json"}
        for raw in (b"not json", b'{"context": {"caller": NaN}}', b'{"a": 1, "a": 2}', b"[]", b'"x"'):
            r = await client.post("/v0/node.get", content=raw, headers=h)
            assert r.status_code in (400, 403) and check("node.get", r)
        r = await client.post("/v0/node.get", content=b" " * (65 * 1024), headers=h)
        assert r.status_code == 413
        r = await client.get("/v0/node.get", headers=h)
        assert r.status_code in (404, 405)  # 只有 POST；其他方法落到 /mcp 挂载点之外的 404
