"""报告：汇总、按条件分组的指标与样本编号（Markdown + JSON）。

- 只含数字、编号与标签：不含音频、不含参考文本与转写原文、不含供应商名与模型名（只用盲测编号）、不含时间与本机路径；
- 同样输入逐字节相同（固定种子、固定排序、数字保留 6 位小数）；
- 拒绝写进任何 gold/ 目录（金标只由教研写）。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from . import FRAMEWORK_VERSION
from .manifest import ACCENTS, LANGS, NOISES, Manifest, Sample
from .metrics import EditCounts, PhoneCounts, corpus_rate, edit_counts, pearson, phone_counts, quantile, spearman
from .stats import LEVEL, METHOD, RESAMPLES, bootstrap_ci, r6
from .text import EnNorm, ZhNorm, units_for
from .vendor import AsrOutput, PronOutput

REPORT_FORMAT = "voice-blind-report/1"
DEFAULT_SEED = "voice-blind/1"
SAMPLE_NOTICE = "流程样例，不是评测结论：清单与三家供应商都是合成数据，只证明脚本流程与指标计算。"
HUMAN_NOTICE = "本报告只给数据、不设通过线，选型由人定（spec 待人确认 #5）。"
MIN_GROUP_N = 20
QUANTILE_METHOD = "最近秩（排序后取第 ceil(p·n) 个）"
MD_NAME, JSON_NAME = "voice-blind.md", "voice-blind.json"


class ReportPathError(ValueError):
    """报告输出位置不允许"""


def _groups(samples: Sequence[Sample], *, noise: bool = True, per_lang: bool = True) -> list[tuple[str, list[Sample]]]:
    out: list[tuple[str, list[Sample]]] = []
    for lang in LANGS:
        base = [s for s in samples if s.lang == lang]
        if not base:
            continue
        out.append((lang, base))
        if not per_lang:
            continue
        for a in ACCENTS:
            sub = [s for s in base if s.accent == a]
            if sub:
                out.append((f"{lang}/accent={a}", sub))
        if noise:
            for n in NOISES:
                sub = [s for s in base if s.noise == n]
                if sub:
                    out.append((f"{lang}/noise={n}", sub))
    return out


def _ci(items: Sequence[Any], stat: Callable[[Sequence[Any]], float | None], seed: str, resamples: int) -> dict[str, Any]:
    return bootstrap_ci(items, stat, seed=seed, resamples=resamples)


def _asr_rows(manifest: Manifest, outs: Mapping[str, AsrOutput], code: str, zh: ZhNorm, en: EnNorm, seed: str,
              resamples: int) -> list[dict[str, Any]]:
    counts: dict[str, EditCounts] = {}
    for s in manifest.samples:
        hyp = outs[s.id].text if s.id in outs else ""  # 没有结果：按全部删除计，另报 missing
        counts[s.id] = edit_counts(units_for(s.lang, s.reference, zh, en), units_for(s.lang, hyp, zh, en))
    rows = []
    for name, sub in _groups(manifest.samples):
        cs = [counts[s.id] for s in sub]
        rows.append({
            "group": name, "metric": "cer" if sub[0].lang == "zh" else "wer", "n": len(sub),
            "units": sum(c.ref_len for c in cs), "sub": sum(c.sub for c in cs), "del": sum(c.dele for c in cs),
            "ins": sum(c.ins for c in cs), "missing": sum(s.id not in outs for s in sub),
            "rate": _ci(cs, corpus_rate, f"{seed}:{code}:asr:{name}", resamples),
        })
    return rows


def _latency_rows(manifest: Manifest, outs: Mapping[str, AsrOutput], code: str, seed: str, resamples: int) -> list[dict[str, Any]]:
    rows = []
    groups = [("all", list(manifest.samples))] + _groups(manifest.samples, per_lang=False)
    for name, sub in groups:
        pairs = [(outs[s.id], s.audio.duration_s) for s in sub if s.id in outs]
        rtf = [o.total_s / d for o, d in pairs]
        first = [o.first_token_s for o, _ in pairs]
        row: dict[str, Any] = {"group": name, "n": len(pairs),
                               "rtf_overall": r6(sum(o.total_s for o, _ in pairs) / sum(d for _, d in pairs)) if pairs else None}
        for label, vals in (("rtf", rtf), ("first_token_s", first)):
            for p in (0.5, 0.95):
                key = f"{label}_p{int(p * 100)}"
                row[key] = _ci(vals, lambda xs, p=p: quantile(xs, p), f"{seed}:{code}:lat:{name}:{key}", resamples)
        rows.append(row)
    return rows


def _pron_rows(manifest: Manifest, outs: Mapping[str, PronOutput], code: str, seed: str, resamples: int) -> list[dict[str, Any]]:
    annotated = [s for s in manifest.samples if s.expert is not None]
    per: dict[str, tuple[PhoneCounts, tuple[float, float] | None]] = {}
    for s in annotated:
        assert s.expert is not None
        o = outs.get(s.id)
        if o is None:  # 没有结果：专家音素都算“没判”
            pc = PhoneCounts(0, len(s.expert.phones), 0, 0, sum(not p.ok for p in s.expert.phones))
            per[s.id] = (pc, None)
        else:
            per[s.id] = (phone_counts(s.expert.phones, o.phones), (s.expert.sentence_score, o.sentence_score))

    def total(items: Sequence[tuple[PhoneCounts, Any]]) -> PhoneCounts:
        acc = PhoneCounts()
        for pc, _ in items:
            acc = acc + pc
        return acc

    def corr(fn: Callable[[Sequence[float], Sequence[float]], float | None]) -> Callable[[Sequence[Any]], float | None]:
        def stat(items: Sequence[tuple[PhoneCounts, tuple[float, float] | None]]) -> float | None:
            pairs = [sc for _, sc in items if sc is not None]
            return fn([a for a, _ in pairs], [b for _, b in pairs]) if len(pairs) >= 2 else None
        return stat

    rows = []
    for name, sub in _groups(annotated, noise=False):
        items = [per[s.id] for s in sub]
        t = total(items)
        k = f"{seed}:{code}:pron:{name}"
        rows.append({
            "group": name, "n": len(sub), "phones": t.total, "expert_errors": t.tp + t.fn,
            "missing": sum(sc is None for _, sc in items),
            "phone_accuracy": _ci(items, lambda xs: total(xs).accuracy, f"{k}:acc", resamples),
            "error_recall": _ci(items, lambda xs: total(xs).recall, f"{k}:recall", resamples),
            "error_precision": _ci(items, lambda xs: total(xs).precision, f"{k}:precision", resamples),
            "sentence_pearson": _ci(items, corr(pearson), f"{k}:pearson", resamples),
            "sentence_spearman": _ci(items, corr(spearman), f"{k}:spearman", resamples),
        })
    return rows


def evaluate(manifest: Manifest, asr: Mapping[str, Mapping[str, AsrOutput]], pron: Mapping[str, Mapping[str, PronOutput]], *,
             zh: ZhNorm = ZhNorm(), en: EnNorm = EnNorm(), seed: str = DEFAULT_SEED, resamples: int = RESAMPLES,
             blind_key_sha256: str | None = None) -> dict[str, Any]:
    """asr / pron：盲测编号 → 样本编号 → 结果。编号以外的供应商信息一概不进报告。"""
    codes = sorted(set(asr) | set(pron))
    known = {s.id for s in manifest.samples}
    for code in codes:
        extra = (set(asr.get(code, {})) | set(pron.get(code, {}))) - known
        if extra:
            raise ValueError(f"{code}: 结果里有清单外的样本编号（{len(extra)} 条）")
    samples = manifest.samples
    notices = [SAMPLE_NOTICE] if manifest.synthetic else []
    notices.append(HUMAN_NOTICE)
    rep: dict[str, Any] = {
        "format": REPORT_FORMAT,
        "framework": FRAMEWORK_VERSION,
        "synthetic": manifest.synthetic,
        "notices": notices,
        "inputs": {
            "manifest_sha256": manifest.digest, "samples": len(samples),
            "by_lang": {lang: sum(s.lang == lang for s in samples) for lang in LANGS},
            "expert_annotated": sum(s.expert is not None for s in samples),
            "audio_seconds": r6(sum(s.audio.duration_s for s in samples)),
        },
        "config": {
            "zh_norm": zh.to_dict(), "en_norm": en.to_dict(), "quantile": QUANTILE_METHOD,
            "statistics": {"method": METHOD, "resamples": resamples, "level": LEVEL, "seed": seed},
        },
        "blind": {"codes": codes, "key_sha256": blind_key_sha256},
        "asr": {c: _asr_rows(manifest, asr[c], c, zh, en, seed, resamples) for c in codes if c in asr},
        "latency": {c: _latency_rows(manifest, asr[c], c, seed, resamples) for c in codes if c in asr},
        "pron": {c: _pron_rows(manifest, pron[c], c, seed, resamples) for c in codes if c in pron},
        "samples": [{"id": s.id, "lang": s.lang, "accent": s.accent, "noise": s.noise, "task": s.task,
                     "expert": s.expert is not None} for s in samples],
    }
    small = sorted({row["group"] for rows in rep["asr"].values() for row in rows if row["n"] < MIN_GROUP_N}
                   | {row["group"] for rows in rep["pron"].values() for row in rows if row["n"] < MIN_GROUP_N})
    rep["small_groups"] = small
    return rep


# ---------- Markdown ----------
def _num(x: Any) -> str:
    if x is None:
        return "—"
    if isinstance(x, bool):
        return "是" if x else "否"
    if isinstance(x, float):
        return f"{x:.4f}"
    return str(x)


def _cell(ci: Mapping[str, Any]) -> str:
    if ci["value"] is None:
        return "—"
    if ci["ci_low"] is None:
        return _num(ci["value"])
    return f"{_num(ci['value'])} [{_num(ci['ci_low'])}, {_num(ci['ci_high'])}]"


def _table(title: str, section: Mapping[str, list[dict[str, Any]]], cols: list[tuple[str, str]], codes: list[str]) -> list[str]:
    present = [c for c in codes if c in section]
    if not present:
        return []
    groups: list[str] = []
    for c in present:
        for row in section[c]:
            if row["group"] not in groups:
                groups.append(row["group"])
    lines = ["", f"## {title}", ""]
    for key, label in cols:
        lines += [f"### {label}", "", "| 分组 | n | " + " | ".join(present) + " |", "|---|---|" + "---|" * len(present)]
        for g in groups:
            rows = {c: next((r for r in section[c] if r["group"] == g), None) for c in present}
            n = next(r["n"] for r in rows.values() if r is not None)
            cells = []
            for c in present:
                r = rows[c]
                v = None if r is None else r[key]
                cells.append(_cell(v) if isinstance(v, dict) else _num(v))
            lines.append(f"| {g} | {n} | " + " | ".join(cells) + " |")
        lines.append("")
    return lines


def render_markdown(rep: Mapping[str, Any]) -> str:
    codes = rep["blind"]["codes"]
    inp, cfg, st = rep["inputs"], rep["config"], rep["config"]["statistics"]
    lines = ["# 语音盲测报告" + ("（流程样例）" if rep["synthetic"] else ""), ""]
    lines += [f"> {n}" for n in rep["notices"]]
    lines += [
        "",
        f"- 清单：{inp['samples']} 条（中文 {inp['by_lang']['zh']}、英文 {inp['by_lang']['en']}；带专家音素标注 {inp['expert_annotated']}），"
        f"音频合计 {_num(inp['audio_seconds'])} 秒，sha256 {inp['manifest_sha256'][:16]}…；合成：{_num(rep['synthetic'])}",
        f"- 盲测编号：{'、'.join(codes)}；编号表 sha256：{rep['blind']['key_sha256'] or '—'}（揭盲由执行人按执行手册进行）",
        f"- 规范化：中文 {json.dumps(cfg['zh_norm'], sort_keys=True)}；英文 {json.dumps(cfg['en_norm'], sort_keys=True)}",
        f"- 区间：{st['method']}，{st['resamples']} 次，置信水平 {st['level']}，种子 `{st['seed']}`；分位数：{cfg['quantile']}",
        "- 单元格写法：点估计 [95% 区间下限, 上限]；— 表示无定义（例如分组里没有专家标出的错误）",
        f"- 框架：{rep['framework']}；报告只含汇总、分组指标与样本编号，不含音频与转写原文",
    ]
    if rep["small_groups"]:
        lines.append(f"- 样本少于 {MIN_GROUP_N} 条的分组（区间很宽，只作参考）：{'、'.join(rep['small_groups'])}")
    lines += _table("识别错误率（中文 CER 按字、英文 WER 按词）", rep["asr"],
                    [("rate", "错误率"), ("units", "参考单位数"), ("sub", "替换"), ("del", "删除"), ("ins", "插入"), ("missing", "缺结果")], codes)
    lines += _table("延迟", rep["latency"],
                    [("rtf_overall", "实时率（总耗时 / 总音频时长）"), ("rtf_p50", "实时率 p50"), ("rtf_p95", "实时率 p95"),
                     ("first_token_s_p50", "首字延迟 p50（秒）"), ("first_token_s_p95", "首字延迟 p95（秒）")], codes)
    lines += _table("音素评测与专家标注的一致性", rep["pron"],
                    [("phone_accuracy", "音素级准确率"), ("error_recall", "错误检出召回"), ("error_precision", "错误检出精确"),
                     ("sentence_pearson", "句级分数 Pearson"), ("sentence_spearman", "句级分数 Spearman"),
                     ("phones", "专家音素数"), ("missing", "缺结果")], codes)
    lines += ["", "## 样本编号", "", "| 样本编号 | 语言 | 口音 | 噪声 | 任务 | 专家标注 |", "|---|---|---|---|---|---|"]
    for s in rep["samples"]:
        lines.append(f"| {s['id']} | {s['lang']} | {s['accent']} | {s['noise']} | {s['task']} | {_num(s['expert'])} |")
    return "\n".join(lines) + "\n"


def _refuse_gold(d: Path) -> None:
    parts = [p.lower() for p in d.resolve().parts]
    if "gold" in parts or any(p.lower() == "gold" for p in d.parts):
        raise ReportPathError("报告与任何产物不得写进 gold/ 目录（金标只由教研写）")


def write_report(rep: Mapping[str, Any], out: str | Path) -> tuple[Path, Path]:
    d = Path(out)
    _refuse_gold(d)
    d.mkdir(parents=True, exist_ok=True)
    md, js = d / MD_NAME, d / JSON_NAME
    md.write_text(render_markdown(rep), encoding="utf-8")
    js.write_text(json.dumps(rep, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return md, js
