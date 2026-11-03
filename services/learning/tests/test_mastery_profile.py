"""KGE-04 第二步 PR 4：学情档案页编译器的纯函数测试（不连库）。

覆盖第一步 spec 档案页验收里与库无关的部分：
- 引用：每条陈述都有 claim_id；只有成立 / 假设作陈述，待验证只进待办；库里只存引用，不存陈述文字；
- 一致性检查拦截程度升级（开发计划验收）：“判断为尚不稳定、衔接语写薄弱”被阻断；衔接语新增结论、含数字、命中禁用推断词表、含人设名各自被阻断；
  阻断后为纯模板版本（没有衔接语）并有阻断记录（禁用推断与含数字的不存片段）；合规用例 100% 通过；
- 读取时重核：被推翻、撤回、过期（= 读不到）、转待验证、模板版本变化的陈述消失，同节衔接语一起不显示；
- claims_digest：判断新增、修订、转状态都变；顺序无关；
- 渲染规则：唯一实现（洞察服务 RenderedStatement 直接复用 learning/mastery/render.py）；
- 配置：衔接语必须在人审短语表里、不得命中禁用推断词表；非 test 环境拒绝合成 / 未签字配置；配置指纹忽略注释字段；
- 边界：编译模块不依赖页面仓库客户端、模型路由或任何 HTTP 客户端；作答与重算的代码路径不引用档案页模块（静态检查）。
全部合成数据（规则 8）。
"""
from __future__ import annotations

import ast
import copy
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from learning.mastery import profile as PF
from learning.mastery.config import CONFIG_DIR, ConfigError
from learning.mastery.consistency import default_forbidden_matcher
from learning.mastery.render import render_text

FORBID = default_forbidden_matcher()
CFG = PF.synthetic_profile_config()
T = datetime(2026, 10, 1, 8, 0, tzinfo=timezone.utc)
TPL = "（合成模板）{node_id}｜{gate}"


def cid(n: int) -> str:
    return f"00000000-0000-4000-8000-{n:012d}"


def claim(n: int, key: str = "syn.mastery.stable", status: str = "established", *, gate: str = "stable", text: str | None = TPL,
          version: int = 1, updated: datetime = T, target: str | None = None) -> PF.ClaimRow:
    return PF.ClaimRow(cid(n), "aya", target or f"MATH.SYN.T1.N{n}", key, version, status, {"level": "apply", "gate": gate}, text, updated)


def compile_(*rows, connective=None):
    return PF.compile_page(PF.candidates(list(rows), CFG), CFG, FORBID, connective)


def sec(page, key):
    return next(s for s in page.sections if s["key"] == key)


# ---------- 引用与存储形状 ----------
def test_compile_cites_claims_and_stores_no_text():
    page = compile_(claim(1), claim(2, "syn.mastery.unstable", "hypothesis", gate="execute"), claim(3, "syn.mastery.unstable", "to_verify"),
                    claim(4, "syn.progress.recent"))
    assert page.check_status == "passed" and page.violations == []
    assert [s["key"] for s in page.sections] == list(PF.SECTION_KEYS)
    assert sec(page, "mastered")["statements"] == [{"claim_id": cid(1), "template_key": "syn.mastery.stable", "template_version": 1}]
    assert [x["claim_id"] for x in sec(page, "working_on")["statements"]] == [cid(2)]
    assert [x["claim_id"] for x in sec(page, "working_on")["todo"]] == [cid(3)]       # 待验证只作待办（规则 11）
    assert sec(page, "mastered")["connective"] == "另外" and sec(page, "recent_progress")["connective"] == "最近一段时间"
    assert sec(page, "current_stage")["connective"] == "" and sec(page, "next_step")["connective"] == ""   # 没有陈述的节没有衔接语
    stored = json.dumps(page.sections, ensure_ascii=False)
    assert "合成模板" not in stored and "MATH.SYN" not in stored                        # 陈述文字不落库
    for s in page.sections:
        for item in s["statements"] + s["todo"]:
            assert set(item) == {"claim_id", "template_key", "template_version"}


def test_by_gate_section_and_unconfigured_templates():
    page = compile_(claim(1, "syn.mastery.node", gate="transfer"), claim(2, "syn.mastery.node", gate="execute"),
                    claim(3, "other.template.key"))
    assert [x["claim_id"] for x in sec(page, "mastered")["statements"]] == [cid(1)]
    assert [x["claim_id"] for x in sec(page, "working_on")["statements"]] == [cid(2)]
    assert cid(3) not in json.dumps(page.sections)                                       # 配置外的模板不进页


def test_unrenderable_claims_are_not_stated():
    page = compile_(claim(1, text=None), claim(2, text="（合成）{missing_var}"), claim(3, text="孩子有点懒惰"), claim(4))
    assert [x["claim_id"] for x in sec(page, "mastered")["statements"]] == [cid(4)]


def test_refuted_and_withdrawn_never_candidates():
    rows = [claim(1, status="refuted"), claim(2, status="withdrawn"), claim(3)]
    assert [r.claim_id for r in PF.candidates(rows, CFG)] == [cid(3)]


# ---------- 一致性检查：拦截程度升级（开发计划验收） ----------
BLOCKED = {
    "degree_escalation": (claim(1, "syn.mastery.unstable", gate="execute"), "working_on", "这部分薄弱"),
    "new_conclusion": (claim(1, "syn.mastery.unstable", gate="execute"), "working_on", "已经掌握得很扎实"),
    "number_in_connective": (claim(1), "mastered", "这周做对了3题"),
    "forbidden_inference": (claim(1), "mastered", "你很聪明"),
    "persona_language": (claim(1), "mastered", "点点陪你"),
}


@pytest.mark.parametrize("code", sorted(BLOCKED))
def test_each_violation_blocks_and_falls_back(code):
    row, section, bad = BLOCKED[code]
    page = compile_(row, connective=lambda k, s: bad if k == section else CFG.connectives[k])
    assert page.check_status == "blocked_fallback"
    assert code in {v["code"] for v in page.violations}
    assert all(s["connective"] == "" for s in page.sections)                          # 降级为纯模板版本：没有衔接语
    assert [x["claim_id"] for s in page.sections for x in s["statements"]] == [row.claim_id]   # 陈述保留
    for v in page.violations:
        assert len(v["fragment"]) <= 60 and not any(ch.isdigit() for ch in v["fragment"])
        if v["code"] == "forbidden_inference":
            assert v["fragment"] == ""                                                 # 命中词不进个人层（规则 12）


def test_unstable_claim_with_weak_connective_is_degree_escalation():
    page = compile_(claim(1, "syn.mastery.unstable", gate="execute"), connective=lambda k, s: "这部分薄弱")
    v = next(v for v in page.violations if v["code"] == "degree_escalation")
    assert v["section"] == "working_on" and v["claim_ids"] == [cid(1)] and v["fragment"] == "薄弱"


COMPLIANT = [
    (claim(1),),
    (claim(1), claim(2, "syn.mastery.unstable", "hypothesis", gate="execute")),
    (claim(1, "syn.mastery.weak", gate="understand"), claim(2, "syn.progress.recent"), claim(3, "syn.mastery.node", gate="retain")),
    (claim(1, "syn.mastery.unstable", "to_verify"),),
    (),
]


@pytest.mark.parametrize("rows", COMPLIANT, ids=range(len(COMPLIANT)))
def test_compliant_pages_all_pass(rows):
    page = compile_(*rows)
    assert page.check_status == "passed" and page.violations == []


# ---------- 读取时重核 ----------
def test_read_rechecks_current_claim_state():
    rows = [claim(1), claim(2, target="MATH.SYN.T1.X"), claim(3, "syn.mastery.unstable", "hypothesis", gate="execute"),
            claim(4, "syn.mastery.unstable", "to_verify")]
    stored = compile_(*rows).sections
    current = {r.claim_id: r for r in rows}
    out = PF.render_sections(stored, current, CFG, FORBID)
    assert [x["claim_id"] for x in sec_of(out, "mastered")["statements"]] == [cid(1), cid(2)]
    assert sec_of(out, "mastered")["connective"] == "另外"
    assert sec_of(out, "mastered")["statements"][0]["text"] == "（合成模板）MATH.SYN.T1.N1｜stable"
    # 推翻 / 撤回 / 过期 = 读不到；转待验证；模板版本变了：陈述消失，同节衔接语一起不显示
    now = dict(current)
    del now[cid(2)]
    now[cid(3)] = claim(3, "syn.mastery.unstable", "to_verify")
    out = PF.render_sections(stored, now, CFG, FORBID)
    assert [x["claim_id"] for x in sec_of(out, "mastered")["statements"]] == [cid(1)]
    assert sec_of(out, "mastered")["connective"] == ""
    assert sec_of(out, "working_on")["statements"] == [] and sec_of(out, "working_on")["connective"] == ""
    assert [x["claim_id"] for x in sec_of(out, "working_on")["todo"]] == [cid(4)]
    now[cid(1)] = claim(1, version=2)
    assert sec_of(PF.render_sections(stored, now, CFG, FORBID), "mastered")["statements"] == []
    # 待办里的判断不再是待验证（例如验证后成立）就不再是待办
    now[cid(4)] = claim(4, "syn.mastery.unstable", "established")
    assert sec_of(PF.render_sections(stored, now, CFG, FORBID), "working_on")["todo"] == []


def sec_of(sections, key):
    return next(s for s in sections if s["key"] == key)


def test_read_never_shows_to_verify_as_statement():
    stored = [{"key": "working_on", "connective": "接下来", "todo": [],
               "statements": [{"claim_id": cid(1), "template_key": "syn.mastery.unstable", "template_version": 1}]}]
    out = PF.render_sections(stored, {cid(1): claim(1, "syn.mastery.unstable", "to_verify")}, CFG, FORBID)
    assert all(s["statements"] == [] for s in out)


# ---------- 摘要 ----------
def test_digest_changes_with_claims_and_ignores_order():
    a, b = claim(1), claim(2)
    d = PF.claims_digest([a, b], CFG)
    assert d == PF.claims_digest([b, a], CFG) and len(d) == 64
    assert d != PF.claims_digest([a], CFG)                                                     # 判断消失（推翻、撤回、过期）
    assert d != PF.claims_digest([a, b, claim(3)], CFG)                                        # 新判断
    assert d != PF.claims_digest([a, claim(2, updated=T + timedelta(seconds=1))], CFG)        # 判断被修订
    assert d != PF.claims_digest([a, claim(2, status="to_verify")], CFG)                      # 转待验证


# ---------- 渲染规则（与洞察服务同一套） ----------
def test_render_rules():
    assert render_text("（合成）{node_id}｜{gate}", "MATH.X.Y", {"gate": "stable"}, FORBID) == "（合成）MATH.X.Y｜stable"
    assert render_text("（合成）{gate}", "N", {}, FORBID) is None                         # 缺变量
    assert render_text("（合成）{gate}", "N", {"gate": "不是编码"}, FORBID) is None         # 值不是编码
    assert render_text("（合成）{gate}", "N", {"gate": True}, FORBID) is None               # 布尔不收
    assert render_text("（合成）{p}", "N", {"p": 0.456}, FORBID) == "（合成）0.46"
    assert render_text("x" * 300, "N", {}, FORBID) == "x" * 300
    assert render_text("x" * 301, "N", {}, FORBID) is None
    assert render_text("孩子很聪明", "N", {}, FORBID) is None                              # 禁用推断
    assert render_text("（合成）{s}", "N", {"s": "-leading"}, FORBID) is None              # 编码不能以符号开头
    assert render_text("（合成）{s}", "N", {"s": "a" * 65}, FORBID) is None                # 编码最长 64
    assert render_text("（合成）{n}", "N", {"n": 7}, FORBID) == "（合成）7"
    assert render_text("（合成）{p}", "N", {"p": float("nan")}, FORBID) is None
    assert render_text("（合成）{{gate}}", "N", {"gate": "stable"}, FORBID) is None         # 多余花括号
    assert render_text("  （合成）{gate}  ", "N", {"gate": "stable"}, FORBID) == "（合成）stable"
    assert render_text("（合成）{Upper}", "N", {"Upper": "x"}, FORBID) is None             # 变量名只收小写


# ---------- 配置 ----------
def _docs():
    with open(CONFIG_DIR / "profile-page.synthetic.json", encoding="utf-8") as f:
        doc = json.load(f)
    with open(CONFIG_DIR / "profile-check.synthetic.json", encoding="utf-8") as f:
        check = json.load(f)
    return doc, check


def test_config_rules():
    doc, check = _docs()
    assert PF.parse_profile_config(doc, check, env="test").subjects == frozenset({"aya"})
    with pytest.raises(ConfigError):
        PF.parse_profile_config(doc, check, env="dev")                                   # 合成 / 未签字只允许 test
    signed_check = dict(check, synthetic=False, signed_by="synthetic-signer", signed_at="2026-10-01")
    with pytest.raises(ConfigError, match="档案页配置"):
        PF.parse_profile_config(doc, signed_check, env="dev")                            # 词表签了字，归节配置没签也不行
    signed_doc = dict(doc, synthetic=False, signed_by="synthetic-signer", signed_at="2026-10-01")
    assert PF.parse_profile_config(signed_doc, signed_check, env="dev").signed
    bad = copy.deepcopy(doc)
    bad["connectives"]["working_on"] = "这部分薄弱"                                        # 不在人审短语表里
    with pytest.raises(ConfigError):
        PF.parse_profile_config(bad, check, env="test")
    bad = copy.deepcopy(doc)
    bad["sections"]["syn.mastery.stable"] = {"section": "personality"}
    with pytest.raises(ConfigError):
        PF.parse_profile_config(bad, check, env="test")
    bad = copy.deepcopy(doc)
    bad["subjects"] = ["echo"]
    with pytest.raises(ConfigError):
        PF.parse_profile_config(bad, check, env="test")
    bad = copy.deepcopy(doc)
    bad["max_per_section"] = 31
    with pytest.raises(ConfigError):
        PF.parse_profile_config(bad, check, env="test")


def test_connective_hitting_forbidden_lexicon_refused():
    """短语表本身没把禁用推断词挡住时（一致性检查配置只查程度词、结论词、人设词、数字），档案页配置加载时再挡一次"""
    doc, check = _docs()
    check = copy.deepcopy(check)
    check["connective_phrases"].append("你很聪明")
    doc = copy.deepcopy(doc)
    doc["connectives"]["next_step"] = "你很聪明"
    with pytest.raises(ConfigError, match="禁用推断"):
        PF.parse_profile_config(doc, check, env="test")
    doc["connectives"]["next_step"] = "下一步"
    assert PF.parse_profile_config(doc, check, env="test").connectives["next_step"] == "下一步"


def test_fingerprint_ignores_comments_only():
    doc, check = _docs()
    base = PF.parse_profile_config(doc, check, env="test").fingerprint
    c1, c2 = copy.deepcopy(doc), copy.deepcopy(check)
    c1["$comment"], c2["$comment"] = "（合成）改了注释", "（合成）也改了注释"
    assert PF.parse_profile_config(c1, c2, env="test").fingerprint == base           # 改注释不触发全部重编
    c1["connectives"]["next_step"] = "接下来"
    assert PF.parse_profile_config(c1, c2, env="test").fingerprint != base           # 改生效配置位才变


def test_answer_and_recompute_paths_do_not_reference_profile_compiler():
    """作答与重算不触发编译（第一步 spec 第 14 节）：写入路径、重算、对账、掌握读取的模块都不引用档案页模块"""
    root = Path(PF.__file__).resolve().parents[1]                                       # learning/
    paths = ["service.py", "jobs.py", "plan.py", "mastery/recompute.py", "mastery/api.py", "mastery/jobs.py", "mastery/store.py"]
    for rel in paths:
        src = (root / rel).read_text(encoding="utf-8")
        tree = ast.parse(src)
        names = [a.name for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom)) for a in n.names]
        mods = [n.module or "" for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)]
        mods += [a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names]
        assert not any(m.split(".")[-1] == "profile" or m.endswith("mastery.profile") for m in mods), f"{rel} 引用了档案页模块"
        assert "profile" not in names and "ProfilePages" not in names, f"{rel} 引用了档案页模块"
        assert "ProfilePages" not in src and "ensure_profile" not in src and "profile_page" not in src, f"{rel} 调用了档案页编译"


# ---------- 边界：编译模块不依赖页面仓库客户端、模型与 HTTP ----------
def test_compiler_has_no_page_repo_model_or_http_dependency():
    root = Path(PF.__file__).resolve().parent
    banned = ("model_router", "httpx", "requests", "urllib", "socket", "subprocess", "git", "anthropic", "openai", "knowledge",
              "adapter_client", "pages_repo", "page_repo")
    for name in ("profile.py", "render.py", "consistency.py"):
        tree = ast.parse((root / name).read_text(encoding="utf-8"))
        mods = [a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names]
        mods += [n.module or "" for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)]
        for m in mods:
            assert not any(b in m.split(".") or m.startswith(b) for b in banned), f"{name} 依赖了 {m}"
    assert PF.COMPILER_VERSION.startswith("kge-profile@")
