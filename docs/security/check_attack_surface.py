#!/usr/bin/env python3
"""攻击面清单与测试的对应检查（specs/household-pentest-sprint3/spec.md 第 7 节“清单与测试的对应”）。

make check-fast / make attack-surface-check 调用。只用标准库，约 0.1 秒。核对：
1. docs/security/attack-surface.md 的条目编号与两份 spec 第 2 节一一对应（不多、不少、不重复）：第一轮
   specs/household-pentest-sprint3/spec.md 的 A–M 类，第二轮 specs/household-pentest-round2/spec.md 的 N–S 类（第二轮 7.9，4-H H6）；
2. “已有自动化断言”一列里的每条引用都真实存在：
   - `ref:路径#片段`：文件存在且含这段文字（表格里的 \\| 还原成 |）。SQL、TS / JS、Python、shell 文件只认
     断言消息、测试名这类“代码里真的用到”的文字，注释里的不算（安全审查建议 13 第二项），规则见 fragment_in；
     其他文件（YAML、授权快照等数据文件）按全文匹配；
   - `mut:名称`：db/tests/mutations.sh 里有这个变异；`mut-bff:名称`：services/bff/tests/mutations.sh 里有；
3. 没有引用的条目，这一列写“无”，而且“渗透测试要做的”一列标了“人工”或“第二轮”；每条都要标这两者之一；
4. docs/security/pentest/scope.md 的 SECURITY DEFINER 函数表与 db/tests/expected/privileges.txt 一致：
   函数清单对 secdef| 行，“可执行的应用角色”一列对 func| 行；
5. 交接包文件齐全，findings.csv 表头与 spec 第 6 节一致；
6. 自测：先用内置的正例、反例（片段只在注释里出现应判失效）核对各语言的注释识别，自测不过检查就失败。
退出码：0 通过；1 失败。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SPECS = [ROOT / "specs/household-pentest-sprint3/spec.md", ROOT / "specs/household-pentest-round2/spec.md"]
SURFACE = ROOT / "docs/security/attack-surface.md"
SCOPE = ROOT / "docs/security/pentest/scope.md"
PRIVILEGES = ROOT / "db/tests/expected/privileges.txt"
MUTATION_FILES = {"mut": ROOT / "db/tests/mutations.sh", "mut-bff": ROOT / "services/bff/tests/mutations.sh"}
HANDOFF = ["scope.md", "rules-of-engagement.md", "report-template.md", "findings.csv", "coverage.md"]
FINDINGS_HEADER = "finding_id,severity,surface_ids,status,fix_pr,regression_test"
COLUMNS = ["编号", "攻击面", "攻击者 / 前提", "现有防护", "已有自动化断言", "渗透测试要做的", "已知遗留"]


def spec_ids(text: str) -> list[str]:
    """spec 第 2 节里以“- A1 ”开头的条目编号"""
    m = re.search(r"^### 2\..*?(?=^### 3\.)", text, re.S | re.M)
    if not m:
        raise SystemExit("attack-surface-check: 失败（spec 里找不到第 2 节）")
    return re.findall(r"^- ([A-Z]\d+) ", m.group(0), re.M)


def split_row(line: str) -> list[str]:
    cells = re.split(r"(?<!\\)\|", line.strip())
    return [c.strip() for c in cells[1:-1]]


def mutation_names(path: Path) -> set[str]:
    return set(re.findall(r'^\s*\["(.+?)"\]=', path.read_text(encoding="utf-8"), re.M))


# ---- `ref:` 片段只在“代码里用到的文字”里匹配，不匹配注释 ----------------------------------------------
# 各语言的词法只做到能分清注释与字符串字面量为止，不是完整的解析器。规则：
# - SQL（.sql）：-- 行注释、/* */ 块注释（可嵌套）排除；'…'（'' 转义）与 E'…'（反斜杠转义）是字符串；
#   "…" 是标识符，跳过；$标签$…$标签$ 当作 PL/pgSQL 函数体或 DO 块，递归按 SQL 处理（测试的断言消息写在里面），
#   所以只当字符串用的 $f$…$f$ 不算字符串。psql 元命令（\echo '…'）里的 '…' 照样是字符串。
# - TS / JS（.ts .tsx .js .mjs .cjs）：// 与 /* */（含 /** */ 文档注释）排除；'…' "…" 是字符串；模板字符串 `…`
#   整段（连同 ${…}）算字符串，${…} 里面再按代码处理；正则字面量 /…/ 跳过（前一个记号是运算符、括号、逗号、
#   return 等时才当正则，否则是除号）。test('名字', …) 的测试名、assert 的消息都是字符串。
# - Python（.py）：# 注释排除；'…' "…" ''' ''' """ """（可带 r/b/u/f 前缀）是字符串，但“独占一个语句”的字符串
#   （括号外、行首开始、结束后到行尾只有空白或注释，即文档字符串与裸字符串语句）当注释排除；
#   另外 def 定义的函数名算测试名（例如 test_xxx）。
# - shell（.sh .bash）：# 在行首或空白、; | & ( ) 之后才是注释（$# ${#a} 不是）；'…' $'…' "…" 是字符串，
#   "…" 里的 $(…) 与反引号按代码递归处理；heredoc（<<EOF / <<-EOF / <<'EOF'，不含 <<<）的正文算字符串；
#   ["名字"]= 形式的变异名本来就是字符串。
# 片段要落在同一个字符串（或同一个函数名）之内；其他扩展名的文件按全文匹配。
LANGS = {".sql": "sql", ".ts": "ts", ".tsx": "ts", ".js": "ts", ".mjs": "ts", ".cjs": "ts",
         ".py": "py", ".sh": "sh", ".bash": "sh"}
_WORD = re.compile(r"[\w$]+")
_DOLLAR_TAG = re.compile(r"\$([A-Za-z_][A-Za-z0-9_]*)?\$")
_REGEX_AFTER_CHAR = set("(,=:[!&|?{};+-*%<>~^")
_REGEX_AFTER_WORD = {"return", "typeof", "case", "in", "of", "delete", "void", "throw", "new", "yield", "await",
                     "else", "do"}
_HEREDOC = re.compile(r"<<(-?)[ \t]*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\2")


def _lex_sql(s: str) -> list[str]:
    out: list[str] = []
    i, n = 0, len(s)
    while i < n:
        c = s[i]
        if s.startswith("--", i):
            j = s.find("\n", i)
            i = n if j < 0 else j
        elif s.startswith("/*", i):
            depth, i = 1, i + 2
            while i < n and depth:
                if s.startswith("/*", i):
                    depth, i = depth + 1, i + 2
                elif s.startswith("*/", i):
                    depth, i = depth - 1, i + 2
                else:
                    i += 1
        elif c == "'":
            esc = i > 0 and s[i - 1] in "eE" and not (i > 1 and (s[i - 2].isalnum() or s[i - 2] == "_"))
            j = i + 1
            while j < n:
                if esc and s[j] == "\\":
                    j += 2
                elif s[j] == "'":
                    if not s.startswith("''", j):
                        break
                    j += 2
                else:
                    j += 1
            out.append(s[i + 1:j])
            i = j + 1
        elif c == '"':
            j = s.find('"', i + 1)
            i = n if j < 0 else j + 1
        elif c == "$" and not (i > 0 and (s[i - 1].isalnum() or s[i - 1] == "_")) and (m := _DOLLAR_TAG.match(s, i)):
            tag = m.group(0)
            j = s.find(tag, m.end())
            j = n if j < 0 else j
            out += _lex_sql(s[m.end():j])
            i = j + len(tag)
        else:
            i += 1
    return out


def _lex_ts(s: str) -> list[str]:
    out: list[str] = []
    n = len(s)

    def code(i: int, in_template: bool) -> int:
        depth, prev = 0, ""
        while i < n:
            c = s[i]
            if s.startswith("//", i):
                j = s.find("\n", i)
                i = n if j < 0 else j
            elif s.startswith("/*", i):
                j = s.find("*/", i + 2)
                i = n if j < 0 else j + 2
            elif c in "'\"":
                j = i + 1
                while j < n and s[j] not in (c, "\n"):
                    j += 2 if s[j] == "\\" else 1
                out.append(s[i + 1:j])
                i, prev = j + 1, "x"
            elif c == "`":
                i, prev = template(i + 1), "x"
            elif c == "/" and (prev == "" or prev in _REGEX_AFTER_CHAR or prev in _REGEX_AFTER_WORD):
                j, in_class = i + 1, False
                while j < n and s[j] != "\n":
                    if s[j] == "\\":
                        j += 2
                        continue
                    if s[j] == "[":
                        in_class = True
                    elif s[j] == "]":
                        in_class = False
                    elif s[j] == "/" and not in_class:
                        break
                    j += 1
                i, prev = j + 1, "x"
            elif c.isalnum() or c in "_$":
                m = _WORD.match(s, i)
                assert m
                i, prev = m.end(), m.group(0)
            else:
                if in_template and c == "{":
                    depth += 1
                elif in_template and c == "}":
                    if depth == 0:
                        return i + 1
                    depth -= 1
                if not c.isspace():
                    prev = c
                i += 1
        return i

    def template(i: int) -> int:
        start = i
        while i < n and s[i] != "`":
            if s[i] == "\\":
                i += 2
            elif s.startswith("${", i):
                i = code(i + 2, True)
            else:
                i += 1
        out.append(s[start:i])
        return i + 1

    code(0, False)
    return out


def _lex_py(s: str) -> list[str]:
    out: list[str] = []
    code_parts: list[str] = []
    i, n, depth, last = 0, len(s), 0, 0  # last：上一段代码的起点，用来截出代码
    while i < n:
        c = s[i]
        if c == "#":
            code_parts.append(s[last:i])
            j = s.find("\n", i)
            i = last = n if j < 0 else j
        elif c in "'\"":
            q = s[i:i + 3] if s[i:i + 3] in ('"""', "'''") else c
            j = i + len(q)
            while j < n and not s.startswith(q, j):
                if s[j] == "\\":
                    j += 2
                elif len(q) == 1 and s[j] == "\n":
                    break
                else:
                    j += 1
            end = min(j + len(q), n)
            line_start = s.rfind("\n", 0, i) + 1
            before = s[line_start:i]
            line_end = s.find("\n", end)
            after = s[end:n if line_end < 0 else line_end]
            continued = line_start >= 2 and s[line_start - 2] == "\\"
            alone = (depth == 0 and not continued and re.fullmatch(r"\s*[rRuUbBfF]{0,2}", before)
                     and re.fullmatch(r"\s*(#.*)?", after))
            if not alone:
                out.append(s[i + len(q):j])
            code_parts.append(s[last:i] + '""')
            i = last = end
        else:
            if c in "([{":
                depth += 1
            elif c in ")]}":
                depth = max(0, depth - 1)
            i += 1
    code_parts.append(s[last:])
    return out + re.findall(r"\bdef\s+(\w+)", "".join(code_parts))


def _lex_sh(s: str) -> list[str]:
    out: list[str] = []
    n = len(s)
    heredocs: list[tuple[str, bool]] = []

    def code(i: int, stop: str | None) -> int:
        depth = 0
        while i < n:
            c = s[i]
            if c == "\\":
                i += 2
            elif c == "#" and (i == 0 or s[i - 1] in " \t\n;|&()"):
                j = s.find("\n", i)
                i = n if j < 0 else j
            elif c == "'":
                j = i + 1
                ansi = i > 0 and s[i - 1] == "$"
                while j < n and s[j] != "'":
                    j += 2 if ansi and s[j] == "\\" else 1
                out.append(s[i + 1:j])
                i = j + 1
            elif c == '"':
                i = double_quoted(i + 1)
            elif s.startswith("$(", i) and not s.startswith("$((", i):
                i = code(i + 2, ")")
            elif c == "`":
                if stop == "`":
                    return i + 1
                i = code(i + 1, "`")
            elif s.startswith("<<", i) and not s.startswith("<<<", i) and (m := _HEREDOC.match(s, i)):
                heredocs.append((m.group(3), m.group(1) == "-"))
                i = m.end()
            elif c == "\n" and heredocs and stop is None:
                i += 1
                for delim, strip_tabs in heredocs:
                    body = i
                    while i < n:
                        j = s.find("\n", i)
                        j = n if j < 0 else j
                        line = s[i:j]
                        if (line.lstrip("\t") if strip_tabs else line) == delim:
                            out.append(s[body:i])
                            i = j + 1
                            break
                        i = j + 1
                    else:
                        out.append(s[body:])
                heredocs.clear()
            else:
                if stop == ")" and c == "(":
                    depth += 1
                elif stop == ")" and c == ")":
                    if depth == 0:
                        return i + 1
                    depth -= 1
                i += 1
        return i

    def double_quoted(i: int) -> int:
        start = i
        while i < n and s[i] != '"':
            if s[i] == "\\":
                i += 2
            elif s.startswith("$(", i) and not s.startswith("$((", i):
                i = code(i + 2, ")")
            elif s[i] == "`":
                i = code(i + 1, "`")
            else:
                i += 1
        out.append(s[start:i])
        return i + 1

    code(0, None)
    return out


LEXERS = {"sql": _lex_sql, "ts": _lex_ts, "py": _lex_py, "sh": _lex_sh}


def fragment_in(text: str, lang: str | None, fragment: str) -> bool:
    """片段是否出现在断言消息 / 测试名 / 字符串字面量里（lang 为 None 时按全文匹配）"""
    if lang is None:
        return fragment in text
    return any(fragment in x for x in LEXERS[lang](text))


# 自测：(语言, 源码, 片段, 是否应命中)。反例是“片段只在注释里出现”，必须判失效
SELFTEST: list[tuple[str, str, str, bool]] = [
    ("sql", "SELECT t.ok(true, '攻击面 甲');", "攻击面 甲", True),
    ("sql", "-- 攻击面 甲\nSELECT 1;", "攻击面 甲", False),
    ("sql", "/* 外 /* 攻击面 甲 */ 仍是注释 */ SELECT 1;", "攻击面 甲", False),
    ("sql", "DO $$ BEGIN\n  -- 攻击面 甲\n  RAISE NOTICE 'ok';\nEND $$;", "攻击面 甲", False),
    ("sql", "DO $body$ BEGIN RAISE EXCEPTION '攻击面 甲'; END $body$;", "攻击面 甲", True),
    ("sql", "SELECT 'it''s -- 攻击面 甲';", "攻击面 甲", True),
    ("sql", "SELECT E'a\\'b';  -- 攻击面 甲", "攻击面 甲", False),
    ("sql", "SELECT \"col--x\" FROM t; -- 攻击面 甲", "攻击面 甲", False),
    ("ts", "test('攻击面 甲', () => {});", "攻击面 甲", True),
    ("ts", "// 攻击面 甲\ntest('x', () => {});", "攻击面 甲", False),
    ("ts", "/** 攻击面 甲 */\nfunction f() {}", "攻击面 甲", False),
    ("ts", "assert.ok(x, `${what} 攻击面 甲`);", "攻击面 甲", True),
    ("ts", "const a = `${b ? 'x' : `y`}`; // 攻击面 甲", "攻击面 甲", False),
    ("ts", "assert.ok(!/\\/home\\/'/i.test(t)); // 攻击面 甲", "攻击面 甲", False),
    ("ts", "const r = a / b; // 攻击面 甲 'x'", "攻击面 甲", False),
    ("py", "def test_attack_surface_a():\n    assert x, '甲'\n", "test_attack_surface_a", True),
    ("py", "# def test_attack_surface_a():\ndef test_b():\n    pass\n", "test_attack_surface_a", False),
    ("py", "def test_b():\n    \"\"\"攻击面 甲\"\"\"\n    assert x\n", "攻击面 甲", False),
    ("py", "def test_b():\n    assert x, (\n        '攻击面 甲'\n    )\n", "攻击面 甲", True),
    ("py", "x = 1  # 攻击面 甲\n", "攻击面 甲", False),
    ("py", "x = '# 不是注释 攻击面 甲'\n", "攻击面 甲", True),
    ("sh", "check '[ 1 ]' \"攻击面 甲\"\n", "攻击面 甲", True),
    ("sh", "# 攻击面 甲\ncheck '[ 1 ]' \"x\"\n", "攻击面 甲", False),
    ("sh", "echo ${#a} $# 'x'  # 攻击面 甲\n", "攻击面 甲", False),
    ("sh", "echo \"$(grep -c \"x\" <<< \"$y\") 攻击面 甲\"\n", "攻击面 甲", True),
    ("sh", "M=(\n  [\"攻击面 甲\"]=\"$(fr 'a' 'b')\"\n)\n", "攻击面 甲", True),
    ("sh", "python3 - <<'PY'\nprint('x')\nPY\n# 攻击面 甲\n", "攻击面 甲", False),
    ("sh", "cat <<EOF\n攻击面 甲\nEOF\n", "攻击面 甲", True),
]


def selftest(errors: list[str]) -> None:
    for lang, src, fragment, want in SELFTEST:
        if fragment_in(src, lang, fragment) != want:
            errors.append(f"自测失败（{lang}）：片段“{fragment}”应判{'有效' if want else '失效'}：{src!r}")


def check_refs(cell: str, muts: dict[str, set[str]], errors: list[str], sid: str) -> int:
    n = 0
    for span in re.findall(r"`([^`]+)`", cell):
        prefix, sep, rest = span.partition(":")
        if not sep or prefix not in ("ref", *muts):
            continue
        n += 1
        if prefix == "ref":
            path, hash_, fragment = rest.partition("#")
            fragment = fragment.replace("\\|", "|")
            target = ROOT / path
            if not hash_ or not fragment:
                errors.append(f"{sid}：引用缺少 #片段：{span}")
            elif not target.is_file():
                errors.append(f"{sid}：引用的文件不存在：{path}")
            else:
                text = target.read_text(encoding="utf-8")
                if not fragment_in(text, LANGS.get(target.suffix), fragment):
                    where = "只出现在注释或其他代码里，不算" if fragment in text else "找不到"
                    errors.append(f"{sid}：{path} 里的“{fragment}”{where}（只认断言消息、测试名、字符串字面量）")
        elif rest not in muts[prefix]:
            errors.append(f"{sid}：{MUTATION_FILES[prefix].relative_to(ROOT)} 里没有变异“{rest}”")
    return n


def main() -> int:
    errors: list[str] = []
    selftest(errors)
    expected =[sid for spec in SPECS for sid in spec_ids(spec.read_text(encoding="utf-8"))]
    if len(expected) != len(set(expected)):
        errors.append("两份 spec 的条目编号有重复")
    muts = {k: mutation_names(p) for k, p in MUTATION_FILES.items()}

    rows: dict[str, list[str]] = {}
    for line in SURFACE.read_text(encoding="utf-8").splitlines():
        if line.startswith("| 编号 "):
            if split_row(line) != COLUMNS:
                errors.append(f"表头不是 {' | '.join(COLUMNS)}：{line}")
            continue
        m = re.match(r"^\| ([A-Z]\d+) \|", line)
        if not m:
            continue
        sid = m.group(1)
        if sid in rows:
            errors.append(f"{sid}：重复出现")
        cells = split_row(line)
        if len(cells) != len(COLUMNS):
            errors.append(f"{sid}：应有 {len(COLUMNS)} 列，实际 {len(cells)} 列（单元格里的 | 要写成 \\|）")
            continue
        rows[sid] = cells

    missing = [s for s in expected if s not in rows]
    extra = [s for s in rows if s not in expected]
    if missing:
        errors.append(f"清单缺少 spec 里的条目：{'、'.join(missing)}")
    if extra:
        errors.append(f"清单有 spec 里没有的条目：{'、'.join(extra)}")

    n_refs = 0
    for sid, cells in rows.items():
        auto, todo = cells[4], cells[5]
        n = check_refs(auto, muts, errors, sid)
        n_refs += n
        if n == 0 and auto != "无":
            errors.append(f"{sid}：没有可核对的引用时，“已有自动化断言”一列只能写“无”（实际：{auto}）")
        if "人工" not in todo and "第二轮" not in todo:
            errors.append(f"{sid}：“渗透测试要做的”一列必须标“人工”或“第二轮”")

    # scope.md 的函数表 ↔ 授权快照
    snapshot = {l[len("secdef|"):] for l in PRIVILEGES.read_text(encoding="utf-8").splitlines() if l.startswith("secdef|")}
    scope = SCOPE.read_text(encoding="utf-8") if SCOPE.is_file() else ""
    table = re.search(r"<!-- secdef-table:start -->(.*?)<!-- secdef-table:end -->", scope, re.S)
    if not table:
        errors.append("scope.md 里找不到 SECURITY DEFINER 函数表（secdef-table 标记）")
    else:
        rows_ = re.findall(r"^\| `((?:platform|insight|kge|policy)\.[a-z_]+\([^`]*\))` \| [^|]+ \| ([^|]+) \|", table.group(1), re.M)
        listed = [f for f, _ in rows_]
        # “可执行的应用角色”一列与快照的 func| 行一致（属主 authz 与超级用户不算）
        granted: dict[str, set[str]] = {}
        for line in PRIVILEGES.read_text(encoding="utf-8").splitlines():
            parts = line.split("|")
            if parts[0] == "func" and parts[3] == "EXECUTE" and parts[2] not in ("starbuddy_authz", "SUPERUSER"):
                granted.setdefault(parts[1], set()).add(parts[2])
        for f, who in rows_:
            want = set() if who.strip().startswith("无") else {
                "starbuddy_" + w.strip() for w in re.split(r"[、,，]", who) if w.strip()}
            if granted.get(f, set()) != want:
                errors.append(f"scope.md 函数表 {f} 的可执行角色写的是“{who.strip()}”，授权快照里是 "
                              f"{'、'.join(sorted(granted.get(f, set()))) or '无'}")
        if len(listed) != len(set(listed)):
            errors.append("scope.md 函数表有重复行")
        for f in sorted(snapshot - set(listed)):
            errors.append(f"scope.md 函数表缺少 {f}（授权快照里是 SECURITY DEFINER）")
        for f in sorted(set(listed) - snapshot):
            errors.append(f"scope.md 函数表多了 {f}（授权快照里不是 SECURITY DEFINER 或不存在）")
        m = re.search(r"### 3\.2 SECURITY DEFINER 函数（(\d+) 个）", scope)
        if not m or int(m.group(1)) != len(snapshot):
            errors.append(f"scope.md 3.2 节标题里的函数个数应为 {len(snapshot)}")

    for name in HANDOFF:
        if not (ROOT / "docs/security/pentest" / name).is_file():
            errors.append(f"交接包缺少 docs/security/pentest/{name}")
    findings = ROOT / "docs/security/pentest/findings.csv"
    if findings.is_file() and findings.read_text(encoding="utf-8").splitlines()[:1] != [FINDINGS_HEADER]:
        errors.append(f"findings.csv 第一行应为 {FINDINGS_HEADER}")

    if errors:
        for e in errors:
            print(f"attack-surface-check: {e}")
        print(f"attack-surface-check: 失败（{len(errors)} 处）")
        return 1
    print(f"attack-surface-check: 通过（{len(rows)} 条攻击面、{n_refs} 条引用全部有效；"
          f"scope.md 的 {len(snapshot)} 个 SECURITY DEFINER 函数与授权快照一致）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
