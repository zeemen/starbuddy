"""人设治理组件测试的公共夹具：全部是合成数据；每个测试都禁止网络调用（spec 验收：不读密钥、不发起任何网络请求）。"""
from __future__ import annotations

import socket
import sys
from pathlib import Path

import pytest

PKG_PARENT = Path(__file__).resolve().parents[2]  # packages/
ROOT = PKG_PARENT.parent
if str(PKG_PARENT) not in sys.path:
    sys.path.insert(0, str(PKG_PARENT))


class NetworkCalled(AssertionError):
    pass


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    calls: list[object] = []

    def refuse(*args, **kwargs):
        calls.append(args)
        raise NetworkCalled(f"人设治理组件不应发起网络请求：{args!r}")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket.socket, "connect_ex", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(socket, "getaddrinfo", refuse)
    yield calls
    assert not calls, "测试期间发生了网络调用"
