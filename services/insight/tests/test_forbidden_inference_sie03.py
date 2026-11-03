"""SIE-03 PR④ 禁用推断阻断测试（跨层静态扫描；spec 验收“禁用推断 0”、Gate 0“禁用推断 0”）。

扫描 SIE-03 引入的全部可见编码与文案来源，用 SIE-00 词表的匹配函数（docs/design/sie/sie_lexicon.py，经 insight.shared.forbidden_matcher，
与洞察服务加载配置时是同一个函数）断言零命中。来源按 glob 发现（显式排除写在 EXCLUDE），并断言发现的文件数不少于预期，防止扫描空转：
  1. 洞察服务：config/sie03.synthetic.json 的全部键与值；SIE-03 模块的全部字符串字面量（编码按编码查，其余按文案查）；
  2. 迁移 0032 起的全部 up 脚本：全部字符串字面量（CHECK 枚举、流转表、清单、COMMENT、异常消息，含中文）；回执会展示的“schema.table”名；
  3. BFF：trust.v0.yaml 的全部枚举、description 与 example；src/trust/** 与 trust-routes.ts 的全部字符串字面量；问题码；
  4. 契约 insight-service.v0.9.yaml 的全部 enum 与 const；
  5. 前端：copy.ts 的全部字符串；apps/web/src/trust/**（.ts 与 .tsx）与 lib/trust-api.ts 的全部字符串字面量。
另静态扫描面向学生 / 家长的渲染路径（apps/web/src/trust 与 copy.ts、TrustSlot.tsx）：one_off_slip、careless 等编码不以原文出现在
JSX 文本、字符串、模板字符串与 title / aria-* / alt / placeholder 属性里；编码字段不直接渲染（{x.kind}、`${x.kind}`、String(x.kind)、
x.kind.toString()），映射缺省不回退成编码原文。运行时渲染（真实 DOM 的文本节点）由 apps/web 的 web-test 负责，这里只做静态扫描。
每条扫描都有反例：在测试里注入一个禁用词或原文渲染（只在内存里，不改文件），确认扫描会失败。不连库、不联网；只读仓库文件。
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Callable, Iterable

import pytest
import yaml

from insight.shared import ROOT, forbidden_matcher

MIG = ROOT / "db" / "migrations"
INSIGHT = ROOT / "services" / "insight"
BFF = ROOT / "services" / "bff"
WEB = ROOT / "apps" / "web" / "src"
COPY_TS = WEB / "shell" / "copy.ts"
TRUST_DIR = WEB / "trust"
CONTRACT = ROOT / "packages" / "contracts" / "openapi" / "insight-service.v0.9.yaml"
TRUST_API = BFF / "openapi" / "trust.v0.yaml"
CODE = re.compile(r"^[a-z][a-z0-9_.]{1,62}$")
# 显式排除（发现规则之外的例外，写明理由）
EXCLUDE: dict[str, str] = {
    "services/bff/src/trust/features.ts": "只有功能开关名（dispute / reconcile / data_rights），与路由表同源，不产生学生可见文字",
}

FM = forbidden_matcher()


# ---------------------------------------------------------------- 契约与 BFF 枚举（编码集合从这里派生，不在测试里写死）
def _load(p: Path) -> dict:
    return yaml.safe_load(p.read_text(encoding="utf-8"))


def _schema(doc: dict, name: str) -> dict:
    return doc["components"]["schemas"][name]


def _result(doc: dict, name: str) -> dict:
    return _schema(doc, name)["allOf"][1]["properties"]["result"]["properties"]


def contract_sets() -> dict[str, set[str]]:
    c = _load(CONTRACT)
    return {
        "dispute_kind": set(_schema(c, "InsightDisputeRequest")["properties"]["kind"]["enum"]),
        "next_step": set(_result(c, "InsightDisputeResponse")["next_step"]["enum"]),
        "direction": set(_result(c, "InsightReconcileResponse")["deviation_directions"]["items"]["enum"]),
        "adjustment": set(_result(c, "InsightReconcileResponse")["adjustments"]["items"]["enum"]),
        "erase_scope": set(_result(c, "InsightEraseResponse")["scopes"]["items"]["enum"]),
    }


def bff_sets() -> dict[str, set[str]]:
    b = _load(TRUST_API)
    enums = yaml_values(b, ("enum",))
    statuses = {v for v in enums if v in {"open", "verifying", "resolved_supported", "resolved_unsupported", "fact_corrected", "rejected", "withdrawn"}}
    assert statuses, "trust.v0.yaml 里找不到异议状态枚举"
    return {"dispute_kind": set(_schema(b, "DisputeKind")["enum"]), "dispute_status": _enum_containing(b, "verifying"),
            "cancel_reason": _enum_containing(b, "confirm_expired")}


def _enum_containing(doc: object, member: str) -> set[str]:
    found: list[set[str]] = []

    def walk(n: object) -> None:
        if isinstance(n, dict):
            if isinstance(n.get("enum"), list) and member in n["enum"]:
                found.append({str(x) for x in n["enum"] if x is not None})
            for v in n.values():
                walk(v)
        elif isinstance(n, list):
            for v in n:
                walk(v)
    walk(doc)
    assert found, member
    return set().union(*found)


def raw_codes() -> tuple[str, ...]:
    """面向学生 / 家长时不能以原文出现的编码：契约与 BFF 的异议类型、下一步、偏差方向、调整、异议状态与结论里的多词编码，外加
    七科合同第 5 条禁用的 careless（契约里没有，但最容易被写成编码）。单词编码（none、open 等）太泛，按映射覆盖检查"""
    cs, bs = contract_sets(), bff_sets()
    migration_results = {m for m in sql_literals(read(MIG / "0032_insight__dispute.up.sql")) if CODE.fullmatch(m) and m.endswith(("_recorded", "_supported", "_confirmed"))}
    codes = cs["dispute_kind"] | cs["next_step"] | cs["direction"] | cs["adjustment"] | bs["dispute_kind"] | bs["dispute_status"] | migration_results
    return tuple(sorted({c for c in codes if "_" in c} | {"careless"}))


# 这些字段是编码：不能直接作为子文本或属性文本渲染（只能经 copy.ts 的映射取占位）
CODE_FIELDS = ("kind", "status", "next_step", "result_code", "cancel_reason", "state", "direction", "adjustment", "scope")
TEXT_ATTRS = r"(?:title|alt|placeholder|aria-[a-z]+)"


# ---------------------------------------------------------------- 扫描函数（反例也用它们）
def read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def code_hits(codes: Iterable[str], fm: Callable[[str], list] = FM) -> list[str]:
    """编码原文、以及把 _ 与 . 换成空格后（英文词表按词边界匹配，careless_x 这类要拆开才命中）逐个过词表"""
    return sorted({c for c in codes if fm(c) or fm(re.sub(r"[_.]+", " ", c))})


def text_hits(texts: Iterable[str], fm: Callable[[str], list] = FM) -> list[str]:
    return sorted({t for t in texts if fm(t)})


def all_hits(strings: Iterable[str]) -> list[str]:
    """编码样式的按编码查，其余按文案查"""
    ss = set(strings)
    return code_hits({s for s in ss if CODE.fullmatch(s)}) + text_hits({s for s in ss if not CODE.fullmatch(s)})


def sql_literals(sql: str) -> list[str]:
    """SQL 的全部字符串字面量（含中文、COMMENT、异常消息）；先去掉注释，'' 还原成 '"""
    code = "\n".join(re.sub(r"--.*$", "", line) for line in sql.splitlines())
    return [m.replace("''", "'") for m in re.findall(r"'((?:[^']|'')*)'", code)]


def py_literals(src: str) -> list[str]:
    """Python 的字符串字面量；文档字符串（三引号）是开发说明、不进任何输出，先去掉"""
    src = re.sub(r'"""[\s\S]*?"""|\'\'\'[\s\S]*?\'\'\'', "", src)
    code = "\n".join(re.sub(r"(^|\s)#.*$", r"\1", line) for line in src.splitlines())
    return [a or b for a, b in re.findall(r'"((?:[^"\\\n]|\\.)*)"|\'((?:[^\'\\\n]|\\.)*)\'', code)]


def strip_ts_comments(src: str) -> str:
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    return "\n".join(re.sub(r"(^|\s)//.*$", r"\1", line) for line in src.splitlines())


def ts_strings(src: str) -> list[str]:
    """TS / TSX 里的字符串字面量（单引号、双引号、反引号；不含注释）"""
    code = strip_ts_comments(src)
    return [a or b or c for a, b, c in re.findall(r"'((?:[^'\\\n]|\\.)*)'|\"((?:[^\"\\\n]|\\.)*)\"|`((?:[^`\\]|\\.)*)`", code)]


def yaml_values(node: object, keys: tuple[str, ...]) -> set[str]:
    """YAML 里指定键（enum / const / description / example / examples）下的全部字符串"""
    out: set[str] = set()
    if isinstance(node, dict):
        for k, v in node.items():
            if k in keys:
                out |= {str(x) for x in (v if isinstance(v, list) else [v]) if x is not None and not isinstance(x, (dict, list))}
                if isinstance(v, (dict, list)):
                    out |= _all_scalars(v)
            else:
                out |= yaml_values(v, keys)
    elif isinstance(node, list):
        for v in node:
            out |= yaml_values(v, keys)
    return out


def _all_scalars(node: object) -> set[str]:
    if isinstance(node, dict):
        return set(map(str, node)) | {s for v in node.values() for s in _all_scalars(v)}
    if isinstance(node, list):
        return {s for v in node for s in _all_scalars(v)}
    return set() if node is None else {str(node)}


def json_strings(node: object) -> set[str]:
    if isinstance(node, dict):
        return set(node) | {s for v in node.values() for s in json_strings(v)}
    if isinstance(node, list):
        return {s for v in node for s in json_strings(v)}
    return {node} if isinstance(node, str) else set()


def render_leaks(src: str, codes: tuple[str, ...], *, render_file: bool = True) -> list[str]:
    """渲染路径里编码原文的泄漏。
    render_file（.tsx 与 copy.ts）：任何字符串 / 模板字符串 / JSX 文本 / 文本类属性含编码原文都算；
    非渲染文件（trust 下的 .ts）：编码只能作为完整的字面量（编码数组、比较），嵌在更长的文字或模板字符串里算泄漏。
    另外两类写法都算：编码字段直接作为子文本、属性文本或模板插值渲染；映射缺省回退成编码原文。"""
    code = strip_ts_comments(src)
    leaks = []
    for s in ts_strings(src):
        for r in codes:
            if r in s and (render_file or s != r):
                leaks.append(f"字面量含编码：{s}")
    for text in re.findall(r">([^<>{}]+)<", code):
        if any(r in text for r in codes):
            leaks.append(f"JSX 文本含编码：{text.strip()}")
    for m in re.finditer(rf"\b{TEXT_ATTRS}=(\{{[^}}]*\}}|\"[^\"]*\"|'[^']*')", code):
        if any(r in m.group(1) for r in codes):
            leaks.append(f"文本属性含编码：{m.group(0)}")
    fields = "|".join(CODE_FIELDS)
    field = rf"[a-z][\w?]*(?:\??\.\w+)*\??\.(?:{fields})\b"  # 数据变量（小写开头）上的编码字段；大写开头的是 copy.ts 的文案常量
    patterns = [
        (rf">\s*\{{\s*({field})\s*\}}", "编码字段直接作为子文本渲染"),
        (rf"\b{TEXT_ATTRS}=\{{\s*({field})\s*\}}", "编码字段作为文本属性"),
        (rf"\$\{{\s*({field})\s*\}}", "编码字段插进模板字符串"),
        (rf"\bString\(\s*({field})\s*\)", "String(编码字段)"),
        (rf"({field})\s*\.\s*(?:toString|toUpperCase|toLowerCase|replace\w*)\(", "编码字段上调字符串方法"),
        (rf"(?:\?\?|\|\|)\s*({field})(?!\s*[=!<>])", "映射缺省回退成编码"),  # 后面跟比较运算的是条件判断，不算
    ]
    for pat, what in patterns:
        for m in re.finditer(pat, code):
            leaks.append(f"{what}：{m.group(0).strip()}")
    return leaks


# ---------------------------------------------------------------- 来源发现（glob + 显式排除 + 数量下限）
def rel(p: Path) -> str:
    return p.relative_to(ROOT).as_posix()


def discover(pattern: str, base: Path) -> list[Path]:
    return sorted(p for p in base.glob(pattern) if p.is_file() and rel(p) not in EXCLUDE)


def migration_files() -> list[Path]:
    return [p for p in discover("*.up.sql", MIG) if int(p.name[:4]) >= 32]


def insight_module_files() -> list[Path]:
    names = ("dispute", "predict", "triggers", "guardian_requests", "sie03_policy", "store_sie03", "jobs", "scheduled")
    return [INSIGHT / "insight" / f"{n}.py" for n in names]


def bff_files() -> list[Path]:
    return discover("src/trust/**/*.ts", BFF) + [BFF / "src" / "http" / "trust-routes.ts"]


def web_files() -> list[Path]:
    return discover("**/*.ts*", TRUST_DIR) + [WEB / "lib" / "trust-api.ts"]


def render_files() -> list[Path]:
    return discover("**/*.tsx", TRUST_DIR) + [WEB / "shell" / "TrustSlot.tsx"]


def test_sources_discovered():
    """发现规则真的找到了文件（少于预期说明 glob 失效、扫描空转）"""
    assert len(migration_files()) >= 4 and {"0032", "0033", "0034", "0035"} <= {p.name[:4] for p in migration_files()}
    assert len(bff_files()) >= 4 and all(p.exists() for p in bff_files())
    assert len(web_files()) >= 9 and (WEB / "lib" / "trust-api.ts") in web_files() and any(p.suffix == ".ts" for p in discover("**/*.ts", TRUST_DIR))
    assert len(render_files()) >= 7
    assert all(p.exists() for p in insight_module_files())
    assert all((ROOT / k).exists() for k in EXCLUDE), "排除项指向的文件不存在"


# ---------------------------------------------------------------- 各层的来源
def insight_strings() -> set[str]:
    cfg = json.loads(read(INSIGHT / "config" / "sie03.synthetic.json"))
    out = json_strings(cfg)
    for f in insight_module_files():
        out |= set(py_literals(read(f)))
    return out


def migration_strings() -> set[str]:
    out: set[str] = set()
    for f in migration_files():
        out |= set(sql_literals(read(f)))
    # 回执展示的是“schema.table”：按 0035 清单登记拼出来
    reg = read(MIG / "0035_platform__erasure.up.sql")
    out |= {f"{a}.{b}" for a, b in re.findall(r"\(\s*'(insight|platform)',\s*'([a-z_]+)'", reg)}
    return out


def bff_strings() -> set[str]:
    out = yaml_values(_load(TRUST_API), ("enum", "description", "example", "examples", "summary", "title"))
    for f in bff_files():
        out |= set(ts_strings(read(f)))
    out |= {c.lower() for c in re.findall(r"problem\(\d{3}, '([A-Z_]+)'", read(BFF / "src" / "http" / "problems.ts"))}
    return out


def contract_strings() -> set[str]:
    return yaml_values(_load(CONTRACT), ("enum", "const"))


def web_strings() -> set[str]:
    out = set(ts_strings(read(COPY_TS)))
    for f in web_files():
        out |= set(ts_strings(read(f)))
    return out


# ---------------------------------------------------------------- 匹配函数就是 SIE-00 词表
def test_matcher_is_the_sie00_lexicon():
    """同一个词表：词表每一类的禁止示例都被命中，允许示例都不命中（否则下面的零命中没有意义）"""
    lex = _load(ROOT / "docs" / "design" / "sie" / "forbidden-inference.v0.yaml")
    for c in lex["categories"]:
        for e in c["forbidden_examples"]:
            assert c["id"] in FM(e), (c["id"], e)
    for e in lex.get("allowed_examples") or []:
        assert FM(e) == [], e
    assert FM("careless") and FM("你总是太粗心了"), "词表拦得住英文编码与中文特质标签"


# ---------------------------------------------------------------- 零命中 + 反例（每个来源一组）
SOURCES: dict[str, tuple[Callable[[], set[str]], set[str]]] = {
    # 来源：(取字符串, 必须提取到的样本——证明不是空转)
    "insight": (insight_strings, {"i_know", "one_off_slip", "item_type_transfer", "shorten_task", "claim_ledger"}),
    "migrations": (migration_strings, {"i_know", "self_statement_recorded", "plan_review_node", "diagnosis_optimistic", "uploads",
                                       "insight.dispute", "platform.error_attribution"}),
    "bff": (bff_strings, {"i_know", "verification_scheduled", "calculation_slip", "confirm_expired", "state_conflict", "reauth_required"}),
    "contract": (contract_strings, {"i_know", "one_off_slip", "diagnosis_optimistic", "claim_ledger", "bff"}),
    "web": (web_strings, {"dispute.kind.2", "i_know", "one_off_slip"}),
}


@pytest.mark.parametrize("name", sorted(SOURCES))
def test_zero_hits(name):
    get, must = SOURCES[name]
    strings = get()
    assert must <= strings, sorted(must - strings)
    assert any(not CODE.fullmatch(s) and re.search(r"[一-鿿]", s) for s in strings) or name == "contract", "没有提取到中文文案"
    assert all_hits(strings) == []


@pytest.mark.parametrize("name", sorted(SOURCES))
@pytest.mark.parametrize("injected", ["careless_slip", "【占位·待人审】孩子性格偏内向", "The student seems anxious"])
def test_zero_hits_counterexample(name, injected):
    strings = SOURCES[name][0]()
    assert all_hits(strings | {injected}) == [injected]


def test_migration_literal_injection_is_seen():
    """反例（源码级）：把 0032 的异议类型改成 careless、把 COMMENT 改成禁用表述，提取与扫描都能看到"""
    sql = read(MIG / "0032_insight__dispute.up.sql")
    bad = sql.replace("CHECK (kind IN ('i_know', 'one_off_slip', 'data_error'))", "CHECK (kind IN ('i_know', 'careless', 'data_error'))")
    assert bad != sql
    assert all_hits(sql_literals(bad)) == ["careless"]
    bad2 = sql + "\nCOMMENT ON TABLE insight.dispute IS '这个孩子很懒惰';\n"
    assert all_hits(sql_literals(bad2)) == ["这个孩子很懒惰"]


def test_contract_injection_is_seen():
    doc = _load(CONTRACT)
    doc["components"]["schemas"]["InsightDisputeRequest"]["properties"]["kind"]["enum"].append("careless")
    doc["components"]["schemas"]["InsightDisputeRequest"]["properties"]["kind"]["const"] = "lazy_learner"
    assert all_hits(yaml_values(doc, ("enum", "const"))) == ["careless", "lazy_learner"]


def test_bff_yaml_description_injection_is_seen():
    doc = _load(TRUST_API)
    doc["components"]["schemas"]["DisputeKind"]["description"] = "异议类型：孩子天赋不在理科"
    assert all_hits(yaml_values(doc, ("enum", "description", "example"))) == ["异议类型：孩子天赋不在理科"]


def test_copy_scan_counterexample():
    src = read(COPY_TS).replace("T('dispute.kind.2')", "T('dispute.kind.2', '你总是太粗心了')")
    assert src != read(COPY_TS)
    assert all_hits(ts_strings(src)) == ["你总是太粗心了"]


# ---------------------------------------------------------------- 编码原文不进面向学生 / 家长的渲染路径
def test_raw_code_set_is_derived_from_contract_and_bff():
    codes = raw_codes()
    assert {"one_off_slip", "careless", "calculation_slip", "diagnosis_optimistic", "self_statement_recorded"} <= set(codes), codes


def test_raw_codes_never_rendered():
    codes = raw_codes()
    assert render_leaks(read(COPY_TS), codes) == []
    for f in render_files():
        assert render_leaks(read(f), codes) == [], rel(f)
    for f in web_files():
        if f.suffix == ".ts":
            assert render_leaks(read(f), codes, render_file=False) == [], rel(f)
    # copy.ts 的占位键不含编码原文（键会出现在页面上）
    keys = re.findall(r"\bT\('([^']+)'", read(COPY_TS))
    assert keys and not [k for k in keys if any(r in k for r in codes)]


def test_every_code_has_a_placeholder_mapping():
    """前端拿到的每个编码都有映射到占位键（映射缺了才可能想去渲染原文）；编码集合从契约与 BFF 接口派生"""
    src = strip_ts_comments(read(COPY_TS))

    def mapping(name: str) -> set[str]:
        body = re.search(rf"export const {name}: Record<string, string> = \{{(.*?)\n\}};", src, re.S)
        assert body, name
        return set(re.findall(r"\b([a-z][a-z0-9_]*):", body.group(1)))
    cs, bs = contract_sets(), bff_sets()
    assert cs["dispute_kind"] | bs["dispute_kind"] <= mapping("DISPUTE_KIND")
    assert cs["direction"] <= mapping("DEVIATION_DIRECTION")
    assert cs["adjustment"] <= mapping("ADJUSTMENT")
    assert bs["dispute_status"] <= mapping("DISPUTE_STATUS")
    assert cs["next_step"] <= mapping("DISPUTE_RESULT")
    assert cs["erase_scope"] <= mapping("ERASURE_SCOPE")
    assert bs["cancel_reason"] <= mapping("ERASURE_CANCEL_REASON")


@pytest.mark.parametrize("snippet", [
    "<td>one_off_slip</td>",
    "<td>{d.kind}</td>",
    "<td>{d?.status}</td>",
    "<span>{DISPUTE_KIND[d.kind] ?? d.kind}</span>",
    "<li>{label('careless')}</li>",
    "<td>\n  {row.cancel_reason}\n</td>",
    "<td title={d.kind}>x</td>",
    "<td aria-label=\"one_off_slip\">x</td>",
    "<td>{`类型 ${d.kind}`}</td>",
    "<td>{String(d.kind)}</td>",
    "<td>{d.kind.toString()}</td>",
    "<td>{d.kind.replaceAll('_', ' ')}</td>",
    "<input placeholder={`${d.status}`} />",
])
def test_render_scan_counterexample(snippet):
    """反例：往真实页面源码里注入一处编码原文渲染（只在内存里），扫描必须报出来"""
    src = read(TRUST_DIR / "DisputesPage.tsx")
    injected = src.replace("<td>{DISPUTE_KIND[d.kind]}</td>", snippet)
    assert injected != src, "注入点不存在"
    assert render_leaks(injected, raw_codes()), snippet


def test_non_render_file_counterexample():
    """反例：非渲染的 .ts 里，编码作为完整字面量可以（编码数组），嵌进文字就算泄漏"""
    src = read(TRUST_DIR / "view.ts")
    assert render_leaks(src, raw_codes(), render_file=False) == []
    assert render_leaks(src + "\nexport const X = `类型：one_off_slip`;\n", raw_codes(), render_file=False)
