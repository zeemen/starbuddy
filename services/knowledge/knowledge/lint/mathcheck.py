"""公式体检（spec 设计要点 4“公式”）：LaTeX 可解析 + SymPy 验算带 `% check` 标记的等式链，不信模型文本。

写法（编纂规范 codification-math-v1 §5）：
  - 行内公式 `$...$`；独立公式块用单独一行的 `$$` 开合，块内一行一个式子。
  - 块内某行以 `% check` 结尾：该行按 `=` 切成等式链，相邻两项必须恒等（SymPy 化简差为 0）。
  - 以 `% check x=2, y=-1` 结尾：把给定取值代入后，链上各项的值必须相等（验解）。
  - 标了 `% check` 却无法解析或含不等号等无法验证的内容 → 阻断；未标记的式子解析失败只报警告。

只认一个 LaTeX 子集（分式、根式、乘方、下标变量、四则、括号、π）。转换结果先过字符与标识符白名单、数字长度与指数上限，
再交给 sympy.parse_expr（它内部会 eval，所以白名单是安全边界，不能放宽成任意文本）。
"""
from __future__ import annotations

import re
from dataclasses import dataclass

import sympy
from sympy.parsing.sympy_parser import implicit_multiplication_application, parse_expr, standard_transformations

MAX_FORMULA_CHARS = 400
MAX_INT_DIGITS = 6
MAX_EXPONENT = 64
# 语法检查认识的命令（其中只有一部分能进 SymPy 验算）
KNOWN_COMMANDS = frozenset({
    "frac", "dfrac", "tfrac", "sqrt", "cdot", "times", "div", "left", "right", "pi", "pm", "mp", "le", "ge", "leq", "geq", "ne",
    "neq", "lt", "gt", "approx", "Rightarrow", "Leftrightarrow", "therefore", "because", "angle", "triangle", "circ", "degree",
    "alpha", "beta", "gamma", "theta", "infty", "dots", "cdots", "ldots", "quad", "qquad", "text", "mathrm", "cdotp", "parallel",
    "perp", "cong", "sim", "in", "notin", "cup", "cap", "emptyset", "mid", "overline", "vec",
})
_SPACING = {",", ";", "!", ":", " ", "quad", "qquad"}
_CMD = re.compile(r"\\([A-Za-z]+|.)")
_SAFE = re.compile(r"^[0-9A-Za-z_+\-*/().,\s]*$")
_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_ALLOWED_IDENT = re.compile(r"^(sqrt|root|pi|[A-Za-z](_[A-Za-z0-9]{1,3})?)$")
_INT = re.compile(r"\d+")
_POW_LITERAL = re.compile(r"\*\*\(?\s*(\d+)")
CHECK_MARK = re.compile(r"%\s*check(?:\s+(.*))?$")
_ASSIGN = re.compile(r"^\s*([A-Za-z](?:_[A-Za-z0-9]{1,3})?)\s*=\s*(-?\d+(?:\.\d+)?(?:/\d+)?)\s*$")
_TRANSFORMS = standard_transformations + (implicit_multiplication_application,)
_LOCALS = {"sqrt": sympy.sqrt, "root": sympy.root, "pi": sympy.pi}


class LatexError(ValueError):
    pass


def syntax_problems(src: str) -> list[str]:
    """LaTeX 可解析性：括号成对、命令认识。返回问题列表（空 = 可解析）"""
    problems: list[str] = []
    depth = 0
    for ch in src:
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth < 0:
                problems.append("花括号不成对")
                break
    if depth > 0:
        problems.append("花括号不成对")
    if src.count("\\left") != src.count("\\right"):
        problems.append("\\left 与 \\right 不成对")
    for m in _CMD.finditer(src):
        name = m.group(1)
        if name.isalpha() and name not in KNOWN_COMMANDS:
            problems.append(f"不认识的命令 \\{name}")
    if "$" in src:
        problems.append("公式里还有 $")
    return problems


class _Converter:
    """LaTeX 子集 → Python 表达式文本（只输出白名单字符）"""

    def __init__(self, src: str) -> None:
        self.s = src
        self.i = 0

    def peek(self) -> str:
        return self.s[self.i] if self.i < len(self.s) else ""

    def command(self) -> str:
        m = _CMD.match(self.s, self.i)
        if not m:
            raise LatexError("命令写法不对")
        self.i = m.end()
        return m.group(1)

    def group(self) -> str:
        """{...} 或单个记号"""
        self.skip_space()
        ch = self.peek()
        if ch == "{":
            self.i += 1
            out = self.seq(until="}")
            if self.peek() != "}":
                raise LatexError("花括号不成对")
            self.i += 1
            return out
        if ch == "\\":
            return self.atom()
        if ch and (ch.isalnum()):
            self.i += 1
            return ch
        raise LatexError("缺少参数")

    def skip_space(self) -> None:
        while self.peek() == " ":
            self.i += 1

    def atom(self) -> str:
        ch = self.peek()
        if ch == "\\":
            name = self.command()
            if name in _SPACING or name in ("left", "right"):
                return ""
            if name in ("frac", "dfrac", "tfrac"):
                a, b = self.group(), self.group()
                return f"(({a})/({b}))"
            if name == "sqrt":
                self.skip_space()
                if self.peek() == "[":
                    j = self.s.index("]", self.i) if "]" in self.s[self.i:] else -1
                    if j < 0:
                        raise LatexError("根指数的方括号不成对")
                    n = _Converter(self.s[self.i + 1:j]).seq()
                    self.i = j + 1
                    return f" root(({self.group()}),({n}))"
                return f" sqrt({self.group()})"
            if name in ("cdot", "times"):
                return "*"
            if name == "div":
                return "/"
            if name == "pi":
                return " pi "
            raise LatexError(f"验算不支持 \\{name}")
        if ch == "{":
            self.i += 1
            inner = self.seq(until="}")
            if self.peek() != "}":
                raise LatexError("花括号不成对")
            self.i += 1
            return f"({inner})"
        if ch == "^":
            self.i += 1
            return f"**({self.group()})"
        if ch == "_":
            self.i += 1
            sub = self.group()
            if not re.fullmatch(r"[A-Za-z0-9]{1,3}", sub):
                raise LatexError("下标只收 1–3 位字母或数字")
            return f"_{sub}"
        if ch == "[" or ch == "]":
            self.i += 1
            return "(" if ch == "[" else ")"
        self.i += 1
        return ch

    def seq(self, until: str = "") -> str:
        out = []
        while self.i < len(self.s) and self.peek() != until:
            out.append(self.atom())
        return "".join(out)


def _split_ident(m: re.Match[str]) -> str:
    """相邻字母是隐式乘法：ab → a*b，ab_1 → a*b_1；函数名与 pi 原样保留，其余一律拒绝"""
    ident = m.group(0)
    if ident in ("sqrt", "root", "pi"):
        return ident
    parts = re.fullmatch(r"([A-Za-z]+)(_[A-Za-z0-9]{1,3})?", ident)
    if not parts:
        raise LatexError(f"验算不支持的标识符 {ident}")
    letters = list(parts.group(1))
    letters[-1] += parts.group(2) or ""
    out = "*".join(letters)
    if not all(_ALLOWED_IDENT.match(x) for x in letters):
        raise LatexError(f"验算不支持的标识符 {ident}")
    return out


def to_expr(src: str) -> sympy.Expr:
    src = src.strip()
    if not src:
        raise LatexError("空式子")
    if len(src) > MAX_FORMULA_CHARS:
        raise LatexError("式子太长")
    text = _Converter(src).seq()
    if not _SAFE.match(text):
        raise LatexError("含验算不支持的字符")
    text = _IDENT.sub(_split_ident, text)
    if any(len(n) > MAX_INT_DIGITS for n in _INT.findall(text)):
        raise LatexError("数字位数超出验算上限")
    if any(int(n) > MAX_EXPONENT for n in _POW_LITERAL.findall(text)):
        raise LatexError("指数超出验算上限")
    try:
        expr = parse_expr(text, local_dict=dict(_LOCALS), transformations=_TRANSFORMS, evaluate=True)
    except Exception as e:  # noqa: BLE001 parse_expr 抛的异常种类很多，一律视为无法解析
        raise LatexError("SymPy 无法解析") from e
    if not isinstance(expr, sympy.Expr):
        raise LatexError("不是代数式")
    return expr


@dataclass(frozen=True)
class CheckOutcome:
    ok: bool
    verifiable: bool
    message: str


def _equal(a: sympy.Expr, b: sympy.Expr) -> bool:
    diff = sympy.simplify(sympy.expand(a - b))
    if diff == 0:
        return True
    eq = a.equals(b)
    return eq is True


def verify_chain(formula: str, assignment: str | None) -> CheckOutcome:
    """验算一行等式链。assignment 为 None 时验恒等，否则代入取值验相等"""
    if re.search(r"\\(le|ge|leq|geq|ne|neq|lt|gt|approx)\b|[<>]", formula):
        return CheckOutcome(False, False, "标了 % check 但含不等号或约等号，无法验算")
    parts = formula.split("=")
    if len(parts) < 2:
        return CheckOutcome(False, False, "标了 % check 但没有等号")
    try:
        exprs = [to_expr(p) for p in parts]
        subs: dict[sympy.Symbol, sympy.Expr] = {}
        if assignment is not None:
            for item in assignment.split(","):
                m = _ASSIGN.match(item)
                if not m:
                    return CheckOutcome(False, False, "% check 后的取值写法不对（应为 x=2, y=-1）")
                subs[sympy.Symbol(m.group(1))] = sympy.Rational(m.group(2))
    except LatexError as e:
        return CheckOutcome(False, False, f"标了 % check 但无法解析：{e}")
    for k in range(len(exprs) - 1):
        a, b = exprs[k], exprs[k + 1]
        if subs:
            a, b = a.subs(subs), b.subs(subs)
            if a.free_symbols or b.free_symbols:
                return CheckOutcome(False, False, "代入后仍有未赋值的变量")
        if not _equal(a, b):
            what = "代入后不相等" if subs else "不恒等"
            return CheckOutcome(False, True, f"第 {k + 1} 个等号两边{what}：{parts[k].strip()} ≠ {parts[k + 1].strip()}")
    return CheckOutcome(True, True, "通过")


@dataclass(frozen=True)
class Formula:
    line: int  # 相对段落内容的行号（0 起）
    source: str
    display: bool
    check: str | None  # None = 未标记；"" = 恒等；其余 = 代入取值


_INLINE = re.compile(r"(?<![\\$])\$(?!\$)(.+?)(?<![\\$])\$(?!\$)")
_ONE_LINE_DISPLAY = re.compile(r"^\s*\$\$(.+)\$\$\s*$")


def extract(content: str) -> tuple[list[Formula], list[tuple[int, str]]]:
    """（公式列表，结构问题列表）。结构问题：$$ 块不闭合"""
    formulas: list[Formula] = []
    problems: list[tuple[int, str]] = []
    in_block = False
    block_start = 0
    for idx, ln in enumerate(content.split("\n")):
        if ln.strip() == "$$":
            in_block = not in_block
            block_start = idx
            continue
        if in_block:
            body = ln
            check = None
            m = CHECK_MARK.search(body)
            if m:
                check = (m.group(1) or "").strip()
                body = body[:m.start()]
            elif "%" in body:
                body = body[:body.index("%")]
            if body.strip():
                formulas.append(Formula(idx, body.strip(), True, check))
            continue
        m1 = _ONE_LINE_DISPLAY.match(ln)
        if m1:
            formulas.append(Formula(idx, m1.group(1).strip(), True, None))
            continue
        for m in _INLINE.finditer(ln):
            formulas.append(Formula(idx, m.group(1), False, None))
        rest = _INLINE.sub("", ln).replace("\\$", "")
        if "$" in rest:
            problems.append((idx, "行内公式的 $ 不成对"))
    if in_block:
        problems.append((block_start, "$$ 公式块没有闭合"))
    return formulas, problems
