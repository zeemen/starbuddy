"""KGE-09 第二步（specs/kge09-map-calibration/spec-step2.md）：错因分类表与易错点关联的读取、作答与错因输入守卫、
易错候选（未覆盖的高频错因）、错因难归类信号、节点内部分化（两群检验、学生层面、错因加强证据）的结论与边界、
合成队列上三类埋入结构的召回与误报上限、题目级正确率的人数门槛、防泄露（无精确计数、伪名不外泄、差分测试）。

不连库、不联网、只用合成数据；用洞察服务的 venv 运行（make kge-calibrate-test）。
"""
from __future__ import annotations

import dataclasses
import json
import math
import random
import re
from pathlib import Path
from typing import Any

import pytest

from kge_calibrate import calibrate as C
from kge_calibrate import pitfall as PF
from kge_calibrate import split as SP
from kge_calibrate import stats as S
from kge_calibrate import synth as Y
from kge_calibrate.__main__ import main
from kge_calibrate.config import MIN_ITEM_ATTEMPTS_FLOOR, PENDING, ConfigError, load_config, parse_config
from kge_calibrate.inputs import InputRefused, parse_map
from kge_calibrate.report import build_report, build_step2, render
from kge_calibrate.responses import (ATTEMPTS_FORMAT, ERRORS_FORMAT, ITEM_ID, PITFALL_CODES_FORMAT, Attempts, Errors, Step2,
                                     parse_attempts, parse_errors, parse_pitfall_codes)
from kge_calibrate.taxonomy import CROSS_SUBJECT, load_taxonomy, parse_taxonomy
from test_calibrate import CELL_DELTAS, OUT_OF_TABLE, REPO, _pid, _possible_cells, _raw_config, _walk_numbers

CFG = load_config()
TAX_PATH = REPO / "docs/mapping/error-taxonomy.md"
TAX = load_taxonomy(TAX_PATH)
Z95 = S.z_for(0.95)
K, Y_, G = "shijian_wenti", "yunsuan_cuowu", "gainian_cuowu"     # 都是分类表里“明确”的阿鸦编码
TA = "MATH.X.A"
A, B, C2 = (f"{TA}.N{i}" for i in range(1, 4))
PIT = f"{TA}.PIT"


# ---------- 帮助函数 ----------
def map2(nodes: list[str], pitfalls: tuple[tuple[str, str, str, str | None], ...] = (), extra_edges: list[dict] | None = None):
    """nodes：知识节点（主题取 ID 去掉最后一段）；pitfalls：(易错点 ID, 层级, 父级, has_pitfall 的来源节点或 None)"""
    topics = sorted({n.rsplit(".", 1)[0] for n in nodes} | {TA})
    doc = {"map_version": "0.1.0", "subject": "aya", "status": "published", "standard_year": 2022,
           "nodes": [{"id": "MATH.X", "level": "domain", "name": "测试领域"}]
           + [{"id": t, "level": "topic", "parent_id": "MATH.X", "name": "测试主题"} for t in topics]
           + [{"id": n, "level": "node", "parent_id": n.rsplit(".", 1)[0], "kind": "kp", "name": "测试节点"} for n in nodes]
           + [{"id": p, "level": lv, "parent_id": par, "kind": "pitfall", "name": "测试易错点"} for p, lv, par, _s in pitfalls],
           "edges": [{"from_id": s, "to_id": p, "type": "has_pitfall"} for p, _lv, _par, s in pitfalls if s] + list(extra_edges or [])}
    return parse_map(doc)


def errs(students: list[dict[str, set[str]]], offset: int = 0) -> Errors:
    """students：每名学生 {节点 → 高置信编码集合}（空集合 = 只有低置信行）"""
    ep: dict[str, dict[str, frozenset[str]]] = {}
    for i, st in enumerate(students):
        for n, codes in st.items():
            ep.setdefault(n, {})[_pid(i + offset)] = frozenset(codes)
    return Errors(True, ep, frozenset(_pid(i + offset) for i in range(len(students))))


def rep(n: int, st: dict[str, set[str]]) -> list[dict[str, set[str]]]:
    return [dict(st) for _ in range(n)]


def rec_of(recs: list[dict[str, Any]], node: str, code: str) -> dict[str, Any] | None:
    return next((r for r in recs if r["node"] == node and r["code"] == code), None)


def attempts_split(node: str, counts: dict[tuple[bool, bool], int], low_items: list[str], high_items: list[str],
                   correct: tuple[int, int, int, int] = (2, 0, 3, 1), offset: int = 0) -> tuple[Attempts, dict[tuple[bool, bool], list[str]]]:
    """按（低群过关, 高群过关）的人数构造作答：低群过关答对 correct[0] 道、不过关 correct[1] 道，高群同理（轮换答对哪几道，
    让同一群的题正确率接近）。返回作答与每类学生的伪名（offset 换一批伪名，表键与扰动随之改变）"""
    by_item: dict[str, dict[str, bool]] = {it: {} for it in low_items + high_items}
    who: dict[tuple[bool, bool], list[str]] = {}
    i = 0
    for (lp, hp), n in counts.items():
        for _ in range(n):
            pid = _pid(i + offset)
            who.setdefault((lp, hp), []).append(pid)
            for items, k in ((low_items, correct[0] if lp else correct[1]), (high_items, correct[2] if hp else correct[3])):
                for j, it in enumerate(items):
                    by_item[it][pid] = (j + i) % len(items) < k
            i += 1
    return Attempts(True, {node: by_item}, frozenset(_pid(j + offset) for j in range(i))), who


LOW = [f"Q-L{j}" for j in range(3)]
HIGH = [f"Q-H{j}" for j in range(3)]


def split_rec(counts, correct=(2, 0, 3, 1), errors=None, cfg=CFG, low=LOW, high=HIGH):
    mp = map2([A])
    att, who = attempts_split(A, counts, low, high, correct)
    recs = SP.scan_splits(mp, att, errors, TAX if errors is not None else None, cfg, noisy=False)
    return next(r for r in recs if r["node"] == A), who


# ---------- 错因分类表（只读）与地图里的易错点 ----------
def test_taxonomy_matches_yaml_and_db_enum():
    sql = (REPO / "db/migrations/0001_platform__foundation.up.sql").read_text(encoding="utf-8")
    m = re.search(r"CREATE TYPE platform\.cross_subject_error AS ENUM \((.*?)\);", sql, re.S)
    assert tuple(re.findall(r"'([a-z_]+)'", m.group(1))) == CROSS_SUBJECT
    yml = (REPO / "docs/mapping/error-taxonomy.v0.9.yaml").read_text(encoding="utf-8")
    codes = set(re.findall(r"\{ code: ([a-z0-9_\-]+)", yml))
    tags = {t.strip() for grp in re.findall(r"tags: \[([^\]]*)\]", yml) for t in grp.split(",") if t.strip()}
    assert {c for s in TAX.codes.values() for c in s} == codes | tags
    assert len(TAX.codes["aya"]) == 12 and TAX.codes["aya"]["yunsuan_cuowu"] == "computation_or_grammar"
    assert TAX.codes["wenli"]["prompt_type_confuse"] == "reading_the_question"           # 下辖标签继承所属大类
    assert TAX.codes["wenli"]["logic_disordered"] == "expression_convention"
    assert set(TAX.codes) == {"aya", "wenli", "zhixing", "echo"}


def _yaml_codes() -> dict[str, tuple[str, str]]:
    """YAML 里每个编码（含下辖标签）→ (maps_to, status)；只用正则读，本工具不依赖 PyYAML"""
    yml = (REPO / "docs/mapping/error-taxonomy.v0.9.yaml").read_text(encoding="utf-8")
    out: dict[str, tuple[str, str]] = {}
    for m in re.finditer(r"\{ code: ([a-z0-9_\-]+),.*?maps_to: ([a-z_]+),\s*status: ([a-z]+)(.*?)\}", yml, re.S):
        code, maps_to, status, rest = m.groups()
        out[code] = (maps_to, status)
        for grp in re.findall(r"tags: \[([^\]]*)\]", rest):
            for t in grp.split(","):
                if t.strip():
                    out[t.strip()] = (maps_to, status)
    return out


def test_taxonomy_each_code_matches_yaml():
    """逐编码核对 maps_to 与状态（YAML 的 disputed = 待定；xinli_xiguan 的说明标“敏感”）"""
    yml = _yaml_codes()
    mine = {c: (cat, TAX.status[s][c]) for s, codes in TAX.codes.items() for c, cat in codes.items()}
    assert set(mine) == set(yml) and len(mine) == 47
    for c, (cat, st) in mine.items():
        assert cat == yml[c][0], c
        assert (st == "clear") == (yml[c][1] == "clear"), c
    assert TAX.status["aya"]["xinli_xiguan"] == "sensitive"
    assert TAX.subject_codes("aya") == ("biaoda_cuowu", "fangfa_cuowu", "gainian_cuowu", "shenti_cuowu", "shijian_wenti",
                                        "tuili_cuowu", "yunsuan_cuowu", "zhishi_queshi")
    assert TAX.excluded_codes("aya") == ("biaozheng_cuowu", "fenlei_yilou", "jianyan_cuowu", "xinli_xiguan")
    assert "summary_copy_original" in TAX.excluded_codes("wenli") and "prompt_type_confuse" in TAX.subject_codes("wenli")


def test_synthetic_codes_match_taxonomy():
    assert {c: TAX.codes["aya"][c] for c in Y.CODE_CATEGORY} == Y.CODE_CATEGORY
    assert {c for c, _ in Y.BASE_CODES} <= set(Y.CODE_CATEGORY) and math.isclose(sum(w for _, w in Y.BASE_CODES), 1.0)
    usable = set(TAX.subject_codes("aya"))
    assert {c for n, c in Y.PLANTED_CODE.items() if n != Y.N_PLAIN} <= usable and set(Y.SPLIT_CODES) <= usable
    assert Y.EXCLUDED_PLANTED[1] not in usable and Y.SENSITIVE_CODE not in usable


@pytest.mark.parametrize("mutate,why", [
    (lambda t: t.replace("<!-- BEGIN GENERATED: mapping -->", ""), "BEGIN/END"),
    (lambda t: t.replace("运算语法 `computation_or_grammar` | ✅ 明确 |  |\n| 数学 · 阿鸦 | 十二大错误根因 | `fenlei_yilou`",
                         "运算语法 `careless` | ✅ 明确 |  |\n| 数学 · 阿鸦 | 十二大错误根因 | `fenlei_yilou`"), "不合法"),
    (lambda t: t.replace("`zhishi_queshi` | 知识缺失", "`gainian_cuowu` | 知识缺失"), "重复"),
    (lambda t: t.replace("| 数学 · 阿鸦 | 十二大错误根因 | `yunsuan_cuowu`", "| 数学 | 十二大错误根因 | `yunsuan_cuowu`"), "学科不认识"),
    (lambda t: t.replace("| 知识 `knowledge` | ✅ 明确 |  |\n| 数学 · 阿鸦 | 十二大错误根因 | `zhishi_queshi`",
                         "| 知识 `knowledge` | 明确 |  |\n| 数学 · 阿鸦 | 十二大错误根因 | `zhishi_queshi`"), "状态"),
])
def test_taxonomy_refused(mutate, why):
    text = TAX_PATH.read_text(encoding="utf-8")
    bad = mutate(text)
    assert bad != text
    with pytest.raises(InputRefused, match=why):
        parse_taxonomy(bad)


def test_item_id_matches_knowledge_service():
    src = (REPO / "services/knowledge/knowledge/m2.py").read_text(encoding="utf-8")
    assert re.search(r'^ITEM_ID = re\.compile\(r"(.+?)"\)$', src, re.M).group(1) == ITEM_ID.pattern


def test_map_pitfall_links():
    mp = parse_map(Y.map_snapshot())
    assert mp.pitfalls_of == {Y.N_UNCOVERED: ("MATH.CALIB.T5.P3",), Y.N_COVERED: ("MATH.CALIB.T5.P1",),
                              Y.N_SPLIT: (f"{Y.N_SPLIT}.SLIP1", f"{Y.N_SPLIT}.SLIP2"), Y.N_UNMAPPED: ("MATH.CALIB.T5.P2",)}
    assert mp.pitfall_ids == {p[0] for p in Y.PITFALLS} and not (mp.pitfall_ids & set(mp.nodes))
    doc = Y.map_snapshot()
    doc["nodes"].append({"id": "MATH.CALIB.T5.OLDP", "level": "node", "parent_id": "MATH.CALIB.T5", "kind": "pitfall", "name": "撤下",
                         "status": "deprecated"})
    doc["edges"] += [{"from_id": Y.N_PLAIN, "to_id": "MATH.CALIB.T5.OLDP", "type": "has_pitfall"},          # 撤下的易错点
                     {"from_id": Y.N_PLAIN, "to_id": "MATH.CALIB.T5.P1", "type": "has_pitfall", "status": "deprecated"},
                     {"from_id": Y.N_PLAIN, "to_id": Y.N_TIERS, "type": "has_pitfall"},                       # 终点不是易错点
                     {"from_id": Y.N_LOWCONF, "to_id": "MATH.CALIB.T5.P1", "type": "related"}]
    mp2 = parse_map(doc)
    assert mp2.pitfalls_of == mp.pitfalls_of and "MATH.CALIB.T5.OLDP" not in mp2.pitfall_ids
    doc["edges"].append({"from_id": Y.N_PLAIN, "to_id": "MATH.CALIB.T5.P1", "type": "has_pitfall"})
    assert parse_map(doc).pitfalls_of[Y.N_PLAIN] == ("MATH.CALIB.T5.P1",)


# ---------- 配置 ----------
def test_step2_config_defaults_pending():
    assert CFG.signoff == PENDING and "待教研确认" in _raw_config()["pitfall"]["$comment"] and "待教研确认" in _raw_config()["split"]["$comment"]
    assert CFG.split.min_item_attempts == 20 == MIN_ITEM_ATTEMPTS_FLOOR and CFG.pitfall.min_confidence == 0.6
    assert (CFG.max_false_pitfall, CFG.max_false_unclassifiable, CFG.max_false_split) == (0, 0, 0)


@pytest.mark.parametrize("path,value", [
    (("split", "min_item_attempts"), 19), (("split", "min_items_per_group"), 1), (("split", "bootstrap_draws"), 10),
    (("split", "pass_rate"), 1.0), (("split", "max_h_ratio"), 1.5), (("split", "fdr_q"), 0.5), (("pitfall", "min_lift"), 0.0),
    (("split", "bootstrap_draws"), 10_001), (("split", "max_items_per_node"), 3), (("split", "max_items_per_node"), 5),
    (("pitfall", "fdr_q"), 0.5), (("pitfall", "min_confidence"), "high"), (("pitfall", "low_conf_share"), 1.0),
    (("synthetic_acceptance", "max_false_split"), -1), (("synthetic_acceptance", "max_false_pitfall"), 1.5)])
def test_step2_config_rejects(path, value):
    doc = _raw_config()
    doc[path[0]][path[1]] = value
    with pytest.raises(ConfigError):
        parse_config(doc)


def test_step2_config_rejects_extra_and_missing():
    for sec, key in (("split", "max_h_ratio"), ("split", "max_items_per_node"), ("pitfall", "low_conf_share"),
                     ("synthetic_acceptance", "max_false_unclassifiable")):
        doc = _raw_config()
        del doc[sec][key]
        with pytest.raises(ConfigError):
            parse_config(doc)
    doc = _raw_config()
    doc["split"]["min_students"] = 5
    with pytest.raises(ConfigError):
        parse_config(doc)
    doc = _raw_config()
    del doc["pitfall"]
    with pytest.raises(ConfigError):
        parse_config(doc)


# ---------- 输入守卫：作答聚合表、错因表、易错点编码对照 ----------
MP = parse_map(Y.map_snapshot())
N = Y.N_PLAIN
P1, P2 = "syn-aaaaaaaaaa", "syn-bbbbbbbbbb"
AMETA = f"# format={ATTEMPTS_FORMAT} synthetic=true subject=aya map_version=0.1.0"
EMETA = f"# format={ERRORS_FORMAT} synthetic=true subject=aya map_version=0.1.0"
AHEAD, EHEAD = "pseudonym,item_id,node_id,correct", "pseudonym,node_id,subject_category,cross_subject_category,confidence"


def _a(*rows: str, head: str = AHEAD, meta: str = AMETA) -> str:
    return "\n".join([meta, head, *rows]) + "\n"


def _e(*rows: str, head: str = EHEAD, meta: str = EMETA) -> str:
    return "\n".join([meta, head, *rows]) + "\n"


def _refused_a(text: str) -> str:
    with pytest.raises(InputRefused) as ei:
        parse_attempts(text, MP)
    return str(ei.value)


def _refused_e(text: str) -> str:
    with pytest.raises(InputRefused) as ei:
        parse_errors(text, MP, TAX, CFG.pitfall.min_confidence)
    return str(ei.value)


def test_attempts_parse():
    a = parse_attempts(_a(f"{P1},Q-1,{N},1", f"{P2},Q-1,{N},0", f"{P1},Q-1,{Y.N_SPLIT},0"), MP)
    assert a.by_node == {N: {"Q-1": {P1: True, P2: False}}, Y.N_SPLIT: {"Q-1": {P1: False}}} and a.students == 2 and a.synthetic


@pytest.mark.parametrize("row,why", [
    (f"syn-abcdefghi,Q-1,{N},1", "伪名格式"), (f"pil-abcdefghij,Q-1,{N},1", "syn- 开头"),
    (f"{P1},-bad,{N},1", "题目 ID"), (f"{P1},Q 1,{N},1", "题目 ID"), (f"{P1},Q-1,{N},2", "correct"), (f"{P1},Q-1,{N},true", "correct"),
    (f"{P1},Q-1,MATH.CALIB.T5.P1,1", "可计算的知识节点"),                 # 易错点不是可计算节点
    (f"{P1},13812345678,{N},1", "个人信息"), (f"{P1},Q-张,{N},1", "个人信息"), (f"{P2},Q-1,{N},0", "重复"),
    (f"{P1},4e6fb7f6-8fed-519a-be2b-0a444183e371,{N},1", "形似 UUID"), (f"{P1},4e6fb7f68fed519abe2b0a444183e371,{N},1", "形似 UUID")])
def test_attempts_refuses_bad_rows(row, why):
    msg = _refused_a(_a(f"{P2},Q-1,{N},1", row))
    assert why in msg and "第 4 行" in msg and row.split(",")[0] not in msg


@pytest.mark.parametrize("head,meta,why", [
    (AHEAD + ",student_name", AMETA, "疑似个人信息"), (AHEAD + ",学校", AMETA, "疑似个人信息"), (AHEAD + ",attempts", AMETA, "不认识"),
    ("pseudonym,item_id,node_id", AMETA, "必须有"), (AHEAD, AMETA.replace("synthetic=true", "synthetic=false"), "只接受合成样例"),
    (AHEAD, AMETA.replace("subject=aya", "subject=wenli"), "不一致"), (AHEAD, AMETA.replace(ATTEMPTS_FORMAT, ERRORS_FORMAT), "format"),
    (AHEAD, AMETA + " school=x", "不认识"), (AHEAD, AHEAD, "元数据行")])
def test_attempts_refuses_layout(head, meta, why):
    extra = ",x" if head.count(",") > 3 else ""
    assert why in _refused_a(_a(f"{P1},Q-1,{N},1{extra}", head=head, meta=meta))
    assert "没有数据行" in _refused_a(_a())


def test_attempts_cognitive_level_and_alignment():
    head = AHEAD + ",cognitive_level"
    a = parse_attempts(_a(f"{P1},Q-1,{N},1,apply", f"{P2},Q-1,{N},0,apply", f"{P1},Q-2,{N},1,", head=head), MP)
    assert a.levels == {"Q-1": "apply"} and a.by_node[N]["Q-2"] == {P1: True}
    assert "认知层次不合法" in _refused_a(_a(f"{P1},Q-1,{N},1,remember", head=head))
    assert "前后不一致" in _refused_a(_a(f"{P1},Q-1,{N},1,apply", f"{P2},Q-1,{N},1,synthesize", head=head))
    e = parse_errors(_e(f"syn-zzzzzzzzzz,{N},{K},strategy_and_time,0.9"), MP, TAX, 0.6)
    with pytest.raises(InputRefused, match="完全不相交"):
        Step2(TAX, a, e)                                   # 两次不同导出：伪名对不上
    Step2(TAX, a, parse_errors(_e(f"{P1},{N},{K},strategy_and_time,0.9"), MP, TAX, 0.6))
    Step2(TAX, a, None)


def test_errors_parse_confidence_split():
    e = parse_errors(_e(f"{P1},{N},{K},strategy_and_time,0.9", f"{P1},{N},{Y_},computation_or_grammar,0.3",
                        f"{P2},{N},{Y_},computation_or_grammar,0.59", f"{P2},{N},{G},knowledge,0.2",
                        f"{P1},{N},{K},strategy_and_time,0.6", f"syn-cccccccccc,{N},{G},knowledge,0.6",
                        f"{P2},{N},fenlei_yilou,thinking_method,0.9", f"{P2},{N},xinli_xiguan,strategy_and_time,0.95",
                        f"syn-dddddddddd,{N},xinli_xiguan,strategy_and_time,0.9",
                        f"syn-eeeeeeeeee,{Y.N_SPLIT},fenlei_yilou,thinking_method,0.9"), MP, TAX, 0.6)
    # 待定（fenlei_yilou）与敏感（xinli_xiguan）编码整行丢弃：P2 仍只有低置信行（难归类），只有被丢弃行的学生不进任何计数
    assert e.episodes == {N: {P1: frozenset({K}), P2: frozenset(), "syn-cccccccccc": frozenset({G})}} and e.students == 3
    assert "syn-dddddddddd" not in e.pseudonyms and Y.N_SPLIT not in e.episodes
    e2 = parse_errors(_e(f"{P2},{N},{Y_},computation_or_grammar,0.59"), MP, TAX, 0.5)
    assert e2.episodes == {N: {P2: frozenset({Y_})}}


@pytest.mark.parametrize("row,why", [
    (f"{P1},{N},careless,knowledge,0.9", "不在错因分类表"), (f"{P1},{N},prompt_type_confuse,reading_the_question,0.9", "不在错因分类表"),
    (f"{P1},{N},{K},knowledge,0.9", "映射不一致"), (f"{P1},{N},{K},strategy_and_time,1.2", "置信度"),
    (f"{P1},{N},{K},strategy_and_time,nan", "置信度"), (f"{P1},{N},{K},strategy_and_time,", "置信度"),
    (f"pil-abcdefghij,{N},{K},strategy_and_time,0.9", "syn- 开头"), (f"{P1},MATH.CALIB.T9.N9,{K},strategy_and_time,0.9", "可计算"),
    (f"{P1},{N},{K},strategy_and_time,a@b.cn", "个人信息")])
def test_errors_refuses_bad_rows(row, why):
    msg = _refused_e(_e(f"{P2},{N},{Y_},computation_or_grammar,0.9", row))
    assert why in msg and "第 4 行" in msg


def test_errors_refuses_layout():
    assert "疑似个人信息" in _refused_e(_e(f"{P1},{N},{K},strategy_and_time,0.9,x", head=EHEAD + ",phone"))
    assert "只接受合成样例" in _refused_e(_e(f"{P1},{N},{K},strategy_and_time,0.9", meta=EMETA.replace("synthetic=true", "synthetic=false")))
    assert "必须有" in _refused_e(_e(f"{P1},{N},{K},0.9", head="pseudonym,node_id,subject_category,confidence"))
    with pytest.raises(InputRefused, match="没有该学科"):
        parse_errors(_e(f"{P1},{N},{K},strategy_and_time,0.9"), MP, dataclasses.replace(TAX, codes={"wenli": TAX.codes["wenli"]}), 0.6)


def _pc(**over: Any) -> dict[str, Any]:
    return {**Y.pitfall_codes(), **over}


@pytest.mark.parametrize("doc", [
    _pc(pitfalls={Y.N_PLAIN: [K]}), _pc(pitfalls={"MATH.CALIB.T5.P1": ["careless"]}), _pc(pitfalls={"MATH.CALIB.T5.P1": [K, K]}),
    _pc(pitfalls={"MATH.CALIB.T5.P1": K}), _pc(pitfalls=[]), _pc(subject="wenli"), _pc(map_version="0.2.0"), _pc(format="x/1"),
    {**Y.pitfall_codes(), "student_ids": []}, {"pitfalls": {}},
    _pc(pitfalls={f"{Y.N_SPLIT}.SLIP1": ["tuili_cuowu", "zhishi_queshi"]})])   # 一个易错点的编码跨两个大类
def test_pitfall_codes_refused(doc):
    with pytest.raises(InputRefused):
        parse_pitfall_codes(doc, MP, TAX)


def test_pitfall_codes_parse():
    assert parse_pitfall_codes(Y.pitfall_codes(), MP, TAX) == {
        "MATH.CALIB.T5.P1": {"biaoda_cuowu"}, "MATH.CALIB.T5.P3": {"shenti_cuowu"}, f"{Y.N_SPLIT}.SLIP1": {"tuili_cuowu"},
        f"{Y.N_SPLIT}.SLIP2": {"zhishi_queshi"}}
    same = _pc(pitfalls={"MATH.CALIB.T5.P1": ["fangfa_cuowu", "tuili_cuowu"]})                   # 同一大类（思维方法）可以
    assert parse_pitfall_codes(same, MP, TAX) == {"MATH.CALIB.T5.P1": {"fangfa_cuowu", "tuili_cuowu"}}


# ---------- 易错候选 ----------
def _pitfalls(students, mp=None, codes=None, cfg=CFG):
    return PF.scan_pitfalls(mp or map2([A, B, C2]), errs(students), TAX, codes, cfg, noisy=False)


BASIC = rep(60, {A: {K}}) + rep(40, {A: {Y_}}) + rep(30, {B: {K}}) + rep(170, {B: {Y_}})


def test_pitfall_new_candidate_and_numbers():
    recs, insufficient = _pitfalls(BASIC)
    t = rec_of(recs, A, K)
    assert t["conclusion"] == PF.NEW_PITFALL and t["cross_subject_category"] == "strategy_and_time" and t["topic"] == TA
    assert t["node_group"] == {"n_approx": 100, "share": 0.6, "share_ci": [0.5, 0.69]}
    assert t["baseline"]["n_approx"] == 200 and t["baseline"]["share"] == 0.15 and t["diff"] == 0.45 and t["p_adj_band"] == "<0.001"
    assert t["covering_pitfalls"] == [] and t["unmapped_pitfalls"] == []
    assert rec_of(recs, A, Y_)["conclusion"] == C.BELOW and rec_of(recs, B, K)["conclusion"] == C.BELOW
    assert {(r["node"], r["code"]) for r in recs} == {(A, K), (A, Y_), (B, K), (B, Y_)}
    assert insufficient == 3 * len(TAX.subject_codes("aya")) - 4              # 其余节点 × 编码只计数；只检验“明确”的编码
    assert [(r["node"], r["code"]) for r in recs] == sorted((r["node"], r["code"]) for r in recs)


def test_pitfall_coverage_from_links_and_codes():
    linked = map2([A, B, C2], ((PIT, "node", TA, A),))
    assert rec_of(_pitfalls(BASIC, linked, {PIT: frozenset({K})})[0], A, K)["conclusion"] == PF.COVERED
    covered = rec_of(_pitfalls(BASIC, linked, {PIT: frozenset({K})})[0], A, K)
    assert covered["covering_pitfalls"] == [PIT] and covered["unmapped_pitfalls"] == []
    other = rec_of(_pitfalls(BASIC, linked, {PIT: frozenset({G})})[0], A, K)     # 关联的易错点覆盖别的编码 → 仍是候选
    assert other["conclusion"] == PF.NEW_PITFALL and other["covering_pitfalls"] == []
    for codes in ({}, None):                                                    # 关联了但对照里没有 → 对照缺失
        miss = rec_of(_pitfalls(BASIC, linked, codes)[0], A, K)
        assert miss["conclusion"] == PF.MAPPING_MISSING and miss["unmapped_pitfalls"] == [PIT]
    detail = map2([A, B, C2], ((f"{A}.SLIP", "detail", A, None),))              # 细目级易错点经 parent_id 关联
    assert rec_of(_pitfalls(BASIC, detail, {f"{A}.SLIP": frozenset({K})})[0], A, K)["conclusion"] == PF.COVERED
    unlinked = map2([A, B, C2], ((PIT, "node", TA, B),))                        # 关联在别的节点上
    assert rec_of(_pitfalls(BASIC, unlinked, {PIT: frozenset({K})})[0], A, K)["conclusion"] == PF.NEW_PITFALL


def test_pitfall_thresholds():
    lift = rep(400, {A: {K}}) + rep(600, {A: {Y_}}) + rep(600, {B: {K}}) + rep(1400, {B: {Y_}})   # 0.4 对 0.3：显著但差值 < 0.15
    t = rec_of(_pitfalls(lift)[0], A, K)
    assert t["diff"] == 0.1 and t["diff_ci"][0] > 0 and t["p_adj_band"] == "<0.001" and t["conclusion"] == C.BELOW
    loose = dataclasses.replace(CFG, pitfall=dataclasses.replace(CFG.pitfall, min_lift=0.05))
    assert rec_of(_pitfalls(lift, cfg=loose)[0], A, K)["conclusion"] == PF.NEW_PITFALL
    share = rep(36, {A: {K}}) + rep(164, {A: {Y_}}) + rep(20, {B: {K}}) + rep(980, {B: {Y_}})    # 0.18 对 0.02：差值够但占比 < 0.2
    t = rec_of(_pitfalls(share)[0], A, K)
    assert t["node_group"]["share"] == 0.18 and t["diff"] == 0.16 and t["conclusion"] == C.BELOW
    low_share = dataclasses.replace(CFG, pitfall=dataclasses.replace(CFG.pitfall, min_share=0.1))
    assert rec_of(_pitfalls(share, cfg=low_share)[0], A, K)["conclusion"] == PF.NEW_PITFALL
    weak = rep(20, {A: {K}}) + rep(45, {A: {Y_}}) + rep(20, {B: {K}}) + rep(114, {B: {Y_}})       # 0.31 对 0.15：单独检验显著
    nulls = [f"MATH.X.B.M{i}" for i in range(14)]                                                  # 另一主题里 28 个无差异的检验
    many = weak + [s for m in nulls for s in rep(20, {m: {K}}) + rep(114, {m: {Y_}})]
    alone = rec_of(_pitfalls(weak)[0], A, K)
    crowded = rec_of(_pitfalls(many, map2([A, B, C2] + nulls))[0], A, K)
    assert alone["conclusion"] == PF.NEW_PITFALL and crowded["conclusion"] == C.BELOW            # 全部节点 × 编码一族做 BH
    assert crowded["node_group"] == alone["node_group"] and crowded["baseline"] == alone["baseline"]


def test_pitfall_any_small_cell_is_withheld():
    for a, b, c, d in ((19, 40, 30, 170), (60, 19, 30, 170), (60, 40, 19, 170), (60, 40, 30, 19)):
        st = rep(a, {A: {K}}) + rep(b, {A: {Y_}}) + rep(c, {B: {K}}) + rep(d, {B: {Y_}})
        assert rec_of(_pitfalls(st)[0], A, K) is None, (a, b, c, d)
    st = rep(20, {A: {K}}) + rep(20, {A: {Y_}}) + rep(20, {B: {K}}) + rep(20, {B: {Y_}})
    assert rec_of(_pitfalls(st)[0], A, K) is not None


def test_pitfall_baseline_one_record_per_student():
    """基线：本节点有高置信记录的学生不进基线；其他节点有多条记录的学生只取一条（伪名哈希），每人只落一格；只有低置信的不进四格"""
    st = rep(100, {A: {K}, B: {Y_}}) + rep(50, {B: {Y_}, C2: {K}}) + rep(30, {A: set(), B: {G}}) + rep(10, {A: set()})
    mp = map2([A, B, C2])
    e = errs(st)
    tables = {(t["node"], t["code"]): t for t in PF.pitfall_tables(mp, e, TAX.subject_codes("aya"))}
    a, b, c, d = tables[(A, K)]["cells"]
    assert (a, b) == (100, 0) and c + d == 80                                    # 50 名（B、C2 各有记录，只算一次）+ 30 名（A 上只有低置信）
    picks = [PF.baseline_pick(_pid(i), [B, C2]) for i in range(100, 150)]
    assert c == sum(1 for p in picks if p == C2) and 0 < c < 50                 # 取到 C2 的才有 K
    a, b, c, d = tables[(B, Y_)]["cells"]
    assert (a, b) == (150, 30) and c + d == 0                                    # A 上有记录的学生在 B 上也有：不进 B 的基线
    assert tables[(A, K)]["key"] == C.set_key([_pid(i) for i in range(180)])
    assert PF.baseline_pick(P1, [B, C2]) == PF.baseline_pick(P1, [C2, B])


# ---------- 难归类 ----------
def test_unclassifiable_signal_and_thresholds():
    st = rep(60, {A: set()}) + rep(40, {A: {K}}) + rep(25, {B: set()}) + rep(75, {B: {Y_}}) + rep(19, {C2: set()}) + rep(80, {C2: {K}})
    recs = {r["node"]: r for r in PF.scan_unclassifiable(map2([A, B, C2]), errs(st), CFG, noisy=False)}
    assert recs[A]["conclusion"] == PF.UNCLASSIFIABLE and recs[A]["low_conf_rate"] == 0.6 and recs[A]["n_approx"] == 100
    assert recs[B]["conclusion"] == C.BELOW and recs[B]["low_conf_rate"] == 0.25
    assert recs[C2]["conclusion"] == C.INSUFFICIENT and not recs[C2]["disclosed"] and "low_conf_rate" not in recs[C2]
    assert list(recs) == [A, B, C2]


def test_unclassifiable_family_adjusted():
    """Wilson 下界按族校正：单独一个节点时 40/100 的下界 > 0.3，可给出的节点多了以后不再算信号"""
    assert S.wilson(40, 100, Z95)[0] > 0.3
    alone = PF.scan_unclassifiable(map2([A]), errs(rep(40, {A: set()}) + rep(60, {A: {K}})), CFG, noisy=False)[0]
    assert alone["conclusion"] == PF.UNCLASSIFIABLE
    st = rep(40, {A: set()}) + rep(60, {A: {K}}) + rep(20, {B: set()}) + rep(80, {B: {K}}) + rep(20, {C2: set()}) + rep(80, {C2: {K}})
    crowded = PF.scan_unclassifiable(map2([A, B, C2]), errs(st), CFG, noisy=False)[0]
    assert crowded["conclusion"] == C.BELOW and crowded["low_conf_rate_ci"] == alone["low_conf_rate_ci"]


# ---------- 节点内部分化 ----------
def test_best_split_and_bimodality():
    k, r2 = SP.best_split([0.1, 0.12, 0.14, 0.8, 0.82, 0.84], 3)
    assert k == 3 and r2 > 0.99
    k, r2 = SP.best_split([1.0, 2.0, 3.0, 4.0, 5.0, 6.0], 3)
    assert k == 3 and r2 == pytest.approx(13.5 / 17.5)
    assert SP.best_split([0.5] * 6, 3) is None and SP.best_split([0.1, 0.2, 0.3, 0.4, 0.5], 3) is None
    assert SP.best_split([0.0, 0.9, 0.92, 0.94, 0.96, 0.98], 3)[0] == 3       # 每群至少 3 题（不取 1 对 5）
    assert SP.best_split([0.0, 0.9, 0.92, 0.94, 0.96, 0.98], 1)[0] == 1
    assert SP.best_split([0.1, 0.1, 0.5, 0.9, 0.9, 0.9, 0.9], 2) == SP.best_split([0.1, 0.1, 0.5, 0.9, 0.9, 0.9, 0.9], 2)
    assert SP.bimodality_p(0.99, 6, 3, 2000) < 0.01
    assert SP.bimodality_p(13.5 / 17.5, 6, 3, 2000) > 0.2
    assert SP.null_r2(8, 3, 500) == SP.null_r2(8, 3, 500) and len(SP.null_r2(8, 3, 500)) == 500
    assert SP.bimodality_p(1.0, 8, 4, 500) == pytest.approx(1 / 501)


SPLIT4 = {(True, True): 100, (True, False): 100, (False, True): 100, (False, False): 100}
TIERS4 = {(True, True): 150, (True, False): 30, (False, True): 70, (False, False): 150}


ML = [f"Q-L{j}" for j in range(4)]
MH = [f"Q-H{j}" for j in range(4)]


def attempts_model(node: str, n: int, kind: str, seed: int = 7, offset: int = 0, per_group: int = 4) -> Attempts:
    """固定种子的作答模型（每群 per_group 题）：split = 两种独立能力（高群能力掌握率 0.7、低群 0.35，掌握 0.85 / 未掌握 0.15）；
    tiers = 同一连续能力的难易两档，难度 ±1.1，猜对 0.3、失误 0.15；flat = 同一能力、同一难度"""
    rng = random.Random(seed)
    lo_items = [f"Q-L{j}" for j in range(per_group)]
    hi_items = [f"Q-H{j}" for j in range(per_group)]
    by_item: dict[str, dict[str, bool]] = {it: {} for it in lo_items + hi_items}
    for i in range(n):
        pid = _pid(i + offset)
        theta, s_hi, s_lo = rng.gauss(0.0, 1.0), rng.random() < 0.7, rng.random() < 0.35
        for items, hard in ((hi_items, False), (lo_items, True)):
            for it in items:
                if kind == "split":
                    p = 0.85 if (s_lo if hard else s_hi) else 0.15
                elif kind == "tiers":
                    p = 0.3 + 0.55 / (1 + math.exp(-1.6 * (theta - (1.1 if hard else -1.1))))
                else:
                    p = 1 / (1 + math.exp(-theta))
                by_item[it][pid] = rng.random() < p
    return Attempts(True, {node: by_item}, frozenset(_pid(i + offset) for i in range(n)))


def model_rec(kind: str, n: int = 1000, cfg=CFG, seed: int = 7, per_group: int = 4) -> dict[str, Any]:
    return SP.scan_splits(map2([A]), attempts_model(A, n, kind, seed, per_group=per_group), None, None, cfg, noisy=False)[0]


def test_loevinger_h():
    assert SP.loevinger_h(C.Table2(100, 0, 100, 100)) == 1.0          # 完全嵌套：没有“难的过、易的不过”
    assert SP.loevinger_h(C.Table2(100, 100, 100, 100)) == 0.0        # 独立
    assert SP.loevinger_h(C.Table2(100, 100, 0, 100)) == 1.0          # 方向由过关率决定（后者更难）
    assert SP.loevinger_h(C.Table2(150, 30, 70, 150)) == pytest.approx(1 - 30 / (180 * 180 / 400))
    assert SP.loevinger_h(C.Table2(0, 0, 0, 0)) is None and SP.loevinger_h(C.Table2(50, 0, 0, 0)) is None


def test_split_candidate():
    r = model_rec("split")
    assert r["conclusion"] == SP.SPLIT and r["tested"] and r["items_withheld"] == 0
    assert r["low_group"] == ML and r["high_group"] == MH and r["gap"] >= 0.2 and r["p_adj_band"] == "<0.001"
    cg = r["cross_group"]
    assert cg["disclosed"] and cg["n_approx"] == 1000 and abs(cg["loevinger_h"]) < 0.2 and min(cg["within_h"]) > 0.4
    assert cg["h_ratio"] <= CFG.split.max_h_ratio
    assert [it["item_id"] for it in r["items"]] == sorted(ML + MH) and all("rate" in it for it in r["items"])
    assert r["error_evidence"] == {"conclusion": SP.NO_ERRORS}


def test_within_reference_is_perturbed():
    """群内参照（两半）的四格同样按表键与表标签扰动，H 由扰动后的四格算出"""
    att = attempts_model(A, 1000, "split")
    r = SP.scan_splits(map2([A]), att, None, None, CFG, noisy=True)[0]
    for items, tag, got in ((r["low_group"], "within-low", r["cross_group"]["within_h"][0]),
                            (r["high_group"], "within-high", r["cross_group"]["within_h"][1])):
        t, _, _ = SP.cross_table(SP._passes(att, A, items[0::2], 0.5), SP._passes(att, A, items[1::2], 0.5), A, True, tag)
        raw, _, _ = SP.cross_table(SP._passes(att, A, items[0::2], 0.5), SP._passes(att, A, items[1::2], 0.5), A, False, tag)
        assert got == C.r2(SP.loevinger_h(t)) and t.cells() != raw.cells()
        # 记录里的 H 只保留 2 位，±2 的扰动可能被舍入掩盖：直接比未舍入的值
        exact = SP.within_h(att, A, items, 0.5, tag, CFG.min_cell, True)
        assert exact == SP.loevinger_h(t) and exact != SP.loevinger_h(raw)


def test_tiers_with_guessing_not_split():
    """契约 B1 反例：同一能力的难易两档、有猜对与失误——phi 不大（按 phi 判会误报成拆分），但两群之间的 H 与群内两半相当 → 难度分档"""
    for seed in (7, 8, 9):
        r = model_rec("tiers", n=1200, seed=seed, per_group=6)
        cg = r["cross_group"]
        assert r["gap"] >= 0.2 and r["p_adj_band"] == "<0.001" and cg["disclosed"]
        assert cg["phi"] < 0.25 and cg["h_ratio"] > 1.0 and r["conclusion"] == SP.TIERS, seed
    loose = dataclasses.replace(CFG, split=dataclasses.replace(CFG.split, max_h_ratio=10.0))
    assert model_rec("tiers", n=1200, per_group=6, cfg=loose)["conclusion"] == SP.SPLIT
    strict = dataclasses.replace(CFG, split=dataclasses.replace(CFG.split, max_h_ratio=0.0))
    assert model_rec("split", cfg=strict)["conclusion"] in (SP.SPLIT, SP.TIERS)


def test_split_undecided_below_insufficient():
    und = model_rec("split", n=60)                                             # 人少：学生层面四格不足
    assert und["conclusion"] == C.UNDECIDED and not und["cross_group"]["disclosed"] and "h_ratio" not in und["cross_group"]
    ref = model_rec("tiers", n=1000, seed=7)                                   # 两群之间可给出，但易群两半的四格不足 → 待定
    assert ref["cross_group"]["disclosed"] and ref["cross_group"]["within_h"][1] is None and ref["conclusion"] == C.UNDECIDED
    flat = model_rec("flat")                                                   # 两群正确率相同
    assert flat["conclusion"] == C.BELOW and flat["gap"] < 0.2
    small_gap = dataclasses.replace(CFG, split=dataclasses.replace(CFG.split, min_gap=0.6))
    assert model_rec("split", cfg=small_gap)["conclusion"] == C.BELOW
    att = attempts_model(A, 1000, "split")
    five = Attempts(True, {A: {k: v for k, v in att.by_node[A].items() if k not in ML[:3]}}, att.pseudonyms)
    few = SP.scan_splits(map2([A]), five, None, None, CFG, noisy=False)[0]     # 只有 5 道题
    assert few["conclusion"] == C.INSUFFICIENT and not few["tested"] and "low_group" not in few


def test_split_item_cap():
    """每节点参与两群检验的题数有上限：按作答人数多到少、题目 ID 取前若干道，其余只列出、计数"""
    att = attempts_model(A, 1000, "split")
    extra = {f"Q-X{j}": {p: True for p in sorted(att.pseudonyms)[:500]} for j in range(3)}   # 作答人数少的 3 道
    many = Attempts(True, {A: {**att.by_node[A], **extra}}, att.pseudonyms)
    cfg = dataclasses.replace(CFG, split=dataclasses.replace(CFG.split, max_items_per_node=8))
    r = SP.scan_splits(map2([A]), many, None, None, cfg, noisy=False)[0]
    assert r["items_not_tested"] == 3 and len(r["items"]) == 11 and r["low_group"] == ML and r["conclusion"] == SP.SPLIT
    r_all = SP.scan_splits(map2([A]), many, None, None, CFG, noisy=False)[0]
    assert "items_not_tested" not in r_all and set(r_all["low_group"]) | set(r_all["high_group"]) >= set(extra)


def test_split_bh_family():
    """两群检验在可检验节点间做 BH：自助法 p 值的下限是 1/(B+1)，节点多时校正后不再显著"""
    cfg = dataclasses.replace(CFG, split=dataclasses.replace(CFG.split, bootstrap_draws=200, fdr_q=0.01))
    assert model_rec("split", cfg=cfg)["conclusion"] == SP.SPLIT                  # 1/201 < 0.01
    att = attempts_model(A, 1000, "split")
    by_node = dict(att.by_node)
    for n in (B, C2):                                                            # 正确率连续分布的节点（p 值大）
        by_node[n] = {f"Q-{j}": {_pid(i): i < 120 + 20 * j for i in range(400)} for j in range(6)}
    recs = {r["node"]: r for r in SP.scan_splits(map2([A, B, C2]), Attempts(True, by_node, att.pseudonyms), None, None, cfg, noisy=False)}
    assert recs[A]["conclusion"] == C.BELOW and recs[A]["p_adj_band"] == "<0.05"
    assert recs[B]["conclusion"] == C.BELOW and recs[B]["p_adj_band"] == "≥0.05"


def test_group_pass_boundary():
    """一群题目答对比例恰好等于 pass_rate 算过关；只算作答过的题"""
    att = Attempts(True, {A: {"Q1": {P1: True, P2: False}, "Q2": {P1: False, P2: False}, "Q3": {P2: True}}}, frozenset({P1, P2}))
    assert SP._passes(att, A, ["Q1", "Q2"], 0.5) == {P1: True, P2: False}
    assert SP._passes(att, A, ["Q1", "Q2", "Q3"], 0.5) == {P1: True, P2: False}
    assert SP._passes(att, A, ["Q3"], 0.5) == {P2: True}


def test_item_rate_threshold_and_bands():
    """不加扰动核对口径：作答 ≥ 20 才参与；两格都 ≥ min_cell 才给 2 位小数，否则只给分档；全对、全错只给端点分档"""
    att = Attempts(True, {A: {"Q-19": {_pid(i): i % 2 == 0 for i in range(19)}, "Q-20": {_pid(i): i % 2 == 0 for i in range(20)},
                              "Q-40": {_pid(i): i % 2 == 0 for i in range(40)}, "Q-ALL": {_pid(i): True for i in range(30)},
                              "Q-NONE": {_pid(i): False for i in range(30)}, "Q-MOST": {_pid(i): i < 35 for i in range(40)}}},
                   frozenset(_pid(i) for i in range(40)), {"Q-40": "apply"})
    shown, withheld = SP.item_rates(att, A, CFG, noisy=False)
    assert [s["item_id"] for s in shown] == ["Q-20", "Q-40", "Q-ALL", "Q-MOST", "Q-NONE"] and withheld == 1
    rec = SP.scan_splits(map2([A]), att, None, None, CFG, noisy=False)[0]
    assert rec["items"] == [{"item_id": "Q-20", "n_approx": 20, "rate_band": "0.10–0.90"},
                            {"item_id": "Q-40", "cognitive_level": "apply", "n_approx": 40, "rate": 0.5, "rate_ci": [0.35, 0.65]},
                            {"item_id": "Q-ALL", "n_approx": 30, "rate_band": "≥0.90"},
                            {"item_id": "Q-MOST", "n_approx": 40, "rate_band": "0.10–0.90"},
                            {"item_id": "Q-NONE", "n_approx": 30, "rate_band": "≤0.10"}]
    assert rec["items_withheld"] == 1
    assert [SP.rate_band(x) for x in (0.0, 0.1, 0.11, 0.89, 0.9, 1.0)] == ["≤0.10", "≤0.10", "0.10–0.90", "0.10–0.90", "≥0.90", "≥0.90"]


def test_item_rate_noisy_margins():
    """安全 B1：两格都扰动，作答人数的误差至多 2 × NOISE_MAX——扰动后 ≥ min_item_attempts + 4 才参与；
    真实 18、19 人永远不出现，出现的题真实都 ≥ 20；安全 B2：两格扰动后都 ≥ min_cell + 2 才给精确比率"""
    seen = {18: 0, 19: 0, 20: 0, 21: 0}
    hit = False
    for n in seen:
        for off in range(0, 40000, 97):
            resp = {_pid(i + off): i % 2 == 0 for i in range(n)}
            att = Attempts(True, {A: {"Q": resp}}, frozenset())
            cells = C.perturb_cells((sum(resp.values()), n - sum(resp.values())), C.set_key(resp), f"item/{A}/Q/")
            shown, _ = SP.item_rates(att, A, CFG, noisy=True)
            assert bool(shown) == (sum(cells) >= CFG.split.min_item_attempts + 2 * C.NOISE_MAX)
            seen[n] += bool(shown)
            hit |= sum(cells) >= CFG.split.min_item_attempts + C.NOISE_MAX and not shown
    assert seen[18] == 0 and seen[19] == 0 and hit
    for off in range(0, 6000, 61):                                             # 精确比率：两格都要够
        resp = {_pid(i + off): i < 23 for i in range(46)}
        cells = C.perturb_cells((23, 23), C.set_key(resp), f"item/{A}/Q/")
        shown, _ = SP.item_rates(Attempts(True, {A: {"Q": resp}}, frozenset()), A, CFG, noisy=True)
        assert shown[0]["exact"] == (min(cells) >= CFG.min_cell + C.NOISE_MAX)


def test_coarse_group_numbers_when_any_item_banded():
    """参与检验的题目里有只给分档的：两群均值、差值、R² 只给 1 位小数（不能由均值反推单题正确率）"""
    att = attempts_model(A, 1000, "split")
    by_item = dict(att.by_node[A])
    by_item["Q-L0"] = {p: False for p in by_item["Q-L0"]}                       # 一道全错的题：只给分档
    r = SP.scan_splits(map2([A]), Attempts(True, {A: by_item}, att.pseudonyms), None, None, CFG, noisy=False)[0]
    assert {"item_id": "Q-L0", "n_approx": 1000, "rate_band": "≤0.10"} in r["items"]
    for k in ("low_mean", "high_mean", "gap", "r2"):
        assert round(r[k], 1) == r[k], k
    exact = model_rec("split")
    assert any(round(exact[k], 1) != exact[k] for k in ("low_mean", "high_mean", "gap"))


def test_split_error_evidence():
    _, who = split_rec(SPLIT4)
    fail_low, fail_high = who[(False, True)], who[(True, False)]
    def make(r1: tuple[int, int], r2: tuple[int, int]) -> Errors:
        ep: dict[str, frozenset[str]] = {}
        for pids, (nz, _ny) in ((fail_low, r1), (fail_high, r2)):
            for i, p in enumerate(pids):
                ep[p] = frozenset({"zhishi_queshi"} if i < nz else {Y_})
        return Errors(True, {A: ep}, frozenset(ep))
    ev = split_rec(SPLIT4, errors=make((70, 30), (30, 70)))[0]["error_evidence"]
    assert ev["conclusion"] == SP.SUPPORT and ev["code"] in ("zhishi_queshi", Y_) and abs(ev["phi"]) == 0.4 and ev["codes_tested"] == 2
    assert ev["n_approx"] == 200 and ev["p_adj_band"] == "<0.001"
    assert split_rec(SPLIT4, errors=make((50, 50), (50, 50)))[0]["error_evidence"]["conclusion"] == SP.NO_SUPPORT
    assert split_rec(SPLIT4, errors=make((90, 10), (85, 15)))[0]["error_evidence"]["conclusion"] == C.INSUFFICIENT
    assert split_rec(SPLIT4, errors=Errors(True, {}, frozenset()))[0]["error_evidence"]["conclusion"] == C.INSUFFICIENT
    strict = dataclasses.replace(CFG, split=dataclasses.replace(CFG.split, error_min_phi=0.5))
    assert split_rec(SPLIT4, errors=make((70, 30), (30, 70)), cfg=strict)[0]["error_evidence"]["conclusion"] == SP.NO_SUPPORT
    loose = dataclasses.replace(CFG, split=dataclasses.replace(CFG.split, error_min_phi=0.1))
    ev = split_rec(SPLIT4, errors=make((60, 40), (45, 55)), cfg=loose)[0]["error_evidence"]   # 单个 p ≈ 0.034，两个编码 Bonferroni 后 > 0.05
    assert ev["phi"] in (0.15, -0.15) and ev["codes_tested"] == 2 and ev["p_adj_band"] == "≥0.05" and ev["conclusion"] == SP.NO_SUPPORT


def test_split_error_evidence_picks_strongest_code_and_skips_low_only():
    """取 |phi| 最大的编码（不是排序第一个）；只有低置信错因的学生不进四格"""
    _, who = split_rec(SPLIT4)
    fail_low, fail_high = who[(False, True)], who[(True, False)]
    ep: dict[str, frozenset[str]] = {}
    for i, p in enumerate(fail_low):
        ep[p] = frozenset(({"zhishi_queshi"} if i < 70 else set()) | ({Y_} if i >= 50 else set()))
    for i, p in enumerate(fail_high):
        ep[p] = frozenset(({"zhishi_queshi"} if i < 30 else set()) | ({Y_} if i >= 60 else set()))   # 30–59 只有低置信
    ev = split_rec(SPLIT4, errors=Errors(True, {A: ep}, frozenset(ep)))[0]["error_evidence"]
    assert ev["code"] == "zhishi_queshi" and ev["phi"] == 0.27 and ev["n_approx"] == 170 and ev["codes_tested"] == 2


def test_split_error_evidence_is_perturbed():
    _, who = split_rec(SPLIT4)
    r1, r2 = who[(False, True)], who[(True, False)]
    ep = {p: frozenset({"zhishi_queshi", Y_} if i < 70 else {Y_}) for i, p in enumerate(r1)}
    ep |= {p: frozenset({"zhishi_queshi", Y_} if i < 30 else {Y_}) for i, p in enumerate(r2)}
    ev = SP.error_evidence(Errors(True, {A: ep}, frozenset(ep)), TAX, "aya", A, r1, r2, CFG, True)
    cells = C.perturb_cells((70, 30, 30, 70), C.set_key(r1 + r2), f"err/{A}/zhishi_queshi/")
    assert ev["codes_tested"] == 1 and ev["code"] == "zhishi_queshi"
    assert {k: ev[k] for k in ("n_approx", "u_rate", "v_rate", "phi")} == {k: C.pair_numbers(*cells, Z95)[k] for k in ("n_approx", "u_rate", "v_rate", "phi")}
    assert cells != (70, 30, 30, 70)


def test_pitfall_tables_share_row_noise():
    """建议 6：同一节点的各编码表共用行人数扰动——各编码表报出的行人数完全相同（多表平均还原不出真实行人数）；
    “有该编码”格按编码各自扰动；“无该编码”格 = 行人数 − “有”格，误差在 ±2 × NOISE_MAX 内"""
    st = (rep(60, {A: {K}}) + rep(50, {A: {Y_}}) + rep(40, {A: {G}}) + rep(60, {B: {K}}) + rep(70, {B: {Y_}}) + rep(50, {B: {G}}))
    mp = map2([A, B])
    e = errs(st)
    tables = [t for t in PF.pitfall_tables(mp, e, TAX.subject_codes("aya")) if t["node"] == A and t["code"] in (K, Y_, G)]
    pert = [PF.perturb_pitfall(t["cells"], t["key"], A, t["code"]) for t in tables]
    assert len({p[0] + p[1] for p in pert}) == 1 and len({p[2] + p[3] for p in pert}) == 1
    for t, p in zip(tables, pert):
        assert all(abs(x - y) <= C.NOISE_MAX for x, y in zip(p[::2], t["cells"][::2]))
        assert all(abs(x - y) <= 2 * C.NOISE_MAX for x, y in zip(p[1::2], t["cells"][1::2]))
    a_noise = {p[0] - t["cells"][0] for t, p in zip(tables, pert)}
    recs, _ = PF.scan_pitfalls(mp, e, TAX, None, CFG)
    assert len({r["node_group"]["n_approx"] for r in recs if r["node"] == A}) == 1
    many = [PF.perturb_pitfall(t["cells"], t["key"] + k, A, t["code"]) for t in tables for k in range(30)]
    assert len({p[0] - tables[0]["cells"][0] for p in many[:30]}) > 1 or len(a_noise) > 1     # 扰动确实在变


def test_step2_noisy_threshold_keeps_margin():
    """扰动时门槛加余量：易错候选（“无该编码”两格是两份扰动之差，余量加倍）、难归类、学生层面四格"""
    hit = {"pitfall": False, "low": False, "cross": False}
    for off in range(0, 6000, 131):
        st = rep(22, {A: {K}}) + rep(23, {A: {Y_}}) + rep(22, {B: {K}}) + rep(23, {B: {Y_}})
        e = errs(st, offset=off)
        mp = map2([A, B])
        t = next(x for x in PF.pitfall_tables(mp, e, TAX.subject_codes("aya")) if (x["node"], x["code"]) == (A, K))
        a, b, c, d = PF.perturb_pitfall(t["cells"], t["key"], A, K)
        recs, _ = PF.scan_pitfalls(mp, e, TAX, None, CFG)
        ok = min(a, c) >= CFG.min_cell + C.NOISE_MAX and min(b, d) >= CFG.min_cell + 2 * C.NOISE_MAX
        assert (rec_of(recs, A, K) is not None) == ok
        hit["pitfall"] |= min(a, c) >= CFG.min_cell + C.NOISE_MAX and CFG.min_cell + C.NOISE_MAX <= min(b, d) < CFG.min_cell + 2 * C.NOISE_MAX
        el = errs(rep(21, {A: set()}) + rep(21, {A: {K}}), offset=off)
        lm = min(C.perturb_cells((21, 21), C.set_key(el.episodes[A]), f"low/{A}/"))
        assert PF.scan_unclassifiable(map2([A]), el, CFG)[0]["disclosed"] == (lm >= CFG.min_cell + C.NOISE_MAX)
        hit["low"] |= CFG.min_cell <= lm < CFG.min_cell + C.NOISE_MAX
    for off in range(0, 40000, 1000):
        counts = {(True, True): 21, (True, False): 21, (False, True): 21, (False, False): 21}
        att, _ = attempts_split(A, counts, LOW, HIGH, offset=off)
        rec = SP.scan_splits(map2([A]), att, None, None, CFG)[0]
        if not rec.get("tested"):
            continue
        lp = SP._passes(att, A, rec["low_group"], CFG.split.pass_rate)
        hp = SP._passes(att, A, rec["high_group"], CFG.split.pass_rate)
        cm = min(SP.cross_table(lp, hp, A, True)[0].cells())
        assert rec["cross_group"]["disclosed"] == (cm >= CFG.min_cell + C.NOISE_MAX)
        hit["cross"] |= CFG.min_cell <= cm < CFG.min_cell + C.NOISE_MAX
    assert hit["pitfall"] and hit["low"] and hit["cross"]


# ---------- 合成队列：三类埋入结构的召回与误报 ----------
def test_step2_synthetic_recall(synthetic):
    ev = Y.evaluate_step2(synthetic["report"], json.loads(synthetic["inputs"]["truth.json"]))
    assert ev["pitfall_found"] == ev["pitfall_planted"] == 1 and ev["false_pitfall"] <= CFG.max_false_pitfall
    assert ev["unclassifiable_found"] == ev["unclassifiable_planted"] == 1 and ev["false_unclassifiable"] <= CFG.max_false_unclassifiable
    assert ev["split_found"] == ev["split_planted"] == 1 and ev["false_split"] <= CFG.max_false_split
    assert ev["covered_confirmed"] == 1 and ev["unmapped_confirmed"] == 1 and ev["tiers_as_split"] == 0
    assert ev["excluded_tested"] == 0 and ev["tiers_confirmed"] == 1      # 待定与敏感编码不进检验；难易两档反例判为分档
    sp = {r["node"]: r for r in synthetic["report"]["node_split"]["nodes"]}
    assert sorted(sp[Y.N_SPLIT]["low_group"]) == Y._items(Y.N_SPLIT)[4:] and sorted(sp[Y.N_SPLIT]["high_group"]) == Y._items(Y.N_SPLIT)[:4]
    assert sp[Y.N_SPLIT]["error_evidence"]["conclusion"] == SP.SUPPORT
    tiers = sp[Y.N_TIERS]["cross_group"]
    assert sp[Y.N_TIERS]["conclusion"] == SP.TIERS and tiers["phi"] < 0.25 and tiers["h_ratio"] > CFG.split.max_h_ratio
    assert {"levels": sorted({it.get("cognitive_level") for it in sp[Y.N_SPLIT]["items"]})} == {"levels": ["apply", "synthesize"]}
    rep = synthetic["report"]
    assert set(sp) == set(Y.T5) and set(rep["node_split"]["insufficient_nodes"]) == set(synthetic["map"].nodes) - set(Y.T5)
    lc = rep["unclassifiable"]
    assert {t["node"] for t in lc["nodes"]} == set(Y.T5) and set(lc["insufficient_nodes"]) == set(synthetic["map"].nodes) - set(Y.T5)
    assert rep["summary"]["node_split"][C.INSUFFICIENT] == len(synthetic["map"].nodes) - len(Y.T5)


def test_step2_recall_across_seeds(synthetic):
    mp, tax, codes = synthetic["map"], synthetic["taxonomy"], synthetic["step2"].pitfall_codes
    tot: dict[str, int] = {}
    seeds = range(1, 6)
    for seed in seeds:
        a, e = Y.step2_csvs(seed)
        s2 = Step2(tax, parse_attempts(a, mp), parse_errors(e, mp, tax, CFG.pitfall.min_confidence), codes)
        report = build_step2(mp, CFG, s2)
        for k, v in Y.evaluate_step2(report, Y.truth(seed)).items():
            tot[k] = tot.get(k, 0) + v
    n = len(seeds)
    assert tot["pitfall_found"] == tot["pitfall_planted"] == n and tot["false_pitfall"] <= CFG.max_false_pitfall * n
    assert tot["unclassifiable_found"] == n and tot["false_unclassifiable"] <= CFG.max_false_unclassifiable * n
    assert tot["split_found"] == n and tot["false_split"] <= CFG.max_false_split * n and tot["tiers_as_split"] == 0
    assert tot["covered_confirmed"] == tot["unmapped_confirmed"] == n and tot["excluded_tested"] == 0
    assert tot["tiers_confirmed"] >= n - 1                                   # 难易两档：判为分档（偶有学生层面四格不足 → 待定）


def test_step2_generator_deterministic_and_synthetic():
    a1, e1 = Y.step2_csvs()
    assert (a1, e1) == Y.step2_csvs() and Y.step2_csvs(seed=7) != (a1, e1)
    assert a1.startswith(f"# format={ATTEMPTS_FORMAT} synthetic=true ") and e1.startswith(f"# format={ERRORS_FORMAT} synthetic=true ")
    pids = {ln.split(",")[0] for ln in a1.splitlines()[2:]} | {ln.split(",")[0] for ln in e1.splitlines()[2:]}
    assert len(pids) == Y.SIZE2 and all(re.fullmatch(r"syn-[a-z]{10}", p) for p in pids)
    assert Y.mastery_csv() == Y.render_inputs()["mastery.csv"]                  # 第二步不改第一步的随机流
    lows = sum(1 for ln in e1.splitlines()[2:] if float(ln.split(",")[4]) < CFG.pitfall.min_confidence)
    assert lows > 500


# ---------- 防泄露：无精确计数、题目人数门槛、伪名不外泄、报表与工作单 ----------
INT_KEYS = ("n_approx", "items_withheld", "codes_tested", "items_not_tested")


def _step2_records(rep: dict[str, Any]):
    yield from rep["pitfall"]["tests"]
    yield from rep["unclassifiable"]["nodes"]
    yield from rep["node_split"]["nodes"]


def test_step2_no_exact_counts(synthetic):
    rep = json.loads(synthetic["files"]["calibration-report.json"])
    m = rep["method"]["min_cell"]
    seen = set()
    for r in _step2_records(rep):
        for path, x in _walk_numbers(r):
            seen.add(path.rsplit(".", 1)[-1])
            if path.endswith(".n_approx"):
                assert isinstance(x, int) and x % 10 == 0 and x >= m, path
            elif path.endswith(INT_KEYS[1:]):
                assert isinstance(x, int), path
            else:
                assert isinstance(x, float) and round(x, 2) == x, path
        if not r.get("disclosed", True):
            assert set(r) <= {"node", "topic", "conclusion", "reason", "disclosed"}
    assert {"n_approx", "share", "rate", "phi", "low_conf_rate", "loevinger_h", "within_h", "h_ratio"} <= seen
    assert seen <= {"n_approx", "items_withheld", "items_not_tested", "codes_tested", "share", "share_ci", "diff", "diff_ci",
                    "low_conf_rate", "low_conf_rate_ci", "rate", "rate_ci", "low_mean", "high_mean", "gap", "r2", "u_rate", "v_rate",
                    "phi", "log_odds_ratio", "log_odds_ratio_ci", "loevinger_h", "within_h", "h_ratio"}
    assert isinstance(rep["pitfall"]["insufficient_tests"], int)
    assert all(t["conclusion"] != C.BELOW for t in rep["pitfall"]["tests"]) and rep["pitfall"]["below_tests"]
    assert rep["pitfall"]["excluded_codes"] == ["biaozheng_cuowu", "fenlei_yilou", "jianyan_cuowu", "xinli_xiguan"]
    text = synthetic["files"]["calibration-report.json"]
    for code in ("fenlei_yilou", "xinli_xiguan", "jianyan_cuowu", "biaozheng_cuowu"):   # 只出现在“不参与的编码”清单里
        assert text.count(f'"{code}"') == 1 and f"· {code}" not in text


def test_item_rates_only_with_enough_attempts(synthetic):
    """题目级正确率只在作答人数 ≥ 20 时给出：报表里每道题的真实作答人数都 ≥ min_item_attempts"""
    att = synthetic["step2"].attempts
    shown = 0
    for r in synthetic["report"]["node_split"]["nodes"]:
        for it in r["items"]:
            assert len(att.by_node[r["node"]][it["item_id"]]) >= CFG.split.min_item_attempts
            shown += 1
    assert shown == sum(len(Y._items(n)) for n in Y.T5)
    small = Attempts(True, {Y.N_PLAIN: {"Q-SMALL": {_pid(i): True for i in range(15)}}}, frozenset(_pid(i) for i in range(15)))
    rec = next(r for r in SP.scan_splits(synthetic["map"], small, None, None, CFG) if r["node"] == Y.N_PLAIN)
    assert rec["items"] == [] and rec["items_withheld"] == 1 and rec["conclusion"] == C.INSUFFICIENT


def test_step2_sections_and_worklist(synthetic):
    rep, wl = synthetic["report"], synthetic["files"]["worklist.md"]
    assert rep["pitfall"]["provided"] and rep["pitfall"]["pitfall_codes_provided"]
    assert rep["pitfall"]["nodes_with_linked_pitfalls"][Y.N_SPLIT] == [f"{Y.N_SPLIT}.SLIP1", f"{Y.N_SPLIT}.SLIP2"]
    assert rep["summary"]["pitfall"][PF.NEW_PITFALL] == 1 and rep["summary"]["node_split"][SP.SPLIT] == 1
    assert rep["summary"]["pitfall"][C.BELOW] == len(rep["pitfall"]["below_tests"]) and rep["summary"]["node_split"][SP.TIERS] == 1
    assert rep["summary"]["unclassifiable"][PF.UNCLASSIFIABLE] == 1
    assert wl.count("- [ ] 确认新增易错点（文本由教研撰写）") == 1 and f"{Y.N_UNCOVERED}（合成节点 T5.N1） · 错因编码 `shijian_wenti`" in wl
    assert "H 比值 = " in wl and "`xinli_xiguan`" in wl
    assert wl.count("- [ ] 确认拆分（按下列两群）") == 1 and all(it in wl for it in Y._items(Y.N_SPLIT))
    assert re.search(r"- 高正确率一群（平均 0\.\d\d）：CALIB-T5N4-Q[1-4]、", wl)
    assert wl.count("- [ ] 已查看：需细化错因编码、判分 Rubric 或题目") == 1 and f"### 4.1 {Y.N_LOWCONF}" in wl
    assert f"{PF.COVERED}（" in wl and f"{Y.N_COVERED} · biaoda_cuowu" in wl and f"{Y.N_UNMAPPED} · fangfa_cuowu" in wl
    assert "未提供" not in wl
    assert not re.search(r"(?<![<≥\d])\d\.\d{3}", wl)


def test_step2_not_provided_and_partial():
    mp = map2([A, B])
    from test_calibrate import four, mastery_from
    m = mastery_from(four(A, B, 30, 30, 30, 30))
    rep = build_report(mp, m, CFG)
    assert rep["pitfall"] == {"provided": False, "note": "未提供错因输入"} and rep["node_split"]["provided"] is False
    assert rep["summary"]["node_split"] == "未提供作答输入" and "errors_students_approx" not in rep["cohort"]
    wl = render(rep, dict(mp.names))["worklist.md"]
    assert "（未提供错因输入）" in wl and "（未提供作答输入）" in wl
    att, _ = attempts_split(A, SPLIT4, LOW, HIGH)
    rep = build_report(mp, m, CFG, Step2(None, att, None, None))
    rec = next(r for r in rep["node_split"]["nodes"] if r["node"] == A)
    assert rec["error_evidence"] == {"conclusion": SP.NO_ERRORS} and rep["pitfall"]["provided"] is False
    assert rep["cohort"]["attempts_students_approx"] == 400
    for n, want in ((5, C.INSUFFICIENT), (19, C.INSUFFICIENT), (25, 20)):
        tiny = Attempts(True, {A: {"Q": {_pid(i): True for i in range(n)}}}, frozenset(_pid(i) for i in range(n)))
        assert build_report(mp, m, CFG, Step2(None, tiny))["cohort"]["attempts_students_approx"] == want


def test_leak_check_covers_step2_pseudonyms():
    """出口检查也查第二步输入里的伪名：题目 ID 恰好与某个伪名同形时拒绝写出"""
    from kge_calibrate.report import OutputRefused, run
    from test_calibrate import four, mastery_from
    mp = map2([A, B])
    m = mastery_from(four(A, B, 30, 30, 30, 30))
    other = "syn-zzzzzzzzzz"
    att = Attempts(True, {A: {other: {_pid(i): i % 2 == 0 for i in range(40)}}}, frozenset({other} | {_pid(i) for i in range(40)}))
    with pytest.raises(OutputRefused):
        run(mp, m, CFG, Step2(None, att))
    run(mp, m, CFG, Step2(None, dataclasses.replace(att, pseudonyms=frozenset(_pid(i) for i in range(40)))))


def test_cli_step2_refusals(tmp_path, capsys):
    assert main(["synth", "--out", str(tmp_path / "in")]) == 0
    base = ["run", "--map", str(tmp_path / "in/map-snapshot.json"), "--mastery", str(tmp_path / "in/mastery.csv"), "--taxonomy", str(TAX_PATH)]
    text = (tmp_path / "in/errors.csv").read_text(encoding="utf-8")
    (tmp_path / "in/errors.csv").write_text(text.replace("synthetic=true", "synthetic=false", 1), encoding="utf-8")
    assert main(base + ["--errors", str(tmp_path / "in/errors.csv"), "--out", str(tmp_path / "o1")]) == 2
    assert "拒绝输入" in capsys.readouterr().out and not (tmp_path / "o1").exists()
    (tmp_path / "in/codes.yaml").write_text("x: 1", encoding="utf-8")
    assert main(base + ["--pitfall-codes", str(tmp_path / "in/codes.yaml"), "--out", str(tmp_path / "o2")]) == 2
    assert main(base[:-1] + [str(tmp_path / "missing.md"), "--errors", str(tmp_path / "in/errors.csv"), "--out", str(tmp_path / "o3")]) == 2
    assert main(base + ["--attempts", str(tmp_path / "in/attempts.csv"), "--out", str(tmp_path / "o4")]) == 0   # 只给作答也能跑
    out = json.loads((tmp_path / "o4/calibration-report.json").read_text(encoding="utf-8"))
    assert out["node_split"]["provided"] and not out["pitfall"]["provided"]


def test_step2_sql_and_runbook_static():
    """导出说明只写文档、不执行：第二步函数草案只读、salt 库内生成、错因只取当前行；调用文件在只读事务里"""
    tool = Path(__file__).resolve().parents[1]
    fn = (tool / "sql/export-function.draft.sql").read_text(encoding="utf-8")
    call = (tool / "sql/export-responses.readonly.sql").read_text(encoding="utf-8")
    runbook = (tool / "RUNBOOK.md").read_text(encoding="utf-8")
    assert "CREATE FUNCTION kge.calibration_export_responses(" in fn and fn.count("gen_random_bytes(32)") == 2
    body = fn.split("CREATE FUNCTION kge.calibration_export_responses(", 1)[1]
    assert "x.superseded_by IS NULL" in body and "g0.superseded_by IS NULL" in body and "evidence_kind = 'independent'" in body
    assert "tag_review_status = 'approved'" in body and "erased_at IS NULL" in body and "er.status = 'pending'" in body
    for text in (fn, call):
        sql = re.sub(r"--[^\n]*", "", text)
        assert ":'salt'" not in sql and not re.search(r"\b(INSERT|UPDATE|DELETE|TRUNCATE|GRANT|ALTER|DROP)\b", sql)
    assert "READ ONLY" in call and "kge.calibration_export_responses" in call
    assert "## 6. 第二步" in runbook and "--pitfall-codes" in runbook and "superseded_by IS NULL" in runbook
    assert "SALT" not in runbook and "演练流程" not in runbook


# ---------- 差分测试：删一人跑两次，第二步的每类输出都还原不了这个人 ----------
DELTAS2 = {"前一格": (1, 0), "后一格": (0, 1)}


def _possible2(c0: set[tuple[int, ...]], c1: set[tuple[int, ...]]) -> set[str]:
    out = {OUT_OF_TABLE} if c0 & c1 else set()
    span = 2 * C.NOISE_MAX
    for name, d in DELTAS2.items():
        if any(all(abs(p0[i] - p1[i] - d[i]) <= span for i in range(2)) for p0 in c0 for p1 in c1):
            out.add(name)
    return out


PITFALL_SPANS = (2 * C.NOISE_MAX, 4 * C.NOISE_MAX, 2 * C.NOISE_MAX, 4 * C.NOISE_MAX)   # “无”两格是两份扰动之差


def _possible_pitfall(c0: set[tuple[int, ...]], c1: set[tuple[int, ...]]) -> set[str]:
    """易错四格：这个人落在哪一格（或不在表里）与两次输出相容的可能；“无该编码”两格的两次扰动差最多 4 × NOISE_MAX"""
    out = {OUT_OF_TABLE} if c0 & c1 else set()
    for name, d in CELL_DELTAS.items():
        if any(all(abs(p0[i] - p1[i] - d[i]) <= PITFALL_SPANS[i] for i in range(4)) for p0 in c0 for p1 in c1):
            out.add(name)
    return out


def _rate_group(g: dict[str, Any], key: str) -> list[tuple[int, int]]:
    """能产生 rate_numbers 这份输出的 (x, n)"""
    out = []
    want = {k: g[k] for k in ("n_approx", key, key + "_ci")}
    for n in range(max(1, g["n_approx"]), g["n_approx"] + 10):
        for x in range(max(0, int((g[key] - 0.006) * n)), min(n, int((g[key] + 0.006) * n) + 1) + 1):
            if C.rate_numbers(x, n, Z95, key) == want:
                out.append((x, n))
    return out


def _rate_cands(rec: dict[str, Any], key: str) -> set[tuple[int, int]]:
    return {(x, n - x) for x, n in _rate_group(rec, key)}


def _two_cands(rec: dict[str, Any], groups: tuple[str, str], key: str) -> set[tuple[int, int, int, int]]:
    want = {k: rec[k] for k in (*groups, "diff", "diff_ci")}
    return {(x1, n1 - x1, x2, n2 - x2) for x1, n1 in _rate_group(rec[groups[0]], key) for x2, n2 in _rate_group(rec[groups[1]], key)
            if C.two_group_numbers(x1, n1, x2, n2, Z95, groups, key) == want}


def _pair_cands(rec: dict[str, Any]) -> set[tuple[int, int, int, int]]:
    """能产生 pair_numbers 这份输出的四格（由 phi 反解 a，只在小窗口里找）"""
    want = {k: rec[k] for k in ("n_approx", "u_rate", "v_rate", "phi", "log_odds_ratio", "log_odds_ratio_ci")}
    out = set()
    for n in range(rec["n_approx"], rec["n_approx"] + 10):
        us = [u for u in range(max(1, int((rec["u_rate"] - 0.006) * n)), min(n, int((rec["u_rate"] + 0.006) * n) + 2)) if C.r2(u / n) == rec["u_rate"]]
        vs = [v for v in range(max(1, int((rec["v_rate"] - 0.006) * n)), min(n, int((rec["v_rate"] + 0.006) * n) + 2)) if C.r2(v / n) == rec["v_rate"]]
        for u in us:
            for v in vs:
                sd = math.sqrt(u * (n - u) * v * (n - v))
                a0 = (rec["phi"] * sd + u * v) / n
                w = int(0.006 * sd / n) + 2
                for a in range(int(a0) - w, int(a0) + w + 2):
                    cells = (a, u - a, v - a, n - u - v + a)
                    if min(cells) > 0 and C.pair_numbers(*cells, Z95) == want:
                        out.add(cells)
    return out


def _without2(s2: Step2, pid: str) -> Step2:
    att = Attempts(True, {n: {it: {p: v for p, v in r.items() if p != pid} for it, r in items.items()} for n, items in s2.attempts.by_node.items()},
                   s2.attempts.pseudonyms - {pid})
    err = Errors(True, {n: {p: c for p, c in d.items() if p != pid} for n, d in s2.errors.episodes.items()}, s2.errors.pseudonyms - {pid})
    return dataclasses.replace(s2, attempts=att, errors=err)


def _truth_pitfall(s2: Step2, mp, pid: str, node: str, code: str) -> str:
    ep = s2.errors.episodes
    names = list(CELL_DELTAS)
    codes = ep.get(node, {}).get(pid)
    if codes:
        return names[0] if code in codes else names[1]
    topic = mp.nodes[node]
    mine = [n for n, t in mp.nodes.items() if t == topic and ep.get(n, {}).get(pid)]
    if not mine:
        return OUT_OF_TABLE
    return names[2] if code in ep[PF.baseline_pick(pid, mine)][pid] else names[3]


def _truth2(member: dict[str, Any], pid: str, first) -> str:
    if pid not in member:
        return OUT_OF_TABLE
    return "前一格" if first(member[pid]) else "后一格"


@pytest.fixture(scope="module")
def differential2(synthetic):
    mp, s2 = synthetic["map"], synthetic["step2"]
    r0 = build_step2(mp, CFG, s2)
    # 易错检验取全部可给出的记录（报表只列非“未达阈值”的完整数值；这里按更强的假设：全部数值都公开）
    pf0, _ = PF.scan_pitfalls(mp, s2.errors, s2.taxonomy, s2.pitfall_codes, CFG)
    return {"mp": mp, "s2": s2, "r0": r0,
            "pf": {(t["node"], t["code"]): (t, _two_cands(t, ("node_group", "baseline"), "share")) for t in pf0},
            "lc": {t["node"]: (t, _rate_cands(t, "low_conf_rate")) for t in r0["unclassifiable"]["nodes"] if t["disclosed"]},
            "it": {(t["node"], it["item_id"]): (it, _rate_cands(it, "rate")) for t in r0["node_split"]["nodes"] for it in t["items"]
                   if "rate" in it},
            "cg": {t["node"]: (t, _pair_cands(t["cross_group"])) for t in r0["node_split"]["nodes"]
                   if t.get("tested") and t["cross_group"]["disclosed"]},
            "ee": {t["node"]: (t, _pair_cands(t["error_evidence"])) for t in r0["node_split"]["nodes"]
                   if t.get("tested") and "phi" in t["error_evidence"]}}


def test_step2_enumeration_includes_perturbed_truth(differential2):
    """枚举本身是对的：扰动后的真实计数在候选里（否则差分测试是空转）；每条记录的候选都不止一种"""
    d = differential2
    mp, s2 = d["mp"], d["s2"]
    tables = {(t["node"], t["code"]): t for t in PF.pitfall_tables(mp, s2.errors, TAX.subject_codes("aya"))}
    for key, (_rec, cands) in d["pf"].items():
        t = tables[key]
        assert PF.perturb_pitfall(t["cells"], t["key"], *key) in cands and len(cands) >= 2, key
    for (node, item), (_rec, cands) in d["it"].items():
        resp = s2.attempts.by_node[node][item]
        right = sum(resp.values())
        assert C.perturb_cells((right, len(resp) - right), C.set_key(resp), f"item/{node}/{item}/") in cands and len(cands) >= 2
    for node, (_rec, cands) in d["lc"].items():                                # 比率极端时可能唯一反解出扰动后两格（同下）
        ep = s2.errors.episodes[node]
        low = sum(1 for c in ep.values() if not c)
        assert C.perturb_cells((low, len(ep) - low), C.set_key(ep), f"low/{node}/") in cands
    for node, (rec, cands) in d["cg"].items():
        lp = SP._passes(s2.attempts, node, rec["low_group"], CFG.split.pass_rate)
        hp = SP._passes(s2.attempts, node, rec["high_group"], CFG.split.pass_rate)
        assert SP.cross_table(lp, hp, node, True)[0].cells() in cands
    # 人数较少的四格（错因加强证据约 200 人）从 2 位小数能唯一反解出“扰动后”的四格：保护靠扰动本身（下面的差分测试），
    # 不靠取整——与第一步同一口径（lessons 待人确认：表键扰动不是差分隐私）
    for _node, (_rec, cands) in d["ee"].items():
        assert cands
    assert len(d["pf"]) > 20 and len(d["it"]) > 40 and Y.N_SPLIT in d["cg"] and Y.N_SPLIT in d["ee"]


def test_step2_differential_deletion(differential2):
    """删一人跑两次：易错候选、难归类、题目正确率、学生层面四格、错因加强证据，每条可比的记录里这个人落在哪一格
    （或不在表里）都至少有两种可能与两次输出相容。取前 4 个伪名"""
    d = differential2
    mp, s2 = d["mp"], d["s2"]
    seen: set[str] = set()
    for pid in sorted(s2.pseudonyms())[:4]:
        s2b = _without2(s2, pid)
        r1 = build_step2(mp, CFG, s2b)
        pf1 = {(t["node"], t["code"]): t for t in PF.scan_pitfalls(mp, s2b.errors, s2b.taxonomy, s2b.pitfall_codes, CFG)[0]}
        for key, (_rec, c0) in d["pf"].items():
            if key not in pf1:
                continue
            possible = _possible_pitfall(c0, _two_cands(pf1[key], ("node_group", "baseline"), "share"))
            truth = _truth_pitfall(s2, mp, pid, *key)
            seen.add(truth)
            assert truth in possible and len(possible) >= 2, (key, truth, possible)
        lc1 = {t["node"]: t for t in r1["unclassifiable"]["nodes"] if t["disclosed"]}
        for node, (_rec, c0) in d["lc"].items():
            if node in lc1:
                possible = _possible2(c0, _rate_cands(lc1[node], "low_conf_rate"))
                truth = _truth2(s2.errors.episodes.get(node, {}), pid, lambda c: not c)
                assert truth in possible and len(possible) >= 2, (node, truth, possible)
        sp1 = {t["node"]: t for t in r1["node_split"]["nodes"]}
        for (node, item), (_rec, c0) in d["it"].items():
            it1 = next((it for it in sp1.get(node, {"items": []})["items"] if it["item_id"] == item), None)
            if it1 is not None and "rate" in it1:
                possible = _possible2(c0, _rate_cands(it1, "rate"))
                truth = _truth2(s2.attempts.by_node[node][item], pid, bool)
                seen.add("题目" + truth)
                assert truth in possible and len(possible) >= 2, (node, item, truth, possible)
        for node, (rec0, c0) in d["cg"].items():
            t1 = sp1.get(node, {})
            if not (t1.get("tested") and t1["cross_group"]["disclosed"]) or (t1["low_group"], t1["high_group"]) != (rec0["low_group"], rec0["high_group"]):
                continue
            possible = _possible_cells(c0, _pair_cands(t1["cross_group"]))
            lp = SP._passes(s2.attempts, node, rec0["low_group"], CFG.split.pass_rate)
            hp = SP._passes(s2.attempts, node, rec0["high_group"], CFG.split.pass_rate)
            truth = (OUT_OF_TABLE if pid not in lp or pid not in hp
                     else list(CELL_DELTAS)[{(True, True): 0, (True, False): 1, (False, True): 2, (False, False): 3}[(lp[pid], hp[pid])]])
            assert truth in possible and len(possible) >= 2, (node, truth, possible)
        for node, (rec0, c0) in d["ee"].items():
            ev1 = sp1.get(node, {}).get("error_evidence", {})
            if "phi" in ev1 and ev1["code"] == rec0["error_evidence"]["code"] and sp1[node]["low_group"] == rec0["low_group"]:
                assert len(_possible_cells(c0, _pair_cands(ev1))) >= 2, node
    assert {"题目前一格", "题目后一格", "题目" + OUT_OF_TABLE} <= seen and len(seen & set(CELL_DELTAS)) >= 3


def test_step2_differential_joint_across_codes(differential2):
    """多表联合差分（建议 6）：同一节点的全部编码表一起看——这个人在本节点组、在基线、还是不在表里，
    要求每张表都相容的假设至少两种（行人数扰动共用，多表平均也还原不出“少的是哪一行”）"""
    d = differential2
    mp, s2 = d["mp"], d["s2"]
    rows = {"本节点组": ("都掌握", "只掌握前者"), "基线": ("只掌握后者", "都未掌握")}
    names = list(CELL_DELTAS)
    checked = 0
    for pid in sorted(s2.pseudonyms())[4:10]:
        s2b = _without2(s2, pid)
        pf1 = {(t["node"], t["code"]): t for t in PF.scan_pitfalls(mp, s2b.errors, s2b.taxonomy, s2b.pitfall_codes, CFG)[0]}
        for node in sorted({k[0] for k in d["pf"]}):
            keys = [k for k in d["pf"] if k[0] == node and k in pf1]
            if len(keys) < 2:
                continue
            per = [_possible_pitfall(d["pf"][k][1], _two_cands(pf1[k], ("node_group", "baseline"), "share")) for k in keys]
            joint = {h for h, cells in rows.items() if all(set(cells) & p for p in per)}
            if all(OUT_OF_TABLE in p for p in per):
                joint.add(OUT_OF_TABLE)
            truth = _truth_pitfall(s2, mp, pid, *keys[0])
            truth_row = OUT_OF_TABLE if truth == OUT_OF_TABLE else ("本节点组" if truth in names[:2] else "基线")
            assert truth_row in joint and len(joint) >= 2, (pid, node, truth_row, joint)
            checked += 1
    assert checked >= 6
