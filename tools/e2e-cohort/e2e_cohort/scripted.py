"""假供应商的“模型输出”（只在 test 环境的模型路由里用，做法同 test_e2e_zhixing / test_e2e_wenli）。

适配器经学习服务内部路由接口调模型；这里的假供应商在最后一条 user 消息里找到已登记的合成作答原文，
按合成规则给出结构化判定（只有档位、分值、证据位置，没有自由文本）。判分仍由适配器的后处理与 Rubric 决定（规则 3）。
- 道法：按内容包采分点的关键词族——学科术语 → full、可接受表述 → partial（1 分）、其余 → miss；
- 语文：四个模型维按作答模式给档（全对类 L3、其余 L2），证据位置指向作文里的锚点片段。
"""
from __future__ import annotations

import json

from .cohort import ESSAY_ANCHORS


class ScriptedModel:
    def __init__(self, zhixing_items: dict[str, dict]) -> None:
        self.zhixing_items = zhixing_items
        self.texts: dict[str, tuple[str, str, str]] = {}  # 原文 → (学科, 题目, 模式)
        self.calls = 0

    def register(self, subject: str, item: str, text: str, pattern: str) -> None:
        self.texts[text] = (subject, item, pattern)

    def reply(self, user_message: str) -> str:
        self.calls += 1
        hits = [t for t in self.texts if t in user_message]
        if not hits:
            return json.dumps({"points": []})  # 不认识的作答：输出不合格，适配器整题转复核
        text = max(hits, key=len)
        subject, item, pattern = self.texts[text]
        return self._zhixing(item, text) if subject == "zhixing" else self._essay(text, pattern)

    def _zhixing(self, item_id: str, answer: str) -> str:
        out = []
        for p in self.zhixing_items[item_id]["points"]:
            fam, max_score = p["keyword_family"], p["score"]
            loss = (p.get("common_errors") or [{"type": "shenti_cuo"}])[0]["type"]
            core = next((w for w in fam.get("core", []) if w in answer), None)
            accepted = next((w for w in fam.get("accepted", []) if w in answer), None)
            if core:
                s = answer.index(core)
                out.append({"point_id": p["point_id"], "judgment": "full", "score_awarded": max_score,
                            "evidence_start": s, "evidence_end": s + len(core), "attribution": "none"})
            elif accepted and max_score > 1:
                s = answer.index(accepted)
                out.append({"point_id": p["point_id"], "judgment": "partial", "score_awarded": 1,
                            "evidence_start": s, "evidence_end": s + len(accepted), "attribution": loss})
            else:
                out.append({"point_id": p["point_id"], "judgment": "miss", "score_awarded": 0,
                            "evidence_start": None, "evidence_end": None, "attribution": loss})
        return json.dumps({"points": out}, ensure_ascii=False)

    def _essay(self, text: str, pattern: str) -> str:
        level = "L3" if pattern in ("all_correct", "hint_dependent") else "L2"
        dims = []
        for d, frag in ESSAY_ANCHORS.items():
            s = text.index(frag)
            dims.append({"dimension_id": d, "level": level, "evidence_start": s, "evidence_end": s + len(frag),
                         "uncertainty_levels": {"low": "L2" if level == "L3" else "L1", "high": level}})
        return json.dumps({"dimensions": dims}, ensure_ascii=False)
