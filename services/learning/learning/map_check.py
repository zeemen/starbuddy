"""地图版本交叉核对（PLT-25，W13 T13；specs/plt25-map-version-check）。

同一条证据的地图版本有两个来源：平台按请求的内容版本查到的绑定地图（platform.curriculum_version.map_version_id → kge.map_version），
与适配器在 source_version 里自报的 `graph:<学科>-map@x.y.z` 段（插件契约 v1.0 第 41 行）。本模块只做比对，不读库：

  absent    返回里没有 graph: 段                                      → 照常
  unbound   有 graph: 段，但平台这边没有可比的地图版本（内容版本未绑定）→ 照常（待人确认 #2）
  mismatch  名称不是 <本科>-map、有两个以上 graph: 段、或版本号不同      → grade / diagnose 拒收（#1）；plan 只计数（#3）
  match     版本号与平台的地图版本相同                                  → 照常
"""
from __future__ import annotations

from collections.abc import Collection

OUTCOMES = ("match", "mismatch", "unbound", "absent")


def graph_segments(source_version: object) -> list[str]:
    """source_version 里全部 graph: 段的“名称@版本”（契约已校验整体格式；这里只拆段）。"""
    if not isinstance(source_version, str):
        return []
    return [seg[len("graph:"):] for seg in source_version.split(";") if seg.startswith("graph:")]


def classify(subject: str, source_version: object, platform_versions: Collection[str] | None) -> str:
    """platform_versions：平台这边可比的地图版本号（grade / diagnose 是绑定的那一个；plan 是该科已发布的全部）；None 或空 = 没有可比的。"""
    segs = graph_segments(source_version)
    if not segs:
        return "absent"
    if len(segs) > 1:
        return "mismatch"
    name, _, version = segs[0].partition("@")
    if name != f"{subject}-map" or not version:
        return "mismatch"
    if not platform_versions:
        return "unbound"
    return "match" if version in platform_versions else "mismatch"
