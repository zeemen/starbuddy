"""PLT-18 模考第一步：配置与卷清单校验（specs/mock-exam-plt18/spec.md 范围第 1 条）。纯函数，不连库、不发 HTTP；全部合成数据。

- 配置：合成占位只在 test / dev；数值越界、多键、合成带签字被拒；学科码映射来自 config/mock_exam/subjects.json；
- 两份合成卷（数学九年级函数、道法法律领域）通过结构（JSON Schema 草案）与跨字段校验；
- 反例：题目重复、卷面分值和 ≠ 满分、满分或限时与考情配置不一致、题目不在准入清单、题型码不在该科词表或与准入快照不一致、
  合成卷在 pilot、合成来源未标 synthetic、多余键（夹带题目原文）、分值三位小数或布尔；
- 合成卷题号取自现有合成夹具（数学）或明确标注的合成号（道法）；清单里没有题目原文与模型标识；模块不引入数据库层。
"""
from __future__ import annotations

import copy
import json
import subprocess
import sys
from pathlib import Path

import jsonschema
import pytest

from learning.mock_exam import config as C
from learning.mock_exam import manifest as M

HERE = Path(__file__).resolve().parent
LEARNING = HERE.parent
CONF_DIR = LEARNING / "config" / "mock_exam"
FIX_DIR = HERE / "fixtures" / "mock_exam"
MATH_PATH = CONF_DIR / "paper.math-fun-syn.json"
DAOFA_PATH = CONF_DIR / "paper.daofa-law-syn.json"
SCHEMA = json.loads((CONF_DIR / "paper-manifest.schema.json").read_text(encoding="utf-8"))
EXAM_DOC = json.loads((FIX_DIR / "exam-config.synthetic.json").read_text(encoding="utf-8"))
ADMISSION_DOC = json.loads((FIX_DIR / "admission.synthetic.json").read_text(encoding="utf-8"))
ITEM_NODES = json.loads((FIX_DIR / "item-nodes.synthetic.json").read_text(encoding="utf-8"))["items"]
POINT_NODES = json.loads((FIX_DIR / "scoring-points.synthetic.json").read_text(encoding="utf-8"))["items"]
ADMITTED = M.admitted_from(ADMISSION_DOC)
CFG = C.load("synthetic", env="test")


def doc(path: Path = MATH_PATH) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def parse(d: dict, *, env: str = "test", exam_doc: dict = EXAM_DOC, admitted=ADMITTED) -> M.Paper:
    return M.parse(d, exam=M.exam_snapshot_from(exam_doc, d["subject"]), admitted=admitted, env=env)


def load_math() -> M.Paper:
    return M.load(MATH_PATH, exam_doc=EXAM_DOC, admission_doc=ADMISSION_DOC, env="test")


def load_daofa() -> M.Paper:
    return M.load(DAOFA_PATH, exam_doc=EXAM_DOC, admission_doc=ADMISSION_DOC, env="test")


def code_of(fn) -> str:
    with pytest.raises(M.ManifestError) as e:
        fn()
    return e.value.code


# ---------- 配置 ----------
def test_synthetic_config_only_in_test_and_dev():
    for env in ("test", "dev"):
        c = C.load("synthetic", env=env)
        assert c.synthetic and not c.signed
        assert (c.grace_seconds, c.review_confidence_below, c.ticket_ttl_minutes) == (30, 0.6, 240)
    for env in ("pilot", "prod", ""):
        with pytest.raises(C.MockExamConfigUnsigned):
            C.load("synthetic", env=env)


def test_config_rejects_bad_values():
    base = json.loads(C.SYNTHETIC_PATH.read_text(encoding="utf-8"))
    for over in ({"grace_seconds": -1}, {"grace_seconds": 601}, {"grace_seconds": True}, {"review_confidence_below": 1.5},
                 {"review_confidence_below": None}, {"ticket_ttl_minutes": 0}, {"format": "x"}, {"extra": 1},
                 {"signed_by": "syn-reviewer", "signed_at": "2026-10-17T00:00:00Z"}):
        with pytest.raises(C.MockExamConfigError):
            C.parse({**base, **over}, env="test")
    with pytest.raises(C.MockExamConfigError):
        C.load(str(FIX_DIR / "missing.json"), env="test")


def test_signed_config_allowed_outside_test():
    base = json.loads(C.SYNTHETIC_PATH.read_text(encoding="utf-8"))
    c = C.parse({**base, "synthetic": False, "signed_by": "syn-reviewer", "signed_at": "2026-10-17T00:00:00Z"}, env="pilot")
    assert c.signed


def test_subject_mapping_comes_from_config_file():
    sub = json.loads((CONF_DIR / "subjects.json").read_text(encoding="utf-8"))
    assert sub["pending_human_confirmation"] is True
    assert M.POLICY_SUBJECT == {k: v["policy_subject"] for k, v in sub["subjects"].items()} == {"aya": "math", "zhixing": "morality_law"}
    assert sub["subjects"]["zhixing"]["catalog_code"] == "politics"       # 学科目录与考情库对道法写法不同


def test_module_does_not_import_database_layer():
    code = ("import sys; import learning.mock_exam.manifest, learning.mock_exam.session, learning.mock_exam.summary; "
            "bad = [m for m in sys.modules if m == 'asyncpg' or m.startswith('asyncpg.') or m in "
            "('learning.store', 'learning.service', 'learning.app')]; print(bad); sys.exit(1 if bad else 0)")
    r = subprocess.run([sys.executable, "-c", code], cwd=LEARNING, capture_output=True, text=True,
                       env={"PYTHONDONTWRITEBYTECODE": "1", "PATH": "/usr/bin:/bin"})
    assert r.returncode == 0, r.stdout + r.stderr


# ---------- 合成卷通过 ----------
def test_math_paper_valid():
    p = load_math()
    assert (p.paper_id, p.subject, p.version, p.full_cents, p.duration_minutes) == ("SYN-MOCK-MATH-FUN-01", "aya", "0.1.0", 12000, 120)
    assert p.synthetic and p.source == "synthetic"
    assert [s.name for s in p.sections] == ["一、选择题（合成）", "二、填空题（合成）", "三、综合题（合成）"]
    assert [s.full_cents for s in p.sections] == [3000, 3000, 6000]
    assert [len(s.item_ids) for s in p.sections] == [10, 10, 6]
    assert sum(i.paper_cents for i in p.items) == p.full_cents


def test_daofa_paper_valid():
    p = load_daofa()
    assert (p.paper_id, p.subject, p.full_cents, p.duration_minutes) == ("SYN-MOCK-DAOFA-LAW-01", "zhixing", 6000, 60)
    assert [s.full_cents for s in p.sections] == [2400, 3600]
    assert {i.question_type_code for i in p.items} == {"single_choice", "material_analysis"}


def test_both_manifests_pass_json_schema_draft():
    jsonschema.Draft202012Validator.check_schema(SCHEMA)
    v = jsonschema.Draft202012Validator(SCHEMA)
    for path in (MATH_PATH, DAOFA_PATH):
        assert list(v.iter_errors(doc(path))) == []
    bad = doc()
    bad["sections"][0]["items"][0]["stem"] = "夹带题面"
    assert list(v.iter_errors(bad))
    bad = doc()
    bad["synthetic"] = False
    assert list(v.iter_errors(bad))
    bad = doc()
    del bad["duration_minutes"]
    assert list(v.iter_errors(bad))


def test_math_item_ids_come_from_existing_synthetic_fixture():
    fixture = json.loads((LEARNING / "learning" / "diagnosis" / "fixtures" / "quad-synthetic.json").read_text(encoding="utf-8"))
    by_item = {i["item_id"]: i for i in fixture["items"]}
    p = load_math()
    assert {i.item_id for i in p.items} <= set(by_item)
    assert {k: v for k, v in ITEM_NODES.items() if k in by_item} == {k: [v["node_id"]] for k, v in by_item.items()}
    fmt = {"single": "single_choice", "fill": "fill_blank"}
    assert all(ADMITTED[("aya", k)] == fmt[v["format"]] for k, v in by_item.items() if ("aya", k) in ADMITTED)


def test_daofa_item_ids_are_marked_synthetic():
    assert all(i.item_id.startswith("SYN-MOCK-LAW-") for i in load_daofa().items)


def test_manifests_carry_no_item_text_and_no_model_identifiers():
    for path in (MATH_PATH, DAOFA_PATH):
        text = path.read_text(encoding="utf-8")
        assert "model:" not in text
        for k in ("stem", "options", "answer", "material_text", "prompt_question", "points"):
            assert f'"{k}"' not in text


# ---------- 反例 ----------
def test_duplicate_item_rejected():
    d = doc()
    d["sections"][1]["items"][0]["item_id"] = d["sections"][0]["items"][0]["item_id"]
    d["sections"][1]["items"][0]["question_type_code"] = "single_choice"
    assert code_of(lambda: parse(d)) == "ITEM_DUPLICATE"


def test_score_sum_must_equal_full_score():
    d = doc()
    d["sections"][0]["items"][0]["paper_score"] = 4
    assert code_of(lambda: parse(d)) == "SCORE_SUM_MISMATCH"
    d = doc()
    d["sections"][0]["items"][0]["paper_score"] = 2.99
    assert code_of(lambda: parse(d)) == "SCORE_SUM_MISMATCH"


def test_two_decimal_points_summed_exactly():
    d = doc()
    d["sections"][0]["items"][0]["paper_score"] = 2.25
    d["sections"][0]["items"][1]["paper_score"] = 3.75
    p = parse(d)
    assert [i.paper_cents for i in p.items[:2]] == [225, 375]
    for bad in (2.255, True, "3", -3, 0, float("nan")):
        d = doc()
        d["sections"][0]["items"][0]["paper_score"] = bad
        assert code_of(lambda: parse(d)) == "MANIFEST_INVALID"


def test_full_score_must_match_exam_config():
    d = doc()
    d["full_score"] = 123
    d["sections"][2]["items"][0]["paper_score"] = 13          # 分值和仍等于清单满分
    assert code_of(lambda: parse(d)) == "FULL_SCORE_MISMATCH"
    exam = copy.deepcopy(EXAM_DOC)
    exam["subjects"]["math"]["full_score"] = 150
    assert code_of(lambda: parse(doc(), exam_doc=exam)) == "FULL_SCORE_MISMATCH"


def test_duration_must_match_exam_config():
    d = doc()
    d["duration_minutes"] = 100
    assert code_of(lambda: parse(d)) == "DURATION_MISMATCH"
    exam = copy.deepcopy(EXAM_DOC)
    exam["subjects"]["morality_law"]["duration_minutes"] = 90
    assert code_of(lambda: parse(doc(DAOFA_PATH), exam_doc=exam)) == "DURATION_MISMATCH"


def test_items_must_be_admitted():
    d = doc()
    d["sections"][0]["items"][0]["item_id"] = "Q-FUN-U09-P-002"          # 快照里是 pending_review
    assert code_of(lambda: parse(d)) == "ITEM_NOT_ADMITTED"
    d = doc()
    d["sections"][0]["items"][0]["item_id"] = "SYN-NOT-REGISTERED-01"
    assert code_of(lambda: parse(d)) == "ITEM_NOT_ADMITTED"
    d = doc(DAOFA_PATH)
    d["sections"][0]["items"][0]["item_id"] = "SYN-MOCK-LAW-X99"         # source_unverified
    assert code_of(lambda: parse(d)) == "ITEM_NOT_ADMITTED"
    d = doc()                                                            # 道法题号放进数学卷：学科不对也不算准入
    d["sections"][0]["items"][0]["item_id"] = "SYN-MOCK-LAW-S01"
    assert code_of(lambda: parse(d)) == "ITEM_NOT_ADMITTED"


def test_question_type_must_belong_to_subject():
    d = doc()
    d["sections"][0]["items"][0]["question_type_code"] = "material_analysis"    # 道法的题型码，数学词表里没有
    assert code_of(lambda: parse(d)) == "QUESTION_TYPE_UNKNOWN"
    exam = copy.deepcopy(EXAM_DOC)
    exam["subjects"]["math"]["question_type_codes"] = []
    assert code_of(lambda: parse(doc(), exam_doc=exam)) == "EXAM_CONFIG_INVALID"


def test_question_type_must_match_admission_snapshot():
    d = doc()
    d["sections"][0]["items"][0]["question_type_code"] = "fill_blank"           # 词表里有，但准入快照里这题是 single_choice
    assert code_of(lambda: parse(d)) == "QUESTION_TYPE_MISMATCH"
    bad = copy.deepcopy(ADMISSION_DOC)
    del bad["items"][0]["question_type_code"]
    assert code_of(lambda: M.admitted_from(bad)) == "ADMISSION_SNAPSHOT_INVALID"


def test_exam_config_of_other_subject_rejected():
    d = doc()
    assert code_of(lambda: M.parse(d, exam=M.exam_snapshot_from(EXAM_DOC, "zhixing"), admitted=ADMITTED, env="test")) \
        == "EXAM_CONFIG_MISMATCH"
    assert code_of(lambda: M.exam_snapshot_from({"subjects": {}}, "aya")) == "EXAM_CONFIG_MISSING"
    assert code_of(lambda: M.exam_snapshot_from(EXAM_DOC, "wenli")) == "SUBJECT_UNSUPPORTED"


def test_synthetic_paper_only_in_test_and_dev():
    assert parse(doc(), env="dev").synthetic
    assert code_of(lambda: parse(doc(), env="pilot")) == "SYNTHETIC_NOT_ALLOWED"


def test_synthetic_source_must_be_flagged():
    d = doc()
    d["synthetic"] = False
    assert code_of(lambda: parse(d)) == "MANIFEST_INVALID"
    d = doc()
    d["source"] = "curated"
    assert parse(d).source == "curated"           # 教研组卷也可以是合成样例（测试环境）


BREAKS = {
    "顶层夹带题面": lambda d: d.update(stem="x"),
    "题目夹带题面": lambda d: d["sections"][0]["items"][0].update(stem="夹带题面"),
    "题目用旧键名 score": lambda d: d["sections"][0]["items"][0].update(score=d["sections"][0]["items"][0].pop("paper_score")),
    "没有分节": lambda d: d.update(sections=[]),
    "空分节": lambda d: d["sections"][0].update(items=[]),
    "版本格式": lambda d: d.update(version="v1"),
    "格式码": lambda d: d.update(format="starbuddy-mock-paper/2"),
    "卷 ID": lambda d: d.update(paper_id="bad id"),
    "空节名": lambda d: d["sections"][0].update(name=""),
}


@pytest.mark.parametrize("name", list(BREAKS))
def test_structure_errors_rejected(name):
    d = doc()
    BREAKS[name](d)
    assert code_of(lambda: parse(d)) == "MANIFEST_INVALID"


def test_duplicate_section_rejected():
    d = doc()
    d["sections"][1]["name"] = d["sections"][0]["name"]
    assert code_of(lambda: parse(d)) == "SECTION_DUPLICATE"


def test_cents():
    assert [M.cents(v) for v in (0, 3, 2.5, 0.01, 2.25, 120)] == [0, 300, 250, 1, 225, 12000]
    assert [M.cents(v) for v in (0.005, -1, True, None, "1", float("inf"))] == [None] * 6
