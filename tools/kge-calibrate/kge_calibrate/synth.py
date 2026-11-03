"""合成队列生成器（spec 范围 2）：固定种子生成学生 × 节点掌握状态，埋入三类结构与噪声，用来检验工具的召回与误报。

埋入的结构（truth.json 写明，只含公共层节点 ID）：
- 真成立的前置：地图上是强前置，生成时 B 的掌握依赖 A（A 掌握 → B 高概率掌握，否则低概率）；含一条跨主题前置与一对兄弟节点（共同前置）；
- 不成立的前置：地图上是强前置，但生成时 B 与 A 独立；
- 隐藏依赖：地图上没有边，但生成时后者依赖前者；
另有一条弱前置（生成时确有依赖，只报告不出候选）与一个全独立的噪声主题。
噪声：每名学生一个“总体能力”潜变量（对每个节点的对数几率加 ABILITY_LOADING × θ，让所有节点都弱相关）；
每个学生 × 节点以 UNSEEN_RATE 的概率未观测（门槛 unseen）；以 MISALIGN_RATE 的概率让掌握概率与门槛不一致（判定只看门槛）。
合成地图每个节点只有一个认知层次，那一行即星亮层次的那一行。伪名只由种子派生（syn- 加小写字母），不对应任何人。

第二步（spec-step2 范围 5）另加主题五（掌握状态表里没有它，第一步的结果不受影响）与作答聚合表、错因表、易错点编码对照，
用独立的随机流（第一步的掌握状态表逐字节不变），学生数 SIZE2（前 SIZE 个伪名与掌握状态表相同）。埋入：
- 未覆盖的高频错因：T5.N1 上 shijian_wenti 高频，N1 关联的易错点只覆盖 shenti_cuowu → 新增易错点候选；
- 已覆盖的高频错因：T5.N2 上 biaoda_cuowu 高频，关联易错点覆盖它 → 已有易错点覆盖（不是候选）；
- 对照缺失：T5.N7 上 fangfa_cuowu 高频，关联的易错点不在对照里 → 易错点对照缺失（不是候选）；
- 待定 / 敏感编码：T5.N6 上 fenlei_yilou（分类表待定）高频，另混入 xinli_xiguan（敏感）→ 读入时丢弃，不得出现在任何检验里；
- 难归类聚集：T5.N3 上只有低置信错因的学生占比高；
- 应拆分节点：T5.N4 的 8 道题分别考两种互相独立的能力（两群正确率不同），错因编码随失败的那一群分成两类；
- 难度分档（不该拆，反例）：T5.N5 的题目分难易两档、难度差大，只考一种能力（连续能力潜变量），并有猜对与失误
  （学生层面 phi 不大，但呈嵌套结构）；其余节点题目难度连续分布。
"""
from __future__ import annotations

import hashlib
import json
import math
import random
from dataclasses import dataclass
from typing import Any

from .inputs import MASTERY_FORMAT
from .responses import ATTEMPTS_FORMAT, ERRORS_FORMAT, PITFALL_CODES_FORMAT

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
TOPIC_NAMES = {1: "合成主题一（链）", 2: "合成主题二", 3: "合成主题三（兄弟节点）", 4: "合成主题四（噪声）", 5: "合成主题五（错因与分化）"}

# ---------- 第二步：主题五 ----------
SIZE2 = 1200
T5 = tuple(_nid(5, n) for n in range(1, 8))
N_UNCOVERED, N_COVERED, N_LOWCONF, N_SPLIT, N_TIERS, N_PLAIN, N_UNMAPPED = T5
# 易错点：(ID, 层级, 父级, 名称, 关联来源节点（has_pitfall 边；细目级易错点经 parent_id 关联）, 对照里覆盖的编码；None = 不在对照里)
PITFALLS: tuple[tuple[str, str, str, str, str | None, tuple[str, ...] | None], ...] = (
    (f"{DOMAIN}.T5.P1", "node", f"{DOMAIN}.T5", "合成易错点 表达", N_COVERED, ("biaoda_cuowu",)),
    (f"{DOMAIN}.T5.P2", "node", f"{DOMAIN}.T5", "合成易错点 未对照", N_UNMAPPED, None),
    (f"{DOMAIN}.T5.P3", "node", f"{DOMAIN}.T5", "合成易错点 审题", N_UNCOVERED, ("shenti_cuowu",)),
    (f"{N_SPLIT}.SLIP1", "detail", N_SPLIT, "合成易错点 推理", None, ("tuili_cuowu",)),       # 一个易错点一个错因大类
    (f"{N_SPLIT}.SLIP2", "detail", N_SPLIT, "合成易错点 知识", None, ("zhishi_queshi",)),
)
# 合成里用到的阿鸦错因编码 → 跨科大类（与 docs/mapping/error-taxonomy.md 一致，测试核对）；基础分布的权重
CODE_CATEGORY = {"yunsuan_cuowu": "computation_or_grammar", "gainian_cuowu": "knowledge", "shenti_cuowu": "reading_the_question",
                 "fangfa_cuowu": "thinking_method", "tuili_cuowu": "thinking_method", "zhishi_queshi": "knowledge",
                 "biaoda_cuowu": "expression_convention", "shijian_wenti": "strategy_and_time", "fenlei_yilou": "thinking_method",
                 "jianyan_cuowu": "strategy_and_time", "biaozheng_cuowu": "thinking_method", "xinli_xiguan": "strategy_and_time"}
# 埋入的高频编码在基础分布里也要有一定占比：否则主题基线那一格人数不足门槛，检验只能记“样本不足”（见 lessons 待人确认）。
# 基础分布里也有分类表“待定”的编码（fenlei_yilou、jianyan_cuowu、biaozheng_cuowu）：读入时丢弃，覆盖“只用明确编码”
BASE_CODES = (("yunsuan_cuowu", 0.20), ("gainian_cuowu", 0.15), ("shenti_cuowu", 0.13), ("fangfa_cuowu", 0.09),
              ("tuili_cuowu", 0.07), ("zhishi_queshi", 0.06), ("biaoda_cuowu", 0.07), ("shijian_wenti", 0.07),
              ("fenlei_yilou", 0.06), ("jianyan_cuowu", 0.05), ("biaozheng_cuowu", 0.05))
PLANTED_CODE = {N_UNCOVERED: "shijian_wenti", N_COVERED: "biaoda_cuowu", N_UNMAPPED: "fangfa_cuowu", N_PLAIN: "fenlei_yilou"}
EXCLUDED_PLANTED = (N_PLAIN, "fenlei_yilou")      # 待定编码的高频：不得进任何检验
SENSITIVE_CODE = "xinli_xiguan"                    # 敏感编码：混入少量行，不得进任何计数
# 难度分档反例：猜对 0.3、失误 0.15、难度 ±1.1——学生层面 phi 只有 0.05–0.2（按 phi 判会误报成拆分），但 H 比值 > 1
TIERS_GUESS, TIERS_SLIP, TIERS_DIFFICULTY = 0.3, 0.15, 1.1
PLANTED_RATE = 0.55
EPISODE_RATE = 0.6         # 有错因记录的概率（拆分节点除外：那里由答错的题决定）
LOW_ONLY_RATE = 0.1        # 只有低置信错因的概率
LOW_ONLY_HOT = 0.45        # 难归类节点上的概率
ATTEMPT_RATE = 0.92        # 学生在主题五某节点上有作答的概率（有作答就作答该节点全部题目）
SPLIT_SKILLS = (0.75, 0.35)   # 拆分节点两种能力的掌握率（高正确率一群、低正确率一群）
SPLIT_CODES = ("tuili_cuowu", "zhishi_queshi")   # 失败高 / 低正确率一群时的错因


def _items(node: str) -> list[str]:
    n = {N_SPLIT: 8, N_TIERS: 12}.get(node, 6)
    return [f"CALIB-{node.split('.', 2)[2].replace('.', '')}-Q{j}" for j in range(1, n + 1)]


def map_snapshot() -> dict[str, Any]:
    """knowledge.atoms 快照白名单格式的合成地图（JSON 写法）"""
    nodes: list[dict[str, Any]] = [{"id": DOMAIN, "level": "domain", "name": "合成校准领域"}]
    for t, name in TOPIC_NAMES.items():
        nodes.append({"id": f"{DOMAIN}.T{t}", "level": "topic", "parent_id": DOMAIN, "name": name})
    for nid in [spec.id for spec in NODES] + list(T5):
        topic = nid.rsplit(".", 1)[0]
        nodes.append({"id": nid, "level": "node", "parent_id": topic, "kind": "kp",
                      "name": f"合成节点 {nid.split('.', 2)[2]}", "cognitive_levels": ["apply"]})
    for pid, level, parent, name, _src, _codes in PITFALLS:
        nodes.append({"id": pid, "level": level, "parent_id": parent, "kind": "pitfall", "name": name, "includes": ["合成识别特征"]})
    edges = [{"from_id": f, "to_id": t, "type": "prerequisite", "strength": "strong"} for f, t in HOLDS + NOT_HOLDS]
    edges += [{"from_id": f, "to_id": t, "type": "prerequisite", "strength": "weak"} for f, t in WEAK_TRUE]
    edges += [{"from_id": src, "to_id": pid, "type": "has_pitfall"} for pid, _l, _p, _n, src, _c in PITFALLS if src]
    edges.sort(key=lambda e: (e["from_id"], e["to_id"], e["type"]))
    return {"map_version": MAP_VERSION, "subject": SUBJECT, "status": "published", "standard_year": 2022,
            "nodes": nodes, "edges": edges}


def pitfall_codes() -> dict[str, Any]:
    """教研给出的易错点编码对照（公共层；合成）"""
    return {"format": PITFALL_CODES_FORMAT, "subject": SUBJECT, "map_version": MAP_VERSION,
            "pitfalls": {pid: list(codes) for pid, _l, _p, _n, _s, codes in PITFALLS if codes is not None}}


def truth(seed: int = SEED, size: int = SIZE) -> dict[str, Any]:
    return {"$comment": "合成队列埋入的结构（只含公共层节点 ID 与错因编码）：用于检验召回与误报，不是任何真实数据。",
            "seed": seed, "students": size, "holds": [list(p) for p in HOLDS], "not_holds": [list(p) for p in NOT_HOLDS],
            "hidden": [list(p) for p in HIDDEN], "weak_true": [list(p) for p in WEAK_TRUE],
            "step2_students": SIZE2,
            "pitfall_uncovered": [[N_UNCOVERED, PLANTED_CODE[N_UNCOVERED]]],
            "pitfall_covered": [[N_COVERED, PLANTED_CODE[N_COVERED]]],
            "pitfall_unmapped": [[N_UNMAPPED, PLANTED_CODE[N_UNMAPPED]]],
            "pitfall_excluded": [list(EXCLUDED_PLANTED), [N_PLAIN, SENSITIVE_CODE]],
            "unclassifiable": [N_LOWCONF], "split": [N_SPLIT], "tiers": [N_TIERS]}


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


def _draw_code(rng: random.Random) -> str:
    u, acc = rng.random(), 0.0
    for code, w in BASE_CODES:
        acc += w
        if u < acc:
            return code
    return BASE_CODES[-1][0]


def _base_codes(rng: random.Random) -> set[str]:
    codes = {_draw_code(rng)}
    if rng.random() < 0.3:
        codes.add(_draw_code(rng))
    return codes


def _level(node: str, j: int) -> str:
    """题目的认知层次（公共层；拆分与分档节点的后 4 题记“综合”，其余记“运用”）"""
    return "synthesize" if node in (N_SPLIT, N_TIERS) and j >= len(_items(node)) // 2 else "apply"


def step2_csvs(seed: int = SEED, size: int = SIZE2) -> tuple[str, str]:
    """(作答聚合表, 错因表)：独立随机流，不影响第一步的掌握状态表"""
    rng = random.Random(seed * 7919 + 2)
    attempts: list[tuple[str, str, str, str, str]] = []
    errors: list[tuple[str, str, str, str, str]] = []
    jitter = {it: (rng.random() - 0.5) * 0.06 for node in T5 for it in _items(node)}   # 题目难度的小扰动（与学生无关）
    for i in range(size):
        pid = pseudonym(seed, i)
        theta = rng.gauss(0.0, 1.0)
        skills = tuple(rng.random() < p for p in SPLIT_SKILLS)
        for node in T5:
            items = _items(node)
            failed_groups: set[int] = set()
            if rng.random() < ATTEMPT_RATE:
                for j, it in enumerate(items):
                    if node == N_SPLIT:          # 前 4 题考能力 0（高正确率一群），后 4 题考能力 1
                        g = 0 if j < 4 else 1
                        p = (0.9 if skills[g] else 0.1) + jitter[it]
                    elif node == N_TIERS:        # 同一能力的难易两档（各 6 题）：难度差大，有猜对与失误
                        g = 0 if j < 6 else 1
                        b = -TIERS_DIFFICULTY if g == 0 else TIERS_DIFFICULTY
                        p = TIERS_GUESS + (1 - TIERS_GUESS - TIERS_SLIP) * _sigmoid(1.6 * (theta - b)) + jitter[it]
                    else:                        # 难度连续分布
                        g = 0
                        p = _sigmoid(theta - (-0.5 + 0.2 * j)) + jitter[it]
                    ok = rng.random() < p
                    if not ok:
                        failed_groups.add(g)
                    attempts.append((pid, it, node, "1" if ok else "0", _level(node, j)))
            if node == N_SPLIT:
                has_episode = bool(failed_groups)
            else:
                has_episode = rng.random() < EPISODE_RATE
            if not has_episode:
                continue
            if rng.random() < (LOW_ONLY_HOT if node == N_LOWCONF else LOW_ONLY_RATE):
                for _ in range(1 + (rng.random() < 0.5)):
                    c = _draw_code(rng)
                    errors.append((pid, node, c, CODE_CATEGORY[c], f"{0.2 + 0.35 * rng.random():.3f}"))
                continue
            if node == N_SPLIT:
                codes = {SPLIT_CODES[g] for g in sorted(failed_groups) if rng.random() < 0.75}
                if not codes or rng.random() < 0.4:
                    codes |= _base_codes(rng)
            else:
                codes = _base_codes(rng)
                if node in PLANTED_CODE and rng.random() < PLANTED_RATE:
                    codes.add(PLANTED_CODE[node])
            if node == N_PLAIN and rng.random() < 0.1:
                codes.add(SENSITIVE_CODE)        # 敏感编码（分类表“待定（敏感）”）：读入时丢弃
            for c in sorted(codes):
                errors.append((pid, node, c, CODE_CATEGORY[c], f"{0.65 + 0.34 * rng.random():.3f}"))
            if rng.random() < 0.2:               # 混入一条低置信行：这名学生在这个节点上仍有高置信编码，不算难归类
                c = _draw_code(rng)
                errors.append((pid, node, c, CODE_CATEGORY[c], f"{0.2 + 0.35 * rng.random():.3f}"))
    attempts.sort()
    errors.sort()
    meta = f"synthetic=true subject={SUBJECT} map_version={MAP_VERSION}"
    a = "\n".join([f"# format={ATTEMPTS_FORMAT} {meta}", "pseudonym,item_id,node_id,correct,cognitive_level",
                   *(",".join(r) for r in attempts)])
    e = "\n".join([f"# format={ERRORS_FORMAT} {meta}", "pseudonym,node_id,subject_category,cross_subject_category,confidence",
                   *(",".join(r) for r in errors)])
    return a + "\n", e + "\n"


def render_inputs(seed: int = SEED, size: int = SIZE) -> dict[str, str]:
    dump = lambda d: json.dumps(d, ensure_ascii=False, sort_keys=True, indent=2) + "\n"  # noqa: E731
    attempts, errors = step2_csvs(seed)
    return {"map-snapshot.json": dump(map_snapshot()), "mastery.csv": mastery_csv(seed, size), "truth.json": dump(truth(seed, size)),
            "attempts.csv": attempts, "errors.csv": errors, "pitfall-codes.json": dump(pitfall_codes())}


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
            "false_hidden": len(cand - hidden), **evaluate_step2(report, tr)}


def evaluate_step2(report: dict[str, Any], tr: dict[str, Any]) -> dict[str, int]:
    """第二步：未覆盖的高频错因、难归类聚集、应拆分节点的召回与误报；已覆盖与对照缺失的结论；难度分档不被报成拆分"""
    tests = report["pitfall"]["tests"] if report["pitfall"]["provided"] else []
    by = lambda c: {(t["node"], t["code"]) for t in tests if t["conclusion"] == c}  # noqa: E731
    new, covered, unmapped = by("新增易错点候选"), by("已有易错点覆盖"), by("易错点对照缺失")
    planted = {tuple(p) for p in tr["pitfall_uncovered"]}
    low = ({t["node"] for t in report["unclassifiable"]["nodes"] if t["conclusion"] == "错因难归类信号"}
           if report["unclassifiable"]["provided"] else set())
    split = ({t["node"] for t in report["node_split"]["nodes"] if t["conclusion"] == "拆分节点候选"}
             if report["node_split"]["provided"] else set())
    tiers = ({t["node"] for t in report["node_split"]["nodes"] if t["conclusion"] == "难度分档（不建议拆分）"}
             if report["node_split"]["provided"] else set())
    excluded = {c for _n, c in tr["pitfall_excluded"]}
    mentioned = {t["code"] for t in tests} | {s.split(" · ", 1)[1] for s in report["pitfall"].get("below_tests", [])}
    return {"pitfall_found": len(new & planted), "pitfall_planted": len(planted), "false_pitfall": len(new - planted),
            "excluded_tested": len(mentioned & excluded), "tiers_confirmed": len(tiers & set(tr["tiers"])),
            "covered_confirmed": len(covered & {tuple(p) for p in tr["pitfall_covered"]}),
            "unmapped_confirmed": len(unmapped & {tuple(p) for p in tr["pitfall_unmapped"]}),
            "unclassifiable_found": len(low & set(tr["unclassifiable"])), "unclassifiable_planted": len(tr["unclassifiable"]),
            "false_unclassifiable": len(low - set(tr["unclassifiable"])),
            "split_found": len(split & set(tr["split"])), "split_planted": len(tr["split"]),
            "false_split": len(split - set(tr["split"])), "tiers_as_split": len(split & set(tr["tiers"]))}
