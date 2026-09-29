"""生成合成页面仓库夹具（tests/fixtures/pages/）：4 个数学知识点页，节点 ID 为合成 ID，叙述段为合成文本。

用法（在 services/knowledge 下）：.venv/bin/python tests/fixtures/make_pages.py [--check]
--check：只核对仓库里的夹具与生成结果逐字节一致（tests/test_fixtures.py 也会核对）。
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))

from knowledge.atoms import load_snapshot  # noqa: E402
from knowledge.compiler.rebuild import assemble  # noqa: E402
from knowledge.pages.format import KP_SECTIONS, SCHEMA_VERSION, Page, Section, serialize  # noqa: E402

SNAPSHOT = HERE / "map-v1.yaml"
PAGES = HERE / "pages"

# 叙述段（合成文本，中性教研口吻）：段落 ID → 正文（不含二级标题）
NARRATIVE: dict[str, dict[str, str]] = {
    "MATH.SYN.EQ.TRANSPOSE": {
        "key_points": "移项的依据是等式的基本性质：等式两边同时加上或减去同一个数，结果仍相等。[@node:MATH.SYN.EQ.TRANSPOSE]\n\n"
                      "$$\nx + 5 = 9 % check x=4\n$$\n\n"
                      "移项后要检查每一项的符号是否已经改变。[@curriculum:2022/SYN-EQ-02]",
        "pitfalls": "只移动了项却没有改变符号，是移项中出现最多的错误。[@node:MATH.SYN.EQ.TRANSPOSE]",
        "frequent_questions": "- 说明某一步变形用到了等式的哪条性质。[@node:MATH.SYN.EQ.TRANSPOSE]\n"
                              "- 指出移项过程中符号出错的一步。[@node:MATH.SYN.EQ.TRANSPOSE]",
    },
    "MATH.SYN.EQ.LINEAR": {
        "key_points": "解一元一次方程的一般步骤是去分母、去括号、移项、合并同类项、系数化为一。[@node:MATH.SYN.EQ.LINEAR]\n\n"
                      "$$\n2x + 3 = 7 % check x=2\n$$\n\n"
                      "把求得的解代入原方程，左右两边相等才说明解正确。[@curriculum:2022/SYN-EQ-01]",
        "pitfalls": "去括号时括号前是负号，括号里的各项都要改变符号。[@node:MATH.SYN.EQ.LINEAR]\n\n"
                    "移项时漏改符号会直接得到错误的解。[@edge:MATH.SYN.EQ.TRANSPOSE>prerequisite>MATH.SYN.EQ.LINEAR]",
        "frequent_questions": "- 判断一个等式是不是一元一次方程。[@node:MATH.SYN.EQ.LINEAR]\n"
                              "- 已知方程的解，求方程中字母系数的值。[@node:MATH.SYN.EQ.LINEAR]",
    },
    "MATH.SYN.EQ.APPLY": {
        "key_points": "列方程解应用题的关键是找出题中的等量关系，再用未知数表示相关的量。[@node:MATH.SYN.EQ.APPLY]\n\n"
                      "求出解后要检验它是否符合实际意义。[@curriculum:2022/SYN-EQ-03]",
        "pitfalls": "设未知数时没有写单位，或者列出的两边表示的不是同一个量。[@node:MATH.SYN.EQ.APPLY]",
        "frequent_questions": "- 行程问题中根据路程相等列方程。[@node:MATH.SYN.EQ.APPLY]\n"
                              "- 工程问题中根据工作总量列方程。[@node:MATH.SYN.EQ.APPLY]",
    },
    "MATH.SYN.EXPR.SQUARE_DIFF": {
        "key_points": "两个数的和与这两个数的差相乘，等于这两个数的平方差。[@node:MATH.SYN.EXPR.SQUARE_DIFF]\n\n"
                      "$$\n(a+b)(a-b) = a^2 - b^2 % check\n$$\n\n"
                      "用公式计算前，先认清哪一项是相同项，哪一项是相反项。[@curriculum:2022/SYN-EXPR-01]",
        "pitfalls": "把公式右边写成两数差的平方，是本公式最常见的混淆。[@node:MATH.SYN.EXPR.SQUARE_DIFF]\n\n"
                    "$$\n(a-b)^2 = a^2 - 2ab + b^2 % check\n$$",
        "frequent_questions": "- 用平方差公式简便计算两个相近整数的积。[@node:MATH.SYN.EXPR.SQUARE_DIFF]\n"
                              "- 判断一个乘法算式能否用平方差公式。[@node:MATH.SYN.EXPR.SQUARE_DIFF]",
    },
}


def build() -> dict[str, str]:
    snap = load_snapshot(SNAPSHOT)
    known = set(NARRATIVE)
    out = {}
    for node_id, texts in NARRATIVE.items():
        narrative = {s.id: Section(s.id, "narrative", f"## {s.title}\n\n{texts[s.id]}") for s in KP_SECTIONS if s.kind == "narrative"}
        skeleton = Page({"page_id": node_id, "page_type": "kp", "subject": "aya", "node_id": node_id, "map_version": snap.map_version,
                         "schema_version": SCHEMA_VERSION, "page_version": "1.0.0", "risk_level": "low", "rebuild_kind": "render",
                         "locked_sections": [s.id for s in KP_SECTIONS if s.kind == "locked"], "atom_refs": []}, "", ())
        out[f"kp/{node_id}.md"] = serialize(assemble(skeleton, snap, narrative, known))
    return out


def main(argv: list[str]) -> int:
    pages = build()
    if "--check" in argv:
        bad = [rel for rel, text in pages.items() if not (PAGES / rel).is_file() or (PAGES / rel).read_text(encoding="utf-8") != text]
        print("夹具一致" if not bad else f"夹具与生成结果不一致：{bad}")
        return 1 if bad else 0
    for rel, text in pages.items():
        (PAGES / rel).parent.mkdir(parents=True, exist_ok=True)
        (PAGES / rel).write_text(text, encoding="utf-8")
    print(f"已生成 {len(pages)} 页")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
