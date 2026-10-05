"""走查命令行：产物目录在一开始核对（已存在且非空、或不是目录即退出码 2，一步也不走）。合成数据，不起库。"""
from __future__ import annotations

import pytest

import release_walkthrough.__main__ as m


@pytest.fixture
def no_run(monkeypatch):
    calls: list = []

    def boom(out):
        calls.append(out)
        raise AssertionError("产物目录不合要求时不应开始走查")
    monkeypatch.setattr(m, "main_run", boom)
    return calls


def test_non_empty_out_refused(tmp_path, no_run, capsys):
    (tmp_path / "walkthrough.json").write_text("{}\n", encoding="utf-8")
    assert m.main(["run", "--out", str(tmp_path), "--check-sample", str(tmp_path)]) == 2
    assert no_run == [] and "已存在且非空" in capsys.readouterr().err
    assert [p.name for p in tmp_path.iterdir()] == ["walkthrough.json"]  # 不动已有产物


def test_out_is_file_refused(tmp_path, no_run, capsys):
    f = tmp_path / "out"
    f.write_text("x", encoding="utf-8")
    assert m.main(["run", "--out", str(f)]) == 2
    assert no_run == [] and "不是目录" in capsys.readouterr().err


def test_new_or_empty_out_accepted(tmp_path, monkeypatch):
    seen: list = []

    def fake_run(out):
        seen.append(out)
        return 2  # 假装前提不满足：只核对走到了这一步
    monkeypatch.setattr(m, "main_run", fake_run)
    assert m.main(["run", "--out", str(tmp_path / "new")]) == 2
    empty = tmp_path / "empty"
    empty.mkdir()
    assert m.main(["run", "--out", str(empty)]) == 2
    assert [p.name for p in seen] == ["new", "empty"]
