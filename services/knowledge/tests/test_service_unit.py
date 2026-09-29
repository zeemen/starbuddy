"""知识服务的单测（不需要数据库）：契约加载与工具清单、MCP inputSchema 与契约校验一致、配置（监听地址、令牌）、
鉴权比较、严格 JSON、页面 ID 映射、规则解析、页面快照加载的反例。全部合成数据。"""
from __future__ import annotations

import copy
import hashlib
import json
import shutil
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, FormatChecker

from conftest import PAGES
from knowledge.service.app import caller_for, strict_json
from knowledge.service.config import DEFAULT_HOST, Settings, check_host
from knowledge.service.contract import PERSONAL_TOOLS, PUBLIC_TOOLS, contract
from knowledge.service.mcp_server import transport_security
from knowledge.service.pages import FORMAT, SnapshotLoadError, contract_page_id, load_snapshot_dir, node_id_of
from knowledge.service.resolve import RuleResolver
from knowledge.service.tools import ALLOWED_CALLERS

C = contract()
EXAMPLES = C.doc["components"]["examples"]
ENV = {"KNOWLEDGE_ENV": "test", "KNOWLEDGE_DATABASE_URL": "postgresql://synthetic@/x",
       "KNOWLEDGE_TOKEN_ORCHESTRATOR": "o" * 32, "KNOWLEDGE_TOKEN_BFF": "b" * 32}


# ---------- 契约 ----------
def test_tools_come_from_contract_paths():
    assert set(C.tools) == set(PUBLIC_TOOLS) | set(PERSONAL_TOOLS)
    for name, t in C.tools.items():
        assert t.path == f"/v0/{name}" and t.operation_id == name.replace(".", "_")
        assert t.request_schema in C.doc["components"]["schemas"] and t.response_schema in C.doc["components"]["schemas"]


def test_contract_examples_validate():
    for t in C.tools.values():
        assert C.validate(t.request_schema, EXAMPLES[t.request_schema]["value"]) == []
        assert C.validate(t.response_schema, EXAMPLES[t.response_schema]["value"]) == []


def _counterexamples(schema: str, example: dict) -> list[dict]:
    out = []
    extra = copy.deepcopy(example)
    extra["unexpected"] = 1
    out.append(extra)                                          # additionalProperties
    ctx_extra = copy.deepcopy(example)
    ctx_extra["context"]["student_ref"] = "00000000-0000-4000-8000-00000000a001"
    out.append(ctx_extra)                                      # 公共层上下文不带学生标识
    bad_caller = copy.deepcopy(example)
    bad_caller["context"]["caller"] = "student"
    out.append(bad_caller)                                     # 枚举
    bad_version = copy.deepcopy(example)
    bad_version["context"]["contract_version"] = "1.0.0"
    out.append(bad_version)
    if "text" in example:
        long_text = copy.deepcopy(example)
        long_text["text"] = "长" * 2001
        out.append(long_text)                                  # 长度
    return out


@pytest.mark.parametrize("tool", sorted(PUBLIC_TOOLS))
def test_mcp_input_schema_agrees_with_contract(tool):
    """MCP 的 inputSchema 是契约请求 schema 的自包含打包：正例与反例的判定与契约校验完全一致"""
    t = C.tools[tool]
    bundled = C.bundle(t.request_schema)
    assert bundled["type"] == "object" and "$defs" in bundled
    Draft202012Validator.check_schema(bundled)
    v = Draft202012Validator(bundled, format_checker=FormatChecker())
    example = EXAMPLES[t.request_schema]["value"]
    assert list(v.iter_errors(example)) == [] and C.validate(t.request_schema, example) == []
    for bad in _counterexamples(t.request_schema, example):
        assert C.validate(t.request_schema, bad) != []
        assert list(v.iter_errors(bad)) != []


def test_allowed_callers_match_contract_enums():
    schemas = C.doc["components"]["schemas"]
    public = set(schemas["PublicContext"]["properties"]["caller"]["enum"])
    personal = set(schemas["StudentContext"]["properties"]["caller"]["enum"])
    resolve = {schemas["ResolveContext"]["allOf"][1]["properties"]["caller"]["const"]}
    assert ALLOWED_CALLERS["node.resolve"] == resolve == {"orchestrator"}
    for t in PUBLIC_TOOLS:
        if t != "node.resolve":
            assert ALLOWED_CALLERS[t] == public
    for t in PERSONAL_TOOLS:
        assert ALLOWED_CALLERS[t] == personal and "bff" not in ALLOWED_CALLERS[t]
    assert set(ALLOWED_CALLERS) == set(C.tools)


# ---------- 配置：监听地址与令牌 ----------
def test_default_host_is_loopback_and_wildcards_rejected():
    s = Settings.from_env(dict(ENV))
    assert s.host == DEFAULT_HOST == "127.0.0.1" and s.port == 8790
    for wild in ("0.0.0.0", "::", "[::]", "", "*", " 0.0.0.0 "):
        with pytest.raises(RuntimeError):
            Settings.from_env({**ENV, "KNOWLEDGE_HOST": wild})
    assert check_host("10.8.0.12") == "10.8.0.12"
    assert Settings.from_env({**ENV, "KNOWLEDGE_HOST": "10.8.0.12"}).host == "10.8.0.12"


def test_mcp_transport_security_always_on():
    loop = transport_security("127.0.0.1")
    assert loop.enable_dns_rebinding_protection and all(h.startswith(("127.0.0.1", "localhost", "[::1]")) for h in loop.allowed_hosts)
    lan = transport_security("10.8.0.12")
    assert lan.enable_dns_rebinding_protection and lan.allowed_hosts == ["10.8.0.12:*"]


def test_settings_require_env_tokens_and_distinct():
    with pytest.raises(RuntimeError):
        Settings.from_env({**ENV, "KNOWLEDGE_ENV": "prod"})
    with pytest.raises(RuntimeError):
        Settings.from_env({k: v for k, v in ENV.items() if not k.startswith("KNOWLEDGE_TOKEN")})
    with pytest.raises(RuntimeError):
        Settings.from_env({**ENV, "KNOWLEDGE_TOKEN_LEARNING": "short"})
    with pytest.raises(RuntimeError):
        Settings.from_env({**ENV, "KNOWLEDGE_TOKEN_LEARNING": "o" * 32})
    with pytest.raises(RuntimeError):
        Settings.from_env({k: v for k, v in ENV.items() if k != "KNOWLEDGE_DATABASE_URL"})
    s = Settings.from_env({**ENV, "KNOWLEDGE_TOKEN_STUDENT": "s" * 32, "KNOWLEDGE_PAGE_SNAPSHOT": "/srv/kge/snap-1"})
    assert set(s.tokens) == {"orchestrator", "bff"}  # 没有学生、家长类调用方
    assert s.page_snapshot == Path("/srv/kge/snap-1")


def test_caller_for_token_decides_caller():
    tokens = {"bff": "b" * 32, "orchestrator": "o" * 32}
    assert caller_for("Bearer " + "o" * 32, tokens) == "orchestrator"
    assert caller_for("Bearer " + "b" * 32, tokens) == "bff"
    for bad in (None, "", "Bearer ", "bearer " + "o" * 32, "Basic " + "o" * 32, "Bearer " + "x" * 32, "Bearer " + "o" * 31):
        assert caller_for(bad, tokens) is None


def test_strict_json_rejects_nan_and_duplicate_keys():
    assert strict_json(b'{"a": 1}') == {"a": 1}
    for raw in (b'{"a": NaN}', b'{"a": Infinity}', b'{"a": 1, "a": 2}', b'{"a": {"b": 1, "b": 1}}'):
        with pytest.raises(ValueError):
            strict_json(raw)


# ---------- 页面 ID ----------
def test_page_id_mapping_roundtrip():
    assert contract_page_id("MATH.FUNC.QUAD.VERTEX_FORM") == "math.func.quad.vertex-form"
    assert node_id_of("math.func.quad.vertex-form") == "MATH.FUNC.QUAD.VERTEX_FORM"
    for bad in ("math", "math.func.quad.vertex_form", "MATH.FUNC.QUAD", "../etc", "math..x"):
        assert node_id_of(bad) is None
    assert C.validate("PageId", contract_page_id("MATH.SYN.EQ.LINEAR")) == []


# ---------- 规则解析 ----------
NODES = [{"id": "MATH.SYN.EQ.LINEAR", "level": "node", "kind": "kp", "name": "一元一次方程", "includes": ["只含一个未知数的整式方程"]},
         {"id": "MATH.SYN.EQ.TRANSPOSE", "level": "node", "kind": "kp", "name": "移项", "includes": ["把项变号后移到另一边"]},
         {"id": "MATH.SYN.EQ", "level": "topic", "kind": None, "name": "方程", "includes": []}]


def test_rule_resolver_deterministic_and_ranked():
    r = RuleResolver()
    out = r.resolve("一元一次方程怎么移项", NODES, 5)
    assert [c.node_id for c in out] == ["MATH.SYN.EQ.LINEAR", "MATH.SYN.EQ.TRANSPOSE"]
    assert out == r.resolve("一元一次方程怎么移项", list(reversed(NODES)), 5)
    assert r.resolve("看 MATH.SYN.EQ.TRANSPOSE 这一条", NODES, 5)[0].confidence == 1.0
    assert r.resolve("完全无关的文字", NODES, 5) == []
    assert all(c.node_id != "MATH.SYN.EQ" for c in r.resolve("方程", NODES, 5))  # 主题层不作候选
    assert len(r.resolve("一元一次方程怎么移项", NODES, 1)) == 1


# ---------- 页面快照加载：反例 ----------
def _make_snapshot(tmp: Path, *, commit: str = "a" * 40) -> Path:
    d = tmp / "snap"
    (d / "pages" / "kp").mkdir(parents=True)
    entries = []
    for p in sorted((PAGES / "kp").glob("*.md")):
        raw = p.read_bytes()
        shutil.copy(p, d / "pages" / "kp" / p.name)
        entries.append({"page_id": p.stem, "path": f"pages/kp/{p.name}", "page_version": "1.0.0", "map_version": "0.9.0",
                        "sha256": hashlib.sha256(raw).hexdigest()})
    (d / "manifest.json").write_text(json.dumps({"format": FORMAT, "commit": commit, "pages": entries}), encoding="utf-8")
    return d


def test_snapshot_loads_only_manifest_pages(tmp_path):
    d = _make_snapshot(tmp_path)
    m = json.loads((d / "manifest.json").read_text(encoding="utf-8"))
    dropped = m["pages"].pop()
    (d / "manifest.json").write_text(json.dumps(m), encoding="utf-8")
    snap = load_snapshot_dir(d)
    assert snap.commit == "a" * 40 and snap.short_commit == "a" * 12
    assert len(snap.pages) == 3
    assert snap.get(contract_page_id(dropped["page_id"])) is None  # 文件在，manifest 没列 = 未发布
    assert (d / dropped["path"]).is_file()


@pytest.mark.parametrize("breakage", ["missing_dir", "no_manifest", "bad_format", "short_commit", "hash", "header_version",
                                      "extra_field", "path_escape", "missing_file"])
def test_snapshot_rejects_broken(tmp_path, breakage):
    d = _make_snapshot(tmp_path)
    m = json.loads((d / "manifest.json").read_text(encoding="utf-8"))
    e = m["pages"][0]
    if breakage == "missing_dir":
        d = tmp_path / "nope"
    elif breakage == "no_manifest":
        (d / "manifest.json").unlink()
    elif breakage == "bad_format":
        m["format"] = "other"
    elif breakage == "short_commit":
        m["commit"] = "abc123"
    elif breakage == "hash":
        p = d / e["path"]
        p.write_text(p.read_text(encoding="utf-8").replace("## 易错", "## 易错点", 1), encoding="utf-8")
    elif breakage == "header_version":
        e["page_version"] = "9.9.9"
    elif breakage == "extra_field":
        e["draft"] = True
    elif breakage == "path_escape":
        e["path"] = "../" + e["path"]
    elif breakage == "missing_file":
        (d / e["path"]).unlink()
    if d.exists() and breakage != "no_manifest":
        (d / "manifest.json").write_text(json.dumps(m), encoding="utf-8")
    with pytest.raises(SnapshotLoadError):
        load_snapshot_dir(d)
