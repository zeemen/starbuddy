"""不联网、不持有密钥、不读环境变量、不含任何供应商实现、不含音频文件（CLAUDE.md 规则 8、9、12；spec 验收第 4 条）。"""
from __future__ import annotations

import re
import socket
from pathlib import Path

import pytest

import run_blind

HERE = Path(__file__).resolve().parents[1]
FIX = HERE / "fixtures" / "manifest-synthetic.yaml"
SOURCES = sorted(p for p in (HERE / "voiceblind").glob("*.py")) + [HERE / "run_blind.py"]
AUDIO_EXT = {".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg", ".opus", ".pcm", ".amr", ".webm", ".silk", ".speex"}


@pytest.fixture
def no_network(monkeypatch):
    calls: list[str] = []

    def deny(*a, **k):
        calls.append("network")
        raise AssertionError("评测框架不得联网")

    monkeypatch.setattr(socket.socket, "connect", deny)
    monkeypatch.setattr(socket.socket, "connect_ex", deny)
    monkeypatch.setattr(socket, "create_connection", deny)
    monkeypatch.setattr(socket, "getaddrinfo", deny)
    return calls


def test_full_pipeline_makes_no_network_calls(tmp_path, no_network):
    res = tmp_path / "r"
    assert run_blind.main(["sample", "--manifest", str(FIX), "--out", str(tmp_path / "a"), "--results", str(res)]) == 0
    assert run_blind.main(["score", "--manifest", str(FIX), "--results", str(res), "--out", str(tmp_path / "b")]) == 0
    assert no_network == []


def test_sources_have_no_network_env_or_secret_access():
    banned = re.compile(r"^\s*(import|from)\s+(socket|ssl|urllib|http|requests|httpx|aiohttp|websocket|websockets|grpc|subprocess)\b"
                        r"|os\.environ|getenv|dotenv|api[_-]?key|secret[_-]?key|access[_-]?key|\bpassword\b|Authorization",
                        re.M | re.I)
    for p in SOURCES:
        hits = banned.findall(p.read_text(encoding="utf-8"))
        assert not hits, f"{p.name} 不得联网、读环境变量或持有密钥"


def test_no_vendor_implementation_beyond_synthetic_fakes():
    import inspect

    from voiceblind import fakes, vendor
    for mod in (vendor,):
        for _, cls in inspect.getmembers(mod, inspect.isclass):
            if issubclass(cls, (vendor.AsrVendor, vendor.PronunciationVendor)):
                assert inspect.isabstract(cls), f"{cls.__name__}：vendor.py 只能有抽象接口"
    concrete = [c for _, c in inspect.getmembers(fakes, inspect.isclass)
                if issubclass(c, (vendor.AsrVendor, vendor.PronunciationVendor)) and not inspect.isabstract(c)]
    assert concrete and all(c.vendor_id.startswith("fake-") for c in concrete)


def test_no_audio_files_in_framework():
    for p in HERE.rglob("*"):
        assert p.suffix.lower() not in AUDIO_EXT, f"评测目录里不得有音频文件：{p.name}"
    assert not (HERE / "gold").exists(), "本任务不创建 gold/"
