"""引语逐字校验：一致性检查第 7–12 条与逐字口径（specs/quote-verbatim-guard 第二节第 2、3、7 条）。纯函数，不连库。

衔接短语表在合成占位（profile-check.synthetic.json）上追加一条带占位的短语；全部合成数据（规则 8）。
现有第 0–6 条的用例在 test_mastery_consistency.py，本文件不改它们。
"""
from __future__ import annotations

import dataclasses
import random

import pytest

from learning.mastery import consistency as C
from learning.mastery.config import CONFIG_DIR, ConfigError
from learning.mastery.quotes import matches

BASE = C.load_check_config(CONFIG_DIR / "profile-check.synthetic.json", env="test")
PHRASE = "接下来{q:a}"
CFG = dataclasses.replace(BASE, connective_phrases=BASE.connective_phrases | {C.normalize(PHRASE), C.normalize("另外{q:b}")})
FORBID = C.default_forbidden_matcher()
EV, EV2 = "ev-syn-1", "ev-syn-2"
CLAIMS = {
    "claim-a": C.ClaimView("claim-a", "established", "syn.mastery.unstable", evidence_ids=(EV,)),
    "claim-b": C.ClaimView("claim-b", "hypothesis", "syn.mastery.weak", evidence_ids=(EV2,)),
    "claim-v": C.ClaimView("claim-v", "to_verify", "syn.mastery.stable", evidence_ids=(EV,)),
}
OK = {("working_on", "a"): C.QUOTE_OK}


def sec(connective=PHRASE, quotes=(C.Quote("a", "claim-a", EV, "合成作答片段"),), claims=("claim-a",), key="working_on"):
    return C.Section(key, connective, tuple(C.Statement(c, "合成模板渲染文字") for c in claims), tuple(quotes))


def codes(*sections, allowed=True, verdicts=OK):
    return C.check_page(list(sections), CLAIMS, CFG, FORBID, quotes_allowed=allowed, quote_verdicts=verdicts).codes


QUOTE_CODES = {"unmarked_quote", "quote_placeholder", "quote_too_long", "too_many_quotes", "quote_not_cited",
               "quote_unverified", "quote_audience"}


def test_verified_quote_passes_and_finalize_keeps_it():
    assert codes(sec()) == set()
    page = C.finalize([sec()], CLAIMS, CFG, FORBID, quotes_allowed=True, quote_verdicts=OK)
    assert page.check_status == "passed" and page.sections[0].quotes == sec().quotes


def test_defaults_block_quotes_fail_closed():
    """缺省 quotes_allowed=False、没有核对结果：出现引语一律阻断"""
    res = C.check_page([sec()], CLAIMS, CFG, FORBID)
    assert {"quote_audience", "quote_unverified"} <= res.codes


def test_no_quotes_no_new_codes():
    """不带引语的页面：新规则一条也不触发（现有输出不变）"""
    for conn in ("接下来", "另外", ""):
        assert not codes(sec(conn, quotes=()), allowed=False, verdicts=None) & QUOTE_CODES


SPEC_MARKS = "\"'“”‘’「」『』＂＇﹁﹂﹃﹄"   # spec 第二节第 2 条列的引号类字符（写死，不从实现取）


def test_quote_marks_cover_spec():
    assert set(SPEC_MARKS) <= C.QUOTE_MARKS


@pytest.mark.parametrize("mark", sorted(SPEC_MARKS))
def test_unmarked_quote_every_mark(mark):
    assert "unmarked_quote" in codes(sec(f"接下来{mark}合成{mark}", quotes=()))


def test_unmarked_quote_fullwidth_after_nfkc():
    assert "unmarked_quote" in codes(sec("接下来＂合成", quotes=()))


@pytest.mark.parametrize("connective,quotes", [
    ("接下来{q:a}", ()),                                                     # 用了没声明
    ("接下来", (C.Quote("a", "claim-a", EV, "合成作答片段"),)),             # 声明了没用
    ("接下来{q:a}{q:a}", (C.Quote("a", "claim-a", EV, "合成作答片段"),)),   # 用了两次
    ("接下来{q:A1}", (C.Quote("A1", "claim-a", EV, "合成作答片段"),)),       # key 格式不对
    ("接下来{q:a}", (C.Quote("b", "claim-a", EV, "合成作答片段"),)),         # key 对不上
])
def test_placeholder_mismatch(connective, quotes):
    assert "quote_placeholder" in codes(sec(connective, quotes))


def test_too_long_and_empty():
    assert "quote_too_long" in codes(sec(quotes=(C.Quote("a", "claim-a", EV, "合" * (C.QUOTE_MAX_CHARS + 1)),)))
    assert "quote_too_long" in codes(sec(quotes=(C.Quote("a", "claim-a", EV, " ​ "),)))
    assert "quote_too_long" not in codes(sec(quotes=(C.Quote("a", "claim-a", EV, "合" * C.QUOTE_MAX_CHARS),)))


def test_too_many_quotes_in_one_section():
    two = (C.Quote("a", "claim-a", EV, "合成一"), C.Quote("b", "claim-a", EV, "合成二"))
    verdicts = {("working_on", "a"): "ok", ("working_on", "b"): "ok"}
    assert "too_many_quotes" in codes(sec("接下来{q:a}{q:b}", two), verdicts=verdicts)


@pytest.mark.parametrize("quote,claims", [
    (C.Quote("a", "claim-a", EV2, "合成作答片段"), ("claim-a",)),   # 证据不在那条判断的依据里
    (C.Quote("a", "claim-b", EV2, "合成作答片段"), ("claim-a",)),   # 判断不是本节所引
    (C.Quote("a", "claim-v", EV, "合成作答片段"), ("claim-v",)),    # 判断是待验证（不可见）
    (C.Quote("a", "claim-x", EV, "合成作答片段"), ("claim-a",)),    # 判断不存在
])
def test_quote_not_cited(quote, claims):
    assert "quote_not_cited" in codes(sec(quotes=(quote,), claims=claims))


@pytest.mark.parametrize("verdict", ["not_found", "disputed", "no_match", None, "OK", ""])
def test_unverified_unless_ok(verdict):
    v = {} if verdict is None else {("working_on", "a"): verdict}
    assert "quote_unverified" in codes(sec(), verdicts=v)


def test_verdict_keyed_by_section():
    """同一 key 在别的节核对通过，不能顶替本节"""
    assert "quote_unverified" in codes(sec(), verdicts={("other", "a"): "ok"})


def test_audience_guardian_blocked():
    assert "quote_audience" in codes(sec(), allowed=False)
    assert "quote_audience" in codes(sec("接下来{q:a}", quotes=()), allowed=False)   # 只有占位也算


def test_forbidden_inference_checks_quote_text():
    lex = C.default_forbidden_matcher()
    sample = next(w for w in ("太笨了", "笨", "抑郁", "多动症", "智商") if lex(w))
    assert "forbidden_inference" in codes(sec(quotes=(C.Quote("a", "claim-a", EV, sample),)))


def test_quote_text_is_not_checked_as_connective():
    """引语文字不当衔接语查（程度词、数字只查占位以外）；但不在固定短语表里的自由文字仍挡（第 0 条按含占位的整句比对）"""
    assert codes(sec(quotes=(C.Quote("a", "claim-a", EV, "x=3 薄弱"),))) == set()
    assert "connective_unapproved" in codes(sec("接下来你写了{q:a}", quotes=(C.Quote("a", "claim-a", EV, "合成"),)))


def test_fragments_never_carry_quote_text():
    secret = "合成独有片段甲乙"
    res = C.check_page([sec("接下来再看{q:a}", (C.Quote("a", "claim-b", EV, secret),))], CLAIMS, CFG, FORBID)
    assert res.violations and all(secret not in v.fragment for v in res.violations)


def test_blocked_fallback_drops_quotes():
    page = C.finalize([sec()], CLAIMS, CFG, FORBID)
    assert page.check_status == "blocked_fallback" and all(not s.quotes and not s.connective for s in page.sections)


def test_quote_requests_lists_all():
    s2 = sec("另外{q:b}", (C.Quote("b", "claim-b", EV2, "合成"),), ("claim-b",), key="next")
    assert [(k, q.key) for k, q in C.quote_requests([sec(), s2])] == [("working_on", "a"), ("next", "b")]


def test_config_rejects_phrase_with_quote_marks():
    import json
    doc = json.loads((CONFIG_DIR / "profile-check.synthetic.json").read_text(encoding="utf-8"))
    doc["connective_phrases"] = [*doc["connective_phrases"], "接下来“合成”"]
    with pytest.raises(ConfigError):
        C.parse_check_config(doc, env="test")
    doc["connective_phrases"][-1] = "接下来{q:a}"
    C.parse_check_config(doc, env="test")   # 占位本身不是引号、不含数字


# ---- 逐字口径（#1）----

SOURCE = "（合成作答）先移项，再把 x 的系数化为 1。所以 x＝3！"


@pytest.mark.parametrize("q", ["先移项", "先移项，再把x的系数", "x=3!", "ｘ＝３！", "再 把 x", "所以x＝3！", SOURCE[1:20]])
def test_verbatim_matches(q):
    assert matches(SOURCE, q)


@pytest.mark.parametrize("q", ["先移项。", "先移項", "先移项再把", "所以x=4", "移项先", "合成作答）先移项，再把x的系数化为1。所以x＝3！多",
                               "先移项 所以", "", " ", "先" * (C.QUOTE_MAX_CHARS + 1)])
def test_verbatim_rejects(q):
    assert not matches(SOURCE, q)


def test_property_substrings_match_and_single_edits_do_not():
    """性质：随机连续子串都通过；子串里替换、插入、删除一个非空白字符后都不通过（原文里不含该变体时）"""
    rng = random.Random(20261125)
    alphabet = "合成作答先移项再把系数化为所以等式两边同时加减乘除，。！？xyz0123456789（）"
    for _ in range(400):
        src = "".join(rng.choice(alphabet + "  ") for _ in range(rng.randint(5, 60)))
        norm = C.normalize(src)
        if len(norm) < 2:
            continue
        i = rng.randrange(len(norm))
        j = rng.randint(i + 1, min(len(norm), i + C.QUOTE_MAX_CHARS))
        sub = norm[i:j]
        assert matches(src, sub)
        op = rng.choice(("replace", "insert", "delete"))
        k = rng.randrange(len(sub))
        ch = rng.choice([c for c in "甲乙丙丁戊" if c not in norm])
        bad = {"replace": sub[:k] + ch + sub[k + 1:], "insert": sub[:k] + ch + sub[k:], "delete": sub[:k] + sub[k + 1:]}[op]
        if op == "delete" and (not bad or bad in norm):
            continue
        assert not matches(src, bad), (src, sub, bad)


def test_verbatim_length_cap_applies_even_when_source_contains_it():
    src = "合成" * 40
    assert matches(src, src[:C.QUOTE_MAX_CHARS]) and not matches(src, src[:C.QUOTE_MAX_CHARS + 1])
