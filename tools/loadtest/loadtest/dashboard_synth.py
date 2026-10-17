"""PLT-17 成本仪表盘的合成队列（spec 范围 2）：固定种子，按档位与学科生成消耗聚合行（含未归因份额）与模型调用记录。

全部合成（规则 8）：伪名 syn- 开头、synthetic=true；调用记录不含模型标识。分布形状只为检验口径与报表，不代表任何试点结论：
- 各档人数不同、不恰在取整边界上，Max 档故意少于 20 人（检验“样本不足”）；
- 每名学生一个月：使用强度 u ~ Beta（各档参数不同）；基本档在读 1 科，Plus / Pro 2–3 科，Max 3 科；
- 学科可归因的资源（批改、诊断）分到在读科目；讲解分钟、语音、上传、存储、深度报告记为“未归因”；
- 调用记录：各科教学对话与守卫、道法主观题、语文作文，另有无科目、价格表里没有的上传识别（T3）；
  约 3% 不可用、1% 被拒；成功调用里一部分有重试（attempts > 1）、一部分由备选供应商完成（fallback）。
"""
from __future__ import annotations

import json
import random
from pathlib import Path

from .cost import load_quotas
from .dashboard import USAGE_FORMAT

SEED = 20261017
MONTH = "2026-09"
QUOTA_CONFIG = "v12-pilot@1"
TIER_SIZES = {"basic": 87, "plus": 63, "pro": 44, "max": 14}   # 不恰在取整边界上（安全审查 B1）
USE_BETA = {"basic": (2.0, 3.0), "plus": (2.0, 2.5), "pro": (2.2, 2.0), "max": (3.0, 1.5)}
N_SUBJECTS = {"basic": (1,), "plus": (2, 3), "pro": (2, 3), "max": (3,)}
SUBJECT_WEIGHT = {"aya": 0.45, "wenli": 0.30, "zhixing": 0.25}
CALLS = {("aya", "T1"): 120, ("aya", "T9"): 120, ("wenli", "T1"): 80, ("wenli", "T9"): 80, ("zhixing", "T1"): 70,
         ("zhixing", "T9"): 70, ("zhixing", "T2"): 60, ("wenli", "T4"): 40, (None, "T3"): 20}
TOKENS = {"T1": (1800, 200, 250, 40), "T9": (600, 60, 20, 4), "T2": (1500, 200, 400, 60), "T4": (2500, 300, 600, 80),
          "T3": (1200, 150, 150, 30)}
P_UNAVAILABLE, P_REJECTED, P_RETRY, P_FALLBACK = 0.03, 0.01, 0.06, 0.04


def _pseudonym(rng: random.Random, seen: set[str]) -> str:
    while True:
        p = "syn-" + "".join(rng.choice("abcdefghijklmnopqrstuvwxyz") for _ in range(12))
        if p not in seen:
            seen.add(p)
            return p


def _pick_subjects(rng: random.Random, k: int) -> list[str]:
    pool = dict(SUBJECT_WEIGHT)
    out = []
    for _ in range(k):
        x, acc = rng.random() * sum(pool.values()), 0.0
        for s in sorted(pool):
            acc += pool[s]
            if x <= acc:
                break
        out.append(s)
        del pool[s]
    return sorted(out)


def usage_rows(seed: int = SEED) -> list[tuple[str, str, str, str, str, int]]:
    rng = random.Random(seed)
    q = load_quotas(QUOTA_CONFIG)
    seen: set[str] = set()
    rows = []
    for tier in ("basic", "plus", "pro", "max"):
        a, b = USE_BETA[tier]
        Q = q[tier]
        for _ in range(TIER_SIZES[tier]):
            pid = _pseudonym(rng, seen)
            u = rng.betavariate(a, b)
            subjects = _pick_subjects(rng, rng.choice(N_SUBJECTS[tier]))
            tot = sum(SUBJECT_WEIGHT[s] for s in subjects)
            for s in subjects:
                w = SUBJECT_WEIGHT[s] / tot
                vals = {"mistake_analysis": max(1, min(int(Q["mistake_analysis"]), round(Q["mistake_analysis"] * u * w))),
                        "diagnosis_full": min(int(Q["diagnosis_full"]), round(Q["diagnosis_full"] * u * w * rng.uniform(0.5, 1.5))),
                        "diagnosis_screen": rng.randint(0, 3)}
                rows += [(pid, MONTH, tier, s, r, v) for r, v in vals.items() if v > 0]
            upload = round(Q["upload"] * u * rng.uniform(0.2, 0.6))
            un = {"ai_active_time": max(1, min(int(Q["ai_active_time"]), round(Q["ai_active_time"] * u * rng.uniform(0.7, 1.0)))),
                  "voice_realtime": min(int(Q["voice_realtime"]), round(Q["voice_realtime"] * u * rng.uniform(0.1, 0.8))),
                  "upload": upload,
                  "storage_bytes": min(int(Q["storage_bytes"]), upload * rng.randint(200_000, 2_000_000)),
                  "deep_report": rng.randint(0, int(Q["deep_report"]))}
            rows += [(pid, MONTH, tier, "unattributed", r, v) for r, v in un.items() if v > 0]
    return sorted(rows)


def call_records(seed: int = SEED) -> list[dict]:
    rng = random.Random(seed + 1)
    out = []
    for (subject, task), n in CALLS.items():
        mi, si, mo, so = TOKENS[task]
        for _ in range(n):
            x = rng.random()
            rec = {"data_class": "L2", "elapsed_ms": rng.randint(80, 2400), "subject": subject, "task": task}
            if x < P_UNAVAILABLE:
                rec.update(outcome="unavailable", attempts=rng.randint(2, 4), fallback=False, input_tokens=0, output_tokens=0)
            elif x < P_UNAVAILABLE + P_REJECTED:
                rec.update(outcome="rejected", attempts=0, fallback=False, input_tokens=0, output_tokens=0)
            else:
                y = rng.random()
                fallback = y < P_FALLBACK
                attempts = rng.randint(2, 3) if fallback else (2 if y < P_FALLBACK + P_RETRY else 1)
                rec.update(outcome="ok", attempts=attempts, fallback=fallback,
                           input_tokens=max(1, round(rng.gauss(mi, si))), output_tokens=max(1, round(rng.gauss(mo, so))))
            out.append(rec)
    return out


def render(seed: int = SEED) -> dict[str, str]:
    head = f"# format={USAGE_FORMAT} synthetic=true quota_config={QUOTA_CONFIG}\npseudonym,month,tier,subject,resource,quantity\n"
    usage = head + "".join(",".join(map(str, r)) + "\n" for r in usage_rows(seed))
    records = "".join(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n" for r in call_records(seed))
    return {"usage.csv": usage, "call-records.jsonl": records}


def run(out: Path | None, seed: int = SEED) -> Path:
    from . import outdir

    d = outdir.prepare(out, "starbuddy-cost-dashboard-synth")
    for name, text in render(seed).items():
        outdir.write_text(d / name, text)
    return d
