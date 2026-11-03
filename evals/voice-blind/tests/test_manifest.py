"""输入清单校验：音频只能是不透明引用、标签取值受限、不许多余字段、参考文本扫个人信息、消息不含原文。"""
from __future__ import annotations

import copy
from pathlib import Path

import pytest
import yaml

from voiceblind.manifest import ManifestError, load, parse

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "manifest-synthetic.yaml"


def base() -> dict:
    return {"format": "voice-blind-manifest/1", "synthetic": True, "samples": [{
        "id": "S-1", "audio": {"ref": "synthetic://silence/S-1", "duration_s": 2.0, "sample_rate_hz": 16000},
        "reference": "今天天气很好。", "lang": "zh", "accent": "standard", "noise": "quiet", "task": "read",
        "expert": {"phones": ["j", "in"], "errors": [1], "sentence_score": 80}}]}


def with_(path: str, value) -> dict:
    d = base()
    cur = d
    keys = path.split(".")
    for k in keys[:-1]:
        cur = cur[int(k)] if k.isdigit() else cur[k]
    cur[keys[-1]] = value
    return d


def test_fixture_is_valid_and_synthetic():
    m = load(FIX)
    assert m.synthetic and len(m.samples) == 24 and len(m.digest) == 64
    assert "合成" in FIX.read_text(encoding="utf-8").split("\n", 1)[0]
    assert all(s.audio.ref.startswith("synthetic://") for s in m.samples)
    assert {s.accent for s in m.samples} == {"standard", "nanjing", "other"} and {s.lang for s in m.samples} == {"zh", "en"}
    assert any(s.reference == "" for s in m.samples)
    assert [s.id for s in m.samples] == sorted(s.id for s in m.samples)
    e = next(s for s in m.samples if s.id == "ZH-001").expert
    assert e is not None and [p.ok for p in e.phones].count(False) == 1 and not e.phones[9].ok


def test_minimal_manifest_parses():
    m = parse(base())
    assert m.samples[0].expert is not None and m.samples[0].expert.phones[1].ok is False


@pytest.mark.parametrize("ref", [
    "https://example.invalid/a.wav", "http://x/a.wav", "file:///tmp/a.wav", "/tmp/a.wav", "a.wav",
    "synthetic://../etc/passwd", "synthetic://a/../../b", "store://x",
])
def test_audio_ref_must_be_opaque_key(ref):
    with pytest.raises(ManifestError):
        parse(with_("samples.0.audio.ref", ref))


@pytest.mark.parametrize("ref", ["https://example.invalid/a.wav", "file:///tmp/a.wav", "oss://bucket/a.wav"])
def test_real_manifest_only_accepts_store_keys(ref):
    d = with_("samples.0.audio.ref", ref)
    d["synthetic"] = False
    with pytest.raises(ManifestError):
        parse(d)


def test_synthetic_flag_matches_ref_scheme():
    d = with_("samples.0.audio.ref", "store://batch1/S-1")
    with pytest.raises(ManifestError):
        parse(d)
    d["synthetic"] = False
    assert parse(d).synthetic is False
    d2 = base()
    d2["synthetic"] = False
    with pytest.raises(ManifestError):
        parse(d2)


@pytest.mark.parametrize("path,value", [
    ("samples.0.lang", "fr"), ("samples.0.accent", "shanghai"), ("samples.0.noise", "loud"), ("samples.0.task", "sing"),
    ("samples.0.id", "../x"), ("samples.0.audio.duration_s", 0), ("samples.0.audio.duration_s", 601),
    ("samples.0.audio.sample_rate_hz", 12345), ("samples.0.audio.channels", 3), ("samples.0.reference", None),
    ("samples.0.expert.errors", [5]), ("samples.0.expert.errors", [1, 1]), ("samples.0.expert.sentence_score", 120),
    ("samples.0.expert.phones", []), ("samples.0.student_name", "某某"), ("samples.0.audio.bytes", "AAAA"),
    ("synthetic", "yes"), ("format", "other/1"),
])
def test_invalid_fields_rejected(path, value):
    with pytest.raises(ManifestError):
        parse(with_(path, value))


def test_duplicate_ids_and_refs_rejected():
    d = base()
    d["samples"].append(copy.deepcopy(d["samples"][0]))
    with pytest.raises(ManifestError):
        parse(d)
    d["samples"][1]["id"] = "S-2"
    with pytest.raises(ManifestError):
        parse(d)  # 同一音频引用


@pytest.mark.parametrize("pii", ["请联系13912345678", "证件 00000020000101000X", "发到 someone@example.com"])
def test_reference_pii_rejected_without_echoing_text(pii):
    with pytest.raises(ManifestError) as e:
        parse(with_("samples.0.reference", pii))
    assert pii not in str(e.value) and "S-1" in str(e.value)


def test_error_messages_do_not_echo_reference(tmp_path):
    d = with_("samples.0.lang", "fr")
    with pytest.raises(ManifestError) as e:
        parse(d)
    assert "今天天气" not in str(e.value)
    p = tmp_path / "m.yaml"
    p.write_bytes(b"\xff\xfe")
    with pytest.raises(ManifestError):
        load(p)
    p.write_text(yaml.safe_dump(base(), allow_unicode=True), encoding="utf-8")
    assert load(p).samples[0].id == "S-1"
