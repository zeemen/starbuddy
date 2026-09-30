"""供应商适配接口（只有抽象类，没有任何具体供应商实现）与统一结果格式。

- 具体供应商的适配器在候选名单签字（ADR 04 / 08）、数据处理协议签好之后另起任务实现，只在境内执行环境里运行，
  密钥由执行环境注入，不进仓库；本框架不读取任何环境变量或密钥。
- 请求里只有随机条目编号、音频引用与元数据、语言；ASR 请求**不含参考文本**，任何请求都不含口音、噪声标签与样本编号。
- 延迟由适配器在调用处测量（首字延迟只有流式接口内部看得到），框架不计时，所以同样的结果文件逐次得到相同报告。
"""
from __future__ import annotations

import abc
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from . import blind
from .manifest import Audio, Manifest
from .metrics import PhoneJudgement


@dataclass(frozen=True)
class AsrRequest:
    item_id: str
    audio: Audio
    language: str


@dataclass(frozen=True)
class AsrOutput:
    item_id: str
    text: str
    first_token_s: float   # 从开始送音频到收到第一个识别字的秒数
    total_s: float         # 从开始送音频到拿到最终结果的秒数（实时率 = total_s / 音频时长）


@dataclass(frozen=True)
class PronRequest:
    item_id: str
    audio: Audio
    language: str
    reference_text: str    # 发音评测是朗读评测，需要参考文本


@dataclass(frozen=True)
class PronOutput:
    item_id: str
    phones: tuple[PhoneJudgement, ...]
    sentence_score: float  # 0–100


class AsrVendor(abc.ABC):
    """ASR 适配接口。vendor_id 只有执行人知道，报告里只出现盲测编号。"""
    vendor_id: str

    @abc.abstractmethod
    def transcribe(self, req: AsrRequest) -> AsrOutput:
        """识别一条音频。实现方负责读取音频（在境内执行环境里）、测量延迟；不得把音频或结果用于训练。"""


class PronunciationVendor(abc.ABC):
    """音素级发音评测适配接口。"""
    vendor_id: str

    @abc.abstractmethod
    def assess(self, req: PronRequest) -> PronOutput:
        """评测一条朗读音频，返回音素级对 / 错判断与句级分数。"""


class VendorOutputError(ValueError):
    """供应商输出不合统一格式（消息只含字段名与条目编号）"""


def _check_timing(item: str, first: float, total: float) -> None:
    for v in (first, total):
        if isinstance(v, bool) or not isinstance(v, (int, float)) or v < 0:
            raise VendorOutputError(f"{item}: 延迟必须是非负数")
    if first > total:
        raise VendorOutputError(f"{item}: 首字延迟不能大于总耗时")


def run_asr(vendor: AsrVendor, manifest: Manifest, *, seed: str, code: str) -> dict[str, AsrOutput]:
    """把同一批音频按随机顺序提交给一家，返回 样本编号 → 结果"""
    ids = blind.item_ids([s.id for s in manifest.samples], seed)
    by_id = {s.id: s for s in manifest.samples}
    out: dict[str, AsrOutput] = {}
    for sid in blind.submission_order(list(by_id), seed, code):
        s = by_id[sid]
        res = vendor.transcribe(AsrRequest(ids[sid], s.audio, s.lang))
        if not isinstance(res, AsrOutput) or res.item_id != ids[sid] or not isinstance(res.text, str):
            raise VendorOutputError(f"{ids[sid]}: ASR 输出格式不对")
        _check_timing(ids[sid], res.first_token_s, res.total_s)
        out[sid] = res
    return out


def run_pron(vendor: PronunciationVendor, manifest: Manifest, *, seed: str, code: str) -> dict[str, PronOutput]:
    """只提交带专家标注的样本"""
    ids = blind.item_ids([s.id for s in manifest.samples], seed)
    by_id = {s.id: s for s in manifest.samples if s.expert is not None}
    out: dict[str, PronOutput] = {}
    for sid in blind.submission_order(list(by_id), seed, code):
        s = by_id[sid]
        res = vendor.assess(PronRequest(ids[sid], s.audio, s.lang, s.reference))
        if (not isinstance(res, PronOutput) or res.item_id != ids[sid]
                or not all(isinstance(p, PhoneJudgement) for p in res.phones)
                or isinstance(res.sentence_score, bool) or not isinstance(res.sentence_score, (int, float))
                or not 0 <= res.sentence_score <= 100):
            raise VendorOutputError(f"{ids[sid]}: 发音评测输出格式不对")
        out[sid] = res
    return out


# ---------- 结果文件（执行环境里的中间产物；含转写原文，不进仓库，评测完按方案删除） ----------
def write_results(d: Path, code: str, asr: Mapping[str, AsrOutput] | None, pron: Mapping[str, PronOutput] | None) -> None:
    d.mkdir(parents=True, exist_ok=True)
    if asr is not None:
        with (d / f"{code}.asr.jsonl").open("w", encoding="utf-8") as f:
            for sid in sorted(asr):
                o = asr[sid]
                f.write(json.dumps({"sample_id": sid, "text": o.text, "first_token_s": o.first_token_s, "total_s": o.total_s},
                                   ensure_ascii=False, sort_keys=True) + "\n")
    if pron is not None:
        with (d / f"{code}.pron.jsonl").open("w", encoding="utf-8") as f:
            for sid in sorted(pron):
                o = pron[sid]
                f.write(json.dumps({"sample_id": sid, "phones": [[p.phone, p.ok] for p in o.phones],
                                    "sentence_score": o.sentence_score}, ensure_ascii=False, sort_keys=True) + "\n")


def load_results(d: Path) -> tuple[dict[str, dict[str, AsrOutput]], dict[str, dict[str, PronOutput]]]:
    """读 <编号>.asr.jsonl 与 <编号>.pron.jsonl；文件名里的编号必须是 VA、VB… 形式"""
    asr: dict[str, dict[str, AsrOutput]] = {}
    pron: dict[str, dict[str, PronOutput]] = {}
    for p in sorted(d.glob("*.jsonl")):
        code, _, kind = p.name[: -len(".jsonl")].partition(".")
        if not (len(code) == 2 and code[0] == "V" and code[1].isupper()) or kind not in ("asr", "pron"):
            raise VendorOutputError(f"结果文件名不合规范：{p.name}")
        for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            try:
                r = json.loads(line)
                sid = r["sample_id"]
                if kind == "asr":
                    _check_timing(f"{code}:{n}", r["first_token_s"], r["total_s"])
                    if not isinstance(r["text"], str):
                        raise TypeError
                    asr.setdefault(code, {})[sid] = AsrOutput(sid, r["text"], float(r["first_token_s"]), float(r["total_s"]))
                else:
                    phones = tuple(PhoneJudgement(str(ph), bool(ok)) for ph, ok in r["phones"])
                    pron.setdefault(code, {})[sid] = PronOutput(sid, phones, float(r["sentence_score"]))
            except (ValueError, KeyError, TypeError):
                raise VendorOutputError(f"{p.name} 第 {n} 行格式不对") from None
    return asr, pron
