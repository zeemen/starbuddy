"""公式体检（spec 设计要点 4“公式”）：LaTeX 可解析 + SymPy 验算带 `% check` 标记的等式链，不信模型文本。

写法（编纂规范 codification-math-v1 §5）：
  - 行内公式 `$...$`；独立公式块用单独一行的 `$$` 开合，块内一行一个式子。
  - 块内某行以 `% check` 结尾：该行按 `=` 切成等式链，相邻两项必须恒等（SymPy 化简差为 0）。
  - 以 `% check x=2, y=-1` 结尾：把给定取值代入后，链上各项的值必须相等（验解）。
  - 标了 `% check` 却无法解析或含不等号等无法验证的内容 → 阻断；未标记的式子解析失败只报警告。

只认一个 LaTeX 子集（分式、根式、乘方、下标变量、四则、括号、π）。安全边界（parse_expr 内部会 eval，不能放宽成任意文本）：
  1. 转换结果先过字符与标识符白名单、数字长度与指数字面量上限；`.` 只允许出现在两个数字之间（小数点），拦住 `E.n(99999)` 一类属性调用；
  2. parse_expr 的 global_dict 只给最小集合（Integer、Symbol、Rational、Float、Add、Mul、Pow、Function 与 sqrt、root、pi），
     `__builtins__` 置空；先以 evaluate=False 解析，遍历表达式树：只收加、乘、乘方、符号、有理数、浮点数与 π，
     限节点数、嵌套深度，常数指数的绝对值不超过 MAX_EXPONENT（拦住 `2^{2^{2^{20}}}` 这类指数塔），通过后才按 evaluate=True 重新解析；
  3. 验算整体放进 fork 出的子进程（resource.setrlimit 限 CPU 与内存），限时 VERIFY_TIMEOUT 秒；超时、超限或子进程异常一律按
     “无法验证”处理（标了 `% check` 的即阻断）。
消息只给检查结论，不回显公式原文（体检消息只给定位，规则 13）。
"""
from __future__ import annotations

import os
import re
import warnings
from dataclasses import dataclass

import sympy
from sympy.parsing.sympy_parser import implicit_multiplication_application, parse_expr, standard_transformations

MAX_FORMULA_CHARS = 400
MAX_INT_DIGITS = 6
MAX_EXPONENT = 64
MAX_TREE_NODES = 256
MAX_TREE_DEPTH = 32
MAX_ASSIGN_DIGITS = MAX_INT_DIGITS
VERIFY_TIMEOUT = 2.0          # 秒：单个式子验算的墙钟时限
VERIFY_CPU_SECONDS = 3        # 子进程 CPU 时间上限（兜底）
VERIFY_EXTRA_MEMORY = 512 * 1024 * 1024  # 子进程在 fork 时地址空间之上最多再用这么多
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
_ASSIGN = re.compile(r"^\s*([A-Za-z](?:_[A-Za-z0-9]{1,3})?)\s*=\s*(-?\d{1,6}(?:\.\d{1,6})?(?:/\d{1,6})?)\s*$")
_BAD_DOT = re.compile(r"(?<!\d)\.|\.(?!\d)")
_TRANSFORMS = standard_transformations + (implicit_multiplication_application,)
_LOCALS = {"sqrt": sympy.sqrt, "root": sympy.root, "pi": sympy.pi}
# parse_expr 的全局名字只给最小集合：自动数字与符号（Integer、Float、Rational、Symbol）、evaluate=False 改写出的 Add、Mul、Pow、
# 名字后跟括号时 auto_symbol 生成的 Function（随后被隐式乘法改写或在树检查里拒收）；不给 builtins
_GLOBALS = {"__builtins__": {}, "Integer": sympy.Integer, "Float": sympy.Float, "Rational": sympy.Rational, "Symbol": sympy.Symbol,
            "Add": sympy.Add, "Mul": sympy.Mul, "Pow": sympy.Pow, "Function": sympy.Function}


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
            problems.append("不认识的命令")
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
            raise LatexError("验算不支持的命令")
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
        raise LatexError("验算不支持的标识符")
    letters = list(parts.group(1))
    letters[-1] += parts.group(2) or ""
    out = "*".join(letters)
    if not all(_ALLOWED_IDENT.match(x) for x in letters):
        raise LatexError("验算不支持的标识符")
    return out


def _parse(text: str, evaluate: bool) -> sympy.Basic:
    with warnings.catch_warnings():  # 非代数参数（如逗号生成的 Tuple）的弃用警告：随后在树检查里拒收
        warnings.simplefilter("ignore")
        return parse_expr(text, local_dict=dict(_LOCALS), global_dict=dict(_GLOBALS), transformations=_TRANSFORMS, evaluate=evaluate)


def check_tree(expr: sympy.Basic) -> None:
    """evaluate=False 解析出的表达式树：只收加、乘、乘方、符号、有理数、浮点数与 π；限节点数与深度；常数指数 |e| ≤ MAX_EXPONENT"""
    count = 0

    def walk(e: sympy.Basic, depth: int) -> None:
        nonlocal count
        count += 1
        if count > MAX_TREE_NODES:
            raise LatexError("式子的结构超出验算上限")
        if depth > MAX_TREE_DEPTH:
            raise LatexError("式子嵌套太深")
        if isinstance(e, (sympy.Symbol, sympy.Rational, sympy.Float)) or e is sympy.pi:
            return
        if not isinstance(e, (sympy.Add, sympy.Mul, sympy.Pow)):
            raise LatexError("验算不支持的写法")
        for a in e.args:
            walk(a, depth + 1)
        if isinstance(e, sympy.Pow):
            exp = e.args[1]
            if not exp.free_symbols:
                # 子树已检查过（内层常数指数都在上限内），这里按浮点求值只为比较大小
                v = sympy.N(exp, 15)
                if not v.is_number or not v.is_finite or not v.is_real or abs(v) > MAX_EXPONENT:
                    raise LatexError("指数超出验算上限")

    walk(expr, 0)


def to_expr(src: str) -> sympy.Expr:
    src = src.strip()
    if not src:
        raise LatexError("空式子")
    if len(src) > MAX_FORMULA_CHARS:
        raise LatexError("式子太长")
    text = _Converter(src).seq()
    if not _SAFE.match(text):
        raise LatexError("含验算不支持的字符")
    if _BAD_DOT.search(text):
        raise LatexError("小数点只能写在两个数字之间")
    text = _IDENT.sub(_split_ident, text)
    if any(len(n) > MAX_INT_DIGITS for n in _INT.findall(text)):
        raise LatexError("数字位数超出验算上限")
    if any(int(n) > MAX_EXPONENT for n in _POW_LITERAL.findall(text)):
        raise LatexError("指数超出验算上限")
    try:
        raw = _parse(text, evaluate=False)
    except Exception as e:  # noqa: BLE001 parse_expr 抛的异常种类很多，一律视为无法解析
        raise LatexError("SymPy 无法解析") from e
    check_tree(raw)
    try:
        expr = _parse(text, evaluate=True)
    except Exception as e:  # noqa: BLE001
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


def _verify_chain_inproc(formula: str, assignment: str | None) -> CheckOutcome:
    """验算一行等式链（在当前进程里跑；对外用 verify_chain，它把这里放进受限子进程）"""
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
            return CheckOutcome(False, True, f"第 {k + 1} 个等号两边{what}")
    return CheckOutcome(True, True, "通过")


def _limit_child() -> None:
    import resource
    resource.setrlimit(resource.RLIMIT_CPU, (VERIFY_CPU_SECONDS, VERIFY_CPU_SECONDS))
    try:
        with open("/proc/self/statm", encoding="ascii") as f:
            vm = int(f.read().split()[0]) * os.sysconf("SC_PAGE_SIZE")
        limit = vm + VERIFY_EXTRA_MEMORY
        resource.setrlimit(resource.RLIMIT_AS, (limit, limit))
    except (OSError, ValueError):  # 没有 /proc（非 Linux）：只靠 CPU 与墙钟时限
        pass


def _child(conn, formula: str, assignment: str | None) -> None:  # pragma: no cover - 在子进程里运行
    try:
        _limit_child()
        res = _verify_chain_inproc(formula, assignment)
        conn.send((res.ok, res.verifiable, res.message))
    except BaseException:  # noqa: BLE001 子进程里任何异常（含 MemoryError）都按无法验证
        try:
            conn.send((False, False, "验算出错，无法验证"))
        except BaseException:  # noqa: BLE001
            pass
    finally:
        conn.close()
        os._exit(0)


UNVERIFIABLE_LIMIT = CheckOutcome(False, False, "验算超时或超出资源上限，无法验证")


_RESULTS: dict[tuple[str, str | None], CheckOutcome] = {}
_RESULTS_MAX = 4096


def verify_chain(formula: str, assignment: str | None, *, timeout: float = VERIFY_TIMEOUT) -> CheckOutcome:
    """验算一行等式链。assignment 为 None 时验恒等，否则代入取值验相等。
    在 fork 出的子进程里跑（限 CPU、内存与墙钟时间）；超时或超限按无法验证（verifiable = False）。
    子进程正常给出的结论按（式子，取值）缓存在本进程（结论只取决于输入；超时不缓存）"""
    key = (formula, assignment)
    if key in _RESULTS:
        return _RESULTS[key]
    res = _verify_isolated(formula, assignment, timeout)
    if res is not UNVERIFIABLE_LIMIT:
        if len(_RESULTS) >= _RESULTS_MAX:
            _RESULTS.clear()
        _RESULTS[key] = res
    return res


def _verify_isolated(formula: str, assignment: str | None, timeout: float) -> CheckOutcome:
    import multiprocessing as mp
    try:
        ctx = mp.get_context("fork")
    except ValueError:  # 没有 fork 的平台：不做验算，按无法验证（fail closed）
        return UNVERIFIABLE_LIMIT
    recv, send = ctx.Pipe(duplex=False)
    proc = ctx.Process(target=_child, args=(send, formula, assignment), daemon=True)
    proc.start()
    send.close()
    try:
        if not recv.poll(timeout):
            return UNVERIFIABLE_LIMIT
        ok, verifiable, message = recv.recv()
        return CheckOutcome(bool(ok), bool(verifiable), str(message))
    except (EOFError, OSError):
        return UNVERIFIABLE_LIMIT
    finally:
        recv.close()
        if proc.is_alive():
            proc.kill()
        proc.join(1)


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
