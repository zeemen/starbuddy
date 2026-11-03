"""共享库 starbuddy_contract.present 的单测（PLT-12，specs/present-item-contract/spec.md）：题面的平台侧白名单。合成数据（规则 8）。

契约 v1.1 候选版里的三个题面示例必须原样通过白名单；答案类字段、多余字段、HTML、危险公式、公网图片、题干与 content_state 不一致都拒绝。
"""
from __future__ import annotations

import copy
import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from starbuddy_contract.checks import ContractViolation  # noqa: E402
from starbuddy_contract.contract import PLUGIN_CONTRACTS, UnsupportedContractVersion, plugin_contract  # noqa: E402
from starbuddy_contract.present import ANSWER_FIELD_NAMES, answer_field_paths, plain_text, project_presented_item  # noqa: E402

EXAMPLES = yaml.safe_load(PLUGIN_CONTRACTS["1.1.0"].read_text(encoding="utf-8"))["components"]["examples"]
NAMES = ["PresentItemResponseAya", "PresentItemResponseAyaWithheld", "PresentItemResponseZhixing"]


def result(name: str) -> dict:
    return copy.deepcopy(EXAMPLES[name]["value"]["result"])


@pytest.mark.parametrize("name", NAMES)
def test_contract_examples_pass_unchanged(name):
    r = result(name)
    assert project_presented_item(r) == r


@pytest.mark.parametrize("name,mutate", [
    ("answer", lambda r: r.__setitem__("answer", ["A"])),
    ("correct_options", lambda r: r.__setitem__("correct_options", ["A"])),
    ("option is_correct", lambda r: r["options"][0].__setitem__("is_correct", True)),
    ("run answer", lambda r: r["stem"][0]["runs"][0].__setitem__("answer", "A")),
    ("paragraph analysis", lambda r: r["stem"][0].__setitem__("analysis", "（合成）")),
    ("format rubric_version", lambda r: r["answer_format"].__setitem__("rubric_version", "x@1")),
    ("extra field", lambda r: r.__setitem__("note", "（合成）")),
    ("html text", lambda r: r["stem"][0]["runs"][0].__setitem__("text", "<b>x</b>")),
    ("control char", lambda r: r["stem"][0]["runs"][0].__setitem__("text", "a\u0007b")),
    ("href latex", lambda r: r["stem"][0]["runs"][1].__setitem__("latex", "\\href{https://x.invalid}{a}")),
    ("html run", lambda r: r["stem"][0]["runs"].append({"type": "html", "html": "<b>x</b>"})),
    ("public image", lambda r: r["stem"][0]["runs"].append({"type": "image", "image_ref": "https://x.invalid/a.png"})),
    ("withheld with stem", lambda r: r.__setitem__("content_state", "withheld")),
    ("complete without stem", lambda r: r.pop("stem")),
    ("option without content", lambda r: r["options"][1].pop("content")),
    ("repeated option key", lambda r: r["options"][1].__setitem__("key", "A")),
    ("bad item_ref", lambda r: r.__setitem__("item_ref", "aya:题目")),
    ("free-text version", lambda r: r.__setitem__("item_version", "最新版")),
    ("unknown type", lambda r: r.__setitem__("item_type", "essay")),
    ("bool max_choices", lambda r: r["answer_format"].__setitem__("max_choices", True)),
])
def test_rejected(name, mutate):
    r = result("PresentItemResponseAya")
    mutate(r)
    with pytest.raises(ContractViolation):
        project_presented_item(r)


INVISIBLE = ["\u202e", "\u202a", "\u2066", "\u2069", "\u200b", "\u200d", "\u200f", "\u2028",
             "\u2029", "\ufeff", "\u0080", "\u0085", "\u009f"]


@pytest.mark.parametrize("ch", INVISIBLE)
def test_invisible_and_bidi_characters_rejected(ch):
    r = result("PresentItemResponseAya")
    r["stem"][0]["runs"][0]["text"] = f"（合成）a{ch}b"
    with pytest.raises(ContractViolation):
        project_presented_item(r)
    r = result("PresentItemResponseAya")
    r["stem"][0]["runs"].append({"type": "image", "image_ref": "objstore://synthetic/aya/fig-01.png", "alt": f"a{ch}b"})
    with pytest.raises(ContractViolation):
        project_presented_item(r)


def test_text_and_alt_regex_match_contract_patterns():
    """共享白名单与契约 pattern 同一字符集：逐个码点对照（BMP 前 0x3000 与几个关键区间）。"""
    import re

    schemas = yaml.safe_load(PLUGIN_CONTRACTS["1.1.0"].read_text(encoding="utf-8"))["components"]["schemas"]
    from starbuddy_contract.present import ALT_RE, TEXT_RE

    for schema, prop, mine in (("RichTextRun", "text", TEXT_RE), ("RichImageRun", "alt", ALT_RE)):
        theirs = re.compile(schemas[schema]["properties"][prop]["pattern"])
        for cp in [*range(0, 0x3000), *range(0xFE00, 0x10000)]:
            s = chr(cp)
            assert bool(theirs.search(s)) == bool(mine.fullmatch(s)), (schema, hex(cp))


def test_total_chars_limit():
    r = result("PresentItemResponseZhixing")
    para = {"type": "paragraph", "runs": [{"type": "text", "text": "合" * 2000}]}
    r["stem"] = [para, copy.deepcopy(para), copy.deepcopy(para), copy.deepcopy(para)]  # 正好 8000
    assert len(project_presented_item(r)["stem"]) == 4
    r["stem"][0]["runs"].append({"type": "math", "latex": "x"})  # 8001
    with pytest.raises(ContractViolation):
        project_presented_item(r)
    r = result("PresentItemResponseAya")  # 选项与图片说明也计入
    r["stem"] = [{"type": "paragraph", "runs": [{"type": "text", "text": "合" * 2000}] * 3 +
                  [{"type": "text", "text": "合" * 1999}, {"type": "image", "image_ref": "objstore://a/b.png", "alt": "图图"}]}]
    with pytest.raises(ContractViolation):
        project_presented_item(r)


def test_answer_field_paths_and_plain_text():
    assert answer_field_paths({"a": [{"Answer": 1}], "solution": None}) == ["/a/0/Answer", "/solution"]
    assert {"answer", "solution", "explanation", "rubric", "scoring_points", "hint_path", "is_correct"} <= ANSWER_FIELD_NAMES
    runs = result("PresentItemResponseAya")["stem"][0]["runs"] + [{"type": "image", "image_ref": "objstore://a/b.png"}]
    assert plain_text(runs) == "（合成示例）把2x-2写成积的形式，下面哪一项正确？[图]"


def test_candidate_gate_for_1_1_0(monkeypatch):
    monkeypatch.delenv("STARBUDDY_ALLOW_CANDIDATE_CONTRACT", raising=False)
    with pytest.raises(UnsupportedContractVersion):
        plugin_contract("1.1.0")
    monkeypatch.setenv("STARBUDDY_ALLOW_CANDIDATE_CONTRACT", "1")
    assert plugin_contract("1.1.0").version == "1.1.0"
