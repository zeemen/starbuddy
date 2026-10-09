"""PLT-12（specs/present-item-contract/spec.md）学习服务侧：题面来源开关、经适配器 present_item 取题面、两道白名单。不连库。

- 开关：LEARNING_PRESENT_ITEM_SOURCE 缺省 synthetic；adapter 只在候选契约开关 STARBUDDY_ALLOW_CANDIDATE_CONTRACT=1 时允许（配置层、
  create_app、AdapterItemSource 构造各拦一道）。
- 适配器是按契约 1.1.0 形状应答的假服务（httpx.MockTransport）；请求按契约 PresentItemRequest 自检（只读：不带 usage_id）。
- 第一道白名单：契约校验 + 共享库 project_presented_item；第二道：clean_adapter_view（键与富文本再核一次）。契约被放宽时第二道仍拦得住。
诊断与再练的端到端（开关打开走适配器题面、开关关闭逐字节对拍）在 test_diagnosis_session.py 与 test_practice_service.py。
全部合成数据（规则 8）。
"""
from __future__ import annotations

import copy
import json

import httpx
import pytest

from conftest import CREDENTIAL
from learning import present_item as P
from learning.app import create_app
from learning.config import AdapterTarget, Settings
from learning.diagnosis.presenter import SyntheticPresenter
from learning.diagnosis.source import load_fixture
from learning.errors import IngestError

ADAPTER = AdapterTarget("http://aya-adapter.invalid", "synthetic-learning-to-aya-present-cred-01")
STUDENT = "5c000000-0000-4000-8000-0000000007c1"
CV = {"region": "nanjing", "exam_year": 2027, "grade": 9, "textbook_version": "suke-2024", "subject": "aya", "content_version": "0.1.0"}
STEM = [{"type": "paragraph", "runs": [{"type": "text", "text": "（合成题面）计算 "}, {"type": "math", "latex": "3\\times 4"},
                                        {"type": "text", "text": "。"}]},
        {"type": "paragraph", "runs": [{"type": "image", "image_ref": "objstore://synthetic/aya/fig-01.png", "alt": "（合成）示意图"}]}]
OPTIONS = [{"key": k, "content": [{"type": "text", "text": f"（合成选项）{k}"}]} for k in "ABCD"]


@pytest.fixture(autouse=True)
def candidate(monkeypatch):
    monkeypatch.setenv("STARBUDDY_ALLOW_CANDIDATE_CONTRACT", "1")


def presented(item_ref: str, *, item_type="fill_blank", state="complete", license_state="original") -> dict:
    result: dict = {"item_ref": item_ref, "item_version": "aya-pack@0.3.0", "item_type": item_type, "content_state": state,
                    "license_state": license_state}
    if state == "complete":
        result["stem"] = copy.deepcopy(STEM)
    if item_type in ("single_choice", "multi_choice"):
        result["options"] = copy.deepcopy(OPTIONS) if state == "complete" else [{"key": k} for k in "ABCD"]
        result["answer_format"] = {"kind": "choice", "min_choices": 1, "max_choices": 1 if item_type == "single_choice" else 4}
    elif item_type == "short_answer":
        result["answer_format"] = {"kind": "text", "max_length": 4000}
    else:
        result["answer_format"] = {"kind": "expression", "max_length": 200}
    return {"contract_version": "1.1.0", "result": result, "evidence_ids": [], "confidence": 1,
            "source_version": "algo:aya-present@0.1.0;items:aya-pack@0.3.0", "model_version": "algo:aya-present@0.1.0",
            "human_review_required": False, "semantics": "practice"}


class FakeAdapter:
    def __init__(self) -> None:
        self.requests: list[dict] = []
        self.mode: dict[str, object] = {}   # item_id → 响应（dict）、("status", code, body) 或 callable(body) → dict

    async def handler(self, request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == f"Bearer {ADAPTER.credential}"
        assert request.url.path == "/plugin/v0/present_item"
        body = json.loads(request.content)
        self.requests.append(body)
        item_id = body["item_ref"].split(":", 1)[1]
        m = self.mode.get(item_id, "fill")
        if isinstance(m, tuple):
            return httpx.Response(m[1], json=m[2], headers={"content-type": "application/problem+json"})
        if callable(m):
            return httpx.Response(200, json=m(body))
        kinds = {"fill": "fill_blank", "single": "single_choice", "multi": "multi_choice", "short": "short_answer"}
        if m == "withheld":
            return httpx.Response(200, json=presented(body["item_ref"], item_type="single_choice", state="withheld", license_state="unverified"))
        if m == "restricted":
            return httpx.Response(200, json=presented(body["item_ref"], item_type="single_choice", state="withheld", license_state="restricted"))
        return httpx.Response(200, json=presented(body["item_ref"], item_type=kinds[str(m)]))


async def cv_of(subject: str, item_id: str) -> dict | None:
    return dict(CV) if item_id != "NOT-REGISTERED" else None


@pytest.fixture
async def source():
    fake = FakeAdapter()
    async with httpx.AsyncClient(transport=httpx.MockTransport(fake.handler)) as client:
        src = P.AdapterItemSource(client, {"aya": ADAPTER}, cv_of)
        src.fake = fake
        yield src


# ---------- 开关 ----------
def env(**over) -> dict:
    base = {"LEARNING_ENV": "test", "LEARNING_SERVICE_TOKEN": CREDENTIAL, "LEARNING_DATABASE_URL": "postgresql://unused"}
    base.update(over)
    return base


def test_switch_defaults_to_synthetic_and_adapter_needs_candidate_contract():
    assert Settings.from_env(env()).present_item_source == "synthetic"
    assert Settings.from_env(env(LEARNING_PRESENT_ITEM_SOURCE="synthetic")).present_item_source == "synthetic"
    with pytest.raises(RuntimeError, match="STARBUDDY_ALLOW_CANDIDATE_CONTRACT"):
        Settings.from_env(env(LEARNING_PRESENT_ITEM_SOURCE="adapter"))
    with pytest.raises(RuntimeError, match="STARBUDDY_ALLOW_CANDIDATE_CONTRACT"):
        Settings.from_env(env(LEARNING_PRESENT_ITEM_SOURCE="adapter", STARBUDDY_ALLOW_CANDIDATE_CONTRACT="true"))
    ok = Settings.from_env(env(LEARNING_PRESENT_ITEM_SOURCE="adapter", STARBUDDY_ALLOW_CANDIDATE_CONTRACT="1"))
    assert ok.present_item_source == "adapter"
    with pytest.raises(RuntimeError, match="synthetic 或 adapter"):
        Settings.from_env(env(LEARNING_PRESENT_ITEM_SOURCE="knowledge"))


def test_adapter_source_refuses_without_candidate_contract(monkeypatch):
    monkeypatch.delenv("STARBUDDY_ALLOW_CANDIDATE_CONTRACT")
    with pytest.raises(RuntimeError, match="1.1.0"):
        P.require_candidate()
    with pytest.raises(RuntimeError, match="1.1.0"):
        P.AdapterItemSource(httpx.AsyncClient(), {"aya": ADAPTER}, cv_of)
    # create_app 在建任何连接之前就拒绝（配置层被绕过时的第二道）
    s = Settings(env="test", database_url="unused", service_credential=CREDENTIAL, present_item_source="adapter")
    with pytest.raises(RuntimeError, match="1.1.0"):
        create_app(s)


# ---------- 经适配器取题面 ----------
async def test_fill_view_has_plain_fallback_and_rich_stem(source):
    v = await source.fetch("aya", "Q-SYN-FILL")
    assert v == {"format": "fill", "stem": "（合成题面）计算 3\\times 4。\n[图]", "rich": {"stem": STEM}}
    req = source.fake.requests[-1]
    # PresentItemContext：不带学生 ID、幂等键与计量 ID（只读、非消耗类、与学生无关），L1（审查阻断 2）
    assert set(req["context"]) == {"request_id", "contract_version", "subject", "data_class"}
    assert req["context"]["contract_version"] == "1.1.0" and req["context"]["data_class"] == "L1"
    assert req["item_ref"] == "aya:Q-SYN-FILL" and req["content_version"] == CV
    assert not P.plugin_contract("1.1.0").validate("PresentItemRequest", req)


async def test_choice_view_has_options_with_text_and_rich_content(source):
    source.fake.mode["Q-SYN-ONE"] = "single"
    v = await source.fetch("aya", "Q-SYN-ONE")
    assert v["format"] == "single"
    assert v["options"] == [{"key": k, "text": f"（合成选项）{k}"} for k in "ABCD"]
    assert v["rich"] == {"stem": STEM, "options": OPTIONS}
    assert set(v) == {"format", "stem", "options", "rich"}


@pytest.mark.parametrize("mode", ["withheld", "restricted", "short"])
async def test_not_presentable_items_give_none(source, mode):
    """题干缺失、版权受限、诊断与再练不出的题型（简答）：没有题面（调用方回 501），平台不补写。"""
    source.fake.mode["Q-SYN-X"] = mode
    assert await source.fetch("aya", "Q-SYN-X") is None


async def test_withheld_is_not_shown_even_when_license_is_cleared(source):
    """题干缺失（withheld）时即使版权已核（original）也没有题面：只有题型、选项键与作答格式，平台不补写。"""
    source.fake.mode["Q-SYN-W"] = lambda body: presented(body["item_ref"], item_type="single_choice", state="withheld", license_state="original")
    assert await source.fetch("aya", "Q-SYN-W") is None


@pytest.mark.parametrize("license_state,shown", [("original", True), ("licensed", True), ("public", True), ("unverified", False)])
async def test_only_cleared_licenses_are_shown(source, license_state, shown):
    """审查建议 5：只放行版权状态 original、licensed、public 的完整题面；unverified（版权未核）按没有题面。"""
    source.fake.mode["Q-SYN-L"] = lambda body: presented(body["item_ref"], item_type="single_choice", license_state=license_state)
    v = await source.fetch("aya", "Q-SYN-L")
    assert (v is not None) is shown


@pytest.mark.parametrize("status,code", [(404, "ITEM_NOT_FOUND"), (404, "CONTENT_VERSION_NOT_FOUND"), (501, "METHOD_NOT_IMPLEMENTED"),
                                         (400, "CONTRACT_VERSION_UNSUPPORTED"), (404, "INVALID_REQUEST"), (501, "INTERNAL"),
                                         (501, None)])
async def test_adapter_rejections_mean_no_view(source, status, code):
    """停在 1.0 的学科（例如文砺）：不认 1.1.0（CONTRACT_VERSION_UNSUPPORTED）、不认路由（404 INVALID_REQUEST）、任何 501，
    都是“这道题没有题面”（调用方回 501），不映射为可重试的 503（审查建议 8）。"""
    body = {"type": "about:blank", "title": "x", "status": status, "retryable": False, **({"code": code} if code else {})}
    source.fake.mode["Q-SYN-X"] = ("status", status, body)
    assert await source.fetch("aya", "Q-SYN-X") is None


@pytest.mark.parametrize("status,code", [(400, "INVALID_REQUEST"), (404, "JOB_NOT_FOUND"), (500, "INTERNAL"), (503, "UPSTREAM_UNAVAILABLE")])
async def test_other_rejections_stay_unavailable(source, status, code):
    source.fake.mode["Q-SYN-X"] = ("status", status, {"type": "about:blank", "title": "x", "status": status, "code": code, "retryable": False})
    with pytest.raises(IngestError) as e:
        await source.fetch("aya", "Q-SYN-X")
    assert (e.value.status, e.value.code) == (503, "ITEM_VIEW_UNAVAILABLE")


async def test_adapter_failure_is_retryable_503(source):
    source.fake.mode["Q-SYN-X"] = ("status", 500, {"type": "about:blank", "title": "x", "status": 500, "code": "INTERNAL", "retryable": False})
    with pytest.raises(IngestError) as e:
        await source.fetch("aya", "Q-SYN-X")
    assert (e.value.status, e.value.code, e.value.retryable) == (503, "ITEM_VIEW_UNAVAILABLE", True)


async def test_no_adapter_or_unregistered_item_gives_none_without_calling(source):
    assert await source.fetch("zhixing", "synthetic-m3-01") is None   # 没配这一科的适配器
    assert await source.fetch("aya", "NOT-REGISTERED") is None        # 题目没有登记内容版本
    assert source.fake.requests == []


def _leak(field: str, where: str):
    def f(body):
        out = presented(body["item_ref"], item_type="single_choice")
        if where == "result":
            out["result"][field] = ["A"]
        elif where == "option":
            out["result"]["options"][0][field] = True
        elif where == "run":
            out["result"]["stem"][0]["runs"][0][field] = "A"
        else:
            out[field] = ["A"]
        return out
    return f


@pytest.mark.parametrize("field,where", [("answer", "result"), ("explanation", "result"), ("rubric_version", "result"),
                                         ("is_correct", "option"), ("answer", "run"), ("answer", "envelope")])
async def test_answer_fields_are_rejected(source, field, where):
    source.fake.mode["Q-SYN-LEAK"] = _leak(field, where)
    with pytest.raises(IngestError) as e:
        await source.fetch("aya", "Q-SYN-LEAK")
    assert (e.value.status, e.value.code) == (502, "ADAPTER_RESULT_REJECTED")


@pytest.mark.parametrize("field,where", [("answer", "result"), ("is_correct", "option"), ("answer", "run")])
async def test_second_whitelist_holds_when_contract_is_loosened(source, monkeypatch, field, where):
    """契约文件被放宽（例如 additionalProperties 被改成 true）时，共享库白名单仍按字段重建并拒绝答案类字段。"""
    class Loose:
        def validate(self, schema, value):
            return []
    monkeypatch.setattr(P, "plugin_contract", lambda version=None: Loose())
    source.fake.mode["Q-SYN-LEAK"] = _leak(field, where)
    with pytest.raises(IngestError) as e:
        await source.fetch("aya", "Q-SYN-LEAK")
    assert (e.value.status, e.value.code) == (502, "ADAPTER_RESULT_REJECTED")


async def test_restricted_or_withheld_never_shown_even_if_contract_is_loosened(source, monkeypatch):
    """契约被放宽、适配器把版权受限的题也标成完整题面时，学习服务照样不出题面（不能只靠契约的 restricted → withheld）。"""
    class Loose:
        def validate(self, schema, value):
            return []
    monkeypatch.setattr(P, "plugin_contract", lambda version=None: Loose())
    source.fake.mode["Q-SYN-R"] = lambda body: presented(body["item_ref"], item_type="single_choice", license_state="restricted")
    assert await source.fetch("aya", "Q-SYN-R") is None


async def test_html_and_foreign_item_are_rejected(source):
    def html(body):
        out = presented(body["item_ref"])
        out["result"]["stem"][0]["runs"][0]["text"] = "<img src=x onerror=alert(1)>"
        return out
    source.fake.mode["Q-SYN-HTML"] = html
    source.fake.mode["Q-SYN-OTHER"] = lambda body: presented("aya:Q-SYN-SOMETHING-ELSE")
    for item in ("Q-SYN-HTML", "Q-SYN-OTHER"):
        with pytest.raises(IngestError) as e:
            await source.fetch("aya", item)
        assert e.value.code == "ADAPTER_RESULT_REJECTED", item


async def test_views_are_cached_and_copies_are_independent(source):
    a = await source.fetch("aya", "Q-SYN-FILL")
    a["stem"] = "（被调用方改动）"
    b = await source.fetch("aya", "Q-SYN-FILL")
    assert source.calls == 1 and len(source.fake.requests) == 1
    assert b["stem"] == "（合成题面）计算 3\\times 4。\n[图]"
    assert await source.cached("aya", "Q-SYN-FILL") == b


async def test_cache_expires_after_ttl_and_is_keyed_by_content_version():
    """缓存 10 分钟过期（时钟注入）；换了内容版本是另一个缓存条目（审查建议 6）。"""
    fake = FakeAdapter()
    now = [1000.0]
    cv = dict(CV)

    async def cv_now(subject: str, item_id: str) -> dict:
        return dict(cv)
    async with httpx.AsyncClient(transport=httpx.MockTransport(fake.handler)) as client:
        src = P.AdapterItemSource(client, {"aya": ADAPTER}, cv_now, clock=lambda: now[0])
        assert P.CACHE_TTL_SEC == 600
        await src.fetch("aya", "Q-SYN-FILL")
        now[0] += 599
        await src.fetch("aya", "Q-SYN-FILL")
        assert src.calls == 1
        now[0] += 1                                              # 到 10 分钟：过期，重新取
        assert await src.fetch("aya", "Q-SYN-FILL") is not None
        assert src.calls == 2
        cv["content_version"] = "0.2.0"                           # 内容版本变了：不能用旧版本的题面
        with pytest.raises(P.ViewNotCached):
            await src.cached("aya", "Q-SYN-FILL")
        await src.fetch("aya", "Q-SYN-FILL")
        assert src.calls == 3 and fake.requests[-1]["content_version"]["content_version"] == "0.2.0"


async def test_cached_only_lookup_never_calls_the_adapter(source):
    """事务内用的 cached：没命中抛 ViewNotCached（不发网络请求）；没有这一科、没有内容版本时是 None；None 题面也缓存。"""
    with pytest.raises(P.ViewNotCached) as e:
        await source.cached("aya", "Q-SYN-FILL")
    assert (e.value.subject, e.value.item_id) == ("aya", "Q-SYN-FILL")
    assert source.fake.requests == []
    assert await source.cached("zhixing", "synthetic-m3-01") is None
    assert await source.cached("aya", "NOT-REGISTERED") is None
    source.fake.mode["Q-SYN-W"] = "withheld"
    assert await source.fetch("aya", "Q-SYN-W") is None
    assert await source.cached("aya", "Q-SYN-W") is None and source.calls == 1


# ---------- 第二道白名单本身 ----------
@pytest.mark.parametrize("mutate", [
    lambda v: v.__setitem__("answer", "12"),
    lambda v: v["rich"].__setitem__("analysis", "（合成）"),
    lambda v: v["rich"]["stem"][0]["runs"][0].__setitem__("answer", "12"),
    lambda v: v["rich"]["stem"][0].__setitem__("style", "color:red"),
    lambda v: v["rich"]["stem"][0]["runs"].append({"type": "html", "html": "<b>x</b>"}),
    lambda v: v["options"][0].__setitem__("is_correct", True),
    lambda v: v["rich"]["options"][0].__setitem__("correct", True),
    lambda v: v.__setitem__("stem", ""),
    lambda v: v.pop("rich"),
    lambda v: v["rich"]["options"].pop(),
])
def test_clean_adapter_view_rejects(mutate):
    view = {"format": "single", "stem": "（合成）", "options": [{"key": k, "text": "（合成）"} for k in "ABCD"],
            "rich": {"stem": copy.deepcopy(STEM), "options": copy.deepcopy(OPTIONS)}}
    assert P.clean_adapter_view(copy.deepcopy(view)) == view
    mutate(view)
    with pytest.raises(ValueError):
        P.clean_adapter_view(view)


def _rich_patterns(node, out: dict) -> dict:
    """收集 OpenAPI 里受限富文本片段的 pattern：（片段类型, 字段）→ pattern。"""
    if isinstance(node, dict):
        props = node.get("properties")
        if isinstance(props, dict) and isinstance(props.get("type"), dict) and props["type"].get("const") in ("text", "math", "image"):
            for field in ("text", "latex", "alt"):
                if isinstance(props.get(field), dict) and "pattern" in props[field]:
                    out.setdefault((props["type"]["const"], field), set()).add(props[field]["pattern"])
        for v in node.values():
            _rich_patterns(v, out)
    elif isinstance(node, list):
        for v in node:
            _rich_patterns(v, out)
    return out


@pytest.mark.parametrize("name", ["diagnosis-internal.v0.yaml", "practice-internal.v0.yaml"])
def test_internal_openapi_rich_patterns_match_contract(name):
    """审查建议 11：学习服务两份内部 OpenAPI 的文字、公式、图片说明 pattern 与契约 v1.1 逐字相同（含危险 LaTeX 命令与不可见字符的排除）。"""
    import yaml
    from pathlib import Path

    from starbuddy_contract.contract import PLUGIN_CONTRACTS

    contract = yaml.safe_load(PLUGIN_CONTRACTS["1.1.0"].read_text(encoding="utf-8"))["components"]["schemas"]
    want = {("text", "text"): contract["RichTextRun"]["properties"]["text"]["pattern"],
            ("math", "latex"): contract["RichMathRun"]["properties"]["latex"]["pattern"],
            ("image", "alt"): contract["RichImageRun"]["properties"]["alt"]["pattern"]}
    doc = yaml.safe_load((Path(__file__).resolve().parents[1] / "openapi" / name).read_text(encoding="utf-8"))
    got = _rich_patterns(doc, {})
    for k, pattern in want.items():
        assert got.get(k) == {pattern}, (name, k)


async def test_present_view_keeps_synthetic_path_unchanged():
    """开关关闭（合成题面）时 present_view 原样调用同步的 present：结果与直接调用逐字节相同。"""
    syn = SyntheticPresenter("test")
    for it in load_fixture()["items"]:
        direct = syn.present("aya", it["item_id"])
        via = await P.present_view(syn, "aya", it["item_id"])
        assert json.dumps(via, ensure_ascii=False) == json.dumps(direct, ensure_ascii=False)
        assert json.dumps(await P.present_cached(syn, "aya", it["item_id"]), ensure_ascii=False) == json.dumps(direct, ensure_ascii=False)
        assert "rich" not in via
