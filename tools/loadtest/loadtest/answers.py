"""压测用的合成作答（规则 8）：数学取阿鸦合成内容包的填空题，道法、语文用 C+D 合成队列（tools/e2e-cohort）的句库拼成。

驱动器与假供应商用同一个函数、同一个种子生成同一批作答：假供应商据此在提示里认出已登记的合成作答，给出结构化判定
（与 e2e_cohort.scripted.ScriptedModel 同一套规则）；判分仍由适配器后处理与 Rubric 决定（规则 3）。
"""
from __future__ import annotations

import random
from dataclasses import dataclass

from e2e_cohort.cohort import AYA_BANK, ESSAYS, ZHIXING_ITEMS, essay_text, zhixing_answer

QUALITIES = ("core", "accepted", "colloquial", "mixed")


@dataclass(frozen=True)
class Answers:
    aya_item: str
    aya_text: str
    zhixing_item: str
    zhixing_text: str
    wenli_item: str
    wenli_text: str
    pattern: str  # 给假供应商的作文档位：all_correct 类给 L3，其余 L2


def for_student(seed: int, slot: int, zx_points: dict[str, list[dict]]) -> Answers:
    rng = random.Random(f"{seed}:answers:{slot}")
    area = rng.choice(sorted(AYA_BANK))
    item, right, trap = rng.choice(AYA_BANK[area])
    correct = rng.random() < 0.6
    zx_item = rng.choice(ZHIXING_ITEMS)
    label = f"LT{slot:05d}"
    zx = zhixing_answer(label, zx_item, rng.choice(QUALITIES), zx_points[zx_item])
    essay = essay_text(label, rng)
    return Answers(item, right if correct else trap, zx_item, zx, rng.choice(ESSAYS), essay,
                   "all_correct" if correct else "systematic_gap")
