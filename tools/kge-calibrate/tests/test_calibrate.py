"""KGE-09 第一步（specs/kge09-map-calibration）：统计函数正反例、配置与输入守卫、前置边检验与隐藏依赖的结论与边界、
合成队列上的召回与误报上限、样本不足与最小格守卫、伪名不外泄、差分泄露（删一人跑两次）、逐字节一致。

不连库、不联网、只用合成数据；用洞察服务的 venv 运行（make kge-calibrate-test，本工具纯标准库，只借 pytest）。
"""
from __future__ import annotations

import ast
import copy
import dataclasses
import hashlib
import json
import math
import os
import re
import stat
from pathlib import Path
from typing import Any

import pytest

from kge_calibrate import SYNTHETIC_NOTICE
from kge_calibrate import calibrate as C
from kge_calibrate import stats as S
from kge_calibrate import synth as Y
from kge_calibrate.__main__ import main
from kge_calibrate.config import CONFIG_PATH, GATES, LIT, MIN_CELL_FLOOR, PENDING, ConfigError, load_config, parse_config
from kge_calibrate.inputs import (COMPUTABLE_KINDS, EDGE_TYPES, MASTERY_FORMAT, NODE_ID, SUBJECT_PREFIX, InputRefused, Mastery,
                                  parse_map, parse_mastery, read_limited)
from kge_calibrate.inputs import _FIELDS, _LISTS, _TOP  # noqa: PLC2701 — 与知识服务白名单逐字核对
from kge_calibrate.report import (OUTPUT_NAMES, OutputRefused, assert_no_pseudonyms, build_report, check_out_dir, render, run,
                                  write_files)

TOOL = Path(__file__).resolve().parents[1]
REPO = Path(os.environ.get("KGE_CALIBRATE_REPO") or TOOL.parents[1])
SAMPLE = TOOL / "sample"
CFG = load_config()
Z95 = S.z_for(0.95)
NUMERIC_KEYS = {"a_mastered", "a_not_mastered", "diff", "diff_ci", "p_adj_band", "n_approx", "u_rate", "v_rate", "phi",
                "log_odds_ratio", "log_odds_ratio_ci"}
EXACT_COUNT_KEYS = {"n", "cells", "b_mastered", "students", "p", "p_adj", "both", "u_only", "v_only", "neither"}


# ---------- 帮助函数 ----------
def _assign(src: str, name: str) -> Any:
    """从仓库源码里取一个模块级常量（只读核对，不 import 其他服务）；frozenset({...}) 取其中的集合"""
    tree = ast.parse(src)
    for node in tree.body:
        targets = node.targets if isinstance(node, ast.Assign) else [node.target] if isinstance(node, ast.AnnAssign) else []
        for t in targets:
            if isinstance(t, ast.Name) and t.id == name:
                value = node.value
                if isinstance(value, ast.Call) and isinstance(value.func, ast.Name) and value.func.id == "frozenset":
                    return frozenset(ast.literal_eval(value.args[0]))
                return ast.literal_eval(value)
    raise AssertionError(f"{name} 不在源码里")


def mkmap(nodes: list[tuple[str, str]], edges: list[tuple[str, str, str]], extra_nodes: list[dict] | None = None) -> Any:
    """nodes: (节点 ID, 主题 ID)；edges: (from, to, strength)。生成 knowledge.atoms 快照格式的文档并解析"""
    topics = sorted({t for _, t in nodes} | {n["parent_id"] for n in extra_nodes or []})
    doc = {"map_version": "0.1.0", "subject": "aya", "status": "published", "standard_year": 2022,
           "nodes": [{"id": "MATH.X", "level": "domain", "name": "测试领域"}]
           + [{"id": t, "level": "topic", "parent_id": "MATH.X", "name": "测试主题"} for t in topics]
           + [{"id": n, "level": "node", "parent_id": t, "kind": "kp", "name": "测试节点"} for n, t in nodes] + list(extra_nodes or []),
           "edges": [{"from_id": f, "to_id": t, "type": "prerequisite", "strength": s} for f, t, s in edges]}
    return parse_map(doc)


def _pid(i: int) -> str:
    """测试用合成伪名：syn- 加 10 个字母（由序号确定）"""
    out = []
    for _ in range(10):
        out.append(chr(97 + i % 26))
        i //= 26
    return "syn-" + "".join(out)


def mastery_from(students: list[dict[str, bool]], offset: int = 0) -> Mastery:
    observed: dict[str, dict[str, bool]] = {}
    for i, st in enumerate(students):
        for nid, m in st.items():
            observed.setdefault(nid, {})[_pid(i + offset)] = m
    return Mastery(True, len(students), observed, frozenset(_pid(i + offset) for i in range(len(students))))


def four(u: str, v: str, a: int, b: int, c: int, d: int, extra: dict[str, bool] | None = None) -> list[dict[str, bool]]:
    """四格表 → 学生列表：a 都掌握，b 只掌握 u，c 只掌握 v，d 都未掌握"""
    e = extra or {}
    out: list[dict[str, bool]] = []
    for k, (x, y) in zip((a, b, c, d), ((True, True), (True, False), (False, True), (False, False))):
        out += [{u: x, v: y, **e} for _ in range(k)]   # 每名学生一个独立的字典
    return out


A, B, C2, D, E = (f"MATH.X.A.N{i}" for i in range(1, 6))
TA, TB = "MATH.X.A", "MATH.X.B"


def edge_rec(students: list[dict[str, bool]], edges: list[tuple[str, str, str]], cfg=CFG) -> list[dict[str, Any]]:
    ids = sorted({n for st in students for n in st} | {x for f, t, _ in edges for x in (f, t)})
    mp = mkmap([(n, n.rsplit(".", 1)[0]) for n in ids], edges)
    return C.check_edges(mp, mastery_from(students), cfg, noisy=False)   # 关掉扰动核对统计口径


def by_edge(recs: list[dict[str, Any]], f: str, t: str) -> dict[str, Any]:
    return next(r for r in recs if r["from"] == f and r["to"] == t)


# ---------- 统计函数 ----------
def test_z_and_wilson_textbook_values():
    assert Z95 == pytest.approx(1.959964, abs=1e-6)
    assert S.z_for(0.99) == pytest.approx(2.575829, abs=1e-6)
    lo, hi = S.wilson(81, 263, Z95)       # Newcombe (1998) 例：81/263 → 0.2553–0.3662
    assert (round(lo, 4), round(hi, 4)) == (0.2553, 0.3662)
    assert S.wilson(0, 10, Z95)[0] == 0.0 and S.wilson(10, 10, Z95)[1] == 1.0
    assert S.wilson(13, 13, Z95)[1] == 1.0 and S.wilson(0, 13, Z95)[0] == 0.0   # n = 13 时公式有 1e-16 的浮点残差
    assert S.wilson(0, 10, Z95)[1] == pytest.approx(0.2775, abs=1e-4)
    assert S.wilson(10, 10, Z95)[0] == pytest.approx(0.7225, abs=1e-4)
    assert S.wilson(30, 100, Z95)[1] - S.wilson(30, 100, Z95)[0] > S.wilson(300, 1000, Z95)[1] - S.wilson(300, 1000, Z95)[0]


def test_newcombe_textbook_values():
    d, lo, hi = S.newcombe(56, 70, 48, 80, Z95)   # Newcombe (1998) 方法 10 例：0.2，0.0524–0.3339
    assert (round(d, 4), round(lo, 4), round(hi, 4)) == (0.2, 0.0524, 0.3339)
    d2, lo2, hi2 = S.newcombe(48, 80, 56, 70, Z95)
    assert (round(d2, 4), round(lo2, 4), round(hi2, 4)) == (-0.2, -0.3339, -0.0524)


def test_two_proportion_and_chi2_agree():
    p = S.two_proportion_p(56, 70, 48, 80)
    assert p == pytest.approx(0.008045, abs=1e-6)
    assert S.two_proportion_p(48, 80, 56, 70) == pytest.approx(p)
    assert S.chi2_p(S.phi(56, 14, 48, 32), 150) == pytest.approx(p, rel=1e-9)   # 2×2 皮尔逊卡方 = 合并方差 z²
    assert S.two_proportion_p(10, 20, 30, 60) == 1.0
    assert S.two_proportion_p(0, 20, 0, 30) == 1.0
    assert S.two_proportion_p(20, 20, 30, 30) == 1.0
    assert S.chi2_p(0.0, 100) == 1.0


def test_bh_adjust():
    assert S.bh_adjust([0.01, 0.04, 0.03, 0.005]) == pytest.approx([0.02, 0.04, 0.04, 0.02])
    assert S.bh_adjust([0.5, 0.9]) == pytest.approx([0.9, 0.9])
    assert S.bh_adjust([0.04]) == [0.04]
    assert S.bh_adjust([]) == []
    assert S.bh_adjust([0.9, 0.95, 0.99]) == pytest.approx([0.99, 0.99, 0.99])
    assert S.bh_adjust([0.8, 0.01]) == pytest.approx([0.8, 0.02])


def test_phi_and_log_odds_ratio():
    assert S.phi(10, 0, 0, 10) == 1.0
    assert S.phi(0, 10, 10, 0) == -1.0
    assert S.phi(25, 25, 25, 25) == 0.0
    assert S.phi(10, 10, 0, 0) is None
    assert S.phi(150, 30, 30, 150) == pytest.approx(0.6667, abs=1e-4)
    lor, lo, hi = S.log_odds_ratio(56, 14, 48, 32, Z95)
    assert lor == pytest.approx(math.log(56 * 32 / (14 * 48)))
    assert (hi - lor) == pytest.approx(Z95 * math.sqrt(1 / 56 + 1 / 14 + 1 / 48 + 1 / 32))
    assert S.log_odds_ratio(5, 0, 3, 2, Z95) is None


def test_stats_reject_bad_input():
    for bad in ((5, 0), (6, 5), (-1, 5)):
        with pytest.raises(S.StatsError):
            S.wilson(*bad, Z95)
    with pytest.raises(S.StatsError):
        S.bh_adjust([1.2])
    with pytest.raises(S.StatsError):
        S.phi(-1, 2, 3, 4)
    with pytest.raises(S.StatsError):
        S.z_for(0.3)


def test_output_rounding_helpers():
    assert [C.n_approx(n) for n in (20, 29, 30, 599, 600)] == [20, 20, 30, 590, 600]
    assert C.r2(-0.001) == 0.0 and C.r2(0.5284) == 0.53
    assert [C.p_band(p) for p in (0.0, 0.0009, 0.001, 0.009, 0.04, 0.05, 0.9)] == \
        ["<0.001", "<0.001", "<0.01", "<0.01", "<0.05", "≥0.05", "≥0.05"]


# ---------- 配置 ----------
def _raw_config() -> dict:
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def test_config_defaults_pending():
    assert CFG.signoff == PENDING and "待教研确认" in _raw_config()["$comment"]
    assert CFG.min_cell == 20 == MIN_CELL_FLOOR
    assert CFG.mastery.rule == "star_lit" and CFG.condition_on_shared is True and CFG.max_pairs_per_topic == 5000
    assert re.fullmatch(r"[0-9a-f]{16}", CFG.fingerprint)
    assert CFG.provenance() == {"config_version": "draft-0.2.0", "fingerprint": CFG.fingerprint, "signoff": PENDING}


@pytest.mark.parametrize("path,value", [
    (("min_cell",), 19), (("min_cell",), 0), (("min_cell",), 20.5), (("mastery", "rule"), "gate"),
    (("mastery", "rule"), "p_mastery"), (("prerequisite", "confidence"), 0.5), (("prerequisite", "fdr_q"), 0.5),
    (("hidden_dependency", "min_phi"), 0.0), (("hidden_dependency", "condition_on_shared_prerequisites"), "yes"),
    (("hidden_dependency", "max_pairs_per_topic"), 0), (("signoff",), ""), (("schema_version",), "x"),
    (("config_version",), "has space"), (("synthetic_acceptance", "max_false_hidden"), -1)])
def test_config_rejects(path, value):
    doc = _raw_config()
    cur = doc
    for k in path[:-1]:
        cur = cur[k]
    cur[path[-1]] = value
    with pytest.raises(ConfigError):
        parse_config(doc)


def test_config_rejects_extra_and_missing_keys():
    doc = _raw_config()
    doc["mastery"]["p_threshold"] = 0.7          # 概率阈值路径已删除
    with pytest.raises(ConfigError):
        parse_config(doc)
    doc = _raw_config()
    del doc["hidden_dependency"]["min_phi"]
    with pytest.raises(ConfigError):
        parse_config(doc)


def test_config_fingerprint_changes_with_values():
    doc = _raw_config()
    doc["prerequisite"]["min_effect"] = 0.15
    assert parse_config(doc).fingerprint != CFG.fingerprint


def test_gates_match_learning_service():
    """门槛取值与星亮门槛与学习服务 mastery/gates.py 逐字一致（只读口径，不改掌握计算）"""
    src = (REPO / "services/learning/learning/mastery/gates.py").read_text(encoding="utf-8")
    assert tuple(_assign(src, "GATES")) == GATES
    assert _assign(src, "LIT") == LIT
    assert "def star_basis_level(" in src and "gates.get(level) in LIT" in src
    store = (REPO / "services/learning/learning/mastery/store.py").read_text(encoding="utf-8")
    assert tuple(_assign(store, "COMPUTABLE_KINDS")) == COMPUTABLE_KINDS
    assert [CFG.mastery.mastered(g) for g in GATES] == [False, False, False, False, True, True]


# ---------- 地图快照：与知识服务同一格式 ----------
def test_snapshot_whitelist_matches_knowledge_service():
    src = (REPO / "services/knowledge/knowledge/atoms.py").read_text(encoding="utf-8")
    assert _assign(src, "_FIELDS") == _FIELDS
    assert _assign(src, "_TOP") == _TOP
    assert _assign(src, "_LISTS") == _LISTS
    assert tuple(_assign(src, "EDGE_TYPES")) == EDGE_TYPES
    nid = (REPO / "services/knowledge/knowledge/nodeid.py").read_text(encoding="utf-8")
    assert _assign(nid, "NODE_ID_REGEX") == NODE_ID.pattern
    assert _assign(nid, "SUBJECT_PREFIX") == SUBJECT_PREFIX


def test_map_parsing_picks_computable_nodes_and_prereq_edges():
    doc = Y.map_snapshot()
    doc["nodes"].append({"id": "MATH.CALIB.T1.OLD", "level": "node", "parent_id": "MATH.CALIB.T1", "kind": "kp", "name": "撤下",
                         "status": "deprecated"})
    doc["nodes"].append({"id": "MATH.CALIB.T1.PIT", "level": "node", "parent_id": "MATH.CALIB.T1", "kind": "pitfall", "name": "易错"})
    doc["edges"].append({"from_id": "MATH.CALIB.T1.N1", "to_id": "MATH.CALIB.T1.N5", "type": "related"})
    doc["edges"].append({"from_id": "MATH.CALIB.T1.N5", "to_id": "MATH.CALIB.T1.N4", "type": "prerequisite", "status": "deprecated"})
    doc["edges"].append({"from_id": "MATH.CALIB.T1.PIT", "to_id": "MATH.CALIB.T1.N5", "type": "prerequisite"})
    mp = parse_map(doc)
    assert len(mp.nodes) == len(Y.NODES) and "MATH.CALIB.T1.OLD" not in mp.nodes and "MATH.CALIB.T1.PIT" not in mp.nodes
    assert mp.nodes["MATH.CALIB.T1.N1"] == "MATH.CALIB.T1"
    assert {(e.from_id, e.to_id) for e in mp.edges} == {*Y.HOLDS, *Y.NOT_HOLDS, *Y.WEAK_TRUE}
    assert ("MATH.CALIB.T1.PIT", "MATH.CALIB.T1.N5") in mp.all_prereq
    assert {e.strength for e in mp.edges if (e.from_id, e.to_id) in Y.WEAK_TRUE} == {"weak"}
    doc = Y.map_snapshot()
    del doc["edges"][0]["strength"]                                                # 快照缺省强度 = strong（迁移 0011 的默认值）
    first = doc["edges"][0]
    assert next(e for e in parse_map(doc).edges if (e.from_id, e.to_id) == (first["from_id"], first["to_id"])).strength == "strong"


@pytest.mark.parametrize("mutate", [
    lambda d: d["nodes"][-1].update(student_id="x"),
    lambda d: d.update(learners=[]),
    lambda d: d["nodes"].append({"id": "CHN.CALIB.T1.N9", "level": "node", "parent_id": "MATH.CALIB.T1", "kind": "kp", "name": "x"}),
    lambda d: d["nodes"].append({"id": "math.bad", "level": "node", "name": "x"}),
    lambda d: d["edges"].append({"from_id": "MATH.CALIB.T1.N1", "to_id": "MATH.NOPE.T1.N1", "type": "prerequisite"}),
    lambda d: d["edges"].append({"from_id": "MATH.CALIB.T1.N1", "to_id": "MATH.CALIB.T1.N5", "type": "prerequisite", "strength": "medium"}),
    lambda d: d["edges"].append({"from_id": "MATH.CALIB.T1.N1", "to_id": "MATH.CALIB.T1.N5", "type": "depends"}),
    lambda d: d["edges"].append(dict(d["edges"][0])),
    lambda d: d.update(subject="math"),
    lambda d: d.update(map_version="v1"),
    lambda d: d.pop("nodes"),
])
def test_map_refused(mutate):
    doc = Y.map_snapshot()
    mutate(doc)
    with pytest.raises(InputRefused):
        parse_map(doc)


def test_map_digest_matches_knowledge_m1_rule():
    doc = Y.map_snapshot()
    shuffled = json.loads(json.dumps(doc))
    shuffled["nodes"].reverse()
    assert parse_map(doc).digest != parse_map(shuffled).digest
    raw = json.dumps(doc, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    assert parse_map(doc).digest == hashlib.sha256(raw.encode("utf-8")).hexdigest()


def test_read_limited(tmp_path):
    f = tmp_path / "x.json"
    f.write_text("123456", encoding="utf-8")
    assert read_limited(f, 6, "测试文件") == b"123456"
    with pytest.raises(InputRefused, match="大小上限"):
        read_limited(f, 5, "测试文件")
    with pytest.raises(InputRefused, match="普通文件"):
        read_limited(tmp_path, 100, "测试文件")
    with pytest.raises(InputRefused, match="读不了"):
        read_limited(tmp_path / "missing", 100, "测试文件")


# ---------- 掌握状态表：输入守卫 ----------
MP = parse_map(Y.map_snapshot())
N1, N2 = "MATH.CALIB.T1.N1", "MATH.CALIB.T1.N2"
META = f"# format={MASTERY_FORMAT} synthetic=true subject=aya map_version=0.1.0"
P1, P2, P3, P4, P5 = "syn-aaaaaaaaaa", "syn-bbbbbbbbbb", "syn-cccccccccc", "syn-dddddddddd", "syn-eeeeeeeeee"


def _csv(*rows: str, head: str = "pseudonym,node_id,gate,p_mastery", meta: str = META) -> str:
    return "\n".join([meta, head, *rows]) + "\n"


def _refused(text: str) -> str:
    with pytest.raises(InputRefused) as ei:
        parse_mastery(text, MP, CFG.mastery)
    return str(ei.value)


@pytest.mark.parametrize("col", ["name", "student_name", "姓名", "school", "学校", "phone", "手机号", "mobile", "student_id",
                                 "id_card", "身份证", "address", "email", "guardian", "class", "班级", "wechat", "birthday"])
def test_mastery_refuses_personal_columns(col):
    msg = _refused(_csv(f"{P1},{N1},transfer,0.9,x", head=f"pseudonym,node_id,gate,p_mastery,{col}"))
    assert msg.startswith("掌握状态表第 5 列疑似个人信息字段") and f"“{col}”" not in msg


def test_mastery_refuses_unknown_column_and_layout():
    assert "不认识" in _refused(_csv(f"{P1},{N1},transfer,0.9,1", head="pseudonym,node_id,gate,p_mastery,attempts"))
    assert "必须有 pseudonym" in _refused(_csv(f"{N1},transfer", head="node_id,gate"))
    assert "必须有 gate" in _refused(_csv(f"{P1},{N1},0.9", head="pseudonym,node_id,p_mastery"))
    assert "列数不对" in _refused(_csv(f"{P1},{N1},transfer"))
    assert "列重复" in _refused(_csv(f"{P1},{N1},transfer,transfer", head="pseudonym,node_id,gate,gate"))
    assert "没有数据行" in _refused(_csv())


@pytest.mark.parametrize("meta,why", [
    (META.replace("synthetic=true", "synthetic=false"), "只接受合成样例"),
    (META.replace(" synthetic=true", ""), "必须含"),
    (META.replace("subject=aya", "subject=wenli"), "不一致"),
    (META.replace("map_version=0.1.0", "map_version=0.2.0"), "不一致"),
    (META.replace(MASTERY_FORMAT, "other/1"), "format"),
    (META + " school=x", "不认识"),
    ("pseudonym,node_id,gate,p_mastery", "元数据行"),
])
def test_mastery_refuses_bad_metadata(meta, why):
    assert why in _refused(_csv(f"{P1},{N1},transfer,0.9", meta=meta))


@pytest.mark.parametrize("row,why", [
    (f"4e6fb7f6-8fed-519a-be2b-0a444183e371,{N1},transfer,0.9", "伪名格式"),     # 原始 student_id
    (f"syn-4e6fb7f68fed519a,{N1},transfer,0.9", "伪名格式"),                       # 含数字
    (f"Syn-Abcdefghij,{N1},transfer,0.9", "伪名格式"),
    (f"syn-abcdefghi,{N1},transfer,0.9", "伪名格式"),                              # 只有 9 个字母
    (f"syn-{'a' * 25},{N1},transfer,0.9", "伪名格式"),
    (f"abc-abcdefghij,{N1},transfer,0.9", "伪名格式"),
    (f"pil-abcdefghij,{N1},transfer,0.9", "syn- 开头"),                            # 合成样例里不收试点伪名
    (f"syn-13812345678,{N1},transfer,0.9", "疑似含个人信息"),                     # 手机号
    (f"{P1},{N1},transfer,320102200801011234", "疑似含个人信息"),                  # 身份证号
    (f"{P1},{N1},transfer,a@b.cn", "疑似含个人信息"),
    (f"张三,{N1},transfer,0.9", "疑似含个人信息"),
    (f"{P1},MATH.CALIB.T9.N9,transfer,0.9", "可计算的知识节点"),
    (f"{P1},{N1},lit,0.9", "门槛状态"),
    (f"{P1},{N1},transfer,1.5", "掌握概率"),
    (f"{P1},{N1},transfer,nan", "掌握概率"),
    (f"{P1},{N1},transfer,abc", "掌握概率"),
])
def test_mastery_refuses_bad_rows(row, why):
    msg = _refused(_csv(f"syn-zzzzzzzzzz,{N2},stable,0.3", row))
    assert why in msg and "第 4 行" in msg
    assert row.split(",")[0] not in msg     # 不回显输入内容


def test_mastery_refuses_duplicate_rows():
    assert "重复" in _refused(_csv(f"{P1},{N1},transfer,0.9", f"{P1},{N1},stable,0.4"))


def test_mastery_star_lit_only_gate_decides():
    """星亮口径：只看门槛（transfer / retain 算掌握），掌握概率与门槛不一致时以门槛为准；unseen 与空门槛算未观测"""
    m = parse_mastery(_csv(f"{P1},{N1},transfer,0.05", f"{P2},{N1},retain,", f"{P3},{N1},stable,0.99",
                           f"{P4},{N1},unseen,0.9", f"{P5},{N1},,0.9", f"syn-ffffffffff,{N2},understand,0.95"), MP, CFG.mastery)
    assert m.observed[N1] == {P1: True, P2: True, P3: False}
    assert m.observed[N2] == {"syn-ffffffffff": False}
    assert m.students == 6 and m.synthetic is True
    m2 = parse_mastery(_csv(f"{P1},{N1},execute", head="pseudonym,node_id,gate"), MP, CFG.mastery)
    assert m2.observed[N1] == {P1: False}


def test_synthetic_gate_and_probability_misaligned():
    """合成数据里确有门槛与概率不一致的行（概率高但未星亮、或星亮但概率低），用来覆盖“只按门槛判定”"""
    lines = Y.mastery_csv().splitlines()[2:]
    hi_not_lit = sum(1 for ln in lines if (f := ln.split(","))[3] and float(f[3]) >= 0.7 and f[2] not in LIT)
    lo_lit = sum(1 for ln in lines if (f := ln.split(","))[3] and float(f[3]) < 0.7 and f[2] in LIT)
    assert hi_not_lit > 100 and lo_lit > 100


# ---------- 前置边检验 ----------
def test_edge_holds_not_holds_reversed():
    st = four(A, B, 80, 20, 20, 80) + four(C2, D, 50, 50, 50, 50) + four(E, "MATH.X.A.N6", 20, 80, 80, 20)
    recs = edge_rec(st, [(A, B, "strong"), (C2, D, "strong"), (E, "MATH.X.A.N6", "strong")])
    h, n, r = by_edge(recs, A, B), by_edge(recs, C2, D), by_edge(recs, E, "MATH.X.A.N6")
    assert h["conclusion"] == C.HOLDS and h["disclosed"]
    assert h["a_mastered"] == {"n_approx": 100, "b_rate": 0.8, "b_rate_ci": [0.71, 0.87], "b_observed_rate": 1.0}
    assert h["a_not_mastered"]["n_approx"] == 100 and h["a_not_mastered"]["b_rate"] == 0.2
    assert h["diff"] == 0.6 and h["diff_ci"][0] > 0 and h["p_adj_band"] == "<0.001"
    assert n["conclusion"] == C.NOT_HOLDS and n["diff"] == 0.0 and n["diff_ci"][0] < 0 < n["diff_ci"][1] and "包含 0" in n["reason"]
    assert n["p_adj_band"] == "≥0.05"
    assert r["conclusion"] == C.NOT_HOLDS and r["diff_ci"][1] < 0 and "反向" in r["reason"]


def test_edge_effect_threshold():
    st = four(A, B, 1180, 820, 1000, 1000)       # 差值 0.09：区间在 0 以上但跨过 0.1 → 待定
    rec = by_edge(edge_rec(st, [(A, B, "strong")]), A, B)
    assert rec["diff"] == 0.09 and rec["diff_ci"][0] > 0 and rec["diff_ci"][1] >= 0.1 and rec["conclusion"] == C.UNDECIDED
    st = four(A, B, 2750, 2250, 2500, 2500)      # 差值 0.05、区间整体低于 0.1 → 不成立候选
    rec = by_edge(edge_rec(st, [(A, B, "strong")]), A, B)
    assert rec["diff_ci"][0] > 0 and rec["diff_ci"][1] < 0.1 and rec["conclusion"] == C.NOT_HOLDS and "效应量" in rec["reason"]
    loose = dataclasses.replace(CFG, min_effect=0.05)
    assert by_edge(edge_rec(four(A, B, 1180, 820, 1000, 1000), [(A, B, "strong")], loose), A, B)["conclusion"] == C.HOLDS


def test_edge_effect_threshold_uses_family_adjusted_interval():
    """“区间上界 < min_effect”用按族校正的置信水平：单独检验时判不成立，族里多两条边后区间变宽 → 待定"""
    base = four(A, B, 550, 450, 500, 500)
    alone = by_edge(edge_rec(base, [(A, B, "strong")]), A, B)
    assert alone["diff_ci"][0] > 0 and alone["diff_ci"][1] < 0.1 and alone["conclusion"] == C.NOT_HOLDS
    st = base + [s for i in range(2) for s in four(f"MATH.X.B.M{i}", f"MATH.X.B.K{i}", 50, 50, 50, 50)]
    edges = [(A, B, "strong")] + [(f"MATH.X.B.M{i}", f"MATH.X.B.K{i}", "strong") for i in range(2)]
    crowded = by_edge(edge_rec(st, edges), A, B)
    assert crowded["diff_ci"] == alone["diff_ci"] and crowded["conclusion"] == C.UNDECIDED


def test_edge_bh_family():
    """单独检验时成立的边，加入三条无差异的强前置后经 BH 校正不再显著 → 待定；弱前置不进强前置这一族"""
    base = four(A, B, 120, 80, 98, 102)
    alone = by_edge(edge_rec(base, [(A, B, "strong")]), A, B)
    assert alone["p_adj_band"] == "<0.05" and alone["conclusion"] == C.HOLDS
    st = base + [s for i in range(3) for s in four(f"MATH.X.B.M{i}", f"MATH.X.B.K{i}", 50, 50, 50, 50)]
    edges = [(A, B, "strong")] + [(f"MATH.X.B.M{i}", f"MATH.X.B.K{i}", "strong") for i in range(3)]
    crowded = by_edge(edge_rec(st, edges), A, B)
    assert crowded["p_adj_band"] == "≥0.05" and crowded["conclusion"] == C.UNDECIDED
    weak_edges = [(A, B, "strong")] + [(f"MATH.X.B.M{i}", f"MATH.X.B.K{i}", "weak") for i in range(3)]
    recs = edge_rec(st, weak_edges)
    assert by_edge(recs, A, B)["conclusion"] == C.HOLDS
    assert {by_edge(recs, f"MATH.X.B.M{i}", f"MATH.X.B.K{i}")["conclusion"] for i in range(3)} == {C.WEAK_ONLY}


def test_weak_edge_only_reported():
    rec = by_edge(edge_rec(four(A, B, 80, 20, 20, 80), [(A, B, "weak")]), A, B)
    assert rec["conclusion"] == C.WEAK_ONLY and rec["disclosed"] and rec["diff"] == 0.6 and rec["strength"] == "weak"
    rec = by_edge(edge_rec(four(A, B, 50, 50, 50, 50), [(A, B, "weak")]), A, B)
    assert rec["conclusion"] == C.WEAK_ONLY


def test_edge_any_small_cell_is_insufficient():
    """四格任一格 < 20：结论只写“样本不足”（不给“成立”等），不出任何数值；恰好 20 → 检验"""
    for cells in ((80, 20, 19, 81), (80, 19, 20, 80), (19, 81, 20, 80), (80, 20, 81, 19), (60, 40, 9, 10)):
        rec = by_edge(edge_rec(four(A, B, *cells), [(A, B, "strong")]), A, B)
        assert rec["conclusion"] == C.INSUFFICIENT and not rec["disclosed"] and not (NUMERIC_KEYS & set(rec)), cells
    rec = by_edge(edge_rec(four(A, B, 20, 20, 20, 20), [(A, B, "strong")]), A, B)
    assert rec["conclusion"] != C.INSUFFICIENT and rec["disclosed"]


def test_unobserved_students_not_counted():
    st = four(A, B, 80, 20, 20, 80) + [{A: True}] * 50 + [{B: False}] * 50
    rec = by_edge(edge_rec(st, [(A, B, "strong")]), A, B)
    assert rec["a_mastered"]["n_approx"] == 100 and rec["a_not_mastered"]["n_approx"] == 100
    assert rec["a_mastered"]["b_observed_rate"] == 0.7 and rec["a_not_mastered"]["b_observed_rate"] == 1.0   # 100 / 150


# ---------- 隐藏依赖 ----------
def _pairs(students, nodes, edges, cfg=CFG, extra_nodes=None):
    mp = mkmap(nodes, edges, extra_nodes)
    return C.scan_pairs(mp, mastery_from(students), cfg, noisy=False)   # 关掉扰动核对统计口径


def _pair(recs, u, v):
    return next(r for r in recs if {r["u"], r["v"]} == {u, v})


def test_reach_closure_and_ancestors():
    cl = C.reach([("a", "b"), ("b", "c"), ("c", "a"), ("d", "e")])
    assert cl["a"] == {"a", "b", "c"} and cl["d"] == {"e"} and "e" not in cl
    anc = C.ancestors_map(cl)
    assert anc["c"] == {"a", "b", "c"} and anc["e"] == {"d"} and "d" not in anc


def test_hidden_scope_and_exclusions():
    n6, bn = "MATH.X.A.N6", "MATH.X.B.N1"
    nodes = [(A, TA), (B, TA), (C2, TA), (D, TA), (E, TA), (n6, TA), (bn, TB)]
    edges = [(A, B, "strong"), (B, C2, "weak")]
    st = four(D, E, 150, 30, 30, 150, {A: True, B: True, C2: True, n6: False, bn: False})
    for i, s in enumerate(st):   # 让 bn 与 D 完全一致：跨主题的强相关不进入扫描
        s[bn] = s[D]
        s[n6] = i % 2 == 0
    recs, excluded = _pairs(st, nodes, edges)
    keys = {frozenset((r["u"], r["v"])) for r in recs}
    assert frozenset((A, B)) not in keys and frozenset((B, C2)) not in keys
    assert frozenset((A, C2)) not in keys
    assert excluded == {"direct_edge": 2, "transitive": 1, "topic_over_limit": 0}
    assert all(r["topic"] == TA for r in recs) and not any(bn in (r["u"], r["v"]) for r in recs)
    cand = _pair(recs, D, E)
    assert cand["conclusion"] == C.CANDIDATE and cand["directions"] == [[D, E], [E, D]]
    assert cand["n_approx"] == 360 and cand["phi"] == 0.67 and cand["u_rate"] == 0.5 and cand["v_rate"] == 0.5
    assert cand["p_adj_band"] == "<0.001" and cand["log_odds_ratio"] == round(math.log(25), 2)


def test_hidden_transitive_either_direction():
    nodes = [(A, TA), (B, TA), (C2, TA)]
    recs, excluded = _pairs(four(A, C2, 150, 30, 30, 150, {B: True}), nodes, [(C2, B, "strong"), (B, A, "strong")])
    assert excluded == {"direct_edge": 2, "transitive": 1, "topic_over_limit": 0} and recs == []


def test_hidden_topic_pair_limit():
    nodes = [(A, TA), (B, TA), (C2, TA), ("MATH.X.B.N1", TB)]
    cfg = dataclasses.replace(CFG, max_pairs_per_topic=2)
    recs, excluded = _pairs(four(A, B, 150, 30, 30, 150, {C2: True}), nodes, [], cfg)
    assert recs == [] and excluded["topic_over_limit"] == 3
    recs, excluded = _pairs(four(A, B, 150, 30, 30, 150, {C2: True}), nodes, [], dataclasses.replace(CFG, max_pairs_per_topic=3))
    assert excluded["topic_over_limit"] == 0 and len(recs) == 3


def test_hidden_thresholds():
    nodes = [(A, TA), (B, TA)]
    weak = _pair(_pairs(four(A, B, 120, 80, 80, 120), nodes, [])[0], A, B)
    assert weak["phi"] == 0.2 and weak["p_adj_band"] == "<0.001" and weak["conclusion"] == C.BELOW and "directions" not in weak
    neg = _pair(_pairs(four(A, B, 30, 150, 150, 30), nodes, [])[0], A, B)
    assert neg["phi"] < 0 and neg["conclusion"] == C.BELOW
    lo = dataclasses.replace(CFG, min_phi=0.2)
    assert _pair(_pairs(four(A, B, 120, 80, 80, 120), nodes, [], lo)[0], A, B)["conclusion"] == C.CANDIDATE
    small = _pair(_pairs(four(A, B, 26, 20, 20, 26), nodes, [], dataclasses.replace(CFG, min_phi=0.1))[0], A, B)
    assert small["phi"] == 0.13 and small["p_adj_band"] == "≥0.05" and small["conclusion"] == C.BELOW


def test_hidden_bh_family():
    lo = dataclasses.replace(CFG, min_phi=0.1)
    nodes = [(A, TA), (B, TA)]
    alone = _pair(_pairs(four(A, B, 58, 42, 42, 58), nodes, [], lo)[0], A, B)
    assert alone["p_adj_band"] == "<0.05" and alone["conclusion"] == C.CANDIDATE
    extra = [f"MATH.X.A.M{i}" for i in range(4)]
    st = four(A, B, 58, 42, 42, 58)
    for i, s in enumerate(st):        # 无关节点：取学生序号的二进制位（与 A、B 和彼此近似独立）
        for k, nid in enumerate(extra):
            s[nid] = (i >> k) % 2 == 0
    crowded = _pair(_pairs(st, nodes + [(n, TA) for n in extra], [], lo)[0], A, B)
    assert crowded["p_adj_band"] == "≥0.05" and crowded["conclusion"] == C.BELOW


def test_hidden_min_cell_boundary():
    nodes = [(A, TA), (B, TA)]
    for cells in ((150, 30, 19, 150), (19, 30, 30, 150), (150, 19, 30, 150), (150, 30, 30, 19)):
        rec = _pair(_pairs(four(A, B, *cells), nodes, [])[0], A, B)
        assert rec["conclusion"] == C.INSUFFICIENT and not rec["disclosed"] and not (NUMERIC_KEYS & set(rec)), cells
    assert _pair(_pairs(four(A, B, 150, 30, 20, 150), nodes, [])[0], A, B)["conclusion"] == C.CANDIDATE


def test_hidden_conditions_on_shared_prerequisite():
    """兄弟节点：都依赖 P，P 掌握者里两者独立 → 分层后不报候选；不分层会因共同前置报成假候选"""
    p, q = "MATH.X.A.N9", "MATH.X.A.N8"      # q 只是 A 的前置，不是共同前置
    nodes = [(A, TA), (B, TA), (p, TA), (q, TA)]
    edges = [(p, A, "strong"), (p, B, "strong"), (q, A, "strong")]
    st = four(A, B, 196, 84, 84, 36, {p: True, q: True}) + four(A, B, 4, 36, 36, 324, {p: False, q: True})
    on = _pair(_pairs(st, nodes, edges)[0], A, B)
    assert on["conditioned_on_shared"] and on["shared_prerequisites"] == [p]
    assert on["n_approx"] == 400 and on["phi"] == 0.0 and on["conclusion"] == C.BELOW
    off = _pair(_pairs(st, nodes, edges, dataclasses.replace(CFG, condition_on_shared=False))[0], A, B)
    assert not off["conditioned_on_shared"] and off["n_approx"] == 800 and off["phi"] == pytest.approx(0.38, abs=0.011)
    assert off["conclusion"] == C.CANDIDATE


def test_shared_prerequisites_only_computable_and_observed():
    """契约 B2：共同前置只算地图里可计算、且有观测的节点；不可计算（易错点）或无人观测的祖先不拿来分层"""
    pit = {"id": "MATH.X.A.PIT", "level": "node", "parent_id": TA, "kind": "pitfall", "name": "易错"}
    p = "MATH.X.A.N9"                         # 可计算但没有任何观测
    nodes = [(A, TA), (B, TA), (p, TA)]
    edges = [("MATH.X.A.PIT", A, "strong"), ("MATH.X.A.PIT", B, "strong"), (p, A, "strong"), (p, B, "strong")]
    st = four(A, B, 150, 30, 30, 150)
    recs = _pairs(st, nodes, edges, extra_nodes=[pit])[0]
    rec = _pair(recs, A, B)
    assert rec["shared_prerequisites"] == [] and not rec["conditioned_on_shared"]
    assert rec["n_approx"] == 360 and rec["conclusion"] == C.CANDIDATE
    for s in st:                              # 一旦 p 有观测（全部掌握），它就是共同前置、拿来分层
        s[p] = True
    rec = _pair(_pairs(st, nodes, edges, extra_nodes=[pit])[0], A, B)
    assert rec["shared_prerequisites"] == [p] and rec["conditioned_on_shared"] and rec["n_approx"] == 360


# ---------- 合成队列：召回与误报 ----------
@pytest.fixture(scope="module")
def synthetic():
    inputs = Y.render_inputs()
    mp = parse_map(json.loads(inputs["map-snapshot.json"]))
    mastery = parse_mastery(inputs["mastery.csv"], mp, CFG.mastery)
    report = build_report(mp, mastery, CFG)
    return {"inputs": inputs, "map": mp, "mastery": mastery, "report": report, "files": run(mp, mastery, CFG)}


def test_synthetic_recall_and_false_positive_caps(synthetic):
    ev = Y.evaluate(synthetic["report"], json.loads(synthetic["inputs"]["truth.json"]))
    assert ev["not_holds_found"] == ev["not_holds_planted"] == 3
    assert ev["hidden_found"] == ev["hidden_planted"] == 2
    assert ev["false_not_holds"] <= CFG.max_false_not_holds
    assert ev["false_hidden"] <= CFG.max_false_hidden
    assert ev["holds_confirmed"] == ev["holds_planted"] == 6
    weak = [e for e in synthetic["report"]["prerequisite_tests"] if e["strength"] == "weak"]
    assert [e["conclusion"] for e in weak] == [C.WEAK_ONLY]
    sib = _pair(synthetic["report"]["hidden_dependency"]["pairs"], "MATH.CALIB.T3.N2", "MATH.CALIB.T3.N3")
    assert sib["conditioned_on_shared"] and sib["conclusion"] != C.CANDIDATE


def test_synthetic_recall_across_seeds():
    """固定的 10 个种子上合计：不成立前置召回 ≥ 85%（检验本身有漏报），隐藏依赖全部找出，误报不超过上限"""
    mp = parse_map(Y.map_snapshot())
    tot: dict[str, int] = {}
    for seed in range(1, 11):
        rep = build_report(mp, parse_mastery(Y.mastery_csv(seed), mp, CFG.mastery), CFG)
        for k, v in Y.evaluate(rep, Y.truth(seed)).items():
            tot[k] = tot.get(k, 0) + v
    assert tot["not_holds_found"] / tot["not_holds_planted"] >= 0.85
    assert tot["hidden_found"] == tot["hidden_planted"] == 20
    assert tot["false_not_holds"] <= CFG.max_false_not_holds * 10 and tot["false_hidden"] <= CFG.max_false_hidden * 10


def test_ability_confound_lowers_recall():
    """局限的记录（待人确认）：总体能力载荷 0.4 时不成立前置的期望差值约 0.045，“区间包含 0”的判据会漏报更多"""
    mp = parse_map(Y.map_snapshot())
    found = sum(Y.evaluate(build_report(mp, parse_mastery(Y.mastery_csv(s, loading=0.4), mp, CFG.mastery), CFG), Y.truth(s))
                ["not_holds_found"] for s in range(1, 11))
    assert found < 30


def test_synthetic_generator_is_deterministic_and_synthetic():
    a, b = Y.render_inputs(), Y.render_inputs()
    assert a == b and Y.render_inputs(seed=7) != a
    text = a["mastery.csv"]
    assert text.startswith(f"# format={MASTERY_FORMAT} synthetic=true ")
    lines = text.splitlines()
    assert len(lines) == 2 + Y.SIZE * len(Y.NODES)
    pids = {ln.split(",")[0] for ln in lines[2:]}
    assert len(pids) == Y.SIZE and all(re.fullmatch(r"syn-[a-z]{10}", p) for p in pids)
    assert sum(ln.split(",")[2] == "unseen" for ln in lines[2:]) > 0
    tr = json.loads(a["truth.json"])
    assert tr["seed"] == Y.SEED and len(tr["not_holds"]) == 3 and len(tr["hidden"]) == 2


# ---------- 防泄露守卫与输出 ----------
def _records(report):
    yield from (("edge", r) for r in report["prerequisite_tests"])
    yield from (("pair", r) for r in report["hidden_dependency"]["pairs"])


def _walk_numbers(obj, path=""):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield from _walk_numbers(v, f"{path}.{k}")
    elif isinstance(obj, list):
        for v in obj:
            yield from _walk_numbers(v, path)
    elif isinstance(obj, (int, float)) and not isinstance(obj, bool):
        yield path, obj


def test_no_exact_counts_in_output(synthetic):
    """安全 B1：报表里没有任何精确计数——人数只有向下取整到 10 的 n_approx，比率与区间 2 位，p 值只给分档；
    同一次运行内跨表相减也就得不到任何精确数（更得不到 < 20 的格子）"""
    rep = json.loads(synthetic["files"]["calibration-report.json"])
    m = rep["method"]["min_cell"]
    assert rep["cohort"] == {"students_approx": 600}
    seen_insufficient = False
    for kind, r in _records(rep):
        assert not (EXACT_COUNT_KEYS & set(r))
        if not r["disclosed"]:
            assert r["conclusion"] == C.INSUFFICIENT and not (NUMERIC_KEYS & set(r))
            seen_insufficient = True
            continue
        for path, x in _walk_numbers(r):
            if path.endswith("n_approx"):
                assert isinstance(x, int) and x % 10 == 0 and x >= m, path
            elif path.endswith("b_observed_rate"):
                assert round(x, 1) == x
            else:
                assert isinstance(x, float) and round(x, 2) == x, path
        for g in ("a_mastered", "a_not_mastered") if kind == "edge" else ():
            assert not (EXACT_COUNT_KEYS & set(r[g]))
    assert seen_insufficient
    for k in ('"students":', '"cells":', '"b_mastered":', '"n":', '"p":', '"p_adj":'):
        assert k not in synthetic["files"]["calibration-report.json"]


# 差分测试：删一人跑两次，断言两次输出联合起来也还原不了这个人。攻击者的已知：工具源码（扰动分布、取整规则），不知道：
# 记录键（由伪名派生，试点伪名由库内一次性 salt 生成）。做法：对每条记录枚举所有能产生这份输出的“扰动后四格”，
# 再看“这个人落在哪一格（或不在这张表里）”有几种可能与两次输出都相容——至少两种才算还原不了。
# 这个人在表里：删掉后表键整体换掉，四格扰动全部重抽，两次的扰动各在 ±NOISE_MAX 内、互相独立；不在表里：表键不变，扰动相同。
CELL_DELTAS = {"都掌握": (1, 0, 0, 0), "只掌握前者": (0, 1, 0, 0), "只掌握后者": (0, 0, 1, 0), "都未掌握": (0, 0, 0, 1)}
OUT_OF_TABLE = "不在表里"


def _possible_cells(c0: set[tuple[int, ...]], c1: set[tuple[int, ...]]) -> set[str]:
    out = {OUT_OF_TABLE} if c0 & c1 else set()
    span = 2 * C.NOISE_MAX
    for name, d in CELL_DELTAS.items():
        if any(all(abs(p0[i] - p1[i] - d[i]) <= span for i in range(4)) for p0 in c0 for p1 in c1):
            out.add(name)
    return out


def _edge_candidates(rec: dict[str, Any]) -> set[tuple[int, int, int, int]]:
    """能产生这份前置边输出的扰动后四格 (a, b, c, d)"""
    def group(g):
        out = []
        for n in range(g["n_approx"], g["n_approx"] + 10):
            for x in range(max(0, int((g["b_rate"] - 0.006) * n)), min(n, int((g["b_rate"] + 0.006) * n) + 1) + 1):
                if C.r2(x / n) == g["b_rate"]:
                    lo, hi = S.wilson(x, n, Z95)
                    if [C.r2(lo), C.r2(hi)] == g["b_rate_ci"]:
                        out.append((x, n))
        return out
    want = {k: rec[k] for k in ("a_mastered", "a_not_mastered", "diff", "diff_ci")}
    strip = lambda d: {**d, "a_mastered": {k: v for k, v in d["a_mastered"].items() if k != "b_observed_rate"},  # noqa: E731
                       "a_not_mastered": {k: v for k, v in d["a_not_mastered"].items() if k != "b_observed_rate"}}
    want = strip(want)
    return {(x1, n1 - x1, x2, n2 - x2) for x1, n1 in group(rec["a_mastered"]) for x2, n2 in group(rec["a_not_mastered"])
            if C.edge_numbers(x1, n1, x2, n2, Z95) == want}


def _pair_candidates(rec: dict[str, Any]) -> set[tuple[int, int, int, int]]:
    want = {k: rec[k] for k in ("n_approx", "u_rate", "v_rate", "phi", "log_odds_ratio", "log_odds_ratio_ci")}
    out = set()
    for n in range(rec["n_approx"], rec["n_approx"] + 10):
        us = [u for u in range(n + 1) if C.r2(u / n) == rec["u_rate"]]
        vs = [v for v in range(n + 1) if C.r2(v / n) == rec["v_rate"]]
        for u in us:
            for v in vs:
                for a in range(max(0, u + v - n), min(u, v) + 1):
                    cells = (a, u - a, v - a, n - u - v + a)
                    if min(cells) <= 0:
                        continue
                    ph = S.phi(*cells)
                    if ph is None or C.r2(ph) != rec["phi"]:
                        continue
                    if C.pair_numbers(*cells, Z95) == want:
                        out.add(cells)
    return out


def _without(m: Mastery, pid: str) -> Mastery:
    obs = {n: {k: v for k, v in d.items() if k != pid} for n, d in m.observed.items()}
    return Mastery(m.synthetic, m.students - 1, obs, m.pseudonyms - {pid})


def _true_cell(m: Mastery, pid: str, u: str, v: str) -> str:
    mu, mv = m.observed.get(u, {}), m.observed.get(v, {})
    if pid not in mu or pid not in mv:
        return OUT_OF_TABLE
    return list(CELL_DELTAS)[{(True, True): 0, (True, False): 1, (False, True): 2, (False, False): 3}[(mu[pid], mv[pid])]]


@pytest.fixture(scope="module")
def differential(synthetic):
    mp, m0 = synthetic["map"], synthetic["mastery"]
    r0 = synthetic["report"]
    e0 = {(e["from"], e["to"]): e for e in r0["prerequisite_tests"] if e["disclosed"]}
    p0 = {(p["u"], p["v"]): p for p in r0["hidden_dependency"]["pairs"] if p["conclusion"] == C.CANDIDATE}
    return {"mp": mp, "m0": m0, "e0": e0, "p0": p0, "c0": {k: _edge_candidates(e) for k, e in e0.items()},
            "q0": {k: _pair_candidates(p) for k, p in p0.items()}}


def test_differential_deletion_cannot_recover_individual(differential):
    """安全 B1：删一人跑两次。逐条记录：能产生输出的四格不止一种；且这个人落在哪一格，与两次输出相容的可能至少两种。
    取前 6 个伪名（覆盖四格的各种落点与不在表里）"""
    d = differential
    for k, cands in [*d["c0"].items(), *d["q0"].items()]:
        assert len(cands) >= 2, k
    seen_true: set[str] = set()
    for pid in sorted(d["m0"].pseudonyms)[:6]:
        r1 = build_report(d["mp"], _without(d["m0"], pid), CFG)   # 学生总数分档可能跨档：只透露“少了一人”
        e1 = {(e["from"], e["to"]): e for e in r1["prerequisite_tests"]}
        p1 = {(p["u"], p["v"]): p for p in r1["hidden_dependency"]["pairs"]}
        for key in d["e0"]:
            if not e1[key]["disclosed"]:          # 跨过门槛：两次输出里没有可比的数值
                continue
            possible = _possible_cells(d["c0"][key], _edge_candidates(e1[key]))
            truth = _true_cell(d["m0"], pid, *key)
            seen_true.add(truth)
            assert truth in possible and len(possible) >= 2, (key, truth, possible)
            if truth != OUT_OF_TABLE:
                assert set(CELL_DELTAS) <= possible, (key, truth, possible)   # 在表里：四格哪一格都说得通
        for key in d["p0"]:
            if not p1[key]["disclosed"]:
                continue
            possible = _possible_cells(d["q0"][key], _pair_candidates(p1[key]))
            truth = _true_cell(d["m0"], pid, *key)
            assert truth in possible and len(possible) >= 2, (key, truth, possible)
    assert len(seen_true) >= 4          # 确实覆盖了多种落点


def test_table_key_tracks_members_and_observed_rate_is_noisy():
    """表键由表内学生决定：删掉表内的人表键就变，删掉不在表里的人表键不变；观测率的分母同样按成员集加扰动"""
    st = four(A, B, 30, 30, 30, 30) + [{A: True}] * 5
    m = mastery_from(st)
    t = C.table2(m, A, B)
    inside, outside = _pid(0), _pid(len(st) - 1)
    assert C.table2(_without(m, inside), A, B).key != t.key
    assert C.table2(_without(m, outside), A, B).key == t.key
    members = [k for k, v in m.observed[A].items() if v]
    seen_diff = False
    for off in range(0, 3000, 101):
        mo = mastery_from(st, offset=off)
        mem = [k for k, v in mo.observed[A].items() if v]
        nz = C.noise(C.set_key(mem), "obs")
        assert C._observed_rate(mo, A, True, 50, True) == min(1.0, 50 / (len(mem) + nz))
        assert C._observed_rate(mo, A, True, 50, False) == 50 / len(mem)
        seen_diff |= nz != 0
    assert seen_diff and len(members) == 65


def test_noisy_threshold_keeps_margin():
    """扰动时门槛加余量：扰动后最小格落在 [min_cell, min_cell + NOISE_MAX) 的也只写“样本不足”（真实格可能 < min_cell）"""
    mp = mkmap([(A, TA), (B, TA)], [(A, B, "strong")])
    hit_edge = hit_pair = False
    for off in range(0, 4000, 97):
        m = mastery_from(four(A, B, 21, 21, 21, 21), offset=off)
        pmin = min(C.perturb(C.table2(m, A, B)).cells())
        rec = C.check_edges(mp, m, CFG)[0]
        assert rec["disclosed"] == (pmin >= CFG.min_cell + C.NOISE_MAX)
        mp2 = mkmap([(A, TA), (B, TA)], [])
        pair = C.scan_pairs(mp2, m, CFG)[0][0]
        assert pair["disclosed"] == (pmin >= CFG.min_cell + C.NOISE_MAX)
        if CFG.min_cell <= pmin < CFG.min_cell + C.NOISE_MAX:
            hit_edge = hit_pair = True
    assert hit_edge and hit_pair


def test_true_counts_are_among_candidates(synthetic, differential):
    """枚举本身是对的：报表确实由扰动后的四格算出，扰动后的四格在候选里（否则上面的差分测试是空转）；
    扰动确实开着：至少有一条记录的扰动后四格不等于真实四格"""
    m0 = synthetic["mastery"]
    moved = 0
    for key, cands in differential["c0"].items():
        t = C.table2(m0, *key)
        assert C.perturb(t).cells() in cands
        moved += C.perturb(t).cells() != t.cells()
    for key, cands in differential["q0"].items():
        sp = differential["p0"][key]
        only = C._all_mastered(m0, sp["shared_prerequisites"]) if sp["conditioned_on_shared"] else None
        assert C.perturb(C.table2(m0, *key, only)).cells() in cands
    assert moved >= 3


def test_perturbation_properties():
    keys = [C.set_key([_pid(i), _pid(i + 1)]) for i in range(2000)]
    vals = [C.noise(k, "a") for k in keys]
    assert set(vals) == {-2, -1, 0, 1, 2} and abs(sum(vals) / len(vals)) < 0.1
    assert C.noise(keys[0], "a") == C.noise(keys[0], "a")                     # 确定性
    assert C.set_key([_pid(1), _pid(2)]) == C.set_key([_pid(2), _pid(1)])      # 与顺序无关
    assert C.set_key([_pid(1), _pid(2)]) != C.set_key([_pid(1)])               # 少一人表键就变
    t = C.Table2(30, 30, 30, 30, keys[0])
    p = C.perturb(t)
    assert p.key == t.key and all(abs(x - y) <= C.NOISE_MAX for x, y in zip(p.cells(), t.cells()))
    m = mastery_from(four(A, B, 22, 22, 22, 22))
    mp = mkmap([(A, TA), (B, TA)], [(A, B, "strong")])
    noisy = C.check_edges(mp, m, CFG)[0]
    exact = C.check_edges(mp, m, CFG, noisy=False)[0]
    assert exact["disclosed"]
    assert noisy["disclosed"] == (min(C.perturb(C.table2(m, A, B)).cells()) >= CFG.min_cell + C.NOISE_MAX)


def test_pseudonyms_never_in_output(synthetic):
    files = synthetic["files"]
    assert set(files) == set(OUTPUT_NAMES)
    for text in files.values():
        assert "syn-" not in text
        for pid in list(synthetic["mastery"].pseudonyms)[:50]:
            assert pid not in text


def test_leak_check_refuses():
    with pytest.raises(OutputRefused):
        assert_no_pseudonyms({"worklist.md": "x syn-abcdefghij y"}, ["syn-abcdefghij"])
    assert_no_pseudonyms({"worklist.md": "x"}, ["syn-abcdefghij"])
    mp = mkmap([(A, TA), (B, TA)], [(A, B, "strong")])
    m = mastery_from(four(A, B, 80, 20, 20, 80))
    with pytest.raises(OutputRefused):
        run(mp, dataclasses.replace(m, pseudonyms=frozenset({A})), CFG)   # 伪装成伪名的字串出现在输出里 → 拒绝


def test_cohort_size_banded_and_withheld():
    mp = mkmap([(A, TA), (B, TA)], [(A, B, "strong")])
    rep = build_report(mp, mastery_from(four(A, B, 5, 5, 4, 5)), CFG)
    assert rep["cohort"] == {"students_approx": C.INSUFFICIENT}
    assert rep["prerequisite_tests"][0]["conclusion"] == C.INSUFFICIENT and rep["hidden_dependency"]["pairs"] == []
    assert build_report(mp, mastery_from(four(A, B, 5, 5, 5, 5)), CFG)["cohort"] == {"students_approx": 20}
    assert build_report(mp, mastery_from(four(A, B, 7, 7, 7, 8)), CFG)["cohort"] == {"students_approx": 20}


def test_notice_follows_metadata():
    mp = mkmap([(A, TA), (B, TA)], [(A, B, "strong")])
    m = mastery_from(four(A, B, 80, 20, 20, 80))
    assert build_report(mp, m, CFG)["notice"] == SYNTHETIC_NOTICE
    assert build_report(mp, dataclasses.replace(m, synthetic=False), CFG)["notice"] == ""


def test_report_shape(synthetic):
    rep = synthetic["report"]
    assert rep["notice"] == SYNTHETIC_NOTICE and rep["config"]["signoff"] == PENDING
    assert rep["map"]["snapshot_digest"] == synthetic["map"].digest and rep["map"]["prerequisite_edges"] == {"strong": 9, "weak": 1}
    keys = [(e["from"], e["to"]) for e in rep["prerequisite_tests"]]
    assert keys == sorted(keys)
    assert sum(rep["summary"]["prerequisite"].values()) == len(rep["prerequisite_tests"])
    assert rep["summary"]["hidden_dependency"][C.CANDIDATE] == 2


def test_worklist_content(synthetic):
    wl = synthetic["files"]["worklist.md"]
    assert SYNTHETIC_NOTICE in wl and PENDING in wl and "不改地图、不写库" in wl
    for f, t in Y.NOT_HOLDS:
        assert f"{f}（合成节点 {f.split('.', 2)[2]}） → {t}" in wl
    assert wl.count("- [ ] 确认（删除这条前置边）") == 3 and wl.count("- [ ] 修改为弱前置\n") == 3
    assert wl.count("- [ ] 驳回") == 5
    for u, v in Y.HIDDEN:
        assert f"- [ ] 确认 {u} → {v}" in wl and f"- [ ] 确认 {v} → {u}" in wl
    assert "Newcombe" in wl and "Wilson" in wl and "phi" in wl and "约 " in wl
    assert not re.search(r"(?<![<≥\d])\d\.\d{3}", wl)       # 工作单里也没有 3 位以上的小数（p 分档“<0.001”除外）


def test_worklist_empty_sections():
    mp = mkmap([(A, TA), (B, TA), (C2, TA)], [(A, B, "strong")])
    st = four(A, B, 10, 40, 10, 40)
    for i, s in enumerate(st):
        s[C2] = i % 3 == 0
    rep = build_report(mp, mastery_from(st), CFG)
    assert rep["prerequisite_tests"][0]["conclusion"] == C.INSUFFICIENT
    wl = render(rep, dict(mp.names))["worklist.md"]
    assert "（无）" in wl and "前置边样本不足（1 条）" in wl


def test_byte_identical(synthetic):
    again = run(synthetic["map"], parse_mastery(Y.render_inputs()["mastery.csv"], synthetic["map"], CFG.mastery), CFG)
    assert again == synthetic["files"]
    assert synthetic["files"]["calibration-report.json"].endswith("}\n")


def test_matches_committed_sample(synthetic):
    """与仓库样例逐字节一致（mastery.csv 约 600 KB，不进仓库：由固定种子重生）。
    样例过期时：python -m kge_calibrate synth --out <空的临时目录>，再 run 到一个空目录，把结果放进 tools/kge-calibrate/sample，并在 PR 里说明差异。"""
    want = {**{k: v for k, v in synthetic["inputs"].items() if k != "mastery.csv"}, **synthetic["files"]}
    assert sorted(p.name for p in SAMPLE.iterdir()) == sorted(want)
    for name, content in want.items():
        assert (SAMPLE / name).read_text(encoding="utf-8") == content, f"{name} 与样例不同"


# ---------- SQL 与手册（静态核对；本仓不执行 SQL） ----------
def test_sql_texts_static_guards():
    fn = (TOOL / "sql/export-function.draft.sql").read_text(encoding="utf-8")
    call = (TOOL / "sql/export-mastery.readonly.sql").read_text(encoding="utf-8")
    runbook = (TOOL / "RUNBOOK.md").read_text(encoding="utf-8")
    assert "gen_random_bytes" in fn and "SECURITY DEFINER" in fn and "star_level_rule" in fn
    assert "(cr.decision = 'revoked') DESC" in fn and "erased_at IS NULL" in fn and "er.status = 'pending'" in fn
    assert "c.cognitive_level = b.level" in fn and "max(state)" not in fn and "max(p_mastery)" not in fn
    for text in (fn, call):
        text = re.sub(r"--[^\n]*", "", text)       # 只看 SQL 本身，不看注释
        assert ":'salt'" not in text and not re.search(r"\b(INSERT|UPDATE|DELETE|TRUNCATE|GRANT|ALTER|DROP)\b", text)
    assert "READ ONLY" in call and "kge.calibration_export_mastery" in call
    assert "SALT" not in runbook and "openssl" not in runbook and "演练流程" not in runbook
    assert "不得做任何真实导出" in runbook and "只用合成数据" in runbook


# ---------- 命令行与输出目录 ----------
def test_cli_end_to_end(tmp_path, synthetic, capsys):
    assert main(["synth", "--out", str(tmp_path / "in")]) == 0
    assert sorted(p.name for p in (tmp_path / "in").iterdir()) == ["map-snapshot.json", "mastery.csv", "truth.json"]
    args = ["run", "--map", str(tmp_path / "in/map-snapshot.json"), "--mastery", str(tmp_path / "in/mastery.csv"),
            "--out", str(tmp_path / "out")]
    assert main(args) == 0
    for name, content in synthetic["files"].items():
        path = tmp_path / "out" / name
        assert path.read_text(encoding="utf-8") == content
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert main(args) == 2          # 目录非空（含同名文件）也不覆盖
    assert "拒绝写出" in capsys.readouterr().out


def test_cli_refusals(tmp_path, capsys):
    assert main(["synth", "--out", str(tmp_path / "in")]) == 0
    text = (tmp_path / "in/mastery.csv").read_text(encoding="utf-8")
    (tmp_path / "in/mastery.csv").write_text(text.replace("synthetic=true", "synthetic=false", 1), encoding="utf-8")
    rc = main(["run", "--map", str(tmp_path / "in/map-snapshot.json"), "--mastery", str(tmp_path / "in/mastery.csv"),
               "--out", str(tmp_path / "out")])
    assert rc == 2 and not (tmp_path / "out").exists()
    assert "拒绝输入" in capsys.readouterr().out
    bad = copy.deepcopy(_raw_config())
    bad["min_cell"] = 5
    (tmp_path / "cfg.json").write_text(json.dumps(bad), encoding="utf-8")
    rc = main(["run", "--map", str(tmp_path / "in/map-snapshot.json"), "--mastery", str(tmp_path / "in/mastery.csv"),
               "--out", str(tmp_path / "out"), "--config", str(tmp_path / "cfg.json")])
    assert rc == 2 and "配置不合法" in capsys.readouterr().out
    (tmp_path / "in/map.yaml").write_text("x: 1", encoding="utf-8")
    rc = main(["run", "--map", str(tmp_path / "in/map.yaml"), "--mastery", str(tmp_path / "in/mastery.csv"), "--out", str(tmp_path / "o2")])
    assert rc == 2
    assert main(["synth", "--out", str(tmp_path / "s"), "--size", "0"]) == 2
    assert main(["synth", "--out", str(tmp_path / "in")]) == 2      # 合成输入也不覆盖


def test_out_dir_guard(tmp_path):
    target = tmp_path / "real"
    target.mkdir()
    link = tmp_path / "link"
    link.symlink_to(target)
    with pytest.raises(OutputRefused):
        check_out_dir(link)
    f = tmp_path / "file"
    f.write_text("x", encoding="utf-8")
    with pytest.raises(OutputRefused):
        check_out_dir(f)
    check_out_dir(target)                          # 空目录可以
    (target / "worklist.md").write_text("old", encoding="utf-8")
    with pytest.raises(OutputRefused):
        check_out_dir(target)
    other = tmp_path / "other"
    other.mkdir()
    (other / "notes.txt").write_text("x", encoding="utf-8")
    with pytest.raises(OutputRefused):
        write_files({"worklist.md": "a"}, other)            # 写出前一定先查目录
    assert sorted(p.name for p in other.iterdir()) == ["notes.txt"]
    fresh = tmp_path / "fresh"
    written = write_files({"worklist.md": "a", "calibration-report.json": "{}\n"}, fresh)
    assert [p.name for p in written] == ["calibration-report.json", "worklist.md"]
    assert all(stat.S_IMODE(p.stat().st_mode) == 0o600 for p in written)
