"""合成队列生成器（spec 范围 6）：按已知真值参数（1PL / 2PL 混合，含少量“先验难度标错”的题）生成作答，固定种子。

结构（真值只在 truth() 里，供测试核对；不写进报表）：
- 四个节点：N_MIXED（2PL，区分度 0.6–2.0 混合）、N_RASCH（全部区分度 1.0，1PL 成立；其中一道题没有先验难度）、
  N_EXTREME（2PL，含一道接近全对的题）、N_SPARSE（6 道题，作答率很低：覆盖“作答人数不足”“接近全对或全错”、人数刚过门槛与冷启动学生）；
- 每道题：认知层次在 understand / apply 间交替，题型在三种合成题型间轮换，题目族每两道（同一认知层次）一族；
- 先验难度 = 0.5 + 真值难度 / 6 加小噪声（截到 [0.02, 0.98]，3 位小数）；MISLABELED 三道题的先验难度取反（1 − 先验）；
- 学生能力：每名学生一个总体能力，每个节点的能力 = 0.8 × 总体 + 0.6 × 节点独有（方差 1，与模型的 N(0, 1) 一致）；
- 时间序：每次作答一个均匀随机时刻，全表按时刻排序编号；前 synthetic_fit_fraction 的作答为 fit 段，其余为 eval 段；
- 掌握估计基线：每名学生每个单元（节点 × 认知层次）按拟合段作答的时间序重放现行掌握估计的 BKT 部分——与
  services/learning/learning/mastery 同一口径：独立作答、A 级、日常练习来源、默认错因系数，强度 = |证据权重| × 等级 × 来源 × 错因，
  同一题目族只计第一次；bkt_step 逐字复制（不 import 服务代码；测试在同一合成输入上与学习服务的 replay 逐个核对）。
  取切分点上的掌握概率；拟合段在该单元没有计入证据的学生不写行（工具用 p_init）。
学生数 SIZE = 487（不在取整到 10 的边界上，PLT-17 教训）。伪名复用 kge-calibrate 的生成函数（syn- 加 10 个小写字母，不对应任何人）。
"""
from __future__ import annotations

import json
import math
import random
from typing import Any

from kge_calibrate.synth import pseudonym

from .config import BaselineRef, Config, load_config
from .inputs import BASELINE_FORMAT, ITEMS_FORMAT, RESPONSES_FORMAT

SEED = 20261017
SIZE = 487
SUBJECT = "aya"
MAP_VERSION = "0.1.0"
FIT_FRACTION = 0.7
N_MIXED, N_RASCH, N_EXTREME, N_SPARSE = ("MATH.IRT.T1.N1", "MATH.IRT.T1.N2", "MATH.IRT.T2.N1", "MATH.IRT.T2.N2")
NODES = (N_MIXED, N_RASCH, N_EXTREME, N_SPARSE)
DENSE_RATE = 0.8
SPARSE_RATES = (0.04, 0.06, 0.09, 0.13, 0.17, 0.24)
LEVELS = ("understand", "apply")
ITEM_TYPES = ("syn-choice", "syn-fill", "syn-solve")
# (真值区分度, 真值难度)
TRUE_PARAMS: dict[str, tuple[tuple[float, float], ...]] = {
    N_MIXED: ((0.6, -1.6), (1.8, -1.2), (1.0, -0.8), (2.0, -0.4), (0.7, -0.1), (1.4, 0.2), (0.9, 0.5), (1.6, 0.9), (0.6, 1.3), (1.2, 1.7)),
    N_RASCH: tuple((1.0, b) for b in (-1.8, -1.3, -0.9, -0.5, -0.2, 0.1, 0.4, 0.8, 1.2, 1.6)),
    N_EXTREME: ((2.2, -3.6), (0.8, -1.4), (1.5, -0.9), (1.1, -0.5), (1.7, -0.1), (0.7, 0.3), (1.3, 0.7), (1.9, 1.1), (0.9, 1.5), (1.0, 2.0)),
    N_SPARSE: ((1.0, -0.6), (1.2, -0.2), (0.9, 0.0), (1.1, 0.3), (1.0, 0.5), (1.0, 0.0)),
}
LOADING = 0.8


def item_id(node: str, j: int) -> str:
    return f"IRT-{node.split('.', 2)[2].replace('.', '')}-Q{j + 1:02d}"


MISLABELED = (item_id(N_MIXED, 1), item_id(N_MIXED, 8), item_id(N_EXTREME, 7))
NO_PRIOR = (item_id(N_RASCH, 4),)


def item_meta(node: str, j: int) -> dict[str, Any]:
    level = LEVELS[j % 2]
    return {"cognitive_level": level, "item_type": ITEM_TYPES[j % 3],
            "family_code": f"FAM-{node.split('.', 2)[2].replace('.', '')}-{level[0].upper()}{j // 4}"}


def _items(seed: int) -> list[dict[str, Any]]:
    rng = random.Random(f"kge-irt/items/{seed}")
    out = []
    for node in NODES:
        for j, (_, b) in enumerate(TRUE_PARAMS[node]):
            iid = item_id(node, j)
            prior: float | None = min(0.98, max(0.02, 0.5 + b / 6.0 + rng.gauss(0.0, 0.02)))
            if iid in MISLABELED:
                prior = 1.0 - prior
            if iid in NO_PRIOR:
                prior = None
            out.append({"item_id": iid, "node_id": node, "prior_difficulty": None if prior is None else round(prior, 3),
                        **item_meta(node, j)})
    return sorted(out, key=lambda r: r["item_id"])


def items_json(seed: int = SEED) -> str:
    doc = {"format": ITEMS_FORMAT, "synthetic": True, "subject": SUBJECT, "map_version": MAP_VERSION, "items": _items(seed)}
    return json.dumps(doc, ensure_ascii=False, sort_keys=True, indent=2) + "\n"


def truth() -> dict[str, Any]:
    return {"items": {item_id(n, j): {"node": n, "a": a, "b": b} for n in NODES for j, (a, b) in enumerate(TRUE_PARAMS[n])},
            "mislabeled": list(MISLABELED), "no_prior": list(NO_PRIOR), "rasch_node": N_RASCH, "sparse_node": N_SPARSE,
            "extreme_item": item_id(N_EXTREME, 0)}


def bkt_step(p: float, correct: bool, strength: float, slip: float, guess: float, learn: float) -> float:
    """services/learning/learning/mastery/bkt.py 的 bkt_step 逐字复制的公式（强度插值）"""
    if strength <= 0:
        return p
    if correct:
        post = p * (1 - slip) / (p * (1 - slip) + (1 - p) * guess)
        target = post + (1 - post) * learn
    else:
        post = p * slip / (p * slip + (1 - p) * (1 - guess))
        target = post
    out = p + min(1.0, strength) * (target - p)
    return min(1.0, max(0.0, out))


def _responses(seed: int, size: int) -> list[tuple[float, str, str, str, bool]]:
    """(时刻, 伪名, 题目, 节点, 是否答对)"""
    rng = random.Random(f"kge-irt/responses/{seed}")
    rows = []
    for i in range(size):
        pid = pseudonym(seed, i)
        g = rng.gauss(0.0, 1.0)
        for node in NODES:
            theta = LOADING * g + math.sqrt(1.0 - LOADING ** 2) * rng.gauss(0.0, 1.0)
            for j, (a, b) in enumerate(TRUE_PARAMS[node]):
                rate = DENSE_RATE if node != N_SPARSE else SPARSE_RATES[j]
                take = rng.random() < rate
                y = rng.random() < 1.0 / (1.0 + math.exp(-a * (theta - b)))
                t = rng.random()
                if take:
                    rows.append((t, pid, item_id(node, j), node, y))
    rows.sort()
    return rows


def baseline_replay(fit_rows: list[tuple[str, str, str, bool]], ref: BaselineRef) -> dict[tuple[str, str, str], float]:
    """(伪名, 题目, 节点, 是否答对) 按时间序 → (伪名, 节点, 认知层次) → 切分点上的掌握概率（只含有计入证据的单元）"""
    meta = {item_id(n, j): item_meta(n, j) for n in NODES for j in range(len(TRUE_PARAMS[n]))}
    state: dict[tuple[str, str, str], float] = {}
    consumed: dict[tuple[str, str, str], set[str]] = {}
    for pid, iid, node, y in fit_rows:
        m = meta[iid]
        unit = (pid, node, m["cognitive_level"])
        fams = consumed.setdefault(unit, set())
        if m["family_code"] in fams:          # 同一题目族只计第一次（gates.py #9）
            continue
        fams.add(m["family_code"])
        p = state.get(unit, ref.p_init)
        state[unit] = bkt_step(p, y, ref.strength(y), ref.p_slip, ref.p_guess(m["item_type"]), ref.p_learn)
    return state


def render_inputs(seed: int = SEED, size: int = SIZE, fit_fraction: float = FIT_FRACTION, cfg: Config | None = None) -> dict[str, str]:
    ref = (cfg or load_config()).bkt
    rows = _responses(seed, size)
    cut = int(len(rows) * fit_fraction)
    meta = f"synthetic=true subject={SUBJECT} map_version={MAP_VERSION}"
    lines = [f"# format={RESPONSES_FORMAT} {meta}", "pseudonym,item_id,node_id,correct,seq,segment"]
    for k, (_, pid, iid, node, y) in enumerate(rows):
        lines.append(f"{pid},{iid},{node},{int(y)},{k},{'fit' if k < cut else 'eval'}")
    state = baseline_replay([(pid, iid, node, y) for _, pid, iid, node, y in rows[:cut]], ref)
    base = [f"# format={BASELINE_FORMAT} {meta}", "pseudonym,node_id,cognitive_level,p_mastery"]
    base += [f"{pid},{node},{level},{p:.6f}" for (pid, node, level), p in sorted(state.items())]
    return {"items.json": items_json(seed), "responses.csv": "\n".join(lines) + "\n", "baseline.csv": "\n".join(base) + "\n"}
