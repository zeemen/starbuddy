"""盲测编号：供应商随机编号（VA、VB、VC…）与条目随机编号（供应商看不到样本编号与标签）。

编号种子由执行人持有，不写进报告；报告只记编号表的 sha256，揭盲时用来核对。
"""
from __future__ import annotations

import hashlib
import json
import random
from typing import Mapping, Sequence


def assign_codes(vendor_ids: Sequence[str], seed: str) -> dict[str, str]:
    """供应商 ID → 盲测编号。先排序再按种子洗牌，与传入顺序无关。"""
    ids = sorted(vendor_ids)
    if len(set(ids)) != len(ids) or not ids:
        raise ValueError("供应商 ID 为空或重复")
    if len(ids) > 26:
        raise ValueError("最多 26 家")
    rng = random.Random(f"codes:{seed}")
    rng.shuffle(ids)
    return {vid: f"V{chr(ord('A') + k)}" for k, vid in enumerate(ids)}


def item_ids(sample_ids: Sequence[str], seed: str) -> dict[str, str]:
    """样本编号 → 提交给供应商的条目编号（随机，不带任何标签信息）"""
    rng = random.Random(f"items:{seed}")
    out: dict[str, str] = {}
    used: set[str] = set()
    for sid in sorted(sample_ids):
        tok = f"I-{rng.getrandbits(48):012x}"
        while tok in used:
            tok = f"I-{rng.getrandbits(48):012x}"
        used.add(tok)
        out[sid] = tok
    return out


def submission_order(sample_ids: Sequence[str], seed: str, code: str) -> list[str]:
    """每家同一批音频，但提交顺序各自随机（避免顺序效应）"""
    order = sorted(sample_ids)
    random.Random(f"order:{seed}:{code}").shuffle(order)
    return order


def key_digest(codes: Mapping[str, str]) -> str:
    """编号表（供应商 ID → 编号）的 sha256。报告只记这个值。"""
    return hashlib.sha256(json.dumps(dict(sorted(codes.items())), ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
