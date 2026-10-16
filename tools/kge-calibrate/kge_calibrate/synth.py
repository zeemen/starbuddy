"""合成队列生成器（spec 范围 2）：固定种子生成学生 × 节点掌握状态，埋入三类结构与噪声，用来检验工具的召回与误报。

埋入的结构（truth.json 写明，只含公共层节点 ID）：
- 真成立的前置：地图上是强前置，生成时 B 的掌握依赖 A（A 掌握 → B 高概率掌握，否则低概率）；含一条跨主题前置与一对兄弟节点（共同前置）；
- 不成立的前置：地图上是强前置，但生成时 B 与 A 独立；
- 隐藏依赖：地图上没有边，但生成时后者依赖前者；
另有一条弱前置（生成时确有依赖，只报告不出候选）与一个全独立的噪声主题。
噪声：每名学生一个“总体能力”潜变量（对每个节点的对数几率加 ABILITY_LOADING × θ，让所有节点都弱相关）；
每个学生 × 节点以 UNSEEN_RATE 的概率未观测（门槛 unseen）；以 MISALIGN_RATE 的概率让掌握概率与门槛不一致（判定只看门槛）。
合成地图每个节点只有一个认知层次，那一行即星亮层次的那一行。伪名只由种子派生（syn- 加小写字母），不对应任何人。
"""
from __future__ import annotations

import hashlib
import json
import math
import random
from dataclasses import dataclass
from typing import Any

from .inputs import MASTERY_FORMAT

SEED = 20261016
SIZE = 600
SUBJECT = "aya"
MAP_VERSION = "0.1.0"
DOMAIN = "MATH.CALIB"
ABILITY_LOADING = 0.25    # 总体能力的载荷：0.4 时不成立前置的期望差值约 0.045，召回明显下降（见测试 test_ability_confound_is_documented）
UNSEEN_RATE = 0.08
MISALIGN_RATE = 0.15      # 掌握概率与门槛“不一致”的比例（概率高但门槛未到星亮，或反之）：覆盖“只按门槛判定”的口径
P_GIVEN_MASTERED = 0.82   # 依赖结构里：前置全部掌握时的掌握概率
P_GIVEN_NOT = 0.2         # 前置未全部掌握时


def _nid(topic: int, n: int) -> str:
    return f"{DOMAIN}.T{topic}.N{n}"


@dataclass(frozen=True)
class NodeSpec:
    id: str
    parents: tuple[str, ...]  # 生成时真实依赖（与地图边无关）
    base: float               # 无依赖时的掌握概率


# 生成顺序即拓扑序
NODES: tuple[NodeSpec, ...] = (
    NodeSpec(_nid(1, 1), (), 0.65), NodeSpec(_nid(1, 2), (_nid(1, 1),), 0.0), NodeSpec(_nid(1, 3), (_nid(1, 2),), 0.0),
    NodeSpec(_nid(1, 4), (), 0.5), NodeSpec(_nid(1, 5), (), 0.55),
    NodeSpec(_nid(2, 1), (), 0.6), NodeSpec(_nid(2, 2), (_nid(2, 1),), 0.0), NodeSpec(_nid(2, 3), (), 0.5),
    NodeSpec(_nid(2, 4), (), 0.6), NodeSpec(_nid(2, 5), (_nid(2, 4),), 0.0),
    NodeSpec(_nid(3, 1), (), 0.65), NodeSpec(_nid(3, 2), (_nid(3, 1),), 0.0), NodeSpec(_nid(3, 3), (_nid(3, 1),), 0.0),
    NodeSpec(_nid(3, 4), (), 0.6), NodeSpec(_nid(3, 5), (_nid(3, 4),), 0.0),
    NodeSpec(_nid(3, 6), (), 0.6), NodeSpec(_nid(3, 7), (_nid(3, 6),), 0.0),
    NodeSpec(_nid(4, 1), (), 0.45), NodeSpec(_nid(4, 2), (), 0.55), NodeSpec(_nid(4, 3), (), 0.5),
    NodeSpec(_nid(4, 4), (), 0.4), NodeSpec(_nid(4, 5), (_nid(2, 2),), 0.0),
)
HOLDS = ((_nid(1, 1), _nid(1, 2)), (_nid(1, 2), _nid(1, 3)), (_nid(2, 1), _nid(2, 2)), (_nid(3, 1), _nid(3, 2)),
         (_nid(3, 1), _nid(3, 3)), (_nid(2, 2), _nid(4, 5)))
NOT_HOLDS = ((_nid(1, 3), _nid(1, 4)), (_nid(2, 1), _nid(2, 3)), (_nid(4, 1), _nid(4, 2)))
HIDDEN = ((_nid(2, 4), _nid(2, 5)), (_nid(3, 6), _nid(3, 7)))
WEAK_TRUE = ((_nid(3, 4), _nid(3, 5)),)
TOPIC_NAMES = {1: "合成主题一（链）", 2: "合成主题二", 3: "合成主题三（兄弟节点）", 4: "合成主题四（噪声）"}


def map_snapshot() -> dict[str, Any]:
    """knowledge.atoms 快照白名单格式的合成地图（JSON 写法）"""
    nodes: list[dict[str, Any]] = [{"id": DOMAIN, "level": "domain", "name": "合成校准领域"}]
    for t, name in TOPIC_NAMES.items():
        nodes.append({"id": f"{DOMAIN}.T{t}", "level": "topic", "parent_id": DOMAIN, "name": name})
    for spec in NODES:
        topic = spec.id.rsplit(".", 1)[0]
        nodes.append({"id": spec.id, "level": "node", "parent_id": topic, "kind": "kp",
                      "name": f"合成节点 {spec.id.split('.', 2)[2]}", "cognitive_levels": ["apply"]})
    edges = [{"from_id": f, "to_id": t, "type": "prerequisite", "strength": "strong"} for f, t in HOLDS + NOT_HOLDS]
    edges += [{"from_id": f, "to_id": t, "type": "prerequisite", "strength": "weak"} for f, t in WEAK_TRUE]
    edges.sort(key=lambda e: (e["from_id"], e["to_id"]))
    return {"map_version": MAP_VERSION, "subject": SUBJECT, "status": "published", "standard_year": 2022,
            "nodes": nodes, "edges": edges}


def truth(seed: int = SEED, size: int = SIZE) -> dict[str, Any]:
    return {"$comment": "合成队列埋入的结构（只含公共层节点 ID）：用于检验召回与误报，不是任何真实数据。",
            "seed": seed, "students": size, "holds": [list(p) for p in HOLDS], "not_holds": [list(p) for p in NOT_HOLDS],
            "hidden": [list(p) for p in HIDDEN], "weak_true": [list(p) for p in WEAK_TRUE]}


def _sigmoid(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-x))


def _logit(p: float) -> float:
    return math.log(p / (1.0 - p))


def pseudonym(seed: int, i: int) -> str:
    """由种子派生的伪名：syn- 加 10 个小写字母（不含数字，避免碰上手机号等模式）"""
    h = hashlib.sha256(f"kge-calibrate:{seed}:{i}".encode("utf-8")).digest()
    return "syn-" + "".join(chr(97 + b % 26) for b in h[:10])


def mastery_csv(seed: int = SEED, size: int = SIZE, loading: float = ABILITY_LOADING) -> str:
    rng = random.Random(seed)
    rows: list[tuple[str, str, str, str]] = []
    for i in range(size):
        pid = pseudonym(seed, i)
        theta = rng.gauss(0.0, 1.0)
        latent: dict[str, bool] = {}
        for spec in NODES:
            if spec.parents:
                p = P_GIVEN_MASTERED if all(latent[q] for q in spec.parents) else P_GIVEN_NOT
            else:
                p = spec.base
            m = rng.random() < _sigmoid(_logit(p) + loading * theta)
            latent[spec.id] = m
            if rng.random() < UNSEEN_RATE:
                rows.append((pid, spec.id, "unseen", ""))
                continue
            if m:
                gate = "retain" if rng.random() < 0.3 else "transfer"
            else:
                gate = ("understand", "execute", "stable")[rng.randrange(3)]
            high = m if rng.random() >= MISALIGN_RATE else not m   # 门槛与概率不完全对齐：判定只看门槛
            prob = 0.7 + 0.29 * rng.random() if high else 0.05 + 0.6 * rng.random()
            rows.append((pid, spec.id, gate, f"{prob:.4f}"))
    rows.sort()
    head = f"# format={MASTERY_FORMAT} synthetic=true subject={SUBJECT} map_version={MAP_VERSION}"
    return "\n".join([head, "pseudonym,node_id,gate,p_mastery", *(",".join(r) for r in rows)]) + "\n"


def render_inputs(seed: int = SEED, size: int = SIZE) -> dict[str, str]:
    dump = lambda d: json.dumps(d, ensure_ascii=False, sort_keys=True, indent=2) + "\n"  # noqa: E731
    return {"map-snapshot.json": dump(map_snapshot()), "mastery.csv": mastery_csv(seed, size), "truth.json": dump(truth(seed, size))}


def evaluate(report: dict[str, Any], tr: dict[str, Any]) -> dict[str, Any]:
    """在合成队列上对照埋入结构：召回（不成立前置、隐藏依赖）与误报"""
    flagged = {(e["from"], e["to"]) for e in report["prerequisite_tests"] if e["conclusion"] == "不成立候选"}
    held = {(e["from"], e["to"]) for e in report["prerequisite_tests"] if e["conclusion"] == "成立"}
    cand = {frozenset((p["u"], p["v"])) for p in report["hidden_dependency"]["pairs"] if p["conclusion"] == "新增前置边候选"}
    not_holds = {tuple(p) for p in tr["not_holds"]}
    holds = {tuple(p) for p in tr["holds"]}
    hidden = {frozenset(p) for p in tr["hidden"]}
    return {"not_holds_found": len(flagged & not_holds), "not_holds_planted": len(not_holds),
            "false_not_holds": len(flagged & holds),
            "holds_confirmed": len(held & holds), "holds_planted": len(holds),
            "hidden_found": len(cand & hidden), "hidden_planted": len(hidden),
            "false_hidden": len(cand - hidden)}
