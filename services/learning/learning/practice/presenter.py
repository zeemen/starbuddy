"""再练题面与判分版本的提供者（spec 第 4 节“题面”）。

题面复用诊断的出口白名单（diagnosis/presenter._clean：只有 format、stem、options 的键与文字），不含答案、解析、节点、错因。
判分请求要带 rubric_version，题面提供者一并给出（真实题库接入后随 present_item 契约由适配器内容包给，待人确认）。
判分方式（grading）也由题面提供者登记：选题只留验证器或确定性 Rubric 判分的客观题（审查 S7）。平台题目表与契约里还没有
“判分方式”字段，合成题面在夹具里逐题标注；真实来源随 present_item 契约另定（待人确认）。
本任务只有测试环境的合成实现（fixtures/synthetic-items.json）；非 test 环境没有真实题面提供者，next 与 answer 一律 501。
"""
from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Protocol

from ..diagnosis.presenter import ItemView, _clean
from ..diagnosis.source import require_test_env

FIXTURE = Path(__file__).with_name("fixtures") / "synthetic-items.json"
ITEM_KEYS = frozenset({"subject", "item_id", "format", "stem", "options", "rubric_version", "grading"})
VERSIONED = re.compile(r"^[a-z0-9][a-z0-9\-]{0,47}@\d+\.\d+\.\d+$")
# 判分方式（审查 S7）：validator = 验证器；deterministic_rubric = 确定性 Rubric；model_rubric = 模型按 Rubric 判（主观题，不进再练）
GRADING = frozenset({"validator", "deterministic_rubric", "model_rubric"})


class PracticePresenter(Protocol):
    def present(self, subject: str, item_id: str) -> ItemView | None: ...

    def rubric_version(self, subject: str, item_id: str) -> str | None: ...

    def grading(self, subject: str, item_id: str) -> str | None:
        """这道题登记的判分方式（GRADING 之一）；不知道为空（不知道的不当客观题出）。"""
        ...


@lru_cache(maxsize=2)
def load_items(path: str = str(FIXTURE)) -> dict:
    doc = json.loads(Path(path).read_text(encoding="utf-8"))
    if doc.get("format") != "starbuddy-practice-items/1":
        raise RuntimeError("再练合成题面格式不对")
    for it in doc["items"]:
        if set(it) - ITEM_KEYS or not VERSIONED.fullmatch(it.get("rubric_version", "")) or it.get("grading") not in GRADING:
            raise RuntimeError("再练合成题面只能有学科、题号、题型、题面、选项、判分版本、判分方式（不能带答案；判分方式必填）")
    return doc


class SyntheticPracticePresenter:
    """测试环境的合成题面。非 test 环境构造即失败（配置层另有一道）。"""

    def __init__(self, env: str, doc: dict | None = None) -> None:
        require_test_env(env, "再练合成题面")
        doc = doc or load_items()
        self._items = {(it["subject"], it["item_id"]): it for it in doc["items"]}

    def present(self, subject: str, item_id: str) -> ItemView | None:
        it = self._items.get((subject, item_id))
        if it is None:
            return None
        view = {"format": it["format"], "stem": it["stem"]}
        if "options" in it:
            view["options"] = it["options"]
        return _clean(view)

    def rubric_version(self, subject: str, item_id: str) -> str | None:
        it = self._items.get((subject, item_id))
        return None if it is None else it["rubric_version"]

    def grading(self, subject: str, item_id: str) -> str | None:
        it = self._items.get((subject, item_id))
        return None if it is None else it["grading"]
