"""输入清单（format: voice-blind-manifest/1）：音频引用、参考文本、语言、口音与噪声标签、可选的专家音素标注。

清单只写音频的**引用与元数据**，框架不读、不存音频字节。校验失败的消息只含字段名与样本编号，不含参考文本。
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from .metrics import PhoneJudgement

FORMAT = "voice-blind-manifest/1"
LANGS = ("zh", "en")
ACCENTS = ("standard", "nanjing", "other")     # 普通话 / 标准口音、带南京口音、其他口音
NOISES = ("quiet", "classroom", "outdoor")     # 安静、教室环境、户外
TASKS = ("read", "dialogue")                   # 朗读、对话
SAMPLE_RATES = (8000, 16000, 22050, 24000, 32000, 44100, 48000)
MAX_DURATION_S = 600.0
MAX_REF_CHARS = 2000
SAMPLE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\-]{0,63}$")
# 音频引用只允许两种不透明键：synthetic://（程序生成的静音或合成波形元数据）与 store://（执行环境里的境内存储键）。
# 不允许 http(s)、file:// 与本机绝对路径：框架不联网，也不从仓库读音频。
AUDIO_REF = re.compile(r"^(synthetic|store)://[A-Za-z0-9_\-][A-Za-z0-9_\-./]{0,255}$")
PHONE = re.compile(r"^[A-Za-z0-9_:+\-]{1,16}$")
SAMPLE_KEYS = {"id", "audio", "reference", "lang", "accent", "noise", "task", "expert"}
AUDIO_KEYS = {"ref", "duration_s", "sample_rate_hz", "channels"}
EXPERT_KEYS = {"phones", "errors", "sentence_score"}
# 个人信息扫描（参考文本只能是朗读 / 对话脚本）：手机号、身份证号、邮箱
PII = {
    "手机号": re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)"),
    "身份证号": re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)"),
    "邮箱": re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}"),
}


class ManifestError(ValueError):
    """清单格式错误（消息只含字段名与样本编号）"""


@dataclass(frozen=True)
class Audio:
    ref: str
    duration_s: float
    sample_rate_hz: int
    channels: int = 1


@dataclass(frozen=True)
class Expert:
    phones: tuple[PhoneJudgement, ...]
    sentence_score: float


@dataclass(frozen=True)
class Sample:
    id: str
    audio: Audio
    reference: str
    lang: str
    accent: str
    noise: str
    task: str
    expert: Expert | None = None


@dataclass(frozen=True)
class Manifest:
    synthetic: bool
    samples: tuple[Sample, ...]
    digest: str


def _audio(sid: str, a: Any, synthetic: bool) -> Audio:
    if not isinstance(a, dict) or set(a) - AUDIO_KEYS or not {"ref", "duration_s", "sample_rate_hz"} <= set(a):
        raise ManifestError(f"{sid}: audio 必须含 ref、duration_s、sample_rate_hz（可选 channels），不得有其他字段")
    ref = a["ref"]
    if not isinstance(ref, str) or not AUDIO_REF.fullmatch(ref) or ".." in ref:
        raise ManifestError(f"{sid}: audio.ref 只能是 synthetic:// 或 store:// 键")
    if synthetic != ref.startswith("synthetic://"):
        raise ManifestError(f"{sid}: 合成清单只能用 synthetic:// 引用，非合成清单不得用 synthetic://")
    dur = a["duration_s"]
    if isinstance(dur, bool) or not isinstance(dur, (int, float)) or not 0 < dur <= MAX_DURATION_S:
        raise ManifestError(f"{sid}: audio.duration_s 必须在 (0, {MAX_DURATION_S:g}] 秒")
    if a["sample_rate_hz"] not in SAMPLE_RATES or isinstance(a["sample_rate_hz"], bool):
        raise ManifestError(f"{sid}: audio.sample_rate_hz 不在允许值里")
    ch = a.get("channels", 1)
    if ch not in (1, 2) or isinstance(ch, bool):
        raise ManifestError(f"{sid}: audio.channels 只能是 1 或 2")
    return Audio(ref, float(dur), int(a["sample_rate_hz"]), int(ch))


def _expert(sid: str, e: Any) -> Expert:
    if not isinstance(e, dict) or set(e) != EXPERT_KEYS:
        raise ManifestError(f"{sid}: expert 必须恰好含 phones、errors、sentence_score")
    phones, errors, score = e["phones"], e["errors"], e["sentence_score"]
    if not isinstance(phones, list) or not phones or not all(isinstance(p, str) and PHONE.fullmatch(p) for p in phones):
        raise ManifestError(f"{sid}: expert.phones 必须是非空的音素标签数组")
    if (not isinstance(errors, list) or len(set(errors)) != len(errors)
            or not all(isinstance(k, int) and not isinstance(k, bool) and 0 <= k < len(phones) for k in errors)):
        raise ManifestError(f"{sid}: expert.errors 必须是不重复的音素下标")
    if isinstance(score, bool) or not isinstance(score, (int, float)) or not 0 <= score <= 100:
        raise ManifestError(f"{sid}: expert.sentence_score 必须在 [0, 100]")
    bad = set(errors)
    return Expert(tuple(PhoneJudgement(p, k not in bad) for k, p in enumerate(phones)), float(score))


def parse(doc: Any, digest: str = "") -> Manifest:
    if not isinstance(doc, dict) or set(doc) != {"format", "synthetic", "samples"} or doc.get("format") != FORMAT:
        raise ManifestError(f"清单必须恰好含 format（{FORMAT}）、synthetic、samples")
    synthetic = doc["synthetic"]
    if not isinstance(synthetic, bool):
        raise ManifestError("清单必须写明 synthetic: true / false")
    raw = doc["samples"]
    if not isinstance(raw, list) or not raw:
        raise ManifestError("samples 必须是非空数组")
    out: list[Sample] = []
    seen: set[str] = set()
    refs: set[str] = set()
    for k, s in enumerate(raw):
        where = f"samples[{k}]"
        if not isinstance(s, dict) or set(s) - SAMPLE_KEYS:
            raise ManifestError(f"{where}: 字段不对（只允许 {'、'.join(sorted(SAMPLE_KEYS))}）")
        sid = s.get("id")
        if not isinstance(sid, str) or not SAMPLE_ID.fullmatch(sid) or sid in seen:
            raise ManifestError(f"{where}.id 缺失、不合规范或重复")
        seen.add(sid)
        audio = _audio(sid, s.get("audio"), synthetic)
        if audio.ref in refs:
            raise ManifestError(f"{sid}: audio.ref 重复")
        refs.add(audio.ref)
        ref = s.get("reference")
        if not isinstance(ref, str) or len(ref) > MAX_REF_CHARS:
            raise ManifestError(f"{sid}: reference 必须是字符串（可为空：检查静音或纯噪声下的插入），≤ {MAX_REF_CHARS} 字")
        hits = [name for name, rx in PII.items() if rx.search(ref)]
        if hits:
            raise ManifestError(f"{sid}: reference 疑似含个人信息（{'、'.join(hits)}）")
        for field, allowed in (("lang", LANGS), ("accent", ACCENTS), ("noise", NOISES), ("task", TASKS)):
            if s.get(field) not in allowed:
                raise ManifestError(f"{sid}: {field} 只能是 {'、'.join(allowed)}")
        expert = _expert(sid, s["expert"]) if s.get("expert") is not None else None
        out.append(Sample(sid, audio, ref, s["lang"], s["accent"], s["noise"], s["task"], expert))
    return Manifest(synthetic, tuple(sorted(out, key=lambda x: x.id)), digest)


def load(path: str | Path) -> Manifest:
    raw = Path(path).read_bytes()
    try:
        doc = yaml.safe_load(raw.decode("utf-8"))
    except (yaml.YAMLError, UnicodeDecodeError):
        raise ManifestError("清单不是 UTF-8 YAML") from None
    return parse(doc, hashlib.sha256(raw).hexdigest())
