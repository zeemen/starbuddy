"""SymPy 验算与 LaTeX 子集；人设词表的只读抽取。"""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from conftest import ROOT
from knowledge.lint import mathcheck
from knowledge.lint.lexicon import REGISTRY, Lexicon, LexiconError, load_from_repo


@pytest.mark.parametrize("formula,assign", [
    ("(x+1)^2 = x^2 + 2x + 1", None),
    ("a^2 - b^2 = (a+b)(a-b)", None),
    ("\\frac{1}{2} + \\frac{1}{3} = \\frac{5}{6}", None),
    ("\\sqrt{8} = 2\\sqrt{2}", None),
    ("\\left(a+b\\right)^2 = a^2 + 2ab + b^2", None),
    ("2(x+3) = 2x + 6 = 6 + 2x", None),
    ("3x - 5 = 10", "x=5"),
    ("x + y = 3", "x=1, y=2"),
    ("x_1 + x_1 = 2x_1", None),
    ("\\pi r^2 = r \\cdot r \\cdot \\pi", None),
])
def test_verify_accepts_true_equalities(formula, assign):
    res = mathcheck.verify_chain(formula, assign)
    assert res.ok, res.message


@pytest.mark.parametrize("formula,assign,verifiable", [
    ("(x+1)^2 = x^2 + 1", None, True),
    ("2(x+3) = 2x + 6 = 2x + 5", None, True),
    ("3x - 5 = 10", "x=4", True),
    ("x + y = 3", "x=1", False),           # 代入后还有未赋值的变量
    ("x \\le 3", None, False),
    ("x + 1", None, False),                # 没有等号
    ("\\int x = 1", None, False),          # 不支持的命令
    ("2^{99} = 1", None, False),           # 指数上限
    ("1234567 = 1234567", None, False),    # 数字位数上限
    ("x = 1", "x==1", False),
])
def test_verify_rejects(formula, assign, verifiable):
    res = mathcheck.verify_chain(formula, assign)
    assert not res.ok and res.verifiable is verifiable


@pytest.mark.parametrize("text", ["__import__('os')", "x.__class__", "lambda: 1", "a;b", "'s'", "x@y"])
def test_to_expr_rejects_python_syntax(text):
    with pytest.raises(mathcheck.LatexError):
        mathcheck.to_expr(text)


@pytest.mark.parametrize("text", ["eval(1)", "open(1)", "exec"])
def test_to_expr_turns_names_into_letter_products(text):
    """多字母名字按隐式乘法拆成单字母变量，永远不会成为可调用的 Python 名字"""
    expr = mathcheck.to_expr(text)
    assert all(len(s.name) == 1 for s in expr.free_symbols)


def test_syntax_problems():
    assert mathcheck.syntax_problems("\\frac{1}{2}") == []
    assert mathcheck.syntax_problems("\\text{面积} = ab") == []
    assert "花括号不成对" in mathcheck.syntax_problems("\\frac{1}{2")
    assert any("不认识" in p for p in mathcheck.syntax_problems("\\foo x"))
    assert any("\\left" in p for p in mathcheck.syntax_problems("\\left( x"))


def test_extract_display_inline_and_check_marks():
    content = "## 讲解要点\n\n文字 $x+1$ 与 $y$。\n\n$$\na = b % check\nc = d % check x=1\ne = f % 注释\n$$\n\n$$g=h$$\n\n单个 $ 符号"
    formulas, problems = mathcheck.extract(content)
    got = [(f.source, f.display, f.check) for f in formulas]
    assert got == [("x+1", False, None), ("y", False, None), ("a = b", True, ""), ("c = d", True, "x=1"), ("e = f", True, None),
                   ("g=h", True, None)]
    assert [p[1] for p in problems] == ["行内公式的 $ 不成对"]


# ---------- 词表 ----------

def test_lexicon_extracts_from_persona_docs(lexicon):
    assert {"阿鸦", "点点", "妙笔", "方圆", "春秋", "雅典娜"} <= set(lexicon.role_names)
    assert {"鸦老师", "笔老师"} <= set(lexicon.address_terms)
    assert "你现在是怎么想的" in lexicon.catchphrases
    classes = {r.cls for r in lexicon.forbidden}
    assert classes == {"A", "B", "C", "D", "E", "F", "G"}
    assert any(r.regex == "粗心" and r.subjects == ("aya",) for r in lexicon.forbidden)
    assert REGISTRY in lexicon.sources and "packages/personas/forbidden/manifest.yaml" in lexicon.sources


# 冲刺 3-F 词表收拢：改为复用 guard 的导出之后，与迁移前（从 docs/personas 只读抽取）的词表逐条比对。
# 只允许两处有意的差异（lessons 记录）：考官型的称谓（点点直呼老师名）进了称呼表；阿鸦规格书里被当成口头禅抽出的反例句式
# “你为什么不……”（原文是“而不是‘你为什么不……’”）不再算口头禅。禁语与设定元素逐条不变。
BEFORE = Path(__file__).resolve().parent / "fixtures" / "lexicon-before-persona-guard.json"


def test_lexicon_same_as_before_persona_guard(lexicon):
    old = Lexicon.from_json(BEFORE.read_text(encoding="utf-8"))
    key = lambda r: (r.id, r.cls, r.regex, r.subjects)  # noqa: E731
    assert [key(r) for r in lexicon.forbidden] == [key(r) for r in old.forbidden]
    assert [key(r) for r in lexicon.setting_patterns] == [key(r) for r in old.setting_patterns]
    assert set(lexicon.role_names) == set(old.role_names)
    assert set(lexicon.address_terms) - set(old.address_terms) == {"方圆", "春秋", "雅典娜"} and set(old.address_terms) <= set(lexicon.address_terms)
    assert set(old.catchphrases) - set(lexicon.catchphrases) == {"你为什么不……"} and set(lexicon.catchphrases) <= set(old.catchphrases)
    assert lexicon.max_sentence_chars == old.max_sentence_chars


def test_lexicon_json_round_trip(lexicon):
    assert Lexicon.from_json(lexicon.to_json()) == lexicon


def test_lexicon_json_rejects_wrong_format(lexicon):
    with pytest.raises(LexiconError):
        Lexicon.from_json('{"format": "other"}')
    broken = lexicon.to_json().replace('"regex": "你应该"', '"regex": "(("')
    with pytest.raises(LexiconError):
        Lexicon.from_json(broken)


def _copy_personas(dst: Path) -> None:
    shutil.copytree(ROOT / "packages" / "personas", dst / "packages" / "personas", ignore=shutil.ignore_patterns("__pycache__", "tests"))
    shutil.copytree(ROOT / "docs" / "personas", dst / "docs" / "personas")


@pytest.mark.parametrize("rel,old,new", [
    ("packages/personas/forbidden/G.yaml", "  - { rule_id: G-01,", "  - { rule_id: G-01, pattern_broken: ["),   # YAML 坏了
    ("packages/personas/forbidden/manifest.yaml", "G.yaml, ", ""),                                            # G 类整类缺失
    ("packages/personas/forbidden/F-math.yaml", 'pattern: "粗心"', 'pattern: "(("'),                           # 正则不合法（literal 仍能编译，改成非 literal）
])
def test_lexicon_fails_closed_when_guard_lexicon_broken(tmp_path, rel, old, new):
    _copy_personas(tmp_path)
    load_from_repo(tmp_path)  # 完整副本能加载
    p = tmp_path / rel
    text = p.read_text(encoding="utf-8")
    assert old in text
    if rel.endswith("F-math.yaml"):
        new = new + ", literal: false"
        text = text.replace('literal: true, pattern: "粗心"', 'pattern: "粗心"')
    p.write_text(text.replace(old, new, 1), encoding="utf-8")
    with pytest.raises(LexiconError):
        load_from_repo(tmp_path)


def test_lexicon_fails_closed_when_source_missing(tmp_path):
    with pytest.raises(LexiconError, match="找不到"):
        load_from_repo(tmp_path)


# ---------- 审查处理：SymPy 加固 ----------
@pytest.mark.parametrize("formula", ["2^{2^{2^{20}}} = 1", "E.n(99999) = 1", "N(pi,999999) = 1", "N(\\pi,999999) = 1",
                                     "2^{60+60} = 1", "x.__class__ = 1", "1. = 1", ".5 = 1"])
def test_resource_attacks_rejected_fast(formula):
    import time
    t = time.monotonic()
    res = mathcheck.verify_chain(formula, None)
    assert not res.ok and res.verifiable is False
    assert time.monotonic() - t < mathcheck.VERIFY_TIMEOUT + 1.5


def test_parse_globals_are_minimal():
    g = mathcheck._GLOBALS
    assert g["__builtins__"] == {}
    assert set(g) == {"__builtins__", "Integer", "Float", "Rational", "Symbol", "Add", "Mul", "Pow", "Function"}
    for name in ("N", "E", "exp", "Integral", "evalf"):
        assert name not in g and name not in mathcheck._LOCALS


def test_tree_limits():
    with pytest.raises(mathcheck.LatexError):
        mathcheck.to_expr("+".join(["x"] * 300))           # 节点数上限
    with pytest.raises(mathcheck.LatexError):
        mathcheck.to_expr("(" * 40 + "x" + ")" * 40 + "^{2^{7}}")  # 常数指数 128 超上限
    assert mathcheck.to_expr("x^{\\frac{1}{2}}") is not None   # 分式指数照常


def test_verify_timeout_is_unverifiable(monkeypatch):
    """验算放在子进程里限时：超时按“无法验证”（带 % check 的即阻断），父进程不被拖住"""
    import time

    def slow(formula, assignment):
        time.sleep(30)
    monkeypatch.setattr(mathcheck, "_verify_chain_inproc", slow)
    t = time.monotonic()
    res = mathcheck.verify_chain("x = x + 0 % slow", None, timeout=0.5)
    assert res == mathcheck.UNVERIFIABLE_LIMIT and not res.ok and res.verifiable is False
    assert time.monotonic() - t < 3


def test_verify_child_crash_is_unverifiable(monkeypatch):
    def boom(formula, assignment):
        raise MemoryError
    monkeypatch.setattr(mathcheck, "_verify_chain_inproc", boom)
    res = mathcheck.verify_chain("y = y % crash", None)
    assert not res.ok and res.verifiable is False


def test_assignment_digits_limited():
    res = mathcheck.verify_chain("x^{n} = 1", "x=2, n=9999999")
    assert not res.ok and res.verifiable is False


def test_failure_message_does_not_echo_formula():
    res = mathcheck.verify_chain("(x+1)^2 = x^2 + 1", None)
    assert not res.ok and res.verifiable and "x^2" not in res.message and "(x+1)" not in res.message


@pytest.mark.parametrize("text", ["N( pi ,999999)", "f(x,y)"])
def test_check_tree_rejects_calls_and_tuples(text):
    raw = mathcheck._parse(text, evaluate=False)
    with pytest.raises(mathcheck.LatexError, match="不支持的写法"):
        mathcheck.check_tree(raw)
