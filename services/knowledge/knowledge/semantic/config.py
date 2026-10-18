"""语义体检的阻断 / 提示分级（KGE-11 范围 1 末条：每条检查有级别，集中在这里）。

**全部待教研确认**（spec 待人确认 #2）：下面的缺省值是构建方按“会误导学生的从严、只是写法问题的提示”起的草稿，
教研签发正式分级前不得据此改审核级别。可用 `--levels <文件>`（kge-semantic-levels/1）整体或逐条覆盖；
文件里出现未登记的规则键即拒收（fail closed），不静默忽略。

键 = <检查类型>.<规则>；适龄文风可逐条覆盖：age_style.<文风词表里的规则 ID>（没有逐条覆盖时用 age_style.default）。
"""
from __future__ import annotations

import json
import re
from collections.abc import Mapping
from pathlib import Path
from types import MappingProxyType

LEVELS_FORMAT = "kge-semantic-levels/1"
LEVEL_VALUES = ("block", "warn")

# 待教研确认（spec 待人确认 #2）
DEFAULT_LEVELS: Mapping[str, str] = MappingProxyType({
    # 与原子冲突：页面里的数值、公式、结论与所引原子不符，会把错的东西教给学生
    "atom_conflict.formula": "block",          # 叙述段的等式与所引节点原子里的同一等式（左边 SymPy 等价）右边不等价
    "atom_conflict.region_number": "block",    # 引用地区投影的段落里，权重、占比、年份与原子不符
    "atom_conflict.curriculum_level": "block",  # 引用课标条目的段落里，写的要求层次（知道 / 理解 / 运用 / 综合）与条目不符
    "atom_conflict.page_range": "block",       # 引用教材投影的段落里，页码超出投影的页码范围
    "atom_conflict.excluded_scope": "block",   # 把节点“不包括”的内容说成属于本节点
    # 跨页矛盾：前置关系方向
    "prerequisite_direction.reversed": "block",       # 与骨架里的前置边方向相反
    "prerequisite_direction.pages_disagree": "block",  # 骨架里没有这条边，两张相邻页的说法又相反
    "prerequisite_direction.no_edge": "warn",         # 骨架里没有这条前置边（可能是骨架缺边，也可能是页面说错）
    # 跨页矛盾：相邻页同一术语写法不一致
    "term_consistency.variant": "warn",
    # 版本差异误判：版次专属表述出现在未标该版次的段落
    "edition_scope.unmarked": "block",
    "edition_scope.mismatch": "block",
    # 超纲
    "out_of_scope.term": "block",
    "out_of_scope.no_vocab": "warn",  # 没有给超纲词表：本项跳过
    "out_of_scope.synthetic_vocab": "warn",  # 超纲词表是合成样例：结论只演示规则，不代表课标范围
    # 适龄文风（文风词表是合成占位）
    "age_style.default": "warn",
    "age_style.no_lexicon": "warn",  # 没有给文风词表：本项跳过
    # 角色语言混入（复用人设词表；程序层逐行字面比对之外，按段落归一化后再比）
    "persona_mix.normalized": "block",
    # 比对范围：编入清单或 --affected-pages 里的受影响页不在页面目录里（清单与页面目录对不上，不静默忽略）
    "affected_scope.missing": "block",
})


def levels_config(levels: Mapping[str, str]) -> dict[str, object]:
    """报告 config 里的分级说明：是否全是缺省、覆盖了哪些键"""
    overrides = {k: v for k, v in sorted(levels.items()) if DEFAULT_LEVELS.get(k) != v}
    return {"levels_default": not overrides, "levels_overrides": overrides}
_STYLE_KEY = re.compile(r"^age_style\.[A-Za-z0-9_-]{1,64}$")


class LevelsError(ValueError):
    pass


def check_levels(levels: Mapping[str, str], style_ids: frozenset[str] = frozenset()) -> None:
    for k, v in levels.items():
        if k not in DEFAULT_LEVELS and not (_STYLE_KEY.match(k) and k.split(".", 1)[1] in style_ids):
            raise LevelsError(f"分级配置里有未登记的规则键：{k}")
        if v not in LEVEL_VALUES:
            raise LevelsError(f"规则 {k} 的级别只能是 block / warn")


def load_levels(path: str | Path | None, style_ids: frozenset[str] = frozenset()) -> Mapping[str, str]:
    """缺省分级叠加覆盖文件。覆盖文件格式：{"format": "kge-semantic-levels/1", "levels": {规则键: block | warn}}"""
    levels = dict(DEFAULT_LEVELS)
    if path is not None:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(raw, dict) or raw.get("format") != LEVELS_FORMAT or not isinstance(raw.get("levels"), dict):
            raise LevelsError(f"分级配置的格式必须是 {LEVELS_FORMAT}，levels 为对象")
        if set(raw) - {"format", "levels", "note"}:
            raise LevelsError("分级配置只收 format、levels、note")
        check_levels(raw["levels"], style_ids)
        levels.update(raw["levels"])
    check_levels(levels, style_ids)
    return MappingProxyType(levels)


def level_of(levels: Mapping[str, str], key: str) -> str:
    if key in levels:
        return levels[key]
    if key.startswith("age_style."):
        return levels["age_style.default"]
    raise KeyError(key)
