"""LT-02 真实模型供应商接入路由（specs/launch-lt02-model-provider）的测试：staging 路由表（变量名与占位）、staging 按 pilot 口径校验、
供应商登记联动（docs/compliance/vendors.md）、成本护栏（UTC+8 自然日、边界、并发）、成本估算、staging 出网过滤与地址核对。

全部本机、不联网、合成数据（规则 8）：主机名用保留的 .invalid 域，快照名是合成的，Key 是合成字符串；不调用任何真实模型。
"""
from __future__ import annotations

import asyncio
import copy
import dataclasses
import datetime as dt
import json
import logging
import uuid
from pathlib import Path

import httpx
import pytest
import yaml

from learning.model_router import (DEFAULT_PATH, BudgetExceeded, ConfigError, DataClass, FakeProvider, LogSink, MemorySink, ModelRouter,
                                   OpenAICompatProvider, ProviderError, ProviderReply, Residency, RouteRejected, RouteRequest,
                                   RouteUnavailable, Task, Usage, egress_allowed, load, parse)
from learning.model_router.budget import DailyBudget, day_of, seconds_until_reset
from learning.model_router.config import Budget, STRICT_ENVS
from learning.model_router.registry import RegistryError, check_routes_against_registry, parse_registry

REPO = Path(__file__).resolve().parents[3]
VENDORS_MD = REPO / "docs" / "compliance" / "vendors.md"
CST = dt.timezone(dt.timedelta(hours=8))
SYN_HOSTS = {"bailian": "llm-a.synthetic.invalid", "ark": "llm-b.synthetic.invalid"}
SYN_VERSION = "2026-09-01"
SYN_KEY = "synthetic-placeholder-credential-0001"


def repo_doc() -> dict:
    return yaml.safe_load(DEFAULT_PATH.read_text(encoding="utf-8"))


def filled(doc: dict | None = None, *, whitelist: str = "approved") -> dict:
    """把 staging 的占位换成合成快照名与合成主机（模拟人开通后的填写）；不改仓库文件"""
    doc = copy.deepcopy(doc or repo_doc())
    st = doc["environments"]["staging"]
    for vid, v in st["vendors"].items():
        v["allowed_hosts"] = [SYN_HOSTS[vid]]
        v["whitelist"] = whitelist
    for r in st["routes"].values():
        for k in ("primary", "backup"):
            t = r[k]
            t["version"] = SYN_VERSION
            t["api_name"] = f"{t['model']}-{SYN_VERSION}"
    return doc


def staging_section(doc: dict) -> dict:
    return doc["environments"]["staging"]


# ---------------- 仓库里的 staging 节 ----------------

def test_repo_staging_registers_two_domestic_vendors_by_env_name_only():
    st = staging_section(repo_doc())
    assert set(st["vendors"]) == {"bailian", "ark"}
    names = {(v["base_url_env"], v["api_key_env"]) for v in st["vendors"].values()}
    assert names == {("MODEL_BAILIAN_URL", "MODEL_BAILIAN_API_KEY"), ("MODEL_ARK_URL", "MODEL_ARK_API_KEY")}
    for v in st["vendors"].values():
        assert v["residency"] == "domestic" and v["protocol"] == "openai_compat" and v["whitelist"] == "pending"
        assert v["allowed_hosts"] and all(h.startswith("<") and h.endswith(">") for h in v["allowed_hosts"]), "主机名只写占位"
    text = yaml.safe_dump(st, allow_unicode=True)
    assert "://" not in text and "sk-" not in text, "仓库里不写真实地址与 Key"


def test_repo_staging_routes_follow_spec():
    routes = staging_section(repo_doc())["routes"]
    want = {"T1": (("bailian", "qwen3.8-flash"), ("ark", "doubao-2.1-turbo")),
            "T2": (("bailian", "qwen3.8-max"), ("ark", "deepseek-v4.1-flash")),
            "T4": (("bailian", "qwen3.8-max"), ("ark", "doubao-2.1-pro")),
            "T8": (("ark", "deepseek-v4.1-flash"), ("bailian", "qwen3.8-flash"))}
    assert set(routes) == set(want), "T9 本期不接云内容安全"
    for task, (p, b) in want.items():
        r = routes[task]
        assert (r["primary"]["vendor"], r["primary"]["model"]) == p and (r["backup"]["vendor"], r["backup"]["model"]) == b, task
        for t in (r["primary"], r["backup"]):
            assert t["api_name"].startswith("<") and t["version"].startswith("<"), "快照名与版本由人按控制台填写"
    assert routes["T8"]["data_classes"] == ["L0"]
    assert routes["T2"]["primary"]["params"] == {"temperature": 0}


def test_repo_staging_has_small_budget_for_every_route_and_empty_pricing():
    st = staging_section(repo_doc())
    assert set(st["budgets"]) == set(st["routes"]), "staging 每个已配路由的任务都要有成本护栏"
    for b in st["budgets"].values():
        assert 1 <= b["daily_calls"] <= 1000 and 1 <= b["daily_tokens"] <= 5_000_000, "staging 缺省小额度"
    assert st["pricing"] == {}, "单价由人填写，缺省为空"


def test_repo_staging_refuses_to_start_until_filled():
    with pytest.raises(ConfigError, match="占位"):
        load(DEFAULT_PATH, "staging")
    from learning.model_router.config import load_for_deployment
    with pytest.raises(ConfigError):
        load_for_deployment(DEFAULT_PATH, {"LEARNING_ENV": "staging"})


def test_filled_staging_with_synthetic_snapshots_and_hosts_is_accepted():
    cfg = parse(filled(), "staging")
    assert set(cfg.routes) == {Task.T1, Task.T2, Task.T4, Task.T8}
    assert cfg.routes[Task.T2].primary.model_version == "model:bailian/qwen3.8-max@2026-09-01"
    assert cfg.routes[Task.T8].backup.api_name == "qwen3.8-flash-2026-09-01"
    assert cfg.budgets[Task.T1] == Budget(300, 600000) and dict(cfg.pricing) == {}
    assert cfg.vendors["ark"].allowed_hosts == ("llm-b.synthetic.invalid",)


def _edit(fn):
    doc = filled()
    fn(staging_section(doc))
    return doc


def _first_target(st):
    return st["routes"]["T1"]["primary"]


STAGING_BAD = {
    "api_name 仍是占位": lambda st: _first_target(st).update(api_name="<由人填写的带日期快照名>"),
    "version 仍是占位": lambda st: _first_target(st).update(version="<由人填写：快照日期或版本号>"),
    "主机名仍是占位": lambda st: st["vendors"]["ark"].update(allowed_hosts=["<由人填写：方舟接入主机名>"]),
    "whitelist 仍是 pending": lambda st: st["vendors"]["ark"].update(whitelist="pending"),
    "测试专用供应商": lambda st: st["vendors"]["ark"].update(whitelist="test_only"),
    "缺 allowed_hosts": lambda st: st["vendors"]["bailian"].pop("allowed_hosts"),
    "allowed_hosts 为空": lambda st: st["vendors"]["bailian"].update(allowed_hosts=[]),
    "api_name 不带日期或版本": lambda st: _first_target(st).update(api_name="qwen-flash"),
    "没写 api_name": lambda st: _first_target(st).pop("api_name"),
    "api_name 用 latest 别名": lambda st: _first_target(st).update(api_name="qwen3.8-flash-latest"),
    "境外供应商（只接 L0 的 T8 也不行）": lambda st: (st["vendors"]["ark"].update(residency="foreign"),
                                         st["routes"].update(T8={"data_classes": ["L0"], "primary": st["routes"]["T8"]["primary"]}),
                                         [st["routes"].pop(k) for k in ("T1", "T2", "T4")],
                                         [st["budgets"].pop(k) for k in ("T1", "T2", "T4")]),
    "缺成本护栏（安全审查建议 2）": lambda st: st["budgets"].pop("T4"),
    "没有 budgets 一节": lambda st: st.pop("budgets"),
    "假协议": lambda st: st["vendors"]["ark"].update(protocol="fake"),
    "未登记的供应商": lambda st: _first_target(st).update(vendor="ghost"),
    "地址直接写进配置": lambda st: st["vendors"]["ark"].update(base_url_env="https://llm-b.synthetic.invalid/v1"),
    "主备同一平台": lambda st: st["routes"]["T1"]["backup"].update(vendor="bailian"),
    "成本护栏：未知任务": lambda st: st["budgets"].update(T3={"daily_calls": 1, "daily_tokens": 1}),
    "成本护栏：没有路由的任务": lambda st: st["budgets"].update(T9={"daily_calls": 1, "daily_tokens": 1}),
    "成本护栏：次数为 0": lambda st: st["budgets"]["T1"].update(daily_calls=0),
    "成本护栏：次数是布尔": lambda st: st["budgets"]["T1"].update(daily_calls=True),
    "成本护栏：token 是字符串": lambda st: st["budgets"]["T1"].update(daily_tokens="1000"),
    "成本护栏：未知键": lambda st: st["budgets"]["T1"].update(per_minute=5),
    "成本护栏：缺一项": lambda st: st["budgets"]["T1"].pop("daily_tokens"),
    "单价：键不是路由里的目标": lambda st: st.update(pricing={"bailian/qwen-unknown": {"input_per_million": 1, "output_per_million": 2}}),
    "单价：负数": lambda st: st.update(pricing={"bailian/qwen3.8-max": {"input_per_million": -1, "output_per_million": 2}}),
    "单价：缺一项": lambda st: st.update(pricing={"bailian/qwen3.8-max": {"input_per_million": 1}}),
    "环境节有未知键": lambda st: st.update(budget={"T1": {"daily_calls": 1, "daily_tokens": 1}}),
}


@pytest.mark.parametrize("name", sorted(STAGING_BAD))
def test_bad_staging_refuses_to_start(name):
    with pytest.raises(ConfigError):
        parse(_edit(STAGING_BAD[name]), "staging")


def test_placeholders_are_refused_in_every_environment():
    doc = repo_doc()
    t = copy.deepcopy(doc)
    t["environments"]["test"] = copy.deepcopy(t["environments"]["test"])
    t["environments"]["test"]["routes"]["T1"]["primary"]["api_name"] = "<由人填写的带日期快照名>"
    with pytest.raises(ConfigError, match="占位"):
        parse(t, "test")


def test_strict_envs_cover_staging_pilot_prod():
    assert STRICT_ENVS == {"staging", "pilot", "prod"}


def test_hand_built_staging_config_cannot_bypass_invariants():
    good = parse(filled(), "staging")
    for bad_vendor in (dataclasses.replace(good.vendors["ark"], residency=Residency.FOREIGN),
                       dataclasses.replace(good.vendors["ark"], whitelist="pending"),
                       dataclasses.replace(good.vendors["ark"], allowed_hosts=("<由人填写>",))):
        bad = dataclasses.replace(good, vendors={**good.vendors, "ark": bad_vendor})
        with pytest.raises(ConfigError):
            ModelRouter(bad, {v: FakeProvider(bad.vendors[v]) for v in bad.vendors}, sink=MemorySink())


# ---------------- staging 出网过滤与地址核对（不联网） ----------------

def test_staging_runtime_egress_filter_blocks_foreign_for_every_data_class():
    cfg = parse(filled(), "staging")
    domestic = cfg.vendors["bailian"]
    foreign = dataclasses.replace(domestic, residency=Residency.FOREIGN)
    for dc in DataClass:
        assert egress_allowed(domestic, dc, "staging") and not egress_allowed(foreign, dc, "staging")


def _bailian():
    return parse(filled(), "staging").vendors["bailian"]


def test_staging_endpoint_must_be_https_and_registered_host():
    v = _bailian()
    ok = {"MODEL_BAILIAN_URL": "https://llm-a.synthetic.invalid/compatible/v1", "MODEL_BAILIAN_API_KEY": SYN_KEY}
    OpenAICompatProvider(v, ok, env="staging")
    for url in ("http://llm-a.synthetic.invalid/v1", "https://other.synthetic.invalid/v1", "https://llm-a.synthetic.invalid.evil.invalid/v1",
                "https://u:p@llm-a.synthetic.invalid/v1", ""):
        with pytest.raises(ConfigError) as e:
            OpenAICompatProvider(v, {**ok, "MODEL_BAILIAN_URL": url}, env="staging")
        assert "evil" not in str(e.value) and SYN_KEY not in str(e.value)
    with pytest.raises(ConfigError, match="MODEL_BAILIAN_API_KEY"):
        OpenAICompatProvider(v, {"MODEL_BAILIAN_URL": ok["MODEL_BAILIAN_URL"]}, env="staging")


async def test_staging_router_builds_from_env_and_sends_snapshot_name():
    """填好之后从执行环境变量建连（本机模拟传输，不联网）：发给供应商的是快照名，Key 只在请求头里"""
    cfg = parse(filled(), "staging")
    environ = {"MODEL_BAILIAN_URL": "https://llm-a.synthetic.invalid/v1", "MODEL_BAILIAN_API_KEY": SYN_KEY,
               "MODEL_ARK_URL": "https://llm-b.synthetic.invalid/api/v3", "MODEL_ARK_API_KEY": SYN_KEY}
    router = ModelRouter.from_config(cfg, environ, sink=MemorySink())
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["host"], seen["model"] = request.url.host, json.loads(request.content)["model"]
        seen["auth"] = request.headers.get("authorization")
        return httpx.Response(200, json={"model": seen["model"], "choices": [{"message": {"content": "ok"}}],
                                         "usage": {"prompt_tokens": 2, "completion_tokens": 1}})

    for p in router._providers.values():  # noqa: SLF001 测试注入本机传输
        p._client._transport = httpx.MockTransport(handler)  # noqa: SLF001
    res = await router.route(_req(task=Task.T2, dc=DataClass.L2))
    assert res.model_version == "model:bailian/qwen3.8-max@2026-09-01" and not res.model_mismatch
    assert seen == {"host": "llm-a.synthetic.invalid", "model": "qwen3.8-max-2026-09-01", "auth": f"Bearer {SYN_KEY}"}
    await router.aclose()
    with pytest.raises(ConfigError):
        ModelRouter.from_config(cfg, {}, sink=MemorySink())


# ---------------- 供应商登记联动 ----------------

def test_repo_registry_lists_both_vendors_and_matches_routes():
    reg = parse_registry(VENDORS_MD.read_text(encoding="utf-8"))
    assert {"bailian", "ark"} <= set(reg)
    assert all(reg[v].status == "待开通 / 待登记" and reg[v].hosts == () and reg[v].hosts_placeholder for v in ("bailian", "ark"))
    assert check_routes_against_registry(repo_doc(), reg) == []


REGISTRY = """# 合成登记表
说明文字 | 不是表格

| 路由表 whitelist | 本表“状态” |
|---|---|
| pending | 待开通 / 待登记 |

示例表（不是登记表：在标记之前，即使表头含供应商 ID 也不读）
| 供应商 ID | 允许的主机名 | 状态 |
|---|---|---|
| decoy | decoy.synthetic.invalid | 已批准 |

<!-- starbuddy:vendor-registry -->
|  状态（待审 / 已批准） | 其他 | `供应商 ID`（路由表里的键） | 允许的主机名（路由表 allowed_hosts） |
| :--- | --- | ---: | --- |
|已批准| a \\| b | `bailian` | llm-a.synthetic.invalid |
| 已批准 |  | ark | `llm-b.synthetic.invalid` |
| （无） | | （无） | |

尾注
"""


def test_registry_parser_is_robust_to_layout():
    reg = parse_registry(REGISTRY)
    assert set(reg) == {"bailian", "ark"}
    assert reg["bailian"].status == "已批准" and reg["bailian"].hosts == ("llm-a.synthetic.invalid",)
    assert reg["ark"].hosts == ("llm-b.synthetic.invalid",) and not reg["ark"].hosts_placeholder
    assert check_routes_against_registry(filled(), reg) == [], "填好、批准、登记一致时通过"


M = "<!-- starbuddy:vendor-registry -->\n"
HEAD = "| 供应商 ID | 允许的主机名 | 状态 |\n|---|---|---|\n"


@pytest.mark.parametrize("text,needle", [
    ("# 没有表格\n", "标记"),
    (HEAD + "| a | | 已批准 |\n", "标记"),  # 有表但没有标记：不靠“第一张含供应商 ID 的表”
    (M + HEAD + "| a | | 已批准 |\n" + M, "标记"),
    (M + "\n正文\n", "表头"),
    (M + "| 别的表 | 状态 |\n|---|---|\n| x | 已批准 |\n", "表头"),
    (M + "| 供应商 ID | 状态 |\n|---|---|\n| a | 已批准 |\n", "允许的主机名"),
    (M + HEAD + "| a | | 已批准 |\n| a | | 已批准 |\n", "两次"),
    (M + HEAD + "| a | | 批准了 |\n", "不认识"),
    (M + HEAD + "| Bad_ID | | 已批准 |\n", "不合格式"),
    (M + HEAD + "| a | 已批准 |\n", "列数"),
    (M + HEAD + "| a | https://llm-a.synthetic.invalid/v1 | 已批准 |\n", "不像主机名"),
    (M + HEAD + "| a | llm-a.synthetic.invalid 由人确认 | 已批准 |\n", "不像主机名"),
    (M + HEAD + "| a | LLM-A.synthetic.invalid | 已批准 |\n", "不像主机名"),
    (M + HEAD + "| a | <由人填写> llm-a.synthetic.invalid | 已批准 |\n", "不像主机名"),
])
def test_registry_parser_refuses_malformed(text, needle):
    with pytest.raises(RegistryError, match=needle):
        parse_registry(text)


def test_registry_hosts_list_and_placeholders():
    reg = parse_registry(M + HEAD + "| a | llm-a.synthetic.invalid、llm-a2.synthetic.invalid | 已批准 |\n| b | （由人填写） | 待审 |\n"
                         "| c | <由人填写：接入主机名> | 待开通 / 待登记 |\n")
    assert reg["a"].hosts == ("llm-a.synthetic.invalid", "llm-a2.synthetic.invalid") and not reg["a"].hosts_placeholder
    assert reg["b"].hosts == () and reg["b"].hosts_placeholder and reg["c"].hosts_placeholder


def _reg(**rows) -> str:
    head = M + HEAD
    return head + "".join(f"| {k} | {h} | {s} |\n" for k, (h, s) in rows.items())


BOTH_APPROVED = {"bailian": ("llm-a.synthetic.invalid", "已批准"), "ark": ("llm-b.synthetic.invalid", "已批准")}


@pytest.mark.parametrize("name,doc_fn,rows,needle", [
    ("路由里的供应商不在登记表", lambda: filled(), {"bailian": BOTH_APPROVED["bailian"]}, "不在"),
    ("路由已批准、登记表待开通", lambda: filled(), {**BOTH_APPROVED, "ark": ("llm-b.synthetic.invalid", "待开通 / 待登记")}, "不一致"),
    ("路由 pending、登记表已批准", lambda: repo_doc(), {"bailian": ("（由人填写）", "已批准"), "ark": ("（由人填写）", "待审")}, "不一致"),
    ("登记表已停用", lambda: filled(), {**BOTH_APPROVED, "ark": ("llm-b.synthetic.invalid", "停用")}, "已停用"),
    ("主机名不一致", lambda: filled(), {**BOTH_APPROVED, "ark": ("llm-c.synthetic.invalid", "已批准")}, "allowed_hosts"),
    ("登记表主机名还是占位", lambda: filled(), {**BOTH_APPROVED, "ark": ("<由人填写>", "已批准")}, "allowed_hosts"),
    ("测试专用供应商进 staging", lambda: _with_vendor_whitelist("ark", "test_only"), BOTH_APPROVED, "test_only"),
    ("已批准却还是占位主机", lambda: _with_placeholder_hosts(), BOTH_APPROVED, "allowed_hosts"),
    ("路由引用了本环境没有的供应商", lambda: _with_ghost_route(), BOTH_APPROVED, "没有登记"),
    ("pilot 也核对", lambda: _pilot_from_staging(), {"bailian": BOTH_APPROVED["bailian"]}, "pilot.vendors.ark"),
])
def test_registry_mismatch_is_reported(name, doc_fn, rows, needle):
    problems = check_routes_against_registry(doc_fn(), parse_registry(_reg(**rows)))
    assert problems and any(needle in p for p in problems), (name, problems)


def _with_vendor_whitelist(vid, wl):
    doc = filled()
    staging_section(doc)["vendors"][vid]["whitelist"] = wl
    return doc


def _with_placeholder_hosts():
    doc = filled()
    staging_section(doc)["vendors"]["ark"]["allowed_hosts"] = ["<由人填写>"]
    return doc


def _with_ghost_route():
    doc = filled()
    staging_section(doc)["routes"]["T1"]["backup"]["vendor"] = "ghost"
    return doc


def _pilot_from_staging():
    doc = filled()
    doc["environments"]["pilot"] = copy.deepcopy(staging_section(doc))
    return doc


# ---------------- 成本护栏 ----------------

def cst(*args) -> float:
    return dt.datetime(*args, tzinfo=CST).timestamp()


class Wall:
    def __init__(self, t: float) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t


def budget_cfg(budgets: dict, pricing: dict | None = None):
    doc = copy.deepcopy(repo_doc())
    section = copy.deepcopy(doc["environments"]["test"])
    section["budgets"] = budgets
    section["pricing"] = pricing or {}
    doc["environments"]["test"] = section
    return parse(doc, "test")


def _req(task: Task = Task.T1, dc: DataClass = DataClass.L2, **kw) -> RouteRequest:
    base = dict(task=task, data_class=dc, subject="zhixing", prompt_template="zhixing-m3-grade", prompt_version="0.1.0",
                messages=({"role": "user", "content": "合成题干"},), usage_id=f"usage-{uuid.uuid4()}", idempotency_key=f"idem-{uuid.uuid4()}")
    base.update(kw)
    return RouteRequest(**base)


def _ok(inp: int = 3, out: int = 5):
    async def b(t, m, k):
        return ProviderReply("合成输出", Usage(inp, out), t.api_name)
    return b


async def _no_sleep(seconds: float) -> None:
    await asyncio.sleep(0)


def _router(cfg, behavior=None, wall=None, sink=None):
    provs = {v: FakeProvider(cfg.vendors[v], behavior or _ok()) for v in cfg.vendors}
    return ModelRouter(cfg, provs, sink=sink or MemorySink(), sleep=_no_sleep,
                       wall_clock=wall or Wall(cst(2026, 10, 21, 12, 0, 0))), provs


async def test_daily_call_limit_rejects_deterministically_without_calling_vendor():
    cfg = budget_cfg({"T1": {"daily_calls": 3, "daily_tokens": 1_000_000}})
    sink = MemorySink()
    router, provs = _router(cfg, sink=sink)
    for _ in range(3):
        await router.route(_req())
    n = sum(len(p.calls) for p in provs.values())
    for _ in range(2):  # 确定性：之后每次都同样拒绝
        with pytest.raises(BudgetExceeded) as e:
            await router.route(_req())
        assert isinstance(e.value, RouteUnavailable) and e.value.code == "ROUTE_BUDGET_EXCEEDED"
    assert sum(len(p.calls) for p in provs.values()) == n, "超限不调任何供应商"
    assert [r.outcome for r in sink.records][-2:] == ["budget_exceeded", "budget_exceeded"]
    assert router.budget.used(Task.T1) == (3, 24)
    # 其他任务（T2 没配上限）不受影响
    assert (await router.route(_req(task=Task.T2))).model_version.startswith("model:fake-a/")


async def test_daily_token_limit():
    cfg = budget_cfg({"T1": {"daily_calls": 100, "daily_tokens": 10}})
    router, _ = _router(cfg)
    await router.route(_req())  # 8
    await router.route(_req())  # 8 < 10 → 放行，累计 16
    with pytest.raises(BudgetExceeded):
        await router.route(_req())
    assert router.budget.used(Task.T1) == (2, 16)


async def test_every_vendor_call_counts_including_retries_and_backup():
    """安全审查建议 1：按每次真实的供应商调用计次（重试与切备都算）；不合规的请求不占额度"""
    cfg = budget_cfg({"T1": {"daily_calls": 10, "daily_tokens": 1000}})

    async def down(t, m, k):
        raise ProviderError("server")

    router, provs = _router(cfg, down)
    for _ in range(3):
        with pytest.raises(RouteRejected):
            await router.route(_req(usage_id=""))
    assert router.budget.used(Task.T1) == (0, 0), "不合规的请求不占额度"
    with pytest.raises(RouteUnavailable) as e:
        await router.route(_req())
    assert not isinstance(e.value, BudgetExceeded)
    calls = sum(len(p.calls) for p in provs.values())
    assert calls == 6 and router.budget.used(Task.T1) == (6, 0), "主 3 次 + 备 3 次，HTTP 错误没有用量、只计次数"


async def test_budget_runs_out_mid_retries():
    cfg = budget_cfg({"T1": {"daily_calls": 2, "daily_tokens": 1000}})

    async def down(t, m, k):
        raise ProviderError("server")

    sink = MemorySink()
    router, provs = _router(cfg, down, sink=sink)
    with pytest.raises(BudgetExceeded) as e:
        await router.route(_req())
    assert [a.status for a in e.value.attempts] == ["server", "server"], "到上限之前的尝试照常记录"
    assert sum(len(p.calls) for p in provs.values()) == 2 and sink.records[-1].outcome == "budget_exceeded"
    assert router.breaker.state("fake-a", "T1") == "closed", "没发出的那次不占熔断名额"


async def test_failed_call_with_reported_usage_counts_tokens():
    cfg = budget_cfg({"T1": {"daily_calls": 100, "daily_tokens": 10_000}})

    async def malformed(t, m, k):
        raise ProviderError("bad_response", usage=Usage(30, 12))

    router, _ = _router(cfg, malformed)
    with pytest.raises(RouteUnavailable):
        await router.route(_req())
    # bad_response 不重试：主、备各一次
    assert router.budget.used(Task.T1) == (2, 84)


async def test_openai_compat_reports_usage_on_malformed_200():
    v = parse(filled(), "staging").vendors["bailian"]
    p = OpenAICompatProvider(v, {"MODEL_BAILIAN_URL": "https://llm-a.synthetic.invalid/v1", "MODEL_BAILIAN_API_KEY": SYN_KEY}, env="staging")
    p._client._transport = httpx.MockTransport(  # noqa: SLF001
        lambda r: httpx.Response(200, json={"choices": [], "usage": {"prompt_tokens": 7, "completion_tokens": 3}}))
    t = parse(filled(), "staging").routes[Task.T1].primary
    with pytest.raises(ProviderError) as e:
        await p.complete(t, [], idempotency_key="k" * 8, timeout=5)
    assert e.value.kind == "bad_response" and e.value.usage == Usage(7, 3)
    p._client._transport = httpx.MockTransport(lambda r: httpx.Response(503, json={"usage": {"prompt_tokens": 7}}))  # noqa: SLF001
    with pytest.raises(ProviderError) as e:
        await p.complete(t, [], idempotency_key="k" * 8, timeout=5)
    assert e.value.kind == "server" and e.value.usage is None, "HTTP 错误响应不读用量"


async def test_no_candidates_means_unavailable_without_reserving():
    """契约审查建议 2：数据级别过滤后没有候选，不预留、直接 unavailable"""
    doc = copy.deepcopy(repo_doc())
    section = copy.deepcopy(doc["environments"]["test"])
    section["routes"] = {"T8": {"data_classes": ["L0", "L1"], "primary": {"vendor": "fake-x", "model": "fake-batch", "version": "2026-09-01"}}}
    section["budgets"] = {"T8": {"daily_calls": 5, "daily_tokens": 100}}
    doc["environments"]["test"] = section
    router, provs = _router(parse(doc, "test"))
    with pytest.raises(RouteUnavailable) as e:
        await router.route(_req(task=Task.T8, dc=DataClass.L1))  # 境外只接 L0：没有候选
    assert not isinstance(e.value, BudgetExceeded) and router.budget.used(Task.T8) == (0, 0)
    assert all(not p.calls for p in provs.values())


@pytest.mark.parametrize("before,after,resets", [
    ((2026, 10, 21, 23, 59, 59), (2026, 10, 22, 0, 0, 0), True),   # 北京时间 0 点重置
    ((2026, 10, 22, 7, 59, 59), (2026, 10, 22, 8, 0, 0), False),   # UTC 0 点（北京时间 8 点）不重置
    ((2026, 10, 21, 0, 0, 0), (2026, 10, 21, 23, 59, 59), False),  # 同一天的首尾
    ((2026, 12, 31, 23, 59, 59), (2027, 1, 1, 0, 0, 0), True),     # 跨年
])
async def test_budget_resets_at_utc8_midnight(before, after, resets):
    cfg = budget_cfg({"T1": {"daily_calls": 1, "daily_tokens": 1000}})
    wall = Wall(cst(*before))
    router, _ = _router(cfg, wall=wall)
    await router.route(_req())
    with pytest.raises(BudgetExceeded):
        await router.route(_req())
    wall.t = cst(*after)
    if resets:
        assert (await router.route(_req())).output == "合成输出"
    else:
        with pytest.raises(BudgetExceeded):
            await router.route(_req())


def test_day_boundary_helpers():
    assert day_of(cst(2026, 10, 22, 0, 0, 0)) == day_of(cst(2026, 10, 21, 23, 59, 59)) + 1
    assert day_of(dt.datetime(2026, 10, 21, 16, 0, 0, tzinfo=dt.timezone.utc).timestamp()) == day_of(cst(2026, 10, 22, 0, 0, 0))
    assert seconds_until_reset(cst(2026, 10, 21, 23, 59, 59)) == 1
    assert seconds_until_reset(cst(2026, 10, 21, 0, 0, 0)) == 86_400
    assert seconds_until_reset(cst(2026, 10, 21, 12, 0, 0)) == 43_200


async def test_concurrent_requests_never_exceed_call_limit():
    cfg = budget_cfg({"T1": {"daily_calls": 5, "daily_tokens": 1_000_000}})
    gate = asyncio.Event()

    async def slow(t, m, k):
        await gate.wait()
        return ProviderReply("合成输出", Usage(1, 1), t.api_name)

    router, provs = _router(cfg, slow)
    tasks = [asyncio.create_task(router.route(_req())) for _ in range(20)]
    await asyncio.sleep(0)
    gate.set()
    results = await asyncio.gather(*tasks, return_exceptions=True)
    ok = [r for r in results if not isinstance(r, BaseException)]
    over = [r for r in results if isinstance(r, BudgetExceeded)]
    assert len(ok) == 5 and len(over) == 15
    assert sum(len(p.calls) for p in provs.values()) == 5


def test_budget_settle_after_midnight_counts_toward_reservation_day_only():
    wall = Wall(cst(2026, 10, 21, 23, 59, 59))
    b = DailyBudget({Task.T1: Budget(daily_calls=10, daily_tokens=100)}, wall)
    r = b.reserve(Task.T1)
    wall.t = cst(2026, 10, 22, 0, 0, 1)
    b.settle(r, 500)
    assert b.used(Task.T1) == (0, 0), "跨 0 点才结束的调用不占新一天的额度"
    assert b.reserve(Task.T1) is not None


def test_budget_threads_share_one_counter():
    from concurrent.futures import ThreadPoolExecutor
    b = DailyBudget({Task.T1: Budget(daily_calls=50, daily_tokens=10**9)}, Wall(cst(2026, 10, 21, 9, 0, 0)))
    with ThreadPoolExecutor(max_workers=8) as pool:
        got = list(pool.map(lambda _: b.reserve(Task.T1), range(400)))
    assert sum(r is not None for r in got) == 50


# ---------------- 内部模型路由接口：超限映射为明确问题码 ----------------

async def test_route_api_maps_budget_exceeded_to_explicit_problem():
    from conftest import settings
    from learning.app import create_app

    zx = "synthetic-zhixing-route-client-credential-0001"
    cfg = budget_cfg({"T2": {"daily_calls": 1, "daily_tokens": 1_000_000}})
    router, _ = _router(cfg, wall=Wall(cst(2026, 10, 21, 23, 0, 0)))
    app = create_app(settings(route_clients={"zhixing": zx}), model_router=router)
    body = {"task": "T2", "data_class": "L2", "subject": "zhixing", "prompt_template": "zhixing-m3-grade", "prompt_version": "0.1.0",
            "messages": [{"role": "user", "content": "合成作答"}]}
    headers = {"authorization": f"Bearer {zx}", "content-type": "application/json"}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        r1 = await c.post("/internal/v1/model/route", headers=headers,
                          json={**body, "usage_id": str(uuid.uuid4()), "idempotency_key": str(uuid.uuid4())})
        r2 = await c.post("/internal/v1/model/route", headers=headers,
                          json={**body, "usage_id": str(uuid.uuid4()), "idempotency_key": str(uuid.uuid4())})
    assert r1.status_code == 200
    p = r2.json()
    assert r2.status_code == 503 and p["code"] == "ROUTE_UNAVAILABLE" and p["detail"] == "ROUTE_BUDGET_EXCEEDED"
    assert p["retryable"] is False and r2.headers["retry-after"] == "3600"
    assert r2.headers["content-type"].startswith("application/problem+json")
    _assert_problem_valid_in_1_0_and_1_1(p)


def _assert_problem_valid_in_1_0_and_1_1(problem: dict) -> None:
    """契约审查建议 3：同时按 learning-internal 1.0.0 与 1.1.0 校验。仓库只有一份 v1 文件（info.version = 1.1.0，加载键为 "1.0.0"）；
    1.0.0 的 Problem 由它去掉 1.1.0 新增的错误码还原（1.1.0 只做增量：新增接口、令牌方案与 Problem 错误码，已有 schema 不变）"""
    from starbuddy_contract.contract import INTERNAL_CONTRACTS, contract_from_doc

    from learning.contract import internal_contract

    current = internal_contract("1.0.0")
    assert current.version == "1.1.0", current.version
    assert current.validate("Problem", problem) == []
    path = INTERNAL_CONTRACTS["1.0.0"]
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    doc["info"]["version"] = "1.0.0"
    enum = doc["components"]["schemas"]["Problem"]["properties"]["code"]["enum"]
    added_in_1_1 = {"CALLER_MISMATCH", "MASTERY_UNAVAILABLE", "TASK_NOT_FOUND", "PROFILE_UNAVAILABLE"}
    doc["components"]["schemas"]["Problem"]["properties"]["code"]["enum"] = [c for c in enum if c not in added_in_1_1]
    v10 = contract_from_doc(doc, path, "urn:starbuddy:learning-internal:1.0.0-reconstructed")
    assert v10.validate("Problem", problem) == []


# ---------------- 成本估算 ----------------

async def test_cost_estimate_uses_pricing_and_is_empty_without_it(caplog):
    priced = budget_cfg({}, {"fake-a/fake-chat": {"input_per_million": 2, "output_per_million": 8}})
    sink = MemorySink()
    router, _ = _router(priced, _ok(1000, 500), sink=sink)
    await router.route(_req())
    assert sink.records[-1].estimated_cost_cny == pytest.approx((1000 * 2 + 500 * 8) / 1_000_000)
    plain_sink = MemorySink()
    plain, _ = _router(budget_cfg({}), _ok(1000, 500), sink=plain_sink)
    await plain.route(_req())
    assert plain_sink.records[-1].estimated_cost_cny is None, "单价表为空则不估算"

    async def down(t, m, k):
        raise ProviderError("server")

    failing, _ = _router(priced, down, sink=sink)
    with pytest.raises(RouteUnavailable):
        await failing.route(_req())
    assert sink.records[-1].estimated_cost_cny is None
    with caplog.at_level(logging.INFO, logger="starbuddy.model_router"):
        logged, _ = _router(priced, _ok(1000, 500), sink=LogSink())
        await logged.route(_req())
    assert '"estimated_cost_cny": 0.006' in caplog.text and "合成题干" not in caplog.text
