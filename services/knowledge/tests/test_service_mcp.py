"""MCP（spec 待人确认 #4）：同一组工具以 MCP 工具暴露，工具名 = operationId，inputSchema 来自契约；
工具实现与 HTTP 共用一层（ToolCore），校验与鉴权一致。用进程内 MCP 客户端测试，另经 streamable HTTP（挂在 /mcp）
测令牌、caller 绑定与 DNS 重绑定防护。"""
from __future__ import annotations

import json

import httpx2
import pytest
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

from knowledge.service.contract import PERSONAL_TOOLS, PUBLIC_TOOLS, contract
from knowledge.service.mcp_server import build_mcp_server
from knowledge.service.reader import KgeReader, create_reader_pool
from knowledge.service.tools import ToolCore
from pgkge import APP_ROLE, DRAFT_NAME, LINEAR, UNVERIFIED_QUOTE
from svc import CREDS, call, ctx, running_app
from test_service_contract import _bad_requests, example_request

pytestmark = pytest.mark.anyio
C = contract()


async def _core(db) -> tuple[ToolCore, object]:
    pool = await create_reader_pool(**db.connect_kwargs(APP_ROLE))
    return ToolCore(KgeReader(pool), C), pool


def _check(tool: str, res) -> dict:
    body = res.structured_content
    assert json.loads(res.content[0].text) == body
    if res.is_error:
        assert C.problem_errors(body) == [], body
    else:
        assert C.validate(C.tools[tool].response_schema, body) == [], body
    return body


async def test_mcp_tools_are_contract_operations(kgedb):
    core, pool = await _core(kgedb)
    try:
        server = build_mcp_server(core, lambda _ctx: "orchestrator")
        async with Client(server) as client:
            tools = (await client.list_tools()).tools
        # 只列公共层 7 个工具；个人层三个在 MCP 不暴露（HTTP 仍回契约的 501）
        assert [t.name for t in tools] == sorted(C.tools[t].operation_id for t in PUBLIC_TOOLS) and len(tools) == 7
        assert not {C.tools[t].operation_id for t in PERSONAL_TOOLS} & {t.name for t in tools}
        for t in tools:
            spec = C.by_operation_id(t.name)
            assert t.input_schema == C.bundle(spec.request_schema)
    finally:
        await pool.close()


async def test_mcp_matches_http_and_validates_the_same(kgedb):
    core, pool = await _core(kgedb)
    try:
        server = build_mcp_server(core, lambda _ctx: "orchestrator")
        async with Client(server) as client, running_app(kgedb) as http:
            for tool in sorted(set(PUBLIC_TOOLS) - {"page.get"}):
                body = example_request(tool)
                body["context"]["caller"] = "orchestrator"
                res = await client.call_tool(C.tools[tool].operation_id, body)
                got = _check(tool, res)
                want = (await call(http, tool, body, "orchestrator")).json()
                assert res.is_error is False and got == want, tool
                for name, bad, status, code in _bad_requests(tool):
                    res = await client.call_tool(C.tools[tool].operation_id, bad)
                    got = _check(tool, res)
                    assert res.is_error and (got["status"], got["code"]) in ((status, code), (403, "INVALID_REQUEST")), (tool, name)
                    http_status = (await call(http, tool, bad, "orchestrator")).status_code
                    assert got["status"] == http_status, (tool, name)
            res = await client.call_tool("page_get", example_request("page.get") | {"context": ctx()})
            assert res.is_error and _check("page.get", res)["code"] == "METHOD_NOT_IMPLEMENTED"
            for tool in PERSONAL_TOOLS:  # MCP 不接个人层工具：与未知工具同一个 404；HTTP 仍是 501
                res = await client.call_tool(C.tools[tool].operation_id, example_request(tool))
                assert res.is_error and _check(tool, res)["status"] == 404
                assert res.structured_content["code"] == "CONTENT_VERSION_NOT_FOUND" and "detail" not in res.structured_content
                assert (await call(http, tool, example_request(tool), "orchestrator")).status_code == 501
            res = await client.call_tool("node_delete", {})
            assert res.is_error and res.structured_content["status"] == 404
            res = await client.call_tool("node_get", {"context": ctx(), "node_id": LINEAR, "edition": "syn-ed-c"})
            text = res.content[0].text
            assert UNVERIFIED_QUOTE not in text and DRAFT_NAME not in text
    finally:
        await pool.close()


async def test_mcp_caller_binding(kgedb):
    core, pool = await _core(kgedb)
    try:
        async with Client(build_mcp_server(core, lambda _ctx: "bff")) as client:
            res = await client.call_tool("node_resolve", example_request("node.resolve"))  # 只给编排层
            assert res.is_error and res.structured_content["status"] == 403
            res = await client.call_tool("node_get", {"context": ctx("orchestrator"), "node_id": LINEAR})  # 与令牌不一致
            assert res.is_error and res.structured_content["status"] == 403
            res = await client.call_tool("node_get", {"context": ctx("bff"), "node_id": LINEAR})
            assert not res.is_error
        async with Client(build_mcp_server(core, lambda _ctx: None)) as client:
            res = await client.call_tool("node_get", {"context": ctx("bff"), "node_id": LINEAR})
            assert res.is_error and res.structured_content["status"] == 401
    finally:
        await pool.close()


async def test_mcp_over_http_requires_token_and_loopback_host(kgedb):
    async with running_app(kgedb) as http:
        app = http.app  # type: ignore[attr-defined]
        transport = httpx2.ASGITransport(app=app)
        url = "http://127.0.0.1:8790/mcp"
        async with httpx2.AsyncClient(transport=transport, headers={"authorization": f"Bearer {CREDS['orchestrator']}"}) as hc:
            async with Client(streamable_http_client(url, http_client=hc)) as client:
                names = [t.name for t in (await client.list_tools()).tools]
                assert "node_get" in names and len(names) == 7 and "evidence_write" not in names
                res = await client.call_tool("node_get", {"context": ctx("orchestrator"), "node_id": LINEAR})
                assert not res.is_error and res.structured_content["result"]["node"]["node_id"] == LINEAR
                res = await client.call_tool("node_get", {"context": ctx("bff"), "node_id": LINEAR})
                assert res.is_error and res.structured_content["status"] == 403
        rpc = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
        accept = {"accept": "application/json, text/event-stream", "content-type": "application/json"}
        async with httpx2.AsyncClient(transport=transport) as raw:
            r = await raw.post(url, json=rpc, headers=accept)
            assert r.status_code == 401
            r = await raw.post(url, json=rpc, headers={**accept, "authorization": "Bearer student-app-session"})
            assert r.status_code == 401
            r = await raw.post("http://evil.example/mcp", json=rpc,
                               headers={**accept, "authorization": f"Bearer {CREDS['orchestrator']}"})
            assert r.status_code in (400, 403, 421)  # DNS 重绑定防护：非本机 Host 拒绝
        # 整个应用（含 /v0/*）都校验 Host，与 MCP 同一份允许列表
        async with httpx2.AsyncClient(transport=transport) as raw:
            body = {"context": ctx(), "node_id": LINEAR}
            auth_h = {"authorization": f"Bearer {CREDS['orchestrator']}"}
            r = await raw.post("http://evil.example/v0/node.get", json=body, headers=auth_h)
            assert r.status_code == 400 and r.json()["code"] == "INVALID_REQUEST"
            r = await raw.post("http://127.0.0.1.evil.example:8790/v0/node.get", json=body, headers=auth_h)
            assert r.status_code == 400
            for ok_host in ("http://127.0.0.1:8790", "http://localhost:8790", "http://[::1]:8790", "http://127.0.0.1"):
                r = await raw.post(f"{ok_host}/v0/node.get", json=body, headers=auth_h)
                assert r.status_code == 200, ok_host
