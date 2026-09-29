"""SymPy 验算与 LaTeX 子集；人设词表的只读抽取。"""
from __future__ import annotations

import shutil

import pytest

from conftest import ROOT
from knowledge.lint import mathcheck
from knowledge.lint.lexicon import (AYA, EXAMINER, FORBIDDEN_YAML, MIAOBI, PERSONA_FILES, Lexicon, LexiconError, load_from_repo)


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
    assert set(lexicon.sources) == {FORBIDDEN_YAML, *PERSONA_FILES}


def test_lexicon_json_round_trip(lexicon):
    assert Lexicon.from_json(lexicon.to_json()) == lexicon


def test_lexicon_json_rejects_wrong_format(lexicon):
    with pytest.raises(LexiconError):
        Lexicon.from_json('{"format": "other"}')
    broken = lexicon.to_json().replace('"regex": "你应该"', '"regex": "(("')
    with pytest.raises(LexiconError):
        Lexicon.from_json(broken)


@pytest.mark.parametrize("rel,anchor", [(AYA, "**F 类 · 数学教学禁语**"), (EXAMINER, "考官型专属 **G 类**"),
                                        (MIAOBI, "**F 类 · 语文教学禁语**")])
def test_lexicon_fails_closed_when_anchor_missing(tmp_path, rel, anchor):
    for f in (FORBIDDEN_YAML, *PERSONA_FILES):
        (tmp_path / f).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(ROOT / f, tmp_path / f)
    load_from_repo(tmp_path)  # 完整副本能抽取
    p = tmp_path / rel
    p.write_text(p.read_text(encoding="utf-8").replace(anchor, "（锚点被改掉）"), encoding="utf-8")
    with pytest.raises(LexiconError, match="锚点"):
        load_from_repo(tmp_path)


def test_lexicon_fails_closed_when_source_missing(tmp_path):
    with pytest.raises(LexiconError, match="找不到"):
        load_from_repo(tmp_path)
