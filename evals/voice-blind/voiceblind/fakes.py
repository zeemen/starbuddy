"""合成假供应商（只用于证明流程与指标计算；不是任何真实供应商的模拟）。

三家行为都是对参考文本的**确定性扰动**（按 供应商 ID + 样本编号 播种），不读音频、不联网：
- fake-accurate：只改标点与全半角（默认规范化下 CER / WER 为 0），延迟稳定；音素判断与专家一致，句级分数小幅偏差；
- fake-accent-biased：标准口音样本准确，口音样本（nanjing、other）每 3 个单位错 1 个（系统性替换），
  音素上把口音样本的前两个音素判错、句级分数压低 15 分；
- fake-random：每个单位 12% 概率替换、4% 删除、4% 插入；音素判断 20% 翻转，句级分数加 ±20 以内的噪声；延迟长尾。
假供应商从构造参数拿到“真值”（条目编号 → 样本），接口里的请求本身不含参考文本（ASR）与任何标签。
"""
from __future__ import annotations

import abc
import random
from typing import Mapping

from . import blind
from .manifest import Manifest, Sample
from .metrics import PhoneJudgement
from .text import EnNorm, ZhNorm, en_words, zh_units
from .vendor import AsrOutput, AsrRequest, AsrVendor, PronOutput, PronRequest, PronunciationVendor

FAKE_IDS = ("fake-accurate", "fake-accent-biased", "fake-random")
_ZH_FILL = "的一是在不了有和人这中大为上个国我以要他"
_EN_FILL = ("the", "a", "and", "to", "of", "in", "is", "it", "uh", "so")
_FULLWIDTH = str.maketrans({",": "，", ".": "。", "?": "？", "!": "！", "A": "Ａ", "B": "Ｂ", "1": "１", "2": "２"})


def _oracle(manifest: Manifest, seed: str) -> dict[str, Sample]:
    ids = blind.item_ids([s.id for s in manifest.samples], seed)
    return {ids[s.id]: s for s in manifest.samples}


def _units(s: Sample) -> list[str]:
    raw = ZhNorm(strip_punct=True, nfkc=False, lowercase=False)
    return zh_units(s.reference, raw) if s.lang == "zh" else en_words(s.reference, EnNorm(lowercase=False))


def _join(s: Sample, units: list[str]) -> str:
    return "".join(units) if s.lang == "zh" else " ".join(units)


class _FakeBase:
    vendor_id = ""

    def __init__(self, manifest: Manifest, seed: str) -> None:
        self._truth: Mapping[str, Sample] = _oracle(manifest, seed)

    def _rng(self, s: Sample, what: str) -> random.Random:
        return random.Random(f"{self.vendor_id}:{what}:{s.id}")


class _FakeAsr(_FakeBase, AsrVendor):
    """三家 ASR 假供应商的公共部分（抽象）"""

    def transcribe(self, req: AsrRequest) -> AsrOutput:
        s = self._truth[req.item_id]
        rng = self._rng(s, "asr")
        text = self._hyp(s, rng)
        first, rtf = self._timing(rng)
        total = round(max(first, rtf * req.audio.duration_s), 4)
        return AsrOutput(req.item_id, text, round(first, 4), total)

    @abc.abstractmethod
    def _hyp(self, s: Sample, rng: random.Random) -> str: ...

    @abc.abstractmethod
    def _timing(self, rng: random.Random) -> tuple[float, float]: ...


class FakeAccurateAsr(_FakeAsr):
    vendor_id = "fake-accurate"

    def _hyp(self, s: Sample, rng: random.Random) -> str:
        # 只改标点与全半角：默认规范化下应当 0 错
        if s.lang == "zh":
            return s.reference.translate(_FULLWIDTH).replace("。", "")
        return s.reference.upper().replace(".", " .")

    def _timing(self, rng: random.Random) -> tuple[float, float]:
        return 0.30 + 0.10 * rng.random(), 0.20 + 0.05 * rng.random()


class FakeAccentBiasedAsr(_FakeAsr):
    vendor_id = "fake-accent-biased"

    def _hyp(self, s: Sample, rng: random.Random) -> str:
        units = _units(s)
        if s.accent == "standard":
            return _join(s, units)
        fill = _ZH_FILL if s.lang == "zh" else _EN_FILL
        return _join(s, [fill[k % len(fill)] if k % 3 == 1 else u for k, u in enumerate(units)])

    def _timing(self, rng: random.Random) -> tuple[float, float]:
        return 0.45 + 0.15 * rng.random(), 0.30 + 0.10 * rng.random()


class FakeRandomAsr(_FakeAsr):
    vendor_id = "fake-random"

    def _hyp(self, s: Sample, rng: random.Random) -> str:
        fill = _ZH_FILL if s.lang == "zh" else _EN_FILL
        out: list[str] = []
        for u in _units(s):
            x = rng.random()
            if x < 0.12:
                out.append(fill[rng.randrange(len(fill))])
            elif x < 0.16:
                continue
            elif x < 0.20:
                out += [u, fill[rng.randrange(len(fill))]]
            else:
                out.append(u)
        if not s.reference.strip() and rng.random() < 0.5:  # 空参考（静音）上偶尔“幻听”
            out.append(fill[0])
        return _join(s, out)

    def _timing(self, rng: random.Random) -> tuple[float, float]:
        tail = 2.0 if rng.random() < 0.1 else 0.0
        return 0.25 + 0.5 * rng.random() + tail, 0.15 + 0.6 * rng.random()


class _FakePron(_FakeBase, PronunciationVendor):
    def assess(self, req: PronRequest) -> PronOutput:
        s = self._truth[req.item_id]
        assert s.expert is not None
        rng = self._rng(s, "pron")
        phones, score = self._judge(s, rng)
        return PronOutput(req.item_id, tuple(phones), round(min(100.0, max(0.0, score)), 2))

    @abc.abstractmethod
    def _judge(self, s: Sample, rng: random.Random) -> tuple[list[PhoneJudgement], float]: ...


class FakeAccuratePron(_FakePron):
    vendor_id = "fake-accurate"

    def _judge(self, s: Sample, rng: random.Random) -> tuple[list[PhoneJudgement], float]:
        assert s.expert is not None
        return list(s.expert.phones), s.expert.sentence_score + rng.uniform(-3, 3)


class FakeAccentBiasedPron(_FakePron):
    vendor_id = "fake-accent-biased"

    def _judge(self, s: Sample, rng: random.Random) -> tuple[list[PhoneJudgement], float]:
        assert s.expert is not None
        if s.accent == "standard":
            return list(s.expert.phones), s.expert.sentence_score + rng.uniform(-3, 3)
        phones = [PhoneJudgement(p.phone, False) if k < 2 else p for k, p in enumerate(s.expert.phones)]
        return phones, s.expert.sentence_score - 15 + rng.uniform(-3, 3)


class FakeRandomPron(_FakePron):
    vendor_id = "fake-random"

    def _judge(self, s: Sample, rng: random.Random) -> tuple[list[PhoneJudgement], float]:
        assert s.expert is not None
        phones = [PhoneJudgement(p.phone, (not p.ok) if rng.random() < 0.2 else p.ok) for p in s.expert.phones]
        return phones, s.expert.sentence_score + rng.uniform(-20, 20)


def build(manifest: Manifest, seed: str) -> tuple[list[AsrVendor], list[PronunciationVendor]]:
    asr: list[AsrVendor] = [FakeAccurateAsr(manifest, seed), FakeAccentBiasedAsr(manifest, seed), FakeRandomAsr(manifest, seed)]
    pron: list[PronunciationVendor] = [FakeAccuratePron(manifest, seed), FakeAccentBiasedPron(manifest, seed),
                                       FakeRandomPron(manifest, seed)]
    return asr, pron
