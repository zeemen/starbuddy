"""PLT-20 --profile peak2x 的配置（config/peak2x.yaml）：1 倍峰值并发与阶段时长（合成占位，待人确认）。纯函数，不碰网络。"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .arrivals import Stage, peak2x_stages
from .scenarios import parse_journeys

CONFIG = Path(__file__).resolve().parents[1] / "config"
FORMAT = "loadtest-peak2x/1"
KEYS = frozenset({"format", "confirmed", "basis", "peak_concurrency", "ramp_seconds", "hold_seconds", "journeys"})


@dataclass(frozen=True)
class PeakConfig:
    peak_concurrency: int
    ramp_s: float
    hold_s: float
    confirmed: bool
    basis: str
    journeys: tuple[str, ...]

    def stages(self, peak_override: int | None = None) -> tuple[Stage, ...]:
        return peak2x_stages(self.peak_concurrency if peak_override is None else peak_override, self.ramp_s, self.hold_s)


def _num(v, what: str) -> float:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or v <= 0:
        raise ValueError(f"{what} must be a positive number")
    return float(v)


def parse(doc: dict) -> PeakConfig:
    if not isinstance(doc, dict) or doc.get("format") != FORMAT:
        raise ValueError(f"peak config format must be {FORMAT}")
    extra = set(doc) - KEYS
    if extra:
        raise ValueError(f"unknown keys in peak config: {sorted(extra)}")
    peak = doc.get("peak_concurrency")
    if isinstance(peak, bool) or not isinstance(peak, int) or peak < 1:
        raise ValueError("peak_concurrency must be a positive integer")
    if not isinstance(doc.get("confirmed"), bool):
        raise ValueError("confirmed must be true or false")
    journeys = doc.get("journeys") or []
    if not isinstance(journeys, list):
        raise ValueError("journeys must be a list")
    return PeakConfig(peak, _num(doc.get("ramp_seconds"), "ramp_seconds"), _num(doc.get("hold_seconds"), "hold_seconds"),
                      doc["confirmed"], str(doc.get("basis") or ""), parse_journeys(journeys))


def load(path: Path | None = None) -> PeakConfig:
    import yaml

    return parse(yaml.safe_load((path or CONFIG / "peak2x.yaml").read_text(encoding="utf-8")))


def note(cfg: PeakConfig, peak_override: int | None) -> str:
    """报告里的一句：1 倍取值与来源（未确认时明写“占位”）"""
    if peak_override is not None:
        return f"1 倍峰值并发 = {peak_override}（命令行覆盖，本机对照占位，不是容量目标）"
    src = "配置（已确认）" if cfg.confirmed else "配置占位，待确认"
    return f"1 倍峰值并发 = {cfg.peak_concurrency}（{src}；{cfg.basis or '无来源说明'}）"
